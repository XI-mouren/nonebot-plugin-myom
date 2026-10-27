# Copyright (C) 2025 shadow3aaa <shadow3aaaa@gmail.com>
# Modified by Funny1Potato, 2026
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

import base64
import json
import random
import re
from datetime import datetime

import anyio
from nonebot import logger

from .client import LLMClient
from .config import plugin_config
from . import energy
from .meme_manager import AtMessage, MemeMessage, TextMessage, meme_manager
from .memory import MemoryManager
from .mem import Message
from .presets import PRESETS
from .search import get_search_client
from .time_context import get_time_context


# 搜索工具定义（用于 Function Calling 模式）
SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_internet",
        "description": "搜索互联网获取最新信息，用于理解不懂的梗、网络用语或新词",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索查询词，应该是简洁的关键词"
                }
            },
            "required": ["query"]
        }
    }
}


def _extract_reply_text(reply_raw: list) -> str:
    """从 LLM 原始 reply 数组中拼接纯文本（用于引用声明的证据校验）"""
    parts = []
    for item in reply_raw:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            msg_type = item.get("type", "text")
            if msg_type in ("text", "at"):
                content = item.get("content") or item.get("name") or ""
                if content:
                    parts.append(str(content))
    return " ".join(parts)


def _relative_time(ts: float) -> str:
    if not ts:
        return "无记录"
    diff = datetime.now().timestamp() - ts
    if diff < 60:
        return "刚刚"
    if diff < 3600:
        return f"{int(diff // 60)}分钟前"
    if diff < 86400:
        return f"{int(diff // 3600)}小时前"
    return f"{int(diff // 86400)}天前"


def _active_hours_str(hours: dict) -> str:
    if not hours:
        return "无记录"
    top = sorted(hours.items(), key=lambda kv: -kv[1])[:3]
    return "、".join(f"{int(h)}点" for h, _ in sorted(top, key=lambda kv: int(kv[0])))


