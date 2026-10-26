# Copyright (C) 2026 Funny1Potato
# Modified by XI-mouren, 2026
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

import hashlib
import json
import math
import time
from datetime import datetime

import anyio
from datetime import date as _date
from nonebot import logger
import nonebot_plugin_localstore as store

from .config import plugin_config
from .time_context import _now as _local_now

FRIENDS_DIR = store.get_plugin_data_dir() / "memory" / "friends"


# ============================ 熟悉度（好感度）与遗忘 ============================ #

def base_familiarity(count: int, last_active: float = 0.0) -> float:
    """发言量 → 基础熟悉度：log10(1+n)/log10(201)，即 200 条 ≈ 1.0。

    仅在「衰减机制关闭」时保留旧的「30 天未发言封顶 0.4」规则；
    开启衰减后由每日任务平滑遗忘，不再需要这个断崖。
    """
    if not count or count <= 0:
        return 0.0
    score = math.log10(1 + count) / math.log10(201)
    if not plugin_config.aigf_familiarity_decay_enabled and last_active:
        if (time.time() - last_active) / 86400 > 30:
            score = min(score, 0.4)
    return max(0.0, min(1.0, score))


def decay_step(score: float) -> float:
    """一天的自然遗忘：score' = score − (base + gain ×(1−score))

    - 分数越高，日衰减量越小（满分每天只掉 base）
    - 分数越低，日衰减量越大（加速项 gain×(1−score)）
    - 因为是加法形式，日衰减量随分数**严格单调递减**（不会出现"中间段掉得更狠"）
    - base > 0 保证任何分数都会衰减，不存在"永远不掉"的固定点
    """
    base = float(plugin_config.aigf_familiarity_decay_base)
    gain = float(plugin_config.aigf_familiarity_decay_gain)
    return max(0.0, score - (base + gain * (1.0 - score)))


def _today() -> _date:
    """按配置时区的"今天"（衰减记账用）"""
    return _local_now().date()


async def decay_all_friends(force_days: int | None = None) -> tuple[int, int, int]:
    """每日凌晨 4 点的遗忘任务：遍历所有群友画像做熟悉度衰减。

    返回 (扫描数, 仍在册数, 被忘记数)。低于 `forget_below` 的画像文件直接删除，
    相当于"印象淡到想不起来这个人"。
    """
    if not plugin_config.aigf_familiarity_decay_enabled:
        return 0, 0, 0
    if not FRIENDS_DIR.exists():
        return 0, 0, 0

    below = float(plugin_config.aigf_familiarity_forget_below)
    today = _today()
    keep = forget = scanned = 0

    for path in sorted(FRIENDS_DIR.glob("*.json")):
        scanned += 1
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.error(f"[familiarity] 画像读取失败 {path.name}: {e}")
            continue
        if not isinstance(data, dict):
            continue

        score = data.get("familiarity")
        if not isinstance(score, (int, float)):
            score = base_familiarity(int(data.get("message_count", 0) or 0),
                                     float(data.get("last_active", 0) or 0))
        score = max(0.0, min(1.0, float(score)))

        if force_days is None:
            elapsed = 1
            last = data.get("decay_date")
            if last:
                try:
                    elapsed = (today - _date.fromisoformat(str(last))).days
                except ValueError:
                    elapsed = 1
            if elapsed <= 0:
                continue          # 今天已衰减过：重复执行任务不会重复扣
        else:
            elapsed = max(0, int(force_days))
            if elapsed == 0:
                continue

        # 离线多天时补扣（上限防病态数据，<=0 时提前收敛）
        for _ in range(min(elapsed, 3650)):
            score = decay_step(score)
            if score <= 0.0:
                break

        if score < below:
            path.unlink(missing_ok=True)
            forget += 1
            logger.info(f"[familiarity] 已忘记 {path.stem}（熟悉度 {score:.3f} < {below}）")
            continue

        data["familiarity"] = round(score, 6)
        data["decay_date"] = today.isoformat()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        keep += 1

    return scanned, keep, forget


