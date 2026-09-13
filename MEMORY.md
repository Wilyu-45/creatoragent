# 开发进度记忆文档 · Creator Agent Studio

> 本文件记录本项目（多智能体 AI 内容创作台）的开发进度、关键决策与验证结果。
> 每次开发后请追加/更新本文件，作为跨会话的「项目记忆」。
> 设计依据：`creator.md`（架构与智能体定义）、`plan.md`（实施计划）。

---

## 1. 项目定位

编排式多智能体协作创作台：把一个营销 Brief，经「策略 → 创意 → 策划 → 文案 → 审校 → 事实核查 → 合规 → 人工审批」的流水线，
产出可直接使用的营销内容，并保证质量门禁与合规红线。

核心架构要素：
- **共享黑板（Blackboard）**：产物 / 事实 / 活动 / 评审的统一读写区，带版本号与租约意图。
- **编排器（Orchestrator / A0）**：拆解任务卡、调度智能体、驱动门禁状态机、控制 Turn Budget。
- **阶段门禁（Gatekeeper）**：`pass / revise / reject / escalate`，含最大返工轮次与升级人工。
- **否决权**：A6 事实核查、A7 品牌合规可一票否决。
- **人工在环（HITL）**：升级裁决 + 发布前审批，用 LangGraph `interrupt()` 挂起等待。
- **可观测性**：事件总线 + SSE 实时推送，前端可视化流水线与时间线。
- **两级容错**：指数退避重试（3 次）→ 降级到内置离线引擎。

---

## 2. 技术栈与运行方式

> **2026-09-12 完成 Python 全量重写**：后端由 Node/TypeScript 迁移为 Python，前端 React 代码**零改动**。

| 项 | 选型 | 说明 |
| --- | --- | --- |
| 后端运行时 | Python 3.12（conda env `multi-agent-creator`） | 需 `PYTHONNOUSERSITE=1`（见踩坑 1） |
| 后端框架 | FastAPI + uvicorn | REST + SSE（`StreamingResponse`） |
| 编排框架 | LangGraph 1.2.11 | `StateGraph` + 条件边门禁环 + `interrupt()` + checkpointer |
| 模型抽象 | OpenAI 兼容 Provider（httpx）+ 内置 Mock | 无密钥也可完整跑通 |
| 前端 | React 19 + TypeScript + Vite 7 | **未改动**，仍由后端静态托管 `dist/` |
| 持久化 | JSON 文件 + SQLite 检查点（`data/`） | `tasks/`、`blackboard.json`、`settings.json`、`checkpoints.sqlite` |

常用命令：
```bash
# 后端（默认 8787）
conda run -n multi-agent-creator python -m app.main
# 环境与智能体链路自检
conda run -n multi-agent-creator python scripts/doctor.py
# 端到端验收（真实 uvicorn + 全部 /api 契约）
conda run -n multi-agent-creator python scripts/smoke_api.py
# 压测 / Prompt 调优基线（Mock 校验并发正确性；openai 量真实延迟与成本）
conda run -n multi-agent-creator python scripts/stress_llm.py -n 12 -c 4
# 前端（构建后由后端托管）
npm run build        # → dist/
npm run dev:web      # 仅前端，/api 代理到 8787
```

环境变量：
- `CREATOR_DATA_DIR`：持久化根目录，默认 `<项目根>/data`（测试/多实例部署用，避免互相污染）。
- `PORT` / LLM 相关配置见 `.env.example`。
- 本轮新增：`EMBEDDING_PROVIDER/DIM/WEIGHT/BASE_URL/API_KEY/MODEL`（记忆库向量检索）、
  `PUBLISH_WEBHOOK_URL/PUBLISH_RETRY/PUBLISH_AUTO_DISPATCH/PUBLISH_TICK_SECONDS`（发布投递）、
  `CREATOR_API_TOKENS`（开启后 `/api/*` 需携带 Token，并映射到租户做数据隔离）、
  `JUDGE_MODE/JUDGE_PROVIDER/JUDGE_MODEL/JUDGE_PASS_THRESHOLD/JUDGE_WEIGHT`（LLM-as-a-Judge 评估）。

---

## 3. 目录结构

```
app/
  config.py              # 运行时配置 + .env 加载（含 CREATOR_DATA_DIR 覆盖）
  logger.py
  main.py                # uvicorn 启动入口
  server.py              # FastAPI 装配：/api 路由 + /dist 静态托管 + lifespan
  core/
    types.py             # 全量数据契约（Brief/TaskRecord/Artifact/Event/Scorecard…）
    events.py            # 事件总线（publish/subscribe/replay/history）
    blackboard.py        # 共享黑板
    store.py             # 任务持久化（重启把 running 标记为中断）
    gatekeeper.py        # 门禁决策 + 质量记分卡
    publisher.py         # 发布时段解析 → 到期时间 → webhook 投递（含退避重试）
    orchestrator.py      # LangGraph 编排图（编译 + 驱动 + 断点续跑 + 投递队列）
    clock.py / util.py
  llm/
    engine.py            # chat() 统一入口：重试 + 降级
    openai_provider.py   # OpenAI 兼容 Provider
    mock.py              # 内置离线创作引擎（规则+知识库）
    json_utils.py / types.py
  knowledge/
    industry.py          # 渠道规范、SEO 模式、建议发布时段、行业画像
    compliance.py        # 广告法词库扫描、品牌语气检查、自动改写
    visual.py            # 视觉风格库（色彩/构图/光线/Prompt 片段 + 渠道画幅）
    memory.py            # A11 记忆库：卡片持久化 + 关键词/向量混合检索（RAG）+ 租户分区
    embedding.py         # 文本向量化：本地确定性 hashing embedding + 可选 OpenAI /embeddings
  agents/
    base.py              # 统一上下文/提示词/产物与结果构造
    a1_strategy.py … a11_memory.py
    registry.py          # 已实现 AGENTS（A1-A11）+ PLANNED_AGENTS（仅 A0）
  api/routes.py          # REST + SSE 路由（契约与 TS 版逐字段对齐）
scripts/
  doctor.py              # 环境 + 能力自检（16 项：含租户隔离、检查点后端、评估器、回归判定器、传播/采样、数字人样例）
  golden_eval.py         # 黄金数据集回归（固定 11 条用例 vs 基线；--update-baseline / --coverage）
  smoke_api.py           # 端到端验收（REST/SSE/错误分支/持久化/评估/独立实例的鉴权与租户隔离）
  verify_contracts.py    # 快速契约核验（约 20s：检查点后端 / 评估 / 租户视角 / 追踪 / 传播采样 / 数字人样例）
  stress_llm.py          # 压测与 Prompt 调优基线（并发正确性 / 延迟分位 / 成本缓存 / 租约残留）
golden/                  # 黄金数据集（随代码版本化的测试资产，不在 data/ 下）
  briefs/*.json          # 11 条固定用例，覆盖全部 7 个渠道
  baseline.json          # 基线分数 + 生成时的 rubric / engine
.github/workflows/ci.yml # CI：自检 / 质量回归 / 契约 / 端到端 / 并发 / 前端 / 文档一致性
.doctor-data/            # 自检与冒烟脚本的临时数据目录（已 gitignore，可安全删除）
src/                     # 前端；契约类型自持于 src/lib/types.ts
  components/MemoryPanel.tsx   # 设置抽屉「记忆库」标签页：统计 + 租户 + 检索召回 + 卡片列表
  components/PublishPanel.tsx  # 发布登记 + 效果回填（触发 A10 复盘）
  components/JudgePanel.tsx    # 「评估报告」标签页：六维评分 + 评估历史 + 按需评估
  components/GoldenPanel.tsx   # 设置抽屉「回归测试」标签页：跑回归 + 基线对比 + 覆盖矩阵
```

### 核心层新增文件（第五轮）

| 文件 | 职责 |
| --- | --- |
| `app/core/judge.py` | LLM-as-a-Judge 评估器：六维评分、离线规则版 + 模型版（失败回退）、评分口径版本 |
| `app/core/evaluations.py` | 评估历史存储（`data/evaluations.json`）：记录 / 查询 / 聚合统计 |

### 黄金数据集与回归门禁（第六轮）

| 文件 | 职责 |
| --- | --- |
| `golden/briefs/*.json` | **11 条固定用例**，覆盖全部 7 个渠道 × 9 个行业 + 边界用例 |
| `golden/baseline.json` | 基线分数（含生成时的 `rubric` / `engine`），随代码版本化 |
| `app/core/golden.py` | 数据集加载、覆盖矩阵、基线读写、`compare()` 回归判定 |
| `app/core/golden_runner.py` | 执行器：驱动真实流水线 → 压缩为标量 → 后台任务状态机 |
| `scripts/golden_eval.py` | CLI：跑回归 / 更新基线 / 查看覆盖，退出码即结论 |
| `.github/workflows/ci.yml` | CI：自检 / 质量回归 / 契约 / 端到端 / 并发 / 前端 / 文档一致性 |

### 调用轨迹（第七轮）

| 文件 | 职责 |
| --- | --- |
| `app/core/tracing.py` | OTel 数据模型的进程内实现：trace/span 树、thread-local span 栈、耗时占比、OTel 形状导出 |
| `data/traces/<task_id>.json` | 每个任务的 trace 落盘（含 `otel[]` 段，可直接喂 OTLP collector） |
| `src/components/TracePanel.tsx` | 「调用轨迹」标签页：可折叠 span 树 + 瀑布图 + 耗时排行 |

### OTLP 导出与容器化（第八轮）

| 文件 | 职责 |
| --- | --- |
| `app/core/otel.py` | 把进程内 span 转发到真实 OTel（OTLP/HTTP）：手工构造 `ReadableSpan` 保证 id 一致；默认不加载 SDK |
| `Dockerfile` / `.dockerignore` | 多阶段镜像：Node 构建前端 → Python 运行时不带构建工具链 |
| `docker-compose.yml` | 应用 + Jaeger 一键起；命名卷持久化 `/data` |
| `deploy/k8s.yaml` | Namespace / ConfigMap / Secret / PVC / Deployment / Service / Ingress |
| `scripts/check_deploy.py` | **不需要 Docker daemon** 的清单一致性核验（COPY 路径 / 环境变量 / 探针免鉴权） |

> 为什么数据集放 `golden/` 而不是 `data/`：`data/` 是运行期产物（已 gitignore），
> 而黄金数据集是**随代码版本化的测试资产** —— 它必须能被 review、被 diff、被追责，
> 否则「改了基线让 CI 变绿」就成了一种无人察觉的作弊。

---

## 4. 已完成内容

### 4.1 后端 · Python 重写（已完成）

- **数据契约**：`Brief`、`TaskPacket`、`Artifact`(13 种类型)、`AgentResult`、`AgentEvent`、`QualityScorecard`、`PipelineNode`、`A2AMessage` 等，Pydantic 模型逐字段对齐 TS。
- **共享黑板**：产物 / 事实 / 意图 / 活动 / 评审五类实体，`next_version` 保证版本单调递增。
- **事件总线**：内存事件流，支持 `replay(since)` 供 SSE 断线续传。
- **门禁状态机**：A5 质量分阈值、A6/A7 否决、最大返工轮次 → 升级人工（escalate）。
- **LangGraph 编排图**：
  `START → A0 → A1 → A2 → A3 → A4 → review ─pass→ A8 → A9 → A10 → A11 → approval ─delivered→ delivery → END`
  　　　　　　　　　　　　　　　　　　　　`├revise→ revise → A4`
  　　　　　　　　　　　　　　　　　　　　`└escalate→ escalation ─{pass→A8 / revise→A4 / rejected→END}`
- **11 个智能体**：A1 策略、A2 创意、A3 策划、A4 文案、A5 编辑、A6 核查、A7 合规、
  A8 视觉美术指导、A9 渠道运营与 SEO、A10 效果分析、A11 记忆与知识库；
  仅 A0 总控在 `registry.PLANNED_AGENTS` 中作为路线图展示（其编排职责已由编排器承担）。
- **知识层**：渠道规范、行业洞察库、广告法词库（绝对化用语/医疗功效/收益承诺）+ 品牌语气检查 + 自动改写 +
  视觉风格库（色彩/构图/光线/Prompt 片段 + 渠道画幅）、SEO 模式与标题限制、建议发布时段。
- **投放侧资产进交付件**：A8/A9/A11 的产物（视觉指导 / 渠道适配 / 知识沉淀卡片）会一并装配进
  `final_delivery`，交付件含 `channels`、`visual`、`knowledge_cards` 三段，可直接交运营执行。
- **记忆库（A11 的 RAG 闭环，对应 creator.md「A11 为其他智能体提供检索」）**：
  `app/knowledge/memory.py` 把 A11 沉淀的知识卡片与模板固化到 `data/memory.json`（跨任务存活，
  `MAX_CARDS=500` 超限淘汰最旧），并提供
  `retrieve(query, brand=, channel=, industry=, top_k=, exclude_task=)` 打分召回——
  中文 2-gram + 英文/数字分词算文本相关度，再叠加同品牌（0.25）/同渠道（0.1）/同行业（0.05）加成，
  低于 `MIN_SCORE=0.2` 不返回，避免「什么都召回一点」的虚假命中。
  编排层在 **A1/A2/A4 执行前**统一检索并注入 `ctx.memory`（`Orchestrator._memory_hits()`），
  智能体只需消费，检索口径只有一处实现；A11 自身把召回结果写进产物 `content["recalled"]`。
  卡片按 `sha1(kind|title|content)` 去重，因此重试或多轮返工不会重复入库。
- **模型层**：真实 OpenAI 兼容 Provider（重试 + 超时）与离线 Mock 引擎，二者产出同一 JSON 契约。
- **断点续跑与归档回收**：`SqliteSaver` 把每步状态落到 `data/checkpoints.sqlite`；进程重启后
  `resume_interrupted()` 对中断任务用 `Command(resume=…)` 续跑（实测：新建 `Orchestrator` 实例可接管并完成交付）。
  `DELETE /api/tasks/:id` 会调用 `Orchestrator.purge_checkpoints()` 按 `thread_id`
  回收该任务的 `writes` / `checkpoints` 行（实测回收 55 行），避免 SQLite 只增不减。
- **REST/SSE**：`/health`、`/agents`、`/knowledge`、`/memory`(GET)、`/memory/search`(POST)、
  `/settings`(GET/PUT)、`/tasks`(GET/POST)、`/tasks/:id`(GET/DELETE)、`/tasks/:id/decide`、
  `/tasks/:id/events`(SSE)、`/metrics`、`/tasks/:id/blackboard`。

### 4.1b 后端 · 第三轮加固（2026-09-13，已完成）

- **黑板 Intent 租约闭环**：`blackboard.acquire_intent(task_id, agent_id, direction, ttl_ms)` 返回
  `(IntentEntry|None, "claimed"|"renewed"|"conflict")`，`release_task_intents(task_id)` 在任务终态回收；
  编排层 `_acquire_lease()` 冲突重试并累计 `task.intent_conflicts`，`_run_agent_step` 的 `finally` 释放租约。
- **成本核算 + 熔断 + 缓存**：`app/llm/pricing.py`（迁出定价）、`app/llm/cost.py`（一任务一账本
  `cost_guard.begin/should_cut/charge/ledger/end/metrics`，超 `cost_budget_usd` 或 `token_budget` 即熔断到离线引擎）、
  `app/llm/cache.py`（`response_cache`，键含 provider/model/temperature/max_tokens/messages，容量 256、TTL 1h）；
  `llm/engine.py` 的 `chat()` 前置熔断判断 + 缓存命中复用 + 成功后计费与写缓存；`LLMResponse.cached` 透传。
- **知识过期下线与新鲜度**：`memory.py` 增加 `MAX_AGE_DAYS=180`（超期自动下线）、`FRESH_DAYS=30`、
  `STALE_DAYS=120`；`MemoryHit` 带 `age_days/fresh`，同分优先新鲜；`stats()` 增加新鲜度统计。
- **发布排期与效果回填复盘闭环**：编排新增 `_node_publish/_stage_publish`（审批通过后生成 `publish_plan`，
  阶段 PUBLISHED）、`publish_schedule()`、`mark_published()`、`record_feedback()`；A10 上游注入 `actuals`
  时从「发布前预估」切换到「发布后复盘」（`REVIEW_SCHEMA` / `normalize_review` / `_run_review`）；
  `mock.py` 新增 `A10.review`。
- **接口扩展**：`GET/POST /api/tasks/:id/publish`、`POST /api/tasks/:id/feedback`；
  `/api/metrics` 的 `system` 新增 `cost` / `cache` / `leases`；`/api/settings` 支持
  `costBudgetUsd` / `tokenBudget` / `llmCache`。
- **配置**：`RuntimeConfig` 增加 `cost_budget_usd=0.5`、`token_budget=50000`、`llm_cache=True`
  （环境变量 `COST_BUDGET_USD` / `TOKEN_BUDGET` / `LLM_CACHE`）。
- **清理旧实现**：`git rm -r server/`（TypeScript 后端目录），`tsconfig.json` 的 `include` 去掉 `"server"`，
  `package.json` 清空 `dependencies`。
