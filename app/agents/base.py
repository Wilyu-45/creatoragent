"""智能体公共基座（移植自 server/agents/base.ts）。

所有智能体共享同一套：
  - 系统提示词契约（``SHARED_RULES``）
  - 结构化调用入口（``call_with_prompts`` → 重试/降级 → JSON 抽取）
  - 产物与结果构造（``build_artifact`` / ``build_result``）
  - 真实模型输出的字段漂移兜底（``read_confidence`` 等）
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..core.clock import now_iso
from ..core.events import new_id
from ..core.types import (
    AgentId,
    AgentMetrics,
    AgentResult,
    Artifact,
    ArtifactType,
    Brief,
    Evidence,
    GateResult,
    Phase,
    ReviewItem,
)
from ..llm.engine import chat
from ..llm.json_utils import (
    as_num,
    as_obj,
    as_obj_array,
    as_str,
    as_str_array,
    normalize_confidence,
    normalize_score,
)
from ..llm.pricing import DEFAULT_PRICE, PRICING, cost_of, price_of  # noqa: F401
from ..llm.types import ChatMessage, ImagePart, LLMRequest

# ------------------------------------------------------------------ #
# 运行时上下文                                                        #
# ------------------------------------------------------------------ #


@dataclass
class AgentRunContext:
    task_id: str
    brief: Brief
    phase: str
    #: 当前返工轮次，0 表示首轮
    revision: int
    #: 本轮需要处理的返工意见
    feedback: list[str]
    #: 上游产物内容，key 为 up_ 前缀约定名（strategy / creative / plan / draft ...）
    upstream: dict[str, dict[str, Any]]
    #: 由编排层注入，保证产物版本号在黑板内单调递增
    next_version: Callable[[str], int]
    emit: Callable[..., None]
    #: 当前任务已写入黑板的产物快照，供 A11 这类收敛型智能体做归档统计
    artifacts: list[Artifact] = field(default_factory=list)
    #: A11 记忆库召回的历史资产（RAG），由编排层在创作类智能体执行前注入
    memory: list[dict[str, Any]] = field(default_factory=list)
    #: 归属租户（plan.md D17）。A11 据此读写**本租户**的记忆库，
    #: 避免跨租户复用品牌调性；未启用鉴权时固定为 ``default``。
    tenant: str = "default"

    def upstream_of(self, key: str) -> dict[str, Any]:
        return self.upstream.get(key) or {}


@dataclass
class AgentMeta:
    id: str
    name: str
    #: 对应团队职位
    role: str
    kind: str
    phase: str
    produces: str
    description: str
    #: 是否拥有否决权（A6/A7）
    veto: bool
    capabilities: list[str] = field(default_factory=list)


@dataclass
class AgentDefinition:
    meta: AgentMeta
    run: Callable[[AgentRunContext], AgentResult]


# ------------------------------------------------------------------ #
# 统一系统提示词：所有智能体共享的输出契约                            #
# ------------------------------------------------------------------ #

SHARED_RULES = "\n".join(
    [
        "你是多智能体创作团队中的一员，只做职责范围内的事，不越权代替其他智能体。",
        "必须区分「事实」「假设」「创意建议」，不得编造数据、来源或用户证言。",
        "所有输出必须是合法 JSON，不要输出 Markdown 代码块以外的任何解释性文字。",
        "每个结论都要能说明依据；不确定时降低 confidence，而不是编造依据。",
        "涉及价格、功效、收益、数据引用时，必须在 risks 中标注风险。",
    ]
)


def system_prompt(meta: AgentMeta, body: str) -> str:
    return f"{SHARED_RULES}\n\n【你的角色】{meta.id} {meta.name}（{meta.role}）\n{body}"


# ------------------------------------------------------------------ #
# 结构化调用                                                          #
# ------------------------------------------------------------------ #

#: 计价表已迁到 ``llm/pricing.py``（供 llm 层的成本熔断共用），
#: 这里仍以同名转出 ``PRICING`` / ``DEFAULT_PRICE`` / ``price_of`` 兼容旧引用。


@dataclass
class StructuredResult:
    data: dict[str, Any]
    raw: str
    metrics: AgentMetrics


class TruncatedOutputError(Exception):
    """模型输出被 ``max_tokens`` 截断，JSON 未闭合。

    单独成型是为了让编排层能**针对性地重试**（调大 max_tokens 再试一次），
    而不是把它当成普通的「模型胡说」直接失败。
    """


def request_max_tokens(response: Any) -> int:
    return int(getattr(response, "max_tokens", 0) or 0)


def call_with_prompts(
    ctx: AgentRunContext,
    meta: AgentMeta,
    system: str,
    user: str,
    purpose: str,
    context: dict[str, Any],
    *,
    schema: str = "",
    images: list[ImagePart] | None = None,
) -> StructuredResult:
    """智能体唯一的结构化调用入口。

    ``context`` 同时服务于两条路径：
      - 真实模型：由 system/user 提示词驱动
      - 离线引擎：由 context 中的结构化数据驱动
    两条路径产出同一份 JSON 契约，上层无需感知差异。

    ``schema`` 传入该智能体的输出结构（JSON 文本）。它带来两个真实收益：

    1. **丢弃模式外的字段**：真实模型常额外附送 ``reasoning`` / ``notes`` /
       逐条解释，白烧输出 token（实测某任务 completion 达 6.5 万 token）。
    2. **提示词里显式要求精简**：在 schema 之后追加一条「不要附加解释字段」的约束。
       这两点合起来把输出预算压回可控范围。

    ``images`` 是本轮素材图片（``vision_attachments`` 产出）。多模态不可用时
    （离线引擎 / 纯文本模型）自动丢弃：离线引擎不读消息，塞图只会污染 token
    口径；纯文本模型收到内容块数组会直接报错。丢弃是静默的——提示词里的素材
    清单已按「本模型不读图」如实说明，智能体不会误以为自己看到了画面。
    """
    from ..llm.json_utils import extract_json, strip_unknown_keys

    # 素材清单在**所有**智能体的用户提示词前统一注入：它是 Brief 的组成部分，
    # 每个角色都需要知道「本次带了哪些素材」（合规看授权风险、审校看图文是否对得上、
    # 渠道看能不能用作封面）。没有素材时返回空串，提示词与历史逐字一致。
    block = assets_block(ctx, meta.id)
    if block:
        user = f"{block}{user}"

    if schema:
        user = f"{user}\n\n【输出要求】只输出上述 JSON，不要附加任何未列出的字段或解释性文字。"

    if images:
        from ..llm.engine import vision_enabled

        if not vision_enabled(meta.id):
            images = None

    response = chat(
        LLMRequest(
            purpose=purpose,
            messages=[
                ChatMessage(role="system", content=system),
                ChatMessage(role="user", content=user, images=list(images or [])),
            ],
            context=context,
            json=True,
            # 每智能体模型覆盖：engine 据此合并该角色的模型/端点/密钥设置
            agent_id=meta.id,
        )
    )

    parsed = extract_json(response.content)
    if isinstance(parsed, dict) and schema:
        parsed = strip_unknown_keys(parsed, schema)
    if not isinstance(parsed, dict):
        # 区分「模型胡说」与「被 max_tokens 截断」——两者的处置完全不同：
        # 截断要调大 max_tokens，胡说要看提示词。含糊地报「无法解析」会让人查错方向。
        if response.finish_reason == "length":
            raise TruncatedOutputError(
                f"{meta.id} 输出被 max_tokens 截断（completion={response.usage.completion_tokens}），"
                f"JSON 未闭合：请调大 LLM_MAX_TOKENS 或精简该智能体的输出结构"
            )
        snippet = response.content[:200]
        raise ValueError(f"{meta.id} 返回内容无法解析为 JSON：{snippet}")

    usage = response.usage
    metrics = AgentMetrics(
        latency_ms=response.latency_ms,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        # 本地端点成本恒为 0（硬件归用户），与 cost_guard 的计费口径一致
        cost_usd=(
            0.0
            if response.local
            else cost_of(
                response.model,
                usage.prompt_tokens,
                usage.completion_tokens,
                cached_tokens=usage.cached_tokens,
            )
        ),
        provider=response.provider,
        model=response.model,
        simulated=response.simulated,
        cached=response.cached,
    )
    return StructuredResult(data=parsed, raw=response.content, metrics=metrics)


# ------------------------------------------------------------------ #
# 产物与结果构造                                                      #
# ------------------------------------------------------------------ #


@dataclass
class ArtifactDraft:
    type: str
    title: str
    content: dict[str, Any]
    text: str
    tags: list[str] = field(default_factory=list)


def build_artifact(ctx: AgentRunContext, meta: AgentMeta, draft: ArtifactDraft) -> Artifact:
    return Artifact(
        id=new_id("art"),
        task_id=ctx.task_id,
        agent_id=meta.id,  # type: ignore[arg-type]
        type=draft.type,  # type: ignore[arg-type]
        version=ctx.next_version(draft.type),
        title=draft.title,
        content=draft.content,
        text=draft.text,
        revision=ctx.revision,
        created_at=now_iso(),
        tags=draft.tags,
    )


@dataclass
class ResultDraft:
    summary: str
    artifacts: list[Artifact]
    confidence: float
    risks: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    needs_human_review: bool = False
    gate_result: GateResult | None = None
    revision_requests: list[str] = field(default_factory=list)
    handoff: dict[str, Any] | None = None
    status: str = "success"


def build_result(
    ctx: AgentRunContext,
    meta: AgentMeta,
    metrics: AgentMetrics,
    draft: ResultDraft,
) -> AgentResult:
    return AgentResult(
        task_id=ctx.task_id,
        agent_id=meta.id,  # type: ignore[arg-type]
        status=draft.status,  # type: ignore[arg-type]
        summary=draft.summary,
        artifacts=draft.artifacts,
        evidence=draft.evidence,
        confidence=draft.confidence,
        risks=draft.risks,
        needs_human_review=draft.needs_human_review,
        gate_result=draft.gate_result,
        revision_requests=draft.revision_requests,
        handoff=draft.handoff,  # type: ignore[arg-type]
        metrics=metrics,
        created_at=now_iso(),
    )


# ------------------------------------------------------------------ #
# 解析辅助：真实模型的字段名常有漂移，统一在此兜底                     #
# ------------------------------------------------------------------ #


def _first_present(*values: Any) -> Any:
    """等价于 JS 的 ``??`` 链：返回第一个非 None 的值。"""
    for value in values:
        if value is not None:
            return value
    return None


def read_confidence(data: dict[str, Any], fallback: float = 0.78) -> float:
    return normalize_confidence(data.get("confidence"), fallback)


def read_risks(data: dict[str, Any]) -> list[str]:
    return as_str_array(data.get("risks"))


def read_evidence(data: dict[str, Any]) -> list[Evidence]:
    return [
        Evidence(
            claim=as_str(item.get("claim")),
            source=as_str(item.get("source"), "未标注"),
            reliability=normalize_confidence(item.get("reliability"), 0.6),
        )
        for item in as_obj_array(data.get("evidence"))
    ]


def read_gate(data: dict[str, Any], fallback: GateResult) -> GateResult:
    raw = as_str(_first_present(data.get("verdict"), data.get("gate_result")), "").lower()
    if raw in ("pass", "revise", "reject"):
        return raw  # type: ignore[return-value]
    return fallback


_SEVERITY_MAP: dict[str, str] = {
    "blocker": "blocker",
    "high": "blocker",
    "critical": "blocker",
    "阻断": "blocker",
    "major": "major",
    "medium": "major",
    "重要": "major",
    "minor": "minor",
    "low": "minor",
    "建议": "minor",
}


def read_reviews(data: dict[str, Any], key: str = "issues") -> list[ReviewItem]:
    items: list[ReviewItem] = []
    for item in as_obj_array(data.get(key)):
        raw_severity = as_str(item.get("severity"), "minor").lower()
        location = as_str(_first_present(item.get("location"), item.get("snippet")))
        items.append(
            ReviewItem(
                severity=_SEVERITY_MAP.get(raw_severity, "info"),  # type: ignore[arg-type]
                category=as_str(_first_present(item.get("category"), item.get("type")), "一般问题"),
                detail=as_str(_first_present(item.get("detail"), item.get("description"))),
                suggestion=as_str(_first_present(item.get("suggestion"), item.get("fix"))),
                location=location or None,
            )
        )
    return items


def memory_block(ctx: AgentRunContext, *, limit: int = 5) -> str:
    """渲染 A11 记忆库召回片段，供创作类智能体在提示词里复用历史资产。"""
    from ..knowledge.memory import render_hits

    return render_hits(ctx.memory, limit=limit)


def localization_block(ctx: AgentRunContext) -> str:
    """渲染目标语言的本地化要求；中文任务返回空串（不影响既有行为）。

    关键点是「**原生创作，不要先写中文再翻译**」：翻译腔的营销文案在本地市场
    基本不可用，而模型在收到中文 Brief 时天然会先用中文思考。
    """
    from ..knowledge.language import localization_directive

    return localization_directive(ctx.brief.channel, ctx.brief.language)


def assets_block(ctx: AgentRunContext, agent_id: str = "") -> str:
    """渲染 Brief 素材清单（多模态输入的文字视图）。无素材时返回空串。

    这是「素材」在**离线与纯文本模型**下的唯一来源，因此素材相关的智能体
    （合规、审校、渠道）都接它，哪怕它们自己不读图。
    ``agent_id`` 传入智能体 id 时按该智能体的模型覆盖判定读图能力，
    保证「图片已附带/本模型不读图」的说法与实际发送的内容块一致。
    """
    from ..core.assets import assets_prompt_block

    return assets_prompt_block(ctx.brief.assets, agent_id=agent_id)


def vision_attachments(ctx: AgentRunContext, agent_id: str = "") -> list[ImagePart]:
    """取可随本次调用发送的素材图片；多模态不可用时为空列表。

    ``agent_id`` 传入智能体 id 时按该智能体的模型覆盖判定读图能力。
    """
    from ..core.assets import vision_parts

    return vision_parts(ctx.brief.assets, agent_id=agent_id)


def documents_block(ctx: AgentRunContext, *, facts_only: bool = False) -> str:
    """渲染素材文档研读要点（A0 的 map-reduce 产物）。无文档素材时返回空串。

    与 ``assets_block`` 的「素材清单」不同，这里给的是**内容**：关键事实、
    可引用摘录与风格要点。首行如实标注来源（系统研读，非模型直接阅读全文），
    防止智能体误以为自己读过原文全文。
    ``facts_only=True`` 供核查类智能体使用：只要事实与摘录，不要创作向导语。
    """
    digest = ctx.upstream_of("documents")
    docs = digest.get("documents")
    docs = docs if isinstance(docs, list) else []
    docs = [doc for doc in docs if isinstance(doc, dict)]
    if not docs and facts_only:
        return ""
    issues = [str(item) for item in (digest.get("issues") or []) if str(item)]

    lines: list[str] = []
    if not docs:
        # 有文档但研读全部失败：如实说明，让智能体按「无素材」口径工作
        lines.append("【素材文档研读要点】本次附带的文档素材研读失败，请勿引用其内容。")
    else:
        lines.append(
            "【素材文档研读要点】（由系统对任务附带文档做要点研读生成，"
            "非模型直接阅读全文；引用时以「任务素材文档：<标题>」为来源）"
            if not facts_only
            else "【素材文档研读要点】（系统研读产物，用作事实比对基准）"
        )
        for doc in docs:
            title = str(doc.get("title") or "未命名文档")
            lines.append(f"◆ {title}")
            summary = str(doc.get("summary") or "")
            if summary and not facts_only:
                lines.append(f"  概要：{summary}")
            for fact in [str(f) for f in (doc.get("key_facts") or []) if str(f)]:
                lines.append(f"  事实：{fact}")
            for quote in [str(q) for q in (doc.get("quotes") or []) if str(q)]:
                lines.append(f"  摘录：「{quote}」")
            if not facts_only:
                for note in [str(n) for n in (doc.get("style_notes") or []) if str(n)]:
                    lines.append(f"  风格：{note}")
        brief_text = str(digest.get("creative_brief") or "")
        if brief_text and not facts_only:
            lines.append(f"创作参考：{brief_text}")
    for issue in issues:
        lines.append(f"  研读问题：{issue}")
    return "\n".join(lines) + "\n\n"


def content_to_text(content: dict[str, Any]) -> str:
    """把结构化产物压平成可读文本，用于版本 diff、关键词检索与全文合规扫描。"""
    lines: list[str] = []

    def walk(value: Any, path: str, depth: int) -> None:
        if depth > 5:
            return
        if isinstance(value, str):
            if value.strip():
                lines.append(f"{path}：{value}" if path else value)
            return
        if isinstance(value, bool):
            lines.append(f"{path}: {'true' if value else 'false'}")
            return
        if isinstance(value, (int, float)):
            lines.append(f"{path}: {value}")
            return
        if isinstance(value, list):
            for index, item in enumerate(value):
                walk(item, f"{path}[{index + 1}]" if path else str(index + 1), depth + 1)
            return
        if isinstance(value, dict):
            for key, child in value.items():
                walk(child, f"{path}.{key}" if path else str(key), depth + 1)

    walk(content, "", 1)
    return "\n".join(lines)


__all__ = [
    "AgentRunContext",
    "AgentMeta",
    "AgentDefinition",
    "SHARED_RULES",
    "system_prompt",
    "call_with_prompts",
    "StructuredResult",
    "TruncatedOutputError",
    "ArtifactDraft",
    "build_artifact",
    "ResultDraft",
    "build_result",
    "read_confidence",
    "read_risks",
    "read_evidence",
    "read_gate",
    "read_reviews",
    "memory_block",
    "localization_block",
    "assets_block",
    "vision_attachments",
    "documents_block",
    "content_to_text",
    "as_num",
    "as_obj",
    "as_obj_array",
    "as_str",
    "as_str_array",
    "normalize_confidence",
    "normalize_score",
]
