"""A11 记忆与知识库智能体。

对应《creator.md》4.11 与 5 的流程位置：A10 复盘之后、人工审批之前。
产出 ``knowledge_card``：把本次创作中「可复用的资产」固化成知识卡片与模板，
并显式登记知识缺口，供下一次任务检索复用。

设计要点
--------
A11 是**收敛型**智能体：它不产出新内容，只做归档、提炼与检索索引构建，
因此不会对门禁结果产生影响（``veto=False``，且不写入需审核的正文产物）。
知识卡片采用「类型 + 正文 + 标签 + 复用建议」四元组。

RAG 闭环
--------
* **召回**：执行前列用 ``memory_store.retrieve()`` 取回历史资产，写入
  ``content["recalled"]``（即《creator.md》要求的「检索结果」输出项），
  同时把它拼进提示词，让模型在已有资产基础上做沉淀而不是从零开始；
* **写入**：执行后调用 ``memory_store.remember()`` 把本次卡片与模板固化到
  ``data/memory.json``，供后续任务召回。

闭环的另一半（把召回结果喂给 A1/A2/A4）由编排层在 ``_memory_hits()`` 中完成，
这样检索口径只有一处实现，智能体只需消费 ``ctx.memory``。
"""

from __future__ import annotations

from typing import Any

from ..core.types import AgentResult, Evidence
from ..knowledge.industry import channel_rule
from ..knowledge.memory import evidence_from_hits, memory_store, render_hits
from ..tools import run_agent_tools
from .base import (
    AgentDefinition,
    AgentMeta,
    AgentRunContext,
    ArtifactDraft,
    ResultDraft,
    as_obj,
    as_obj_array,
    as_str,
    as_str_array,
    build_artifact,
    build_result,
    call_with_prompts,
    content_to_text,
    read_confidence,
    read_evidence,
    read_risks,
    system_prompt,
)

META = AgentMeta(
    id="A11",
    name="记忆与知识库智能体",
    role="知识管理",
    kind="analyst",
    phase="MEMORY",
    produces="knowledge_card",
    description="沉淀品牌调性、有效表达与踩坑经验为知识卡片与模板，并登记知识缺口",
    veto=False,
    capabilities=["知识沉淀", "模板提炼", "检索索引", "知识缺口识别"],
)

#: 允许的卡片类型（与前端展示分组一致）。
CARD_TYPES = ("brand", "case", "template", "lesson")

SYSTEM = system_prompt(
    META,
    """你的职责：
1. 从本次任务的全部产物中，提炼可跨任务复用的知识卡片
2. 把稳定有效的表达固化为模板
3. 生成检索索引（关键词 → 可复用资产），供下一次任务召回
4. 登记本次暴露出的知识缺口，明确后续需要补充的资料

硬性规则：
- 卡片正文必须自包含：脱离本次任务也能读懂，禁止出现「上述」「本文档提到的」这类指代
- 只沉淀**已被验证**的内容：合规未通过或事实存疑的表述不得写入知识库
- 不得把一次性的事实（如某次活动的具体数字）包装成通用规律
- 只输出 JSON，不输出任何解释性文字

输出体量（内容优先于条数；确有增量再增加，不为凑数注水）：
- knowledge_cards ≥1 张（建议 2-6 张）；templates / gaps 按实际需要，不凑数
- 每张卡片 content ≤300 字：写「可复用的结论」，不复述创作过程""",
)

SCHEMA = """{
  "knowledge_cards": [{
    "type": "brand|case|template|lesson",
    "title": "", "content": "", "tags": [""], "reuse_hint": ""
  }],
  "templates": [{"name": "", "usage": "", "body": ""}],
  "archive": {
    "artifact_count": 0, "artifact_types": [""], "revision_rounds": 0,
    "key_decisions": [""], "reusable_assets": [""]
  },
  "reuse_suggestions": [""],
  "gaps": [""],
  "confidence": 0.0,
  "risks": [""],
  "evidence": [{"claim": "", "source": "", "reliability": 0.0}]
}"""


