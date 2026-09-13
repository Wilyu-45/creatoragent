"""A8 视觉美术指导智能体。

对应《creator.md》4.8 与 5 的流程位置：A7 品牌合规之后、A9 渠道适配之前。
产出 ``visual_brief``：视觉方向、色彩方案、配图 Prompt、分镜脚本与版式建议，
并**校验文案与视觉是否一致**（creator.md 明确要求的能力）。

注意：A8 不产出图片本身，只产出「可执行的视觉指令」，
这样在没有图像生成能力时链路依然完整，接入文生图时只需消费 ``image_prompts``。
"""

from __future__ import annotations

from typing import Any

from ..core.types import AgentResult
from ..knowledge.industry import channel_rule
from ..knowledge.video import needs_video_script
from ..knowledge.visual import DEFAULT_STYLE, channel_visual_spec
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
    read_confidence,
    read_evidence,
    read_risks,
    system_prompt,
)

META = AgentMeta(
    id="A8",
    name="视觉美术指导智能体",
    role="美术指导",
    kind="producer",
    phase="VISUAL_ADAPT",
    produces="visual_brief",
    description="给出视觉风格、色彩与版式方向，产出配图 Prompt 与分镜脚本，并校验图文一致性",
    veto=False,
    capabilities=["视觉风格", "色彩方案", "配图 Prompt", "分镜脚本", "图文一致性"],
)

SYSTEM = system_prompt(
    META,
    """你的职责：
1. 依据创意方向与渠道形态，给出视觉风格、情绪、构图与光线方案
2. 输出一组可直接投喂图像模型的 Prompt（必须含负面词与画幅）
3. 为需要动态内容的渠道输出分镜脚本
4. 给出封面与内页的版式、字体层级建议
5. 校验文案与视觉方向是否一致，明确指出冲突点

硬性规则：
- 视觉描述必须可执行（风格词 + 光线 + 构图），禁止只写「高级感」这类空洞形容词
- 不得承诺能生成特定真实人物、受版权保护或平台禁止的素材
- 产品外观、材质、功效的画面表现必须与文案表述一致，
  禁止用画面暗示文案未声明的功效（否则会绕过 A7 合规门禁）
- 只输出 JSON，不输出任何解释性文字""",
)

SCHEMA = """{
  "visual_direction": {
    "style": "", "mood": "", "composition": "", "lighting": "",
    "palette": [{"name": "", "hex": "#000000", "usage": ""}],
    "rationale": ""
  },
  "assets": {"main_ratio": "", "cover_ratio": "", "shot_count": 0},
  "image_prompts": [{"id": "IMG1", "usage": "", "scene": "", "prompt": "", "negative": "", "aspect_ratio": ""}],
  "storyboard": [{"shot": "", "duration": "", "visual": "", "copy_overlay": "", "transition": ""}],
  "layout": {"cover": "", "body": "", "typography": ""},
  "copy_visual_check": {"aligned": true, "conflicts": [""], "notes": [""]},
  "confidence": 0.0,
  "risks": [""],
  "evidence": [{"claim": "", "source": "", "reliability": 0.0}]
}"""


def normalize(data: dict[str, Any], channel: str) -> dict[str, Any]:
    main_ratio, cover_ratio, shot_count = channel_visual_spec(channel)
    direction = as_obj(data.get("visual_direction"))
    assets = as_obj(data.get("assets"))
    check = as_obj(data.get("copy_visual_check"))

    palette: list[dict[str, Any]] = []
    for item in as_obj_array(direction.get("palette")):
        palette.append(
            {
                "name": as_str(item.get("name"), "未命名"),
                "hex": as_str(item.get("hex"), "#000000"),
                "usage": as_str(item.get("usage")),
            }
        )
    if not palette:
        palette = [
            {"name": c.name, "hex": c.hex, "usage": c.usage}
            for c in DEFAULT_STYLE.palette
        ]

    return {
        "channel": channel,
        "visual_direction": {
            "style": as_str(direction.get("style"), DEFAULT_STYLE.name),
            "mood": as_str(direction.get("mood"), DEFAULT_STYLE.mood),
            "composition": as_str(direction.get("composition"), DEFAULT_STYLE.composition),
            "lighting": as_str(direction.get("lighting"), DEFAULT_STYLE.lighting),
            "palette": palette,
            "rationale": as_str(direction.get("rationale")),
        },
        "assets": {
            "main_ratio": as_str(assets.get("main_ratio"), main_ratio),
            "cover_ratio": as_str(assets.get("cover_ratio"), cover_ratio),
            "shot_count": int(assets.get("shot_count") or shot_count),
        },
        "image_prompts": [
            {
                "id": as_str(item.get("id"), f"IMG{index + 1}"),
                "usage": as_str(item.get("usage")),
                "scene": as_str(item.get("scene")),
                "prompt": as_str(item.get("prompt")),
                "negative": as_str(item.get("negative"), DEFAULT_STYLE.negative),
                "aspect_ratio": as_str(item.get("aspect_ratio"), main_ratio),
            }
            for index, item in enumerate(as_obj_array(data.get("image_prompts")))
        ],
        "storyboard": [
            {
                "shot": as_str(item.get("shot"), f"镜头 {index + 1}"),
                "duration": as_str(item.get("duration")),
                "visual": as_str(item.get("visual")),
                "copy_overlay": as_str(item.get("copy_overlay")),
                "transition": as_str(item.get("transition")),
            }
            for index, item in enumerate(as_obj_array(data.get("storyboard")))
        ],
        "layout": {
            "cover": as_str(as_obj(data.get("layout")).get("cover")),
            "body": as_str(as_obj(data.get("layout")).get("body")),
            "typography": as_str(as_obj(data.get("layout")).get("typography")),
        },
        "copy_visual_check": {
            "aligned": bool(check.get("aligned", True)),
            "conflicts": as_str_array(check.get("conflicts")),
            "notes": as_str_array(check.get("notes")),
        },
    }


