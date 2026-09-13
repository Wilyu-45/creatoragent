"""LLM-as-a-Judge 评估流水线（plan.md 4.3 D14、2.5「系统级：黄金数据集评测」）。

为什么需要它
------------
门禁（A5/A6/A7）回答的是「这篇稿子能不能发布」——它是**否决式**的；
而评估要回答的是「这篇稿子有多好、下一轮 Prompt 该怎么改」——它是**度量式**的。
两者混在一起会出问题：把质量度量塞进门禁，会让「低于阈值就返工」的规则被
一个本身有噪声的评分放大成流程抖动。因此本项目把评估做成**独立的一条流水线**：
它消费已生成的产物，产出带证据的评分报告，供人工调优与回归对比。

两种评估器
----------
* ``offline``（默认）：确定性规则评估器，零依赖、可离线、同一输入永远同一分数。
  它是 CI 回归的基线，也是 Prompt 改动的「对照尺」。
* ``llm``：调用 OpenAI 兼容网关做真正的 LLM-as-a-Judge（任意 OpenAI 协议网关均可）。
  **任何异常都静默回退到离线评估器**，与 embedding 的「可失败」设计保持一致：
  评估是旁路能力，不能因为它抖动就让创作链路挂掉。

评分口径
--------
六个维度各 0–5 分，加权汇总为 0–100：

===========  ======  ============================================
维度          权重    依据
===========  ======  ============================================
brief_fit     0.25    是否命中 Brief 的关键词、受众、渠道与硬性约束
compliance    0.20    广告法词库 + 行业规则扫描（复用知识层，与 A7 同源）
structure     0.15    标题 / 正文 / CTA / 话题标签的完整度与长度合规
brand_voice   0.15    调性关键词、品牌词出现与语气一致性
fact_safety   0.15    无来源数字、绝对化用语、功效与收益承诺
appeal        0.10    标题吸引力、行动号召、互动引导
===========  ======  ============================================

裁决：``total >= pass_threshold`` 为 ``pass``，低于 0.8 倍阈值为 ``reject``，其余 ``review``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from ..knowledge.compliance import scan_compliance
from ..knowledge.industry import channel_rule, title_limit
from ..config import RUBRIC_VERSION
from ..logger import create_logger
from .types import Brief

log = create_logger("judge")

JudgeMode = Literal["offline", "llm"]
Verdict = Literal["pass", "review", "reject"]

#: 各维度权重（合计 1.0）
AXIS_WEIGHTS: dict[str, float] = {
    "brief_fit": 0.25,
    "compliance": 0.20,
    "structure": 0.15,
    "brand_voice": 0.15,
    "fact_safety": 0.15,
    "appeal": 0.10,
}

AXIS_LABEL: dict[str, str] = {
    "brief_fit": "需求契合",
    "compliance": "合规安全",
    "structure": "结构完整",
    "brand_voice": "品牌语气",
    "fact_safety": "事实稳妥",
    "appeal": "吸引力",
}

#: 每条问题最多留存的证据条数
MAX_EVIDENCE = 6

#: 视为「无来源数字」的检测式：数字 + 百分号/倍/成/万 等量级词
_NUMERIC_CLAIM_HINTS = ("%", "％", "倍", "成", "万+", "亿", "提升", "下降", "增长")

#: 行动号召用语（用于 appeal 维度）
_CTA_HINTS = ("点击", "评论", "收藏", "关注", "下单", "购买", "领取", "私信", "转发", "预约", "抢")


@dataclass
class JudgeAxis:
    """单个评分维度。"""

    key: str
    label: str
    score: float          # 0–5
    weight: float
    rationale: str = ""
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "score": round(self.score, 2),
            "weight": self.weight,
            "rationale": self.rationale,
            "evidence": list(self.evidence),
        }


@dataclass
class JudgeReport:
    """一次评估的结论。"""

    total: float                                  # 0–100
    verdict: Verdict
    mode: str                                     # offline / llm
    model: str = ""
    summary: str = ""
    axes: list[JudgeAxis] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    confidence: float = 0.0
    fallback: bool = False                        # llm 模式下是否回退到了离线评估器
    fallback_reason: str = ""
    rubric: str = RUBRIC_VERSION
    kind: str = "final"                           # final / revision（不同时点的评估）
    revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": round(self.total, 1),
            "verdict": self.verdict,
            "mode": self.mode,
            "model": self.model,
            "summary": self.summary,
            "axes": [axis.to_dict() for axis in self.axes],
            "issues": list(self.issues),
            "suggestions": list(self.suggestions),
            "confidence": round(self.confidence, 3),
            "fallback": self.fallback,
            "fallback_reason": self.fallback_reason,
            "rubric": self.rubric,
            "kind": self.kind,
            "revision": self.revision,
        }

    @property
    def axis_scores(self) -> dict[str, float]:
        return {axis.key: round(axis.score, 2) for axis in self.axes}


# ------------------------------------------------------------------ #
# 输入装配                                                            #
# ------------------------------------------------------------------ #


def target_text(upstream: dict[str, dict[str, Any]]) -> str:
    """抽取「待评估文本」：编辑定稿优先，缺失时退回文案初稿的推荐版本。

    评估对象必须是**真正要发布的那一稿**，否则评估分数与交付内容脱节。
    """
    edit = upstream.get("edit") or {}
    revised = edit.get("revised") if isinstance(edit.get("revised"), dict) else {}
    body = str(revised.get("body") or "").strip() if revised else ""
    if body:
        title = str(revised.get("title") or "")
        cta = str(revised.get("cta") or "")
        tags = revised.get("hashtags") or []
        return "\n".join(
            part for part in (title, body, cta, " ".join(str(tag) for tag in tags)) if part
        )

    draft = upstream.get("draft") or {}
    versions = draft.get("versions") if isinstance(draft.get("versions"), list) else []
    recommended = str(draft.get("recommended_version") or "")
    chosen = next(
        (v for v in versions if isinstance(v, dict) and str(v.get("id")) == recommended),
        versions[0] if versions else {},
    )
    if not isinstance(chosen, dict):
        return ""
    tags = chosen.get("hashtags") or []
    return "\n".join(
        part
        for part in (
            str(chosen.get("title") or ""),
            str(chosen.get("body") or ""),
            str(chosen.get("cta") or ""),
            " ".join(str(tag) for tag in tags),
        )
        if part
    )


def target_fields(upstream: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """抽取结构化字段（标题 / 正文 / CTA / 标签 / 关键词），供热词命中统计。"""
    edit = upstream.get("edit") or {}
    revised = edit.get("revised") if isinstance(edit.get("revised"), dict) else {}
    if revised and str(revised.get("body") or "").strip():
        return {
            "title": str(revised.get("title") or ""),
            "body": str(revised.get("body") or ""),
            "cta": str(revised.get("cta") or ""),
            "hashtags": [str(tag) for tag in (revised.get("hashtags") or [])],
            "source": "edited_copy",
        }

    draft = upstream.get("draft") or {}
    versions = draft.get("versions") if isinstance(draft.get("versions"), list) else []
    recommended = str(draft.get("recommended_version") or "")
    chosen = next(
        (v for v in versions if isinstance(v, dict) and str(v.get("id")) == recommended),
        versions[0] if versions else {},
    )
    chosen = chosen if isinstance(chosen, dict) else {}
    return {
        "title": str(chosen.get("title") or ""),
        "body": str(chosen.get("body") or ""),
        "cta": str(chosen.get("cta") or ""),
        "hashtags": [str(tag) for tag in (chosen.get("hashtags") or [])],
        "source": "copy_draft",
    }


# ------------------------------------------------------------------ #
# 离线规则评估器                                                      #
# ------------------------------------------------------------------ #


def _clamp(value: float, low: float = 0.0, high: float = 5.0) -> float:
    return max(low, min(high, value))


def _dedupe(items: list[str], limit: int = MAX_EVIDENCE) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _axis_brief_fit(brief: Brief, fields: dict[str, Any], text: str) -> JudgeAxis:
    """需求契合：关键词覆盖、渠道形态、硬性约束。"""
    keywords = [str(item).strip() for item in brief.keywords if str(item).strip()]
    hits = [word for word in keywords if word and word in text]
    coverage = (len(hits) / len(keywords)) if keywords else 1.0

    rule = channel_rule(brief.channel)
    limit = title_limit(brief.channel)
    title = str(fields.get("title") or "")
    title_ok = (not limit) or (0 < len(title) <= limit)

    constraints = [str(item).strip() for item in brief.constraints if str(item).strip()]
    # 硬性约束在文本里被显式回应的比例（用约束词自身做粗匹配）
    answered = [
        item
        for item in constraints
        if any(token and token in text for token in item.replace("，", " ").replace("、", " ").split())
    ]
    constraint_ratio = (len(answered) / len(constraints)) if constraints else 1.0

    audience_hit = bool(brief.audience) and any(
        token in text for token in brief.audience.replace("，", " ").split() if len(token) >= 2
    )

    score = 1.0 + coverage * 2.0 + (0.6 if title_ok else 0.0) + constraint_ratio * 1.0
    if audience_hit:
        score += 0.4
    if not title_ok:
        score -= 0.5

    rationale = (
        f"关键词命中 {len(hits)}/{len(keywords)}"
        + ("（未设置关键词，按满分计）" if not keywords else "")
        + f"；标题 {len(title)} 字"
        + (f"（超 {limit} 字上限）" if not title_ok else "（符合渠道上限）")
        + f"；硬性约束回应 {len(answered)}/{len(constraints)}"
        + ("；提到了目标受众" if audience_hit else "")
    )
    evidence = [f"命中关键词：{word}" for word in hits[:3]]
    evidence += [f"未覆盖关键词：{word}" for word in keywords if word not in hits][:2]
    evidence += [f"未回应约束：{item}" for item in constraints if item not in answered][:2]
    return JudgeAxis("brief_fit", AXIS_LABEL["brief_fit"], _clamp(score), AXIS_WEIGHTS["brief_fit"], rationale, evidence)


def _axis_compliance(brief: Brief, text: str) -> JudgeAxis:
    """合规安全：复用 A7 的词库与行业规则（同一事实来源，避免两套口径）。

    这里刻意调用 ``scan_compliance`` 而不是自己再写一遍分词扫描：
    评估结论与 A7 的门禁结论必须建立在同一份词库上，否则会出现
    「A7 说通过、评估说合规有问题」的自相矛盾。
    """
    scan = scan_compliance(text, brief.industry)
    blockers = [item for item in scan.hits if item.severity == "blocker"]
    majors = [item for item in scan.hits if item.severity == "major"]
    minors = [item for item in scan.hits if item.severity not in ("blocker", "major")]
    required = scan.required_failures

    score = 5.0 - len(blockers) * 2.0 - len(majors) * 0.8 - len(minors) * 0.3
    score -= min(1.2, len(required) * 0.4)
    rationale = (
        f"广告法扫描：阻断级 {len(blockers)}、重要 {len(majors)}、轻微 {len(minors)}"
        + f"；行业必备要素缺失 {len(required)} 项（{brief.industry}）"
    )
    evidence = [f"阻断用语：{item.term}（{item.law}）" for item in blockers][:3]
    evidence += [f"重要用语：{item.term}" for item in majors][:2]
    evidence += [f"缺少必备要素：{item.hint}" for item in required][:2]
    return JudgeAxis(
        "compliance", AXIS_LABEL["compliance"], _clamp(score), AXIS_WEIGHTS["compliance"],
        rationale, evidence,
    )


def _axis_structure(brief: Brief, fields: dict[str, Any]) -> JudgeAxis:
    """结构完整：标题 / 正文 / CTA / 标签四项齐备，且长度在渠道规范内。"""
    title = str(fields.get("title") or "")
    body = str(fields.get("body") or "")
    cta = str(fields.get("cta") or "")
    tags = [str(tag) for tag in (fields.get("hashtags") or []) if str(tag).strip()]

    limit = title_limit(brief.channel)

    parts = [bool(title.strip()), len(body.strip()) >= 80, bool(cta.strip()), len(tags) >= 2]
    score = 1.0 + sum(1 for flag in parts if flag) * 1.0
    if limit and 0 < len(title) <= limit:
        score += 0.5
    if len(body) > 2000:
        score -= 0.3

    missing = [
        name
        for name, ok in zip(("标题", "正文（≥80 字）", "行动号召", "话题标签（≥2 个）"), parts)
        if not ok
    ]
    rationale = (
        f"标题 {len(title)} 字、正文 {len(body)} 字、CTA {'有' if cta.strip() else '无'}、"
        f"标签 {len(tags)} 个"
        + (f"；缺少：{'、'.join(missing)}" if missing else "；四项齐备")
    )
    evidence = [f"缺少 {name}" for name in missing]
    return JudgeAxis(
        "structure", AXIS_LABEL["structure"], _clamp(score), AXIS_WEIGHTS["structure"],
        rationale, evidence,
    )


def _axis_brand_voice(brief: Brief, text: str) -> JudgeAxis:
    """品牌语气：品牌词出现、调性关键词命中。"""
    brand = brief.brand.strip()
    brand_hit = bool(brand) and brand in text

    tone_tokens = [
        token
        for token in brief.tone.replace("，", " ").replace("、", " ").replace("+", " ").split()
        if len(token) >= 2
    ]
    tone_hits = [token for token in tone_tokens if token in text]
    tone_ratio = (len(tone_hits) / len(tone_tokens)) if tone_tokens else 0.5

    score = 1.5 + (1.5 if brand_hit else 0.0) + tone_ratio * 2.0
    rationale = (
        f"品牌词{'已出现' if brand_hit else '未出现'}"
        + f"；调性关键词命中 {len(tone_hits)}/{len(tone_tokens) or '—'}"
    )
    evidence = [f"命中调性词：{token}" for token in tone_hits[:3]]
    if not brand_hit and brand:
        evidence.append(f"未出现品牌名「{brand}」")
    return JudgeAxis(
        "brand_voice", AXIS_LABEL["brand_voice"], _clamp(score), AXIS_WEIGHTS["brand_voice"],
        rationale, evidence,
    )


def _axis_fact_safety(upstream: dict[str, dict[str, Any]], text: str) -> JudgeAxis:
    """事实稳妥：优先采信 A6 的核查结论，再叠加「无来源数字」的启发式检测。"""
    fact = upstream.get("factcheck") or {}
    summary = fact.get("summary") if isinstance(fact.get("summary"), dict) else {}
    unverified = int(summary.get("unverified") or 0)
    exaggerated = int(summary.get("exaggerated") or 0)
    contradicted = int(summary.get("contradicted") or 0)
    risk = str(fact.get("risk_level") or "")

    numeric = [line for line in text.splitlines() if any(hint in line for hint in _NUMERIC_CLAIM_HINTS)]
    has_source = ("来源" in text) or ("数据来源" in text) or ("http" in text)

    score = 5.0 - unverified * 0.7 - exaggerated * 1.0 - contradicted * 1.5
    if numeric and not has_source:
        score -= min(1.0, len(numeric) * 0.25)
    if risk == "high":
        score -= 0.5
    elif risk == "low":
        score += 0.3

    rationale = (
        f"A6 结论：未验证 {unverified}、夸大 {exaggerated}、矛盾 {contradicted}"
        + (f"；风险等级 {risk}" if risk else "")
        + (f"；出现数字类表述 {len(numeric)} 处且未见来源标注" if numeric and not has_source else "")
    )
    evidence = [f"A6 风险项：{item}" for item in (fact.get("required_fixes") or [])[:3]]
    evidence += [f"含数字表述：{line[:60]}" for line in numeric[:2]]
    return JudgeAxis(
        "fact_safety", AXIS_LABEL["fact_safety"], _clamp(score), AXIS_WEIGHTS["fact_safety"],
        rationale, evidence,
    )


def _axis_appeal(fields: dict[str, Any]) -> JudgeAxis:
    """吸引力：标题长度、行动号召、互动引导、标签数量。"""
    title = str(fields.get("title") or "")
    cta = str(fields.get("cta") or "")
    body = str(fields.get("body") or "")
    tags = [str(tag) for tag in (fields.get("hashtags") or []) if str(tag).strip()]

    cta_hit = any(hint in (cta + body) for hint in _CTA_HINTS)
    hook = any(mark in title for mark in ("？", "?", "！", "!", "｜", "|", "：", ":"))
    length_ok = 8 <= len(title) <= 24

    score = 1.5 + (1.2 if cta_hit else 0.0) + (0.9 if hook else 0.0) + (0.8 if length_ok else 0.0)
    score += min(0.6, len(tags) * 0.15)

    rationale = (
        f"标题 {len(title)} 字"
        + ("（长度适中）" if length_ok else "（偏短或偏长）")
        + ("；含钩子标点" if hook else "；无钩子标点")
        + ("；含行动号召" if cta_hit else "；缺行动号召")
        + f"；标签 {len(tags)} 个"
    )
    evidence = []
    if not cta_hit:
        evidence.append("CTA / 正文缺少明确的行动引导词")
    if not length_ok:
        evidence.append(f"标题长度 {len(title)} 字不在 8–24 字区间")
    return JudgeAxis(
        "appeal", AXIS_LABEL["appeal"], _clamp(score), AXIS_WEIGHTS["appeal"], rationale, evidence,
    )


def _aggregate(axes: list[JudgeAxis], pass_threshold: float) -> tuple[float, Verdict, float]:
    weight_sum = sum(axis.weight for axis in axes) or 1.0
    weighted = sum(axis.score * axis.weight for axis in axes) / weight_sum
    total = max(0.0, min(100.0, weighted / 5.0 * 100.0))
    if total >= pass_threshold:
        verdict: Verdict = "pass"
    elif total < pass_threshold * 0.8:
        verdict = "reject"
    else:
        verdict = "review"

    # 置信度：分数离阈值越远越有把握（0.55 ~ 0.95）
    distance = abs(total - pass_threshold) / max(1.0, pass_threshold)
    confidence = max(0.55, min(0.95, 0.7 + distance * 0.5))
    return total, verdict, confidence


def judge_offline(
    brief: Brief,
    upstream: dict[str, dict[str, Any]],
    *,
    pass_threshold: float = 75.0,
    kind: str = "final",
    revision: int = 0,
) -> JudgeReport:
    """确定性规则评估器（默认模式，零依赖、可离线、可回归）。"""
    fields = target_fields(upstream)
    text = target_text(upstream)
    if not text.strip():
        return JudgeReport(
            total=0.0,
            verdict="reject",
            mode="offline",
            model="rule-based",
            summary="没有可评估的正文产物（A4 草稿与 A5 定稿均缺失）",
            axes=[],
            issues=["缺少待评估文本"],
            suggestions=["先完成 A4 文案创作与 A5 编辑审校，再进行评估"],
            confidence=0.9,
            kind=kind,
            revision=revision,
        )

    axes = [
        _axis_brief_fit(brief, fields, text),
        _axis_compliance(brief, text),
        _axis_structure(brief, fields),
        _axis_brand_voice(brief, text),
        _axis_fact_safety(upstream, text),
        _axis_appeal(fields),
    ]
    total, verdict, confidence = _aggregate(axes, pass_threshold)

    weakest = sorted(axes, key=lambda axis: axis.score)[:2]
    issues = [f"[{axis.label}] {axis.rationale}" for axis in weakest if axis.score < 4.0]
    suggestions = [
        item
        for axis in axes
        for item in (
            [f"[{axis.label}] 补齐：{evidence}" for evidence in axis.evidence]
            if axis.score < 4.0
            else []
        )
    ][:MAX_EVIDENCE]

    summary = (
        f"综合 {total:.1f}/100（{verdict}）｜最弱维度："
        + "、".join(f"{axis.label} {axis.score:.1f}/5" for axis in weakest)
    )
    return JudgeReport(
        total=total,
        verdict=verdict,
        mode="offline",
        model="rule-based",
        summary=summary,
        axes=axes,
        issues=issues,
        suggestions=suggestions,
        confidence=confidence,
        kind=kind,
        revision=revision,
    )


# ------------------------------------------------------------------ #
# LLM 评估器（可选，失败回退离线）                                     #
# ------------------------------------------------------------------ #

JUDGE_SYSTEM = """你是资深内容质量评审官（LLM-as-a-Judge）。你的职责不是改写文案，而是**评分并给出证据**。

