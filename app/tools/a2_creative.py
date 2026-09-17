"""A2 创意总监智能体的工具集。

创意参考类工具：案例库（与 A1 共用同一知识源）、视觉风格库、开场钩子结构，
以及候选创意方向的**预评分**（direction_scoring）。

工具在生成前执行，因此 direction_scoring 评的不是 A2 自己的产出，而是把
上游策略要点 + 行业动机 + 案例角度汇成「候选方向池」先打一遍分，
让 A2 有可比较的先验排序，而不是凭直觉挑一个方向再补理由。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..knowledge.industry import cases_for, industry_profile
from ..knowledge.visual import visual_styles_for
from .base import Tool, ToolOutcome

if TYPE_CHECKING:
    from ..agents.base import AgentRunContext


def case_library(ctx: "AgentRunContext") -> ToolOutcome:
    """同行业、同渠道的历史案例，供 Big Idea 参考。"""
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
                {"name": c.name, "angle": c.angle, "why": c.why, "channel": c.channel}
                for c in cases
            ]
        },
    )


def visual_style_library(ctx: "AgentRunContext") -> ToolOutcome:
    """按行业匹配度排序的视觉风格候选，支撑调性指南中的视觉建议。"""
    brief = ctx.brief
    styles = visual_styles_for(brief.industry)[:3]
    lines = [
        f"{index}. {style.name}｜情绪：{style.mood}｜构图：{style.composition}｜光线：{style.lighting}"
        for index, style in enumerate(styles, start=1)
    ]
    lines.append("（来源：内置视觉风格库）")
    return ToolOutcome(
        summary=f"检索到 {len(styles)} 个候选视觉风格",
        detail="\n".join(lines),
        data={
            "styles": [
                {
                    "key": style.key,
                    "name": style.name,
                    "mood": style.mood,
                    "composition": style.composition,
                    "lighting": style.lighting,
                }
                for style in styles
            ]
        },
    )


def hook_patterns(ctx: "AgentRunContext") -> ToolOutcome:
    """该渠道的必备内容结构，作为开场钩子与节奏的骨架参考。"""
    brief = ctx.brief
    from ..knowledge.industry import channel_rule

    rule = channel_rule(brief.channel)
    lines = [
        f"内容结构（{brief.channel}）：{' → '.join(rule.blocks)}",
        f"标题风格：{rule.title_style}",
        "（来源：内置渠道规范库——钩子须落在结构前段，不得承诺效果）",
    ]
    return ToolOutcome(
        summary=f"「{brief.channel}」内容结构与标题风格",
        detail="\n".join(lines),
        data={"blocks": rule.blocks, "title_style": rule.title_style},
    )


#: 情绪张力词：能唤起具体感受的表达，比抽象动机更容易被记住
_EMOTION_WORDS = (
    "焦虑", "后悔", "省心", "放心", "自信", "松弛", "安心", "舒服", "惊喜", "尴尬",
    "累", "难", "麻烦", "踩坑", "纠结", "怕", "终于", "不用", "再也不", "省下",
)

#: 空洞词：出现在方向里说明还没落到可执行层面
_EMPTY_WORDS = (
    "品质", "高端", "专业", "领先", "全方位", "一站式", "赋能", "价值", "理念", "升级", "匠心",
)

_SCORE_KEYS = ("distinctiveness", "emotional_pull", "substantiable", "channel_fit", "extendable")

_SCORE_LABEL = {
    "distinctiveness": "差异化",
    "emotional_pull": "情绪张力",
    "substantiable": "可证实性",
    "channel_fit": "渠道适配",
    "extendable": "可延展性",
}


def _clamp(value: float) -> int:
    return int(max(5, min(100, round(value))))


def _candidates(ctx: "AgentRunContext") -> list[dict[str, str]]:
    """候选方向池：A1 结论/利益点 + 行业动机 + 案例角度（去重，保序）。"""
    from ..llm.json_utils import as_obj, as_str_array

    strategy = ctx.upstream_of("strategy")
    house = as_obj(strategy.get("message_house"))
    profile = industry_profile(ctx.brief.industry)

    pooled: list[tuple[str, str]] = []
    pooled.extend(("A1 关键结论", text) for text in as_str_array(strategy.get("key_takeaways"))[:3])
    pooled.extend(("A1 利益点", text) for text in as_str_array(house.get("benefits"))[:2])
    pooled.extend(("行业动机", text) for text in profile.motivations[:2])
    pooled.extend(
        ("案例角度", case.angle) for case in cases_for(ctx.brief.industry, ctx.brief.channel)[:2]
    )

    seen: set[str] = set()
    candidates: list[dict[str, str]] = []
    for source, text in pooled:
        text = text.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        candidates.append({"source": source, "text": text})
    return candidates[:8]


def _score_direction(text: str, industry: str) -> dict[str, int]:
    """五个维度的启发式打分（规则确定、可复现，非用户测试数据）。"""
    profile = industry_profile(industry)
    motivation_hits = [m for m in profile.motivations if m and m in text]
    empty_hits = [w for w in _EMPTY_WORDS if w in text]
    emotion_hits = [w for w in _EMOTION_WORDS if w in text]
    proof_hits = [a for a in profile.proof_assets if a and a in text]
    scene_hits = [s for s in profile.scenarios if s and s in text]

    dims = {
        # 与行业通用动机重合越多越难被区分；空洞词说明还没落到可执行
        "distinctiveness": _clamp(88 - 26 * len(motivation_hits) - 9 * len(empty_hits)),
        "emotional_pull": _clamp(45 + 20 * len(emotion_hits)),
        # 愿意落在可提供的自证材料上，才不靠口号
        "substantiable": _clamp(55 + 30 * len(proof_hits)),
        # 渠道标题风格要求短促，方向描述过长意味着还没收敛
        "channel_fit": 90 if len(text) <= 20 else (74 if len(text) <= 40 else 55),
        "extendable": _clamp(50 + 25 * len(scene_hits)),
    }
    dims["overall"] = _clamp(sum(dims.values()) / len(_SCORE_KEYS))
    return dims


def direction_scoring(ctx: "AgentRunContext") -> ToolOutcome:
    """候选创意方向预评分：五维启发式打分 + 排序，供 A2 取舍与偏离说明。"""
    candidates = _candidates(ctx)
    if not candidates:
        return ToolOutcome(summary="上游暂无可用素材，无法预评分", data={"directions": []})

    scored: list[dict[str, Any]] = []
    for item in candidates:
        dims = _score_direction(item["text"], ctx.brief.industry)
        scored.append({**item, **dims})
    scored.sort(key=lambda row: row["overall"], reverse=True)

    lines = []
    for index, row in enumerate(scored[:6], start=1):
        breakdown = "、".join(f"{_SCORE_LABEL[key]} {row[key]}" for key in _SCORE_KEYS)
        lines.append(f"{index}. [{row['source']}] {row['text']}｜综合 {row['overall']}（{breakdown}）")
    lines.append(
        "（来源：内置启发式规则打分——**不是用户测试或投放数据**，"
        f"只用于候选方向的先验排序；{_SCORE_LABEL['distinctiveness']}低说明与行业通用主张重合，"
        "不得直接照搬，须说明最终选择或偏离该排序的理由）"
    )
    top = scored[0]
    return ToolOutcome(
        summary=f"预评分 {len(scored)} 个候选方向，最高「{top['text'][:16]}」{top['overall']} 分",
        detail="\n".join(lines),
        data={
            "directions": scored,
            "score_keys": list(_SCORE_KEYS),
            "score_labels": _SCORE_LABEL,
        },
    )


TOOLS: list[Tool] = [
    Tool(
        name="case_library",
        description="检索同行业、同渠道的历史案例",
        agent_ids=("A2",),
        handler=case_library,
    ),
    Tool(
        name="visual_style_library",
        description="按行业匹配度检索视觉风格候选",
        agent_ids=("A2",),
        handler=visual_style_library,
    ),
    Tool(
        name="hook_patterns",
        description="该渠道的必备内容结构与标题风格",
        agent_ids=("A2",),
        handler=hook_patterns,
    ),
    Tool(
        name="direction_scoring",
        description="候选创意方向五维预评分与排序（启发式，非投放数据）",
        agent_ids=("A2",),
        handler=direction_scoring,
    ),
]

__all__ = [
    "TOOLS",
    "case_library",
    "visual_style_library",
    "hook_patterns",
    "direction_scoring",
]
