"""二创/解说类版权风险清单（知识层，plan「解说类二创合规补位」）。

为什么需要它
------------
现有 A7 只有**广告法词库**——它管的是「绝对化用语、虚假宣传」这类宣称合规，
对影视 / 游戏 / 动漫**解说二创**真正的红线（著作权）却完全没覆盖。解说号最大的
翻车点不是广告法，而是**版权**：整段搬运、引用比例过高、无免责声明、剧透不标注、
BGM 侵权，任何一条都可能触发下架 / 限流 / 维权。

诚实边界（务必理解）
--------------------
本模块**不假装能自动检测侵权**。著作权是否构成「合理使用」是**个案法律判断**，
机器无法替人裁定。这里做的是一件能落地的、诚实的事：

* 依据结构化规则，判断当前创作**是否属于解说 / 二创语境**；
* 若属于，产出一份**可核对的版权风险 checklist**（引用比例、免责声明、剧透标注、
  BGM 版权、平台搬运判定要点），逐条给出**风险说明与整改建议**；
* **强制 `needs_human_review=True`**——把最终把关交回给人，与「非中文词库不适用时
  降级为需人工复核」（见 ``language.compliance_coverage``）同一套处理哲学。

这与「静默放行」的区别，就是「如实告知有风险待核」与「假装检查过了」的区别。
"""

from __future__ import annotations

from typing import Any

#: 解说 / 二创强相关的渠道（长视频二创主阵地）
_COMMENTARY_CHANNELS = ("B站", "bilibili", "哔哩哔哩", "YouTube", "西瓜视频", "抖音", "快手", "视频号")

#: 解说 / 二创强相关的行业调性
_COMMENTARY_INDUSTRIES = ("文化娱乐", "影视", "娱乐", "游戏", "动漫", "二次元", "电竞")

#: 出现在交付物 / 主题 / 约束里的解说类信号词
_COMMENTARY_SIGNAL_WORDS = (
    "解说",
    "杂谈",
    "吐槽",
    "剧情",
    "影视",
    "影评",
    "游戏",
    "动漫",
    "番剧",
    "二创",
    "混剪",
    "盘点",
    "测评",
    "通关",
    "彩蛋",
    "搬运",
    "剧透",
)


def _hits(text: str, needles: tuple[str, ...]) -> list[str]:
    low = (text or "").lower()
    return [needle for needle in needles if needle.lower() in low]


def detect_commentary(brief: dict[str, Any] | None, text: str = "") -> tuple[bool, list[str]]:
    """判断当前创作是否属于解说 / 二创语境，并给出命中的信号（可解释性）。"""
    brief = brief or {}
    signals: list[str] = []

    channel = str(brief.get("channel") or "")
    industry = str(brief.get("industry") or "")
    deliverables = " ".join(str(item) for item in (brief.get("deliverables") or []))
    constraints = " ".join(str(item) for item in (brief.get("constraints") or []))
    corpus = " ".join([channel, industry, deliverables, constraints, str(brief.get("product") or ""), text])

    if _hits(channel, _COMMENTARY_CHANNELS):
        signals.append(f"渠道「{channel}」是影视/游戏解说二创的主阵地")
    if _hits(industry, _COMMENTARY_INDUSTRIES):
        signals.append(f"行业「{industry}」涉及他人视听作品 / 游戏画面")
    signal_words = _hits(corpus, _COMMENTARY_SIGNAL_WORDS)
    if signal_words:
        signals.append("主题/交付物/约束出现解说二创信号词：" + "、".join(signal_words[:6]))

    # 命中两条及以上，或明确出现「剧透/搬运/二创」这类硬信号，才判为解说类：
    # 避免把一条普通的品牌短片误判成需要版权复核的重负。
    hard = _hits(corpus, ("剧透", "搬运", "二创", "混剪", "影评", "解说"))
    applies = bool(hard) or len(signals) >= 2
    return applies, signals


def copyright_checklist(brief: dict[str, Any] | None, text: str = "") -> dict[str, Any]:
    """产出解说 / 二创类的版权风险清单（结构化规则，非自动检测）。

    返回字段：
    * ``applies``：是否属于解说二创语境（否则各项 checklist 不适用，直接放行）；
    * ``signals``：判定依据（为什么认为这是二创）；
    * ``items``：风险清单，每项 ``{topic, risk, suggestion, severity}``；
    * ``needs_human_review``：属于解说二创即为 True（著作权是法律判断，机器不裁定）；
    * ``message``：给界面/报告用的一句话结论。
    """
    applies, signals = detect_commentary(brief, text)
    if not applies:
        return {
            "applies": False,
            "signals": signals,
            "items": [],
            "needs_human_review": False,
            "message": "非解说/二创语境，未触发版权专项清单",
        }

    constraints = " ".join(str(item) for item in ((brief or {}).get("constraints") or []))
    corpus = f"{constraints} {text or ''}"
    spoiler_flagged = bool(_hits(corpus, ("剧透",)))

    items: list[dict[str, Any]] = [
        {
            "topic": "片源引用比例",
            "severity": "major",
            "risk": "解说若大段/连续引用原片画面、原声，超出「适当引用」边界，可能被认定侵犯复制权/信息网络传播权",
            "suggestion": "以原创观点、评析为主线，画面仅截取支撑论点的必要片段并压低占比；避免整段平铺原片剧情",
        },
        {
            "topic": "转化性/评析性",
            "severity": "major",
            "risk": "纯剧情复述、无观点无评析的内容更易被判为搬运而非二创",
            "suggestion": "每个引用片段都绑定原创分析（评价、考证、对比、批评），体现「转换性使用」",
        },
        {
            "topic": "免责声明与来源标注",
            "severity": "minor",
            "risk": "未标注作品名/出处/权利人，易被认定不注明来源",
            "suggestion": "片头或简介注明作品名称、出处，并声明仅作评论/研究之目的的合理引用、非商业搬运",
        },
        {
            "topic": "BGM/配乐版权",
            "severity": "major",
            "risk": "背景音乐、音效若来自受版权保护曲库，可能单独触发音频侵权",
            "suggestion": "使用平台曲库/免版税音乐，或自行配音；不直接铺原声带",
        },
        {
            "topic": "平台搬运判定",
            "severity": "major",
            "risk": "B站/YouTube 等有原创保护与搬运识别机制，命中搬运可致下架、限流、扣分",
            "suggestion": "确系二创而非搬运；避免与源视频画面/音频高度重合，保留原创口播与字幕",
        },
    ]

    # 剧透标注：只有当 Brief 已声明剧透约束时才提整改口径，否则给出通用建议
    if not spoiler_flagged:
        items.append(
            {
                "topic": "剧透标注",
                "severity": "minor",
                "risk": "影视/游戏解说不标注关键剧情/结局剧透，影响观众体验与投诉风险",
                "suggestion": "涉及关键情节/结局处加「前方剧透」提示，或在封面/标题给出剧透预警",
            }
        )
    else:
        items.append(
            {
                "topic": "剧透标注",
                "severity": "minor",
                "risk": "已声明剧透约束，需确认成片在关键节点确实落了剧透提示",
                "suggestion": "逐处核对涉及关键情节/结局的分镜是否已加「前方剧透」提示",
            }
        )

    return {
        "applies": True,
        "signals": signals,
        "items": items,
        "needs_human_review": True,
        "message": "解说/二创语境：著作权是否构成合理使用属法律个案判断，以下风险项须人工把关",
    }


__all__ = [
    "copyright_checklist",
    "detect_commentary",
]
