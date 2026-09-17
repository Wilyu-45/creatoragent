"""跨智能体的确定性计算与数据变换工具集。

对应《plan.md》2.2.3「让智能体能自行使用工具完成计算」的部分：把**量级换算、
日期推算、增长率**这类本可精确计算的事交给沙箱公式，而不是让模型「心算」——
模型心算的结果一处一个样，且无法复核。

三条纪律（与工具层总原则一致）：

* **不执行任意代码**——全部算式走 ``app.core.sandbox`` 的受限求值
  （AST 白名单，无属性访问 / 下标 / 导入），提示词注入拿不到宿主机执行面；
* **区间优先**——换算结论以区间呈现（经验基准本身是区间），
  不把推导值包装成承诺值；
* **如实标注**——换算写明「基于内部经验基准的推导，非平台真实数据」，
  缺失输入时说明「无法计算」而不是补一个看起来合理的数。

唯一**输出随时间变化**的工具是 ``publish_timeline``（排期本就该锚定当前时刻）。
它不影响黄金基线：Mock 离线引擎只消费 context 中的既有 key，不读工具产出。
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from ..core.clock import to_iso
from ..core.publisher import parse_slot_time
from ..core.sandbox import apply_formula, evaluate
from ..core.util import js_round
from ..knowledge.industry import publish_slots
from .a10_analyst import benchmark_for
from .base import Tool, ToolOutcome, recommended_draft

if TYPE_CHECKING:
    from ..agents.base import AgentRunContext

#: 曝光量级档位（内部推演用，非行业标准分档）
_IMPRESSION_STEPS = (10_000, 50_000, 100_000, 500_000)

#: 反推用的点击目标档位
_TARGET_CLICKS = (500, 1_000, 5_000)

#: 排期推算窗口：每天取前 N 个建议时段（其余时段见 ``publish_windows`` 工具）
_TIMELINE_DAYS = 5
_SLOTS_PER_DAY = 2

_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")

#: 中文数量单位 -> 倍率（``12,000`` 与 ``1.2 万`` 都要能读懂）
_UNIT_FACTORS: tuple[tuple[str, float], ...] = (
    ("亿", 1e8),
    ("万", 1e4),
    ("千", 1e3),
    ("k", 1e3),
    ("K", 1e3),
)


def _number(raw: Any) -> float | None:
    """从「约 12,000 次」「1.2 万」这类人工填写的文本里取数值；取不到返回 ``None``。"""
    text = str(raw or "").strip()
    if not text:
        return None
    cleaned = text.replace(",", "").replace("，", "")
    match = _NUMBER_RE.search(cleaned)
    if match is None:
        return None
    value = float(match.group())
    tail = cleaned[match.end() :]
    for unit, factor in _UNIT_FACTORS:
        if unit in tail:
            value *= factor
            break
    return value


def _fmt(value: float) -> str:
    return f"{value:,.0f}"


def _rate(value: float) -> str:
    return f"{value * 100:.2f}%"


def _has_actuals(ctx: "AgentRunContext") -> bool:
    actuals = ctx.upstream.get("actuals")
    return isinstance(actuals, dict) and bool(actuals)


# ------------------------------------------------------------------ #
# ① 曝光 → 点击/互动 量级换算（A1 写目标、A10 写预估共用同一口径）      #
# ------------------------------------------------------------------ #


def funnel_sensitivity(ctx: "AgentRunContext") -> ToolOutcome:
    """按渠道经验基准把曝光档位换算成点击/互动量级，并给出点击目标的反推。

    A1 写 ``objectives``、A10 写 ``predicted`` 都要给量级。若两边各自心算，
    同一份 Brief 会得出两套互相打架的数字；这里统一由沙箱公式推导。
    """
    if _has_actuals(ctx):
        return ToolOutcome(
            summary="复盘模式：以回填数据为准，跳过曝光档位换算",
            data={"mode": "review"},
        )

    brief = ctx.brief
    bench = benchmark_for(brief.channel)
    ctr_low, ctr_high = bench["ctr"][0] / 100, bench["ctr"][1] / 100
    eng_low, eng_high = bench["engagement"][0] / 100, bench["engagement"][1] / 100

    rows: list[dict[str, Any]] = []
    for impressions in _IMPRESSION_STEPS:
        rows.append(
            {
                "impressions": impressions,
                "clicks": [
                    js_round(apply_formula("clicks", {"impressions": impressions, "ctr": ctr_low})),
                    js_round(apply_formula("clicks", {"impressions": impressions, "ctr": ctr_high})),
                ],
                "engagement": [
                    js_round(evaluate("impressions * rate", {"impressions": impressions, "rate": eng_low})),
                    js_round(evaluate("impressions * rate", {"impressions": impressions, "rate": eng_high})),
                ],
            }
        )

    reverse: list[dict[str, Any]] = []
    for target in _TARGET_CLICKS:
        # CTR 越低所需曝光越多，因此区间两端要反过来
        reverse.append(
            {
                "target_clicks": target,
                "impressions": [
                    js_round(evaluate("target / ctr", {"target": target, "ctr": ctr_high})),
                    js_round(evaluate("target / ctr", {"target": target, "ctr": ctr_low})),
                ],
            }
        )

    lines = [
        f"「{brief.channel}」经验基准：CTR {ctr_low * 100:.1f}%–{ctr_high * 100:.1f}%｜"
        f"互动率 {eng_low * 100:.1f}%–{eng_high * 100:.1f}%（互动与点击同为**曝光口径**）",
        "曝光 → 点击 / 互动（确定性换算 clicks = impressions × ctr）：",
    ]
    for row in rows:
        lines.append(
            f"- 曝光 {_fmt(row['impressions'])} → 点击 {_fmt(row['clicks'][0])}–{_fmt(row['clicks'][1])} 次"
            f"｜互动 {_fmt(row['engagement'][0])}–{_fmt(row['engagement'][1])} 次"
        )
    lines.append("反推：点击目标 ÷ CTR = 所需曝光（CTR 越高所需曝光越少）：")
    for row in reverse:
        lines.append(
            f"- 目标 {_fmt(row['target_clicks'])} 次点击 → 需曝光 "
            f"{_fmt(row['impressions'][0])}–{_fmt(row['impressions'][1])}"
        )
    lines.append(
        "（来源：渠道经验基准 × 沙箱公式确定性换算——**非平台真实数据**；"
        "目标量级请写成区间，并声明不构成效果承诺）"
    )

    return ToolOutcome(
        summary=(
            f"曝光 1 万–50 万档位的点击/互动换算"
            f"（CTR {ctr_low * 100:.1f}%–{ctr_high * 100:.1f}%，反推点击目标所需曝光）"
        ),
        detail="\n".join(lines),
        data={
            "channel": brief.channel,
            "ctr": [bench["ctr"][0], bench["ctr"][1]],
            "engagement": [bench["engagement"][0], bench["engagement"][1]],
            "funnel": rows,
            "reverse": reverse,
        },
    )


# ------------------------------------------------------------------ #
# ② 建议时段 → 具体发布时刻（A3 排期、A9 渠道适配）                    #
# ------------------------------------------------------------------ #


def publish_timeline(ctx: "AgentRunContext") -> ToolOutcome:
    """把「建议时段」换算成未来若干天的**具体到期时刻**，供排期直接落地。

    时刻口径与发布网关（``core/publisher.compute_due_at``）**保持一致**，
    否则工具给智能体的时间会和后台真正投递的时间对不上。
    """
    brief = ctx.brief
    slots = publish_slots(brief.channel)
    now = datetime.now(timezone.utc)

    items: list[dict[str, Any]] = []
    for offset in range(_TIMELINE_DAYS):
        for slot in slots[:_SLOTS_PER_DAY]:
            parsed = parse_slot_time(slot)
            if parsed is None:
                continue
            hour, minute = parsed
            moment = (now + timedelta(days=offset)).replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
            if moment <= now:
                continue
            items.append(
                {
                    "slot": slot,
                    "due_at": to_iso(moment),
                    "in_hours": round((moment - now).total_seconds() / 3600, 1),
                }
            )

    if not items:
        return ToolOutcome(
            summary=f"「{brief.channel}」无可解析的发布时段",
            detail="（内置时段里没有可解析的 HH:MM，排期只能由运营手动指定）",
            data={"slots": slots, "items": []},
        )

    lines = [
        f"「{brief.channel}」建议时段：{'；'.join(slots)}",
        f"未来 {_TIMELINE_DAYS} 天内可执行排期（自当前时刻推算，每天前 "
        f"{_SLOTS_PER_DAY} 个时段，共 {len(items)} 条）：",
    ]
    for item in items:
        lines.append(f"- {item['due_at']}｜{item['slot']}（约 {item['in_hours']} 小时后）")
    lines.append(
        "（来源：内置经验时段 + 确定性日期推算，口径与发布网关 compute_due_at 一致"
        "——时刻按 UTC 解释；节假日与大促顺延未纳入，需运营确认）"
    )

    return ToolOutcome(
        summary=f"推算未来 {_TIMELINE_DAYS} 天共 {len(items)} 个可执行发布时刻",
        detail="\n".join(lines),
        data={"channel": brief.channel, "slots": slots, "items": items},
    )


# ------------------------------------------------------------------ #
# ③ 回填数据 → 实测派生指标与预估对照（A10 复盘）                      #
# ------------------------------------------------------------------ #


def _compare(actual: float, predicted: dict[str, Any]) -> dict[str, Any] | None:
    """实测值 vs 预估区间（low/mid/high）：给出结论与相对中值的偏离。

    预估口径可能是「5.0」这样的百分数，也可能是「0.05」这样的小数，
    以中值是否大于 1 作为判据统一成比值——这是启发式，因此区间缺失时
    退回「与中值比较」，绝不把猜出来的区间写进结论。
    """
    low, mid, high = (_number(predicted.get(key)) for key in ("low", "mid", "high"))
    if mid in (None, 0.0):
        return None
    if mid > 1:
        low = low / 100 if low is not None else None
        mid = mid / 100
        high = high / 100 if high is not None else None
    interval_known = low is not None and high is not None and (low < mid or high > mid)
    low = mid if low is None else low
    high = mid if high is None else high
    delta = apply_formula("growth", {"current": actual, "previous": mid})
    if actual < low:
        verdict = "低于预期"
    elif actual > high:
        verdict = "超预期"
    else:
        verdict = "落在预估区间内"
    return {
        "predicted_low": low,
        "predicted_mid": mid,
        "predicted_high": high,
        "interval_known": interval_known,
        "actual": actual,
        "delta": round(delta, 4),
        "verdict": verdict,
    }


def actuals_audit(ctx: "AgentRunContext") -> ToolOutcome:
    """把运营回填的数值换算成实测派生指标，并与发布前预估对照。

    复盘最容易出问题的地方是「口径」：回填里给的是原始量（曝光/点击/互动），
    结论里要的是比率（CTR/互动率/转化率）。若让模型自行相除，
    除错了也没人发现；这里全部由沙箱计算，并把算不出的项如实列出。
    """
    raw = ctx.upstream.get("actuals")
    if not isinstance(raw, dict) or not raw:
        return ToolOutcome(summary="当前为发布前预估，无回填数据可核算", data={})

    exposure = _number(raw.get("exposure"))
    clicks = _number(raw.get("clicks"))
    interactions = _number(raw.get("interactions"))
    conversions = _number(raw.get("conversions"))

    derived: dict[str, float] = {}
    if exposure and clicks is not None:
        derived["ctr"] = evaluate("clicks / impressions", {"clicks": clicks, "impressions": exposure})
    if exposure and interactions is not None:
        derived["engagement"] = evaluate(
            "interactions / impressions", {"interactions": interactions, "impressions": exposure}
        )
    if clicks and conversions is not None:
        derived["conversion"] = evaluate(
            "conversions / clicks", {"conversions": conversions, "clicks": clicks}
        )

    missing = [
        name
        for name, present in (
            ("曝光", exposure is not None),
            ("点击", clicks is not None),
            ("互动", interactions is not None),
            ("转化", conversions is not None),
        )
        if not present
    ]

    lines = [
        "回填原始量：" + "｜".join(
            f"{name} {_fmt(value)}" if value is not None else f"{name} 未填写"
            for name, value in (
                ("曝光", exposure),
                ("点击", clicks),
                ("互动", interactions),
                ("转化", conversions),
            )
        ),
    ]
    if derived:
        lines.append(
            "实测派生指标（沙箱换算）："
            + "｜".join(
                f"{label} {_rate(derived[key])}"
                for key, label in (("ctr", "CTR"), ("engagement", "互动率"), ("conversion", "转化率"))
                if key in derived
            )
        )

    comparison = None
    predicted = ctx.upstream_of("predicted")
    has_baseline = bool(predicted) and "ctr" in derived
    if has_baseline:
        comparison = _compare(derived["ctr"], dict(predicted.get("ctr") or {}))
    if comparison is not None:
        interval = (
            f"（区间 {_rate(comparison['predicted_low'])}–{_rate(comparison['predicted_high'])}）"
            if comparison["interval_known"]
            else "（预估未给区间，仅对中值）"
        )
        lines.append(
            f"vs 发布前预估 CTR：中值 {_rate(comparison['predicted_mid'])}{interval}"
            f" → 实际 {_rate(comparison['actual'])}，"
            f"相对中值 {comparison['delta'] * 100:+.1f}%，结论：{comparison['verdict']}"
        )
    elif has_baseline:
        lines.append("有回填 CTR 但预估缺少 ctr.mid，**无法对照**：请只做绝对表现解读，不要编造对照数据")
    else:
        lines.append("无发布前预估基线或 CTR 无法计算，**不做对照**（不得编造对照数据）")

    if missing:
        lines.append(f"未填写项：{'、'.join(missing)}——相关指标无法计算，请勿以估算值替代")
    lines.append(
        "（来源：回填数值 × 沙箱公式（ctr = clicks / impressions、"
        "growth = (current - previous) / previous）——回填由人工录入，口径可能不一致；"
        "「落在区间内 / 超预期」只针对预估口径，不等于达成策略目标）"
    )

    return ToolOutcome(
        summary=(
            f"实测 "
            + "｜".join(
                f"{label} {_rate(derived[key])}"
                for key, label in (("ctr", "CTR"), ("engagement", "互动率"), ("conversion", "转化率"))
                if key in derived
            )
            if derived
            else "回填数据不足以换算任何派生指标"
        ),
        detail="\n".join(lines),
        data={
            "raw": {
                "exposure": exposure,
                "clicks": clicks,
                "interactions": interactions,
                "conversions": conversions,
            },
            "derived": {key: round(value, 6) for key, value in derived.items()},
            "comparison": comparison,
            "missing": missing,
        },
    )


# ------------------------------------------------------------------ #
# ④ Brief 关键词布局核对（A5 对 Brief 契约负责）                       #
# ------------------------------------------------------------------ #


def _count(text: str, keyword: str) -> int:
    return text.lower().count(keyword.lower()) if keyword.strip() else 0


def brief_keyword_audit(ctx: "AgentRunContext") -> ToolOutcome:
    """Brief 指定关键词在标题/正文/标签中的布局情况（确定性字符串检索）。

    没有任何环节核对「用户要求布局的词到底写了没有」，而这类漏词是最常见、
    也最容易被模型自己忽略的返工原因。这里只做**字面检索**并如实说明其边界：
    同义改写与近义表达不会命中。
    """
    keywords = [item.strip() for item in ctx.brief.keywords if item.strip()]
    if not keywords:
        return ToolOutcome(
            summary="Brief 未指定关键词，跳过布局核对",
            data={"keywords": []},
        )

    target, versions = recommended_draft(ctx)
    if not target:
        return ToolOutcome(
            summary="尚无文案草稿，关键词布局无法核对",
            detail=f"Brief 要求布局 {len(keywords)} 个关键词，但上游没有可取用的文案版本。",
            data={"keywords": keywords, "version": None},
        )

    from ..llm.json_utils import as_str, as_str_array

    title = as_str(target.get("title"))
    body = as_str(target.get("body"))
    cta = as_str(target.get("cta"))
    hashtags = as_str_array(target.get("hashtags"))
    tag_text = " ".join(hashtags)
    version = as_str(target.get("id"), "V1")

    items: list[dict[str, Any]] = []
    for keyword in keywords:
        in_title = _count(title, keyword)
        in_body = _count(body, keyword)
        in_cta = _count(cta, keyword)
        in_tags = _count(tag_text, keyword)
        if in_title + in_body + in_cta:
            state = "已布局"
        elif in_tags:
            state = "仅出现在标签"
        else:
            state = "缺失"
        items.append(
            {
                "keyword": keyword,
                "title": in_title,
                "body": in_body,
                "cta": in_cta,
                "tags": in_tags,
                "state": state,
            }
        )

    lines = [f"Brief 要求布局 {len(keywords)} 个关键词；核对主推版本 {version}（共 {len(versions)} 版）："]
    for item in items:
        lines.append(
            f"- 「{item['keyword']}」：标题 {item['title']} 次、正文 {item['body']} 次、"
            f"CTA {item['cta']} 次、标签 {item['tags']} 个 → {item['state']}"
        )
    missing = [item["keyword"] for item in items if item["state"] == "缺失"]
    tag_only = [item["keyword"] for item in items if item["state"] == "仅出现在标签"]
    if missing:
        lines.append(f"缺失项：{'、'.join(missing)}——需在 review items 中给出应插入的位置")
    if tag_only:
        lines.append(f"仅标签命中：{'、'.join(tag_only)}——标签不算正文布局，需在正文中至少出现一次")
    lines.append(
        "（来源：对标题/正文/CTA/标签的确定性字面检索——同义词与近义改写不会命中，"
        "命中也不代表语句通顺；关键词堆砌另由可读性检查把关）"
    )

    return ToolOutcome(
        summary=f"Brief 关键词 {len(keywords)} 个：{len(keywords) - len(missing)} 个已布局"
        + (f"、{len(missing)} 个缺失" if missing else ""),
        detail="\n".join(lines),
        data={"version": version, "keywords": items, "missing": missing, "tag_only": tag_only},
    )


TOOLS: list[Tool] = [
    Tool(
        name="funnel_sensitivity",
        description="曝光档位到点击/互动的确定性换算与点击目标反推（区间形式）",
        agent_ids=("A1", "A10"),
        handler=funnel_sensitivity,
    ),
    Tool(
        name="publish_timeline",
        description="把建议时段推算为未来若干天的具体发布时刻（与发布网关同口径）",
        agent_ids=("A3", "A9"),
        handler=publish_timeline,
    ),
    Tool(
        name="actuals_audit",
        description="回填数据到实测派生指标的确定性换算，并与发布前预估对照",
        agent_ids=("A10",),
        handler=actuals_audit,
    ),
    Tool(
        name="brief_keyword_audit",
        description="Brief 指定关键词在标题/正文/CTA/标签中的布局核对",
        agent_ids=("A5",),
        handler=brief_keyword_audit,
    ),
]

__all__ = [
    "TOOLS",
    "actuals_audit",
    "brief_keyword_audit",
    "funnel_sensitivity",
    "publish_timeline",
]