# 存储层替换契约（Storage Contract）

单机默认用 **JSON 文件 + SQLite**（零外部依赖、离线可跑）；k8s 清单因此写死
`replicas: 1` + `strategy: Recreate`（有 `check_deploy.py` 断言守护）。要横向扩展
（多副本），必须先按本契约把存储层替换为 **PostgreSQL + Redis**。本文列出全部
持久化实体的落盘位置、方法签名契约与替换映射，替换时不必反向推导接口。

---

## 1. 总边界

- 所有持久化都收敛在 `CREATOR_DATA_DIR`（默认 `<项目根>/data`，见 `app/config.py`）之下。
  这是单机存储的隐式边界：**目录即实例**，两个进程共用一个目录即共用一个实例。
- 写入统一采用「临时文件 + `os.replace` 原子替换」，避免进程被杀留下半个 JSON；
  换数据库后由事务保证同等级别的一致性。
- 租户隔离：任务、记忆库、评估历史、数字人作业都带 `tenant` 字段。替换存储时
  必须保留「按租户过滤 + 跨租户不可见」的语义（`doctor.py` 有对应断言）。
- 高频写都做了防抖（任务 0.25s、黑板 flush 防抖）；数据库实现可用事务直接写，
  但接口签名不要变。

## 2. 持久化实体总表

| # | 落盘位置 | 模块 | 内容 | 多副本替换 |
|---|---|---|---|---|
| 1 | `tasks/<task_id>.json` | `app/core/store.py` `TaskStore` | 任务记录（TaskRecord 全量快照） | PostgreSQL 表 `tasks` |
| 2 | `blackboard.json` | `app/core/blackboard.py` `Blackboard` | 意图租约 / 事实 / 活动 / 产物 / 评审 | PG 表 5 张 + **Redis 租约锁** |
| 3 | `memory.json` | `app/knowledge/memory.py` `MemoryStore` | A11 知识卡片（含本地向量） | PG 表 + **pgvector** |
| 4 | `evaluations.json` | `app/core/evaluations.py` `EvaluationStore` | LLM-as-a-Judge 评估历史 | PG 表 `evaluations` |
| 5 | `checkpoints.sqlite` | `app/core/orchestrator.py`（LangGraph `SqliteSaver`） | 断点续跑检查点 | **`langgraph-checkpoint-postgres`** |
| 6 | `traces/<task_id>.json` | `app/core/tracing.py`（`EXPORT_DIR`） | OTel 形状的 trace 导出 | OTLP collector + Jaeger/ClickHouse |
| 7 | `digital_human.json` | `app/core/digital_human.py`（`STORE_FILE`） | 数字人渲染作业 | PG 表或 Redis Hash |
| 8 | （内存） | `app/llm/cache.py` `ResponseCache` | LLM 响应缓存（TTL + 容量，不落盘） | 可选 Redis，miss 只影响成本不影响正确性 |

---

## 3. 各实体接口契约

### 3.1 任务记录 — `TaskStore`

```python
load_all() -> {"loaded": int, "interrupted": list[str]}
get(task_id: str) -> TaskRecord | None
list() -> list[TaskRecord]                # 按 created_at 倒序
set(task: TaskRecord) -> None             # 只更新内存索引，不落盘
schedule_save(task, immediate: bool = False)   # 0.25s 防抖落盘
save(task: TaskRecord) -> None            # 立即落盘
flush_all() -> None                       # 进程退出前强制刷盘
delete(task_id: str) -> None              # 内存 + 磁盘一起删
```

不变量：
- 损坏的任务文件**跳过不崩溃**（`load_all` 逐文件 try/except）；
- 重启时 `status ∈ {running, awaiting_approval}` 的任务归入 `interrupted`，
  交由编排器尝试断点续跑，确实无法续跑才标失败 —— 替换实现必须保留这一语义；
- 文件名即任务 id（`tasks/<task_id>.json`），删除 = 删文件 + 删索引。

**替换映射**：PG 表 `tasks(id text PK, tenant text, status text, payload jsonb,
created_at timestamptz)`；`list()` → `ORDER BY created_at DESC`；
`interrupted` → `WHERE status IN ('running','awaiting_approval')`。

### 3.2 共享黑板 — `Blackboard`

```python
load() -> None
flush() -> None                           # 防抖后的整文件重写
# 意图租约（plan.md 2.2.2 / 4.5）
declare_intent(task_id, agent_id, direction, ttl_ms=INTENT_TTL_MS) -> IntentEntry | None
acquire_intent(task_id, agent_id, direction, ttl_ms=...) -> tuple[IntentEntry | None, str]
release_task_intents(task_id) -> int
release_intent(key: str) -> None
active_intents(task_id: str | None = None) -> list[IntentEntry]
# 事实 / 活动 / 产物 / 评审
add_fact(...) -> ...;  facts(task_id) -> list[FactEntry]
has_activity(task_id, signature) -> bool;  add_activity(...);  activities(task_id)
put_artifact(artifact) -> Artifact;  artifacts(task_id);  latest_artifact(task_id, type_)
next_version(task_id, type_) -> int
add_review(...) -> ...;  reviews(task_id) -> list[ReviewEntry]
snapshot(task_id) -> BlackboardSnapshot
```

不变量：
- `acquire_intent` 是**租约**语义（TTL 过期自动清扫，`_sweep_expired_intents`）：
  同一智能体重复声明 = 续期；冲突时返回 `(None, reason)`。