- **契约扩展**：`ArtifactType` 增加 `publish_plan`；`TokenUsage` 增加 `calls/cached/cut_off`；
  `TaskRecord` 增加 `intent_conflicts/published_at`；`AgentMetrics` 增加 `cached/cut_off`。
- **验收同步**：`smoke_api.py` 增加发布排期 → 登记发布 → 效果回填复盘断言，以及
  `/api/metrics` 的 cost/cache/leases 断言（含 `leases['active'] == 0`）。

### 4.1c 后端 · 第四轮：向量检索 + 自动投递 + 鉴权租户（2026-09-13，已完成）

- **记忆库向量检索（补齐「换向量检索」待办，对应 plan.md 2.2.3「RAG Server」）**：
  - 新增 `app/knowledge/embedding.py`：`tokenize`（与关键词口径一致的中文 2-gram + 英文/数字分词）、
    `hashing_vector`（blake2b 符号哈希 + L2 归一化，确定性、零依赖）、`cosine`、
    `embed_texts`（`provider=local` 走本地；`provider=openai` 走 `/embeddings` 并**任何异常回退本地**）、`describe`。
  - `memory.py` 检索改为**混合打分**：`score = keyword*(1-weight) + vector_sim*weight`，
    默认 `weight=0.35`（`weight=0` 即退化为旧行为）；`MemoryHit` 新增 `vector`/`vector_score`，
    命中理由新增「语义相近」；向量按卡片懒计算并缓存，提供方/模型/维度变更时整体失效重算。
  - `stats()` 新增 `embedding`（提供方/模型/维度/权重）与 `vector_indexed`（已建索引条数）。
- **发布自动投递（plan.md v2.0「自动发布」的落地形态）**：
  - 新增 `app/core/publisher.py`：`parse_slot_time`（解析 `07:30-08:30 通勤时段`，兼容全角冒号）、
    `compute_due_at`（**今天已过则顺延到明天**，避免永远不触发）、`is_due`、
    `dispatch(url, payload, retry)`（指数退避重试；未配置 webhook 直接返回失败供调用方退化）。
  - 排期项新增字段：`due_at` / `dispatch_status` / `attempts` / `last_error`（与人工「登记发布」共用状态机）。
  - 编排新增 `dispatch_publish(task_id, channel, force)`、`publish_queue(due_only, tenant)`、
    `dispatch_due(tenant)`；未配置 webhook 时状态记为 `skipped` 并**等价比「登记发布」**，保证离线语义完整。
  - `server.py` lifespan 在 `PUBLISH_AUTO_DISPATCH=true` 时启动后台巡检（`PUBLISH_TICK_SECONDS`，默认 60s），
    默认关闭以保持进程零副作用。
- **API 鉴权与租户隔离（plan.md D17「安全加固：API 鉴权、数据隔离」）**：
  - `config.py` 新增 `_parse_api_tokens` / `API_TOKENS` / `auth_enabled()`，支持 `tok1,tok2`（均归 `default`）
    与 `acme:tok1,beta:tok2`（显式指定租户）。
  - `server._install_auth()`：**未配置 token 时不挂载中间件**（保持单机零配置）；配置后 `/api/*`
    （`/api/health` 免鉴权）必须携带 `Authorization: Bearer <token>` 或 `X-API-Token`，
    SSE 允许用 `?token=`（EventSource 无法自定义头）；Token 解析出的租户写入 `request.state.tenant`。
  - `TaskRecord.tenant` 落库；`routes.py` 以 `_tenant_of / _require_task / _tenant_tasks` 统一做
    列表过滤与详情越权 404，覆盖 tasks/decide/publish/feedback/blackboard/events/metrics 全部接口。
- **压测 / Prompt 调优基线**：新增 `scripts/stress_llm.py`（`-n 任务数 -c 并发度 --provider`），
  输出完成率、端到端 p50/p95/p99、返工轮次与首轮通过率、质量分、成本/缓存、**租约冲突与活跃租约残留（应归零）**；
  Mock 模式可作 CI 并发正确性回归，`openai` 模式量真实延迟与成本。
- **契约与配置**：`RuntimeConfig` 新增 `embedding` / `publish` 两组设置（camelCase 扁平 patch 写入，
  非法值静默跳过），`public_config()` 新增 `authRequired`；settings PUT 支持 `embedding*` / `publish*` 字段。
- **前端同步**：`src/lib/api.ts` 新增 `X-API-Token` 头（401 提示）、SSE 带 `&token=`、
  `dispatchPublish / publishQueue / tickPublish` 三个调用与 `PublishQueueItem` 等视图类型；
  `PublishPanel` 增加「到期时间」列、投递/登记双按钮、投递状态 Chip 与「全部投递」；
  `SettingsDrawer` 新增向量检索、发布投递、访问令牌三组；`MemoryPanel` 增加向量检索 chips 与命中「语义」分。
- **验收同步**：`doctor.py` 新增 `check_embedding / check_publisher / check_auth`（`main` 汇总五项检查）；
  `smoke_api.py` 增加 settings（embedding/publish/authRequired）、向量检索字段、dispatch → queue 断言。

### 4.1d 后端 · 第五轮：租户记忆库 + LLM-as-a-Judge（2026-09-13，已完成）

- **记忆库租户隔离（补齐 creator.md A11「管理权限」与 plan.md D17 的最后一块拼图）**：
  - `MemoryCard` 新增 `tenant` 字段（旧数据缺字段时按 `default` 读取，**无需迁移脚本**）；
    去重键从 `sha1(kind|title|content)` 改为 `sha1(tenant|kind|title|content)`——
    不同租户的同名知识各自存活，同租户内仍然幂等。
  - `MemoryStore.remember/retrieve/list_cards/stats` 全部新增 `tenant` 参数；
    `_evict` 改为**按租户计量**（容量是「每租户 500 条」，否则先入库的租户会挤掉后来者的名额）。
  - `AgentRunContext` 新增 `tenant` 字段（由编排器从 `TaskRecord.tenant` 注入），
    `A11` 与编排层 `_memory_hits()` 的检索/写入都带租户；
    `MemoryHit.to_dict()` 与提示词渲染（多租户时标注归属租户）同步带上租户。
  - API：`GET /api/memory`、`POST /api/memory/search` 按 `request.state.tenant` 过滤；
    `stats()` 新增 `tenant`/`tenants`/`global_total`（仅计数，不泄露内容）。
- **LLM-as-a-Judge 评估流水线（plan.md 4.3 D14 / 2.5「黄金数据集评测」）**：
  - 新增 `app/core/judge.py`：六维评分（**brief_fit 0.25｜compliance 0.20｜structure 0.15｜
    brand_voice 0.15｜fact_safety 0.15｜appeal 0.10**）加权汇总为 0–100；
    合规维度直接复用 A7 的 `scan_compliance`（同一份词库，避免两套口径互相打脸）。
  - **双评估器**：`offline`（确定性规则，零依赖可离线、同一输入永远同一分数）+ `llm`
    （走当前 OpenAI 兼容网关，**任何异常静默回退 offline** 并标注 `fallback`/`fallback_reason`）。
  - 新增 `app/core/evaluations.py`：评估历史落 `data/evaluations.json`
    （单任务最多 20 条、全局 500 条，`record/list/latest/stats`）；
    `build_record()` 落盘前把总分统一到 1 位小数。
  - 编排：`_judge_step()` 在**门禁时刻**（`kind=final`）与**交付前**各评估一次，
    并发 `judge.scored` 事件；`_apply_judge_to_scorecard()` 按 `judge.weight`（默认 0.2）
    把评估分混入 `scorecard.overall`（`weight=0` 与历史行为逐位一致，可回滚）。
  - 门禁：`decide_gate(judge=, judge_mode=)` 支持 `off / advisory（默认，只记录不改裁决）/ blocking`
    （`reject` 升级人工、`review` 计入返工理由），门禁事件 `payload.judge` 带上完整报告。
  - 接口：`GET /api/evaluations`（历史 + 聚合 + 当前口径）、`POST /api/tasks/{id}/evaluate`
    （按需评估「评分台」，改完 Prompt 不必重跑流水线）、`/api/metrics.system.judge` 聚合、
    `/api/settings` 新增 `judgeMode/judgeProvider/judgeModel/judgePassThreshold/judgeWeight`。
- **断点续跑健康度可见性（本轮自检拦下的真实缺陷）**：
  - 自检原本用 `tempfile.mkdtemp`（`0o700` 目录）当数据目录，在本机沙箱下**写入被拒**，
    于是记忆库落盘静默失败、`SqliteSaver` 退回内存检查点，而自检依然全绿——
    「断点续跑」这条能力从未被真正覆盖过。
  - 新增 `app/core/util.make_temp_dir()`（逐级 `mkdir` + 随机后缀，避开 `0o700` ACL），
    `doctor.py` / `smoke_api.py` / `stress_llm.py` 全部改用它，数据目录统一落在
    `<项目根>/.doctor-data/`（已 gitignore）。
  - `Orchestrator` 暴露 `checkpointer_kind` / `checkpointer_error`，经 `/api/health.checkpointer`
    透出；前端顶栏在退化为内存实现时显式告警「断点续跑不可用」。
- **契约扩展**：`AgentEventType` 新增 `judge.scored`（前后端 + `EVENT_ICON` 同步）；
  `MemoryCard`/`MemoryHit` 新增 `tenant`；`MemoryView.stats` 新增 `tenant/tenants/global_total`；
  `RuntimeConfig` 新增 `judge` 设置组；`HealthView` 新增 `checkpointer`。
- **前端同步**：新增 `src/components/JudgePanel.tsx`（六维得分条 + 证据 + 评估历史 + 全库聚合 +
  「规则评估 / 模型评估」双按钮）→ App 新增「评估报告」标签页；任务头部新增最近评估分 Chip；
  `MetricsPanel` 新增「质量评估」区块；`SettingsDrawer` 新增评估设置组；
  `MemoryPanel` 新增租户 chips 与卡片「租户」列；`Topbar` 新增断点续跑告警。
- **验收同步**：`doctor.py` 扩到**八项检查**（新增 `check_memory_tenant / check_checkpointer / check_judge`）；
  `smoke_api.py` 新增评估流水线断言、`evaluations.json` 持久化断言，并**另起一个带
  `CREATOR_API_TOKENS` 的实例**验证 401 / 任务越权 404 / 记忆库不串租户 / 按租户统计。

### 4.1e 后端 · 第六轮：黄金数据集 + 回归门禁 + CI（2026-09-13，已完成）

- **补齐 mock 引擎的评估路径（先修掉上一轮留下的缺口）**：
  `GENERATORS` 新增 `"judge.evaluate": generate_judge`。此前默认 `provider=mock` 下
  `judge_with_llm()` 必然抛错并回退 —— 于是 `_normalize_llm_axes()` 那条**成功分支**
  在离线环境下永远跑不到，而它恰恰最容易因字段名漂移而坏。现在用规则评估器的真实
  打分作为「模型回答」，成功分支也纳入离线可测范围（分数来源仍是 `judge_offline`，
  不是第二个口径）。
- **黄金数据集（plan.md D13）**：
  - `golden/briefs/*.json` —— **10 条固定用例**，覆盖全部 **7 个渠道 × 9 个行业**，
    另含两个刻意设计的边界用例：`xiaohongshu_minimal_brief`（无关键词/约束/交付物，
    验证缺字段不崩、且「无关键词」在评估器里按满分计而不是误判 0 分）与
    `ecommerce_medical_strict`（强监管，验证合规门禁**真的拦得住**）。
  - `golden/baseline.json` —— 基线，逐用例记录质量分 / 评估分（门禁时刻 + 交付前）/
    返工轮次 / 产物数 / 事实准确率 / 品牌一致性 / 合规裁决序列 / 首轮是否被拦，
    并绑定生成时的 `rubric` 与 `engine`（口径或引擎不同则拒绝直接比较）。
  - `app/core/golden.py` —— `load_dataset / coverage / load_baseline / save_baseline / compare`。
  - `app/core/golden_runner.py` —— 执行器（驱动真实流水线 → 压缩为标量）+ 后台任务状态机
    `start_background_run / state / reset_state`。
  - `scripts/golden_eval.py` —— CLI：跑回归 / `--update-baseline` / `--coverage` /
    `--tolerance` / `-n` / `--case`，退出码即结论。
  - 接口：`GET /api/golden`（用例 + 基线 + 覆盖 + 运行状态 + 对比结论）、
    `POST /api/golden/run`（后台启动，立即 202）、`POST /api/golden/reset`。
- **回归判定规则**：分数回退超容差（默认 ±3）→ `regressed`；**返工轮次 +1 → `regressed`**
  （更慢更贵，即使总分没掉）；**监管用例首轮合规不再被拦截 → `regressed`**（门禁强度下降）；
  运行失败 / 全量运行缺用例 → 不通过；容差内波动 → 持平（不把噪声当回归）。
- **CI 流水线（plan.md D18）**：`.github/workflows/ci.yml` 三组任务 ——
  后端（doctor → golden → verify_contracts → smoke → stress）、前端（typecheck + build）、
  文档一致性（关键章节与接口速查是否仍被提及）。失败时上传 `.doctor-data/**/server.log` 便于定位。
  之所以能全量做成 CI，是因为所有脚本都是「无外部依赖 + 退出码即结论」（默认离线引擎，无需密钥）。
- **自检扩展**：`doctor.py` 新增 `check_golden()`，**逐条构造偏差**确认判定器真的会判回归
  （9 个子断言：一致通过 / 质量分回退 / 评估分回退 / 返工增加 / 容差内持平 /
  全量缺失 / 部分运行缺失 / 运行失败 / 监管未拦截）。
- **前端同步**：新增 `src/components/GoldenPanel.tsx` → 设置抽屉「回归测试」标签页
  （跑全量 / 快速跑 3 条 / 清除结果、进度条、基线对比表、覆盖矩阵、用例清单）；
  `api.ts` 新增 `golden / runGolden / resetGolden` 与完整视图类型。

### 4.1f 后端 · 第七轮：调用轨迹与分布式追踪（2026-09-13，已完成）

- **`app/core/tracing.py`（新增）**：**OTel 数据模型的进程内实现** ——
  W3C 格式的 `trace_id`（32 位）/ `span_id`（16 位）、`parent_span_id`、`kind`、
  `attributes`、`status`、嵌套 span、self-time 占比计算；
  `Tracer` 用 thread-local 维护 span 栈（编排在工作线程、HTTP 在事件循环，不能假设单线程），
  按任务存 trace 并在终态落盘为 `data/traces/<task_id>.json`（含 `otel[]` 段）。
  单任务 span 上限 600、进程内 trace 上限 200，防异常环路吃内存。
- **埋点位置**：
  | span | 层级 | 关键属性 |
  |---|---|---|
  | `graph.invoke#<turn>` | 根（每次 LangGraph 调用） | `graph.interrupted` |
  | `human.wait` | 独立根 | 与执行链并列，避免把「人在犹豫」算成系统耗时 |
  | `A<n>.<produces>` | 智能体一次执行 | `agent.gate_result` / `confidence` / `risks` |
  | `gate.review` | 门禁整体 | `gate.verdict` / `blocking` / `overall_score` |
  | `judge.evaluate` | 评估 | `judge.total` / `verdict` / `used_llm` / `fallback` |
  | `llm.<purpose>` | client | provider / model / token / 成本 / 缓存命中 / 降级原因 |
  | `memory.retrieve` | RAG 召回 | `recall.hits` |
  | `publish.dispatch` | client | `publish.channel` / `result` |
- **事件自动关联 trace**：事件总线在 `publish()` 里统一注入当前的 `trace_id` / `span_id`
  （`AgentEvent` 新增这两个字段），因此排查时不必靠时间戳猜「这条事件属于哪个 span」。
- **接口**：`GET /api/tasks/{id}/trace`（span 树 + 按 span 名的耗时聚合 + 导出信息）；
  `/api/metrics.system.tracing`（跨任务的 span 耗时排行、错误数、导出路径）；
  `DELETE /api/tasks/{id}` 一并回收 trace。
- **前端同步**：新增 `src/components/TracePanel.tsx` → 任务详情「调用轨迹」标签页
  （可折叠 span 树、瀑布图、span 属性内联显示、耗时排行）；
  `MetricsPanel` 新增「调用轨迹」区块（已追踪任务 / span 错误数 / 模型调用耗时占比 + 排行）。
- **验收同步**：`doctor.py` 新增 `check_tracing()`（第 13 项，断言 span 树**不是平铺列表**、
  llm span 正确嵌套、属性齐全、OTel 形状导出）；
  `verify_contracts.py` 新增 14 项 trace 契约断言；
  `smoke_api.py` 新增 trace 端点断言（含跨租户 404 与事件 trace 一致性）。

### 4.1g 后端 · 第八轮：OTLP 真实导出 + 内容级期望 + 容器化（2026-09-13，已完成）

