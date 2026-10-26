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

"""时间上下文模块：让 LLM 知道当前的日期和时间。

LLM 本身没有时钟，所有需要"现在"的判断（相对时间、日期记录、跟进时机）
都必须由代码在每次构建 prompt 时注入。所有格式化入口集中在这里，
需要改时间表示方式（如加农历）只动这个文件。

时区不依赖服务器系统设置，由配置项 aigf_timezone_offset 控制
（默认 UTC+8 北京时间），海外服务器部署时改配置即可。
"""

from datetime import datetime, timedelta, timezone

from .config import plugin_config

_WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]


def _now() -> datetime:
    """当前时间（按配置的时区偏移，默认 UTC+8）"""
    tz = timezone(timedelta(hours=plugin_config.aigf_timezone_offset))
    return datetime.now(tz)


def get_date_str() -> str:
    """完整日期字符串，如 "2026年10月23日"（供记忆条目等复用）"""
    return _now().strftime("%Y年%m月%d日")


def get_time_context() -> str:
    """供 prompt 注入的时间上下文，如 "2026年10月23日 星期五 18:09"。
    每次调用都实时取当前时间，prompt 每轮构建一次所以天然是新鲜的。"""
    now = _now()
    weekday = _WEEKDAYS[now.weekday()]
    return f"{now.strftime('%Y年%m月%d日')} 星期{weekday} {now.strftime('%H:%M')}"
