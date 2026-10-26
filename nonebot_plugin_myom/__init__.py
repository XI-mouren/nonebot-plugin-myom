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

import asyncio
import base64
from dataclasses import dataclass, field
from datetime import datetime
import re
import ssl
import traceback

import anyio
import httpx
from nonebot import logger, on_command, on_message, require
from nonebot.rule import to_me
from nonebot.adapters import Message
from nonebot.adapters.onebot.v11 import (
    Bot, Event, GroupMessageEvent, MessageSegment, Message as OneBotMessage, PrivateMessageEvent,
)
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata
from openai import AsyncOpenAI

require("nonebot_plugin_localstore")

import nonebot_plugin_localstore as store


# 历史包名（从新到旧）：内部代号 → 上游原名
LEGACY_PLUGIN_DIRS = ("nonebot_plugin_muelsyse", "nonebot_plugin_aigf")


def _migrate_legacy_dirs() -> None:
    """包名变更后自动接管旧数据。

    历史包名：nonebot_plugin_aigf（上游原名）→ nonebot_plugin_muelsyse（内部代号）
    → nonebot_plugin_myom（当前）。localstore 按插件名分配目录，改名后新目录是空的；
    这里按「新 → 旧」顺序找第一个存在的旧目录，把 data/config/cache 整体拷过来，
    避免群友画像、记忆、表情包和用量记录「凭空消失」。
    只在目标目录不存在或为空时执行，旧目录保留不删（确认无误后可自行清理）。
    """
    import shutil

    for getter, label in (
        (store.get_plugin_data_dir, "数据"),
        (store.get_plugin_config_dir, "配置"),
        (store.get_plugin_cache_dir, "缓存"),
    ):
        try:
            new_dir = getter()
            if new_dir.exists() and any(new_dir.iterdir()):
                continue          # 新目录已有内容，绝不覆盖
            for old_name in LEGACY_PLUGIN_DIRS:
                old_dir = new_dir.parent / old_name
                if not old_dir.is_dir() or old_dir == new_dir:
                    continue
                new_dir.mkdir(parents=True, exist_ok=True)
                shutil.copytree(old_dir, new_dir, dirs_exist_ok=True)
                logger.info(f"[myom] 已从旧目录接管{label}：{old_dir} -> {new_dir}")
                break
        except Exception as e:
            logger.warning(f"[myom] 接管旧{label}目录失败（不影响新目录使用）：{e}")


_migrate_legacy_dirs()

from .client import LLMClient, make_http_client
from .config import Config, plugin_config
from . import energy
from .image_manager import IMAGE_CACHE_DIR, image_manager
from .meme_manager import AtMessage, MemeMessage, TextMessage, meme_manager
from .mem import Message as MMessage
from .presets import PRESETS, load_presets
from .session import Session
from .security import check_leak, log_leak, redact
# 用量统计模块：必须随插件导入即加载，导入时注册查询命令、4点定时任务与启动补录。
# 若只在函数内懒加载，命令将永不注册（群聊/私聊都用不了）。
from . import usage  # noqa: F401

__plugin_meta__ = PluginMetadata(
    name="AI 群友（MYOM）",
    description="群聊特化的拟人化 LLM 聊天机器人：分层记忆、群友画像、表情包学习、联网搜索、生物节律能量与 token 用量统计",
    usage=(
        "入群即用，无需 @ 也能自然接话。\n"
        "用量查询：日用量 [n] / 周用量 [n] / 月用量 [n] / 总用量 / 群用量 [n] / 用量价格 / 用量配置 / 用量帮助\n"
        "管理指令（需 @ 或私聊）：状态 / 重载节律 / 预设 / 设置预设 / 重置 / 清除用量 日|周|月|全部"
    ),
    type="application",
    config=Config,
    supported_adapters={"~onebot.v11"},
    homepage="https://github.com/XI-mouren/nonebot-plugin-myom",
    extra={
        "author": "XI-mouren",
        "upstream": "https://github.com/Funny1Potato/nonebot-plugin-aigf",
        "license": "AGPL-3.0-or-later",
    },
)


