# Creator Agent Studio

> 把一个营销 Brief 交给一支编排好的 AI 内容团队：**策略 → 创意 → 策划 → 文案 → 审校 → 事实核查 → 合规 → 视觉 → 渠道 → 评估 → 人工审批 → 发布 → 复盘**，
> 全程看得见、可干预、有门禁、能复盘，**不填一个 API Key 也能完整跑通**。

这是一个**编排式多智能体内容创作台**：11 个专业智能体（A1–A11）由一个总控编排器驱动，
审核智能体把守质量与合规门禁，人在关键节点拍板，产出可直接投放的多平台内容。
它不是「一个能写文案的提示词」，而是一套带有状态机、门禁、成本熔断、质量度量与回归门禁的**生产线**。

---

## 目录

- [核心能力](#核心能力)
- [快速开始](#快速开始)
- [界面与实操流程](#界面与实操流程)
- [架构总览](#架构总览)
- [智能体清单](#智能体清单)
- [质量与合规门禁](#质量与合规门禁)
- [质量评估与回归门禁](#质量评估与回归门禁)
- [可观测性与调用轨迹](#可观测性与调用轨迹)
- [多语言与视频脚本](#多语言与视频脚本)
- [部署](#部署)
- [配置项](#配置项)
- [REST / SSE 接口](#rest--sse-接口)
- [开发与验证](#开发与验证)
- [项目状态](#项目状态)
- [需要人工协助的事项](#需要人工协助的事项)
- [文档导航](#文档导航)

---

## 核心能力

| 能力 | 说明 |
|---|---|
| **多智能体流水线** | A0 总控编排 11 个智能体，LangGraph `StateGraph` 承载控制流（条件边门禁环 + `interrupt()` 人工审批 + 断点续跑） |
| **共享黑板** | 产物 / 事实 / 意图 / 活动 / 评审五类实体，带版本号、Intent 租约与冲突重试 |
| **阶段门禁** | `pass / revise / reject / escalate`；A6 事实核查与 A7 品牌合规有**一票否决权**；返工超限自动升级人工 |
| **人工在环** | 关键节点挂起等待批准 / 打回 / 驳回；批准后自动生成发布排期并归档 |
| **两级容错** | 重试退避 → 降级内置离线引擎；**无 API Key 也能完整跑通** |
| **成本可控** | 一任务一账本，超预算自动熔断到离线引擎；LLM 响应缓存省钱且零延迟 |
| **知识复利** | A11 把可复用资产沉淀进记忆库（关键词 + 向量混合检索），过期自动下线；**按租户 + 按语言分区** |
| **质量评估** | 与门禁解耦的 LLM-as-a-Judge 六维评分；离线规则版可复现，模型版失败自动回退 |
| **回归门禁** | 11 条黄金用例 + 147 项内容级断言（品牌名 / 关键词 / 阻断用语 / 标题字数），改动导致质量下降会被 CI 拦下 |
| **调用轨迹** | span 树覆盖整个 Agent session，可按层级看「哪一层最贵」；可选 OTLP 导出到 Jaeger |
| **多语言** | 中文 / 英文 / 日文 / 韩文 / 西语画像；目标语言**原生创作**而非翻译 |
| **多租户** | 令牌 → 租户，任务与记忆库双向隔离；跨租户访问一律 404 |
| **发布闭环** | 审批 → 排期 → 登记发布 / webhook 自动投递 → 回填真实效果 → A10 复盘 → A/B 结论 |
| **容器化** | 多阶段镜像 + docker compose（含 Jaeger）+ k8s 清单 |

---

## 快速开始

### 依赖

- **Python 3.12**（推荐 conda 环境 `multi-agent-creator`）
- **Node.js ≥ 22.6**（仅前端构建需要；生产模式后端直接托管 `dist/`）

### 安装与启动

```bash
# 1) 后端依赖
conda activate multi-agent-creator
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 2) 前端依赖并构建
npm install
npm run build

# 3) 启动（默认 http://127.0.0.1:8787）
python -m app.main
```

浏览器打开 `http://127.0.0.1:8787` 即可。**默认使用内置离线引擎，无需任何密钥。**

开发模式（前后端热更新）：

```bash
npm run dev          # 同时起 python -m app.main（:8787）与 Vite（:5273）
```

### Docker 一键起（含 Jaeger）

```bash
docker compose up -d --build
#   应用   http://127.0.0.1:8787
#   Jaeger http://127.0.0.1:16686
```

### 接入真实模型

在界面「运行时设置 → 模型」里切到 `openai` 并填 Base URL / Key / 模型名即可
（兼容一切 OpenAI 协议网关：DeepSeek / 通义 / 豆包 / vLLM / Ollama）。
也可以用环境变量：

```bash
LLM_PROVIDER=openai OPENAI_BASE_URL=https://api.deepseek.com/v1 \
OPENAI_API_KEY=sk-xxx OPENAI_MODEL=deepseek-chat python -m app.main
```

---

## 界面与实操流程

```
┌──────────────────────────────────────────────────────────────┐
│ 顶栏：服务状态 · 任务计数 · OTLP 状态 · 新建任务 · 运行时设置    │
├───────────────┬──────────────────────────────────────────────┤
│ 任务列表       │ 任务详情                                      │
│               │  ├ 基本信息 + 质量评分卡                      │
│               │  ├ 人工审批横幅（待审批时出现）                 │
│               │  ├ 发布与效果回填面板（已排期后出现）            │
│               │  └ 标签页：流水线看板 / 共享黑板产物 / 评估报告  │
│               │            / 调用轨迹 / 事件时间线 / 运行指标   │
└───────────────┴──────────────────────────────────────────────┘
```

1. **新建创作任务**：填 Brief（品牌 / 产品 / 目标受众 / 主渠道 / 行业 / 关键词 / 硬性约束 / 目标语言）。
   勾选「自动审批」可跳过人工裁决，适合批量试跑。
2. **观察流水线**：A0–A11 依次执行，界面每 2.5s 拉快照 + SSE 实时事件。
   门禁不通过自动返工，超限升级人工；成本超预算自动熔断到离线引擎（任务不中断）。
3. **人工审批**：全部审核通过后挂起，选「通过 / 退回返工 / 驳回」。
4. **发布与回填**：逐渠道「登记发布」或走 webhook 自动投递；
   回填曝光/点击/互动/转化后 A10 从「发布前预估」切到「发布后复盘」并给出 A/B 结论。
5. **看质量与轨迹**：「评估报告」看六维评分与改进建议；「调用轨迹」看耗时分布在哪一层。

---

## 架构总览

```
交互层      Web UI / REST API / SSE 实时事件 / 人工审批面板
              │
编排层      A0 总控编排器（LangGraph StateGraph）
            ├ 条件边门禁环：review ─pass→ A8…  ├revise→ A4  └escalate→ 人工裁决
            ├ 人工在环：interrupt() + SQLite 检查点（断点续跑）
            ├ 成本熔断 / Turn Budget / 响应缓存
            └ 共享黑板（产物 / 事实 / 意图租约 / 活动 / 评审）
              │
智能体层    A1 策略  A2 创意  A3 策划  A4 文案  A5 编辑  A6 核查
            A7 合规  A8 视觉  A9 渠道  A10 分析  A11 记忆
              │
知识层      渠道规范 / 行业洞察 / 广告法词库 / 视觉风格库 / 记忆库(RAG) / 语言画像
              │
模型层      OpenAI 兼容 Provider（重试 + 降级）/ 内置离线引擎（零密钥可跑）
              │
观测层      事件总线 + SSE / 调用轨迹 span 树（可选 OTLP → Jaeger）/ 指标聚合
              │
持久层      JSON 文件 + SQLite 检查点（data/），可按接口替换为 PostgreSQL + Redis
```

**技术栈**：Python 3.12 · FastAPI · LangGraph · Pydantic · React 19 + TypeScript + Vite 7

---

## 智能体清单

| ID | 智能体 | 职责 | 产出物 |
|---|---|---|---|
| A0 | 总控 | 解析 Brief、拆任务卡、编排流水线 | `task_plan` |
| A1 | 策略洞察 | 受众画像、核心信息屋、传播目标 | `strategy_brief` |
| A2 | 创意方向 | Big Idea 与 2–3 个创意方向 | `creative_concept` |
| A3 | 内容策划 | 选题、标题备选、内容结构 | `content_plan` |
| A4 | 文案创作 | 多版本文案（理性/感性/主推） | `copy_draft` |
| A5 | 编辑审校 | 结构 / 表达 / 品牌语气打分与修订 | `edited_copy` |
| A6 | 事实核查 | 主张核查、来源标注、风险分级（**可否决**） | `fact_check_report` |
| A7 | 品牌合规 | 广告法词库、行业规则、品牌一致性（**可否决**） | `compliance_report` |
| A8 | 视觉美术指导 | 视觉方向、配色、配图 Prompt、分镜 | `visual_brief` |
| A9 | 渠道运营 | 多平台适配、SEO、发布排期 | `channel_adaptation`、`publish_plan` |
| A10 | 效果分析 | 发布前预估 / 发布后复盘与 A/B 结论 | `effect_report` |
| A11 | 知识沉淀 | 归档最佳实践、跨任务 RAG 召回 | `knowledge_card`、`final_delivery` |

> A0 的编排职责由编排器承担，因此不在 `registry.AGENTS` 中，仅作为路线图展示。

---

## 质量与合规门禁

| 门禁 | 规则 | 不通过时 |
|---|---|---|
| A5 编辑 | 综合质量分 ≥ `QUALITY_THRESHOLD`（默认 75） | 退回 A4 重写 |
| A6 事实核查 | 高风险未修正 → 否决 | 升级人工裁决 |
| A7 品牌合规 | 阻断级违规 → 否决 | 升级人工裁决 |
| 返工上限 | 超过 `MAX_REVISIONS`（默认 2）仍未通过 | 升级人工裁决 |
| Turn Budget | 超过 `TURN_BUDGET`（默认 25）步 | 升级人工处理 |
| 人工审批 | 全部自动审核通过后 | 挂起等待批准 / 打回 / 驳回 |

**非中文市场的合规如实告知**：广告法词库只覆盖中文。选择非中文语言时，
A7 会明确声明「该语言尚无自动合规词库、需人工复核」，并把当地红线（如 FTC 披露要求）
写进 `risks` 且强制 `needs_human_review` —— **不假装检查过了**。

---

## 质量评估与回归门禁

### LLM-as-a-Judge 评估（与门禁解耦）

门禁回答「能不能发布」（否决式），评估回答「有多好、差在哪」（度量式）。
六维加权评分（0–100）：需求契合 0.25｜合规安全 0.20｜结构完整 0.15｜
品牌语气 0.15｜事实稳妥 0.15｜吸引力 0.10。

- **两种评估器**：`offline`（确定性规则，同一输入永远同一分数，用于回归对照）与
  `llm`（走网关，**任何异常静默回退 offline** 并标注）。
- **三种介入方式**：`off` / `advisory`（默认，只打分）/ `blocking`（低分参与返工判定）。
- **界面**：任务详情「评估报告」标签页；支持随时「规则评估 / 模型评估」重评。

### 黄金数据集（11 条固定用例 + 147 项断言）

「我把 Prompt 改好了」在没有固定输入时无法验证。黄金数据集把输入固定、输出压成标量：

- 覆盖**全部 7 个渠道 × 9 个行业**，外加边界用例（极简 Brief、强监管行业、英文多语言）。
- **判定两层互补**：
  - 分数层：质量分/评估分回退超容差、**返工轮次 +1**、监管用例首轮未被拦截 → 回退；
  - 内容层：品牌名缺失、关键词覆盖率不足、禁用表述、**广告法阻断级用语**、标题超渠道上限 → 回退。
- **这条门禁已经抓出三个真实缺陷**：标题压缩 off-by-one、交付标题未受渠道上限约束、
  合规扫描把免责声明误判为违规。

```bash
python scripts/golden_eval.py                  # 跑全量并对比基线
python scripts/golden_eval.py --coverage       # 看渠道覆盖矩阵
python scripts/golden_eval.py --update-baseline --force
```

---

## 可观测性与调用轨迹

一次任务一棵 span 树，回答「时间与花费分布在哪一层」：

```
graph.invoke#0                    ← 每次 LangGraph 调用
├─ A1.strategy_brief → llm.A1.strategy        ← 智能体 → 模型调用
├─ memory.retrieve                            ← RAG 召回
├─ gate.review                                ← 门禁整体
│   ├─ A5.edited_copy / A6.fact_check_report / A7.compliance_report
│   └─ judge.evaluate
└─ …
human.wait                        ← 人工等待（独立，不计入执行耗时）
graph.invoke#16                   ← 裁决后恢复
```

- **界面**：任务详情「调用轨迹」标签页（可折叠 span 树 + 瀑布图 + 耗时排行）。
- **可选 OTLP 导出**：配置 `OTLP_ENDPOINT`（如 `http://localhost:4318`）即转发到
  Jaeger/Tempo；**导出的 trace_id/span_id 与本地 JSON 完全一致**。
  不配置时**不加载 OTel SDK**，进程内追踪完整可用。

---

## 多语言与视频脚本

### 多语言（中文 / 英文 / 日文 / 韩文 / 西语）

- Brief 支持 `language` 字段；创作类智能体收到**本地化指令**（原生创作、当地表达惯例、度量格式、合规红线）。
- **标题按目标语言口径度量**：英文按**词**、中日韩按**字** —— 用错口径会让「标题合规」失去意义。
- **记忆库按语言分区**：英文资产不会被中文任务当语气基线复用（复用语言不对的资产比不复用更糟）。
- 商业推广的市场强制披露（`#ad` / `#PR` / `#광고` / `#publicidad`）写入合规提示。

### 视频脚本

抖音渠道天然产出**分镜脚本**（黄金 3 秒钩子 + 分镜 + 口播 + 字幕 + 9:16 画幅）。
通用「短视频脚本」交付物类型仍在路线图上（见 [项目状态](#项目状态)）。

---

## 部署

```bash
# 本地容器（含 Jaeger）
docker compose up -d --build

# 只要应用
docker compose up -d --build app

# Kubernetes
kubectl apply -f deploy/k8s.yaml
kubectl -n creator port-forward svc/creator 8787:8787
```

**部署前自检**（不需要 Docker daemon）：

```bash
python scripts/check_deploy.py
```

它静态核对：Dockerfile 的 `COPY` 路径是否存在且未被 `.dockerignore` 排除、
Compose/k8s 声明的环境变量是否真的被代码读取、探针路径是否等于免鉴权路径。
这类错误本来只有 `docker build` 或部署时才暴露。

**两点须知**：
- **数据在 `/data`，必须挂卷**，否则容器重建即丢任务与记忆库。
- **k8s 副本数固定为 1**：当前持久化是本地 JSON + SQLite，多副本会状态分裂。
  要横向扩展需先换存储层（见 `plan.md` §5.1）。

---

## 配置项

完整清单见 [`.env.example`](.env.example)。最常改的：

| 变量 | 默认 | 说明 |
|---|---|---|
| `PORT` | `8787` | 服务端口 |
| `LLM_PROVIDER` | `mock` | `mock`（离线，无需密钥）/ `openai`（任意兼容网关） |
| `OPENAI_BASE_URL` / `OPENAI_API_KEY` / `OPENAI_MODEL` | — | 模型接入 |
| `QUALITY_THRESHOLD` | `75` | 编辑质量分门禁 |
| `MAX_REVISIONS` | `2` | 最大返工轮次 |
| `TURN_BUDGET` | `25` | 单任务最大编排步数 |
| `AUTO_APPROVE` | `false` | 跳过人工审批（批量试跑） |
| `COST_BUDGET_USD` / `TOKEN_BUDGET` | `0.5` / `50000` | 成本与 token 熔断线 |
| `EMBEDDING_PROVIDER` / `EMBEDDING_WEIGHT` | `local` / `0.35` | 记忆库向量检索（`0` = 纯关键词） |
| `JUDGE_MODE` / `JUDGE_PROVIDER` | `advisory` / `offline` | 评估介入方式与评估器 |
| `PUBLISH_WEBHOOK_URL` | 空 | 发布投递目标；为空时「投递」等价「登记发布」 |
| `OTLP_ENDPOINT` | 空 | 追踪导出目标；为空则只做进程内追踪 |
| `CREATOR_API_TOKENS` | 空 | 访问令牌（为空不鉴权；**生产必设**） |
| `CREATOR_DATA_DIR` | `<项目根>/data` | 持久化目录（容器内为 `/data`） |

---

## REST / SSE 接口

所有接口挂在 `/api` 下；设置 `CREATOR_API_TOKENS` 后除 `/api/health` 外都需携带令牌。

| 分组 | 主要接口 |
|---|---|
| 基础 | `GET /api/health`（含 `checkpointer` 与 `otlp` 状态）、`GET /api/agents`、`GET /api/knowledge`（含语言画像）、`GET/PUT /api/settings` |
| 任务 | `GET/POST /api/tasks`、`GET/DELETE /api/tasks/{id}`、`POST /api/tasks/{id}/decide`、`GET /api/tasks/{id}/blackboard`、`GET /api/tasks/{id}/events`（SSE） |
| 评估 | `GET /api/evaluations`、`POST /api/tasks/{id}/evaluate` |
| 轨迹 | `GET /api/tasks/{id}/trace` |
| 回归 | `GET /api/golden`、`POST /api/golden/run`、`POST /api/golden/reset` |
| 发布 | `GET/POST /api/tasks/{id}/publish`、`POST /api/tasks/{id}/publish/dispatch`、`GET /api/publish/queue`、`POST /api/publish/tick`、`POST /api/tasks/{id}/feedback` |
| 记忆库 | `GET /api/memory`、`POST /api/memory/search`（按租户 + 语言隔离） |
| 指标 | `GET /api/metrics`（含成本 / 缓存 / 租约 / 评估 / 追踪） |

SSE 事件类型：`task.created`、`task.status`、`phase.enter`、`agent.start/progress/finish`、
`gate.decision`、`revision.requested`、`approval.required/decided`、`blackboard.write`、
`judge.scored`、`task.completed/failed`、`log`。

---

## 开发与验证

```bash
# 环境与能力自检（16 项，退出码即结论）
python scripts/doctor.py

# 黄金数据集回归（分数 + 内容级硬约束）
python scripts/golden_eval.py

# 端到端验收（真实服务 + 全部接口 + 租户隔离）
python scripts/smoke_api.py

# 快速契约核验（约 15 秒）
python scripts/verify_contracts.py

# 容器化清单核验（不需要 Docker daemon）
python scripts/check_deploy.py

# 并发正确性 / 性能基线
python scripts/stress_llm.py -n 8 -c 4

# 前端
npm run typecheck && npm run build
```

全部关卡已接入 [`.github/workflows/ci.yml`](.github/workflows/ci.yml)：
**无外部依赖、退出码即结论**，因此 PR 阶段就能拦住质量回归。

---

## 项目状态

### 迭代路线（对照 `plan.md`）

| 阶段 | 计划内容 | 状态 |
|---|---|---|
| MVP | A0/A1/A2/A4/A5/A6/A7 + 基础黑板 + 人工审批 | ✅ 完成（并超出：A3/A8/A9/A10/A11 也已实现） |
| v1.1 | A3 内容策划、A10 数据分析、MCP 工具链 | ✅ 完成（MCP 以**内置知识层**替代，无外部进程依赖） |
| v1.2 | A8 视觉、A9 渠道 SEO、多平台输出 | ✅ 完成 |
| v1.3 | A11 记忆知识库、自动复盘与模板沉淀 | ✅ 完成（含混合 RAG、过期下线、租户 + 语言隔离） |
| v2.0 | 多语言本地化 | ✅ 完成（中/英/日/韩/西，原生创作 + 字数口径 + 合规如实告知） |
| v2.0 | A/B 测试闭环 | ✅ 完成（回填 → A10 复盘 → A/B 结论） |
| v2.0 | 自动发布 | 🟡 平台无关的 webhook 投递 + 到期队列；平台私有授权由发布网关承接 |
| v2.0 | 视频脚本 | 🟡 抖音已产出分镜脚本；**独立「短视频脚本」交付物类型未实现** |
| v2.0 | 数字人 | ⬜ 未实现（需第三方数字人视频服务） |

### 横切能力

| 能力 | 状态 |
|---|---|
| API 鉴权 + 任务/记忆库租户隔离 | ✅ |
| LLM-as-a-Judge 评估流水线 | ✅ |
| 黄金数据集与回归门禁（11 用例 / 147 断言） | ✅ |
| CI 流水线（7 道关卡 + 前端 + 文档一致性） | ✅ |
| 调用轨迹 span 树 + OTLP 导出 | ✅ |
| 容器化与部署（镜像 + compose + k8s） | ✅ |
| 断点续跑健康度可见性 | ✅ |
| 跨进程 trace 传播 / 采样 | ⬜ |
| 横向扩展（多副本） | ⬜ 需先换共享存储 |
| 真实模型链路压测与 Prompt 调优 | ⬜ 脚本已就绪 |

### 实测指标（Mock 引擎）

| 指标 | 实测 |
|---|---|
| 端到端流程跑通率 | 100%（`smoke_api.py` / `stress_llm.py`） |
| 单篇生成时间 | 8 任务 / 并发 4：p50 5.98s、p99 6.57s |
| 平均质量分 | 93.0（返工 1 轮后收敛） |
| 黄金回归 | 11/11 持平，147 项断言 0 失败 |
| 自检 | 16 项全绿 |
| 并发正确性 | 租约冲突 0、活跃租约残留 0 |
| 内容级硬约束 | 品牌名 / 关键词 / 阻断用语 / 标题字数全部达标 |

---

## 需要人工协助的事项

以下是**必须由人来做**的事，代码无法替代。按优先级排列：

### P0 — 上线前必须完成

| # | 事项 | 为什么需要人 | 建议做法 |
|---|---|---|---|
| 1 | **验证 Docker 镜像构建与运行** | 开发机上 Docker daemon 未运行，镜像**未实机验证**；`check_deploy.py` 只能做静态核验 | 在装了 Docker 的机器执行 `docker build -t creator-agent-studio:latest .` 与 `docker compose up -d`，确认 `/data` 卷可写、Jaeger 能收到 trace |
| 2 | **设置生产访问令牌** | `CREATOR_API_TOKENS` 决定租户隔离边界；为空则完全不鉴权 | 生成强随机令牌，按 `acme:tok,beta:tok` 形式配置，放入 Secret 而非 ConfigMap |
| 3 | **真实模型网关跑通并定基线** | Mock 的延迟与成本不代表真实链路；`COST_BUDGET_USD` 需要真实数据校准 | `LLM_PROVIDER=openai ... python scripts/stress_llm.py -n 6 -c 2`，据结果调 Prompt 与预算 |
| 4 | **人工审核非中文市场的合规表述** | 系统只有中文广告法词库；英文/日文/韩文/西语的合规红线已写入提示词但**未经法规校验** | 由目标市场的法务或合规专家复核首批产出，必要时补当地词库 |

### P1 — 影响可用性

| # | 事项 | 为什么需要人 |
|---|---|---|
| 5 | **提供品牌 VI 与产品资料** | 品牌语气、禁用词、Logo 规范、产品参数与可引用证据目前是通用示例；真实效果取决于喂给系统的品牌资产 |
| 6 | **配置发布网关** | `PUBLISH_WEBHOOK_URL` 需指向能调用各平台开放接口的服务（n8n / Zapier / 自建），平台授权与限流必须由该网关承接 |
| 7 | **补充黄金数据集的行业用例** | 现有 11 条覆盖渠道与边界，但真实业务的高频选题应由业务方提供 |
| 8 | **确认行业合规词库** | 医疗健康 / 金融 / 教育等强监管行业的规则需法务确认（当前为示例词库） |

### P2 — 增强项

| # | 事项 | 为什么需要人 |
|---|---|---|
| 9 | **确定是否需要视频脚本交付物与数字人** | 两者需要明确产品形态；数字人还需选定第三方视频生成服务并接入 |
| 10 | **决定横向扩展方案** | 多副本需先把共享黑板与检查点换成 PostgreSQL + Redis，属架构决策 |
| 11 | **接入 OTel Collector / Jaeger 生产实例** | 本地用 compose 里的 all-in-one 即可；生产需要持久化存储与采样策略 |
| 12 | **建立人工抽检机制** | 评估与门禁能拦住大部分问题，但品牌调性与创意质量最终仍需人判断 |

---

## 文档导航

| 文档 | 内容 |
|---|---|
| [`creator.md`](creator.md) | **架构设计与智能体定义**：智能体职责、协作流程、状态机、数据模型、门禁与权限设计、实施状态对照 |
| [`plan.md`](plan.md) | **技术选型与开发排期**：框架对比、五层架构、测试策略、4 周 MVP 排期、验收指标、as-built 对照 |
| [`MEMORY.md`](MEMORY.md) | **开发进度与踩坑记录**：每轮变更、关键设计决策、62 条踩坑与验证结果 |
| [`USER_GUIDE.md`](USER_GUIDE.md) | **用户操作手册**：启动、界面导览、实操流程、配置表、接口速查、FAQ |

---

## 许可

见 [LICENSE](LICENSE)。
