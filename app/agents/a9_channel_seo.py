"""A9 渠道运营与 SEO 智能体。

对应《creator.md》4.9 与 5 的流程位置：A8 视觉之后、A10 复盘之前。
产出 ``channel_adaptation``：多平台版本、关键词布局、发布策略与 A/B 方案。

与其他智能体的边界
------------------
A4 负责「写内容」，A9 负责「让内容在该渠道被看见」：改写标题以适配平台截断规则、
布局搜索关键词、给出发布时段与 A/B 测试方案。**A9 不改动正文事实与合规表述**，
因此不参与门禁环，只在 ``channel_checklist`` 中报告适配风险。
"""

from __future__ import annotations

from typing import Any

from ..core.types import AgentResult
from ..knowledge.industry import channel_rule, publish_slots, seo_pattern, title_limit
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
    id="A9",
    name="渠道运营与 SEO 智能体",
    role="运营 / SEO",
    kind="producer",
    phase="CHANNEL_ADAPT",
    produces="channel_adaptation",
    description="按各平台规则适配标题与标签，布局搜索关键词，输出发布策略与 A/B 测试方案",
    veto=False,
    capabilities=["渠道适配", "关键词布局", "发布策略", "A/B 方案"],
)

SYSTEM = system_prompt(
    META,
    """你的职责：
1. 按目标渠道规则改写标题、标签与关键词布局（标题必须符合平台截断上限）
2. 给出该渠道的搜索意图判断与关键词分层（主词 / 长尾词）
3. 规划发布节奏与时段，输出 A/B 测试方案
4. 逐条核对渠道规范清单，指出未满足项

硬性规则：
- 只做「适配」，不得改变正文的事实、数据、承诺与合规表述
- 标题字数不得超过平台上限，超出必须给出压缩后的版本
- 关键词不得堆砌：同一关键词在标题中出现 1 次即可，正文自然嵌入
- 只输出 JSON，不输出任何解释性文字

工具用法：
- tag_quality_audit 会对你上一轮给出的标签做客观审计（数量区间、重复、宽泛度、
  长度、红线词、关键词覆盖）：**凡是工具标出的问题项，必须整改后再定稿**，
  审计结论要逐条反映到 channel_checklist 的 status 里
- 工具只能判「可判定的部分」（数量/重复/长度/红线词），
  「大词/垂类词/长尾词」的结构配比与搜索意图匹配仍需你自己判断并在 seo 中说明
- publish_timeline 已把建议时段换算成**具体时刻**（与发布网关同一口径）：
  publish_plan 的 slot 直接落到这些时刻上，不要自行推算日期；
  工具标注的限制（按 UTC 解释、节假日未顺延）要在 note 中原样保留""",
)

SCHEMA = """{
  "primary_channel": "",
  "platforms": [{
    "channel": "", "format": "", "title": "", "body": "",
    "hashtags": [""], "keywords": [""], "publish_slot": "", "notes": ""
  }],
  "seo": {
    "primary_keywords": [""], "long_tail": [""], "search_intent": "",
    "difficulty": "low|medium|high",
    "placement": [{"position": "", "keyword": "", "note": ""}],
    "density_hint": ""
  },
  "publish_plan": [{"slot": "", "action": "", "note": ""}],
  "ab_tests": [{"hypothesis": "", "variant_a": "", "variant_b": "", "metric": ""}],
  "channel_checklist": [{"rule": "", "status": "pass|warn|fail", "note": ""}],
  "confidence": 0.0,
  "risks": [""],
  "evidence": [{"claim": "", "source": "", "reliability": 0.0}]
}"""


def _normalize_platform(item: dict[str, Any]) -> dict[str, Any]:
    channel = as_str(item.get("channel"), "小红书")
    title = as_str(item.get("title"))
    limit = title_limit(channel)
    return {
        "channel": channel,
        "format": as_str(item.get("format"), channel_rule(channel).format),
        "title": title,
        "body": as_str(item.get("body")),
        "hashtags": as_str_array(item.get("hashtags")),
        "keywords": as_str_array(item.get("keywords")),
        "publish_slot": as_str(item.get("publish_slot")),
        # 标题长度是硬约束，由代码计算而非交给模型自述
        "title_length": len(title),
        "title_limit": limit,
        "title_ok": len(title) <= limit,
        "notes": as_str(item.get("notes")),
    }