async def is_group_message(event: Event) -> bool:
    return isinstance(event, GroupMessageEvent)

async def is_private_message(event: Event) -> bool:
    return isinstance(event, PrivateMessageEvent)


def _make_llm_client() -> "LLMClient":
    """构造主 LLM 客户端。

    零配置友好：api_key / base_url 为空时给占位值，让 AsyncOpenAI 能完成构造
    （新版 openai SDK 对空凭证直接抛 OpenAIError）。占位值不会被真正用到——
    发请求前 client._llm_config_ready() 会拦截并告警。
    """
    return LLMClient(
        client=AsyncOpenAI(api_key=plugin_config.aigf_chat_openai_api_key or "sk-not-configured",
                           base_url=plugin_config.aigf_chat_openai_base_url or "https://placeholder.invalid/v1",
                           http_client=make_http_client()))


@dataclass
class GroupState:
    event: Event | None = None
    bot: Bot | None = None
    session: Session = field(default_factory=Session)
    messages_chunk: list[MMessage] = field(default_factory=list)
    last_message_time: float = 0.0
    client: LLMClient = field(default_factory=_make_llm_client)
    lock = asyncio.Lock()


_tasks: set[asyncio.Task] = set()
group_states: dict[int, GroupState] = {}


async def _resolve_user_id(bot: Bot, event: Event, nickname: str) -> int | None:
    if not isinstance(event, GroupMessageEvent):
        return None
    try:
        members = await bot.get_group_member_list(group_id=event.group_id)
        for m in members:
            if m.get("nickname", "").strip() == nickname:
                return m["user_id"]
    except Exception as e:
        logger.error(f"获取群成员列表失败: {e}")
    return None


async def spawn_state(state: GroupState):
    while True:
        await asyncio.sleep(1.0)
        async with state.lock:
            if not state.bot or not state.event:
                continue
            if not state.messages_chunk:
                continue
            now = asyncio.get_event_loop().time()
            reached_count = len(state.messages_chunk) >= plugin_config.aigf_batch_count
            # 根据最后消息状态选择超时时间
            last_msg = state.messages_chunk[-1] if state.messages_chunk else None
            effective_timeout = plugin_config.aigf_batch_timeout
            if last_msg and last_msg.is_at_only:
                # 纯@消息，等待用户发送后续内容
                effective_timeout = plugin_config.aigf_incomplete_timeout
            elif len(state.messages_chunk) >= 2:
                # 连续发送检测：最后两条消息来自同一用户且间隔在合并窗口内
                prev_msg = state.messages_chunk[-2]
                if (last_msg.user_id == prev_msg.user_id and
                        (last_msg.time - prev_msg.time).total_seconds() < plugin_config.aigf_merge_window):
                    effective_timeout = plugin_config.aigf_incomplete_timeout
            reached_time = (now - state.last_message_time) >= effective_timeout
            if not reached_count and not reached_time:
                continue
            messages_chunk = state.messages_chunk.copy()
            state.messages_chunk.clear()


        try:
            # 从缓存中获取待处理的表情包
            stickers = meme_manager.get_cached_stickers()
            logger.debug(f"[spawn_state] 处理 batch: {len(messages_chunk)} 条消息, 缓存表情包: {len(stickers)} 个")
            responses = await state.session.process(messages_chunk, state.client, cached_stickers=stickers)
            logger.debug(f"[spawn_state] LLM 返回 {len(responses) if responses else 0} 条回复")
            # 处理完成后清空缓存（只清空本次传给 LLM 的，避免跨 batch 重复）
            if stickers:
                meme_manager.clear_cache()
        except Exception as e:
            logger.error(f"Error: {e}")
            traceback.print_exc()
            continue

        if not responses:
            continue

        try:
            # 出站安全校验：任一回复命中疑似密钥 → 取消本次整批发送并记录
            joined_text = "\n".join(
                r.content if isinstance(r, TextMessage) else ""
                for r in responses
            )
            leaked, reason = check_leak(joined_text)
            if leaked:
                log_leak("outgoing", getattr(state.event, "group_id", "-"), reason, joined_text)
                logger.warning("[security] 已取消本次发送")
                continue

            pending = OneBotMessage()
            for response in responses:
                if isinstance(response, TextMessage):
                    pending.append(MessageSegment.text(response.content))
                elif isinstance(response, AtMessage):
                    uid = await _resolve_user_id(state.bot, state.event, response.user_name)
                    if uid:
                        pending.append(MessageSegment.at(uid))
                elif isinstance(response, MemeMessage):
                    if pending:
                        await state.bot.send(message=pending, event=state.event)
                        pending = OneBotMessage()
                    path = await meme_manager.resolve(response.meme_id)
                    if path:
                        await state.bot.send(message=MessageSegment.image(path), event=state.event)
                else:
                    if pending:
                        await state.bot.send(message=pending, event=state.event)
                        pending = OneBotMessage()
                    await state.bot.send(message=str(response), event=state.event)
            if pending:
                await state.bot.send(message=pending, event=state.event)
                logger.success("[发送] 消息已发送")
        except Exception as e:
            logger.error(f"发送消息失败: {e}")


