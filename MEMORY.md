# 开发记忆 · Creator Agent Studio

**读者**：接手本项目的开发工程师。**回答**：还有哪些事没做、哪些设计是刻意为之（别乱改重来）。
**不负责**：需求定义（见 `creator.md`）、技术方案与选型取舍（见 `plan.md`）、使用与配置操作（见 `USER_GUIDE.md`）、
工程约定与历史教训（见 `ENGINEERING_PRINCIPLES.md`）、存储替换契约（见 `storage_contract.md`）。

> 上线前人工事项的权威副本见 `README.md`；本文保留一份供开发侧对照。

---

## 1. 项目定位与开发速查

编排式多智能体创作台：把一份营销 Brief 经「策略 → 创意 → 策划 → 文案 → 审校 → 事实核查 → 合规 → 人工审批」
流水线产出可交付内容，带质量门禁与合规红线。11 个智能体 A1–A11；A0 总控职责由编排器承担。

| 项 | 选型 |
| --- | --- |
| 后端 | Python 3.12 + FastAPI + LangGraph（conda env `multi-agent-creator`） |
| 模型 | OpenAI 兼容 Provider（httpx）+ 内置 Mock，无密钥也可完整跑通 |
| 前端 | React 19 + TypeScript + Vite，构建后由后端静态托管 `dist/` |
| 持久化 | 默认 file（JSON + SQLite）；`CREATOR_STORAGE=pg` 切 PostgreSQL + Redis |

开发时最常敲的几条（完整验证入口见 `README.md`）：

```bash
conda run -n multi-agent-creator python -m app.main            # 后端（默认 8787）
conda run -n multi-agent-creator python scripts/doctor.py      # 环境 + 智能体链路自检
conda run -n multi-agent-creator python scripts/smoke_api.py   # 端到端验收（真实 uvicorn + /api 契约）
conda run -n multi-agent-creator python scripts/stress_llm.py -n 12 -c 4  # 压测：mock 查并发 / openai 量真实成本
npm run build && npm run dev:web                               # 前端构建 / 仅前端开发
```

关键配置键：`CREATOR_DATA_DIR`（持久化根目录，测试与多实例隔离）、`CREATOR_STORAGE`、`CREATOR_API_TOKENS`、
`OTLP_ENDPOINT`；其余见 `.env.example`。

---

## 2. 非显然的设计决策（别乱改）

> 这些决策「看代码看不出来、后人容易推翻重来」。改之前先读理由；对应的踩坑教训见 `ENGINEERING_PRINCIPLES.md`。

**编排与契约**

1. **`interrupt()` 放 escalation / approval 节点第一行**：LangGraph 恢复中断时会从头重放节点，落盘、发布等副作用只能在拿到裁决后执行一次。
2. **续跑用 `invoke(None)`**：让 LangGraph 从检查点继续；只有新任务才注入初始状态。
3. **SPA 回落路由显式排除 `api/` 前缀**：`/api/*` 未命中必须保持 404，不能被前端页面顶掉。
4. **前端契约类型自持于 `src/lib/types.ts`**（不 import 后端），与后端逐字段对齐；改契约要同步前端联合类型，否则 typecheck 直接报错。

**记忆库与检索**

5. **检索只在编排层做一次**：`Orchestrator._memory_hits()` 在 `MEMORY_RECALL_AGENTS`（A1/A2/A4）执行前统一检索并注入 `ctx.memory`，智能体只消费 —— 各写一份检索口径必然漂移。
6. **召回必须 `exclude_task=task.id`**：否则 A11 会召回自己刚写入的卡片，变成自我循环的「假复用」。
7. **不引入专用向量库**：MVP 主场景是「同品牌历史资产复用」，元数据加成 + 2-gram 已够用；接口按 vector-ready 设计（`retrieve(query, …, top_k) -> list[MemoryHit]`），日后替换上层无需改动。
8. **混合权重必须能退化**：`weight=0` 即纯关键词分、与旧行为逐位一致，让「上向量」成为可回滚开关而不是一次性替换。
9. **本地 embedding 用确定性 hashing**（blake2b 符号哈希 + L2 归一化，`app/knowledge/embedding.py`）：零依赖、同一文本永远同一向量，测试才敢断言分数。
10. **去重键与容量都按租户**：去重键 `sha1(tenant|kind|title|content)` 保证同租户幂等、跨租户各自存活；`MAX_CARDS` 按每租户计量，否则先入库的租户会挤光后来者的名额。

**评估、回归与追踪**