- **OTLP 真实导出（把 §7.5 的「未实现」补上）**：
  - 新增 `app/core/otel.py`：配置 `OTLP_ENDPOINT` 后经 OTel SDK 的 OTLP/HTTP exporter
    转发 span；`OTLP_HEADERS` 支持带鉴权的托管 collector；`shutdown()` 先 `force_flush`
    再关闭（批量处理器异步发送，不 flush 会丢掉最后一批）。
  - **关键实现取舍**：不走 `Tracer.start_span`，而是**手工构造 `ReadableSpan`**
    直接投给 span processor。因为 OTel 的 span id 由 SDK 生成，
    用 API 创建的 span 不可能带我们自己的 `span_id` —— 本地 trace JSON 与 Jaeger 的 id 会对不上，
    「按 id 去 Jaeger 查这一条」就断了。手工构造后**本地与远端是同一份数据**。
  - **默认零依赖**：OTel SDK 只在配置了 endpoint 时才被 import；
    未配置时进程内追踪完全自实现（`doctor.py` 专门断言这一点）。
  - 智能体归属沿 span 树继承：`llm.*` 自身没有 `agent_id`，
    由 `tracing._export()` 计算归属后传入，否则在 Jaeger 里按 agent 过滤会丢掉这些 span。
- **黄金数据集的内容级期望（D13 的「期望输出」那一半）**：
  - `golden/briefs/*.json` 新增 `expect` 块：`must_contain` / `min_keyword_hits` /
    `must_not_contain` / `min_quality` / `min_judge` / `max_revisions` / `max_title_length` /
    `expect_first_round_blocked` / `reference_points`。
  - `app/core/golden.py` 新增 `check_expectations()`：品牌名、关键词覆盖率、禁用表述、
    标题字数上限（用例未指定时取渠道规则）、**广告法阻断级用语**（由词库现算，词库更新后自动生效）、
    质量分/评估分门槛、返工预算、首轮门禁行为。
  - 内容级期望失败**一律算回归**（`compare()` 里并入 `regressed`）：
    分数波动可以容忍，但「品牌名漏了」「阻断级用语漏出去了」是确定性缺陷。
  - CLI 打印逐条断言结果；`/api/golden` 的对比结论同样带上。
- **本轮由内容级断言发现的三个真实缺陷**（这是它存在的意义）：
  1. **标题压缩 off-by-one**：`mock.py` 的 `_compress_title` 写成
     `f"{title[: limit - 1]}…"`，省略号占一个字符，实际产出 `limit + 1` 字 ——
     恰好比渠道上限多一字。
  2. **交付标题未受渠道上限约束**：`_publish_delivery` 直接用 A5 的基础标题，
     而 A5 只在「小红书」分支做压缩 → 官网用例交付了 21 字标题（上限 20）。
     修法是在交付装配时按 `title_limit(channel)` 兜底压缩，并保留
     `base_title` / `title_limit` / `title_trimmed` 三个字段以便追溯。
     （没有改用 A9 的渠道标题：那是**关键词前置的搜索变体**，语义不同，不能当交付标题。）
  3. **合规检查把免责声明误判为违规**：`不得涉及疾病预防与治疗功能` 里的「治疗」
     被当成违规宣称。详见踩坑 53–54。
- **否定语境判定（`_is_negated`）**：按**小句**判断 —— 所在小句内、且位于该词之前存在否定词，
  即视为免责/禁止表述。必须「所有出现都处于否定语境」，只要有一处肯定性宣称就算违规
  （`不得用于治疗，但可治疗失眠` 必须判出来）。
- **调性不做机器断言**：`min_tone_hits` 降级为「仅记录、不参与判定」。
  Brief 的 tone 是抽象描述（「克制」「用数据说话」），不是要逐字写入正文的关键词；
  若据此断言，唯一稳定的达标方式就是在文案里堆这些词 —— 与「克制」背道而驰。
- **容器化（D18 后半）**：
  - `Dockerfile`：多阶段（node:22-alpine 构建 → python:3.12-slim 运行），
    非 root（uid 10001）、`HEALTHCHECK` 用免鉴权的 `/api/health`、`/data` 卷。
  - `.dockerignore`：排除 `data/`、`node_modules/`、`dist/` 等，加快构建并避免覆盖镜像内容。
  - `docker-compose.yml`：应用 + Jaeger（`COLLECTOR_OTLP_ENABLED=true`），
    通过 `OTLP_ENDPOINT` 把追踪送进 Jaeger UI。
  - `deploy/k8s.yaml`：ConfigMap/Secret 分离、PVC、Deployment（**单副本 + Recreate**，
    因为本地 JSON + SQLite 存储不支持共享）、Service、Ingress（SSE 关缓冲 + 长超时）。
  - `scripts/check_deploy.py`：**不需要 Docker daemon** 的清单核验 ——
    Dockerfile 的 COPY 路径是否存在且未被 .dockerignore 排除、
    声明的环境变量是否真的被代码 `os.environ.get` 读取、
    探针路径是否等于免鉴权路径。这条拦的正是「只有 docker build 才会失败」的那类错误。
- **前端同步**：`Topbar` 在 OTLP 已接入时显示 Chip；
  `SettingsDrawer` 新增「调用轨迹与 OTLP 导出」区块（显示端点与服务名，并说明不配置也完整可用）。
- **配置**：`RuntimeConfig` 新增 `tracing`（`OTLP_ENDPOINT` / `OTEL_SERVICE_NAME` / `OTLP_HEADERS`）；
  `public_config()` 新增 `tracing`；`/api/health` 新增 `otlp` 状态；
  `.env.example` 补齐全部配置项（此前缺 embedding / publish / judge 等）。

### 4.2 前端（`npm run build` 通过）

- Topbar、侧栏任务列表、流水线看板、共享黑板产物查看器（**14 种类型** + 版本 diff）、
  事件时间线、审批面板、发布与效果回填面板、指标面板、新建任务弹窗、
  设置抽屉（含知识库 / 记忆库两个标签页）。
- 实时性：SSE 订阅（`since` 续传）+ 2.5s 详情轮询兜底，按 `seq` 去重。
- **契约解耦**：类型自持于 `src/lib/types.ts`（不再 import 旧的 `server/`），
  并补齐 `VISUAL_ADAPT / CHANNEL_ADAPT / MEMORY / PUBLISHED` 阶段与
  `visual_brief / channel_adaptation / publish_plan / knowledge_card` 产物类型；
  `ArtifactViewer` 新增视觉、渠道、**发布排期**、知识四类专用视图。
- **发布面板**（`src/components/PublishPanel.tsx`）：审批后自动出现，逐渠道/批量「登记发布」，
  并回填曝光/点击/互动/转化触发 A10 复盘；任务详情头部显示已排期与租约冲突徽标。
- **指标面板**：新增「成本 · 缓存 · 并发」区块（累计/平均成本、预算、熔断任务数、
  缓存命中率与命中/未命中、黑板活跃租约与冲突）。
- **设置抽屉**：新增「成本与缓存」分组（单任务成本上限、token 上限、LLM 响应缓存开关）。
- **记忆库面板**（`src/components/MemoryPanel.tsx`）：展示卡片总数 / 容量 / 覆盖任务 / 各类型分布，
  **新增新鲜度统计（新鲜 / 陈旧 / 最旧天数 / 过期阈值）**；
  内置检索框（走 `POST /api/memory/search`，与智能体召回同源），
  命中项显示分数、命中理由与复用建议，下方列出全部卡片及其来源任务。
  **本轮再新增向量检索 chips（提供方 / 模型 / 语义权重 / 已建索引条数）与命中项的「语义 {vector_score}」Chip。**
- **发布面板投递**（`PublishPanel.tsx`）：排期表新增「到期时间」列、投递 / 登记双按钮与投递状态 Chip
  （`pending / dispatched / skipped / failed`），并支持「全部投递」；投递走 `POST /api/tasks/{id}/publish/dispatch`。
- **设置抽屉**（`SettingsDrawer.tsx`）：新增「向量检索」（提供方 / 模型 / 语义权重 / API Key）、
  「发布投递」（webhook / 重试次数 / 自动投递开关）、「访问令牌」（本地保存，随请求带 `X-API-Token`）三组。
- **API 客户端**（`src/lib/api.ts`）：统一附带鉴权头，401 给出明确文案；SSE URL 追加 `&token=`；
  新增 `dispatchPublish / publishQueue / tickPublish`。

---

## 5. 关键设计决策与踩坑记录

**移植与架构**

1. **前端契约冻结**：Python 后端的请求/响应结构与 TS 版逐字段对齐，React 无需任何改动。
2. **`interrupt()` 之前不得有副作用**：LangGraph 恢复中断时会从头重放节点，
   故 `escalation` / `approval` 把 `interrupt()` 放在第一行，发布与落盘只在拿到裁决后执行一次。
3. **`resuming` 时 `invoke(None)`**：续跑传 `None` 让 LangGraph 从检查点继续；新任务才注入初始状态。
4. **重试只认可重试错误**：仅按错误串判定（400 之类的参数错误不重试），与 TS 版一致。
5. **SSE 不手工设置 `Connection` 头**：这是 hop-by-hop 头，交给 uvicorn/h11 管理与连接复用更安全。
6. **`/api/*` 未命中保持 404**：SPA 回落路由显式排除 `api/` 前缀，避免前端页面顶掉 API 的 404。
7. **`BrandVoiceReport` / `RewriteResult` 用 dataclass**：知识层早期返回 dict 却在调用方按对象访问，已统一为对象。

**Windows / Python 环境**

8. **pip 安装报 `WinError 448`（不受信任装入点）**：装完后 pip 校验 PATH 中的
   `AppData\Local\Programs\Cua\...` 触发 `realpath` 异常。绕过方式：`--no-warn-script-location`
   并设 `PYTHONNOUSERSITE=1` 跳过 user site。
9. **⚠️ `subprocess.PIPE` 未消费会伪装成「服务端死锁」**（本轮最大的坑）：
   冒烟脚本用 `Popen(stdout=PIPE)` 起服务却只在最后才读，管道缓冲区写满后子进程卡在写日志上；
   由于写日志时持有 GIL，连 `faulthandler` 都无法转储线程栈，表现为「服务整体无响应、请求不出现在 access log」。
   **结论：拉服务端做测试时把输出重定向到文件，或起线程持续消费管道。** 排查花了不少时间，
   期间一度误判为 `SqliteSaver` 跨线程共享连接死锁（已用 `InMemorySaver` 对照排除）。
10. **测试客户端的连接复用**：压测脚本每个请求用一个新连接；共享连接池时服务端可能在
    keep-alive 超时后关闭套接字，而客户端仍复用，导致请求石沉大海。浏览器与 curl 不受影响。
11. **持久化目录可覆盖**：`CREATOR_DATA_DIR` 让测试互不污染，也让多实例部署成为可能。

**第二轮智能体（A8/A9/A11）**

12. **⚠️ `doctor.py` 通过 ≠ 智能体代码正确**：自检直接调用 `app.llm.mock.GENERATORS["A11.memory"]`，
    绕过了 `app/agents/a11_memory.py` 的真实执行路径，于是「上下文缺字段」这类错误在自检里完全不可见。
    实战教训：**契约类改动必须跑到 `smoke_api.py`（真实 LangGraph 全链路）才算验证**，
    自检只覆盖「离线引擎 + 知识层」。
13. **收敛型智能体的上下文要显式注入**：`AgentRunContext` 原本只有 `upstream`（上游产物 dict），
    A11 需要「全量产物清单」来统计归档，于是新增 `artifacts: list[Artifact]` 字段并由编排器注入
    `list(task.artifacts)`。默认值 `field(default_factory=list)` 保持对既有智能体的向后兼容。
14. **`handoff.to` 只接受智能体 ID**：`Handoff.to` 是 `AgentId | None`，写 `"human"` 会直接 Pydantic 校验失败。
    「交给人工」在本项目里的既定约定是 `{"to": None, "reason": …}`（A10 已如此），A11 已对齐。
15. **`escalate` 分支也要跟上流水线变更**：`review → escalate → escalation → {pass→A8/A9/A10/A11}`，
    即人工覆盖门禁后同样要补齐投放侧与知识沉淀阶段，否则交付件会缺 `channels/visual/knowledge_cards`。

**记忆库与 RAG（A11 闭环）**

16. **检索只在编排层做一次**：若 A1/A2/A4 各写一份检索逻辑，三份口径必然漂移。因此把
    `_memory_hits()` 统一放在 `Orchestrator._run_agent_step()`，智能体只消费 `ctx.memory`。
17. **召回必须排除当前任务**：`retrieve(..., exclude_task=task.id)`，否则 A11 会召回自己刚写入的卡片，
    变成自我循环的「假复用」。`doctor` 的第一轮断言正是「任务 1 召回 0 条」。
18. **去重键 = `sha1(kind|title|content)`**：A11 会在 `_with_retry` 重试与多轮返工中重复执行，
    没有幂等去重就会把同一张卡片写进库好几次。副作用是「同品牌重复任务不会重复扩库」——这是期望行为。
19. **不引入向量库的理由**：MVP 主场景是「同品牌历史资产复用」，元数据加成 + 2-gram 文本相关度已足够，
    且省掉了 embedding 依赖与索引构建这一额外失败面。接口按 vector-ready 设计
    （`retrieve(query, …, top_k) -> list[MemoryHit]`），日后换向量检索上层无需改动。
20. **自检必须跑智能体本体**：新增的 `check_memory_recall()` 直接构造 `AgentRunContext` 调
    `app/agents/a11_memory.run()`，而不是只调 `GENERATORS`——这是踩坑 12 的补救：
    自检从此覆盖真实执行路径，`AgentRunContext` 字段漂移会当场暴露。
21. **自检默认写临时数据目录**：`isolate_data_dir()` 必须在 import `app.*` **之前**设置
    `CREATOR_DATA_DIR`（`app/config.py` 在 import 时读取环境变量），否则自检会污染开发环境的 `data/`。
22. **清理检查点要按 `thread_id` 且防御式处理**：LangGraph 的表结构归它自己维护，
    代码里只做「表存在就删、不存在就跳过」，先删 `writes` 再删 `checkpoints`，失败不影响删除接口。

**向量检索与发布投递（第四轮）**

23. **embedding 必须「可失败」**：记忆库检索是创作链路的前置步骤，若 embedding 服务抖动就让检索报错，
    整个任务都会挂。因此 `embed_texts` 把远端调用的**任何异常**（含返回条数/维度异常）都静默回退到本地向量，
    调用方无需 try/except。同时 `provider=local` 用确定性 hashing（blake2b + 符号哈希），
    同一文本永远同一向量，跨进程/重启可复现，测试才敢断言分数。
24. **混合权重必须能退化**：`weight=0` 时 `score` 就是纯关键词分，与旧行为逐位一致——
    这让「上向量」成为可回滚的开关，而不是一次性替换；新老行为能用同一套断言对照。
25. **`due_at` 要顺延而不是「当天已过就作废」**：排期文案是「每天到点」的语义。
    若按「今天该时刻已过 → 无到期时间」实现，晚上建的任务会在第二天永远不触发。
    正确做法是 `due <= now` 时 `+1 day`，让后台巡检总能等到下一次投放点。
26. **未配置 webhook 不等于失败**：离线/单机场景下 `PUBLISH_WEBHOOK_URL` 为空是常态。
    `dispatch` 返回失败但编排层把 `dispatch_status` 记为 `skipped` 并**等价比「登记发布」**，
    这样 smoke 测试在无外部依赖时也能覆盖「投递 → 队列清空」的完整分支。

**租户隔离与评估（第五轮）**

27. **⚠️ `tempfile.mkdtemp` 建出的目录在本机沙箱下不可写**（本轮最大的坑）：
    它以 `mode=0o700` 建目录，Windows 上会落成一条「仅创建者可访问」的 ACL，
    于是子进程往里写文件直接 `PermissionError`——现象是**目录明明存在、就是写不进去**。
    受害面比想象中大：`doctor.py` 的记忆库落盘静默失败（捕获 OSError 只打日志）、
    `sqlite3.connect` 抛 `unable to open database file` 让检查点**静默退回内存实现**，
    而自检依然全绿 —— 也就是说「断点续跑」这条能力此前从未被真正验证过。
    排查过程：`mkdir(parents=True)` 成功 → 直接写入失败 → 对比 `os.mkdir` **成功**、
    `mkdtemp` **失败** → 定位到 `0o700`。
    **结论：测试用临时目录一律走 `make_temp_dir()`（逐级 mkdir + 随机后缀），
    并且把「目录可写」当成一条自检断言而不是假设。**
28. **静默退化必须有可见出口**：同一个坑还暴露出一个设计问题——检查点退回内存实现时，
    除了 `log.warn` 之外没有任何地方能看出来。于是把它提升为一等公民：
    `Orchestrator.checkpointer_kind` → `/api/health.checkpointer` → 前端顶栏告警 + 自检断言。
    **凡是「降级后功能仍然跑得通、但能力已经缺失」的路径，都要有可查询的状态。**
29. **评估必须能失败且必须能回退**：与 embedding 同理，`JUDGE_PROVIDER=llm` 下网关抖动
    不应该让评估流水线整体不可用。`judge_with_llm` 把远端调用的**任何异常**
    （含返回非 JSON、维度缺失）都转成「回退离线评估器 + 标注 fallback」，
    调用方无需 try/except；回退率本身就是可观测指标（`stats.fallbacks`）。