- ⚠️ 当前实现是**进程内**锁（`threading.RLock` + 内存列表）：单副本正确，
  多副本下两个进程会各自拿到同一把「锁」。替换时意图租约必须换成
  **Redis `SET key value NX PX <ttl>`**，其余实体按表存储即可。

**替换映射**：PG 表 `bb_facts / bb_activities / bb_artifacts / bb_reviews`
（均以 `task_id` 为外键）+ Redis 租约 `intent:{task_id}:{direction}`。
`next_version` 需要在事务内 `SELECT ... FOR UPDATE` 或改用序列。

### 3.3 记忆库 — `MemoryStore`（A11 知识卡片）

```python
load() -> None
flush() -> None                           # tmp + os.replace 原子替换
remember(*, task_id, brand, channel, industry, cards: list[dict],
         templates=None, revision=0, tenant=DEFAULT_TENANT,
         language=DEFAULT_MEMORY_LANGUAGE) -> int    # 返回新增条数
retrieve(query: str, *, brand="", channel="", industry="", top_k=5,
         exclude_task: str | None = None, tenant: str | None = None,
         language: str | None = None) -> list[MemoryHit]
list_cards(*, kind=None, brand=None, limit=50, tenant=None) -> list[MemoryCard]
stats(tenant: str | None = None) -> dict
```

不变量（替换实现必须逐条保留）：
- **幂等写入**：卡片按内容摘要去重（`_card_key`），同租户重复写入跳过；
- **租户隔离**：`tenant` 非空时只召回/统计该租户；`stats` 额外给 `global_total`
  （只给数量，不构成数据泄露）；
- **语言隔离**：`language` 非空时只召回该语言的卡片 —— 英文资产不能被中文任务
  当作语气基线，反之亦然；
- **自我排除**：`exclude_task` 排掉当前任务刚写的卡片，召回的是历史资产；
- **混合检索**：关键词重叠 + 向量余弦（`EMBEDDING_WEIGHT` 控制语义分量权重）；
  本地向量是确定性 hashing embedding，换 pgvector 时相似度排序语义对齐即可；
- **容量与时效**：每租户容量上限逐出（`_evict`）+ TTL 过期清扫（`_sweep_expired`）。

**替换映射**：PG 表 `memory_cards(tenant, digest UNIQUE, kind, title, content,
tags[], language, created_at, expires_at)` + pgvector 列；
`retrieve` → 关键词倒排 + `ORDER BY embedding <=> query_vec` 混合排序。

### 3.4 评估历史 — `EvaluationStore`

```python
load() -> None
flush() -> None
record(entry: EvaluationRecord) -> EvaluationRecord
list(...) -> list[EvaluationRecord]
latest(task_id: str, *, tenant: str | None = None) -> EvaluationRecord | None
stats(*, tenant: str | None = None) -> dict
```

**替换映射**：PG 表 `evaluations(id, tenant, task_id, revision, total, verdict,
axes jsonb, created_at)`；`latest` → `ORDER BY created_at DESC LIMIT 1`。

### 3.5 断点检查点 — LangGraph Checkpointer

`orchestrator._checkpointer()` 用 `SqliteSaver` 打开 `checkpoints.sqlite`；
**打开失败时静默退回 memory checkpointer**（`checkpointer_kind` 字段可见，
doctor 第 10 项专门守护「必须是真的 SQLite」）。

多副本下 memory 退回会让断点续跑直接失效（状态在各自进程里），
因此替换时直接换 `langgraph-checkpoint-postgres`（`PostgresSaver`），
并**移除静默退回**：数据库连不上应该起不来，而不是悄悄丢续跑能力。

### 3.6 Trace 导出

进程内 span 树是权威源；落盘 `traces/<task_id>.json` 只是**导出面**
（配置 `OTLP_ENDPOINT` 后同时走 OTLP）。多副本下进程内轨迹天然分散，
生产观测应直接接 collector + Jaeger/ClickHouse，落盘导出仅作本地排障。

### 3.7 数字人渲染作业

`_jobs() -> dict[str, dict]`（懒加载 + `_save_locked()` 原子落盘）。
两个多副本注意点：
- 作业存储需共享（PG 表或 Redis Hash）；
- `sample` 内置引擎的「按流逝时间惰性推进」是**进程内**行为，多副本下要么
  把查询转发到持有作业的副本，要么把样例引擎改为无状态幂等计算。

### 3.8 LLM 响应缓存 — `ResponseCache`

纯内存（TTL + 容量上限），**不落盘**，重启即空。多副本可换 Redis，
缓存 miss 只增加成本与延迟，不影响正确性 —— 替换优先级最低。

---

## 4. 替换触发条件（来自 plan.md §5.1）

| 信号 | 阈值 | 动作 |
|---|---|---|
| 需要多副本部署 | —— | 本契约全量替换（前置条件） |
| 单任务 span 数 | > 200 | 黑板换共享存储 |
| 并发任务数 | > 8 | 黑板 + 任务记录换共享存储 |
| 记忆库卡片 | > 2000 / 召回精确率 < 0.7 | pgvector / 独立向量库 |

## 5. 替换后的回归验证

1. `doctor.py` 的租户隔离断言（写入归属 / 召回不越租户 / 同租户幂等）；
2. `doctor.py` 第 10 项：断点续跑必须落在真持久层（把「SQLite/memory」断言
   改为「PostgresSaver」后重跑）；
3. `verify_contracts.py`：API 契约不变 —— 存储替换对接口层必须零感知；
4. 双副本手动演练：副本 A 创建任务、副本 B 能看到并续跑。
