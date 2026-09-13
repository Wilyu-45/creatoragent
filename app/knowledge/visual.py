"""视觉方向知识库（服务 A8 视觉美术指导智能体）。

设计取舍
--------
生产环境应接入品牌 VI 手册 + 图库/生成模型；这里提供**可离线运行**的风格模板，
目的是让 A8 在没有任何外部依赖时也能产出结构化、可被 A9/前端消费的视觉方案，
而不是返回一段无法验证的形容词。

模板只描述「风格、色彩、构图、光线、Prompt 片段」这些**可复用的方法论**，
不涉及任何具体品牌资产，因此不会与真实品牌调性冲突。
"""

from __future__ import annotations

from dataclasses import dataclass, field

# ------------------------------------------------------------------ #
# 数据结构                                                            #
# ------------------------------------------------------------------ #


@dataclass(frozen=True)
class PaletteColor:
    name: str
    hex: str
    usage: str


@dataclass(frozen=True)
class VisualStyle:
    key: str
    name: str
    mood: str
    composition: str
    lighting: str
    palette: list[PaletteColor] = field(default_factory=list)
    prompt_fragments: list[str] = field(default_factory=list)
    negative: str = ""
    best_for: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ #
# 风格库                                                              #
# ------------------------------------------------------------------ #

VISUAL_STYLES: list[VisualStyle] = [
    VisualStyle(
        key="documentary",
        name="真实场景纪实",
        mood="松弛、可信、不像广告",
        composition="主体偏置三分线，保留生活化背景杂物，避免绝对对称与摆拍",
        lighting="自然侧光，窗边或户外散射光，允许轻微过曝营造通透感",
        palette=[
            PaletteColor("暖米", "#F2ECE3", "背景与纸张质感"),
            PaletteColor("雾霾蓝", "#7C93A6", "辅助色与文字底色"),
            PaletteColor("焦糖", "#B9764A", "强调色，用于价格/按钮"),
            PaletteColor("墨黑", "#23211F", "正文文字"),
        ],
        prompt_fragments=[
            "photorealistic lifestyle photography",
            "natural window light, soft shadows",
            "slightly imperfect real-world environment",
            "35mm lens, shallow depth of field",
        ],
        negative="over-retouched skin, plastic texture, stock-photo smile, cluttered text overlay",
        best_for=["消费品", "食品饮料", "美妆个护"],
    ),
    VisualStyle(
        key="minimal",
        name="极简产品特写",
        mood="克制、精密、有参数感",
        composition="主体居中或黄金分割，大面积留白，几何线条对齐",
        lighting="柔光箱硬边高光 + 渐变背景，突出材质与工艺细节",
        palette=[
            PaletteColor("雪白", "#FAFAF8", "背景与留白"),
            PaletteColor("石墨灰", "#3A3D42", "产品主体与标题"),
            PaletteColor("信号蓝", "#2F6FED", "关键参数强调"),
            PaletteColor("浅银", "#D9DCE1", "分隔线与次要信息"),
        ],
        prompt_fragments=[
            "studio product photography, seamless softbox lighting",
            "macro detail of material texture, high micro-contrast",
            "clean gradient background, generous negative space",
            "ultra sharp, commercial grade",
        ],
        negative="busy background, harsh reflection, distorted proportions, watermark",
        best_for=["科技数码", "企业服务", "消费品"],
    ),
    VisualStyle(
        key="dataviz",
        name="成分与数据可视化",
        mood="理性、透明、专业",
        composition="信息层级三栏布局，图表与实拍各占一半，避免纯文字堆叠",
        lighting="均匀平光，强调标签与数字的可读性",
        palette=[
            PaletteColor("纸白", "#FFFFFF", "信息底板"),
            PaletteColor("深蓝", "#1F3A5F", "标题与坐标轴"),
            PaletteColor("薄荷绿", "#3FAF8F", "正向数据与达标项"),
            PaletteColor("琥珀橙", "#E08A2E", "风险提示与待验证项"),
        ],
        prompt_fragments=[
            "editorial infographic photography, flat even lighting",
            "clean annotation callouts and measurement marks",
            "lab glassware or ingredient close-up",
            "high legibility, technical documentation style",
        ],
        negative="illegible micro text, misleading chart axis, decorative glitter",
        best_for=["美妆个护", "医疗健康", "企业服务", "教育培训"],
    ),
    VisualStyle(
        key="narrative",
        name="人物情绪叙事",
        mood="温暖、有代入感、不完美感",
        composition="人物视线方向留白，跟随动作而非正对镜头，制造抓拍感",
        lighting="逆光轮廓 + 面部补光，形成柔和层次",
        palette=[
            PaletteColor("暖杏", "#F6E3D5", "肤色与环境"),
            PaletteColor("青灰", "#5E6B72", "背景与阴影"),
            PaletteColor("砖红", "#C25E4C", "情绪强调色"),
            PaletteColor("奶油", "#FFF4E8", "文字底色"),
        ],
        prompt_fragments=[
            "candid documentary portrait, subject looking away from camera",
            "backlit rim light with soft facial fill",
            "authentic micro-expressions, slight motion blur",
            "warm color grading, film grain",
        ],
        negative="direct eye contact with stiff pose, HDR glow, exaggerated emotion",
        best_for=["教育培训", "美妆个护", "医疗健康"],
    ),
    VisualStyle(
        key="brand",
        name="品牌资产质感",
        mood="沉稳、有分量、长期主义",
        composition="版式优先：主标题/副标题/价值三支柱形成清晰竖向节奏",
        lighting="低反差环境光，重点靠版式与字体重量而非光影",
        palette=[
            PaletteColor("深墨", "#14161A", "主背景"),
            PaletteColor("米白", "#EFE9DE", "主标题与正文"),
            PaletteColor("古金", "#B9975B", "品牌强调与分割"),
            PaletteColor("石板", "#4A5058", "次要信息"),
        ],
        prompt_fragments=[
            "premium brand key visual, editorial layout",
            "matte texture background, subtle paper grain",
            "typography-driven composition with clear hierarchy",
            "muted, timeless color grading",
        ],
        negative="neon gradient, discount badge, crowded badge clutter",
        best_for=["企业服务", "PR稿", "官网"],
    ),
]

