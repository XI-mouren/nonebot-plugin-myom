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

"""Token 用量统计（原独立插件 nonebot-plugin-token-usage 并入本插件）

统计两种来源的 token 用量，统计日以「凌晨 4 点」为界（04:00 前计入前一天）：

来源一：单次调用明细（client.py / vlm.py 每次 LLM 调用后调用 record_call 上报）
        ——输入 tokens、输出 tokens、缓存命中 tokens、调用次数，
        配置了单价时折算花费。
来源二：API 账户总用量（每天凌晨 4 点请求 WBY 用量接口，快照差值 = 前一
        统计日的 API 增量；需安装 nonebot-plugin-apscheduler 并配置接口）。

日用量 = 两种来源合并到同一统计日条目。
周用量：星期日 -> 星期一 切换时，汇总上一周（周一~周日）的日用量。
月用量：月份变化时，汇总上一月的日用量。
清理：日用量保留「当前月 + 上一个整月」，月份再向前变化一次时删除更早的
  日用量（例如 十月 -> 十一月 -> 十二月 时，删除全部十月日用量）。
周用量、月用量汇总后永久保留，不做清理。

指令（priority=0 + block=True，不会泄漏给 auto_chat 当成群聊）：
- 日用量 [n] / 周用量 [n] / 月用量 [n] / 总用量 / 用量价格 / 用量配置 / 用量帮助
"""
from __future__ import annotations

import json
import re
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import httpx
from nonebot import get_driver, logger, on_command, require
from nonebot.adapters import Message
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.rule import to_me
from nonebot.typing import T_State

from .config import plugin_config
from .security import check_leak, log_leak
import nonebot_plugin_localstore as store

# ---- 可选依赖：定时任务（缺失时仅 API 总量快照不可用，单次调用统计不受影响） ----
try:
    scheduler = require("nonebot_plugin_apscheduler").scheduler
    _HAS_SCHEDULER = True
except Exception as e:
    scheduler = None
    _HAS_SCHEDULER = False
    logger.warning(f"[usage] 未安装 nonebot-plugin-apscheduler，API 总量快照不可用: {e}")

# ---- 存储：配置放 config 目录，数据放 data 目录（与 presets 等保持一致）----
USAGE_DIR = store.get_plugin_data_dir() / "usage"          # 数据：records.json
CONFIG_DIR = store.get_plugin_config_dir() / "usage"       # 配置：config.json
CONFIG_FILE = CONFIG_DIR / "config.json"
RECORDS_FILE = USAGE_DIR / "records.json"

_lock = threading.RLock()

EMPTY_RECORDS: dict[str, Any] = {
    # 最近一次请求快照 {"date","total_used","total_available","total_granted","name"}
    "snapshot": None,
    # [{"date","calls","input","output","cached_input","cost_input","cost_output","api_used"}]
    "daily": [],
    # [{"month", + 同日字段（不含 date）}]
    "monthly": [],
    # [{"end","start", + 同日字段（不含 date）}]
    "weekly": [],
}

_SUM_FIELDS = ("calls", "input", "output", "cached_input", "api_used")
_COST_FIELDS = ("cost_input", "cost_output")

_record_lock = threading.RLock()


# --------------------------- 存储层 --------------------------- #


_corrupt_warned: set[tuple[str, float, float]] = set()


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        # utf-8-sig：兼容 Windows 记事本保存时写入的 BOM（无 BOM 时等价 utf-8）
        return json.loads(path.read_text("utf-8-sig"))
    except Exception as e:
        # 不能静默按空数据处理：调用方随后 save 会把损坏文件覆盖成空记录，
        # 历史统计永久丢失。先把坏文件原样备份，再降级为空数据。
        try:
            st = path.stat()
            sig = (str(path), st.st_mtime, float(st.st_size))
        except OSError:
            sig = (str(path), 0.0, -1.0)
        if sig not in _corrupt_warned:
            _corrupt_warned.add(sig)
            try:
                bad = path.with_name(path.name + ".corrupt")
                bad.write_bytes(path.read_bytes())
                logger.error(
                    f"[usage] {path.name} 解析失败（{type(e).__name__}: {e}），"
                    f"已备份为 {bad.name}，本次按空数据处理")
            except Exception as be:
                logger.error(
                    f"[usage] {path.name} 解析失败（{type(e).__name__}: {e}），"
                    f"且备份失败: {be}")
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    tmp.replace(path)


def load_file_config() -> dict[str, Any]:
    """仅返回 config.json 中的键（不含 .env 默认值）。

    写操作用它做「读-改-写」，避免把 .env 的键固化进 config.json，
    否则用指令改过一次配置后，改 .env 将不再生效。
    """
    with _lock:
        data = _read_json(CONFIG_FILE, {})
    if not isinstance(data, dict):
        # 手编成数组/标量时不能让上层 .items() 崩掉（否则统计与查询全挂）
        logger.warning(f"[usage] {CONFIG_FILE.name} 不是 JSON 对象，已忽略")
        return {}
    return data


def load_config() -> dict[str, Any]:
    """读取用量配置：.env 的 AIGF_USAGE_* 作为默认值，config.json 中的同名键覆盖。

    config.json 由指令（用量配置/用量价格）与手工编辑维护，
    因此「.env 管静态默认、config.json 管运行期调整」，两者都可用。
    """
    file_cfg = load_file_config()

    env_cfg: dict[str, Any] = {
        "base": plugin_config.aigf_usage_base,
        "key": plugin_config.aigf_usage_key,
        "url": plugin_config.aigf_usage_url,
        "method": plugin_config.aigf_usage_method,
        "auth_header": plugin_config.aigf_usage_auth_header,
        "body": plugin_config.aigf_usage_body,
        "fields": plugin_config.aigf_usage_fields,
        "price_input": plugin_config.aigf_usage_price_input,
        "price_output": plugin_config.aigf_usage_price_output,
        "quota_per_yuan": plugin_config.aigf_usage_quota_per_yuan,
    }
    merged: dict[str, Any] = {
        k: v for k, v in env_cfg.items() if v not in (None, "", 0, 0.0)
    }
    for k, v in (file_cfg or {}).items():
        if v not in (None, ""):
            merged[k] = v

    # fields 允许 JSON 字符串（.env 写法），统一转成 dict
    fields = merged.get("fields")
    if isinstance(fields, str):
        try:
            merged["fields"] = json.loads(fields) if fields.strip() else None
        except Exception:
            merged["fields"] = None
    # body 允许 JSON 字符串（.env 写法）或 JSON 对象（config.json 里的自然写法）；
    # 统一成字符串，避免 _fetch_usage 里 json.loads(str(dict)) 必然失败。
    body = merged.get("body")
    if isinstance(body, (dict, list)):
        merged["body"] = json.dumps(body, ensure_ascii=False)
    return merged


