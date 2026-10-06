"""创作技能提炼（旁路作业通道，见 capability_samples.md「创作技能提炼」一节）。

定位
----
使用者把**自己的创作作品**喂进任务（往期成稿 / 文案文档 / 字幕文件 / 视频脚本 /
整集视频的理解摘要），本通道从这些作品里**提炼可复用的「创作技能」**：结构骨架、
节奏参数、开场与收束套路、检查清单与反例——一份能直接放进扩展插件 skills 目录的
``SKILL.md``，换选题、换品牌后仍然可用。

技能生产（「什么样的写法值得复用」是判断，不是查表）不在本系统内实现硬编码规则库，
两条通道按诚实边界分工：

* ``rules``（默认，零依赖、零 token）：**真算**的量化提炼——句长、段落、疑问句与
  人称占比、字幕语速等指标从作品正文统计而来，每条结论都带证据。它**不含**模型推理
  出的风格判断，所以产物 ``simulated=true`` 并写明「需要判断请选 llm」；
* ``llm``：走**系统既有模型网关**（``LLM_*``，不另持端点与密钥，避免两处配置漂移）
  做一次结构化提炼，把量化画像当参照、产出带步骤与模板的通用技能。未配真实网关
  **显式失败，绝不假装提炼**。

诚实边界
--------
* 逐字样例（examples）只能是从作品里摘的原句，且标注来源；技能正文不得写入本期
  具体产品名与事实——否则那不是「通用技能」，是一份抄错的稿子。
* 样本太少时如实说明：一篇作品的统计只是那一篇的偏好，不构成通用方法。
* ``llm`` 通道保守估算一次调用纳入 ``cost_budget_usd``，超预算 fail-loud、不提交。

接入形态
--------
与其他旁路通道同构（``app/core/gen_jobs.py`` 作业后端、惰性推进、按租户隔离、
任务删除时回收）；技能**不落黑板**（MEMORY #31），落盘在 ``data/skills/<job_id>/``，
并可一键**沉淀进记忆库**（``template`` 卡片）供后续任务检索复用。
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import DATA_DIR, get_config
from ..llm.engine import _is_local_endpoint, chat
from ..llm.json_utils import as_obj_array, as_str, as_str_array, extract_json
from ..llm.types import ChatMessage, LLMRequest
from ..logger import create_logger
from .clock import now_iso, to_iso
from .digest import collect_documents
from .events import event_bus, new_id
from .gen_jobs import FileJobBackend, PgJobBackend, make_backend
from .tracing import tracer
from .types import ARTIFACT_LABEL, TaskRecord
from .videounderstand import list_jobs as list_videounderstand_jobs

log = create_logger("skillgen")

#: 作业状态机：queued（受理）→ distilling（提炼中）→ done / failed
STATUSES = ("queued", "distilling", "done", "failed")

STORE_FILE = DATA_DIR / "skillgen_jobs.json"
_TABLE = "skill_jobs"
SKILLS_DIR = DATA_DIR / "skills"

#: 短于该字数的文本不算一份「作品」（标题、碎片备注没有统计价值）
_MIN_WORK_CHARS = 40
#: 单次调用预估成本（美元）：一次提炼 = 一次调用，与 videounderstand 同口径的保守值
_EST_COST_PER_SKILL_USD = 0.02
#: llm 通道每份作品进入提示词的字数上限
_CHARS_PER_WORK = 4_000
#: llm 通道提示词的总字数上限（超出即按顺序丢弃并记入 issues）
_PROMPT_CHAR_CAP = 48_000
#: rules 通道的惰性推进时长（秒）：受理后到这里才真统计并落盘
_SAMPLE_SECONDS = 6

#: 断句：在句末标点后切开（**保留标点**，疑问句占比要看结尾），换行仍作硬边界
_SENT_SPLIT = re.compile(r"(?<=[。！？!?；;])|\n+")
#: 句子片段里只剩这些字符时不算一句
_SENT_PUNCT = "。！？!?；;，,、：:…—·「」『』（）()《》 \t"
_DIGIT_RE = re.compile(r"\d")
_SECOND_PERSON = ("你", "您", "your", "You")
_TAG_RE = re.compile(r"<[^>]+>")
_ASS_BRACE_RE = re.compile(r"\{[^}]*}")
_TIME_RE = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})(?:[.,](\d{1,3}))?")
#: 字幕时间轴行：整行**开头**就是「时间戳 --> 时间戳」才算（正文里转述的戳不算，见 _parse_cues）
_TIMING_LINE_RE = re.compile(r"\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}\s*-->\s*\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}")

#: 参与提炼的产物类型（用户作品面：写出来的文字，而非情报或排期）
_ARTIFACT_KINDS = (
    "edited_copy",
    "copy_draft",
    "channel_adaptation",
    "final_delivery",
    "video_script",
    "content_plan",
    "creative_concept",
    "document_digest",
    "effect_report",
)

#: 渠道 → SKILL.md ``name`` 的 ASCII 短名（技能名要能当目录名用）
_CHANNEL_SLUG = {
    "抖音": "douyin",
    "快手": "kuaishou",
    "视频号": "shipinhao",
    "小红书": "xiaohongshu",
    "微博": "weibo",
    "b站": "bilibili",
    "哔哩哔哩": "bilibili",
    "tiktok": "tiktok",
    "youtube": "youtube",
    "公众号": "wechat",
    "知乎": "zhihu",
}

DISTILL_SYSTEM = (
    "你是创作方法论提炼员。只依据给定作品的正文提炼**可换选题、换品牌仍然可用的通用技能**："
    "结构骨架、节奏参数、开场与收束套路、检查清单与反例。不得编造作品里没有的东西；"
    "examples 必须逐字摘自作品并标注来源。技能正文里不得出现本期具体产品名、品牌名与本期事实。"
    "只输出 JSON，不输出任何解释性文字。"
)

DISTILL_SCHEMA = """{
  "name": "（ascii 短横线技能名）",
  "title": "（中文技能名）",
  "description": "（≤200 字：这个技能让谁在什么时候用、产出什么）",
  "when_to_use": ["（适用场景，≤6 条）"],
  "inputs": ["（套用前需要的输入，≤6 条）"],
  "steps": [{"step": "（步骤名）", "detail": "（怎么做，含可量化参数）", "evidence": "（依据本组作品的哪条观察）"}],
  "templates": {"hook": "（开场模板，含占位符）", "outline": ["（主体骨架，≤6 条）"], "closing": "（收束模板）"},
  "checklist": ["（交付前自检项，≤8 条）"],
  "anti_patterns": ["（不该做的事，≤6 条）"],
  "examples": [{"source": "（作品名）", "quote": "（逐字原句）"}]
}"""


# ------------------------------------------------------------------ #
# 作品收集（用户输入的「创作作品」）                                     #
# ------------------------------------------------------------------ #


def _flatten(value: Any, depth: int = 0) -> str:
    """把产物 content 里的文字取平成一段文本（结构化产物没有 ``text`` 时的兜底）。"""
    if depth > 6:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        parts = (_flatten(item, depth + 1) for item in value.values())
    elif isinstance(value, list):
        parts = (_flatten(item, depth + 1) for item in value)
    else:
        return ""
    return "\n".join(part for part in parts if part)


def _cue_time(raw: str) -> float:
    match = _TIME_RE.search(raw or "")
    if not match:
        return -1.0
    hour, minute, second, fraction = match.groups()
    return int(hour) * 3600 + int(minute) * 60 + int(second) + int((fraction or "0")[:3].ljust(3, "0")) / 1000


def _parse_cues(text: str) -> list[dict[str, Any]]:
    """字幕（SRT / WebVTT / ASS）→ ``[{start, text}]``；不是字幕返回空列表。

    后端此前没有字幕解析器（视频工场的解析器在前端）。提炼「口播节奏」必须有
    语速与单条字数，所以这里按最小够用实现：只取起始时间与去掉标记后的正文。

    时间轴行必须**整行以时间戳开头**，且至少凑出 2 条才算字幕文件：研读摘要会把
    字幕时间戳写在句子里（``要点：… 00:00:00,000 --> …``），那种正文若被当成一条
    巨型字幕，整份作品的段落与语速统计会一起失真。
    """
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    if "-->" in normalized:
        cues: list[dict[str, Any]] = []
        for block in re.split(r"\n\s*\n", normalized):
            lines = [line.strip() for line in block.split("\n") if line.strip()]
            timing_index = next((i for i, line in enumerate(lines) if _TIMING_LINE_RE.match(line)), -1)
            if timing_index < 0:
                continue
            timing = lines[timing_index]
            left, _, right = timing.partition("-->")
            body = " ".join(
                line
                for line in lines[timing_index + 1 :]
                if not line.upper().startswith("NOTE")
            )
            body = _TAG_RE.sub("", body).strip()
            if body:
                cues.append(
                    {"start": _cue_time(left), "end": _cue_time(right), "text": body}
                )
        if len(cues) >= 2:
            return cues
    if "Dialogue:" in normalized:
        cues = []
        for line in normalized.split("\n"):
            if not line.startswith("Dialogue:"):
                continue
            parts = line.split(",", 9)
            if len(parts) < 10:
                continue
            body = _ASS_BRACE_RE.sub("", parts[9]).strip()
            if body:
                cues.append({"start": _cue_time(parts[2]), "end": _cue_time(parts[3]), "text": _TAG_RE.sub("", body)})
        return cues
    return []


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = min(max(q, 0.0), 1.0) * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _plain(text: str) -> str:
    return "".join((text or "").split())


def _visible_len(text: str) -> int:
    """字数口径：非空白字符数（换行与缩进不计，与文案编辑器的「字数」一致）。"""
    return len(_plain(text))


def _num(value: Any) -> str:
    """写进文案的数字：整数就去掉尾巴（「159 字」而非「159.0 字」），小数留一位。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number:.0f}" if number == int(number) else f"{number:.1f}"


