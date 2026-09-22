# Creator Agent Studio 用户使用说明

> 把「一句 Brief」交给一支编排好的 AI 内容团队，全程看得见、可干预、有门禁、能复盘，并且**不填一个 API Key 也能完整跑通**。

**读者**：使用者与运营。**回答**：界面怎么操作、运行时怎么配置、怎么评估与回归、出问题怎么排障。
**不负责**：项目定位与上线前人工清单（→ [`README.md`](README.md)）、架构与角色定义（→ [`creator.md`](creator.md)）、工程约定（→ [`ENGINEERING_PRINCIPLES.md`](ENGINEERING_PRINCIPLES.md)）。

---

## 目录

1. [它能做什么](#1-它能做什么)
2. [环境准备与启动](#2-环境准备与启动)
3. [界面导览](#3-界面导览)
4. [完整实操流程](#4-完整实操流程)
5. [状态机与门禁规则](#5-状态机与门禁规则)
6. [运行时配置](#6-运行时配置)
7. [成本熔断与响应缓存](#7-成本熔断与响应缓存)
8. [记忆库与知识复用](#8-记忆库与知识复用)
9. [质量评估（LLM-as-a-Judge）](#9-质量评估llm-as-a-judge)
10. [回归测试与质量门禁](#10-回归测试与质量门禁)
11. [调用轨迹与性能排障](#11-调用轨迹与性能排障)
12. [多语言创作与视频脚本](#12-多语言创作与视频脚本)
13. [接口速查](#13-接口速查)
14. [部署](#14-部署)
15. [数据文件与持久化](#15-数据文件与持久化)
16. [自检与验收](#16-自检与验收)
17. [常见问题](#17-常见问题)
18. [MCP 插件接入（agent harness）](#18-mcp-插件接入agent-harness)

---

## 1. 它能做什么

系统把一次内容创作拆成一条**多智能体流水线**：总控 A0 编排，A1–A11 专业智能体协同，
审核智能体把守门禁，最后由人工拍板。各智能体的职责与产出物定义见 [`creator.md`](creator.md)。

能力总览见 [`README.md`](README.md)「能力概览」。本文只讲**怎么用**：
怎么下 Brief、怎么看流水线、怎么审批与回填、怎么配置与排障。

需要留意的两条默认行为：**默认离线**（内置 Mock 引擎，无密钥可跑通全流程）；
**默认不鉴权**（单机零配置，配置 `CREATOR_API_TOKENS` 后才按租户隔离）。

---

## 2. 环境准备与启动

依赖、安装与 Docker 启动见 [`README.md`](README.md)「快速开始」，这里只补三点：

- **默认离线**：内置离线 Mock 引擎无需任何密钥；接入真实模型见 [6.2 环境变量](#62-环境变量) 或界面右上角「运行时设置」。
- **开发模式**：`npm run dev` 同时起后端（:8787）与 Vite（:5273），Vite 把 `/api` 反代到 8787。
- **Windows**：`pip install` 若报 `WinError 448`，设置 `PYTHONNOUSERSITE=1` 并给 pip 加 `--no-warn-script-location`。

---

## 3. 界面导览

```
┌──────────────────────────────────────────────────────────────┐
│ 顶栏：服务状态 · 任务计数 · OTLP 状态 · 新建任务 · 运行时设置    │
├───────────────┬──────────────────────────────────────────────┤
│ 任务列表       │ 任务详情                                      │
│ （实时刷新）   │  ├ 基本信息 + 质量评分卡                      │
│               │  ├ 人工审批横幅（待审批时出现）                 │
│               │  ├ 发布与效果回填面板（已排期后出现）            │
│               │  ├ 数字人渲染面板（产出视频脚本后出现，开发样例） │
│               │  └ 标签页：流水线看板 / 共享黑板产物 / 评估报告 │
│               │            / 调用轨迹 / 事件时间线 / 运行指标    │
└───────────────┴──────────────────────────────────────────────┘
```

- **任务列表**：品牌/产品、状态、当前阶段、返工轮次、产物数、质量分。
- **流水线看板**：每个智能体的运行状态、置信度、门禁结果与摘要。
- **共享黑板产物**：结构化产物按类型定制渲染；同一类型可**版本对比（diff）**。
- **评估报告**：六维评分、问题与改进建议、评估历史与全库聚合，支持按需重评。
- **调用轨迹**：可折叠 span 树 + 瀑布图 + 耗时排行（见 [11](#11-调用轨迹与性能排障)）。
- **事件时间线**：SSE 实时推送，事件带 `trace_id` / `span_id` 可回溯到 span。
- **运行指标**：系统聚合、成本/缓存/租约/评估/追踪、逐智能体指标。
- **发布与效果回填面板**：含到期时间、投递 / 登记双按钮、投递状态与「全部投递」。
- **数字人渲染面板**：**开发样例**，见 [12.4](#124-数字人渲染开发样例)。
- **运行时设置**：模型 / 智能体模型覆盖 / 编排与门禁 / 成本与缓存 / 向量检索 / 发布投递 / 质量评估 / 访问令牌 / 知识库 / 记忆库。
- **顶栏告警**：`checkpointer` 退化为内存实现时显示「断点续跑不可用」——进程重启后中断任务将无法续跑。

---

## 4. 完整实操流程

### 4.1 新建创作任务

右上角「新建创作任务」，填写 Brief：

| 字段 | 说明 |
| --- | --- |
| 品牌 / 产品 | 必填，用于语气与一致性校验 |
| 传播目标 | 曝光 / 互动 / 转化 / 教育 / 信任 |
| 目标受众 | 越具体越好，直接影响 A1 洞察质量 |
| 主渠道 | 小红书 / 抖音 / 公众号 / 知乎 / 电商详情页 / 官网 / PR；英文常用 Instagram / TikTok / LinkedIn |
| 行业 | 决定合规规则强度 |
| **目标语言** | 中文 / English / 日本語 / 한국어 / Español（见 [12](#12-多语言创作与视频脚本)） |
| 关键词 / 硬性约束 / 期望交付物 | 硬约束会贯穿全流程 |

勾选「自动审批」可跳过人工裁决，适合批量试跑。

### 4.2 观察流水线

创建后 A0–A11 依次执行，界面每 2.5s 拉取快照，同时通过 SSE 接收实时事件。

- 门禁不通过时自动**返工**，最多 `MAX_REVISIONS` 轮；超限升级为**人工裁决**。
- 成本或 token 超预算时**熔断**，后续步骤自动切离线引擎，任务继续跑完不受影响。
- Brief 附带 `kind=document` 素材时，A0 先做 map-reduce **研读**，产物「素材研读要点」供 A2/A3/A4/A6 引用（A6 把与素材一致的主张判为已核实）；研读与正文都吃 token，跑长素材前先调高下节的 token 上限与「文档研读调用数」。
- 硬性约束写明「每篇 N–M 字」（上界 ≥1500）时，A4 自动**分篇**按三种风格逐篇生成长文（注意：别把「合计 X–Y 字」写进期望交付物，会被误当作每篇字数）。

### 4.3 人工审批

全部自动审核通过（或门禁升级）时，任务挂起并弹出审批横幅：

- **通过**：生成多平台发布排期 → 归档交付 → 触发 A11 知识沉淀。
  产物列表的「最终交付物」出现**导出成品**按钮，或直接 `GET /api/tasks/{id}/export` 下载归档文本。
- **退回返工**：带着意见回到上游重跑（消耗返工轮次）。
- **驳回**：任务终止为「已驳回」。

### 4.4 发布登记与效果回填

审批通过后出现「**发布与效果回填**」面板：

1. **登记发布**：逐渠道点「登记发布」或「全部登记发布」。系统不代点平台发布按钮（各平台接口差异大且需授权），
   只记录「哪个渠道、什么时候投出去」这条基线，可填发布链接。
2. **回填效果**：填写渠道、观察窗口、曝光量、点击量、互动量、转化量，点「回填并复盘」。

回填后编排层重跑 A10：上游一旦出现 `actuals`，A10 从「发布前预估」切换到「**发布后复盘**」，
产出新版本效果报告，给出实际 CTR、预估中位对比与 **A/B 结论（是否可放量）**。

> 同一任务可多次回填（如 24h / 72h / 7 天），每次生成新的复盘版本便于对比。

### 4.5 自动投递与待发布队列

排期项带「到期时间」（由建议时段换算，当天该时刻已过则顺延到次日）：

| 投递状态 | 含义 |
| --- | --- |
| `pending` | 已排期，尚未到点 |
| `dispatched` | 已成功推送给 webhook |
| `skipped` | 未配置 webhook，等价比「登记发布」（离线默认行为） |
| `failed` | 投递失败，`last_error` 记录原因，`attempts` 记录重试次数 |

使用方式（三选一）：

1. **界面**：发布面板逐条「投递」或「全部投递」。
2. **外部定时器**：定时调 `POST /api/publish/tick`，一次投递所有已到期条目。
3. **进程内巡检**：设 `PUBLISH_AUTO_DISPATCH=true`（配合 `PUBLISH_TICK_SECONDS`），服务启动后自动巡检。

投递目标由 `PUBLISH_WEBHOOK_URL` 决定：系统只负责把「该投什么」POST 出去，
对接各平台的授权与限流交给你的发布网关（n8n / Zapier / 自建服务）。
`GET /api/publish/queue?dueOnly=true` 可查看跨任务的待发布队列（按到期时间升序）。

### 4.6 查看知识沉淀

任务归档后 A11 把可复用资产写入记忆库。在「运行时设置 → 记忆库」中可查看卡片列表、
类型分布、新鲜度、当前租户与全局计数，并用自然语言检索（与智能体复用同一套打分逻辑）。
新任务创建时 A1/A2/A4 会自动召回历史资产（事件时间线可见「复用来源」）。

### 4.7 查看质量评估

审批通过、任务归档后，任务详情多出「**评估报告**」标签页（见 [9](#9-质量评估llm-as-a-judge)）。
可看最近一次综合分与六维得分，点「规则评估」/「模型评估」随时重评，无需重跑整条流水线。

---

## 5. 状态机与门禁规则

**阶段顺序**

```
INIT → STRATEGY → CREATIVE → PLANNING → DRAFTING → REVIEW → EDITING
→ FACT_CHECK → COMPLIANCE → VISUAL_ADAPT → CHANNEL_ADAPT → APPROVAL
→ PUBLISHED → ANALYZED → MEMORY → ARCHIVED
```

异常分支：`REVISION`（返工中）、`REJECTED`（已驳回）、`FAILED`（执行失败）、`PAUSED`（等待人工）。

| 裁决 | 含义 | 后续动作 |
| --- | --- | --- |
| `pass` | 通过 | 进入下一阶段 |
| `revise` | 需返工 | 携带修改要求回到上游，消耗返工轮次 |
| `reject` | 否决 | 触发人工裁决 |
| 升级 | 自动门禁多次未收敛 | 挂起等待人工裁定 |

**四级门禁**：A5 编辑审校 → A6 事实核查 → A7 品牌合规 → 人工审批。**降级与容错**：调用失败 → 重试退避 → 降级离线引擎；成本熔断 → 强制离线引擎；进程重启 → 未完成任务断点续跑。

---

## 6. 运行时配置

### 6.1 界面配置（即时生效，无需重启）

点击顶栏「运行时设置」：

| 分组 | 配置项 |
| --- | --- |
| 模型 | 提供方（mock / openai）、模型名、Base URL（含本地端点一键填入）、API Key、Temperature、Max Tokens、超时 |
| 智能体模型覆盖 | 每个智能体可单独覆盖 模型 / Base URL / API Key（留空继承全局；与站点监控同为持久化设置） |
| 编排与门禁 | Turn Budget、最大返工轮次、质量分阈值、自动审批 |
| 成本与缓存 | 单任务成本上限（USD）、单任务 token 上限、是否启用响应缓存、文档研读调用数（`digestMaxCalls`） |
| 向量检索 | 提供方（local / openai）、模型、语义权重（0–1）、API Key |
| 发布投递 | webhook 地址、失败重试次数、是否自动投递 |
| 质量评估 | 介入方式（off / advisory / blocking）、评估器（offline / llm）、通过线、权重 |
| 访问令牌 | 本地保存 API Token（随请求带 `X-API-Token`） |
| 知识库 / 记忆库 | 只读浏览与检索 |

> 模型提供方选 `openai` 时兼容一切 OpenAI 协议网关（DeepSeek / 通义 / 豆包 / vLLM / Ollama）。
> 本地模型一键填端点：Ollama `http://localhost:11434/v1`、LM Studio `http://localhost:1234/v1`、vLLM `http://localhost:8000/v1`
> （须带 `http://` 前缀；本机 / 私网端点**不计费**、token 照记，多数文本模型无需 Key）。API Key 回显为掩码，明文不落盘。

### 6.2 环境变量

可在根目录 `.env` 或进程环境变量中设置（进程环境变量优先）。**本表是配置项的唯一归属处**，
完整默认值以 [`.env.example`](.env.example) 为准：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` / `HOST` | `8787` / `127.0.0.1` | 服务端口 / 监听地址（容器清单固定 `0.0.0.0`；局域网直连改 `0.0.0.0`，暴露前必设令牌） |
| `LLM_PROVIDER` | `mock` | `mock` / `openai` |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI 兼容网关地址 |
| `OPENAI_API_KEY` / `OPENAI_MODEL` | 空 / `gpt-4o-mini` | 密钥与模型名 |
| `LLM_TEMPERATURE` / `LLM_MAX_TOKENS` / `LLM_TIMEOUT_MS` | `0.7` / `2048` / `60000` | 采样温度 / 单次最大输出 token / 单次超时（毫秒） |
| `TURN_BUDGET` / `MAX_REVISIONS` / `QUALITY_THRESHOLD` | `25` / `2` / `75` | 单任务最大编排步数 / 最大返工轮次 / 质量分门禁 |
| `AUTO_APPROVE` | `false` | 新任务是否默认自动审批 |
| `COST_BUDGET_USD` / `TOKEN_BUDGET` | `0.5` / `50000` | 单任务成本与 token 熔断线 |
| `LLM_CACHE` | `true` | 是否启用响应缓存 |
| `EMBEDDING_PROVIDER` / `EMBEDDING_WEIGHT` | `local` / `0.35` | 向量提供方（`local` / `openai`）/ 语义权重（`0` = 纯关键词） |
| `LLM_VISION` / `LLM_VISION_MAX_IMAGES` | `false` / `6` | 图片素材是否作为多模态内容块发给模型（仅读图模型可开；有智能体模型覆盖时按该智能体实际端点判定）/ 单次最多注入图片数 |
| `WEB_SEARCH_PROVIDER` / `_API_URL` / `_API_KEY` | `none` / 空 / 空 | 联网检索网关（`none` = 完全离线；POST 查询 → JSON 结果的最小契约见 [`.env.example`](.env.example)） |
| `WEB_SEARCH_MAX_RESULTS` / `_TIMEOUT_MS` / `_FETCH_PAGES` / `_MAX_PAGES` | `8` / `15000` / `true` / `4` | 每次检索条数 / 超时 / 是否抓取命中页正文（私网地址一律拒绝）/ 最多抓取页数 |
| `EMBEDDING_BASE_URL` / `_API_KEY` / `_MODEL` / `_DIM` | 空 / 空 / `text-embedding-3-small` / `256` | 向量网关（留空复用 `OPENAI_*`）/ 本地向量维度（最小 16） |
| `PUBLISH_WEBHOOK_URL` | 空 | 投递目标；为空时投递退化为「登记发布」 |
| `PUBLISH_RETRY` / `PUBLISH_AUTO_DISPATCH` / `PUBLISH_TICK_SECONDS` | `2` / `false` / `60` | 失败重试次数 / 是否自动投递 / 巡检间隔（秒，最小 5） |
| `DIGITAL_HUMAN_PROVIDER` / `_API_URL` / `_API_KEY` | `sample` / 空 / 空 | 数字人样例通道；http 通道 URL 留空时**显式失败** |
| `OTLP_ENDPOINT` / `OTLP_HEADERS` | 空 | 追踪导出目标与鉴权头；为空则只做进程内追踪 |
| `OTEL_TRACES_SAMPLER` / `_ARG` | `parentbased_always_on` / `1.0` | 导出面采样器与比例（进程内轨迹不受影响） |
| `JUDGE_MODE` / `JUDGE_PROVIDER` / `JUDGE_MODEL` | `advisory` / `offline` / 空 | 评估介入方式 / 评估器 / 模型名 |
| `JUDGE_PASS_THRESHOLD` / `JUDGE_WEIGHT` | `75` / `0.2` | 评估通过线 / 评估分在综合质量分中的权重 |
| `CREATOR_API_TOKENS` | 空 | 访问令牌；支持 `tok1,tok2` 或 `acme:tok1,beta:tok2` |
| `CREATOR_DATA_DIR` | `<项目根>/data` | 持久化目录 |
| `CREATOR_STORAGE` / `CREATOR_DATABASE_URL` / `CREATOR_REDIS_URL` | `file` / 空 | 存储后端（`file` / `pg`）与 pg 模式连接串 |
| `CREATOR_VERIFY_PROVIDER` | 空 | 置 `openai` 可让验证脚本走真实网关（默认强制 mock） |

> **验证脚本默认强制离线**：`doctor.py` / `golden_eval.py` / `smoke_api.py` / `verify_contracts.py` 断言的是契约与逻辑，
> 必须秒级、可复现、不花钱；即使 `.env` 指向真实网关也会强制 `mock`。量真实链路用 `python scripts/real_check.py` 或设 `CREATOR_VERIFY_PROVIDER=openai`。

### 6.3 访问令牌与租户隔离

默认**不鉴权**（单机零配置）。设置 `CREATOR_API_TOKENS` 后，除 `GET /api/health` 外的所有 `/api/*` 都需携带令牌：

```bash
CREATOR_API_TOKENS=tok_abc123                 # 单租户：全部归 default
CREATOR_API_TOKENS=acme:tok_aaa,beta:tok_bbb  # 多租户：令牌 → 租户名
```

携带方式（优先级从高到低）：

```bash
curl -H "Authorization: Bearer tok_abc123" http://127.0.0.1:8787/api/tasks
curl -H "X-API-Token: tok_abc123"          http://127.0.0.1:8787/api/tasks
curl "http://127.0.0.1:8787/api/tasks?token=tok_abc123"   # SSE / EventSource 无法自定义头时用
```

- 令牌无效或缺失 → `401`。任务、黑板、发布、回填、事件流、指标均按租户过滤；跨租户访问返回 `404`（不泄露任务是否存在）。
- **记忆库同样按租户隔离**：写入、召回、列表与容量计量都在租户内进行。
- 前端在「运行时设置 → 访问令牌」填入即可，客户端自动带 `X-API-Token`，SSE 自动追加 `&token=`。

---

## 7. 成本熔断与响应缓存

**成本账本**：每任务一份，记录 prompt / completion token、累计成本、调用次数与缓存命中次数。

- 累计成本 ≥ `COST_BUDGET_USD` 或累计 token ≥ `TOKEN_BUDGET` 时触发**熔断**：后续步骤强制走离线引擎，任务**不中断**。
- 成本达到预算 80% 时提前预警。

**响应缓存**：只缓存真实模型调用，键为 `(provider, model, temperature, max_tokens, messages)` 的指纹。

- 断点续跑、人工打回重试时**不重复付费、零延迟**；默认容量 256 条、TTL 1 小时，LRU 淘汰。
- 离线引擎不产生费用，因此不入缓存。

「运行指标」页可直接看到累计成本、平均单篇成本、单任务预算、熔断任务数、缓存命中率。

---

## 8. 记忆库与知识复用

A11 把每次任务的最佳实践沉淀为跨任务知识卡片，供后续任务 RAG 召回。

- **卡片类型**：品牌、案例、模板、经验。
- **检索打分**：`score = 关键词分 × (1 - 语义权重) + 向量余弦 × 语义权重`，再叠加同品牌 / 同渠道 / 同行业加成；
  低于阈值不返回，避免「什么都召回一点」的虚假命中。
- **向量分量**：默认 `provider=local`（确定性 hashing，离线可用）；设 `openai` 则调任意兼容 `/embeddings`，**远端失败自动回退本地向量**。
- **语义权重**：`EMBEDDING_WEIGHT`（默认 `0.35`），调为 `0` 退化为纯关键词检索。
- **召回范围**：A1/A2/A4 执行前召回历史资产并注入上下文，排除当前任务避免自我复用。
- **租户隔离**：写入 / 召回 / 列表 / 统计 / 容量全部按租户分区；未启用鉴权时全部归 `default`。
- **新鲜度与下线**：新鲜 / 陈旧 / 过期三级，同分时优先新鲜；超龄卡片读写时自动清理。**容量**按每租户计量，超出按最旧淘汰。

> 界面「运行时设置 → 记忆库」可看到当前检索方式（提供方 / 模型 / 语义权重 / 已建索引条数）、
> 租户与全局计数，命中项会显示语义分，便于判断是关键词命中还是语义命中。

---

## 9. 质量评估（LLM-as-a-Judge）

门禁回答「能不能发布」（否决式），评估回答「有多好、差在哪」（度量式）。两者刻意解耦：
把质量度量塞进门禁，会被有噪声的评分放大成流程抖动。

### 9.1 六维评分

每次评估输出 0–100 综合分与六个维度细项（各 0–5 分，带判分理由与证据）：

| 维度 | 权重 | 看什么 |
| --- | --- | --- |
| 需求契合 | 0.25 | 是否命中 Brief 的关键词、受众、渠道标题上限与硬性约束 |
| 合规安全 | 0.20 | 广告法词库 + 行业规则（**与 A7 用同一份词库**） |
| 结构完整 | 0.15 | 标题 / 正文 / CTA / 话题标签是否齐备且长度合规 |
| 品牌语气 | 0.15 | 品牌名与调性关键词是否体现 |
| 事实稳妥 | 0.15 | 数字与主张是否有来源（优先采信 A6 结论） |
| 吸引力 | 0.10 | 标题钩子、行动号召与互动引导 |

裁决：综合分 ≥ 通过线为 `pass`，低于通过线 80% 为 `reject`，其余 `review`。

### 9.2 两种评估器与介入方式

| 评估器 | 特点 | 何时用 |
| --- | --- | --- |
| `offline`（默认） | 确定性规则，零依赖、可离线，**同一输入永远同一分数** | CI 回归、Prompt 改动的对照尺 |
| `llm` | 走当前 OpenAI 兼容网关做真正的 LLM-as-a-Judge | 需要语义级判断时 |

> `llm` 评估器**任何异常都会静默回退** `offline`，并在报告里标注「已回退规则评估器」与原因——
> 评估是旁路能力，不会因网关抖动让创作链路出问题。

| `JUDGE_MODE` | 行为 |
| --- | --- |
| `off` | 不评估 |
| `advisory`（默认） | 只产出分数与建议，**完全不改变门禁结论** |
| `blocking` | `reject` 升级人工裁决、`review` 计入返工理由 |

- **生效时机**：门禁时刻评估一次（可参与 `blocking` 裁决），交付前再评估一次（用于跨任务回归对比）。
- **对综合质量分的影响**：由 `JUDGE_WEIGHT` 决定（默认 `0.2`），设为 `0` 即完全不影响。
- **评分口径版本**：报告里带 `RUBRIC_VERSION`。**跨版本对比分数前必须先对齐它**，否则「分数变好了」可能只是尺子变了。

### 9.3 在界面上使用

任务详情 →「**评估报告**」标签页：顶部显示总分、裁决、评估器、置信度与评分口径版本；
六条维度得分条（含理由与证据）并列出问题与改进建议；「规则评估」「模型评估」可随时重评；
下方是本任务评估历史与全库聚合（均分 / 通过率 / 各维度均分）。

> 维度均分是 Prompt 调优的抓手：优先补**最弱的那一个维度**，比「整体重写提示词」更容易看到变化。

---

## 10. 回归测试与质量门禁

「我把 Prompt 改好了」在没有固定输入时**无法验证**：内容创作本身发散，换一个 Brief 分数就会漂移。
黄金数据集把**输入固定**、把**输出压缩成标量**，于是这个问题变得可判定。

### 10.1 数据集长什么样

`golden/briefs/*.json` 是固定用例，覆盖**全部渠道 × 多个行业**，含边界用例（极简 Brief、强监管行业、英文多语言）；
`golden/baseline.json` 是基线，逐用例记录质量分、评估分、返工轮次、产物数、事实准确率、合规裁决序列，
以及生成它时的**评分口径**与**引擎**。用例清单与覆盖矩阵见界面「运行时设置 → 回归测试」或 `--coverage`。

> 数据集放在 `golden/` 而非 `data/`：它是**随代码版本化的测试资产**，必须能被 review、diff、追责 ——
> 否则「悄悄改宽基线让 CI 变绿」就是一条无人察觉的作弊路径。

### 10.2 怎么判定「变差了」

判定分两层，**互补且缺一不可**：分数层看基线，内容层看硬约束。

| 情况 | 判定 |
| --- | --- |
| 运行失败 / 全量运行时有用例缺失 | 不通过 |
| 质量分或评估分回退超过容差 | **回退** |
| 分数没动但**返工轮次 +1** | **回退**（更慢更贵） |
| 监管用例**首轮合规不再被拦截** | **回退**（门禁强度下降） |
| **内容级期望未通过**（见下） | **回退** |
| 容差内的波动 | 持平（不把噪声当回归） |
| 部分运行（`-n` / `--case`） | 只对范围内用例判定，并标注「部分运行」 |

**内容级期望**是分数之外的硬约束 —— 一篇高分但漏了品牌名的稿子依然不可交付：

- 品牌名出现在最终文本；Brief 关键词覆盖率 ≥ 门槛（**不要求全部命中**，否则会逼出「硬塞关键词」）。
- 用例特有的禁用表述逐条写在 `expect.must_not_contain`。
- **无广告法阻断级用语**：由合规词库现算，**词库更新后自动生效**，无需逐条维护。
- 标题 ≤ 渠道字数上限（渠道规则现算，用例可用 `max_title_length` 覆盖）；质量分 / 评估分 ≥ 门槛；返工轮次 ≤ 上限。
- 强监管用例断言「首轮门禁应被拦截」，验证门禁真的拦得住。

三点值得展开：

- **返工轮次增加即使总分没掉也算劣化**；强监管用例首轮被拦截本是期望行为 ——
  若某次改动让首轮直接放行，分数可能不降反升（少了一轮返工），看起来是改进，实际是**门禁失效**。
- **否定语境不算违规**：「不得涉及疾病预防与治疗功能」是免责声明，不会被判成违规宣称；
  判定按**小句**进行 ——「不得用于治疗，但可治疗失眠」的后半句是肯定宣称，照样会被判出来。
- **调性不做机器断言**：`克制`、`用数据说话` 这类抽象描述不是要逐字写进正文的关键词，
  强制命中只会逼出「堆语气词」，反而与「克制」背道而驰；调性由人工抽查覆盖。

### 10.3 在界面与命令行用

界面「运行时设置 → **回归测试**」：跑全量回归 / 快速跑 3 条 / 基线对比表（逐用例结论、分数增量、返工轮次、
产物数与原因）/ 用例清单与覆盖矩阵。

```bash
python scripts/golden_eval.py                     # 跑全量并与基线对比（退出码即结论）
python scripts/golden_eval.py -n 3                # 只跑前 3 条
python scripts/golden_eval.py --case <case_id>    # 只跑指定用例
python scripts/golden_eval.py --coverage          # 只看覆盖矩阵
python scripts/golden_eval.py --tolerance 5       # 放宽回归容差
python scripts/golden_eval.py --update-baseline   # 把本次结果写为新基线
```

**更新基线的纪律**：更新等于「把当前分数认定为新的合格线」，因此存在回归/失败/缺失时
`--update-baseline` **会拒绝执行**，必须显式加 `--force`。请先逐条确认回退都能解释，再更新。

---

## 11. 调用轨迹与性能排障

事件时间线告诉你「**发生了什么**」，调用轨迹告诉你「**时间和钱花在哪一层**」。
「这次任务为什么慢」翻事件是拼不出结论的，看 span 耗时排行一眼就清楚。

### 11.1 界面上看

任务详情 →「**调用轨迹**」标签页：

- **span 树**：可逐级折叠，每个 span 内联显示关键属性（模型名、token 数、是否命中缓存、门禁裁决、降级原因……）。
- **瀑布图**：横条表示该 span 的时间位置与长度，一眼看出串行/嵌套关系。
- **耗时排行**：按 span 名聚合的次数 / 总耗时 / 平均 / 峰值 / 占比，并给出「模型调用耗时占比」。

「运行指标」页也有跨任务的全局 span 耗时排行。

### 11.2 span 树长什么样

```
graph.invoke#0                    ← 每次 LangGraph 调用包一层
├─ A1.strategy_brief              ← 一个智能体一次执行 = 一个 span
│   └─ llm.A1.strategy            ← 模型调用（client span）
├─ memory.retrieve                ← RAG 召回（带命中条数）
├─ gate.review                    ← 门禁整体
│   ├─ A5.edited_copy / A6.fact_check_report / A7.compliance_report
│   └─ judge.evaluate
└─ …
human.wait                        ← 人工等待（独立，不计入执行链耗时）
graph.invoke#16                   ← 人工裁决后恢复执行
```

> **人工等待单独成段**是刻意的：否则排障时会把「人在犹豫三小时」误读成「系统卡了三小时」。

### 11.3 接到 Jaeger / OTLP collector（可选）

默认**不需要**任何外部服务；想接真实追踪后端时配置一个环境变量即可：

```bash
docker compose up -d --build                             # 方式 A：一键起「应用 + Jaeger」，Jaeger :16686
OTLP_ENDPOINT=http://localhost:4318 python -m app.main   # 方式 B：已有 collector，只告诉应用往哪发
```

| 项 | 行为 |
| --- | --- |
| `OTLP_ENDPOINT` 为空 | **不加载 OTel SDK**，只做进程内追踪（span 树 + `data/traces/*.json`） |
| 配置了 | 把**同一份 span** 转发给 collector —— trace_id / span_id 与本地 JSON 完全一致，可按 id 直接对查 |
| `OTLP_HEADERS` | 形如 `key1=value1,key2=value2`，用于带鉴权的托管 collector |
| 端点写法 | 写 `http://host:4318` 即可，`/v1/traces` 会自动补全 |

顶栏在已接入时显示「OTLP 已接入」；`GET /api/health` 的 `otlp` 字段可看到导出数量与失败数。转发失败**不会影响任务**，失败次数在 `health.otlp.failed` 里可见。

### 11.4 与 OpenTelemetry 的关系（请如实理解）

- **trace_id / span_id**：W3C 格式（32 / 16 位十六进制），与 OTel 语义一致；span 结构（kind / parent /
  attributes / status / 嵌套）与 OTel span 同构。
- **本地导出**：`data/traces/<task_id>.json`，含 `otel[]` 段；配置 `OTLP_ENDPOINT` 后转发到 Jaeger，**id 与本地一致**。
- **采样只作用于导出面**：`OTEL_TRACES_SAMPLER` / `_ARG` 与 OTel 语义对齐；未采样的 trace 不转发、不落盘，
  但**进程内轨迹始终完整**（Jaeger 里查不到它是预期行为）；入站 `traceparent` 的采样标记优先于本地比例。
- **跨进程传播**：W3C `traceparent` —— 入站 `POST /api/tasks` 沿用远端 trace_id，出站 webhook 自动携带当前 span；坏头一律忽略。

一个实现细节：span 是**自采集**的，转发时**手工构造 OTel 的 `ReadableSpan`** 而非用 OTel API 重建 ——
否则 SDK 会生成新的 span_id，「按本地 trace_id 去 Jaeger 查这一条」就失效了。

### 11.5 命令行看

```bash
curl -s "http://127.0.0.1:8787/api/tasks/<task_id>/trace" | python -m json.tool   # span 树 + 耗时聚合
cat data/traces/<task_id>.json                                                    # 落盘的 OTel 形状 trace
```

**降级与缓存命中不会计为错误**（它们是设计出来的正常路径），因此 span 错误率是可信信号：非 0 就真的有问题。

---

## 12. 多语言创作与视频脚本

系统支持 **中文 / English / 日本語 / 한국어 / Español**。

### 12.1 怎么用

在 Brief 里填「目标语言」即可（也接受 `en-US`、`English`、`英文` 等别名）。不填默认中文，行为与以往一致。

| 语言 | 常用渠道 | 标题口径 | 商业推广披露 |
| --- | --- | --- | --- |
| 简体中文 | 小红书 / 抖音 / 公众号 / 知乎 / 电商 / 官网 / PR稿 | 按**字** | 需标注「合作」 |
| English | Instagram / TikTok / LinkedIn / Email / Landing Page / Press Release | 按**词** | `#ad` |
| 日本語 | X / Instagram / LINE / プレスリリース / 商品ページ | 按**字** | `#PR` |
| 한국어 | 네이버 블로그 / 인스타그램 / 유튜브 / 카카오톡 / 보도자료 | 按**字** | `#광고` |
| Español | Instagram / TikTok / LinkedIn / Email / Página de producto | 按**词** | `#publicidad` |

**它是「原生创作」，不是翻译**：选定非中文语言后，创作智能体会收到本地化指令，要求用目标语言原生创作（不先写中文再翻译）、
遵守当地表达惯例、按目标语言字数口径控制标题、遵守当地合规红线。

> **标题口径用错会让「合规」失去意义**：一个 12 词的英文标题按字符算有 60+ 字，
> 用中文字数上限去判断会得出完全错误的结论。系统在交付、评估、回归断言三处都按语言口径处理。

### 12.2 合规上的一条重要限制

**广告法词库只覆盖中文。** 选择非中文语言时，A7 会在合规报告里明确声明「该语言尚无自动合规词库，
需人工复核」，把当地红线（如美国 FTC 披露要求）写入 `risks`，并**强制标记需要人工复核**，而不是静默判为通过。

> **「如实告知未覆盖」与「假装检查过了」的区别，就是产品能否被信任的区别。**
> 非中文市场的合规表述必须由人工（目标市场法务）把关。

界面「运行时设置 → 知识库」可以看到每种语言的合规覆盖情况。
**记忆库按语言隔离**：英文资产不会被中文任务当作品牌调性基线复用，反之亦然。

### 12.3 视频脚本

短视频形态的任务会产出**独立的「视频脚本」产物**（在「共享黑板产物」里按类型筛选即可看到）：

- **钩子**（黄金 3 秒）、**分镜表**（每镜含时长 / 画面 / 口播 / 字幕 / 机位 / 意图）、
  **口播表**、**字幕表**（含起止时间）、**行动号召**、**拍摄要点**、**合规注意**。
- 形态参数按渠道给（抖音 45s/9:16/5 镜、视频号 60s、B 站 120s/16:9、TikTok 30s…），
  时间轴由知识层骨架生成，**保证单调且总时长与目标一致**。
- **只在需要时产出**：短视频渠道、交付物点名要脚本、或渠道形态含视频特征 —— 任一命中。
  图文渠道**不会**被塞入无关脚本（黄金数据集对这一点做了双向断言）。
- 界面以**时间轴**呈现（比例条 + 分镜表 + 时间轴覆盖率），并对「存在无口播分镜」直接告警。

### 12.4 数字人渲染（开发样例）

> **定位**：数字人渲染**不在本系统内实现** —— 各家服务的授权、形象库、计费与回调协议差异极大，
> **接哪家、要不要接属产品形态决策，由你决定**。系统提供可回归的接入样例（`app/core/digital_human.py`）。

- **前置条件**：任务已产出 `video_script` 产物；没有脚本时创建接口返回 409。
- **两条样例通道**（创建作业时可用 `provider` 覆盖）：`sample`（默认，离线确定性模拟并按脚本生成渲染清单）
  与 `http`（配置 `DIGITAL_HUMAN_API_URL` 后对接「POST 建任务 → GET 查状态」最小契约，未配置时**显式失败**）。
- **界面**：任务详情出现「数字人渲染」面板（创建作业、进度条、渲染清单分镜表、成片地址）。
- **生命周期**：状态在读取时惰性推进（不靠后台线程）；任务删除时渲染作业一并回收；按租户隔离。

---

## 13. 接口速查

所有接口挂载在 `/api` 前缀下。**接口契约以代码 `app/api/routes.py` 为准，
完整清单见运行中的 `/docs`（FastAPI 自动生成）**，这里只列最常用的：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 + 当前配置 + 断点续跑后端 + OTLP 状态（**免鉴权**） |
| GET / POST | `/api/tasks` | 任务列表 / 新建任务 |
| GET / DELETE | `/api/tasks/{id}` | 任务详情（含事件与黑板快照）/ 删除任务 |
| POST | `/api/tasks/{id}/decide` | 人工裁决 |
| GET | `/api/tasks/{id}/events` | SSE 实时事件流 |
| GET | `/api/tasks/{id}/trace` | 调用轨迹 span 树 |
| POST | `/api/tasks/{id}/evaluate` | 按需评估 |
| POST | `/api/tasks/{id}/publish` · `/publish/dispatch` | 登记发布 / 自动投递 |
| POST | `/api/tasks/{id}/feedback` | 回填真实效果，触发 A10 复盘 |
| GET | `/api/tasks/{id}/export` | 下载成品导出文本（仅终稿任务） |
| GET | `/api/evaluations` | 评估历史与全库聚合 |
| POST | `/api/golden/run` · GET `/api/golden/status` | 触发黄金回归 / 轮询进度 |
| GET | `/api/memory` · POST `/api/memory/search` | 记忆库列表 / 检索 |
| GET | `/api/metrics` | 系统聚合指标（成本 / 缓存 / 租约 / 评估 / 追踪） |

若设置了 `CREATOR_API_TOKENS`，除 `/api/health` 外都需携带令牌（见 [6.3](#63-访问令牌与租户隔离)），否则返回 `401`。

**SSE 事件类型**：`task.created`、`task.status`、`phase.enter`、`agent.start/progress/finish`、
`gate.decision`、`revision.requested`、`approval.required/decided`、`blackboard.write`、
`judge.scored`、`task.completed/failed`、`log`。

---

## 14. 部署

部署方案的唯一归属是 [`DEPLOYMENT.md`](DEPLOYMENT.md)（裸机 systemd + 反代 / Docker·compose / k8s /
Windows 任务计划的选择、安装、升级与回滚）。本文只留最短路径与三个最易踩的点：

```bash
docker compose up -d --build     # 容器：应用 + Jaeger（数据在命名卷，勿省挂卷）
python -m app.main               # 裸机 / 开发：默认 http://127.0.0.1:8787
python scripts/check_deploy.py   # 部署清单自检（不需要 Docker daemon）
```

- **数据必须持久化**（`/data` 或 `<项目根>/data`）：不挂卷则容器重建即丢任务与记忆库；
- **对外暴露前必须设置 `CREATOR_API_TOKENS`**（决定租户隔离边界，为空则完全不鉴权）；
- **应用默认只监听 127.0.0.1**：对外访问经反向代理（样例 `deploy/nginx` / `deploy/caddy`）；
  容器清单已显式设 `HOST=0.0.0.0`，裸机直连在 `.env` 里改。

> k8s 清单副本数固定 1（`Recreate`）：默认 file 存储多副本会状态分裂；横向扩展先切 `CREATOR_STORAGE=pg`
> （步骤见 [`storage_contract.md`](storage_contract.md)）。

---

## 15. 数据文件与持久化

默认写入 `<项目根>/data`（可用 `CREATOR_DATA_DIR` 覆盖）：

| 文件 | 内容 |
| --- | --- |
| `data/tasks/*.json` | 任务快照 |
| `data/blackboard.json` | 共享黑板（事实 / 评审 / 活动 / 租约） |
| `data/memory.json` | A11 记忆库卡片（含租户归属） |
| `data/evaluations.json` | 评估历史 |
| `data/traces/<task_id>.json` | 调用轨迹（span 树 + OTel 形状的 `otel[]` 段） |
| `data/digital_human.json` | 数字人渲染作业（开发样例） |
| `data/checkpoints.sqlite` | LangGraph 检查点（断点续跑） |

> `CREATOR_STORAGE=pg` 时任务/黑板/记忆库/评估/数字人作业改存 PostgreSQL（`payload jsonb`）、
> 意图租约存 Redis、检查点走 PostgresSaver，`traces/` 导出仍在本目录。两后端契约与迁移步骤见
> [`storage_contract.md`](storage_contract.md)；存量迁移用 `python scripts/pg_migrate.py`（幂等，先 `--dry-run`）。

**随代码版本化、不在 `data/` 下的资产**：`golden/briefs/*.json`（黄金用例）、`golden/baseline.json`（基线）、
`.doctor-data/`（自检/冒烟/回归脚本的临时数据目录，可随时整个删掉）。

> 优雅退出时会自动刷盘；删除任务时会顺带清理该任务的检查点与 trace 文件。
> 自检与冒烟脚本使用 `.doctor-data/`，不会碰你的 `data/`。

---

## 16. 自检与验收

```bash
python scripts/doctor.py                 # 环境与能力自检（覆盖范围以脚本输出清单为准，退出码即结论）
python scripts/golden_eval.py            # 黄金数据集回归（分数层 + 内容级硬约束）
python scripts/golden_eval.py --coverage # 只看渠道覆盖矩阵
python scripts/smoke_api.py              # 端到端验收：拉起真实服务，逐条核对 /api/* 与 SSE 契约
python scripts/verify_contracts.py       # 快速契约核验：评估 / 租户 / 轨迹 / 传播采样 / 数字人样例闭环
python scripts/check_deploy.py           # 部署清单核验（不需要 Docker daemon）
python scripts/pg_check.py               # PG 存储层专项回归（需 CREATOR_STORAGE=pg + 本地 postgres/redis）
python scripts/pg_migrate.py --dry-run   # file → pg 存量迁移预览（幂等）
python scripts/real_check.py --tasks 1   # 真实网关链路核验（延迟 / token / 成本 / 缓存 / 门禁）
python scripts/stress_llm.py -n 12 -c 4  # 并发正确性 / 性能基线
npm run typecheck && npm run build       # 前端
```

`doctor.py` 覆盖离线引擎、记忆库 RAG 与租户隔离、向量检索、发布排期、鉴权、检查点后端、评估器、
回归判定器、调用轨迹与 OTLP、多语言、视频脚本、多模态与计算沙箱等；`smoke_api.py` 覆盖主流程、
自动审批、记忆与发布闭环、评估流水线、调用轨迹、聚合指标、错误分支与租户隔离。
**各自的确切覆盖范围以脚本输出清单为准**，本文不复述。

**持续集成**：`.github/workflows/ci.yml` 已把上述关卡接成自动化门禁（**无需任何模型密钥**，默认走离线引擎）：
后端 `doctor.py` → `golden_eval.py` → `verify_contracts.py` → `check_deploy.py` → `smoke_api.py` → `stress_llm.py`；
前端 `typecheck` + `build`；另有一道文档一致性关卡（关键章节与接口速查是否仍被提及）。
失败时自动上传 `.doctor-data/**/server.log` 作为诊断产物。

`stress_llm.py` 输出完成率、端到端延迟分位、返工轮次与首轮通过率、质量分、成本 / 缓存命中率 / 熔断任务数，
以及**黑板租约冲突与活跃租约残留**（应归零）。它在临时目录里拉起独立实例，不会污染 `data/`。

---

## 17. 常见问题

**Q：需要 API Key 吗？** 不需要。默认内置离线引擎全流程可跑通；接真实模型在「运行时设置」切到 `openai` 即可。

**Q：接口突然返回 401？** 服务端设置了 `CREATOR_API_TOKENS`。请在请求里带令牌，或在界面「运行时设置 → 访问令牌」填入；`GET /api/health` 不需要令牌。

**Q：排期一直不自动投递？** 确认 `PUBLISH_AUTO_DISPATCH=true`（默认关闭）；也可外部定时调 `POST /api/publish/tick`，或界面手动「全部投递」。只有**已到到期时间**的条目才会被自动投递。

**Q：回归测试显示「回退」，但只改了一点点？** ①看对比表的「说明」列（哪个指标超了容差）；②是否只是**返工轮次 +1**（即使分数没掉也判回退）；③基线 `rubric` 是否与当前一致（不一致则分数不可比）。容差内的波动不判回退。

**Q：评估分为什么和预期差很多？** 先确认三件事：①「评估报告」页顶部的评分口径版本（跨版本不可比）；②评估器是 `offline` 还是 `llm`（若显示「已回退规则评估器」说明模型评估当时不可用）；③通过线是否被改过。想让分数影响门禁，需把 `JUDGE_MODE` 从 `advisory` 改成 `blocking`。

**Q：记忆库检索不到我刚沉淀的知识？** 依次排查：①是否**同一租户**；②`score` 是否低于阈值；③是否被当作当前任务自己的卡片被排除；④是否已超龄被自动下线。

**Q：顶栏出现「断点续跑不可用」？** 说明 SQLite 检查点初始化失败（通常是数据目录不可写），系统已退回内存检查点，**运行中任务在进程重启后无法续跑**。检查 `CREATOR_DATA_DIR` 权限后重启；`GET /api/health` 的 `checkpointer` 字段可看到具体错误。

---

## 18. MCP 插件接入（agent harness）

把本项目当**工具插件**接入支持 MCP（Model Context Protocol）的 harness / agent（DeepSeek harness、Claude Code、Trae、Cursor 等）。适配层 [`app/mcp_server.py`](app/mcp_server.py) 只是 REST API 的协议转换：**状态唯一归属主服务**，harness 起的任务在 Web 界面同步可见，鉴权与租户判定复用上游。工具 9 个：`creator_health` / `creator_list_tasks` / `creator_create_task` / `creator_get_task` / `creator_wait_task`（轮询到待审批或终态）/ `creator_decide`（approve/revise/reject 人工裁决）/ `creator_export`（成品全文）/ `creator_search_memory` / `creator_trace`。

**stdio（本机，推荐）**：先启动服务（`python -m app.main`），在 harness 的 MCP 配置里加：

```json
{ "mcpServers": { "creator": {
    "command": "python", "args": ["-m", "app.mcp_server"], "cwd": "<项目根>" } } }
```

**HTTP（远程 harness）**：`python -m app.mcp_server --http --port 8766`，接入点 `http://<host>:8766/mcp`（streamable-http）。暴露到本机以外前务必给上游服务配置 `CREATOR_API_TOKENS`：HTTP 模式只透传请求自带的 Authorization / X-API-Token（租户判定在上游完成）；stdio 模式用 `CREATOR_MCP_TOKEN` / `CREATOR_MCP_BASE_URL` 指定令牌与上游地址，完整清单见 [.env.example](.env.example)「MCP 插件」。

典型链路：`creator_health` 确认配置 → `creator_create_task` 提交 Brief → 反复 `creator_wait_task` 等待 → `awaiting_approval` 时 `creator_decide` 拍板 → `creator_export` 取全文。

---

需要变更流水线行为或新增智能体时，请先阅读 `creator.md` 的「核心接口与数据模型」章节，确保前后端契约一致。
