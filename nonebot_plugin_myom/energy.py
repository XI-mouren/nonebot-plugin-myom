# Copyright (C) 2026 XI-mouren
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program. If not, see <https://www.gnu.org/licenses/>.

"""能量节律引擎（四态 + 能量档位）。

两层职责严格分离：
- **时间片状态**（兴奋/平淡/疲倦）只决定能量怎么变（回复消耗、未回复恢复、被 @ 提升）
  和语气基调（写进 prompt），**不做任何硬拦截**；
- **能量档位**（四档）决定怎么回复：语气、表情包概率与候选数量、回复倾向提示。
- **睡眠是独立状态**，也是唯一的硬拦截：能量 <= sleep.threshold 即入睡，非 @ 消息直接丢弃。

休眠是第四态（硬拦截），优先级最高。
设计稿：docs/能量机制重构方案.md
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from nonebot import logger
import nonebot_plugin_localstore as store

from .config import plugin_config

CONFIG_FILE = store.get_plugin_config_dir() / "energy.json"
DATA_DIR = store.get_plugin_data_dir() / "energy"


def config_path() -> Path:
    """实际使用的 energy.json 路径（AIGF_ENERGY_CONFIG 可覆盖，便于多实例/测试）"""
    custom = (plugin_config.aigf_energy_config or "").strip()
    return Path(custom).expanduser() if custom else CONFIG_FILE

STATE_KEYS: tuple[str, ...] = ("excited", "normal", "tired")
SLEEP_STATE = "sleep"
DAY_MINUTES = 24 * 60
MIN_WINDOW_MINUTES = 30
INITIAL_ENERGY = 0.75


class EnergyConfigError(ValueError):
    """energy.json 校验失败（启动时应 fail-fast）"""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("；".join(errors))


# 时间片：只影响能量变化
DEFAULT_STATES: dict[str, Any] = {
    "excited": {
        "label": "兴奋期",
        "window": ["17:00", "01:00"],
        "reply_cost": [0.03, 0.08],
        "idle_gain": [0.06, 0.14],
        "at_boost": 0.10,
    },
    "normal": {
        "label": "平淡期",
        "window": ["09:00", "17:00"],
        "reply_cost": [0.05, 0.11],
        "idle_gain": [-0.01, 0.04],
        "at_boost": 0.08,
    },
    "tired": {
        "label": "疲倦期",
        "window": ["01:00", "09:00"],
        "reply_cost": [0.12, 0.20],
        "idle_gain": [0.00, 0.02],
        "at_boost": 0.05,
    },
}

# 能量档位：决定怎么回复（阈值从高到低；末档必须 > 0，0 及以下属睡眠态）
DEFAULT_LEVELS: list[dict[str, Any]] = [
    {
        "threshold": 0.80,
        "label": "精力充沛",
        "tone": "精力充沛，看到什么都想插嘴；语气轻快，可以主动接梗、开玩笑",
        "meme_prob": 0.55,
        "meme_candidates": 20,
        "reply_base": 0.55,
    },
    {
        "threshold": 0.60,
        "label": "状态不错",
        "tone": "状态不错，有兴趣的话题会主动参与；正常聊天，偶尔接梗",
        "meme_prob": 0.45,
        "meme_candidates": 16,
        "reply_base": 0.38,
    },
    {
        "threshold": 0.40,
        "label": "一般般",
        "tone": "一般般，有人找就回，不太主动；句子简短，不展开话题",
        "meme_prob": 0.30,
        "meme_candidates": 12,
        "reply_base": 0.25,
    },
    {
        "threshold": 0.20,
        "label": "有点懒",
        "tone": "有点懒，倾向于潜水；只 @ 或点你名字时才回，回得很短",
        "meme_prob": 0.12,
        "meme_candidates": 8,
        "reply_base": 0.10,
    },
]

DEFAULTS: dict[str, Any] = {
    "version": 2,
    "timezone_offset": None,
    "noise": 0.01,
    "states": DEFAULT_STATES,
    "levels": DEFAULT_LEVELS,
    "sleep": {
        "threshold": 0.15,
        "duration_minutes": [180, 300],
        "recover_per_10min": 0.02,
        "wake_boost": 0.28,
        "wake_floor": 0.55,
        "drowsy_minutes": 30,
        "block_non_at": True,
        "wake_tone": "你刚才睡着了，被 @ 叫醒。先说明自己刚睡着、现在还很困，再回应对方的话；语气不要冲。",
    },
    "decision": {
        "meme_max_per_reply": 1,
        "meme_prob_at_boost": 0.15,
        "reply_hint_k": 0.60,
        "reply_hint_max": 0.95,
    },
}

_cfg_cache: dict[str, Any] | None = None
_cfg_mtime: float = 0.0


# --------------------------- 配置：读取 / 校验 / 缓存 --------------------------- #


def _parse_hhmm(text: str) -> int:
    """'HH:MM' → 当日分钟数；格式非法抛 ValueError"""
    if not isinstance(text, str) or ":" not in text:
        raise ValueError(f"时间格式必须是 HH:MM，收到 {text!r}")
    hh, mm = text.split(":", 1)
    h, m = int(hh), int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"时间越界：{text!r}")
    return h * 60 + m


def _window_spans(window: list[str]) -> list[tuple[int, int]]:
    """时间窗 → 当日区间列表（跨零点拆成两段）"""
    start, end = _parse_hhmm(window[0]), _parse_hhmm(window[1])
    if start == end:
        raise ValueError("时间窗的起止时间不能相同")
    return [(start, end)] if start < end else [(start, DAY_MINUTES), (0, end)]


def _num_pair(value: Any, name: str, *, allow_negative: bool) -> list[str]:
    errs: list[str] = []
    if not (isinstance(value, (list, tuple)) and len(value) == 2):
        return [f"{name} 必须是 [min, max] 两元素数组"]
    lo, hi = value
    if not all(isinstance(x, (int, float)) for x in (lo, hi)):
        return [f"{name} 的元素必须是数字"]
    if lo > hi:
        errs.append(f"{name} 的 min({lo}) 大于 max({hi})")
    if not allow_negative and lo < 0:
        errs.append(f"{name} 不能为负（收到 {lo}）")
    return errs


def validate_config(cfg: dict[str, Any]) -> list[str]:
    """校验配置，返回错误信息列表（空列表 = 合法）"""
    errs: list[str] = []

    # ---- 时间片：只校验能量参数与时间窗 ----
    states = cfg.get("states") or {}
    for key in STATE_KEYS:
        st = states.get(key)
        if not isinstance(st, dict):
            errs.append(f"缺少状态配置：states.{key}")
            continue
        window = st.get("window")
        if not (isinstance(window, (list, tuple)) and len(window) == 2):
            errs.append(f"states.{key}.window 必须是 [起, 止] 两个 HH:MM")
        else:
            try:
                span = _window_spans(list(window))
                total = sum(e - s for s, e in span)
                if total < MIN_WINDOW_MINUTES:
                    errs.append(
                        f"{st.get('label', key)} 时间窗过短（{total} 分钟 < {MIN_WINDOW_MINUTES}）")
            except Exception as exc:  # noqa: BLE001
                errs.append(f"states.{key}.window 非法：{exc}")
        errs += _num_pair(st.get("reply_cost"), f"states.{key}.reply_cost", allow_negative=False)
        errs += _num_pair(st.get("idle_gain"), f"states.{key}.idle_gain", allow_negative=True)
        boost = st.get("at_boost")
        if not isinstance(boost, (int, float)) or boost < 0:
            errs.append(f"states.{key}.at_boost 必须是非负数")

    # ---- 时间窗：重叠与覆盖（必须正好铺满 24 小时）----
    owner: list[str] = [""] * DAY_MINUTES
    spans: dict[str, list[tuple[int, int]]] = {}
    windows_ok = True
    for key in STATE_KEYS:
        try:
            spans[key] = _window_spans(list(states[key]["window"]))
        except Exception:  # noqa: BLE001
            windows_ok = False
            break
    if windows_ok:
        for key in STATE_KEYS:
            label = states[key].get("label", key)
            for start, end in spans[key]:
                for minute in range(start, end):
                    if owner[minute]:
                        prev = states[owner[minute]].get("label", owner[minute])
                        clock = f"{minute // 60:02d}:{minute % 60:02d}"
                        errs.append(f"时间窗重叠：{prev} 与 {label} 在 {clock} 冲突")
                        break
                    owner[minute] = key
        gaps = [m for m, o in enumerate(owner) if not o]
        if gaps:
            start = gaps[0]
            errs.append(
                f"时间窗未覆盖 24 小时：缺少 {start // 60:02d}:{start % 60:02d} 起共 {len(gaps)} 分钟")

    # ---- 能量档位：阈值必须严格递减；末档必须 > 0（0 及以下属睡眠态）----
    levels = cfg.get("levels")
    if not (isinstance(levels, list) and levels):
        errs.append("levels 必须是非空数组")
    else:
        prev_threshold = 1.01
        for idx, lv in enumerate(levels):
            if not isinstance(lv, dict):
                errs.append(f"levels[{idx}] 必须是对象")
                continue
            thr = lv.get("threshold")
            if not isinstance(thr, (int, float)) or not (0 <= thr <= 1):
                errs.append(f"levels[{idx}].threshold 必须在 0~1 之间")
                continue
            if thr >= prev_threshold:
                errs.append(f"levels[{idx}].threshold({thr}) 必须小于上一档({prev_threshold})")
            prev_threshold = thr
            prob = lv.get("meme_prob")
            if not isinstance(prob, (int, float)) or not (0 <= prob <= 1):
                errs.append(f"levels[{idx}].meme_prob 必须在 0~1 之间")
            cand = lv.get("meme_candidates")
            if not isinstance(cand, int) or cand < 0:
                errs.append(f"levels[{idx}].meme_candidates 必须是非负整数")
            base = lv.get("reply_base")
            if not isinstance(base, (int, float)) or not (0 <= base <= 1):
                errs.append(f"levels[{idx}].reply_base 必须在 0~1 之间")
        last_thr = levels[-1].get("threshold") if (levels and isinstance(levels[-1], dict)) else None
        if isinstance(last_thr, (int, float)) and last_thr <= 0:
            errs.append(
                "levels 最后一档的 threshold 必须大于 0："
                "能量 <= sleep.threshold 时已进入睡眠态，不再是清醒档位")

    # ---- 休眠 ----
    sleep = cfg.get("sleep") or {}
    thr = sleep.get("threshold")
    if not isinstance(thr, (int, float)) or not (0 < thr < 1):
        errs.append("sleep.threshold 必须在 (0, 1) 之间")
    else:
        # 档位与睡眠态不能重叠：末档低于睡眠阈值时，该档永远无法到达
        last_thr = (levels[-1].get("threshold")
                    if (isinstance(levels, list) and levels and isinstance(levels[-1], dict))
                    else None)
        if isinstance(last_thr, (int, float)) and last_thr < thr:
            errs.append(
                f"levels 最后一档 threshold({last_thr}) 不能低于 sleep.threshold({thr})，"
                f"否则能量落在 {last_thr}~{thr} 时已经入睡，该档位永远无法到达")
    dur = sleep.get("duration_minutes")
    if isinstance(dur, (int, float)):
        dur = [dur, dur]
    dur_errs = _num_pair(dur, "sleep.duration_minutes", allow_negative=False)
    errs += dur_errs
    if not dur_errs:
        lo, hi = dur
        if lo < MIN_WINDOW_MINUTES or hi > DAY_MINUTES:
            errs.append(f"sleep.duration_minutes 需在 [{MIN_WINDOW_MINUTES}, {DAY_MINUTES}] 内")
    if not isinstance(sleep.get("recover_per_10min"), (int, float)):
        errs.append("sleep.recover_per_10min 必须是数字")
    if not isinstance(sleep.get("wake_boost"), (int, float)):
        errs.append("sleep.wake_boost 必须是数字")
    floor = sleep.get("wake_floor")
    if not isinstance(floor, (int, float)) or not (0 <= floor <= 1):
        errs.append("sleep.wake_floor 必须在 0~1 之间")

    # ---- 决策 ----
    decision = cfg.get("decision") or {}
    max_meme = decision.get("meme_max_per_reply")
    if not isinstance(max_meme, int) or max_meme < 0:
        errs.append("decision.meme_max_per_reply 必须是非负整数")
    boost = decision.get("meme_prob_at_boost")
    if not isinstance(boost, (int, float)) or boost < 0:
        errs.append("decision.meme_prob_at_boost 必须是非负数")
    k = decision.get("reply_hint_k")
    if not isinstance(k, (int, float)) or k < 0:
        errs.append("decision.reply_hint_k 必须是非负数")

    return errs


def _deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for k, v in override.items():
            out[k] = _deep_merge(base.get(k), v) if k in base else v
        return out
    return override


def _merge_levels(raw_levels: Any) -> list[dict[str, Any]]:
    """档位数组按索引与默认值合并；新增档位沿用最后一档的字段作为模板。

    兼容旧配置：旧版本把「完全不想说话」写成 threshold=0 的末档，但能量 <=
    sleep.threshold 时已经进入睡眠态，那一档永远不可达。检测到末档 threshold<=0
    时自动丢弃并告警，避免旧 energy.json 直接把校验打红。
    """
    if not isinstance(raw_levels, list) or not raw_levels:
        return [dict(lv) for lv in DEFAULT_LEVELS]
    items = list(raw_levels)
    dropped = 0
    while len(items) > 1 and isinstance(items[-1], dict):
        last = items[-1].get("threshold")
        if isinstance(last, (int, float)) and last <= 0:
            items.pop()
            dropped += 1
        else:
            break
    if dropped:
        logger.warning(
            "[energy] 已移除 threshold<=0 的末档（睡眠态不属于清醒档位），"
            "其余档位按原配置生效")
    if not items:
        return [dict(lv) for lv in DEFAULT_LEVELS]
    out: list[dict[str, Any]] = []
    for idx, lv in enumerate(items):
        base = DEFAULT_LEVELS[idx] if idx < len(DEFAULT_LEVELS) else DEFAULT_LEVELS[-1]
        out.append({**base, **(lv if isinstance(lv, dict) else {})})
    # 增删档位后「按索引继承默认文案」会串位（例如新插入的 0.90 档继承到
    # 原本 0.80 档的 label/tone），检测到这种情况时提示写全字段
    if len(items) != len(DEFAULT_LEVELS) and any(
            isinstance(it, dict) and ("label" not in it or "tone" not in it) for it in items):
        logger.warning(
            "[energy] 档位数量与默认不同，且部分档位未写 label/tone："
            "缺失字段会按索引继承默认档文案，可能出现档位名与阈值错位，建议每档写全")
    return out


def _merge_config(raw: dict[str, Any]) -> dict[str, Any]:
    """合并默认值；states 逐键合并，levels 整体替换（数组顺序敏感）"""
    cfg = dict(DEFAULTS)
    if "enabled" in raw:
        # 旧配置遗留字段：代码从不读取它，开关只认 .env 的 AIGF_ENERGY_ENABLED
        logger.warning(
            "[energy] energy.json 里的 enabled 字段无效（已废弃），"
            "开关请改 .env 的 AIGF_ENERGY_ENABLED")
    cfg.update({k: v for k, v in raw.items()
                if k not in ("states", "sleep", "decision", "levels", "enabled")})
    cfg["states"] = {
        key: {**DEFAULT_STATES[key], **((raw.get("states") or {}).get(key) or {})}
        for key in STATE_KEYS
    }
    cfg["levels"] = _merge_levels(raw.get("levels"))
    cfg["sleep"] = {**DEFAULTS["sleep"], **(raw.get("sleep") or {})}
    cfg["decision"] = {**DEFAULTS["decision"], **(raw.get("decision") or {})}
    if "tired_block_non_at" in (raw.get("decision") or {}):
        # 0.5.2 及以前的行为：疲倦期非 @ 硬拦截。0.5.3 起只有睡眠态拦截。
        logger.warning(
            "[energy] energy.json 里的 decision.tired_block_non_at 已废弃"
            "（疲倦期不再硬拦截，该字段会被忽略），可从 energy.json 中删除")
    return cfg


def load_config(*, create: bool = True) -> dict[str, Any]:
    """读取并校验 energy.json（缺失时按默认值生成），非法则抛 EnergyConfigError"""
    path = config_path()
    if not path.exists():
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(DEFAULTS, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.info(f"[energy] 已生成默认配置：{path}")
        return json.loads(json.dumps(DEFAULTS))

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise EnergyConfigError([f"energy.json 不是合法 JSON：{exc}"]) from exc

    cfg = _merge_config(raw)
    errs = validate_config(cfg)
    if errs:
        raise EnergyConfigError(errs)
    return cfg


def get_config() -> dict[str, Any]:
    """带缓存的配置读取；文件变更自动重载。校验失败时回退默认值并告警。"""
    global _cfg_cache, _cfg_mtime
    path = config_path()
    try:
        mtime = path.stat().st_mtime if path.exists() else 0.0
    except OSError:
        mtime = 0.0
    if _cfg_cache is not None and mtime == _cfg_mtime:
        return _cfg_cache
    try:
        cfg = load_config()
    except EnergyConfigError as exc:
        logger.error(f"[energy] 配置校验失败，回退默认值：{exc.errors}")
        cfg = json.loads(json.dumps(DEFAULTS))
    _cfg_cache, _cfg_mtime = cfg, mtime
    return cfg


def reload_config() -> dict[str, Any]:
    """强制重载（供「重载节律」指令）；失败时抛 EnergyConfigError，调用方保留旧配置"""
    global _cfg_cache
    _cfg_cache = None
    return get_config()


def now(cfg: dict[str, Any] | None = None) -> datetime:
    """按配置时区返回"带时区"的当前时间（默认沿用 AIGF_TIMEZONE_OFFSET）

    注意：必须用 timezone-aware 的写法，不能用 datetime.now() + 偏移——
    否则在系统时区本身就是 UTC+8 的机器上会偏移两次（多推 8 小时）。
    """
    cfg = cfg or get_config()
    offset = cfg.get("timezone_offset")
    if offset is None:
        offset = plugin_config.aigf_timezone_offset
    tz = timezone(timedelta(hours=float(offset or 0)))
    return datetime.now(timezone.utc).astimezone(tz)


# --------------------------- 状态与档位解析 --------------------------- #


def resolve_state(cfg: dict[str, Any], when: datetime) -> str:
    """按时间窗解析周期状态（不含休眠）"""
    minute = when.hour * 60 + when.minute
    for key in STATE_KEYS:
        for start, end in _window_spans(list(cfg["states"][key]["window"])):
            if start <= minute < end:
                return key
    return "normal"  # 理论上不可达（校验保证全覆盖）


def next_boundary(cfg: dict[str, Any], when: datetime) -> tuple[str, int]:
    """返回 (下一个状态 key, 距切换的分钟数)"""
    current = resolve_state(cfg, when)
    minute = when.hour * 60 + when.minute
    for delta in range(1, DAY_MINUTES + 1):
        candidate = (minute + delta) % DAY_MINUTES
        probe = when.replace(hour=candidate // 60, minute=candidate % 60)
        nxt = resolve_state(cfg, probe)
        if nxt != current:
            return nxt, delta
    return current, DAY_MINUTES


def resolve_level(cfg: dict[str, Any], energy: float) -> dict[str, Any]:
    """按能量值解析档位（阈值从高到低，取第一个满足 energy >= threshold 的档）"""
    for level in cfg["levels"]:
        if energy >= float(level["threshold"]):
            return level
    return cfg["levels"][-1]


def reply_hint(cfg: dict[str, Any], energy: float) -> float:
    """估算"非 @ 消息会回复"的倾向（仅用于 prompt 提示，不作硬性判定）"""
    level = resolve_level(cfg, energy)
    k = float(cfg["decision"].get("reply_hint_k") or 0)
    prob = float(level["reply_base"]) + k * (energy - 0.5)
    return max(0.0, min(float(cfg["decision"].get("reply_hint_max") or 0.95), prob))


def meme_plan(cfg: dict[str, Any], energy: float, *, is_at: bool = False,
              rng: random.Random | None = None) -> dict[str, Any]:
    """表情包概率前置判定：返回 {allowed, prob, candidates, label}"""
    rng = rng or random
    level = resolve_level(cfg, energy)
    prob = float(level["meme_prob"]) + (
        float(cfg["decision"].get("meme_prob_at_boost") or 0) if is_at else 0.0)
    prob = max(0.0, min(1.0, prob))
    return {
        "allowed": rng.random() < prob,
        "prob": prob,
        "candidates": int(level["meme_candidates"]),
        "label": level["label"],
    }


# --------------------------- 持久化 --------------------------- #


@dataclass
class EnergyState:
    energy: float = INITIAL_ENERGY
    sleep_until: float | None = None
    drowsy_until: float | None = None
    updated_at: float = 0.0

    def is_sleeping(self, at: float | None = None) -> bool:
        return bool(self.sleep_until and (at or time.time()) < self.sleep_until)

    def is_drowsy(self, at: float | None = None) -> bool:
        return bool(self.drowsy_until and (at or time.time()) < self.drowsy_until)


def _state_path(group_id: int | str) -> Path:
    return DATA_DIR / f"{group_id}.json"


def load_state(group_id: int | str, cfg: dict[str, Any] | None = None,
               when: datetime | None = None) -> EnergyState:
    """读取群能量状态；离线期间只结算「睡眠恢复」，其余增量仍需消息触发"""
    cfg = cfg or get_config()
    when = when or now(cfg)
    ts = when.timestamp()
    path = _state_path(group_id)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            st = EnergyState(
                energy=float(data.get("energy", INITIAL_ENERGY)),
                sleep_until=data.get("sleep_until"),
                drowsy_until=data.get("drowsy_until"),
                updated_at=float(data.get("updated_at") or ts),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[energy] 状态文件损坏，重建：{path}（{exc}）")
            st = EnergyState(updated_at=ts)
    else:
        st = EnergyState(updated_at=ts)

    sleep_cfg = cfg["sleep"]
    changed = False
    if st.sleep_until:
        if ts < st.sleep_until:
            elapsed = max(0.0, ts - st.updated_at)
            st.energy = min(1.0, st.energy + sleep_cfg["recover_per_10min"] * elapsed / 600)
            st.updated_at = ts
            changed = True
        else:
            elapsed = max(0.0, st.sleep_until - st.updated_at)
            st.energy = min(1.0, st.energy + sleep_cfg["recover_per_10min"] * elapsed / 600)
            st.energy = max(st.energy, float(sleep_cfg["wake_floor"]))
            st.sleep_until = None
            st.updated_at = ts
            changed = True
            logger.info(f"[energy] 群 {group_id} 自然醒，能量 {st.energy:.2f}")
    elif st.updated_at == 0:
        st.updated_at = ts
        changed = True

    if changed:
        save_state(group_id, st)
    return st


def save_state(group_id: int | str, st: EnergyState) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = _state_path(group_id)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(st), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# --------------------------- 结算 --------------------------- #


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def settle_idle(cfg: dict[str, Any], st: EnergyState, state: str, *,
                is_at: bool = False, rng: random.Random | None = None) -> float:
    """一批消息处理后未回复（或被 @）的能量变化，返回变化量"""
    rng = rng or random
    info = cfg["states"][state]
    if is_at:
        delta = float(info["at_boost"])
        if st.is_drowsy():
            delta *= 0.5  # 困倦期兴奋不起来
    else:
        lo, hi = info["idle_gain"]
        delta = rng.uniform(lo, hi)
    noise = float(cfg.get("noise") or 0)
    if noise:
        delta += rng.uniform(-noise, noise)
    st.energy = _clamp(st.energy + delta)
    st.updated_at = time.time()
    return delta


def settle_reply(cfg: dict[str, Any], st: EnergyState, state: str, text_length: int, *,
                 rng: random.Random | None = None) -> float:
    """一次回复的能量消耗（长度因子让长回复更费），返回消耗量"""
    rng = rng or random
    lo, hi = cfg["states"][state]["reply_cost"]
    factor = 0.8 + 0.4 * min(1.0, max(0, text_length) / 60)
    cost = rng.uniform(lo, hi) * factor
    st.energy = _clamp(st.energy - cost)
    st.updated_at = time.time()
    return cost


def enter_sleep(cfg: dict[str, Any], st: EnergyState, *,
                rng: random.Random | None = None, when: datetime | None = None) -> int:
    """进入休眠，返回睡眠分钟数（区间内随机）"""
    rng = rng or random
    lo, hi = cfg["sleep"]["duration_minutes"]
    minutes = int(round(rng.uniform(lo, hi)))
    base = (when.timestamp() if when else time.time())
    st.sleep_until = base + minutes * 60
    st.updated_at = base
    logger.info(f"[energy] 进入休眠 {minutes} 分钟（能量 {st.energy:.2f}）")
    return minutes


def wake(cfg: dict[str, Any], st: EnergyState, *, when: datetime | None = None) -> float:
    """被 @ 强制唤醒：清休眠、加精力、进入困倦期，返回提升量"""
    at = (when.timestamp() if when else time.time())
    boost = float(cfg["sleep"]["wake_boost"])
    st.energy = _clamp(st.energy + boost)
    st.sleep_until = None
    st.drowsy_until = at + float(cfg["sleep"]["drowsy_minutes"]) * 60
    st.updated_at = at
    logger.info(f"[energy] 被唤醒，能量 {st.energy:.2f}，困倦 {cfg['sleep']['drowsy_minutes']} 分钟")
    return boost


def should_block(cfg: dict[str, Any], st: EnergyState, state: str, *,
                 is_at: bool, when: datetime | None = None) -> str:
    """是否需要硬拦截（不调 LLM）。返回原因：'sleep' / ''

    只有睡眠态会硬拦截；时间片（兴奋/平淡/疲倦）只影响能量结算与语气，
    不丢弃任何消息。state 参数保留是为兼容调用方签名（当前不参与判定）。
    """
    at = (when.timestamp() if when else time.time())
    sleep_cfg = cfg["sleep"]
    if st.sleep_until and at < st.sleep_until:
        if is_at or not sleep_cfg.get("block_non_at", True):
            return ""
        return "sleep"
    return ""


# --------------------------- 展示 --------------------------- #


def status_text(cfg: dict[str, Any], st: EnergyState, group_id: int | str,
                when: datetime | None = None) -> str:
    """给「状态」指令用的节律描述"""
    when = when or now(cfg)
    at = when.timestamp()
    if st.sleep_until and at < st.sleep_until:
        left = int((st.sleep_until - at) // 60)
        return (f"休眠中，剩 {left // 60} 小时 {left % 60} 分"
                f"（阈值 {cfg['sleep']['threshold']:.2f}，被 @ 可唤醒 +{cfg['sleep']['wake_boost']:.2f}）")

    state = resolve_state(cfg, when)
    level = resolve_level(cfg, st.energy)
    nxt, minutes = next_boundary(cfg, when)
    text = (f"能量 {st.energy:.2f}（{level['label']} · {cfg['states'][state]['label']}"
            f" · 距{cfg['states'][nxt]['label']}还有 {minutes // 60} 小时 {minutes % 60} 分）")
    if st.is_drowsy(at):
        text += " · 刚被叫醒，还没缓过来"
    return text


def prompt_context(cfg: dict[str, Any], st: EnergyState, state: str, *,
                   is_at: bool = False, woke_from_sleep: bool = False) -> str:
    """生成注入 prompt 的「当前状态」段落（语气 + 表情包提示 + 回复倾向）"""
    level = resolve_level(cfg, st.energy)
    lines = [
        f"当前状态：{cfg['states'][state]['label']}，能量 {st.energy:.2f}（{level['label']}）。",
        f"你现在的状态：{level['tone']}。",
        f"表情包：这段时间你配图的概率约 {level['meme_prob'] * 100:.0f}%。",
    ]
    if state == "tired":
        lines.append("现在是深夜疲倦期，你很困：说话变蔫、句子更短，但别人 @ 你还是会回应。")
    hint = reply_hint(cfg, st.energy)
    if not is_at:
        lines.append(
            f"对于没有 @ 你的消息，你大概只有 {hint * 100:.0f}% 会接话——不想回就直接不回。")
    if woke_from_sleep:
        lines.append(cfg["sleep"].get("wake_tone") or
                     "你刚才睡着了，被 @ 叫醒，先说明自己刚睡着、现在很困。")
    elif st.is_drowsy():
        lines.append("你还没睡醒，反应有点慢，句子更短。")
    return "\n".join(lines)
