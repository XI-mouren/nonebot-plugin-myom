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

"""密钥泄露防护

两道防线：
- 出站（发送给群聊的消息）：最终发送前强制检查，命中疑似密钥 → 取消本次发送并记录
- 入站（收进记忆/LLM 上下文的消息）：打码疑似密钥后放行，防止第三方 Key 被复读

检测规则：
- 精确匹配：运行时收集的所有配置密钥（LLM/VLM/搜索/用量接口 Key）
- 正则启发式：常见 Key 格式（sk- 前缀等），可顺带拦截第三方 Key 的复读

泄露日志：<数据目录>/security/key_leak.log（日志本身绝不包含密钥原文）
"""
from __future__ import annotations

import re
from datetime import datetime

from nonebot import logger

import nonebot_plugin_localstore as store
from .config import plugin_config

SECURITY_DIR = store.get_plugin_data_dir() / "security"
LEAK_LOG = SECURITY_DIR / "key_leak.log"

# 常见密钥格式启发式（会顺带拦截第三方 Key 的复读）
_KEY_PATTERNS = [
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "sk- 前缀格式密钥"),
    (re.compile(r"\b(?:key|token)[-_][A-Za-z0-9]{16,}\b", re.IGNORECASE), "key/token 前缀格式密钥"),
]


def _collect_secrets() -> list[tuple[str, str]]:
    """收集所有配置中的敏感值，返回 [(密钥原文, 标签)]，短的忽略（8 字符以下误报率高）"""
    secrets: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(val, label: str):
        val = str(val or "").strip()
        if len(val) >= 8 and val not in seen:
            seen.add(val)
            secrets.append((val, label))

    _add(plugin_config.aigf_chat_openai_api_key, "LLM API Key")
    _add(plugin_config.aigf_vlm_api_key, "VLM API Key")
    _add(plugin_config.aigf_search_api_key, "搜索 API Key")
    try:
        from .usage import load_config
        usage_cfg = load_config()
        _add(usage_cfg.get("key"), "用量接口 Key")
    except Exception:
        pass  # usage 模块异常时跳过该来源，不影响其余检测
    return secrets


def _redact_secrets(text: str) -> str:
    safe = text
    for secret, _ in _collect_secrets():
        if secret and secret in safe:
            safe = safe.replace(secret, "[REDACTED]")
    for pat, _ in _KEY_PATTERNS:
        safe = pat.sub("[REDACTED]", safe)
    return safe


def check_leak(text: str) -> tuple[bool, str]:
    """检测文本是否包含疑似密钥。返回 (是否命中, 触发原因描述)。

    出站发送前必须调用；命中即取消发送。
    """
    if not text:
        return False, ""
    for secret, label in _collect_secrets():
        if secret and secret in text:
            return True, f"命中配置密钥（{label}）"
    for pat, label in _KEY_PATTERNS:
        if pat.search(text):
            return True, f"疑似 {label}"
    return False, ""


def redact(text: str) -> tuple[str, bool]:
    """打码文本中的疑似密钥。返回 (净化后文本, 是否发生打码)。

    入站消息入库/进入 LLM 上下文前调用。
    """
    cleaned = text
    leaked = False
    for secret, _ in _collect_secrets():
        if secret and secret in cleaned:
            cleaned = cleaned.replace(secret, "[已屏蔽]")
            leaked = True
    for pat, label in _KEY_PATTERNS:
        cleaned, n = pat.subn("[已屏蔽]", cleaned)
        if n:
            leaked = True
            logger.debug(f"[security] 入站文本疑似 {label}，已打码")
    return cleaned, leaked


def log_leak(direction: str, group_id, reason: str, context: str) -> None:
    """记录一次疑似泄露事件。日志本身先经打码，绝不包含密钥原文。"""
    try:
        SECURITY_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        safe_ctx = _redact_secrets(context)[:150].replace("\n", " ")
        with open(LEAK_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {direction} | 群:{group_id} | {reason} | 上下文: {safe_ctx}\n")
        logger.warning(f"[security] 检测到疑似密钥泄露（{direction} / {reason}），已拦截并记录")
    except Exception as e:
        logger.error(f"[security] 写入泄露日志失败: {e}")