# ---- 命令 ----

status_cmd = on_command(rule=to_me(), permission=SUPERUSER, cmd="status", aliases={"状态"}, priority=0, block=True)
set_role_cmd = on_command(rule=to_me() & is_group_message, permission=SUPERUSER, cmd="set_role", aliases={"设置角色"}, priority=0, block=True)
reset_cmd = on_command(rule=to_me() & is_group_message, permission=SUPERUSER, cmd="reset", aliases={"重置"}, priority=0, block=True)
presets_cmd = on_command(rule=to_me() & is_group_message, permission=SUPERUSER, cmd="presets", aliases={"preset"}, priority=0, block=True)
set_preset_cmd = on_command(rule=to_me() & is_group_message, permission=SUPERUSER, cmd="set_preset", aliases={"set_presets"}, priority=0, block=True)
reload_meme_cmd = on_command(rule=to_me() & is_group_message, permission=SUPERUSER, cmd="reload_meme", aliases={"重载表情包"}, priority=0, block=True)
reload_energy_cmd = on_command(rule=to_me() & is_group_message, permission=SUPERUSER, cmd="reload_energy", aliases={"重载节律"}, priority=0, block=True)
auto_chat = on_message(rule=is_group_message, priority=1, block=False)


def _ensure_group_state(group_id: int) -> GroupState:
    if group_id not in group_states:
        group_states[group_id] = GroupState(
            session=Session(id=str(group_id), name="小助手", role="一个友好的群聊助手"))
        group_states[group_id].client.group_id = str(group_id)  # 分群用量统计
        task = asyncio.create_task(spawn_state(group_states[group_id]))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
    return group_states[group_id]


def _resolve_group_id(event: Event, text: str) -> tuple[int | None, str]:
    """解析目标群号：群聊取当前群；私聊需首个参数为群号。

    返回 (群号, 剩余参数文本)；私聊未带合法群号时返回 (None, "")。
    """
    if isinstance(event, GroupMessageEvent):
        return event.group_id, text
    parts = text.split(maxsplit=1)
    if parts and parts[0].isdigit():
        return int(parts[0]), (parts[1] if len(parts) > 1 else "")
    return None, ""


def _check_group_enabled(group_id: int) -> str | None:
    """群未启用且无运行态时返回拒绝信息，否则 None"""
    if group_id not in plugin_config.aigf_enabled_groups and group_id not in group_states:
        return f"群 {group_id} 未在 AIGF_ENABLED_GROUPS 中启用"
    return None


