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
8. [状态机与门禁规则](#8-状态机与门禁规则)
9. [REST / SSE 接口速查](#9-rest--sse-接口速查)
10. [数据文件与持久化](#10-数据文件与持久化)
11. [自检与验收](#11-自检与验收)
12. [常见问题](#12-常见问题)
13. [目录结构](#13-目录结构)

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
| A11 | 知识沉淀 | 归档最佳实践、跨任务 RAG 召回 | 知识卡片 `knowledge_card`、最终交付件 `final_delivery` |

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
- **多租户**：配置访问令牌后，接口需鉴权且任务数据按租户隔离（单机不配置则零感知）。

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
│ 顶栏：服务状态 · 任务计数 · 新建任务 · 运行时设置                │
├───────────────┬──────────────────────────────────────────────┤
│ 任务列表       │ 任务详情                                      │
│ （3s 轮询）    │  ├ 基本信息 + 质量评分卡                      │
│               │  ├ 人工审批横幅（待审批时出现）                 │
│               │  ├ 发布与效果回填面板（已排期后出现）            │
│               │  └ 标签页：流水线看板 / 共享黑板产物 / 事件时间线 / 运行指标 │
└───────────────┴──────────────────────────────────────────────┘
```

- **任务列表**：实时刷新，展示品牌/产品、状态、当前阶段、返工轮次、产物数、质量分。
- **流水线看板**：每个智能体的运行状态、置信度、门禁结果与摘要。
- **共享黑板产物**：14 类结构化产物，按类型定制渲染；同一类型可**版本对比（diff）**。
- **事件时间线**：SSE 实时推送，含事实与评审计数。
- **运行指标**：系统聚合、成本/缓存/租约、逐智能体运行指标。
- **发布与效果回填面板**：审批后出现，含「到期时间」列、投递 / 登记双按钮、投递状态 Chip 与「全部投递」。
- **运行时设置**：模型 / 编排与门禁 / 成本与缓存 / **向量检索** / **发布投递** / **访问令牌** / 知识库 / 记忆库。

---

## 4. 完整实操流程

### 4.1 新建创作任务

点击右上角「新建创作任务」，填写 Brief：

| 字段 | 说明 |
| --- | --- |
| 品牌 / 产品 | 必填，用于语气与一致性校验 |
| 传播目标 | 曝光 / 互动 / 转化 / 教育 / 信任 |
| 目标受众 | 越具体越好，直接影响 A1 洞察质量 |
| 主渠道 | 小红书 / 抖音 / 公众号 / 知乎 / 电商详情页 / 官网 / PR |
| 行业 | 决定合规规则强度（食品饮料、美妆、医疗健康等） |
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

- 查看卡片列表、类型分布、新鲜度统计；
- 用自然语言检索，看到的结果与智能体实际复用**同一套打分逻辑**；
- 新任务创建时，A1/A2/A4 会自动召回历史资产（可在事件时间线看到「复用来源」）。

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
| `CREATOR_API_TOKENS` | 空 | API 访问令牌；为空不鉴权。支持 `tok1,tok2` 或 `acme:tok1,beta:tok2` |
| `CREATOR_DATA_DIR` | `<项目根>/data` | 持久化目录（测试/多实例隔离用） |

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
- **新鲜度**：`FRESH_DAYS = 30` 天视为新鲜，`STALE_DAYS = 120` 天标记陈旧；**同分时优先新鲜知识**。
- **过期下线**：超过 `MAX_AGE_DAYS = 180` 天的卡片在读写时自动清理，避免旧玩法污染新任务。
- **容量**：上限 500 条，超出时按最旧淘汰。

> 界面「运行时设置 → 记忆库」可直接看到当前检索方式（提供方 / 模型 / 语义权重 / 已建索引条数），
> 命中项除总分外还会显示「语义 {vector_score}」，方便判断是关键词命中还是语义命中。

---

## 8. 状态机与门禁规则

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

## 9. REST / SSE 接口速查

所有接口挂载在 `/api` 前缀下。

> 若设置了 `CREATOR_API_TOKENS`，需为每个请求附带令牌（`Authorization: Bearer` / `X-API-Token` / `?token=`），
> 否则返回 `401`；`GET /api/health` 免鉴权。详见 [5.3 访问令牌与租户隔离](#53-访问令牌与租户隔离)。

### 基础信息

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查 + 当前配置 + 模型提供方 |
| GET | `/api/agents` | 已实现/规划中的智能体与流水线阶段 |
| GET | `/api/knowledge` | 广告法词库、行业规则、渠道规范 |
| GET | `/api/settings` | 读取运行时配置 |
| PUT | `/api/settings` | 更新运行时配置（部分字段即可） |

### 记忆库

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/memory?kind=&limit=` | 卡片列表 + 容量 / 新鲜度 / 向量索引统计 |
| POST | `/api/memory/search` | 检索召回（返回总分与 `vector_score` 语义分） |

```jsonc
// POST /api/memory/search
{ "query": "冷萃 咖啡 早八通勤 小红书", "topK": 5, "brand": "", "channel": "", "industry": "" }
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

```jsonc
// POST /api/tasks
{ "brief": { "brand": "晨野", "product": "冷萃即饮咖啡", "channel": "小红书", "industry": "食品饮料" },
  "autoApprove": false }

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
| GET | `/api/metrics` | 系统聚合、成本/缓存/租约、逐智能体指标（启用鉴权时按租户统计） |

**SSE 事件类型**：`task.created`、`task.status`、`phase.enter`、`agent.start/progress/finish`、`gate.decision`、`revision.requested`、`approval.required/decided`、`blackboard.write`、`task.completed/failed`、`log`。

---

## 10. 数据文件与持久化

默认写入 `<项目根>/data`（可用 `CREATOR_DATA_DIR` 覆盖）：

| 文件 | 内容 |
| --- | --- |
| `data/tasks/*.json` | 任务快照 |
| `data/blackboard.json` | 共享黑板（事实 / 评审 / 活动 / 租约） |
| `data/memory.json` | A11 记忆库卡片 |
| `data/checkpoints.sqlite` | LangGraph 检查点（断点续跑） |
| `data/settings.json` | 运行时配置 |

> 优雅退出时会自动刷盘；删除任务时会顺带清理该任务的检查点，避免 `checkpoints.sqlite` 只增不减。

---

## 11. 自检与验收

```bash
# 环境与 Mock 引擎自检（含记忆库 RAG 闭环、向量检索、发布排期、鉴权解析）
python scripts/doctor.py

# 端到端验收：拉起真实服务，逐条核对 /api/* 与 SSE 契约（退出码 0 即通过）
python scripts/smoke_api.py

# 压测 / Prompt 调优基线（Mock 校验并发正确性；openai 量真实延迟与成本）
python scripts/stress_llm.py -n 12 -c 4
LLM_PROVIDER=openai OPENAI_API_KEY=sk-xxx python scripts/stress_llm.py -n 6 -c 2

# 前端类型检查与构建
npm run typecheck
npm run build
```

`smoke_api.py` 覆盖：主流程（建任务 → SSE → 挂起 → 裁决 → 归档）、自动审批、记忆闭环、**发布闭环（登记发布 → 回填 → A10 复盘）**、**自动投递 → 待发布队列**、聚合指标、错误分支（404/400/409）、持久化核对。

`stress_llm.py` 输出：完成率、端到端延迟 p50/p95/p99、平均返工轮次与首轮通过率、质量分、
总成本 / 单篇成本 / 缓存命中率 / 熔断任务数，以及**黑板租约冲突与活跃租约残留**（应归零）。
它在临时数据目录里拉起独立服务实例，不会污染你的 `data/`。

---

## 12. 常见问题

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

**Q：`smoke_api.py` 跑到一半卡住不动？**
这是历史上踩过的坑（子进程日志管道写满），已在脚本内规避。
若你自行改造脚本，请把服务端 stdout/stderr **重定向到文件**而不是 `PIPE`。

**Q：接口列表里的时间格式？**
统一为 ISO 8601（UTC）。

---

## 13. 目录结构

```
creator/
├── app/                 # Python 后端（FastAPI + LangGraph）
│   ├── agents/          # A1–A11 智能体实现
│   ├── api/             # REST + SSE 路由
│   ├── core/            # 黑板、事件总线、门禁、编排器、发布投递、存储、类型
│   ├── knowledge/       # 渠道规范、行业洞察、广告法词库、记忆库、向量化
│   ├── llm/             # 引擎、定价、成本账本、响应缓存、Mock/OpenAI 提供方
│   ├── config.py        # 运行时配置（含 embedding / publish / API 令牌）
│   ├── main.py          # 进程入口
│   └── server.py        # 应用装配 + 鉴权中间件 + 静态托管
├── src/                 # React + TypeScript 前端
│   ├── components/      # 界面组件
│   └── lib/             # API 客户端、类型契约、格式化
├── scripts/             # doctor.py（自检）、smoke_api.py（端到端验收）、stress_llm.py（压测）
├── dist/                # 前端构建产物（后端托管）
├── creator.md           # 架构设计
├── plan.md              # 迭代计划
├── MEMORY.md            # 开发进度与踩坑
└── USER_GUIDE.md        # 本文档
```

---

需要变更流水线行为或新增智能体时，请先阅读 `creator.md` 的「核心接口与数据模型」章节，确保前后端契约一致。