评分维度（每项 0-5 分，允许一位小数）：
- brief_fit：是否命中 Brief 的关键词、受众与硬性约束
- compliance：广告法与行业规则的合规安全度（绝对化用语、功效承诺、收益承诺）
- structure：标题 / 正文 / CTA / 话题标签是否齐备且长度合规
- brand_voice：品牌名与调性关键词是否体现
- fact_safety：数字与主张是否有来源，是否存在夸大
- appeal：标题吸引力、行动号召与互动引导

硬性规则：
- 每个维度必须给出 rationale（判分理由），证据不足时下调 confidence，而不是编造证据
- 只评不改：不得输出改写后的文案
- 只输出 JSON，不输出任何解释性文字"""

JUDGE_SCHEMA = """{
  "axes": [{"key": "brief_fit|compliance|structure|brand_voice|fact_safety|appeal",
            "score": 0.0, "rationale": "", "evidence": [""]}],
  "summary": "",
  "issues": [""],
  "suggestions": [""],
  "confidence": 0.0
}"""


def _normalize_llm_axes(data: dict[str, Any]) -> list[JudgeAxis]:
    raw = data.get("axes")
    raw = raw if isinstance(raw, list) else []
    by_key: dict[str, dict[str, Any]] = {}
    for item in raw:
        if isinstance(item, dict):
            by_key[str(item.get("key") or "").strip()] = item

    axes: list[JudgeAxis] = []
    for key, weight in AXIS_WEIGHTS.items():
        item = by_key.get(key) or {}
        raw_score = item.get("score")
        score = (
            float(raw_score)
            if isinstance(raw_score, (int, float)) and not isinstance(raw_score, bool)
            else 3.0
        )
        evidence = item.get("evidence")
        axes.append(
            JudgeAxis(
                key=key,
                label=AXIS_LABEL[key],
                score=_clamp(score),
                weight=weight,
                rationale=str(item.get("rationale") or "").strip(),
                evidence=_dedupe([str(x) for x in (evidence if isinstance(evidence, list) else [])]),
            )
        )
    return axes


def judge_with_llm(
    brief: Brief,
    upstream: dict[str, dict[str, Any]],
    *,
    pass_threshold: float = 75.0,
    kind: str = "final",
    revision: int = 0,
    timeout_ms: int | None = None,
    model: str | None = None,
) -> JudgeReport:
    """LLM-as-a-Judge：调用 OpenAI 兼容网关评分；任何异常回退到离线评估器。

    「回退」而不是「报错」是刻意的：评估是旁路能力，网关抖动不应该让评估流水线
    整体不可用，更不应该影响创作链路。回退事实会写进报告的 ``fallback`` 字段，
    使用者能明确看出这一份分数是规则评估器给的。
    """
    from ..llm.engine import chat
    from ..llm.json_utils import extract_json
    from ..llm.types import ChatMessage, LLMRequest

    text = target_text(upstream)
    if not text.strip():
        return judge_offline(
            brief, upstream, pass_threshold=pass_threshold, kind=kind, revision=revision
        )

    user = f"""【Brief】品牌：{brief.brand}｜产品：{brief.product}｜渠道：{brief.channel}｜行业：{brief.industry}