class MemoryManager:
    def __init__(self, group_id: str = "default"):
        self.group_id = group_id
        # 短期/长期记忆按群隔离
        self.data_dir = store.get_plugin_data_dir() / "memory" / group_id
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # 群友信息全局共享
        self.friends_dir = store.get_plugin_data_dir() / "memory" / "friends"
        self.friends_dir.mkdir(parents=True, exist_ok=True)

    async def load_short_term(self) -> list[str]:
        path = self.data_dir / "short_term.json"
        if not path.exists():
            return []
        try:
            async with await anyio.open_file(path, encoding="utf-8") as f:
                return json.loads(await f.read()).get("items", [])
        except Exception as e:
            logger.error(f"加载短期记忆失败: {e}")
            return []

    async def save_short_term(self, items: list[str]):
        path = self.data_dir / "short_term.json"
        async with await anyio.open_file(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps({"items": items}, ensure_ascii=False, indent=2))

    async def load_long_term(self) -> list[str]:
        path = self.data_dir / "long_term.json"
        if not path.exists():
            return []
        try:
            async with await anyio.open_file(path, encoding="utf-8") as f:
                return json.loads(await f.read()).get("facts", [])
        except Exception as e:
            logger.error(f"加载长期记忆失败: {e}")
            return []

    async def save_long_term(self, facts: list[str]):
        path = self.data_dir / "long_term.json"
        async with await anyio.open_file(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps({"facts": facts}, ensure_ascii=False, indent=2))

    async def load_friend(self, user_id: str) -> dict | None:
        path = self.friends_dir / f"{user_id}.json"
        if not path.exists():
            return None
        try:
            async with await anyio.open_file(path, encoding="utf-8") as f:
                return json.loads(await f.read())
        except Exception as e:
            logger.error(f"加载群友 {user_id} 信息失败: {e}")
            return None

    async def save_friend(self, user_id: str, data: dict):
        path = self.friends_dir / f"{user_id}.json"
        async with await anyio.open_file(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(data, ensure_ascii=False, indent=2))

    async def load_friends_batch(self, users: list[dict[str, str]]) -> dict[str, dict]:
        """批量加载当前群的群友信息"""
        result = {}
        for user in users:
            data = await self.load_friend(user["id"])
            if data and self.group_id in data.get("groups", []):
                result[user["id"]] = data
        return result

    async def apply_ops(self, memory_ops: dict):
        if not memory_ops:
            return
        logger.debug("[记忆] 开始执行记忆操作")

        st_ops = memory_ops.get("short_term", {})
        if st_ops:
            logger.debug(f"[记忆] 短期记忆操作: add={len(st_ops.get('add', []))}, modify={len(st_ops.get('modify', []))}, delete={len(st_ops.get('delete', []))}")
            items = await self.load_short_term()
            for idx in sorted([int(i) for i in st_ops.get("delete", [])], reverse=True):
                if 0 <= idx < len(items):
                    items.pop(idx)
            for mod in st_ops.get("modify", []):
                idx, content = int(mod.get("index", -1)), mod.get("content", "")
                if 0 <= idx < len(items) and content:
                    items[idx] = content
            for item in st_ops.get("add", []):
                if item and item not in items:
                    items.append(item)
            await self.save_short_term(items)

        lt_ops = memory_ops.get("long_term", {})
        if lt_ops:
            logger.debug(f"[记忆] 长期记忆操作: add={len(lt_ops.get('add', []))}, modify={len(lt_ops.get('modify', []))}, delete={len(lt_ops.get('delete', []))}")
            facts = await self.load_long_term()
            for idx in sorted([int(i) for i in lt_ops.get("delete", [])], reverse=True):
                if 0 <= idx < len(facts):
                    facts.pop(idx)
            for mod in lt_ops.get("modify", []):
                idx, content = int(mod.get("index", -1)), mod.get("content", "")
                if 0 <= idx < len(facts) and content:
                    facts[idx] = content
            for item in lt_ops.get("add", []):
                if item and item not in facts:
                    facts.append(item)
            await self.save_long_term(facts)

        # 构建昵称→QQ号反查表（从当前已加载的群友数据中）
        nickname_to_id: dict[str, str] = {}
        for f in self.friends_dir.iterdir():
            if f.suffix == ".json":
                try:
                    async with await anyio.open_file(f, encoding="utf-8") as fh:
                        data = json.loads(await fh.read())
                    if data.get("name") and data.get("id"):
                        nickname_to_id[data["name"]] = data["id"]
                except Exception:
                    pass

        friends_ops = memory_ops.get("friends", {})
        if friends_ops:
            logger.debug(f"[记忆] 群友信息操作: {len(friends_ops)} 个群友")
        for key, ops in friends_ops.items():
            # 如果 key 不是纯数字（QQ号），尝试从反查表中找到对应的 QQ 号
            user_id = key if key.isdigit() else nickname_to_id.get(key, key)
            friend = await self.load_friend(user_id) or {
                "id": user_id,
                "nickname": key if not key.isdigit() else "",
                "aliases": [],
                "past_nicknames": [],
                "info": [],
                "groups": [],
                "follow_ups": [],
                "taboos": [],
                "message_count": 0,
                "first_seen": time.time(),
                "last_active": 0.0,
                "hours": {},
            }
            # 确保当前群在 groups 列表中
            if self.group_id not in friend.get("groups", []):
                friend.setdefault("groups", []).append(self.group_id)
            # 处理 info 的增删改
            info = friend.get("info", [])
            for idx in sorted([int(i) for i in ops.get("delete", [])], reverse=True):
                if 0 <= idx < len(info):
                    info.pop(idx)
            for mod in ops.get("modify", []):
                idx, content = int(mod.get("index", -1)), mod.get("content", "")
                if 0 <= idx < len(info) and content:
                    info[idx] = content
            for item in ops.get("add", []):
                if item and item not in info:
                    info.append(item)
            friend["info"] = info
            # 处理 aliases 的增删
            aliases = friend.get("aliases", [])
            for item in ops.get("add_alias", []):
                if item and item not in aliases:
                    aliases.append(item)
            for item in ops.get("remove_alias", []):
                if item in aliases:
                    aliases.remove(item)
            friend["aliases"] = aliases
            # 待跟进话题（按内容匹配移除，降低模型给错下标的概率）
            follow_ups = friend.get("follow_ups", [])
            for item in ops.get("add_follow_up", []):
                if item and item not in follow_ups:
                    follow_ups.append(item)
            for item in ops.get("remove_follow_up", []):
                if item in follow_ups:
                    follow_ups.remove(item)
            friend["follow_ups"] = follow_ups
            # 雷点（按内容匹配移除）
            taboos = friend.get("taboos", [])
            for item in ops.get("add_taboo", []):
                if item and item not in taboos:
                    taboos.append(item)
            for item in ops.get("remove_taboo", []):
                if item in taboos:
                    taboos.remove(item)
            friend["taboos"] = taboos
            # 更新昵称
            if ops.get("update_name"):
                friend["nickname"] = ops["update_name"]
            await self.save_friend(user_id, friend)
            logger.debug(f"[记忆] 群友信息已保存: {user_id}")

    async def record_member_activity(self, user_id: str, qq_nickname: str):
        """每次收到群友消息时调用：同步昵称（变更记入 past_nicknames）
        + 累计活跃统计（message_count / last_active / hours 活跃时段直方图）"""
        if not user_id:
            return
        friend = await self.load_friend(user_id)
        if friend is None:
            friend = {
                "id": user_id,
                "nickname": qq_nickname,
                "aliases": [],
                "past_nicknames": [],
                "info": [],
                "groups": [self.group_id],
                "follow_ups": [],
                "taboos": [],
                "message_count": 0,
                "first_seen": time.time(),
                "last_active": 0.0,
                "hours": {},
            }
        if qq_nickname:
            old_nick = friend.get("nickname", "")
            if old_nick and old_nick != qq_nickname:
                past = friend.get("past_nicknames", [])
                if old_nick not in past:
                    past.append(old_nick)
                friend["past_nicknames"] = past
            friend["nickname"] = qq_nickname
        if self.group_id not in friend.get("groups", []):
            friend.setdefault("groups", []).append(self.group_id)
        # 活跃统计（代码维护，不经模型）
        friend["message_count"] = friend.get("message_count", 0) + 1
        friend.setdefault("first_seen", time.time())
        friend["last_active"] = time.time()
        hours = friend.setdefault("hours", {})
        # 活跃时段必须用配置时区（AIGF_TIMEZONE_OFFSET）；用 datetime.now() 在 UTC
        # 服务器上会整体偏移 8 小时，导致 prompt 里的"活跃时段"完全错位
        hour = str(_local_now().hour)
        hours[hour] = hours.get(hour, 0) + 1
        # 说过话 = 重新记住他：熟悉度刷新为发言量对应的真实值，衰减记账日归零
        friend["familiarity"] = round(
            base_familiarity(friend["message_count"], friend.get("last_active", 0.0)), 6)
        friend["decay_date"] = _today().isoformat()
        await self.save_friend(user_id, friend)

    @staticmethod
    def compute_familiarity(friend: dict) -> float:
        """熟悉度：优先返回持久化字段 `familiarity`（已被每日任务衰减过），
        老数据没有该字段时按发言量现算。纯代码计算，不经模型自评。"""
        stored = friend.get("familiarity")
        if isinstance(stored, (int, float)):
            return max(0.0, min(1.0, float(stored)))
        return base_familiarity(int(friend.get("message_count", 0) or 0),
                                float(friend.get("last_active", 0) or 0))

    @staticmethod
    def familiarity_label(score: float) -> str:
        if score >= 0.7:
            return "老熟人"
        if score >= 0.4:
            return "熟人"
        if score >= 0.15:
            return "点头之交"
        return "陌生"

    @staticmethod
    def get_active_users(messages: list) -> list[dict[str, str]]:
        """返回 [{"id": "QQ号", "name": "昵称"}, ...]"""
        seen = set()
        users = []
        for msg in messages:
            uid = msg.user_id if hasattr(msg, "user_id") else str(msg.get("user_id", ""))
            name = msg.user_name if hasattr(msg, "user_name") else str(msg.get("user_name", ""))
            if uid and uid not in seen:
                seen.add(uid)
                users.append({"id": uid, "name": name})
        return users

    # ========== 文化记忆管理 ==========

    async def load_culture(self) -> list[dict]:
        """加载文化记忆（梗、网络用语等）"""
        path = self.data_dir / "culture.json"
        if not path.exists():
            return []
        try:
            async with await anyio.open_file(path, encoding="utf-8") as f:
                return json.loads(await f.read()).get("terms", [])
        except Exception as e:
            logger.error(f"加载文化记忆失败: {e}")
            return []

    async def save_culture(self, terms: list[dict]):
        """保存文化记忆"""
        path = self.data_dir / "culture.json"
        async with await anyio.open_file(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps({"terms": terms}, ensure_ascii=False, indent=2))

    async def apply_culture_ops(self, ops: dict):
        """处理文化记忆操作（add/modify/delete）
        
        ops 格式:
        {
            "add": [{"term": "yyds", "meaning": "永远的神", "context": "表示赞美"}],
            "modify": [{"index": 0, "term": "yyds", "meaning": "永远滴神", "context": "..."}],
            "delete": [1]
        }
        """
        if not ops:
            return
        
        terms = await self.load_culture()
        
        # 删除操作
        for idx in sorted([int(i) for i in ops.get("delete", [])], reverse=True):
            if 0 <= idx < len(terms):
                removed = terms.pop(idx)
                logger.debug(f"[文化记忆] 删除: {removed.get('term', '')}")
        
        # 修改操作
        for mod in ops.get("modify", []):
            idx = int(mod.get("index", -1))
            if 0 <= idx < len(terms):
                # 更新字段
                for key in ["term", "meaning", "context", "usage_examples"]:
                    if key in mod and mod[key]:
                        terms[idx][key] = mod[key]
                logger.debug(f"[文化记忆] 修改: {terms[idx].get('term', '')}")
        
        # 添加操作
        for item in ops.get("add", []):
            if item and item.get("term"):
                # 检查是否已存在
                existing = next((t for t in terms if t.get("term") == item["term"]), None)
                if existing:
                    # 更新使用次数
                    existing["usage_count"] = existing.get("usage_count", 0) + 1
                    logger.debug(f"[文化记忆] 更新使用次数: {item['term']}")
                else:
                    # 新增条目
                    from datetime import datetime
                    new_term = {
                        "term": item["term"],
                        "meaning": item.get("meaning", ""),
                        "context": item.get("context", ""),
                        "usage_examples": item.get("usage_examples", []),
                        "first_seen": datetime.now().strftime("%Y-%m-%d"),
                        "usage_count": 1,
                        "source": item.get("source", "learned"),
                    }
                    terms.append(new_term)
                    logger.info(f"[文化记忆] 新增: {item['term']} - {item.get('meaning', '')}")
        
        await self.save_culture(terms)

    def match_culture_terms(self, terms: list[dict], text: str) -> list[dict]:
        """根据文本内容匹配相关的文化词汇

        Args:
            terms: 文化记忆列表
            text: 要匹配的文本

        Returns:
            匹配到的文化词汇列表
        """
        matched = []
        text_lower = text.lower()

        for term in terms:
            term_str = term.get("term", "").lower()
            if term_str and term_str in text_lower:
                matched.append(term)

        return matched

    # ========== 引用声明（citations）处理 ==========

    async def _load_long_term_stats(self) -> dict:
        """加载长期记忆的引用统计（sidecar 文件，以条目内容 md5 为键）"""
        path = self.data_dir / "long_term_stats.json"
        if not path.exists():
            return {"stats": {}}
        try:
            async with await anyio.open_file(path, encoding="utf-8") as f:
                return json.loads(await f.read())
        except Exception as e:
            logger.error(f"加载长期记忆引用统计失败: {e}")
            return {"stats": {}}

    async def _save_long_term_stats(self, data: dict):
        path = self.data_dir / "long_term_stats.json"
        async with await anyio.open_file(path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(data, ensure_ascii=False, indent=2))

    async def apply_citations(self, citations: dict, reply_text: str) -> dict:
        """处理 LLM 的引用声明。声明只是候选信号，代码负责校验：

        - culture：按词条名声明。先核存在性（表里有没有），再核证据性
          （回复文本里是否真出现）：命中 → used_count+1；
          未命中（可能为意译引用或谎报）→ weak_citations+1（弱引用，单独统计）
        - long_term：按下标声明。核越界；统计存放在 sidecar 文件，
          以条目内容的 md5 为键——增删改导致下标移位后统计仍能对上条目
        - 兜底：无条件统计回复文本中逐字出现的词条 → seen_count（不依赖模型声明）

        必须在 apply_ops 之前调用：模型引用的是本轮记忆操作之前的数组状态。

        Returns:
            {"used": 验证通过数, "weak": 弱引用数, "seen": 逐字出现数, "invalid": 无效声明数}
        """
        report = {"used": 0, "weak": 0, "seen": 0, "invalid": 0}
        text_lower = reply_text.lower()
        now = datetime.now().isoformat(timespec="seconds")

        terms = await self.load_culture()
        culture_changed = False

        # ---- culture 声明：存在性 + 证据性双重校验 ----
        cited_terms = citations.get("culture", []) or []
        if isinstance(cited_terms, str):
            cited_terms = [cited_terms]
        for term in {str(t).strip() for t in cited_terms if str(t).strip()}:
            entry = next((x for x in terms if x.get("term", "").lower() == term.lower()), None)
            if entry is None:
                report["invalid"] += 1
                logger.debug(f"[引用] 声明的词条不存在，丢弃: {term}")
                continue
            if term.lower() in text_lower:
                entry["used_count"] = entry.get("used_count", 0) + 1
                entry["last_used"] = now
                report["used"] += 1
                logger.debug(f"[引用] 词条引用已验证: {term}")
            else:
                entry["weak_citations"] = entry.get("weak_citations", 0) + 1
                entry["last_weak"] = now
                report["weak"] += 1
                logger.debug(f"[引用] 声明未在回复中找到原词，记为弱引用: {term}")
            culture_changed = True

        # ---- 兜底：逐字出现统计（不依赖模型是否声明）----
        if text_lower:
            for entry in self.match_culture_terms(terms, reply_text):
                entry["seen_count"] = entry.get("seen_count", 0) + 1
                entry["last_seen"] = now
                report["seen"] += 1
                culture_changed = True

        if culture_changed:
            await self.save_culture(terms)

        # ---- long_term 声明：越界校验 + md5 键 sidecar 统计 ----
        cited_idx = citations.get("long_term", []) or []
        if cited_idx:
            facts = await self.load_long_term()
            stats_data = await self._load_long_term_stats()
            stats = stats_data.setdefault("stats", {})
            for i in cited_idx:
                try:
                    idx = int(i)
                except (TypeError, ValueError):
                    report["invalid"] += 1
                    continue
                if not (0 <= idx < len(facts)):
                    report["invalid"] += 1
                    logger.debug(f"[引用] 长期记忆下标越界，丢弃: {idx}")
                    continue
                key = hashlib.md5(facts[idx].encode("utf-8")).hexdigest()
                s = stats.setdefault(key, {"content_preview": facts[idx][:30], "used_count": 0})
                s["used_count"] = s.get("used_count", 0) + 1
                s["last_used"] = now
                report["used"] += 1
                logger.debug(f"[引用] 长期记忆引用已记录: [{idx}] {facts[idx][:20]}")
            await self._save_long_term_stats(stats_data)

        if any(report.values()):
            logger.info(f"[引用] 引用统计: 验证通过={report['used']} 弱引用={report['weak']} "
                        f"逐字出现={report['seen']} 无效声明={report['invalid']}")
        return report


# ============================ 定时任务：每日遗忘 ============================ #

try:
    from nonebot import require as _require

    _scheduler = _require("nonebot_plugin_apscheduler").scheduler
except Exception as _e:  # noqa: BLE001
    _scheduler = None
    logger.warning(f"[familiarity] 未安装 nonebot-plugin-apscheduler，熟悉度衰减任务未注册: {_e}")

if _scheduler is not None:
    @_scheduler.scheduled_job(
        "cron", hour=4, minute=5, id="aigf_familiarity_decay", misfire_grace_time=3600
    )
    async def _familiarity_decay_job():
        """与用量统计同频（04:00 之后 5 分钟），避免两个任务抢同一时刻"""
        try:
            scanned, keep, forget = await decay_all_friends()
            logger.info(
                f"[familiarity] 每日衰减完成：扫描 {scanned}，保留 {keep}，忘记 {forget}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"[familiarity] 每日衰减任务失败: {e}")