30. **评估不能塞进门禁**：门禁是否决式（能不能发），评估是度量式（有多好）。
    把度量塞进门禁，「低于阈值就返工」会被一个有噪声的评分放大成流程抖动。
    因此默认 `JUDGE_MODE=advisory`，只产出报告；要收紧再切 `blocking`。
    `judge.weight` 同理——默认 0.2 只微调综合分，`0` 则与历史行为逐位一致，保证可回滚。
31. **合规维度必须复用 A7 的词库**：评估器若自己再写一遍扫描逻辑，迟早出现
    「A7 判定通过、评估说合规有问题」的自相矛盾。直接调 `scan_compliance` 后，
    两侧结论永远同源——这条也在 `doctor.py` 里被断言（注入阻断用语后合规维度 5.0 → 0.0）。
32. **评估口径要带版本号**：权重或规则一变，旧分数就不可比。`RUBRIC_VERSION`
    写进每条记录与配置接口，跨版本对比前必须先对齐——否则「Prompt 改好了」可能只是尺子变了。
33. **去重键必须含租户**：租户隔离后若仍用 `sha1(kind|title|content)` 去重，
    B 租户沉淀同名知识会被 A 租户的旧卡片顶掉（表现为「入库成功但检索不到」）。
    正确做法是把租户并进指纹，同租户内幂等、跨租户各自存活。
34. **容量要按租户计量**：`MAX_CARDS` 若是全局上限，先入库的租户会把后来者的名额挤光，
    多租户下表现为「新租户什么都存不进去」。`_evict` 改为只淘汰本租户最旧的卡片。
35. **混合检索的向量缓存只算当前批次**：`_ensure_vectors` 原本对所有卡片建索引，
    改为只处理调用方传入（已按租户过滤）的那批——否则 A 租户检索一次会替 B/C/D 白算一遍。
    `stats().vector_indexed` 也相应改为「本租户已建索引条数」，因此**重启后为 0 是正常的**，
    首次检索后才涨上来（早期把它误当异常排查过）。
36. **浮点尾数要在落盘前收干净**：加权求和会产出 `90.99999999999999` 这类值，
    流到接口和 JSON 文件里既难看，又会在「分数是否相等」的对比里咬人。
    `build_record()` 统一 `round(total, 1)`。

**黄金数据集与回归门禁（第六轮）**

37. **数据集必须放在 `data/` 之外**：`data/` 已 gitignore，是运行期产物；
    而黄金数据集与基线是**测试资产**，必须随代码版本化 —— 能被 review、能被 diff、
    能被追责。否则「悄悄把基线改宽让 CI 变绿」就是一条无人察觉的作弊路径。
38. **⚠️「永远显示通过」的门禁比没有门禁更糟**：回归判定器如果因为 bug 恒返回 ok，
    表面上 CI 全绿，实际质量已经在滑坡。因此 `doctor.py` 专门用 9 个子断言
    **逐条构造偏差**去证明它会判回归（含「容差内不该误报」的反向断言）。
    这是本项目一贯的做法：**关键判定逻辑本身也要有测试**，而且测试要能证伪。
39. **返工轮次是成本指标，必须参与回归判定**：只比分数会出现「总分没掉但多返工一轮」
    的悄悄劣化——那意味着更慢、更贵、且更接近返工上限。`compare()` 对
    `revision_round` 单独判定方向（越少越好）。
40. **门禁强度下降要能被发现**：强监管用例（医疗/金融/教育）在基线里是
    「首轮合规被拦截 → 返工后通过」。如果某次改动让首轮直接放行，
    分数可能不降反升（少了一轮返工），**看起来是改进，实际是门禁失效**。
    因此 `first_round_blocked` 从 true 变 false 直接判 `regressed`。
41. **部分运行不能沿用全量的缺失判定**：最初 `compare()` 把「基线里有、本次没跑」
    一律判 `missing`，结果是 `--case X` 单跑一条时另外 9 条全被算成缺失，
    部分运行**永远无法通过** —— 那等于逼着人每次都跑全量。
    修法是给 `compare(scope=)`：只在范围内判定缺失，并在结果里标注 `partial`。
    全量运行时（`scope=None`）缺失仍然算失败，跳过用例的成本依旧很高。
42. **跨口径对比要先拒绝，而不是先给分**：基线带 `rubric` 与 `engine`。
    若当前 `rubric` 与基线不一致，`compare()` 会置 `rubric_mismatch` 并显式提示
    「分数不可直接比较」，而不是默默算出一个差值 —— 那会让人把「尺子换了」
    误读成「质量变了」。
43. **更新基线要有摩擦**：`--update-baseline` 在存在回归/失败/缺失时**拒绝执行**，
    必须显式 `--force`。把「认定新合格线」这一步做成有意动作，
    而不是顺手一跑就把回归固化下来。
44. **数据集的覆盖口径要选「分支覆盖」而不是「组合覆盖」**：渠道 × 行业有 49 种组合，
    全铺会显著拖慢回归。改为「每个渠道特化分支 + 每个行业规则组至少被一条用例命中」，
    10 条即达成（`--coverage` 可查矩阵并报告未覆盖渠道）。
45. **runner 抽到 `app/core` 而不是写在脚本里**：Web 界面「跑回归」与 CLI 跑回归
    必须共用同一段执行逻辑，否则两边迟早在「什么算通过」上产生分歧。
    脚本负责进程编排与退出码，服务端负责执行 —— 职责分清。
46. **后台任务不能挂在 HTTP 请求上**：跑完 10 条用例是分钟级，浏览器与 fetch 都会超时。
    因此 `/api/golden/run` 立刻返回 202，真实执行在后台线程里，
    状态由 `RunState` 聚合供轮询。已在运行时返回 409 而不是排队（排队会掩盖双触发）。

**调用轨迹（第七轮）**

47. **⚠️ 不包 `graph.invoke` 会让 trace 树退化成平铺列表**（本轮实测发现的真实缺陷）：
    LangGraph 的每个节点是**独立执行**的，节点 span 之间没有共享父节点，
    于是 13 个节点 span 全部成了**根 span** —— trace 里 `roots` 有 13 个，
    self-time 计算全部失真，看起来「有追踪」但对排障毫无帮助。
    修法是把每次 `graph.invoke()` 包一层 span，节点 span 自然挂到它下面。
    **教训：追踪做完要断言「根 span 数量 < span 总数」**，否则等于没做
    （这条已固化进 `doctor.py` 与 `verify_contracts.py`）。
48. **人工等待必须单独成 span，且与执行链并列**：如果把 `_wait_for_human` 算进节点耗时，
    排障时会把「人在犹豫 3 小时」误读成「系统卡了 3 小时」。
    实际上它本就是独立的一段，所以它成为自己的根 span 是**正确的建模**，不是缺陷。
49. **自建追踪要如实标注边界**：本实现是 **OTel 数据模型**，不是 OTel SDK ——
    没有 OTLP 导出、没有采样、没有跨进程上下文传播。文档里必须写清楚，
    否则读者会以为「有 span 就等于接入了 OpenTelemetry」。
    自建的理由是守住「默认零外部依赖、离线可跑通」；接 collector 的路径已铺好
    （`Span.to_otel()` + 落盘 `otel[]` 段），不需要改任何埋点处。
50. **降级与缓存命中要记为 `ok` 而不是 `error`**：成本熔断、网关降级、
    响应缓存命中都是被设计出来的正常路径（plan.md D10/D12）。
    把它们记成错误会让「span 错误率」这个指标彻底失去意义 ——
    一个永远不为 0 的错误率等于没有错误率。`doctor.py` 与 `smoke_api.py` 都断言 `errors == 0`。
51. **trace 落盘失败绝不能影响任务**：追踪是观测能力，不是业务链路的一环。
    `_export()` 兜住 `OSError` 并计入 `exportFailures`（可在指标里看到），
    与 embedding、评估器的「可失败」原则一致。
52. **事件与 span 的关联要在总线里统一注入**：若让每个调用方自己传 `span_id`，
    必然有遗漏（本轮 127 条事件里 126 条带上 trace，唯一缺的那条是
    `task.created` —— 它发生在 `start_trace` 之前的建任务阶段，属正确行为）。
    在 `publish()` 里读 thread-local 当前 span 是唯一不会漂移的做法。

**OTLP 导出与内容级断言（第八轮）**

53. **⚠️ 用 OTel API 创建 span 会让 id 对不上**（接入 OTLP 时的第一个坑）：
    `Tracer.start_span` 的 span id 由 SDK 生成，无法指定。
    于是本地 trace JSON 与 Jaeger 里的 id 完全不同 ——
    「按本地日志里的 trace_id 去 Jaeger 查这一条」这个最基本的排障动作直接失效。
    修法是**手工构造 `ReadableSpan` 并投给 span processor**：
    我们已经有完整的 trace_id / span_id / 起止时间 / 属性，直接构造最诚实，
    本地与远端看到的就是同一份数据。`doctor.py` 断言
    `local_traces == exported_traces and local_ids ⊆ exported_ids`。
54. **导出后必须复查父子关系**：即使 id 对了，如果 parent 没设，
    在 Jaeger 里依然会看到「42 个并列根 span」—— 正是第七轮踩过的坑换了个地方复现。
    因此断言里同时检查「导出后根 span 数」与「llm span 有父节点」。
55. **⚠️ 标题压缩的 off-by-one**：`f"{title[: limit - 1]}…"` 看起来没错，
    但省略号**占一个字符**，实际产出 `(limit-1) + 1 = limit + 1` 字 —— 恰好比渠道上限多一字。
    更坑的是同一份数据里 `title_ok: len(title) <= limit` 会显示「通过」，
    因为它算在**压缩前**的变体上，而交付用的是基础标题。
    **教训：凡是「截断到 N」的逻辑，都要断言 `len(result) <= N`，而不是相信表达式看起来对。**
56. **⚠️ 交付物必须受渠道规则约束，不能只靠上游自觉**：
    `_publish_delivery` 直接取 A5 的基础标题，而 A5 只在「小红书」分支做压缩，
    于是官网用例交付了 21 字标题（上限 20）。这类缺陷的共同点是
    **分数完全正常**（质量分 91），只有把「渠道上限」写成断言才会暴露。
    修法是在交付装配时兜底压缩，并保留 `base_title` / `title_limited` 便于追溯。
    注意**不要**改用 A9 的渠道标题：那是关键词前置的**搜索变体**，语义不同。
57. **合规扫描必须理解否定语境**：`不得涉及疾病预防与治疗功能` 是**免责声明**，
    但朴素子串匹配会把它当违规宣称 —— 于是每一条监管用例都误报，
    而误报会让人开始忽略这套断言，等于把门禁废掉。
    两个反复改错的地方：① 固定字符窗口太短（否定词与禁用词相隔 8 字就漏判）→
    改成**按小句**判断；② 没把**中文逗号**当小句边界 →
    `不得用于治疗，但可治疗失眠` 的后半句被前半句的否定词豁免了。
    **小句边界的定义必须包含中文标点**，这是中文文本处理与英文最大的差异之一。
58. **断言设计要避免反向激励**：最初给「调性」也写了 `min_tone_hits`，
    结果 `克制`/`用数据说话` 这类抽象描述永远匹配不到，而且**唯一稳定的达标方式
    就是在文案里堆这些词** —— 那正好与「克制」背道而驰。
    结论：调性这类主观项只做记录与人工抽查（`reference_points`），不做机器判定。
    同类的还有「关键词必须全部命中」：那会逼出「把关键词硬塞进正文」，
    因此改为「覆盖率 ≥ N」（`min_keyword_hits`）。
59. **派生字段不能进持久化契约**：`CaseResult.to_dict()` 里加了给人看的
    `checks_failed` 统计，而 `save_baseline` 直接把它写进了基线；
    下次读取时 `CaseResult(**item)` 抛 `TypeError: unexpected keyword argument`。
    修法是落盘与还原都以 `__dataclass_fields__` 为准过滤。
    **自检当场拦下了它**（`check_golden` 读基线失败）—— 这正是「关键路径要有自检」的价值。
60. **清单类配置要有 lint**：Dockerfile 的 COPY 路径、compose/k8s 的环境变量名，
    写错了只有真正 `docker build`/部署才失败，而 CI 与开发机常常没有 daemon。
    `scripts/check_deploy.py` 把这些变成静态检查（路径存在性、变量名是否被
    `os.environ.get` 读取、探针路径是否免鉴权），于是这类错误能在无 daemon 环境下被拦住。
61. **探针路径必须免鉴权**：`HEALTHCHECK`/readinessProbe 打 `/api/health`，
    而这个路径恰好是鉴权中间件唯一放行的 —— 这不是巧合，是**必须**：
    否则一旦开启 `CREATOR_API_TOKENS`，容器会永远处于不健康状态并被反复重启。
    `check_deploy.py` 专门断言这条对应关系。
62. **k8s 副本数要与存储能力匹配**：当前持久化是本地 JSON + SQLite，
    多副本会各写各的导致状态分裂。因此清单里写死 `replicas: 1` + `Recreate`，
    并在注释里说明「要横向扩展必须先换存储层」。
    **在清单里假装支持多副本，比不支持更危险。**

**多语言本地化（第九轮）**

63. **⚠️ 新增字段必须在所有「重建对象」的地方透传**：`Brief` 加了 `language`，
    但 `mock.as_brief()` 手工逐字段构造 `Brief`，**漏了 language** ——
    于是本地化分支永远走不到，英文 Brief 静默产出中文。
    危险点在于：只有单语言时这个 bug **完全不可见**，而且看起来「链路是通的」。
    **教训：给契约加字段时，要搜出所有手工重建该对象的地方**（本项目里
    `mock.as_brief()`、`parse_brief()`、黄金用例加载都属此类）。
64. **断言与被断言对象必须同口径**：交付层已按「英文按词」处理标题，
    黄金断言却用 `len(title)` 数字符 —— 12 词上限的英文标题有 60+ 字符，
    于是被判成「超出上限 12 字」。这类 bug 的特征是**两边都「没错」，但量纲不同**。
    统一走 `title_measure()` 后才一致。
65. **多语言最容易做假的地方是「英文 Brief 产出中文」**：看起来链路通了，实际只是把
    中文稿当英文交付。因此自检直接断言**英文产物的中文字符数为 0**，
    而不是断言「有输出」。
66. **本地化要做「原生创作」而不是翻译**：翻译腔的营销文案在本地市场基本不可用。
    提示词里必须显式写明「不要先写中文再翻译」，因为模型收到中文 Brief 时天然先想中文。
67. **合规能力不足时要如实告知，而不是静默放行**：广告法词库只覆盖中文。
    选择非中文时 A7 明确声明「需人工复核」+ 写入当地红线 + 强制人工复核 ——
    这与「假装检查过了」的区别，就是**产品能否被信任**的区别。
68. **mock 引擎也要覆盖新语言，否则离线环境下新能力是假的**：默认
    `LLM_PROVIDER=mock`，若 mock 只会说中文，那么「支持英文」在 CI 与自检里
    永远验证不到。已知边界：mock 的**支撑类产物**（创意概念、内容策划、视觉指导、
    渠道适配、效果报告、知识卡片）仍是中文模板，只有**交付物**（标题/正文/CTA/标签）
    与最终交付件是完整的目标语言。

**CI 与视频脚本（第十轮）**

69. **⚠️ `.gitignore` 掉的构建产物，CI 必须先构建**（用户报障的 CI 失败）：
    `dist/` 被 gitignore，后端**只在 `dist/` 存在时**才挂载静态托管与 SPA 回落路由，
    而 backend job 只装了 Python 依赖 → `GET /` 返回 404 →
    契约核验里「status」与「index.html 挂载点」两项失败。
    这类问题的特征是：**本地永远不复现**（开发机有 dist/），只在干净 checkout 上出现。
    修法是让 CI 显式构建，并在核验脚本里给出可操作提示（而不是只报 404）。
    排查手法值得记下来：`mv dist dist_backup` 后在本地跑同一条命令，
    得到与 CI **逐字相同**的失败，就把「环境差异」这个变量消掉了。
70. **自己新加的检查，必须先看到它「通过」和「失败」两种表现**：我给契约核验加的
    `dist/index.html 存在` 断言，把「失败提示文案」传进了 `expected` 参数，
    于是断言变成 `True == '请先执行 npm run build'` —— 恒失败。
    加一个 `detail` 参数分开「说明」与「期望值」后正常。
    **断言工具的参数顺序本身就是一类 bug 来源，加检查时要正反都跑一遍。**
71. **任务终态 ≠ 编排线程收尾完成**：自检读到任务 `completed` 就去检查 trace，
    但编排线程可能还在 `finally` 里（`finish_trace` 才计算总耗时与 self-time），
    于是偶发 `duration=0ms` 与 `unset` 状态的 span。
    修法是**等待收尾条件**（duration > 0）并在断言里接受 OTel 合法的 `unset`，
    而不是要求全 `ok` —— 后者会在收尾边界上偶发失败（flaky 测试比没有测试更糟）。
