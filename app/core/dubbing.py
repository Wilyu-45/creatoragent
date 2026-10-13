"""全自动化配音（真实功能：逐句合成真音频，落盘、可试听、可拼接全轨）。

定位（务必如实理解）
--------------------
台词切分、说话人识别、作业调度、音频落盘、时长测量与时间轴汇总都在本系统内
**真做**；声音「克隆/合成」本身是模型推理，由引擎通道承担，按配置选择：

1. **``sapi`` 本机系统音色（Windows 零依赖，真实出声）**：PowerShell System.Speech
   逐句合成真 wav。注意：该通道**不消费参考音频做克隆**，说话人确定性映射到
   已安装音色，汇总中如实标注——需要真克隆请选 sovits / elevenlabs；
2. **``sovits`` 自建克隆服务**（GPT-SoVITS api_v2 兼容协议，免费离线）：参考音频
   零样本克隆，逐句 POST 返回 wav 字节落盘；
3. **``elevenlabs`` 云端克隆**：上传参考音频建克隆音色（每作业每说话人一次），
   再逐句合成 mp3；按字符计费，纳入成本熔断；
4. **``real`` 自定义配音网关**：「POST 建任务 → GET 查状态」最小契约，厂商私有
   协议由你的网关层消化；
5. **``sample`` 离线联调通道（不出真音频）**：确定性模拟「排队 → 合成 → 完成」，
   产出带 simulated 标记的时间轴汇总，供回归与流程联调；
6. **``auto``**：按 elevenlabs → sovits → sapi → real 择优取第一个已配置可用的
   引擎，全都没配则回退 sample 并在汇总里写明。

跨语言译制配音（dubbing 的行业本义）
------------------------------------
请求带上 ``target_lang`` 时，先把剧情文本**逐句翻译**成目标语言再交引擎合成——
保留说话人与句序，原句留在每段 ``source_text``、配音文本换成译文，时间轴 txt 里
原文与译配对照展示。翻译复用**系统既有 LLM 网关**（``LLM_*``，不另持端点与密钥），
与 skillgen 的 llm 通道同一取舍：配了真实网关才是真译制（``translation_simulated``
为假），离线引擎只能产出**占位串**并**如实标注** ``translation_simulated``——绝不把
占位串冒充真实译文。翻译网关返回与台词句数不齐时**显式失败并点名缺哪句**，
绝不拿原文顶替。译制调用计入成本熔断（离线/本地端点归零，同 LLM 口径）。

引擎通道由**后台 worker 线程逐句推进**（区别于 sample/网关的惰性推进）：已合成的
句子落盘后不重复合成，服务重启后读取时自动复活作业，断点续跑。

输入契约（均由用户提供）
------------------------
* **剧情文本**：逐行解析台词，有标记没标记都要能识别——``说话人：台词``（全/半角冒号）、
  ``【说话人】台词``、小说式 ``甲说：“台词”`` 与 ``“台词。”乙低声说。``（说话动词
  自动剥离取名字）；引号包裹的台词会剥掉外层引号；舞台指示（``场景：``/``（内）``/``△`` 等）
  与确实识别不出说话人的行归入旁白（``DEFAULT_NARRATOR``）并在清单里**如实标注**，
  不静默丢弃、不凭空猜测归属；
* **参考音频**：说话人 → 音频的映射（``voices``）。``assets/`` 下的本地文件**只认
  相对路径**（安全边界与 ``core/videounderstand.py`` 完全一致：拒绝绝对路径与
  ``..`` 越界），公网 http(s) 地址原样透传给网关。real 通道要求**每个说话人都有
  参考音频**——缺一个就显式失败并点名缺谁，绝不回退预设音色假装克隆。

输出：时间轴汇总 + 真音频
--------------------------
每句合成落盘 ``data/dubbing/{job_id}/seg_0001.wav``（经带鉴权路由试听）；作业完成时
生成 ``summary``：每段台词的起止时间（wav 按音频实长测量，非估算）、说话人、源文本、
所用参考音频与配音音频地址；全段均为同参数 wav 时额外拼接整轨 ``full.wav``；可导出为
txt（``GET .../dubbing/{job_id}/timeline``）。

成本是真金白银：elevenlabs 按字符估算、real 网关按句估算，纳入 ``cost_budget_usd``
事前熔断；sapi/自建 sovits 零费用。本地端点计 0 元（同 LLM 引擎口径）。
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import sys
import threading
import wave
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from ..config import ASSETS_DIR, DATA_DIR, EXPORTS_DIR, get_config
from ..llm.engine import _is_local_endpoint, chat
from ..llm.json_utils import as_str, extract_json
from ..llm.types import ChatMessage, LLMRequest
from ..logger import create_logger
from .clock import now_iso
from .events import event_bus, new_id
from .gen_jobs import FileJobBackend, PgJobBackend, make_backend
from .tracing import tracer
from .types import TaskRecord

log = create_logger("dubbing")

#: 作业状态机：queued（受理/排队）→ synthesizing（逐句合成中）→ done / failed
STATUSES = ("queued", "synthesizing", "done", "failed")

#: 作业存储（file 模式 JSON；pg 模式 dubbing_jobs 表，见 storage_contract.md）
STORE_FILE = DATA_DIR / "dubbing_jobs.json"
_TABLE = "dubbing_jobs"

#: 无说话人标记的行归入旁白，汇总里如实标注（不静默改归属）
DEFAULT_NARRATOR = "旁白"

#: 参考音频扩展名白名单：配音通道只吃音频，不收「kind 猜错」的任意文件
VOICE_EXTS = (".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac")

#: real 通道两次远端轮询的最小间隔（秒）：避免列表刷新打爆网关
_HTTP_POLL_INTERVAL = 2.0

#: 每句台词（=一次克隆合成）的保守成本估算（美元），仅用于预算熔断的**事前**判断
_EST_COST_PER_LINE_USD = 0.01

#: elevenlabs 按字符的保守估算单价（美元/字符），同样只用于事前熔断
_EST_COST_PER_CHAR_USD = 0.0003

#: 译制（翻译）是一次真实 LLM 调用：按源文本字符的保守估算单价（美元/字符），
#: 仅用于事前熔断；离线/本地引擎翻译不产生费用（与 LLM 引擎成本口径一致）
_EST_COST_PER_TRANSLATE_CHAR_USD = 0.00002

#: 跨语言译制配音支持的目标语言（代码 → 名称）：翻译复用系统既有 LLM 网关（LLM_*），
#: 合成的文本语种按目标语言交给引擎（见 ``_synth_sovits``）
DUBBING_LANGS: dict[str, str] = {
    "zh": "中文",
    "en": "英语",
    "ja": "日语",
    "ko": "韩语",
    "es": "西班牙语",
    "fr": "法语",
    "de": "德语",
    "ru": "俄语",
}

#: 真实合成引擎（后台 worker 逐句推进，区别于 sample/real 的惰性推进）
ENGINE_PROVIDERS = ("sapi", "sovits", "elevenlabs")

#: auto 择优顺序：云端克隆 → 自建克隆 → 本机系统音色 → 自定义网关
_AUTO_ORDER = ("elevenlabs", "sovits", "sapi", "real")

#: 远端状态 → 作业状态映射（与 videogen/videounderstand 同一契约口径）
_STATUS_MAP = {
    "queued": "queued",
    "pending": "queued",
    "submitted": "queued",
    "processing": "synthesizing",
    "synthesizing": "synthesizing",
    "running": "synthesizing",
    "generating": "synthesizing",
    "done": "done",
    "completed": "done",
    "succeeded": "done",
    "finished": "done",
    "failed": "failed",
    "error": "failed",
}

# ------------------------------------------------------------------ #
# 台词切分与说话人识别（纯文本函数，离线真做、可回归）                   #
# ------------------------------------------------------------------ #

#: ``说话人：台词``——冒号须为全/半角；名字排除时间戳样式的冒号前缀（00:12:34 不是人）
_SPEAKER_LINE = re.compile(
    r"^\s*(?:[(（【\[]\s*)?"
    r"([^：:】)\]]{1,24}?)"
    r"\s*(?:[)）】\])])?\s*[：:]\s*(.+?)\s*$"
)

#: 括号名开头的两种写法：``【甲】台词``（无冒号）与 ``【甲】：台词``（带冒号）
#: 名字段不允许冒号，``：`` 可选——同一形状只写一遍，避免多条正则口径漂移
_BRACKET_SPEAKER = re.compile(r"^\s*[(（【[]\s*([^)）】\]][^：:]{1,24}?)\s*[)）】\])]\s*[：:]?\s*(.+)$")

#: 语气状语与说话动词（长词在前，防「说道」被拆成「说」+「道」）：
#: 用于剧本/小说混合句式，说话人名 = 动词前那一截
_TONE_ADVERB = r"(?:轻声|低声|大声|冷冷|淡淡|缓缓|急忙)?"
_SPEECH_VERB = r"(?:说道|问道|喊道|叫道|答道|笑道|开口说|开口|说|道|问|喊|叫|答|笑|吼|念|喃喃)"

#: 说话人前置的小说式写法：``甲说：“台词”`` / ``乙低声说：“台词”`` → 说话人=甲/乙
#: 必须先于 ``_SPEAKER_LINE`` 试，否则 ``甲说`` 整截会被当成说话人名
_SPEAKER_VERB_HEAD = re.compile(
    rf"^\s*([^：:】)\s]{{1,12}}?){_TONE_ADVERB}{_SPEECH_VERB}\s*[：:]\s*(.+?)\s*$"
)

#: 引号台词 + 说话人后置：``「台词。」乙低声说。`` / ``“台词。”甲说。``
#: 名字段里出现 ``了/着/过`` 等动态助词时按叙述句处理（如「他叹了口气说」），
#: 不当名字标记——宁归旁白如实标注，也不把「他叹了口气」冒充说话人。
#: 同一防护也作用于前置句式与冒号句式（见 ``_is_narration_name``）
_QUOTE_SPEAKER_TAIL = re.compile(
    rf"^\s*[“\"「『]\s*(.+?)\s*[”\"」』]\s*[，,]?\s*"
    rf"([\u4e00-\u9fa5A-Za-z0-9_·]{{1,12}}?){_TONE_ADVERB}{_SPEECH_VERB}\s*[。！!？?…]*\s*$"
)

#: 成对引号（剥外层用）
_QUOTE_PAIRS = {"“": "”", '"': '"', "「": "」", "『": "』"}

#: 舞台指示 / 元信息前缀：这些行不是台词，但也不是「无主之言」——如实标注后归旁白
_DIRECTION_PREFIXES = ("场景", "镜头", "画面", "字幕", "备注", "地点", "时间", "人物", "出场")

#: 括号内纯舞台提示的行（如 ``（内）``、``（风声）``），整行不算台词
_PARENTHETICAL = re.compile(r"^\s*[（(].{0,24}[)）]\s*$")


def _strip_markup(text: str) -> str:
    """去掉行首 markdown 列表记号，让 ``- 甲：你好`` 也能识别出说话人「甲」。"""
    return re.sub(r"^\s*[-*·>]+\s*", "", text)


def _is_stage_direction(line: str) -> bool:
    body = line.strip()
    if not body:
        return False
    if body.startswith(("△", "▲", "注：", "备注")):
        return True
    if _PARENTHETICAL.match(body):
        return True
    head = body.lstrip("#").strip()
    return any(head.startswith(prefix) for prefix in _DIRECTION_PREFIXES)


def _clean_speaker(name: str) -> str:
    """去掉说话人名的包裹符：``【甲】``/``(甲)`` → ``甲``；``乙（低声）`` → ``乙``。"""
    cleaned = name.strip().strip("【】[]()（） \u3000")
    cleaned = re.sub(r"[（(].*$", "", cleaned).strip()
    return cleaned


def _strip_quotes(text: str) -> str:
    """整行被一对引号包裹时剥掉外层：``“台词”`` / ``「台词」`` → ``台词``。"""
    body = (text or "").strip()
    while len(body) >= 2 and body[0] in _QUOTE_PAIRS and body[-1] == _QUOTE_PAIRS[body[0]]:
        body = body[1:-1].strip()
    return body


def _is_narration_name(name: str) -> bool:
    """名字段混着 ``了/着/过`` 等动态助词 = 叙述句（「他叹了口气」），不是说话人名。"""
    return bool(re.search(r"[了着过]", name))


def parse_script(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """把剧情文本切成台词段，识别说话人；返回 ``(lines, warnings)``。

    识别规则（可回归的确定性口径，不做启发式猜测）：

    * ``甲：台词`` / ``【甲】台词`` / ``甲（低声）：台词`` → 说话人 = 甲；
    * ``甲说：“台词”`` / ``乙低声说：“台词”`` → 剥掉「说/道」类说话动词取名字；
    * ``「台词。」乙说。`` / ``“台词。”甲说。`` → 引号内是台词、后置名字是说话人；
      名字段含「了/着/过」（如「他叹了口气说」）视为叙述句，不冒充名字标记；
    * 引号包裹的台词一律剥掉外层引号再配音；
    * 舞台指示行 → 跳过并计数告警；
    * 其余有内容的行（包括完全没标记的正文）→ 归旁白并在每段 ``issue`` 里如实写明，
      宁归旁白也不猜说话人。
    """
    warnings: list[str] = []
    parsed: list[dict[str, Any]] = []
    skipped = 0
    verb_named = 0
    for raw in (text or "").splitlines():
        line = _strip_markup(raw).strip()
        if not line:
            continue
        if _is_stage_direction(line):
            skipped += 1
            continue
        head = _SPEAKER_VERB_HEAD.match(line)
        if head is not None and _is_narration_name(head.group(1)):
            head = None  # 前置名里混着动态助词 = 叙述句，不当说话人标记
        tail = None if head else _QUOTE_SPEAKER_TAIL.match(line)
        if tail is not None and _is_narration_name(tail.group(2)):
            tail = None  # 名字段混着动态助词 = 叙述句不是名字标记
        match = None if (head or tail) else (_SPEAKER_LINE.match(line) or _BRACKET_SPEAKER.match(line))
        if match is not None and _is_narration_name(_clean_speaker(match.group(1))):
            match = None  # 冒号前是叙述句（如「他叹了口气：」）同样不采信，落旁白
        if head:
            speaker = _clean_speaker(head.group(1))
            content = _strip_quotes(head.group(2))
            issue = ""
            verb_named += 1
        elif tail:
            speaker = _clean_speaker(tail.group(2))
            content = tail.group(1).strip()
            issue = ""
            verb_named += 1
        elif match:
            speaker = _clean_speaker(match.group(1))
            content = _strip_quotes(match.group(2))
            if not speaker:  # 只有冒号/括号没有名字，按无标记处理
                speaker, content, issue = DEFAULT_NARRATOR, _strip_quotes(line), "行首有标记但没识别到说话人名"
            else:
                issue = ""
        elif line[0] in _QUOTE_PAIRS:
            speaker = DEFAULT_NARRATOR
            content = _strip_quotes(line)
            issue = "引号台词但没识别到说话人标记，已归入旁白"
        else:
            speaker, content, issue = DEFAULT_NARRATOR, line, "未识别到说话人标记，已归入旁白"
        if not content:
            skipped += 1
            continue
        parsed.append({"line": len(parsed) + 1, "speaker": speaker, "text": content, "issue": issue})
    narrated = sum(1 for item in parsed if item["issue"])
    if narrated:
        warnings.append(f"{narrated} 行没有说话人标记，已归入「{DEFAULT_NARRATOR}」，请核对是否需要人工指认")
    if verb_named:
        warnings.append(
            f"{verb_named} 行按小说式句式识别说话人（甲说：“…”/「…」乙说。），已剥离说话动词取名字"
        )
    if skipped:
        warnings.append(f"{skipped} 行舞台指示/空台词已跳过（不配音）")
    if not parsed:
        raise ValueError(
            "剧情文本里没有解析出任何台词行，配音无从合成（「说话人：台词」「【说话人】台词」"
            "「甲说：“台词”」及无标记旁白行均可解析）"
        )
    return parsed, warnings


def parse_voices(raw: Any) -> dict[str, str]:
    """把「说话人 → 参考音频」映射宽容解析成字典（字符串按行 ``名字=音频``）。"""
    voices: dict[str, str] = {}
    if isinstance(raw, dict):
        items: list[Any] = list(raw.items())
    elif isinstance(raw, str):
        items = [
            tuple(part.split("=", 1)) if "=" in part else tuple(part.split(":", 1))
            for part in (chunk.strip() for chunk in re.split(r"[\n;；]", raw))
            if part.strip()
        ]
    elif isinstance(raw, (list, tuple)):
        items = []
        for element in raw:
            if isinstance(element, dict):
                name = str(element.get("speaker") or element.get("name") or "")
                ref = str(element.get("ref") or element.get("audio") or element.get("voice") or "")
                items.append((name, ref))
            elif isinstance(element, str) and ("=" in element or ":" in element or "：" in element):
                sep = "=" if "=" in element else ("：" if "：" in element else ":")
                head, _, tail = element.partition(sep)
                items.append((head, tail))
            else:
                items.append(("", ""))
    else:
        return voices
    for name, ref in items:
        key, value = _clean_speaker(str(name or "")), str(ref or "").strip()
        if key and value:
            voices[key] = value
    return voices


# ------------------------------------------------------------------ #
# 渲染清单：把剧情文本 + 参考音频翻译成「哪些话该由谁的声音来配」        #
# ------------------------------------------------------------------ #


def _round(value: float) -> float:
    return round(value, 2)


def build_manifest(
    script_text: str, *, voices: dict[str, str] | None = None, provider: str = "sample"
) -> dict[str, Any]:
    """台词切分 + 音色映射 + 时间轴估算，产出可审阅的配音清单。

    时间轴按「先估后配」口径：合成前每段时长 = 文本长度 / 语速（``chars_per_second``
    可配，默认 4.5 字/秒，只作联调估算）；真实引擎完成后按音频实长重建（见
    ``_finalize_engine``），网关通道用回传的真实时长重建（见 ``_finalize_summary``）。
    """
    lines, warnings = parse_script(script_text)
    voice_map = dict(voices or {})
    cfg = get_config().dubbing
    rate = max(0.5, cfg.chars_per_second)

    speakers = sorted({line["speaker"] for line in lines})
    missing = [speaker for speaker in speakers if speaker not in voice_map]
    unused = [name for name in voice_map if name not in speakers]
    if missing:
        warnings.append("缺少参考音频的说话人：" + "、".join(missing))
    if unused:
        warnings.append("提供了参考音频但文本中没有其台词：" + "、".join(unused))

    segments: list[dict[str, Any]] = []
    cursor = 0.0
    for line in lines:
        length = max(1, len(line["text"]))
        duration = _round(max(0.8, length / rate))
        segments.append(
            {
                "index": line["line"],
                "speaker": line["speaker"],
                "text": line["text"],
                "voice_ref": voice_map.get(line["speaker"], ""),
                "start_second": _round(cursor),
                "duration_seconds": duration,
                "end_second": _round(cursor + duration),
                "audio_ref": "",
                "audio_url": "",
                "estimated": True,
                "issue": line["issue"],
            }
        )
        cursor += duration
    return {
        "provider": provider,
        "line_count": len(segments),
        "speakers": speakers,
        "voices": voice_map,
        "missing_voices": missing,
        "chars_per_second": rate,
        "duration_seconds": _round(cursor),
        "segments": segments,
        "warnings": warnings,
    }


# ------------------------------------------------------------------ #
# 跨语言译制：把台词逐句翻译成目标语言（复用系统既有 LLM 网关）          #
# ------------------------------------------------------------------ #


class DubbingTranslateError(RuntimeError):
    """译制翻译失败（网关返回与台词不齐 / 无法解析）：作业显式失败，
    绝不拿原文冒充译文，也不静默丢句。"""


#: 语种粗判：汉字→zh、平假名/片假名→ja、谚文→ko，其余拉丁字母→en
_HIRAGANA_KATAKANA = re.compile(r"[\u3040-\u30ff]")
_HANGUL = re.compile(r"[\uac00-\ud7af\u1100-\u11ff]")
_HAN = re.compile(r"[\u4e00-\u9fff]")


def detect_language(text: str) -> str:
    """从台词粗判源语种（只用于「目标=源则跳过翻译」的判断，不做精细识别）。"""
    body = text or ""
    if _HIRAGANA_KATAKANA.search(body):
        return "ja"
    if _HANGUL.search(body):
        return "ko"
    if _HAN.search(body):
        return "zh"
    return "en"


#: 译制系统提示：配音本地化不是逐字直译，而是「说得出来、对得上口型时长」的译配
_TRANSLATE_SYSTEM = (
    "你是影视译制配音（dubbing）译者。把台词翻译成目标语言，要求："
    "1) 保留每句的语义、语气与人物关系；"
    "2) 面向配音朗读——自然、口语、长度尽量贴近原句，不做逐字硬译；"
    "3) 只翻译台词文本本身，不改动说话人、不加解释；"
    "4) 严格按输入句序返回，一句不多、一句不少，index 与输入一致。"
    '只输出 JSON：{"translations": [{"index": 句号, "text": "译文"}]}。'
)


def _translate_prompt(segments: list[dict[str, Any]], source_lang: str, target_lang: str) -> str:
    src = DUBBING_LANGS.get(source_lang, source_lang or "原文")
    tgt = DUBBING_LANGS.get(target_lang, target_lang)
    script = "\n".join(
        f'{int(seg.get("index") or i + 1)}. {seg.get("speaker") or ""}：{seg.get("text") or ""}'
        for i, seg in enumerate(segments)
    )
    return (
        f"源语言：{src}\n目标语言：{tgt}\n"
        f"请把下列台词逐句译成{tgt}（编号即 index，逐句对齐、只译冒号后的台词正文）：\n{script}"
    )


def _parse_translations(parsed: Any) -> dict[int, str]:
    """宽容解析网关译文：顶层数组或带 translations/segments/results 键的对象都收。"""
    if isinstance(parsed, list):
        arr: list[Any] = parsed
    elif isinstance(parsed, dict):
        arr = []
        for key in ("translations", "segments", "results", "lines", "items", "data"):
            if isinstance(parsed.get(key), list):
                arr = parsed[key]
                break
    else:
        arr = []
    out: dict[int, str] = {}
    for position, item in enumerate(arr, start=1):
        if isinstance(item, dict):
            raw_index = item.get("index")
            text = as_str(
                item.get("text") or item.get("translation") or item.get("target") or item.get("dubbed")
            )
        else:
            raw_index, text = position, as_str(item)
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            index = position
        if index not in out:
            out[index] = text
    return out


def translate_manifest(
    manifest: dict[str, Any], *, target_lang: str, source_lang: str = ""
) -> None:
    """把台词逐句译成目标语言，**原地**改写清单：原句存 ``source_text``、
    配音文本 ``text`` 换成译文，并按译文重估时间轴（合成后仍按音频实长重建）。

    翻译走 ``LLM_*`` 网关；离线/降级引擎产出占位串时如实标 ``translation_simulated``。
    目标语言为空或等于源语言时不翻译（直接配原文）。句数不齐抛
    :class:`DubbingTranslateError`，由调用方转成显式作业失败。"""
    segments = manifest.get("segments") or []
    src = (source_lang or "").strip().lower() or detect_language(
        "\n".join(str(seg.get("text") or "") for seg in segments)
    )
    tgt = (target_lang or "").strip().lower()
    manifest["source_lang"] = src
    manifest["target_lang"] = tgt
    if not segments or not tgt or tgt == src:
        manifest["translated"] = False
        return

    payload = [
        {
            "index": int(seg.get("index") or i + 1),
            "speaker": str(seg.get("speaker") or ""),
            "text": str(seg.get("text") or ""),
        }
        for i, seg in enumerate(segments)
    ]
    response = chat(
        LLMRequest(
            purpose="DUB.translate",
            json=True,
            messages=[
                ChatMessage(role="system", content=_TRANSLATE_SYSTEM),
                ChatMessage(role="user", content=_translate_prompt(payload, src, tgt)),
            ],
            context={"segments": payload, "source_lang": src, "target_lang": tgt},
        )
    )
    translations = _parse_translations(extract_json(response.content))
    missing = [
        int(seg.get("index") or i + 1)
        for i, seg in enumerate(segments)
        if not (translations.get(int(seg.get("index") or i + 1)) or "").strip()
    ]
    if missing:
        raise DubbingTranslateError(
            f"译制失败：翻译网关未返回 {len(missing)} 句译文（缺句号 {missing[:5]}"
            f"{'…' if len(missing) > 5 else ''}），已中止配音，绝不拿原文冒充译文"
        )

    rate = max(0.5, get_config().dubbing.chars_per_second)
    cursor = 0.0
    for i, seg in enumerate(segments):
        index = int(seg.get("index") or i + 1)
        seg["source_text"] = str(seg.get("text") or "")
        seg["text"] = translations[index].strip()
        duration = _round(max(0.8, len(seg["text"]) / rate))
        seg["duration_seconds"] = duration
        seg["start_second"] = _round(cursor)
        seg["end_second"] = _round(cursor + duration)
        seg["estimated"] = True
        cursor += duration
    manifest["translated"] = True
    manifest["duration_seconds"] = _round(cursor)

    simulated = bool(getattr(response, "simulated", False)) or bool(
        getattr(response, "degraded_reason", None)
    )
    manifest["translation_simulated"] = simulated
    warnings = list(manifest.get("warnings") or [])
    src_label = DUBBING_LANGS.get(src, src or "原文")
    tgt_label = DUBBING_LANGS.get(tgt, tgt)
    warnings.insert(
        0, f"已启用跨语言译制：{src_label} → {tgt_label}，逐句保留说话人与句序（原句见每段 source_text）"
    )
    if simulated:
        reason = str(getattr(response, "degraded_reason", "") or "")
        warnings.insert(
            1,
            "译文由离线/降级引擎产出，是占位串（非真实翻译）；要真译制请配置真实 LLM 网关（LLM_*）后重试"
            + (f"（降级原因：{reason}）" if reason else ""),
        )
    manifest["warnings"] = warnings


# ------------------------------------------------------------------ #
# 参考音频引用解析（安全边界与 videounderstand 完全一致）                #
# ------------------------------------------------------------------ #


def voice_source(ref: str) -> str:
    """参考音频来源分类：``assets/`` 本地相对路径 / http(s) 远端 / 其余不认。"""
    text = (ref or "").strip()
    if urlparse(text).scheme in ("http", "https"):
        return "remote"
    return "local" if text else ""


def resolve_voice_path(ref: str) -> tuple[Path | None, str]:
    """把 ``assets/`` 下的相对音频路径解析成真实文件路径（不读字节、不限体积）。"""
    text = (ref or "").strip().removeprefix("assets/")
    parts = [seg for seg in text.replace("\\", "/").split("/") if seg not in ("", ".")]
    if not parts or any(seg == ".." for seg in parts):
        return None, "参考音频只接受 assets/ 目录内的相对文件名（拒绝绝对路径与 .. 越界）"
    try:
        root = ASSETS_DIR.resolve()
        path = ASSETS_DIR.joinpath(*parts).resolve()
        if not path.is_relative_to(root):
            return None, "参考音频路径越出 assets/ 目录"
    except OSError:
        return None, "参考音频路径无法解析"
    if not path.is_file():
        return None, f"assets/ 目录下没有音频文件「{ref}」"
    return path, ""


def save_voice(name: str, b64: str) -> dict[str, Any]:
    """把 base64 参考音频落盘到 ``ASSETS_DIR``，返回 ``assets/`` 相对引用。

    只收白名单扩展名与 ``[\\w.-]`` 文件名（与 imagegen 落盘同一口径），
    解析后必须仍落在 assets/ 内——不接受任意路径写入。
    """
    safe = re.sub(r"[^\w.\-]+", "-", (name or "").strip()).strip("-.")[:64]
    if not safe or "." not in safe:
        raise ValueError("音频文件名无效（需含扩展名，如 narrator.wav）")
    ext = Path(safe).suffix.lower()
    if ext not in VOICE_EXTS:
        raise ValueError(f"不支持的音频格式 {ext}（只收 {'、'.join(VOICE_EXTS)}）")
    try:
        blob = base64.b64decode(b64.split(",", 1)[-1])
    except (ValueError, TypeError) as error:
        raise ValueError(f"音频数据无法解码：{error}") from error
    if not blob:
        raise ValueError("音频数据为空")
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    path = (ASSETS_DIR / safe).resolve()
    if path.parent != ASSETS_DIR.resolve():
        raise ValueError("音频文件名越出 assets/ 目录")
    path.write_bytes(blob)
    return {"name": safe, "ref": f"assets/{safe}", "bytes": len(blob)}


# ------------------------------------------------------------------ #
# 成本估算（真实引擎按各自计费口径；只用于事前熔断）                     #
# ------------------------------------------------------------------ #


def _estimate_synth_cost(manifest: dict[str, Any], provider: str) -> float:
    """合成费用：sample/sapi/自建 sovits 零费用归 0；elevenlabs 按字符保守估算；
    real 网关本地/归零端点归 0，否则按句估算（宁可高估触发熔断）。"""
    if provider in ("sample", "sapi", "sovits", "auto"):
        return 0.0
    line_count = int(manifest.get("line_count") or 0)
    if provider == "elevenlabs":
        chars = sum(len(str(seg.get("text") or "")) for seg in manifest.get("segments") or [])
        return _EST_COST_PER_CHAR_USD * max(0, chars)
    cfg = get_config().dubbing
    if not cfg.api_url or _is_local_endpoint(cfg.api_url):
        return 0.0
    return _EST_COST_PER_LINE_USD * max(1, line_count)


def _estimate_translate_cost(manifest: dict[str, Any]) -> float:
    """译制翻译费：仅当真配了非本地 LLM 网关时才估（离线/本地翻译归 0，
    与 LLM 引擎成本口径一致），按源文本字符保守估算。"""
    if not manifest.get("translated"):
        return 0.0
    llm_cfg = get_config().llm
    base_url = (llm_cfg.base_url or "").strip()
    if llm_cfg.provider != "openai" or not base_url or _is_local_endpoint(base_url):
        return 0.0
    chars = sum(
        len(str(seg.get("source_text") or seg.get("text") or ""))
        for seg in manifest.get("segments") or []
    )
    return _EST_COST_PER_TRANSLATE_CHAR_USD * max(0, chars)


def _estimate_cost(manifest: dict[str, Any], provider: str) -> float:
    """事前熔断总估算 = 合成 + 译制（译制仅在启用且走真实网关时非零）。"""
    return round(_estimate_synth_cost(manifest, provider) + _estimate_translate_cost(manifest), 6)


# ------------------------------------------------------------------ #
# sample 通道：按流逝时间惰性推进，产出带 simulated 标记的时间轴汇总      #
# ------------------------------------------------------------------ #


def _sample_seconds(manifest: dict[str, Any]) -> int:
    """样例合成时长：随台词句数变化但封顶，让生命周期可见又不拖慢联调。"""
    return min(25, max(6, 2 + int(manifest.get("line_count") or 1)))


def _sample_summary(job: dict[str, Any]) -> dict[str, Any]:
    """确定性模拟：每段挂 ``sample://`` 地址，simulated=true，绝不假装真音频。"""
    manifest = job.get("manifest") or {}
    segments = []
    for seg in manifest.get("segments") or []:
        item = dict(seg)
        item["audio_url"] = f"sample://dubbing/{job['id']}/{item['index']}.wav"
        item["estimated"] = False  # 样例时长即「成片」口径，不再标估算
        segments.append(item)
    return _build_summary(job, segments, simulated=True)


