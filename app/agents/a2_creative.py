"""A2 创意总监智能体（移植自 server/agents/a2-creative.ts）。"""

from __future__ import annotations

from typing import Any

from ..core.types import AgentResult
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
    memory_block,
    normalize_confidence,
    read_confidence,
    read_evidence,
    read_risks,
    system_prompt,
    vision_attachments,
)

META = AgentMeta(
    id="A2",
    name="创意总监智能体",
    role="创意总监",
    kind="producer",
    phase="CREATIVE",
    produces="creative_concept",
    description="基于策略提出 Big Idea、创意方向与调性指南，决定「怎么说才吸引人」",
    veto=False,
    capabilities=["Big Idea", "多方向创意", "调性指南", "案例参考"],
)

SYSTEM = system_prompt(
    META,
    """你的职责：
1. 基于策略简报提出 1 个 Big Idea 与 2-3 个可执行创意方向
2. 每个方向必须包含：切入角度、开场钩子、示例标题、推荐理由、潜在风险
3. 明确调性指南（该做什么、不该做什么）
4. 给出可参考的同类案例方向

硬性规则：
- 创意方向之间必须有实质差异，不能是同一方向的措辞变化
- 不得承诺效果、收益，不得使用绝对化用语
- 每个方向都要说明适配度与风险，不要把创意建议写成事实
- 只输出 JSON，不输出任何解释性文字

工具用法：
- direction_scoring 会对你给出的候选方向做五维启发式预评分（差异化/情绪张力/
  可实证性/渠道适配/可延展），**它是启发式排序而非投放数据**：
  可以参考它来调整推荐方向与 fit_score，但必须在 recommendation_reason 里
  说明你采纳或推翻它排序的理由，不得把分数当作业绩预测写进正文
- case_library / hook_patterns 给出的是同行业历史结构与案例方向，用于找差异空位；
  引用时不得声称是竞品实际投放数据
- web_search 返回带链接的外部案例时可在 reference_cases.source 里写明链接；
  若工具说明「本轮未启用联网」，则不得引用任何在线案例
- topic_dedupe 会把你的候选方向与品牌历史选题做词面查重（2-gram 重合度）：
  标为高重合的方向必须换切入角度，不要只改措辞；查重是词面比对，
  未报警不等于不撞车，你仍要主动避开明显同质的题
- hook_strength 可对每个方向的 sample_headline 做钩子强度预检：
  低分钩子先改写再给出，命中红线词的标题一律换掉""",
)

SCHEMA = """{
  "big_idea": {"title": "", "statement": "", "rationale": ""},
  "directions": [{"id": "D1", "name": "", "angle": "", "hook": "", "sample_headline": "", "rationale": "", "risk": "", "fit_score": 0}],
  "tone_guide": {"voice": "", "dos": [""], "donts": [""], "visual_suggestion": ""},
  "reference_cases": [{"name": "", "why": "", "source": ""}],
  "recommended_direction": "D1",
  "recommendation_reason": "",
  "confidence": 0.0,
  "risks": [""],
  "evidence": [{"claim": "", "source": "", "reliability": 0.0}]
}"""


def normalize(data: dict[str, Any]) -> dict[str, Any]:
    big_idea = as_obj(data.get("big_idea"))
    tone_guide = as_obj(data.get("tone_guide"))
    return {
        "big_idea": {
            "title": as_str(big_idea.get("title")),
            "statement": as_str(big_idea.get("statement")),
            "rationale": as_str(big_idea.get("rationale")),
        },
        "directions": [
            {
                "id": as_str(item.get("id"), f"D{index + 1}"),
                "name": as_str(item.get("name")),
                "angle": as_str(item.get("angle")),
                "hook": as_str(item.get("hook")),
                "sample_headline": as_str(item.get("sample_headline")),
                "rationale": as_str(item.get("rationale")),
                "risk": as_str(item.get("risk")),
                # fit_score 允许 0-1 或 0-100 两种量纲，统一归一到百分制
                "fit_score": normalize_confidence(item.get("fit_score"), 0.7) * 100,
            }
            for index, item in enumerate(as_obj_array(data.get("directions")))
        ],
        "tone_guide": {
            "voice": as_str(tone_guide.get("voice")),
            "dos": as_str_array(tone_guide.get("dos")),
            "donts": as_str_array(tone_guide.get("donts")),
            "visual_suggestion": as_str(tone_guide.get("visual_suggestion")),
        },
        "reference_cases": as_obj_array(data.get("reference_cases")),
        "recommended_direction": as_str(data.get("recommended_direction"), "D1"),
        "recommendation_reason": as_str(data.get("recommendation_reason")),
    }


def run(ctx: AgentRunContext) -> AgentResult:
    brief = ctx.brief
    strategy = ctx.upstream_of("strategy")
    house = as_obj(strategy.get("message_house"))
    audience = as_obj(strategy.get("audience_profile"))

    ctx.emit("基于策略简报推导 Big Idea 与创意方向")
    tools = run_agent_tools(ctx, META)

    user = f"""【创作 Brief】
品牌：{brief.brand}｜产品：{brief.product}｜渠道：{brief.channel}｜调性：{brief.tone}

【A1 策略简报】
核心主张：{as_str(house.get('proposition'))}
支撑点：{'；'.join(as_str_array(house.get('support_points')))}
利益点：{'；'.join(as_str_array(house.get('benefits')))}
受众痛点：{'；'.join(as_str_array(audience.get('pain_points')))}
使用场景：{'；'.join(as_str_array(audience.get('scenarios')))}
核心动机：{'；'.join(as_str_array(audience.get('motivations')))}

{memory_block(ctx)}{tools.prompt_block()}请给出 Big Idea、2-3 个创意方向与调性指南，严格要求 JSON 结构如下：
{SCHEMA}"""

    result = call_with_prompts(
        ctx,
        META,
        SYSTEM,
        user,
        "A2.creative",
        {"brief": brief.model_dump(mode="json"), "strategy": strategy, "memory": ctx.memory, "tools": tools.context()},
        # 创意方向从素材本身找切入（产品细节/使用场景），图像随本次调用发送
        images=vision_attachments(ctx, META.id),
    )
    content = normalize(result.data)
    directions = as_obj_array(content["directions"])
    recommended = as_str(content.get("recommended_direction"), "D1")

    ctx.emit(f"已产出 {len(directions)} 个创意方向，推荐 {recommended}")

    artifact = build_artifact(
        ctx,
        META,
        ArtifactDraft(
            type="creative_concept",
            title="创意概念与调性指南",
            content=content,
            text=content_to_text(content),
            tags=["创意", brief.channel],
        ),
    )

    return build_result(
        ctx,
        META,
        result.metrics,
        ResultDraft(
            summary=(
                f"Big Idea：「{as_str(as_obj(content['big_idea']).get('title'))}」；"
                f"共 {len(directions)} 个方向，推荐 {recommended}"
            ),
            artifacts=[artifact],
            confidence=read_confidence(result.data, 0.78),
            risks=read_risks(result.data),
            evidence=read_evidence(result.data),
            handoff={"to": "A3", "reason": "创意方向已收敛，请策划选题与内容大纲"},
        ),
    )


a2_creative = AgentDefinition(meta=META, run=run)