def save_config(cfg: dict[str, Any]) -> None:
    global _price_cache
    with _lock:
        _write_json(CONFIG_FILE, cfg)
    _price_cache = None  # 改配置后让单价缓存失效，避免沿用旧价格


# --------------------------- 记录结构清洗 --------------------------- #
# 目的：records.json / config.json 是纯文本，用户可能手改或写到一半被中断。
# 单条坏数据不能让「每日汇总/清理」永久失败，也不能让查询指令整体抛异常。

_COUNTER_FIELDS = ("calls", "input", "output", "cached_input", "api_used", "used")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")


def _valid_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _valid_month(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    m = _MONTH_RE.match(value)
    return bool(m) and 1 <= int(m.group(2)) <= 12


def _as_int(value: Any) -> int:
    """接口返回值/手改值转 int：容忍千分位、小数、空白，失败返回 0。

    中转站常把额度返回成字符串（"1,234" / "1234.5"），直接 int() 会抛异常，
    让整个用量解析失败、当天增量丢失。
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        try:
            return int(value)
        except (ValueError, OverflowError):
            return 0
    if isinstance(value, str):
        s = value.strip().replace(",", "").replace("_", "").replace(" ", "")
        if not s:
            return 0
        try:
            return int(float(s))
        except (ValueError, OverflowError):
            return 0
    return 0


def _as_float(value: Any, default: float = 0.0) -> float:
    """同 _as_int，转为 float；失败返回 default（用于单价/换算系数）"""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        try:
            return float(value)
        except (ValueError, OverflowError):
            return default
    if isinstance(value, str):
        s = value.strip().replace(",", "").replace("_", "").replace(" ", "")
        if not s:
            return default
        try:
            return float(s)
        except (ValueError, OverflowError):
            return default
    return default


def _as_bool(value: Any) -> bool:
    """字段映射转 bool：字符串 "false"/"0"/"no" 不能被当成 True"""
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "no", "none", "null")
    return bool(value)


def _norm_counter_block(d: dict) -> dict:
    """把计数/花费字段强制转成数字，顺便清洗嵌套的 sources 子表"""
    for f in _COUNTER_FIELDS:
        if f in d:
            try:
                d[f] = int(d[f] or 0)
            except (TypeError, ValueError):
                d[f] = 0
    for f in _COST_FIELDS:
        if f in d:
            try:
                d[f] = float(d[f] or 0.0)
            except (TypeError, ValueError):
                d[f] = 0.0
    if "sources" in d:
        d["sources"] = _norm_subtable(d["sources"])
    return d


def _norm_subtable(tbl: Any) -> dict[str, dict]:
    """groups / sources 这类「键 → 计数块」的子表：键统一为字符串，值必须是 dict"""
    if not isinstance(tbl, dict):
        return {}
    out: dict[str, dict] = {}
    for k, v in tbl.items():
        if isinstance(v, dict):
            out[str(k)] = _norm_counter_block(v)
    return out


def _sanitize_records(raw: dict) -> tuple[dict[str, Any], int, int]:
    """把记录规整成预期形状，返回 (数据, 被丢弃的条目数, 被合并的重复条目数)。

    - daily：必须含可解析的 date；groups/sources 逐层清洗；同一天重复条目累加
    - weekly：必须含可解析的 start / end；同一周重复条目累加
    - monthly：month 必须形如 YYYY-MM；同一月重复条目累加
    - snapshot：必须是 dict 或 None；total_used 必须可转 int，否则丢弃
    """
    dropped = 0
    merged_dup = 0

    daily_raw = raw.get("daily")
    if not isinstance(daily_raw, list):
        if daily_raw not in (None, []):
            dropped += 1
        daily_raw = []
    daily: list[dict] = []
    for r in daily_raw:
        if not isinstance(r, dict) or not _valid_date(r.get("date")):
            dropped += 1
            continue
        r["groups"] = _norm_subtable(r.get("groups"))
        r.setdefault("sources", {})
        r["sources"] = _norm_subtable(r.get("sources"))
        daily.append(_norm_counter_block(r))
    # 同一天出现多条（手改、旧版本并发写）时不能靠 dict 建索引——那会静默丢数据
    merged_daily: dict[str, dict] = {}
    for r in daily:
        prev = merged_daily.get(r["date"])
        if prev is None:
            merged_daily[r["date"]] = r
            continue
        _sum_entry(prev, r)
        merged_dup += 1
    raw["daily"] = sorted(merged_daily.values(), key=lambda r: r["date"])

    weekly_raw = raw.get("weekly")
    if not isinstance(weekly_raw, list):
        if weekly_raw not in (None, []):
            dropped += 1
        weekly_raw = []
    weekly: list[dict] = []
    for w in weekly_raw:
        if (not isinstance(w, dict) or not _valid_date(w.get("start"))
                or not _valid_date(w.get("end"))):
            dropped += 1
            continue
        w["groups"] = _norm_subtable(w.get("groups"))
        w["sources"] = _norm_subtable(w.get("sources"))
        weekly.append(_norm_counter_block(w))
    merged_weekly: dict[str, dict] = {}
    for w in weekly:
        prev = merged_weekly.get(w["end"])
        if prev is None:
            merged_weekly[w["end"]] = w
            continue
        _sum_entry(prev, w)          # 同一周重复条目累加，而不是被 dict 覆盖
        merged_dup += 1
    raw["weekly"] = sorted(merged_weekly.values(), key=lambda w: w["end"])

    monthly_raw = raw.get("monthly")
    if not isinstance(monthly_raw, list):
        if monthly_raw not in (None, []):
            dropped += 1
        monthly_raw = []
    monthly: list[dict] = []
    for m in monthly_raw:
        if not isinstance(m, dict) or not _valid_month(m.get("month")):
            dropped += 1
            continue
        m["groups"] = _norm_subtable(m.get("groups"))
        m["sources"] = _norm_subtable(m.get("sources"))
        monthly.append(_norm_counter_block(m))
    merged_monthly: dict[str, dict] = {}
    for m in monthly:
        prev = merged_monthly.get(m["month"])
        if prev is None:
            merged_monthly[m["month"]] = m
            continue
        _sum_entry(prev, m)
        merged_dup += 1
    raw["monthly"] = sorted(merged_monthly.values(), key=lambda m: m["month"])

    snap = raw.get("snapshot")
    if snap is not None:
        if not isinstance(snap, dict):
            raw["snapshot"] = None
            dropped += 1
        elif not _valid_date(snap.get("date")):
            raw["snapshot"] = None
            dropped += 1
        else:
            # snapshot.total_used 会被 run_daily_task 直接做减法；非数字会让
            # 4 点任务每天都抛异常（汇总/清理/增量全部停摆），且坏值不会被自动修复。
            # 无法转换时丢弃快照，下个统计日重建基准（最多丢一天增量，不会算错）。
            try:
                snap["total_used"] = int(snap.get("total_used") or 0)
            except (TypeError, ValueError):
                raw["snapshot"] = None
                dropped += 1

    return raw, dropped, merged_dup


def load_records() -> dict[str, Any]:
    with _lock:
        data = _read_json(RECORDS_FILE, None)
        if not isinstance(data, dict):
            data = {}
        for k, v in EMPTY_RECORDS.items():
            if k not in data:
                # 深拷贝：避免返回的对象与 EMPTY_RECORDS 共享同一 list，
                # 后续任何就地 append 都会污染模块常量（清理时会“清不干净”）
                data[k] = json.loads(json.dumps(v))
        data, dropped, merged_dup = _sanitize_records(data)
        if dropped:
            logger.warning(
                f"[usage] 已忽略 {dropped} 条结构损坏的用量记录"
                f"（{RECORDS_FILE.name}），其余记录正常使用")
        if merged_dup:
            logger.warning(
                f"[usage] 已合并 {merged_dup} 条重复的用量记录"
                f"（{RECORDS_FILE.name}，同一天/同一周/同一月出现多条）")
        return data


def save_records(records: dict[str, Any]) -> None:
    with _lock:
        _write_json(RECORDS_FILE, records)


# --------------------------- 统计日与工具 --------------------------- #

_STAT_DAY_SHIFT = timedelta(hours=4)  # 凌晨 4 点为界


def _stat_date(dt: datetime | None = None) -> date:
    """统计日：04:00 前的调用计入前一天。

    省略 dt 时按 AIGF_TIMEZONE_OFFSET 的"本地时间"取值（与 time_context 一致），
    避免服务器系统时区为 UTC 时统计日在错误时刻切分。
    """
    if dt is None:
        from .time_context import _now  # 延迟导入，避免与 time_context 循环依赖
        dt = _now()
    return (dt - _STAT_DAY_SHIFT).date()


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _fmt_month(month: str) -> str:
    try:
        y, m = str(month).split("-", 1)
        return f"{int(y)}年{int(m)}月"
    except (TypeError, ValueError):
        return str(month)          # 非法值原样显示，不让展示层崩掉


def _first_day_of_prev_month(d: date) -> date:
    first = date(d.year, d.month, 1)
    return (first - timedelta(days=1)).replace(day=1)


def _empty_day(day: str) -> dict:
    return {
        "date": day, "calls": 0, "input": 0, "output": 0,
        "cached_input": 0, "cost_input": 0.0, "cost_output": 0.0, "api_used": 0,
    }


def _merge_groups(target: dict, r: dict) -> None:
    """把一条日记录的分群子表累加进周/月聚合对象"""
    for gid, g in (r.get("groups") or {}).items():
        t = target.setdefault("groups", {}).setdefault(
            gid, {"calls": 0, "input": 0, "output": 0, "cached_input": 0,
                  "cost_input": 0.0, "cost_output": 0.0})
        for f in ("calls", "input", "output", "cached_input"):
            t[f] = t.get(f, 0) + g.get(f, 0)
        for f in ("cost_input", "cost_output"):
            t[f] = round(t.get(f, 0.0) + g.get(f, 0.0), 6)
        for name, sv in (g.get("sources") or {}).items():
            ts = t.setdefault("sources", {}).setdefault(
                name, {"calls": 0, "input": 0, "output": 0, "cached_input": 0})
            for f in ("calls", "input", "output", "cached_input"):
                ts[f] = ts.get(f, 0) + sv.get(f, 0)


def _sum_entry(target: dict, r: dict) -> None:
    """把一条日记录累加进周/月聚合对象（兼容 legacy "used" 字段）"""
    for f in _SUM_FIELDS:
        if f == "api_used":
            target[f] = target.get(f, 0) + r.get("api_used", r.get("used", 0))
        else:
            target[f] = target.get(f, 0) + r.get(f, 0)
    for f in _COST_FIELDS:
        target[f] = round(target.get(f, 0.0) + r.get(f, 0.0), 6)
    _merge_groups(target, r)
    for name, sv in (r.get("sources") or {}).items():
        t = target.setdefault("sources", {}).setdefault(
            name, {"calls": 0, "input": 0, "output": 0, "cached_input": 0})
        for f in ("calls", "input", "output", "cached_input"):
            t[f] = t.get(f, 0) + sv.get(f, 0)


def _hit_rate(r: dict) -> float:
    inp = r.get("input", 0)
    return (r.get("cached_input", 0) / inp * 100) if inp else 0.0


def _fmt_entry(prefix: str, r: dict) -> str:
    """把一条日/周/月记录格式化为多行文本"""
    rate = _hit_rate(r)
    lines = [
        f"{prefix}输入 {r.get('input', 0):,}"
        f"（缓存命中 {r.get('cached_input', 0):,} / {rate:.1f}%）"
        f"· 输出 {r.get('output', 0):,} · 调用 {r.get('calls', 0)} 次"
    ]
    # 两套账各自独立：① token 口径花费（单价 × tokens）② 额度口径花费（站方 quota 折算）
    ci, co = r.get("cost_input", 0.0), r.get("cost_output", 0.0)
    if ci or co:
        lines.append(
            f"　　└ 花费（token 口径）：输入 ¥{ci:.4f} · 输出 ¥{co:.4f} · 合计 ¥{ci + co:.4f}")
    api = r.get("api_used", r.get("used", 0))
    if api:
        lines.append(f"　　└ 额度消耗（API 口径）：{api:,}")
        ratio = _as_float(load_config().get("quota_per_yuan"))
        if ratio > 0:
            lines.append(f"　　　└ 折算 ≈ ¥{api / ratio:.4f}（{ratio:,.0f} 额度/元）")
    sources = r.get("sources") or {}
    if len(sources) > 1 or (sources and "chat" not in sources):
        parts = []
        for name, label in (("chat", "对话"), ("vlm", "看图")):
            sv = sources.get(name)
            if sv:
                parts.append(f"{label} {sv.get('input', 0):,} 入 / {sv.get('output', 0):,} 出（{sv.get('calls', 0)} 次）")
        if parts:
            lines.append("　　└ 来源拆分：" + " · ".join(parts))
    return "\n".join(lines)


# --------------------------- 单次调用上报（client.py / vlm.py 调用） --------------------------- #


_price_cache: tuple[float, float] | None = None   # (mtime, size)
_price_cfg: dict[str, Any] | None = None


def _pricing() -> tuple[float, float]:
    """单价读取（带 mtime+size 缓存，避免每次调用都读文件）"""
    global _price_cache, _price_cfg
    try:
        st = CONFIG_FILE.stat()
        stamp = (st.st_mtime, float(st.st_size))
    except OSError:
        # config.json 不存在：仍要走 load_config()，这样 .env 里配的单价才会生效
        stamp = (0.0, -1.0)
    if _price_cache != stamp:
        _price_cfg = load_config()
        _price_cache = stamp
    cfg = _price_cfg or {}
    price_in = _as_float(cfg.get("price_input"))
    price_out = _as_float(cfg.get("price_output"))
    # 配置写成 "2元" 这类非数字时不能让 record_call 抛异常：
    # client.py 会吞掉异常，结果这一整次调用的 token 都不记账。
    for name, raw, val in (("price_input", cfg.get("price_input"), price_in),
                           ("price_output", cfg.get("price_output"), price_out)):
        if raw not in (None, "", 0, 0.0) and val == 0:
            logger.warning(
                f"[usage] 配置项 {name}={raw!r} 不是数字，已按 0 处理（本次仍照常记录 token）")
    return price_in, price_out


def record_call(input_tokens: int, output_tokens: int, cached_tokens: int = 0,
                group_id: str | None = None, source: str = "chat") -> None:
    """每次 LLM/VLM 调用完成后上报用量。

    按「凌晨 4 点为界」的统计日归档；配置了单价时同步折算花费。
    cached_tokens 指输入中被缓存命中的部分（输出不参与缓存，恒为 0 命中）。
    group_id 提供时，同一条日记录内按群拆分明细（groups 子表），支持分群排行。
    source 标记调用来源（chat=对话/搜索分析，vlm=看图），用于来源拆分核对口径。
    """
    input_tokens = max(0, _as_int(input_tokens))
    output_tokens = max(0, _as_int(output_tokens))
    cached_tokens = max(0, min(_as_int(cached_tokens), input_tokens))
    day = _stat_date().isoformat()

    price_in, price_out = _pricing()
    cost_in = round(input_tokens / 1_000_000 * price_in, 6)
    cost_out = round(output_tokens / 1_000_000 * price_out, 6)

    with _record_lock:
        records = load_records()
        daily = {r["date"]: r for r in records["daily"]}
        entry = daily.setdefault(day, _empty_day(day))
        entry["calls"] = entry.get("calls", 0) + 1
        entry["input"] = entry.get("input", 0) + input_tokens
        entry["output"] = entry.get("output", 0) + output_tokens
        entry["cached_input"] = entry.get("cached_input", 0) + cached_tokens
        entry["cost_input"] = round(entry.get("cost_input", 0.0) + cost_in, 6)
        entry["cost_output"] = round(entry.get("cost_output", 0.0) + cost_out, 6)
        if group_id:
            gid = str(group_id)
            groups = entry.setdefault("groups", {})
            g = groups.setdefault(gid, {"calls": 0, "input": 0, "output": 0,
                                        "cached_input": 0, "cost_input": 0.0, "cost_output": 0.0})
            g["calls"] = g.get("calls", 0) + 1
            g["input"] = g.get("input", 0) + input_tokens
            g["output"] = g.get("output", 0) + output_tokens
            g["cached_input"] = g.get("cached_input", 0) + cached_tokens
            g["cost_input"] = round(g.get("cost_input", 0.0) + cost_in, 6)
            g["cost_output"] = round(g.get("cost_output", 0.0) + cost_out, 6)
            # 分群内再按用途（对话/看图）拆分，用于「群用量」查看明细
            gs = g.setdefault("sources", {}).setdefault(
                source, {"calls": 0, "input": 0, "output": 0, "cached_input": 0})
            gs["calls"] = gs.get("calls", 0) + 1
            gs["input"] = gs.get("input", 0) + input_tokens
            gs["output"] = gs.get("output", 0) + output_tokens
            gs["cached_input"] = gs.get("cached_input", 0) + cached_tokens
        # 来源拆分：chat（对话/搜索分析）、vlm（看图）
        sources = entry.setdefault("sources", {})
        s = sources.setdefault(source, {"calls": 0, "input": 0, "output": 0, "cached_input": 0})
        s["calls"] = s.get("calls", 0) + 1
        s["input"] = s.get("input", 0) + input_tokens
        s["output"] = s.get("output", 0) + output_tokens
        s["cached_input"] = s.get("cached_input", 0) + cached_tokens
        records["daily"] = sorted(daily.values(), key=lambda r: r["date"])
        save_records(records)


# --------------------------- API 总量快照 --------------------------- #


async def _fetch_usage(cfg: dict) -> dict:
    """按配置请求用量接口，返回归一化的 {total_used, total_available, total_granted, unlimited_quota, name}。

    - cfg["url"] 未配置：回退 WBY 风格（base + /api/usage/token/，Bearer 认证）
    - cfg["url"] 配置了自定义模板：支持 {base}/{key} 占位符（{key} 在 URL 中会被 URL 编码）；
      method 可选 GET/POST（POST 时可用 cfg["body"] 提供 JSON 模板，同样支持占位符）
    - 认证：cfg["auth_header"] 模板（如 "Bearer {key}" 或 "Token {key}"），缺省 Bearer {key}
    - 响应字段：cfg["fields"] 提供点号路径映射（如 {"total_used": "data.total_used"}），
      缺省自动识别 WBY（data.*）与 one-api 风格（顶层同名字段）
    """
    key = str(cfg.get("key") or "")
    base = str(cfg.get("base") or "")
    url_template = str(cfg.get("url") or "")
    if not url_template:
        if not base:
            raise RuntimeError("未配置用量接口（base 或 url）")
        url_template = base.rstrip("/") + "/api/usage/token/"
    url = url_template.replace("{base}", base).replace("{key}", quote_plus(key))
    method = str(cfg.get("method") or "GET").upper()
    if method not in ("GET", "POST"):
        logger.warning(f"[usage] 未知请求方法 {method}，已回退 GET")
        method = "GET"

    headers = {"Content-Type": "application/json"}
    auth_header = cfg.get("auth_header")
    if auth_header:
        if key:
            headers["Authorization"] = str(auth_header).replace("{key}", key)
    elif key:
        headers["Authorization"] = f"Bearer {key}"

    async with httpx.AsyncClient(timeout=15.0, headers=headers) as client:
        if method == "POST":
            payload = None
            body_template = cfg.get("body")
            if body_template:
                payload = json.loads(str(body_template).replace("{key}", key).replace("{base}", base))
            resp = await client.post(url, json=payload)
        else:
            resp = await client.get(url)
        resp.raise_for_status()
        return _parse_usage_payload(resp.json(), cfg.get("fields"))


def _parse_usage_payload(payload: dict, fields: dict | None) -> dict:
    """解析用量接口响应：fields 提供点号路径映射，缺省自动识别常见格式"""

    def extract(path):
        if not path:
            return None
        node = payload
        for part in str(path).split("."):
            if isinstance(node, dict) and part in node:
                node = node[part]
            else:
                return None
        return node

    if fields:
        return {
            "total_used": _as_int(extract(fields.get("total_used"))),
            "total_available": _as_int(extract(fields.get("total_available"))),
            "total_granted": _as_int(extract(fields.get("total_granted"))),
            "unlimited_quota": _as_bool(extract(fields.get("unlimited_quota"))),
            "name": extract(fields.get("name")) or "unknown",
        }
    # 自动识别：WBY 风格（data.* 的 total_*）与 one-api 风格（quota / used_quota）
    node = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    has_total = any(k in node for k in ("total_used", "total_available", "total_granted"))
    if not has_total and ("quota" in node or "used_quota" in node):
        used_q = _as_int(node.get("used_quota"))
        remain_q = _as_int(node.get("quota"))
        return {
            "total_used": used_q,
            "total_available": remain_q,
            "total_granted": used_q + remain_q,
            "unlimited_quota": False,
            "name": node.get("username") or node.get("name"),
        }
    return {
        "total_used": _as_int(node.get("total_used")),
        "total_available": _as_int(node.get("total_available")),
        "total_granted": _as_int(node.get("total_granted")),
        "unlimited_quota": _as_bool(node.get("unlimited_quota")),
        "name": node.get("name"),
    }


async def run_daily_task() -> None:
    """每日任务：能查 API 时记录前一统计日的 API 增量。

    关键：周/月汇总与日用量清理**不依赖** API 配置——未配置接口或请求失败时
    也必须执行，否则本地统计的周/月查询会永远为空、日用量永不清理。
    """
    cfg = load_config()
    today = _stat_date()

    data = None
    if cfg.get("base") or cfg.get("url"):
        try:
            data = await _fetch_usage(cfg)
        except Exception as e:
            logger.error(f"[usage] 用量接口请求失败: {e}")
    else:
        logger.debug("[usage] 未配置用量接口，仅维护本地统计的汇总与清理")

    # 必须在 await 之后再读记录：若提前读取，fetch 期间（最长 15s）record_call
    # 写入的调用会被下面 save_records 的旧副本覆盖，造成统计丢失。
    records = load_records()

    if data:
        total_used = data["total_used"]
        # 快照差值覆盖「上一统计日 04:00 → 今天 04:00」= 上一统计日的完整窗口，
        # 归属于上一统计日（4 点整时 _stat_date() 已翻到今天，因此必须 -1 天）
        day = _stat_date() - timedelta(days=1)
        snap = records["snapshot"]
        new_snap = {
            "date": day.isoformat(),
            "total_used": total_used,
            "total_available": data.get("total_available"),
            "total_granted": data.get("total_granted"),
            "name": data.get("name"),
        }
        # 该统计日已记录过，避免重复（配合启动补录的幂等性）
        if snap and snap.get("date") == day.isoformat():
            pass
        elif snap is None:
            records["snapshot"] = new_snap
            logger.info("[usage] 已记录基准快照，从下个统计日起产生 API 增量")
        else:
            used = total_used - _as_int(snap.get("total_used"))
            if used < 0:
                logger.warning(
                    f"[usage] {day} API 增量为负（{used}），疑似额度重置/更换密钥，已按 0 记录")
                used = 0
            daily = {r["date"]: r for r in records["daily"]}
            entry = daily.setdefault(day.isoformat(), _empty_day(day.isoformat()))
            entry["api_used"] = used
            records["daily"] = sorted(daily.values(), key=lambda r: r["date"])
            records["snapshot"] = new_snap
            logger.info(f"[usage] 已记录 {day} API 增量 {used}，账户累计 {total_used}")
    try:
        _aggregate_months(records, today)
        _aggregate_weeks(records, today)
        _cleanup_daily(records, today)
    except Exception as e:  # noqa: BLE001
        # 汇总/清理失败不应影响 API 快照落盘，也不能让任务从此每天失败
        logger.error(f"[usage] 汇总/清理失败（本次跳过，已记录的增量仍会保存）: {e}")
    save_records(records)


def _aggregate_months(records: dict, today: date) -> None:
    """已结束的月份（早于当前月）若尚未汇总，则按日用量求和写入月用量。"""
    cur_month = _month_key(today)
    monthly = {m["month"]: m for m in records["monthly"]}
    by_month: dict[str, dict] = {}
    for r in records["daily"]:
        mk = _month_key(date.fromisoformat(r["date"]))
        agg = by_month.setdefault(mk, {"month": mk})
        _sum_entry(agg, r)

    changed = False
    for mk in sorted(by_month):
        if mk >= cur_month or mk in monthly:
            continue
        monthly[mk] = by_month[mk]
        changed = True
        logger.info(f"[usage] 已汇总 {_fmt_month(mk)} 月用量")
    if changed:
        records["monthly"] = [monthly[m] for m in sorted(monthly)]


def _aggregate_weeks(records: dict, today: date) -> None:
    """对"已经结束"的自然周（周一~周日）汇总周用量。

    以「该周周日是否早于今天」判定，而不是「是否有周日当天的记录」——
    否则某个周日全程没人说话时，那一周永远不会被汇总。
    """
    weekly = {w["end"]: w for w in records["weekly"]}
    daily_by_date = {r["date"]: r for r in records["daily"]}

    week_ends: dict[str, date] = {}
    for ds in daily_by_date:
        d = date.fromisoformat(ds)
        end = d + timedelta(days=6 - d.weekday())
        week_ends[end.isoformat()] = end

    changed = False
    for end_s, end in sorted(week_ends.items()):
        if end >= today:  # 本周尚未结束
            continue
        if end_s in weekly:
            continue
        start = end - timedelta(days=6)
        agg = {"end": end_s, "start": start.isoformat()}
        for i in range(7):
            r = daily_by_date.get((start + timedelta(days=i)).isoformat())
            if r:
                _sum_entry(agg, r)
        weekly[end_s] = agg
        changed = True
        logger.info(f"[usage] 已汇总 {start} ~ {end} 周用量")
    if changed:
        records["weekly"] = [weekly[k] for k in sorted(weekly)]


def _cleanup_daily(records: dict, today: date) -> None:
    """日用量只保留当前月与上一个整月；更早的删除（周/月汇总不受影响）。"""
    keep_from = _first_day_of_prev_month(today)
    before = len(records["daily"])
    records["daily"] = [
        r for r in records["daily"] if date.fromisoformat(r["date"]) >= keep_from
    ]
    removed = before - len(records["daily"])
    if removed:
        logger.info(f"[usage] 已清理 {keep_from} 之前的日用量记录 {removed} 条")


# --------------------------- 定时任务与补录 --------------------------- #

if _HAS_SCHEDULER:

    @scheduler.scheduled_job(
        "cron", hour=4, minute=0, id="aigf_usage_daily", misfire_grace_time=3600
    )
    async def _daily_job():
        await run_daily_task()


@get_driver().on_startup
async def _usage_startup():
    _migrate_legacy_store()
    await run_daily_task()  # 启动补录；未配置 WBY 时自动跳过


def _migrate_legacy_store():
    """旧数据迁移：config 从 data 目录搬到 config 目录；兼容旧独立插件的数据目录"""
    old_cfg = USAGE_DIR / "config.json"
    if old_cfg.exists() and not CONFIG_FILE.exists():
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_bytes(old_cfg.read_bytes())
            old_cfg.unlink(missing_ok=True)
            logger.success("[usage] config.json 已迁移至 config 目录")
        except Exception as e:
            logger.error(f"[usage] config.json 迁移失败: {e}")

    legacy = Path("data") / "token_usage"
    for old, new in ((legacy / "records.json", RECORDS_FILE),
                     (legacy / "config.json", CONFIG_FILE)):
        if old.exists() and not new.exists():
            try:
                new.parent.mkdir(parents=True, exist_ok=True)
                new.write_bytes(old.read_bytes())
                logger.success(f"[usage] 已迁移旧 token-usage 的 {new.name}")
            except Exception as e:
                logger.error(f"[usage] 迁移 {new.name} 失败: {e}")


# --------------------------- 指令（priority=0 + block，不泄漏给 auto_chat） --------------------------- #


def _parse_n(args: Message, default: int, limit: int):
    text = args.extract_plain_text().strip()
    if not text:
        return default
    if text.isdigit() and int(text) >= 1:
        return min(int(text), limit)
    return None


HELP_TEXT = (
    "📊 Token 用量查询指令（统计日以凌晨 4 点为界）\n"
    "· 日用量 [n] —— 最近 n 日用量（默认 7），含输入/输出/缓存命中率\n"
    "· 周用量 [n] —— 最近 n 周用量（默认 4），说明为「前多少周」\n"
    "· 月用量 [n] —— 最近 n 月用量（默认 6）\n"
    "· 总用量 —— 本地累计统计 + 实时查询该 API 总用量（快速验证用这个）\n"
    "· 群用量 [n] —— 最近 n 个统计日各群用量排行（默认 7）\n"
    "· 清除用量 日/周/月 [n]、清除用量 全部 确认 —— 手动清理用量记录\n"
    "· 用量价格 [输入价] [输出价] —— 配置单价（元/1M tokens），折算花费\n"
    "· 用量配置 [BASE KEY] —— 配置接口地址与密钥\n"
    "· 用量帮助 —— 查看帮助"
)

help_cmd = on_command("用量帮助", aliases={"token用量帮助"}, rule=to_me(), permission=SUPERUSER, priority=0, block=True)


@help_cmd.handle()
async def _(matcher: Matcher):
    await matcher.finish(HELP_TEXT)


usage_config = on_command("用量配置", aliases={"token配置", "配置用量"}, rule=to_me(), permission=SUPERUSER, priority=0, block=True)


@usage_config.handle()
async def _(matcher: Matcher, args: Message = CommandArg()):
    parts = args.extract_plain_text().split()
    if len(parts) >= 2:
        if not parts[0].startswith(("http://", "https://")):
            await matcher.finish("BASE 需以 http:// 或 https:// 开头，例如：用量配置 https://api.example.com KEY")
        cfg = load_file_config()
        cfg["base"] = parts[0]
        cfg["key"] = parts[1]
        save_config(cfg)
        await matcher.finish("✅ 配置已保存，每天凌晨 4 点自动记录 API 总量")


@usage_config.got("base", prompt="请发送 WBY_BASE（接口地址，例如 https://api.example.com）\n发送「取消」退出配置")
async def _(matcher: Matcher, state: T_State):
    base = str(state["base"]).strip()
    if base == "取消":
        await matcher.finish("已取消配置")
    if not base.startswith(("http://", "https://")):
        await matcher.reject("BASE 需以 http:// 或 https:// 开头，请重新发送：")
    state["_base"] = base


@usage_config.got("key", prompt="请发送 WBY_KEY（API Key）")
async def _(matcher: Matcher, state: T_State):
    key = str(state["key"]).strip()
    if key == "取消":
        await matcher.finish("已取消配置")
    if not key:
        await matcher.reject("KEY 不能为空，请重新发送：")
    cfg = load_file_config()
    cfg["base"] = state["_base"]
    cfg["key"] = key
    save_config(cfg)
    await matcher.finish("✅ 配置已保存，每天凌晨 4 点自动记录 API 总量\n可发送「日用量 7」查看统计")


price_cmd = on_command("用量价格", rule=to_me(), permission=SUPERUSER, priority=0, block=True)


@price_cmd.handle()
async def _(matcher: Matcher, args: Message = CommandArg()):
    parts = args.extract_plain_text().split()
    if len(parts) >= 2 and parts[0].replace(".", "", 1).isdigit() and parts[1].replace(".", "", 1).isdigit():
        cfg = load_file_config()
        cfg["price_input"] = float(parts[0])
        cfg["price_output"] = float(parts[1])
        save_config(cfg)
        await matcher.finish(
            f"✅ 单价已保存：输入 ¥{cfg['price_input']}/1M · 输出 ¥{cfg['price_output']}/1M\n"
            "此后每次调用将自动折算花费（配置前的历史用量不追溯）"
        )
    cfg = load_config()
    # 配置里手写成 "2元" 这类值时也不能让查询指令崩掉
    await matcher.finish(
        f"当前单价：输入 ¥{_as_float(cfg.get('price_input'))}/1M · 输出 ¥{_as_float(cfg.get('price_output'))}/1M\n"
        "用法：用量价格 输入价 输出价（元/1M tokens，例如「用量价格 2 8」）"
    )


# ---------- 输出格式化（纯函数，保证"实际输出 = 期望输出"可断言） ---------- #

def format_daily(n: int = 7) -> str:
    daily = load_records()["daily"]
    if not daily:
        return "暂无日用量记录，等待插件上报或每天凌晨 4 点自动统计"
    picks = daily[-n:]
    blocks = [f"📊 最近 {len(picks)} 日 Token 用量（统计日以凌晨 4 点为界）："]
    for r in reversed(picks):
        d = date.fromisoformat(r["date"])
        blocks.append(f"{d.month}月{d.day}日：\n　　" + _fmt_entry("", r))
    return "\n\n".join(blocks)


def format_weekly(n: int = 4) -> str:
    weekly = load_records()["weekly"]
    if not weekly:
        return "暂无周用量记录（每周日结束后自动汇总上一周）"
    picks = weekly[-n:]
    blocks = [f"📊 最近 {len(picks)} 周 Token 用量："]
    for i, w in enumerate(reversed(picks), 1):
        s_, e_ = date.fromisoformat(w["start"]), date.fromisoformat(w["end"])
        blocks.append(
            f"前{i}周（{s_.month}月{s_.day}日~{e_.month}月{e_.day}日）：\n　　" + _fmt_entry("", w))
    return "\n\n".join(blocks)


def format_monthly(n: int = 6) -> str:
    monthly = load_records()["monthly"]
    if not monthly:
        return "暂无月用量记录（每月初自动汇总上一月）"
    picks = monthly[-n:]
    blocks = [f"📊 最近 {len(picks)} 个月 Token 用量："]
    for m in reversed(picks):
        blocks.append(f"{_fmt_month(m['month'])}：\n　　" + _fmt_entry("", m))
    return "\n\n".join(blocks)


def format_source_split(entry: dict) -> str | None:
    """把 sources 子表格式化为「来源拆分：对话 … · 看图 …」（无数据返回 None）"""
    sources = entry.get("sources") or {}
    parts = []
    for name, label in (("chat", "对话"), ("vlm", "看图")):
        sv = sources.get(name)
        if sv:
            parts.append(
                f"{label} {sv.get('input', 0):,} 入 / {sv.get('output', 0):,} 出（{sv.get('calls', 0)} 次）")
    return ("来源拆分：" + " · ".join(parts)) if parts else None


def format_total_local() -> str:
    """本地累计统计（不含 API 实时查询部分）"""
    records = load_records()
    lines = ["📊 本地统计累计（自启用以来，凌晨 4 点为界）："]
    total: dict = {f: 0 for f in _SUM_FIELDS}
    for f in _COST_FIELDS:
        total[f] = 0.0
    for r in records["daily"]:
        _sum_entry(total, r)
    rate = _hit_rate(total)
    if not total["calls"]:
        lines.append("暂无本地调用记录（等待对话触发 LLM 调用后自动累计）")
        return "\n".join(lines)

    lines.append(
        f"调用 {total['calls']:,} 次 · 输入 {total['input']:,}"
        f"（缓存命中 {total['cached_input']:,} / {rate:.1f}%）· 输出 {total['output']:,}")
    ci, co = total["cost_input"], total["cost_output"]
    if ci or co:
        lines.append(
            f"花费合计（token 口径）：输入 ¥{ci:.4f} · 输出 ¥{co:.4f} · 合计 ¥{ci + co:.4f}")
    if total["api_used"]:
        lines.append(f"额度消耗合计（API 口径）：{total['api_used']:,}")
    split = format_source_split(total)
    if split:
        lines.append(split)

    day = _stat_date().isoformat()
    today_entry = next((r for r in records["daily"] if r["date"] == day), None)
    if today_entry and today_entry.get("calls"):
        lines.append(f"今日统计日（{day}）：")
        lines.append(_fmt_entry("", today_entry))
    return "\n".join(lines)


def format_group_usage(n: int = 7) -> str:
    daily = load_records()["daily"]
    if not daily:
        return "暂无用量记录（等待插件上报后才有分群排行）"
    recent = daily[-n:]
    agg: dict[str, dict] = {}
    for r in recent:
        for gid, g in (r.get("groups") or {}).items():
            t = agg.setdefault(gid, {"calls": 0, "input": 0, "output": 0, "cached_input": 0})
            for f in ("calls", "input", "output", "cached_input"):
                t[f] = t.get(f, 0) + g.get(f, 0)
            for name, sv in (g.get("sources") or {}).items():
                ts = t.setdefault("sources", {}).setdefault(
                    name, {"calls": 0, "input": 0, "output": 0, "cached_input": 0})
                for f in ("calls", "input", "output", "cached_input"):
                    ts[f] = ts.get(f, 0) + sv.get(f, 0)
    if not agg:
        return (f"最近 {len(recent)} 个有记录的统计日暂无分群数据\n"
                "（分群统计从部署支持该功能的版本后开始累计）")

    lines = [f"📊 最近 {len(recent)} 个有记录的统计日各群用量排行（按总量降序）："]
    for gid, t in sorted(agg.items(), key=lambda kv: -(kv[1]["input"] + kv[1]["output"])):
        rate = (t["cached_input"] / t["input"] * 100) if t["input"] else 0.0
        lines.append(
            f"群 {gid}：调用 {t['calls']:,} 次 · 输入 {t['input']:,}"
            f"（缓存命中 {t['cached_input']:,} / {rate:.0f}%）· 输出 {t['output']:,}")
        # 分用途明细（对话 / 看图）
        for name, label in (("chat", "对话"), ("vlm", "看图")):
            sv = (t.get("sources") or {}).get(name)
            if sv:
                lines.append(
                    f"　　└ {label} {sv.get('input', 0):,} 入 / {sv.get('output', 0):,} 出"
                    f"（{sv.get('calls', 0)} 次）")
    if not any(t["cached_input"] for t in agg.values()):
        total_cached = sum(int(r.get("cached_input", 0)) for r in recent)
        lines.append("")
        if total_cached:
            lines.append(
                f"提示：最近 {len(recent)} 日总量命中 {total_cached:,} 但分群全为 0，"
                "可能是分群统计部署晚于总量；请稍后再看。")
        else:
            lines.append(
                "提示：总量与分群命中都为 0 → 上游（中转站/模型）没返回缓存字段。"
                "DeepSeek 需官方接口且 prompt 前缀相同才会计缓存命中；"
                "多数中转站会剥离 prompt_cache_hit_tokens。"
                "把日志级别设为 DEBUG 可看到原始 usage 字段。")
    return "\n".join(lines)


# ---------- 指令处理 ---------- #

daily_cmd = on_command("日用量", rule=to_me(), permission=SUPERUSER, priority=0, block=True)


@daily_cmd.handle()
async def _(matcher: Matcher, args: Message = CommandArg()):
    n = _parse_n(args, default=7, limit=92)
    if n is None:
        await matcher.finish("用法：日用量 n（n 为查看最近 n 日用量）")
    await matcher.finish(format_daily(n))


weekly_cmd = on_command("周用量", rule=to_me(), permission=SUPERUSER, priority=0, block=True)


@weekly_cmd.handle()
async def _(matcher: Matcher, args: Message = CommandArg()):
    n = _parse_n(args, default=4, limit=104)
    if n is None:
        await matcher.finish("用法：周用量 n（n 为查看最近 n 周用量）")
    await matcher.finish(format_weekly(n))


monthly_cmd = on_command("月用量", rule=to_me(), permission=SUPERUSER, priority=0, block=True)


@monthly_cmd.handle()
async def _(matcher: Matcher, args: Message = CommandArg()):
    n = _parse_n(args, default=6, limit=60)
    if n is None:
        await matcher.finish("用法：月用量 n（n 为查看最近 n 月用量）")
    await matcher.finish(format_monthly(n))


total_cmd = on_command("总用量", rule=to_me(), permission=SUPERUSER, priority=0, block=True)


@total_cmd.handle()
async def _(matcher: Matcher):
    lines = [format_total_local()]
    cfg = load_config()
    if cfg.get("base") or cfg.get("url"):
        try:
            data = await _fetch_usage(cfg)
            lines.append("")
            lines.append(f"📈 API 总用量实时查询（{data.get('name') or 'unknown'}）：")
            lines.append(f"已用额度：{data['total_used']:,}")
            if data.get("unlimited_quota"):
                lines.append("额度：无限制")
            else:
                lines.append(f"剩余额度：{data.get('total_available', 0):,}")
        except Exception as e:
            # 异常文本可能包含带密钥的 URL（自定义 {key} 模板场景），发送前强制安检
            err = str(e)
            leaked, reason = check_leak(err)
            if leaked:
                log_leak("outgoing", "-", reason, err)
                lines.append("（API 实时查询失败：连接异常，详情见服务端日志）")
            else:
                lines.append(f"（API 实时查询失败：{e}）")
    else:
        lines.append("（未配置接口，跳过 API 总量实时查询；可在 .env 配 AIGF_USAGE_BASE / AIGF_USAGE_KEY 或用「用量配置」）")
    await matcher.finish("\n".join(lines))


def clear_records(scope: str, n: int = 1) -> str:
    """手动清除用量记录。scope ∈ {全部, 日, 周, 月}，返回结果描述（供指令与测试复用）。

    - 日：删除最近 n 个统计日的日记录（含其分群子表，API 增量一并删除）
    - 周/月：删除最近 n 条周/月汇总
    - 全部：清空日/周/月记录与 API 快照基准（下次 4 点任务重新建立基准）
    注意：周/月汇总由日记录聚合而来，删日记录不会级联删汇总，需分别清理。
    """
    records = load_records()
    if scope == "全部":
        save_records(json.loads(json.dumps(EMPTY_RECORDS)))
        return "✅ 已清除全部用量统计（日/周/月记录 + API 快照基准）"

    key = {"日": "daily", "周": "weekly", "月": "monthly"}.get(scope)
    if key is None:
        return "用法：清除用量 [日|周|月|全部]"
    items = records.get(key) or []
    if not items:
        return f"暂无{scope}用量记录，无需清除"
    n = max(1, min(int(n), len(items)))
    removed, records[key] = items[-n:], items[:-n]
    save_records(records)

    if key == "daily":
        span = f"{removed[0]['date']} ~ {removed[-1]['date']}"
    elif key == "weekly":
        span = f"{removed[0]['start']} ~ {removed[-1]['end']}"
    else:
        span = f"{_fmt_month(removed[0]['month'])} ~ {_fmt_month(removed[-1]['month'])}"
    return f"✅ 已清除 {len(removed)} 条{scope}用量记录（{span}）"


# ---- 清除用量（手动清理，仅超管）---- #

CLEAR_HELP = (
    "⚠️ 清除用量（不可恢复，仅超管可用）\n"
    "· 清除用量 日 [n] —— 删除最近 n 个统计日的日记录（默认 1），含其分群数据与 API 增量\n"
    "· 清除用量 周 [n] / 清除用量 月 [n] —— 删除最近 n 条周/月汇总\n"
    "· 清除用量 全部 确认 —— 清空全部用量统计与 API 快照基准\n"
    "注：周/月汇总由日记录聚合而成，删日记录不会级联删除汇总，需要时请分别清理"
)

clear_usage_cmd = on_command(
    "清除用量", aliases={"清空用量", "删除用量"},
    rule=to_me(), permission=SUPERUSER, priority=0, block=True)


@clear_usage_cmd.handle()
async def _(matcher: Matcher, args: Message = CommandArg()):
    parts = args.extract_plain_text().split()
    if not parts or parts[0] not in ("全部", "日", "周", "月"):
        await matcher.finish(CLEAR_HELP)

    scope = parts[0]
    if scope == "全部":
        if len(parts) >= 2 and parts[1] in ("确认", "confirm"):
            await matcher.finish(clear_records("全部"))
        await matcher.finish(
            "⚠️ 将清除**全部**用量统计（日/周/月记录 + API 快照基准），此操作不可恢复。\n"
            "确认请发送：清除用量 全部 确认")

    n = 1
    if len(parts) >= 2:
        if not parts[1].isdigit() or int(parts[1]) < 1:
            await matcher.finish("条数必须是正整数，例如：清除用量 日 7")
        n = int(parts[1])
    await matcher.finish(clear_records(scope, n))


# ---- 群用量（分群排行）---- #

group_usage_cmd = on_command("群用量", rule=to_me(), permission=SUPERUSER, priority=0, block=True)


@group_usage_cmd.handle()
async def _(matcher: Matcher, args: Message = CommandArg()):
    n = _parse_n(args, default=7, limit=92)
    if n is None:
        await matcher.finish("用法：群用量 n（统计最近 n 个统计日各群的用量排行）")
    await matcher.finish(format_group_usage(n))