VIDEO_SCHEMA = """{
  "format": "short_video_script",
  "aspect_ratio": "9:16",
  "duration_seconds": 45,
  "hook": "",
  "shots": [{"shot": 1, "role": "钩子|痛点|方案|佐证|转化",
             "start_second": 0, "end_second": 3, "duration_seconds": 3,
             "visual": "", "voiceover": "", "subtitle": "", "camera": "", "intent": ""}],
  "voiceover": [{"shot": 1, "start_second": 0, "line": ""}],
  "subtitles": [{"shot": 1, "start_second": 0, "end_second": 3, "text": ""}],
  "cta": "",
  "production_notes": [""],
  "compliance_notes": [""],
  "confidence": 0.0,
  "risks": [""],
  "evidence": [{"claim": "", "source": "", "reliability": 0.0}]
}"""


def normalize_video_script(data: dict[str, Any]) -> dict[str, Any]:
    """规整视频脚本：保证分镜/口播/字幕三段结构齐备且时间轴单调。"""
    shots: list[dict[str, Any]] = []
    for index, item in enumerate(as_obj_array(data.get("shots")), start=1):
        start = as_num(item.get("start_second"), 0)
        end = as_num(item.get("end_second"), start + 1)
        shots.append(
            {
                "shot": int(as_num(item.get("shot"), index)),
                "role": as_str(item.get("role"), "方案"),
                "start_second": int(start),
                "end_second": int(max(end, start + 1)),
                "duration_seconds": int(max(1, end - start)),
                "visual": as_str(item.get("visual")),
                "voiceover": as_str(item.get("voiceover")),
                "subtitle": as_str(item.get("subtitle")),
                "camera": as_str(item.get("camera")),
                "intent": as_str(item.get("intent")),
            }
        )

    return {
        "format": as_str(data.get("format"), "short_video_script"),
        "aspect_ratio": as_str(data.get("aspect_ratio"), "9:16"),
        "duration_seconds": int(as_num(data.get("duration_seconds"), 45)),
        "shot_count": len(shots),
        "hook": as_str(data.get("hook")),
        "shots": shots,
        "voiceover": as_obj_array(data.get("voiceover")),
        "subtitles": as_obj_array(data.get("subtitles")),
        "cta": as_str(data.get("cta")),
        "production_notes": as_str_array(data.get("production_notes")),
        "compliance_notes": as_str_array(data.get("compliance_notes")),
    }