def _per_sentence(ratio: float, action: str) -> str:
    """占比翻成中文能读的频次：接近每句一次就说「几乎每句」，别写出「每 1 句」。"""
    nth = round(1 / max(float(ratio), 0.01))
    return f"几乎每句{action}" if nth <= 1 else f"约每 {nth} 句{action}"


def profile_text(text: str) -> dict[str, Any]:
    """一份作品的量化画像（字幕先解析成口播文本，再按句统计）。"""
    cues = _parse_cues(text)
    body = "\n".join(str(cue["text"]) for cue in cues) if cues else (text or "")
    # 只剩标点的片段（连续问号被切开的残片）不配算一句
    sentences = [item.strip() for item in _SENT_SPLIT.split(body) if _plain(item).strip(_SENT_PUNCT)]
    lengths = [float(_visible_len(item)) for item in sentences]
    paragraphs = [item.strip() for item in re.split(r"\n{2,}", body) if item.strip()]
    count = len(sentences) or 1
    profile: dict[str, Any] = {
        "chars": _visible_len(body),
        "sentences": len(sentences),
        "sentence_median": round(_quantile(lengths, 0.5), 1),
        "sentence_p90": round(_quantile(lengths, 0.9), 1),
        "paragraphs": len(paragraphs),
        "question_ratio": round(sum(1 for s in sentences if s.endswith(("？", "?"))) / count, 3),
        "second_person_ratio": round(
            sum(1 for s in sentences if any(word in s for word in _SECOND_PERSON)) / count, 3
        ),
        "digit_ratio": round(sum(1 for s in sentences if _DIGIT_RE.search(s)) / count, 3),
        "hook": (sentences[0][:80] if sentences else ""),
        "closing": (sentences[-1][:80] if sentences else ""),
    }
    if cues:
        starts = [float(cue["start"]) for cue in cues if float(cue["start"]) >= 0]
        ends = [float(cue["end"]) for cue in cues if float(cue["end"]) > 0]
        # 语速按「首条开始 → 末条结束」，只看末条开始会把最后一条的时长丢掉
        span = (max(ends) - min(starts)) if starts and ends else 0.0
        cue_lengths = [float(_visible_len(str(cue["text"]))) for cue in cues]
        profile["subtitle"] = {
            "cues": len(cues),
            "span_seconds": round(span, 1),
            "cue_len_median": round(_quantile(cue_lengths, 0.5), 1),
            "cue_len_p90": round(_quantile(cue_lengths, 0.9), 1),
            "chars_per_minute": round(sum(cue_lengths) / (span / 60)) if span >= 5 else 0,
        }
    return profile