72. **「是否产出某产物」要由知识层判断，不能一律产出**：视频脚本若对所有渠道都出，
    图文任务会被塞入一份无关脚本，既制造噪声又让「交付物是否符合 Brief」失去可判断性。
    判断口径（渠道 / 交付物 / 渠道形态）与命中原因一并记录在产物里，便于解释。
73. **新产物类型要同时改「契约 + 产出方 + 前端 + 断言」四处**：加 `video_script` 时
    实际动了后端 `ArtifactType`、A8 产出、前端联合类型与专用视图、
    黄金断言、自检 —— 少任何一处都会留下不一致（前端 typecheck 会立刻报错，
    这一点上 TypeScript 帮了大忙）。

**真实网关与 Docker（第十一轮）**

74. **⚠️ 真实模型的第一课：结构化输出会被 `max_tokens` 截断**。
    离线引擎的输出「刚好够用」，因此这个问题在 mock 下**永远暴露不出来**；
    接上真实网关的第一个任务就死在 A3：JSON 未闭合 → `_find_matching_end` 返回 -1
    → `extract_json` 放弃 → 智能体报「无法解析为 JSON」，而内容其实已产出大半。
    三件事都要做：**修复截断的 JSON**、**透传 `finish_reason` 区分「截断」与「胡说」**、
    **针对性放大上限后重试**（原样重试必然再被截断一次）。
75. **⚠️ 按单一输入单价计费会高估成本数倍**：DeepSeek 的输入侧缓存命中比未命中
    便宜约 50 倍。忽略这一维 → 成本被高估 3.8 倍（实测）→ **过早熔断** →
    本该走真实模型的任务被降级到离线引擎，而使用者只会看到「质量莫名其妙变差」。
    计费模型必须覆盖提供方的真实计费维度。模型名匹配还要**长名优先**：
    `v4-pro` 与 `v4-flash` 价差 3 倍，前缀规则会抢错。
76. **⚠️ 验证脚本必须强制离线，否则会被 `.env` 带跑偏**：本机 `.env` 指向真实网关后，
    `doctor.py` 与 `golden_eval.py` 跟着走真实模型 —— 一条任务 5 分钟、自检超时、
    11 条黄金用例跑不完、每次验证都花钱，而且真实模型输出发散会让
    **同一条断言今天过明天不过**。验证脚本断言的是契约与逻辑，必须秒级、可复现、零成本；
    量真实链路的入口应当是单独的 `real_check.py`。
    配套的顺序坑：`force_offline_provider()` 写 `os.environ`，
    而 `env` dict 由 `os.environ` 展开 —— **先调用、后构造**，反了就白设。
77. **换个底座就换了一套隐含环境**（受限网络构建镜像时连踩三个）：
    ① 底座把 venv 放在 PATH 前，而那个 venv 里**没有 pip**；
    ② 底座预设 `NODE_ENV=production`，npm 于是**跳过 devDependencies**，
    而 vite/typescript 全在里面 —— 报错只有 `vite: not found`，极易误判成缓存或网络问题；
    ③ 底座自带 `ENTRYPOINT` 会去启动它自己的服务（`gunicorn: not found`），**CMD 根本不生效**。
    **教训：用非官方底座时，显式检查 PATH、关键环境变量、ENTRYPOINT 三项。**
78. **Docker Hub 不通 ≠ 没法构建镜像**：PyPI 往往仍然可达。
    用本地已有镜像做底座 + 从镜像源装 Python 依赖，就能在受限网络下产出**可用**镜像。
    这是可行方案而非更优方案（底座更大），要如实标注并保留标准 Dockerfile。
79. **端口别写死**：Windows/Hyper-V 保留成片端口区间，写死端口会直接
    `WinError 10013 / [Errno 13] bind`（实测 `free_port(8811)` 返回 10012，
    说明 8811 确在排除区内）。测试脚本一律动态取端口，
    否则会出现「CI 通过、本机失败」这类环境相关假故障。

---

## 6. 验证结果（端到端实测）

> 下面各轮的数字是**当轮**的实测记录，保留原样以便追溯。
> 第五轮的产物数是 18（比早期的 17 多一件），产物类型数仍是 14 种；
> 最新一轮的完整实测见 §6.1。

- `scripts/doctor.py` → **自检通过**。A1→A11 离线生成器全链路可跑：
  - 首轮：A5 pass/88、A6 revise(high)、A7 revise/75（blocker=1）——门禁触发返工，符合预期
  - 返工 1 轮后收敛：A5 pass/88、A6 pass(low)、A7 pass/100
  - A8 风格=「真实场景纪实」配图=7 张 图文一致=True；A9 平台=3 标题=19/20 字 长尾词=4
  - A11 卡片=4 模板=3 知识缺口=2 记录返工=1 轮
  - 动态渠道（抖音）分支：A8 分镜=5 镜 画幅 9:16；A9 标题 18/18 字（搜索变体 19 字 → 已压缩）
  - **记忆库 RAG 闭环（跑的是真实 A11 智能体，不是生成器）**：
    任务 1 入库 7 条新知识、召回 0 条（`exclude_task` 生效）；
    任务 2 召回 5 条历史资产，示例命中 `晨野｜小红书 语气与主张基线`
    （score=0.7145｜**语义=0.5865**，理由=文本相关、语义相近、同品牌、同渠道、同行业）
  - 自检默认落在临时 `CREATOR_DATA_DIR`，不污染开发环境数据
  - **本轮新增三组自检**：`[记忆库向量检索]`（确定性=True / 归一化范数=1.0000 / 自相似=1.000 /
    异话题相似=0.000）、`[发布排期]`（`07:30-08:30` → `(7,30)`、跨天顺延 23:00→次日 07:30、
    未到点=False / 已到点=True）、`[鉴权]`（`tok1` → `default`、`acme:tok1,beta:tok2` → `{'tok1':'acme','tok2':'beta'}`）。
    自检末尾汇总「五项检查全通过」。
- `scripts/smoke_api.py` → **结果：通过（exit 0）**，覆盖：
  - `/api/health`、`/api/agents`（**11 个已实现 + A0 规划中**）、`/api/knowledge`、`/api/settings`(GET/PUT)
  - `GET /` 静态托管 200（`dist/index.html`）
  - 主流程：建任务 → SSE 实时收到 `approval.required`（综合质量分 94）→ 挂起于 `MEMORY` 阶段
    （产物 16 / 事实 7 / 活动 16）→ `decide` → 归档交付
    （终态 `completed` / `ARCHIVED`，质量分 94，**17 个产物、13 种产物类型、16 turns**）
  - **A8/A9/A11 确已进入流水线**：产物同时含 `visual_brief` / `channel_adaptation` / `knowledge_card`，
    流水线节点 A8/A9/A11 状态均为 `done`；交付件含平台 3 个、视觉风格「真实场景纪实」、知识卡片 4 张
  - 自动审批（`autoApprove`）任务同样跑完整程（17 个产物）
  - **记忆闭环**：`GET /api/memory` → 卡片 7 张 / 覆盖任务 1 / 品牌 `['晨野']` / 容量上限 500；
    `POST /api/memory/search`（“冷萃 咖啡 早八通勤 小红书”）命中 3 条
    （`晨野｜小红书 语气与主张基线` 0.26 等）
  - **跨任务召回真的生效**：第二个任务 A11 检索结果 **5 条**，A1 的 evidence 中出现
    `记忆库/task_7acf95db` 来源 **2 条** —— 证明召回确实流到了创作智能体，而不只是躺在库里
  - `/api/tasks`、`/api/metrics`（total=2 / completed=2 / avg_score=94.0 / gate_pass=66.7% / p99=516ms）
  - 错误分支：404（详情缺失）、400（非法 decision）、409（重复 decide）、409（删除运行中任务）
  - 第三个任务全节点 `A0…A11 均 done`；已完成任务 `DELETE` → 200 且响应含
    `purged_checkpoints: 55`，随后详情 404
  - 持久化：`tasks/*.json`、`checkpoints.sqlite`、`blackboard.json`、`memory.json` 均生成
  - **本轮新增断言**：`PUT /api/settings` 的 `embedding`（provider=local / weight=0.4）与
    `publish`（webhook 已配置）、`authRequired=False`；`GET /api/memory` 的 `vector_indexed`、
    命中项 `vector_score`（如 `晨野｜小红书 语气与主张基线` 0.2765/语义 0.3012）；
    `POST /api/tasks/{id}/publish/dispatch` → 排期含 `due_at`（小红书 07:30Z / 抖音 12:00Z / 公众号 20:00Z）、
    投递状态 `skipped`（未配 webhook 的既定退化）→ `GET /api/publish/queue` 返回 **0 条残留**。
- 前端：`npm run typecheck` 与 `npm run build` 通过（`tsc --noEmit` 无错误，47 modules）。
- 并发行正确性：`scripts/stress_llm.py`（Mock，`-n N -c M`）下黑板租约冲突可控、活跃租约归零、成本账本不串味。
- 关于「验收过程」：上一轮先被两个真实缺陷拦下（详见踩坑 12–14），修复后才拿到通过结果——
  **这说明自检通过并不等于链路可用**；本轮因此把「跑智能体本体」直接写进了 `doctor.py`。

### 6.1 第五轮实测（2026-09-13）

- `scripts/doctor.py` → **八项检查全通过**：
  - Mock 引擎收敛（返工 1 轮后 A7 100）、记忆库 RAG 闭环（入库 7 → 召回 5）、
    向量检索（确定性 / 范数 1.0000 / 自相似 1.000）、发布排期（跨天顺延正确）、
    鉴权 Token→租户解析。
  - **新增〔记忆库租户隔离〕**：`acme` 入库 1 条、`beta` 入库 1 条（同名同内容各自存活）、
    acme 重复入库 0 条（同租户内仍幂等）、acme 可见 1 条（全局 9）、跨租户泄漏 0 条。
  - **新增〔断点续跑检查点〕**：后端 = `sqlite`（修复前是 `memory`）。
  - **新增〔LLM-as-a-Judge〕**：总分 91.0/100（pass，置信度 0.81），
    六维 需求契合 4.0｜合规安全 5.0｜结构完整 5.0｜品牌语气 3.7｜事实稳妥 5.0｜吸引力 5.0，
    可复现 = True；注入阻断用语后总分 91.0 → 46.6、合规维度 5.0 → 0.0。
- `scripts/smoke_api.py` → **结果：通过（exit 0）**，新增覆盖：
  - settings 的 `judge`（advisory/offline/通过线 70/权重 0.2/rubric）、`/api/health.checkpointer`
  - **评估流水线**：`/api/metrics.system.judge`（6 条记录 / 覆盖 2 任务 / 均分 85.18 / 通过率 100%）、
    `/api/evaluations`（记录 5 条、每条 6 个维度）、`POST /evaluate`（trigger=manual、总分 91.0）、
    门禁事件 `payload.judge` 携带评估结论
  - **租户隔离（另起带 `CREATOR_API_TOKENS` 的实例）**：`/api/health` 免鉴权 200、
    无 token/错误 token = 401、beta 访问 acme 任务 = 404、acme 记忆库 7 张（租户 `{'acme'}`）/
    beta 0 张、beta 检索 acme 知识命中 0 条、beta 视图任务数 0
  - 持久化新增 `evaluations.json`；主流程 18 产物 / 14 种类型
- `scripts/verify_contracts.py` → **核验通过**（约 10s）：`/api/health.checkpointer=sqlite`、
  `/api/settings.judge`（advisory/offline/75/0.2）、`/api/evaluations` 空库与落库后结构、
  `/api/memory` 租户视角、`/api/metrics.system.judge`、`judge.scored` 事件、
  门禁事件携带评估结论、`POST /evaluate` 的 manual 记录（六维齐全）。
- `scripts/stress_llm.py -n 8 -c 4` → 完成率 100%，p50 5.98s / p99 6.57s，
  平均返工 1.00 轮，质量分 93.0，**租约冲突 0、活跃租约 0（已归零）**，吞吐 0.64 任务/秒。
- 前端：`npm run typecheck` 通过；`npm run build` 通过（48 modules，317 kB / gzip 95.9 kB）。

### 6.2 第六轮实测（2026-09-13）

- `scripts/golden_eval.py --coverage` → **10 条用例覆盖 7/7 渠道、9 个行业**，
  无未覆盖渠道。
- `scripts/golden_eval.py --update-baseline` → 生成首版基线，结果呈现真实差异
  （不是一片同分）：

  | 用例 | 渠道 | 质量分 | 评估分 | 返工 | 产物 |
  |---|---|---|---|---|---|
  | xiaohongshu_food | 小红书 | 93.0 | 91.0 | 1 | 18 |
  | xiaohongshu_beauty | 小红书 | 93.0 | 89.0 | 1 | 18 |
  | xiaohongshu_minimal_brief | 小红书 | 94.0 | 94.0 | 1 | 18 |
  | douyin_short_video | 抖音 | 92.0 | 84.0 | 1 | 18 |
  | wechat_longform_tech | 公众号 | 92.0 | 84.0 | 1 | 18 |
  | zhihu_b2c_education | 知乎 | 93.0 | 89.0 | 1 | 18 |
  | ecommerce_home_appliance | 电商详情页 | 94.0 | 91.5 | 1 | 18 |
  | ecommerce_medical_strict | 电商详情页 | 87.0 | 81.5 | **2** | 22 |
  | pr_release_finance | PR稿 | 90.0 | 82.4 | **2** | 22 |
  | website_b2b_industrial | 官网 | 91.0 | 78.5 | 1 | 18 |

  两条强监管用例（医疗健康、金融）走满 2 轮返工才通过 —— 这正是期望行为。
- `scripts/golden_eval.py`（全量复跑）→ **10/10 持平，退出码 0**。
  离线评估器的确定性得到实证：两次运行分数逐位相同。
- `scripts/golden_eval.py --case xiaohongshu_food` → **部分运行退出码 0**，
  并打印「这是部分运行，结论仅对范围内用例成立」的提示。
- 回归判定器 **9 项偏差全部正确识别**（`doctor.py` 第 12 项，现已固化为自检）：
  完全一致→通过｜质量分 −10→回退｜评估分 −5→回退｜返工 +1→回退｜
  容差内 −2→持平｜全量缺用例→不通过｜部分运行缺范围外用例→通过｜
  运行失败→不通过｜监管用例未被拦截→回退。
- `scripts/doctor.py` → **12 项检查全通过**（新增第 12 项「黄金数据集判定器」）。
- `scripts/smoke_api.py` / `scripts/verify_contracts.py` → 仍为 **通过（exit 0）**。
- 前端：`npm run typecheck` 与 `npm run build` 通过。
- CI：新增 `.github/workflows/ci.yml`；**本地无法验证 GitHub runner**，
  但其中每一条命令都已在本地以相同参数跑通（`doctor` / `golden_eval` / `verify_contracts` /
  `smoke_api` / `stress_llm` / `typecheck` / `build`）。

### 6.3 第七轮实测（2026-09-13）

- `scripts/doctor.py` → **13 项检查全通过**（新增第 13 项「调用轨迹追踪」）。实测数据：

  | 指标 | 值 |
  |---|---|
  | span 总数 | 42 |
  | 根 span | **3**（`graph.invoke#0` / `human.wait` / `graph.invoke#16`）—— 层级成型 |
  | 智能体 span | 15（11 个智能体 + 返工轮次的 A4/A5/A6/A7） |
  | 门禁 span | 2（返工 1 轮 → 两次门禁） |
  | llm span | 15（全部带 `llm.model` 属性，且全部嵌套在智能体/评估之下） |
  | span 类型 | internal 27 / client 15 |
  | span 状态 | 全部 `ok`（错误 0） |
  | 事件关联 | 127 条事件，126 条带 `trace_id`、125 条带 `span_id`，trace 一致 |
  | OTel 形状导出 | ✅ `data/traces/<task_id>.json`，42 条 `otel[]` 记录，字段齐全 |
  | 总耗时 | 5792ms（其中 `graph.invoke#0` 5745ms） |

  说明：`task.created` 是唯一没有 `trace_id` 的事件 —— 它发生在 `start_trace` 之前的
  建任务阶段，属正确行为而非遗漏。
- 耗时排行实测（返工 1 轮）：`gate.review` 跨 2 轮累计 **2538ms**，是编排本身之外
  最贵的层级；单看「最慢的一次调用」只会看到某个 500ms 的模型调用，
  看不出门禁链路的总开销 —— 这正是按层级聚合的价值。
- `scripts/verify_contracts.py` → **核验通过**，新增 14 项 trace 契约断言
  （trace_id 非空 / span 数量 / **根 span 少于总数** / llm span 数量 /
  llm 嵌套正确 / 带模型属性 / 状态全 ok / 耗时聚合非空 / 导出格式 /
  事件带 span_id / 事件 trace 一致 / metrics.tracing 四项）。
- `scripts/smoke_api.py` → **通过（exit 0）**，新增 trace 端点断言：
  span=42 根=3、智能体 span=19、llm span=15、最贵 `graph.invoke#0` 5577ms、
  不存在任务的 trace → 404、`metrics.system.tracing` 的 traces/spans/errors。
- 前端：`npm run typecheck` 通过；`npm run build` 通过（50 modules，334 kB / gzip 101 kB）。

