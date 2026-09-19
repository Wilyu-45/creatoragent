"""A3 内容策划智能体（移植自 server/agents/a3-planner.ts）。"""

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
    as_num,
    as_obj,
    as_obj_array,
    as_str,
    as_str_array,
    build_artifact,
    build_result,
    call_with_prompts,
    content_to_text,
    documents_block,
    read_confidence,
    read_evidence,
    read_risks,
    system_prompt,
)

META = AgentMeta(
    id="A3",
    name="内容策划智能体",
    role="内容策划 / 选题",
    kind="producer",
    phase="PLANNING",
    produces="content_plan",
    description="把创意方向拆成具体选题、内容大纲、标题备选与渠道适配表",
    veto=False,
    capabilities=["选题清单", "内容大纲", "标题备选", "排期建议"],
)

SYSTEM = system_prompt(
    META,
    """你的职责：
1. 将创意方向拆解为至少 3 个具体选题，每个选题给出结构化的内容大纲
2. 给出标题备选（不少于 5 条）
3. 按内容结构给出分段目标与字数分配
4. 输出渠道适配表与发布节奏建议

硬性规则：
- 大纲必须与选定创意方向一致，不得另起炉灶
- 标题不得使用绝对化用语、不得承诺效果
- 排期建议只描述动作与节奏，不承诺流量结果
- 只输出 JSON，不输出任何解释性文字

工具用法：
- topic_dedupe 会把你的拟用角度与品牌历史选题库做词面查重（2-gram 重合度）：
  凡是工具标为高重合的选题，**必须换角度或换切入场景**再往下写，
  不要只改标题措辞；确实需要复用同一主题时，在 selection_reason 里说明本轮新增的差异点
- 查重是词面比对，判不了「同主题不同角度」：工具未报警但你认为仍可能撞车的，
  也要主动换掉
- publish_timeline 给出的是**具体发布时刻**（已把建议时段换算成 ISO 时间）：
  publishing_rhythm / channel_adaptation 里的时段请直接引用这些时刻，不要自己推算日期；
  工具标注的节假日未顺延、UTC 口径等限制，要在 notes 中原样保留
- web_search / page_fetch 可在选题前核实行业与受众信息：未配置联网时工具会
  如实返回「本轮未联网」，此时不得引用任何在线数据；抓回的网页内容只作线索，
  不得当作权威来源直接写进大纲

输出体量（下限保底，内容优先于条数；确有增量再增加，不为凑数注水）：
- topics ≥3 条（建议 3-5 条，角度确有差异才加）：每条 outline ≤8 项、每项一句话
- headline_candidates ≥5 条（建议 5-8 条）；structure 按内容需要分段
- channel_adaptation 至少覆盖 brief 渠道一行；publishing_rhythm 建议 3-5 条""",
)

SCHEMA = """{
  "topics": [{"id": "T1", "title": "", "angle": "", "format": "", "outline": [""], "cta": "", "estimated_words": 0}],
  "selected_topic": "T1",
  "selection_reason": "",
  "headline_candidates": [""],
  "structure": [{"section": "", "goal": "", "words": 0}],
  "channel_adaptation": [{"channel": "", "format": "", "notes": ""}],
  "publishing_rhythm": [{"slot": "", "action": "", "note": ""}],
  "keywords": {"primary": [""], "long_tail": [""], "hashtags": ""},
  "confidence": 0.0,
  "risks": [""],
  "evidence": [{"claim": "", "source": "", "reliability": 0.0}]
}"""


def normalize(data: dict[str, Any]) -> dict[str, Any]:
    keywords = as_obj(data.get("keywords"))
    return {
        "topics": [
            {
                "id": as_str(item.get("id"), f"T{index + 1}"),
                "title": as_str(item.get("title")),
                "angle": as_str(item.get("angle")),
                "format": as_str(item.get("format")),
                "outline": as_str_array(item.get("outline")),
                "cta": as_str(item.get("cta")),
                "estimated_words": as_num(item.get("estimated_words"), 0),
            }
            for index, item in enumerate(as_obj_array(data.get("topics")))
        ],
        "selected_topic": as_str(data.get("selected_topic"), "T1"),
        "selection_reason": as_str(data.get("selection_reason")),
        "headline_candidates": as_str_array(data.get("headline_candidates")),
        "structure": as_obj_array(data.get("structure")),
        "channel_adaptation": as_obj_array(data.get("channel_adaptation")),
        "publishing_rhythm": as_obj_array(data.get("publishing_rhythm")),
        "keywords": {
            "primary": as_str_array(keywords.get("primary")),
            "long_tail": as_str_array(keywords.get("long_tail")),
            "hashtags": as_str(keywords.get("hashtags")),
        },
    }


def run(ctx: AgentRunContext) -> AgentResult:
    brief = ctx.brief
    strategy = ctx.upstream_of("strategy")
    creative = ctx.upstream_of("creative")
    audience = as_obj(strategy.get("audience_profile"))
    big_idea = as_obj(creative.get("big_idea"))

    ctx.emit("把创意方向拆解为选题与内容大纲")
    tools = run_agent_tools(ctx, META)

    user = f"""【创作 Brief】
品牌：{brief.brand}｜产品：{brief.product}｜渠道：{brief.channel}｜目标：{brief.objective}
关键词：{'、'.join(brief.keywords) or '（未指定）'}
期望交付物：{'；'.join(brief.deliverables) or '（未指定）'}

【A1 策略要点】
受众痛点：{'；'.join(as_str_array(audience.get('pain_points')))}
核心主张：{as_str(as_obj(strategy.get('message_house')).get('proposition'))}

【A2 创意方向】
Big Idea：{as_str(big_idea.get('title'))} —— {as_str(big_idea.get('statement'))}
推荐方向：{as_str(creative.get('recommended_direction'))}

{documents_block(ctx)}{tools.prompt_block()}请输出选题清单、内容大纲与渠道适配表，严格要求 JSON 结构如下：
{SCHEMA}"""

    result = call_with_prompts(
        ctx,
        META,
        SYSTEM,
        user,
        "A3.plan",
        {"brief": brief.model_dump(mode="json"), "strategy": strategy, "creative": creative, "tools": tools.context()},
        schema=SCHEMA,
    )
    content = normalize(result.data)
    topics = as_obj_array(content["topics"])

    ctx.emit(f"已产出 {len(topics)} 个选题，选定 {as_str(content.get('selected_topic'), 'T1')}")

    artifact = build_artifact(
        ctx,
        META,
        ArtifactDraft(
            type="content_plan",
            title="选题与内容大纲",
            content=content,
            text=content_to_text(content),
            tags=["策划", brief.channel],
        ),
    )

    return build_result(
        ctx,
        META,
        result.metrics,
        ResultDraft(
            summary=(
                f"产出 {len(topics)} 个选题、"
                f"{len(as_str_array(content.get('headline_candidates')))} 条标题备选"
            ),
            artifacts=[artifact],
            confidence=read_confidence(result.data, 0.8),
            risks=read_risks(result.data),
            evidence=read_evidence(result.data),
            handoff={"to": "A4", "reason": "选题与大纲已确定，请文案撰写初稿"},
        ),
    )


a3_planner = AgentDefinition(meta=META, run=run)