def _works_of(task: TaskRecord) -> tuple[list[dict[str, Any]], list[str]]:
    """收集任务里的「创作作品」：文档素材 → 已产出文本产物 → 视频理解摘要。"""
    issues: list[str] = []
    works: list[dict[str, Any]] = []

    for title, text in collect_documents(task.brief, issues):
        if len(text) >= _MIN_WORK_CHARS:
            works.append({"title": title, "origin": "document", "text": text})

    seen_types: set[str] = set()
    for artifact in reversed(task.artifacts):
        kind = str(artifact.type)
        if kind not in _ARTIFACT_KINDS or kind in seen_types:
            continue
        seen_types.add(kind)  # 同一类型只取最新版本，返工稿不重复计入
        text = (artifact.text or "").strip() or _flatten(artifact.content)
        if len(text) < _MIN_WORK_CHARS:
            continue
        works.append(
            {
                "title": artifact.title or ARTIFACT_LABEL.get(kind, kind),
                "origin": f"artifact:{kind}",
                "text": text,
            }
        )

    for job in list_videounderstand_jobs(task_id=task.id, tenant=task.tenant):
        if job.get("status") != "done":
            continue
        summary = job.get("summary") or {}
        text = "\n".join(
            part
            for part in (
                as_str(summary.get("text_brief")),
                "\n".join(as_str_array(summary.get("scenes"))),
            )
            if part
        )
        if len(text) < _MIN_WORK_CHARS:
            continue
        works.append(
            {
                "title": f"《{job.get('video_title') or job.get('video_ref') or '视频'}》视觉摘要",
                "origin": "video_understanding",
                "text": text,
            }
        )

    limit = max(1, get_config().skill_gen.max_works)
    if len(works) > limit:
        issues.append(f"作品共 {len(works)} 份，超出单作业上限 {limit}（SKILLGEN_MAX_WORKS），其余未纳入")
        works = works[:limit]
    kept: list[dict[str, Any]] = []
    for work in works:
        # 正文只在内存里用于提炼，作业记录只留画像与逐字样例（不把用户原文塞进作业库）
        work["profile"] = profile_text(work["text"])
        if work["profile"]["chars"] < _MIN_WORK_CHARS:
            # 字幕 / 带标记的文档按原文长度入围，正文却可能只剩一句：按正文如实剔除
            issues.append(f"《{work['title']}》正文仅 {work['profile']['chars']} 字（<{_MIN_WORK_CHARS}），未计入")
            continue
        kept.append(work)
    return kept, issues


def collect_materials(task: TaskRecord) -> dict[str, Any]:
    """作品 + 画像 + 问题清单（路由层用它告诉前端「有没有东西可提炼」）。"""
    works, issues = _works_of(task)
    return {"works": works, "issues": issues}


def has_materials(task: TaskRecord) -> bool:
    return bool(_works_of(task)[0])


# ------------------------------------------------------------------ #
# rules 通道：量化提炼（真统计，不含模型推理）                           #
# ------------------------------------------------------------------ #


def _aggregate(works: list[dict[str, Any]]) -> dict[str, Any]:
    def median(key: str) -> float:
        values = [
            float(work["profile"].get(key) or 0)
            for work in works
            if float(work["profile"].get(key) or 0) > 0
        ]
        return round(_quantile(values, 0.5), 1)

    subtitles = [work["profile"]["subtitle"] for work in works if "subtitle" in work["profile"]]
    hooks = [float(_visible_len(work["profile"]["hook"])) for work in works if work["profile"]["hook"]]
    return {
        "works": len(works),
        "chars_median": median("chars"),
        "sentences_median": median("sentences"),
        "paragraphs_median": median("paragraphs"),
        "sentence_len_median": median("sentence_median"),
        "sentence_len_p90": median("sentence_p90"),
        "hook_len_median": round(_quantile(hooks, 0.5), 1),
        "question_ratio": median("question_ratio"),
        "second_person_ratio": median("second_person_ratio"),
        "digit_ratio": median("digit_ratio"),
        "subtitled_works": len(subtitles),
        "cue_len_median": round(_quantile([float(item["cue_len_median"]) for item in subtitles], 0.5), 1),
        "chars_per_minute": round(_quantile([float(item["chars_per_minute"]) for item in subtitles], 0.5)),
    }


def _skill_slug(channel: str, form: str) -> str:
    key = (channel or "").strip().lower()
    slug = _CHANNEL_SLUG.get(key)
    if not slug:
        slug = next(
            (value for name, value in _CHANNEL_SLUG.items() if key and (key.startswith(name) or name in key)),
            "",
        )
    if not slug:
        slug = re.sub(r"[^a-z0-9]+", "-", key).strip("-") or "creator"
    return f"{slug}-{form}-skill"


