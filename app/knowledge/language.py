"""多语言本地化：语言画像、渠道映射与合规提示（plan.md v2.0「多语言本地化」）。

范围与取舍
----------
这里做的是**语言维度**，不是「翻译功能」：

* 每个语言一份画像（本地渠道、字数口径、语气惯例、合规红线、度量单位）；
* 创作类智能体（A1–A5、A8、A9）按目标语言产出**原生文案**，而不是先写中文再翻；
* 记忆库按语言分区召回（英文资产不该被中文任务复用）；
* 评估器按语言切换口径（字数按字符还是按词、合规红线不同）。

**刻意不做**：机器翻译接口。理由是本项目的产品定位是「原生创作」——
翻译腔的营销文案在本地市场基本不可用，而接一个翻译 API 只会让产出看起来
「支持多语言」却达不到可用标准。要新增语言时，补一份 ``LanguageProfile`` 即可，
不需要改任何智能体代码。

已知边界（如实标注）
--------------------
* **非中文语言的合规词库未覆盖**：目前只有 ``zh`` 用广告法词库扫描，
  其它语言返回「未覆盖」而不是假装通过。做真合规需要按目标市场接法规词库
  （如美国 FTC 披露规则、欧盟 DSA），这属于要人工专家参与的事项。
* **度量单位与日期格式**只做到「画像里声明」，未做自动化换算。
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: 支持的语言代码（BCP-47 的主语言子标签）
SUPPORTED_LANGUAGES: tuple[str, ...] = ("zh", "en", "ja", "ko", "es")

#: 语言代码 → 展示名（界面与产物里用）
LANGUAGE_LABEL: dict[str, str] = {
    "zh": "简体中文",
    "en": "English",
    "ja": "日本語",
    "ko": "한국어",
    "es": "Español",
}

#: 解析输入时的别名（用户可能写 zh-CN / Chinese / 中文 …）
_LANGUAGE_ALIASES: dict[str, str] = {
    "zh": "zh", "zh-cn": "zh", "zh-hans": "zh", "zh-tw": "zh", "zh-hant": "zh",
    "cn": "zh", "chinese": "zh", "中文": "zh", "简体中文": "zh", "汉语": "zh",
    "en": "en", "en-us": "en", "en-gb": "en", "english": "en", "英文": "en", "英语": "en",
    "ja": "ja", "ja-jp": "ja", "jp": "ja", "japanese": "ja", "日文": "ja", "日语": "ja",
    "ko": "ko", "ko-kr": "ko", "kr": "ko", "korean": "ko", "韩文": "ko", "韩语": "ko",
    "es": "es", "es-es": "es", "es-mx": "es", "spanish": "es", "西班牙语": "es",
}


@dataclass(frozen=True)
class LanguageProfile:
    """一个语言的创作与合规画像。"""

    code: str
    label: str
    #: 该语言的本地主流渠道（Brief 未指定渠道时的建议）
    native_channels: tuple[str, ...] = ()
    #: 标题长度口径：char = 按字符（中日韩），word = 按词（英西）
    title_unit: str = "char"
    #: 标题上限（按上面的口径）
    title_limit: int = 30
    #: 正文长度建议
    body_hint: str = ""
    #: 语气惯例（供 A2/A4 参考的本地化表达习惯）
    tone_conventions: tuple[str, ...] = ()
    #: 该语言的合规红线（人工整理的要点，不是可执行词库）
    compliance_notes: tuple[str, ...] = ()
    #: 度量与日期惯例
    locale_notes: tuple[str, ...] = ()
    #: 是否已接入可执行的合规词库扫描
    lexicon_coverage: bool = False
    #: 是否需要显式标注广告/赞助（多数市场的硬要求）
    ad_disclosure_required: bool = False
    ad_disclosure_text: str = ""


PROFILES: dict[str, LanguageProfile] = {
    "zh": LanguageProfile(
        code="zh",
        label="简体中文",
        native_channels=("小红书", "抖音", "公众号", "知乎", "电商详情页", "官网", "PR稿"),
        title_unit="char",
        title_limit=20,
        body_hint="图文 300-600 字；长图文 1500-2500 字",
        tone_conventions=("用第二人称对话", "先共情再给方案", "以具体细节替代形容词"),
        compliance_notes=(
            "《广告法》禁绝对化用语（国家级/最高级/最佳）",
            "不得涉及疾病预防与治疗功能（非医疗品类）",
            "不得对未来效果、收益作保证性承诺",
        ),
        locale_notes=("度量单位用公制（kg / km / ℃）", "日期 YYYY-MM-DD", "货币 ¥ / CNY"),
        lexicon_coverage=True,
    ),
    "en": LanguageProfile(
        code="en",
        label="English",
        native_channels=("Instagram", "TikTok", "LinkedIn", "Email", "Landing Page", "Press Release"),
        title_unit="word",
        title_limit=12,
        body_hint="60-120 words for social; 300-600 words for blog/press",
        tone_conventions=("Lead with the benefit, not the feature", "Use active voice", "One idea per sentence"),
        compliance_notes=(
            "FTC: material connections must be disclosed (#ad / #sponsored)",
            "No unsubstantiated health or income claims",
            "Superlatives need substantiation (best / #1 / guaranteed)",
        ),
        locale_notes=("Imperial or metric as the target market requires", "Date MM/DD/YYYY (US) or DD/MM/YYYY (UK)", "Currency USD/GBP/EUR"),
        lexicon_coverage=False,
        ad_disclosure_required=True,
        ad_disclosure_text="#ad",
    ),
    "ja": LanguageProfile(
        code="ja",
        label="日本語",
        native_channels=("X（旧Twitter）", "Instagram", "LINE", "プレスリリース", "商品ページ"),
        title_unit="char",
        title_limit=24,
        body_hint="SNS 100-200 字；長文 800-1500 字",
        tone_conventions=("敬体（です・ます調）を基本", "結論を先に、理由を後に", "過度な誇張表現を避ける"),
        compliance_notes=(
            "景品表示法：優良誤認・有利誤認の禁止",
            "薬機法：化粧品・健康食品の効能効果の範囲",
            "「No.1」「日本一」は客観的根拠が必要",
        ),
        locale_notes=("メートル法", "日付 YYYY年MM月DD日", "通貨 ¥ / JPY"),
        lexicon_coverage=False,
        ad_disclosure_required=True,
        ad_disclosure_text="#PR",
    ),
    "ko": LanguageProfile(
        code="ko",
        label="한국어",
        native_channels=("네이버 블로그", "인스타그램", "유튜브", "카카오톡 채널", "보도자료"),
        title_unit="char",
        title_limit=25,
        body_hint="SNS 100-200자；장문 800-1500자",
        tone_conventions=("존댓말 기본", "결론 우선", "과장 표현 지양"),
        compliance_notes=(
            "표시·광고의 공정화에 관한 법률: 거짓·과장 광고 금지",
            "의약품·화장품 효능 표현 제한",
            "최상급 표현은 객관적 근거 필요",
        ),
        locale_notes=("미터법", "날짜 YYYY.MM.DD", "통화 ₩ / KRW"),
        lexicon_coverage=False,
        ad_disclosure_required=True,
        ad_disclosure_text="#광고",
    ),
    "es": LanguageProfile(
        code="es",
        label="Español",
        native_channels=("Instagram", "TikTok", "LinkedIn", "Email", "Página de producto"),
        title_unit="word",
        title_limit=12,
        body_hint="60-120 palabras para social; 300-600 para artículo",
        tone_conventions=("Tuteo cercano", "Beneficio antes que característica", "Frases cortas"),
        compliance_notes=(
            "Publicidad debe identificarse como tal (#publicidad)",
            "Sin afirmaciones de salud o ingresos no demostrables",
            "Superlativos requieren evidencia",
        ),
        locale_notes=("Sistema métrico", "Fecha DD/MM/YYYY", "Moneda EUR/MXN"),
        lexicon_coverage=False,
        ad_disclosure_required=True,
        ad_disclosure_text="#publicidad",
    ),
}

#: 默认语言
DEFAULT_LANGUAGE = "zh"


def normalize_language(value: str | None) -> str:
    """把用户输入的语言写法归一化；无法识别时回落到中文（并保留原值供界面提示）。"""
    raw = (value or "").strip()
    if not raw:
        return DEFAULT_LANGUAGE
    return _LANGUAGE_ALIASES.get(raw.lower(), DEFAULT_LANGUAGE)


def language_profile(code: str | None) -> LanguageProfile:
    return PROFILES.get(normalize_language(code), PROFILES[DEFAULT_LANGUAGE])


def language_label(code: str | None) -> str:
    return language_profile(code).label


def language_options() -> list[dict[str, str]]:
    """供界面下拉使用。"""
    return [
        {"code": code, "label": PROFILES[code].label} for code in SUPPORTED_LANGUAGES
    ]


def is_multilingual(code: str | None) -> bool:
    """是否非默认语言（用于决定是否走本地化链路）。"""
    return normalize_language(code) != DEFAULT_LANGUAGE


def title_limit_for(channel: str, language: str | None) -> int:
    """标题上限：中文渠道用渠道规则；非中文用语言画像的口径。"""
    from .industry import title_limit

    profile = language_profile(language)
    if profile.code == DEFAULT_LANGUAGE:
        return title_limit(channel)
    return profile.title_limit


def title_measure(text: str, language: str | None) -> tuple[int, str]:
    """按目标语言的口径度量标题长度，返回 ``(数值, 单位)``。

    英文按**词**数、中日韩按**字符**数 —— 这是本地平台的真实口径，
    用错会让「标题合规」的判断失去意义（12 个词的英文标题早已超出信息流截断点）。
    """
    profile = language_profile(language)
    text = (text or "").strip()
    if profile.title_unit == "word":
        return len([part for part in text.split() if part]), "词"
    return len(text), "字"


def localization_directive(channel: str, language: str | None) -> str:
    """渲染成提示词片段：告诉创作类智能体「按目标语言原生创作」而不是翻译。"""
    profile = language_profile(language)
    if profile.code == DEFAULT_LANGUAGE:
        return ""
    unit = "词" if profile.title_unit == "word" else "字"
    lines = [
        f"【本地化要求】目标语言：{profile.label}（{profile.code}）",
        f"- 必须用 {profile.label} **原生创作**，不要先写中文再翻译；避免翻译腔",
        f"- 标题上限约 {profile.title_limit} {unit}，正文建议 {profile.body_hint}",
        "- 本地表达惯例：" + "；".join(profile.tone_conventions),
        "- 度量与格式：" + "；".join(profile.locale_notes),
    ]
    if profile.ad_disclosure_required:
        lines.append(f"- 商业推广**必须**显式标注 {profile.ad_disclosure_text}")
    if profile.compliance_notes:
        lines.append("- 当地合规红线：" + "；".join(profile.compliance_notes))
    if not profile.lexicon_coverage:
        lines.append(
            "- ⚠️ 本语言尚无自动合规词库，系统不会替你判定违规，需人工复核合规表述"
        )
    return "\n".join(lines) + "\n"


def compliance_coverage(code: str | None) -> dict[str, object]:
    """当前语言的合规覆盖情况，供界面明确提示而不是「看起来通过」。"""
    profile = language_profile(code)
    return {
        "language": profile.code,
        "label": profile.label,
        "lexicon_coverage": profile.lexicon_coverage,
        "ad_disclosure_required": profile.ad_disclosure_required,
        "ad_disclosure_text": profile.ad_disclosure_text,
        "notes": list(profile.compliance_notes),
        "message": (
            "已接入广告法词库自动扫描"
            if profile.lexicon_coverage
            else f"{profile.label} 尚无自动合规词库，需人工复核合规表述"
        ),
    }


__all__ = [
    "DEFAULT_LANGUAGE",
    "LANGUAGE_LABEL",
    "PROFILES",
    "SUPPORTED_LANGUAGES",
    "LanguageProfile",
    "compliance_coverage",
    "is_multilingual",
    "language_label",
    "language_options",
    "language_profile",
    "localization_directive",
    "normalize_language",
    "title_limit_for",
    "title_measure",
]