def normalize(data: dict[str, Any], brief_channel: str) -> dict[str, Any]:
    primary = as_str(data.get("primary_channel"), brief_channel)
    seo = as_obj(data.get("seo"))
    pattern = seo_pattern(primary)

    return {
        "primary_channel": primary,
        "platforms": [
            _normalize_platform(item) for item in as_obj_array(data.get("platforms"))
        ],
        "seo": {
            "primary_keywords": as_str_array(seo.get("primary_keywords")),
            "long_tail": as_str_array(seo.get("long_tail")),
            "search_intent": as_str(seo.get("search_intent"), pattern.search_intent),
            "difficulty": as_str(seo.get("difficulty"), pattern.difficulty),
            "placement": [
                {
                    "position": as_str(item.get("position")),
                    "keyword": as_str(item.get("keyword")),
                    "note": as_str(item.get("note")),
                }
                for item in as_obj_array(seo.get("placement"))
            ],
            "density_hint": as_str(
                seo.get("density_hint"), "主关键词出现 1-2 次即可，避免堆砌触发平台限流"
            ),
        },
        "publish_plan": [
            {
                "slot": as_str(item.get("slot")),
                "action": as_str(item.get("action")),
                "note": as_str(item.get("note")),
            }
            for item in as_obj_array(data.get("publish_plan"))
        ],
        "ab_tests": [
            {
                "hypothesis": as_str(item.get("hypothesis")),
                "variant_a": as_str(item.get("variant_a")),
                "variant_b": as_str(item.get("variant_b")),
                "metric": as_str(item.get("metric")),
            }
            for item in as_obj_array(data.get("ab_tests"))
        ],
        "channel_checklist": [
            {
                "rule": as_str(item.get("rule")),
                "status": as_str(item.get("status"), "pass"),
                "note": as_str(item.get("note")),
            }
            for item in as_obj_array(data.get("channel_checklist"))
        ],
    }


def run(ctx: AgentRunContext) -> AgentResult:
    brief = ctx.brief
    draft = ctx.upstream_of("draft")
    plan = ctx.upstream_of("plan")
    strategy = ctx.upstream_of("strategy")

    ctx.emit(f"按 {brief.channel} 规则适配标题与标签，并布局搜索关键词")
    tools = run_agent_tools(ctx, META)

    recommended = as_str(draft.get("recommended_version"), "V1")
    target = next(
        (v for v in as_obj_array(draft.get("versions")) if as_str(v.get("id")) == recommended),
        {},
    )
    rule = channel_rule(brief.channel)
    pattern = seo_pattern(brief.channel)
    slots = publish_slots(brief.channel)

    user = f"""【主渠道】{brief.channel}｜【行业】{brief.industry}｜【目标】{brief.objective}
【受众】{brief.audience}｜【关键词】{'、'.join(brief.keywords) or '（未指定）'}
【渠道形态规范】{rule.format}｜{rule.length_hint}｜标题：{rule.title_style}
【标签策略】{rule.hashtag_policy}
【搜索意图参考】{pattern.search_intent}
【建议时段】{'；'.join(slots)}
【内容策划中的关键词布局】{as_str(as_obj(plan.get('keywords')).get('hashtags'))}
【主推文案标题】{as_str(target.get('title'))}
【主推文案正文】
{as_str(target.get('body'))[:600]}

{tools.prompt_block()}请输出渠道适配方案，严格要求 JSON 结构如下：
{SCHEMA}"""

    result = call_with_prompts(
        ctx,
        META,
        SYSTEM,
        user,
        "A9.channel",
        {
            "brief": brief.model_dump(mode="json"),
            "strategy": strategy,
            "plan": plan,
            "draft": draft,
            "revision": ctx.revision,
            "tools": tools.context(),
        },
    )

    content = normalize(result.data, brief.channel)
    platforms = content["platforms"]
    checklist = content["channel_checklist"]
    failed = [item for item in checklist if item["status"] == "fail"]
    warned = [item for item in checklist if item["status"] == "warn"]
    over_limit = [p for p in platforms if not p["title_ok"]]

    ctx.emit(
        "渠道适配完成",
        {
            "platforms": len(platforms),
            "failed": len(failed),
            "title_overflow": len(over_limit),
        },
    )

    artifact = build_artifact(
        ctx,
        META,
        ArtifactDraft(
            type="channel_adaptation",
            title="渠道适配与发布策略",
            content=content,
            text=content_to_text(content),
            tags=["渠道适配", brief.channel],
        ),
    )

    risks = read_risks(result.data)
    if over_limit:
        risks = [
            f"{item['channel']} 标题 {item['title_length']} 字超出上限 {item['title_limit']} 字"
            for item in over_limit[:2]
        ] + risks
    if failed:
        risks = [f"渠道规范未满足：{item['rule']}" for item in failed[:2]] + risks

    return build_result(
        ctx,
        META,
        result.metrics,
        ResultDraft(
            summary=(
                f"完成 {len(platforms)} 个平台的渠道适配与 "
                f"{len(content['seo']['long_tail'])} 条长尾词布局"
                + (f"；{len(failed)} 项渠道规范未满足" if failed else "；渠道规范全部满足")
            ),
            artifacts=[artifact],
            confidence=read_confidence(result.data, 0.76),
            risks=risks,
            evidence=read_evidence(result.data),
            needs_human_review=bool(failed) or bool(over_limit),
            handoff={"to": "A10", "reason": "渠道版本就绪，进入效果预估"},
        ),
    )


a9_channel_seo = AgentDefinition(meta=META, run=run)

__all__ = ["META", "normalize", "run", "a9_channel_seo"]