def normalize(data: dict[str, Any]) -> dict[str, Any]:
    archive = as_obj(data.get("archive"))

    cards: list[dict[str, Any]] = []
    for item in as_obj_array(data.get("knowledge_cards")):
        card_type = as_str(item.get("type"), "lesson").lower()
        cards.append(
            {
                "type": card_type if card_type in CARD_TYPES else "lesson",
                "title": as_str(item.get("title")),
                "content": as_str(item.get("content")),
                "tags": as_str_array(item.get("tags")),
                "reuse_hint": as_str(item.get("reuse_hint")),
            }
        )

    return {
        "knowledge_cards": cards,
        "templates": [
            {
                "name": as_str(item.get("name")),
                "usage": as_str(item.get("usage")),
                "body": as_str(item.get("body")),
            }
            for item in as_obj_array(data.get("templates"))
        ],
        "archive": {
            "artifact_count": int(archive.get("artifact_count") or 0),
            "artifact_types": as_str_array(archive.get("artifact_types")),
            "revision_rounds": int(archive.get("revision_rounds") or 0),
            "key_decisions": as_str_array(archive.get("key_decisions")),
            "reusable_assets": as_str_array(archive.get("reusable_assets")),
        },
        "reuse_suggestions": as_str_array(data.get("reuse_suggestions")),
        "gaps": as_str_array(data.get("gaps")),
    }


