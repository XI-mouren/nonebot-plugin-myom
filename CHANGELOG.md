# CHANGELOG

## 版本历史

> 结构说明：本文件上半部分是**逐版本变更**，下半部分（`AI-group-friend`）是当初相对上游的技术重构说明，保持不变。
> 本包前身为 `nonebot-plugin-aigf`：上游 Funny1Potato 开发至 0.4.0，之后由本作者接续开发出 aigf 线的
> 0.5.0 / 0.6.0（中途曾用内部代号 `nonebot-plugin-muelsyse`）；更名后以 `nonebot-plugin-myom`
> **从 0.5.0 起算**，0.5.0 即本包第一个发布版本，与 aigf 线 0.5.0 同号不同物。前身版本记录保留在文末「前身历史」附录。

### 0.5.3（2026-10-28）

**行为修正：疲倦期不再硬拦截，只有睡眠态拦截消息**

原设计里疲倦期（01:00–09:00）会把非 @ 消息**直接丢弃**（不构建 prompt、不调 LLM）——
哪怕能量满格、档位显示「精力充沛」也一样。这与「时间片只影响能量结算与语气」的分层
设计自相矛盾：硬拦截应该是**睡眠态独有的语义**（睡着听不见），而不是某个时间段的行为
（深夜也能听见，只是困）。

- `should_block()` 移除疲倦期分支：非 @ 消息在疲倦期照常走 LLM 决策，
  接话倾向按能量档位计算（夜里能量低，自然倾向于不接话）
- 疲倦期在 prompt 里新增困倦语气提示（说话变蔫、句子更短），保留节律对表达的影响
- 删除 `decision.tired_block_non_at` 配置项；旧 `energy.json` 里残留的该字段会被忽略
  并打印一条 warning 提示删除
- 验证脚本第 34/51 项同步改为「疲倦期非 @ 放行」断言

> 注意：疲倦期不再拦截后，深夜非 @ 消息也会构建 prompt 并调用 LLM，**token 消耗会上升**；
> 若在意成本，可调低各档的 `reply_base` 与 `decision.reply_hint_k`，让模型在非 @ 时更少接话。

### 0.5.2（2026-10-27）

**修复：推理型 VLM 看图全部返回空**

`AIGF_VLM_MAX_TOKENS` 默认值曾是 `150`（aigf 0.6.0 引入的省钱措施，把单次看图输出从
约 930 tokens 压到上限内）。对带思考过程的视觉模型（Qwen3-VL、GLM-4.5V 等）来说，
150 个 token 会被 reasoning 阶段吃光，`content` 返回空字符串，表现为日志里只有一行
`[VLM] 描述: None`——图片理解静默失效，且看不出原因。

- 默认值改为 `0`：不向接口传 `max_tokens`，由服务端按模型默认上限处理
- 普通视觉模型仍可在 `.env` 手动设 `150` 来降低看图消耗
- README 配置表补注：推理型模型必须保持 `0`
- 验证脚本第 40 项补充「默认配置必须为 0」的断言

> 上游 0.4.0 的 `vlm.py` 本来就不传 `max_tokens`，这个限制是 aigf 0.6.0 后加的；本次相当于回到「默认不限制、需要时再收紧」。

### 0.5.1（2026-10-27）

**修复：图片缓存无限膨胀**

`sticker_cache` 候选池只进不出——群消息里的每张图都会写入，而原来的
`cleanup_cache()` 定义了却**从未被调用**，注释里写的"7 天自动清理"是死代码。
长期运行后该目录会持续膨胀，白白占用磁盘。

- 新增每日 **4:05** 定时清理任务（错开 4:00 的用量统计），调用链终于接上
- 清理范围扩展到 `image_cache/raw` 原图下载缓存，并对 `image_cache/*.json`
  描述缓存提供可选的保留期（默认 `0` = 永久，避免重复支付 VLM 费用）
- 新增 `清理缓存` 管理指令：无参数清理过期文件、`全部` 无视保留期清空、
  `统计` 只看当前占用
- 保留期填 `0` 视为不清理（防止配置误填导致候选池被清空）；需要立即释放空间请用指令
- **修复 `AIGF_MEME_MAX_COUNT` 配成 `0` 时会把表情包库整个删光的问题**：现在 `0` / 负数
  一律视为「不限制」，绝不会因为配置写错清空资产
