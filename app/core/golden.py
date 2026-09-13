"""黄金数据集：固定 Brief + 基线分数，用于跨版本回归与 Prompt 调优对照。

解决什么问题
------------
「我把 Prompt 改好了」这句话在没有固定输入的情况下无法验证：内容创作本身是
发散的，换一个 Brief、换一次采样，分数就会漂移，分不清是改进了还是噪声。
黄金数据集把「输入」固定下来，把「输出」压缩成可比较的标量（质量分、评估分、
返工轮次、合规裁决），于是一次改动是否真的更好就变成了一个可判定的问题
（plan.md 4.3 D13「黄金数据集构建」、D14「Prompt 优化迭代（基于评估结果）」）。

数据集放哪、为什么
------------------
放在仓库内的 ``golden/``，**不在** ``data/`` 下：``data/`` 是运行期产物（已 gitignore），
而黄金数据集是**随代码一起版本化的测试资产** —— 它必须能被 review、被 diff、
被追责，否则「改了基线让 CI 变绿」就成了一种无人察觉的作弊。
``golden/baseline.json`` 同理，它记录的是「在这份代码 + 这个口径下，我们认可的分数」。

裁剪了哪些覆盖面
----------------
10 个 Brief 覆盖 7 个渠道 × 7 个行业的关键组合，外加两个刻意设计的边界用例：
极简 Brief（缺关键词/约束/交付物）与强监管行业（合规门禁必须真的拦得住）。
**不足 10–20 个的全部组合覆盖**：渠道 × 行业有 49 种组合，全铺会显著拖慢回归，
因此按「分支覆盖」而非「组合覆盖」取样 —— 每个渠道特化分支与每个行业规则组
至少被一个用例命中（``python scripts/golden_eval.py --coverage`` 可查看覆盖矩阵）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config import ROOT_DIR, RUBRIC_VERSION
from ..knowledge.compliance import scan_compliance
from ..knowledge.industry import title_limit  # noqa: F401 - 供外部沿用旧导入
from ..knowledge.language import title_limit_for, title_measure
from ..logger import create_logger
from .clock import now_iso

log = create_logger("golden")

#: 黄金数据集目录（随代码版本化，不在 data/ 下）
GOLDEN_DIR = ROOT_DIR / "golden"
BRIEFS_DIR = GOLDEN_DIR / "briefs"
BASELINE_FILE = GOLDEN_DIR / "baseline.json"

#: 分数回退超过该值即判定为「回归」（评估器有噪声，留出容差）
DEFAULT_TOLERANCE = 3.0

#: 基线条目里参与比较的标量指标（数值越大越好）
SCORED_METRICS: tuple[str, ...] = (
    "quality_score",
    "judge_total",
    "judge_final_total",
    "revision_round",
    "artifact_count",
    "fact_accuracy",
    "brand_consistency",
)


#: 否定语境前缀：出现在禁用词之前时，说明这句话是在**声明不做某事**（安全表述），
#: 而不是在做违规宣称。例如「不得涉及疾病预防与治疗功能」里的「治疗」是免责声明。
#: 若不做这层判断，合规文案里每一句「不承诺……」都会把自己判成违规。
_NEGATION_PREFIXES: tuple[str, ...] = (
    "不得",
    "不涉及",
    "不承诺",
    "不构成",
    "不存在",
    "不用于",
    "不含",
    "不加",
    "不做",
    "不用",
    "不能",
    "不会",
    "不保证",
    "不暗示",
    "避免",
    "禁止",
    "严禁",
    "杜绝",
    "无",
    "非",
    "勿",
)

#: 小句边界：否定词的作用范围是**小句**，不跨句、也不跨逗号。
#: 用固定字符窗口回看是错的 ——「不得涉及疾病预防与治疗功能」里否定词与「治疗」
#: 相隔 8 个字，短窗口会漏判；窗口放长又会把上一句的否定词错配到下一句。
#: 必须包含中文逗号：中文里 `，` 就是小句边界，
#: 「不得用于治疗，但可治疗失眠」的后半句是**肯定性宣称**，不能被前半句的否定词豁免。
_CLAUSE_SEPARATORS = "。；！？；\n\r|/，,、"
_CLAUSE_END_SEPARATORS = "。；！？；\n\r"


def _clause_of(text: str, index: int) -> str:
    """取 ``index`` 所在的小句（用于判断否定语境的范围）。"""
    start = max((text.rfind(sep, 0, index) for sep in _CLAUSE_SEPARATORS), default=-1) + 1
    ends = [pos for sep in _CLAUSE_END_SEPARATORS if (pos := text.find(sep, index)) >= 0]
    end = min(ends) if ends else len(text)
    return text[start:end]


def _is_negated(text: str, term: str) -> bool:
    """判断 ``term`` 在 ``text`` 中的所有出现是否都处于否定语境。

    判定口径：**所在小句内、且位于该词之前**存在否定词，即视为免责/禁止表述。

    必须是「所有出现都否定」：只要有一处是**肯定性宣称**，就算违规。
    例如「不得用于治疗」+「可治疗失眠」同时出现时，后者必须被判出来。
    """
    index = text.find(term)
    if index < 0:
        return False
    while index >= 0:
        # 取该词在所在小句内的前缀部分做否定词匹配
        start = max((text.rfind(sep, 0, index) for sep in _CLAUSE_SEPARATORS), default=-1) + 1
        before_in_clause = text[start:index]
        if not any(neg in before_in_clause for neg in _NEGATION_PREFIXES):
            return False  # 存在一处肯定语境 → 不是免责声明
        index = text.find(term, index + len(term))
    return True


@dataclass
class GoldenExpectation:
    """用例的**可执行期望**（比基线分数更强的约束）。

    为什么需要它
    ------------
    基线分数能发现「质量分掉了」，但发现不了「品牌名漏了」「合规禁用语漏出去了」
    「标题超了渠道字数上限」。这些是**内容层面的硬约束**，与分数无关：
    一篇 95 分但漏了品牌名的稿子，依然是不可交付的。

    约束分成两类，刻意区分：

    * ``must_contain`` / ``min_quality`` / ``max_revisions`` —— 来自 Brief 的
      **明确要求**（关键词、质量线、返工预算），违反即为缺陷；
    * ``min_tone_hits`` —— 语气是**倾向而非硬指标**，因此只要求命中其中若干个，
      否则会变成「为了让 CI 变绿而堆语气词」的反向激励。

    还有一条不写在用例里、对所有用例统一生效的约束：**最终文本不得包含
    广告法阻断级用语**。它由合规词库现算，因此词库更新后自动生效，
    不需要逐条用例维护。
    """

    #: 必须出现在最终文本中的关键词（Brief 的 keywords）
    must_contain: list[str] = field(default_factory=list)
    #: Brief 关键词至少要命中几个。**不要求全部命中**：关键词是选题方向而非逐字要求，
    #: 强制全部出现会逼出「把关键词硬塞进正文」的反向激励。
    min_keyword_hits: int = 0
    #: 明确禁止出现的表述（用例特有的硬约束）
    must_not_contain: list[str] = field(default_factory=list)
    #: 最低综合质量分
    min_quality: float = 0.0
    #: 最低评估分
    min_judge: float = 0.0
    #: 返工轮次上限（成本约束）
    max_revisions: int = 99
    #: 调性关键词命中数（**仅记录、不参与判定**）。
    #: Brief 的 tone 是抽象描述（「克制」「用数据说话」），不是要逐字写入正文的关键词；
    #: 若据此断言「必须命中 N 个」，唯一稳定的达标方式就是在文案里堆这些词，
    #: 那正好与「克制」背道而驰。调性交由 ``reference_points`` 与人工抽查覆盖。
    min_tone_hits: int = 0
    #: 标题字数上限。留 0 表示按渠道默认上限（``knowledge.industry.title_limit``）；
    #: 用例显式指定时以其为准，便于把「这一条必须更短」这类判断写进数据。
    max_title_length: int = 0
    #: 首轮是否应被门禁拦截（强监管用例为 True）
    expect_first_round_blocked: bool | None = None
    #: 是否必须产出视频脚本（短视频渠道 / 交付物点名要脚本时为 True；None = 不检查）
    expect_video_script: bool | None = None
    #: 人工写的期望要点，供 LLM-as-Judge 语义比对与人工抽查
    reference_points: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "must_contain": list(self.must_contain),
            "min_keyword_hits": self.min_keyword_hits,
            "must_not_contain": list(self.must_not_contain),
            "min_quality": self.min_quality,
            "min_judge": self.min_judge,
            "max_revisions": self.max_revisions,
            "min_tone_hits": self.min_tone_hits,
            "max_title_length": self.max_title_length,
            "expect_first_round_blocked": self.expect_first_round_blocked,
            "expect_video_script": self.expect_video_script,
            "reference_points": list(self.reference_points),
        }


@dataclass
class GoldenBrief:
    """一条黄金用例。"""

    id: str
    brief: dict[str, Any]
    note: str = ""
    expect: GoldenExpectation = field(default_factory=GoldenExpectation)

    @property
    def brand(self) -> str:
        return str(self.brief.get("brand") or "")

    @property
    def channel(self) -> str:
        return str(self.brief.get("channel") or "")

    @property
    def industry(self) -> str:
        return str(self.brief.get("industry") or "")

    @property
    def tone(self) -> str:
        return str(self.brief.get("tone") or "")

    @property
    def keywords(self) -> list[str]:
        return [str(item) for item in (self.brief.get("keywords") or [])]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "note": self.note,
            "brand": self.brand,
            "channel": self.channel,
            "industry": self.industry,
            "objective": str(self.brief.get("objective") or ""),
            "audience": str(self.brief.get("audience") or ""),
            "tone": self.tone,
            "keywords": self.keywords,
            "constraints": list(self.brief.get("constraints") or []),
            "brief": self.brief,
            "expect": self.expect.to_dict(),
        }


@dataclass
class CaseResult:
    """一条用例的单次运行结果（已压缩为可比较的标量 + 关键明细）。"""

    id: str
    status: str
    #: 最终综合质量分
    quality_score: float = 0.0
    #: 门禁时刻的评估分（首轮）
    judge_total: float = 0.0
    #: 交付前的最终评估分
    judge_final_total: float = 0.0
    axis_scores: dict[str, float] = field(default_factory=dict)
    revision_round: int = 0
    artifact_count: int = 0
    fact_accuracy: float = 0.0
    brand_consistency: float = 0.0
    compliance_verdicts: list[str] = field(default_factory=list)
    #: 首轮合规是否被拦（强监管用例的预期行为）
    first_round_blocked: bool = False
    predicted_ctr: float = 0.0
    duration_ms: int = 0
    task_id: str = ""
    error: str = ""
    #: 内容级期望的逐条检查结果：[{rule, ok, detail}]
    checks: list[dict[str, Any]] = field(default_factory=list)
    #: 最终交付文本（用于内容级断言与人工抽查；不参与基线分数比较）
    delivered_text: str = ""
    #: 产物类型集合与视频脚本的结构指标（供「必须产出脚本」类断言使用）
    artifact_types: list[str] = field(default_factory=list)
    video_shot_count: int = 0
    video_voiceover_count: int = 0
    video_timeline_ok: bool = False

    @property
    def failed_checks(self) -> list[dict[str, Any]]:
        return [item for item in self.checks if not item.get("ok")]

    def to_dict(self, *, include_text: bool = False) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "status": self.status,
            "quality_score": round(self.quality_score, 1),
            "judge_total": round(self.judge_total, 1),
            "judge_final_total": round(self.judge_final_total, 1),
            "axis_scores": {k: round(v, 2) for k, v in self.axis_scores.items()},
            "revision_round": self.revision_round,
            "artifact_count": self.artifact_count,
            "fact_accuracy": round(self.fact_accuracy, 1),
            "brand_consistency": round(self.brand_consistency, 1),
            "compliance_verdicts": list(self.compliance_verdicts),
            "first_round_blocked": self.first_round_blocked,
            "predicted_ctr": round(self.predicted_ctr, 2),
            "duration_ms": self.duration_ms,
            "task_id": self.task_id,
            "error": self.error,
            "checks": [dict(item) for item in self.checks],
            "checks_failed": len(self.failed_checks),
            "artifact_types": list(self.artifact_types),
            "video_shot_count": self.video_shot_count,
            "video_voiceover_count": self.video_voiceover_count,
            "video_timeline_ok": self.video_timeline_ok,
        }
        if include_text:
            payload["delivered_text"] = self.delivered_text
        return payload


# ------------------------------------------------------------------ #
# 数据集加载                                                          #
# ------------------------------------------------------------------ #


def load_dataset() -> list[GoldenBrief]:
    """加载全部黄金用例，按 id 排序；文件损坏时跳过并告警（不阻断服务）。"""
    cases: list[GoldenBrief] = []
    if not BRIEFS_DIR.is_dir():
        return cases
    for path in sorted(BRIEFS_DIR.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception as error:  # noqa: BLE001 - 单个用例损坏不应影响其他用例
            log.warn(f"黄金用例 {path.name} 解析失败，已跳过", error)
            continue
        if not isinstance(raw, dict):
            continue
        brief = raw.get("brief")
        if not isinstance(brief, dict):
            log.warn(f"黄金用例 {path.name} 缺少 brief 字段，已跳过")
            continue
        raw_expect = raw.get("expect")
        expect = (
            GoldenExpectation(**{k: v for k, v in raw_expect.items() if k in _EXPECT_FIELDS})
            if isinstance(raw_expect, dict)
            else GoldenExpectation()
        )
        cases.append(
            GoldenBrief(
                id=str(raw.get("id") or path.stem),
                note=str(raw.get("note") or ""),
                brief=brief,
                expect=expect,
            )
        )
    return cases


#: GoldenExpectation 的合法字段（用于从 JSON 还原时过滤未知键）
_EXPECT_FIELDS = set(GoldenExpectation.__dataclass_fields__)

#: CaseResult 的构造字段。落盘与还原都以它为准 —— ``to_dict`` 会额外给出
#: ``checks_failed`` 这类**派生统计**，它们不是构造参数，必须过滤掉。
_CASE_FIELDS = set(CaseResult.__dataclass_fields__)


def tone_tokens(tone: str) -> list[str]:
    """把调性描述拆成可匹配的关键词（「轻松、真实、有种草感」→ 三个词）。"""
    parts = re.split(r"[、，,/\s+]+", tone or "")
    return [part for part in (item.strip() for item in parts) if len(part) >= 2]


def check_expectations(
    case: GoldenBrief,
    result: CaseResult,
    *,
    delivered_text: str = "",
) -> list[dict[str, Any]]:
    """按用例期望逐条检查最终结果。

    **内容层面的硬约束不参与基线分数比较**：分数掉 3 分可能只是波动，
    但「品牌名漏了」「阻断级用语漏出去了」是确定性缺陷，必须单独判定。
    两类检查互补，缺一不可。
    """
    expect = case.expect
    text = delivered_text or ""
    checks: list[dict[str, Any]] = []

    def add(rule: str, ok: bool, detail: str) -> None:
        checks.append({"rule": rule, "ok": bool(ok), "detail": detail})

    # 1) Brief 关键词必须进入最终文案（明确要求，违反即缺陷）
    for keyword in expect.must_contain:
        hit = keyword in text
        add(f"关键词「{keyword}」", hit, "已覆盖" if hit else f"最终文本中未出现「{keyword}」")

    # 1b) Brief 关键词的覆盖率（只要求命中若干个，避免逼出「硬塞关键词」）
    if expect.min_keyword_hits and case.keywords:
        hits = [word for word in case.keywords if word in text]
        add(
            f"关键词命中 ≥ {expect.min_keyword_hits}/{len(case.keywords)}",
            len(hits) >= expect.min_keyword_hits,
            f"命中 {len(hits)} 个：{'、'.join(hits) or '无'}",
        )

    # 2) 用例特有的禁止表述（硬约束）。否定语境（「不得涉及……治疗功能」）不算违规。
    for term in expect.must_not_contain:
        if term not in text:
            add(f"禁用「{term}」", True, "未出现")
        elif _is_negated(text, term):
            add(f"禁用「{term}」", True, "仅出现在否定/免责表述中（不算违规宣称）")
        else:
            add(f"禁用「{term}」", False, f"出现了禁用表述「{term}」")

    # 3) 品牌名必须出现（任何渠道都要求）
    if case.brand:
        hit = case.brand in text
        add(f"品牌名「{case.brand}」", hit, "已出现" if hit else "最终文本中未出现品牌名")

    # 4) 渠道标题长度上限。**必须按目标语言的口径度量**：
    #    英文按词、中日韩按字。用错口径会让断言本身失去意义 ——
    #    一个 12 词的英文标题在字符口径下有 60+ 字，会被误判成严重超限。
    title = _title_of(result, delivered_text)
    if title:
        limit = expect.max_title_length or title_limit_for(case.channel, case.brief.get("language"))
        measured, unit = title_measure(title, case.brief.get("language"))
        add(
            f"标题 ≤ {limit} {unit}",
            0 < measured <= limit,
            f"当前 {measured} {unit}"
            + ("" if measured <= limit else f"，超出上限 {limit} {unit}"),
        )

    # 5) 广告法**阻断级**用语一律不得出现在最终交付文本里。
    #    这条对全部用例统一生效，且由词库现算 —— 词库更新后自动覆盖，无需逐条维护。
    #    同样排除否定语境：合规文案常写「不得涉及疾病预防与治疗功能」，
    #    那是免责声明而非违规宣称。
    scan = scan_compliance(text, case.industry)
    blockers = [
        item
        for item in scan.hits
        if item.severity == "blocker" and not _is_negated(text, item.term)
    ]
    negated_only = [
        item
        for item in scan.hits
        if item.severity == "blocker" and _is_negated(text, item.term)
    ]
    add(
        "无广告法阻断级用语",
        not blockers,
        "未发现阻断级用语"
        if not blockers
        else "命中阻断级用语：" + "、".join(f"{item.term}（{item.law[:24]}）" for item in blockers[:3]),
    )
    if negated_only:
        add(
            "阻断用语仅出现在免责表述中",
            True,
            "以下词仅出现在否定语境（已豁免）：" + "、".join(item.term for item in negated_only[:3]),
        )

    # 6) 质量分 / 评估分门槛
    if expect.min_quality:
        add(
            f"质量分 ≥ {expect.min_quality}",
            result.quality_score >= expect.min_quality,
            f"实际 {result.quality_score:.1f}",
        )
    if expect.min_judge:
        add(
            f"评估分 ≥ {expect.min_judge}",
            result.judge_final_total >= expect.min_judge,
            f"实际 {result.judge_final_total:.1f}",
        )

    # 7) 返工预算
    add(
        f"返工轮次 ≤ {expect.max_revisions}",
        result.revision_round <= expect.max_revisions,
        f"实际 {result.revision_round} 轮",
    )

    # 8) 调性**不做机器断言**（``min_tone_hits`` 仅记录、不参与判定）。
    #    原因：Brief 的 tone 是抽象描述（「克制」「用数据说话」），不是要逐字出现在正文里的
    #    关键词。若断言「必须命中 N 个调性词」，唯一稳定的达标方式就是在文案里堆这些词 ——
    #    那正好与「克制」背道而驰。调性由人工抽查与 ``reference_points`` 覆盖。
    #    这里仍然记录命中情况，仅供界面展示与人工参考。
    if expect.min_tone_hits:
        tokens = tone_tokens(case.tone)
        hits = [token for token in tokens if token in text]
        checks.append(
            {
                "rule": f"调性关键词命中（参考，不参与判定）",
                "ok": True,
                "detail": f"命中 {len(hits)}/{len(tokens)}：{'、'.join(hits) or '无'}",
            }
        )

    # 9) 强监管用例的首轮门禁行为
    if expect.expect_first_round_blocked is not None:
        add(
            "首轮合规拦截符合预期",
            result.first_round_blocked == expect.expect_first_round_blocked,
            f"首轮被拦截={result.first_round_blocked}，期望={expect.expect_first_round_blocked}",
        )

    # 10) 视频脚本：短视频渠道 / 点名要脚本时必须产出**结构化**脚本。
    #     只检查「有没有」是不够的 —— 分镜缺时长或缺口播的脚本无法开拍。
    if expect.expect_video_script is not None:
        has_script = "video_script" in result.artifact_types
        add(
            "产出视频脚本",
            has_script == expect.expect_video_script,
            f"实际{'有' if has_script else '无'}脚本，期望{'有' if expect.expect_video_script else '无'}",
        )
        if expect.expect_video_script and has_script:
            shots = result.video_shot_count
            voice = result.video_voiceover_count
            add(
                "脚本分镜含时长与口播",
                shots > 0 and voice > 0,
                f"分镜 {shots} 镜、口播 {voice} 段",
            )
            monotonic = result.video_timeline_ok
            add(
                "脚本时间轴单调递增",
                monotonic,
                "时间轴有序" if monotonic else "分镜起始时间未按顺序排列",
            )

    return checks


def _title_of(result: CaseResult, delivered_text: str) -> str:
    """取最终标题（交付文本的第一行）。"""
    first_line = (delivered_text or "").strip().splitlines()
    return first_line[0].strip() if first_line else ""


def coverage() -> dict[str, Any]:
    """渠道 × 行业覆盖矩阵，用于检查是否新增了未覆盖的分支。"""
    cases = load_dataset()
    channels = sorted({case.channel for case in cases if case.channel})
    industries = sorted({case.industry for case in cases if case.industry})
    matrix = [
        {
            "channel": channel,
            "industries": sorted(
                {case.industry for case in cases if case.channel == channel and case.industry}
            ),
            "cases": [case.id for case in cases if case.channel == channel],
        }
        for channel in channels
    ]
    from ..knowledge.industry import CHANNEL_RULES

    all_channels = list(CHANNEL_RULES.keys())
    missing = [channel for channel in all_channels if channel not in channels]
    return {
        "total": len(cases),
        "channels": channels,
        "industries": industries,
        "matrix": matrix,
        "uncovered_channels": missing,
    }


# ------------------------------------------------------------------ #
# 基线读写                                                            #
# ------------------------------------------------------------------ #


def load_baseline() -> dict[str, Any]:
    """读取基线；不存在或损坏时返回空基线（首次运行即可生成）。"""
    try:
        raw = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as error:  # noqa: BLE001
        log.warn("基线文件损坏，按空基线处理", error)
        return {}


def save_baseline(results: list[CaseResult], *, engine: str = "", note: str = "") -> dict[str, Any]:
    """把一组运行结果写为新的基线。

    刻意记录 ``rubric`` 与 ``engine``：分数只有在**同口径、同引擎**下才可比。
    跨版本对比前如果这两项不同，比对结果没有意义。
    """
    payload = {
        "updated_at": now_iso(),
        "rubric": RUBRIC_VERSION,
        "engine": engine or "mock",
        "note": note or "由 scripts/golden_eval.py --update-baseline 生成",
        # 用 GoldenExpectation 过滤掉派生字段（如 checks_failed）：它是给人看的统计，
        # 不是 CaseResult 的构造参数，原样写进基线会让后续读取直接 TypeError。
        "cases": {
            result.id: {
                key: value
                for key, value in result.to_dict(include_text=True).items()
                if key in _CASE_FIELDS
            }
            for result in results
        },
    }
    BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_FILE.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    log.info(f"黄金基线已更新：{len(results)} 条用例（rubric={RUBRIC_VERSION}）")
    return payload


# ------------------------------------------------------------------ #
# 比较                                                                #
# ------------------------------------------------------------------ #


@dataclass
class Difference:
    """一条用例的基线 vs 当前对比结论。"""

    id: str
    verdict: str  # ok / improved / regressed / new / missing / failed
    deltas: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "verdict": self.verdict,
            "deltas": {k: round(v, 2) for k, v in self.deltas.items()},
            "reasons": list(self.reasons),
        }


def compare(
    results: list[CaseResult],
    baseline: dict[str, Any] | None = None,
    *,
    tolerance: float = DEFAULT_TOLERANCE,
    scope: list[str] | None = None,
) -> dict[str, Any]:
    """把当前结果与基线逐项对比。

    判定规则（刻意保守，避免把噪声当回归）：
    * 运行失败 → ``failed``（一定算问题）
    * 基线里没有 → ``new``（不影响通过，但需要跑一次 ``--update-baseline`` 纳入）
    * 基线里有、本次**在范围内**却没跑 → ``missing``（计入问题，防止「跳过困难用例」）
    * 质量分 / 评估分回退超过 ``tolerance`` → ``regressed``
    * 其余提升或持平 → ``improved`` / ``ok``

    **返工轮次增加也算回归**：它是成本与稳定性的直接指标，
    即使总分没掉，多一轮返工也意味着更慢更贵。

    ``scope`` 指定本次**有意**运行的用例范围（``None`` = 全部）。
    ``missing`` 只在范围内判定：否则「只想单跑一条用例排查」会被另外 9 条
    全部判成缺失，部分运行永远无法得到「通过」——那等于逼着人每次都跑全量。
    但**全量运行时**（``scope=None``）缺失依然算失败，跳过用例的成本仍然很高。
    """
    base = baseline if baseline is not None else load_baseline()
    base_cases: dict[str, Any] = dict(base.get("cases") or {})
    rubric_mismatch = bool(base) and str(base.get("rubric") or "") != RUBRIC_VERSION
    expected = set(base_cases) if scope is None else {item for item in scope if item in base_cases}

    diffs: list[Difference] = []
    for result in results:
        current = result.to_dict()
        previous = base_cases.get(result.id)
        if result.status != "completed":
            diffs.append(
                Difference(
                    id=result.id,
                    verdict="failed",
                    reasons=[f"运行未成功完成：{result.status}" + (f"（{result.error}）" if result.error else "")],
                )
            )
            continue
        if previous is None:
            diffs.append(Difference(id=result.id, verdict="new", reasons=["基线中尚无该用例"]))
            continue

        deltas: dict[str, float] = {}
        reasons: list[str] = []
        regressed = False
        improved = False

        for metric in SCORED_METRICS:
            if metric not in previous:
                continue
            delta = float(current.get(metric) or 0) - float(previous.get(metric) or 0)
            deltas[metric] = delta
            if metric == "revision_round":
                # 返工轮次越少越好，方向与分数相反
                if delta > 0:
                    regressed = True
                    reasons.append(f"返工轮次 +{delta:.0f}（成本与稳定性变差）")
                elif delta < 0:
                    improved = True
            else:
                if delta < -tolerance:
                    regressed = True
                    reasons.append(f"{metric} 回退 {delta:.1f}（超过容差 {tolerance}）")
                elif delta > tolerance:
                    improved = True

        # 首轮是否被合规拦截：基线拦住而这次没拦住，属于门禁强度下降
        if previous.get("first_round_blocked") and not current.get("first_round_blocked"):
            regressed = True
            reasons.append("首轮合规未被拦截（监管用例的门禁强度下降）")

        # 内容级期望失败**一律算回归**：分数波动可以容忍，但「品牌名漏了」
        # 「阻断级用语漏出去了」是确定性缺陷，不该因为总分没掉就放过
        failed = result.failed_checks
        if failed:
            regressed = True
            reasons.append(
                "内容级期望未通过 "
                + f"{len(failed)} 项："
                + "；".join(str(item.get("detail") or item.get("rule")) for item in failed[:3])
            )

        verdict = "regressed" if regressed else ("improved" if improved else "ok")
        diffs.append(Difference(id=result.id, verdict=verdict, deltas=deltas, reasons=reasons))

    seen = {result.id for result in results}
    for case_id in sorted(expected - seen):
        diffs.append(Difference(id=case_id, verdict="missing", reasons=["基线中有该用例，本次未运行"]))

    counts: dict[str, int] = {}
    for diff in diffs:
        counts[diff.verdict] = counts.get(diff.verdict, 0) + 1

    return {
        "ok": counts.get("regressed", 0) == 0
        and counts.get("failed", 0) == 0
        and counts.get("missing", 0) == 0,
        "tolerance": tolerance,
        "rubric": RUBRIC_VERSION,
        "baseline_rubric": str(base.get("rubric") or "") or None,
        # 口径不一致时分数不可比，必须显式提示而不是静默给出「通过」
        "rubric_mismatch": rubric_mismatch,
        "baseline_updated_at": base.get("updated_at"),
        "baseline_engine": base.get("engine"),
        # 部分运行：范围内用例全部通过也算通过，但要把「这不是全量」讲清楚
        "partial": scope is not None,
        "scope": sorted(expected) if scope is not None else None,
        "counts": counts,
        "differences": [diff.to_dict() for diff in diffs],
    }


__all__ = [
    "BASELINE_FILE",
    "BRIEFS_DIR",
    "DEFAULT_TOLERANCE",
    "GOLDEN_DIR",
    "SCORED_METRICS",
    "CaseResult",
    "Difference",
    "GoldenBrief",
    "compare",
    "coverage",
    "load_baseline",
    "load_dataset",
    "save_baseline",
]