def _advance_sample(job: dict[str, Any], now: datetime) -> None:
    started = _parse_iso(str(job.get("started_at") or ""))
    if started is None:
        return
    elapsed = (now - started).total_seconds()
    seconds = int(job.get("sample_seconds") or 8)
    if elapsed < 1.0:
        _transition(job, "queued", "sample 离线通道已受理（排队中）")
        job["progress"] = 0
    elif elapsed < seconds:
        _transition(job, "synthesizing", f"sample 通道逐句模拟中（{seconds}s 总时长）")
        job["progress"] = min(99, int(elapsed / seconds * 100))
    else:
        _transition(job, "done", "sample 通道完成（模拟音频地址，非真实合成）")
        job["progress"] = 100
        job["finished_at"] = now_iso()
        job["summary"] = _sample_summary(job)


# ------------------------------------------------------------------ #
# real 通道：POST 提交台词清单 → GET 惰性轮询取回配音结果               #
# ------------------------------------------------------------------ #


def _auth_headers() -> dict[str, str]:
    cfg = get_config().dubbing
    headers: dict[str, str] = {}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    traceparent = tracer.current_traceparent()
    if traceparent:
        headers["traceparent"] = traceparent
    return headers


def _submit_http(job: dict[str, Any]) -> None:
    """向配音网关提交整份台词清单（网关契约：POST → 2xx + ``{"job_id": "..."}``）。

    前置任一不满足即显式失败、绝不假装配音：未配网关 / 有说话人缺参考音频 /
    本地参考音频文件不存在。
    """
    cfg = get_config().dubbing
    if not cfg.api_url:
        _transition(job, "failed", "未配置 DUBBING_API_URL，无法提交配音任务")
        return
    manifest = job.get("manifest") or {}
    missing = list(manifest.get("missing_voices") or [])
    if missing:
        _transition(job, "failed", "以下说话人没有参考音频，声音克隆无法进行：" + "、".join(missing))
        return
    payload_segments = []
    for seg in manifest.get("segments") or []:
        ref = str(seg.get("voice_ref") or "")
        item = dict(seg)
        if voice_source(ref) == "local":
            path, issue = resolve_voice_path(ref)
            if path is None:
                _transition(job, "failed", f"第 {seg.get('index')} 句的参考音频无效：{issue}")
                return
            item["voice_path"] = str(path)
        payload_segments.append(item)
    try:
        response = httpx.post(
            cfg.api_url,
            json={
                "job_id": job["id"],
                "task_id": job["task_id"],
                "tenant": job.get("tenant") or "",
                "voices": manifest.get("voices") or {},
                "segments": payload_segments,
            },
            headers=_auth_headers(),
            timeout=max(1.0, cfg.timeout_ms / 1000.0),
        )
    except Exception as error:  # noqa: BLE001 - 网络异常转成任务失败，不向上抛
        job["attempts"] = int(job.get("attempts") or 0) + 1
        _transition(job, "failed", f"提交失败：{type(error).__name__}: {error}")
        return
    job["attempts"] = int(job.get("attempts") or 0) + 1
    if response.status_code >= 400:
        _transition(job, "failed", f"提交被拒绝：HTTP {response.status_code} {(response.text or '')[:160]}")
        return
    remote = ""
    try:
        body = response.json()
        remote = str(body.get("job_id") or body.get("id") or "")
    except ValueError:
        pass
    job["remote_id"] = remote
    _transition(job, "queued", f"网关已受理配音清单（job_id={remote or '未知'}）")


