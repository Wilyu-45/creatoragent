"""A8 视觉美术指导智能体的工具集。

视觉规范类工具：渠道画幅规格、风格库候选、视频脚本必要性判断，
以及**图像 Prompt 规范性 lint**（image_prompt_lint）。

``image_prompt_lint`` 做的是「生成前可做的那部分 lint」：A8 自己的
``image_prompts`` 在本次生成之后才存在，因此工具先 lint（a）上游创意里
那段视觉建议是否可执行、（b）主推文案里有哪些要素必须在画面里兑现，
并给出产出后逐条自查的规则清单。产出后的字段级复核由 A8 主流程按清单完成。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..knowledge.industry import channel_rule, industry_profile
from ..knowledge.video import needs_video_script
from ..knowledge.visual import channel_visual_spec, visual_styles_for
from .base import Tool, ToolOutcome, recommended_draft

if TYPE_CHECKING:
    from ..agents.base import AgentRunContext


def visual_spec(ctx: "AgentRunContext") -> ToolOutcome:
    """目标渠道的画幅与张数规格。"""
    brief = ctx.brief
    main_ratio, cover_ratio, shot_count = channel_visual_spec(brief.channel)
    lines = [
        f"主图画幅：{main_ratio}｜封面画幅：{cover_ratio}｜建议张数：{shot_count}",
        "（来源：内置渠道视觉规范——image_prompts 与 assets 必须遵循该规格）",
    ]
    return ToolOutcome(
        summary=f"「{brief.channel}」规格：{main_ratio} / {cover_ratio} / {shot_count} 张",
        detail="\n".join(lines),
        data={
            "channel": brief.channel,
            "main_ratio": main_ratio,
            "cover_ratio": cover_ratio,
            "shot_count": shot_count,
        },
    )


def style_library(ctx: "AgentRunContext") -> ToolOutcome:
    """按行业匹配度的视觉风格候选（含负面词与配色）。"""
    brief = ctx.brief
    styles = visual_styles_for(brief.industry)[:4]
    lines: list[str] = []
    for index, style in enumerate(styles, start=1):
        palette = "、".join(f"{color.name}{color.hex}" for color in style.palette[:3])
        lines.append(
            f"{index}. {style.name}｜情绪：{style.mood}｜构图：{style.composition}｜"
            f"光线：{style.lighting}｜配色：{palette}"
        )
    lines.append("（来源：内置视觉风格库——visual_direction 应从中选择或说明偏离理由）")
    return ToolOutcome(
        summary=f"检索到 {len(styles)} 个候选风格",
        detail="\n".join(lines),
        data={
            "styles": [
                {
                    "key": style.key,
                    "name": style.name,
                    "mood": style.mood,
                    "composition": style.composition,
                    "lighting": style.lighting,
                    "negative": style.negative,
                    "palette": [
                        {"name": c.name, "hex": c.hex, "usage": c.usage}
                        for c in style.palette
                    ],
                }
                for style in styles
            ]
        },
    )


def video_check(ctx: "AgentRunContext") -> ToolOutcome:
    """判断本任务是否需要输出视频脚本，并给出命中原因。"""
    brief = ctx.brief
    needed, reasons = needs_video_script(
        channel=brief.channel,
        deliverables=brief.deliverables,
        channel_format=channel_rule(brief.channel).format,
    )
    detail = (
        "判定：需要输出视频脚本｜原因：" + "；".join(reasons)
        if needed
        else "判定：无需视频脚本（图文形态即可完整交付）"
    )
    detail += "（来源：内置渠道形态规则）"
    return ToolOutcome(
        summary="需要视频脚本" if needed else "无需视频脚本",
        detail=detail,
        data={"needed": needed, "reasons": reasons},
    )


#: 可执行视觉维度：一段视觉描述至少覆盖 3 项才算「可投喂」，否则只是形容词堆叠
_VISUAL_DIMENSIONS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("风格", re.compile(r"纪实|极简|复古|胶片|电影感|插画|写实|手绘|3D|国潮|产品特写|生活方式")),
    ("光线", re.compile(r"自然光|侧光|逆光|柔光|硬光|窗光|散射光|顶光|补光|光线|光源")),
    ("构图", re.compile(r"构图|三分|居中|特写|俯拍|平视|仰拍|留白|对称|景深|前景|对角线")),
    ("色彩", re.compile(r"配色|色调|低饱和|高饱和|暖色|冷色|莫兰迪|色系")),
    ("画幅", re.compile(r"比例|竖版|横版|方图|\d\s*[:：]\s*\d")),
)

#: 空洞视觉词：只写这类词等于没给指令
_EMPTY_VISUAL_WORDS = (
    "高级感", "氛围感", "有质感", "大片感", "ins风", "治愈系", "轻奢", "有格调", "精致感",
)

#: 视觉层面禁止出现的素材指向（prompt 里出现即需人工确认授权/版权）
_FORBIDDEN_VISUAL_SUBJECTS = (
    "明星", "艺人", "代言人", "网红同款", "官方标志", "品牌logo", "品牌 logo",
    "动漫角色", "卡通形象", "二维码", "水印", "版权素材",
)

_NUMBER_CLAIM_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:[%％]|倍|天|小时|分钟)")


def image_prompt_lint(ctx: "AgentRunContext") -> ToolOutcome:
    """图像 Prompt 规范性 lint：上游视觉建议可执行性 + 文案必兑现要素 + 自查清单。"""
    from ..llm.json_utils import as_obj, as_str

    brief = ctx.brief
    creative = ctx.upstream_of("creative")
    suggestion = as_str(as_obj(creative.get("tone_guide")).get("visual_suggestion"))
    target, _ = recommended_draft(ctx)
    copy_text = f"{as_str(target.get('title'))}\n{as_str(target.get('body'))}"

    lines: list[str] = []
    data: dict[str, Any] = {}

    # 1) 上游视觉建议（A2 tone_guide.visual_suggestion）是否可执行
    if suggestion.strip():
        covered = [label for label, pattern in _VISUAL_DIMENSIONS if pattern.search(suggestion)]
        missing = [label for label, _ in _VISUAL_DIMENSIONS if label not in covered]
        empty_hits = [word for word in _EMPTY_VISUAL_WORDS if word in suggestion]
        forbidden = [word for word in _FORBIDDEN_VISUAL_SUBJECTS if word in suggestion]
        lines.append(
            f"上游视觉建议：覆盖 {len(covered)}/{len(_VISUAL_DIMENSIONS)} 个可执行维度"
            f"（已覆盖：{'、'.join(covered) or '无'}；缺：{'、'.join(missing) or '无'}）"
        )
        if empty_hits:
            lines.append(f"- 空洞形容词：{'、'.join(empty_hits)}——须替换为风格词 + 光线 + 构图")
        if forbidden:
            lines.append(f"- 指向受限素材：{'、'.join(forbidden)}——不得据此生成（版权/肖像风险）")
        data["upstream_suggestion"] = {
            "covered": covered,
            "missing": missing,
            "empty_words": empty_hits,
            "forbidden": forbidden,
        }
    else:
        lines.append("上游未给出视觉倾向，visual_direction 完全由你决定（须自行覆盖全部可执行维度）")
        data["upstream_suggestion"] = {}

    # 2) 主推文案里必须在画面里兑现的要素
    profile = industry_profile(brief.industry)
    scenes = [item for item in profile.scenarios if item in copy_text]
    pains = [item for item in profile.pain_points if item in copy_text]
    numbers = _NUMBER_CLAIM_RE.findall(copy_text)
    required: list[str] = []
    if brief.product and brief.product in copy_text:
        required.append(f"产品「{brief.product}」必须在画面中清晰可辨（文案已提到，画面不得缺失）")
    required.extend(f"场景「{item}」已在文案出现，画面须能还原该场景" for item in scenes[:3])
    required.extend(f"痛点「{item}」已在文案出现，画面不得暗示文案未声明的解决效果" for item in pains[:2])
    if numbers:
        required.append(
            f"文案含 {len(numbers)} 处数值表述（如 {numbers[0]}），画面不得用视觉暗示替代或放大该数值"
        )
    data["required_in_visual"] = required
    lines.append("文案→画面必兑现要素：")
    if required:
        lines.extend(f"- {item}" for item in required)
    else:
        lines.append("- （文案中未检出具体产品/场景/数值，画面要素由你按创意方向确定）")

    # 3) 产出后自查清单
    banned = "／".join(_FORBIDDEN_VISUAL_SUBJECTS[:6])
    lines.append(
        "产出后逐条自查（lint 规则）：① 每条 prompt 覆盖风格/光线/构图至少 3 项；"
        "② 每条 prompt 必须带 negative 与 aspect_ratio，且画幅与渠道规格一致；"
        f"③ 禁止出现「{banned}」类素材指向；"
        "④ 禁止空洞形容词单独成句；⑤ 画面不得呈现文案未声明的功效"
    )
    lines.append(
        "（来源：内置视觉规范 + 对上游文本的正则 lint——只覆盖「规范性」，"
        "画面创意好坏仍需你判断；命中项请写进 risks 或 copy_visual_check.conflicts）"
    )
    return ToolOutcome(
        summary=(
            f"lint 上游视觉建议覆盖 {len(data.get('upstream_suggestion', {}).get('covered', []))}"
            f"/{len(_VISUAL_DIMENSIONS)} 维度，文案必兑现 {len(required)} 项"
        ),
        detail="\n".join(lines),
        data=data,
    )


def asset_spec_check(ctx: "AgentRunContext") -> ToolOutcome:
    """Brief 自带素材与渠道画幅规格的**确定性**比对。

    素材的画幅是从文件头实测出来的（``app/core/assets.py``），不是猜的；
    因此「这张图能不能直接当封面用」是一个可计算的事实，不该由模型凭印象说。
    远程素材拿不到尺寸时如实标注「未知」，不做推断。
    """
    from ..core.assets import inventory

    brief = ctx.brief
    main_ratio, cover_ratio, _ = channel_visual_spec(brief.channel)
    data = inventory(brief.assets)
    lines: list[str] = []
    if not data["count"]:
        return ToolOutcome(
            summary="本次 Brief 未附带素材",
            detail=(
                "本次 Brief 未附带素材（无图片/视频/文档）——"
                "视觉方案须完全按文字 Brief 生成，不得声称「已有素材」或复用不存在的画面。"
            ),
            data={"count": 0, "items": []},
        )

    usable = [item for item in data["items"] if not item["issue"]]
    lines.append(
        f"Brief 附带 {data['count']} 条素材，可用 {data['usable']} 条；"
        f"渠道「{brief.channel}」规格：主图 {main_ratio}｜封面 {cover_ratio}"
    )
    for index, item in enumerate(data["items"], start=1):
        if item["issue"]:
            lines.append(f"{index}. {item['title'] or item['ref']}：不可用（{item['issue']}）")
            continue
        if not item["width"]:
            lines.append(
                f"{index}. {item['title'] or item['ref']}：画幅未知（"
                + ("远程素材本进程不下载，尺寸需生成时确认" if item["source"] == "remote" else "文件头未能解析出尺寸")
                + "）"
            )
            continue
        ratio = item["aspect_ratio"] or f"{item['width']}:{item['height']}"
        verdict = (
            f"与封面规格一致，可直接用作封面"
            if ratio == cover_ratio
            else f"与封面规格（{cover_ratio}）不一致，需重裁或留出安全区"
        )
        lines.append(f"{index}. {item['title'] or item['ref']}：{item['width']}×{item['height']}（{ratio}）——{verdict}")
    lines.append(
        "（来源：素材文件头实测尺寸 + 内置渠道视觉规范——以上为确定性结论，"
        f"请据此决定复用或重制；可复用素材请在 copy_visual_check 中说明一致性依据）"
    )
    return ToolOutcome(
        summary=f"{data['count']} 条素材，可用 {data['usable']} 条，已按「{cover_ratio}」封面规格逐条比对",
        detail="\n".join(lines),
        data={"count": data["count"], "usable": data["usable"], "items": data["items"]},
    )


TOOLS: list[Tool] = [
    Tool(
        name="visual_spec",
        description="目标渠道的画幅与张数规格",
        agent_ids=("A8",),
        handler=visual_spec,
    ),
    Tool(
        name="style_library",
        description="按行业匹配度检索视觉风格候选（含负面词与配色）",
        agent_ids=("A8",),
        handler=style_library,
    ),
    Tool(
        name="video_check",
        description="判断是否需要输出视频脚本",
        agent_ids=("A8",),
        handler=video_check,
    ),
    Tool(
        name="image_prompt_lint",
        description="图像 Prompt 规范性 lint（上游视觉建议 + 文案必兑现要素 + 自查清单）",
        agent_ids=("A8",),
        handler=image_prompt_lint,
    ),
    Tool(
        name="asset_spec_check",
        description="Brief 自带素材的类型/画幅与渠道规格逐条比对（尺寸为文件头实测值）",
        agent_ids=("A8",),
        handler=asset_spec_check,
    ),
]

__all__ = [
    "TOOLS",
    "visual_spec",
    "style_library",
    "video_check",
    "image_prompt_lint",
    "asset_spec_check",
]