### 6.4 第八轮实测（2026-09-13）

- `scripts/doctor.py` → **15 项检查全通过**（新增「否定语境判定」与「OTLP 导出链路」）。
  OTLP 实测（内存导出器，无需 collector）：
  | 指标 | 值 |
  |---|---|
  | 默认不启用导出 | ✅（未配置 `OTLP_ENDPOINT` 时不加载 OTel） |
  | 本地 span / 导出 span | 42 / 42 |
  | trace_id 一致 | ✅ |
  | span_id 全部命中 | ✅（本地 id ⊆ 导出 id） |
  | 导出后根 span | 3（层级保留，未退化成并列根） |
  | llm span 嵌套保留 | ✅（15 个全部有父节点） |
  | 带智能体归属 | 15/15 |
  | 导出失败数 | 0 |
- 否定语境判定 → **10 个用例全部符合预期**（免责声明豁免、肯定宣称不豁免、
  逗号后不跨小句豁免、跨句不豁免、绝对化用语必判出）。
- `scripts/golden_eval.py` → **10/10 持平，136 项内容级断言 0 失败**。
  本轮该断言发现的三个真实缺陷：
  1. 标题压缩 off-by-one（`_compress_title` 产出 `limit + 1` 字）；
  2. 交付标题未受渠道上限约束（官网用例 21 字 > 上限 20）；
  3. 合规扫描把免责声明「不得涉及疾病预防与治疗功能」误判为违规。
  修复后交付标题全部达标：

  | 用例 | 渠道 | 交付标题字数 | 上限 |
  |---|---|---|---|
  | website_b2b_industrial | 官网 | 20 | 20 |
  | xiaohongshu_beauty | 小红书 | 20 | 20 |
  | xiaohongshu_minimal_brief | 小红书 | 17 | 20 |
  | pr_release_finance | PR稿 | 21 | 30 |
  | zhihu_b2c_education | 知乎 | 17 | 30 |
- `scripts/check_deploy.py` → **核验通过**：15 条 COPY 路径全部存在且未被 .dockerignore 排除、
  Dockerfile/compose/k8s 共 40 个环境变量名全部被后端真实读取、
  探针路径 = 免鉴权路径（`/api/health`）。
- `scripts/smoke_api.py` / `verify_contracts.py` / `stress_llm.py` → 均 **exit 0**。
- 前端：`npm run typecheck` 与 `npm run build` 通过（50 modules，336 kB / gzip 101 kB）。
- **Docker 镜像未在本机构建**：Docker CLI 存在但 daemon 未运行
  （`failed to connect to the docker API`）。因此 `docker compose config` 做了 YAML
  与变量替换校验（通过），镜像构建本身未验证 —— 这一点如实标注，未假装已构建。

---

### 6.7 第十一轮实测（2026-09-13）

- **真实网关（DeepSeek `deepseek-flash`）首个基线**：

  | 指标 | 实测值 |
  |---|---|
  | 单任务墙钟 | 314.8s（11 次模型调用，单次 17.6–40.0s） |
  | token | prompt 9,607 / completion 70,112（合计 79,719） |
  | 成本 | $0.040328 |
  | 质量分 | 62 |
  | 门禁结论 | A6 reject（无来源主张 8 项）、A7 revise（重要 1 项） |
  | 产物 | 14 |

  说明：A6 否决是**门禁按设计生效** —— 真实模型会写出无来源的具体数据，
  离线 mock 里这些数据是预置可核对的，因此真实链路下门禁更容易触发。
- **截断修复验证**：5 种截断形态（数组未闭合 / 字符串截断 / 嵌套未闭合 /
  悬空值 / 数组内元素截断）全部能被 `repair_truncated_json` 救回；
  完整 JSON 不受影响。
- **成本口径验证**：10 万输入（9 万命中）+ 5 千输出 →
  计入缓存命中 $0.00447、忽略则 $0.0168（**高估 3.8 倍**）。
- **Docker 实机验证**：

  | 环节 | 结果 |
  |---|---|
  | 镜像构建（`Dockerfile.offline`） | ✅ 成功（受限网络，未访问 Docker Hub） |
  | 容器状态 | ✅ `Up (healthy)` |
  | `/api/health` | ✅ ok=True、checkpointer=sqlite |
  | `/`（前端静态托管） | ✅ 200、含 `<div id="root">` |
  | 完整任务 | ✅ completed / ARCHIVED / 质量分 94 / 18 产物 |
  | `/data` 卷（容器删除重建后） | ✅ 任务、记忆库 7 张、评估历史、trace 全部保留 |
  | compose 校验（两种 dockerfile） | ✅ `docker compose config` 通过 |

- **全部门禁**：`doctor` / `golden`（11 用例 151 断言）/ `verify_contracts` /
  `check_deploy` / `smoke_api` / `stress_llm` / `typecheck` 均 **exit 0**。

### 6.6 第十轮实测（2026-09-13）

- **CI 失败复现与修复**：
  | 场景 | `GET /` | 契约核验 |
  |---|---|---|
  | 无 `dist/`（原 CI） | 404 | FAIL：status、index.html 挂载点 |
  | 有 `dist/`（修复后） | 200 | **exit 0** |

  本地复现命令：`mv dist dist_backup && python scripts/verify_contracts.py`
  —— 得到与 GitHub 逐字相同的失败信息。
- `scripts/doctor.py` → **17 项检查全通过**（新增「视频脚本」）。视频脚本实测：

  | 项 | 结果 |
  |---|---|
  | 判断口径（5 例） | 抖音 / TikTok / 「小红书+点名要脚本」→ 需要；小红书图文 / 公众号长图文 → 不需要 |
  | 抖音规格 | 45s、9:16、5 镜 |
  | 分镜骨架 | 钩子 0-3s → 痛点 3-9s → 方案 9-28s → 佐证 28-39s → 转化 39-45s |
  | 时间轴有序 | ✅｜时长覆盖 45s（与目标一致） |
- `scripts/golden_eval.py` → **11 条用例 / 151 项断言，0 失败**。
  `douyin_short_video` 产物数由 18 → **19**（新增 `video_script`）。
- **竞态修复验证**：`doctor.py` 与 `verify_contracts.py` 连跑 2 轮均 exit 0
  （修复前偶发 `duration=0ms` + `unset` 状态导致失败）。
- 其余关卡（`check_deploy.py` / `smoke_api.py` / `stress_llm.py` / `typecheck` / `build`）→ 均 **exit 0**。

### 6.5 第九轮实测（2026-09-13）

- `scripts/doctor.py` → **16 项检查全通过**（新增「多语言本地化」）。该检查实测输出：

  | 项 | 结果 |
  |---|---|
  | 语言识别（11 个写法） | 全部正确（`en-US`/`English`/`英文`→`en`；`日文`→`ja`；`xx`→回落 `zh`） |
  | 字数口径 | 英文 6 词｜中文 13 字 |
  | 本地化指令 | 英文含「原生创作」+ FTC + `#ad`；中文为空串（不影响既有行为） |
  | 合规覆盖 | `en` 如实声明未覆盖｜`zh` 已接入词库 |
  | 英文产出 | 正文 442 字符，**中文字符 0**；标题中文字符 0 |
  | 中文回归 | 中文 Brief 仍产出中文标题 |
- `scripts/golden_eval.py` → **11 条用例 / 147 项断言，0 失败**。
  新增英文用例 `instagram_en_multilingual`：质量分 92、评估分 78.5、返工 1 轮，
  11 项内容级断言全通过。
- 英文任务全链路中文残留实测：

  | 产物 | 交付物 | 支撑类产物 |
  |---|---|---|
  | 中文字符数 | `copy_draft` 0、`edited_copy` 0、`final_delivery` 0 | `creative_concept` 558、`visual_brief` 606、`knowledge_card` 663 等仍为中文模板 |

  即：**交付物是干净的英文，支撑类产物仍是中文**（已知边界，见踩坑 68）。
- `verify_contracts.py` / `check_deploy.py` / `smoke_api.py` / `stress_llm.py` → 均 **exit 0**。
- 前端：`npm run typecheck` 与 `npm run build` 通过。
- 新增 `README.md`（项目门面 + **需要人工协助的事项清单**）。

---

## 7. 待办 / 后续扩展

- [x] A8 视觉美术指导、A9 渠道与 SEO、A11 记忆与知识库智能体 —— 已完成并进入 LangGraph 主流水线。
- [x] 记忆库「写入 → 检索召回」闭环（RAG）—— 已完成：卡片持久化 + 打分召回 + A1/A2/A4 注入 + 检索接口 + 前端面板。
- [x] 检查点清理 —— 已完成：删除任务时按 `thread_id` 回收 `writes`/`checkpoints`。
- [x] 成本熔断与响应缓存 —— 已完成：一任务一账本、超预算熔断到离线引擎、响应缓存命中复用。
- [x] 共享黑板租约机制与冲突重试 —— 已完成：`acquire_intent/release_task_intents` + 冲突退避与计数。
- [x] 发布排期与 A/B 效果回填闭环 —— 已完成：审批后生成 `publish_plan`，登记发布 + 回填触发 A10 复盘。
- [x] 知识过期下线策略 —— 已完成：`MAX_AGE_DAYS` 自动下线 + `FRESH_DAYS/STALE_DAYS` 新鲜度。
- [x] 删除旧 `server/`（TypeScript）目录 —— 已完成（`git rm -r server/`）。
- [x] 用户使用说明文档 —— 已完成：根目录 `USER_GUIDE.md`。
- [x] 记忆库向量检索 —— 已完成：本地 hashing embedding + 可选 OpenAI `/embeddings`，
  关键词/语义按 `EMBEDDING_WEIGHT` 线性混合（`weight=0` 可退化为纯关键词）。
- [x] 压测与 Prompt 调优基线 —— 已完成：`scripts/stress_llm.py`（延迟分位 / 返工 / 成本缓存 / 租约残留）。
- [x] API 鉴权与任务级租户隔离 —— 已完成：`CREATOR_API_TOKENS` + Bearer/X-API-Token/`?token=` →
  `request.state.tenant`，列表/详情/指标按租户过滤。
- [x] 记忆库的多租户 / 权限 —— **已完成**：卡片带 `tenant` 归属，写入/召回/列表/统计/容量全部按租户分区，
  跨租户既不召回也不可见（`doctor.py` 与 `smoke_api.py` 双重断言）。
- [x] LLM-as-a-Judge 评估流水线 —— **已完成**：六维评分、离线/模型双评估器（失败回退）、
  评估历史与聚合接口、按需评估、advisory/blocking 两档门禁介入。
- [x] 分布式追踪（OpenTelemetry / LangSmith）—— **已完成 OTel 数据模型 + 真实 OTLP 导出**：
  配置 `OTLP_ENDPOINT` 即把同一份 span（id 与本地一致）转发给 collector，
  `docker compose up` 自带 Jaeger；未配置时进程内追踪完全自建、零外部依赖。
- [x] 黄金数据集的期望输出 —— **已完成**：每条用例带 `expect` 块（品牌名 / 关键词覆盖率 /
  禁用表述 / 标题字数上限 / 质量与评估门槛 / 返工预算 / 首轮门禁行为 / `reference_points`），
  共 151 项内容级断言，并已据其发现三个真实缺陷。
- [x] 容器化与生产部署（Docker / K8s）—— **已完成**：多阶段镜像 + compose（含 Jaeger）+
  k8s 清单 + 无需 daemon 的清单核验脚本。
- [x] 多语言本地化（plan.md v2.0）—— **已完成**：中/英/日/韩/西语言画像、
  原生创作、按语言口径校字数、记忆库按语言分区、非中文合规如实告知。
- [x] README 门面文档与人工协助清单 —— **已完成**：`README.md`。
- [x] 视频脚本独立交付物（v2.0）—— **已完成**：`video_script` 产物 +
  知识层判断 + 渠道时间轴 + 前端脚本视图 + 黄金断言 + 自检。
- [x] CI 静态托管失败 —— **已修复**：CI 显式构建前端（`dist/` 被 gitignore）。
- [x] 镜像构建实机验证 —— **已完成**：`Dockerfile.offline` 在受限网络下构建成功，
  容器 healthy、前端可访问、跑通完整任务、`/data` 卷跨容器重建持久化。
- [x] 真实网关接入与首版基线 —— **已完成**：DeepSeek `deepseek-flash` 跑通，
  量到 315s / $0.04 / 79.7k token / 质量分 62，见 §6.7。
- [ ] **真实链路的 Prompt 调优** —— 已有首版基线；代码侧本轮已完成：
  ① 压缩 A3/A5/A6/A11 的输出结构（增加输出体量硬约束，离线回归全绿）；
  ② A4 提示词收紧「无来源数据一律不写」，减少 A6 否决。
  **待做**：③ 用真实网关复测（`python scripts/real_check.py`），按结果继续调；
  ④ 用真实数据校准 `COST_BUDGET_USD` / `TOKEN_BUDGET`（当前 1.0 / 200000 为保守值）。
- [ ] **吊销并更换本次联调用的 DeepSeek API Key** —— 该 Key 已在对话中明文出现，
  应视为已泄露（P0 人工事项）。
- [ ] **非中文市场的法规词库** —— 目前只有中文广告法词库；英文/日文/韩文/西语的合规红线
  仅写入提示词，未经法规校验（P0 人工事项）。
- [ ] **mock 支撑类产物的本地化** —— 交付物（标题/正文/CTA/标签/最终交付件）已是完整目标语言，
  但创意概念、内容策划、视觉指导、渠道适配、效果报告、知识卡片仍是中文模板。
  真实模型模式下由本地化指令驱动，不受此限。
- [x] **数字人渲染（v2.0）开发样例** —— **已完成**：`app/core/digital_human.py` 提供
  `sample` 内置引擎（离线确定性模拟「排队 → 渲染 → 完成」+ 按 `video_script` 生成渲染清单，
  无口播/时长偏差显式告警）与 `http` 适配样例（「POST 建任务 → GET 查状态」最小契约，
  未配置 API URL 时**显式失败**）；生命周期读取时惰性推进、按租户隔离、任务删除一并回收；
  API `POST/GET /api/tasks/{id}/digital-human`（无脚本 409）+ 前端「数字人渲染」面板 +
  doctor / verify_contracts 断言。**正式接入哪家第三方服务属产品形态决策，由使用者决定（P2）**。
- [x] 跨进程 trace 上下文传播与采样 —— **已完成**：W3C `traceparent` 解析/生成
  （`parse_traceparent` / `format_traceparent`），入站 `POST /api/tasks` 延续远端 trace_id
  并把首个根 span 挂到远端之下；出站 webhook（发布投递 / 数字人网关）自动携带当前 span 的
  traceparent；`OTEL_TRACES_SAMPLER` / `_ARG` 采样**只作用于导出面**（OTLP + 落盘），
  进程内轨迹始终完整，入站采样标记优先于本地比例，计数在 health/metrics 可见；
  坏头一律忽略，绝不影响业务请求。
- [ ] 横向扩展（多副本）—— 需先把共享黑板与检查点换成 PostgreSQL + Redis。
- [ ] 多平台真实一键发布 —— 已提供平台无关的 webhook 投递通道与到期队列，
  平台私有授权/限流需由发布网关承接，仍在「登记事实 + 复盘」范围内。

---

## 8. 变更记录

- **2026-09-12**
  - 【前端 + TS 后端】完成后端全部模块与前端全部界面；`tsc --noEmit` 通过，`npm run build` 成功；
    端到端冒烟：任务 1 轮返工后完成，质量分 94，产物 14，归档成功。建立本记忆文档。
  - 【Python 全量重写】后端迁移为 Python 3.12 + FastAPI + LangGraph：
    - 逐文件移植配置/日志/核心层/LLM 层/知识层/8 个智能体 + registry。
    - 用 LangGraph `StateGraph` 重写编排器，门禁环用条件边表达，人工审批用 `interrupt()`，
      `SqliteSaver` 提供断点续跑；编写 `app/api/routes.py` 复刻全部 `/api` 与 SSE 契约。
    - 新增 `CREATOR_DATA_DIR` 便于隔离测试；新增 `scripts/smoke_api.py` 端到端验收脚本。
    - 验证：`scripts/doctor.py` 通过；`scripts/smoke_api.py` 通过（exit 0）；
      前端 `dist/` 由后端正确托管；3 任务并发各自跑完。
    - 排查记录：修复了「`subprocess.PIPE` 未消费导致子进程写阻塞、伪装成服务端死锁」的问题（见踩坑 9），
      并据此排除了对 `SqliteSaver` 的误判。
  - 【第二阶段智能体：A8 视觉 / A9 渠道 SEO / A11 记忆知识库】
    - 契约：`Phase` 增加 `VISUAL_ADAPT / CHANNEL_ADAPT / MEMORY`，`ArtifactType` 增加
      `visual_brief / knowledge_card`（共 13 种），并补齐 `PHASE_LABEL / ARTIFACT_LABEL`。
    - 知识层：新增 `app/knowledge/visual.py` 视觉风格库（风格/情绪/构图/光线/色板/Prompt 片段/负向词）；
      `industry.py` 增加 `SEO_PATTERNS`、`CHANNEL_TITLE_LIMIT`、`CHANNEL_PUBLISH_SLOTS`。
    - 智能体：新增 `a8_art_director.py`、`a9_channel_seo.py`、`a11_memory.py`；
      `registry.AGENTS` 覆盖 A1–A11，`PLANNED_AGENTS` 仅剩 A0。
    - 离线引擎：`mock.py` 新增 `A8.visual / A9.channel / A11.memory`（含标题超限压缩、短视频分镜两个分支）。
    - 流水线：`review ─pass→ A8 → A9 → A10 → A11 → approval`；`escalation` 的覆盖分支同步改指向 A8；
      A0 任务卡与蓝图补齐 A8/A9/A11；`final_delivery` 装配 `channels / visual / knowledge_cards`。
    - 前端：类型自持于 `src/lib/types.ts`（不再 import `server/`），`ArtifactViewer` 新增视觉/渠道/知识视图。
    - 修复（本轮冒烟拦下的真实缺陷）：`AgentRunContext` 缺 `artifacts` 字段导致 A11 崩溃；
      A11 `handoff.to="human"` 违反 `AgentId | None` 契约（见踩坑 12–14）。
    - 验证：`scripts/doctor.py` 通过；`scripts/smoke_api.py` **通过（exit 0）**
      （17 产物 / 13 类型 / 16 turns，A8/A9/A11 节点均 done）；`npm run build` 通过。
