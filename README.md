# nonebot-plugin-myom

[![PyPI](https://img.shields.io/pypi/v/nonebot-plugin-myom?refresh=1)](https://pypi.org/project/nonebot-plugin-myom/)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://pypi.org/project/nonebot-plugin-myom/)
[![License](https://img.shields.io/badge/license-AGPL--3.0-green)](./LICENSE)

- MYOM(Make Your Own Muelsyse) 是皱皮缪厨魔改的用于群聊的LLM群友nonebot插件
  - —— 包括分层记忆、群友画像、表情包学习、联网搜索、用量统计、密钥泄露防护与模拟生物节律的社交能量管理机制。
- 本项目对原项目代码的修改及重构也均有AI高度参与，若有做得不够好的地方，也请手下留情QAQ。

> 基于 [Funny1Potato/nonebot-plugin-aigf](https://github.com/Funny1Potato/nonebot-plugin-aigf) 二次开发（上游：[shadow3aaa/nonebot-plugin-nyaturingtest](https://github.com/shadow3aaa/nonebot-plugin-nyaturingtest)），遵循 **AGPL-3.0** 协议开源。

当前版本：**0.5.2**（包名 `nonebot-plugin-myom`，模块名 `nonebot_plugin_myom`）

---

## 目录

- [与上游的差异](#与上游的差异)
- [功能特性](#功能特性)
- [安装](#安装)
- [快速开始](#快速开始)
- [配置项](#配置项)
- [节律配置（energy.json）](#节律energyjson)
- [指令](#指令)
- [数据目录](#数据目录)
- [用量统计说明](#用量统计说明)
- [安全防护](#安全防护)
- [常见问题](#常见问题)
- [许可证与致谢](#许可证与致谢)

---

## 与上游的差异

上游是 [Funny1Potato/nonebot-plugin-aigf](https://github.com/Funny1Potato/nonebot-plugin-aigf)，最后一个版本为 **0.4.0**，之后未再更新。

本仓库从上游 0.4.0 接续开发，本版本（0.5.0）相对原项目的改动**一次性全部列在下表**，不区分批次，也不随上游同步。

| 能力                 | 上游 0.4.0                        | 本项目的更新                                                                                                                             |
| -------------------- | --------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| **用量统计**   | 无                                | 新增模块：日 / 周 / 月 / 总查询、分群 × 分用途、缓存命中率、token 口径与 API 额度双账、单价折算、清除用量、自定义中转站接口、坏数据自愈 |
| **能量机制**   | 单一线性能量（`social_energy`） | 改为生物节律：四态时间窗、四档表现、独立睡眠态、休眠与唤醒、非 @ 硬拦截                                                                  |
| **记忆与画像** | 四层记忆 + 基础群友字段           | 画像扩展（性格 / 喜好 / 说话方式 / 称呼与曾用名 / 雷点 / 待跟进 / 发言统计）；熟悉度算法；每日遗忘；引用统计                             |
| **对话**       | 基础回复决策                      | 引用校验（模型声明 + 代码端双重校验）；能量与表情包策略接入 prompt                                                                       |
| **表情包**     | 模型自由裁量；超上限清理          | 使用频率统计、候选预筛、按档位数量注入、概率前置、白名单、单张上限、保留空位                                                             |
| **搜索**       | Tavily / Bocha / Bing             | 新增 DeepSeek 搜索源                                                                                                                     |
| **图片理解**   | VLM / LLM 双模式、GIF 抽帧        | VLM 输出上限（默认 150 tokens）；图片描述与消耗按群归属；缓存纳入数据目录                                                                |
| **安全**       | 无                                | 密钥泄露检测、日志脱敏                                                                                                                   |
| **时间**       | 无                                | 当前时间注入、时区配置化（UTC 服务器不再偏移 8 小时）                                                                                    |
| **工程**       | 无                                | 数据 / 配置 / 缓存自动迁移、能量设计文档、51 项配置项（含图片缓存清理）                                                                    |

## 功能特性

### 对话核心

- **消息攒批**：群消息攒到 N 条或静默超时后才请求 LLM，贴近真实群聊节奏；纯 @ 消息与"用户连续发送"自动延长等待，避免打断
- **拟人化决策**：生物节律能量——四态时间窗决定能量增减，四档能量决定回复热情；休眠 / 疲倦期非 @ 消息直接不回复（不构建 prompt、不调 LLM）
- **装真人**：口语化约束、禁止暴露 AI 身份、不要求每句都接
- **图片理解**：独立 VLM 描述图片（`vlm` 模式）或多模态 LLM 直接看图（`llm` 模式）；GIF 自动抽帧去重后拼接
- **联网搜索**：遇到不懂的梗自动搜索（两阶段判断 / function calling 两种模式），支持 Tavily / Bocha / Bing / DeepSeek 四个来源
- **引用校验**：模型回复时声明引用了哪些知识/记忆，代码端双重校验（存在性 + 回复中有证据），统计真实引用次数

### 记忆系统（JSON 持久化，按群隔离）

- **短期记忆**：模型自主提炼的对话要点，支持增删改
- **长期记忆**：重要事实（约定/事件/群规），带引用统计
- **文化记忆**：学会的梗与网络用语（term / meaning / context + 使用统计）
- **群友画像**：性格、喜好、说话方式（带前缀分类）、称呼与曾用名、雷点、待跟进话题、发言统计、算法熟悉度（对数曲线 + **每日遗忘，淡到阈值就删掉画像**）
- **统一数据目录**：所有数据集中在一处，备份即拷贝

#### 熟悉度与遗忘

**基础分**（只有发言能提升）：`score = log10(1+发言数) / log10(201)`，即 200 条 ≈ 1.0。

**每日遗忘**（凌晨 4:05，与用量统计同频）：

```
score' = score − [ base + gain × (1 − score) ]
```

- 分数越高，日衰减越小（满分每天只掉 0.15%）；分数越低，掉得越快（最高约 4.5%/天）
- 因为是加法形式，日衰减量随分数**严格单调递减**，不会出现"中间段反而掉得更狠"
- `base > 0` 保证任何分数都会衰减，不存在"永远不掉"的固定点
- **低于 `FORGET_BELOW`（默认 0.05）→ 直接删除 `friends/<QQ>.json`**，等于"忘了这个人"
- 离线多天会按 `decay_date` **补扣**；同一天重复执行不会重复扣（幂等）
- **说过话即刷新**：任意消息把熟悉度重置为该发言量对应的基础分，衰减记账日归零

默认参数下的时间表：

| 累计发言 | 起始分 | 每天掉 | 不发言多久被忘记 |
| -------- | ------ | ------ | ---------------- |
| 1 条     | 0.13   | 0.041  | **5 天**   |
| 2 条     | 0.21   | 0.038  | 9 天             |
| 8 条     | 0.41   | 0.028  | 23 天            |
| 20 条    | 0.57   | 0.021  | 37 天            |
| 40 条    | 0.70   | 0.016  | 51 天            |
| 100 条   | 0.87   | 0.008  | 82 天            |
| 200 条   | 1.00   | 0.002  | 133 天           |

### 表情包系统

- **自动收藏**：LLM 结合上下文判断群图是否值得收藏（含防误判规则）
- **候选预筛**：构建 prompt 时按当前对话关键词预筛候选（默认 20 个），避免全量注入撑爆上下文
- **频率清理**：达到上限（默认 500）时淘汰使用频率最低的 50 个，永远留出接收新表情包的空位
- **防连发重复**：最近发送过的表情包短期不再重复

### 用量统计（内置，无需额外插件）

- 每次 LLM/VLM 调用记录：输入/输出 tokens、缓存命中 tokens、调用次数
- **统计日以凌晨 4 点为界**（0~4 点计入前一天），避免"睡前聊天"被拆到两天
- 支持单价折算花费，支持 WBY 风格用量接口（可自定义请求模板适配任意中转站）
- 自动周/月汇总、日用量自动清理；支持**分群用量排行**

### 安全防护

- **密钥泄露防护**：出站消息发送前强制安检，命中配置密钥或常见 Key 格式即取消发送；入站消息打码后入库；泄露事件写入独立日志（日志本身脱敏）

---

## 安装

**方式一：nb-cli（推荐）**

```bash
nb plugin install nonebot-plugin-myom
```

**方式二：pip**

```bash
pip install nonebot-plugin-myom
```

安装后在 `bot.py` 中加载（使用 nb-cli 创建的项目会自动写入 `pyproject.toml` 的 `[tool.nonebot].plugins`）：

```python
nonebot.load_plugin("nonebot_plugin_myom")
```

> **从 `nonebot-plugin-aigf` / `nonebot-plugin-muelsyse` 升级**：包名与模块名已变更，新旧版本可以并存。
> 首次启动时会自动按「`nonebot_plugin_muelsyse` → `nonebot_plugin_aigf`」的顺序，把第一个存在的旧
> 数据 / 配置 / 缓存目录**整体接管**到新目录（旧目录保留不删，确认无误后可自行清理）。
> `.env` 无需改动：`AIGF_` 前缀继续生效，也可换成新的 `MYOM_` 前缀（见[配置前缀说明](#关于配置前缀)）。

依赖（`pyproject.toml` 已声明，`nb plugin install` / `pip install` 会自动安装）：

| 依赖                                    | 用途                                                               |
| --------------------------------------- | ------------------------------------------------------------------ |
| nonebot2 / nonebot-adapter-onebot       | 框架与协议适配                                                     |
| nonebot-plugin-localstore               | 统一数据目录                                                       |
| **nonebot-plugin-apscheduler**    | 凌晨 4 点定时任务（不装也能跑，但自动汇总/清理退化为"启动时补做"） |
| openai / httpx / pillow / numpy / anyio | LLM 客户端、HTTP、图片处理                                         |

---

## 快速开始

`.env.prod` 最小配置：

```dotenv
# LLM（必填）
AIGF_CHAT_OPENAI_API_KEY=sk-xxxxxxxx
AIGF_CHAT_OPENAI_BASE_URL=https://api.deepseek.com/v1
AIGF_CHAT_OPENAI_MODEL=deepseek-chat

# 启用的群
AIGF_ENABLED_GROUPS=[123456789]

# 超管（指令权限，必须包含你自己的 QQ）
SUPERUSERS=["你的QQ号"]

# 用量统计（可选，不配也能用本地统计）
# AIGF_USAGE_BASE=https://你的中转站
# AIGF_USAGE_KEY=你的KEY
# AIGF_USAGE_PRICE_INPUT=2
# AIGF_USAGE_PRICE_OUTPUT=8
```

> ⚠️ `AIGF_IMAGE_MODE` 默认是 `vlm` 且 `AIGF_VLM_ENABLED` 默认开启——不配 `AIGF_VLM_MODEL` / `AIGF_VLM_BASE_URL` 时插件**照常加载**，只是自动跳过图片描述并在日志告警。想启用图片理解就把这两项配上；不想配就设 `AIGF_IMAGE_MODE=llm`（主 LLM 看图）或 `AIGF_VLM_ENABLED=false`。

> ℹ️ 本插件支持**零配置加载**：`.env` 里什么都不写，插件也能正常导入（商店收录要求）；对话请求会在真正调用前被拦截并告警。要实际聊天仍需配置下面标注「必填」的项。

启动日志正常应出现：

```
[启动] 图片理解模式: ...
[启动] 预设加载完成: N 个
[启动] 表情包加载完成: 管理员 N 个, 自动收集 N 个
```

---

## 配置项

### 关于配置前缀

以下表格统一用 `AIGF_` 前缀列出（沿用上游写法，老配置继续生效）。从 0.5.0 起**同时支持 `MYOM_` 前缀**，
两者可以混写，`MYOM_` 优先：

```dotenv
# 新写法
MYOM_CHAT_OPENAI_API_KEY=sk-xxxxxxxx
# 老写法依旧有效，不必改 .env
AIGF_CHAT_OPENAI_API_KEY=sk-xxxxxxxx
```

同一项两边都写时以 `MYOM_` 为准；只写了一边则取写了的那个。

### LLM 对话

| 配置项                        | 默认值            | 说明                                                               |
| ----------------------------- | ----------------- | ------------------------------------------------------------------ |
| `AIGF_CHAT_OPENAI_API_KEY`  | **必填***   | LLM API Key（空时插件可加载但不对话）                              |
| `AIGF_CHAT_OPENAI_BASE_URL` | **必填***   | OpenAI 兼容接口地址（通常以 `/v1` 结尾；空时插件可加载但不对话） |
| `AIGF_CHAT_OPENAI_MODEL`    | `gpt-3.5-turbo` | 模型名称                                                           |
| `AIGF_JSON_MODE`            | `True`          | 强制 JSON 输出（需模型支持 `response_format`）                   |

### 图片理解

| 配置项                  | 默认值   | 说明                                                         |
| ----------------------- | -------- | ------------------------------------------------------------ |
| `AIGF_IMAGE_MODE`     | `vlm`  | `vlm`=独立 VLM 描述 / `llm`=主 LLM 直接看图              |
| `AIGF_VLM_ENABLED`    | `True` | 是否启用图片理解（仅 vlm 模式有效）                          |
| `AIGF_VLM_MODEL`      | `""`   | VLM 模型名称                                                 |
| `AIGF_VLM_BASE_URL`   | `""`   | VLM 接口地址                                                 |
| `AIGF_VLM_API_KEY`    | `""`   | VLM 的 Key（留空则复用 LLM Key）                             |
| `AIGF_VLM_MAX_TOKENS` | `0`    | VLM 单次输出上限（`0` = 不限制，默认）。**推理型视觉模型（Qwen3-VL / GLM-4.5V 等）必须保持 `0`**，否则思考过程会吃光配额导致描述为空；普通视觉模型可设 `150` 降低看图消耗 |

### 群与行为

| 配置项                             | 默认值      | 说明                                                                    |
| ---------------------------------- | ----------- | ----------------------------------------------------------------------- |
| `AIGF_ENABLED_GROUPS`            | `[]`      | 启用群号列表                                                            |
| `AIGF_DEFAULT_PRESET`            | `default` | 默认角色预设                                                            |
| `AIGF_BATCH_COUNT`               | `10`      | 攒满多少条触发请求                                                      |
| `AIGF_BATCH_TIMEOUT`             | `30.0`    | 静默多少秒触发请求                                                      |
| `AIGF_INCOMPLETE_TIMEOUT`        | `40.0`    | 纯 @ / 连续发送时的延长等待（秒）                                       |
| `AIGF_RECENT_MESSAGES`           | `15`      | prompt 中包含的最近消息条数                                             |
| `AIGF_MERGE_WINDOW`              | `5.0`     | 同一用户连续消息合并窗口（秒）                                          |
| `AIGF_FAMILIARITY_DECAY_ENABLED` | `true`    | 熟悉度每日衰减（凌晨 4:05 执行）；关闭则回退旧的「30 天未发言封顶 0.4」 |
| `AIGF_FAMILIARITY_DECAY_BASE`    | `0.0015`  | 每日基础衰减量：任何分数都会掉（满分每天 0.15%）                        |
| `AIGF_FAMILIARITY_DECAY_GAIN`    | `0.02`    | 低分加速项：每日额外衰减 = gain ×(1−熟悉度)                           |
| `AIGF_FAMILIARITY_FORGET_BELOW`  | `0.05`    | 熟悉度低于该值 → 删除画像（忘记这个人）                                |
| `AIGF_ENERGY_ENABLED`            | `true`    | 是否启用生物节律能量机制（`false` 回退旧版线性能量）                  |
| `AIGF_ENERGY_CONFIG`             | `""`      | `energy.json` 路径（留空用配置目录下 `energy.json`）                |
| `AIGF_ENERGY_BASELINE`           | `0.7`     | 旧版能量基线（仅 `AIGF_ENERGY_ENABLED=false` 时生效）                 |
| `AIGF_TIMEZONE_OFFSET`           | `8.0`     | 时区偏移（小时），默认北京时间；支持半时区如 `5.5`                    |

### 表情包

| 配置项                      | 默认值   | 说明                                       |
| --------------------------- | -------- | ------------------------------------------ |
| `AIGF_MEME_ENABLED`       | `True` | 是否启用                                   |
| `AIGF_MEME_MAX_COUNT`     | `500`  | 自动收集上限（达到后触发清理；填 `0` = 无上限） |
| `AIGF_MEME_RESERVE_COUNT` | `50`   | 清理后保留的空位数（回落到 max - reserve） |
| `AIGF_MEME_CANDIDATES`    | `20`   | 每次 prompt 注入的候选数量（按对话预筛）   |

### 图片缓存

群消息里的每张图片都会先进入 `sticker_cache` 候选池（无论最终是否被收藏），候选描述只保存在内存中，进程重启后这些文件就不会再被用到。开启自动清理可避免目录无限膨胀。

| 配置项                          | 默认值 | 说明                                                                         |
| ------------------------------- | ------ | ---------------------------------------------------------------------------- |
| `AIGF_CACHE_CLEAN_ENABLED`      | `True` | 是否启用每日 4:05 自动清理                                                   |
| `AIGF_STICKER_CACHE_KEEP_DAYS`  | `7`    | 候选池保留天数（填 `0` 表示不清理；要立即清空请用 `清理缓存 全部` 指令）     |
| `AIGF_IMAGE_RAW_KEEP_DAYS`      | `7`    | `image_cache/raw` 原图下载缓存保留天数                                       |
| `AIGF_IMAGE_DESC_KEEP_DAYS`     | `0`    | VLM 描述缓存保留天数，`0` = 永久。**这是唯一花过钱的缓存，删了同图再出现要重新调 VLM** |

> 清理只涉及候选池与原图缓存，`memes/` 表情包库和描述缓存永不受影响。

### 联网搜索

| 配置项                      | 默认值        | 说明                                                      |
| --------------------------- | ------------- | --------------------------------------------------------- |
| `AIGF_SEARCH_ENABLED`     | `False`     | 是否启用                                                  |
| `AIGF_SEARCH_MODE`        | `two_stage` | `two_stage`=先判断再搜 / `function_call`=模型自主调用 |
| `AIGF_SEARCH_API`         | `tavily`    | `tavily` / `bocha` / `bing` / `deepseek`          |
| `AIGF_SEARCH_API_KEY`     | `""`        | 搜索服务 Key                                              |
| `AIGF_SEARCH_MAX_RESULTS` | `3`         | 每次返回结果数                                            |

### 代理（仅 LLM/VLM 客户端，搜索与用量接口不走代理）

| 配置项                 | 默认值    | 说明           |
| ---------------------- | --------- | -------------- |
| `AIGF_PROXY_ENABLED` | `False` | 是否启用       |
| `AIGF_HTTP_PROXY`    | `""`    | HTTP 代理地址  |
| `AIGF_HTTPS_PROXY`   | `""`    | HTTPS 代理地址 |

### 用量统计与查询

支持两种配置方式，优先级：**`.env` 提供默认值 → `usage/config.json` 覆盖同名键**。

| 配置项                        | 默认值           | 说明                                                                 |
| ----------------------------- | ---------------- | -------------------------------------------------------------------- |
| `AIGF_USAGE_BASE`           | `""`           | 用量接口地址（WBY 风格，如 `https://api.example.com`）             |
| `AIGF_USAGE_KEY`            | `""`           | 用量接口 Key（Bearer 认证）                                          |
| `AIGF_USAGE_PRICE_INPUT`    | `0.0`          | 输入单价（元/1M tokens），0 表示不折算花费                           |
| `AIGF_USAGE_PRICE_OUTPUT`   | `0.0`          | 输出单价（元/1M tokens）                                             |
| `AIGF_USAGE_URL`            | `""`           | 自定义请求 URL 模板（支持 `{base}` / `{key}` 占位符）            |
| `AIGF_USAGE_METHOD`         | `GET`          | 请求方法：`GET` / `POST`                                         |
| `AIGF_USAGE_AUTH_HEADER`    | `Bearer {key}` | 认证头模板                                                           |
| `AIGF_USAGE_BODY`           | `""`           | POST 请求体 JSON 模板（支持占位符）                                  |
| `AIGF_USAGE_FIELDS`         | `""`           | 响应字段映射 JSON（留空自动识别 WBY / one-api 格式）                 |
| `AIGF_USAGE_QUOTA_PER_YUAN` | `0.0`          | 额度换算系数：多少 quota 等于 1 元（如 `73000`）；0 表示不折算金额 |

运行期也可用指令修改（写入**配置目录**的 `usage/config.json`，**同名键优先级高于 `.env`**）：

| 指令                           | 写入键                             |
| ------------------------------ | ---------------------------------- |
| `用量配置 <BASE> <KEY>`      | `base` / `key`                 |
| `用量价格 <输入价> <输出价>` | `price_input` / `price_output` |

`config.json` 也可手工添加 `url` / `method` / `auth_header` / `body` / `fields` 等键（路径：`<配置根目录>/nonebot_plugin_myom/usage/config.json`），详见[用量接口自定义](#用量接口自定义适配任意中转站)。

> 以上全部**可选**：不配接口时只缺少「API 增量 / 实时总量」两项，单次调用明细统计与所有查询指令（日/周/月/总/群用量）照常工作；不配单价则不显示花费。

---

### 节律（energy.json）

节律不写在 `.env` 里，而是独立 JSON 文件（首次启动自动生成默认值）：

```
<配置根目录>/nonebot_plugin_myom/energy.json
```

修改后保存，在群里 @ 机器人 发送 **`重载节律`** 即可生效，**不需要重启**。配置非法时指令会报出具体冲突项，并且**不会覆盖**正在运行的配置。

> 总开关在 `.env` 的 `AIGF_ENERGY_ENABLED`（`false` 时逐字节回退旧版线性能量），`energy.json` 里没有、也不认 `enabled` 字段。

#### 完整默认值

```json
{
  "version": 2,
  "timezone_offset": null,
  "noise": 0.01,
  "states": {
    "excited": { "label": "兴奋期", "window": ["17:00", "01:00"], "reply_cost": [0.03, 0.08], "idle_gain": [0.06, 0.14], "at_boost": 0.10 },
    "normal":  { "label": "平淡期", "window": ["09:00", "17:00"], "reply_cost": [0.05, 0.11], "idle_gain": [-0.01, 0.04], "at_boost": 0.08 },
    "tired":   { "label": "疲倦期", "window": ["01:00", "09:00"], "reply_cost": [0.12, 0.20], "idle_gain": [0.00, 0.02], "at_boost": 0.05 }
  },
  "levels": [
    { "threshold": 0.80, "label": "精力充沛", "tone": "精力充沛，看到什么都想插嘴；语气轻快，可以主动接梗、开玩笑", "meme_prob": 0.55, "meme_candidates": 20, "reply_base": 0.55 },
    { "threshold": 0.60, "label": "状态不错", "tone": "状态不错，有兴趣的话题会主动参与；正常聊天，偶尔接梗", "meme_prob": 0.45, "meme_candidates": 16, "reply_base": 0.38 },
    { "threshold": 0.40, "label": "一般般",   "tone": "一般般，有人找就回，不太主动；句子简短，不展开话题", "meme_prob": 0.30, "meme_candidates": 12, "reply_base": 0.25 },
    { "threshold": 0.20, "label": "有点懒",   "tone": "有点懒，倾向于潜水；只 @ 或点你名字时才回，回得很短", "meme_prob": 0.12, "meme_candidates": 8, "reply_base": 0.10 }
  ],
  "sleep": {
    "threshold": 0.15,
    "duration_minutes": [180, 300],
    "recover_per_10min": 0.02,
    "wake_boost": 0.28,
    "wake_floor": 0.55,
    "drowsy_minutes": 30,
    "block_non_at": true,
    "wake_tone": "你刚才睡着了，被 @ 叫醒。先说明自己刚睡着、现在还很困，再回应对方的话；语气不要冲。"
  },
  "decision": {
    "tired_block_non_at": true,
    "meme_max_per_reply": 1,
    "meme_prob_at_boost": 0.15,
    "reply_hint_k": 0.60,
    "reply_hint_max": 0.95
  }
}
```

#### 时间片 `states`

| 字段           | 说明                                                                                                                          |
| -------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `window`     | `["开始", "结束"]`，24 小时制，可跨零点（`17:00` → `01:00`）。三个时间窗必须**首尾相接、不重叠、覆盖满 24 小时** |
| `reply_cost` | 每次回复扣除的能量 `[最小, 最大]`，实际值在区间内随机，并按回复长度加码                                                     |
| `idle_gain`  | 有消息但你**没回复**时每轮的能量变化 `[最小, 最大]`，可为负                                                           |
| `at_boost`   | 被 @ 时的瞬时加成                                                                                                             |

默认节奏：兴奋期（17:00–01:00）回得越多掉得越慢、潜水恢复快；平淡期（09:00–17:00）收支接近平衡；疲倦期（01:00–09:00）回复代价最大、几乎不恢复。

#### 能量档位 `levels`

按 `threshold` 从高到低排列，取第一个满足 `energy >= threshold` 的档；低于末档时兜底用末档。

| 字段                | 说明                                                                                                      |
| ------------------- | --------------------------------------------------------------------------------------------------------- |
| `threshold`       | 进入该档的最低能量，必须**严格递减**；末档必须 **> 0** 且**不低于** `sleep.threshold` |
| `label`           | 档位名，只用于日志和 `状态` 指令                                                                        |
| `tone`            | 写进 prompt 的语气描述，**调整回复风格主要靠它**                                                    |
| `meme_prob`       | 该档下允许发图的概率（0~1）                                                                               |
| `meme_candidates` | 允许发图时注入 prompt 的候选数量                                                                          |
| `reply_base`      | 接话倾向基数，与 `reply_hint_k` 一起算出“大概多大概率接话”，只写进 prompt 不做硬拦截                  |

> **睡眠不是档位**：`energy <= sleep.threshold` 直接进入睡眠，不再走档位判定，所以末档不能低于睡眠阈值（否则这一档永远到不了）。

**可以自己增删档位**（比如在 0.80 之上再加一档「亢奋」），数量没有上限，只要满足：

- `threshold` 严格递减
- 末档 `threshold` > 0，且不低于 `sleep.threshold`
- **每档都写全 `label` / `tone`**（建议连 `meme_prob` / `meme_candidates` / `reply_base` 一起写）

第三点是硬要求：缺失字段会按**索引**从默认档位继承文案，增删档位后索引会错位，出现「0.90 档顶着 0.80 档的名字和语气」的情况。检测到档位数量与默认不同、且有档位缺 `label`/`tone` 时，启动/重载会打印 WARNING 提醒。

示例：在最高档之上加一档

```json
"levels": [
  { "threshold": 0.90, "label": "亢奋",     "tone": "刚聊嗨，抢话接梗都很积极", "meme_prob": 0.70, "meme_candidates": 24, "reply_base": 0.70 },
  { "threshold": 0.80, "label": "精力充沛", "tone": "精力充沛，看到什么都想插嘴；语气轻快，可以主动接梗、开玩笑", "meme_prob": 0.55, "meme_candidates": 20, "reply_base": 0.55 },
  { "threshold": 0.60, "label": "状态不错", "tone": "状态不错，有兴趣的话题会主动参与；正常聊天，偶尔接梗", "meme_prob": 0.45, "meme_candidates": 16, "reply_base": 0.38 },
  { "threshold": 0.40, "label": "一般般",   "tone": "一般般，有人找就回，不太主动；句子简短，不展开话题", "meme_prob": 0.30, "meme_candidates": 12, "reply_base": 0.25 },
  { "threshold": 0.20, "label": "有点懒",   "tone": "有点懒，倾向于潜水；只 @ 或点你名字时才回，回得很短", "meme_prob": 0.12, "meme_candidates": 8, "reply_base": 0.10 }
]
```

> 反过来也可以减少档位（比如只留 0.8 / 0.5 / 0.2 三档），同样要保证末档不低于 `sleep.threshold`。

#### 休眠 `sleep`

| 字段                  | 默认           | 说明                                    |
| --------------------- | -------------- | --------------------------------------- |
| `threshold`         | `0.15`       | 能量降到多少就入睡                      |
| `duration_minutes`  | `[180, 300]` | 睡多久（分钟，区间内随机）              |
| `recover_per_10min` | `0.02`       | 睡眠中每 10 分钟恢复多少                |
| `wake_boost`        | `0.28`       | 被 @ 叫醒时加多少能量                   |
| `wake_floor`        | `0.55`       | 叫醒后的保底能量，防止刚醒又秒睡        |
| `drowsy_minutes`    | `30`         | 叫醒后的困倦期长度，期间仍按疲倦期处理  |
| `block_non_at`      | `true`       | 睡眠中非 @ 消息是否直接丢弃（不调 LLM） |
| `wake_tone`         | 见上文         | 被叫醒时写进 prompt 的提示词            |

#### 决策 `decision`

| 字段                   | 默认     | 作用                                                    | 调大 / 调小会怎样                                                                                                                             |
| ---------------------- | -------- | ------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `tired_block_non_at` | `true` | 疲倦期（01:00–09:00）非 @ 消息是否硬拦截               | `true` 直接丢弃、不构建 prompt、不调 LLM（省 token）；改 `false` 后夜里也会正常判断接话，只是语气按低档变蔫，**token 消耗明显上升** |
| `meme_max_per_reply` | `1`    | 单条回复最多发几张表情包                                | `0` = 完全禁发；调到 `2~3` 可能连发多张，刷屏感强。只限制**发送数量**，不影响注入 prompt 的候选数                                   |
| `meme_prob_at_boost` | `0.15` | 被 @ 时在该档 `meme_prob` 上叠加的概率                | 调大 → 被点名时更爱发图（末档 0.12 + 0.15 = 27%）；设为 `0` → @ 不再额外提高发图概率。内部钳到 1.0                                        |
| `reply_hint_k`       | `0.60` | 接话倾向斜率，`p = reply_base + k × (energy − 0.5)` | 调大 → 能量高低对接话意愿影响更剧烈；调小 → 趋近各档 `reply_base`，能量本身影响变弱；设 `0` → 完全由档位决定                           |
| `reply_hint_max`     | `0.95` | 接话倾向的上限                                          | 调低 → 即使满能量也不会说“几乎一定接话”，整体显得更被动；一般不用动                                                                        |

`reply_hint_k` 按默认 `0.60` 的实际计算结果：

| 能量 | 所在档位（`reply_base`） | 计算                            | 写进 prompt 的倾向          |
| ---- | -------------------------- | ------------------------------- | --------------------------- |
| 0.90 | 精力充沛（0.55）           | 0.55 + 0.6 × 0.40              | **79%**               |
| 0.50 | 一般般（0.25）             | 0.25 + 0.6 × 0.00              | **25%**               |
| 0.25 | 有点懒（0.10）             | 0.10 + 0.6 × (−0.25) = −0.05 | 钳到 **0%**，基本不接 |

> 接话倾向**只写进 prompt** 给模型参考（例如“对于没 @ 你的消息，你大概只有 25% 会接话”），不做硬性拦截；真正不回复靠的是睡眠与疲倦期拦截。

#### 顶层字段

| 字段                | 默认     | 说明                                                                   |
| ------------------- | -------- | ---------------------------------------------------------------------- |
| `version`         | `2`    | 配置结构版本，保持不动                                                 |
| `timezone_offset` | `null` | 节律使用的时区；`null` 表示跟随 `.env` 的 `AIGF_TIMEZONE_OFFSET` |
| `noise`           | `0.01` | 每次结算附加的随机扰动幅度，避免能量机械可预测                         |

#### 常见改法

| 想要的效果           | 改哪里                                                            |
| -------------------- | ----------------------------------------------------------------- |
| 让它晚点睡           | 调低 `sleep.threshold`（比如 `0.10`），越难入睡               |
| 让它早点睡           | 调高 `sleep.threshold`（但必须 ≤ 末档 `threshold`）          |
| 睡短一点             | `sleep.duration_minutes` 改 `[60, 120]`                       |
| 白天更活跃           | 调高 `states.normal.idle_gain` 上限、调低 `reply_cost`        |
| 回复更积极           | 各档 `reply_base` +0.1，或把 `decision.reply_hint_k` 调大     |
| 少发表情包           | 各档 `meme_prob` 减半                                           |
| 彻底不发图           | `decision.meme_max_per_reply: 0`                                |
| 被 @ 时更爱发图      | 调高 `decision.meme_prob_at_boost`                              |
| 让能量高低差异更明显 | 调高 `decision.reply_hint_k`                                    |
| 增加 / 减少档位      | 改 `levels` 数组，见上文「可以自己增删档位」                    |
| 嫌它太吵             | 调高 `states.excited.reply_cost`，让它更快累                    |
| 半夜也允许接话       | 设 `decision.tired_block_non_at: false`                         |
| 改说话风格           | 直接改对应档位的 `tone` 文案                                    |
| 换时区               | 改 `timezone_offset`（如 `8`），或保持 `null` 跟随 `.env` |

#### 改错了会报什么

| 报错信息                               | 原因                                                             |
| -------------------------------------- | ---------------------------------------------------------------- |
| `时间窗重叠：… 与 … 在 HH:MM 冲突` | 两个 `window` 覆盖了同一时间段                                 |
| `时间窗未覆盖 24 小时`               | 三个 `window` 之间有空档                                       |
| `window 非法`                        | 不是 `HH:MM`，或超出 `00:00`~`24:00`                       |
| `必须小于上一档`                     | `levels` 的 `threshold` 没有严格递减                         |
| `最后一档的 threshold 必须大于 0`    | 末档写成了 `0`（睡眠不属于档位）                               |
| `不能低于 sleep.threshold`           | 末档落在睡眠区内，永远到不了                                     |
| `大于 max`                           | `reply_cost` / `idle_gain` / `duration_minutes` 区间写反了 |

配置非法时 `重载节律` 会逐条列出问题，运行中的旧配置保持不变；启动时遇到非法配置会记录错误并回退旧版线性能量逻辑，不会让 bot 起不来。

---

## 指令

### 权限规则

- 所有指令均要求 **`SUPERUSER`**（`.env.prod` 中 `SUPERUSERS=["QQ号"]`，必须是字符串列表）
- 所有指令均要求 **`to_me`**：群聊中需 **@机器人**，私聊直接发送
- 触发还需命令前缀：nonebot 默认 `COMMAND_START={"/"}`，即 `/状态`；若希望裸文本直发，配置 `COMMAND_START=["", "/"]`

### 管理指令

| 指令                       | 场景                              | 说明                                                                         |
| -------------------------- | --------------------------------- | ---------------------------------------------------------------------------- |
| `状态 [群号]`            | 群聊 / **私聊（需带群号）** | 查看角色、人设、当前节律（阶段/能量/档位/距切换）、最近消息                  |
| `设置角色 <名字> <设定>` | 群聊                              | 修改当前群人设                                                               |
| `重置`                   | 群聊                              | 重置会话并加载默认预设                                                       |
| `presets`                | 群聊                              | 列出可用预设                                                                 |
| `set_preset <名称>`      | 群聊                              | 切换当前群预设                                                               |
| `重载表情包`             | 群聊                              | 重新加载表情包库                                                             |
| `重载节律`               | 群聊                              | 重新读取并校验 energy.json，应用到所有群会话（非法配置不会覆盖运行中的配置） |
| `清理缓存 [全部/统计]`   | 群聊                              | 清理过期图片缓存；`全部` = 无视保留期清空候选池与原图缓存；`统计` = 只看占用 |

> 写操作（设置角色/重置/切预设/重载）限定群聊执行，避免私聊误改多群行为。

#### 清理缓存怎么用

图片缓存分三处，`清理缓存` 只动前两处——**表情包库和 VLM 描述缓存永远不参与清理**。

| 用法                | 效果                                                                                                   |
| ------------------- | ------------------------------------------------------------------------------------------------------ |
| `清理缓存`        | 按配置的保留期删过期文件（`AIGF_STICKER_CACHE_KEEP_DAYS` / `AIGF_IMAGE_RAW_KEEP_DAYS`，默认各 7 天） |
| `清理缓存 全部`   | 无视保留期，立刻清空候选池与原图缓存，用于磁盘告急                                                     |
| `清理缓存 统计`   | 不删任何东西，只报告三处缓存各占多少文件、多少 MB                                                      |

```
@机器人 清理缓存 统计
→ 当前缓存占用：
  sticker_cache 候选池: 1284 个 / 87.3 MB
  原图缓存 raw: 1211 个 / 96.5 MB
  VLM 描述缓存: 1187 个 / 2.1 MB
```

> 每天 **4:05** 自动执行一次「清理缓存」。`sticker_cache` 里的图片一旦不在内存索引中，就再也不会被模型看到，
> 定期回收不影响任何功能；而 `memes/` 的表情包和 `image_cache/*.json` 的 VLM 描述都不在清理范围内。

### 用量指令（群聊 / 私聊均可）

| 指令                      | 说明                                                                                       |
| ------------------------- | ------------------------------------------------------------------------------------------ |
| `日用量 [n]`            | 最近 n 日用量（默认 7）：输入/输出/缓存命中率/调用次数/花费/API 增量                       |
| `周用量 [n]`            | 最近 n 周用量（默认 4）                                                                    |
| `月用量 [n]`            | 最近 n 月用量（默认 6）                                                                    |
| `总用量`                | 本地累计统计 + 今日明细 + API 实时总量（**验证上报链路用这个**）                     |
| `群用量 [n]`            | 最近 n 个统计日各群用量排行（默认 7），每群附「对话 / 看图」分用途明细                     |
| `清除用量 日/周/月 [n]` | 手动删除最近 n 条日/周/月用量记录（默认 1；删日记录会连同该日分群数据与 API 增量一起删除） |
| `清除用量 全部 确认`    | 清空全部用量统计与 API 快照基准（不可恢复，需二次确认）                                    |
| `用量价格 [入] [出]`    | 配置单价（元/1M tokens），配置后折算花费                                                   |
| `用量配置 [BASE] [KEY]` | 配置 WBY 风格用量接口（或手编 config.json 自定义）                                         |
| `用量帮助`              | 查看帮助                                                                                   |

---

### 能量节律

两层职责分离：

| 层                                                                            | 决定什么                                                                                              | 配置位置                      |
| ----------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- | ----------------------------- |
| **时间片**（兴奋 17:00–01:00 / 平淡 09:00–17:00 / 疲倦 01:00–09:00） | **只决定能量怎么变**：回复消耗 `reply_cost`、未回复恢复 `idle_gain`、被 @ 提升 `at_boost` | `energy.json` 的 `states` |
| **能量档位**（四档，阈值 0.80/0.60/0.40/0.20）                          | **决定怎么回复**：语气文案、表情包概率与候选数、接话倾向提示                                    | `energy.json` 的 `levels` |

- **睡眠是独立状态**，不属于任何档位：能量 ≤ `sleep.threshold`（默认 0.15）即入睡；0.15~0.20 这一段未入睡，仍按末档「有点懒」处理
- **硬拦截**：休眠期（能量 ≤ `sleep.threshold`）与疲倦期，**非 @ 的消息直接丢弃**——不构建 prompt、不调 LLM、连图片都不识别（省 token）
- **休眠**：能量跌破阈值自动入睡，时长在 `duration_minutes` 区间内随机；期间按 `recover_per_10min` 恢复；到点自然醒并保底 `wake_floor`；被 @ 立刻唤醒（`wake_boost`）并进入 `drowsy_minutes` 困倦期
- **表情包**：构建 prompt **前**按档位概率掷骰子——命中才注入候选（数量按档位 20/16/12/8），未命中完全不注入且白名单为空；发送前校验白名单，单条回复最多 `meme_max_per_reply` 张
- **回复倾向**：`p = reply_base + reply_hint_k × (energy − 0.5)`，**只写进 prompt** 给模型参考，不做硬性拦截（@ 与疲倦期不提示）
- **关闭开关**：`AIGF_ENERGY_ENABLED=false` 回退到旧版线性能量逻辑（行为一致，实测生成的 prompt 与原版逐字节相同）

|          | `AIGF_ENERGY_ENABLED=true`（默认）             | `AIGF_ENERGY_ENABLED=false`  |
| -------- | ------------------------------------------------ | ------------------------------ |
| 能量来源 | 按群持久化 `data/energy/<群号>.json`           | 内存变量，重启回到 0.75        |
| 硬拦截   | 休眠期 / 疲倦期非 @ 直接丢弃                     | 无（任何时刻都可能回复）       |
| prompt   | 阶段 + 能量 + 档位语气 + 配图概率 + 接话倾向     | 旧文案"社交能量：状态不错……" |
| 表情包   | 概率前置 + 白名单 + 单张上限                     | 自由裁量，不校验               |
| 文件     | 首次启动生成 `energy.json` 与 `data/energy/` | 都不生成                       |

`energy.json` 首次启动自动生成默认值（写入配置目录），改完执行 `重载节律` 即可生效，无需重启；配置非法会报出具体冲突段（重叠 / 缺口 / 区间颠倒）且不会覆盖运行中的配置。

---

## 数据目录

数据位置由 [`nonebot-plugin-localstore`](https://github.com/nonebot/plugin-localstore) 决定，不同部署方式对应不同根目录：

| localstore 配置                                                                           | 数据根目录                                                             | 配置根目录                                                   |
| ----------------------------------------------------------------------------------------- | ---------------------------------------------------------------------- | ------------------------------------------------------------ |
| `LOCALSTORE_USE_CWD=true`（或 `bot.py` 里 `nonebot.init(localstore_use_cwd=True)`） | `<bot目录>/data/`                                                    | `<bot目录>/config/`                                        |
| `LOCALSTORE_DATA_DIR` / `LOCALSTORE_CONFIG_DIR` 指定路径                              | 该路径（如 `/srv/aigf-data/`）                                       | 该路径                                                       |
| 都不配（默认）                                                                            | Linux `~/.local/share/nonebot2/`Windows `%LOCALAPPDATA%\nonebot2\` | Linux `~/.config/nonebot2/`Windows `%APPDATA%\nonebot2\` |

**实际路径 = 上表根目录 + `nonebot_plugin_myom/`**。例如开启 `LOCALSTORE_USE_CWD` 时：

```
/root/workspace/MYOM/data/nonebot_plugin_myom/      ← 数据
/root/workspace/MYOM/config/nonebot_plugin_myom/    ← 配置
```

> 不确定自己是哪种？在 bot 目录执行下面这段，直接打印真实路径：
>
> ```bash
> python - <<'EOF'
> import nonebot; nonebot.init()
> import nonebot_plugin_localstore as s
> print("USE_CWD:", s.plugin_config.localstore_use_cwd)
> print("DATA   :", s.BASE_DATA_DIR)
> print("CONFIG :", s.BASE_CONFIG_DIR)
> print("CACHE  :", s.BASE_CACHE_DIR)
> EOF
> ```
>
> 输出里 `DATA` / `CONFIG` 后面那串，后面再拼上 `nonebot_plugin_myom/` 就是本文说的数据目录与配置目录。

```
<数据根目录>/nonebot_plugin_myom/
├── memory/
│   ├── <群号>/
│   │   ├── short_term.json      短期记忆
│   │   ├── long_term.json       长期记忆
│   │   ├── long_term_stats.json 长期记忆引用统计
│   │   └── culture.json         文化记忆（梗）
│   └── friends/<QQ号>.json      群友画像（按人存，跨群共享，含 groups 字段）
├── usage/records.json           用量记录（daily / weekly / monthly / snapshot）
├── energy/<群号>.json           各群能量状态（能量值 / 休眠截止 / 困倦截止）
├── memes/
│   ├── memes.json               管理员表情包索引
│   ├── collected.json           自动收集索引（含使用频率）
│   └── *.png / *.gif            表情包本体
├── sticker_cache/               表情包临时缓存（可删，自动重建）
├── image_cache/                 VLM 图片描述缓存（可删，会重新识别）
└── security/key_leak.log        密钥泄露日志（已脱敏）

<配置根目录>/nonebot_plugin_myom/
├── presets/                     角色预设
├── energy.json                  生物节律配置（四态时间窗/能量区间/四档表现/休眠参数）
└── usage/config.json            用量接口与单价配置（可用指令写入，也可手工编辑）
```

> 配置目录的规则与数据目录一致：`<配置根目录>/nonebot_plugin_myom/`（见上表）。

- **备份**：备份**数据目录**（`memory/` + `memes/` + `usage/records.json`）和**配置目录**（`presets/` + `usage/config.json`）；两个 cache 子目录可排除
- **改名迁移**：从 `nonebot-plugin-aigf` 升级时，首次启动会把旧 `nonebot_plugin_aigf` 的 数据 / 配置 / 缓存目录整体拷到新目录（仅在新目录为空时执行，旧目录保留）
- **缓存迁移**：旧版本把缓存放在系统 cache 目录，首次启动新版会自动迁移到数据目录并打印 `[迁移] ...`
- **配置迁移**：`usage/config.json` 若仍在数据目录（旧版位置），启动时自动搬到配置目录

---

## 用量统计说明

- **统计日**：凌晨 4 点为界（`04:00` 前计入前一天）
- **数据来源一（单次调用）**：每次 LLM/VLM 调用从响应 `usage` 提取 `prompt_tokens` / `completion_tokens` / 缓存命中（兼容 OpenAI `prompt_tokens_details.cached_tokens` 与 DeepSeek `prompt_cache_hit_tokens`）
- **数据来源二（API 总量）**：每天 04:00 请求用量接口，用快照差值记录前一统计日的账户增量；未配置接口时该部分跳过（**不影响**汇总与清理）
- **两套账独立统计**（同一套日/周/月规则，互不混算）：
  - **token 口径花费** = 上游响应 `usage` 的 tokens × `.env` 单价 → 「花费（token 口径）」
  - **额度口径花费** = 站方用量接口的额度增量 ÷ `AIGF_USAGE_QUOTA_PER_YUAN` → 「额度消耗（API 口径）」
  - 两者口径不同（额度含模型倍率/缓存折扣/视觉计费），数值本就不会相等，分开看即可
- **缓存命中率** = 缓存命中输入 tokens / 输入 tokens（输出不参与缓存）
- **分群**：`群用量` 读取日记录里的 `groups` 子表；分群统计自部署该版本起累计，历史数据无分群维度
- **汇总与清理**：周日结束后汇总周用量；月初汇总月用量；日用量保留「当前月 + 上一整月」，更早自动删除（如 12 月时删除 10 月日记录）

### 用量接口自定义（适配任意中转站）

默认请求 WBY 风格（`$BASE/api/usage/token/`）。可在配置目录的 `usage/config.json` 中自定义（也可写在 `.env` 的 `AIGF_USAGE_*` 里）：

```json
{
  "base": "https://你的中转站",
  "key": "你的KEY",
  "url": "https://你的中转站/api/usage/token/?k={key}",
  "method": "GET",
  "auth_header": "Bearer {key}",
  "body": "{\"key\": \"{key}\"}",
  "fields": {
    "total_used": "data.total_used",
    "total_available": "data.total_available",
    "total_granted": "data.total_granted",
    "unlimited_quota": "data.unlimited_quota",
    "name": "data.name"
  }
}
```

| 键              | 说明                                                                                       |
| --------------- | ------------------------------------------------------------------------------------------ |
| `url`         | 请求 URL 模板，支持 `{base}` / `{key}`（`{key}` 做 URL 编码）                        |
| `method`      | `GET`（默认）/ `POST`                                                                  |
| `auth_header` | 认证头模板，如 `Bearer {key}`；留空且有 key 时默认 Bearer                                |
| `body`        | POST 请求体 JSON 模板（支持占位符）                                                        |
| `fields`      | 响应字段的点号路径映射；**留空自动识别** WBY（`data.*`）与 one-api（顶层字段）格式 |

> one-api 系返回的是**美元额度**而非 token 数，统计数值的单位随之变化。

---

## 安全防护

三道卡点（详见 `security.py`）：

| 位置               | 行为                                                                                                 |
| ------------------ | ---------------------------------------------------------------------------------------------------- |
| 出站（发群消息前） | 整批回复强制安检，命中配置密钥或 `sk-`/`key-`/`token-` 格式 → **取消本次发送** + 记日志 |
| 入站（消息入库前） | 疑似密钥打码为 `[已屏蔽]`，防止第三方 Key 进入记忆后被复读                                         |
| 命令输出           | 用量查询异常文本（可能含带 Key 的 URL）发送前脱敏                                                    |

日志：`<数据根目录>/nonebot_plugin_myom/security/key_leak.log`，记录时间/方向/群号/触发规则/上下文摘要——**日志本身不含密钥原文**。

> 局限：仅能拦住「配置中的 Key」与「常见格式的 Key」。用 `用量配置` 在**群里**交互输入 Key 时，明文已进入 QQ 聊天记录，插件无法清除——配置类操作请在私聊进行或直接编辑 config.json。

---

## 常见问题

**Q：启动报 `VLM 模式已启用，但 AIGF_VLM_MODEL 或 AIGF_VLM_BASE_URL 未配置`**
设 `AIGF_IMAGE_MODE=llm`，或配齐 VLM 三项，或 `AIGF_VLM_ENABLED=false`。

**Q：指令没有任何反应**
依次检查：① `SUPERUSERS` 是否为 `["QQ号"]` 字符串列表；② 群里是否 @ 了机器人；③ 命令前缀（默认需 `/`，想裸文本就配 `COMMAND_START=["", "/"]`）；④ 是否已部署含 `from . import usage` 的版本（早期版本用量指令未注册）。

**Q：`总用量` 显示"未配置 WBY 接口"**
只有「API 增量 / 实时总量」需要接口配置：在 `.env` 设 `AIGF_USAGE_BASE` / `AIGF_USAGE_KEY`，或用 `用量配置` 指令写入（优先级更高）。本地明细统计不受影响。

**Q：表情包一直不清理**
检查 `.env` 的 `AIGF_MEME_MAX_COUNT` 是否覆盖了默认值；清理在达到上限时触发，且使用次数从部署含频率清理的版本后开始统计。

**Q：私聊 `状态` 不回复**
私聊必须带群号：`状态 123456789`；群聊直接发 `状态`。

---

## 许可证与致谢

- 许可证：[AGPL-3.0](./LICENSE)
- 原作者：[shadow3aaa](https://github.com/shadow3aaa)（nonebot-plugin-nyaturingtest）、[Funny1Potato](https://github.com/Funny1Potato)（nonebot-plugin-aigf）
- 本项目在此基础上新增：引用校验、群友画像扩展、时间模块、DeepSeek 搜索、表情包候选预筛与频率清理、用量统计（分群/缓存命中/花费）、密钥泄露防护等