- 新增 4 项配置：`AIGF_CACHE_CLEAN_ENABLED` / `AIGF_STICKER_CACHE_KEEP_DAYS` /
  `AIGF_IMAGE_RAW_KEEP_DAYS` / `AIGF_IMAGE_DESC_KEEP_DAYS`

> 表情包库 `memes/` 和 VLM 描述缓存默认都不在清理范围内，无需担心误删资产。

### 0.5.0（2026-10-26）

**更名与发布**
- 包名 `nonebot-plugin-aigf` → `nonebot-plugin-myom`（原名已被上游占用，PyPI 不可重复）；
  中间曾短暂使用内部代号 `nonebot-plugin-muelsyse`，未对外发布，0.5.0 起统一为 `myom`
- 模块名 `nonebot_plugin_aigf` → `nonebot_plugin_myom`，可与上游并存安装、互不覆盖
- 首次启动按「`nonebot_plugin_muelsyse` → `nonebot_plugin_aigf`」顺序自动接管旧数据 / 配置 / 缓存目录（旧目录保留不删）
- 配置项新增 `MYOM_` 前缀，`AIGF_` 前缀继续兼容：两者可混写、`MYOM_` 优先，老 `.env` 无需改动
- 补齐 `build-system`、`keywords`、`classifiers`、`project.urls`，满足 PyPI 与 NoneBot 插件商店要求
- **零配置可加载**（商店收录硬性要求）：`AIGF_CHAT_OPENAI_API_KEY` / `AIGF_CHAT_OPENAI_BASE_URL` 改为默认空串，
  缺失时启动与调用仅告警不抛异常；VLM 缺 `MODEL` / `BASE_URL` 时自动降级为跳过图片描述（原为启动即 `ValueError`），
  对话请求在真正调用前由运行时守卫拦截

**新增：生物节律能量**
- 两层职责分离：**时间片只决定能量增减**（兴奋 17–01 / 平淡 09–17 / 疲倦 01–09），**回复表现由五档能量档位决定**
- 硬拦截：休眠期（能量 ≤ 阈值）与疲倦期，非 @ 消息直接丢弃——不构建 prompt、不调 LLM、不识别图片
- 休眠：时长区间随机、按睡眠速率恢复、到点自然醒并保底、被 @ 唤醒 +0.30 并进入困倦期
- 表情包概率前置：命中才注入候选（20/16/12/8/4），发送前校验白名单，单条最多 1 张
- 回复倾向估算只写进 prompt，不做硬性拦截
- `状态` 显示阶段 / 能量 / 档位 / 距切换；新增 `重载节律` 指令
- `AIGF_ENERGY_ENABLED=false` 可逐字节回退旧版线性能量行为（实测 prompt 与原版完全相同）

**调整：能量档位由五档改为四档**
- 删除 `threshold=0` 的「完全不想说话」末档：能量 ≤ `sleep.threshold`（0.15）时已进入睡眠态，该档永远不可达，配置里写的 0~0.2 宽实际只剩 0.05
- 睡眠改为**独立状态**，不再作为档位：0.15~0.20 未入睡时按末档「有点懒」兜底
- 旧 `energy.json` 里 `threshold<=0` 的末档会自动移除并告警，不会启动失败
- 配置校验新增两条：末档 `threshold` 必须 > 0；末档不得低于 `sleep.threshold`（否则该档落在睡眠区内永不可达）

**新增：熟悉度每日遗忘**
- `score' = score − [base + gain×(1−score)]`，分数越高掉得越少，越低掉得越快
- 低于 `AIGF_FAMILIARITY_FORGET_BELOW`（默认 0.05）→ 删除画像文件（忘记这个人）
- 凌晨 4:05 执行，离线按 `decay_date` 补扣，同日重复执行幂等

**修复**
- `decision.meme_max_per_reply: 0` 被 `or 1` 悄悄改成 1，无法真正禁发（现在 0 = 完全不发图）
- 表情包候选补足时返回 `(score, meme)` 元组而非 `Meme` 对象，构建 prompt 取 `m.id` 会抛 `AttributeError: 'tuple' object has no attribute 'id'`（命中不足、需要按使用频率补足时必现）
- 同步旧版维护线的全部用量与记忆修复
- `energy.now()` 时区偏移两次（系统已是 UTC+8 时多推 8 小时）
- 自然醒后 `sleep_until` 未清空（状态一直显示休眠，且永远无法再次入睡）