@status_cmd.handle()
async def _(event: Event, args: Message = CommandArg()):
    gid, _ = _resolve_group_id(event, args.extract_plain_text().strip())
    if gid is None:
        await status_cmd.finish(
            "私聊用法：状态 <群号>\n例如：状态 123456789\n（群聊中直接发「状态」即可）")
    err = _check_group_enabled(gid)
    if err:
        await status_cmd.finish(err)
    state = _ensure_group_state(gid)
    await status_cmd.finish(f"群 {gid}\n" + state.session.status())


@set_role_cmd.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    parts = args.extract_plain_text().strip().split(" ", 1)
    if len(parts) != 2:
        await set_role_cmd.finish("用法: 设置角色 <名字> <设定>")
    state = _ensure_group_state(event.group_id)
    state.session.name, state.session.role = parts[0], parts[1]
    await set_role_cmd.finish(f"角色已设为: {parts[0]}\n设定: {parts[1]}")


@reset_cmd.handle()
async def _(event: GroupMessageEvent):
    state = _ensure_group_state(event.group_id)
    state.session = Session(id=str(event.group_id))
    await state.session.load_preset(plugin_config.aigf_default_preset)
    await reset_cmd.finish("已重置会话")


@presets_cmd.handle()
async def _():
    msg = "可用预设:\n" + "\n".join(f"- {k}: {v.name} {v.role}" for k, v in PRESETS.items() if not v.hidden)
    msg += "\n用法: set_preset <预设名>"
    await presets_cmd.finish(msg)


@set_preset_cmd.handle()
async def _(event: GroupMessageEvent, args: Message = CommandArg()):
    name = args.extract_plain_text().strip()
    if not name:
        await set_preset_cmd.finish("用法: set_preset <预设名>")
    state = _ensure_group_state(event.group_id)
    if await state.session.load_preset(name):
        await set_preset_cmd.finish(f"预设已加载: {name}")
    else:
        await set_preset_cmd.finish(f"不存在的预设: {name}")


@reload_meme_cmd.handle()
async def _():
    await meme_manager.load_all()
    await reload_meme_cmd.finish(
        f"已重载。管理员: {len(meme_manager.admin_memes)} 个, 自动收集: {len(meme_manager.collected_memes)} 个")


@reload_energy_cmd.handle()
async def _(event: GroupMessageEvent):
    if not plugin_config.aigf_energy_enabled:
        await reload_energy_cmd.finish("能量节律未启用（AIGF_ENERGY_ENABLED=false），当前走旧版线性能量逻辑")
    try:
        energy.load_config()          # 校验：非法配置直接报错，不覆盖运行中的配置
    except energy.EnergyConfigError as e:
        await reload_energy_cmd.finish(
            "energy.json 校验失败，未应用：\n- " + "\n- ".join(e.errors))
    cfg = energy.reload_config()
    # 应用到所有已存在的群会话
    for st in group_states.values():
        st.session._energy_cfg = cfg
        if st.session._energy is None:
            st.session._energy = energy.load_state(st.session.id, cfg)
            st.session.social_energy = st.session._energy.energy
        st.session._energy_on = True
    labels = "/".join(f"{v['label']}{v['window'][0]}-{v['window'][1]}" for v in cfg["states"].values())
    await reload_energy_cmd.finish(f"已重载 energy.json 并应用（{labels}）")


