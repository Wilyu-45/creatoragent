"""A1 策略与洞察智能体（移植自 server/agents/a1-strategy.ts）。"""

from __future__ import annotations

from typing import Any

from ..core.types import AgentResult
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
    memory_block,
    read_confidence,
    read_evidence,
    read_risks,
    system_prompt,
)

META = AgentMeta(
    id="A1",
    name="策略与洞察智能体",
    role="策略总监 / 用户研究",
    kind="producer",
    phase="STRATEGY",
    produces="strategy_brief",
    description="分析受众、场景、竞品与传播目标，输出核心信息屋与内容 KPI 建议",
    veto=False,
    capabilities=["受众洞察", "核心信息屋", "渠道优先级", "证据标注"],
)

SYSTEM = system_prompt(
    META,
    """你的职责：
1. 分析目标受众：人群特征、核心痛点、使用场景、购买动机、决策阻力
2. 构建核心信息屋：品牌主张（proposition）、支撑点、利益点、证据
3. 确定内容目标（曝光/互动/转化/教育/信任）及衡量口径
4. 给出渠道优先级权重与内容基调建议

硬性规则：
- 严格区分「事实」「假设」「创意建议」，不得把假设写成事实
- 不得编造调研数据、用户数量、百分比或来源；无法确认的信息必须放入 risks
- 受众结论要说明推断依据
- 只输出 JSON，不输出任何解释性文字""",
)

SCHEMA = """{
  "audience_profile": {"segment": "", "pain_points": [""], "scenarios": [""], "motivations": [""], "objections": [""]},
  "message_house": {"proposition": "", "support_points": [""], "benefits": [""], "evidence": [{"type": "", "status": "", "note": ""}]},
  "objectives": [{"type": "", "metric": "", "target": ""}],
  "channel_priority": [{"channel": "", "weight": 0.6, "why": ""}],
  "tone_guide": {"keywords": [""], "avoid": [""]},
  "key_takeaways": [""],
  "confidence": 0.0,
  "risks": [""],
  "evidence": [{"claim": "", "source": "", "reliability": 0.0}]
}"""


def normalize(data: dict[str, Any], fallback_audience: str) -> dict[str, Any]:
    audience = as_obj(data.get("audience_profile"))
    house = as_obj(data.get("message_house"))
    tone_guide = as_obj(data.get("tone_guide"))
    return {
        "audience_profile": {
            "segment": as_str(audience.get("segment"), fallback_audience),
            "pain_points": as_str_array(audience.get("pain_points")),
            "scenarios": as_str_array(audience.get("scenarios")),
            "motivations": as_str_array(audience.get("motivations")),
            "objections": as_str_array(audience.get("objections")),
        },
        "message_house": {
            "proposition": as_str(house.get("proposition")),
            "support_points": as_str_array(house.get("support_points")),
            "benefits": as_str_array(house.get("benefits")),
            "evidence": as_obj_array(house.get("evidence")),
        },
        "objectives": as_obj_array(data.get("objectives")),
        "channel_priority": as_obj_array(data.get("channel_priority")),
        "tone_guide": {
            "keywords": as_str_array(tone_guide.get("keywords")),
            "avoid": as_str_array(tone_guide.get("avoid")),
        },
        "key_takeaways": as_str_array(data.get("key_takeaways")),
    }


def run(ctx: AgentRunContext) -> AgentResult:
    brief = ctx.brief
    ctx.emit("开始分析受众、场景与传播目标")

    user = f"""【创作 Brief】
品牌：{brief.brand}
产品/服务：{brief.product}
传播目标：{brief.objective}
目标受众：{brief.audience}
主渠道：{brief.channel}
内容调性：{brief.tone}
所属行业：{brief.industry}
关键词：{'、'.join(brief.keywords) or '（未指定）'}
硬性约束：{'；'.join(brief.constraints) or '（未指定）'}
补充说明：{brief.notes or '（无）'}

{memory_block(ctx)}请输出策略简报，严格要求 JSON 结构如下：
{SCHEMA}"""

    result = call_with_prompts(
        ctx,
        META,
        SYSTEM,
        user,
        "A1.strategy",
        {"brief": brief.model_dump(mode="json"), "memory": ctx.memory},
    )
    content = normalize(result.data, brief.audience)

    ctx.emit(
        "策略简报已生成",
        {"pain_points": len(as_str_array(as_obj(content["audience_profile"]).get("pain_points")))},
    )

    artifact = build_artifact(
        ctx,
        META,
        ArtifactDraft(
            type="strategy_brief",
            title="策略简报",
            content=content,
            text=content_to_text(content),
            tags=["策略", brief.industry, brief.channel],
        ),
    )

    return build_result(
        ctx,
        META,
        result.metrics,
        ResultDraft(
            summary=(
                "完成受众洞察与核心信息屋，主张："
                f"「{as_str(as_obj(content['message_house']).get('proposition'))}」"
            ),
            artifacts=[artifact],
            confidence=read_confidence(result.data, 0.8),
            risks=read_risks(result.data),
            evidence=read_evidence(result.data),
            handoff={"to": "A2", "reason": "策略已产出，请创意总监确认创意方向"},
        ),
    )


a1_strategy = AgentDefinition(meta=META, run=run)
