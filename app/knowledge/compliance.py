"""合规词库与规则引擎（移植自 server/knowledge/compliance.ts），服务于 A7 品牌合规智能体。

说明：MVP 使用本地词表 + 规则匹配，保证离线可跑、结果可解释、误杀可追溯。
生产环境应升级为「词库服务 + 法规库 RAG + 人工误杀反馈学习」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["blocker", "major", "minor"]


@dataclass(frozen=True)
class LexiconHit:
    term: str = ""
    category: str = ""
    severity: Severity = "minor"
    law: str = ""
    snippet: str = ""
    fix: str = ""


@dataclass(frozen=True)
class LexiconGroup:
    category: str = ""
    severity: Severity = "minor"
    law: str = ""
    fix: str = ""
    terms: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ #
# 通用词库                                                            #
# ------------------------------------------------------------------ #

LEXICON_GROUPS: list[LexiconGroup] = [
    LexiconGroup(
        category="绝对化用语",
        severity="blocker",
        law="《广告法》第九条第（三）项：不得使用“国家级”“最高级”“最佳”等用语",
        fix="删除绝对化表述，改为可验证的限定描述，如「同类产品中较优」「我们实测中表现更好」。",
        terms=[
            "国家级", "世界级", "最高级", "最佳", "最好", "最强", "最大", "最优", "最先进",
            "最高", "最值", "最省", "最专业", "最靠谱", "最健康", "最好用", "最好吃", "最舒适",
            "最便宜", "最划算", "最低价", "最高端", "第一品牌", "全国第一", "全球第一",
            "销量第一", "排名第一", "行业第一", "首家", "首个", "首创", "独家", "唯一",
            "顶级", "顶尖", "极致", "至尊", "王牌", "领导品牌", "行业领先", "国际领先",
            "绝对", "100%", "百分百", "全网最低", "史上最低", "永久", "终身", "万能",
            "无所不能", "无与伦比", "空前绝后", "登峰造极", "绝无仅有", "完美",
        ],
    ),
    LexiconGroup(
        category="医疗功效宣称",
        severity="blocker",
        law="《广告法》第十七条：非医疗、药品、医疗器械广告不得涉及疾病治疗功能",
        fix="删除疾病治疗相关表述，仅描述使用体验与外观感受，如「用后感觉更清爽」。",
        terms=[
            "治疗", "治愈", "根治", "疗效", "药效", "包治", "主治", "消炎", "杀菌",
            "抗癌", "抗肿瘤", "降血压", "降血糖", "降血脂", "降三高", "减肥", "丰胸",
            "壮阳", "排毒", "无副作用", "药到病除", "立竿见影", "立刻见效", "当天见效",
            "一周见效", "七天见效", "提高免疫力", "修复受损", "医用级",
        ],
    ),
    LexiconGroup(
        category="金融收益承诺",
        severity="blocker",
        law="《广告法》第二十五条：不得对未来效果、收益作保证性承诺",
        fix="删除保本/保收益表述，补充「市场有风险，投资需谨慎」。",
        terms=[
            "保本", "保收益", "保本保息", "稳赚", "稳赚不赔", "无风险", "零风险",
            "高收益", "高回报", "年化收益", "躺赚", "一夜暴富", "必赚", "稳赢", "收益翻倍",
        ],
    ),
    LexiconGroup(
        category="效果与时限承诺",
        severity="major",
        law="《广告法》第二十八条：不得对商品性能、功能作虚假或引人误解的宣传",
        fix="删除保证性承诺，改为条件化的客观描述，如「在 XX 条件下，实测数据为 XX」。",
        terms=["保证有效", "保证通过", "保过", "包过", "无效退款", "立刻变白", "马上见效", "一次性解决"],
    ),
    LexiconGroup(
        category="竞品贬损",
        severity="major",
        law="《反不正当竞争法》第十一条：不得编造、传播虚假信息损害竞争对手商誉",
        fix="删除贬损性对比，改为客观参数对比并标注测试条件与来源。",
        terms=["最差", "垃圾品牌", "假货", "山寨货", "吊打同行", "碾压所有"],
    ),
    LexiconGroup(
        category="版权与来源风险",
        severity="minor",
        law="《著作权法》：使用他人作品需获得授权",
        fix="补充图片来源与授权说明，避免使用「图片来源网络」等无授权表述。",
        terms=["图片来源网络", "转载无需授权", "素材随便用", "网图侵删"],
    ),
    LexiconGroup(
        category="个人信息风险",
        severity="minor",
        law="《个人信息保护法》：处理个人信息需明示目的并取得同意",
        fix="删除对个人信息的直接索取表述，改为引导至合规表单并说明用途。",
        terms=["加微信领取", "私信发身份证", "填写手机号即可", "详细住址"],
    ),
]


# ------------------------------------------------------------------ #
# 行业附加规则                                                        #
# ------------------------------------------------------------------ #


@dataclass(frozen=True)
class RequiredSpec:
    pattern: re.Pattern[str]
    hint: str = ""
    severity: Severity = "minor"


@dataclass(frozen=True)
class IndustryRule:
    category: str = ""
    severity: Severity = "minor"
    law: str = ""
    fix: str = ""
    terms: list[str] = field(default_factory=list)
    #: 该行业文案必须包含的内容（缺失则告警）
    required: RequiredSpec | None = None


INDUSTRY_RULES: dict[str, list[IndustryRule]] = {
    "医疗健康": [
        IndustryRule(
            category="医疗宣传资质",
            severity="major",
            law="《医疗广告管理办法》第七条：医疗广告须经审查并标注审查文号",
            fix="补充机构资质与审查文号，或删除诊疗相关表述改为科普口吻。",
            terms=["专家推荐", "三甲医院指定", "临床验证", "有效率99%"],
            required=RequiredSpec(
                pattern=re.compile(r"(指南|临床|研究|文献|来源|参考文献)"),
                hint="健康类内容涉及结论时需标注权威来源（临床研究 / 诊疗指南）",
                severity="major",
            ),
        ),
    ],
    "金融": [
        IndustryRule(
            category="金融风险提示缺失",
            severity="major",
            law="《广告法》第二十五条：金融产品广告应显著提示风险",
            fix="在文末补充「市场有风险，投资需谨慎」等风险提示。",
            terms=["稳赚", "保收益"],
            required=RequiredSpec(
                pattern=re.compile(r"(风险提示|投资需谨慎|市场有风险|不构成投资建议)"),
                hint="金融类内容必须包含风险提示",
                severity="major",
            ),
        ),
    ],
    "教育培训": [
        IndustryRule(
            category="教育培训承诺",
            severity="blocker",
            law="《广告法》第二十四条：教育、培训广告不得对升学、通过考试作保证性承诺",
            fix="删除提分/保过承诺，改为描述教学方法与学习过程。",
            terms=["保过", "保分", "提分保证", "不过退款", "一次通过"],
            required=RequiredSpec(
                pattern=re.compile(r"(课程|课时|教学|讲师|师资|大纲)"),
                hint="教育类内容建议说明课程与师资信息",
                severity="minor",
            ),
        ),
    ],
    "食品饮料": [
        IndustryRule(
            category="食品功效宣称",
            severity="major",
            law="《食品安全法》第七十三条：食品广告不得涉及疾病预防、治疗功能",
            fix="删除保健功效表述，仅描述口感、配料与食用场景。",
            terms=["防癌", "抗癌", "治疗便秘", "降三高", "保健功效"],
        ),
    ],
    "美妆个护": [
        IndustryRule(
            category="化妆品医疗术语",
            severity="major",
            law="《化妆品监督管理条例》第二十二条：不得明示或暗示具有医疗作用",
            fix="将医疗术语替换为化妆品功效用语，如用「舒缓」替代「消炎」。",
            terms=["消炎", "抗炎", "修复损伤", "再生", "医用面膜"],
        ),
    ],
    "企业服务": [
        IndustryRule(
            category="B2B 效果承诺",
            severity="major",
            law="《广告法》第二十八条：不得对服务效果作保证性承诺",
            fix="将效果承诺改为可验证的客户案例数据，并标注统计口径。",
            terms=["必定降本50%", "保证翻倍", "零风险上线"],
        ),
    ],
}


# ------------------------------------------------------------------ #
# 扫描器                                                              #
# ------------------------------------------------------------------ #


@dataclass(frozen=True)
class RequiredFailure:
    category: str = ""
    hint: str = ""
    severity: Severity = "minor"
    law: str = ""
    fix: str = ""


@dataclass(frozen=True)
class ScanResult:
    hits: list[LexiconHit] = field(default_factory=list)
    required_failures: list[RequiredFailure] = field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "low"


def _snippet_of(text: str, index: int, length: int) -> str:
    start = max(0, index - 12)
    end = min(len(text), index + length + 12)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


def scan_compliance(text: str, industry: str) -> ScanResult:
    """多轮扫描：通用词库 → 行业专项 → 必备要素检查。"""
    hits: list[LexiconHit] = []
    seen: set[str] = set()

    def push_hit(group: LexiconGroup | IndustryRule, term: str, index: int) -> None:
        key = f"{group.category}::{term}"
        if key in seen:
            return
        seen.add(key)
        hits.append(
            LexiconHit(
                term=term,
                category=group.category,
                severity=group.severity,
                law=group.law,
                snippet=_snippet_of(text, index, len(term)),
                fix=group.fix,
            )
        )

    for group in LEXICON_GROUPS:
        for term in group.terms:
            index = text.find(term)
            if index >= 0:
                push_hit(group, term, index)

    industry_rules = INDUSTRY_RULES.get(industry, [])
    for rule in industry_rules:
        for term in rule.terms:
            index = text.find(term)
            if index >= 0:
                push_hit(rule, term, index)

    required_failures: list[RequiredFailure] = []
    for rule in industry_rules:
        if rule.required is None:
            continue
        if not rule.required.pattern.search(text):
            required_failures.append(
                RequiredFailure(
                    category=rule.category,
                    hint=rule.required.hint,
                    severity=rule.required.severity,
                    law=rule.law,
                    fix=rule.fix,
                )
            )

    blockers = len([h for h in hits if h.severity == "blocker"])
    majors = len([h for h in hits if h.severity == "major"])
    if blockers > 0 or any(f.severity == "blocker" for f in required_failures):
        risk_level: Literal["low", "medium", "high"] = "high"
    elif majors > 0 or required_failures:
        risk_level = "medium"
    else:
        risk_level = "low"

    return ScanResult(hits=hits, required_failures=required_failures, risk_level=risk_level)


# ------------------------------------------------------------------ #
# 自动改写                                                            #
# ------------------------------------------------------------------ #

SAFE_REPLACEMENTS: dict[str, str] = {
    "最好": "表现不错",
    "最佳": "较优",
    "最强": "偏强",
    "最大": "较大",
    "最优": "更优",
    "最先进": "较为先进",
    "最便宜": "价格更有优势",
    "最划算": "性价比不错",
    "最低价": "活动价",
    "最高端": "偏高端",
    "第一品牌": "较早进入该领域的品牌",
    "全国第一": "在全国范围内表现突出",
    "全球第一": "在全球范围内表现突出",
    "销量第一": "销量表现突出",
    "排名第一": "排名靠前",
    "首家": "较早推出",
    "首个": "较早推出",
    "独家": "自有",
    "唯一": "少数具备该能力的",
    "顶级": "高规格",
    "顶尖": "高水准",
    "极致": "很讲究",
    "绝对": "相对",
    "100%": "绝大多数情况下",
    "永久": "长期",
    "终身": "长期",
    "万能": "多场景适用",
    "完美": "很满意",
    "治疗": "护理",
    "治愈": "改善",
    "根治": "缓解",
    "疗效": "使用感受",
    "消炎": "舒缓",
    "杀菌": "清洁",
    "减肥": "身材管理",
    "排毒": "代谢支持",
    "无副作用": "成分温和",
    "立竿见影": "用后感受明显",
    "立刻见效": "短期内可感知变化",
    "提高免疫力": "状态支持",
    "保本": "相对稳健",
    "保收益": "历史表现",
    "稳赚": "收益存在波动",
    "无风险": "风险相对可控",
    "高收益": "收益与风险并存",
    "高回报": "回报存在不确定性",
}


@dataclass(frozen=True)
class RewriteResult:
    text: str = ""
    applied: list[dict[str, str]] = field(default_factory=list)


def auto_rewrite(text: str, hits: list[LexiconHit]) -> RewriteResult:
    """自动改写：把命中的违规词替换为合规表达。

    结果中的 ``applied`` 会作为 ``safe_rewrites`` 回传给 A4，供返工时直接参考。
    """
    output = text
    applied: list[dict[str, str]] = []
    for hit in hits:
        replacement = SAFE_REPLACEMENTS.get(hit.term)
        if not replacement:
            continue
        if hit.term not in output:
            continue
        output = output.replace(hit.term, replacement)
        applied.append({"from": hit.term, "to": replacement})
    return RewriteResult(text=output, applied=applied)


# ------------------------------------------------------------------ #
# 品牌一致性检查                                                      #
# ------------------------------------------------------------------ #


@dataclass(frozen=True)
class BrandVoiceIssue:
    type: str = ""
    detail: str = ""
    suggestion: str = ""
    severity: Severity = "minor"


@dataclass(frozen=True)
class BrandVoiceReport:
    score: int = 100
    issues: list[BrandVoiceIssue] = field(default_factory=list)


_RIGID = ["综上所述", "特此通知", "本公司", "兹定于", "务必", "敬请知悉"]
_CASUAL = ["绝绝子", "yyds", "栓Q", "家人们", "宝子", "爆改"]
_HYPED = ["疯狂", "史上", "震惊", "不看后悔", "速抢"]

_SOFT_TONE_RE = re.compile(r"(轻松|真实|年轻|活泼|幽默|温暖)")
_YOUNG_TONE_RE = re.compile(r"(年轻|活泼|网感|幽默)")


def check_brand_voice(text: str, tone: str) -> BrandVoiceReport:
    """按品牌调性关键词检查文案语气是否走偏。"""
    issues: list[BrandVoiceIssue] = []
    score = 100

    for word in _RIGID:
        if word in text and _SOFT_TONE_RE.search(tone):
            issues.append(
                BrandVoiceIssue(
                    type="语气偏正式",
                    detail=f"出现书面化表达「{word}」，与设定的「{tone}」调性不符",
                    suggestion="改写为口语化表达，或拆成短句。",
                    severity="minor",
                )
            )
            score -= 6

    for word in _CASUAL:
        if word in text and not _YOUNG_TONE_RE.search(tone):
            issues.append(
                BrandVoiceIssue(
                    type="网络用语过度",
                    detail=f"「{word}」与设定的「{tone}」调性不匹配",
                    suggestion="替换为更中性的口语表达。",
                    severity="minor",
                )
            )
            score -= 6

    for word in _HYPED:
        if word in text:
            issues.append(
                BrandVoiceIssue(
                    type="标题党倾向",
                    detail=f"出现煽动性表达「{word}」",
                    suggestion="改为具体事实描述，避免空泛夸张。",
                    severity="minor",
                )
            )
            score -= 4

    return BrandVoiceReport(score=max(40, min(100, score)), issues=issues)
