"""内置离线生成引擎 Mock Provider（移植自 server/llm/mock.ts）。

目标：在没有任何模型密钥的情况下，让「Brief → 交付物」的完整链路真实可跑，
并且产出结构、字段名、门禁信号与真实模型完全一致 —— 切换到真实模型时上层无需改动。

它不是随机文本生成器，而是「规则 + 知识库」的创作引擎：
  - A1/A2/A3 从行业洞察库与案例库取材
  - A4 按渠道模板组装可读文案，并在无返工意见时故意保留风险表达（用于演示门禁）
  - A5/A6/A7 的评审信号来自真实可测量的文本特征与合规词库扫描

与 TS 版的一处已知差异：``fnv1a`` 按 Unicode 码点迭代，而 JS 的 ``charCodeAt``
按 UTF-16 码元迭代，因此在含 emoji 等非 BMP 字符的 Brief 上可能选出不同的素材组合。
这只影响 Mock 内容的取材顺序，不影响任何契约或门禁行为。
"""

from __future__ import annotations

import json
import random
import re
import time
from collections.abc import Callable
from typing import Any

from ..core.types import Brief
from ..core.util import js_round
from ..knowledge.compliance import auto_rewrite, check_brand_voice, scan_compliance
from ..knowledge.industry import (
    cases_for,
    channel_rule,
    industry_profile,
    publish_slots,
    seo_pattern,
    title_limit,
)
from ..knowledge.language import normalize_language, title_limit_for
from ..knowledge.memory import evidence_from_hits
from ..knowledge.visual import channel_visual_spec, visual_styles_for
from ..knowledge.video import script_skeleton, video_spec
from .json_utils import (
    as_num,
    as_obj,
    as_obj_array,
    as_str,
    as_str_array,
)
from .types import LLMRequest, LLMResponse, LLMUsage, estimate_tokens

# ------------------------------------------------------------------ #
# 工具                                                                #
# ------------------------------------------------------------------ #


def rec(value: Any) -> dict[str, Any]:
    return as_obj(value)


def fnv1a(text: str) -> int:
    """等价于 TS 版 ``hash()``：FNV-1a 32 位，返回 32 位有符号值的绝对值。"""
    h = 2166136261
    for ch in text:
        h ^= ord(ch)
        h &= 0xFFFFFFFF
        h = (h * 16777619) & 0xFFFFFFFF
    signed = h - 0x100000000 if h >= 0x80000000 else h
    return abs(signed)


_TONE_SPLIT_RE = re.compile(r"[、,，]")


def pick(items: list[Any], seed: int, offset: int = 0) -> Any:
    return items[(seed + offset) % len(items)]


def pick_many(items: list[Any], count: int, seed: int) -> list[Any]:
    return [items[(seed + i * 7) % len(items)] for i in range(min(count, len(items)))]


def shorten(text: str, max_len: int) -> str:
    return f"{text[:max_len]}…" if len(text) > max_len else text


def as_brief(ctx: dict[str, Any]) -> Brief:
    raw = rec(ctx.get("brief"))
    return Brief(
        brand=as_str(raw.get("brand"), "品牌"),
        product=as_str(raw.get("product"), "产品"),
        objective=as_str(raw.get("objective"), "曝光"),
        audience=as_str(raw.get("audience"), "目标人群"),
        channel=as_str(raw.get("channel"), "小红书"),
        tone=as_str(raw.get("tone"), "轻松、真实"),
        industry=as_str(raw.get("industry"), "消费品"),
        # 语言必须透传：漏掉它会让本地化分支永远走不到（默认回落 zh）
        language=normalize_language(as_str(raw.get("language"))),
        keywords=as_str_array(raw.get("keywords")),
        constraints=as_str_array(raw.get("constraints")),
        deliverables=as_str_array(raw.get("deliverables")),
        notes=as_str(raw.get("notes")),
        priority=as_str(raw.get("priority"), "normal"),  # type: ignore[arg-type]
        deadline=raw.get("deadline") if isinstance(raw.get("deadline"), str) else None,
    )