- **2026-09-13**【记忆库 RAG 闭环 + 工程加固】
  - 补齐《creator.md》中 A11「存储 + 为其他智能体提供 RAG 检索」的**召回**那一半
    （此前只有「写入卡片」，没有「被复用」）：
    - 新增 `app/knowledge/memory.py`：`MemoryStore` 把知识卡片与模板固化到 `data/memory.json`
      （跨任务存活、`MAX_CARDS=500` 淘汰最旧、`sha1(kind|title|content)` 幂等去重、进程内 `RLock`）；
      `retrieve()` 用中文 2-gram + 英文分词算文本相关度，叠加同品牌/同渠道/同行业加成，
      `MIN_SCORE=0.2` 过滤噪声，`exclude_task` 避免自我召回。
      另提供 `render_hits()` / `evidence_from_hits()` 供提示词与证据复用同一段文案。
    - 契约：`AgentRunContext` 新增 `memory` 字段；`app/agents/base.py` 新增 `memory_block(ctx)`。
    - A11：执行前召回历史资产（写入产物 `content["recalled"]`，即文档要求的「检索结果」输出项）
      并拼进提示词；执行后 `remember()` 入库，`archive.indexed_cards` 记录新增条数，
      evidence 追加 `记忆库/<task>` 来源。
    - A1/A2/A4：提示词注入「历史可复用资产」段落，上下文透传 `memory`；
      `mock.py` 新增 `memory_hits()` / `memory_evidence()`，让离线引擎同样体现复用（含 `reuse_suggestions` 与 risks 调整）。
    - 编排：`Orchestrator._memory_hits()` 在 A1/A2/A4 执行前统一检索并注入（`MEMORY_RECALL_AGENTS`），
      并发一条 debug 事件记录召回；避免每个智能体各写一份检索逻辑。
    - 接口：`GET /api/memory`（统计 + 卡片列表）、`POST /api/memory/search`（检索召回）。
    - 前端：设置抽屉新增「记忆库」标签页 → `src/components/MemoryPanel.tsx`
      （统计 chips + 检索框 + 命中项分数/理由/复用建议 + 全部卡片表格）。
  - 工程加固：
    - **检查点回收**：`Orchestrator.purge_checkpoints()` + `DELETE /api/tasks/:id` 按 `thread_id`
      清理 `writes`/`checkpoints`（防御式处理表缺失），响应返回 `purged_checkpoints`。
    - **自检不再只跑生成器**：`doctor.py` 新增 `check_memory_recall()`，直接构造 `AgentRunContext`
      调用真实 `a11_memory.run()`，两轮验证「写入 7 条 → 第二轮召回 5 条」；
      同时 `isolate_data_dir()` 让自检默认落临时目录，不再污染开发环境 `data/`（踩坑 12 的补救）。
    - **验收补断言**：`smoke_api.py` 增加 `/api/memory`、`/api/memory/search`、
      跨任务召回（A11 `recalled` 5 条 + A1 evidence 含 `记忆库/…` 2 条）、`memory.json` 持久化、
      删除任务回收检查点（55 行）。
  - 验证：`doctor.py` 通过（含 RAG 闭环）；`smoke_api.py` **通过（exit 0）**；`npm run build` 通过（46 modules）。
  - **版本管理**：建立首次提交 `476abc0`（101 文件 / 25661 行，分支 `master`）；
    `.gitignore` 补上 `__pycache__/`、`*.py[cod]`、`.pytest_cache/`（此前会误提交 41 个 `.pyc`）。
    远端 `origin = https://github.com/Wilyu-45/creatoragent.git` 已存在 `main`（`bd81a13`）。
    当前环境直连 `github.com:443` 持续超时（无代理、`Direct access`），**推送尚未完成**；
    恢复网络后按下面顺序执行（远端 `main` 与本提交**历史无关联**，因此必须先 fetch + rebase，禁止强推）：

    ```
    git fetch origin main
    git rebase origin/main          # 把 476abc0 接到 bd81a13 之上
    git branch -M master main       # 本地分支改名以对齐远端默认分支
    git push -u origin main
    ```

    若 `rebase` 出现冲突，先 `git ls-tree -r --name-only origin/main` 看清远端已有哪些文件再决定保留策略。

- **2026-09-13（第三轮：剩余条目闭环 + 用户文档）**
  - 依据《plan.md》与《creator.md》清点并落地尚未实现的条目：
    - **黑板 Intent 租约**：`blackboard.acquire_intent/release_task_intents`，编排层登记/续租/冲突退避 + `intent_conflicts` 计数。
    - **成本核算 / 预算熔断 / 响应缓存**：新增 `app/llm/pricing.py`、`cost.py`、`cache.py`；
      `chat()` 前置熔断 + 缓存复用 + 计费；配置项 `cost_budget_usd/token_budget/llm_cache`。
    - **知识过期下线与新鲜度**：`memory.py` 的 `MAX_AGE_DAYS/FRESH_DAYS/STALE_DAYS` + 同分优先新鲜 + 统计。
    - **发布闭环**：`publish_plan` 产物、`/tasks/:id/publish`、`/tasks/:id/feedback`、A10 发布后复盘（`A10.review`）。
    - **删除旧 `server/`**：`git rm -r server/`，同步 `tsconfig.json` 与 `package.json`。
    - **用户文档**：新增 `USER_GUIDE.md`（启动、界面导览、实操流程、配置表、接口速查、FAQ）。
  - 前端同步：`publish_plan` 视图、`PublishPanel` 发布/回填、指标面板成本与缓存区块、
    设置抽屉成本分组、记忆库新鲜度 chips。
  - 验证：`scripts/doctor.py` 通过；`scripts/smoke_api.py` **通过（exit 0，含发布闭环与 cost/cache/leases 断言）**；
    `npm run typecheck` 与 `npm run build` 通过。

- **2026-09-13（第四轮：向量检索 + 自动投递 + 鉴权租户 + 压测脚本）**
  - 依据《plan.md》2.2.3「RAG Server」、D17「安全加固」、v2.0「自动发布」与 MEMORY 待办收口：
    - **向量检索**：新增 `app/knowledge/embedding.py`（本地 hashing embedding + 可选 OpenAI `/embeddings`，
      远端失败静默回退）；`memory.py` 改为 `keyword*(1-w) + vector*w` 混合打分（默认 `w=0.35`，可退化为纯关键词），
      `MemoryHit.vector_score`、`stats().embedding/vector_indexed`。
    - **发布自动投递**：新增 `app/core/publisher.py`（时段解析 → `due_at`（已过顺延到次日）→ webhook 退避重试）；
      排期项新增 `due_at/dispatch_status/attempts/last_error`；编排新增 `dispatch_publish / publish_queue / dispatch_due`；
      lifespan 支持 `PUBLISH_AUTO_DISPATCH` 后台巡检（默认关）。
    - **鉴权与租户隔离**：`CREATOR_API_TOKENS`（`tok` 或 `acme:tok`），`server._install_auth` 中间件，
      三处取 Token（Bearer / X-API-Token / query），`TaskRecord.tenant` + 路由统一租户过滤（越权 404）。
    - **压测**：新增 `scripts/stress_llm.py`（延迟分位 / 返工 / 质量 / 成本缓存 / 租约残留）。
    - **前端**：api 客户端鉴权头与 SSE token、`PublishPanel` 投递列与按钮、`SettingsDrawer` 三组新设置、
      `MemoryPanel` 向量 chips。
    - **验收**：`doctor.py` 新增 embedding/publisher/auth 三检；`smoke_api.py` 新增对应断言。
  - 验证：`scripts/doctor.py` 通过（五项检查全绿）；`scripts/smoke_api.py` **通过（exit 0）**；
    `npm run typecheck` 与 `npm run build` 通过（47 modules）。

- **2026-09-13（第五轮：租户记忆库 + LLM-as-a-Judge + 断点续跑可见性）**
  - 依据《plan.md》4.3 D14「LLM-as-a-Judge 评估流水线」、2.5「黄金数据集评测」、
    D17「数据隔离」与《creator.md》A11「管理版本、标签、权限与过期知识」中的**权限**一项：
    - **记忆库租户隔离**：`MemoryCard.tenant` + 去重键含租户（`sha1(tenant|kind|title|content)`）+
      `remember/retrieve/list_cards/stats` 全量 `tenant` 参数 + `_evict` 按租户计量；
      `AgentRunContext.tenant` 由编排器注入，A11 与 `_memory_hits()` 的读写都带租户；
      `/api/memory*` 按 `request.state.tenant` 过滤，`stats` 新增 `tenant/tenants/global_total`。
    - **LLM-as-a-Judge**：新增 `app/core/judge.py`（六维加权评分；离线规则评估器 +
      LLM 评估器，后者任何异常静默回退并标注）与 `app/core/evaluations.py`
      （评估历史落 `data/evaluations.json`）；编排在门禁时刻与交付前各评估一次并发
      `judge.scored` 事件；`decide_gate(judge=, judge_mode=)` 支持 off/advisory/blocking；
      `judge.weight` 把评估分按比例混入综合质量分（0 = 完全不影响，可回滚）。
    - **新接口**：`GET /api/evaluations`、`POST /api/tasks/{id}/evaluate`、
      `/api/metrics.system.judge`、`/api/settings` 的 judge 组、`/api/health.checkpointer`。
    - **修复（自检拦下的真实缺陷）**：测试用 `tempfile.mkdtemp`（`0o700`）目录在本机沙箱下不可写，
      导致记忆库落盘静默失败、**SQLite 检查点静默退回内存实现**（断点续跑从未被真正覆盖）。
      新增 `app/core/util.make_temp_dir()` 并让三个脚本改用 `.doctor-data/`；
      `Orchestrator.checkpointer_kind/error` 经 `/api/health` 暴露，前端顶栏显式告警。
    - **前端**：新增 `JudgePanel.tsx` 与「评估报告」标签页；任务头部评估分 Chip；
      `MetricsPanel` 评估区块；`SettingsDrawer` 评估设置组；`MemoryPanel` 租户 chips 与列；
      `Topbar` 断点续跑告警；`types.ts` 新增 `judge.scored` 事件类型。
    - **验收**：`doctor.py` 扩到八项检查；`smoke_api.py` 新增评估断言与
      **独立鉴权实例的租户隔离验收**。
  - 验证：`scripts/doctor.py` 通过（八项全绿）；`scripts/smoke_api.py` **通过（exit 0）**；
    `scripts/stress_llm.py -n 8 -c 4` 完成率 100% / 租约残留 0；
    `npm run typecheck` 与 `npm run build` 通过（48 modules）。

- **2026-09-13（第六轮：黄金数据集 + 回归门禁 + CI）**
  - 依据《plan.md》4.3 D13「黄金数据集构建」、D14「Prompt 优化迭代（基于评估结果）」、
    D18「CI/CD 流水线」与 2.5「系统级：黄金数据集评测」清点并落地：
    - **mock 引擎补 `judge.evaluate`**：此前默认 mock 下 `judge_with_llm` 必然回退，
      `_normalize_llm_axes` 的成功分支离线永远跑不到（最容易因字段漂移而坏的正是它）。
      新增 `generate_judge()`，用规则评估器的真实打分作为「模型回答」。
    - **黄金数据集**：`golden/briefs/*.json`（10 条，覆盖 7/7 渠道）+ `golden/baseline.json`；
      `app/core/golden.py`（加载 / 覆盖矩阵 / 基线读写 / `compare` 判定）；
      `app/core/golden_runner.py`（执行器 + 后台任务状态机）；
      `scripts/golden_eval.py`（CLI，退出码即结论）。
    - **判定规则**：分数回退超容差、**返工轮次增加**、**监管用例首轮合规未被拦截**
      三类均判 `regressed`；全量缺用例算失败；部分运行只对范围内判定（`scope`/`partial`）；
      跨 `rubric` 时置 `rubric_mismatch` 并拒绝直接比较。
    - **接口**：`GET /api/golden`、`POST /api/golden/run`（202 + 后台执行）、`POST /api/golden/reset`。
    - **CI**：`.github/workflows/ci.yml` —— 后端五道关卡（doctor / golden / verify_contracts /
      smoke / stress）+ 前端 typecheck&build + 文档一致性检查；失败时上传服务端日志。
    - **自检**：`doctor.py` 新增第 12 项，用 9 个子断言证明回归判定器**真的会判回归**
      （含「容差内不该误报」的反向断言）。
    - **前端**：新增 `GoldenPanel.tsx`（设置抽屉「回归测试」标签页：
      跑全量 / 快速跑 3 条 / 清除结果、进度条、基线对比表、覆盖矩阵与用例清单）。
  - 验证：`doctor.py` **12 项全绿**；`golden_eval.py` 全量 **10/10 持平（exit 0）**、
    `--coverage` 7/7 渠道；`smoke_api.py` 与 `verify_contracts.py` 仍 **exit 0**；
    `npm run typecheck` 与 `npm run build` 通过。

- **2026-09-13（第七轮：调用轨迹与分布式追踪）**
  - 依据《plan.md》2.4「可观测性：创建覆盖整个 Agent session 的 span，而非仅追踪单次模型调用」
    与 MEMORY 待办收口：
    - **新增 `app/core/tracing.py`**：OTel 数据模型的进程内实现（W3C trace_id/span_id、
      parent、kind、attributes、status、嵌套 span、self-time 占比、OTel 形状导出）。
      thread-local span 栈（编排在工作线程、HTTP 在事件循环），
      单任务 span 上限 600、进程内 trace 上限 200，终态落盘 `data/traces/<task_id>.json`。
    - **埋点**：`graph.invoke#<turn>`（根）／`human.wait`（独立根）／
      `A<n>.<produces>`（智能体）／`gate.review`（门禁）／`judge.evaluate`（评估）／
      `llm.<purpose>`（client，带 provider/model/token/成本/缓存/降级）／
      `memory.retrieve`（RAG）／`publish.dispatch`（投递）。
    - **修复（实测发现的真实缺陷）**：不包 `graph.invoke` 会让所有节点 span 成为根，
      trace 树退化成「13 个并列根节点」，self-time 全部失真；包一层后根降到 3 个。
      并把「根 span 少于总数」固化为自检与契约断言。
    - **事件关联**：`AgentEvent` 新增 `trace_id` / `span_id`，由事件总线在 `publish()` 里
      统一注入（调用方无需传参），排查时不必靠时间戳猜事件归属。
    - **接口**：`GET /api/tasks/{id}/trace`、`/api/metrics.system.tracing`；
      `DELETE /api/tasks/{id}` 一并回收 trace。
    - **前端**：新增 `TracePanel.tsx` → 任务详情「调用轨迹」标签页
      （可折叠 span 树 + 瀑布图 + span 属性内联 + 耗时排行）；
      `MetricsPanel` 新增「调用轨迹」区块。
    - **验收**：`doctor.py` 第 13 项 `check_tracing()`；`verify_contracts.py` 新增 14 项断言；
      `smoke_api.py` 新增 trace 端点断言（含跨租户 404）。
  - 验证：`doctor.py` **13 项全绿**（span=42 / 根=3 / llm=15 全部正确嵌套 / OTel 导出形状齐全）；
    `verify_contracts.py` 与 `smoke_api.py` **exit 0**；`golden_eval.py` 仍 **exit 0**；
    `npm run typecheck` 与 `npm run build` 通过（50 modules）。

