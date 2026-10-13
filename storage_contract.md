# 存储层替换契约（Storage Contract）

**读者**：要替换或扩展存储层的开发与部署方。**回答**：持久化了哪些实体、
各自的落盘位置与方法签名契约、换成 PostgreSQL + Redis 时怎么一一对应。
**不负责**：架构选型理由（见 `plan.md` 2.2）、使用操作（见 `USER_GUIDE.md`）。

单机默认用 **JSON 文件 + SQLite**（零外部依赖、离线可跑）；k8s 清单因此写死
`replicas: 1` + `strategy: Recreate`（有 `check_deploy.py` 断言守护）。要横向扩展
（多副本），必须先按本契约把存储层替换为 **PostgreSQL + Redis**。本文列出全部
持久化实体的落盘位置、方法签名契约与替换映射，替换时不必反向推导接口。

> **实现状态**：本契约已全量实现。设置 `CREATOR_STORAGE=pg`
> 即切换到 PostgreSQL + Redis 后端（默认 `file` 完全不变）；pg 依赖
> （psycopg / redis / langgraph-checkpoint-postgres）只在 pg 分支内延迟 import，
> file 模式保持零依赖。各实体的 PG 实现见 §2 表「实现模块」列；
> 数据迁移用 `scripts/pg_migrate.py`（幂等，支持 `--dry-run`），
> 存储层回归用 `scripts/pg_check.py`，模式自检已并入 `scripts/doctor.py`。
> 本地一键起库：`docker compose --profile pg up -d postgres redis`。

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

