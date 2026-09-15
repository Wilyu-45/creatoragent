"""PostgreSQL schema（仅 ``CREATOR_STORAGE=pg`` 时使用）。

设计原则（见 storage_contract.md）：

* 每张表都有 ``payload jsonb`` 存**全量**原始记录（``model_dump(mode="json")``），
  API 输出从 payload 取值 —— 时间格式、字段集合与 file 版**逐字节零漂移**；
  过滤 / 排序 / 去重需要的字段提升为独立列。
* 时间列统一 ``timestamptz``，payload 内保留原 ISO 字符串。
* 意图租约的**权威源在 Redis**（TTL 即 file 版清扫语义），不建 bb_intents 表；
  仅留 ``bb_intents_audit`` 观测扩展位（默认不写）。
* 全部语句 ``CREATE TABLE IF NOT EXISTS``，幂等可重入；由 lifespan 启动时
  与 ``scripts/pg_migrate.py`` 共用。
"""

from __future__ import annotations

#: 建表语句，按依赖顺序排列（当前无外键，顺序仅作可读性约定）
SCHEMA_STATEMENTS: tuple[str, ...] = (
    # 1) 任务记录（TaskRecord 全量快照；interrupted 用 partial index 覆盖）
    """
    CREATE TABLE IF NOT EXISTS tasks (
      id         text PRIMARY KEY,
      tenant     text NOT NULL DEFAULT 'default',
      status     text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      payload    jsonb NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS tasks_tenant_created_idx
      ON tasks (tenant, created_at DESC, id DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS tasks_interrupted_idx
      ON tasks (status) WHERE status IN ('running', 'awaiting_approval')
    """,
    # 2) 黑板·事实（add_fact 去重与 verified 升级 → UPSERT）
    """
    CREATE TABLE IF NOT EXISTS bb_facts (
      id           text PRIMARY KEY,
      task_id      text NOT NULL,
      agent_id     text NOT NULL,
      claim_digest text NOT NULL,
      status       text NOT NULL,
      created_at   timestamptz NOT NULL DEFAULT now(),
      payload      jsonb NOT NULL,
      UNIQUE (task_id, claim_digest)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS bb_facts_task_idx ON bb_facts (task_id, created_at, id)
    """,
    # 3) 黑板·活动（防重签名 → UNIQUE + ON CONFLICT DO NOTHING）
    """
    CREATE TABLE IF NOT EXISTS bb_activities (
      id         text PRIMARY KEY,
      task_id    text NOT NULL,
      agent_id   text NOT NULL,
      signature  text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      payload    jsonb NOT NULL,
      UNIQUE (task_id, signature)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS bb_activities_task_idx
      ON bb_activities (task_id, created_at, id)
    """,
    # 4) 黑板·产物（next_version / latest_artifact / artifacts 排序）
    """
    CREATE TABLE IF NOT EXISTS bb_artifacts (
      id         text PRIMARY KEY,
      task_id    text NOT NULL,
      type       text NOT NULL,
      version    int NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now(),
      payload    jsonb NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS bb_artifacts_task_type_idx
      ON bb_artifacts (task_id, type, created_at, id)
    """,
    # 5) 黑板·评审
    """
    CREATE TABLE IF NOT EXISTS bb_reviews (
      id                 text PRIMARY KEY,
      task_id            text NOT NULL,
      target_artifact_id text NOT NULL,
      created_at         timestamptz NOT NULL DEFAULT now(),
      payload            jsonb NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS bb_reviews_task_idx ON bb_reviews (task_id, created_at, id)
    """,
    # 6) 记忆库（内容指纹去重；语言分区；评分在 Python，向量不落库）
    """
    CREATE TABLE IF NOT EXISTS memory_cards (
      id         text PRIMARY KEY,
      tenant     text NOT NULL DEFAULT 'default',
      digest     text NOT NULL,
      task_id    text NOT NULL,
      brand      text NOT NULL DEFAULT '',
      channel    text NOT NULL DEFAULT '',
      industry   text NOT NULL DEFAULT '',
      kind       text NOT NULL,
      language   text NOT NULL DEFAULT 'zh',
      title      text NOT NULL,
      content    text NOT NULL,
      revision   int NOT NULL DEFAULT 0,
      hits       int NOT NULL DEFAULT 0,
      created_at timestamptz NOT NULL DEFAULT now(),
      payload    jsonb NOT NULL,
      UNIQUE (tenant, digest)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS memory_cards_tenant_lang_idx
      ON memory_cards (tenant, language)
    """,
    """
    CREATE INDEX IF NOT EXISTS memory_cards_tenant_kind_idx
      ON memory_cards (tenant, kind, created_at, id)
    """,
    # 7) 评估历史（total/verdict 提列供过滤；stats 聚合在 Python，记录 ≤500）
    """
    CREATE TABLE IF NOT EXISTS evaluations (
      id         text PRIMARY KEY,
      tenant     text NOT NULL DEFAULT 'default',
      task_id    text NOT NULL,
      kind       text NOT NULL DEFAULT 'final',
      revision   int NOT NULL DEFAULT 0,
      total      numeric(5, 1) NOT NULL DEFAULT 0,
      verdict    text NOT NULL DEFAULT 'review',
      created_at timestamptz NOT NULL DEFAULT now(),
      payload    jsonb NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS evaluations_task_idx
      ON evaluations (tenant, task_id, created_at DESC, id DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS evaluations_tenant_idx
      ON evaluations (tenant, created_at DESC, id DESC)
    """,
    # 8) 数字人渲染作业（作业 dict 全量含 manifest/history）
    """
    CREATE TABLE IF NOT EXISTS digital_human_jobs (
      id         text PRIMARY KEY,
      task_id    text NOT NULL,
      tenant     text NOT NULL DEFAULT 'default',
      status     text NOT NULL DEFAULT 'queued',
      created_at timestamptz NOT NULL DEFAULT now(),
      updated_at timestamptz NOT NULL DEFAULT now(),
      payload    jsonb NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS dh_jobs_task_idx ON digital_human_jobs (task_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS dh_jobs_tenant_idx
      ON digital_human_jobs (tenant, created_at, id)
    """,
    # 9) 租约审计镜像（可选观测位，默认不写；租约权威源在 Redis）
    """
    CREATE TABLE IF NOT EXISTS bb_intents_audit (
      id         bigserial PRIMARY KEY,
      task_id    text NOT NULL,
      direction  text NOT NULL,
      agent_id   text NOT NULL,
      outcome    text NOT NULL,
      created_at timestamptz NOT NULL DEFAULT now()
    )
    """,
)


def ensure_pg_schema() -> None:
    """建齐全部表与索引（幂等）；连接失败直接抛异常（fail-loud）。"""
    from .pg import database_url, mask_url, pg_pool

    pool = pg_pool()
    with pool.connection() as conn:
        for statement in SCHEMA_STATEMENTS:
            conn.execute(statement)
    print(f"[pg] schema 就绪：{mask_url(database_url())}（{len(SCHEMA_STATEMENTS)} 条 DDL 执行完毕）")
