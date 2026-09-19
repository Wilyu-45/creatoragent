"""核心数据模型（移植自 server/core/types.ts）。

与《creator.md》第 6 章「核心接口与数据模型」保持一致，
所有智能体的输入输出都必须满足这里的契约。

设计取舍：
- 使用 ``Literal`` 而非 ``Enum``，让模型在 JSON 序列化时天然是字符串常量，
  与前端 TypeScript 联合类型逐字对应。
- 所有模型开启 ``extra="allow"``：智能体产出常带额外字段（尤其结构化正文），
  这里要保持宽容，不能因为多余键就整体校验失败。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# ------------------------------------------------------------------ #
# 智能体标识                                                          #
# ------------------------------------------------------------------ #

AgentId = Literal[
    "A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10", "A11"
]

ALL_AGENT_IDS: tuple[str, ...] = (
    "A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10", "A11",
)

#: 智能体分组：负责生产、负责审核、负责编排。
AgentKind = Literal["orchestrator", "producer", "reviewer", "analyst"]

# ------------------------------------------------------------------ #
# 流程状态机                                                          #
# ------------------------------------------------------------------ #

Phase = Literal[
    "INIT", "STRATEGY", "CREATIVE", "PLANNING", "DRAFTING", "REVIEW", "EDITING",
    "FACT_CHECK", "COMPLIANCE", "VISUAL_ADAPT", "CHANNEL_ADAPT", "REVISION",
    "APPROVAL", "PUBLISHED", "ANALYZED", "MEMORY", "ARCHIVED", "REJECTED",
    "FAILED", "PAUSED",
]

#: 状态机的规范顺序（用于 UI 展示进度）。
PHASE_ORDER: list[str] = [
    "INIT", "STRATEGY", "CREATIVE", "PLANNING", "DRAFTING", "REVIEW", "EDITING",
    "FACT_CHECK", "COMPLIANCE", "VISUAL_ADAPT", "CHANNEL_ADAPT", "APPROVAL",
    "PUBLISHED", "ANALYZED", "MEMORY", "ARCHIVED",
]

PHASE_LABEL: dict[str, str] = {
    "INIT": "需求接入",
    "STRATEGY": "策略洞察",
    "CREATIVE": "创意方向",
    "PLANNING": "内容策划",
    "DRAFTING": "文案创作",
    "REVIEW": "审核链路",
    "EDITING": "编辑审校",
    "FACT_CHECK": "事实核查",
    "COMPLIANCE": "品牌合规",
    "VISUAL_ADAPT": "视觉方向",
    "CHANNEL_ADAPT": "渠道适配",
    "REVISION": "返工修订",
    "APPROVAL": "人工审批",
    "PUBLISHED": "已发布",
    "ANALYZED": "效果复盘",
    "MEMORY": "知识沉淀",
    "ARCHIVED": "已归档",
    "REJECTED": "已驳回",
    "FAILED": "执行失败",
    "PAUSED": "等待人工",
}

# ------------------------------------------------------------------ #
# Brief —— 用户输入                                                   #
# ------------------------------------------------------------------ #

Priority = Literal["low", "normal", "high", "urgent"]

#: 素材形态（plan.md v2.0「多模态理解」）
AssetKind = Literal["image", "video", "document", "link"]


class BriefAsset(BaseModel):
    """Brief 附带的参考素材。

    三种指向方式（由 ``app/core/assets.py`` 统一解析）：

    * 公网图片/视频地址（``https://...``）——多模态模型自行拉取；
    * 内联 data URL——调用方已在别处取到字节时使用；
    * ``assets/`` 目录下的**相对**文件名——本地素材的唯一入口，
      绝对路径与 ``..`` 越界一律拒绝（Brief 来自 API 调用方，不能当本机
      文件读取原语用）。

    ``note`` 是使用者对素材的口头说明（「这是上一版的封面」），会原样进提示词；
    它同时也是图片进入离线链路时的文字视图来源。
    """

    model_config = ConfigDict(extra="allow")

    kind: AssetKind = "image"
    #: 图片/视频地址、data URL，或 ``assets/`` 下的相对文件名
    ref: str = ""
    #: 素材名（如「产品正面图」）
    title: str = ""
    #: 用途说明（会原样进入提示词）
    note: str = ""


class Brief(BaseModel):
    model_config = ConfigDict(extra="allow")

    brand: str = ""
    product: str = ""
    objective: str = "曝光"
    audience: str = ""
    channel: str = "小红书"
    tone: str = "轻松、真实、有种草感"
    industry: str = "消费品"
    #: 目标语言（plan.md v2.0「多语言本地化」）。``zh`` 为默认；
    #: 其它语言走本地化链路：原生创作而非翻译、按该语言口径校字数、记忆库按语言分区。
    language: str = "zh"
    keywords: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    deliverables: list[str] = Field(default_factory=list)
    notes: str = ""
    priority: Priority = "normal"
    deadline: str | None = None
    #: 参考素材（多模态输入）。默认空列表：不带素材的任务与历史行为完全一致。
    assets: list[BriefAsset] = Field(default_factory=list)


def create_empty_brief() -> Brief:
    """默认 Brief（新建任务表单的初值）。"""
    return Brief(
        brand="",
        product="",
        objective="曝光",
        audience="",
        channel="小红书",
        tone="轻松、真实、有种草感",
        industry="消费品",
        language="zh",
        keywords=[],
        constraints=[],
        deliverables=["图文笔记 1 篇", "标题备选 5 条"],
        notes="",
        priority="normal",
        deadline=None,
        assets=[],
    )


# ------------------------------------------------------------------ #
# TaskPacket —— A0 拆解出的任务卡                                     #
# ------------------------------------------------------------------ #


class TaskContext(BaseModel):
    model_config = ConfigDict(extra="allow")

    brand: str = ""
    audience: str = ""
    channel: str = ""
    tone: str = ""
    industry: str = ""
    keywords: list[str] = Field(default_factory=list)
    objective: str = ""
    product: str = ""


class TaskPacket(BaseModel):
    model_config = ConfigDict(extra="allow")

    task_id: str = ""
    parent_id: str | None = None
    phase: Phase = "INIT"
    assigned_agent: AgentId = "A0"
    objective: str = ""
    context: TaskContext = Field(default_factory=TaskContext)
    constraints: list[str] = Field(default_factory=list)
    expected_output_schema: str = ""
    priority: Priority = "normal"
    deadline: str | None = None
    created_at: str = ""


# ------------------------------------------------------------------ #
# Artifact —— 写入共享黑板的产物                                      #
# ------------------------------------------------------------------ #

ArtifactType = Literal[
    "task_plan", "document_digest", "strategy_brief", "creative_concept",
    "content_plan", "copy_draft",
    "edited_copy", "fact_check_report", "compliance_report", "visual_brief",
    "video_script", "channel_adaptation", "publish_plan", "effect_report",
    "knowledge_card", "final_delivery",
]

#: 产物类型 → 中文标题（UI 与归档时使用）。
ARTIFACT_LABEL: dict[str, str] = {
    "task_plan": "任务计划",
    "document_digest": "素材研读要点",
    "strategy_brief": "策略简报",
    "creative_concept": "创意概念",
    "content_plan": "内容策划",
    "copy_draft": "文案初稿",
    "edited_copy": "编辑定稿",
    "fact_check_report": "事实核查报告",
    "compliance_report": "合规审查报告",
    "visual_brief": "视觉指导",
    "video_script": "视频脚本",
    "channel_adaptation": "渠道适配稿",
    "publish_plan": "发布排期",
    "effect_report": "效果复盘报告",
    "knowledge_card": "知识沉淀卡片",
    "final_delivery": "最终交付件",
}


class Artifact(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = ""
    task_id: str = ""
    agent_id: AgentId = "A0"
    type: ArtifactType = "task_plan"
    version: int = 1
    title: str = ""
    content: dict[str, Any] = Field(default_factory=dict)
    text: str = ""
    revision: int = 0
    created_at: str = ""
    tags: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------ #
# AgentResult —— 智能体统一返回结构                                   #
# ------------------------------------------------------------------ #

GateResult = Literal["pass", "revise", "reject"]


class Evidence(BaseModel):
    model_config = ConfigDict(extra="allow")

    claim: str = ""
    source: str = ""
    reliability: float = 0.0


class AgentMetrics(BaseModel):
    model_config = ConfigDict(extra="allow")

    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    provider: str = ""
    model: str = ""
    simulated: bool = False
    #: 该次调用是否命中 LLM 响应缓存（plan.md 4.5「成本熔断 + 缓存」）
    cached: bool = False
    #: 是否因成本熔断而强制走离线引擎
    cut_off: bool = False


class Handoff(BaseModel):
    model_config = ConfigDict(extra="allow")

    to: AgentId | None = None
    reason: str = ""


class AgentResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    task_id: str = ""
    agent_id: AgentId = "A0"
    status: Literal["success", "revise", "error"] = "success"
    summary: str = ""
    artifacts: list[Artifact] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    confidence: float = 0.0
    risks: list[str] = Field(default_factory=list)
    needs_human_review: bool = False
    gate_result: GateResult | None = None
    #: 返工时给上游的修改要求
    revision_requests: list[str] = Field(default_factory=list)
    handoff: Handoff | None = None
    metrics: AgentMetrics = Field(default_factory=AgentMetrics)
    created_at: str = ""


# ------------------------------------------------------------------ #
# 共享黑板                                                            #
# ------------------------------------------------------------------ #

FactStatus = Literal["candidate", "verified", "rejected"]


class FactEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = ""
    task_id: str = ""
    agent_id: AgentId = "A0"
    claim: str = ""
    source: str = ""
    status: FactStatus = "candidate"
    confidence: float = 0.0
    created_at: str = ""


class IntentEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    key: str = ""
    task_id: str = ""
    agent_id: AgentId = "A0"
    direction: str = ""
    expires_at: str = ""
    created_at: str = ""


class ActivityEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = ""
    task_id: str = ""
    agent_id: AgentId = "A0"
    action: str = ""
    signature: str = ""
    created_at: str = ""


class ReviewItem(BaseModel):
    model_config = ConfigDict(extra="allow")

    severity: Literal["blocker", "major", "minor", "info"] = "info"
    category: str = ""
    detail: str = ""
    suggestion: str = ""
    location: str | None = None


class ReviewEntry(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = ""
    task_id: str = ""
    agent_id: AgentId = "A0"
    target_artifact_id: str = ""
    verdict: GateResult = "pass"
    score: int = 0
    items: list[ReviewItem] = Field(default_factory=list)
    created_at: str = ""


class BlackboardStats(BaseModel):
    model_config = ConfigDict(extra="allow")

    fact_count: int = 0
    verified_fact_count: int = 0
    artifact_count: int = 0
    review_count: int = 0
    active_intents: int = 0


class BlackboardSnapshot(BaseModel):
    model_config = ConfigDict(extra="allow")

    facts: list[FactEntry] = Field(default_factory=list)
    intents: list[IntentEntry] = Field(default_factory=list)
    activities: list[ActivityEntry] = Field(default_factory=list)
    reviews: list[ReviewEntry] = Field(default_factory=list)
    stats: BlackboardStats = Field(default_factory=BlackboardStats)


# ------------------------------------------------------------------ #
# 任务记录                                                            #
# ------------------------------------------------------------------ #

TaskStatus = Literal[
    "running", "awaiting_approval", "completed", "rejected", "failed", "cancelled"
]


class RevisionRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    round: int = 0
    phase: Phase = "REVISION"
    reason: str = ""
    requests: list[str] = Field(default_factory=list)
    created_at: str = ""


class GateRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    phase: Phase = "REVIEW"
    agent_id: AgentId = "A5"
    verdict: GateResult = "pass"
    score: int = 0
    round: int = 0
    blocking: list[str] = Field(default_factory=list)
    created_at: str = ""


class QualityScorecard(BaseModel):
    model_config = ConfigDict(extra="allow")

    structure: int = 0
    clarity: int = 0
    brand_voice: int = 0
    appeal: int = 0
    fact_safety: int = 0
    compliance: int = 0
    overall: int = 0


class PipelineNode(BaseModel):
    model_config = ConfigDict(extra="allow")

    agent_id: AgentId = "A0"
    phase: Phase = "INIT"
    status: Literal[
        "pending", "running", "done", "revise", "blocked", "skipped"
    ] = "pending"
    runs: int = 0
    confidence: float | None = None
    gate_result: GateResult | None = None
    summary: str = ""
    started_at: str | None = None
    finished_at: str | None = None


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="allow")

    prompt: int = 0
    completion: int = 0
    cost_usd: float = 0.0
    #: 计费调用次数与其中命中缓存的次数（用于 /api/metrics 的缓存命中率）
    calls: int = 0
    cached: int = 0
    #: 是否已触发成本熔断（plan.md 2.4「单篇内容成本 > 预算 120% 熔断」）
    cut_off: bool = False


class ApprovalState(BaseModel):
    model_config = ConfigDict(extra="allow")

    required: bool = False
    decision: Literal["pending", "approved", "rejected"] = "pending"
    comment: str = ""
    decided_at: str | None = None


class TaskRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = ""
    brief: Brief = Field(default_factory=create_empty_brief)
    status: TaskStatus = "running"
    phase: Phase = "INIT"
    revision_round: int = 0
    turn_used: int = 0
    created_at: str = ""
    updated_at: str = ""
    finished_at: str | None = None
    packets: list[TaskPacket] = Field(default_factory=list)
    results: list[AgentResult] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    pipeline: list[PipelineNode] = Field(default_factory=list)
    revisions: list[RevisionRecord] = Field(default_factory=list)
    gates: list[GateRecord] = Field(default_factory=list)
    scorecard: QualityScorecard | None = None
    tokens: TokenUsage = Field(default_factory=TokenUsage)
    approval: ApprovalState = Field(default_factory=ApprovalState)
    error: str | None = None
    #: 黑板租约冲突次数（plan.md 4.5 风险缓解的可观测指标）
    intent_conflicts: int = 0
    #: 发布排期时间（approval 通过后自动生成的生效时间）
    published_at: str | None = None
    #: 归属租户（plan.md D17「数据隔离」）。未启用鉴权时固定为 ``default``，
    #: 启用 ``CREATOR_API_TOKENS`` 后由请求方 token 决定，列表与详情按此过滤。
    tenant: str = "default"


# ------------------------------------------------------------------ #
# 事件（SSE 推送）                                                    #
# ------------------------------------------------------------------ #

AgentEventType = Literal[
    "task.created", "task.status", "task.completed", "task.failed", "phase.enter",
    "agent.start", "agent.progress", "agent.finish", "gate.decision",
    "revision.requested", "approval.required", "approval.decided",
    "blackboard.write", "judge.scored", "log",
]


class AgentEvent(BaseModel):
    model_config = ConfigDict(extra="allow")

    seq: int = 0
    task_id: str = ""
    ts: str = ""
    type: AgentEventType = "log"
    agent_id: AgentId | None = None
    phase: Phase | None = None
    level: Literal["debug", "info", "warn", "error"] = "info"
    message: str = ""
    payload: dict[str, Any] | None = None
    #: 追踪关联：让每个事件都能定位到所属 trace 与产出它的 span
    #: （plan.md 2.4 要求「覆盖整个 Agent session 的 span」，而非仅单次模型调用）
    trace_id: str | None = None
    span_id: str | None = None


# ------------------------------------------------------------------ #
# 抓包：A2A 消息                                                      #
# ------------------------------------------------------------------ #


class A2AMessage(BaseModel):
    model_config = ConfigDict(extra="allow")

    a2a_version: Literal["1.0"] = "1.0"
    message_id: str = ""
    from_agent: AgentId = "A0"
    to_agent: AgentId = "A0"
    intent: Literal["handoff", "request_revision", "notify", "veto"] = "notify"
    payload: dict[str, Any] = Field(default_factory=dict)
    trace_id: str = ""
    timestamp: str = ""