- **2026-09-13（第八轮：OTLP 真实导出 + 内容级期望 + 容器化）**
  - 依据《plan.md》2.4「OpenTelemetry + Jaeger」、4.3 D13「黄金数据集构建」、
    D18「生产环境部署」与 MEMORY 待办收口：
    - **OTLP 真实导出**：新增 `app/core/otel.py`；配置 `OTLP_ENDPOINT` 后经 OTel SDK
      OTLP/HTTP exporter 转发。**手工构造 `ReadableSpan`** 而非走 `start_span`，
      以保证导出的 trace_id/span_id 与本地 JSON 完全一致（否则 Jaeger 与本地的 id 对不上）。
      默认不加载 OTel（`doctor.py` 断言）；智能体归属沿 span 树继承到 `llm.*`。
    - **黄金数据集内容级期望**：`golden/briefs/*.json` 新增 `expect` 块；
      `app/core/golden.py` 新增 `check_expectations()` 与 `_is_negated()`（按小句判定否定语境）；
      内容级失败一律算回归。
    - **修复三个真实缺陷**：标题压缩 off-by-one、交付标题未受渠道上限约束、
      合规扫描把免责声明误判为违规。
    - **容器化**：`Dockerfile`（多阶段、非 root、健康检查）、`.dockerignore`、
      `docker-compose.yml`（应用 + Jaeger）、`deploy/k8s.yaml`、
      `scripts/check_deploy.py`（无需 daemon 的清单核验）。
    - **前端**：`Topbar` 显示 OTLP 接入状态；`SettingsDrawer` 新增「调用轨迹与 OTLP 导出」区块。
    - **配置**：`RuntimeConfig.tracing` + `public_config().tracing` + `/api/health.otlp`；
      `.env.example` 补齐全部配置项。
    - **依赖**：`requirements.txt` 新增 opentelemetry-api/sdk/exporter-otlp-proto-http
      （仅在配置 OTLP 后才 import）。
  - 验证：`doctor.py` **15 项全绿**；`golden_eval.py` **136 项断言 0 失败**；
    `check_deploy.py` 通过；`smoke_api.py` / `verify_contracts.py` / `stress_llm.py` 均 **exit 0**；
    `npm run typecheck` 与 `npm run build` 通过。
    **镜像构建未实机验证**（本机 Docker daemon 未运行），已记入待办。

### 4.1h 后端 · 第九轮：多语言本地化 + README + 人工事项清单（2026-09-13，已完成）

- **多语言本地化（plan.md v2.0 的第一项）**：
  - 新增 `app/knowledge/language.py`：**语言画像**（中/英/日/韩/西），每个语言包含
    本地渠道、标题口径与上限、正文长度建议、表达惯例、合规红线、度量与日期格式、
    广告披露要求、以及**是否已接入可执行词库**。
  - `Brief` 新增 `language` 字段；`parse_brief()` 做**归一化**
    （`en-US` / `English` / `英文` → `en`），无法识别时回落 `zh`。
  - **原生创作而非翻译**：`localization_block(ctx)` 把本地化指令注入 A4 等创作智能体，
    明确要求「用目标语言原生创作，不要先写中文再翻译」。
  - **字数口径按语言**：`title_measure()` 英文按**词**、中日韩按**字**；
    `title_limit_for()` 非中文用语言画像的上限。交付标题压缩也改用该口径 ——
    Instagram 的 12 **词**上限若按字符算（60+ 字）会得出完全错误的结论。
  - **记忆库按语言分区**：`MemoryCard.language` + `remember/retrieve` 的 `language` 参数；
    英文资产不会被中文任务当语气基线复用（复用语言不对的资产比不复用更糟）。
  - **合规诚实性**：非中文市场**没有自动词库**，A7 显式声明「需人工复核」、
    写入当地红线（如 FTC 披露要求）并强制 `needs_human_review` —— 不假装检查过了。
  - **离线链路也支持英文**：mock 引擎新增英文分支（`_english_strategy` / `_english_copy`），
    否则默认 `LLM_PROVIDER=mock` 时「多语言」在离线环境就是假的。
  - 接口：`/api/knowledge` 新增 `languages` 与 `language_compliance`（各语言的合规覆盖情况）。
- **黄金数据集新增英文用例**：`instagram_en_multilingual`，
  验证原生英文（正文中文字符数必须为 0）、标题按词计上限、非中文合规如实告知。
  共 **11 条用例 / 147 项断言**。
- **修复两个真实缺陷**：
  1. `mock.as_brief()` **没有透传 `language`** —— 本地化分支永远走不到（静默回落 `zh`）。
     这类「字段漏传」在只有单一语言时完全不可见。
  2. 黄金数据集的标题断言用**字符数**度量，而交付层已按**词**处理，
     导致 12 词上限的英文标题被判成「超出上限 12 字」。断言与被断言对象必须同口径。
- **README.md**：新增项目门面文档（核心能力、快速开始、架构、智能体清单、门禁、
  评估与回归、可观测性、多语言、部署、配置、接口、开发验证、项目状态、
  **需要人工协助的事项**、文档导航）。
- **需要人工协助的事项清单**（写进 README 与 `USER_GUIDE.md`）：
  P0 四项（镜像实机验证、生产令牌、真实网关定基线、非中文合规人工审核）、
  P1 四项（品牌资产、发布网关、行业用例、行业词库）、P2 四项（视频脚本/数字人形态、
  横向扩展方案、Jaeger 生产实例、人工抽检机制）。

### 4.1i 后端 · 第十轮：CI 修复 + 视频脚本 + 竞态修复（2026-09-13，已完成）

- **修复 CI 失败（用户报障）**：GitHub 上 `verify_contracts.py` 报
  `status` 与 `index.html 挂载点` 两项失败。根因是 **`dist/` 被 .gitignore 排除，
  而 CI 的 backend job 只装了 Python 依赖、从没构建前端** →
  后端只在 `dist/` 存在时才挂载静态托管与 SPA 回落路由 → `GET /` 返回 404。
  本地复现：`mv dist dist_backup` 后跑契约核验，得到逐字相同的失败。
  修法：CI backend job 增加 `actions/setup-node` + `npm ci && npm run build`
  （排在契约核验之前）；核验脚本里补一条前置断言与可操作提示。
- **视频脚本（plan.md v2.0 的最后一项功能）**：
  - 新增 `app/knowledge/video.py`：`needs_video_script()`（短视频渠道 / 交付物点名 /
    渠道形态含视频特征，任一命中才产出）、`video_spec()`（各渠道时长/画幅/镜头数）、
    `script_skeleton()`（按时间占比生成分镜骨架）、`required_sections()`。
  - 契约新增 `ArtifactType = "video_script"` + 标签；前端类型同步。
  - A8 在产出 `visual_brief` 的同时追加 `video_script` 产物（同一智能体两项产物，
    **不新增智能体**）：钩子 / 分镜（时长·画面·口播·字幕·机位）/ 口播表 / 字幕表 /
    CTA / 拍摄要点 / 合规注意。
  - `normalize_video_script()` 规整结构并保证时间轴单调、每镜时长 ≥ 1s。
  - mock 新增 `A8.video_script` 生成器。
  - 前端新增 `VideoScriptView`：以**时间轴**为主视图（比例条 + 分镜表 + 覆盖率 +
    「存在无口播分镜」告警），而不是按字段平铺 —— 脚本可用性取决于时间轴连贯性。
  - 黄金数据集：`douyin_short_video` 断言 `expect_video_script: true`
    （并检查分镜/口播数量与时间轴有序），`xiaohongshu_food` 断言 `false`
    （图文渠道不该被塞入无关脚本）。
  - `doctor.py` 新增第 17 项「视频脚本」（判断口径 5 例 + 骨架时间轴 + 时长覆盖）。
- **修复一个真实竞态**：自检与契约核验读取 trace 时，任务虽已是终态，
  但编排线程可能仍在 `finally` 里收尾（`finish_trace` 才计算总耗时与 self-time）——
  于是偶发看到 `duration=0ms` 与未收尾的 `unset` span 状态。
  修法：自检**等待 trace 收尾**（duration > 0，上限 15s）；
  同时把「span 状态必须全是 ok」放宽为「**不得有 error**」——
  OTel 里 `unset` 是合法的「未显式设置状态」，要求全 ok 会在收尾边界上偶发失败。
  连跑两轮确认不再抖动。
- **修复一个自检脚本自身的缺陷**：`verify_contracts.check()` 只有
  `(label, actual, expected)` 三个参数，我把「失败提示文案」传进了 `expected`，
  于是断言变成「`True == '请先执行 npm run build'`」，必然失败 ——
  即我新加的检查自己写错了。修法是给 `check()` 增加独立的 `detail` 参数。
  **教训：给测试加检查时，检查本身也要先看到它「通过」与「失败」两种表现。**

### 4.1j 后端 · 第十一轮：真实网关接入 + Docker 实机验证（2026-09-13，已完成）

- **真实网关（DeepSeek）接入并跑通**：`.env`（已 gitignore）指向 `api.deepseek.com/v1`，
  模型 `deepseek-flash`。首个真实任务**立即暴露一个致命缺陷**：
  A3 的结构化输出被 `max_tokens` 截断，JSON 未闭合，`extract_json` 直接放弃解析，
  整个智能体以「无法解析为 JSON」失败 —— 而内容其实已产出大半。
  修法四件套：
  1. `repair_truncated_json()`：按未闭合容器补全、丢弃不完整元素、截断字符串补引号；
  2. `LLMResponse.finish_reason` 透传，**区分「被截断」与「模型胡说」**；
  3. 新增 `TruncatedOutputError`，编排层**针对性地放大输出上限后重试**
     （`boost_max_tokens`，thread-local，对智能体透明）；
  4. 输出上限默认 4096 → 8192。
- **成本口径修正（关键）**：DeepSeek 输入侧「缓存命中」比未命中便宜约 50 倍
  （2026-09-10 定价：空闲时段命中 ¥0.02/M、未命中 ¥1/M、输出 ¥4/M）。
  原先只按单一输入单价计费，会**高估成本 3.8 倍**（实测数字），
  进而过早触发熔断、把本该走真实模型的任务降级到离线引擎。
  现在 `price_of()` 返回 `(未命中, 命中, 输出)` 三档，`LLMUsage.cached_tokens`
  从 `prompt_tokens_details.cached_tokens` 透传，账本新增 `providerCachedTokens`。
  模型匹配改为**长名优先**，避免 `deepseek-v4-pro` 被前缀规则抢占（价差 3 倍）。
- **输出膨胀治理**：实测单任务 completion 达 6.5–7 万 token，其中相当部分是
  模型附送的 `reasoning` / `notes` 等**无人消费的字段**。
  新增 `strip_unknown_keys()`：按各智能体的 SCHEMA 裁掉顶层多余字段
  （`ALWAYS_KEEP_KEYS` 保证 `confidence` / `risks` / `evidence` 等契约字段永不被裁）。
- **验证脚本必须强制离线**（本轮踩到的真实坑）：本机 `.env` 指向真实网关后，
  `doctor.py` 与 `golden_eval.py` 跟着走真实模型 →
  一条任务 5 分钟，自检超时失败；11 条黄金用例跑不完。**验证脚本断言的是契约与逻辑，
  必须秒级、可复现、不花钱**。新增 `force_offline_provider()`，
  在 doctor / golden / smoke / verify_contracts 里默认固定 `mock`
  （`real_check.py` 才是量真实链路的入口）。
  另外两个顺序错误也一并修掉：`force_offline_provider()` 写的是 `os.environ`，
  而 env dict 由 `os.environ` 展开 —— **必须先调用再构造 env**，否则白设。
- **Docker 实机验证（原 P0 事项，已完成）**：
  - 标准 `Dockerfile` 走不通：`registry-1.docker.io` 不可达（连认证 token 都取不到）。
  - 新增 `Dockerfile.offline`：只用**本地已有镜像**作底座
    （dify-api 提供 Debian+Python3.12+pip，dify-web 提供 Node 22），
    Python 依赖从**镜像源**装（实测容器内 PyPI 可达，只是 Docker Hub 不通）。
  - 一路踩掉三个坑（都记在踩坑 74–76）：底座走 venv 的 python 没 pip、
    底座预设 `NODE_ENV=production` 导致 npm 跳过 devDependencies（vite 找不到）、
    底座自带 ENTRYPOINT 去启动 gunicorn。
  - 实测通过：镜像构建成功 → 容器 `healthy` → `/` 返回前端（含 `<div id="root">`）
    → 完整任务跑通（18 产物 / 质量分 94）→ **`/data` 卷跨容器删除重建后任务、
    记忆库、评估历史、trace 全部保留** → compose（标准与离线两种 dockerfile）校验通过。
- **动态端口**：Windows 保留成片端口区间，写死端口会 `WinError 10013`
  （`free_port(8811)` 实测返回 10012，说明 8811 确实在排除区内）。
  新增 `free_port()`，四个起服务的脚本改为动态取端口。
- **新增 `scripts/real_check.py`**：真实链路核验（延迟 / token / 成本 /
  提供方缓存命中率 / 门禁结论 / 交付文本预览），是量真实账的入口。

### 4.1k 后端 · 第十二轮：数字人样例 + trace 传播/采样 + Prompt 收紧（2026-09-13，已完成）

- **数字人渲染（开发样例，`app/core/digital_human.py`）**：
  - **定位**：渲染本身**不在本系统内实现** —— HeyGen / D-ID / 腾讯智影等的授权、形象库、
    计费与回调协议差异极大，「接哪家、要不要接」是产品形态决策。系统提供**可回归的接入样例**，
    让 API / UI / 下游流程的联调在买任何服务之前就能发生。
  - `sample` 内置引擎（默认，零依赖）：离线确定性模拟「排队 → 渲染 → 完成」，
    并按 `video_script` 生成**渲染清单** `build_manifest()`（每镜台词 / 字幕 / 机位 /
    起止时间 / 是否有口播；无口播分镜与总时长偏差**显式告警**，不假装没问题）。
  - `http` 适配样例：对接「POST 建任务 → GET 查状态」最小契约的任意网关
    （自建渲染农场、n8n 均可）；`DIGITAL_HUMAN_API_URL` 未配置时**显式失败，绝不假装成功**；
    失败在作业上记账（`attempts` / `error`），不向上抛。
  - **生命周期惰性推进**：状态在读取时按流逝时间（sample）或远端状态（http）计算，
    不靠后台线程 —— 服务空转时零任务；按租户隔离；任务删除时作业一并回收（`drop_task`）。
  - 接缝：`POST/GET /api/tasks/{id}/digital-human`（无 `video_script` → 409）；
    事件与 `digitalhuman.render` span 随任务轨迹对齐；前端 `DigitalHumanPanel.tsx`
    在脚本产出后出现（创建作业 / 进度条 / 渲染清单分镜表 / 成片地址）。
- **W3C traceparent 跨进程传播（`app/core/tracing.py`）**：
  - `parse_traceparent()` / `format_traceparent()` / `RemoteParent`：严格校验版本与全零 id，
    **坏头一律忽略**，绝不影响业务请求。
  - 入站 `POST /api/tasks` 解析 `traceparent` → 本地 trace **沿用远端 trace_id**，
    首个根 span 挂到远端 span 之下（Jaeger 里拼成完整一棵树）。
  - 出站 webhook（发布投递 / 数字人网关）自动携带当前 span 的 `traceparent` ——
    下游服务可以接着传播，形成端到端链路。
- **导出面采样（OTel 语义对齐）**：`decide_sampling()` 支持 `parentbased_always_on/off`、
  `parentbased_traceidratio`、`always_on/off`、`traceidratio`；入站 `traceparent` 的采样标记
  优先于本地比例。**采样只作用于导出面**（OTLP + 落盘）：未采样的 trace 不转发、不落盘，
  但**进程内轨迹始终完整** —— Jaeger 里查不到它是预期行为。计数在 `/api/metrics` 与
  `/api/health` 可见。关键取舍：采样决策在 trace 创建时一次性做出（trace 级），
  而不是每个 span 各自决定 —— 同一棵树要么全导出要么全不导出，不会出现「半棵树」。
- **Prompt 收紧（真实网关复测前的代码侧准备，离线回归全绿）**：
  - A4：新增「**无来源数据一律不写**」硬规则（针对真实链路实测的 8/11 无来源主张被 A6 否决）；
  - A3 / A5 / A6 / A11：增加输出体量约束（topics 恰好 3 条、标题备选 5 条、每项一句话等），
    压 completion token（此前实测单任务 completion 达 7 万）。
- **`.env.example` 新增**：`DIGITAL_HUMAN_PROVIDER / _API_URL / _API_KEY / _AVATAR / _TIMEOUT_MS`、
  `OTEL_TRACES_SAMPLER / _ARG`。
- **自检扩展**：`doctor.py` 新增 `check_trace_propagation()`（坏头 / 全零 id / 未采样标记 /
  沿用远端 trace_id / 出站携带）与 `check_digital_human()`（清单告警、惰性推进、租户隔离、
  http 失败路径与回收），共 **16 项**；`verify_contracts.py` 增加数字人样例与传播采样闭环（实测约 20s）。
- **验证结果（本轮实测）**：`doctor.py` **16 项全绿**；`verify_contracts.py` 通过（~20s）；
  `golden_eval.py` **11 用例 11/11 持平，151 项内容级断言 0 失败**。
- **五份文档同步**：README / plan / creator / USER_GUIDE / MEMORY 全部对齐当前状态
  （数字人样例定位、传播与采样、doctor 16 项、黄金 11/151 计数、P0/P2 清单一致化）。