11. **门禁是否决式、评估是度量式**：默认 `JUDGE_MODE=advisory` 只记录不改裁决；`judge.weight` 默认 0.2（=0 时与历史行为逐位一致）—— 把度量塞进门禁会把评分噪声放大成流程抖动。
12. **评估的合规维度复用 A7 词库**（`scan_compliance`）：同一事实只允许一个判定来源，自己再写一遍迟早出现「A7 判通过、评估说有问题」。
13. **评估口径带版本号**（`RUBRIC_VERSION`）：口径不同先拒绝跨版本比较，而不是默默算出一个差值。
14. **黄金数据集 runner 抽到 `app/core/golden_runner.py`**：Web「跑回归」与 CLI 必须共用同一段执行与判定逻辑，脚本只管进程编排与退出码。
15. **回归是后台任务，不挂在 HTTP 请求上**：`/api/golden/run` 立即返回 202，执行在后台线程，状态由 `RunState` 聚合轮询；运行中再触发返回 409 而不是排队（排队会掩盖双触发）。
16. **数据集覆盖口径选「分支覆盖」而非「组合覆盖」**：每渠道特化分支 + 每行业规则组至少一条用例命中，避免「渠道 × 行业」全铺拖慢回归。
17. **采样是 trace 级决策，且只作用于导出面**（OTLP + 落盘）：同一棵树要么全导出要么全不导出；未采样的 trace 不转发、不落盘，但进程内轨迹始终完整。
18. **跨进程用 W3C `traceparent` 传播**：入站 `POST /api/tasks` 沿用远端 `trace_id` 并把根 span 挂在其下，出站 webhook 自动携带；坏头一律忽略，绝不影响业务请求。

**离线引擎、多语言与存储**