| # | 落盘位置 | 模块 | 内容 | PG 实现（`CREATOR_STORAGE=pg`） |
|---|---|---|---|---|
| 1 | `tasks/<task_id>.json` | `app/core/store.py` `TaskStore` | 任务记录（TaskRecord 全量快照） | `store_pg.py` `PgTaskStore`（表 `tasks`，`payload jsonb` 存全量） |
| 2 | `blackboard.json` | `app/core/blackboard.py` `Blackboard` | 意图租约 / 事实 / 活动 / 产物 / 评审 | `blackboard_pg.py` `PgBlackboard`（表 5 张 + **Redis 租约锁**） |
| 3 | `memory.json` | `app/knowledge/memory.py` `MemoryStore` | A11 知识卡片（含本地向量） | `memory_pg.py` `PgMemoryStore`（表 `memory_cards`；**向量不落库**，评分复用 file 版函数） |
| 4 | `evaluations.json` | `app/core/evaluations.py` `EvaluationStore` | LLM-as-a-Judge 评估历史 | `evaluations_pg.py` `PgEvaluationStore`（表 `evaluations`） |
| 5 | `checkpoints.sqlite` | `app/core/orchestrator.py`（LangGraph `SqliteSaver`） | 断点续跑检查点 | `langgraph-checkpoint-postgres` `PostgresSaver`（**fail-loud，不静默退回**） |
| 6 | `traces/<task_id>.json` | `app/core/tracing.py`（`EXPORT_DIR`） | OTel 形状的 trace 导出 | 不替换（导出面保留文件；生产观测走 OTLP collector） |
| 7 | `digital_human.json` | `app/core/digital_human.py`（`STORE_FILE`） | 数字人渲染作业 | `dh_jobs_pg.py` `PgJobBackend`（表 `digital_human_jobs`） |
| 8 | `exports/<task_id>.txt` | `app/core/orchestrator.py`（`EXPORTS_DIR`） | 审批通过后的成品导出文本（final_delivery 渲染） | 不替换（导出面保留文件，与 traces 同口径；下载走 `GET /api/tasks/{id}/export`） |
| 9 | （内存） | `app/llm/cache.py` `ResponseCache` | LLM 响应缓存（TTL + 容量，不落盘） | 不替换（纯内存；可选 Redis，miss 只影响成本不影响正确性） |
| 10 | `imagegen_jobs.json` / `videogen_jobs.json` / `videounderstand_jobs.json` / `dubbing_jobs.json` / `petgen_jobs.json` / `skillgen_jobs.json` / `surf_sessions.json` | `app/core/gen_jobs.py` `FileJobBackend`（各工具 `STORE_FILE`） | 旁路生成作业（图片 / 视频 / 视频理解 / 全自动配音 / 桌面宠物 / 技能提炼 / 灵感冲浪会话），整行 job dict | `gen_jobs.py` `PgJobBackend`（表 `imagegen_jobs` / `videogen_jobs` / `videounderstand_jobs` / `dubbing_jobs` / `pet_jobs` / `skill_jobs` / `surf_jobs`，`payload jsonb` 存整行） |
| 11 | `data/pets/<job_id>/` | `app/core/petgen.py`（`PETS_DIR`） | 桌宠帧 PNG + `pet.json` + README（含包内 `runner/pet.py`） | 不替换（二进制帧与 traces/exports 同口径保留文件；库中只存引用与清单，读取走带鉴权的 pets 路由） |
| 12 | `data/skills/<job_id>/` | `app/core/skillgen.py`（`SKILLS_DIR`） | 提炼产物 `SKILL.md` + `skill.json` + README，装配 `package.zip` 供扩展插件装载 | 不替换（作业行在库里，正文与包保留文件；读取走带鉴权的 skills 路由，`skill_dir_of()` 守住「只能落在 `skills/` 下」的不变量） |
| 13 | `data/dubbing/<job_id>/` | `app/core/dubbing.py`（`_audio_dir()`） | 引擎逐句**真合成**的音频（`seg_####.wav/mp3`）与拼好的整轨 `full.wav`；作业行与清单在 #10 的库里 | 不替换（音频与桌宠帧同口径保留文件，库中只存 `dubbing/<job_id>/<文件>` 引用；读取走带鉴权的 dubbing audio 路由，`resolve_audio_path()` 守「三段相对引用 + 引用作业 id 必须等于路由作业 id」，`drop_task()` 回收前先比对父目录） |
| 14 | `data/surf_pool.json` | `app/core/surf.py`（`_pool_backend()` / `FilePoolBackend`） | 灵感池（idea 库）：跨任务沉淀的灵感卡列表，按 `SURF_POOL_MAX` 裁剪最旧的 | 同构 `PgPoolBackend`（表 `surf_insights`，列 idea/simulated/created_at + tenant/topic 索引）；**不随任务删除**（`drop_task()` 只清 `surf_jobs`、不动灵感池），用户勾选后才由 `/surf/apply` 注入 Brief |
| 15 | `data/surf_history.json` | `app/core/surf.py`（`_history_backend()`，复用 `FilePoolBackend`） | 冲浪**浏览历史**：跨会话「这个租户读过哪些页」的记忆，主键 `<tenant>\|<url>`，行带 `url/topic/title/via/first_seen/last_seen/reads`，按 `SURF_HISTORY_MAX` 先忘最旧的（`last_seen` 最小的） | 同构 `PgPoolBackend`（表 `surf_history`，`status` 固定 `seen` + tenant 索引）；**只由 browser 通道写入**（`remember_read_pages()` 只收 `kind` 为 item/video 的**真读进去**的信号，首页/列表页/搜索结果页/失败页与 sample·http 通道一律不记，不拿占位信号冒充读过）；同样**不随任务删除**，`SURF_REMEMBER_READ=false` 时既不记也不跳、`GET /api/surf/history` 如实返回空 items，查看与清空走 `GET` / `DELETE /api/surf/history`（按租户隔离） |

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
- file 版是**进程内**锁（`threading.RLock` + 内存列表）：单副本正确，多副本下
  两个进程会各自拿到同一把「锁」。PG 版（`PgBlackboard`）的意图租约已改为
  **Redis `SET key value NX PX <ttl>` + Lua 原子脚本**：跨进程原子地完成
  「抢占 / 续期 / 冲突判定」三态，TTL 到期自动释放；key 前缀
  `CREATOR_REDIS_PREFIX`（默认 `creator:`）隔离多套环境。

