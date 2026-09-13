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
# 前端（构建后由后端托管）
npm run build        # → dist/
npm run dev:web      # 仅前端，/api 代理到 8787
```

环境变量：
- `CREATOR_DATA_DIR`：持久化根目录，默认 `<项目根>/data`（测试/多实例部署用，避免互相污染）。
- `PORT` / LLM 相关配置见 `.env.example`。

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
    orchestrator.py      # LangGraph 编排图（编译 + 驱动 + 断点续跑）
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
    memory.py            # A11 记忆库：知识卡片持久化 + 打分检索（RAG-lite）
  agents/
    base.py              # 统一上下文/提示词/产物与结果构造
    a1_strategy.py … a11_memory.py
    registry.py          # 已实现 AGENTS（A1-A11）+ PLANNED_AGENTS（仅 A0）
  api/routes.py          # REST + SSE 路由（契约与 TS 版逐字段对齐）
scripts/
  doctor.py              # 环境 + 智能体链路自检（含抖音分支、真实 A11 智能体的 RAG 闭环）
  smoke_api.py           # 端到端验收（REST/SSE/错误分支/持久化/静态托管/新增阶段/记忆召回）
src/                     # 前端；契约类型自持于 src/lib/types.ts
  components/MemoryPanel.tsx   # 设置抽屉「记忆库」标签页：统计 + 检索召回 + 卡片列表
  components/PublishPanel.tsx  # 发布登记 + 效果回填（触发 A10 复盘）
```

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

---

## 6. 验证结果（端到端实测）

- `scripts/doctor.py` → **自检通过**。A1→A11 离线生成器全链路可跑：
  - 首轮：A5 pass/88、A6 revise(high)、A7 revise/75（blocker=1）——门禁触发返工，符合预期
  - 返工 1 轮后收敛：A5 pass/88、A6 pass(low)、A7 pass/100
  - A8 风格=「真实场景纪实」配图=7 张 图文一致=True；A9 平台=3 标题=19/20 字 长尾词=4
  - A11 卡片=4 模板=3 知识缺口=2 记录返工=1 轮
  - 动态渠道（抖音）分支：A8 分镜=5 镜 画幅 9:16；A9 标题 18/18 字（搜索变体 19 字 → 已压缩）
  - **记忆库 RAG 闭环（跑的是真实 A11 智能体，不是生成器）**：
    任务 1 入库 7 条新知识、召回 0 条（`exclude_task` 生效）；
    任务 2 召回 5 条历史资产，示例命中 `晨野｜小红书 语气与主张基线`（score=0.568，
    理由=文本相关、同品牌、同渠道、同行业）
  - 自检默认落在临时 `CREATOR_DATA_DIR`，不污染开发环境数据
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
- 前端：`npm run build` 通过（`tsc --noEmit` 无错误，46 modules；新增记忆库面板后由 45 → 46）。
- 并发：多个任务同时执行可各自独立跑完（每单 16 turns）。
- 关于「验收过程」：上一轮先被两个真实缺陷拦下（详见踩坑 12–14），修复后才拿到通过结果——
  **这说明自检通过并不等于链路可用**；本轮因此把「跑智能体本体」直接写进了 `doctor.py`。

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
- [ ] 换向量检索（当前是 2-gram + 元数据加成的 RAG-lite；接口已按 vector-ready 设计）。
- [ ] 真实模型链路压测与 Prompt 调优。
- [ ] 多平台真实一键发布（各平台开放接口授权与鉴权差异大，当前为「登记事实 + 复盘」闭环）。
- [ ] 记忆库的多租户 / 权限。

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