def _finalize_summary(job: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
    """把网关回传收敛成时间轴汇总：优先用 ``segments``，只有 ``audios`` 时按序对齐。

    缺字段用空值兜底、句数不齐如实记入 issues——**绝不拿估算值冒充真实配音结果**。
    """
    manifest = job.get("manifest") or {}
    base_segments = list(manifest.get("segments") or [])
    raw_segments = body.get("segments")
    raw_audios = body.get("audios")
    merged: list[dict[str, Any]] = []
    if isinstance(raw_segments, list) and raw_segments:
        by_index = {
            int(item.get("index") if isinstance(item, dict) else 0) or (i + 1): item
            for i, item in enumerate(raw_segments)
            if isinstance(item, dict)
        }
        cursor = 0.0
        for position, seg in enumerate(base_segments, start=1):
            item = dict(seg)
            returned = by_index.get(position) or {}
            text = str(returned.get("text") or "").strip()
            if text and text != item["text"]:
                item["issue"] = (item["issue"] + "；" if item.get("issue") else "") + "网关回传文本与源文本不一致"
            duration = returned.get("duration_seconds")
            duration = float(duration) if isinstance(duration, (int, float)) else item["duration_seconds"]
            item["audio_url"] = str(returned.get("audio_url") or returned.get("url") or "")
            item["duration_seconds"] = round(duration, 2)
            item["start_second"] = round(cursor, 2)
            item["end_second"] = round(cursor + duration, 2)
            item["estimated"] = False
            cursor += duration
            merged.append(item)
    elif isinstance(raw_audios, list) and raw_audios:
        # 只有音频列表：按序对齐源文本；句数不齐必须如实告警
        if len(raw_audios) != len(base_segments):
            job["issues"] = list(job.get("issues") or []) + [
                f"网关回传 {len(raw_audios)} 段音频，与台词 {len(base_segments)} 句不齐，请人工核对"
            ]
        cursor = 0.0
        for position, seg in enumerate(base_segments):
            item = dict(seg)
            audio = raw_audios[position] if position < len(raw_audios) else {}
            audio = audio if isinstance(audio, dict) else {"audio_url": str(audio)}
            duration = audio.get("duration_seconds")
            duration = float(duration) if isinstance(duration, (int, float)) else item["duration_seconds"]
            item["audio_url"] = str(audio.get("audio_url") or audio.get("url") or "")
            item["duration_seconds"] = round(duration, 2)
            item["start_second"] = round(cursor, 2)
            item["end_second"] = round(cursor + duration, 2)
            item["estimated"] = False
            cursor += duration
            merged.append(item)
    else:
        job["issues"] = list(job.get("issues") or []) + ["网关标记完成但未返回可解析的配音段落清单"]
        return _build_summary(job, [], simulated=False)
    return _build_summary(job, merged, simulated=False)


def _poll_http(job: dict[str, Any], now: datetime) -> None:
    """查询远端状态（样例契约：GET → ``{"status": "...", "segments": [...]}``）。"""
    cfg = get_config().dubbing
    last_poll = _parse_iso(str(job.get("last_poll_at") or ""))
    if last_poll is not None and (now - last_poll).total_seconds() < _HTTP_POLL_INTERVAL:
        return
    job["last_poll_at"] = now_iso()
    remote_id = str(job.get("remote_id") or job["id"])
    try:
        response = httpx.get(
            f"{cfg.api_url.rstrip('/')}/{remote_id}",
            headers=_auth_headers(),
            timeout=max(1.0, cfg.timeout_ms / 1000.0),
        )
    except Exception as error:  # noqa: BLE001
        job["error"] = f"轮询失败：{type(error).__name__}: {error}"
        return
    if response.status_code >= 400:
        job["error"] = f"轮询失败：HTTP {response.status_code}"
        return
    try:
        body = response.json()
    except ValueError:
        job["error"] = "轮询响应不是 JSON"
        return
    body = body if isinstance(body, dict) else {}
    status = _STATUS_MAP.get(str(body.get("status") or "").strip().lower(), "")
    if status == "done":
        job["summary"] = _finalize_summary(job, body)
        _transition(job, "done", "网关已产出配音时间轴汇总")
        job["progress"] = 100
        job["finished_at"] = now_iso()
    elif status == "failed":
        _transition(job, "failed", str(body.get("error") or body.get("message") or "网关配音失败"))
    elif status in ("queued", "synthesizing"):
        _transition(job, status, "网关逐句合成中")
        job["progress"] = max(int(job.get("progress") or 0), 10)


def _advance_real(job: dict[str, Any], now: datetime) -> None:
    if job.get("status") == "queued" and not job.get("remote_id") and not job.get("submitted"):
        job["submitted"] = True
        _submit_http(job)
        return
    if job.get("status") in ("queued", "synthesizing"):
        _poll_http(job, now)


# ------------------------------------------------------------------ #
# 引擎通道：sapi / sovits / elevenlabs 真实逐句合成（worker 线程推进）   #
# ------------------------------------------------------------------ #


class DubbingEngineError(RuntimeError):
    """引擎合成失败（点名句子/说话人/原因），worker 捕获后转作业失败。"""


#: sapi 每批交给一个 PowerShell 进程合成的句数（平衡进度粒度与进程启动开销）
_SAPI_CHUNK = 10

#: 批量合成脚本：逐句新建 synthesizer（Dispose 时刷盘），只写 wav 不出声
_SAPI_PS = r"""
param([Parameter(Mandatory=$true)][string]$ManifestPath)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$items = [IO.File]::ReadAllText($ManifestPath, [Text.Encoding]::UTF8) | ConvertFrom-Json
foreach ($item in $items) {
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    try { if ($item.voice) { $synth.SelectVoice($item.voice) } } catch { }
    $synth.SetOutputToWaveFile($item.out)
    $synth.Speak([string]$item.text)
    $synth.Dispose()
}
"""

_SAPI_LIST_PS = (
    "Add-Type -AssemblyName System.Speech; "
    "(New-Object System.Speech.Synthesis.SpeechSynthesizer).GetInstalledVoices()"
    " | ForEach-Object { $_.VoiceInfo.Name + '|' + $_.VoiceInfo.Culture }"
)

_SAPI_VOICES: list[tuple[str, str]] | None = None
_SAPI_VOICES_LOCK = threading.Lock()

#: CJK 正文判定：含中日韩汉字的文本必须选 zh-* 音色，英文系统音色读中文 = 静音
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

#: 正在跑 worker 的作业 id（防重复启动；进程重启后集合为空，读取时自动复活）
_WORKERS: set[str] = set()
_WORKERS_LOCK = threading.Lock()


def resolve_provider(requested: str = "") -> tuple[str, str]:
    """定下实际通道。``auto``：按 _AUTO_ORDER 取第一个可用的；全不可用回退 sample。

    返回 ``(通道, 说明)``：说明非空 = auto 的择优结果或回退原因，写进清单 warnings，
    让用户知道声音到底是谁合成的，绝不默默降级。"""
    cfg = get_config().dubbing
    wanted = (requested or cfg.provider or "sample").strip().lower()
    valid = ("sample", "sapi", "sovits", "elevenlabs", "real")
    if wanted != "auto":
        return (wanted if wanted in valid else "sample"), ""
    for engine in _AUTO_ORDER:
        available, _ = _engine_available(engine)
        if available:
            return engine, f"auto 选中引擎通道：{engine}"
    if cfg.provider == "auto":
        return "sample", "auto 未找到任何已配置的合成引擎（elevenlabs/sovits/sapi/real 均不可用），本次为 sample 模拟音频"
    return "sample", ""


def _engine_available(engine: str) -> tuple[bool, str]:
    """引擎可用性判断与不可用原因（缺配置就点名缺哪个变量）。"""
    cfg = get_config().dubbing
    if engine == "elevenlabs":
        return bool(cfg.elevenlabs_api_key), "未配置 DUBBING_ELEVENLABS_API_KEY"
    if engine == "sovits":
        return bool(cfg.sovits_base_url), "未配置 DUBBING_SOVITS_BASE_URL（自建 GPT-SoVITS 服务地址）"
    if engine == "sapi":
        if sys.platform != "win32" or shutil.which("powershell") is None:
            return False, "sapi 仅在装有 PowerShell 的 Windows 上可用"
        return bool(_sapi_voice_pool()), "本机没有已安装的系统音色"
    if engine == "real":
        return bool(cfg.api_url), "未配置 DUBBING_API_URL"
    return False, f"未知通道 {engine}"


def _engine_preflight(job: dict[str, Any]) -> str:
    """引擎通道前置检查：不满足返回显式失败原因（'' = 就绪）。

    克隆类引擎（sovits/elevenlabs）缺任一说话人的参考音频即失败并点名，
    绝不回退预设音色假装克隆——与 real 网关同一口径。"""
    provider = str(job.get("provider") or "")
    if provider not in ENGINE_PROVIDERS:
        return ""
    available, reason = _engine_available(provider)
    if not available:
        return f"{provider} 通道不可用：{reason}"
    manifest = job.get("manifest") or {}
    if provider in ("sovits", "elevenlabs"):
        missing = [str(name) for name in manifest.get("missing_voices") or []]
        if missing:
            return "以下说话人没有参考音频，声音克隆无法进行：" + "、".join(missing)
        for seg in manifest.get("segments") or []:
            ref = str(seg.get("voice_ref") or "")
            if voice_source(ref) == "local":
                path, issue = resolve_voice_path(ref)
                if path is None:
                    return f"第 {seg.get('index')} 句的参考音频无效：{issue}"
    return ""


# --------------------------- 音频落盘与时长 --------------------------- #


def _safe_job_part(job_id: str) -> str:
    return re.sub(r"[^\w.-]+", "-", str(job_id or "")).strip("-.")


def _audio_dir(job_id: str) -> Path:
    """作业音频目录 ``data/dubbing/{job_id}/``（job_id 消毒后拼接，不放开任意路径）。"""
    return DATA_DIR / "dubbing" / _safe_job_part(job_id)


def resolve_audio_path(ref: str, job_id: str) -> tuple[Path | None, str]:
    """解析合成音频引用 ``dubbing/<job_id>/<文件>`` → 真实路径。

    与参考音频同一安全口径：拒绝绝对路径与 ``..``，且引用里的作业 id 必须与
    路由层已鉴权的作业一致（拿别人的 job_id 也读不到自己的文件，反之亦然）。"""
    text = (ref or "").strip()
    if urlparse(text).scheme in ("http", "https"):
        return None, "该引用的音频在远端网关，请直接使用网关回传的 audio_url"
    parts = [seg for seg in text.replace("\\", "/").split("/") if seg not in ("", ".")]
    if len(parts) != 3 or parts[0] != "dubbing" or any(seg == ".." for seg in parts):
        return None, "合成音频只认 dubbing/<作业id>/<文件名> 形式的相对引用（拒绝绝对路径与 .. 越界）"
    if parts[1] != _safe_job_part(job_id):
        return None, "音频引用不属于该作业"
    if not re.fullmatch(r"[\w.-]+", parts[2]) or parts[2].startswith("."):
        return None, "非法的音频文件名"
    path = _audio_dir(parts[1]) / parts[2]
    if not path.is_file():
        return None, f"音频文件不存在：{ref}"
    return path, ""


def _file_duration(path: Path) -> tuple[float | None, bool]:
    """读音频实长，返回 ``(秒, 是否实测)``。

    wav 用标准库 ``wave`` 按帧数/采样率**测量**；mp3 按 128kbps 恒定码率粗估
    （仅适用于 elevenlabs ``mp3_44100_128`` 输出），不叫实测——estimated 标记
    如实传给前端与 txt。"""
    try:
        if path.suffix.lower() == ".wav":
            with wave.open(str(path), "rb") as handle:
                rate, frames = handle.getframerate(), handle.getnframes()
            if rate > 0:
                # frames=0 也照实返回 0.0s（测得出 = 静音，比「测不出」更诚实）
                return round(frames / rate, 2), True
        if path.suffix.lower() == ".mp3":
            size = path.stat().st_size
            if size > 0:
                return round(max(0.5, (size - 512) * 8 / 128_000), 2), False
    except (OSError, ValueError, wave.Error):
        return None, False
    return None, False


def _attach_segment_file(job: dict[str, Any], seg: dict[str, Any], path: Path) -> None:
    """把已落盘的合成音频挂到段落：audio_ref / 可试听的鉴权路由 URL / 实测时长。"""
    ref = f"dubbing/{_safe_job_part(str(job['id']))}/{path.name}"
    duration, measured = _file_duration(path)
    seg["audio_ref"] = ref
    seg["audio_url"] = (
        f"/api/tasks/{job['task_id']}/dubbing/{job['id']}/audio?ref={quote(ref)}"
    )
    if duration is not None:
        seg["duration_seconds"] = duration
    seg["estimated"] = not measured


def _store_segment_blob(job: dict[str, Any], seg: dict[str, Any], blob: bytes, ext: str) -> None:
    """合成字节落盘（先 .part 再改名，不留半文件），再挂到段落。"""
    directory = _audio_dir(str(job["id"]))
    directory.mkdir(parents=True, exist_ok=True)
    name = f"seg_{int(seg.get('index') or 0):04d}{ext}"
    tmp = directory / (name + ".part")
    tmp.write_bytes(blob)
    tmp.replace(directory / name)
    _attach_segment_file(job, seg, directory / name)


def _mark_progress(job: dict[str, Any], done: int, total: int) -> None:
    """逐句回写进度（真实合成可能几分钟，面板要看到它在动）。"""
    job["progress"] = min(95, int(done / max(1, total) * 95))
    job["updated_at"] = now_iso()
    _job_backend().write_back([job])


# ------------------------------ sapi ------------------------------ #


def _sapi_voice_pool() -> list[tuple[str, str]]:
    """已安装系统音色 ``(名称, 语种)``（进程内缓存）；``DUBBING_SAPI_VOICES`` 可限定子集。"""
    global _SAPI_VOICES
    with _SAPI_VOICES_LOCK:
        if _SAPI_VOICES is None:
            names: list[tuple[str, str]] = []
            try:
                out = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", _SAPI_LIST_PS],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                for line in (out.stdout or "").splitlines():
                    name, _, culture = line.strip().partition("|")
                    if name:
                        names.append((name, culture.strip() or ""))
            except (OSError, subprocess.SubprocessError):
                names = []
            _SAPI_VOICES = names
        pool = list(_SAPI_VOICES)
    wanted = [v.strip() for v in (get_config().dubbing.sapi_voices or "").split(",") if v.strip()]
    if wanted:
        pool = [item for item in pool if any(item[0].lower() == w.lower() for w in wanted)]
    return pool


def _sapi_pick_voice(pool: list[tuple[str, str]], speaker: str, text: str) -> str:
    """说话人 → 音色的**确定性**映射（crc32，重启/续跑不变），不做任何相似度猜测。

    含 CJK 的句子只在 zh-* 音色里选（英文音色读中文 = 静音，这不是假设而是实测）；
    本机没有中文音色时直接报错，不交一轨不出声的「成品」。"""
    if _CJK_RE.search(text or ""):
        candidates = [item for item in pool if item[1].lower().startswith("zh")]
        if not candidates:
            raise DubbingEngineError(
                "台词含中文，但本机没有 zh-* 系统音色（可在 Windows 语音设置里添加，"
               "或改用 sovits/elevenlabs 通道）"
            )
    else:
        candidates = pool
    return candidates[zlib.crc32(str(speaker).encode("utf-8")) % len(candidates)][0]


def _synth_sapi(job: dict[str, Any]) -> None:
    cfg = get_config().dubbing
    pool = _sapi_voice_pool()
    if not pool:
        raise DubbingEngineError("sapi 通道不可用：没有可用的系统音色（检查 DUBBING_SAPI_VOICES）")
    segments = [s for s in (job.get("manifest") or {}).get("segments") or [] if not s.get("audio_ref")]
    total = int((job.get("manifest") or {}).get("line_count") or 0) or len(segments)
    done = total - len(segments)
    directory = _audio_dir(str(job["id"]))
    directory.mkdir(parents=True, exist_ok=True)
    script = directory / "_sapi_synth.ps1"
    script.write_text(_SAPI_PS, encoding="utf-8")
    for start in range(0, len(segments), _SAPI_CHUNK):
        chunk = segments[start : start + _SAPI_CHUNK]
        tasks = [
            {
                "text": str(seg.get("text") or ""),
                "voice": _sapi_pick_voice(pool, str(seg.get("speaker") or ""), str(seg.get("text") or "")),
                "out": str(directory / f"seg_{int(seg.get('index') or 0):04d}.wav"),
            }
            for seg in chunk
        ]
        manifest_path = directory / f"_sapi_manifest_{start}.json"
        manifest_path.write_text(json.dumps(tasks, ensure_ascii=False), encoding="utf-8-sig")
        timeout = max(120.0, cfg.timeout_ms / 1000.0) * len(chunk)
        try:
            out = subprocess.run(
                [
                    "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(script), "-ManifestPath", str(manifest_path),
                ],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise DubbingEngineError(
                f"sapi 批量合成超时（第 {chunk[0].get('index')}–{chunk[-1].get('index')} 句）"
            ) from error
        finally:
            manifest_path.unlink(missing_ok=True)
        if out.returncode != 0:
            raise DubbingEngineError(
                f"sapi 合成失败（第 {chunk[0].get('index')} 句起）："
                f"{(out.stderr or out.stdout or '')[:200].strip()}"
            )
        for seg, task in zip(chunk, tasks):
            produced = Path(task["out"])
            if not produced.is_file() or produced.stat().st_size < 200:
                raise DubbingEngineError(f"第 {seg.get('index')} 句（{seg.get('speaker')}）没有产出有效 wav")
            _attach_segment_file(job, seg, produced)
            # 只有文件头没有音频帧的「静音 wav」同样不算配音成功，宁失败不冒充
            if float(seg.get("duration_seconds") or 0.0) <= 0.0:
                raise DubbingEngineError(
                    f"第 {seg.get('index')} 句（{seg.get('speaker')}）合成结果是静音（没有音频帧）"
                )
        done += len(chunk)
        _mark_progress(job, done, total)


# ----------------------------- sovits ----------------------------- #


def _synth_sovits(job: dict[str, Any]) -> None:
    """自建 GPT-SoVITS api_v2 兼容服务：每句 POST ``/tts``（参考音频零样本克隆）。

    ``ref_audio_path`` 传本机绝对路径（服务与本项目同机/共享盘的典型部署）或
    公网地址原样透传；响应必须是 wav 字节，否则显式失败。"""
    cfg = get_config().dubbing
    base = cfg.sovits_base_url.rstrip("/")
    timeout = max(1.0, cfg.timeout_ms / 1000.0)
    manifest = job.get("manifest") or {}
    segments = manifest.get("segments") or []
    # 译制时文本语种按目标语言交给引擎（参考音频语种仍用 DUBBING_SOVITS_LANGUAGE）
    text_lang = str(manifest.get("target_lang") or "") if manifest.get("translated") else cfg.sovits_language
    text_lang = text_lang or cfg.sovits_language
    pending = [s for s in segments if not s.get("audio_ref")]
    done = len(segments) - len(pending)
    for seg in pending:
        ref = str(seg.get("voice_ref") or "")
        send_ref = ref
        if voice_source(ref) == "local":
            path, issue = resolve_voice_path(ref)
            if path is None:
                raise DubbingEngineError(f"第 {seg.get('index')} 句的参考音频无效：{issue}")
            send_ref = str(path)
        try:
            response = httpx.post(
                f"{base}/tts",
                json={
                    "text": str(seg.get("text") or ""),
                    "text_lang": text_lang,
                    "ref_audio_path": send_ref,
                    "prompt_text": "",
                    "prompt_lang": cfg.sovits_language,
                    "media_type": "wav",
                    "streaming_mode": False,
                },
                timeout=timeout,
            )
        except Exception as error:  # noqa: BLE001 - 网络异常点名到句，不蒙头重试
            raise DubbingEngineError(
                f"第 {seg.get('index')} 句（{seg.get('speaker')}）sovits 请求失败："
                f"{type(error).__name__}: {error}"
            ) from error
        if response.status_code >= 400:
            raise DubbingEngineError(
                f"第 {seg.get('index')} 句 sovits 合成失败：HTTP {response.status_code} "
                f"{(response.text or '')[:160]}"
            )
        blob = response.content or b""
        if len(blob) < 44:
            raise DubbingEngineError(f"第 {seg.get('index')} 句 sovits 返回不是有效 wav（{len(blob)} 字节）")
        _store_segment_blob(job, seg, blob, ".wav")
        done += 1
        _mark_progress(job, done, len(segments))


# --------------------------- elevenlabs --------------------------- #


def _elevenlabs_headers() -> dict[str, str]:
    cfg = get_config().dubbing
    headers = {"xi-api-key": cfg.elevenlabs_api_key}
    traceparent = tracer.current_traceparent()
    if traceparent:
        headers["traceparent"] = traceparent
    return headers


def _elevenlabs_ref_bytes(ref: str, seg_index: Any) -> bytes:
    """取参考音频字节：本地 assets/ 相对路径直读；公网地址下载后上传克隆。"""
    if voice_source(ref) == "remote":
        try:
            response = httpx.get(ref, timeout=60.0, follow_redirects=True)
        except Exception as error:  # noqa: BLE001
            raise DubbingEngineError(f"参考音频下载失败（{ref}）：{type(error).__name__}") from error
        if response.status_code >= 400 or not response.content:
            raise DubbingEngineError(f"参考音频下载失败（{ref}）：HTTP {response.status_code}")
        return bytes(response.content)
    path, issue = resolve_voice_path(ref)
    if path is None:
        raise DubbingEngineError(f"第 {seg_index} 句的参考音频无效：{issue}")
    blob = path.read_bytes()
    if not blob:
        raise DubbingEngineError(f"参考音频是空文件：{ref}")
    return blob


def _synth_elevenlabs(job: dict[str, Any]) -> None:
    """云端 Instant Voice Clone：说话人参考音频 → ``POST /voices/add`` 建音色（每作业
    每说话人一次，voice_id 持久化在作业里、续跑不重建）→ 逐句 ``POST
    /text-to-speech/{voice_id}`` 取回 mp3。克隆音色不自动删除，由用户在云端自管。"""
    cfg = get_config().dubbing
    base = cfg.elevenlabs_base_url.rstrip("/")
    timeout = max(60.0, cfg.timeout_ms / 1000.0)
    manifest = job.get("manifest") or {}
    voice_map = manifest.get("voices") or {}
    clones: dict[str, str] = dict(job.get("clone_voices") or {})
    for speaker in manifest.get("speakers") or []:
        if speaker in clones:
            continue
        ref = str(voice_map.get(speaker) or "")
        if not ref:
            raise DubbingEngineError(f"说话人「{speaker}」没有参考音频，无法克隆")
        blob = _elevenlabs_ref_bytes(ref, speaker)
        files = {"files": (f"{_safe_job_part(speaker)}{Path(urlparse(ref).path or 'ref.wav').suffix or '.wav'}", blob)}
        data = {"name": f"creatoragent-{job['id']}-{speaker}", "description": "全自动配音作业的克隆音色"}
        try:
            response = httpx.post(f"{base}/voices/add", headers=_elevenlabs_headers(), files=files, data=data, timeout=timeout)
        except Exception as error:  # noqa: BLE001
            raise DubbingEngineError(f"说话人「{speaker}」克隆请求失败：{type(error).__name__}") from error
        if response.status_code >= 400:
            raise DubbingEngineError(
                f"说话人「{speaker}」克隆失败：HTTP {response.status_code} {(response.text or '')[:160]}"
            )
        try:
            voice_id = str(response.json().get("voice_id") or "")
        except ValueError as error:
            raise DubbingEngineError(f"说话人「{speaker}」克隆响应不是 JSON") from error
        if not voice_id:
            raise DubbingEngineError(f"说话人「{speaker}」克隆响应里没有 voice_id")
        clones[speaker] = voice_id
        job["clone_voices"] = clones
        _job_backend().write_back([job])
    segments = manifest.get("segments") or []
    pending = [s for s in segments if not s.get("audio_ref")]
    done = len(segments) - len(pending)
    for seg in pending:
        speaker = str(seg.get("speaker") or "")
        voice_id = clones.get(speaker, "")
        if not voice_id:
            raise DubbingEngineError(f"说话人「{speaker}」没有可用的克隆音色（克隆步骤未完成）")
        try:
            response = httpx.post(
                f"{base}/text-to-speech/{voice_id}",
                params={"output_format": "mp3_44100_128"},
                headers={**_elevenlabs_headers(), "content-type": "application/json"},
                json={"text": str(seg.get("text") or ""), "model_id": cfg.elevenlabs_model_id},
                timeout=timeout,
            )
        except Exception as error:  # noqa: BLE001
            raise DubbingEngineError(
                f"第 {seg.get('index')} 句（{speaker}）合成请求失败：{type(error).__name__}"
            ) from error
        if response.status_code >= 400:
            raise DubbingEngineError(
                f"第 {seg.get('index')} 句合成失败：HTTP {response.status_code} {(response.text or '')[:160]}"
            )
        blob = response.content or b""
        if len(blob) < 128:
            raise DubbingEngineError(f"第 {seg.get('index')} 句 elevenlabs 返回不是有效音频（{len(blob)} 字节）")
        _store_segment_blob(job, seg, blob, ".mp3")
        done += 1
        _mark_progress(job, done, len(segments))


# --------------------------- worker 与全轨拼接 --------------------------- #


def _spawn_worker(job_id: str) -> bool:
    """为引擎作业启动后台 worker（同一作业只允许一个；daemon 线程不阻退出）。"""
    with _WORKERS_LOCK:
        if job_id in _WORKERS:
            return False
        _WORKERS.add(job_id)
    threading.Thread(target=_engine_worker, args=(job_id,), name=f"dubbing-{job_id}", daemon=True).start()
    return True


def _engine_worker(job_id: str) -> None:
    try:
        _run_engine_job(job_id)
    finally:
        with _WORKERS_LOCK:
            _WORKERS.discard(job_id)


def _run_engine_job(job_id: str) -> None:
    backend = _job_backend()
    job = next((row for row in backend.snapshot() if row.get("id") == job_id), None)
    if job is None or job.get("status") in ("done", "failed"):
        return
    provider = str(job.get("provider") or "")
    reason = _engine_preflight(job)
    if reason:
        _transition(job, "failed", reason)
        backend.write_back([job])
        return
    _transition(job, "synthesizing", f"{provider} 引擎逐句真合成开始")
    backend.write_back([job])
    try:
        if provider == "sapi":
            _synth_sapi(job)
        elif provider == "sovits":
            _synth_sovits(job)
        elif provider == "elevenlabs":
            _synth_elevenlabs(job)
        else:
            raise DubbingEngineError(f"未知引擎通道：{provider}")
        _finalize_engine(job)
        _transition(job, "done", f"{provider} 配音完成：真音频已逐句落盘，时间轴按音频实长重建")
        job["progress"] = 100
        job["finished_at"] = now_iso()
    except DubbingEngineError as error:
        _transition(job, "failed", str(error))
    except Exception as error:  # noqa: BLE001 - 引擎异常全部收敛成显式作业失败
        _transition(job, "failed", f"{provider} 合成失败：{type(error).__name__}: {error}")
    backend.write_back([job])


def _concat_full(job: dict[str, Any]) -> str:
    """全段都是同参数 wav 时用标准库拼整轨 ``full.wav``；否则返回 ''（汇总如实说明）。

    整轨按句序直拼（不加句间静音），克隆/系统音色的成品试听与交付以逐句 +
    时间轴 txt 为准；需要精排时间轴的用户拿逐句 wav 自行对齐。"""
    manifest = job.get("manifest") or {}
    directory = _audio_dir(str(job["id"]))
    paths: list[Path] = []
    for seg in manifest.get("segments") or []:
        ref = str(seg.get("audio_ref") or "")
        path = DATA_DIR / ref if ref else None
        if path is None or path.suffix.lower() != ".wav" or not path.is_file():
            return ""
        paths.append(path)
    if not paths:
        return ""
    readers: list[wave.Wave_read] = []
    out = directory / "full.wav"
    try:
        params = None
        for path in paths:
            handle = wave.open(str(path), "rb")
            readers.append(handle)
            # 只比格式参数（nframes 每句必然不同，不参与比较）：nchannels/sampwidth/
            # framerate/comptype/compname = getparams() 除第 3 个索引外的全部字段
            key = handle.getparams()._replace(nframes=0)
            if params is None:
                params = key
            elif key != params:
                return ""
        with wave.open(str(out), "wb") as sink:
            assert params is not None
            sink.setparams(params)
            for handle in readers:
                sink.writeframes(handle.readframes(handle.getnframes()))
    except (OSError, ValueError, wave.Error):
        return ""
    finally:
        for handle in readers:
            try:
                handle.close()
            except wave.Error:
                pass
    return f"dubbing/{_safe_job_part(str(job['id']))}/full.wav"


def _finalize_engine(job: dict[str, Any]) -> None:
    """合成完成：按音频实长重建时间轴 → 拼整轨 → 汇总 → 导出 txt。

    任何一段读不到实长就保留估算值并标 ``estimated=True``——宁标估算，
    不拿假数据冒充测量结果。"""
    manifest = job.get("manifest") or {}
    segments = manifest.get("segments") or []
    cursor = 0.0
    unmeasured = 0
    for seg in segments:
        ref = str(seg.get("audio_ref") or "")
        path = DATA_DIR / ref if ref else None
        duration, measured = _file_duration(path) if path and path.is_file() else (None, False)
        if duration is None:
            duration = float(seg.get("duration_seconds") or 0.8)
        elif not measured:
            unmeasured += 1
        seg["duration_seconds"] = round(duration, 2)
        seg["start_second"] = round(cursor, 2)
        seg["end_second"] = round(cursor + duration, 2)
        seg["estimated"] = not measured or duration is None
        cursor += duration
    manifest["duration_seconds"] = round(cursor, 2)
    summary = _build_summary(job, segments, simulated=False)
    if unmeasured:
        summary["issues"] = list(summary.get("issues") or []) + [
            f"{unmeasured} 段音频时长按码率粗估（非 wav 实测），时间轴可能有小偏差"
        ]
    if str(job.get("provider")) == "sapi":
        summary["issues"] = list(summary.get("issues") or []) + [
            "sapi 通道是 Windows 系统音色合成：未消费参考音频做克隆，说话人按确定性映射指向已安装音色；"
            "需要真克隆请改用 sovits / elevenlabs 通道"
        ]
    full = _concat_full(job)
    if full:
        summary["full_audio"] = full
        summary["full_audio_url"] = (
            f"/api/tasks/{job['task_id']}/dubbing/{job['id']}/audio?ref={quote(full)}"
        )
    else:
        summary["issues"] = list(summary.get("issues") or []) + [
            "未拼接整轨（存在非 wav 或采样参数不一致的段落），逐句音频仍可试听"
        ]
    job["summary"] = summary
    try:
        relative, _ = export_timeline(job)
        job["timeline_path"] = relative
    except (OSError, ValueError) as error:
        log.warn(f"配音时间轴汇总导出失败（{job.get('id')}）：{error}")


# ------------------------------------------------------------------ #
# 时间轴汇总                                                            #
# ------------------------------------------------------------------ #


def _build_summary(job: dict[str, Any], segments: list[dict[str, Any]], *, simulated: bool) -> dict[str, Any]:
    """把（可能已按真实时长重建的）段落收敛成稳定的汇总结构。"""
    manifest = job.get("manifest") or {}
    speaker_set = sorted({str(seg.get("speaker") or "") for seg in segments}) or list(manifest.get("speakers") or [])
    voice_map = dict(manifest.get("voices") or {})
    total = round(sum(float(seg.get("duration_seconds") or 0) for seg in segments), 2)
    issues = list(job.get("issues") or [])
    if simulated:
        issues = issues + ["sample 通道未调用配音网关，音频为 sample:// 模拟地址"]
    elif not segments:
        issues = issues + ["汇总中没有可展示的配音段落"]
    for name in manifest.get("missing_voices") or []:
        issues.append(f"说话人「{name}」未提供参考音频")
    return {
        "job_id": str(job.get("id") or ""),
        "task_id": str(job.get("task_id") or ""),
        "provider": str(job.get("provider") or ""),
        "simulated": simulated,
        "translated": bool(manifest.get("translated")),
        "source_lang": str(manifest.get("source_lang") or ""),
        "target_lang": str(manifest.get("target_lang") or ""),
        "translation_simulated": bool(manifest.get("translation_simulated")),
        "line_count": len(segments),
        "speakers": speaker_set,
        "voices": {name: voice_map.get(name, "") for name in speaker_set},
        "duration_seconds": total,
        "segments": segments,
        "warnings": list(manifest.get("warnings") or []),
        "issues": issues,
    }


def render_timeline_text(summary: dict[str, Any]) -> str:
    """把时间轴汇总渲染成可读的 txt（导出下载与自检口径一致）。"""
    title = "配音时间轴汇总"
    provider = str(summary.get("provider") or "")
    if summary.get("simulated"):
        nature = "模拟（sample:// 占位，非真实合成）"
    elif provider == "real":
        nature = "配音网关回传的真实音频"
    else:
        nature = f"{provider} 引擎真实合成的音频（已落盘，可试听）"
    estimated = sum(1 for seg in summary.get("segments") or [] if seg.get("estimated"))
    total_segments = int(summary.get("line_count") or 0)
    caliber = (
        f"时长口径：{total_segments - estimated} 段实测／{estimated} 段粗估"
        if total_segments
        else "时长口径：无段落"
    )
    lines = [
        title,
        "=" * len(title),
        f"作业：{summary.get('job_id')}｜通道：{summary.get('provider')}"
        f"｜台词 {summary.get('line_count')} 句｜说话人 {len(summary.get('speakers') or [])} 位"
        f"｜总时长 {summary.get('duration_seconds')}s",
        f"音频性质：{nature}",
        caliber,
    ]
    if summary.get("translated"):
        src = DUBBING_LANGS.get(str(summary.get("source_lang")), str(summary.get("source_lang") or "原文"))
        tgt = DUBBING_LANGS.get(str(summary.get("target_lang")), str(summary.get("target_lang") or ""))
        note = f"译制配音：{src} → {tgt}（逐句保留说话人与句序）"
        if summary.get("translation_simulated"):
            note += "；译文为离线/降级引擎产出的占位串，非真实翻译"
        lines.append(note)
    lines.append("")
    for seg in summary.get("segments") or []:
        voice = str(seg.get("voice_ref") or "（未提供参考音频）")
        audio = str(seg.get("audio_ref") or seg.get("audio_url") or "（无音频地址）")
        header = (
            f"[{seg.get('start_second')}s – {seg.get('end_second')}s]"
            f"（{seg.get('duration_seconds')}s）{seg.get('speaker')}"
        )
        lines.append(header)
        if seg.get("source_text"):
            src_tag = DUBBING_LANGS.get(str(summary.get("source_lang")), str(summary.get("source_lang") or "原文"))
            tgt_tag = DUBBING_LANGS.get(str(summary.get("target_lang")), str(summary.get("target_lang") or ""))
            lines.append(f"  原文（{src_tag}）：{seg.get('source_text')}")
            lines.append(f"  译配（{tgt_tag}）：{seg.get('text')}")
        else:
            lines.append(f"  源文本：{seg.get('text')}")
        lines.append(f"  参考音频：{voice}")
        lines.append(f"  配音：{audio}")
        if seg.get("issue"):
            lines.append(f"  标注：{seg['issue']}")
        lines.append("")
    if summary.get("full_audio"):
        lines.append(f"整轨音频：{summary['full_audio']}（按句序直拼，未加句间静音）")
        lines.append("")
    for warning in summary.get("warnings") or []:
        lines.append(f"⚠ {warning}")
    for issue in summary.get("issues") or []:
        lines.append(f"· {issue}")
    return "\n".join(lines) + "\n"


def export_timeline(job: dict[str, Any]) -> tuple[str, str]:
    """把汇总落盘到 ``EXPORTS_DIR``，返回 ``(相对路径, 正文)``（原子写，与交付导出同口径）。"""
    summary = job.get("summary") or {}
    if not summary.get("segments"):
        raise ValueError("该作业尚无时间轴汇总可导出")
    text = render_timeline_text(summary)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    name = f"dubbing-{job.get('id')}-timeline.txt"
    tmp = EXPORTS_DIR / (name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    Path(tmp).replace(EXPORTS_DIR / name)
    return f"exports/{name}", text


# ------------------------------------------------------------------ #
# 状态推进与公共 API                                                    #
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
    """状态变更：只有真正变化才落历史并发事件（避免重复刷屏）。"""
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
        message=f"自动配音：{note}",
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


def create_job(
    task: TaskRecord,
    *,
    script_text: str,
    voices: Any = None,
    provider: str = "",
    target_lang: str = "",
    source_lang: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    """为任务创建一个全自动配音作业（``POST /api/tasks/{id}/dubbing``）。

    ``script_text`` 为剧情文本、``voices`` 为说话人→参考音频映射（均由用户提供）；
    ``target_lang`` 非空且不等于源语言时先**逐句译制**再合成（复用 LLM_* 网关）。
    文本解析不出任何台词时抛 ``ValueError`` 由路由层转 409。引擎/网关通道任一前置
    （缺参考音频 / 缺 key 或端点 / 成本超预算 / 译制不齐）不满足即**显式失败，绝不假装配音**；
    引擎通道受理后由后台 worker 逐句真合成，调用方轮询 ``list_jobs`` 看进度。
    """
    cfg = get_config().dubbing
    requested = (provider or "").strip().lower()
    chosen, auto_note = resolve_provider(requested)
    voice_map = parse_voices(voices)
    moment = now or datetime.now(timezone.utc)

    # 句数闸门：台词过多直接拒绝（克隆按句计费，防止一次把整本剧本烧出去）
    max_lines = max(1, cfg.max_lines)
    nonempty = [line for line in (script_text or "").splitlines() if line.strip()]
    if len(nonempty) > max_lines:
        raise ValueError(
            f"剧情文本共 {len(nonempty)} 行，超过单作业上限 {max_lines} 行"
            f"（DUBBING_MAX_LINES 可调），请分集/分幕提交"
        )

    manifest = build_manifest(script_text, voices=voice_map, provider=chosen)
    if auto_note:
        manifest["warnings"] = list(manifest.get("warnings") or []) + [auto_note]

    # 跨语言译制：受理时就翻译（与 real 同步提交、引擎前置同口径），
    # 翻译不齐当场失败、绝不拿原文顶替；离线/降级则产出占位串并如实标注继续跑
    translation_error = ""
    wanted_target = (target_lang or "").strip().lower()
    if wanted_target:
        try:
            translate_manifest(manifest, target_lang=wanted_target, source_lang=source_lang)
        except DubbingTranslateError as error:
            translation_error = str(error)
        except Exception as error:  # noqa: BLE001 - 翻译异常收敛成显式失败，不抛 500
            translation_error = f"译制失败：{type(error).__name__}: {error}"

    estimated_cost = _estimate_cost(manifest, chosen)
    job_id = new_id("dub")
    job: dict[str, Any] = {
        "id": job_id,
        "task_id": task.id,
        "tenant": task.tenant,
        "provider": chosen,
        "requested_provider": requested or cfg.provider,
        "status": "queued",
        "progress": 0,
        "channel": task.brief.channel,
        "script_text": script_text.strip(),
        "voices": voice_map,
        "target_lang": manifest.get("target_lang") or "",
        "source_lang": manifest.get("source_lang") or "",
        "translated": bool(manifest.get("translated")),
        "translation_simulated": bool(manifest.get("translation_simulated")),
        "line_count": manifest.get("line_count"),
        "speakers": manifest.get("speakers"),
        "duration_seconds": manifest.get("duration_seconds"),
        "sample_seconds": _sample_seconds(manifest),
        "estimated_cost_usd": estimated_cost,
        "max_lines": max_lines,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "started_at": now_iso(),
        "finished_at": "",
        "summary": None,
        "timeline_path": "",
        "error": "",
        "attempts": 0,
        "remote_id": "",
        "submitted": False,
        "last_poll_at": "",
        "issues": [],
        "clone_voices": {},
        "manifest": manifest,
        "history": [{"ts": now_iso(), "from": "", "to": "queued", "note": "配音作业已受理"}],
    }

    # 跨语言译制不齐：受理即显式失败（译制是配音前置，不齐绝不能继续合成）
    if translation_error:
        _transition(job, "failed", translation_error)
        _job_backend().create(job)
        return dict(job)

    # 成本熔断：受理前按引擎计费口径估算，超预算直接 fail-loud，绝不提交、绝不假装
    budget = get_config().cost_budget_usd
    if estimated_cost > 0 and budget > 0 and estimated_cost >= budget:
        _transition(
            job,
            "failed",
            f"成本熔断：{chosen} 通道按台词量预估 ${estimated_cost:.3f}"
            f" 已达预算 ${budget:.2f}，未提交配音（可拆分文本或调高 COST_BUDGET_USD）",
        )
        _job_backend().create(job)
        return dict(job)

    # 引擎通道前置检查在受理时就做（缺 key/端点/参考音频当场点名，不等 worker 失败）
    reason = _engine_preflight(job)
    if reason:
        _transition(job, "failed", reason)
        _job_backend().create(job)
        return dict(job)

    _job_backend().create(job)

    with tracer.span_on_task(
        task.id,
        "dubbing.synthesize",
        kind="client",
        agent_id="A8",
        attributes={
            "dubbing.job_id": job_id,
            "dubbing.provider": chosen,
            "dubbing.lines": manifest.get("line_count") or 0,
            "dubbing.speakers": len(manifest.get("speakers") or []),
            "dubbing.estimated_cost_usd": estimated_cost,
        },
    ):
        event_bus.publish(
            task_id=task.id,
            type="log",
            message=(
                f"自动配音作业已创建（{chosen}，{manifest.get('line_count')} 句台词"
                f"／{len(manifest.get('speakers') or [])} 位说话人"
                + (
                    f"，译制 {manifest.get('source_lang')}→{manifest.get('target_lang')}"
                    + ("（占位）" if manifest.get("translation_simulated") else "")
                    if manifest.get("translated")
                    else ""
                )
                + "）"
            ),
            agent_id="A8",
            payload={"job_id": job_id, "estimated_cost_usd": estimated_cost},
        )
        if chosen == "real":
            job["attempts"] = int(job.get("attempts") or 0) + 1
            _advance_real(job, moment)
            _job_backend().write_back([job])
        elif chosen in ENGINE_PROVIDERS:
            # 真实引擎：后台 worker 逐句合成（同步创建请求不等几分钟合成）
            _spawn_worker(job_id)
            _transition(job, "synthesizing", f"{chosen} 引擎 worker 已启动，逐句合成中")
            _job_backend().write_back([job])
    log.info(f"自动配音作业已创建：{job_id}（task={task.id}, provider={chosen}, status={job['status']}）")
    return dict(job)


def _refresh(job: dict[str, Any], now: datetime) -> None:
    """惰性推进一个作业；纯推进不落盘。

    引擎通道（sapi/sovits/elevenlabs）由 worker 线程拥有状态：读取只负责
    **复活**没有活体 worker 的作业（进程重启/崩溃后续跑，已合成句不重复）；
    sample 按流逝时间，real 按远端状态。"""
    if job.get("status") in ("done", "failed"):
        return
    provider = str(job.get("provider") or "")
    if provider in ENGINE_PROVIDERS:
        _spawn_worker(str(job.get("id") or ""))
    elif provider == "real":
        with tracer.span_on_task(str(job.get("task_id")), "dubbing.poll", kind="client"):
            _advance_real(job, now)
    else:
        _advance_sample(job, now)


def list_jobs(
    task_id: str | None = None, *, tenant: str | None = None, now: datetime | None = None
) -> list[dict[str, Any]]:
    """列出配音作业（读取时惰性推进状态机），新→旧排序。

    sample/网关的推进逻辑在**持久化之外**执行；引擎通道作业在读取时复活无活体
    worker 的作业（断点续跑），网络慢不阻塞其他调用；仅状态有变化的作业才回写。
    完成时同步导出时间轴 txt（幂等）。
    """
    backend = _job_backend()
    moment = now or datetime.now(timezone.utc)
    snapshot = [dict(job) for job in backend.snapshot()]
    changed: list[dict[str, Any]] = []
    for job in snapshot:
        before = str(job.get("status"))
        _refresh(job, moment)
        if str(job.get("status")) == "done" and before != "done":
            try:
                relative, _ = export_timeline(job)
                job["timeline_path"] = relative
            except (OSError, ValueError) as error:
                log.warn(f"配音时间轴汇总导出失败（{job.get('id')}）：{error}")
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
    """读取单个配音作业（同样惰性推进）。"""
    return next((row for row in list_jobs(tenant=tenant) if row.get("id") == job_id), None)


def drop_task(task_id: str) -> int:
    """任务被删除时回收其配音作业与已落盘音频（与 tracer.drop 同一时机）。

    音频目录只认消毒后的作业 id 拼出的 ``data/dubbing/<job_id>/``，不放开任意删除。"""
    backend = _job_backend()
    doomed = [
        str(job.get("id") or "")
        for job in backend.snapshot()
        if str(job.get("task_id")) == task_id and job.get("id")
    ]
    removed = backend.drop_task(task_id)
    root = (DATA_DIR / "dubbing").resolve()
    for job_id in doomed:
        directory = _audio_dir(job_id)
        if directory.is_dir() and directory.resolve().parent == root:
            shutil.rmtree(directory, ignore_errors=True)
    return removed


__all__ = [
    "DEFAULT_NARRATOR",
    "ENGINE_PROVIDERS",
    "STATUSES",
    "STORE_FILE",
    "VOICE_EXTS",
    "build_manifest",
    "create_job",
    "drop_task",
    "export_timeline",
    "get_job",
    "list_jobs",
    "parse_script",
    "parse_voices",
    "render_timeline_text",
    "resolve_audio_path",
    "resolve_provider",
    "resolve_voice_path",
    "save_voice",
    "voice_source",
]