**替换映射**：PG 表 `bb_facts / bb_activities / bb_artifacts / bb_reviews`
（均以 `task_id` 为外键）+ Redis 租约 `intent:{task_id}:{direction}`。
`next_version` 用**事务级咨询锁**（`pg_advisory_xact_lock`）+ 计数，显式包在
`conn.transaction()` 内 —— 连接池必须是 autocommit 模式（PostgresSaver 的
`CREATE INDEX CONCURRENTLY` 不允许事务块），多语句原子性因此必须显式声明。

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
tags[], language, created_at, expires_at)`。**实现取舍（pgvector 不引入）**：
embedding 维度可配（16～1536），固定维度列不适配；且混合评分含元数据加成与
关键词分量，无法用 `<=>` 单独表达。PG 版落库只存原文，向量每进程懒计算缓存，
候选卡片过滤后全量取出（每租户 `MAX_CARDS` 封顶），打分**复用 file 版
`_score_cards` 等函数** —— 两种后端排序语义同源，杜绝召回顺序漂移。

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

`orchestrator._checkpointer()` 按 `STORAGE_MODE` 分支：file 模式用 `SqliteSaver`
打开 `checkpoints.sqlite`（打开失败**静默退回 memory checkpointer**，
`checkpointer_kind` 字段可见，doctor 专门守护「必须是真的 SQLite」）；
pg 模式用 `PostgresSaver`（`langgraph-checkpoint-postgres`），初始化失败
**fail-loud 拒绝启动** —— 数据库连不上就该起不来，而不是悄悄丢续跑能力，
doctor 同样守护「必须是真的 postgres」。

### 3.6 Trace 导出

进程内 span 树是权威源；落盘 `traces/<task_id>.json` 只是**导出面**
（配置 `OTLP_ENDPOINT` 后同时走 OTLP）。多副本下进程内轨迹天然分散，
生产观测应直接接 collector + Jaeger/ClickHouse，落盘导出仅作本地排障。

### 3.7 旁路生成作业（数字人 / 图片 / 视频 / 视频理解 / 全自动配音 / 桌面宠物 / 技能提炼 / 灵感冲浪）

`_jobs() -> dict[str, dict]`（懒加载 + `_save_locked()` 原子落盘）。
数字人后来长出了公共后端 `gen_jobs.py`：`snapshot()` 读全量、`write_back()` 只回写状态有变的行、
`drop_task()` 按 task_id 回收；图片 / 视频 / 视频理解 / 全自动配音 / 桌面宠物 / 技能提炼 / 灵感冲浪**会话**共用它，换存储只换后端对象。
桌宠的帧与宠物包落在 `data/pets/<job_id>/`、技能正文落在 `data/skills/<job_id>/`、配音的合成音频落在
`data/dubbing/<job_id>/`（见总表 #11、#12、#13），删除任务时作业行与这些目录要一起回收。
**灵感冲浪会话**走 `surf_jobs`（`data/surf_sessions.json`，同旁路作业结构，随任务删除回收）；
会话开 `watch_videos` 时，browser 看视频产出的**画面摘要/屏上文字/评论洞察作为 `kind="video"` 信号写入会话 `signals`**
（整行存 `surf_jobs.payload` jsonb），**抽帧的 `page.screenshot` 截图只在内存里交 VLM（一次 `SURF.videounderstand` 多模态调用）、
不落库也不下载视频**（避开把大 base64 塞进作业行）；看懂走系统 `LLM_*` 网关，不另持端点与密钥。
但其产物**灵感池（idea 库）是独立的跨任务存储**（`_pool_backend()`：file `data/surf_pool.json` / pg 表
`surf_insights`），生命周期**不随任务删除**（`drop_task()` 只清会话、不动灵感池），
按 `SURF_POOL_MAX` 裁剪最旧的卡；这也是它与其余六个旁路通道唯一不同的地方。
同一条路上还有**第三份跨会话存储**：browser 通道每轮把「真读进去的页」记进**浏览历史**
（`_history_backend()`：file `data/surf_history.json` / pg 表 `surf_history`，主键 `<tenant>|<url>`，见总表 #15），
下一轮顺路发现的条目/视频先跟它对一遍、读过的跳过改读没见过的；它同样**不随任务删除**、
按 `SURF_HISTORY_MAX` 先忘最旧的（`last_seen` 最小的），且**只由 browser 通道写入**——
sample/http 不联网真读，就不写这份记忆（宁可不记，也不拿占位信号冒充「这页我读过」）。
三个多副本注意点：
- 作业存储需共享（PG 表或 Redis Hash）；
- `sample` 内置引擎的「按流逝时间惰性推进」是**进程内**行为，多副本下要么
  把查询转发到持有作业的副本，要么把样例引擎改为无状态幂等计算；
- 配音的引擎通道（`sapi` / `sovits` / `elevenlabs`）相反：它由**后台 worker 线程**逐句推进，
  续跑依据是 `seg["audio_ref"]` 非空（已合成的句子不重复合成、克隆 `voice_id` 持久化在作业行里），
  所以**音频目录也必须与作业行同机/同卷**；否则引用在库里、文件不在盘上，
  读取路由只会 404（`resolve_audio_path()` 宁缺不发假音频）；
- 跨语言译制配音（`target_lang`）在**受理时同步翻译**，译文与原句（`seg["source_text"]`）、
  `translated` / `translation_simulated` / `source_lang` / `target_lang` 都随整行 job 写进
  #10 的库里（`payload jsonb` 存整行 → 新字段无需改表）；翻译是一次 `LLM_*` 网关调用，
  与合成同口径计入成本熔断。

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

## 5. 替换后的回归验证（已落地为脚本）

1. `python scripts/doctor.py`：file 模式全绿（其中含反向断言
   「file 模式不得泄漏 psycopg/redis import」）；`CREATOR_STORAGE=pg` 下重跑，
   依赖检查与检查点后端断言自动切换为 postgres 口径；
2. `python scripts/pg_check.py`：PG 存储层专项回归 —— 租户隔离、事实去重、
   意图租约三态（claimed / renewed / conflict）、任务增删查、记忆库
   幂等写入与召回、评估历史；
3. `python scripts/verify_contracts.py`：API 契约不变 —— 存储替换对接口层必须零感知；
4. `python scripts/pg_migrate.py --dry-run` → 去掉 `--dry-run`：JSON → PostgreSQL
   一次性迁移，逐类实体输出「源数 / 插入 / 跳过」计数，可重复执行（幂等）；
   **迁移范围含数字人作业表 `digital_human_jobs`，七个旁路生成作业表
   （`imagegen_jobs` / `videogen_jobs` / `videounderstand_jobs` / `dubbing_jobs` / `pet_jobs` / `skill_jobs` / `surf_jobs`）不在迁移脚本内**：
   它们是短生命周期状态，切换存储模式时未跑完的作业不迁移、重新提交即可；
   冲浪的两个跨会话存储（灵感池 `surf_insights`、浏览历史 `surf_history`）**同样不在迁移脚本内**：
   它们是「重新刷就有」的沉淀/记忆，切换后从新一轮攒起，旧 JSON 留在原地不影响正确性；
   桌宠帧与宠物包、技能正文与技能包、配音合成音频都是文件（`data/pets/<job_id>/`、`data/skills/<job_id>/`、
   `data/dubbing/<job_id>/`），换机时须与 `data/assets`、`data/pets`、`data/skills`、`data/dubbing` 一起搬迁；
5. 双副本手动演练（部署方）：副本 A 创建任务、副本 B 能看到并续跑
   （共享 PG + Redis 后此语义成立）。

## 6. 切换步骤速查

```bash
# 1) 起库（端口可用 CREATOR_PG_PORT / CREATOR_REDIS_PORT 改映射，避开本机占用）
docker compose --profile pg up -d postgres redis
# 2) 配置连接串（compose 网络内用服务名；宿主机直连用 localhost + 映射端口）
#    CREATOR_STORAGE=pg
#    CREATOR_DATABASE_URL=postgresql://creator:creator@localhost:5432/creator
#    CREATOR_REDIS_URL=redis://localhost:6379/0
# 3) 迁移存量数据（可选，file → pg 一次性；幂等可重跑）
python scripts/pg_migrate.py --dry-run
python scripts/pg_migrate.py
# 4) 启动应用（lifespan 会自检 schema 与 Redis，失败拒绝启动）
python scripts/doctor.py   # 期望检查点后端=postgres、依赖检查走 pg 分支
```