def distill_by_rules(task: TaskRecord, works: list[dict[str, Any]], agg: dict[str, Any]) -> dict[str, Any]:
    """从量化画像生成技能草稿：每条结论都带 evidence（指向本组作品的观察值）。"""
    has_video = any(
        work["origin"] in ("video_understanding", "artifact:video_script")
        or work["profile"].get("subtitle")
        for work in works
    )
    form = "commentary" if has_video else "copy"
    hooks = [work["profile"]["hook"] for work in works if work["profile"]["hook"]]
    closings = [work["profile"]["closing"] for work in works if work["profile"]["closing"]]
    questions = [work for work in works if float(work["profile"]["question_ratio"]) > 0]

    # 面向用户的文案用 _num 格式化（"159 字" 而不是 "159.0 字"），判断仍用 agg 里的数值
    p = {key: _num(value) for key, value in agg.items()}
    paragraphs_shown = _num(agg["paragraphs_median"] or 1)

    steps: list[dict[str, str]] = [
        {
            "step": "开场钩子",
            "detail": (
                (f"首句控制在 {p['hook_len_median']} 字以内" if agg["hook_len_median"] > 0 else "开场先给结论或冲突，不要铺陈")
                + ("，优先疑问句开场" if agg["question_ratio"] >= 0.1 else "")
            ),
            "evidence": f"本组 {len(works)} 份作品首句长度中位 {p['hook_len_median']}，疑问句占比 {p['question_ratio']}",
        },
        {
            "step": "主体分段",
            "detail": (
                f"分 {paragraphs_shown} 段推进，每段一个要点；"
                f"单句 {p['sentence_len_median']} 字上下、最长不超过 {p['sentence_len_p90']} 字"
            ),
            "evidence": f"段落数中位 {paragraphs_shown}，句长中位 {p['sentence_len_median']}／P90 {p['sentence_len_p90']}",
        },
        {
            "step": "全文体量",
            "detail": f"成稿约 {p['chars_median']} 字（约 {p['sentences_median']} 句）",
            "evidence": f"作品字数中位 {p['chars_median']}，句数中位 {p['sentences_median']}",
        },
    ]
    if agg["subtitled_works"]:
        steps.append(
            {
                "step": "口播节奏",
                "detail": f"字幕单条约 {p['cue_len_median']} 字、语速控制在 {p['chars_per_minute']} 字/分钟以内",
                "evidence": f"本组 {agg['subtitled_works']} 份字幕作品的语速中位与单条长度中位",
            }
        )
    if agg["second_person_ratio"] >= 0.1:
        steps.append(
            {
                "step": "对观众说话",
                "detail": _per_sentence(agg["second_person_ratio"], "出现一次第二人称"),
                "evidence": f"第二人称句占比 {p['second_person_ratio']}",
            }
        )
    steps.append(
        {
            "step": "收束与行动号召",
            "detail": "结尾一句收束，回指开场钩子",
            "evidence": f"取自 {len(closings)} 份作品的末句（逐字样例见 examples）",
        }
    )

    checklist = [
        f"首句 ≤ {p['hook_len_median']} 字",
        f"单句 ≤ {p['sentence_len_p90']} 字",
        f"段落数 {paragraphs_shown}（±1）",
        f"全文约 {p['chars_median']} 字",
    ]
    if agg["subtitled_works"]:
        checklist.append(f"字幕单条 ≤ {p['cue_len_median']} 字、语速 ≤ {p['chars_per_minute']} 字/分钟")
    if agg["digit_ratio"] >= 0.15:
        checklist.append(_per_sentence(agg["digit_ratio"], "给一个可核查的数字"))

    anti_patterns = [
        "把 examples 里的逐字原句搬到别的品牌或别的选题——那是本组作品的原文，不是通用表达",
        "只按本技能的结构填空而不给新的事实：结构可复用，内容必须重新做",
    ]
    if len(works) < 2:
        anti_patterns.insert(
            0,
            f"样本只有 {len(works)} 份：这里的骨架只是这一份作品的个人偏好，不构成通用方法，"
            "请至少再投 2–3 份作品后重跑",
        )
    if agg["question_ratio"] < 0.05:
        anti_patterns.append("本组作品几乎不用疑问句开场：不要把「疑问句钩子」当成本组风格硬塞进新作品")

    hook_samples = (questions or works)[:2]
    examples: list[dict[str, str]] = []
    picked: set[str] = set()
    for item, slot in [(w, "hook") for w in hook_samples] + [(w, "closing") for w in works[-2:]]:
        quote = str(item["profile"][slot])
        if not quote or quote in picked:  # 同一句既是甲的开场又是乙的收束时只留一次
            continue
        picked.add(quote)
        examples.append({"source": item["title"], "quote": quote})

    return {
        "name": _skill_slug(task.brief.channel, form),
        "title": f"{task.brief.channel or '本渠道'}{'解说视频' if form == 'commentary' else '文案'}创作技能（量化骨架）",
        "description": (
            f"从 {len(works)} 份《{task.brief.channel or '本渠道'}》作品提炼的结构与节奏技能："
            f"段落 {paragraphs_shown} 段、单句 {p['sentence_len_median']} 字、"
            f"成稿 {p['chars_median']} 字"
            + (f"、字幕语速 {p['chars_per_minute']} 字/分钟" if agg["subtitled_works"] else "")
            + "。套用前替换选题与关键词。"
        ),
        "method": "rules",
        "simulated": True,
        "when_to_use": (
            [
                f"需要在 {task.brief.channel or '同一渠道'} 上批量产出同形态内容时",
                "需要复用同一套结构与节奏，而选题、品牌与产品换掉时",
            ]
            + ([f"口播/字幕节奏需要对齐既有作品（{p['chars_per_minute']} 字/分钟）"] if agg["subtitled_works"] else [])
            + (["开场需要在 1 句内立住钩子时"] if hooks else [])
        ),
        "inputs": [
            "本次选题或主题（技能不含具体事实）",
            f"渠道：{task.brief.channel or '未指定'}",
            "品牌 / 产品 / 受众（用于替换样例里的指代）",
            f"必带关键词：{'、'.join(task.brief.keywords) if task.brief.keywords else '（Brief 未给）'}",
            f"硬约束与禁忌：{'；'.join(task.brief.constraints) if task.brief.constraints else '（Brief 未给）'}",
        ],
        "steps": steps,
        "templates": {
            "hook": hooks[0] if hooks else "",
            "outline": [item["detail"] for item in steps],
            "closing": closings[-1] if closings else "",
        },
        "checklist": checklist,
        "anti_patterns": anti_patterns,
        "examples": examples,
    }