def run(ctx: AgentRunContext) -> AgentResult:
    brief = ctx.brief
    creative = ctx.upstream_of("creative")
    draft = ctx.upstream_of("draft")

    ctx.emit("基于创意方向与渠道形态制定视觉方案，并校验图文一致性")

    recommended = as_str(draft.get("recommended_version"), "V1")
    target = next(
        (v for v in as_obj_array(draft.get("versions")) if as_str(v.get("id")) == recommended),
        {},
    )
    tone_guide = as_obj(creative.get("tone_guide"))
    big_idea = as_obj(creative.get("big_idea"))

    user = f"""【渠道】{brief.channel}｜【行业】{brief.industry}
【产品】{brief.brand} {brief.product}｜【调性】{brief.tone}
【创意主题】{as_str(big_idea.get('title'))}
【既定视觉倾向】{as_str(tone_guide.get('visual_suggestion'), '（未指定）')}
【主推文案标题】{as_str(target.get('title'))}
【主推文案正文】
{as_str(target.get('body'))[:600]}

请输出视觉方案（含视觉方向、色彩、配图 Prompt、分镜、版式与图文一致性校验），
严格要求 JSON 结构如下：
{SCHEMA}"""

    result = call_with_prompts(
        ctx,
        META,
        SYSTEM,
        user,
        "A8.visual",
        {
            "brief": brief.model_dump(mode="json"),
            "creative": creative,
            "draft": draft,
            "revision": ctx.revision,
        },
    )

    content = normalize(result.data, brief.channel)
    prompts = content["image_prompts"]
    storyboard = content["storyboard"]
    check = content["copy_visual_check"]

    ctx.emit(
        "视觉方案完成",
        {"image_prompts": len(prompts), "shots": len(storyboard), "aligned": check["aligned"]},
    )

    artifact = build_artifact(
        ctx,
        META,
        ArtifactDraft(
            type="visual_brief",
            title="视觉美术指导",
            content=content,
            text=content_to_text(content),
            tags=["视觉", brief.channel],
        ),
    )

    # 视频脚本：只在需要时产出（短视频渠道 / 交付物点名要脚本 / 渠道形态含视频特征）。
    # **刻意不做「一律产出」**：图文渠道硬塞脚本只会制造噪声，
    # 也会让「交付物是否符合 Brief」失去可判断性。
    artifacts = [artifact]
    needs_script, script_reasons = needs_video_script(
        channel=brief.channel,
        deliverables=brief.deliverables,
        channel_format=channel_rule(brief.channel).format,
    )
    if needs_script:
        ctx.emit("识别为视频形态，追加输出结构化视频脚本", {"reasons": script_reasons})
        script_result = call_with_prompts(
            ctx,
            META,
            SYSTEM,
            f"""【渠道】{brief.channel}｜【产品】{brief.brand} {brief.product}
【主推文案标题】{as_str(target.get('title'))}
【主推文案正文】
{as_str(target.get('body'))[:600]}

请输出**可直接开拍的短视频脚本**，包含钩子、分镜（每镜含时长/画面/口播/字幕）、
行动号召与拍摄要点，严格要求 JSON 结构如下：
{VIDEO_SCHEMA}""",
            "A8.video_script",
            {
                "brief": brief.model_dump(mode="json"),
                "strategy": ctx.upstream_of("strategy"),
                "creative": creative,
                "plan": ctx.upstream_of("plan"),
                "draft": draft,
                "edit": ctx.upstream_of("edit"),
                "revision": ctx.revision,
            },
            schema=VIDEO_SCHEMA,
        )
        script_content = normalize_video_script(script_result.data)
        # 结构化脚本作为独立产物落黑板：A9 需要按平台时长二次裁剪，
        # 前端需要按结构渲染，黄金数据集需要断言「短视频 Brief 必须出带时长与口播的脚本」。
        script_artifact = build_artifact(
            ctx,
            META,
            ArtifactDraft(
                type="video_script",
                title="视频脚本",
                content=script_content,
                text=content_to_text(script_content),
                tags=["视频", "脚本", brief.channel],
            ),
        )
        artifacts.append(script_artifact)

    conflicts = check["conflicts"]
    risks = read_risks(result.data)
    if conflicts:
        risks = [f"图文一致性待确认：{item}" for item in conflicts[:2]] + risks

    return build_result(
        ctx,
        META,
        result.metrics,
        ResultDraft(
            summary=(
                f"输出「{content['visual_direction']['style']}」视觉方向、"
                f"{len(prompts)} 条配图 Prompt"
                + (f"与 {len(storyboard)} 镜分镜" if storyboard else "")
                + (
                    f"；附带 {len(script_content.get('shots') or [])} 镜视频脚本"
                    if needs_script
                    else ""
                )
                + ("；图文一致性校验通过" if check["aligned"] else f"；{len(conflicts)} 项图文冲突待确认")
            ),
            artifacts=artifacts,
            confidence=read_confidence(result.data, 0.74),
            risks=risks,
            evidence=read_evidence(result.data),
            needs_human_review=not check["aligned"],
            handoff={"to": "A9", "reason": "视觉方案就绪，进入渠道适配"},
        ),
    )


a8_art_director = AgentDefinition(meta=META, run=run)

__all__ = ["META", "normalize", "run", "a8_art_director"]