**用量统计健壮性**
- 快照 `total_used` 为非数字时会让 04:00 任务永久抛异常（增量、周/月汇总、日清理全停）→ 现清洗快照、丢弃坏值并在下个统计日重建基准
- `records.json` / `config.json` 语法损坏或带 UTF-8 BOM 时被静默当成空数据、随后覆盖 → 现先备份为 `*.corrupt` 再降级，读取兼容 BOM
- `config.json` 的 `body` 写成 JSON 对象时自定义 POST 接口必然失败 → 现统一转成 JSON 字符串
- 单价/额度字段写成 `"2元"`、`"1,234"` 等非纯数字时统计整条丢失或解析失败 → 现容错转换并告警
- 同一天/同一周/同一月出现重复记录时被静默丢一条 → 现累加合并
- `用量价格` 查询侧对非法单价崩溃 → 现容错

## AI-group-friend（基于 nonebot-plugin-nyaturingtest 重构）

### 架构变更

- **移除 HippoRAG**：删除基于知识图谱的长期记忆检索系统（`hippo_mem.py`、`siliconflow_embeddings.py`），改为 LLM 自主管理记忆
- **移除 VAD 情绪系统**：删除三维情感模型（`emotion.py`、`impression.py`、`profile.py`），用社交能量系统替代
- **移除三阶段对话流水线**：原版为"检索→反馈→对话"三阶段，每阶段各调一次 LLM；新版合并为单次 LLM 调用，同时完成对话决策和记忆管理
- **移除对话状态机**：原版有潜水/冒泡/对话三种状态，由 LLM 评估意愿 + 随机阈值决定；新版由社交能量 + LLM 自行决定是否发言
- **触发机制变更**：原版每 5-10 秒随机延迟处理；新版为攒够一定数量消息或超时后触发（数量和时间均可配置，默认 10 条 / 30 秒）
- **配置前缀变更**：`nyaturingtest_*` → `AIGF_*`
- **移除 SiliconFlow 依赖**：原版需要硅基流动 API Key 用于嵌入模型；新版不再需要
- **强制 JSON 输出**：新增 `AIGF_JSON_MODE` 配置，通过 `response_format` 确保 LLM 返回 JSON
- **Think 标签剥离**：`remove_leading_think()` 自动去除 LLM 输出中的 `<think>` 标签（兼容 Qwen 等模型）

### 新增功能

#### 表情包系统
- **表情包发送**：AI 从预配置的表情包列表中选择发送，通过 `MessageSegment.image()` 发送图片
- **表情包自动收集**：群聊中的图片经 VLM 分析后缓存，LLM 决定是否收藏，收藏时由 LLM 编写描述和关键词
- **双轨素材库**：管理员手动配置（`memes.json`）+ 自动收集（`collected.json`），管理员优先
- **缓存机制**：图片到达后先缓存到 `sticker_cache/`，LLM 处理后决定是否收藏
- **自动清理**：超过 `AIGF_MEME_MAX_COUNT` 上限时优先清理最近未使用的表情包

#### 社交能量系统
- 机器人拥有动态的"社交能量"（`social_energy`，范围 0.0~1.0，初始 0.75），替代原版的 VAD 情绪模型
- 每次消息处理时自然恢复（向基线 0.7 靠拢，可通过 `AIGF_ENERGY_BASELINE` 配置）+ 随机漂移（±0.08）
- 被 @ 时兴奋加成 +0.1，提升回复热情
- 回复后消耗能量：基础消耗 0.03 + 按回复文字长度增加（最多 0.15）
- 5 档能量描述注入 prompt：精力充沛 / 状态不错 / 一般般 / 有点懒 / 不想说话
- 形成自然的"话多→累了→安静→恢复→又想聊"周期

#### 四层记忆系统
- **短期记忆**：对话摘要 + 临时信息，LLM 可增删改，按群隔离存储
- **长期记忆**：事件、知识，LLM 可增删改，按群隔离存储
- **群友信息**：按 QQ 号全局共享存储，LLM 可增删改 info/aliases，系统自动更新 QQ 昵称和曾用昵称
- **文化记忆**：梗、网络用语、流行语，LLM 主动学习，根据聊天内容自动匹配注入 prompt