# ------------------------------------------------------------------ #
# llm 通道：走既有模型网关做一次结构化提炼                               #
# ------------------------------------------------------------------ #


def _llm_channel_usable() -> str:
    """真实模型网关是否可用：空串表示可用，否则返回失败原因（不假装提炼）。"""
    cfg = get_config().llm
    if cfg.provider != "openai":
        return (
            "未配置真实模型网关：LLM_PROVIDER=mock 不会产出风格判断，"
            "请把 LLM_PROVIDER 设为 openai，或改用 rules 通道做量化提炼"
        )
    if not cfg.base_url:
        return "未配置 OPENAI_BASE_URL，无法调用提炼网关"
    return ""


def _estimate_cost(provider: str) -> float:
    if provider != "llm":
        return 0.0
    cfg = get_config().llm
    if cfg.provider != "openai" or _is_local_endpoint(cfg.base_url):
        return 0.0
    return round(_EST_COST_PER_SKILL_USD, 6)


def _materials_block(works: list[dict[str, Any]], issues: list[str]) -> str:
    """把作品正文压进提示词：每份截断到 ``_CHARS_PER_WORK``，总量超上限即按序丢弃并记 issues。"""
    blocks: list[str] = []
    used = 0
    dropped = 0
    for work in works:
        text = work["text"][:_CHARS_PER_WORK]
        chunk = (
            f"【作品｜{work['title']}｜来源 {work['origin']}｜{len(work['text'])} 字】\n"
            f"量化画像：{json.dumps({k: v for k, v in work['profile'].items() if k not in ('hook', 'closing')}, ensure_ascii=False)}\n"
            f"正文：{text}"
        )
        if used + len(chunk) > _PROMPT_CHAR_CAP:
            dropped += 1
            continue
        used += len(chunk)
        blocks.append(chunk)
    if dropped:
        issues.append(f"提示词字数超出上限 {_PROMPT_CHAR_CAP}，已丢弃 {dropped} 份作品未进入提炼")
    return "\n\n".join(blocks)