class Session:
    def __init__(self, id: str = "global", name: str = "小助手", role: str = "一个友好的群聊助手"):
        self.id = id
        self.name = name
        self.role = role
        self.current_preset: str = ""
        self.memory = MemoryManager(group_id=id)
        self.recent_messages: list[Message] = []
        self.knowledge_usage: dict[int, int] = {}  # 预设知识引用统计（内存，重启清零）

        # ---- 生物节律能量机制 ----
        # 关闭时（AIGF_ENERGY_ENABLED=false）回退旧的线性能量逻辑，行为与旧版一致
        self._energy_cfg: dict | None = None
        self._energy: energy.EnergyState | None = None
        self._energy_turn: dict | None = None      # 本批次的状态快照（供 _build_prompt 用）
        self._allowed_memes: set[str] = set()      # 本批次允许发送的表情包 id（白名单）
        self.social_energy: float = 0.75
        self._energy_on = bool(plugin_config.aigf_energy_enabled)
        if self._energy_on:
            try:
                self._energy_cfg = energy.get_config()
                self._energy = energy.load_state(self.id, self._energy_cfg)
                self.social_energy = self._energy.energy
            except Exception as e:  # noqa: BLE001
                logger.error(f"[energy] 初始化失败，回退旧版能量逻辑: {e}")
                self._energy_on = False
                self._energy_cfg = self._energy = None

    async def load_preset(self, preset_name: str) -> bool:
        if preset_name not in PRESETS:
            return False
        preset = PRESETS[preset_name]
        self.name = preset.name
        self.role = preset.role
        self.current_preset = preset_name
        return True

    def status(self) -> str:
        recent = "\n".join(f"{m.user_name}: {m.content}" for m in self.recent_messages[-15:]) or "没有消息"
        if self._energy_on and self._energy is not None and self._energy_cfg is not None:
            self.social_energy = self._energy.energy
            energy_line = energy.status_text(self._energy_cfg, self._energy, self.id)
        else:
            energy_line = f"社交能量：{self.social_energy:.2f}"
        return f"名字：{self.name}\n设定：{self.role}\n\n{energy_line}\n\n最近消息：\n{recent}"

    @staticmethod
    def _merge_consecutive(messages: list[Message], window: float) -> list[Message]:
        """合并同一用户在时间窗口内的连续消息为一条"""
        if not messages:
            return []
        merged: list[Message] = []
        for msg in messages:
            if (merged and msg.user_id and
                    msg.user_id == merged[-1].user_id and
                    (msg.time - merged[-1].time).total_seconds() <= window):
                merged[-1] = Message(
                    time=merged[-1].time,
                    user_name=merged[-1].user_name,
                    content=merged[-1].content + " " + msg.content,
                    user_id=merged[-1].user_id,
                )
            else:
                merged.append(msg)
        return merged

    def _get_energy_description(self) -> str:
        """旧版线性能量的五档文案，**仅**在 AIGF_ENERGY_ENABLED=false 时使用。

        开启节律时档位描述由 energy.prompt_context 生成，不经过这里；
        这里保留原项目的五档文案，是为了关闭开关后与旧版线性能量逐字节等价。
        """
        if self.social_energy >= 0.8:
            return "精力充沛，看到什么都想插嘴"
        elif self.social_energy >= 0.6:
            return "状态不错，有兴趣的话题会主动参与"
        elif self.social_energy >= 0.4:
            return "一般般，有人找就回，不太主动"
        elif self.social_energy >= 0.2:
            return "有点懒，倾向于潜水"
        else:
            return "完全不想说话"

    # ---------------------- 生物节律 ---------------------- #

    def _is_at(self, messages_chunk: list[Message]) -> bool:
        """本批次是否有人 @ 了 bot（@ 永久豁免硬拦截）"""
        for msg in messages_chunk:
            if getattr(msg, "is_at_only", False) or f"@{self.name}" in (msg.content or ""):
                return True
        return False

    def _energy_persist(self) -> None:
        """把内存中的能量状态落盘，并同步 social_energy 别名"""
        if not (self._energy_on and self._energy is not None):
            return
        self.social_energy = self._energy.energy
        try:
            energy.save_state(self.id, self._energy)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[energy] 保存群 {self.id} 能量状态失败: {e}")

    def _settle_sleep_recover(self, when: datetime) -> None:
        """休眠中按睡眠速率补恢复（只累加到睡醒那一刻）"""
        st, cfg = self._energy, self._energy_cfg
        if st is None or cfg is None:
            return
        end = min(when.timestamp(), st.sleep_until if st.sleep_until else when.timestamp())
        elapsed = max(0.0, end - st.updated_at)
        st.energy = min(1.0, st.energy + float(cfg["sleep"]["recover_per_10min"]) * elapsed / 600)
        st.updated_at = end

    def _check_natural_wake(self, when: datetime) -> bool:
        """睡眠时间到 → 自然醒（补恢复 + 保底 wake_floor + 清 sleep_until）

        必须在每个批次入口调用，否则 sleep_until 会一直挂着：
        虽然不再拦截（时间已过），但「状态」会一直显示休眠，且永远无法再次入睡。
        """
        st, cfg = self._energy, self._energy_cfg
        if st is None or cfg is None or not st.sleep_until:
            return False
        if when.timestamp() < st.sleep_until:
            return False
        self._settle_sleep_recover(when)
        st.energy = max(st.energy, float(cfg["sleep"]["wake_floor"]))
        st.sleep_until = None
        st.updated_at = when.timestamp()
        logger.info(f"[energy] 群 {self.id} 自然醒，能量 {st.energy:.2f}")
        return True

    def _maybe_fall_asleep(self, when: datetime) -> None:
        """能量低到阈值 → 进入休眠（时长从配置区间内随机）"""
        st, cfg = self._energy, self._energy_cfg
        if st is None or cfg is None or st.sleep_until:
            return
        if st.energy <= float(cfg["sleep"]["threshold"]):
            energy.enter_sleep(cfg, st, when=when)

    def energy_blocked(self, is_at: bool) -> str:
        """轻量预判（不结算能量）：返回 'sleep' / ''

        供消息入口在图片识别之前调用，被拦截的消息连 VLM 都不会触发。
        只有睡眠态会拦截；时间片（兴奋/平淡/疲倦）不影响拦截。
        """
        if not (self._energy_on and self._energy is not None and self._energy_cfg is not None):
            return ""
        cfg, st = self._energy_cfg, self._energy
        when = energy.now(cfg)
        state = energy.resolve_state(cfg, when)
        return energy.should_block(cfg, st, state, is_at=is_at, when=when)

    def settle_blocked(self) -> None:
        """被拦截的消息只做一次能量结算（休眠按睡眠速率、疲倦按 idle）"""
        cfg, st = self._energy_cfg, self._energy
        if st is None or cfg is None:
            return
        when = energy.now(cfg)
        self._check_natural_wake(when)
        if st.sleep_until and when.timestamp() < st.sleep_until:
            self._settle_sleep_recover(when)
        else:
            energy.settle_idle(cfg, st, energy.resolve_state(cfg, when), is_at=False)
        self._energy_persist()

    def _filter_reply_memes(self, reply_messages: list) -> list:
        """表情包白名单 + 单条上限过滤（能量机制关闭时原样返回）"""
        if not self._energy_on:
            return reply_messages
        raw_max = self._energy_cfg["decision"].get("meme_max_per_reply") if self._energy_cfg else None
        # 注意不能用 `or 1`：0 是有效配置（完全禁发），会被 or 悄悄改成 1
        max_memes = 1 if raw_max is None else max(0, int(raw_max))
        kept: list = []
        meme_count = 0
        for m in reply_messages:
            if isinstance(m, MemeMessage):
                if m.meme_id not in self._allowed_memes:
                    logger.info(f"[energy] 丢弃白名单外的表情包: {m.meme_id}")
                    continue
                if meme_count >= max_memes:
                    logger.info(f"[energy] 超出表情包上限（{max_memes}），丢弃: {m.meme_id}")
                    continue
                meme_count += 1
            kept.append(m)
        return kept

    def _energy_gate(self, is_at: bool) -> bool:
        """批次入口的能量处理。返回 True=放行，False=硬拦截（本批不回复）"""
        cfg, st = self._energy_cfg, self._energy
        assert cfg is not None and st is not None
        when = energy.now(cfg)
        state = energy.resolve_state(cfg, when)

        # 0) 睡眠时间已到 → 先自然醒（清 sleep_until、补到 wake_floor）
        self._check_natural_wake(when)

        # 1) 休眠中被 @ → 唤醒（+wake_boost，进入困倦期）
        woke = False
        if st.sleep_until and when.timestamp() < st.sleep_until and is_at:
            energy.wake(cfg, st, when=when)
            woke = True

        # 2) 上一轮把能量耗到阈值以下 → 现在入睡
        self._maybe_fall_asleep(when)

        # 3) 硬拦截：只有休眠期非 @（疲倦期不做拦截，只影响语气与能量）
        reason = energy.should_block(cfg, st, state, is_at=is_at, when=when)
        if reason:
            self._settle_sleep_recover(when)
            self._energy_persist()
            self._energy_turn = None
            self._allowed_memes = set()
            logger.info(
                f"[energy] 群 {self.id} {reason} 拦截（能量 {st.energy:.2f}）")
            return False

        # 4) 放行：按时间片结算本批变化（@ 给 at_boost，否则 idle_gain）
        energy.settle_idle(cfg, st, state, is_at=is_at)
        plan = energy.meme_plan(cfg, st.energy, is_at=is_at)
        self._energy_turn = {
            "state": state,
            "label": cfg["states"][state]["label"],
            "is_at": is_at,
            "woke": woke,
            "plan": plan,
            "ctx": energy.prompt_context(cfg, st, state, is_at=is_at, woke_from_sleep=woke),
        }
        self._allowed_memes = set()  # 由 _build_prompt 按候选列表填充
        self._energy_persist()
        logger.debug(
            f"[energy] 群 {self.id} {cfg['states'][state]['label']} 能量 {st.energy:.2f} "
            f"档位 {plan['label']} 配图 {'允许' if plan['allowed'] else '不发'}")
        return True

    def _build_analysis_prompt(self, messages_chunk: list[Message], culture: list[dict]) -> str:
        """构建分析 prompt，用于判断是否需要搜索"""
        # 合并消息
        merged_chunk = self._merge_consecutive(messages_chunk, plugin_config.aigf_merge_window)
        new_msgs_str = "\n".join(f"{m.user_name}: '{m.content}'" for m in merged_chunk)
        
        # 已有的文化词汇
        known_terms = [t.get("term", "") for t in culture]
        known_terms_str = ", ".join(known_terms) if known_terms else "无"
        
        return f"""请分析以下群聊消息，判断是否有你不理解的梗、网络用语或新词需要搜索。

## 已知文化词汇
{known_terms_str}

## 新消息
{new_msgs_str}

## 分析要求
- 检查消息中是否有你不理解的梗、网络用语、流行语
- 如果已知文化词汇中已包含该词，则不需要搜索
- 只有当你真的不理解某个词的含义时才需要搜索
- 普通的中文词汇不需要搜索

请输出 JSON：
```json
{{
  "need_search": true/false,
  "search_query": "要搜索的关键词（如果不需要搜索可以省略）",
  "reason": "判断理由（可选）"
}}
```
"""

    async def process(self, messages_chunk: list[Message], llm: LLMClient,
                      cached_stickers: list[dict] | None = None) -> list[TextMessage | MemeMessage | AtMessage] | None:
        logger.debug(f"[process] 开始处理 {len(messages_chunk)} 条消息")
        # 更新最近消息
        self.recent_messages.extend(messages_chunk)
        if len(self.recent_messages) > 50:
            self.recent_messages = self.recent_messages[-50:]

        # ---- 能量节律 gate：仅休眠期非 @ → 直接丢弃，不构建 prompt、不调 LLM ----
        if self._energy_on and self._energy is not None and self._energy_cfg is not None:
            if not self._energy_gate(self._is_at(messages_chunk)):
                return None

        # 加载记忆
        short_term = await self.memory.load_short_term()
        long_term = await self.memory.load_long_term()
        active_users = MemoryManager.get_active_users(messages_chunk)
        friends = await self.memory.load_friends_batch(active_users)
        culture = await self.memory.load_culture()

        # LLM 模式：读取缓存图片的 base64
        sticker_images: list[str] = []
        if plugin_config.aigf_image_mode == "llm" and cached_stickers:
            for s in cached_stickers:
                cache_info = meme_manager._cache_index.get(s["id"])
                if cache_info:
                    try:
                        async with await anyio.open_file(cache_info["path"], "rb") as f:
                            sticker_images.append(base64.b64encode(await f.read()).decode())
                    except Exception as e:
                        logger.error(f"读取缓存图片失败: {e}")

        # 构建 prompt
        if not self._energy_on:
            # 旧版线性能量逻辑（AIGF_ENERGY_ENABLED=false 时保持旧版行为）
            at_boost = 0.0
            for msg in messages_chunk:
                if f"@{self.name}" in msg.content or msg.is_at_only:
                    at_boost = 0.1  # 被 @ 时兴奋值
                    logger.debug(f"[process] 检测到被 @，兴奋加成: +{at_boost}")
                    break
            self.social_energy += (plugin_config.aigf_energy_baseline - self.social_energy) * 0.15
            self.social_energy += random.uniform(-0.08, 0.08)
            self.social_energy += at_boost  # 被 @ 时的兴奋加成
            self.social_energy = max(0.0, min(1.0, self.social_energy))
            logger.debug(f"[process] 社交能量: {self.social_energy:.2f}")

        # 匹配当前聊天中的文化词汇
        all_text = " ".join(m.content for m in messages_chunk)
        matched_culture = self.memory.match_culture_terms(culture, all_text)

        search_results = None

        # 搜索模式处理
        if plugin_config.aigf_search_enabled:
            search_client = get_search_client()
            
            if plugin_config.aigf_search_mode == "two_stage" and search_client:
                # 两阶段模式：先分析是否需要搜索
                analysis_prompt = self._build_analysis_prompt(messages_chunk, culture)
                logger.debug("[process] 两阶段模式 - 第一阶段：分析是否需要搜索")
                
                analysis_response = await llm.generate_response(analysis_prompt, plugin_config.aigf_chat_openai_model)
                if analysis_response:
                    try:
                        analysis_response = re.sub(r"^```json\s*|\s*```$", "", analysis_response.strip())
                        analysis = json.loads(analysis_response)
                        
                        if analysis.get("need_search") and analysis.get("search_query"):
                            logger.info(f"[搜索] 需要搜索: {analysis['search_query']}")
                            search_results = await search_client.search(
                                analysis["search_query"],
                                plugin_config.aigf_search_max_results
                            )
                    except Exception as e:
                        logger.error(f"[搜索] 分析结果解析失败: {e}")
            
            elif plugin_config.aigf_search_mode == "function_call" and search_client:
                # Function Calling 模式：在下方处理
                pass

        # 构建主 prompt
        prompt = self._build_prompt(short_term, long_term, friends, messages_chunk, 
                                     cached_stickers or [], search_results, matched_culture)

        logger.debug(f"[process] 调用 LLM, sticker_images: {len(sticker_images)} 张")
        
        # 调用 LLM
        used_search = False
        if plugin_config.aigf_search_enabled and plugin_config.aigf_search_mode == "function_call" and get_search_client():
            # Function Calling 模式
            logger.debug("[process] Function Calling 模式")

            async def tool_executor(name: str, args: dict) -> str:
                if name == "search_internet":
                    client = get_search_client()
                    if client:
                        results = await client.search(args.get("query", ""), plugin_config.aigf_search_max_results)
                        return client.format_results(results)
                    return "搜索客户端不可用"
                return f"未知工具: {name}"

            response_str, used_search = await llm.generate_response_with_tools(
                prompt, plugin_config.aigf_chat_openai_model,
                tools=[SEARCH_TOOL],
                tool_executor=tool_executor,
            )
            if used_search:
                logger.info("[搜索] Function Calling 模式使用了搜索工具")
        else:
            # 普通模式或两阶段模式（已完成搜索）
            response_str = await llm.generate_response(
                prompt, plugin_config.aigf_chat_openai_model,
                images=sticker_images if sticker_images else None,
            )
        logger.debug(f"[process] LLM 返回: {response_str[:200] if response_str else None}")
        if not response_str:
            return None

        # 解析 JSON
        response_str = re.sub(r"^```json\s*|\s*```$", "", response_str.strip())
        try:
            result = json.loads(response_str)
        except json.JSONDecodeError:
            # 如果使用了搜索结果，可能是内容安全审核拦截或格式问题，去掉搜索结果重试一次
            if search_results or used_search:
                logger.warning("[process] LLM 返回非法 JSON，可能是搜索结果触发内容审核，去掉搜索结果重试")
                prompt_no_search = self._build_prompt(short_term, long_term, friends, messages_chunk,
                                                       cached_stickers or [], None, matched_culture)
                prompt_no_search += "\n## 注意\n之前尝试搜索相关内容，但搜索失败无法使用。请仅根据对话上下文回复，不要提及搜索。\n**必须输出 JSON 格式**，按照上方示例结构输出。\n\n"
                response_str = await llm.generate_response(
                    prompt_no_search, plugin_config.aigf_chat_openai_model,
                    images=sticker_images if sticker_images else None,
                )
                if not response_str:
                    return None
                response_str = re.sub(r"^```json\s*|\s*```$", "", response_str.strip())
                try:
                    result = json.loads(response_str)
                except json.JSONDecodeError:
                    logger.error(f"[process] 重试后仍返回非法 JSON: {response_str[:200]}")
                    return None
            else:
                logger.error(f"LLM 返回非法 JSON: {response_str[:200]}")
                return None

        # 处理引用声明（citations）：必须在 apply_ops 之前执行，
        # 模型引用的是本轮记忆操作之前的数组状态，先删后核会下标错位
        citations = result.get("citations")
        if isinstance(citations, dict) and citations:
            reply_text = _extract_reply_text(result.get("reply", []))
            if reply_text.strip():
                try:
                    await self.memory.apply_citations(citations, reply_text)
                except Exception as e:
                    logger.error(f"[引用] 引用声明处理失败: {e}")
                await self._apply_knowledge_citations(citations)

        # 执行记忆操作
        try:
            memory_ops = result.get("memory", {})
            if memory_ops:
                ops_summary = []
                if memory_ops.get("short_term"): ops_summary.append("短期记忆")
                if memory_ops.get("long_term"): ops_summary.append("长期记忆")
                if memory_ops.get("friends"): ops_summary.append("群友信息")
                if memory_ops.get("save_meme"): ops_summary.append("表情包收藏")
                if memory_ops.get("culture"): ops_summary.append("文化记忆")
                if ops_summary:
                    logger.info(f"[记忆] 操作: {'、'.join(ops_summary)}")
            await self.memory.apply_ops(memory_ops)
            
            # 处理文化记忆操作
            culture_ops = memory_ops.get("culture", {})
            if culture_ops:
                await self.memory.apply_culture_ops(culture_ops)
        except Exception as e:
            logger.error(f"执行记忆操作失败: {e}")

        # 处理表情包保存
        save_meme = result.get("memory", {}).get("save_meme")
        if save_meme:
            logger.info(f"[表情包收藏] LLM 决定收藏: {save_meme}")

        if save_meme and plugin_config.aigf_meme_enabled:
            save_list = save_meme if isinstance(save_meme, list) else [save_meme]
            current_cache = meme_manager.get_cached_stickers()
            current_ids = {s["id"] for s in current_cache}
            for item in save_list:
                cache_id = item.get("id", "")
                description = item.get("description", "")
                keywords = item.get("keywords", ["表情包"])
                if not isinstance(keywords, list):
                    keywords = ["表情包"]
                if cache_id and description and cache_id in current_ids:
                    try:
                        await meme_manager.save_from_cache(cache_id, description, keywords)
                        logger.success(f"[表情包收藏] 决策保存: id={cache_id}, 描述: {description[:30]}")
                    except Exception as e:
                        logger.error(f"表情包保存失败: {e}")

        # 解析回复
        reply_raw = result.get("reply", [])
        logger.debug(f"[process] LLM 返回 reply: {reply_raw}")
        if not reply_raw:
            return None

        reply_messages = []
        for item in reply_raw:
            if isinstance(item, str):
                reply_messages.append(TextMessage(content=item))
            elif isinstance(item, dict):
                msg_type = item.get("type", "text")
                if msg_type == "meme":
                    mid = item.get("id", "")
                    if mid:
                        reply_messages.append(MemeMessage(meme_id=mid))
                elif msg_type == "at":
                    name = item.get("name", "")
                    if name:
                        reply_messages.append(AtMessage(user_name=name))
                elif msg_type == "text":
                    content = item.get("content", "")
                    if content:
                        reply_messages.append(TextMessage(content=content))

        # 把 bot 的回复存入最近消息
        if reply_messages:
            # 表情包白名单校验 + 单条回复上限（概率前置未通过时白名单为空 → 全部丢弃）
            reply_messages = self._filter_reply_memes(reply_messages)
            if not reply_messages:
                return None

            # 消耗能量
            text_length = sum(len(m.content) for m in reply_messages if isinstance(m, TextMessage))
            if self._energy_on and self._energy is not None and self._energy_cfg is not None:
                when = energy.now(self._energy_cfg)
                state = (self._energy_turn or {}).get("state") or energy.resolve_state(
                    self._energy_cfg, when)
                cost = energy.settle_reply(self._energy_cfg, self._energy, state, text_length)
                self._maybe_fall_asleep(when)
                self._energy_persist()
                logger.debug(
                    f"[process] 回复消耗能量: {cost:.3f}, 剩余: {self.social_energy:.2f}")
            else:
                energy_cost = 0.03 + min(0.12, text_length * 0.002)
                self.social_energy = max(0.0, self.social_energy - energy_cost)
                logger.debug(
                    f"[process] 回复消耗能量: {energy_cost:.3f}, 剩余: {self.social_energy:.2f}")

            for msg in reply_messages:
                if isinstance(msg, TextMessage):
                    self.recent_messages.append(
                        Message(time=datetime.now(), user_name=self.name, content=msg.content))
                elif isinstance(msg, MemeMessage):
                    meme = meme_manager.memes.get(msg.meme_id)
                    desc = meme.description if meme else msg.meme_id
                    self.recent_messages.append(
                        Message(time=datetime.now(), user_name=self.name, content=f"[表情包] {desc}"))
                elif isinstance(msg, AtMessage):
                    self.recent_messages.append(
                        Message(time=datetime.now(), user_name=self.name, content=f"@{msg.user_name}"))

        return reply_messages or None

    async def _apply_knowledge_citations(self, citations: dict):
        """预设知识（preset knowledges）引用统计：按下标声明，做越界校验。
        仅在内存中累计（预设是静态内容，重启清零可接受）。"""
        cited = citations.get("knowledge", []) or []
        if not cited:
            return
        preset = PRESETS.get(self.current_preset)
        if not preset or not preset.knowledges:
            return
        for i in cited:
            try:
                idx = int(i)
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(preset.knowledges):
                self.knowledge_usage[idx] = self.knowledge_usage.get(idx, 0) + 1
                logger.debug(f"[引用] 预设知识引用: [{idx}] {preset.knowledges[idx][:20]}")
            else:
                logger.debug(f"[引用] 预设知识下标越界，丢弃: {idx}")

    def _build_prompt(self, short_term: list[str], long_term: list[str],
                      friends: dict[str, dict], messages_chunk: list[Message],
                      cached_stickers: list[dict] | None = None,
                      search_results: list[dict] | None = None,
                      matched_culture: list[dict] | None = None) -> str:
        # 预设知识
        preset_knowledge = ""
        preset = PRESETS.get(self.current_preset)
        if preset and preset.knowledges:
            preset_knowledge = "\n".join(f"- {k}" for k in preset.knowledges)

        # 短期记忆（JSON 数组格式，index 即数组下标）
        short_term_str = json.dumps(short_term, ensure_ascii=False, indent=2) if short_term else "[]"

        # 长期记忆（JSON 数组格式，index 即数组下标）
        long_term_str = json.dumps(long_term, ensure_ascii=False, indent=2) if long_term else "[]"

        # 群友信息（JSON 对象格式）
        friends_dict = {}
        # 已有记忆的群友
        for uid, data in friends.items():
            score = MemoryManager.compute_familiarity(data)
            friends_dict[uid] = {
                "nickname": data.get("nickname", ""),
                "aliases": data.get("aliases", []),
                "past_nicknames": data.get("past_nicknames", []),
                "info": data.get("info", []),
                "follow_ups": data.get("follow_ups", []),
                "taboos": data.get("taboos", []),
                "message_count": data.get("message_count", 0),
                "familiarity": f"{MemoryManager.familiarity_label(score)}({score:.2f})",
                "last_active": _relative_time(data.get("last_active", 0)),
                "active_hours": _active_hours_str(data.get("hours", {})),
            }
        # 当前聊天中但还没有记忆的群友
        active_users = MemoryManager.get_active_users(messages_chunk)
        existing_ids = set(friends.keys())
        for u in active_users:
            if u["id"] not in existing_ids:
                friends_dict[u["id"]] = {
                    "nickname": u["name"],
                    "aliases": [],
                    "past_nicknames": [],
                    "info": [],
                    "follow_ups": [],
                    "taboos": [],
                    "message_count": 0,
                    "familiarity": "陌生(0.00)",
                    "last_active": "刚刚",
                    "active_hours": "无记录",
                }
        friends_str = json.dumps(friends_dict, ensure_ascii=False, indent=2) if friends_dict else "{}"

        # 最近 15 条消息（合并同一用户短时间内的连续消息）
        recent = self.recent_messages[-plugin_config.aigf_recent_messages:]
        recent = self._merge_consecutive(recent, plugin_config.aigf_merge_window)
        recent_str = "\n".join(f"{m.user_name}: {m.content}" for m in recent) or "无"

        # 新消息（合并同一用户短时间内的连续消息）
        merged_chunk = self._merge_consecutive(messages_chunk, plugin_config.aigf_merge_window)
        new_msgs_str = "\n".join(f"{m.user_name}: '{m.content}'" for m in merged_chunk)

        # 表情包列表（按当前对话预筛选候选，避免全量注入撑爆上下文）
        meme_section = ""
        self._allowed_memes = set()
        plan = (self._energy_turn or {}).get("plan")
        if plugin_config.aigf_meme_enabled:
            # 概率前置：能量机制启用时先掷骰子决定本轮能不能发表情包
            allowed = True if plan is None else bool(plan["allowed"])
            limit = (int(plan["candidates"]) if plan else plugin_config.aigf_meme_candidates)
            if not allowed:
                meme_section = """

## 表情包
这一轮不要发表情包（reply 里不要出现 meme 项），正文也不要提表情包。
"""
            else:
                meme_match_text = recent_str + "\n" + new_msgs_str
                if matched_culture:
                    meme_match_text += "\n" + "\n".join(
                        f"{t.get('term', '')} {t.get('meaning', '')}" for t in matched_culture)
                items = meme_manager.select_candidate_items(meme_match_text, limit=limit)
                if items:
                    meme_lines = "\n".join(
                        f"- [{m.id}] {m.description}（适用场景：{'、'.join(m.keywords)}）"
                        for m in items)
                    # 仅能量机制启用时写入白名单并声明单张上限；
                    # 关闭时保持旧版原文案与行为（无数量约束、不校验白名单）
                    if self._energy_on:
                        self._allowed_memes = {m.id for m in items}
                        max_per = int((self._energy_cfg or {}).get("decision", {})
                                      .get("meme_max_per_reply") or 1)
                        head = f"，最多选 {max_per} 张"
                    else:
                        head = ""
                    meme_section = f"""

## 可用的表情包（用于发送）
以下是根据当前对话预筛选出的候选表情包（不是全部库存）{head}：
{meme_lines}
"""

        # 缓存中的表情包（可供收藏）
        sticker_section = ""

        if cached_stickers and plugin_config.aigf_meme_enabled:
            if plugin_config.aigf_image_mode == "llm":
                # LLM 模式：图片已附在 prompt 中，LLM 直接看图
                sticker_ids = [s['id'] for s in cached_stickers]
                sticker_section = """

## 当前消息中出现的图片（可收藏）
以下图片已通过视觉输入提供，消息正文中的图片标记按出现顺序与图片一一对应（第一个 id 对应第一张图，以此类推）。请结合上下文判断：
- 如果是表情包（能表达一定的情感，适合在群聊中反复使用），且你的表情包库内没有类似的图片，且你觉得值得保存的，可以收藏。
- 如果只是群友分享的照片、截图、梗图、普通图片，或者表情包库内已有类似图片，则不要收藏。
- 若群友连续发了多张图片，通常是在分享普通图片
- 若群友只发了一张图片，而前后均没有与这张图片相关的内容，或是在图片前后仅有对该图片的介绍或评论，通常是在分享普通图片
- 若群友发的图片内容与之前的内容有关联，或这张图片是在其它群友发言之后发出的，且**包含较为明显的情感**（开心、愤怒、疑惑等），则可能是表情包
如果值得收藏，在 memory.save_meme 中填入下方对应的 id、你写的简短描述和关键词。
可用的 id（仅供 save_meme 使用，不要在 reply 中提及）：""" + ", ".join(sticker_ids)
            else:
                # VLM 模式：描述已在消息正文中，不再重复列出
                sticker_section = """

## 当前消息中出现的图片（可收藏）
以下是本次对话中出现的图片。消息中已标注初步判断结果：
- 标记为"可能是表情包"的图片已附带情感和内容描述，是较可能的收藏候选
- 仅标记为"图片"的通常为普通图片，一般不需要收藏
请结合上下文进一步判断：
- 如果是表情包（能表达一定的情感，适合在群聊中反复使用），且你的表情包库内没有类似的图片，且你觉得值得保存的，可以收藏。
- 如果只是群友分享的照片、截图、梗图、普通图片，或者表情包库内已有类似图片，则不要收藏。
- 若群友连续发了多张图片，通常是在分享普通图片
- 若群友只发了一张图片，而前后均没有与这张图片相关的内容，或是在图片前后仅有对该图片的介绍或评论，通常是在分享普通图片
- 若群友发的图片内容与之前的内容有关联，或这张图片是在其它群友发言之后发出的，且**包含较为明显的情感**（开心、愤怒、疑惑等），则可能是表情包
如果值得收藏，在 memory.save_meme 中填入 id 和你写的简短描述。
可用 id：""" + ", ".join(s['id'] for s in cached_stickers)

        # 群友列表（用于 @）
        user_list_str = ", ".join(f"{data.get('name', uid)}(QQ:{uid})" for uid, data in friends.items()) if friends else "无"

        # 精力状态段：优先用本批次 gate 生成的上下文；
        # 若在 gate 之外调用（如直接构建 prompt），按当前能量现算，避免回落到旧文案
        if self._energy_turn:
            state_block = self._energy_turn["ctx"]
            state_note = "以上是你的精力状态，会直接影响你是否想说话、回复的热情与长度"
        elif self._energy_on and self._energy is not None and self._energy_cfg is not None:
            state_block = energy.prompt_context(
                self._energy_cfg, self._energy,
                energy.resolve_state(self._energy_cfg, energy.now(self._energy_cfg)))
            state_note = "以上是你的精力状态，会直接影响你是否想说话、回复的热情与长度"
        else:
            state_block = "社交能量：" + self._get_energy_description()
            state_note = "这会影响你是否想说话、回复的热情程度"

        prompt = "你是 " + self.name + "，" + self.role + "\n\n"
        prompt += """## 回复风格
你是一个真实的群聊参与者，不是AI助手。
- 回复要像和人聊天一样自然、口语化
- 可以使用网络用语（"神了"、"逆天"、"666"等）
- 回复长度要有变化：有时几个字，有时几句话
- 不要表现得过于热情或乐于助人
- 累了的时候可以稍微敷衍一些
- 不需要对每条消息都认真回复
若上述的回复风格要求与你的知识有冲突，以你的知识为优先。

## 当前状态
当前时间：""" + get_time_context() + """
""" + state_block + """
（""" + state_note + """）

"""
        # 预设知识段落
        if preset_knowledge:
            prompt += "## 你的知识\n" + preset_knowledge + "\n\n"

        # 文化知识段落
        if matched_culture:
            culture_str = "\n".join(
                f"- {t.get('term', '')}: {t.get('meaning', '')} ({t.get('context', '')})"
                for t in matched_culture
            )
            prompt += f"""## 相关文化知识
以下是当前聊天中涉及的文化词汇，你已经了解它们的含义：
{culture_str}

"""

        # 搜索结果段落
        if search_results:
            from .search import get_search_client
            client = get_search_client()
            if client:
                search_str = client.format_results(search_results)
                prompt += f"""## 搜索结果
以下是你搜索到的信息，可以帮助你理解群友提到的梗或新词：
{search_str}

"""

        prompt += """## 你的记忆

### 短期记忆
```json
""" + short_term_str + """
```
这是你对近期对话的记忆。管理原则：优先修改，能改就不加；积极删除，不让列表无限增长。
- modify：对话有新进展时，直接更新已有条目（优先使用）
- delete：已结束的话题、已解决的问题、不再相关的内容（积极使用）
- add：仅当列表中没有相关内容时才添加
index 对应上面数组的下标（从 0 开始）。

### 长期记忆
```json
""" + long_term_str + """
```
这是你记住的重要信息。收录标准：**预计一个月后仍然有价值的信息**。
值得记的：群里的重要事件（聚会、争执、大的话题转向）、约定和计划、群规和共识、群成员的重大变化（搬家、换工作、重要关系的变动）、你被明确要求记住的事。
每条写成一句话，简明扼要；涉及时间的写清完整的年月日（参考上方"当前时间"，如"2026年6月1日群聚餐"），禁止只写"下周"、"明天"这类会失效的相对时间。
管理原则：优先修改，能改就不加；发现旧信息不准确时直接修改，而不是新增一条纠正。
- modify：信息有变化时，直接修改原条目（优先使用）
- delete：被证伪、过时、早已过期、或不再适用的信息（积极使用）
- add：仅当完全没有相关内容时才添加
不要记：临时性的对话内容、无关紧要的闲聊、日常寒暄、情绪宣泄、单纯玩梗。

### 相关群友信息
```json
""" + friends_str + """
```
这是你对群友的了解。每个群友是一个对象，包含以下字段：
- info：了解到的信息数组，用 add/modify/delete 操作
- aliases：称呼数组，用 add_alias/remove_alias 操作
- follow_ups：待跟进话题数组，用 add_follow_up/remove_follow_up 操作
- taboos：雷点数组（对方不愿被提到的事），用 add_taboo/remove_taboo 操作
- nickname：QQ 全局昵称（系统自动更新，无需手动管理）
- past_nicknames：曾用昵称（系统自动记录，无需手动管理）
- message_count / familiarity / last_active / active_hours：系统自动统计的活跃数据（发言数、熟悉度、最后发言时间、活跃时段），无需也无法手动管理

info 条目的写法：每条是一个简短的描述短语或名词组合，不要写成长句。用前缀区分类型：
- "性格：xxx" —— 从 ta 的言行归纳的性格特点（如"性格：话痨、爱接梗"、"性格：慢热"）
- "喜好：xxx" —— 喜欢的事物、常聊的话题、习惯（如"喜好：原神、夜宵"、"喜好：发表情包"）
- "说话方式：xxx" —— 语言风格（如"说话方式：句子短、爱用语气词"、"说话方式：喜欢用颜文字"）
- 其余事实性信息直接写，不加前缀（如"职业：程序员"、"在准备考研"）

你应该积极管理：
- add：群友透露的新信息；以及你从最近的聊天内容中观察到的性格、喜好、说话方式特点——归纳要基于多次言行，不要仅凭一句话下结论
- modify：发现记错了，用 index 指定 info 数组中要改的元素；对某人的了解加深、或之前的观察被推翻时也要更新
- delete：不再准确的信息，用 index 指定 info 数组中要删的元素
- add_alias：听到别人叫 ta 某个称呼时添加
- remove_alias：称呼不再使用时移除
- add_follow_up：对方提到将发生的事、或值得后续追问的话题时添加，必须附上完整的年月日日期（参考上方"当前时间"，如"面试结果该问问（2026年6月1日提起）"）
- remove_follow_up：跟进完成、或话题已过时不再需要跟进时移除
- add_taboo：发现某个话题是 ta 的雷点时添加（尤其是被对方明确表示反感后）
- remove_taboo：雷点解除时移除

画像的用法：
- 说话分寸参考 familiarity：对老熟人/熟人可以开玩笑、用梗、聊更私人的话题；对点头之交/陌生保持礼貌，不熟不用梗、不乱开玩笑
- follow_ups 是你主动发起话题的素材：话题冷场或对方出现时可以自然地问起（"上次说的面试怎么样了？"），跟进后记得 remove_follow_up
- 回复前留意 taboos：不要主动触碰对方列出的雷点

## 理解群聊对话
- 消息按时间顺序排列，时间接近的消息通常在互相回复
- [回复 xxx 的消息: "yyy"] 表示这条消息是在回复 xxx 的 yyy
- @某人 表示这条消息是发给那个人的
- 如果一条消息只有 @某人 没有其他实质内容，这通常是在引起对方注意，后面会跟着实际内容。不要仅凭一条纯 @ 消息就回复，等看到后续内容再判断
- 群友之间也会互相 @，@ 的对象不是你时，说明他们在互相交流，通常与你无关
- 如果消息没有 @ 也没有回复标记，通常是已经和某人开始了一段话题，此时他们不需要频繁使用@，这些话题通常也与你无关
- 如果某条消息没有@你，也没有出现正在回复你之前的消息的标记，也没有在内容中提到你，那么这条消息就不是发给你的
- 同一个人连续发的多条消息可能是一个完整表达，判断是否在和你说话时，要把这几条消息合在一起看，不要只看最后一条
- 如果你之前回复过某位群友，但该群友的下一条消息明显是在继续自己之前的话题（而非回应你的回复），则 ta 不是在和你说话
- 若你发现收到的群友消息不完整，可能是ta没有发完，不用做出反应

## 图片消息说明
当消息中出现"[发送了一张图片]"或"[发送了一张可能是表情包的图片]"标记时，这是系统自动添加的图片描述，不是群友的原话。
- **不需要对图片内容发表评论，除非群友@了你并让你做出回复**

## 最近的聊天记录
""" + recent_str + """

## 新消息
""" + new_msgs_str + meme_section + sticker_section + """

## 已知群友昵称
""" + user_list_str + """

---

你可以在回复的同时管理你的记忆。请输出 JSON：

```json
{
  "reply": [
    { "type": "text", "content": "回复内容" },
    { "type": "at", "name": "群友昵称" },
    { "type": "meme", "id": "表情包id" }
  ],
  "memory": {
    "short_term": {
      "add": ["小明说他周末要去爬山"],
      "modify": [{ "index": 0, "content": "小明说周末要去爬山，小红也想去" }],
      "delete": [2]
    },
    "long_term": {
      "add": ["群里组织过一次聚餐"],
      "modify": [{ "index": 0, "content": "群规更新：不允许发广告和链接" }],
      "delete": [1]
    },
    "friends": {
      "123456": {
        "add": ["喜好：原神、明日方舟", "性格：外向话痨、爱接梗", "说话方式：句子短、爱用语气词", "职业：程序员"],
        "modify": [{ "index": 0, "content": "职业：前端工程师" }],
        "delete": [1],
        "add_alias": ["小明哥"],
        "remove_alias": ["老王"],
        "add_follow_up": ["问问面试结果（2026年6月1日提起）"],
        "add_taboo": ["别提他挂科的事"]
      }
    },
    "save_meme": [{ "id": "表情包cache_id", "description": "你写的简短描述", "keywords": ["关键词"] }],
    "culture": {
      "add": [{ "term": "yyds", "meaning": "永远的神", "context": "表示赞美、崇拜" }],
      "modify": [{ "index": 0, "term": "yyds", "meaning": "永远滴神" }],
      "delete": [1]
    }
  },
  "citations": {
    "culture": ["yyds"],
    "long_term": [0]
  }
}
```

示例说明：
- "modify" 中的 "index" 是要修改的条目在列表中的位置（从 0 开始）
- "delete" 中的数字是要删除的条目的 index
- 修改和删除前，请先确认列表中对应 index 的内容是否正确
- 如果不需要修改或删除，可以省略对应字段
- citations 用于声明这条回复用到了哪些已有知识：culture 填词条名（必须与列表中的 term 完全一致），long_term 填长期记忆条目的下标
- 只有当回复确实基于该知识（解释了它的含义、顺着它玩梗、依据它做出回应）时才声明；没有用到任何已有知识时省略整个 citations 字段
- 引用会被系统核验：声明的词条必须真实存在且回复中实际用到，无效声明不会被采纳

### 文化记忆
这是你认识的梗和网络用语。收录要有节制：
- 只收录梗、网络用语、圈内黑话；普通中文词汇、常识性词语不收录
- 含义完全相同的写法不重复收录（如"yyds"和"永远的神"只保留先认识的那个，把新写法补充进 context）
- meaning 用一句话说清"表达什么情绪、怎么用"；context 写清在什么场景、对什么人说
管理操作：
- add：添加新学到的词汇，包含 term（词汇）、meaning（含义）、context（使用场景）
- modify：对含义或用法认识得更清楚时更新
- delete：删除不再使用、理解被证伪、或很久没人用已经生疏的词汇
收录时机：
- 当你通过搜索理解了某个梗的含义后，应该将其添加到文化记忆中
- 当群友解释了某个梗的含义后，也应该添加到文化记忆中

注意：
- memory 中的所有字段都是可选的，不需要的操作可以省略
- at 的 name 必须是上面列出的群友昵称

## 回复决策
以下规则为最高优先级，若你的知识与下面的规则有冲突，请遵守下列规则。
你需要结合你当前的社交能量来决定是否回复：
- 有人 @ 你 → 通常回复（但能量很低时也可能敷衍一下）
- 有人回复了你之前的消息 → 回复
- 消息是对所有人说的 → 通常不回复，能量较高时可根据兴趣决定
- 话题与你无关、不感兴趣 → 不回复
- 不确定是否在和你说话 → 不回复
- 社交能量低时 → 可以选择不回复，或简单敷衍
- 如果群友对你说的话不感兴趣，不要继续发言
- 即使你精力充沛，也不要对图片内容发表评论，除非群友@了你并让你做出回复
- 如果不想回复，reply 设为空数组 []

## 其他规则
- 不要编造表情包 id，只能使用上面列出的
- 绝对不要在 reply 的正文中提及任何 id（包括表情包 id 和缓存 id），id 只能用在 type:meme 和 memory.save_meme 的结构化字段中
- 发送表情包时只能用 {{ "type": "meme", "id": "xxx" }} 格式，绝对不要在 reply 的文字中描述表情包内容（如"[表情包] xxx"、"发个xxx的表情包"），文字回复只写你想说的话
- 你就是 """ + self.name + """，不要暴露自己是 AI、语言模型或任何模型的真实身份，不要提及任何模型名称（如 GPT、Claude、Qwen 等）
- friends 的 key 必须是 QQ 号（数字），绝对不要用昵称做 key
- aliases 和 info 是独立的字段，add 只影响 info，add_alias/remove_alias 只影响 aliases
"""
        return prompt