DEFAULT_STYLE: VisualStyle = VISUAL_STYLES[0]


def visual_styles_for(industry: str) -> list[VisualStyle]:
    """按行业匹配度排序返回风格候选（匹配到的排在前面，顺序稳定）。"""

    def score(style: VisualStyle) -> int:
        for target in style.best_for:
            if target == industry or target in industry or industry in target:
                return 1
        return 0

    return sorted(VISUAL_STYLES, key=score, reverse=True)


# ------------------------------------------------------------------ #
# 渠道 → 画幅 / 张数约定                                              #
# ------------------------------------------------------------------ #

#: 渠道 → (主画幅, 封面画幅, 建议张数/镜头数)
CHANNEL_VISUAL_SPEC: dict[str, tuple[str, str, int]] = {
    "小红书": ("3:4", "3:4", 7),
    "抖音": ("9:16", "9:16", 5),
    "公众号": ("16:9", "2.35:1", 4),
    "知乎": ("16:9", "16:9", 3),
    "电商详情页": ("1:1", "1:1", 6),
    "官网": ("16:9", "16:9", 3),
    "PR稿": ("16:9", "16:9", 2),
}

DEFAULT_VISUAL_SPEC: tuple[str, str, int] = ("3:4", "3:4", 5)


def channel_visual_spec(channel: str) -> tuple[str, str, int]:
    """返回该渠道的 (主画幅, 封面画幅, 建议张数)。"""
    if channel in CHANNEL_VISUAL_SPEC:
        return CHANNEL_VISUAL_SPEC[channel]
    for key, spec in CHANNEL_VISUAL_SPEC.items():
        if key in channel or channel in key:
            return spec
    return DEFAULT_VISUAL_SPEC


__all__ = [
    "PaletteColor",
    "VisualStyle",
    "VISUAL_STYLES",
    "DEFAULT_STYLE",
    "visual_styles_for",
    "CHANNEL_VISUAL_SPEC",
    "channel_visual_spec",
]