19. **mock 离线引擎必须覆盖新语言与新产物**：默认 `LLM_PROVIDER=mock`，若 mock 只会中文，新能力在 CI 与自检里就是假的。
20. **本地化要「原生创作」而不是翻译**：提示词显式写明「不要先写中文再翻译」，翻译腔文案在本地市场不可用。
21. **字数口径按语言**：`title_measure()` 英文按词、中日韩按字，交付标题压缩同口径 —— Instagram 的 12 词上限按字符算会得出完全错误的结论。
22. **双存储后端且默认 file**（`CREATOR_STORAGE=file|pg`）：file 模式零外部依赖，pg 实现放在条件分支里延迟 import（doctor 有反向断言）；切换步骤与替换契约见 `storage_contract.md`，迁移与专项回归用 `scripts/pg_migrate.py`（幂等）/ `scripts/pg_check.py`。
23. **pg 连接池必须 `autocommit=True`**：`PostgresSaver.setup()` 的 `CREATE INDEX CONCURRENTLY` 不允许在事务块内执行；代价是多语句原子性要显式 `conn.transaction()`。
24. **测试临时目录一律走 `make_temp_dir()`**（逐级 `mkdir` + 随机后缀），不用 `tempfile.mkdtemp`：`0o700` 在受限环境下会让落盘与 SQLite 静默失败，而自检仍然全绿。
25. **数字人渲染不在本系统内实现**：授权、形象库、计费与回调协议差异极大，「接哪家」是产品形态决策。系统只提供 `sample` 内置引擎与 `http` 适配样例（未配置 `DIGITAL_HUMAN_API_URL` 时显式失败，绝不假装成功），让 API / UI / 下游联调先于采购发生。
26. **界面设置里只有两类落盘项**：`PUT /api/settings` 整体是纯内存（重启即丢），仅 `search.site_urls`（站点监控）与 `llm.agent_models`（每智能体模型覆盖）持久化到 `data/settings.json` 并在启动时回读——两者都是用户精心维护的资产，不能丢；其余设置保持「即时生效、重启回退 env」的轻行为。settings 不在 `storage_contract.md` 的 PG 实体清单内，pg 模式同样走本地文件。
27. **放开全局编排预算要同步强监管用例的返工期望**：黄金用例的 `expect.max_revisions`（golden/briefs/*.json）与编排侧 `MAX_REVISIONS`（全局）是两个口径——编排读全局、断言读用例。全局放宽后 mock 在强监管用例会第 3 轮才收敛，「返工轮次 ≤ 2」断言随之失败，属口径漂移而非质量回归（同步用例期望即可，勿误判成 Prompt 退化）。
28. **「模型返回空内容」按瞬态可重试**：real_check 实测 deepseek-flash 会间歇返回空 content，命中即降级离线引擎会连锁引发 A6「无来源」返工、拉低全任务质量分；故纳入 `_RETRIABLE_RE`（engine.py）重试，3 次仍空才降级；400 等参数错误仍不重试。
29. **本地推理端点计 0 元但不跳过记账**：Ollama / LM Studio / vLLM 等私网端点（engine.py `_is_local_endpoint`）硬件归用户、无云单价可依，`cost_guard.charge` 仍必须调用、成本传 0——charge 同时驱动 token 熔断，跳过会让 token 预算彻底失效；span 与 `AgentMetrics.cost_usd` 同口径记 0（`LLMResponse.local`）。

**素材研读与长文分篇（core/digest.py + A4 分篇）**

30. **研读「降级」看 `degraded_reason` 而不是 `simulated`**：mock 引擎常态就是 simulated=True，拿它判定会把每次离线自检误报成「研读已降级」；只有熔断/引擎回退才置 degraded_reason（digest.py 单点判定），simulated 只做 sources 的诚实标注。
31. **新增 Artifact type 要过四处契约**：`core/types.py` 的 Literal + `ARTIFACT_LABEL`、前端 `src/lib/types.ts` 联合类型、`ArtifactViewer` 标签——漏 Literal 会在 A0 写黑板时被 Pydantic 判死整个任务（真实踩坑：`document_digest` 首跑 failed）；doctor 第 19 项已加「研读产物过黑板写入」断言守护。
32. **A4 分篇的字数区间取「上界最大的一组」**：`_WORD_RANGE_RE` 扫 constraints+deliverables，期望交付物写「合计 8000-10000 字」会被当成每篇字数 → prompt 撑爆；合计口径要写「约 N 字」。
33. **`PUT /api/settings` 的 patch 白名单与 `config.update_config` 必须同步扩**：只扩后者会出现「设置返回成功但值不生效」（`digestMaxCalls` 曾漏，白名单在 routes.py）。
34. **研读切块配额「每文档保底 1 块 + 按字数比例」**：按「全文统一块长 + 按文档顺序截断」会让长文档吃光 `digestMaxCalls` 配额、把排在后面的短文档（往期成稿、风格 skill）整份挤出研读——真实链路踩过（6 份素材 16 块，前三卷杂谈全部落空）。配额少于文档数时按字符数保长文档，落选文档如实计入 dropped。
35. **导出拼装时 cta 与正文结尾同句则不重复拼接**：模型常把收尾句同时写进正文结尾和 cta 字段（真实链路「咱们下回再见。」连出两次），忽略尾部标点差异后判重。同理：新加的自检断言必须实跑全绿再报完成——研读写黑板检查曾因 `Artifact.id` 默认空串而从未真正通过过（doctor 构造时须传 `new_id("art")`）。

---

## 3. 未完成的开发待办

**当前无未完成开发待办**。原文档按轮次罗列的开发项均已落地（其勾选条目已按文档规范删除，不在本文档保留「进度感」）。

- 需要新功能时，回到 `creator.md`（角色与契约）/ `plan.md`（方案与排期）清点，**不要在本文档追加流水账**。
- 代码与文档不一致时以代码为准，并立即修正文档。
- 部署方 / 用户侧事项见下一节，不属于开发待办。

---

## 4. 部署方 / 用户侧事项（非开发待办）

> 上线前人工事项的权威副本见 `README.md`，此处保留供开发侧对照。
> 以下事项代码侧已提供接口、样例或如实标注边界，由部署方按需实施。

| 事项 | 代码侧现状 |
|---|---|
| 吊销并更换联调用的 DeepSeek API Key | 该 Key 已在对话中明文出现，应视为泄露；替换 `.env` 即可，无代码改动（P0） |
| 设置生产访问令牌 | `CREATOR_API_TOKENS` + 鉴权中间件 + 租户隔离已就绪（P0） |
| 非中文市场法规词库 | 语言画像已写入当地合规红线并强制 `needs_human_review`；词库内容需目标市场法务确认（P0） |
| 提供品牌 VI 与产品资料 | 品牌 RAG、禁用词、术语表接口已就绪（P1） |
| 配置发布网关 | 平台无关 webhook + 到期队列 + 退避重试已备；多平台真实一键发布的私有授权由网关承接（P1） |
| 补充黄金数据集的行业用例 | 用例格式与判定器已就绪，加 JSON 即可（P1） |
| 确认行业合规词库（医疗/金融/教育） | 词库结构就绪，填入法务确认的规则即可（P1） |
| 选定数字人服务商并正式接入 | `sample` 内置引擎 + `http` 适配样例 + 前端面板 + API 已备（P2） |
| 决定是否切换 pg 存储模式 | 双后端已实现（`CREATOR_STORAGE=pg`）；是否启用、PG/Redis 托管还是自建、k8s 副本数调整由部署方决策（P2） |
| 接入 OTel Collector / Jaeger 生产实例 | `OTLP_ENDPOINT` 已支持，导出的 id 与本地一致（P2） |
| 建立人工抽检机制 | 评估报告、审批工作流、抽检面板已就绪（P2） |