【传播目标】{brief.objective}｜目标受众：{brief.audience}｜调性要求：{brief.tone}
【关键词】{'、'.join(brief.keywords) or '（未指定）'}
【硬性约束】{'；'.join(brief.constraints) or '（未指定）'}
【上游审核结论】编辑质量分：{(upstream.get('edit') or {}).get('scorecard', {}).get('overall', '（无）')}｜事实风险：{(upstream.get('factcheck') or {}).get('risk_level', '（无）')}｜合规分：{(upstream.get('compliance') or {}).get('compliance_score', '（无）')}

【待评估文案】
{text}

请按维度评分，严格要求 JSON 结构如下：
{JUDGE_SCHEMA}"""

    try:
        response = chat(
            LLMRequest(
                purpose="judge.evaluate",
                messages=[
                    ChatMessage(role="system", content=JUDGE_SYSTEM),
                    ChatMessage(role="user", content=user),
                ],
                context={"brief": brief.model_dump(mode="json"), "upstream": upstream},
                json=True,
            )
        )
        parsed = extract_json(response.content)
        if not isinstance(parsed, dict):
            raise ValueError("评估模型返回内容无法解析为 JSON")
        axes = _normalize_llm_axes(parsed)
        if not axes:
            raise ValueError("评估模型未返回任何维度分数")
    except Exception as error:  # noqa: BLE001 - 评估失败必须可回退
        report = judge_offline(
            brief, upstream, pass_threshold=pass_threshold, kind=kind, revision=revision
        )
        report.fallback = True
        report.fallback_reason = f"{type(error).__name__}: {error}"[:200]
        report.summary = f"{report.summary}（LLM 评估不可用，已回退离线评估器）"
        log.warn(f"LLM 评估回退离线：{report.fallback_reason}")
        return report

    total, verdict, confidence = _aggregate(axes, pass_threshold)
    raw_conf = parsed.get("confidence")
    if isinstance(raw_conf, (int, float)) and not isinstance(raw_conf, bool):
        confidence = max(0.0, min(1.0, float(raw_conf)))
    weakest = sorted(axes, key=lambda axis: axis.score)[:2]
    return JudgeReport(
        total=total,
        verdict=verdict,
        mode="llm",
        model=response.model,
        summary=str(parsed.get("summary") or "").strip()
        or f"综合 {total:.1f}/100（{verdict}）｜最弱维度："
        + "、".join(f"{axis.label} {axis.score:.1f}/5" for axis in weakest),
        axes=axes,
        issues=_dedupe([str(item) for item in (parsed.get("issues") or [])]),
        suggestions=_dedupe([str(item) for item in (parsed.get("suggestions") or [])]),
        confidence=confidence,
        kind=kind,
        revision=revision,
    )


def evaluate(
    brief: Brief,
    upstream: dict[str, dict[str, Any]],
    *,
    mode: JudgeMode = "offline",
    pass_threshold: float = 75.0,
    kind: str = "final",
    revision: int = 0,
    model: str | None = None,
    timeout_ms: int | None = None,
) -> JudgeReport:
    """评估入口：按模式选择评估器，``llm`` 模式自带回退。"""
    if mode == "llm":
        return judge_with_llm(
            brief,
            upstream,
            pass_threshold=pass_threshold,
            kind=kind,
            revision=revision,
            model=model,
            timeout_ms=timeout_ms,
        )
    return judge_offline(
        brief, upstream, pass_threshold=pass_threshold, kind=kind, revision=revision
    )


__all__ = [
    "AXIS_LABEL",
    "AXIS_WEIGHTS",
    "JUDGE_SCHEMA",
    "JUDGE_SYSTEM",
    "RUBRIC_VERSION",
    "JudgeAxis",
    "JudgeMode",
    "JudgeReport",
    "Verdict",
    "evaluate",
    "judge_offline",
    "judge_with_llm",
    "target_fields",
    "target_text",
]