def _normalize_llm_skill(data: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    """收敛模型返回（缺字段回落到 rules 草稿，绝不填空成「看起来完整」）。"""
    templates_raw = data.get("templates") if isinstance(data.get("templates"), dict) else {}
    steps = [
        {
            "step": as_str(item.get("step")),
            "detail": as_str(item.get("detail")),
            "evidence": as_str(item.get("evidence")),
        }
        for item in as_obj_array(data.get("steps"))
        if as_str(item.get("step")) or as_str(item.get("detail"))
    ]
    examples = [
        {"source": as_str(item.get("source")), "quote": as_str(item.get("quote"))[:120]}
        for item in as_obj_array(data.get("examples"))
        if as_str(item.get("quote"))
    ]
    return {
        "name": as_str(data.get("name")) or fallback["name"],
        "title": as_str(data.get("title")) or fallback["title"],
        "description": as_str(data.get("description")) or fallback["description"],
        "method": "llm",
        "simulated": bool(fallback.get("_simulated")),
        "when_to_use": as_str_array(data.get("when_to_use")) or fallback["when_to_use"],
        "inputs": as_str_array(data.get("inputs")) or fallback["inputs"],
        "steps": steps or fallback["steps"],
        "templates": {
            "hook": as_str(templates_raw.get("hook")) or fallback["templates"]["hook"],
            "outline": as_str_array(templates_raw.get("outline")) or fallback["templates"]["outline"],
            "closing": as_str(templates_raw.get("closing")) or fallback["templates"]["closing"],
        },
        "checklist": as_str_array(data.get("checklist")) or fallback["checklist"],
        "anti_patterns": as_str_array(data.get("anti_patterns")) or fallback["anti_patterns"],
        "examples": examples or fallback["examples"],
    }


def distill_by_llm(task: TaskRecord, works: list[dict[str, Any]], agg: dict[str, Any], issues: list[str]) -> dict[str, Any]:
    """一次调用把作品提炼成通用技能；量化画像始终随附（模型给的数字不可信）。"""
    fallback = distill_by_rules(task, works, agg)
    prompt = (
        f"【目标渠道】{task.brief.channel or '未指定'}\n"
        f"【本组作品的汇总画像】{json.dumps(agg, ensure_ascii=False)}\n\n"
        f"【作品正文】\n{_materials_block(works, issues)}\n\n"
        f"请提炼成一份可复用的通用创作技能，严格要求 JSON 结构如下：\n{DISTILL_SCHEMA}"
    )
    response = chat(
        LLMRequest(
            purpose="SKILL.distill",
            messages=[
                ChatMessage(role="system", content=DISTILL_SYSTEM),
                ChatMessage(role="user", content=prompt),
            ],
            context={"works": [work["title"] for work in works], "brief_channel": task.brief.channel},
            json=True,
        )
    )
    parsed = extract_json(response.content)
    if not isinstance(parsed, dict):
        raise ValueError("提炼网关返回内容无法解析为 JSON")
    fallback["_simulated"] = bool(response.simulated)
    skill = _normalize_llm_skill(parsed, fallback)
    if response.degraded_reason:
        issues.append(f"提炼调用已离线降级（{response.degraded_reason}），其步骤为占位内容")
    return skill


# ------------------------------------------------------------------ #
# 落盘：SKILL.md + skill.json + README，装配 zip                        #
# ------------------------------------------------------------------ #


def skill_markdown(skill: dict[str, Any], stats: dict[str, Any], sources: list[dict[str, Any]]) -> str:
    """渲染成扩展插件 skills 目录直接可用的 SKILL.md（frontmatter + 正文）。"""
    method_note = "\n".join(
        [
            "> 提炼方式：`rules` 量化统计。句长 / 段落 / 语速等数字**来自作品正文的真实统计**，",
            "> 但「为什么这样写有效」属风格判断，**未经模型推理**（`simulated=true`）。",
            "> 需要判断力版请把 `provider` 换成 `llm` 重跑。",
        ]
        if skill.get("method") == "rules"
        else (
            [
                "> 提炼方式：`llm` 模型网关推理，量化画像随附于文末。",
                "> 注意：`simulated=true` 时该结论来自离线引擎占位，**不可当方法论结论使用**。",
            ]
            if skill.get("simulated")
            else ["> 提炼方式：`llm` 模型网关推理，量化画像随附于文末。"]
        )
    )
    lines = [
        "---",
        f"name: {skill.get('name')}",
        f"description: {str(skill.get('description') or '').replace(chr(10), ' ')}",
        "---",
        "",
        f"# {skill.get('title')}",
        "",
        method_note,
        "",
        "## 何时使用",
    ]
    lines += [f"- {item}" for item in skill.get("when_to_use") or []]
    lines += ["", "## 套用前需要给什么"]
    lines += [f"- {item}" for item in skill.get("inputs") or []]
    lines += ["", "## 步骤"]
    for index, step in enumerate(skill.get("steps") or [], start=1):
        lines.append(f"{index}. **{step.get('step')}** — {step.get('detail')}")
        if step.get("evidence"):
            lines.append(f"   - 依据：{step['evidence']}")
    templates = skill.get("templates") or {}
    lines += [
        "",
        "## 模板",
        "",
        f"- 开场（原句，套用前换成自己的钩子）：{templates.get('hook') or '（无）'}",
        "- 骨架：",
    ]
    lines += [f"  - {item}" for item in templates.get("outline") or []]
    lines += [
        f"- 收束（原句，套用前换成自己的行动号召）：{templates.get('closing') or '（无）'}",
        "",
        "## 交付前自检",
    ]
    lines += [f"- [ ] {item}" for item in skill.get("checklist") or []]
    lines += ["", "## 反例（不要做）"]
    lines += [f"- {item}" for item in skill.get("anti_patterns") or []]
    lines += ["", "## 来自作品的逐字样例"]
    lines += [f"- 「{item.get('quote')}」——《{item.get('source')}》" for item in skill.get("examples") or []]
    lines += ["", "## 量化画像", ""]
    lines += [
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| 作品份数 | {stats.get('works')} |",
        f"| 成稿字数中位 | {_num(stats.get('chars_median'))} |",
        f"| 段落数中位 | {_num(stats.get('paragraphs_median'))} |",
        f"| 句长中位 / P90 | {_num(stats.get('sentence_len_median'))} / {_num(stats.get('sentence_len_p90'))} |",
        f"| 首句长度中位 | {_num(stats.get('hook_len_median'))} |",
        f"| 疑问句 / 第二人称 / 含数字句占比 | {_num(stats.get('question_ratio'))} / "
        f"{_num(stats.get('second_person_ratio'))} / {_num(stats.get('digit_ratio'))} |",
        f"| 字幕作品数 / 语速中位 | {stats.get('subtitled_works')} / {_num(stats.get('chars_per_minute'))} 字/分钟 |",
    ]
    lines += ["", "## 素材清单", ""]
    lines += [f"- {item.get('title')}（{item.get('origin')}，{item.get('chars')} 字）" for item in sources]
    lines.append("")
    return "\n".join(lines)


def _skill_payload(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job.get("id"),
        "task_id": job.get("task_id"),
        "provider": job.get("provider"),
        "created_at": job.get("created_at"),
        "simulated": bool((job.get("skill") or {}).get("simulated")),
        "skill": job.get("skill") or {},
        "stats": job.get("stats") or {},
        "sources": job.get("sources") or [],
        "issues": job.get("issues") or [],
    }


def _write_files(job: dict[str, Any]) -> None:
    skill = job.get("skill") or {}
    directory = SKILLS_DIR / str(job["id"])
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "skill.json").write_text(
        json.dumps(_skill_payload(job), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    markdown = skill_markdown(skill, job.get("stats") or {}, job.get("sources") or [])
    (directory / "SKILL.md").write_text(markdown, encoding="utf-8")
    readme = [
        f"# {skill.get('title')}（技能包）",
        "",
        f"- 通道：`{job.get('provider')}`｜作品 {job.get('stats', {}).get('works')} 份｜"
        f"simulated={'true' if skill.get('simulated') else 'false'}",
        "",
        "## 装进扩展插件",
        "",
        "把 `SKILL.md` 连同它所在目录名（技能名）放进 Skills 目录即可，例如：",
        "",
        "```bash",
        f"unzip package.zip -d ~/.qoder/skills/{skill.get('name')}",
        "```",
        "",
        "包内文件：`SKILL.md`（技能正文）、`skill.json`（结构化技能 + 量化画像 + 素材清单）、`README.md`。",
        "",
        "> 这些结论来自**你提供的作品**，不含任何外部事实；逐字样例属原作，"
        "换品牌 / 换选题时只复用结构与参数，不要照抄句子。",
    ]
    issues = [str(item) for item in (job.get("issues") or [])]
    if issues:
        readme += ["", "> 提炼时的问题："] + ["> ⚠ " + item for item in issues]
    (directory / "README.md").write_text("\n".join(readme) + "\n", encoding="utf-8")


def skill_dir_of(job: dict[str, Any]) -> Path | None:
    """把作业目录引用解析成真实路径，并守住「只能在 SKILLS_DIR 下」的不变量。"""
    ref = str(job.get("dir") or f"skills/{job.get('id')}")
    if not ref.startswith("skills/"):
        return None
    path = (DATA_DIR / ref).resolve()
    if path.parent != SKILLS_DIR.resolve() or not path.is_dir():
        return None
    return path


def build_package(job: dict[str, Any]) -> Path:
    """装配技能包 zip（SKILL.md + skill.json + README.md），返回落盘路径。"""
    directory = skill_dir_of(job)
    if directory is None:
        raise ValueError("技能目录不存在，无法装配")
    archive = directory / "package.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.name == archive.name:
                continue
            bundle.write(path, path.relative_to(directory).as_posix())
    return archive


# ------------------------------------------------------------------ #
# 状态推进与公共 API                                                   #
# ------------------------------------------------------------------ #


def _parse_iso(raw: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _transition(job: dict[str, Any], status: str, note: str) -> None:
    if job.get("status") == status:
        job["error"] = note if status == "failed" else job.get("error", "")
        return
    previous = str(job.get("status") or "queued")
    job["status"] = status
    job["error"] = note if status == "failed" else ""
    job["history"] = list(job.get("history") or []) + [
        {"ts": now_iso(), "from": previous, "to": status, "note": note[:200]}
    ]
    job["updated_at"] = now_iso()
    event_bus.publish(
        task_id=str(job.get("task_id") or ""),
        type="log",
        message=f"技能提炼：{note}",
        level="warn" if status == "failed" else "info",
        payload={"job_id": job.get("id"), "status": status, "provider": job.get("provider")},
    )


_BACKEND: "FileJobBackend | PgJobBackend | None" = None
_BACKEND_LOCK = threading.RLock()


def _job_backend() -> "FileJobBackend | PgJobBackend":
    global _BACKEND
    if _BACKEND is None:
        with _BACKEND_LOCK:
            if _BACKEND is None:
                _BACKEND = make_backend(STORE_FILE, _TABLE)
    return _BACKEND


def _finish(job: dict[str, Any], skill: dict[str, Any], note: str) -> None:
    """技能落盘 + 作业转 done；写盘失败按失败处理（半成品技能不算交付）。"""
    job["skill"] = skill
    try:
        _write_files(job)
    except OSError as error:
        _transition(job, "failed", f"技能落盘失败：{type(error).__name__}: {error}")
        return
    _transition(job, "done", note)
    job["progress"] = 100
    job["finished_at"] = now_iso()
    job["skill_ref"] = f"skills/{job['id']}/SKILL.md"


def create_job(task: TaskRecord, *, provider: str = "", now: datetime | None = None) -> dict[str, Any]:
    """为任务创建技能提炼作业（``POST /api/tasks/{id}/skills``）。

    任务必须有可提炼的作品（文档素材 / 文本产物 / 视频理解摘要），否则抛 ``ValueError``
    由路由层转 409。
    """
    materials = collect_materials(task)
    works, issues = materials["works"], materials["issues"]
    if not works:
        raise ValueError(
            "该任务里没有可提炼的创作作品（Brief.assets 的文档 / 字幕 / 成稿，"
            "或已产出的文案·视频脚本·视频理解摘要），无从提炼"
            + ("".join(f"；{issue}" for issue in issues))
        )
    cfg = get_config().skill_gen
    # 以请求体实际生效的通道为准，而非全局配置（MEMORY #38 的同类坑）
    chosen = provider if provider in ("rules", "llm") else cfg.provider
    moment = now or datetime.now(timezone.utc)
    stats = _aggregate(works)
    estimated_cost = _estimate_cost(chosen)
    # rules 通道的提炼是纯统计、零 token，受理时就算完；作业完成时才写盘（空转不写文件）
    skill = distill_by_rules(task, works, stats) if chosen == "rules" else None
    job_id = new_id("skill")
    stamp = to_iso(moment)
    job: dict[str, Any] = {
        "id": job_id,
        "task_id": task.id,
        "tenant": task.tenant,
        "provider": chosen,
        "status": "queued",
        "progress": 0,
        "channel": task.brief.channel,
        "sample_seconds": _SAMPLE_SECONDS,
        "estimated_cost_usd": estimated_cost,
        "stats": stats,
        "sources": [
            {"title": work["title"], "origin": work["origin"], "chars": work["profile"]["chars"]}
            for work in works
        ],
        "skill": skill,
        "skill_ref": "",
        "dir": f"skills/{job_id}",
        "issues": list(issues),
        "created_at": stamp,
        "updated_at": stamp,
        "started_at": stamp,
        "finished_at": "",
        "error": "",
        "attempts": 0,
        "history": [{"ts": stamp, "from": "", "to": "queued", "note": "技能提炼作业已受理"}],
    }

    budget = get_config().cost_budget_usd
    if chosen == "llm" and budget > 0 and estimated_cost >= budget:
        _transition(
            job,
            "failed",
            f"成本熔断：按一次提炼调用预估 ${estimated_cost:.3f} 已达预算 ${budget:.2f}，"
            f"未提交提炼（可改用 rules 通道，或调高 COST_BUDGET_USD）",
        )
        _job_backend().create(job)
        return dict(job)

    _job_backend().create(job)

    with tracer.span_on_task(
        task.id,
        "skillgen.distill",
        kind="client",
        attributes={
            "skillgen.job_id": job_id,
            "skillgen.provider": chosen,
            "skillgen.works": len(works),
            "skillgen.estimated_cost_usd": estimated_cost,
        },
    ):
        event_bus.publish(
            task_id=task.id,
            type="log",
            message=f"技能提炼作业已创建（{chosen}，{len(works)} 份作品）",
            payload={"job_id": job_id, "estimated_cost_usd": estimated_cost},
        )
        if chosen == "llm":
            job["attempts"] = int(job.get("attempts") or 0) + 1
            reason = _llm_channel_usable()
            if reason:
                _transition(job, "failed", f"provider=llm {reason}")
            else:
                try:
                    skill = distill_by_llm(task, works, stats, job["issues"])
                except Exception as error:  # noqa: BLE001 - 提炼失败如实呈现，不向上抛
                    _transition(job, "failed", f"提炼失败：{type(error).__name__}: {error}")
                else:
                    _finish(job, skill, f"已提炼《{skill.get('title')}》（{len(works)} 份作品）")
            _job_backend().write_back([job])
    log.info(f"技能提炼作业已创建：{job_id}（task={task.id}, provider={chosen}, status={job['status']}）")
    return dict(job)


def _advance_rules(job: dict[str, Any], now: datetime) -> None:
    """惰性推进：受理时已算好技能，到点才写盘（服务空转时不产生任何文件）。"""
    started = _parse_iso(str(job.get("started_at") or ""))
    if started is None:
        return
    elapsed = (now - started).total_seconds()
    seconds = int(job.get("sample_seconds") or _SAMPLE_SECONDS)
    if elapsed < 1.0:
        _transition(job, "queued", "样例引擎已受理（排队中）")
        job["progress"] = 0
    elif elapsed < seconds:
        _transition(job, "distilling", "按作品正文统计结构与节奏（未调模型）")
        job["progress"] = int(min(1.0, elapsed / max(1e-6, seconds)) * 99)
    else:
        skill = job.get("skill")
        if not isinstance(skill, dict):
            _transition(job, "failed", "作业里没有技能内容（受理时提炼未成功），无法落盘")
            return
        _finish(job, skill, f"已提炼《{skill.get('title')}》（{job.get('stats', {}).get('works')} 份作品，量化骨架）")


def _refresh(job: dict[str, Any], now: datetime) -> None:
    if job.get("status") in ("done", "failed"):
        return
    if job.get("provider") == "llm":
        return  # llm 在受理时已同步终结
    _advance_rules(job, now)


def list_jobs(
    task_id: str | None = None, *, tenant: str | None = None, now: datetime | None = None
) -> list[dict[str, Any]]:
    """列出作业（读取时惰性推进状态机），新→旧排序。"""
    backend = _job_backend()
    moment = now or datetime.now(timezone.utc)
    snapshot = [dict(job) for job in backend.snapshot()]
    changed: list[dict[str, Any]] = []
    for job in snapshot:
        before = str(job.get("status"))
        _refresh(job, moment)
        if str(job.get("status")) != before:
            changed.append(job)
    if changed:
        backend.write_back(changed)
    rows = [
        job
        for job in snapshot
        if (task_id is None or str(job.get("task_id")) == task_id)
        and (tenant is None or str(job.get("tenant")) == tenant)
    ]
    rows.sort(key=lambda job: job.get("created_at") or "", reverse=True)
    return rows


def get_job(job_id: str, *, tenant: str | None = None) -> dict[str, Any] | None:
    return next((row for row in list_jobs(tenant=tenant) if row.get("id") == job_id), None)


def skill_document(job: dict[str, Any]) -> str:
    """读取已落盘的 SKILL.md（缺文件时回落到现场渲染，保证 UI 永远能看到正文）。"""
    directory = skill_dir_of(job)
    if directory is not None:
        try:
            return (directory / "SKILL.md").read_text(encoding="utf-8")
        except OSError:
            pass
    return skill_markdown(job.get("skill") or {}, job.get("stats") or {}, job.get("sources") or [])


#: 记忆卡片正文上限：卡片会被注入下游提示词，整份 SKILL.md 太长（下载与查看走文件）
_MEMORY_CARD_CHARS = 1_200


def memory_card(job: dict[str, Any]) -> dict[str, Any]:
    """把技能收敛成一张 ``template`` 记忆卡（沉淀进记忆库供后续任务检索复用）。"""
    skill = job.get("skill") or {}
    stats = job.get("stats") or {}
    parts = [
        str(skill.get("description") or ""),
        "适用：" + "；".join(str(item) for item in (skill.get("when_to_use") or [])[:3]),
        "步骤："
        + "；".join(
            f"{item.get('step')}：{item.get('detail')}" for item in (skill.get("steps") or [])[:6]
        ),
        "自检：" + "；".join(str(item) for item in (skill.get("checklist") or [])[:5]),
        "慎用：" + "；".join(str(item) for item in (skill.get("anti_patterns") or [])[:3]),
        "（提炼方式 {}，作品 {} 份{}）".format(
            skill.get("method"),
            stats.get("works"),
            "，未经模型推理、只有量化骨架" if skill.get("simulated") else "",
        ),
    ]
    return {
        "type": "template",
        "title": f"创作技能｜{skill.get('title') or skill.get('name') or '未命名'}",
        "content": "\n".join(part for part in parts if part)[:_MEMORY_CARD_CHARS],
        "tags": ["skillgen", str(skill.get("name") or ""), str(job.get("provider") or "")],
        "reuse_hint": "换选题、换品牌后套用结构与参数；逐字样例属原作，不要照抄句子",
    }


def drop_task(task_id: str) -> int:
    """任务删除时回收其技能作业，并删掉技能目录（持久文件只增不减同样是缺陷）。"""
    doomed = [job for job in list_jobs() if str(job.get("task_id")) == task_id]
    purged = _job_backend().drop_task(task_id)
    for job in doomed:
        directory = skill_dir_of(job)
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)
    return purged


__all__ = [
    "STATUSES",
    "SKILLS_DIR",
    "STORE_FILE",
    "build_package",
    "collect_materials",
    "create_job",
    "drop_task",
    "get_job",
    "has_materials",
    "list_jobs",
    "memory_card",
    "profile_text",
    "skill_document",
    "skill_markdown",
]