def run(ctx: AgentRunContext) -> AgentResult:
    brief = ctx.brief
    strategy = ctx.upstream_of("strategy")
    creative = ctx.upstream_of("creative")
    plan = ctx.upstream_of("plan")
    draft = ctx.upstream_of("draft")
    edit = ctx.upstream_of("edit")
    compliance = ctx.upstream_of("compliance")
    visual = ctx.upstream_of("visual")
    channel = ctx.upstream_of("channel")
    analysis = ctx.upstream_of("analysis")

    ctx.emit("汇总全链路产物，提炼可复用知识卡片并登记知识缺口")
    tools = run_agent_tools(ctx, META)

    scorecard = as_obj(edit.get("scorecard"))
    compliance_score = compliance.get("compliance_score")
    rule = channel_rule(brief.channel)
    structure = "；".join(
        as_str(item.get("section")) for item in as_obj_array(plan.get("structure"))
    )
    channels = "、".join(
        as_str(item.get("channel")) for item in as_obj_array(channel.get("platforms"))
    )
    alignment = as_obj(analysis.get("objective_alignment"))
    proposition = as_str(as_obj(strategy.get("message_house")).get("proposition"))

    # ---- RAG 召回：先取历史资产，再沉淀本次知识 ---------------------------- #
    query = " ".join(
        part
        for part in (
            brief.brand,
            brief.product,
            brief.industry,
            brief.channel,
            brief.audience,
            brief.objective,
            " ".join(brief.keywords),
            proposition,
        )
        if part
    )
    recalled = [
        hit.to_dict()
        for hit in memory_store.retrieve(
            query,
            brand=brief.brand,
            channel=brief.channel,
            industry=brief.industry,
            top_k=5,
            exclude_task=ctx.task_id,
            # 只召回本租户资产：A/B 两个品牌的调性基线不能互相串用（plan.md D17）
            tenant=ctx.tenant,
            # 只召回同语言资产：英文基调用在中文任务上比不用更糟（plan.md v2.0）
            language=brief.language,
        )
    ]
    if recalled:
        ctx.emit(
            f"从团队知识库召回 {len(recalled)} 条历史资产",
            {"titles": [as_str(hit.get("title")) for hit in recalled]},
        )

    user = f"""【任务】{brief.brand}｜{brief.product}｜{brief.channel}｜{brief.industry}
【核心主张】{proposition}
【创意主题】{as_str(as_obj(creative.get('big_idea')).get('title'))}
【渠道形态规范】{rule.format}｜{rule.title_style}
【内容结构】{structure or rule.format}
【定稿质量分】{scorecard.get('overall', '（无）')}｜合规得分：{compliance_score if compliance_score is not None else '（无）'}
【视觉风格】{as_str(as_obj(visual.get('visual_direction')).get('style'))}
【已适配平台】{channels or '（未指定）'}
【效果预估】{as_str(alignment.get('note'))}
【轮次】返工 {ctx.revision} 轮｜历史产物 {len(ctx.artifacts)} 件

{render_hits(recalled)}{tools.prompt_block()}请输出知识沉淀方案，严格要求 JSON 结构如下：
{SCHEMA}"""

    result = call_with_prompts(
        ctx,
        META,
        SYSTEM,
        user,
        "A11.memory",
        {
            "brief": brief.model_dump(mode="json"),
            "strategy": strategy,
            "creative": creative,
            "plan": plan,
            "draft": draft,
            "edit": edit,
            "compliance": compliance,
            "visual": visual,
            "channel": channel,
            "analysis": analysis,
            "revision": ctx.revision,
            "artifact_count": len(ctx.artifacts),
            "memory": recalled,
            "tools": tools.context(),
        },
        schema=SCHEMA,
    )

    content = normalize(result.data)
    cards = content["knowledge_cards"]
    gaps = content["gaps"]

    # 归档统计以黑板真实数据为准，避免模型自述的数字与系统不一致
    content["archive"]["artifact_count"] = len(ctx.artifacts)
    content["archive"]["artifact_types"] = sorted({item.type for item in ctx.artifacts})
    content["archive"]["revision_rounds"] = ctx.revision

    # 检索结果与入库数量同样以系统真实数据为准
    content["recalled"] = recalled
    content["tenant"] = ctx.tenant
    content["archive"]["indexed_cards"] = memory_store.remember(
        task_id=ctx.task_id,
        brand=brief.brand,
        channel=brief.channel,
        industry=brief.industry,
        cards=cards,
        templates=content["templates"],
        revision=ctx.revision,
        tenant=ctx.tenant,
        language=brief.language,
    )

    ctx.emit(
        "知识沉淀完成",
        {
            "cards": len(cards),
            "templates": len(content["templates"]),
            "gaps": len(gaps),
            "recalled": len(recalled),
            "indexed": content["archive"]["indexed_cards"],
        },
    )

    artifact = build_artifact(
        ctx,
        META,
        ArtifactDraft(
            type="knowledge_card",
            title="知识沉淀卡片",
            content=content,
            text=content_to_text(content),
            tags=["知识沉淀", brief.channel],
        ),
    )

    by_type: dict[str, int] = {}
    for card in cards:
        by_type[card["type"]] = by_type.get(card["type"], 0) + 1
    composition = "、".join(f"{key} {value} 张" for key, value in by_type.items())

    recall_evidence = [
        Evidence(
            claim=item["claim"],
            source=item["source"],
            reliability=item["reliability"],
        )
        for item in evidence_from_hits(recalled, limit=3)
    ]

    return build_result(
        ctx,
        META,
        result.metrics,
        ResultDraft(
            summary=(
                f"沉淀 {len(cards)} 张知识卡片（{composition}）"
                + (f"、{len(content['templates'])} 个可复用模板" if content["templates"] else "")
                + (f"；登记 {len(gaps)} 项知识缺口" if gaps else "；无知识缺口")
                + (
                    f"；召回 {len(recalled)} 条历史资产"
                    if recalled
                    else "；知识库暂无同类历史资产"
                )
            ),
            artifacts=[artifact],
            confidence=read_confidence(result.data, 0.8),
            risks=read_risks(result.data) + gaps[:2],
            evidence=read_evidence(result.data) + recall_evidence,
            handoff={"to": None, "reason": "知识已沉淀，等待人工审批交付"},
        ),
    )


a11_memory = AgentDefinition(meta=META, run=run)

__all__ = ["META", "CARD_TYPES", "normalize", "run", "a11_memory"]