#### 联网搜索系统
- **两阶段模式**：LLM 先分析是否需要搜索，需要时调用搜索 API，再带搜索结果生成回复
- **Function Calling 模式**：LLM 自主决定是否调用搜索工具（需要模型支持 function calling）
- **Tavily API**（默认）：专为 AI 设计，返回格式友好（可选依赖 `tavily-python`）
- **Bocha AI**（备选）：国产 AI 搜索 API，中文搜索效果好，国内直连（无需额外依赖）
- **Bing Web Search**：微软必应搜索（无需额外依赖）
- **搜索结果重试**：LLM 返回非 JSON 且使用了搜索结果时，自动去掉搜索结果重试一次

#### 回复风格自然化
- Prompt 新增"回复风格"段落，指导 LLM 像发微信一样口语化
- 鼓励使用网络用语（"哈哈"、"233"、"确实"、"666"等）
- 回复长度有变化：有时一两个字，有时几句话
- 允许敷衍回复，不再对每条消息都认真回复

#### 消息合并
- 新增 `_merge_consecutive()` 方法，同一用户在合并窗口内的连续消息在 prompt 构建时合并
- 底层数据不修改，仅在展示给 LLM 时合并
- 解决群友分多次发送导致 LLM 看到碎片化上下文的问题

#### 不完整消息检测
- 纯 @ 消息或检测到同一用户连续发送（间隔 < `AIGF_MERGE_WINDOW`）时，使用更长的超时 `AIGF_INCOMPLETE_TIMEOUT`（默认 40 秒）
- 给用户更多时间发送完整内容，避免在消息未完成时触发处理

#### 其他新增
- **图片理解双模式**：`vlm` 模式（独立 VLM 分析）和 `llm` 模式（LLM 直接看图），通过 `AIGF_IMAGE_MODE` 配置
- **回复上下文理解**：消息中包含 reply 标记（`[回复 xxx 的消息: "yyy"]`），LLM 可理解对话回复关系
- **纯 @ 消息检测**：`Message` 新增 `is_at_only` 字段，识别纯 @ 消息并延长等待时间
- **Bot 回复记录**：bot 自己的文字、表情包、@ 回复都会存入最近消息，LLM 可看到自己之前说了什么
- **代理支持**：通过 `AIGF_PROXY_ENABLED`、`AIGF_HTTP_PROXY`、`AIGF_HTTPS_PROXY` 配置 HTTP 代理
- **启动日志增强**：启动时显示图片理解模式和联网搜索状态

### 移除功能

| 原版功能 | 说明 |
|---------|------|
| HippoRAG 长期记忆 | 替换为 LLM 自主管理的长期记忆 |
| VAD 三维情感模型 | 替换为社交能量系统 |
| 人物印象系统（PersonProfile） | 替换为群友信息（LLM 管理） |
| 对话状态机（潜水/冒泡/对话） | 替换为社交能量 + LLM 自主决策 |
| 三阶段流水线（检索→反馈→对话） | 合并为单次 LLM 调用 |
| 对话总结（chat_summary） | 功能被短期记忆和最近消息替代 |
| `help` / `帮助` 命令 | 群聊和私聊均已移除 |
| `calm` / `冷静` 命令 | 无情绪系统，不再需要 |
| `role` / `当前角色` 命令 | 功能合并到 `status` 命令 |
| `list_groups` 命令 | 私聊命令已移除 |
| 私聊命令支持 | 仅保留群聊命令 |
| SiliconFlow API Key | 不再需要嵌入模型 |
| 预设中的 relationships/events/bot_self 字段 | 简化为 name/role/knowledges/hidden |
| 会话状态持久化（JSON 文件） | 替换为 JSON 文件持久化记忆 |

### 配置变更

#### 移除的配置项

| 原配置项 | 说明 |
|---------|------|
| `nyaturingtest_chat_openai_api_key` | 替换为 `AIGF_CHAT_OPENAI_API_KEY` |
| `nyaturingtest_chat_openai_model` | 替换为 `AIGF_CHAT_OPENAI_MODEL` |
| `nyaturingtest_chat_openai_base_url` | 替换为 `AIGF_CHAT_OPENAI_BASE_URL` |
| `nyaturingtest_siliconflow_api_key` | 不再需要 |
| `nyaturingtest_vlm_enabled` | 替换为 `AIGF_VLM_ENABLED` |
| `nyaturingtest_enabled_groups` | 替换为 `AIGF_ENABLED_GROUPS` |