@auto_chat.handle()
async def handle_auto_chat(bot: Bot, event: GroupMessageEvent):
    group_id = event.group_id
    if group_id not in plugin_config.aigf_enabled_groups:
        return
    # 跳过机器人自己的消息
    if event.get_user_id() == str(bot.self_id):
        return
    logger.success(f"[接收] 群{group_id} 收到消息 from {event.user_id}")

    state = _ensure_group_state(group_id)
    user_id = event.get_user_id()

    # 能量节律预判：休眠/疲倦期且未被 @ → 连图片识别都跳过（省 token，也符合"睡着了"）
    block_reason = state.session.energy_blocked(event.is_tome())
    if block_reason:
        state.session.settle_blocked()
        logger.debug(f"[energy] 群{group_id} {block_reason} 拦截非 @ 消息，直接忽略")
        return

    async with state.lock:
        message_content, is_at_only = await message2MMessage(
            bot_name=state.session.name, group_id=group_id,
            message=event.original_message, bot=bot, state=state)

    if not message_content:
        return

    # 入站安全校验：打码疑似密钥（防止第三方 Key 进入记忆与 LLM 上下文）
    message_content, redacted = redact(message_content)
    if redacted:
        log_leak("incoming", group_id, "入站消息含疑似密钥（已打码）", message_content)

    try:
        user_info = await bot.get_group_member_info(group_id=group_id, user_id=int(user_id))
        nickname = user_info.get("nickname") or str(user_id)
    except Exception:
        nickname = str(user_id)

    async with state.lock:
        state.event = event
        state.bot = bot
        state.messages_chunk.append(MMessage(time=datetime.now(), user_name=nickname, user_id=user_id, content=message_content, is_at_only=is_at_only))
        state.last_message_time = asyncio.get_event_loop().time()
    # 自动更新 QQ 昵称
    try:
        qq_nickname = user_info.get("nickname", "") if 'user_info' in dir() else ""
        if qq_nickname:
            await state.session.memory.record_member_activity(user_id, qq_nickname)
    except Exception:
        pass


async def _extract_text(message_content, bot: Bot, group_id: int) -> str:
    """从消息中提取纯文本（用于 reply 上下文）"""
    try:
        if isinstance(message_content, str):
            msg = OneBotMessage(message_content)
        elif isinstance(message_content, list):
            msg = OneBotMessage()
            for seg in message_content:
                if isinstance(seg, dict) and "type" in seg:
                    msg.append(MessageSegment(type=seg["type"], data=seg.get("data", {})))
        else:
            return str(message_content)[:50]
        text = msg.extract_plain_text().strip()
        return text[:50] if text else "（非文字消息）"
    except Exception:
        return "（无法获取）"


async def message2MMessage(bot_name: str, group_id: int, message: Message, bot: Bot, state: GroupState) -> tuple[str, bool]:
    content = ""
    has_non_at_content = False
    logger.debug(f"[消息解析] 开始解析消息，共 {len(list(message))} 个 segment")
    for seg in message:
        logger.debug(f"[消息解析] segment type={seg.type}")
        if seg.type == "text":
            text = seg.data.get("text", "")
            content += text
            if text.strip():
                has_non_at_content = True
        elif seg.type in ("image", "emoji"):
            try:
                url = seg.data.get("url", "")
                cache_path = IMAGE_CACHE_DIR / "raw"
                cache_path.mkdir(parents=True, exist_ok=True)
                key = re.search(r"[?&]fileid=([a-zA-Z0-9_-]+)", url)
                key = key.group(1) if key else None
                if key and (cache_path / key).exists():
                    async with await anyio.open_file(cache_path / key, "rb") as f:
                        image_bytes = await f.read()
                else:
                    ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)
                    ssl_ctx.set_ciphers("ALL:@SECLEVEL=1")
                    async with httpx.AsyncClient(verify=ssl_ctx) as client:
                        resp = await client.get(url)
                        resp.raise_for_status()
                        image_bytes = resp.content
                    if key:
                        async with await anyio.open_file(cache_path / key, "wb") as f:
                            await f.write(image_bytes)

                is_sticker = seg.data.get("sub_type") == 1
                image_base64 = base64.b64encode(image_bytes).decode()

                if plugin_config.aigf_image_mode == "llm":
                    cache_id = await meme_manager.save_to_cache(image_bytes=image_bytes, description="", emotion="")
                    logger.debug(f"[缓存] LLM模式 - 图片已缓存, cache_id={cache_id}")
                    content += f"\n[发送了一张图片, id: {cache_id}]\n"
                else:
                    desc = await image_manager.get_image_description(
                        image_base64=image_base64, is_sticker=is_sticker, group_id=str(group_id))
                    logger.info(f"[VLM] 描述: {desc.description if desc else None}")
                    if desc:
                        cache_id = await meme_manager.save_to_cache(
                            image_bytes=image_bytes, description=desc.description, emotion=desc.emotion,
                        )
                        if is_sticker:
                            content += f"\n[发送了一张可能是表情包的图片, id: {cache_id}] [情感:{desc.emotion}] [内容:{desc.description}]\n"
                        else:
                            content += f"\n[发送了一张图片, id: {cache_id}] [内容:{desc.description}]\n"
            except Exception as e:
                logger.error(f"图片处理错误: {e}")
                content += "\n[图片加载失败]\n"
            has_non_at_content = True
        elif seg.type == "at":
            uid = seg.data.get("qq")
            if not uid:
                continue
            if str(uid) == str(bot.self_id):
                content += f" @{bot_name} "
            else:
                try:
                    info = await bot.get_group_member_info(group_id=group_id, user_id=int(uid))
                    content += f" @{info.get('nickname') or uid} "
                except Exception:
                    content += f" @{uid} "
        elif seg.type == "reply":
            has_non_at_content = True
            try:
                reply_msg_id = seg.data.get("message_id")
                if reply_msg_id:
                    original = await bot.get_msg(message_id=int(reply_msg_id))
                    if original and "message" in original:
                        replied_text = await _extract_text(original["message"], bot, group_id)
                        replied_sender = original.get("sender", {}).get("nickname", "未知")
                        content += f"[回复 {replied_sender} 的消息: \"{replied_text}\"] "
            except Exception:
                pass
    return content.strip(), not has_non_at_content