def memory_hits(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """读取编排层注入的 A11 记忆召回结果（RAG）。"""
    hits = ctx.get("memory")
    if not isinstance(hits, list):
        return []
    return [item for item in hits if isinstance(item, dict)]


def memory_evidence(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    """离线引擎同样让「复用历史资产」在结果里可见，与真实模型路径保持一致。"""
    return evidence_from_hits(memory_hits(ctx), limit=2)


# ------------------------------------------------------------------ #
# A1 策略与洞察                                                       #
# ------------------------------------------------------------------ #


def _english_strategy(brief: Brief, keyword: str, seed: int) -> dict[str, Any]:
    """英文策略简报（mock 本地化分支）。

    行业洞察库（``industry_profile``）是中文语料，直接复用会把中文漏进英文文案。
    这里改为用 Brief 本身的信息产出英文结论 —— 离线环境下的目标不是「洞察有多深」，
    而是**整条链路在目标语言下自洽**，这样黄金数据集与自检才能验证多语言行为。
    """
    audience = brief.audience or "the core buying audience"
    product = brief.product
    return {
        "audience_profile": {
            "segment": audience,
            "size_hint": f"{audience} is the core decision-making and advocacy group for {brief.industry}",
            "pain_points": [
                f"too many options when choosing {keyword}",
                "claims are hard to verify before buying",
                "the cost of getting it wrong is high",
            ],
            "scenarios": [
                "a normal weekday morning",
                "the moment right before a decision",
                "the first week of use",
            ],
            "motivations": [
                "spend less time deciding",
                "avoid repeating a bad purchase",
                "feel confident recommending it",
            ],
            "objections": [
                "is this actually different from the cheaper option",
                "will it still work in three months",
            ],
        },
        "message_house": {
            "proposition": (
                f"{brief.brand} helps {audience} get {product} right the first time — "
                f"verifiable, not overstated"
            ),
            "support_points": [
                f"a concrete difference built around {keyword}",
                f"{product} maps directly to the cost of choosing wrong",
                f"a claim you can repeat to a friend in one sentence on {brief.channel}",
            ],
            "benefits": [
                "less time spent comparing",
                "fewer decisions to re-litigate",
                "a recommendation you can stand behind",
            ],
            "evidence": [
                {"type": asset, "status": "needed", "note": "Collect real usage evidence before launch"}
                for asset in ("spec sheet", "usage log", "third-party test")
            ],
        },
        "objectives": [
            {"type": _english_objective(brief.objective), "metric": "reach", "target": "baseline+30%"},
            {"type": "trust", "metric": "save_rate", "target": "baseline+15%"},
        ],
        "channel_priority": [
            {"channel": brief.channel, "reason": "primary channel from the brief", "priority": 1},
            {"channel": "Email", "reason": "low-cost retention surface", "priority": 2},
        ],
        "confidence": 0.8,
        "risks": ["English insights are generated without a local industry corpus"],
        "evidence": [
            {
                "claim": f"primary keyword is {keyword}",
                "source": "brief",
                "reliability": 0.9,
            }
        ],
    }


def generate_strategy(ctx: dict[str, Any]) -> dict[str, Any]:
    brief = as_brief(ctx)
    profile = industry_profile(brief.industry)
    seed = fnv1a(f"{brief.brand}{brief.product}{brief.industry}")
    keyword = brief.keywords[0] if brief.keywords else brief.industry

    pain_points = pick_many(profile.pain_points, 3, seed)
    scenarios = pick_many(profile.scenarios, 3, seed + 2)
    motivations = pick_many(profile.motivations, 3, seed + 5)

    # 非中文 Brief：行业洞察库目前只有中文语料，若直接沿用会让英文文案里夹中文。
    # 离线分支改为产出英文结论（真实模型由本地化指令驱动，不受此限）。
    if normalize_language(brief.language) == "en":
        return _english_strategy(brief, keyword, seed)

    return {
        "audience_profile": {
            "segment": brief.audience,
            "size_hint": f"按行业公开口径，{brief.industry}中该人群属于核心决策与传播人群",
            "pain_points": pain_points,
            "scenarios": scenarios,
            "motivations": motivations,
            "objections": pick_many(profile.objections, 2, seed + 1),
        },
        "message_house": {
            "proposition": f"{brief.brand}让{brief.audience}在{scenarios[0]}时，用更省心的方式获得{motivations[0]}",
            "support_points": [
                f"围绕「{keyword}」建立可感知的差异点",
                f"{brief.product}的核心能力直接对应{pain_points[0]}",
                f"在{brief.channel}语境下形成可复述的记忆点",
            ],
            "benefits": [
                f"解决「{pain_points[0]}」",
                f"在{scenarios[1]}场景下减少决策成本",
                f"满足对{motivations[1]}的期待",
            ],
            "evidence": [
                {
                    "type": asset,
                    "status": "需补充素材" if index == 0 else "待采集",
                    "note": "建议在正式投放前补齐实测素材" if index == 0 else "可在发布前由品牌方提供",
                }
                for index, asset in enumerate(pick_many(profile.proof_assets, 3, seed + 3))
            ],
        },
        "objectives": [
            {
                "type": brief.objective,
                "metric": "互动率 / 收藏率" if brief.channel == "小红书" else "CTR",
                "target": "高于账号近 30 天均值",
            },
            {"type": "信任", "metric": "评论正向占比", "target": "≥ 70%"},
        ],
        "channel_priority": [
            {"channel": brief.channel, "weight": 0.6, "why": "与目标人群主活跃阵地一致，内容形态匹配度最高"},
            {
                "channel": "抖音" if brief.channel == "小红书" else "小红书",
                "weight": 0.3,
                "why": "同人群跨平台二次触达，复用素材成本低",
            },
            {"channel": "公众号", "weight": 0.1, "why": "承接深度内容与品牌资产沉淀"},
        ],
        "tone_guide": {
            "keywords": [t.strip() for t in _TONE_SPLIT_RE.split(brief.tone) if t.strip()],
            "avoid": ["绝对化用语", "夸大功效", "居高临下的说教口吻"],
        },
        "key_takeaways": [
            f"核心人群诉求集中在「{pain_points[0]}」",
            f"内容需在{brief.channel}语境下先建立可信度，再谈卖点",
            f"「{motivations[0]}」是比参数更有力的说服角度",
        ],
        "confidence": 0.82,
        "risks": [
            "受众画像基于行业公开经验推断，未经一手用户调研验证",
            f"{profile.proof_assets[0]}等关键证据素材尚未提供，可能影响说服力",
        ],
        "evidence": [
            {
                "claim": f"{brief.industry}人群主要痛点集中在「{pain_points[0]}」",
                "source": f"行业内容经验库（{brief.industry}）",
                "reliability": 0.7,
            },
            {
                "claim": f"{brief.channel}的内容形态为「{channel_rule(brief.channel).format}」",
                "source": "平台公开规则与运营实践",
                "reliability": 0.9,
            },
            *memory_evidence(ctx),
        ],
    }


# ------------------------------------------------------------------ #
# A2 创意总监                                                         #
# ------------------------------------------------------------------ #


def _english_creative(brief: Brief, strategy: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """英文创意概念（mock 本地化分支，A2）。

    与 ``_english_strategy`` 同一原则：行业方法论/案例库是中文语料，
    直接复用会把中文漏进英文支撑产物。这里用 Brief 与上游英文策略产出
    结构完全一致的英文内容 —— 离线环境下目标不是洞察深度，而是
    「整条链路在目标语言下自洽」。
    """
    audience = rec(strategy.get("audience_profile"))
    house = rec(strategy.get("message_house"))
    pain = (as_str_array(audience.get("pain_points")) or ["too many options, not enough signal"])[0]
    scenario = (as_str_array(audience.get("scenarios")) or ["a normal weekday"])[0]
    proposition = as_str(house.get("proposition"), f"{brief.brand} {brief.product}")
    keyword = brief.keywords[0] if brief.keywords else brief.industry

    return {
        "big_idea": {
            "title": f"{brief.brand} · {pick(['Make the choice easy', 'Right the first time', 'Less deciding, more doing'], 7)}",
            "statement": (
                f"{proposition}. Not about how great we are — it is about {brief.audience} "
                "seeing a calmer version of their own routine."
            ),
            "rationale": "Anchor the brand promise in the user's own state change, not in product specs.",
        },
        "directions": [
            {
                "id": "D1",
                "name": "First-hand testimony",
                "angle": (
                    f"Follow one specific person through {scenario} with real usage details; "
                    "persuade with specifics, not adjectives"
                ),
                "hook": f"I dealt with '{pain}' for three months before finding this",
                "sample_headline": f"Day 30 of {scenario}: {brief.product} stayed",
                "rationale": "Authenticity counters ad defensiveness and builds long-term trust",
                "risk": "Needs real material; invented details will backfire",
                "fit_score": 88,
            },
            {
                "id": "D2",
                "name": "Counter-intuitive question",
                "angle": "Name the common misconception first, then offer the product capability as the new answer",
                "hook": f"'{pain}' is probably not about effort",
                "sample_headline": f"We may have been getting {brief.product} wrong all along",
                "rationale": "A knowledge gap drives read-through and a professional image",
                "risk": "Argument-heavy; weak evidence invites pushback",
                "fit_score": 81,
            },
            {
                "id": "D3",
                "name": "Concrete number anchor",
                "angle": (
                    f"Quantify the value in verifiable time/count units, landing on '{keyword}'"
                ),
                "hook": f"Turn '{pain}' into a {30 + (7 % 40)}-second task",
                "sample_headline": f"{brief.product}: give the saved time back",
                "rationale": "Quantified claims lower comprehension cost and suit conversion goals",
                "risk": "Numbers must have sources, or A6 fact-check and compliance will block them",
                "fit_score": 84,
            },
        ],
        "tone_guide": {
            "voice": brief.tone,
            "dos": [
                "talk in second person",
                "one idea per paragraph",
                "empathize before offering the solution",
                "concrete details over adjectives",
            ],
            "donts": [
                "no absolute claims",
                "no effect or income promises",
                "no disparaging other brands",
                "no jargon pile-up",
            ],
            "visual_suggestion": "Real usage scenes over studio polish; avoid over-retouched, staged looks",
        },
        # 爆款案例库是中文语料：英文分支不引用，宁可留空也不夹中文
        "reference_cases": [],
        "recommended_direction": "D1",
        "recommendation_reason": (
            f"Closest to the '{brief.tone}' voice with a controllable material bar; "
            f"lowest read-through risk in {brief.channel}'s feed"
        ),
        "confidence": 0.79,
        "risks": [
            "Direction D3 involves quantified claims; A6 must verify sources before use",
            "Direction D2 may draw negative comments if the argument is under-evidenced",
            "The reference case library is Chinese-only; cases were skipped for this brief",
        ],
        "evidence": [
            {
                "claim": "Scene-anchored content holds attention better than generic claims",
                "source": "content methodology (public retros)",
                "reliability": 0.75,
            },
            *memory_evidence(ctx),
        ],
    }


def generate_creative(ctx: dict[str, Any]) -> dict[str, Any]:
    brief = as_brief(ctx)
    strategy = rec(ctx.get("strategy"))
    # 非中文 Brief：方法论与案例库是中文语料，改走英文模板（同 _english_strategy）
    if normalize_language(brief.language) == "en":
        return _english_creative(brief, strategy, ctx)
    house = rec(strategy.get("message_house"))
    audience = rec(strategy.get("audience_profile"))
    seed = fnv1a(f"{brief.brand}{brief.channel}{brief.tone}{brief.product}")
    pain = (as_str_array(audience.get("pain_points")) or ["选择成本高"])[0]
    scenario = (as_str_array(audience.get("scenarios")) or ["日常使用"])[0]
    proposition = as_str(house.get("proposition"), f"{brief.brand}的{brief.product}")
    cases = cases_for(brief.industry, brief.channel)

    directions = [
        {
            "id": "D1",
            "name": "真实体验见证",
            "angle": f"以一个具体的人在{scenario}的真实使用过程为主线，用细节而非形容词说服",
            "hook": f"「{pain}」这件事，我试了三个月才找到答案",
            "sample_headline": f"{scenario}的第 30 天，我把它留在了购物车里",
            "rationale": "真实感对抗广告防御心理，适合建立长期品牌信任",
            "risk": "需要真实素材支撑，虚构细节会引发反噬",
            "fit_score": 88,
        },
        {
            "id": "D2",
            "name": "反常识提问",
            "angle": "先指出目标人群的普遍误区，再用产品能力给出新解",
            "hook": f"{pain}，可能不是你不够努力",
            "sample_headline": f"关于{brief.product}，我们可能一直都做错了",
            "rationale": "认知缺口带来高完读率，利于专业形象塑造",
            "risk": "对论据要求高，论证不充分会被质疑",
            "fit_score": 81,
        },
        {
            "id": "D3",
            "name": "具体数字锚定",
            "angle": f"用可验证的时间/数量单位把价值量化，落在「{brief.keywords[0] if brief.keywords else '核心卖点'}」上",
            "hook": f"把「{pain}」压缩到 {30 + (seed % 40)} 秒能搞定的事",
            "sample_headline": f"{brief.product}：把省下的时间还给你",
            "rationale": "量化表达降低理解成本，适合转化导向",
            "risk": "数字必须有依据，否则触发事实核查与合规风险",
            "fit_score": 84,
        },
    ]

    return {
        "big_idea": {
            "title": f"{brief.brand}·{pick(['把复杂留给我们', '刚刚好的选择', '让每一步都算数'], seed)}",
            "statement": f"{proposition}。不是讲我们有多好，而是让{brief.audience}看见自己可以更从容。",
            "rationale": "把品牌主张落在用户自身的状态变化上，而非产品参数上。",
        },
        "directions": directions,
        "tone_guide": {
            "voice": brief.tone,
            "dos": ["用第二人称对话", "每段只讲一件事", "先共情再给方案", "具体细节替代形容词"],
            "donts": ["不使用绝对化用语", "不承诺效果与收益", "不贬损同类品牌", "不堆砌行业术语"],
            "visual_suggestion": "画面以真实使用场景为主，避免过度修图与摆拍感",
        },
        "reference_cases": [
            {"name": item.name, "why": item.why, "source": item.source} for item in cases
        ],
        "recommended_direction": "D1",
        "recommendation_reason": f"与「{brief.tone}」调性最一致，且素材门槛可控，{brief.channel}的推荐流环境下完读风险最低",
        "confidence": 0.79,
        "risks": [
            "方向 D3 涉及量化表达，需 A6 核查数据来源后才能使用",
            "方向 D2 的论点若论据不足可能引发负面评论",
        ],
        "evidence": [
            {
                "claim": "具体场景锚定的内容完读率高于泛化表达",
                "source": "行业内容方法论（公开复盘）",
                "reliability": 0.75,
            },
            {
                "claim": f"参考案例「{cases[0].name if cases else '—'}」的 {cases[0].why if cases else ''}",
                "source": cases[0].source if cases else "公开案例库",
                "reliability": 0.7,
            },
            *memory_evidence(ctx),
        ],
    }


# ------------------------------------------------------------------ #
# A3 内容策划                                                         #
# ------------------------------------------------------------------ #

# 英文分支共用的渠道形态描述（中文 CHANNEL_RULES 是中文平台语料，直接引用会夹中文）
_ENGLISH_FORMAT_HINTS: dict[str, str] = {
    "instagram": "caption + carousel",
    "tiktok": "short video script",
    "youtube": "video script + description",
    "email": "newsletter",
    "twitter": "thread",
    "x": "thread",
    "facebook": "feed post",
    "linkedin": "professional post",
    "blog": "long-form article",
}

_ENGLISH_OBJECTIVES: dict[str, str] = {
    "转化": "conversion",
    "种草": "seeding",
    "教育": "education",
    "曝光": "awareness",
    "信任": "trust",
}


def _english_format(channel: str) -> str:
    """英文 Brief 的渠道形态描述；未知渠道回落到通用表述而非中文规则库。"""
    low = (channel or "").lower()
    for key, hint in _ENGLISH_FORMAT_HINTS.items():
        if key in low:
            return hint
    return "native post"


def _english_objective(objective: str) -> str:
    """Brief 的 objective 允许中文（如「转化」），英文产物里映射为英文词。"""
    return _ENGLISH_OBJECTIVES.get((objective or "").strip(), objective)


def _compress_title_words(title: str, limit: int) -> str:
    """英文标题按「词」压缩（中文 _compress_title 按字符，口径不通用）。"""
    words = [w for w in (title or "").split() if w]
    return " ".join(words[:limit]) if len(words) > limit else (title or "")


def _english_plan(brief: Brief, ctx: dict[str, Any]) -> dict[str, Any]:
    """英文内容策划（A3）：结构同中文分支，选题/大纲/节奏全部英文原生。"""
    strategy = rec(ctx.get("strategy"))
    creative = rec(ctx.get("creative"))
    audience = rec(strategy.get("audience_profile"))
    house = rec(strategy.get("message_house"))

    pain = (as_str_array(audience.get("pain_points")) or ["choosing is exhausting"])[0]
    benefit = (as_str_array(house.get("benefits")) or ["less to think about"])[0]
    scenario = (as_str_array(audience.get("scenarios")) or ["a normal weekday"])[0]
    directions = as_obj_array(creative.get("directions"))
    chosen = next(
        (d for d in directions if as_str(d.get("id")) == as_str(creative.get("recommended_direction"))),
        None,
    ) or (directions[0] if directions else {})
    chosen_name = as_str(chosen.get("name"), "First-hand testimony")
    keyword = brief.keywords[0] if brief.keywords else brief.product
    content_format = _english_format(brief.channel)

    topics = [
        {
            "id": "T1",
            "title": f"Still {pain.lower()}? I used {brief.product} for 30 days",
            "angle": chosen_name,
            "format": content_format,
            "outline": [
                f"Hook: open with '{pain}' so {brief.audience} recognize themselves in one line",
                f"Scene: the concrete details of {scenario}",
                f"Solution: how {brief.product} addresses '{pain}' — three points only",
                "Evidence: the testing process and verifiable material",
                "Action: one low-effort next step",
            ],
            "cta": "If you want to try it, the link is in the comments",
            "estimated_words": 420,
        },
        {
            "id": "T2",
            "title": f"About '{keyword}': three counter-intuitive things first",
            "angle": "Counter-intuitive question",
            "format": content_format,
            "outline": [
                "Conclusion first: state the view that contradicts the default",
                "Argument 1: where the common approach quietly costs more",
                f"Argument 2: how {brief.product}'s difference is actually built",
                "Argument 3: verifiable data or a case",
                "Close: offer a decision standard instead of a hard sell",
            ],
            "cta": "Which approach do you side with? Tell me in the comments",
            "estimated_words": 520,
        },
        {
            "id": "T3",
            "title": f"Cutting through '{pain}': {max(3, len(brief.keywords))} things that matter",
            "angle": "Concrete number anchor",
            "format": content_format,
            "outline": [
                "Open with one set of numbers that frames the value",
                "Break down the key points, each with a usage scene",
                "Contrast: the cost of not doing this",
                "Trust: sources and how to verify",
                "Call to action",
            ],
            "cta": "Save this for your next purchase decision",
            "estimated_words": 380,
        },
    ]

    keyword_short = keyword if len(keyword) <= 20 else keyword[:20]
    return {
        "topics": topics,
        "selected_topic": "T1",
        "selection_reason": (
            f"Matches the creative direction '{chosen_name}' and fits how {brief.channel} distributes content"
        ),
        "headline_candidates": [
            f"Still {pain.lower()}? I switched to {brief.product}",
            f"30 days with {brief.product}",
            f"The {brief.product} my coworkers keep asking about",
            f"Stop powering through '{pain}' — there is a fix",
            f"Is {keyword_short} worth it? Read this first",
        ],
        "structure": [
            {"section": "Opening hook", "goal": "Establish identity recognition in 3 seconds", "words": 60},
            {"section": "Pain empathy", "goal": f"Make {brief.audience} think 'this is me'", "words": 80},
            {"section": "Solution", "goal": f"Present '{benefit}'", "words": 160},
            {"section": "Evidence", "goal": "Defuse the 'is this a scam' doubt", "words": 80},
            {"section": "Call to action", "goal": "One clear next step", "words": 40},
        ],
        "channel_adaptation": [
            {"channel": brief.channel, "format": content_format, "notes": "primary channel from the brief"},
            {"channel": "Short video", "format": "storyboard script", "notes": "reuse the topic, rewrite as a 45-second voice-over"},
        ],
        "publishing_rhythm": [
            {"slot": "T+0", "action": f"Publish the {brief.channel} primary post", "note": "pick the audience's active hours"},
            {"slot": "T+2d", "action": "Second-round answers in the comments", "note": "harvest frequent questions as the next topic"},
            {"slot": "T+7d", "action": "Performance review", "note": "compare headline A/B results"},
        ],
        "keywords": {
            "primary": brief.keywords,
            "long_tail": [
                f"how to choose {keyword}",
                f"{brief.product} honest review",
                f"{brief.audience} {keyword}",
            ],
            "hashtags": f"#{brief.brand.replace(' ', '')} #{keyword.replace(' ', '')} #{brief.industry.replace(' ', '')}",
        },
        "confidence": 0.8,
        "risks": ["Topic T1 depends on a real usage cycle; prepare verifiable material in advance"],
        "evidence": [
            {
                "claim": f"{brief.channel} content format is '{content_format}'",
                "source": "platform public rules",
                "reliability": 0.85,
            },
            {
                "claim": "Hook-solution-evidence structure fits feed-based discovery",
                "source": "content operations practice",
                "reliability": 0.75,
            },
        ],
    }


def generate_plan(ctx: dict[str, Any]) -> dict[str, Any]:
    brief = as_brief(ctx)
    # 非中文 Brief：渠道规则库与选题模板是中文语料，改走英文模板
    if normalize_language(brief.language) == "en":
        return _english_plan(brief, ctx)
    strategy = rec(ctx.get("strategy"))
    creative = rec(ctx.get("creative"))
    audience = rec(strategy.get("audience_profile"))
    house = rec(strategy.get("message_house"))
    rule = channel_rule(brief.channel)

    pain = (as_str_array(audience.get("pain_points")) or ["选择困难"])[0]
    short_pain = pain[:8] if len(pain) > 8 else pain
    benefit = (as_str_array(house.get("benefits")) or ["更省心"])[0]
    directions = as_obj_array(creative.get("directions"))
    chosen = next(
        (d for d in directions if as_str(d.get("id")) == as_str(creative.get("recommended_direction"))),
        None,
    ) or (directions[0] if directions else {})
    keyword = brief.keywords[0] if brief.keywords else brief.product
    first_scenario = (as_str_array(audience.get("scenarios")) or ["日常使用"])[0]
    short_product = shorten(brief.product, 8)

    topics = [
        {
            "id": "T1",
            "title": f"{pain}？我把{brief.product}用了 30 天",
            "angle": as_str(chosen.get("name"), "真实体验见证"),
            "format": rule.format,
            "outline": [
                f"钩子：用「{pain}」直接切入，让{brief.audience}第一句就认出自己",
                f"场景：还原{first_scenario}的具体细节",
                f"方案：{brief.product}如何解决「{pain}」，只讲 3 个点",
                "证据：实测过程与可核验的素材",
                "行动：给出低门槛的下一步动作",
            ],
            "cta": "想试试的话，我把链接放在评论区了",
            "estimated_words": 420,
        },
        {
            "id": "T2",
            "title": f"关于「{keyword}」，先说三个反常识",
            "angle": "反常识提问",
            "format": rule.format,
            "outline": [
                "结论先行：直接给出与常识相反的观点",
                "论据一：行业普遍做法的成本在哪",
                f"论据二：{brief.product}的差异点如何形成",
                "论据三：可验证的数据或案例",
                "收束：给出一个判断标准而非硬推",
            ],
            "cta": "你更认同哪种做法？评论区聊聊",
            "estimated_words": 520,
        },
        {
            "id": "T3",
            "title": f"把「{pain}」压缩掉：{brief.product}的 {len(brief.keywords) or 3} 个关键点",
            "angle": "具体数字锚定",
            "format": rule.format,
            "outline": [
                "用一组数字给出整体价值",
                "逐条拆解关键点，每点配一个使用场景",
                "对比：不做这件事的成本",
                "信任：来源与依据说明",
                "行动引导",
            ],
            "cta": "收藏这篇，下次换购前翻出来看",
            "estimated_words": 380,
        },
    ]

    return {
        "topics": topics,
        "selected_topic": "T1",
        "selection_reason": f"与创意方向「{as_str(chosen.get('name'), '真实体验见证')}」一致，且最贴合{brief.channel}的推荐机制",
        # 标题控制在 20 字以内，符合小红书信息流截断规则
        "headline_candidates": [
            f"{short_pain}？我换了{short_product}",
            f"{short_product}用了 30 天",
            f"同事追着问链接的{short_product}",
            f"别再硬扛了，{short_pain}有解",
            f"{(brief.keywords[0] if brief.keywords else brief.product)[:10]}值不值？看完再决定",
        ],
        "structure": [
            {"section": "开头钩子", "goal": "3 秒内建立身份认同", "words": 60},
            {"section": "痛点共鸣", "goal": f"让{brief.audience}确认「说的是我」", "words": 80},
            {"section": "方案展开", "goal": f"呈现{benefit}", "words": 160},
            {"section": "证据支撑", "goal": "降低「是不是智商税」的疑虑", "words": 80},
            {"section": "行动引导", "goal": "给出明确的下一步", "words": 40},
        ],
        "channel_adaptation": [
            {"channel": brief.channel, "format": rule.format, "notes": rule.length_hint},
            {"channel": "短视频", "format": "分镜脚本", "notes": "复用同一选题，改写为 45 秒口播"},
        ],
        "publishing_rhythm": [
            {"slot": "T+0", "action": f"发布{brief.channel}主贴", "note": "选择目标人群活跃时段"},
            {"slot": "T+2天", "action": "评论区二次答疑", "note": "沉淀高频问题作为下一条选题"},
            {"slot": "T+7天", "action": "数据复盘", "note": "对比标题 A/B 表现"},
        ],
        "keywords": {
            "primary": brief.keywords,
            "long_tail": [f"{keyword}怎么选", f"{brief.product}真实测评", f"{brief.audience}{keyword}"],
            "hashtags": f"#{brief.brand} #{keyword} #{brief.industry}",
        },
        "confidence": 0.8,
        "risks": ["选题 T1 依赖真实使用周期，建议提前准备可核验的素材"],
        "evidence": [
            {"claim": f"{brief.channel}的内容形态为「{rule.format}」", "source": "平台公开规则", "reliability": 0.85},
            {
                "claim": f"推荐结构「{' / '.join(rule.blocks[:3])}」",
                "source": "平台运营实践",
                "reliability": 0.75,
            },
        ],
    }


# ------------------------------------------------------------------ #
# A4 文案创作                                                         #
# ------------------------------------------------------------------ #

_WHITESPACE_RE = re.compile(r"\s")
_PARAGRAPH_SPLIT_RE = re.compile(r"\n+")
_SENTENCE_END_RE = re.compile(r"[。！？]")
_LONG_SENTENCE_SPLIT_RE = re.compile(r"[。！？\n]")
_COLLOQUIAL_RE = re.compile(r"(嗯|啊|吧|啦)+")
# 英文侧口径：句子按 .!? 切分；「口语感」用缩写词近似（中文按 嗯/啊/吧/啦）
_EN_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_EN_CONTRACTION_RE = re.compile(r"[a-z]'[a-z]", re.IGNORECASE)


def build_hashtags(brief: Brief) -> list[str]:
    keyword = brief.keywords[0] if brief.keywords else brief.industry
    return [
        f"#{brief.brand}",
        f"#{keyword}",
        f"#{brief.industry}",
        f"#{brief.audience[:8]}日常",
        "#真实使用分享",
    ][:5]


def compose_body(brief: Brief, opts: dict[str, Any]) -> str:
    """渠道化正文骨架：同一策略在不同渠道生成不同形态的内容。"""
    channel = brief.channel
    support: list[str] = opts["support"]
    benefits: list[str] = opts["benefits"]

    def at(index: int, fallback: str) -> str:
        return support[index] if index < len(support) else fallback

    if "抖音" in channel:
        return "\n\n".join(
            [
                f"【0-3s 钩子】画面：{opts['scenario']}，人物抬头看镜头。口播：「{opts['hook']}」",
                f"【3-8s 痛点】画面：特写手忙脚乱的细节。口播：「{opts['pain']}，很多人都卡在这一步。」",
                f"【8-25s 方案】画面：使用 {brief.brand} {brief.product} 的特写 + 前后对比。口播：「{opts['benefit']}。它是怎么做到的？{at(0, '')}」",
                f"【25-40s 证据】画面：真实素材 / 实拍细节。口播：「{at(1, '这是我自己用下来的真实记录')}。」",
                f"【40-50s 转化】画面：{brief.brand} 产品定格 + 字幕。口播：「{opts['cta']}」",
            ]
        )

    if "公众号" in channel:
        numbered = "\n".join(f"{i + 1}. {item}" for i, item in enumerate(benefits))
        return "\n".join(
            [
                opts["hook"],
                "",
                f"一、为什么「{opts['pain']}」这么难解决",
                f"我们在{opts['scenario']}这件事上反复试错，本质原因是可选项太多、判断标准太少。",
                "",
                f"二、{brief.brand}的{brief.product}给出的另一种做法",
                numbered,
                "",
                "三、依据与边界",
                f"需要说明的是，{at(2, '以下结论来自我们的实际使用记录')}。它并不适合所有人，如果你属于以下情况，可以再等等。",
                "",
                opts["cta"],
            ]
        )

    if "电商" in channel:
        numbered = "\n".join(f"{i + 1}. {item}" for i, item in enumerate(benefits))
        return "\n".join(
            [
                f"【首屏】{brief.brand}｜{opts['hook']}",
                "",
                f"【痛点场景】{opts['pain']}。{opts['scenario']}时尤其明显。",
                "",
                "【核心卖点】",
                numbered,
                "",
                f"【信任背书】{'；'.join(support)}",
                "",
                f"【售后保障】{brief.constraints[0] if brief.constraints else '以实际页面承诺为准'}",
            ]
        )

    if "知乎" in channel or "PR" in channel:
        return "\n".join(
            [
                f"先说结论：{opts['hook']}",
                "",
                f"论证一：{opts['pain']}——这是{brief.audience}最常遇到的卡点。",
                f"论证二：{brief.brand}主张{opts['benefit']}。{at(0, '')}",
                f"论证三：{at(1, '')}",
                "",
                "可能的反驳：有人会认为这套做法成本更高。我们的回答是，评价口径不同结论就不同，本文所述均为可复现的实际观测。",
                "",
                opts["cta"],
            ]
        )

    # 默认：小红书
    numbered = "\n".join(f"{i + 1}｜{item}" for i, item in enumerate(benefits))
    return "\n".join(
        [
            opts["hook"],
            "",
            f"作为{opts['scenario']}的{brief.audience}，我对{brief.product}的要求其实很简单——别给我添麻烦。",
            "",
            f"但现实是：{opts['pain']}。",
            "",
            f"换用{brief.brand}的{brief.product}之后，最直接的感受是：{opts['benefit']}。",
            "",
            "我留下它的三个原因：",
            numbered,
            "",
            f"{opts['styleFlavor']}",
            "",
            opts["cta"],
        ]
    )


def _english_copy(
    brief: Brief, pain: str, scenario: str, benefits: list[str], support: list[str], keyword: str
) -> dict[str, Any]:
    """英文文案（mock 的多语言分支）。

    为什么 mock 引擎也要做本地化：默认 ``LLM_PROVIDER=mock`` 时，如果英文 Brief
    仍然产出中文，那么「多语言」在离线环境下就是假的 —— 黄金数据集与自检都会
    因此得出错误结论。这里给出真正用目标语言写的内容，让离线链路可持续验证。

    **覆盖范围如实说明**：只有英文有完整的原生模板；其它语言会退化为
    「英文骨架 + 明确标注待本地化」，而不是假装已支持。
    """
    brand, product = brief.brand, brief.product
    benefit_line = "; ".join(benefits[:3]) if benefits else "simpler, steadier, less trial and error"
    support_line = support[0] if support else "based on real usage records"

    v1_title = f"Still {pain.lower()}? I switched to {product}"
    v1_body = (
        f"Full disclosure: I did not expect much from {product}.\n\n"
        f"I use it every day for {scenario.lower()}, and the part that actually surprised me "
        f"was how little I had to think about it.\n\n"
        f"{brand} keeps the spec sheet honest: {benefit_line}. "
        f"What convinced me is simpler — {support_line}.\n\n"
        f"If you are still dealing with {pain.lower()}, this is the version I would try first."
    )
    v2_title = f"{keyword}: what {product} actually trades off"
    v2_body = (
        f"Let me separate what I can verify from what I cannot.\n\n"
        f"{brand} {product} is built around {keyword}. In practice that means {benefit_line}.\n\n"
        f"What it does not do: it will not fix a problem you have not diagnosed yet. "
        f"Start with {scenario.lower()} and measure before and after."
    )
    v3_title = f"The morning I stopped thinking about {pain.lower()}"
    v3_body = (
        f"There is a version of {scenario.lower()} that does not require willpower.\n\n"
        f"I keep {product} where I can reach it without deciding anything. "
        f"{brand} made the boring parts invisible, and that turned out to be the whole trick.\n\n"
        f"Comfort is not a feature. It is what is left when the friction is gone."
    )

    def version(vid: str, style: str, title: str, body: str, cta: str) -> dict[str, Any]:
        return {
            "id": vid,
            "style": style,
            "title": title,
            "body": body,
            "cta": cta,
            "hashtags": [f"#{keyword.replace(' ', '')}", f"#{brand}", "#review"],
        }

    return {
        "versions": [
            version("V1", "Primary · first-hand review", v1_title, v1_body, "See the full spec"),
            version("V2", "Rational · decision aid", v2_title, v2_body, "Compare the details"),
            version("V3", "Emotional · resonance", v3_title, v3_body, "Try it for a week"),
        ],
        "recommended_version": "V1",
        "headlines": [v1_title, v2_title, v3_title],
        "claims": [
            {"text": benefit_line, "source": "product spec sheet"},
            {"text": support_line, "source": "internal usage log"},
        ],
        "revision_notes": [],
    }


def generate_copy(ctx: dict[str, Any]) -> dict[str, Any]:
    brief = as_brief(ctx)
    strategy = rec(ctx.get("strategy"))
    creative = rec(ctx.get("creative"))
    plan = rec(ctx.get("plan"))
    audience = rec(strategy.get("audience_profile"))
    house = rec(strategy.get("message_house"))
    feedback = as_str_array(ctx.get("feedback"))
    is_revision = len(feedback) > 0
    seed = fnv1a(f"{brief.brand}{brief.product}{brief.channel}{'r' if is_revision else 'v'}{len(feedback)}")
    rule = channel_rule(brief.channel)

    # 兜底值刻意留空：语言相关的默认值在下面按目标语言补齐，
    # 避免「中文默认值漏进英文文案」这类只有多语言场景才暴露的问题
    english = normalize_language(brief.language) == "en"
    pain = (as_str_array(audience.get("pain_points")) or ([] if english else ["选择成本太高"]))
    scenario = (as_str_array(audience.get("scenarios")) or ([] if english else ["日常使用"]))
    benefits = as_str_array(house.get("benefits")) or ([] if english else ["更省心", "更稳定", "更少的试错成本"])
    support = as_str_array(house.get("support_points")) or ([] if english else ["基于实际使用记录"])
    pain = pain[0] if pain else ""
    scenario = scenario[0] if scenario else ""
    keyword = brief.keywords[0] if brief.keywords else brief.product
    headlines = as_str_array(plan.get("headline_candidates"))

    # 非中文 Brief 走本地化分支：产出目标语言的**原生文案**，而不是中文再翻译。
    # 注意这里的兜底值也要按语言取 —— 否则上游缺字段时会把中文默认值漏进英文文案。
    if normalize_language(brief.language) == "en":
        return _english_copy(
            brief,
            pain or "too many options",
            scenario or "a normal weekday morning",
            benefits or ["less time spent comparing", "fewer decisions to re-litigate"],
            support or ["based on real usage records"],
            keyword,
        )

    # 未经历返工时保留风险表达 —— 用于演示「合规门禁真实拦截」。
    flavor_pool = (
        ["说句实在的，它不便宜，但省下的时间对我是划算的。"]
        if is_revision
        else [
            "说句实在的，这可能是同类里最好喝的一个选择。",
            f"用下来我觉得这就是{pain}的最优解。",
            "不夸张地说，这是我今年买得最值的一件。",
        ]
    )

    style_defs = [
        {
            "id": "V1",
            "style": "主推版·真实体验",
            "title": headlines[0] if headlines else f"{pain}？我换了{brief.product}",
            "flavor": pick(flavor_pool, seed),
        },
        {
            "id": "V2",
            "style": "理性版·决策辅助",
            "title": f"把「{keyword}」讲清楚：{brief.product}的取舍",
            "flavor": "我尽量只说能验证的部分，判断留给你。",
        },
        {
            "id": "V3",
            "style": "感性版·情绪共鸣",
            "title": f"{scenario}的那一刻，我终于不用再纠结了",
            "flavor": "有些改变不需要理由，舒服就够了。",
        },
    ]

    versions: list[dict[str, Any]] = []
    for index, style in enumerate(style_defs):
        cta = "如果它也戳到你了，点个收藏慢慢看" if index == 2 else "想试试的话，评论区我放了入口"
        composed = compose_body(
            brief,
            {
                "hook": style["title"],
                "pain": pain,
                "scenario": scenario,
                "benefit": benefits[0],
                "benefits": benefits,
                "support": support,
                "cta": cta,
                "styleFlavor": style["flavor"],
            },
        )
        # 收到事实核查意见后，作者补充来源标注（真实返工中最常见的修改动作）
        body = (
            f"{composed}\n\n数据来源：品牌方提供的产品说明与内部实测记录（样本 32 人）"
            if is_revision
            else composed
        )
        hashtags = build_hashtags(brief)
        full = f"{style['title']}\n\n{body}\n\n{' '.join(hashtags)}"
        versions.append(
            {
                "id": style["id"],
                "style": style["style"],
                "title": style["title"],
                "body": body,
                "cta": cta,
                "hashtags": hashtags,
                "word_count": len(_WHITESPACE_RE.sub("", full)),
            }
        )

    claims = [
        {
            "text": f"{brief.product}面向{brief.audience}，主打「{keyword}」",
            "source": "品牌方提供的产品说明" if is_revision else "",
        },
        {
            "text": f"{brief.audience}在{scenario}场景下的主要困扰是「{pain}」",
            "source": f"行业洞察库（{brief.industry}）" if is_revision else "",
        },
        {
            "text": f"内容形态遵循{brief.channel}「{rule.format}」规范",
            "source": "平台公开规则",
        },
    ]

    return {
        "versions": versions,
        "recommended_version": "V1",
        "headlines": headlines if headlines else [v["title"] for v in versions],
        "claims": claims,
        "channel_checklist": [{"block": block, "covered": True} for block in rule.blocks],
        "revision_notes": (
            [{"from_feedback": item, "action": "已按要求调整"} for item in feedback] if is_revision else []
        ),
        "confidence": 0.88 if is_revision else 0.81,
        "risks": [] if is_revision else ["文案中包含主观评价性表述，可能触发合规与事实核查门禁"],
        "evidence": (
            [{"claim": "所有关键主张均已标注来源", "source": "本轮补充", "reliability": 0.85}]
            if is_revision
            else [
                {
                    "claim": f"沿用创意方向「{as_str(rec(creative.get('big_idea')).get('title'), '—')}」",
                    "source": "A2 创意概念",
                    "reliability": 0.8,
                }
            ]
        )
        + memory_evidence(ctx),
    }


# ------------------------------------------------------------------ #
# 文档研读（DOC.digest）与 A4 分篇                                    #
# ------------------------------------------------------------------ #


def _digest_sentences(text: str) -> list[str]:
    """把文档节选切成可用的句子（去空白、去超短句），供占位研读取材。"""
    parts = [seg.strip() for seg in (text or "").replace("\n", "。").split("。")]
    return [seg for seg in parts if len(seg) >= 8]


def generate_digest_map(ctx: dict[str, Any]) -> dict[str, Any]:
    """单块研读的离线占位：形状与真实 map 输出一致，内容从节选里如实截取。"""
    documents = [rec(item) for item in as_obj_array(ctx.get("documents"))]
    doc = documents[0] if documents else {}
    title = as_str(doc.get("title"), "未命名文档")
    chunk_index = int(as_num(doc.get("chunk_index"), 1))
    chunk_total = int(as_num(doc.get("chunk_total"), 1))
    sentences = _digest_sentences(as_str(doc.get("text")))

    summary = "；".join(sentences[:2])[:200] if sentences else f"{title} 第 {chunk_index}/{chunk_total} 块（无可读正文）"
    key_facts = [f"{title}｜{seg[:60]}" for seg in sentences[:15]] or [f"{title}｜本块未提取到明确事实"]
    quotes = [seg[:80] for seg in sentences[2:10]] or [summary[:80]]
    style_notes = [f"节选 {chunk_index}/{chunk_total}：以原文摘录为主，未附加外部解读"]

    return {
        "title": title,
        "chunk_index": chunk_index,
        "chunk_total": chunk_total,
        "summary": summary,
        "key_facts": key_facts,
        "quotes": quotes,
        "style_notes": style_notes,
    }


def generate_digest_reduce(ctx: dict[str, Any]) -> dict[str, Any]:
    """研读汇总的离线占位：按文档聚合 map 结果，不新增事实。"""
    maps = [rec(item) for item in as_obj_array(ctx.get("maps"))]
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in maps:
        grouped.setdefault(as_str(item.get("title"), "未命名文档"), []).append(item)

    documents: list[dict[str, Any]] = []
    for title, items in grouped.items():
        facts: list[str] = []
        quotes: list[str] = []
        for item in items:
            for fact in as_str_array(item.get("key_facts")):
                if fact not in facts:
                    facts.append(fact)
            for quote in as_str_array(item.get("quotes")):
                if quote not in quotes:
                    quotes.append(quote)
        summaries = [as_str(item.get("summary")) for item in items if as_str(item.get("summary"))]
        documents.append(
            {
                "title": title,
                "summary": "；".join(summaries)[:400],
                "key_facts": facts[:40],
                "quotes": quotes[:10],
                "style_notes": as_str_array(items[0].get("style_notes"))[:5],
            }
        )

    brief_line = "；".join(f"{doc['title']}：{doc['summary'][:80]}" for doc in documents)
    creative_brief = (
        f"本次任务附带 {len(documents)} 份素材文档，研读要点如下，供选题与创作参考：{brief_line}"
        if documents
        else "（本次任务没有可研读的文档素材）"
    )
    return {"documents": documents, "creative_brief": creative_brief[:5000]}


def generate_copy_version(ctx: dict[str, Any]) -> dict[str, Any]:
    """A4 分篇生成的离线占位：复用整版生成器，按 version_style 取出单版本。"""
    full = generate_copy(ctx)
    wanted = as_str(ctx.get("version_style"), "V1")
    versions = [rec(item) for item in as_obj_array(full.get("versions"))]
    version = next((item for item in versions if as_str(item.get("id")) == wanted), versions[0] if versions else {})
    return {
        "version": version,
        "claims": [rec(item) for item in as_obj_array(full.get("claims"))],
        "revision_notes": [rec(item) for item in as_obj_array(full.get("revision_notes"))],
        "confidence": as_num(full.get("confidence"), 0.81),
        "risks": as_str_array(full.get("risks")),
        "evidence": [rec(item) for item in as_obj_array(full.get("evidence"))],
    }


# ------------------------------------------------------------------ #
# A5 编辑审校                                                         #
# ------------------------------------------------------------------ #


def _english_clip(text: str, limit: int = 80) -> str:
    """按词边界截断英文（中文 ``shorten`` 按字符截，会把英文截成半词）。"""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    space = cut.rfind(" ")
    return cut[:space] if space > 0 else cut


def _english_edit(brief: Brief, ctx: dict[str, Any]) -> dict[str, Any]:
    """英文审校（A5）：结构同中文分支，评审意见/修改建议全部英文原生。

    中文分支的长句口径（60 字符 + 。！？切分）、口语词正则与标题上限都是中文
    语料导向，直接复用会把中文评审意见漏进英文产物；标题上限换用语言画像的
    词数口径（``title_limit_for``），长句按词数判定。
    """
    draft = rec(ctx.get("draft"))
    versions = as_obj_array(draft.get("versions"))
    recommended = as_str(draft.get("recommended_version"), "V1")
    target = next((v for v in versions if as_str(v.get("id")) == recommended), None) or (
        versions[0] if versions else {}
    )
    title = as_str(target.get("title"))
    body = as_str(target.get("body"))
    hashtags = as_str_array(target.get("hashtags"))
    plain = _WHITESPACE_RE.sub("", f"{title}{body}")
    paragraphs = [p for p in _PARAGRAPH_SPLIT_RE.split(body) if p.strip()]

    word_limit = title_limit_for(brief.channel, brief.language)
    title_words = len([w for w in title.split() if w])

    issues: list[dict[str, Any]] = []
    change_log: list[dict[str, str]] = []

    structure = 90
    clarity = 88
    brand_voice = 86
    appeal = 84

    if title_words > word_limit:
        issues.append(
            {
                "severity": "major",
                "category": "Title",
                "detail": f"Title runs {title_words} words, over the {brief.channel}"
                f" recommended limit ({word_limit} words); feeds will truncate it",
                "suggestion": f"Compress to {word_limit} words or fewer, keeping the"
                " identity anchor and the emotional hook",
                "location": "Title",
            }
        )
        structure -= 12
        appeal -= 8
    if len(paragraphs) < 5:
        issues.append(
            {
                "severity": "minor",
                "category": "Structure",
                "detail": f"Only {len(paragraphs)} paragraphs; mobile reading fatigue goes up",
                "suggestion": "Split into hook / empathy / solution / evidence / action",
                "location": "Body",
            }
        )
        structure -= 10
    if brief.brand not in body:
        issues.append(
            {
                "severity": "major",
                "category": "Brand",
                "detail": "The brand name never appears in the body, so brand association cannot form",
                "suggestion": f"Mention {brief.brand} once naturally in the solution section",
                "location": "Body",
            }
        )
        brand_voice -= 15
    if not hashtags:
        issues.append(
            {
                "severity": "minor",
                "category": "Channel fit",
                "detail": "No hashtags, losing search and recommendation traffic",
                "suggestion": "Add 3-5 hashtags: 2 broad + 2 niche + 1 long-tail",
                "location": "Footer",
            }
        )
        appeal -= 6

    # 英文长句按「词数 > 40」判定（中文按 60 字符），避免把整段正文当成一个长句
    sentences = [s for s in _EN_SENTENCE_SPLIT_RE.split(body) if s.strip()]
    long_sentence = next((s for s in sentences if len(s.split()) > 40), None)
    if long_sentence:
        change_log.append(
            {
                "type": "Sentence split",
                "detail": "Split the 40+ word sentence into shorter ones for readability",
                "before": shorten(long_sentence.strip(), 80),
                "after": f"{_english_clip(long_sentence)}.",
            }
        )
        clarity += 4
    if not _EN_CONTRACTION_RE.search(body):
        # 无缩写词则提示补口语连接词，模拟编辑对语气的建议
        change_log.append(
            {
                "type": "Tone pass",
                "detail": f"Add conversational connectors to match the '{brief.tone}' voice",
                "before": "(original)",
                "after": "(add transitions like 'honestly' or 'here is the thing')",
            }
        )
        brand_voice += 3

    revised_body = (
        body.replace(long_sentence, f"{_english_clip(long_sentence)}.", 1)
        if long_sentence
        else body
    )

    revised = {
        "title": _compress_title_words(title, word_limit) if title_words > word_limit else title,
        "body": revised_body,
        "cta": as_str(target.get("cta")),
        "hashtags": hashtags,
    }

    def score(value: float) -> int:
        return max(40, min(98, js_round(value)))

    scorecard = {
        "structure": score(structure),
        "clarity": score(clarity),
        "brand_voice": score(brand_voice),
        "appeal": score(appeal),
    }
    overall = js_round(
        (scorecard["structure"] + scorecard["clarity"] + scorecard["brand_voice"] + scorecard["appeal"]) / 4
    )

    blockers = [i for i in issues if i["severity"] in ("blocker", "major")]
    return {
        "revised": revised,
        "change_log": change_log,
        "scorecard": {**scorecard, "overall": overall},
        "issues": issues,
        "verdict": "revise" if blockers else "pass",
        "verdict_reason": (
            f"{len(blockers)} issues need revision ({', '.join(b['category'] for b in blockers)})"
            if blockers
            else "Structure, clarity, and brand voice all meet the publish bar"
        ),
        "read_metrics": {
            "char_count": len(plain),
            "paragraph_count": len(paragraphs),
            "avg_sentence_length": js_round(len(plain) / max(1, len(sentences))),
        },
        "confidence": 0.86,
        "risks": [],
        "evidence": [
            {
                "claim": f"Reviewed against the {brief.channel} native post format",
                "source": "Platform content guidelines",
                "reliability": 0.85,
            }
        ],
    }


def generate_edit(ctx: dict[str, Any]) -> dict[str, Any]:
    brief = as_brief(ctx)
    if normalize_language(brief.language) == "en":
        return _english_edit(brief, ctx)
    draft = rec(ctx.get("draft"))
    versions = as_obj_array(draft.get("versions"))
    recommended = as_str(draft.get("recommended_version"), "V1")
    target = next((v for v in versions if as_str(v.get("id")) == recommended), None) or (
        versions[0] if versions else {}
    )
    rule = channel_rule(brief.channel)

    title = as_str(target.get("title"))
    body = as_str(target.get("body"))
    hashtags = as_str_array(target.get("hashtags"))
    plain = _WHITESPACE_RE.sub("", f"{title}{body}")
    paragraphs = [p for p in _PARAGRAPH_SPLIT_RE.split(body) if p.strip()]

    # --- 真实可测量的审校信号 ---
    issues: list[dict[str, Any]] = []
    change_log: list[dict[str, str]] = []

    structure = 90
    clarity = 88
    brand_voice = 86
    appeal = 84

    if len(title) > 20 and "小红书" in brief.channel:
        issues.append(
            {
                "severity": "major",
                "category": "标题",
                "detail": f"标题 {len(title)} 字，超出{brief.channel}推荐长度（≤20 字），信息流易被截断",
                "suggestion": "压缩至 20 字以内，保留身份锚点与情绪词",
                "location": "标题",
            }
        )
        structure -= 12
        appeal -= 8
    if len(paragraphs) < 5:
        issues.append(
            {
                "severity": "minor",
                "category": "结构",
                "detail": f"段落数偏少（{len(paragraphs)} 段），移动端阅读疲劳度上升",
                "suggestion": "按「钩子 / 共鸣 / 方案 / 证据 / 行动」拆分段落",
                "location": "正文",
            }
        )
        structure -= 10
    if brief.brand not in body:
        issues.append(
            {
                "severity": "major",
                "category": "品牌",
                "detail": "正文未出现品牌名，品牌联想无法建立",
                "suggestion": "在方案段自然植入品牌名一次",
                "location": "正文",
            }
        )
        brand_voice -= 15
    if not hashtags:
        issues.append(
            {
                "severity": "minor",
                "category": "渠道适配",
                "detail": "缺少话题标签，损失搜索与推荐流量",
                "suggestion": f"补充 {rule.hashtag_policy}",
                "location": "文末",
            }
        )
        appeal -= 6

    long_sentence = next((s for s in _LONG_SENTENCE_SPLIT_RE.split(body) if len(s) > 60), None)
    if long_sentence:
        change_log.append(
            {
                "type": "长句拆分",
                "detail": "将 60 字以上长句拆为短句，提升可读性",
                "before": shorten(long_sentence, 50),
                "after": f"{long_sentence[:40]}。",
            }
        )
        clarity += 4
    if not _COLLOQUIAL_RE.search(body):
        # 无口语词则提示补口语，模拟编辑对语气的建议
        change_log.append(
            {
                "type": "语气调整",
                "detail": f"补充口语化连接词，贴合「{brief.tone}」调性",
                "before": "（原句）",
                "after": "（加入「其实」「说句实在的」等过渡）",
            }
        )
        brand_voice += 3

    revised_body = (
        body.replace(long_sentence, f"{long_sentence[:40]}。", 1) if long_sentence else body
    )

    revised = {
        "title": shorten(title, 20) if len(title) > 20 and "小红书" in brief.channel else title,
        "body": revised_body,
        "cta": as_str(target.get("cta")),
        "hashtags": hashtags,
    }

    def score(value: float) -> int:
        return max(40, min(98, js_round(value)))

    scorecard = {
        "structure": score(structure),
        "clarity": score(clarity),
        "brand_voice": score(brand_voice),
        "appeal": score(appeal),
    }
    overall = js_round(
        (scorecard["structure"] + scorecard["clarity"] + scorecard["brand_voice"] + scorecard["appeal"]) / 4
    )

    blockers = [i for i in issues if i["severity"] in ("blocker", "major")]
    sentence_parts = _SENTENCE_END_RE.split(body)

    return {
        "revised": revised,
        "change_log": change_log,
        "scorecard": {**scorecard, "overall": overall},
        "issues": issues,
        "verdict": "revise" if blockers else "pass",
        "verdict_reason": (
            f"存在 {len(blockers)} 项需修订问题（{'、'.join(b['category'] for b in blockers)}）"
            if blockers
            else "结构、表达、品牌语气均达到放行标准"
        ),
        "read_metrics": {
            "char_count": len(plain),
            "paragraph_count": len(paragraphs),
            "avg_sentence_length": js_round(len(plain) / max(1, len(sentence_parts))),
        },
        "confidence": 0.86,
        "risks": [],
        "evidence": [
            {
                "claim": f"按{brief.channel}形态规范「{rule.format}」评审",
                "source": "平台运营规范",
                "reliability": 0.85,
            }
        ],
    }


# ------------------------------------------------------------------ #
# A6 事实核查                                                         #
# ------------------------------------------------------------------ #

_NUMERIC_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\d+(\.\d+)?%"), "百分比数据"),
    (re.compile(r"\d+\s*倍"), "倍数断言"),
    (re.compile(r"\d+\s*天"), "时间承诺"),
    (re.compile(r"\d+\s*人"), "样本量"),
]
_SOURCE_MARKER_RE = re.compile(r"(来源[:：]|数据来源|据.+统计|实测|样本|参考文献)")
_ABSOLUTE_PATTERNS = ["最好", "最优", "最值", "第一", "唯一", "绝对", "保证", "永久"]


def generate_fact_check(ctx: dict[str, Any]) -> dict[str, Any]:
    brief = as_brief(ctx)
    revision = int(as_num(ctx.get("revision"), 0))
    draft = rec(ctx.get("draft"))
    versions = as_obj_array(draft.get("versions"))
    recommended = as_str(draft.get("recommended_version"), "V1")
    target = next((v for v in versions if as_str(v.get("id")) == recommended), None) or (
        versions[0] if versions else {}
    )
    body = f"{as_str(target.get('title'))}\n{as_str(target.get('body'))}"

    claims = as_obj_array(draft.get("claims"))
    checks: list[dict[str, Any]] = []

    for claim in claims:
        source = as_str(claim.get("source"))
        checks.append(
            {
                "claim": as_str(claim.get("text")),
                "status": "verified" if source else "unverified",
                "source": source or "未提供",
                "note": "来源可追溯，表述与来源一致"
                if source
                else "主张未标注来源，无法核验，需补充或改为安全表达",
                "confidence": 0.85 if source else 0.6,
            }
        )

    # 数值型断言检测：数字最容易失实，必须带来源
    has_source_marker = bool(_SOURCE_MARKER_RE.search(body))
    for pattern, label in _NUMERIC_PATTERNS:
        found = [m.group(0) for m in pattern.finditer(body)]
        if found and not has_source_marker:
            checks.append(
                {
                    "claim": f"{label}「{'、'.join(found[:2])}」",
                    "status": "unverified",
                    "source": "未提供",
                    "note": "正文出现量化表述但未见来源标注，属于高风险项",
                    "confidence": 0.55,
                }
            )

    # 绝对化/效果承诺类断言 → 交由合规智能体处理，但事实侧同步标记
    for word in _ABSOLUTE_PATTERNS:
        if word in body:
            checks.append(
                {
                    "claim": f"主观绝对化表述「{word}」",
                    "status": "exaggerated",
                    "source": "—",
                    "note": "无法被证实的主观断言，建议改为可验证的限定描述",
                    "confidence": 0.7,
                }
            )
            break

    unverified = [c for c in checks if c["status"] == "unverified"]
    exaggerated = [c for c in checks if c["status"] == "exaggerated"]
    blockers = len(unverified) + len(exaggerated)
    risk_level = "high" if blockers >= 2 else ("medium" if blockers == 1 else "low")

    return {
        "checks": checks,
        "risk_level": risk_level,
        "verdict": "revise" if blockers > 0 else "pass",
        "verdict_reason": (
            f"发现 {len(unverified)} 项无来源主张、{len(exaggerated)} 项主观绝对化表述"
            if blockers > 0
            else "所有关键主张均可追溯来源，未发现夸大或矛盾信息"
        ),
        "required_fixes": [
            f"为「{shorten(c['claim'], 30)}」补充可核验来源，或删除该表述" for c in unverified
        ]
        + [f"将「{shorten(c['claim'], 30)}」改为可验证的限定描述" for c in exaggerated],
        "safe_rewrites": [
            {
                "original": c["claim"],
                "rewrite": "改为「基于我们实际使用与公开资料的描述」，并去掉绝对化限定",
                "reason": "避免无法证实的最高级表述",
            }
            for c in exaggerated
        ],
        "verification_scope": [
            f"核查对象：{brief.channel} 主推版本（{as_str(target.get('id'), 'V1')}）",
            f"核查项：{len(checks)} 项",
            "核查方式：来源标注检查 + 数值断言扫描 + 绝对化表述识别",
        ],
        "confidence": 0.84,
        "risks": (
            ["存在多项无来源量化表述，若直接发布存在误导风险，已行使否决权"]
            if risk_level == "high"
            else []
        ),
        "needs_human_review": risk_level == "high" and revision > 0,
        "evidence": [
            {"claim": c["claim"], "source": c["source"], "reliability": 0.85}
            for c in checks
            if c["status"] == "verified"
        ],
    }


# ------------------------------------------------------------------ #
# A7 品牌合规                                                         #
# ------------------------------------------------------------------ #


def generate_compliance(ctx: dict[str, Any]) -> dict[str, Any]:
    brief = as_brief(ctx)
    revision = int(as_num(ctx.get("revision"), 0))
    draft = rec(ctx.get("draft"))
    versions = as_obj_array(draft.get("versions"))
    recommended = as_str(draft.get("recommended_version"), "V1")
    target = next((v for v in versions if as_str(v.get("id")) == recommended), None) or (
        versions[0] if versions else {}
    )
    title = as_str(target.get("title"))
    body = as_str(target.get("body"))
    hashtags = " ".join(as_str_array(target.get("hashtags")))
    full_text = f"{title}\n{body}\n{hashtags}"

    # --- 真实词库扫描 + 品牌语气检查 ---
    scan = scan_compliance(full_text, brief.industry)
    voice = check_brand_voice(full_text, brief.tone)
    rewrite = auto_rewrite(full_text, scan.hits)

    blockers = [h for h in scan.hits if h.severity == "blocker"]
    majors = [h for h in scan.hits if h.severity == "major"]
    minors = [h for h in scan.hits if h.severity == "minor"]

    items = (
        [
            {
                "severity": hit.severity,
                "category": hit.category,
                "term": hit.term,
                "detail": f"{hit.term} —— {hit.snippet}",
                "suggestion": hit.fix,
                "law": hit.law,
            }
            for hit in scan.hits
        ]
        + [
            {
                "severity": fail.severity,
                "category": fail.category,
                "term": "",
                "detail": fail.hint,
                "suggestion": fail.fix,
                "law": fail.law,
            }
            for fail in scan.required_failures
        ]
        + [
            {
                "severity": issue.severity,
                "category": issue.type,
                "term": "",
                "detail": issue.detail,
                "suggestion": issue.suggestion,
                "law": "—",
            }
            for issue in voice.issues
        ]
    )

    voice_score = float(voice.score)
    penalty = (
        len(blockers) * 25
        + len(majors) * 10
        + len(minors) * 4
        + len(scan.required_failures) * 8
    )
    score = max(0, 100 - penalty)
    risk_level = scan.risk_level
    verdict = (
        "revise"
        if (blockers or scan.risk_level == "high")
        else ("revise" if (majors or scan.required_failures or voice_score < 70) else "pass")
    )

    required_fixes = (
        [f"【必改】删除或替换「{h.term}」（{h.category}）：{h.fix}" for h in blockers]
        + [f"【强烈建议】「{h.term}」（{h.category}）：{h.fix}" for h in majors]
        + [f"【必备要素缺失】{f.hint}" for f in scan.required_failures]
        + [f"【建议】「{h.term}」：{h.fix}" for h in minors[:2]]
    )

    return {
        "hits": items,
        "summary": {
            "blocker": len(blockers),
            "major": len(majors),
            "minor": len(minors),
            "missing_required": len(scan.required_failures),
        },
        "brand_consistency": {
            "score": js_round((voice_score / 20) * 10) / 10,
            "issues": [
                {"type": i.type, "detail": i.detail, "suggestion": i.suggestion}
                for i in voice.issues
            ],
            "terminology_ok": True,
            "disclaimer_ok": not any("风险提示" in f.category for f in scan.required_failures),
        },
        "risk_level": risk_level,
        "verdict": verdict,
        "verdict_reason": (
            "未发现违反广告法与品牌规范的表述，可进入发布审批"
            if verdict == "pass"
            else f"命中 {len(blockers)} 项阻断项、{len(majors)} 项重要项，禁止直接发布"
        ),
        "required_fixes": required_fixes,
        "safe_rewrites": rewrite.applied,
        "auto_fixed_text": rewrite.text,
        "checked_against": [
            "《广告法》第九条（绝对化用语）",
            "《广告法》第十七条（医疗功效）",
            "《广告法》第二十五条（收益承诺）",
            *([f"行业专项规则：{brief.industry}"] if brief.industry else []),
            f"品牌调性：{brief.tone}",
        ],
        "compliance_score": score,
        "confidence": 0.92,
        "needs_human_review": risk_level == "high" and revision > 0,
        "risks": (
            [f"存在阻断级合规风险：{'、'.join(b.term for b in blockers)}"] if blockers else []
        ),
        "evidence": [
            {"claim": f"「{h.term}」违反{h.category}", "source": h.law, "reliability": 0.95}
            for h in scan.hits[:5]
        ],
    }


# ------------------------------------------------------------------ #
# A10 效果预估与复盘                                                  #
# ------------------------------------------------------------------ #


def _english_analysis(brief: Brief, ctx: dict[str, Any]) -> dict[str, Any]:
    """英文效果预估（A10）：区间数字与中文分支同口径，表述全部英文。"""
    strategy = rec(ctx.get("strategy"))
    audience = rec(strategy.get("audience_profile"))
    objectives = as_obj_array(strategy.get("objectives"))
    draft = rec(ctx.get("draft"))
    seed = fnv1a(f"{brief.brand}{brief.channel}{brief.objective}")
    # Brief 的 objective 允许中文（如「转化」）；英文产物里映射为英文词，避免夹中文
    objective = _english_objective(
        as_str(objectives[0].get("type") if objectives else None, brief.objective)
    )
    pains = as_str_array(audience.get("pain_points"))
    secondary_pain = pains[1] if len(pains) > 1 else "the next-level pain point"
    versions = as_obj_array(draft.get("versions"))
    headlines = [as_str(v.get("title")) for v in versions if as_str(v.get("title"))]
    primary_headline = headlines[0] if headlines else f"{brief.product} review"
    content_format = _english_format(brief.channel)

    def band(mid: float, spread: float, unit: str) -> dict[str, Any]:
        return {
            "low": js_round(mid - spread),
            "mid": js_round(mid),
            "high": js_round(mid + spread),
            "unit": unit,
        }

    return {
        "mode": "pre_publish_estimate",
        "predicted": {
            "exposure": band(10_000 + (seed % 8) * 5_000, 6_000, "views"),
            "ctr": band(4 + (seed % 4), 1.5, "%"),
            "engagement": band(3 + (seed % 3), 1.2, "%"),
            "conversion": band(1 + (seed % 2), 0.6, "%"),
            "basis": (
                f"Range based on the account's history with '{content_format}' content; "
                "not a promise"
            ),
        },
        "objective_alignment": {
            "objective": objective,
            "score": 78 + (seed % 15),
            "note": f"Structure aligns with the '{objective}' goal and the {brief.channel} format",
        },
        "attribution": [
            {"factor": "Hook strength of the title", "impact": "high", "note": "The title drives most first-screen click decisions"},
            {"factor": "Information density in the first 3 seconds", "impact": "high", "note": "Strongly correlated with read-through"},
            {"factor": "Hashtag coverage", "impact": "medium", "note": "Keep 5+ relevant tags; avoid tag stuffing"},
            {"factor": "Publishing time", "impact": "medium", "note": f"Align with when {brief.audience} are most active"},
        ],
        "optimizations": [
            {
                "priority": "high",
                "action": "A/B test the title: keep two directions live for 24 hours each",
                "expected_gain": "CTR +15%~30%",
                "effort": "low",
            },
            {
                "priority": "high",
                "action": "Pre-seed official answers to frequent questions in the comments",
                "expected_gain": "Engagement +10%",
                "effort": "low",
            },
            {
                "priority": "medium",
                "action": "Recut the long-form into a 45-second short video on the same topic",
                "expected_gain": "Reach +40%",
                "effort": "medium",
            },
            {
                "priority": "low",
                "action": "Archive reusable material into the brand asset library",
                "expected_gain": "Next production cost -30%",
                "effort": "low",
            },
        ],
        "ab_tests": [
            {
                "hypothesis": "Search-oriented titles beat emotion-led titles",
                "variant_a": f"{brief.keywords[0] if brief.keywords else brief.industry} | {primary_headline}",
                "variant_b": primary_headline,
                "metric": "Search-driven share / CTR",
            },
            {
                "hypothesis": "A concrete number on the cover improves save rate",
                "variant_a": "Cover shows the number",
                "variant_b": "Pure scene cover",
                "metric": "Save rate",
            },
        ],
        "next_brief_suggestions": [
            f"Harvest frequent comment questions into the next topic (for {brief.audience})",
            f"Test content around '{secondary_pain}'",
            "If the first post overperforms, follow up within 48 hours to cluster the topic",
        ],
        "cautions": [
            "These are range estimates, not effect promises; actual results depend heavily on platform traffic allocation"
        ],
        "confidence": 0.66,
        "risks": [
            "Estimates come from historical ranges with a limited sample",
            "Platform algorithm changes can significantly shift actual exposure",
        ],
        "evidence": [
            {
                "claim": f"The content format is '{content_format}'",
                "source": "platform public rules",
                "reliability": 0.85,
            },
            {
                "claim": "Titles have the largest impact on first-screen clicks",
                "source": "content operations consensus",
                "reliability": 0.7,
            },
        ],
    }


def generate_analysis(ctx: dict[str, Any]) -> dict[str, Any]:
    brief = as_brief(ctx)
    # 非中文 Brief：渠道规则与话术模板是中文语料，改走英文模板
    if normalize_language(brief.language) == "en":
        return _english_analysis(brief, ctx)
    strategy = rec(ctx.get("strategy"))
    audience = rec(strategy.get("audience_profile"))
    objectives = as_obj_array(strategy.get("objectives"))
    seed = fnv1a(f"{brief.brand}{brief.channel}{brief.objective}")
    rule = channel_rule(brief.channel)
    objective = as_str(objectives[0].get("type") if objectives else None, brief.objective)
    pains = as_str_array(audience.get("pain_points"))
    secondary_pain = pains[1] if len(pains) > 1 else "次级痛点"

    def band(mid: float, spread: float, unit: str) -> dict[str, Any]:
        return {
            "low": js_round(mid - spread),
            "mid": js_round(mid),
            "high": js_round(mid + spread),
            "unit": unit,
        }

    return {
        "mode": "pre_publish_estimate",
        "predicted": {
            "exposure": band(10_000 + (seed % 8) * 5_000, 6_000, "次"),
            "ctr": band(4 + (seed % 4), 1.5, "%"),
            "engagement": band(3 + (seed % 3), 1.2, "%"),
            "conversion": band(1 + (seed % 2), 0.6, "%"),
            "basis": f"参照账号历史同形态内容（{rule.format}）的区间表现，非承诺值",
        },
        "objective_alignment": {
            "objective": objective,
            "score": 78 + (seed % 15),
            "note": f"内容结构与「{objective}」目标一致，{brief.channel}形态匹配",
        },
        "attribution": [
            {"factor": "标题钩子强度", "impact": "high", "note": "标题决定 70% 以上的首屏点击决策"},
            {"factor": "开头 3 秒信息密度", "impact": "high", "note": "与完读率强相关"},
            {"factor": "话题标签覆盖", "impact": "medium", "note": f"建议按「{rule.hashtag_policy}」布局"},
            {"factor": "发布时段", "impact": "medium", "note": f"对齐{brief.audience}活跃高峰"},
        ],
        "optimizations": [
            {
                "priority": "high",
                "action": "标题做 A/B 测试，保留 2 个方向各投放 24 小时",
                "expected_gain": "CTR +15%~30%",
                "effort": "低",
            },
            {
                "priority": "high",
                "action": "评论区预置高频疑问的官方回答，提升正向评论占比",
                "expected_gain": "互动率 +10%",
                "effort": "低",
            },
            {
                "priority": "medium",
                "action": "把长文二次剪辑为 45 秒短视频，复用同一选题",
                "expected_gain": "触达人群 +40%",
                "effort": "中",
            },
            {
                "priority": "low",
                "action": "沉淀本次可复用素材至品牌资产库",
                "expected_gain": "下次制作成本 -30%",
                "effort": "低",
            },
        ],
        "ab_tests": [
            {
                "hypothesis": "身份锚点型标题优于痛点型标题",
                "variant_a": "早八人救命！办公室 30 秒喝上冷萃",
                "variant_b": "起不来又想喝好的？我找到办法了",
                "metric": "CTR",
            },
            {
                "hypothesis": "带具体数字的封面提升收藏率",
                "variant_a": "封面含「30 秒」数字",
                "variant_b": "封面纯场景图",
                "metric": "收藏率",
            },
        ],
        "next_brief_suggestions": [
            f"将评论区高频问题沉淀为下一条选题（面向{brief.audience}）",
            f"补充「{secondary_pain}」相关的内容测试",
            "若首条表现超预期，48 小时内追加同方向内容形成话题聚合",
        ],
        "cautions": ["以上为区间预估，不构成效果承诺；实际表现受平台流量分配影响显著"],
        "confidence": 0.66,
        "risks": ["预估基于历史区间数据，样本有限", "平台算法调整可能显著影响实际曝光"],
        "evidence": [
            {"claim": f"内容形态为「{rule.format}」", "source": "平台公开规则", "reliability": 0.85},
            {"claim": "标题对首屏点击影响最大", "source": "内容运营通用共识", "reliability": 0.7},
        ],
    }


def generate_analysis_review(ctx: dict[str, Any]) -> dict[str, Any]:
    """发布后复盘：真实数据 vs 发布前预估（A/B 闭环回填，plan.md v2.0）。

    与 ``generate_analysis`` 的区别：那个是**发布前预估**，这个拿运营回填的
    真实曝光 / 点击 / 互动 / 转化做**归因与结论**，并给出可放量判断。
    """
    brief = as_brief(ctx)
    rule = channel_rule(brief.channel)
    actuals = rec(ctx.get("actuals"))
    predicted = rec(rec(ctx.get("predicted")).get("predicted"))

    exposure = int(as_num(actuals.get("exposure"), 0))
    clicks = int(as_num(actuals.get("clicks"), 0))
    interactions = int(as_num(actuals.get("interactions"), 0))
    conversions = int(as_num(actuals.get("conversions"), 0))
    window = as_str(actuals.get("window"), "发布后 72 小时")

    ctr = round((clicks / exposure * 100) if exposure else 0.0, 2)
    engagement = round((interactions / exposure * 100) if exposure else 0.0, 2)
    conversion = round((conversions / clicks * 100) if clicks else 0.0, 2)

    pred_ctr = rec(predicted.get("ctr"))
    pred_mid = as_num(pred_ctr.get("mid"), 0)
    delta = round(ctr - pred_mid, 2) if pred_mid else 0.0
    if not pred_mid:
        verdict = "缺少预估基线"
    elif delta > 0.5:
        verdict = "超预期"
    elif delta >= -0.5:
        verdict = "符合预期"
    else:
        verdict = "低于预期"

    return {
        "mode": "post_publish_review",
        "window": window,
        "channel": brief.channel,
        "actuals": {
            "exposure": exposure,
            "clicks": clicks,
            "interactions": interactions,
            "conversions": conversions,
            "ctr": ctr,
            "engagement": engagement,
            "conversion": conversion,
        },
        "predicted_comparison": {
            "metric": "ctr",
            "predicted_mid": pred_mid,
            "predicted_low": as_num(pred_ctr.get("low"), 0),
            "predicted_high": as_num(pred_ctr.get("high"), 0),
            "actual": ctr,
            "delta": delta,
            "verdict": verdict,
            "note": (
                f"预估 CTR 区间 {as_num(pred_ctr.get('low'), 0)}~{as_num(pred_ctr.get('high'), 0)}%，"
                f"实际 {ctr}%（{clicks}/{exposure}）"
                if pred_mid
                else f"实际 CTR {ctr}%（{clicks}/{exposure}），无发布前预估可对照"
            ),
        },
        "attribution": [
            {
                "factor": "标题钩子",
                "impact": "high" if abs(delta) >= 0.5 else "medium",
                "note": (
                    f"实际 CTR {ctr}%，比预估中位{delta:+.2f} 个百分点，"
                    + (
                        "标题与目标人群的匹配度强于预期，该方向可沉淀为模板"
                        if delta >= 0
                        else "标题与受众痛点仍有偏差，建议重做钩子方向"
                    )
                ),
            },
            {
                "factor": "互动结构",
                "impact": "high" if engagement >= 3 else "medium",
                "note": (
                    f"互动率 {engagement}%（{interactions} 次），"
                    + ("评论区可作为下一条选题的来源" if engagement >= 3 else "建议预置引导评论提升互动")
                ),
            },
            {
                "factor": "发布时段",
                "impact": "medium",
                "note": f"{window}内累计曝光 {exposure} 次，未出现明显流量断档",
            },
            {
                "factor": "转化路径",
                "impact": "medium",
                "note": f"转化率 {conversion}%（{conversions} 次），转化承接主要取决于落地页，内容侧已给到行动引导",
            },
        ],
        "ab_conclusion": {
            "hypothesis": f"{rule.format} 的标题钩子方向",
            "winner": "A" if delta >= 0 else "B",
            "confidence": 0.6 if abs(delta) < 1 else 0.75,
            "note": (
                f"实际 CTR {ctr}% vs 预估中位 {pred_mid}%，"
                + (
                    "当前方向成立，可继续沿用并做小幅迭代"
                    if delta >= 0
                    else "当前方向未达预估，建议按对照组方案重做一版"
                )
                if pred_mid
                else f"实际 CTR {ctr}%，建议先补一组对照再下结论"
            ),
            "ready_to_scale": bool(abs(delta) >= 0.5 and engagement >= 2),
        },
        "optimizations": [
            {
                "priority": "high",
                "action": "把本条的标题钩子沉淀为可复用模板",
                "expected_gain": f"同类内容 CTR 稳定在 {ctr}% 附近",
                "effort": "低",
            },
            {
                "priority": "high" if engagement < 3 else "medium",
                "action": "把评论区高频疑问转为下一条选题",
                "expected_gain": "互动率 +10%~20%",
                "effort": "低",
            },
            {
                "priority": "medium",
                "action": "对表现最好的渠道追加同方向内容，形成话题聚合",
                "expected_gain": "自然流量叠加 +20%",
                "effort": "中",
            },
        ],
        "next_brief_suggestions": [
            f"围绕「{as_str(brief.keywords[0] if brief.keywords else brief.industry)}」做同主题延伸，复用本次有效钩子",
            f"{window}的数据已可判定方向，建议对最优渠道追加投放预算",
            "下一轮补齐对照组：同一内容两个标题方向各半量投放，用数据决策",
        ],
        "cautions": [
            "复盘结论基于已回填的投放窗口数据，样本量有限，不代表长期规律",
            "平台算法与流量分配变化可能使结论失效，建议 7 天后复核一次",
        ],
        "confidence": 0.78,
        "risks": [
            "回填数据由人工录入，存在口径不一致的风险",
            "单次投放样本不足以支撑强因果结论",
        ],
        "evidence": [
            {
                "claim": f"实际 CTR {ctr}%（{clicks}/{exposure}）",
                "source": "运营回填的投放数据",
                "reliability": 0.9,
            },
            {
                "claim": f"发布前预估中位 {pred_mid}%",
                "source": "A10 发布前预估",
                "reliability": 0.6,
            },
        ],
    }


# ------------------------------------------------------------------ #
# A8 视觉美术指导                                                     #
# ------------------------------------------------------------------ #

#: 配图场景模板：(用途, 画面描述模板)
_VISUAL_SCENES: list[tuple[str, str]] = [
    ("封面主图", "「{product}」与使用场景同框，主体置于画面左三分之一，右侧预留标题安全区"),
    ("痛点场景", "还原「{pain}」的真实瞬间，保留环境细节，不摆拍、不过度修图"),
    ("产品特写", "「{product}」的材质与包装细节，微距呈现，突出工艺而非特效"),
    ("使用过程", "手部动作与产品的交互中景，体现「{benefit}」的发生过程"),
    ("证据画面", "可核验素材展示：配料表 / 检测报告 / 实测记录，保证画面内文字可读"),
    ("使用前后", "同机位、同光线的对照画面，不做夸张修饰"),
    ("收尾定格", "品牌标识 + 行动引导，画面下方预留字幕安全区"),
]

#: 需要时间轴分镜的渠道（动态内容）。
_STORYBOARD_CHANNELS = ("抖音",)

#: 可验证类表述：出现时应安排证据画面，否则图文口径不一致。
_CLAIM_HINTS = ("秒", "分钟", "倍", "%", "0 糖", "无糖", "不含", "低卡", "零添加")

#: 效果承诺类表述：与「使用前后」画面同时出现时存在合规风险。
_EFFECT_HINTS = ("改善", "变好", "见效", "瘦", "修复", "治愈", "根治")


# 英文分支的视觉风格（中文 VISUAL_STYLES 的 name/mood/palette 是中文语料，
# prompt_fragments 本身是英文 SDXL 语句，可直接复用）
_ENGLISH_VISUAL_STYLE: dict[str, str] = {
    "style": "Clean documentary lifestyle",
    "mood": "honest, everyday, understated",
    "composition": "one subject per frame, generous negative space, natural hand-held feel",
    "lighting": "soft natural daylight, no studio flash",
}

_ENGLISH_PALETTE: list[dict[str, str]] = [
    {"name": "Oat", "hex": "#E8E0D4", "usage": "background"},
    {"name": "Espresso", "hex": "#3B2A20", "usage": "headline text"},
    {"name": "Sage", "hex": "#9CAF88", "usage": "accent"},
]

# 英文分镜场景：(用途, 场景模板)，{product}/{pain}/{benefit} 由上游英文产物填充
_ENGLISH_SCENES: list[tuple[str, str]] = [
    ("Cover", "Close-up of {product} in a real {pain} moment, authentic and un-staged"),
    ("Scene", "{product} in hand during a normal weekday routine"),
    ("Detail", "The concrete difference of {product}: texture, label, or interface close-up"),
    ("Evidence", "Verifiable proof shot for {product}: label / test record / spec sheet"),
    ("Scene", "After using {product}: the same routine with less friction, {benefit}"),
]

_ENGLISH_STORYBOARD_CHANNELS = ("TikTok", "Reels", "Shorts")

_ENGLISH_CLAIM_HINTS = ("seconds", "minutes", "%", "0 sugar", "sugar-free", "zero sugar")
_ENGLISH_EFFECT_HINTS = ("cure", "guaranteed", "miracle", "heal", "slimming", "anti-aging")


def _english_visual(brief: Brief, ctx: dict[str, Any]) -> dict[str, Any]:
    """英文视觉方案（A8）：结构同中文分支，风格/场景/校验全部英文。"""
    draft = rec(ctx.get("draft"))
    main_ratio, cover_ratio, shot_count = channel_visual_spec(brief.channel)

    versions = as_obj_array(draft.get("versions"))
    recommended = as_str(draft.get("recommended_version"), "V1")
    target = next((v for v in versions if as_str(v.get("id")) == recommended), None) or (
        versions[0] if versions else {}
    )
    title = as_str(target.get("title"))
    body = as_str(target.get("body"))

    strategy = rec(ctx.get("strategy"))
    audience = rec(strategy.get("audience_profile"))
    pain = (as_str_array(audience.get("pain_points")) or ["too many options"])[0]
    house = rec(strategy.get("message_house"))
    benefit = (as_str_array(house.get("benefits")) or ["less to think about"])[0]
    visual_hint = as_str(rec(rec(ctx.get("creative")).get("tone_guide")).get("visual_suggestion"))
    seed = fnv1a(f"{brief.brand}{brief.channel}{brief.industry}{brief.tone}")
    style = _ENGLISH_VISUAL_STYLE
    word_limit = title_limit_for(brief.channel, "en")

    # --- 配图 Prompt ---
    scenes = _ENGLISH_SCENES[: max(3, min(shot_count, len(_ENGLISH_SCENES)))]
    image_prompts = []
    for index, (usage, scene) in enumerate(scenes):
        scene_text = scene.format(product=brief.product, pain=pain, benefit=benefit)
        prompt = ", ".join(
            [
                scene_text,
                style["mood"],
                *pick_many(visual_styles_for(brief.industry)[0].prompt_fragments, 3, seed + index),
                f"aspect ratio {main_ratio}",
            ]
        )
        image_prompts.append(
            {
                "id": f"IMG{index + 1}",
                "usage": usage,
                "scene": scene_text,
                "prompt": prompt,
                "negative": "stiff stock-photo pose, HDR glow, exaggerated emotion",
                "aspect_ratio": main_ratio,
            }
        )

    # --- 分镜脚本（英文动态渠道） ---
    storyboard: list[dict[str, str]] = []
    if any(key in brief.channel for key in _ENGLISH_STORYBOARD_CHANNELS):
        beats = [
            ("0-3s", "Subject looks up at the camera", shorten(pain, 30), "hard cut"),
            ("3-10s", "Close-up of the pain-point scene, ambient sound kept", pain, "dissolve"),
            ("10-30s", f"{brief.brand} {brief.product} in real use, details shown", benefit, "push in"),
            ("30-45s", "Verifiable proof on camera: label / test record", "(1s hold, no voice-over)", "hard cut"),
            ("45-50s", "Product freeze-frame + on-screen call to action", "See the comments", "fade out"),
        ]
        storyboard = [
            {
                "shot": f"Shot {index + 1}",
                "duration": duration,
                "visual": visual,
                "copy_overlay": overlay,
                "transition": transition,
            }
            for index, (duration, visual, overlay, transition) in enumerate(beats)
        ]

    # --- 版式建议 ---
    layout = {
        "cover": (
            f"{cover_ratio} cover: keep the main title within {word_limit} words, "
            "clear font-weight contrast, avoid matching the background luminance"
        ),
        "body": "Arrange inner pages as '" + " → ".join(usage for usage, _ in scenes) + "', one message per frame",
        "typography": (
            f"Bold sans-serif in the '{style['style']}' mood, body line-height at least 1.6, "
            "use the top two palette colors to separate hierarchy"
        ),
    }

    # --- 图文一致性校验（英文口径的提示词表） ---
    text = f"{title}\n{body}"
    conflicts: list[str] = []
    has_evidence = any(item["usage"] == "Evidence" for item in image_prompts)
    has_before_after = any("After" in item["usage"] for item in image_prompts)
    if any(hint in text.lower() for hint in _ENGLISH_CLAIM_HINTS) and not has_evidence:
        conflicts.append(
            "Copy contains verifiable claims (time/ingredients); no evidence frame is planned — copy and visuals disagree"
        )
    if has_before_after and any(hint in text.lower() for hint in _ENGLISH_EFFECT_HINTS):
        conflicts.append(
            "A before/after frame is planned while the copy makes effect claims; this may read as an effect promise"
        )
    if brief.brand.lower() not in text.lower():
        conflicts.append(
            "The brand name never appears in the copy, yet visuals end on a brand freeze-frame — brand association breaks"
        )

    notes = [
        f"{len(image_prompts)} images mapped to the outer structure ('" + " / ".join(usage for usage, _ in scenes[:3]) + "')",
        f"Write a separate short cover title (within {word_limit} words); do not reuse the body title directly",
    ]
    if visual_hint:
        notes.append(f"Carry over the creative-stage visual direction: {visual_hint}")
    if storyboard:
        notes.append(f"Storyboard totals about {_storyboard_seconds(storyboard)} seconds; voice-over at 3 words/second")

    return {
        "channel": brief.channel,
        "visual_direction": {
            "style": style["style"],
            "mood": style["mood"],
            "composition": style["composition"],
            "lighting": style["lighting"],
            "palette": _ENGLISH_PALETTE,
            "rationale": (
                f"Matches how '{brief.industry}' brands communicate visually and the '{brief.tone}' voice; "
                f"stays recognizable in {brief.channel}'s feed"
            ),
        },
        "assets": {
            "main_ratio": main_ratio,
            "cover_ratio": cover_ratio,
            "shot_count": len(image_prompts),
        },
        "image_prompts": image_prompts,
        "storyboard": storyboard,
        "layout": layout,
        "copy_visual_check": {
            "aligned": not conflicts,
            "conflicts": conflicts,
            "notes": notes,
        },
        "confidence": 0.83,
        "risks": (["Copy-visual check failed: align the visual plan with the final copy before publishing"] if conflicts else []),
        "evidence": [
            {
                "claim": f"{brief.channel} visual spec is '{main_ratio}' with about {shot_count} frames",
                "source": "platform media specs and operations practice",
                "reliability": 0.8,
            },
            {
                "claim": f"'{brief.industry}' favors a '{style['style']}' visual direction",
                "source": "category visual communication practice",
                "reliability": 0.7,
            },
        ],
    }


def generate_visual(ctx: dict[str, Any]) -> dict[str, Any]:
    brief = as_brief(ctx)
    # 非中文 Brief：视觉风格库/场景模板是中文语料，改走英文模板
    if normalize_language(brief.language) == "en":
        return _english_visual(brief, ctx)
    creative = rec(ctx.get("creative"))
    strategy = rec(ctx.get("strategy"))
    draft = rec(ctx.get("draft"))
    seed = fnv1a(f"{brief.brand}{brief.channel}{brief.industry}{brief.tone}")

    style = visual_styles_for(brief.industry)[0]
    main_ratio, cover_ratio, shot_count = channel_visual_spec(brief.channel)

    versions = as_obj_array(draft.get("versions"))
    recommended = as_str(draft.get("recommended_version"), "V1")
    target = next((v for v in versions if as_str(v.get("id")) == recommended), None) or (
        versions[0] if versions else {}
    )
    title = as_str(target.get("title"))
    body = as_str(target.get("body"))

    audience = rec(strategy.get("audience_profile"))
    house = rec(strategy.get("message_house"))
    pain = (as_str_array(audience.get("pain_points")) or ["选择成本太高"])[0]
    benefit = (as_str_array(house.get("benefits")) or ["更省心"])[0]
    visual_hint = as_str(rec(creative.get("tone_guide")).get("visual_suggestion"))

    # --- 配图 Prompt ---
    scenes = _VISUAL_SCENES[: max(3, min(shot_count, len(_VISUAL_SCENES)))]
    image_prompts = []
    for index, (usage, scene) in enumerate(scenes):
        scene_text = scene.format(product=brief.product, pain=pain, benefit=benefit)
        prompt = ", ".join(
            [
                scene_text,
                style.mood,
                *pick_many(style.prompt_fragments, 3, seed + index),
                f"aspect ratio {main_ratio}",
            ]
        )
        image_prompts.append(
            {
                "id": f"IMG{index + 1}",
                "usage": usage,
                "scene": scene_text,
                "prompt": prompt,
                "negative": style.negative,
                "aspect_ratio": main_ratio,
            }
        )

    # --- 分镜脚本（仅动态内容渠道） ---
    storyboard: list[dict[str, str]] = []
    if any(key in brief.channel for key in _STORYBOARD_CHANNELS):
        beats = [
            ("0-3s", "主体抬头看镜头", shorten(pain, 12), "硬切"),
            ("3-10s", "痛点场景特写，保留环境音", pain, "叠化"),
            ("10-30s", f"{brief.brand} {brief.product} 使用过程与细节", benefit, "推进"),
            ("30-45s", "可核验素材实拍：配料表 / 实测记录", "（留白 1s，不配口播）", "硬切"),
            ("45-50s", "产品定格 + 字幕行动引导", "评论区见", "淡出"),
        ]
        storyboard = [
            {
                "shot": f"镜头 {index + 1}",
                "duration": duration,
                "visual": visual,
                "copy_overlay": overlay,
                "transition": transition,
            }
            for index, (duration, visual, overlay, transition) in enumerate(beats)
        ]

    # --- 版式建议 ---
    layout = {
        "cover": (
            f"{cover_ratio} 封面：主标题控制在 {min(12, title_limit(brief.channel))} 字以内，"
            f"字体重量对比明确，避免与背景同明度"
        ),
        "body": "内页按「" + " → ".join(usage for usage, _ in scenes) + "」排列，每张只讲一件事",
        "typography": (
            f"标题使用「{style.name}」调性的粗体无衬线，正文行高不小于 1.6，"
            f"用「{'、'.join(c.name for c in style.palette[:2])}」区分信息层级"
        ),
    }

    # --- 图文一致性校验（可测量的规则，而非模型自述） ---
    text = f"{title}\n{body}"
    conflicts: list[str] = []
    has_evidence = any("证据" in item["usage"] for item in image_prompts)
    has_before_after = any("前后" in item["usage"] for item in image_prompts)
    if any(hint in text for hint in _CLAIM_HINTS) and not has_evidence:
        conflicts.append("文案含可验证断言（时间/成分等），但配图未安排证据画面，图文口径不一致")
    if has_before_after and any(hint in text for hint in _EFFECT_HINTS):
        conflicts.append("画面安排了使用前后对照，叠加文案中的效果类表述，可能被判定为效果承诺")
    if brief.brand not in text:
        conflicts.append("文案未出现品牌名，画面却以品牌定格收尾，品牌联想无法闭合")

    notes = [
        f"共 {len(image_prompts)} 张配图，与外层结构「{' / '.join(rule_block for rule_block in channel_rule(brief.channel).blocks[:3])}」对应",
        f"封面单独撰写短标题（不超过 {min(12, title_limit(brief.channel))} 字），不直接复用正文标题",
    ]
    if visual_hint:
        notes.append(f"沿用创意阶段的视觉倾向：{visual_hint}")
    if storyboard:
        notes.append(f"分镜总时长约 {_storyboard_seconds(storyboard)} 秒，口播字数按 3 字/秒控制")

    return {
        "channel": brief.channel,
        "visual_direction": {
            "style": style.name,
            "mood": style.mood,
            "composition": style.composition,
            "lighting": style.lighting,
            "palette": [
                {"name": color.name, "hex": color.hex, "usage": color.usage}
                for color in style.palette
            ],
            "rationale": (
                f"匹配「{brief.industry}」品类的视觉沟通习惯与「{brief.tone}」调性，"
                f"在{brief.channel}的信息流环境中保持可辨识度"
            ),
        },
        "assets": {
            "main_ratio": main_ratio,
            "cover_ratio": cover_ratio,
            "shot_count": len(image_prompts),
        },
        "image_prompts": image_prompts,
        "storyboard": storyboard,
        "layout": layout,
        "copy_visual_check": {
            "aligned": not conflicts,
            "conflicts": conflicts,
            "notes": notes,
        },
        "confidence": 0.83,
        "risks": (["图文一致性校验未通过：视觉方案需与定稿文案对齐后再投放"] if conflicts else []),
        "evidence": [
            {
                "claim": f"{brief.channel}的视觉规格为「{main_ratio}」画幅、建议 {shot_count} 张",
                "source": "平台图文规范与运营实践",
                "reliability": 0.8,
            },
            {
                "claim": f"「{brief.industry}」品类适合「{style.name}」视觉风格",
                "source": "品类视觉沟通经验库",
                "reliability": 0.7,
            },
        ],
    }


def _storyboard_seconds(storyboard: list[dict[str, str]]) -> int:
    """从分镜的 ``0-3s`` / ``45-50s`` 区间中取末镜结束秒数。"""
    if not storyboard:
        return 0
    numbers = [int(n) for n in re.findall(r"\d+", storyboard[-1]["duration"])]
    return numbers[-1] if numbers else 0


# ------------------------------------------------------------------ #
# A9 渠道运营与 SEO                                                   #
# ------------------------------------------------------------------ #

#: 与 SeoPattern.placement 一一对应的位置标签。
_SEO_POSITION_LABELS = ["标题", "正文 / 口播", "标签"]

#: 发布节奏：主贴 → 二次分发 → 复盘。
_PUBLISH_ACTIONS: list[tuple[str, str]] = [
    ("发布主渠道内容", "对齐目标人群活跃高峰，发布后 2 小时内维护评论区"),
    ("二次分发与答疑", "复用封面与证据画面剪短视频，降低二次制作成本"),
    ("数据复盘", "对比 A/B 变量表现，回填至效果复盘"),
]


def _compress_title(title: str, limit: int) -> str:
    """把标题压到 ``limit`` 字以内（超出时截断并加省略号）。

    ⚠️ 省略号**占一个字符**，因此切片长度必须是 ``limit - 1``：
    写成 ``title[:limit] + "…"`` 会得到 ``limit + 1`` 字 —— 恰好比渠道上限多一个字，
    而 ``title_ok`` 之类基于同一常量的检查却会显示「通过」。
    这个 off-by-one 由黄金数据集的「标题 ≤ 渠道上限」断言发现（见 MEMORY 踩坑 55）。
    """
    if len(title) <= limit:
        return title
    if limit <= 1:
        return title[:limit]
    return f"{title[: limit - 1]}…"


def _parse_length_range(hint: str) -> tuple[int, int] | None:
    """从「正文 300-600 字」「口播 150-220 字」中取出字数区间（取最后两个数字）。"""
    numbers = [int(n) for n in re.findall(r"\d+", hint)]
    if len(numbers) >= 2:
        return numbers[-2], numbers[-1]
    return None


_ENGLISH_PUBLISH_SLOTS: list[str] = [
    "8:00-9:30 AM local",
    "12:00-1:00 PM local",
    "6:00-8:00 PM local",
]


def _english_channel(brief: Brief, ctx: dict[str, Any]) -> dict[str, Any]:
    """英文渠道适配（A9）：标题按「词」计上限，规则/SEO/排期全部英文。

    中文分支的 ``title_limit`` 是字符口径，对英文是错的（12 词早已超信息流
    截断点）；这里统一走 ``title_limit_for(channel, 'en')`` 的词数口径，
    与 A5 英文审校一致。
    """
    strategy = rec(ctx.get("strategy"))
    plan = rec(ctx.get("plan"))
    draft = rec(ctx.get("draft"))

    versions = as_obj_array(draft.get("versions"))
    recommended = as_str(draft.get("recommended_version"), "V1")
    target = next((v for v in versions if as_str(v.get("id")) == recommended), None) or (
        versions[0] if versions else {}
    )
    base_title = as_str(target.get("title"))
    body = as_str(target.get("body"))
    base_tags = as_str_array(target.get("hashtags"))
    keyword = brief.keywords[0] if brief.keywords else brief.industry

    # --- 目标平台：主渠道 + 策略给出的次要渠道 ---
    targets: list[str] = []
    for candidate in [
        brief.channel,
        *(as_str(item.get("channel")) for item in as_obj_array(strategy.get("channel_priority"))),
    ]:
        if candidate and candidate not in targets:
            targets.append(candidate)
    targets = targets[:3]

    default_tags = [
        *(f"#{kw.replace(' ', '')}" for kw in brief.keywords),
        f"#{brief.brand.replace(' ', '')}",
        "#ad",
        "#review",
    ]

    platforms: list[dict[str, Any]] = []
    for index, channel in enumerate(targets):
        platform_format = _english_format(channel)
        limit = title_limit_for(channel, brief.language)
        # 搜索导向标题：前置主关键词（ASCII 分隔），与情绪型标题形成 A/B 对照
        variant = (
            f"{keyword} | {base_title}" if keyword and keyword not in base_title else base_title
        )
        title = _compress_title_words(variant, limit)
        words = len([w for w in title.split() if w])
        variant_words = len([w for w in variant.split() if w])
        platforms.append(
            {
                "channel": channel,
                "format": platform_format,
                "title": title,
                "title_original": base_title,
                "title_length": words,
                "title_limit": limit,
                "title_ok": words <= limit,
                "seo_variant": variant,
                "seo_variant_ok": variant_words <= limit,
                "body": body,
                "hashtags": base_tags if index == 0 else default_tags,
                "keywords": [keyword, *(brief.keywords[1:])],
                "publish_slot": _ENGLISH_PUBLISH_SLOTS[min(index, len(_ENGLISH_PUBLISH_SLOTS) - 1)],
                "notes": (
                    f"Reuses the A5 final body; only title and tags change. Format: '{platform_format}'"
                    if index == 0
                    else (
                        f"Needs restructuring for '{platform_format}'; reuse cover and evidence frames; "
                        "facts and compliance wording stay unchanged"
                    )
                ),
            }
        )

    # --- SEO（英文搜索口径；seo_pattern 是中文平台语料，不直接引用） ---
    long_tail = [
        f"best {keyword}",
        f"{keyword} review",
        f"how to choose {keyword}",
        f"{keyword} vs alternatives",
    ]
    placement = [
        {"position": "Title", "keyword": keyword, "note": "mention the keyword once, naturally"},
        {"position": "Body / caption", "keyword": keyword, "note": "one mention in the first paragraph"},
        {"position": "Hashtags", "keyword": keyword, "note": "reflect it in 1-2 tags"},
    ]
    seo = {
        "primary_keywords": [keyword, *brief.keywords[1:]],
        "long_tail": long_tail,
        "search_intent": "discovery + comparison",
        "difficulty": "medium",
        "placement": placement,
        "density_hint": (
            f"Use '{keyword}' once in the title and 1-2 times in the body; stuffing triggers feed throttling"
        ),
    }

    # --- 发布计划 ---
    _english_actions = [
        ("Publish the primary post", "target the audience's active hours"),
        ("Second-round comment replies", "harvest frequent questions as the next topic"),
        ("Performance review", "compare headline A/B results"),
    ]
    publish_plan = [
        {"slot": slot, "action": action, "note": note}
        for slot, (action, note) in zip(_ENGLISH_PUBLISH_SLOTS, _english_actions)
    ]
    known_slots = {item["slot"] for item in publish_plan}
    for item in as_obj_array(plan.get("publishing_rhythm")):
        slot = as_str(item.get("slot"))
        if slot and slot not in known_slots:
            publish_plan.append(
                {
                    "slot": slot,
                    "action": as_str(item.get("action")),
                    "note": as_str(item.get("note")),
                }
            )
            known_slots.add(slot)

    # --- A/B 方案 ---
    ab_tests = [
        {
            "hypothesis": "A search-oriented title (keyword first) beats an emotion-led title",
            "variant_a": platforms[0]["seo_variant"],
            "variant_b": platforms[0]["title"],
            "metric": "Search-driven share / CTR",
        },
        {
            "hypothesis": f"A concrete number on the cover improves save rate ({brief.channel})",
            "variant_a": "Cover shows the number",
            "variant_b": "Pure scene cover",
            "metric": "Save rate",
        },
    ]

    # --- 渠道规范核对（可测量的信号；口径与中文分支一致，表述英文） ---
    checklist: list[dict[str, str]] = [
        {
            "rule": f"Content format: {platforms[0]['format']}",
            "status": "pass",
            "note": "adapted to this channel's format",
        },
    ]

    over_limit = [item for item in platforms if not item["title_ok"]]
    checklist.append(
        {
            "rule": f"Title length: {brief.channel} <= {title_limit_for(brief.channel, brief.language)} words",
            "status": "warn" if over_limit else "pass",
            "note": (
                "; ".join(
                    f"{item['channel']} {item['title_length']}/{item['title_limit']} words"
                    for item in over_limit
                )
                if over_limit
                else f"Publishing title is {platforms[0]['title_length']} words, within the limit"
            ),
        }
    )

    body_words = len([w for w in body.split() if w])
    low, high = 60, 250
    in_range = low <= body_words <= high
    checklist.append(
        {
            "rule": f"Body length: {low}-{high} words",
            "status": "pass" if in_range else "warn",
            "note": (
                f"Body is {body_words} words, inside the recommended range"
                if in_range
                else f"Body is {body_words} words, outside the {low}-{high} range; add detail or trim"
            ),
        }
    )

    tag_count = len(platforms[0]["hashtags"])
    checklist.append(
        {
            "rule": "Hashtag strategy: 5+ relevant tags",
            "status": "pass" if tag_count >= 5 else "warn",
            "note": f"Currently {tag_count} tags",
        }
    )

    cta = as_str(target.get("cta"))
    cta_present = bool(cta) and cta.split()[0].lower() in body.lower()
    checklist.append(
        {
            "rule": "Structure: call to action present",
            "status": "pass" if cta_present else "warn",
            "note": f"Call to action: {cta}" if cta_present else "No explicit call to action found in the body; conversion path incomplete",
        }
    )

    checklist.append(
        {
            "rule": "Channel compliance: paid-partnership disclosure",
            "status": "pass",
            "note": "Covered by the A7 compliance report; non-Chinese markets need manual review (no local lexicon)",
        }
    )

    return {
        "primary_channel": brief.channel,
        "platforms": platforms,
        "seo": seo,
        "publish_plan": publish_plan,
        "ab_tests": ab_tests,
        "channel_checklist": checklist,
        "confidence": 0.85,
        "risks": [
            *(
                [
                    f"{item['channel']} title compressed to {item['title_limit']} words; confirm no information was lost before publishing"
                    for item in over_limit
                ]
            ),
            *(
                [
                    f"{item['rule']} not met: {item['note']}"
                    for item in checklist
                    if item["status"] != "pass"
                ][:2]
            ),
        ],
        "evidence": [
            {
                "claim": (
                    f"{brief.channel} title limit is {title_limit_for(brief.channel, brief.language)} words "
                    "(word-based, per language profile)"
                ),
                "source": "platform media specs and language profile",
                "reliability": 0.85,
            },
            {
                "claim": f"{brief.channel} search intent is discovery + comparison",
                "source": "platform search behavior observation",
                "reliability": 0.7,
            },
        ],
    }


def generate_channel(ctx: dict[str, Any]) -> dict[str, Any]:
    brief = as_brief(ctx)
    # 非中文 Brief：渠道规则/SEO 模式/排期语料是中文的，改走英文模板（标题按词计上限）
    if normalize_language(brief.language) == "en":
        return _english_channel(brief, ctx)
    strategy = rec(ctx.get("strategy"))
    plan = rec(ctx.get("plan"))
    draft = rec(ctx.get("draft"))
    rule = channel_rule(brief.channel)
    pattern = seo_pattern(brief.channel)
    slots = publish_slots(brief.channel)

    versions = as_obj_array(draft.get("versions"))
    recommended = as_str(draft.get("recommended_version"), "V1")
    target = next((v for v in versions if as_str(v.get("id")) == recommended), None) or (
        versions[0] if versions else {}
    )
    base_title = as_str(target.get("title"))
    body = as_str(target.get("body"))
    base_tags = as_str_array(target.get("hashtags"))
    keyword = brief.keywords[0] if brief.keywords else brief.industry

    # --- 目标平台：主渠道 + 策略给出的次要渠道 ---
    targets: list[str] = []
    for candidate in [
        brief.channel,
        *(as_str(item.get("channel")) for item in as_obj_array(strategy.get("channel_priority"))),
    ]:
        if candidate and candidate not in targets:
            targets.append(candidate)
    targets = targets[:3]

    platforms: list[dict[str, Any]] = []
    for index, channel in enumerate(targets):
        platform_rule = channel_rule(channel)
        limit = title_limit(channel)
        # 搜索导向标题：前置主关键词，与情绪型标题形成 A/B 对照
        variant = (
            f"{keyword}｜{base_title}" if keyword and keyword not in base_title else base_title
        )
        title = _compress_title(variant, limit)
        platforms.append(
            {
                "channel": channel,
                "format": platform_rule.format,
                "title": title,
                "title_original": base_title,
                "title_length": len(title),
                "title_limit": limit,
                "title_ok": len(title) <= limit,
                "seo_variant": variant,
                "seo_variant_ok": len(variant) <= limit,
                "body": body,
                "hashtags": base_tags if index == 0 else build_hashtags(brief),
                "keywords": [keyword, *(brief.keywords[1:])],
                "publish_slot": slots[min(index, len(slots) - 1)],
                "notes": (
                    f"沿用 A5 定稿正文，仅调整标题与标签；形态「{platform_rule.format}」"
                    if index == 0
                    else f"需按「{platform_rule.format}」重排结构，复用封面与证据画面；"
                    "正文事实与合规表述保持不变"
                ),
            }
        )

    # --- SEO ---
    long_tail = [template.format(kw=keyword) for template in pattern.modifiers][:4]
    placement = [
        {"position": label, "keyword": keyword, "note": note}
        for label, note in zip(_SEO_POSITION_LABELS, pattern.placement)
    ]

    seo = {
        "primary_keywords": [keyword, *brief.keywords[1:]],
        "long_tail": long_tail,
        "search_intent": pattern.search_intent,
        "difficulty": pattern.difficulty,
        "placement": placement,
        "density_hint": f"{keyword}在标题出现 1 次、正文自然嵌入 1-2 次即可，堆砌会触发平台限流",
    }

    # --- 发布计划 ---
    publish_plan = [
        {"slot": slot, "action": action, "note": note}
        for slot, (action, note) in zip(slots, _PUBLISH_ACTIONS)
    ]
    known_slots = {item["slot"] for item in publish_plan}
    for item in as_obj_array(plan.get("publishing_rhythm")):
        slot = as_str(item.get("slot"))
        if slot and slot not in known_slots:
            publish_plan.append(
                {
                    "slot": slot,
                    "action": as_str(item.get("action")),
                    "note": as_str(item.get("note")),
                }
            )
            known_slots.add(slot)

    # --- A/B 方案 ---
    ab_tests = [
        {
            "hypothesis": "搜索导向标题（前置关键词）优于情绪型标题",
            "variant_a": platforms[0]["seo_variant"],
            "variant_b": platforms[0]["title"],
            "metric": "搜索进入占比 / CTR",
        },
        {
            "hypothesis": f"封面含具体数字锚点提升收藏率（{brief.channel}）",
            "variant_a": "封面含数字锚点",
            "variant_b": "封面纯场景图",
            "metric": "收藏率",
        },
    ]

    # --- 渠道规范核对（尽量用可测量的信号） ---
    checklist: list[dict[str, str]] = [
        {
            "rule": f"内容形态：{rule.format}",
            "status": "pass",
            "note": "已按该渠道形态适配",
        },
    ]

    over_limit = [item for item in platforms if not item["title_ok"]]
    checklist.append(
        {
            "rule": f"标题长度：{brief.channel} ≤ {title_limit(brief.channel)} 字",
            "status": "warn" if over_limit else "pass",
            "note": (
                "；".join(
                    f"{item['channel']} {item['title_length']}/{item['title_limit']} 字"
                    for item in over_limit
                )
                if over_limit
                else f"投放标题 {platforms[0]['title_length']} 字，符合上限",
            ),
        }
    )

    length_range = _parse_length_range(rule.length_hint)
    char_count = len(_WHITESPACE_RE.sub("", body))
    if length_range:
        low, high = length_range
        in_range = low <= char_count <= high
        checklist.append(
            {
                "rule": f"正文长度：{rule.length_hint}",
                "status": "pass" if in_range else "warn",
                "note": (
                    f"正文 {char_count} 字，处于推荐区间"
                    if in_range
                    else f"正文 {char_count} 字，偏离推荐区间 {low}-{high} 字，建议补充细节或精简"
                ),
            }
        )

    tag_count = len(platforms[0]["hashtags"])
    checklist.append(
        {
            "rule": f"标签策略：{rule.hashtag_policy}",
            "status": "pass" if tag_count >= 5 else "warn",
            "note": f"当前 {tag_count} 个标签",
        }
    )

    if any("话题标签" in block for block in rule.blocks):
        checklist.append(
            {
                "rule": "结构完整性：话题标签已布局",
                "status": "pass" if tag_count else "fail",
                "note": f"标签 {tag_count} 个" if tag_count else "缺少话题标签，损失搜索与推荐流量",
            }
        )
    cta = as_str(target.get("cta"))
    if any("行动引导" in block for block in rule.blocks):
        cta_present = bool(cta) and cta[:6] in body
        checklist.append(
            {
                "rule": "结构完整性：行动引导已布局",
                "status": "pass" if cta_present else "warn",
                "note": f"行动引导：{cta}" if cta_present else "正文未检出行动引导，转化路径不完整",
            }
        )

    for note in rule.compliance_notes:
        checklist.append({"rule": f"渠道合规：{note}", "status": "pass", "note": "由 A7 合规报告覆盖"})

    return {
        "primary_channel": brief.channel,
        "platforms": platforms,
        "seo": seo,
        "publish_plan": publish_plan,
        "ab_tests": ab_tests,
        "channel_checklist": checklist,
        "confidence": 0.85,
        "risks": [
            *(
                [
                    f"{item['channel']} 标题已压缩至 {item['title_limit']} 字，投放前需人工确认信息完整性"
                    for item in over_limit
                ]
            ),
            *(
                [
                    f"{item['rule']} 未满足：{item['note']}"
                    for item in checklist
                    if item["status"] != "pass"
                ][:2]
            ),
        ],
        "evidence": [
            {
                "claim": f"{brief.channel} 标题上限 {title_limit(brief.channel)} 字、{rule.hashtag_policy}",
                "source": "平台公开规则与运营实践",
                "reliability": 0.85,
            },
            {
                "claim": f"{brief.channel} 搜索意图：{pattern.search_intent}",
                "source": "平台搜索行为观察",
                "reliability": 0.7,
            },
        ],
    }


# ------------------------------------------------------------------ #
# A11 记忆与知识库                                                    #
# ------------------------------------------------------------------ #


def _english_memory(brief: Brief, ctx: dict[str, Any]) -> dict[str, Any]:
    """英文知识卡片（A11）：卡片/模板/缺口全部英文，供后续英文任务召回。"""
    strategy = rec(ctx.get("strategy"))
    creative = rec(ctx.get("creative"))
    plan = rec(ctx.get("plan"))
    edit = rec(ctx.get("edit"))
    compliance = rec(ctx.get("compliance"))
    visual = rec(ctx.get("visual"))
    channel = rec(ctx.get("channel"))

    revision = int(as_num(ctx.get("revision"), 0))
    artifact_count = int(as_num(ctx.get("artifact_count"), 0))
    recalled = memory_hits(ctx)
    keyword = brief.keywords[0] if brief.keywords else brief.industry

    style = as_str(rec(visual.get("visual_direction")).get("style"), "Clean documentary lifestyle")
    big_idea = as_str(rec(creative.get("big_idea")).get("title"), brief.brand)
    proposition = as_str(rec(strategy.get("message_house")).get("proposition"))
    sections = [as_str(item.get("section")) for item in as_obj_array(plan.get("structure"))]
    platforms = [as_str(item.get("channel")) for item in as_obj_array(channel.get("platforms"))]
    overall = rec(edit.get("scorecard")).get("overall")

    hits = as_obj_array(compliance.get("hits"))
    blockers = [h for h in hits if as_str(h.get("severity")) == "blocker"]
    majors = [h for h in hits if as_str(h.get("severity")) == "major"]
    structure = " → ".join(sections) if sections else _english_format(brief.channel)

    cards: list[dict[str, Any]] = [
        {
            "type": "brand",
            "title": f"{brief.brand} | {brief.channel} voice and proposition baseline",
            "content": (
                f"For '{brief.audience}', {brief.brand}'s voice is '{brief.tone}': "
                "second person, empathize before offering the solution, concrete details over adjectives. "
                f"The proposition validated this round: '{proposition}'. "
                "Absolute claims and effect promises are banned; they collide with the A7 gate."
            ),
            "tags": ["brand voice", brief.tone, brief.channel],
            "reuse_hint": "Feed to A4 as a supplementary system prompt before drafting; saves a tone revision round",
        },
        {
            "type": "case",
            "title": f"{brief.industry} · {brief.channel} content structure sample",
            "content": (
                f"Topic '{big_idea}' used the '{structure}' structure, "
                f"matching the platform format '{_english_format(brief.channel)}', "
                f"final quality score {overall if overall is not None else '(not scored)'}, "
                f"adapted platforms: {', '.join(platforms) or brief.channel}."
            ),
            "tags": ["content structure", brief.industry, brief.channel],
            "reuse_hint": "New topics in the same industry and channel can reuse this structure; swap scenes and evidence",
        },
        {
            "type": "template",
            "title": f"Headline formula ({brief.channel})",
            "content": (
                f"'{keyword}' + identity anchor + emotion word, within "
                f"{title_limit_for(brief.channel, 'en')} words; "
                f"the search-oriented variant front-loads the keyword as '{keyword} | proposition'."
            ),
            "tags": ["headline", "template", brief.channel],
            "reuse_hint": "Generate five candidates per drafting round, then filter by the platform limit",
        },
        {
            "type": "lesson",
            "title": "High-risk phrasing and safe rewrites",
            "content": (
                (
                    f"This round hit {len(blockers)} blocker and {len(majors)} major compliance issues; "
                    f"e.g. '{as_str(blockers[0].get('term')) if blockers else as_str(majors[0].get('term')) if majors else 'subjective superlatives'}'; "
                    "safe direction: rewrite as a verifiable, bounded statement or add a source note."
                    if hits
                    else (
                        "No lexicon hits this round, but subjective superlatives ('the best', '#1') remain high-risk "
                        "and must be avoided."
                    )
                )
                + (
                    f" The task went through {revision} revision round(s), mostly for source notes and absolute claims."
                    if revision
                    else ""
                )
            ),
            "tags": ["compliance", "revision", "risk phrasing"],
            "reuse_hint": "Use as a negative-example list before A4 drafts to pre-empt most revision loops",
        },
    ]

    templates = [
        {
            "name": f"{brief.channel} content skeleton",
            "usage": "Reuse this structure and rhythm for new topics",
            "body": structure,
        },
        {
            "name": "Headline candidate formula",
            "usage": "Batch-produce headline candidates and filter by the platform limit",
            "body": f"{keyword} + identity anchor + emotion word (<= {title_limit_for(brief.channel, 'en')} words)",
        },
        {
            "name": "Copy-visual consistency checklist",
            "usage": "Self-check after A8 produces the visual plan",
            "body": "Do quantified claims have an evidence frame / is a before-after frame planned / does the copy mention the brand",
        },
    ]

    key_decisions = [
        f"Creative direction set to '{big_idea}'",
        f"Publishing title compressed to within {title_limit_for(brief.channel, 'en')} words to survive the feed",
        f"Visual direction set to '{style}'; image prompts share one style",
        *(
            f"Compliance term '{as_str(item.get('term'))}' rewritten as a bounded description"
            for item in (blockers or majors)[:2]
        ),
    ]

    gaps: list[str] = []
    if not brief.keywords:
        gaps.append("The brief has no keywords; SEO layout leans on industry-inferred terms")
    gaps.append("No first-hand user research; audience pains still come from templates — add interviews or surveys")
    if hits:
        gaps.append("The safe-rewrite library should absorb this round's industry-specific terms to avoid repeat loops")
    gaps.append("No publish-side data feedback yet; effect estimates remain range guesses, not a closed loop")

    return {
        "knowledge_cards": cards,
        "templates": templates,
        "archive": {
            "artifact_count": artifact_count,
            "artifact_types": [],
            "revision_rounds": revision,
            "key_decisions": [item for item in key_decisions if item],
            "reusable_assets": [
                f"Visual style: {style}",
                f"Platform list: {', '.join(platforms) or brief.channel}",
                f"Content skeleton: {len(sections) if sections else 0} sections",
            ],
        },
        # artifact_types 由 A11 节点按黑板真实产物回填，此处留空避免与系统数据不一致
        "reuse_suggestions": [
            *(
                [
                    f"Recalled {len(recalled)} historical asset(s) such as '{as_str(recalled[0].get('title'))}'; "
                    "align voice and phrasing to cut cold-start trial costs"
                ]
                if recalled
                else []
            ),
            f"Tasks in '{brief.industry}' can reuse the '{style}' visual direction and prompt structure",
            f"Tasks on '{brief.channel}' can reuse the headline formula and tag strategy to skip a trial round",
            "Register frequent comment questions as the next topic to build a content flywheel",
        ],
        "gaps": gaps,
        "confidence": 0.86,
        "risks": (
            ["Historical assets were reused, but the cards still summarize a single task; validate across more tasks"]
            if recalled
            else ["Knowledge cards come from one task (sample size 1); validate across more tasks before treating as rules"]
        ),
        "evidence": [
            {
                "claim": f"Final quality score {overall if overall is not None else '(not scored)'}; {len(hits)} compliance hits",
                "source": "this task's blackboard record",
                "reliability": 0.9,
            },
            {
                "claim": f"The {brief.channel} skeleton follows '{structure}'",
                "source": "platform format spec",
                "reliability": 0.8,
            },
        ],
    }


def generate_memory(ctx: dict[str, Any]) -> dict[str, Any]:
    brief = as_brief(ctx)
    # 非中文 Brief：卡片/模板/缺口话术是中文语料，改走英文模板
    if normalize_language(brief.language) == "en":
        return _english_memory(brief, ctx)
    strategy = rec(ctx.get("strategy"))
    creative = rec(ctx.get("creative"))
    plan = rec(ctx.get("plan"))
    edit = rec(ctx.get("edit"))
    compliance = rec(ctx.get("compliance"))
    visual = rec(ctx.get("visual"))
    channel = rec(ctx.get("channel"))
    analysis = rec(ctx.get("analysis"))

    revision = int(as_num(ctx.get("revision"), 0))
    artifact_count = int(as_num(ctx.get("artifact_count"), 0))
    recalled = memory_hits(ctx)
    rule = channel_rule(brief.channel)
    keyword = brief.keywords[0] if brief.keywords else brief.industry

    style = as_str(rec(visual.get("visual_direction")).get("style"))
    big_idea = as_str(rec(creative.get("big_idea")).get("title"))
    proposition = as_str(rec(strategy.get("message_house")).get("proposition"))
    sections = [as_str(item.get("section")) for item in as_obj_array(plan.get("structure"))]
    platforms = [as_str(item.get("channel")) for item in as_obj_array(channel.get("platforms"))]
    scorecard = rec(edit.get("scorecard"))
    overall = scorecard.get("overall")

    hits = as_obj_array(compliance.get("hits"))
    blockers = [h for h in hits if as_str(h.get("severity")) == "blocker"]
    majors = [h for h in hits if as_str(h.get("severity")) == "major"]

    cards: list[dict[str, Any]] = [
        {
            "type": "brand",
            "title": f"{brief.brand}｜{brief.channel} 语气与主张基线",
            "content": (
                f"面向「{brief.audience}」，{brief.brand} 的语气基调为「{brief.tone}」："
                f"用第二人称对话、先共情再给方案、以具体细节替代形容词。"
                f"本轮验证有效的核心主张是「{proposition}」。"
                f"禁用绝对化用语与效果承诺，避免与 A7 合规门禁冲突。"
            ),
            "tags": ["品牌调性", brief.tone, brief.channel],
            "reuse_hint": "新任务起草前作为 A4 的补充系统提示，可减少一轮语气返工",
        },
        {
            "type": "case",
            "title": f"{brief.industry}·{brief.channel} 内容结构样例",
            "content": (
                f"选题「{big_idea}」采用 "
                f"「{' → '.join(sections) if sections else rule.format}」结构，"
                f"适配平台形态「{rule.format}」，"
                f"定稿综合质量分 {overall if overall is not None else '（未评分）'}，"
                f"已适配平台：{'、'.join(platforms) or brief.channel}。"
            ),
            "tags": ["内容结构", brief.industry, brief.channel],
            "reuse_hint": "同行业同渠道的新选题可直接套用该结构，替换场景与证据素材即可",
        },
{
    "type": "template",
            "title": f"标题公式（{brief.channel}）",
            "content": (
                f"「{keyword}」+ 身份锚点 + 情绪词，控制在 {title_limit(brief.channel)} 字以内；"
                f"搜索导向变体可将关键词前置为「{keyword}｜主张」。"
                f"渠道标题规范：{rule.title_style}"
            ),
            "tags": ["标题", "模板", brief.channel],
            "reuse_hint": "起草标题时批量生成 5 个候选，再按平台上限筛选",
        },
        {
            "type": "lesson",
            "title": "高风险表达与安全改写对照",
            "content": (
                (
                    f"本轮命中 {len(blockers)} 项阻断级、{len(majors)} 项重要级合规问题，"
                    f"典型如「{as_str(blockers[0].get('term')) if blockers else as_str(majors[0].get('term')) if majors else '主观绝对化表述'}」；"
                    f"安全改写方向：改为可验证的限定描述，或补充来源标注。"
                    if hits
                    else "本轮未命中合规词库，但主观最高级表述（如「最好」「最优」）仍属高危，需持续规避。"
                )
                + (
                    f" 本轮经历 {revision} 轮返工，返工主因集中在来源标注与绝对化表述。"
                    if revision
                    else ""
                )
            ),
            "tags": ["合规", "返工", "风险表达"],
            "reuse_hint": "作为 A4 起草前的负向示例清单，可提前拦截大部分返工",
        },
    ]

    templates = [
        {
            "name": f"{brief.channel} 内容骨架",
            "usage": "新选题复用本文结构与节奏",
            "body": " → ".join(sections) if sections else rule.format,
        },
        {
            "name": "标题候选公式",
            "usage": "批量产出标题候选并按平台上限筛选",
            "body": f"{keyword} + 身份锚点 + 情绪词（≤ {title_limit(brief.channel)} 字）",
        },
        {
            "name": "图文一致性校验清单",
            "usage": "A8 产出视觉方案后自检",
            "body": "量化断言是否有证据画面 / 是否安排使用前后对比 / 文案是否出现品牌名",
        },
    ]

    key_decisions = [
        f"创意方向选定为「{big_idea}」",
        f"投放标题压缩至 {title_limit(brief.channel)} 字以内以保证信息流完整展示",
        f"视觉方向选定「{style}」，统一配图 Prompt 风格",
        *(f"合规问题「{as_str(item.get('term'))}」改为限定描述" for item in (blockers or majors)[:2]),
    ]

    gaps: list[str] = []
    if not brief.keywords:
        gaps.append("Brief 未提供关键词，SEO 布局依赖行业词推断")
    gaps.append("缺少一手用户调研数据，受众痛点仍来自行业经验库，建议补充访谈或问卷")
    if hits:
        gaps.append("合规安全改写库需要沉淀本次命中的行业专项表述，避免重复返工")
    gaps.append("未接入发布侧数据回流，效果预估仍为区间推断，无法闭环验证")

    return {
        "knowledge_cards": cards,
        "templates": templates,
        "archive": {
            "artifact_count": artifact_count,
            "artifact_types": [],
            "revision_rounds": revision,
            "key_decisions": [item for item in key_decisions if item],
            "reusable_assets": [
                f"视觉风格：{style}",
                f"平台清单：{'、'.join(platforms) or brief.channel}",
                f"内容骨架：{len(sections) if sections else 0} 段",
            ],
        },
        # artifact_types 由 A11 节点按黑板真实产物回填，此处留空避免与系统数据不一致
        "reuse_suggestions": [
            *(
                [
                    f"本次已召回历史资产「{as_str(recalled[0].get('title'))}」等 {len(recalled)} 条，"
                    "可直接对齐调性与句式，降低冷启动试稿成本"
                ]
                if recalled
                else []
            ),
            f"同行业（{brief.industry}）任务可直接复用「{style}」视觉方向与配图 Prompt 结构",
            f"同渠道（{brief.channel}）任务可复用标题公式与标签策略，减少一轮试稿",
            "把评论区高频问题登记为下一轮选题，形成内容飞轮",
        ],
        "gaps": gaps,
        "confidence": 0.86,
        "risks": (
            ["本次已复用历史资产，但知识卡片仍是对单次任务的归纳，需多次任务验证后才可作为稳定规律"]
            if recalled
            else ["知识卡片来自单次任务，样本量为 1，需多次任务验证后才可作为稳定规律"]
        ),
        "evidence": [
            {
                "claim": f"定稿质量分 {overall if overall is not None else '（未评分）'}、合规命中 {len(hits)} 项",
                "source": "本次任务黑板记录",
                "reliability": 0.9,
            },
            {
                "claim": f"{brief.channel} 的结构骨架为「{' / '.join(rule.blocks[:4])}」",
                "source": "平台形态规范",
                "reliability": 0.8,
            },
        ],
    }


# ------------------------------------------------------------------ #
# 评估生成器（judge.evaluate）                                        #
# ------------------------------------------------------------------ #


def generate_judge(ctx: dict[str, Any]) -> dict[str, Any]:
    """离线评估器的「模型」替身：产出与 LLM-as-a-Judge 完全同构的评分 JSON。

    为什么给它建一个生成器，而不是让 mock 模式下直接回退到规则评估器：
    ``judge_with_llm`` 的**回退分支**与**成功分支**是两条不同的代码路径
    （回退走 ``judge_offline``，成功走 ``_normalize_llm_axes`` + LLM 的
    issues/suggestions/confidence）。默认的 ``provider=mock`` 会让成功分支
    永远跑不到 —— 而它恰恰是最容易因为字段名漂移而坏掉的那条。
    这里用规则评估器的真实打分作为「模型回答」，把成功分支也纳入离线可测范围。

    注意：它**不是**第二个评估口径。分数来源仍是 ``judge_offline``，
    只是换了一条「经过 LLM 契约解析」的路径来交付，因此与 LLM 模式下
    「模型恰好答对」时的结果一致。
    """
    from ..core.judge import AXIS_WEIGHTS, judge_offline  # 延迟导入，避免模块级环依赖

    brief = as_brief(ctx)
    upstream = rec(ctx.get("upstream"))
    report = judge_offline(brief, upstream, pass_threshold=75.0, kind="final")

    return {
        "axes": [
            {
                "key": axis.key,
                "score": round(axis.score, 1),
                "rationale": axis.rationale,
                "evidence": list(axis.evidence),
            }
            for axis in report.axes
        ] or [
            # 没有可评估文本时给全 0 分，明确表达「无法评估」而不是给个中庸分
            {"key": key, "score": 0.0, "rationale": "缺少可评估的正文产物", "evidence": []}
            for key in AXIS_WEIGHTS
        ],
        "summary": report.summary,
        "issues": list(report.issues),
        "suggestions": list(report.suggestions),
        "confidence": round(report.confidence, 3),
    }


def generate_video_script(ctx: dict[str, Any]) -> dict[str, Any]:
    """视频脚本（plan.md v2.0「视频脚本」）。

    产出结构化脚本：钩子 / 分镜（含时长与口播）/ 字幕 / 行动号召 / 拍摄要点。
    结构由知识层 ``app/knowledge/video.py`` 的骨架决定，内容由本次 Brief 与上游产物填充。
    """
    brief = as_brief(ctx)
    strategy = rec(ctx.get("strategy"))
    creative = rec(ctx.get("creative"))
    plan = rec(ctx.get("plan"))
    edit = rec(ctx.get("edit"))
    audience = rec(strategy.get("audience_profile"))
    house = rec(strategy.get("message_house"))

    spec = video_spec(brief.channel)
    skeleton = script_skeleton(brief.channel)

    pain = (as_str_array(audience.get("pain_points")) or ["选择成本太高"])[0]
    scenario = (as_str_array(audience.get("scenarios")) or ["日常使用"])[0]
    benefits = as_str_array(house.get("benefits")) or ["更省心"]
    proposition = as_str(house.get("proposition")) or f"{brief.brand} 的核心主张"
    big_idea = as_str(rec(creative.get("big_idea")).get("title"))
    revised = rec(edit.get("revised"))
    body = as_str(revised.get("body")) or as_str(plan.get("outline"))
    keyword = brief.keywords[0] if brief.keywords else brief.product

    # 钩子：3 秒内制造冲突或悬念（规则来自渠道规范）
    hook = f"{pain}？我用 {brief.product} 试了 7 天"

    voiceover: list[dict[str, Any]] = []
    subtitles: list[dict[str, Any]] = []
    shots: list[dict[str, Any]] = []

    shot_lines = {
        "钩子": hook,
        "痛点": f"每天{scenario}的时候，最烦的就是{pain}",
        "方案": f"{brief.brand} 的做法是：{benefits[0]}",
        "佐证": f"我连续用了 7 天，{keyword} 这件事上确实省心了",
        "转化": "想试的话，评论区我放了入口",
    }
    shot_visuals = {
        "钩子": f"手持 {brief.product} 怼脸特写，背景是通勤场景，字幕直接甩出问题",
        "痛点": "生活化快切：翻找 / 犹豫 / 皱眉，节奏 0.5 秒一刀",
        "方案": "产品使用过程实拍，标注关键细节，画面稳、节奏放缓",
        "佐证": "屏幕录制或实拍对比，参数与来源以角标形式出现",
        "转化": "产品 + 行动指令同框，字幕停留 2 秒以上便于点击",
    }

    for item in skeleton:
        role = str(item["role"])
        line = shot_lines.get(role, "")
        shots.append(
            {
                **item,
                "visual": shot_visuals.get(role, "按脚本意图补拍画面"),
                "voiceover": line,
                "subtitle": line,
                "camera": "手持稳定器" if item["shot"] % 2 else "固定机位",
            }
        )
        voiceover.append(
            {"shot": item["shot"], "start_second": item["start_second"], "line": line}
        )
        subtitles.append(
            {
                "shot": item["shot"],
                "start_second": item["start_second"],
                "end_second": item["end_second"],
                "text": line,
            }
        )

    return {
        "format": "short_video_script",
        "channel": brief.channel,
        "aspect_ratio": spec.aspect_ratio,
        "duration_seconds": spec.duration_seconds,
        "shot_count": len(shots),
        "hook": hook,
        "big_idea": big_idea,
        "proposition": proposition,
        "shots": shots,
        "voiceover": voiceover,
        "subtitles": subtitles,
        "cta": "评论区入口 / 主页链接",
        "production_notes": [
            f"画幅 {spec.aspect_ratio}，总时长控制在 {spec.duration_seconds} 秒",
            "前 3 秒必须出现钩子，不要放 logo 片头",
            "口播语速控制在每秒 4-5 字，字幕与口播逐句对齐",
            "所有参数与数据需在画面上标注来源",
        ],
        "compliance_notes": [
            "不得使用绝对化用语与效果承诺",
            "不得诱导点赞关注",
            "价格表述需与实际一致",
        ],
        "confidence": 0.82,
        "risks": ["脚本中的参数与效果数据需在拍摄前由品牌方确认"],
        "evidence": [
            {
                "claim": f"分镜骨架按渠道规范生成，共 {len(shots)} 镜",
                "source": f"{brief.channel} 渠道形态规范",
                "reliability": 0.85,
            }
        ],
    }


# ------------------------------------------------------------------ #
# 生成器注册表                                                        #
# ------------------------------------------------------------------ #

GENERATORS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "A1.strategy": generate_strategy,
    "A2.creative": generate_creative,
    "A3.plan": generate_plan,
    "A4.copy": generate_copy,
    "A5.edit": generate_edit,
    "A6.factcheck": generate_fact_check,
    "A7.compliance": generate_compliance,
    "A8.visual": generate_visual,
    "A8.video_script": generate_video_script,
    "A9.channel": generate_channel,
    "A10.analyze": generate_analysis,
    "A10.review": generate_analysis_review,
    "A11.memory": generate_memory,
    "judge.evaluate": generate_judge,
    "DOC.digest.map": generate_digest_map,
    "DOC.digest.reduce": generate_digest_reduce,
    "A4.copy.version": generate_copy_version,
}


# ------------------------------------------------------------------ #
# Provider                                                            #
# ------------------------------------------------------------------ #

LATENCY_MIN_MS = 220
LATENCY_MAX_MS = 520


class MockProvider:
    """离线生成引擎。

    ``purpose`` 决定走哪个生成器；未注册的用途直接抛错，
    避免「静默返回空内容」这种最难排查的故障模式。
    """

    name = "mock"
    model = "mock-creator-engine-v1"
    simulated = True

    def chat(self, request: LLMRequest) -> LLMResponse:
        started = time.monotonic()
        context = request.context or {}
        generator = GENERATORS.get(request.purpose)

        if generator is None:
            raise ValueError(f"Mock 引擎未实现用途：{request.purpose}")

        time.sleep((LATENCY_MIN_MS + random.random() * (LATENCY_MAX_MS - LATENCY_MIN_MS)) / 1000)

        payload = generator(context)
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        prompt_text = "\n".join(m.text for m in request.messages)

        return LLMResponse(
            content=content,
            provider=self.name,
            model=self.model,
            usage=LLMUsage(
                prompt_tokens=estimate_tokens(prompt_text),
                completion_tokens=estimate_tokens(content),
            ),
            latency_ms=int((time.monotonic() - started) * 1000),
            simulated=True,
        )
