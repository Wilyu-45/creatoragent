# Creator Agent Studio 用户使用说明

> 一句话：把「一句 Brief」交给一支编排好的 AI 内容团队，全程看得见、可干预、有门禁、能复盘，并且**不填一个 API Key 也能完整跑通**。

本文是面向使用者的操作手册。想了解架构设计请看 `creator.md`，想了解迭代计划请看 `plan.md`，开发进度与踩坑记录见 `MEMORY.md`。

---

## 目录

1. [它能做什么](#1-它能做什么)
2. [环境准备与启动](#2-环境准备与启动)
3. [界面导览](#3-界面导览)
4. [完整实操流程](#4-完整实操流程)
5. [运行时配置](#5-运行时配置)
6. [成本熔断与响应缓存](#6-成本熔断与响应缓存)
7. [记忆库与知识复用](#7-记忆库与知识复用)
8. [质量评估（LLM-as-a-Judge）](#8-质量评估llm-as-a-judge)
9. [回归测试与质量门禁](#9-回归测试与质量门禁)
10. [调用轨迹与性能排障](#10-调用轨迹与性能排障)
11. [容器化部署](#11-容器化部署)
12. [状态机与门禁规则](#12-状态机与门禁规则)
13. [多语言创作](#13-多语言创作)
14. [REST / SSE 接口速查](#14-rest--sse-接口速查)
15. [数据文件与持久化](#15-数据文件与持久化)
16. [自检与验收](#16-自检与验收)
17. [需要人工协助的事项](#17-需要人工协助的事项)
18. [常见问题](#18-常见问题)
19. [目录结构](#19-目录结构)

---

## 1. 它能做什么

系统把一次内容创作拆成一条**多智能体流水线**，由总控 A0 编排，专业智能体协同，审核智能体把守门禁，最后由人工拍板：

| 编号 | 智能体 | 职责 | 产出物 |
| --- | --- | --- | --- |
| A0 | 总控 | 解析 Brief、拆任务卡、编排流水线 | 任务计划 `task_plan` |
| A1 | 策略洞察 | 受众画像、核心信息屋、传播目标 | 策略简报 `strategy_brief` |
| A2 | 创意方向 | Big Idea 与 2–3 个创意方向 | 创意概念 `creative_concept` |
| A3 | 内容策划 | 选题、标题备选、内容结构 | 内容策划案 `content_plan` |
| A4 | 文案创作 | 多版本文案 | 文案初稿 `copy_draft` |
| A5 | 编辑审校 | 结构 / 表达 / 品牌语气打分与修订 | 编辑定稿 `edited_copy` |
| A6 | 事实核查 | 主张核查、来源标注、风险分级 | 事实核查报告 `fact_check_report` |
| A7 | 品牌合规 | 广告法词库、行业规则、品牌一致性 | 合规审查报告 `compliance_report` |
| A8 | 视觉美术指导 | 视觉方向、配色、配图 Prompt、分镜 | 视觉指导 `visual_brief` |
| A9 | 渠道运营 | 多平台适配、SEO、发布排期 | 渠道适配稿 `channel_adaptation`、发布排期 `publish_plan` |
| A10 | 效果分析 | 发布前效果预估 / 发布后复盘与 A/B 结论 | 效果报告 `effect_report` |
| A11 | 知识沉淀 | 归档最佳实践、跨任务 RAG 召回（按租户隔离） | 知识卡片 `knowledge_card`、最终交付件 `final_delivery` |

**核心特性**

- **共享黑板**：所有产物、事实、评审、活动集中沉淀，支持 Intent 租约，避免并发写冲突。
- **阶段门禁环**：每个审核节点给出 `pass / revise / reject`；不通过则自动返工，超过上限升级为人工裁决。
- **人工审批（interrupt）**：关键节点挂起等待人工批准 / 打回 / 驳回，批准后自动生成发布排期并归档。
- **两级容错**：重试退避 → 降级到内置离线 Mock 引擎。**无 API Key 也能完整跑通**。
- **成本可控**：按任务记账，超预算自动熔断；重复请求命中响应缓存，省钱且零延迟。
- **发布闭环**：`审批 → 排期 → 登记发布 / 自动投递 → 回填真实效果 → A10 复盘 → A/B 结论`。
- **自动投递**：排期的建议时段会被解析成到期时间，到点后由 webhook 通道自动推送（可对接 n8n / 自建发布网关）。
- **知识复利**：A11 把可复用资产写进记忆库，后续任务自动召回复用；过期知识自动下线。
- **语义检索**：记忆库召回 = 关键词 + 向量混合打分，本地即可运行（可选接入 OpenAI 兼容 `/embeddings`）。
- **多租户**：配置访问令牌后，接口需鉴权且**任务与记忆库**都按租户隔离（单机不配置则零感知）。
- **质量评估**：与门禁解耦的 LLM-as-a-Judge 六维评分，离线规则版可复现、模型版失败自动回退。
- **调用轨迹**：span 树覆盖整个任务（编排 → 智能体 → 门禁/评估/模型调用），可看「哪一层最贵」；
  可选接入 OTLP 导出到 Jaeger。
- **容器化**：多阶段镜像 + docker compose（含 Jaeger）+ k8s 清单，一条命令起完整环境。

---

## 2. 环境准备与启动

### 2.1 依赖要求

- **Python 3.12**（推荐 conda 环境 `multi-agent-creator`）
- **Node.js ≥ 22.6**（仅前端构建/开发需要；生产模式后端会直接托管 `dist/`）

### 2.2 安装

```bash
# 1) 后端依赖（Windows 上建议加 --no-warn-script-location，并设置 PYTHONNOUSERSITE=1）
conda activate multi-agent-creator
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2) 前端依赖
npm install
```

### 2.3 启动

**方式 A：生产模式（推荐日常使用）**

```bash
npm run build          # 构建前端到 dist/
python -m app.main     # 启动服务，默认 http://127.0.0.1:8787
```

后端会自动托管 `dist/`，浏览器打开 `http://127.0.0.1:8787` 即可。

**方式 B：开发模式（前后端热更新）**

```bash
npm run dev            # 同时启动 python -m app.main（:8787）与 Vite（:5273）
```

Vite 开发服务器在 `http://127.0.0.1:5273`，并把 `/api` 反向代理到 `8787`。

> 默认使用内置离线 Mock 引擎，**无需任何密钥**。要接入真实模型，见 [5.2 环境变量](#52-环境变量) 或界面右上角「运行时设置」。

---

## 3. 界面导览

```
┌──────────────────────────────────────────────────────────────┐
│ 顶栏：服务状态 · 任务计数 · OTLP 状态 · 新建任务 · 运行时设置    │
├───────────────┬──────────────────────────────────────────────┤
│ 任务列表       │ 任务详情                                      │
│ （3s 轮询）    │  ├ 基本信息 + 质量评分卡                      │
│               │  ├ 人工审批横幅（待审批时出现）                 │
│               │  ├ 发布与效果回填面板（已排期后出现）            │
│               │  ├ 数字人渲染面板（产出视频脚本后出现，开发样例） │
│               │  └ 标签页：流水线看板 / 共享黑板产物 / 评估报告 / 调用轨迹 / 事件时间线 / 运行指标 │
└───────────────┴──────────────────────────────────────────────┘
```

- **任务列表**：实时刷新，展示品牌/产品、状态、当前阶段、返工轮次、产物数、质量分。
- **流水线看板**：每个智能体的运行状态、置信度、门禁结果与摘要。
- **共享黑板产物**：14 类结构化产物，按类型定制渲染；同一类型可**版本对比（diff）**。
- **评估报告**：LLM-as-a-Judge 六维评分、问题与改进建议、评估历史与全库聚合，并支持按需重评。
- **调用轨迹**：可折叠的 span 树 + 瀑布图 + 耗时排行，span 属性内联显示（见 [10. 调用轨迹](#10-调用轨迹与性能排障)）。
- **事件时间线**：SSE 实时推送，含事实与评审计数；事件带 `trace_id` / `span_id` 可回溯到 span。
- **运行指标**：系统聚合、成本/缓存/租约/评估/**追踪**、逐智能体运行指标。
- **发布与效果回填面板**：审批后出现，含「到期时间」列、投递 / 登记双按钮、投递状态 Chip 与「全部投递」。
- **数字人渲染面板**：任务产出视频脚本后出现（**开发样例**，见 [13.6](#136-数字人渲染开发样例)）——
  创建作业、进度条、渲染清单分镜表、成片地址；接哪家第三方服务由你决定。
- **运行时设置**：模型 / 编排与门禁 / 成本与缓存 / **向量检索** / **发布投递** / **质量评估** / **访问令牌** / 知识库 / 记忆库。
- **顶栏告警**：`checkpointer` 退化为内存实现时会显示「断点续跑不可用」——出现它说明进程重启后中断任务将无法续跑。

---

## 4. 完整实操流程

### 4.1 新建创作任务

点击右上角「新建创作任务」，填写 Brief：

| 字段 | 说明 |
| --- | --- |
| 品牌 / 产品 | 必填，用于语气与一致性校验 |
| 传播目标 | 曝光 / 互动 / 转化 / 教育 / 信任 |
| 目标受众 | 越具体越好，直接影响 A1 洞察质量 |
| 主渠道 | 小红书 / 抖音 / 公众号 / 知乎 / 电商详情页 / 官网 / PR；英文常用 Instagram / TikTok / LinkedIn |
| 行业 | 决定合规规则强度（食品饮料、美妆、医疗健康等） |
| **目标语言** | 中文 / English / 日本語 / 한국어 / Español（见 [12. 多语言创作](#12-多语言创作)） |
| 关键词 / 硬性约束 / 期望交付物 | 硬约束会贯穿全流程 |

勾选「自动审批」可跳过人工裁决（适合批量试跑）。

### 4.2 观察流水线

创建后 A0–A11 依次执行，界面每 2.5s 拉取快照，同时通过 SSE 接收实时事件。

- 门禁不通过时自动**返工**，最多 `maxRevisions` 轮；超限则升级为**人工裁决**（状态变为「待人工裁决」）。
- 成本或 token 超预算时**熔断**，后续步骤自动切离线引擎，任务继续跑完不受影响。

### 4.3 人工审批

当全部自动审核通过（或门禁升级）时，任务挂起并弹出审批横幅：

- **通过**：生成多平台发布排期 → 归档交付 → 触发 A11 知识沉淀。
- **退回返工**：带着意见回到上游重跑（消耗返工轮次）。
- **驳回**：任务终止为「已驳回」。

### 4.4 发布登记与效果回填

审批通过后，任务详情会出现「**发布与效果回填**」面板：

1. **登记发布**：逐渠道点「登记发布」，或点「全部登记发布」。系统不代点平台发布按钮（各平台接口差异大且需授权），只负责记录「哪个渠道、什么时候投出去」这条基线。可填发布链接。
2. **回填效果**：填写渠道、观察窗口、曝光量、点击量、互动量、转化量，点击「回填并复盘」。

回填后编排层会重跑 A10：上游一旦出现 `actuals`，A10 就从「发布前预估」切换到「**发布后复盘**」，产出新版本效果报告，给出实际 CTR、预估中位对比与 **A/B 结论（是否可放量）**。

> 同一任务可多次回填（例如 24h / 72h / 7 天），每次都会生成新的复盘版本，便于对比。

### 4.5 自动投递与待发布队列

除了人工「登记发布」，排期还支持**自动投递**。排期项会带一个「到期时间」（由建议时段换算，当天该时刻已过则顺延到次日）：

| 投递状态 | 含义 |
| --- | --- |
| `pending` | 已排期，尚未到点 |
| `dispatched` | 已成功推送给 webhook |
| `skipped` | 未配置 webhook，等价比「登记发布」（离线默认行为） |
| `failed` | 投递失败，`last_error` 记录原因，`attempts` 记录重试次数 |

使用方式（三选一）：

1. **界面**：发布面板中逐条点「投递」，或点「全部投递」（对应 `POST /api/tasks/{id}/publish/dispatch`）。
2. **外部定时器**：定时调用 `POST /api/publish/tick`，一次投递所有已到期条目。
3. **进程内后台巡检**：设置 `PUBLISH_AUTO_DISPATCH=true`（配合 `PUBLISH_TICK_SECONDS`，默认 60s），服务启动后自动巡检。

投递目标由 `PUBLISH_WEBHOOK_URL` 决定：系统只负责把「该投什么」POST 出去，
对接各平台的授权与限流交给你的发布网关（n8n / Zapier / 自建服务）。
用 `GET /api/publish/queue?dueOnly=true` 可以查看跨任务的待发布队列（按到期时间升序）。

### 4.6 查看知识沉淀

任务归档后 A11 会把可复用资产写入记忆库。在「运行时设置 → 记忆库」中：

- 查看卡片列表、类型分布、新鲜度统计、**当前租户与全局计数**；
- 用自然语言检索，看到的结果与智能体实际复用**同一套打分逻辑**；
- 新任务创建时，A1/A2/A4 会自动召回历史资产（可在事件时间线看到「复用来源」）。

> 记忆库按租户隔离：不同 API Token 对应不同租户，卡片互不可见。

### 4.7 查看质量评估

审批通过、任务归档后，任务详情会多出「**评估报告**」标签页（见 [8. 质量评估](#8-质量评估llm-as-a-judge)）：

1. 顶部是最近一次评估的综合分与裁决，下面是六个维度的得分条与判分理由；
2. 「问题」「改进建议」直接对应下一轮可执行的动作；
3. 想立刻重评（例如刚改过 Prompt 或人工改稿），点「规则评估」或「模型评估」即可，
   不必重跑整条流水线；
4. 下方的评估历史可看出「返工前后」「交付前后」分数怎么变化。

---

## 5. 运行时配置

### 5.1 界面配置（即时生效，无需重启）

点击顶栏「运行时设置」：

| 分组 | 配置项 |
| --- | --- |
| 模型 | 提供方（mock / openai）、模型名、Base URL、API Key、Temperature、Max Tokens、超时 |
| 编排与门禁 | Turn Budget、最大返工轮次、质量分阈值、自动审批 |
| 成本与缓存 | 单任务成本上限（USD）、单任务 token 上限、是否启用 LLM 响应缓存 |
| 向量检索 | 提供方（local / openai）、模型、语义权重（0–1）、API Key |
| 发布投递 | webhook 地址、失败重试次数、是否自动投递 |
| 质量评估 | 介入方式（off / advisory / blocking）、评估器（offline / llm）、通过线、计入质量分的权重 |
| 访问令牌 | 本地保存 API Token（用于接口鉴权，随请求带 `X-API-Token`） |
| 知识库 / 记忆库 | 只读浏览与检索 |

> 模型提供方选 `openai` 时，兼容一切 OpenAI 协议网关（DeepSeek / 通义 / 豆包 / vLLM / Ollama 均可）。API Key 回显为掩码，明文不落盘。

### 5.2 环境变量

可在根目录 `.env` 或进程环境变量中设置（进程环境变量优先）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `PORT` | `8787` | 服务端口 |
| `LLM_PROVIDER` | `mock` | `mock` / `openai` |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI 兼容网关地址 |
| `OPENAI_API_KEY` | 空 | 密钥 |
| `OPENAI_MODEL` | `gpt-4o-mini` | 模型名 |
| `LLM_TEMPERATURE` | `0.7` | 采样温度 |
| `LLM_MAX_TOKENS` | `2048` | 单次最大输出 token |
| `LLM_TIMEOUT_MS` | `60000` | 单次请求超时（毫秒） |
| `TURN_BUDGET` | `25` | 单任务最大编排步数 |
| `MAX_REVISIONS` | `2` | 最大返工轮次 |
| `QUALITY_THRESHOLD` | `75` | 质量分门禁阈值 |
| `AUTO_APPROVE` | `false` | 新任务是否默认自动审批 |
| `COST_BUDGET_USD` | `0.5` | 单任务成本上限（美元） |
| `TOKEN_BUDGET` | `50000` | 单任务 token 上限 |
| `LLM_CACHE` | `true` | 是否启用响应缓存 |
| `EMBEDDING_PROVIDER` | `local` | 向量提供方：`local`（确定性 hashing）/ `openai` |
| `EMBEDDING_BASE_URL` | 空 | 留空复用 `OPENAI_BASE_URL` |
| `EMBEDDING_API_KEY` | 空 | 留空复用 `OPENAI_API_KEY` |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | 向量模型名 |
| `EMBEDDING_DIM` | `256` | 本地向量维度（hashing 桶数，最小 16） |
| `EMBEDDING_WEIGHT` | `0.35` | 混合检索中语义分量权重：`0` = 纯关键词 |
| `PUBLISH_WEBHOOK_URL` | 空 | 发布投递目标；为空时投递退化为「登记发布」 |
| `PUBLISH_RETRY` | `2` | 投递失败重试次数（指数退避） |
| `PUBLISH_AUTO_DISPATCH` | `false` | 是否由后台定时器自动投递到期排期 |
| `PUBLISH_TICK_SECONDS` | `60` | 后台自动投递的检查间隔（秒，最小 5） |
| `DIGITAL_HUMAN_PROVIDER` | `sample` | 数字人样例通道：`sample`（内置样例引擎，离线模拟）/ `http`（对接自建渲染网关的适配样例） |
| `DIGITAL_HUMAN_API_URL` / `_API_KEY` | 空 | http 样例的网关地址与 Bearer 鉴权；URL 留空时 http 通道**显式失败**，绝不假装成功 |
| `DIGITAL_HUMAN_AVATAR` / `_TIMEOUT_MS` | 空 / `10000` | 默认形象 ID（创建作业时可被请求体覆盖）/ 单次 HTTP 超时（毫秒） |
| `OTEL_TRACES_SAMPLER` / `_ARG` | `parentbased_always_on` / `1.0` | 导出面采样器与比例（OTel 语义；**进程内轨迹始终完整**，详见 [10.4](#104-与-opentelemetry-的关系请如实理解)） |
| `JUDGE_MODE` | `advisory` | 评估介入方式：`off` / `advisory`（只打分）/ `blocking`（低分参与返工） |
| `JUDGE_PROVIDER` | `offline` | 评估器：`offline`（规则，可复现）/ `llm`（走网关，失败自动回退） |
| `JUDGE_MODEL` | 空 | `llm` 评估器的模型名；留空用当前 LLM 配置的模型 |
| `JUDGE_PASS_THRESHOLD` | `75` | 评估通过线（0–100） |
| `JUDGE_WEIGHT` | `0.2` | 评估分在综合质量分中的权重（0 = 完全不影响，可回滚） |
| `CREATOR_API_TOKENS` | 空 | API 访问令牌；为空不鉴权。支持 `tok1,tok2` 或 `acme:tok1,beta:tok2` |
| `CREATOR_DATA_DIR` | `<项目根>/data` | 持久化目录（测试/多实例隔离用） |
| `CREATOR_VERIFY_PROVIDER` | 空 | 置 `openai` 可让验证脚本走真实网关（**默认强制 mock**，见下） |

> **验证脚本默认强制离线**：`doctor.py` / `golden_eval.py` / `smoke_api.py` /
> `verify_contracts.py` 断言的是契约与逻辑，必须秒级、可复现、不花钱。
> 即使 `.env` 指向真实网关，它们也会强制 `mock`；
> 需要量真实链路时用 `python scripts/real_check.py`，或设 `CREATOR_VERIFY_PROVIDER=openai`。

### 5.3 访问令牌与租户隔离

默认**不鉴权**（单机零配置）。设置 `CREATOR_API_TOKENS` 后，除 `GET /api/health` 外的所有 `/api/*` 都需要携带令牌：

```bash
# 单租户：所有令牌都归 default 租户
CREATOR_API_TOKENS=tok_abc123

# 多租户：令牌 → 租户名（acme 的用户看不到 beta 的任务）
CREATOR_API_TOKENS=acme:tok_aaa,beta:tok_bbb
```

携带方式（三选一，优先级从上到下）：

```bash
curl -H "Authorization: Bearer tok_abc123" http://127.0.0.1:8787/api/tasks
curl -H "X-API-Token: tok_abc123"        http://127.0.0.1:8787/api/tasks
curl "http://127.0.0.1:8787/api/tasks?token=tok_abc123"   # SSE / EventSource 无法自定义请求头时用
```

- 令牌无效或缺失 → `401 {"detail": "无效或缺失的 API Token"}`。
- 任务列表、详情、裁决、发布、回填、黑板、事件流、指标均按租户过滤；跨租户访问返回 `404`（不泄露任务是否存在）。
- **记忆库同样按租户隔离**：知识卡片的写入、召回、列表与容量计量都在租户内进行——
  不同令牌对应不同租户，A 品牌的调性基线不会被 B 品牌的智能体复用。
- 前端：在「运行时设置 → 访问令牌」填入令牌即可，客户端会自动为所有请求带上 `X-API-Token`，SSE 会自动追加 `&token=`。

---

## 6. 成本熔断与响应缓存

### 6.1 成本账本

每个任务一份独立账本，记录 prompt / completion token、累计成本、调用次数与缓存命中次数。

- 累计成本 ≥ `COST_BUDGET_USD`，或累计 token ≥ `TOKEN_BUDGET` 时，触发**熔断**：后续步骤强制走离线引擎，任务**不中断**（与降级策略同一套语义）。
- 成本达到预算 80% 时会提前预警。

### 6.2 响应缓存

只缓存真实模型调用，键为 `(provider, model, temperature, max_tokens, messages)` 的指纹：

- 同一任务断点续跑、人工打回重试时**不重复付费、零延迟**；
- 默认容量 256 条、TTL 1 小时，LRU 淘汰；
- 离线引擎不产生费用，因此不入缓存。

在「运行指标」页可直接看到：累计成本、平均单篇成本、单任务预算、熔断任务数、缓存命中率与命中/未命中次数。

---

## 7. 记忆库与知识复用

A11 把每次任务的最佳实践沉淀为跨任务知识卡片，供后续任务 RAG 召回。

- **卡片类型**：品牌、案例、模板、经验。
- **检索打分（关键词 + 向量混合）**：
  `score = 关键词分 × (1 - 语义权重) + 向量余弦 × 语义权重`，再叠加同品牌（0.25）/ 同渠道（0.1）/ 同行业（0.05）加成；
  低于 `MIN_SCORE = 0.2` 不返回，避免「什么都召回一点」的虚假命中。
- **向量分量**：默认 `provider=local`，用确定性 hashing embedding（零依赖、离线可用、同一文本永远同一向量）；
  设为 `openai` 则调用任意 OpenAI 兼容 `/embeddings`，**远端失败会自动回退本地向量**，检索不会因此中断。
- **语义权重**：`EMBEDDING_WEIGHT`（默认 `0.35`）。调为 `0` 即退化为纯关键词检索。
- **召回范围**：A1/A2/A4 在执行前会召回历史资产并注入上下文；召回结果会排除当前任务（避免自我复用）。
- **租户隔离**：卡片带 `tenant` 归属，写入 / 召回 / 列表 / 统计 / 容量**全部按租户分区**；
  不同令牌对应不同租户，知识互不可见。**未启用鉴权时全部归 `default`**，行为与历史版本一致。
- **新鲜度**：`FRESH_DAYS = 30` 天视为新鲜，`STALE_DAYS = 120` 天标记陈旧；**同分时优先新鲜知识**。
- **过期下线**：超过 `MAX_AGE_DAYS = 180` 天的卡片在读写时自动清理，避免旧玩法污染新任务。
- **容量**：上限 500 条，超出时按最旧淘汰。**该上限按每租户计量**——
  多租户下先入库的租户不会挤掉后来租户的名额。

> 界面「运行时设置 → 记忆库」可直接看到当前检索方式（提供方 / 模型 / 语义权重 / 已建索引条数）、
> 当前租户与全局卡片计数，命中项除总分外还会显示「语义 {vector_score}」，
> 方便判断是关键词命中还是语义命中。

---

## 8. 质量评估（LLM-as-a-Judge）

门禁回答的是「这篇稿子**能不能发布**」（否决式），评估回答的是「这篇稿子**有多好、差在哪**」（度量式）。
两者刻意解耦：把质量度量塞进门禁，「低于阈值就返工」会被一个有噪声的评分放大成流程抖动。

### 8.1 六维评分

每次评估输出 0–100 的综合分与六个维度的细项（各 0–5 分，带判分理由与证据）：

| 维度 | 权重 | 看什么 |
| --- | --- | --- |
| 需求契合 | 0.25 | 是否命中 Brief 的关键词、受众、渠道标题上限与硬性约束 |
| 合规安全 | 0.20 | 广告法词库 + 行业规则（**与 A7 用同一份词库**，口径不会互相打架） |
| 结构完整 | 0.15 | 标题 / 正文 / CTA / 话题标签是否齐备且长度合规 |
| 品牌语气 | 0.15 | 品牌名与调性关键词是否体现 |
| 事实稳妥 | 0.15 | 数字与主张是否有来源、是否存在夸大（优先采信 A6 的结论） |
| 吸引力 | 0.10 | 标题钩子、行动号召与互动引导 |

裁决：综合分 ≥ 通过线为 `pass`，低于通过线的 80% 为 `reject`，其余 `review`。

### 8.2 两种评估器

| 评估器 | 特点 | 何时用 |
| --- | --- | --- |
| `offline`（默认） | 确定性规则评估，零依赖、可离线，**同一输入永远同一分数** | CI 回归、Prompt 改动的对照尺 |
| `llm` | 走当前 OpenAI 兼容网关做真正的 LLM-as-a-Judge | 需要语义级判断时 |

> `llm` 评估器**任何异常都会静默回退** `offline`，并在报告里标注「已回退规则评估器」与原因——
> 评估是旁路能力，不会因为网关抖动就让创作链路出问题。

### 8.3 介入方式与生效时机

| `JUDGE_MODE` | 行为 |
| --- | --- |
| `off` | 不评估 |
| `advisory`（默认） | 只产出分数与建议，**完全不改变门禁结论** |
| `blocking` | `reject` 升级人工裁决、`review` 计入返工理由 |

- **生效时机**：门禁时刻评估一次（可参与 `blocking` 裁决），交付前再评估一次（用于跨任务回归对比）。
- **对综合质量分的影响**：由 `JUDGE_WEIGHT` 决定（默认 `0.2`）。设为 `0` 即评估分完全不影响质量分。
- **评分口径版本**：报告里带 `RUBRIC_VERSION`。**跨版本对比分数前必须先对齐它**，
  否则「分数变好了」可能只是尺子变了。

### 8.4 在界面上使用

任务详情 →「**评估报告**」标签页：

- 顶部显示最近一次评估的总分、裁决、评估器、置信度与评分口径版本；
- 六条维度得分条（含判分理由与证据），并列出**问题**与**改进建议**；
- 「**规则评估**」「**模型评估**」两个按钮可随时重评（改完 Prompt 或人工改稿后无需重跑整条流水线）；
- 下方是本任务的评估历史与全库聚合（均分 / 通过率 / 各维度均分）。

任务头部还会显示一个「评估 xx.x」Chip，方便不看面板也能扫到分数。

> 维度均分是 Prompt 调优的抓手：优先补**最弱的那一个维度**，比「整体重写提示词」更容易看到变化。

---

## 9. 回归测试与质量门禁

「我把 Prompt 改好了」这句话，在没有固定输入的情况下是**无法验证**的：内容创作本身发散，
换一个 Brief 或换一次采样分数就会漂移，分不清是改进还是噪声。
黄金数据集把**输入固定**下来、把**输出压缩成标量**，于是这个问题变得可判定。

### 9.1 数据集长什么样

`golden/briefs/*.json` 是 **11 条固定用例**，覆盖**全部 7 个渠道**与 9 个行业：

| 用例 | 渠道 | 行业 | 为什么选它 |
| --- | --- | --- | --- |
| `xiaohongshu_food` | 小红书 | 食品饮料 | MVP 默认路径，验证主链路 |
| `xiaohongshu_beauty` | 小红书 | 美妆 | 化妆品「不得暗示医疗作用」规则组 |
| `xiaohongshu_minimal_brief` | 小红书 | 消费品 | **边界用例**：无关键词/约束/交付物 |
| `douyin_short_video` | 抖音 | 食品饮料 | 分镜脚本 + 9:16 画幅 + 标题压缩 |
| `wechat_longform_tech` | 公众号 | 科技 | 长图文 + B2B 专业口径 |
| `zhihu_b2c_education` | 知乎 | 教育 | 「不得承诺提分/通过」规则组 |
| `ecommerce_home_appliance` | 电商详情页 | 家电 | 卖点结构化 + 参数可核对 |
| `ecommerce_medical_strict` | 电商详情页 | 医疗健康 | **强监管**：门禁必须拦得住 |
| `pr_release_finance` | PR稿 | 金融 | **强监管**：不得承诺收益 |
| `website_b2b_industrial` | 官网 | 工业制造 | 20 字标题上限 + 长决策链受众 |

`golden/baseline.json` 是基线，逐用例记录质量分、评估分（门禁时刻 + 交付前）、返工轮次、
产物数、事实准确率、品牌一致性、合规裁决序列，以及生成它时的**评分口径**与**引擎**。

> 数据集放在 `golden/` 而不是 `data/`：它是**随代码版本化的测试资产**，必须能被 review、
> 被 diff、被追责 —— 否则「悄悄改宽基线让 CI 变绿」就是一条无人察觉的作弊路径。

### 9.2 怎么判定「变差了」

判定分两层，**互补且缺一不可**：分数层面看基线，内容层面看硬约束。

| 情况 | 判定 |
| --- | --- |
| 运行失败 / 全量运行时有用例缺失 | 不通过 |
| 质量分或评估分回退超过容差（默认 ±3） | **回退** |
| 分数没动但**返工轮次 +1** | **回退**（更慢更贵） |
| 监管用例**首轮合规不再被拦截** | **回退**（门禁强度下降） |
| **内容级期望未通过**（见下） | **回退** |
| 容差内的波动 | 持平（不把噪声当回归） |
| 部分运行（`-n` / `--case`） | 只对范围内用例判定，并标注「部分运行」 |

**内容级期望**是分数之外的硬约束 —— 一篇 95 分但漏了品牌名的稿子依然不可交付：

| 断言 | 依据 |
| --- | --- |
| 品牌名出现在最终文本 | 任何渠道都要求 |
| Brief 关键词覆盖率 ≥ N | 关键词是选题方向，**不要求全部命中**（否则会逼出「硬塞关键词」） |
| 用例特有的禁用表述 | 逐条写在用例的 `expect.must_not_contain` |
| **无广告法阻断级用语** | 由合规词库现算，**词库更新后自动生效**，无需逐条维护 |
| 标题 ≤ 渠道字数上限 | 渠道规则现算，用例可用 `max_title_length` 覆盖 |
| 质量分 / 评估分 ≥ 门槛 | 逐用例设定 |
| 返工轮次 ≤ 上限 | 成本约束 |
| 首轮门禁是否应被拦截 | 强监管用例为 `true`（验证门禁真的拦得住） |

两条值得展开：

- **返工轮次增加即使总分没掉也算劣化**；而强监管用例首轮被拦截本是期望行为 ——
  如果某次改动让首轮直接放行，分数可能不降反升（少了一轮返工），
  看起来是改进，实际是**门禁失效**。
- **否定语境不算违规**：「不得涉及疾病预防与治疗功能」是免责声明，
  不会被判成违规宣称。判定按**小句**进行 ——「不得用于治疗，但可治疗失眠」的
  后半句是肯定宣称，照样会被判出来。
- **调性不做机器断言**：`克制`、`用数据说话` 这类抽象描述不是要逐字写进正文的关键词，
  强制命中只会逼出「堆语气词」，反而与「克制」背道而驰。调性由用例里的
  `reference_points` 与人工抽查覆盖。

### 9.3 在界面上用

「运行时设置 → **回归测试**」：

- **跑全量回归**：依次执行 11 条用例（约一分钟，服务端后台任务，带进度条）；
- **快速跑 3 条**：改动中间态自查；
- **基线对比表**：逐用例显示结论（持平/提升/回退/新增/缺失/失败）、质量分与增量、
  评估分、返工轮次（被拦截的用例带「拦」标记）、产物数与原因；
- **用例清单与覆盖矩阵**：确认新增的渠道/行业分支有没有被覆盖。

### 9.4 在命令行用

```bash
python scripts/golden_eval.py                     # 跑全量并与基线对比（退出码即结论）
python scripts/golden_eval.py -n 3                # 只跑前 3 条
python scripts/golden_eval.py --case douyin_short_video
python scripts/golden_eval.py --coverage          # 只看覆盖矩阵
python scripts/golden_eval.py --tolerance 5       # 放宽回归容差
python scripts/golden_eval.py --update-baseline   # 把本次结果写为新基线
```

**更新基线的纪律**：更新等于「把当前分数认定为新的合格线」，因此存在回归/失败/缺失时
`--update-baseline` **会拒绝执行**，必须显式加 `--force`。请先逐条确认回退都能解释，
再更新。

---

## 10. 调用轨迹与性能排障

事件时间线告诉你「**发生了什么**」，调用轨迹告诉你「**时间和钱花在哪一层**」。
比如「这次任务为什么慢」——翻事件是拼不出结论的，但看 span 耗时排行一眼就清楚。

### 10.1 界面上看

任务详情 →「**调用轨迹**」标签页：

- **span 树**：可逐级折叠，每个 span 后面内联显示关键属性
  （模型名、token 数、是否命中缓存、门禁裁决、置信度、降级原因……）；
- **瀑布图**：横条表示该 span 的时间位置与长度，一眼看出串行/嵌套关系；
- **耗时排行**：按 span 名聚合的次数 / 总耗时 / 平均 / 峰值 / 占比，
  顶部还会给出「模型调用耗时占比」这个最直观的数字。

「运行指标」页也有全局的 span 耗时排行（跨任务）。

### 10.2 span 树长什么样

```
graph.invoke#0                    ← 每次 LangGraph 调用包一层
├─ A1.strategy_brief              ← 一个智能体一次执行 = 一个 span
│   └─ llm.A1.strategy            ← 模型调用（client span）
├─ memory.retrieve                ← RAG 召回（带命中条数）
├─ gate.review                    ← 门禁整体
│   ├─ A5.edited_copy
│   ├─ A6.fact_check_report
│   ├─ A7.compliance_report
│   └─ judge.evaluate
└─ …
human.wait                        ← 人工等待（独立，不计入执行链耗时）
graph.invoke#16                   ← 人工裁决后恢复执行
```

> **人工等待单独成段**是刻意的：否则排障时会把「人在犹豫三小时」误读成「系统卡了三小时」。

### 10.3 接到 Jaeger / OTLP collector（可选）

默认**不需要**任何外部服务；想接真实追踪后端时配置一个环境变量即可：

```bash
# 方式 A：一键起「应用 + Jaeger」
docker compose up -d --build
#   应用   http://127.0.0.1:8787
#   Jaeger http://127.0.0.1:16686  （Service 选 creator-agent-studio）

# 方式 B：已有 collector，只告诉应用往哪发
OTLP_ENDPOINT=http://localhost:4318 python -m app.main
```

| 项 | 行为 |
| --- | --- |
| `OTLP_ENDPOINT` 为空 | **不加载 OTel SDK**，只做进程内追踪（span 树 + `data/traces/*.json`） |
| 配置了 | 把**同一份 span** 转发给 collector —— trace_id / span_id 与本地 JSON 完全一致，可按 id 直接对查 |
| `OTLP_HEADERS` | 形如 `key1=value1,key2=value2`，用于带鉴权的托管 collector |
| 端点写法 | 写 `http://host:4318` 即可，`/v1/traces` 会自动补全 |

顶栏在已接入时显示「OTLP 已接入」；`GET /api/health` 的 `otlp` 字段可看到导出数量与失败数。

> 转发失败**不会影响任务**（观测能力不是业务链路的一环），失败次数在 `health.otlp.failed` 里可见。

### 10.4 与 OpenTelemetry 的关系（请如实理解）

| 项 | 现状 |
| --- | --- |
| trace_id / span_id | ✅ W3C 格式（32 / 16 位十六进制），与 OTel 语义一致 |
| span 结构 | ✅ kind / parent / attributes / status / 嵌套，与 OTel span 同构 |
| 本地导出 | ✅ `data/traces/<task_id>.json`，含 `otel[]` 段 |
| **OTLP 导出到 Jaeger** | ✅ 配置 `OTLP_ENDPOINT` 后启用，**id 与本地一致** |
| **采样（只作用于导出面）** | ✅ `OTEL_TRACES_SAMPLER` / `_ARG`，与 OTel 采样语义对齐（`parentbased_always_on` 默认 / `parentbased_traceidratio` / `always_off`…）；未采样的 trace 不转发、不落盘，但**进程内轨迹始终完整**——Jaeger 里查不到它是预期行为；入站 `traceparent` 的采样标记优先于本地比例，采样计数在 `/api/metrics` 与 `/api/health` 可见 |
| **跨进程上下文传播** | ✅ W3C `traceparent`：入站 `POST /api/tasks` 解析该头，本地 trace **沿用远端 trace_id**、首个根 span 挂到远端 span 之下（Jaeger 里拼成完整一棵树）；出站 webhook（发布投递 / 数字人网关）自动携带当前 span 的 `traceparent`；坏头一律忽略，绝不影响业务请求 |

一个实现细节值得说明：本项目的 span 是**自采集**的，转发时**手工构造 OTel 的
`ReadableSpan`**，而不是用 OTel 的 API 重新创建 —— 否则 SDK 会生成新的 span_id，
导致「按本地日志里的 trace_id 去 Jaeger 查这一条」这个最基本的排障动作失效。

### 10.5 命令行看

```bash
# 单个任务的 span 树（含 summary.by_name 耗时聚合）
curl -s "http://127.0.0.1:8787/api/tasks/<task_id>/trace" | python -m json.tool

# 落盘的 OTel 形状 trace
cat data/traces/<task_id>.json

# OTLP 导出状态
curl -s http://127.0.0.1:8787/api/health | python -c "import json,sys; print(json.load(sys.stdin)['otlp'])"
```

**降级与缓存命中不会计为错误**（它们是设计出来的正常路径），
因此 span 错误率是一个可信的信号：非 0 就真的有问题。

---

## 11. 容器化部署

### 11.1 一键起（含 Jaeger）

```bash
docker compose up -d --build
#   应用   http://127.0.0.1:8787
#   Jaeger http://127.0.0.1:16686

# Docker Hub 不可达时（受限网络 / 内网）：改用离线变体底座
#   DOCKERFILE=Dockerfile.offline docker compose up -d --build

# 只要应用、不接 Jaeger
docker compose up -d --build app

# 看日志 / 停止
docker compose logs -f app
docker compose down          # 加 -v 会连数据卷一起删
```

数据落在命名卷 `creator-data`（容器内 `/data`）。**不挂卷则容器重建即丢任务与记忆库。**

> **受限网络说明**：标准 `Dockerfile` 需从 Docker Hub 拉基础镜像。
> 若 `registry-1.docker.io` 不可达，改用 `Dockerfile.offline` —— 它只用本地已有的
> 基础镜像，并从镜像源安装 Python 依赖（通常只有 Docker Hub 被限制，PyPI 是通的）。
> 该变体底座更大，属**可行方案而非更优方案**，网络恢复后请用标准 Dockerfile。
>
> **实测记录**：容器 `healthy`、`/` 返回前端、跑通完整任务（18 产物 / 质量分 94）、
> `/data` 卷在容器删除重建后任务与记忆库全部保留。

### 11.2 单容器

```bash
docker build -t creator-agent-studio:latest .
docker run -d --name creator -p 8787:8787 \
  -v creator-data:/data \
  -e AUTO_APPROVE=true \
  -e CREATOR_API_TOKENS=acme:tok_aaa,beta:tok_bbb \
  creator-agent-studio:latest
```

### 11.3 Kubernetes

```bash
kubectl apply -f deploy/k8s.yaml
kubectl -n creator port-forward svc/creator 8787:8787
```

清单包含 Namespace / ConfigMap / Secret / PVC / Deployment / Service / Ingress。
两点需要知道：

- **副本数固定为 1**（`Recreate` 策略）。默认 file 模式的持久化是本地 JSON + SQLite
  检查点，多副本会各写各的导致状态分裂。要横向扩展先把应用切到
  `CREATOR_STORAGE=pg`（PostgreSQL + Redis，切换步骤见
  [`storage_contract.md`](storage_contract.md) §6），再由部署方调整副本数与托管库连接串。
- **探针用 `/api/health`**，它免鉴权 —— 因此即使开启 `CREATOR_API_TOKENS` 也能正常探活。

生产环境请务必设置 `CREATOR_API_TOKENS`（决定租户隔离边界），
并在 Secret 里放模型密钥。

### 11.4 常用环境变量

完整清单见 `.env.example`。容器部署最常改的几个：

| 变量 | 说明 |
| --- | --- |
| `CREATOR_DATA_DIR` | 数据目录（容器内固定 `/data`，对应挂载卷） |
| `CREATOR_STORAGE` | 存储后端：`file`（默认，JSON + SQLite，零依赖）/ `pg`（PostgreSQL + Redis，多副本前提） |
| `CREATOR_DATABASE_URL` / `CREATOR_REDIS_URL` | 仅 pg 模式生效；compose 网络内默认指向 `postgres` / `redis` 服务 |
| `LLM_PROVIDER` / `OPENAI_*` | `mock`（默认，无需密钥）或任意 OpenAI 兼容网关 |
| `CREATOR_API_TOKENS` | 访问令牌（为空则不鉴权；**生产必设**） |
| `OTLP_ENDPOINT` | 追踪导出目标（如 `http://jaeger:4318`；为空则只做进程内追踪） |
| `AUTO_APPROVE` | 是否跳过人工审批（批量试跑用） |
| `COST_BUDGET_USD` / `TOKEN_BUDGET` | 单任务成本与 token 熔断线 |

### 11.5 部署前的清单自检

```bash
python scripts/check_deploy.py
```

它**不需要 Docker daemon**，静态核对：Dockerfile 的 `COPY` 路径是否存在且未被
`.dockerignore` 排除、Dockerfile/compose/k8s 声明的环境变量是否真的被后端读取、
探针路径是否等于免鉴权路径。这类错误本来只有 `docker build` 或部署时才暴露，
现在能提前拦住。

---

## 12. 状态机与门禁规则

**阶段顺序**

```
INIT → STRATEGY → CREATIVE → PLANNING → DRAFTING → REVIEW → EDITING
→ FACT_CHECK → COMPLIANCE → VISUAL_ADAPT → CHANNEL_ADAPT → APPROVAL
→ PUBLISHED → ANALYZED → MEMORY → ARCHIVED
```

异常分支：`REVISION`（返工中）、`REJECTED`（已驳回）、`FAILED`（执行失败）、`PAUSED`（等待人工）。

**门禁裁决**

| 裁决 | 含义 | 后续动作 |
| --- | --- | --- |
| `pass` | 通过 | 进入下一阶段 |
| `revise` | 需返工 | 携带修改要求回到上游，消耗返工轮次 |
| `reject` | 否决 | 触发人工裁决 |
| 升级 | 自动门禁多次未收敛 | 挂起等待人工裁定 |

**四级门禁**：A5 编辑审校 → A6 事实核查 → A7 品牌合规 → 人工审批。

**降级与容错**：调用失败 → 重试退避 → 降级离线引擎；成本熔断 → 强制离线引擎；任务中断（进程重启）→ 启动时对未完成任务断点续跑。

---

## 13. 多语言创作

系统支持 **中文 / English / 日本語 / 한국어 / Español**（`plan.md` v2.0「多语言本地化」）。

### 13.1 怎么用

在 Brief 里填「目标语言」即可（也可写 `en-US`、`English`、`英文` 这类别名，系统会自动归一化）。
不填默认中文，行为与以往完全一致。

| 语言 | 常用渠道 | 标题口径 | 商业推广披露 |
| --- | --- | --- | --- |
| 简体中文 | 小红书 / 抖音 / 公众号 / 知乎 / 电商 / 官网 / PR稿 | 按**字**（渠道上限 18–30 字） | 需标注「合作」 |
| English | Instagram / TikTok / LinkedIn / Email / Landing Page / Press Release | 按**词**（12 词） | `#ad` |
| 日本語 | X / Instagram / LINE / プレスリリース / 商品ページ | 按**字**（24 字） | `#PR` |
| 한국어 | 네이버 블로그 / 인스타그램 / 유튜브 / 카카오톡 / 보도자료 | 按**字**（25 字） | `#광고` |
| Español | Instagram / TikTok / LinkedIn / Email / Página de producto | 按**词**（12 词） | `#publicidad` |

### 13.2 它是「原生创作」，不是翻译

选定非中文语言后，创作智能体会收到一份**本地化指令**，明确要求：

- 用目标语言**原生创作**，不要先写中文再翻译（翻译腔的营销文案在本地市场基本不可用）；
- 遵守当地表达惯例（如英文「先讲利益再讲功能」、日文「敬体为基本」）；
- 按目标语言的**字数口径**控制标题（英文按词，中日韩按字）；
- 遵守当地合规红线与度量/日期格式。

> **标题口径用错会让「合规」失去意义**：一个 12 词的英文标题按字符算有 60+ 字，
> 用中文字数上限去判断会得出完全错误的结论。系统在交付、评估、回归断言三处都按语言口径处理。

### 13.3 合规上的一条重要限制

**广告法词库只覆盖中文。** 选择非中文语言时，A7 会：

- 在合规报告里明确声明「该语言尚无自动合规词库，需人工复核」；
- 把当地红线（如美国 FTC 的披露要求）写入 `risks`；
- **强制标记需要人工复核**，而不是静默判为通过。

这是刻意的：**「如实告知未覆盖」与「假装检查过了」的区别，就是产品能否被信任的区别。**
非中文市场的合规表述必须由人工（目标市场法务）把关。

界面「运行时设置 → 知识库」可以看到每种语言的合规覆盖情况。

### 13.4 记忆库按语言隔离

英文资产不会被中文任务当作「品牌调性基线」复用，反之亦然 ——
复用一份**语言不对**的资产比不复用更糟（会被模型当成本次任务的语气参照）。

### 13.5 视频脚本

短视频形态的任务会产出**独立的「视频脚本」产物**（在「共享黑板产物」里按类型筛选即可看到）：

| 内容 | 说明 |
| --- | --- |
| 钩子 | 黄金 3 秒，制造冲突或悬念 |
| 分镜表 | 每镜含**时长 / 画面 / 口播 / 字幕 / 机位 / 意图** |
| 口播表 | 按镜头对齐的逐句口播 |
| 字幕表 | 含起止时间 |
| 行动号召 / 拍摄要点 / 合规注意 | 可直接交给拍摄与运营 |

形态参数按渠道给（抖音 45s/9:16/5 镜、视频号 60s、B 站 120s/16:9、TikTok 30s…），
时间轴由知识层骨架生成，**保证单调且总时长与目标一致**。

**只在需要时产出**：短视频渠道、交付物点名要脚本、或渠道形态含视频特征 —— 任一命中。
图文渠道**不会**被塞入无关脚本（黄金数据集对这一点做了双向断言）。

界面里以**时间轴**呈现：比例条 + 分镜表 + 时间轴覆盖率，并对「存在无口播分镜」直接告警。

### 13.6 数字人渲染（开发样例）

> **定位**：数字人渲染**不在本系统内实现** —— HeyGen / D-ID / 腾讯智影等服务的授权、
> 形象库、计费与回调协议差异极大，**接哪家、要不要接属产品形态决策，由你决定**。
> 系统提供的是一份可回归的接入样例（`app/core/digital_human.py`），让联调在买任何服务之前就能发生。

- **前置条件**：任务已产出 `video_script` 产物（短视频形态会自动产出）；没有脚本时创建接口返回 409。
- **两条样例通道**（创建作业时可用 `provider` 覆盖默认值）：
  - `sample`（默认，零依赖）：离线确定性模拟「排队 → 渲染 → 完成」，并按脚本生成**渲染清单**
    （每镜台词 / 字幕 / 机位 / 起止时间 / 是否有口播）；
  - `http`：配置 `DIGITAL_HUMAN_API_URL` 后对接「POST 建任务 → GET 查状态」最小契约的自建网关；
    未配置时**显式失败，绝不假装成功**。
- **界面**：任务详情出现「数字人渲染」面板 —— 创建作业（形象 ID / 通道）、进度条、
  渲染清单分镜表、成片地址（样例引擎为模拟地址）。
- **生命周期**：状态在读取时惰性推进（不靠后台线程）；任务删除时渲染作业一并回收；按租户隔离。

### 13.7 已知边界

- **离线 Mock 引擎**：交付物（标题 / 正文 / CTA / 标签 / 最终交付件）与**支撑类产物**
  （创意概念、内容策划、视觉指导、渠道适配、效果报告、知识卡片）都是完整的目标语言
  （doctor 断言英文链路 9 类产物中文字符数为 0），离线与真实模型行为一致。
- 度量单位与日期格式目前只在语言画像里**声明**，未做自动换算。

---

## 14. REST / SSE 接口速查

所有接口挂载在 `/api` 前缀下。

> 若设置了 `CREATOR_API_TOKENS`，需为每个请求附带令牌（`Authorization: Bearer` / `X-API-Token` / `?token=`），
> 否则返回 `401`；`GET /api/health` 免鉴权。详见 [5.3 访问令牌与租户隔离](#53-访问令牌与租户隔离)。

### 基础信息

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 + 当前配置 + 模型提供方 + **断点续跑后端**（`checkpointer.kind`） |
| GET | `/api/agents` | 已实现/规划中的智能体与流水线阶段 |
| GET | `/api/knowledge` | 广告法词库、行业规则、渠道规范 |
| GET | `/api/settings` | 读取运行时配置 |
| PUT | `/api/settings` | 更新运行时配置（部分字段即可） |

### 记忆库

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/memory?kind=&limit=` | **当前租户**的卡片列表 + 容量 / 新鲜度 / 向量索引 / 租户统计 |
| POST | `/api/memory/search` | 检索召回（限当前租户，返回总分与 `vector_score` 语义分） |

```jsonc
// POST /api/memory/search
{ "query": "冷萃 咖啡 早八通勤 小红书", "topK": 5, "brand": "", "channel": "", "industry": "" }
```

### 质量评估

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/evaluations?taskId=&limit=` | 评估历史 + 聚合统计 + 当前评分口径（不传 `taskId` 即当前租户全库） |
| POST | `/api/tasks/{id}/evaluate` | 按需评估「评分台」（改完 Prompt 或人工改稿后无需重跑流水线） |

```jsonc
// POST /api/tasks/{id}/evaluate
{ "provider": "offline" }     // 可省略；offline = 规则评估，llm = 模型评估（失败自动回退）
```

### 回归测试

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/golden` | 用例清单 + 基线 + 覆盖矩阵 + 最近一次运行状态与对比结论 |
| POST | `/api/golden/run` | **后台启动**一次回归（立即 202，用 `GET /api/golden` 轮询进度） |
| POST | `/api/golden/reset` | 清空上一次运行结果（仅在未运行时允许） |

```jsonc
// POST /api/golden/run
{ "limit": 3 }                       // 只跑前 3 条（快速自查）
{ "caseIds": ["xiaohongshu_food"] }  // 只跑指定用例

// 运行中重复调用返回 409（不排队，避免双触发掩盖问题）
```

### 调用轨迹

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/tasks/{id}/trace` | span 树 + 按 span 名的耗时聚合 + 导出信息（无轨迹时返回空骨架而非 404） |

```jsonc
// GET /api/tasks/{id}/trace 的关键字段
{
  "trace_id": "…32 位十六进制…",
  "spans": [{ "name": "A4.copy_draft", "parent_span_id": "…", "kind": "internal",
              "duration_ms": 510, "self_ratio": 0.0, "attributes": { "agent.gate_result": "pass" } }],
  "summary": { "span_count": 42, "duration_ms": 5749,
               "by_name": [{ "name": "gate.review", "count": 2, "total_ms": 2538, "share": 0.44 }] },
  "export": { "path": "…/data/traces", "format": "otel-shaped-json", "file": "<task_id>.json" }
}
```

### 任务

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/tasks` | 任务摘要列表 |
| POST | `/api/tasks` | 新建任务 |
| GET | `/api/tasks/{id}` | 任务详情（含事件与黑板快照） |
| DELETE | `/api/tasks/{id}` | 删除任务并回收检查点（运行中返回 409） |
| POST | `/api/tasks/{id}/decide` | 人工裁决 |
| GET | `/api/tasks/{id}/blackboard` | 共享黑板快照 |
| GET | `/api/tasks/{id}/events?since=` | SSE 实时事件流 |
| POST | `/api/tasks/{id}/digital-human` | 创建数字人渲染作业（**开发样例**；无视频脚本 409） |
| GET | `/api/tasks/{id}/digital-human` | 查询渲染作业列表（读取时惰性推进样例状态机） |

```jsonc
// POST /api/tasks
{ "brief": { "brand": "晨野", "product": "冷萃即饮咖啡", "channel": "小红书", "industry": "食品饮料" },
  "autoApprove": false }

// POST /api/tasks/{id}/digital-human          （开发样例，两个字段均可省略）
{ "avatar": "brand-avatar-01", "provider": "sample" }

// POST /api/tasks/{id}/decide
{ "decision": "approve", "comment": "可以发布" }
```

### 发布与效果回填

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/tasks/{id}/publish` | 读取发布排期 |
| POST | `/api/tasks/{id}/publish` | 登记发布（`channel` 为空表示全部） |
| POST | `/api/tasks/{id}/publish/dispatch` | **自动投递**到 webhook（`channel` 为空表示全部，`force=false` 可跳过未到期项） |
| GET | `/api/publish/queue` | 跨任务待发布队列（`dueOnly=true` 只看已到期） |
| POST | `/api/publish/tick` | 手动驱动一次到期投递（供外部 cron 调用） |
| POST | `/api/tasks/{id}/feedback` | 回填真实效果，触发 A10 复盘 |

```jsonc
// POST /api/tasks/{id}/publish
{ "channel": "小红书", "url": "https://example.com/note/123" }

// POST /api/tasks/{id}/publish/dispatch
{ "channel": "", "force": true }        // channel 为空 = 全部渠道

// POST /api/tasks/{id}/feedback
{ "channel": "小红书", "window": "发布后 72 小时",
  "metrics": { "exposure": 12000, "clicks": 480, "interactions": 260, "conversions": 24 } }
```

### 指标

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/metrics` | 系统聚合、成本/缓存/租约/**评估**/**追踪**、逐智能体指标（启用鉴权时按租户统计） |

**SSE 事件类型**：`task.created`、`task.status`、`phase.enter`、`agent.start/progress/finish`、`gate.decision`（`payload.judge` 携带当次评估结论）、`revision.requested`、`approval.required/decided`、`blackboard.write`、**`judge.scored`**、`task.completed/failed`、`log`。

---

## 15. 数据文件与持久化

默认写入 `<项目根>/data`（可用 `CREATOR_DATA_DIR` 覆盖）：

| 文件 | 内容 |
| --- | --- |
| `data/tasks/*.json` | 任务快照 |
| `data/blackboard.json` | 共享黑板（事实 / 评审 / 活动 / 租约） |
| `data/memory.json` | A11 记忆库卡片（含租户归属） |
| `data/evaluations.json` | LLM-as-a-Judge 评估历史（单任务最多 20 条、全局 500 条） |
| `data/traces/<task_id>.json` | 调用轨迹（span 树 + OTel 形状的 `otel[]` 段）；删除任务时一并回收 |
| `data/digital_human.json` | 数字人渲染作业（**开发样例**）；删除任务时一并回收 |
| `data/checkpoints.sqlite` | LangGraph 检查点（断点续跑） |

> 默认 `file` 模式下数据都在 `data/`；`CREATOR_STORAGE=pg` 时任务/黑板/记忆库/
> 评估/数字人作业改存 PostgreSQL（`payload jsonb` 保全量）、意图租约存 Redis、
> 检查点走 PostgresSaver，`traces/` 导出仍在本目录（生产观测建议直接接 OTLP）。
> 两后端的接口契约、实体映射与迁移步骤见 [`storage_contract.md`](storage_contract.md)；
> file → pg 存量数据迁移用 `python scripts/pg_migrate.py`（幂等，先 `--dry-run` 预览）。

**随代码版本化、不在 `data/` 下的资产**：

| 文件 | 内容 |
| --- | --- |
| `golden/briefs/*.json` | 黄金数据集用例（固定 Brief） |
| `golden/baseline.json` | 黄金基线分数（含生成时的口径与引擎） |
| `.doctor-data/` | 自检/冒烟/回归脚本的临时数据目录（可随时整个删掉） |

> 优雅退出时会自动刷盘；删除任务时会顺带清理该任务的检查点，避免 `checkpoints.sqlite` 只增不减。
> 自检与冒烟脚本使用 `<项目根>/.doctor-data/` 作为临时数据目录，不会碰你的 `data/`，可以随时整个删掉。

---

## 16. 自检与验收

```bash
# 环境与能力自检（16 项：RAG 闭环 / 向量检索 / 租户隔离 / 检查点后端 / 评估器 /
#   回归判定器 / 否定语境判定 / 多语言 / 视频脚本 / 追踪层级 / OTLP 导出 /
#   W3C 传播与采样 / 数字人渲染样例）
python scripts/doctor.py

# 黄金数据集回归：与基线逐项对比 + 内容级硬约束，退出码 0 表示无回归
python scripts/golden_eval.py
python scripts/golden_eval.py --coverage         # 只看渠道覆盖矩阵

# 端到端验收：拉起真实服务，逐条核对 /api/* 与 SSE 契约（退出码 0 即通过）
python scripts/smoke_api.py

# 快速契约核验（约 20 秒）：评估 / 租户视角 / 追踪 / 传播采样 / 数字人样例闭环，
# 改完相关代码先跑它
python scripts/verify_contracts.py

# 容器化清单核验（不需要 Docker daemon）
python scripts/check_deploy.py

# PG 存储层专项回归（需 CREATOR_STORAGE=pg + 本地 postgres/redis，见 storage_contract.md §6）
python scripts/pg_check.py

# file → pg 存量数据迁移（幂等；先 --dry-run 预览计数再实迁）
python scripts/pg_migrate.py --dry-run
python scripts/pg_migrate.py

# 真实网关链路核验（延迟 / token / 成本 / 缓存命中 / 门禁结论）
python scripts/real_check.py --tasks 1

# 压测 / Prompt 调优基线（Mock 校验并发正确性；openai 量真实延迟与成本）
python scripts/stress_llm.py -n 12 -c 4
LLM_PROVIDER=openai OPENAI_API_KEY=sk-xxx python scripts/stress_llm.py -n 6 -c 2

# 前端类型检查与构建
npm run typecheck
npm run build
```

`doctor.py` 覆盖 16 项：Mock 引擎收敛、记忆库 RAG 闭环、向量检索、发布排期、
鉴权租户解析、**记忆库租户隔离**、**断点续跑检查点后端**、**LLM-as-a-Judge 评估器**、
**黄金数据集回归判定器**（9 个子断言证明它会判回归）、**否定语境判定**（10 个用例）、
**调用轨迹追踪**（断言 span 树不是平铺列表、llm span 正确嵌套）、
**OTLP 导出链路**（id 一致、层级与智能体归属保留、默认不加载 OTel）、
**W3C 传播与采样**（traceparent 解析/生成、坏头忽略、采样只影响导出面）、
**数字人渲染样例**（渲染清单、惰性推进、租户隔离、http 失败路径与回收）。

`smoke_api.py` 覆盖：主流程（建任务 → SSE → 挂起 → 裁决 → 归档）、自动审批、记忆闭环、
**发布闭环（登记发布 → 回填 → A10 复盘）**、**自动投递 → 待发布队列**、
**评估流水线（历史 / 聚合 / 按需评估 / 门禁事件）**、**调用轨迹（span 树层级 / 嵌套 / 导出）**、
**黄金数据集（后台运行 / 重复触发 409 / 部分运行结论）**、聚合指标、
错误分支（404/400/409）、持久化核对，
以及**另起一个带鉴权的实例验证任务与记忆库的租户隔离**。

### 16.1 持续集成

`.github/workflows/ci.yml` 已把上述关卡全部接成自动化门禁，**无需任何模型密钥**
（默认走内置离线引擎）：

| 任务 | 关卡 |
| --- | --- |
| 后端 | `doctor.py` → `golden_eval.py` → `verify_contracts.py` → `check_deploy.py` → `smoke_api.py` → `stress_llm.py` |
| 前端 | `npm run typecheck` + `npm run build` |
| 文档 | 关键章节与接口速查是否仍被提及（防止「代码跑得通、文档停在上一版」） |

失败时会自动上传 `.doctor-data/**/server.log` 作为诊断产物。

`stress_llm.py` 输出：完成率、端到端延迟 p50/p95/p99、平均返工轮次与首轮通过率、质量分、
总成本 / 单篇成本 / 缓存命中率 / 熔断任务数，以及**黑板租约冲突与活跃租约残留**（应归零）。
它在临时数据目录里拉起独立服务实例，不会污染你的 `data/`。

---

## 17. 需要人工协助的事项

以下是**必须由人来做**的事，代码无法替代（同一份清单也在 `README.md` 里）。

### P0 — 上线前必须完成

| # | 事项 | 为什么需要人 | 建议做法 |
| --- | --- | --- | --- |
| 1 | ~~验证 Docker 镜像构建与运行~~ **已完成** ✅ | — | 已实测：`Dockerfile.offline` 构建成功（受限网络下绕开 Docker Hub），容器 `healthy`、前端可访问、跑通完整任务、`/data` 卷跨容器重建持久化 |
| 2 | **设置生产访问令牌** | `CREATOR_API_TOKENS` 决定租户隔离边界；为空则完全不鉴权 | 生成强随机令牌，按 `acme:tok,beta:tok` 形式配置，放入 Secret 而非 ConfigMap |
| 3 | ~~用真实网关数据复测 Prompt 与预算~~ **已完成** ✅ | — | Prompt 收紧（A4「无来源不写」+ A3/A5/A6/A11 输出体量控制）后已完成真实网关复测：536s / 12.0 万 token / $0.056 / 质量分 65，门禁逐轮生效后放行；实测值远低于预算线（$1.0 / 20 万），预算无需调整 |
| 4 | **人工审核非中文市场的合规表述** | 系统只有中文广告法词库；英文/日文/韩文/西语的合规红线已写入提示词但**未经法规校验** | 由目标市场法务复核首批产出，必要时补当地词库 |
| 5 | **吊销并更换本次联调用的 API Key** | 该 DeepSeek Key 已在对话中明文出现，应视为已泄露 | 在 DeepSeek 控制台吊销并新建；新 Key 只写进 `.env`（已 gitignore），不要提交 |

### P1 — 影响可用性

| # | 事项 | 为什么需要人 |
| --- | --- | --- |
| 6 | **提供品牌 VI 与产品资料** | 品牌语气、禁用词、Logo 规范、产品参数与可引用证据目前是通用示例；真实效果取决于喂给系统的品牌资产 |
| 7 | **配置发布网关** | `PUBLISH_WEBHOOK_URL` 需指向能调用各平台开放接口的服务（n8n / Zapier / 自建），平台授权与限流必须由该网关承接 |
| 8 | **补充黄金数据集的行业用例** | 现有 11 条覆盖渠道与边界，但真实业务的高频选题应由业务方提供 |
| 9 | **确认行业合规词库** | 医疗健康 / 金融 / 教育等强监管行业的规则需法务确认（当前为示例词库） |

### P2 — 增强项

| # | 事项 | 为什么需要人 |
| --- | --- | --- |
| 10 | **选定数字人服务商并决定是否正式接入** | 系统提供的是接入样例（[13.6](#136-数字人渲染开发样例)：内置引擎 + http 适配）；正式接入需明确产品形态并选型服务商，网关契约见 `DIGITAL_HUMAN_API_URL` 说明 |
| 11 | **决定是否切换 pg 存储模式** | 双存储后端已实现：默认 `file`（单机零依赖）与 `CREATOR_STORAGE=pg`（PostgreSQL + Redis，多副本前提）。是否为生产环境启用 pg、PG/Redis 用托管还是自建，属架构与部署决策；切换步骤见 [`storage_contract.md`](storage_contract.md) §6 |
| 12 | **接入 OTel Collector / Jaeger 生产实例** | 本地用 compose 里的 all-in-one 即可；生产需要持久化存储与采样策略 |
| 13 | **建立人工抽检机制** | 评估与门禁能拦住大部分问题，但品牌调性与创意质量最终仍需人判断 |

---

## 18. 常见问题

**Q：需要 API Key 吗？**
不需要。默认内置离线 Mock 引擎，全流程可跑通；要接真实模型，在「运行时设置」切到 `openai` 并填 Base URL / Key / 模型名。

**Q：为什么任务一直「执行中」？**
可能正在返工或等待大模型返回。查看事件时间线确认；若长时间无进展，检查模型网关连通性与超时设置。

**Q：成本显示为 0 或没有变化？**
`mock` 引擎不产生费用，成本为 0 属正常。切换到真实模型后即会计费。

**Q：任务重启后还在跑吗？**
会。服务启动时会对未完成任务做断点续跑；返工与人工审批中断点也被保留。

**Q：为什么回填效果后没有新产物？**
确认任务已通过审批（存在发布排期）且 `metrics` 至少含 `exposure` 与 `clicks`；回填成功后 A10 会产出新版本的 `effect_report`。

**Q：Windows 上 `pip install` 报 WinError 448？**
设置环境变量 `PYTHONNOUSERSITE=1`，并给 pip 加 `--no-warn-script-location`。

**Q：接口突然返回 401？**
说明服务端设置了 `CREATOR_API_TOKENS`。请在请求里带令牌（`Authorization: Bearer` / `X-API-Token` / `?token=`），
或在界面「运行时设置 → 访问令牌」填入。`GET /api/health` 不需要令牌。

**Q：点了「投递」但状态是 `skipped`？**
未配置 `PUBLISH_WEBHOOK_URL`。这是离线默认行为，`skipped` 与「登记发布」等价（不会真的发布，但会记录基线）。
配置 webhook 后即变成真实推送；失败会显示 `failed` 与 `last_error`。

**Q：排期一直不自动投递？**
确认 `PUBLISH_AUTO_DISPATCH=true`（默认关闭）。也可外部定时调 `POST /api/publish/tick`，或界面手动「全部投递」。
另外，只有**已到到期时间**的条目才会被自动投递（`due_at` 由建议时段换算，当天已过会顺延到次日）。

**Q：换 `EMBEDDING_PROVIDER=openai` 后检索报错？**
不会报错——远端异常会自动回退本地向量。若想确认实际生效的提供方，看 `GET /api/memory` 的 `stats.embedding`，
或界面「记忆库」的向量检索 chips。

**Q：回归测试显示「回退」，但我只改了一点点东西？**
按顺序看三处：①对比表的「说明」列（会写清是哪个指标回退、超了多少容差）；
②是不是只是**返工轮次 +1** —— 那即使分数没掉也判回退（更慢更贵）；
③基线的 `rubric` 是否与当前一致，不一致会在顶部提示「口径不一致」，此时分数不可比。
容差内的波动（默认 ±3 分）不会判回退；确实判断为噪声时可以 `--tolerance` 放宽。

**Q：可以改基线吗？**
可以，但要有意识地改：`python scripts/golden_eval.py --update-baseline`。
存在回归/失败/缺失时它**会拒绝执行**，需要显式 `--force` —— 这是刻意设计的摩擦，
避免顺手把回归固化成新基线。

**Q：`golden_eval.py --case xxx` 为什么只跑一条也能通过？**
部分运行只对**本次范围内**的用例判定，并会打印「这是部分运行，结论仅对范围内用例成立」。
全量运行时如果缺用例，仍然算失败 —— 跳过困难用例的成本依旧很高。

**Q：调用轨迹里为什么有 3 个根 span？**
正常。一次任务被人工审批打断成两段执行，因此有两个 `graph.invoke#*` 根 span；
中间的 `human.wait` 是人工等待，**刻意为独立一段** —— 否则会把「人在犹豫」的时间算进系统耗时。
如果根 span 数量接近 span 总数，那才是异常（说明层级塌了）。

**Q：`data/traces/` 会一直变大吗？**
进程内 trace 有上限（200 个），落盘文件按任务一个，删除任务时会一并回收 trace。
如需长期保留可自行归档；追踪落盘失败不会影响任务执行（可在指标里看到失败次数）。

**Q：span 错误数一直是 0，是不是没生效？**
是生效的。降级到离线引擎、命中响应缓存、成本熔断都被记为**正常路径**而不是错误 ——
它们是设计出来的行为。所以错误率为 0 说明链路健康；一旦非 0 就真的有问题。

**Q：接了 Jaeger 但看不到 trace？**
先看 `GET /api/health` 的 `otlp` 字段：`enabled=false` 说明 `OTLP_ENDPOINT` 没生效或初始化失败
（`error` 里会写原因）。再确认端点写法 —— 写 `http://host:4318` 即可，`/v1/traces` 会自动补全。
用 compose 起的是 `http://jaeger:4318`（容器内网名），从宿主机连则是 `http://localhost:4318`。
导出失败**不会影响任务**，失败次数在 `otlp.failed` 里。

**Q：Jaeger 里的 trace_id 和本地 `data/traces/*.json` 对得上吗？**
对得上，这是刻意保证的：转发时**手工构造 OTel 的 `ReadableSpan`**，
而不是用 OTel API 重新创建 span（那样 SDK 会生成新的 span_id，
「按 id 去 Jaeger 查这一条」就失效了）。自检里有对应断言。

**Q：容器起来后任务和记忆库都没了？**
数据在 `/data`，没有挂卷的话容器重建就会丢。用 `-v creator-data:/data`
（compose 已默认配好）。别用 `docker compose down -v`，那会连卷一起删。

**Q：k8s 里能起多副本吗？**
默认 file 模式不行，清单里固定 `replicas: 1` —— 本地 JSON + SQLite 多副本会状态分裂。
pg 存储模式（`CREATOR_STORAGE=pg`，PostgreSQL + Redis 共享后端）已实现，
先把应用切过去（步骤见 [`storage_contract.md`](storage_contract.md) §6），
再由部署方决定副本数与托管库。**在清单里假装支持多副本，比不支持更危险**，
所以 file 模式的清单保持写死单副本。

**Q：开启 API 令牌后容器一直不健康？**
不应该 —— 探针用的是免鉴权的 `GET /api/health`。`scripts/check_deploy.py`
专门断言「探针路径 = 免鉴权路径」这条对应关系。若真遇到，
检查是否自行把探针改成了需要鉴权的接口。

**Q：`docker build` 报 COPY 找不到文件？**
先跑 `python scripts/check_deploy.py`：它会核对每个 `COPY` 源路径是否存在、
是否被 `.dockerignore` 排除，以及所有声明的环境变量是否真被后端读取。
这个脚本**不需要 Docker daemon**，比反复试构建快得多。

**Q：`smoke_api.py` 跑到一半卡住不动？**
这是历史上踩过的坑（子进程日志管道写满），已在脚本内规避。
若你自行改造脚本，请把服务端 stdout/stderr **重定向到文件**而不是 `PIPE`。

**Q：评估分为什么和我预期差很多？**
先确认三件事：①「评估报告」页顶部的评分口径版本（`rubric`）——跨版本分数不可比；
②评估器是 `offline`（规则）还是 `llm`（模型），若显示「已回退规则评估器」，说明模型评估当时不可用，
分数是规则评估器给的；③通过线（`JUDGE_PASS_THRESHOLD`）是否被改过（裁决是按它算的）。
分数本身想影响门禁，需要把 `JUDGE_MODE` 从默认的 `advisory` 改成 `blocking`。

**Q：评估分会影响综合质量分吗？**
会，按 `JUDGE_WEIGHT`（默认 `0.2`）混合。设为 `0` 即完全不影响，综合分与历史行为逐位一致（可回滚）。

**Q：换 `JUDGE_PROVIDER=llm` 后评估报错？**
不会报错——任何异常（网关不通、返回非 JSON、维度缺失）都会自动回退到规则评估器，
并在报告里标注 `fallback` 与原因。回退次数可在「运行指标 → 质量评估」看到。

**Q：记忆库检索不到我刚沉淀的知识？**
按顺序排查：①是否**同一个租户**（不同 API Token 对应不同租户，知识不互通）；
②`score` 是否低于 `MIN_SCORE = 0.2`（低于阈值不返回，避免虚假命中）；
③是否被当作当前任务自己的卡片被排除（`exclude_task`）；
④是否已超过 `MAX_AGE_DAYS = 180` 天被自动下线。

**Q：记忆库的「已建索引 0 条」是不是坏了？**
不是。向量是**懒计算**的：重启后 `0` 属正常，首次检索后才会涨上来。
另外它只统计**当前租户**已建索引的卡片数。

**Q：顶栏出现「断点续跑不可用」？**
说明 SQLite 检查点初始化失败（通常是数据目录不可写，例如被安全策略/沙箱限制），
系统已退回内存检查点：**运行中任务在进程重启后无法续跑**。
检查 `CREATOR_DATA_DIR`（或默认 `data/`）目录权限后重启服务即可；
`GET /api/health` 的 `checkpointer` 字段可看到具体错误。

**Q：接口列表里的时间格式？**
统一为 ISO 8601（UTC）。

---

## 19. 目录结构

```
creator/
├── app/                 # Python 后端（FastAPI + LangGraph）
│   ├── agents/          # A1–A11 智能体实现
│   ├── api/             # REST + SSE 路由
│   ├── core/            # 黑板、事件总线、门禁、编排器、发布投递、评估器、评估历史、
│   │                    # 黄金数据集、调用轨迹、OTLP 导出、存储、类型
│   ├── knowledge/       # 渠道规范、行业洞察、广告法词库、记忆库、向量化
│   ├── llm/             # 引擎、定价、成本账本、响应缓存、Mock/OpenAI 提供方
│   ├── config.py        # 运行时配置（含 embedding / publish / judge / API 令牌）
│   ├── main.py          # 进程入口
│   └── server.py        # 应用装配 + 鉴权中间件 + 静态托管
├── src/                 # React + TypeScript 前端
│   ├── components/      # 界面组件（含 JudgePanel 评估报告、TracePanel 调用轨迹、GoldenPanel 回归测试）
│   └── lib/             # API 客户端、类型契约、格式化
├── scripts/             # doctor.py（自检）、golden_eval.py（质量回归）、
│                        # smoke_api.py（端到端验收）、verify_contracts.py（快速契约核验）、
│                        # check_deploy.py（容器化清单核验）、stress_llm.py（压测）
├── golden/              # 黄金数据集（随代码版本化的测试资产）
│   ├── briefs/*.json    # 11 条固定用例（含内容级 expect 断言），覆盖全部 7 个渠道
│   └── baseline.json    # 基线分数 + 生成时的 rubric / engine
├── deploy/k8s.yaml      # Kubernetes 清单（ConfigMap/Secret/PVC/Deployment/Service/Ingress）
├── Dockerfile           # 多阶段镜像（Node 构建前端 → Python 运行时）
├── docker-compose.yml   # 应用 + Jaeger 一键起
├── .github/workflows/   # ci.yml：自检 / 质量回归 / 契约 / 部署清单 / 端到端 / 并发 / 前端 / 文档
├── dist/                # 前端构建产物（后端托管）
├── .doctor-data/        # 自检/冒烟/回归脚本的临时数据（可随时删除，已 gitignore）
├── creator.md           # 架构设计
├── plan.md              # 迭代计划
├── MEMORY.md            # 开发进度与踩坑
├── ENGINEERING_PRINCIPLES.md  # 工程原则（从踩坑提炼）
├── storage_contract.md  # 存储层契约（file / pg 双后端已实现，含切换步骤）
├── README.md            # 项目门面（含人工协助清单）
└── USER_GUIDE.md        # 本文档
```

---

需要变更流水线行为或新增智能体时，请先阅读 `creator.md` 的「核心接口与数据模型」章节，确保前后端契约一致。
