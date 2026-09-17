"""A1 策略与洞察智能体的工具集。

对应《plan.md》2.2.3 的「搜索 Server / RAG Server」内置替代：
行业洞察画像（industry_insight）、案例库检索（case_library）、
渠道格局（channel_landscape）、差异化定位推演（differentiation_map）。
记忆召回由编排层注入 ``ctx.memory``，不在此重复。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..knowledge.industry import CHANNEL_RULES, cases_for, channel_rule, industry_profile
from .base import Tool, ToolOutcome

if TYPE_CHECKING:
    from ..agents.base import AgentRunContext


def industry_insight(ctx: "AgentRunContext") -> ToolOutcome:
    """内置行业洞察画像：痛点 / 场景 / 动机 / 阻力 / 证明素材类型。"""
    brief = ctx.brief
    profile = industry_profile(brief.industry)
    detail = "\n".join(
        [
            f"行业痛点参考：{'；'.join(profile.pain_points[:4])}",
            f"典型场景：{'；'.join(profile.scenarios[:4])}",
            f"核心动机：{'；'.join(profile.motivations[:4])}",
            f"决策阻力：{'；'.join(profile.objections[:3])}",
            f"可准备的证明素材类型：{'；'.join(profile.proof_assets[:4])}",
            "（来源：内置行业洞察库——供推断参考，不得当作调研数据或用户证言引用）",
        ]
    )
    return ToolOutcome(
        summary=f"检索到「{brief.industry}」行业洞察画像",
        detail=detail,
        data={
            "industry": brief.industry,
            "pain_points": profile.pain_points,
            "scenarios": profile.scenarios,
            "motivations": profile.motivations,
            "objections": profile.objections,
            "proof_assets": profile.proof_assets,
        },
    )


def case_library(ctx: "AgentRunContext") -> ToolOutcome:
    """同行业、同渠道的历史案例参考。"""
    brief = ctx.brief
    cases = cases_for(brief.industry, brief.channel)[:3]
    if not cases:
        return ToolOutcome(summary="案例库中暂无同类案例", data={"cases": []})
    lines = [
        f"{index}. {case.name}（{case.channel}）｜{case.angle}｜可借鉴：{case.why}"
        for index, case in enumerate(cases, start=1)
    ]
    lines.append("（来源：内置案例库——方向参考，非本品牌真实投放数据）")
    return ToolOutcome(
        summary=f"检索到 {len(cases)} 条同类案例",
        detail="\n".join(lines),
        data={
            "cases": [
                {
                    "name": case.name,
                    "industry": case.industry,
                    "channel": case.channel,
                    "angle": case.angle,
                    "why": case.why,
                    "source": case.source,
                }
                for case in cases
            ]
        },
    )


def channel_landscape(ctx: "AgentRunContext") -> ToolOutcome:
    """全部候选渠道的内容形态一览，支撑渠道优先级判断。"""
    brief = ctx.brief
    primary = channel_rule(brief.channel)
    lines = [f"主渠道「{brief.channel}」：{primary.format}（{primary.length_hint}）"]
    others = [
        f"{name}：{rule.format}"
        for name, rule in CHANNEL_RULES.items()
        if name != brief.channel
    ]
    lines.extend(f"候选——{item}" for item in others)
    lines.append("（来源：内置渠道规范库）")
    return ToolOutcome(
        summary=f"渠道格局：主渠道 {brief.channel} + {len(others)} 个候选渠道",
        detail="\n".join(lines),
        data={
            "primary_channel": brief.channel,
            "primary_format": primary.format,
            "candidates": [
                {"channel": name, "format": rule.format}
                for name, rule in CHANNEL_RULES.items()
                if name != brief.channel
            ],
        },
    )


def differentiation_map(ctx: "AgentRunContext") -> ToolOutcome:
    """同质化主张 → 差异化空位的结构化推演，供受众定位与信息屋取角。

    诚实边界：本系统**没有竞品投放数据**。这里给出的是「内置行业洞察 + 案例库」
    交叉推演出的空位候选，不是竞品实测结论；A1 必须结合 Brief 自行判断后写入
    ``audience_profile`` 与 ``message_house``，不得把它当成调研数据引用。
    """
    brief = ctx.brief
    profile = industry_profile(brief.industry)
    cases = cases_for(brief.industry, brief.channel)[:3]

    # 同质化区：行业通用动机人人都在喊，喊了等于没差异化
    top_motivation = profile.motivations[0] if profile.motivations else ""
    common_claims = [f"只喊「{item}」" for item in profile.motivations[:3]]
    # 空位来源：未被正面回答的决策阻力
    unmet_objections = list(profile.objections[:3])
    # 可占位的证据型差异化：把「可提供的自证材料」摆在主张位置
    evidence_angles = list(profile.proof_assets[:3])
    case_angles = [f"{case.name}：{case.angle}" for case in cases]

    audience = brief.audience or "目标人群"
    brand = brief.brand or "本品牌"
    product = brief.product or "本产品"
    seed_parts = [
        f"对「{audience}」而言，{brand} 不靠「{top_motivation}」取胜"
        if top_motivation
        else f"对「{audience}」而言，{brand} 需要一个不被行业口号淹没的角度",
        f"而是正面回应「{unmet_objections[0]}」" if unmet_objections else "",
        f"，并用「{evidence_angles[0]}」自证" if evidence_angles else "",
    ]
    positioning_seed = "".join(part for part in seed_parts if part) + f"（围绕 {product} 展开，待 A1 改写）"

    lines = [
        f"同质化区（避让）：{'；'.join(common_claims) or '（无）'}",
        f"差异化空位（未被回应的阻力）：{'；'.join(unmet_objections) or '（无）'}",
        f"可占位的证据角度（我方须能提供）：{'；'.join(evidence_angles) or '（无）'}",
        f"可借鉴的差异化取角（案例库）：{'；'.join(case_angles) or '（无同类案例）'}",
        f"定位句骨架（示例，须改写为 Brief 口径）：{positioning_seed}",
        "（来源：内置行业洞察 + 案例库交叉推演——**非竞品投放数据**，"
        "证据角度须先确认本品牌确实能提供该材料，否则不得写入信息屋）",
    ]
    return ToolOutcome(
        summary=f"识别 {len(common_claims)} 类同质化主张、{len(unmet_objections)} 个差异化空位",
        detail="\n".join(lines),
        data={
            "industry": brief.industry,
            "common_claims": common_claims,
            "unmet_objections": unmet_objections,
            "evidence_angles": evidence_angles,
            "case_angles": case_angles,
            "positioning_seed": positioning_seed,
        },
    )


TOOLS: list[Tool] = [
    Tool(
        name="industry_insight",
        description="检索行业洞察画像（痛点/场景/动机/阻力/证明素材）",
        agent_ids=("A1",),
        handler=industry_insight,
    ),
    Tool(
        name="case_library",
        description="检索同行业、同渠道的历史案例",
        agent_ids=("A1",),
        handler=case_library,
    ),
    Tool(
        name="channel_landscape",
        description="查看全部候选渠道的内容形态，支撑渠道优先级判断",
        agent_ids=("A1",),
        handler=channel_landscape,
    ),
    Tool(
        name="differentiation_map",
        description="同质化主张 → 差异化空位推演（非竞品实测数据）",
        agent_ids=("A1",),
        handler=differentiation_map,
    ),
]

__all__ = [
    "TOOLS",
    "industry_insight",
    "case_library",
    "channel_landscape",
    "differentiation_map",
]