# ---- 启动 ----

import shutil

from nonebot import get_driver


def _migrate_cache_dirs():
    """历史版本把 sticker_cache/image_cache 放在系统 cache 目录下（可能被系统自动清理），
    统一迁移到数据目录下，与记忆、表情包库集中存放。"""
    data_base = store.get_plugin_data_dir()
    old_cache_base = store.get_plugin_cache_dir()
    for name in ("sticker_cache", "image_cache"):
        old, new = old_cache_base / name, data_base / name
        if not old.exists():
            continue
        new.mkdir(parents=True, exist_ok=True)
        moved = 0
        for f in old.iterdir():
            target = new / f.name
            if target.exists():
                continue
            try:
                shutil.move(str(f), str(target))
                moved += 1
            except Exception as e:
                logger.error(f"[迁移] {f.name} 移动失败: {e}")
        try:
            old.rmdir()  # 旧目录空了就移除，留着也不影响
        except OSError:
            pass
        logger.success(f"[迁移] {name} 已迁移至数据目录（{moved} 个文件）")


@get_driver().on_startup
async def _():
    _migrate_cache_dirs()
    # 零配置友好：配置缺失只告警不抛异常，让插件在任何配置状态下都能完成加载
    if not plugin_config.aigf_chat_openai_api_key or not plugin_config.aigf_chat_openai_base_url:
        logger.warning(
            "[启动] LLM 未配置（AIGF_CHAT_OPENAI_API_KEY / AIGF_CHAT_OPENAI_BASE_URL 为空）："
            "插件已加载，但对话请求会被跳过。配置 .env 后重启生效。"
        )
    logger.success(f"[启动] 图片理解模式: {plugin_config.aigf_image_mode}")
    if plugin_config.aigf_search_enabled:
        logger.success(f"[启动] 联网搜索: 已启用 | 模式: {plugin_config.aigf_search_mode} | API: {plugin_config.aigf_search_api}")
    else:
        logger.info("[启动] 联网搜索: 未启用")
    await load_presets()
    await meme_manager.load_all()
    # 加载默认预设到所有群
    default = plugin_config.aigf_default_preset
    for gid in plugin_config.aigf_enabled_groups:
        state = _ensure_group_state(gid)
        await state.session.load_preset(default)
