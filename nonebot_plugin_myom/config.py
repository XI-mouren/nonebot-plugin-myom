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

from nonebot import get_driver, get_plugin_config
from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class Config(BaseModel):
    # populate_by_name: 允许直接用字段名构造（本地验证脚本大量依赖此方式）
    model_config = ConfigDict(populate_by_name=True)

    aigf_chat_openai_api_key: str = Field("", validation_alias=AliasChoices("myom_chat_openai_api_key", "aigf_chat_openai_api_key"), description="LLM API Key；为空时插件可正常加载（零配置友好），调用对话时跳过并告警")
    aigf_chat_openai_model: str = Field("gpt-3.5-turbo", validation_alias=AliasChoices("myom_chat_openai_model", "aigf_chat_openai_model"), description="LLM 模型名称")
    aigf_chat_openai_base_url: str = Field("", validation_alias=AliasChoices("myom_chat_openai_base_url", "aigf_chat_openai_base_url"), description="LLM API 地址；为空时插件可正常加载（零配置友好），调用对话时跳过并告警")
    aigf_json_mode: bool = Field(True, validation_alias=AliasChoices("myom_json_mode", "aigf_json_mode"), description="是否强制 LLM 输出 JSON（需要模型支持 response_format）")
    aigf_image_mode: str = Field("vlm", validation_alias=AliasChoices("myom_image_mode", "aigf_image_mode"), description="图片理解模式: vlm=使用独立VLM, llm=直接用LLM看图")
    aigf_vlm_enabled: bool = Field(True, validation_alias=AliasChoices("myom_vlm_enabled", "aigf_vlm_enabled"), description="是否启用图片理解（仅 vlm 模式有效）")
    aigf_vlm_model: str = Field("", validation_alias=AliasChoices("myom_vlm_model", "aigf_vlm_model"), description="VLM 模型名称")
    aigf_vlm_base_url: str = Field("", validation_alias=AliasChoices("myom_vlm_base_url", "aigf_vlm_base_url"), description="VLM API 地址")
    aigf_vlm_api_key: str = Field("", validation_alias=AliasChoices("myom_vlm_api_key", "aigf_vlm_api_key"), description="VLM API Key（为空时使用 chat_openai_api_key）")
    aigf_vlm_max_tokens: int = Field(0, validation_alias=AliasChoices("myom_vlm_max_tokens", "aigf_vlm_max_tokens"), description="VLM 单次输出上限 tokens；默认 0 = 不限制（不传该参数）。带思考过程的推理型视觉模型必须保持 0，否则 token 会被 reasoning 吃光、描述返回空；普通视觉模型可设 150 降低看图消耗")
    aigf_enabled_groups: list[int] = Field(default_factory=list, validation_alias=AliasChoices("myom_enabled_groups", "aigf_enabled_groups"), description="启用的群号列表")
    aigf_timezone_offset: float = Field(8.0, validation_alias=AliasChoices("myom_timezone_offset", "aigf_timezone_offset"), description="时区偏移（小时），默认 UTC+8 北京时间；服务器在海外时无需改系统时区，直接配此项（支持半时区如 5.5）")
    aigf_meme_enabled: bool = Field(True, validation_alias=AliasChoices("myom_meme_enabled", "aigf_meme_enabled"), description="是否启用表情包功能")
    aigf_meme_max_count: int = Field(500, validation_alias=AliasChoices("myom_meme_max_count", "aigf_meme_max_count"), description="自动收集的表情包最大数量（达到后触发清理，删除使用频率最低的 aigf_meme_reserve_count 个）")
    aigf_meme_candidates: int = Field(20, validation_alias=AliasChoices("myom_meme_candidates", "aigf_meme_candidates"), description="每次构建 prompt 时注入的表情包候选数量（按当前对话关键词预筛选，避免全量注入撑爆上下文）")
    aigf_meme_reserve_count: int = Field(50, validation_alias=AliasChoices("myom_meme_reserve_count", "aigf_meme_reserve_count"), description="达到上限时永久保留的空位数：清理使用频率最低的条目，使其回落到 max_count - reserve")
    # 图片缓存清理（候选池只进不出会无限膨胀，需定期回收）
    aigf_cache_clean_enabled: bool = Field(True, validation_alias=AliasChoices("myom_cache_clean_enabled", "aigf_cache_clean_enabled"), description="是否启用图片缓存每日自动清理（凌晨 4:05 执行；只删过期的候选池与原图缓存，不动表情包库和描述缓存）")
    aigf_sticker_cache_keep_days: int = Field(7, validation_alias=AliasChoices("myom_sticker_cache_keep_days", "aigf_sticker_cache_keep_days"), description="sticker_cache 候选池保留天数：超期删除。候选描述只存在于内存，重启后这些文件已无任何引用，留着纯占空间")
    aigf_image_raw_keep_days: int = Field(7, validation_alias=AliasChoices("myom_image_raw_keep_days", "aigf_image_raw_keep_days"), description="image_cache/raw 原图下载缓存保留天数（删了不影响功能，下次用到重新下载）")
    aigf_image_desc_keep_days: int = Field(0, validation_alias=AliasChoices("myom_image_desc_keep_days", "aigf_image_desc_keep_days"), description="VLM 描述缓存（image_cache/*.json）保留天数；0=永久保留。这是唯一花过钱的缓存，删了同一张图再出现要重新调 VLM")
    aigf_default_preset: str = Field("default", validation_alias=AliasChoices("myom_default_preset", "aigf_default_preset"), description="默认预设名称")
    aigf_batch_count: int = Field(10, validation_alias=AliasChoices("myom_batch_count", "aigf_batch_count"), description="攒满多少条消息后触发 LLM 请求")
    aigf_batch_timeout: float = Field(30.0, validation_alias=AliasChoices("myom_batch_timeout", "aigf_batch_timeout"), description="距最后一条消息多少秒后触发 LLM 请求")
    aigf_incomplete_timeout: float = Field(40.0, validation_alias=AliasChoices("myom_incomplete_timeout", "aigf_incomplete_timeout"), description="检测到消息可能不完整时的等待时间（秒），如纯@消息或连续发送")
    aigf_recent_messages: int = Field(15, validation_alias=AliasChoices("myom_recent_messages", "aigf_recent_messages"), description="prompt 中包含的最近历史消息条数")
    aigf_merge_window: float = Field(5.0, validation_alias=AliasChoices("myom_merge_window", "aigf_merge_window"), description="消息合并时间窗口（秒），同一用户在此时间内的连续消息会被合并为一条")
    # 社交能量
    aigf_energy_baseline: float = Field(0.7, validation_alias=AliasChoices("myom_energy_baseline", "aigf_energy_baseline"), description="社交能量基线，能量会自然向此值回归（0.0~1.0）；仅旧版线性能量逻辑使用")
    aigf_familiarity_decay_enabled: bool = Field(True, validation_alias=AliasChoices("myom_familiarity_decay_enabled", "aigf_familiarity_decay_enabled"), description="群友画像的熟悉度是否每日衰减（凌晨 4 点与用量统计同频执行；关闭则回退旧的 30 天封顶规则）")
    aigf_familiarity_decay_base: float = Field(0.0015, validation_alias=AliasChoices("myom_familiarity_decay_base", "aigf_familiarity_decay_base"), description="熟悉度每日基础衰减量：任何分数都会掉（0.0015 ≈ 满分每天掉 0.15%）")
    aigf_familiarity_decay_gain: float = Field(0.02, validation_alias=AliasChoices("myom_familiarity_decay_gain", "aigf_familiarity_decay_gain"), description="低分加速项：每日额外衰减 = gain ×(1−熟悉度)，分数越低掉得越快")
    aigf_familiarity_forget_below: float = Field(0.05, validation_alias=AliasChoices("myom_familiarity_forget_below", "aigf_familiarity_forget_below"), description="熟悉度低于该值时删除画像（模拟彻底忘记这个人）")
    aigf_energy_enabled: bool = Field(True, validation_alias=AliasChoices("myom_energy_enabled", "aigf_energy_enabled"), description="是否启用生物节律能量机制（关闭则回退旧版线性能量逻辑）")
    aigf_energy_config: str = Field("", validation_alias=AliasChoices("myom_energy_config", "aigf_energy_config"), description="energy.json 路径（留空用配置目录下的 nonebot_plugin_myom/energy.json）")
    # 联网搜索
    aigf_search_enabled: bool = Field(False, validation_alias=AliasChoices("myom_search_enabled", "aigf_search_enabled"), description="是否启用联网搜索")
    aigf_search_mode: str = Field("two_stage", validation_alias=AliasChoices("myom_search_mode", "aigf_search_mode"), description="搜索模式: two_stage=两阶段调用, function_call=使用function calling")
    aigf_search_api: str = Field("tavily", validation_alias=AliasChoices("myom_search_api", "aigf_search_api"), description="搜索API: tavily/bocha/bing/deepseek")
    aigf_search_api_key: str = Field("", validation_alias=AliasChoices("myom_search_api_key", "aigf_search_api_key"), description="搜索 API Key（tavily/bocha/bing/deepseek 模式必填）")
    aigf_search_max_results: int = Field(3, validation_alias=AliasChoices("myom_search_max_results", "aigf_search_max_results"), description="最大搜索结果数")
    aigf_proxy_enabled: bool = Field(False, validation_alias=AliasChoices("myom_proxy_enabled", "aigf_proxy_enabled"), description="是否启用代理")
    aigf_http_proxy: str = Field("", validation_alias=AliasChoices("myom_http_proxy", "aigf_http_proxy"), description="HTTP 代理地址，如 http://127.0.0.1:7890")
    aigf_https_proxy: str = Field("", validation_alias=AliasChoices("myom_https_proxy", "aigf_https_proxy"), description="HTTPS 代理地址，如 http://127.0.0.1:7890")

    # ---- 用量统计 / 查询（config.json 中的同名键会覆盖这里的值）----
    aigf_usage_base: str = Field("", validation_alias=AliasChoices("myom_usage_base", "aigf_usage_base"), description="用量接口地址（WBY 风格，如 https://api.example.com）")
    aigf_usage_key: str = Field("", validation_alias=AliasChoices("myom_usage_key", "aigf_usage_key"), description="用量接口 Key（Bearer 认证）")
    aigf_usage_price_input: float = Field(0.0, validation_alias=AliasChoices("myom_usage_price_input", "aigf_usage_price_input"), description="输入单价（元/1M tokens），0 表示不折算花费")
    aigf_usage_price_output: float = Field(0.0, validation_alias=AliasChoices("myom_usage_price_output", "aigf_usage_price_output"), description="输出单价（元/1M tokens），0 表示不折算花费")
    aigf_usage_url: str = Field("", validation_alias=AliasChoices("myom_usage_url", "aigf_usage_url"), description="自定义用量请求 URL 模板（支持 {base}/{key} 占位符），留空则用 base + /api/usage/token/")
    aigf_usage_method: str = Field("GET", validation_alias=AliasChoices("myom_usage_method", "aigf_usage_method"), description="自定义用量请求方法：GET 或 POST")
    aigf_usage_auth_header: str = Field("Bearer {key}", validation_alias=AliasChoices("myom_usage_auth_header", "aigf_usage_auth_header"), description="认证头模板；留空则发送 Bearer {key}")
    aigf_usage_body: str = Field("", validation_alias=AliasChoices("myom_usage_body", "aigf_usage_body"), description="POST 请求体 JSON 模板（支持 {key}/{base} 占位符）")
    aigf_usage_fields: str = Field("", validation_alias=AliasChoices("myom_usage_fields", "aigf_usage_fields"), description='响应字段映射 JSON，如 {"total_used": "data.total_used"}；留空自动识别 WBY/one-api 格式')
    aigf_usage_quota_per_yuan: float = Field(0.0, validation_alias=AliasChoices("myom_usage_quota_per_yuan", "aigf_usage_quota_per_yuan"), description="额度换算系数：多少 quota 等于 1 元（如 73000）；0 表示不折算成金额")


plugin_config: Config = get_plugin_config(Config)
global_config = get_driver().config