#### 新增的配置项

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `AIGF_CHAT_OPENAI_API_KEY` | LLM API Key | 必填 |
| `AIGF_CHAT_OPENAI_BASE_URL` | LLM API 地址 | 必填 |
| `AIGF_CHAT_OPENAI_MODEL` | LLM 模型名称 | `gpt-3.5-turbo` |
| `AIGF_JSON_MODE` | 是否强制 LLM 输出 JSON | `true` |
| `AIGF_IMAGE_MODE` | 图片理解模式（vlm/llm） | `vlm` |
| `AIGF_VLM_ENABLED` | 是否启用 VLM | `true` |
| `AIGF_VLM_MODEL` | VLM 模型名称（vlm 模式必填） | 空 |
| `AIGF_VLM_BASE_URL` | VLM API 地址（vlm 模式必填） | 空 |
| `AIGF_VLM_API_KEY` | VLM API Key（为空时使用 chat key） | 空 |
| `AIGF_ENABLED_GROUPS` | 启用的群号列表 | `[]` |
| `AIGF_MEME_ENABLED` | 是否启用表情包功能 | `true` |
| `AIGF_MEME_MAX_COUNT` | 自动收集的表情包最大数量 | `200` |
| `AIGF_DEFAULT_PRESET` | 默认预设名称 | `default` |
| `AIGF_BATCH_COUNT` | 攒满多少条消息后触发 LLM 请求 | `10` |
| `AIGF_BATCH_TIMEOUT` | 距最后一条消息多少秒后触发 LLM 请求 | `30.0` |
| `AIGF_INCOMPLETE_TIMEOUT` | 检测到消息可能不完整时的等待时间（秒） | `40.0` |
| `AIGF_RECENT_MESSAGES` | prompt 中包含的最近历史消息条数 | `15` |
| `AIGF_MERGE_WINDOW` | 消息合并时间窗口（秒） | `5.0` |
| `AIGF_ENERGY_BASELINE` | 社交能量基线，能量会自然向此值回归（0.0~1.0） | `0.7` |
| `AIGF_SEARCH_ENABLED` | 是否启用联网搜索 | `false` |
| `AIGF_SEARCH_MODE` | 搜索模式: two_stage / function_call | `two_stage` |
| `AIGF_SEARCH_API` | 搜索API: tavily / bocha / bing | `tavily` |
| `AIGF_SEARCH_API_KEY` | 搜索 API Key | 空 |
| `AIGF_SEARCH_MAX_RESULTS` | 最大搜索结果数 | `3` |
| `AIGF_PROXY_ENABLED` | 是否启用代理 | `false` |
| `AIGF_HTTP_PROXY` | HTTP 代理地址 | 空 |
| `AIGF_HTTPS_PROXY` | HTTPS 代理地址 | 空 |

### 命令变更

| 原版命令 | 状态 | 说明 |
|---------|------|------|
| `help` / `帮助` | 移除 | 群聊和私聊均已移除 |
| `calm` / `冷静` | 移除 | 无情绪系统 |
| `role` / `当前角色` | 移除 | 功能合并到 `status` |
| `list_groups` / `群组列表` | 移除 | 私聊命令已移除 |
| `status` / `状态` | 保留 | 输出新增社交能量显示 |
| `set_role` / `设置角色` | 保留 | 用法不变 |
| `reset` / `重置` | 保留 | 用法不变 |
| `presets` / `preset` | 保留 | 用法不变 |
| `set_preset` / `set_presets` | 保留 | 用法不变 |
| `reload_meme` / `重载表情包` | 新增 | 热重载表情包配置 |

### Prompt 变更

| 原版 Prompt | 状态 | 说明 |
|------------|------|------|
| 三阶段 Prompt（检索/反馈/对话） | 重写 | 合并为单次调用的统一 Prompt |
| VAD 情绪注入 | 移除 | 替换为社交能量描述 |
| 人物印象注入 | 移除 | 替换为群友信息 JSON |
| HippoRAG 检索结果注入 | 移除 | 替换为联网搜索结果（可选） |
| 对话状态机逻辑 | 移除 | 替换为回复决策规则 |

| 新增 Prompt 段落 | 说明 |
|-----------------|------|
| `## 回复风格` | 指导 LLM 口语化、简短、有变化地回复 |
| `## 当前状态` | 展示社交能量描述，影响回复决策 |
| `## 你的知识` | 注入预设知识 |
| `## 相关文化知识` | 展示当前聊天中涉及的文化词汇 |
| `## 搜索结果` | 展示搜索到的信息（仅搜索时） |
| `## 可用的表情包` | 展示可供发送的表情包列表 |
| `## 当前消息中出现的图片` | 展示可收藏的缓存表情包 |
| `## 理解群聊对话` | 教 LLM 通过 @、reply、时间推断对话关系 |
| `## 图片消息说明` | 指导 LLM 不要主动评论图片 |
| `## 回复决策` | 能量感知的自然决策指导 |
| `## 文化记忆` | 文化记忆操作说明 |

### 文件变更

| 原版文件 | 状态 | 说明 |
|---------|------|------|
| `__init__.py` | 重写 | 入口逻辑、触发机制、表情包缓存、reply 提取、发送逻辑、纯 @ 检测 |
| `client.py` | 修改 | 新增 `images` 参数支持多模态、`generate_response_with_tools()` 支持 Function Calling、`response_format` 强制 JSON、`remove_leading_think()` |
| `config.py` | 重写 | 配置前缀改为 `AIGF_`，新增图片模式、表情包、搜索、社交能量等配置 |
| `image_manager.py` | 修改 | VLM 配置从 config 读取，LLM 模式下不初始化 VLM，优化 VLM prompt |
| `mem.py` | 修改 | 简化为 Message 数据类，新增 `user_id`、`is_at_only` 字段 |
| `presets.py` | 修改 | 改为异步加载，新增默认预设自动创建，支持 `utf-8-sig` 编码，预设字段简化 |
| `session.py` | 重写 | 单次 LLM 调用 + 社交能量 + 消息合并 + 搜索集成 + 文化记忆 + prompt 构建 |
| `vlm.py` | 修改 | 模型和地址从 config 读取，移除 SiliconFlow 硬编码 |
| `emotion.py` | 删除 | 不再需要 VAD 情绪系统 |
| `hippo_mem.py` | 删除 | 不再需要 HippoRAG |
| `impression.py` | 删除 | 不再需要印象记录 |
| `profile.py` | 删除 | 不再需要人物画像 |
| `siliconflow_embeddings.py` | 删除 | 不再需要嵌入模型 |
| `meme_manager.py` | 新建 | 表情包管理（缓存、收藏、发送、清理） |
| `memory.py` | 新建 | 记忆管理（短期/长期/群友/文化，增删改操作，按群隔离） |
| `search.py` | 新建 | 搜索客户端（Tavily + Bocha + Bing），Bocha/Bing 使用 httpx 异步请求 |

---

## 前身历史（nonebot-plugin-aigf）

> 以下记录保留备查，**不参与本包的版本序列**。本包（`nonebot-plugin-myom`）的版本从 0.5.0 开始，
> 与下方 aigf 线的 0.5.0 / 0.6.0 **同号不同物**。
>
> 归属说明：上游原作者 Funny1Potato 开发至 **0.4.0**；**0.5.0 与 0.6.0 是本项目作者（XI-mouren）
> fork 上游后接续开发的版本**，当时沿用 aigf 名称发布；下方 0.4.0 及更早条目均为上游原作者作品。

- **0.6.0**（XI-mouren）统计功能并入主插件（usage.py，凌晨 4 点为界，日/周/月/总用量查询，缓存命中率与花费折算）；表情包候选预筛；修复 API 增量归属差一错误；依赖新增 apscheduler
- **0.5.0**（XI-mouren）表情包频率清理与空位保留；citations 引用校验；群友画像扩展（性格/喜好/说话方式/待跟进/雷点/活跃统计/熟悉度算法）；时间模块与时区配置化；记忆收录规则增强；搜索新增 DeepSeek
- **0.4.0**（Funny1Potato）新增联网搜索及文化记忆功能，新增社交能量系统，改进 prompt
- **0.3.2** 优化消息发送逻辑
- **0.3.1** 优化记忆操作，优化上下文理解能力
- **0.3.0** 优化上下文理解能力，修改回复决策，使机器人更拟人；新增多项日志；优化 VLM 理解能力；优化群友信息存储逻辑；改进记忆操作方式
- **0.2.1** 简单优化
- **0.2.0** 按群聊分别存储记忆，改进 prompt
- **0.1.6** 改进 prompt，使 AI 更拟人
- **0.1.5** 插件发布
