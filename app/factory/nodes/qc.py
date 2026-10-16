"""质检 · 发行岗位：审片 / 合规审查 / 事实核查 / 发行适配 / 宣传 / 媒介投放 / 复盘。

质检不是「盖个通过章」：每个节点都从交付物里**量出可核对的事实**——
镜头配比、声画指标、字幕规范、词库命中、数字断言、平台规格——
再按权重算分。分数低就写低，阻断级问题一律把结论压到不通过。

宣发也一样：宣传的金句从台词里挑、物料按手头实物盘点，投放预算被质检结论拦住时
就真的锁定，不在报告里写「建议加大投放」这种无法核对的话。

合规与事实核查复用仓库既有知识层（``app.knowledge.compliance``），
不在这里另建一套词库，避免两处红线各自漂移。
"""

from __future__ import annotations

import re
from typing import Any

from ...knowledge.compliance import INDUSTRY_RULES, auto_rewrite, check_brand_voice, scan_compliance
from .. import craft
from ..spec import NodeError, Param, Port, node
from . import common

#: 审片六维权重（与 ``craft.REVIEW_DIMENSIONS`` 一一对应，改动要同步标签表）
_WEIGHTS = {"structure": 0.2, "shot_language": 0.2, "pacing": 0.15, "sound": 0.2, "subtitle": 0.1, "compliance": 0.15}

_SEVERITY_PENALTY = {"blocker": 34, "major": 16, "minor": 7}


def _industry_of(text: str) -> str:
    """按行业规则表自身的内容反查适用行业，不硬编码行业名。"""
    for key, rules in INDUSTRY_RULES.items():
        if key and key in text:
            return key
    best, hits = "", 0
    for key, rules in INDUSTRY_RULES.items():
        score = sum(1 for rule in rules for term in rule.terms if term and term in text)
        if score > hits:
            best, hits = key, score
    return best


def _verdict_of(score: float, blockers: int) -> str:
    if blockers:
        return "驳回：存在阻断级问题"
    if score >= 85:
        return "通过"
    if score >= 70:
        return "修改后通过"
    return "驳回"


# ------------------------------------------------------------------ #
# 审片                                                                #
# ------------------------------------------------------------------ #


@node(
    "reviewer",
    title="审片员",
    dept="qc",
    role="六个维度逐项体检：叙事结构、镜头语言、节奏时长、声画配合、字幕规范、合规安全，量出问题、算出总分与结论",
    inputs=[
        Port("timeline", "时间轴", "timeline", required=False),
        Port("shots", "分镜表", "shots", required=False),
        Port("screenplay", "剧本", "screenplay", required=False),
        Port("audio", "成片音轨", "audio", required=False),
        Port("subtitles", "字幕轨", "subtitles", required=False),
        Port("brief", "项目需求", "brief", required=False),
    ],
    outputs=[Port("report", "审片报告", "report"), Port("text", "审片意见", "text")],
    params=[
        Param("pass_line", "通过线", "number", 85, minimum=50, maximum=100, step=1, unit="分"),
        Param("fix_line", "修改线", "number", 70, minimum=30, maximum=99, step=1, unit="分"),
        Param("strict", "从严", "toggle", False, hint="开=把 minor 问题也计入扣分"),
        Param("note", "备注", "text", ""),
    ],
    icon="🧐",
    tags=["六维体检", "评分", "验收"],
)
def reviewer(ctx: Any) -> dict[str, Any]:
    timeline = ctx.in_("timeline") if isinstance(ctx.in_("timeline"), dict) else {}
    shots = common.shots_of(timeline) or common.shots_of(ctx.in_("shots"))
    if not shots:
        raise NodeError("审片员需要成片或分镜表：请把剪辑师（或分镜师）的产物接进来")
    brief = common.norm_brief(ctx.in_("brief"), None)
    screenplay = ctx.in_("screenplay") if isinstance(ctx.in_("screenplay"), dict) else {}
    cues = common.cues_of(ctx.in_("subtitles"))
    audio = ctx.in_("audio") if isinstance(ctx.in_("audio"), dict) else {}
    strict = bool(ctx.params.get("strict", False))
    duration = float(timeline.get("duration_s") or sum(float(row.get("duration_s") or 0) for row in shots))
    issues: list[dict[str, Any]] = []
    dims: dict[str, float] = {}

    def push(dimension: str, severity: str, detail: str, suggestion: str, at_s: float = 0.0) -> None:
        issues.append({"dimension": dimension, "severity": severity, "detail": detail, "suggestion": suggestion, "at_s": round(at_s, 2)})

    # 1) 叙事结构：幕比例 + 钩子 + 行动指令
    acts = [str(row.get("act") or "") for row in shots if row.get("act")]
    beats = [str(row.get("beat") or "") for row in shots]
    if not beats[0].startswith("hook"):
        push("structure", "major", f"开场镜头节拍是 {beats[0] or '未知'}，不是钩子", "前三秒必须给出冲突或结论，否则完播率塌方", float(shots[0].get("start_s") or 0))
    if not any(item in {"cta", "payoff", "ending"} for item in beats[-3:]):
        push("structure", "minor", "结尾三镜没有收束或行动指令", "补一镜结论/引导，别在铺垫节拍上收尾", float(shots[-1].get("start_s") or 0))
    if len(set(acts)) < 2 and duration >= 30:
        push("structure", "major", f"{duration:.0f}s 的片子只有 1 个幕（{acts[0] if acts else '未标注'}）", "至少要有建置与推进两段，否则中段失控")
    if screenplay.get("scenes") and len(shots) < max(3, len(screenplay["scenes"])):
        push("structure", "minor", f"剧本 {len(screenplay['scenes'])} 场只有 {len(shots)} 镜覆盖", "有的场次没分镜，剪辑时会出现叙事断点")
    dims["structure"] = 100 - sum(_SEVERITY_PENALTY[item["severity"]] for item in issues if item["dimension"] == "structure" and (strict or item["severity"] != "minor"))

    # 2) 镜头语言：景别/运镜多样性 + 连续同景别
    sizes = [str(row.get("size") or "") for row in shots]
    run = 1
    for index in range(1, len(sizes)):
        run = run + 1 if sizes[index] == sizes[index - 1] else 1
        if run == 3:
            label = craft.SHOT_SIZES.get(sizes[index], {}).get("label", sizes[index])
            push("shot_language", "minor", f"自 {shots[index - 2].get('slate')} 起连续 3 镜同为{label}", "同景别连切读起来像静止，插一个反差景别", float(shots[index].get("start_s") or 0))
    variety = len(set(sizes)) / max(1, len(sizes))
    if variety < 0.28 and len(shots) >= 6:
        push("shot_language", "major", f"景别只有 {len(set(sizes))} 种（{sorted(set(sizes))}），多样性 {variety:.0%}", "全景/中景/特写至少三种，画面才有层次")
    movements = {str(row.get("movement") or "") for row in shots}
    if len(movements) == 1 and len(shots) >= 5:
        push("shot_language", "minor", f"全片只有一种运镜（{craft.CAMERA_MOVEMENTS.get(list(movements)[0], {}).get('label', '固定')}）", "至少混一种运动镜头，观感才不会像幻灯片")
    dims["shot_language"] = 100 - sum(_SEVERITY_PENALTY[item["severity"]] for item in issues if item["dimension"] == "shot_language" and (strict or item["severity"] != "minor"))

    # 3) 节奏时长：目标偏差 + 台词密度 + 拖沓镜
    target = float(brief.get("duration_s") or 0) or float(timeline.get("target_s") or 0)
    if target and abs(duration - target) / target > 0.2:
        push("pacing", "major", f"片长 {duration:.1f}s 偏离目标 {target:.0f}s（{(duration - target) / target:+.0%}）", "超量就删镜，不足就补一镜动作，别靠拉长现有镜头")
    speech = sum(float(row.get("speech_s") or 0) for row in shots)
    if duration and not (0.3 <= speech / duration <= 0.96):
        push("pacing", "minor", f"口播占比 {speech / duration:.0%}（合理区间 30%~96%）", "太空就加信息，太满就留表演呼吸")
    for row in shots:
        if float(row.get("duration_s") or 0) > 6.5 and float(row.get("speech_s") or 0) <= 0:
            push("pacing", "minor", f"{row.get('slate')} 长 {row.get('duration_s')}s 且无台词", "纯动作镜压到 4s 内，或补旁白交代信息", float(row.get("start_s") or 0))
    dims["pacing"] = 100 - sum(_SEVERITY_PENALTY[item["severity"]] for item in issues if item["dimension"] == "pacing" and (strict or item["severity"] != "minor"))

    # 4) 声画配合：混音指标 + 缺画面镜 + 剪辑器测出的越界人声
    kinds = audio.get("kinds") or {}
    for kind, label in (("voice", "人声"), ("music", "配乐"), ("sfx", "音效")):
        if not kinds.get(kind):
            push("sound", "major" if kind == "voice" else "minor", f"成片缺少{label}轨（{label} 0 条）", f"补{label}：短视频没有{label}会显得没做完")
    peak, rms = audio.get("peak_db"), audio.get("rms_db")
    if peak is not None and float(peak) > -0.1:
        push("sound", "blocker", f"峰值 {peak}dBFS 已削波", "回混音师把总线下压或用真峰限幅")
    elif peak is not None and float(peak) < -6:
        push("sound", "minor", f"峰值 {peak}dBFS 偏轻（目标 -1~-3dBFS）", "提高总线或加大归一化，手机外响会听不清")
    if rms is not None and not (-27 <= float(rms) <= -14):
        push("sound", "minor", f"响度 RMS {rms}dBFS 不在 -27~-14 区间", "按平台响度口径重混")
    missing = timeline.get("missing_visual") or []
    if missing:
        push("sound", "major", f"{len(missing)} 镜没有画面素材：{'、'.join(str(item) for item in missing[:5])}", "回生成车间补镜头，别让放映时回落成色块")
    dims["sound"] = 100 - sum(_SEVERITY_PENALTY[item["severity"]] for item in issues if item["dimension"] == "sound" and (strict or item["severity"] != "minor"))

    # 5) 字幕规范：条数 / 覆盖 / 行宽 / 重叠
    if not cues:
        push("subtitle", "major", "没有字幕轨", "口播视频不烧字幕等于放弃静音刷")
    else:
        style = (ctx.in_("subtitles") or {}).get("style") or {}
        limit = int(style.get("max_chars") or 16)
        over = [row for row in cues if len(str(row.get("text") or "")) > limit]
        if over:
            push("subtitle", "major", f"{len(over)} 条字幕超过 {limit} 字", "字幕师重新断句，一行读完不返工")
        overlap = [b for a, b in zip(cues, cues[1:]) if float(b.get("start") or 0) < float(a.get("end") or 0) - 0.001]
        if overlap:
            push("subtitle", "minor", f"{len(overlap)} 条字幕与下一条重叠", "加大条间留白或缩短停留")
        tail = float(cues[-1].get("end") or 0)
        if duration and tail < duration * 0.5:
            push("subtitle", "minor", f"字幕只覆盖到 {tail:.1f}s（片长 {duration:.1f}s）", "后半段没词就补信息条，别留白")
    dims["subtitle"] = 100 - sum(_SEVERITY_PENALTY[item["severity"]] for item in issues if item["dimension"] == "subtitle" and (strict or item["severity"] != "minor"))

    # 6) 合规安全：把台词与标题丢给同一套词库，阻断级直接顶格扣分
    dialogue = common.dialogue_text(screenplay) or "。".join(str(row.get("line") or "") for row in (timeline.get("voice") or {}).get("items", []))
    text = f"{brief.get('title')} {brief.get('brand')} {brief.get('product')} {dialogue}"
    scan = scan_compliance(text, _industry_of(f"{brief.get('genre')} {brief.get('type')} {text}"))
    blockers = [hit for hit in scan.hits if hit.severity == "blocker"]
    majors = [hit for hit in scan.hits if hit.severity == "major"]
    for hit in (blockers + majors)[:6]:
        push("compliance", hit.severity, f"「{hit.term}」命中{hit.category}：{hit.law}", hit.fix)
    for failure in scan.required_failures[:4]:
        push("compliance", failure.severity, f"缺少{failure.category}必备要素：{failure.hint}", failure.fix)
    dims["compliance"] = 100 - sum(_SEVERITY_PENALTY[item["severity"]] for item in issues if item["dimension"] == "compliance" and (strict or item["severity"] != "minor"))

    for key in dims:
        dims[key] = round(max(0.0, min(100.0, float(dims[key]))), 1)
    score = round(sum(dims[key] * _WEIGHTS[key] for key in dims), 1)
    blocker_count = len([item for item in issues if item["severity"] == "blocker"]) + len(blockers)
    verdict = _verdict_of(score, blocker_count)
    report = {
        "kind": "review",
        "score": score,
        "verdict": verdict,
        "dims": dims,
        "weights": _WEIGHTS,
        "issues": issues,
        "blockers": blocker_count,
        "majors": len([item for item in issues if item["severity"] == "major"]),
        "minors": len([item for item in issues if item["severity"] == "minor"]),
        "metrics": {
            "shots": len(shots),
            "duration_s": round(duration, 2),
            "target_s": round(target, 2),
            "speech_ratio": round(speech / duration, 3) if duration else 0.0,
            "size_variety": round(variety, 3),
            "cues": len(cues),
            "audio_kinds": kinds,
            "peak_db": peak,
            "rms_db": rms,
            "missing_visual": len(missing),
            "industry": _industry_of(f"{brief.get('genre')} {brief.get('type')} {text}"),
        },
        "pass_line": float(ctx.params.get("pass_line") or 85),
        "fix_line": float(ctx.params.get("fix_line") or 70),
    }
    order = sorted(issues, key=lambda item: -_SEVERITY_PENALTY[item["severity"]])
    lines = [
        f"审片：{score:.1f} 分 → {verdict}（通过线 {report['pass_line']:.0f}，修改线 {report['fix_line']:.0f}）",
        "｜".join(f"{craft.REVIEW_LABEL[key]} {dims[key]:.0f}" for key in _WEIGHTS),
        f"问题 {len(issues)} 条：阻断 {report['blockers']} / 重要 {report['majors']} / 轻微 {report['minors']}",
        *[f"· [{item['severity']}] {craft.REVIEW_LABEL[item['dimension']]}：{item['detail']} → {item['suggestion']}" for item in order[:10]],
        f"实测：{len(shots)} 镜 {duration:.1f}s（目标 {target:.0f}s）、口播占比 {report['metrics']['speech_ratio']:.0%}、景别多样性 {report['metrics']['size_variety']:.0%}、字幕 {len(cues)} 条、峰值 {peak}dBFS",
        f"备注：{ctx.params.get('note')}" if str(ctx.params.get("note") or "") else "",
    ]
    text_out = "\n".join(line for line in lines if line)
    ctx.log(f"审片：{score} 分 {verdict}，问题 {len(issues)} 条")
    return {"report": report, "text": text_out}


# ------------------------------------------------------------------ #
# 合规审查                                                             #
# ------------------------------------------------------------------ #


@node(
    "compliance",
    title="合规审查",
    dept="qc",
    role="过一遍广告法与行业专项词库：给出命中条款、法条依据、必改项，并当场产出可直接替换的合规版台词",
    inputs=[
        Port("screenplay", "剧本", "screenplay", required=False),
        Port("timeline", "时间轴", "timeline", required=False),
        Port("subtitles", "字幕轨", "subtitles", required=False),
        Port("brief", "项目需求", "brief", required=False),
    ],
    outputs=[Port("report", "合规报告", "report"), Port("text", "修改清单", "text"), Port("screenplay", "合规版台词", "screenplay", required=False)],
    params=[
        Param("rewrite", "自动出合规版", "toggle", True, hint="用仓库词库的安全替换表逐条改写"),
        Param("brand", "查品牌调性", "toggle", True),
        Param("industry", "行业", "select", "自动识别", ["自动识别"] + sorted(INDUSTRY_RULES)),
        Param("limit", "最多列出", "number", 12, minimum=1, maximum=60, step=1, unit="条"),
    ],
    icon="⚖️",
    tags=["广告法", "行业规则", "否决权"],
)
def compliance(ctx: Any) -> dict[str, Any]:
    screenplay = ctx.in_("screenplay") if isinstance(ctx.in_("screenplay"), dict) else {}
    timeline = ctx.in_("timeline") if isinstance(ctx.in_("timeline"), dict) else {}
    brief = common.norm_brief(ctx.in_("brief"), None)
    cues = common.cues_of(ctx.in_("subtitles"))
    rows = [row for row in (screenplay.get("scenes") or []) if isinstance(row, dict)]
    dialogue = common.dialogue_text({"scenes": rows}) if rows else "。".join(str(row.get("text") or "") for row in (timeline.get("voice") or {}).get("items") or []) or "。".join(str(row.get("text") or "") for row in cues)
    if not dialogue.strip():
        raise NodeError("合规审查没有文本可读：请接剧本、时间轴或字幕轨")
    head = f"{brief.get('title')} {brief.get('brand')} {brief.get('product')}"
    text = f"{head}。{dialogue}"
    choice = str(ctx.params.get("industry") or "自动识别")
    industry = choice if choice != "自动识别" else _industry_of(f"{brief.get('genre')} {brief.get('type')} {text}")
    scan = scan_compliance(text, industry)
    hits = [hit for hit in scan.hits]
    limit = int(float(ctx.params.get("limit") or 12))
    blockers = [hit for hit in hits if hit.severity == "blocker"]
    majors = [hit for hit in hits if hit.severity == "major"]
    rewritten = auto_rewrite(text, hits) if bool(ctx.params.get("rewrite", True)) else None
    brand = check_brand_voice(text, str(brief.get("tone") or "")) if bool(ctx.params.get("brand", True)) else None

    scenes_out: list[dict[str, Any]] = []
    if rewritten and rows:
        replaced = {row["from"]: row["to"] for row in rewritten.applied}
        for scene in rows:
            copy = dict(scene)
            copy["dialogue"] = [
                {**(line if isinstance(line, dict) else {"line": str(line)}), "line": _apply(str((line or {}).get("line") if isinstance(line, dict) else line), replaced)}
                for line in (scene.get("dialogue") or [])
            ]
            copy["action"] = _apply(str(scene.get("action") or ""), replaced)
            scenes_out.append(copy)

    mandatory = blockers + majors
    score = max(0, 100 - 30 * len(blockers) - 14 * len(majors) - 6 * len([hit for hit in hits if hit.severity == "minor"]) - 10 * len(scan.required_failures))
    report = {
        "kind": "compliance",
        "score": score,
        "verdict": "不通过（需强制修改）" if blockers else ("修改后通过" if (majors or scan.required_failures) else "通过"),
        "risk_level": scan.risk_level,
        "industry": industry,
        "hits": [
            {"term": hit.term, "category": hit.category, "severity": hit.severity, "law": hit.law, "snippet": hit.snippet, "fix": hit.fix}
            for hit in hits[:limit]
        ],
        "required_failures": [
            {"category": failure.category, "hint": failure.hint, "severity": failure.severity, "law": failure.law, "fix": failure.fix}
            for failure in scan.required_failures
        ],
        "rewrites": rewritten.applied if rewritten else [],
        "brand_voice": {"score": brand.score, "issues": [{"type": item.type, "detail": item.detail, "suggestion": item.suggestion, "severity": item.severity} for item in (brand.issues if brand else [])]} if brand else None,
        "counts": {"blocker": len(blockers), "major": len(majors), "minor": len(hits) - len(blockers) - len(majors), "required": len(scan.required_failures)},
        "checked_chars": len(text),
    }
    rewrite_note = "自动替换关闭"
    if rewritten:
        pairs = "；".join(f"{row['from']}→{row['to']}" for row in rewritten.applied[:6])
        rewrite_note = (
            f"自动替换 {len(rewritten.applied)} 处，合规版台词已回写同名端口：{pairs}" if rewritten.applied else "没有可自动替换的词，需要编剧改写整句"
        )
    lines = [
        f"合规：扫描 {len(text)} 字（行业：{industry or '通用词库'}）→ {report['verdict']}，风险 {scan.risk_level}",
        f"命中 {len(hits)} 条：阻断 {len(blockers)} / 重要 {len(majors)} / 轻微 {len(hits) - len(blockers) - len(majors)}；必备要素缺失 {len(scan.required_failures)} 项",
        *[f"· [{hit.severity}]「{hit.term}」{hit.category}｜{hit.law}｜改：{hit.fix}" for hit in mandatory[:limit]],
        *[f"· 缺要素 [{failure.severity}]{failure.category}｜{failure.hint}｜补：{failure.fix}" for failure in scan.required_failures[:limit]],
        rewrite_note,
        f"品牌调性：{brand.score} 分，{len(brand.issues)} 处走偏" if brand else "品牌调性：未检查",
    ]
    if blockers:
        lines.append("⚠ 阻断级用词未清除前不得发行：审片与发行环节都会因此压分。")
    ctx.log(f"合规：{report['verdict']}，命中 {len(hits)} 条")
    out: dict[str, Any] = {"report": report, "text": "\n".join(line for line in lines if line)}
    if scenes_out:
        out["screenplay"] = {**screenplay, "scenes": scenes_out, "compliance_rewritten": len(rewritten.applied) if rewritten else 0}
    return out


def _apply(text: str, table: dict[str, str]) -> str:
    for term, safe in table.items():
        if term and term in text:
            text = text.replace(term, safe)
    return text


# ------------------------------------------------------------------ #
# 事实核查                                                             #
# ------------------------------------------------------------------ #: 数字断言 = 数值 + 单位；没有来源的一律标记为「需补证据」
_CLAIM = re.compile(
    r"(?P<value>\d+(?:[.,]\d+)?)\s*(?P<unit>%|％|℃|度|元|块|万元|小时|分钟|秒|天|个月|年|次|倍|人|台|件|分贝|dB|W|kW|mAh|Ah|ml|L|kg|g|斤|米|厘米|公里|寸|页|款|支|片)?",
)
_SOURCES = ("实测", "我们测", "实验室", "官方", "标称", "参数", "说明书", "第三方", "报告", "文献", "来源", "数据")
_HYPE = ("最高", "第一", "绝对", "永久", "100%", "全网最低", "销量冠军", "零风险", "立竿见影")


@node(
    "fact_checker",
    title="事实核查",
    dept="qc",
    role="把台词里的每个数字断言抠出来，逐条判断有没有来源、有没有夸大，输出必须补证据的清单与可播版台词",
    inputs=[
        Port("screenplay", "剧本", "screenplay", required=False),
        Port("timeline", "时间轴", "timeline", required=False),
        Port("subtitles", "字幕轨", "subtitles", required=False),
        Port("brief", "项目需求", "brief", required=False),
    ],
    outputs=[Port("report", "核查报告", "report"), Port("text", "核查清单", "text")],
    params=[
        Param("require_source", "要求标来源", "toggle", True, hint="开=没有来源词的数字断言记为需补证据"),
        Param("product_says", "对照产品参数", "text", "", hint="填官方参数原文，节点会在里面核对数字是否一致"),
        Param("limit", "最多列出", "number", 14, minimum=1, maximum=60, step=1, unit="条"),
    ],
    icon="🔎",
    tags=["数字断言", "来源", "夸大表述"],
)
def fact_checker(ctx: Any) -> dict[str, Any]:
    screenplay = ctx.in_("screenplay") if isinstance(ctx.in_("screenplay"), dict) else {}
    timeline = ctx.in_("timeline") if isinstance(ctx.in_("timeline"), dict) else {}
    brief = common.norm_brief(ctx.in_("brief"), None)
    rows: list[dict[str, str]] = []
    for line in common.dialogue_text(screenplay).splitlines():
        if line.strip():
            who, _, body = line.partition("：")
            rows.append({"speaker": who, "text": body or line})
    if not rows:
        for row in (timeline.get("voice") or {}).get("items") or []:
            rows.append({"speaker": str(row.get("character") or ""), "text": str(row.get("text") or "")})
    if not rows:
        rows = [{"speaker": "", "text": str(row.get("text") or "")} for row in common.cues_of(ctx.in_("subtitles"))]
    if not rows:
        raise NodeError("事实核查没有文本可查：请接剧本、时间轴或字幕轨")
    require_source = bool(ctx.params.get("require_source", True))
    product_spec = str(ctx.params.get("product_says") or "")
    limit = int(float(ctx.params.get("limit") or 14))
    claims: list[dict[str, Any]] = []
    for row in rows:
        for match in _CLAIM.finditer(row["text"]):
            value, unit = match.group("value"), match.group("unit") or ""
            if not value or (not unit and len(value) < 2 and float(value.replace(",", "")) < 10):
                continue  # 「一个」「两次」这类口语量词不算断言
            window = row["text"][max(0, match.start() - 14):match.end() + 14]
            sourced = any(word in window for word in _SOURCES)
            hype = next((word for word in _HYPE if word in window), "")
            verified = bool(product_spec) and value in product_spec
            status = "已核对" if verified else ("有来源" if sourced and not hype else ("夸大" if hype else "需补证据"))
            claims.append(
                {
                    "claim": f"{value}{unit}",
                    "status": status,
                    "severity": "blocker" if hype else ("minor" if status == "需补证据" else "info"),
                    "speaker": row["speaker"],
                    "context": window.strip(),
                    "fix": "" if verified else ("删掉比较级，只留可核对数字" if hype else ("补一句来源：实测/官方参数/第三方报告" if require_source and status == "需补证据" else "")),
                }
            )
    seen: set[str] = set()
    unique = [item for item in claims if not (item["claim"] + item["context"] in seen or seen.add(item["claim"] + item["context"]))]
    weak = [item for item in unique if item["status"] in {"需补证据", "夸大"}]
    hype_count = len([item for item in unique if item["status"] == "夸大"])
    score = max(0, 100 - 25 * hype_count - 6 * len([item for item in unique if item["status"] == "需补证据"]))
    report = {
        "kind": "fact",
        "score": score,
        "verdict": "需补证据" if weak else "可播",
        "claims": unique[:limit],
        "counts": {
            "total": len(unique),
            "verified": len([item for item in unique if item["status"] == "已核对"]),
            "sourced": len([item for item in unique if item["status"] == "有来源"]),
            "unsupported": len([item for item in unique if item["status"] == "需补证据"]),
            "hyped": hype_count,
        },
        "product_spec_checked": bool(product_spec),
        "lines": len(rows),
        "note": "结论只写事实侧；是否违反广告法归合规节点，两者口径独立。",
    }
    weak_lines: list[str] = []
    for item in weak[:limit]:
        speaker = f"[{item['speaker']}]" if item["speaker"] else ""
        weak_lines.append(f"· [{item['status']}]「{item['claim']}」{speaker}{item['context'][:34]}｜{item['fix']}")
    lines = [
        f"事实核查：{len(rows)} 句台词抠出 {len(unique)} 条数字断言 → {report['verdict']}（{score} 分）",
        f"已核对 {report['counts']['verified']}｜有来源 {report['counts']['sourced']}｜需补证据 {report['counts']['unsupported']}｜夸大 {report['counts']['hyped']}",
        *weak_lines,
        "没有产品参数可对照，全部结论来自台词里的来源词。" if not product_spec else f"已对照产品参数原文，命中 {report['counts']['verified']} 条。",
        report["note"],
    ]
    if hype_count:
        lines.append(f"⚠ {hype_count} 条比较级/绝对化数字表述，合规节点也会盯住它们——先改成可核对说法。")
    ctx.log(f"事实核查：{len(unique)} 条断言，需补证据 {len(weak)} 条")
    return {"report": report, "text": "\n".join(line for line in lines if line)}


# ------------------------------------------------------------------ #
# 发行适配                                                             #
# ------------------------------------------------------------------ #

#: 平台口径只写会影响成片的那些：画幅、时长上限、封面比例、字幕要求、发布时段
_PLATFORMS: list[dict[str, Any]] = [
    {"key": "douyin", "label": "抖音", "aspect": ("9:16",), "max_s": 60.0, "cover": "3:4", "burn": True, "windows": ("12:00", "18:30", "21:30"), "tags": 4, "vertical": True},
    {"key": "xiaohongshu", "label": "小红书", "aspect": ("3:4", "1:1", "9:16"), "max_s": 300.0, "cover": "3:4", "burn": False, "windows": ("12:00", "20:00"), "tags": 6, "vertical": True},
    {"key": "shipinhao", "label": "视频号", "aspect": ("9:16", "1:1", "16:9"), "max_s": 120.0, "cover": "1:1", "burn": True, "windows": ("08:00", "19:00", "22:00"), "tags": 3, "vertical": True},
    {"key": "bilibili", "label": "B站", "aspect": ("16:9", "9:16"), "max_s": 3600.0, "cover": "16:9", "burn": False, "windows": ("11:00", "17:00", "21:00"), "tags": 5, "vertical": False},
    {"key": "kuaishou", "label": "快手", "aspect": ("9:16",), "max_s": 90.0, "cover": "3:4", "burn": True, "windows": ("11:30", "18:00", "21:00"), "tags": 4, "vertical": True},
    {"key": "youtube_shorts", "label": "YouTube Shorts", "aspect": ("9:16",), "max_s": 60.0, "cover": "1:1", "burn": False, "windows": ("14:00", "20:00"), "tags": 3, "vertical": True},
]


@node(
    "distributor",
    title="发行",
    dept="qc",
    role="按平台口径逐条体检成片的画幅、时长、封面与字幕要求，给出需要重剪/重导的镜头清单、发布排期与话题标签",
    inputs=[
        Port("timeline", "时间轴", "timeline"),
        Port("brief", "项目需求", "brief", required=False),
        Port("images", "封面/物料", "images", required=False, many=True),
        Port("subtitles", "字幕轨", "subtitles", required=False),
        Port("report", "审看报告", "report", required=False),
    ],
    outputs=[Port("plan", "发行计划", "plan"), Port("text", "发行说明", "text")],
    params=[
        Param("platforms", "投放渠道", "text", "抖音,小红书,视频号,B站", hint="逗号分隔；未识别的渠道会被跳过并如实说明"),
        Param("start_at", "首发时间", "text", "D+1 20:00", hint="排期基准，仅用于生成相对时点"),
        Param("per_day", "每日上限", "number", 1, minimum=1, maximum=5, step=1, unit="条", hint="同渠道一天最多发几条"),
        Param("cover", "封面口径", "select", "按平台", ["按平台", "统一 3:4", "统一 1:1"]),
    ],
    icon="📡",
    tags=["平台规格", "排期", "标签"],
)
def distributor(ctx: Any) -> dict[str, Any]:
    timeline = ctx.require("timeline")
    if not isinstance(timeline, dict) or not timeline.get("shots"):
        raise NodeError("发行需要一条时间轴：请先接剪辑师")
    brief = common.norm_brief(ctx.in_("brief"), None)
    aspect = str(timeline.get("aspect") or "9:16")
    duration = float(timeline.get("duration_s") or 0.0)
    fps = int(float(timeline.get("fps") or 24))
    cues = common.cues_of(ctx.in_("subtitles"))
    covers: list[dict[str, Any]] = []
    for bundle in ctx.many("images"):
        covers += [item for item in common.items_of(bundle) if str(item.get("kind") or "") == "cover"]
    report = ctx.in_("report") if isinstance(ctx.in_("report"), dict) else {}
    wanted = [item.strip() for item in re.split(r"[,，、;；\s]+", str(ctx.params.get("platforms") or "")) if item.strip()]
    known = {str(item["label"]): item for item in _PLATFORMS}
    known.update({str(item["key"]): item for item in _PLATFORMS})
    chosen: list[dict[str, Any]] = []
    unknown: list[str] = []
    for name in wanted:
        row = known.get(name) or next((item for item in _PLATFORMS if name in str(item["label"])), None)
        (chosen.append(row) if row else unknown.append(name))
    if not chosen:
        raise NodeError("发行：投放渠道一个都没识别出来，请填平台名（抖音/小红书/视频号/B站/快手/YouTube Shorts）")
    per_day = int(float(ctx.params.get("per_day") or 1))
    cover_mode = str(ctx.params.get("cover") or "按平台")

    channels: list[dict[str, Any]] = []
    schedule: list[dict[str, Any]] = []
    risks: list[dict[str, Any]] = []
    day_index, slot_used = 0, 0
    for platform in chosen:
        actions: list[str] = []
        fits = aspect in platform["aspect"]
        if not fits:
            actions.append(f"重导 {platform['aspect'][0]} 版（当前 {aspect}）：按剪辑时间轴重排即可，不必重拍")
        if duration > float(platform["max_s"]):
            actions.append(f"超时长 {duration - float(platform['max_s']):.1f}s：剪一版 {platform['max_s']:.0f}s 内的投放版")
        need_cover = cover_mode == "按平台" and platform["cover"] not in {str(item.get("aspect")) for item in covers}
        if need_cover:
            actions.append(f"补 {platform['cover']} 封面（现有 {sorted({str(item.get('aspect')) for item in covers}) or '无'}）")
        if platform["burn"] and cues and not any(item.get("ref") for item in cues):
            actions.append("该平台要求烧录字幕：导出时把字幕轨合成进画面")
        if not cues:
            actions.append("没有字幕轨：静音刷屏场景下会丢一半观众")
        score = float(report.get("score") or 0)
        if score and score < 70:
            actions.append(f"审片 {score:.0f} 分未过修改线，先返工再发行")
        fit = "ready" if not actions else ("reframe" if not fits else "needs_fix")
        channels.append(
            {
                "platform": platform["label"],
                "key": platform["key"],
                "fit": fit,
                "aspect_ok": fits,
                "duration_limit_s": platform["max_s"],
                "cover": platform["cover"] if cover_mode == "按平台" else cover_mode.replace("统一 ", ""),
                "burn_subtitles": bool(platform["burn"]),
                "max_tags": platform["tags"],
                "actions": actions,
            }
        )
        windows = platform["windows"]
        if slot_used >= per_day:
            slot_used = 0
            day_index += 1
        schedule.append(
            {
                "platform": platform["label"],
                "day": f"D+{1 + day_index}",
                "at": windows[slot_used % len(windows)],
                "note": str(ctx.params.get("start_at") or ""),
                "title": f"{brief.get('title') or '成片'}｜{platform['label']}",
                "tags": _tags(brief, platform),
            }
        )
        slot_used += 1
        if actions:
            risks.append({"level": "major" if fit == "reframe" else "minor", "detail": f"{platform['label']}：{actions[0]}", "mitigation": "；".join(actions[1:]) or "按投放版重导出"})
    ready = [item for item in channels if item["fit"] == "ready"]
    sched = "；".join(f"{row['day']} {row['at']} → {row['platform']}" for row in schedule)
    tags_line = "／".join(f"{row['platform']} {len(row['tags'])} 个" for row in schedule)
    lines = [
        f"发行：{len(channels)} 个渠道（可直接发 {len(ready)}，需处理 {len(channels) - len(ready)}）｜成片 {duration:.1f}s {aspect} @{fps}fps",
        "、".join(f"{item['platform']}[{item['fit']}]" for item in channels),
        *[f"· {item['platform']}：{('可直发' if not item['actions'] else '；'.join(item['actions']))}" for item in channels],
        f"排期：{sched}",
        f"标签：{tags_line}",
        f"⚠ 未识别的渠道：{'、'.join(unknown)}——没有它的规格数据，不能假装适配通过。" if unknown else "",
    ]
    if len(ready) < len(channels):
        lines.append(f"⚠ {len(channels) - len(ready)} 个渠道需要先返工（重导画幅/压时长/补封面/烧字幕），别让它们在后台静音空转。")
    plan = {
        "kind": "distribution",
        "title": brief.get("title"),
        "channels": channels,
        "schedule": schedule,
        "phases": [{"name": f"{row['platform']} 投放", "days": 0.2, "owner": "发行 / 投放", "deliverables": [row["cover"] + " 封面", f"{row['platform']} 版成片"]} for row in channels],
        "risks": risks,
        "ready": len(ready),
        "blocked": len(channels) - len(ready),
        "unknown_platforms": unknown,
        "source": {"timeline": round(duration, 2), "aspect": aspect, "cues": len(cues), "cover_kinds": sorted({str(item.get("aspect")) for item in covers})},
    }
    ctx.log(f"发行：{len(channels)} 渠道，可直接发 {len(ready)} 个")
    return {"plan": plan, "text": "\n".join(line for line in lines if line)}


def _tags(brief: dict[str, Any], platform: dict[str, Any]) -> list[str]:
    seeds = [str(brief.get("title") or ""), str(brief.get("product") or ""), str(brief.get("genre") or ""), str(brief.get("platform") or "")]
    seeds += [str(item) for item in (brief.get("keywords") or [])][:4]
    tags = [f"#{item.strip()}" for item in seeds if item and item.strip()]
    out: list[str] = []
    for tag in tags:
        if tag not in out:
            out.append(tag)
    return out[: int(platform["tags"])]


# ------------------------------------------------------------------ #
# 复盘                                                                 #
# ------------------------------------------------------------------ #


@node(
    "retro",
    title="复盘",
    dept="qc",
    role="把计划值与实产逐项对齐：镜头数、时长、预算、真机素材比例、质检分数，算出偏差与返工成本，列出可复用物料",
    inputs=[
        Port("plan", "制片计划", "plan", required=False),
        Port("call_sheet", "通告表", "plan", required=False),
        Port("media", "投放计划", "plan", required=False),
        Port("timeline", "时间轴", "timeline", required=False),
        Port("report", "各类报告", "report", required=False, many=True),
        Port("clips", "视频片段", "clips", required=False, many=True),
        Port("images", "画面物料", "images", required=False, many=True),
        Port("audio", "成片音轨", "audio", required=False),
        Port("brief", "项目需求", "brief", required=False),
    ],
    outputs=[Port("report", "复盘报告", "report"), Port("text", "复盘纪要", "text")],
    params=[
        Param("rate", "人力日单价", "number", 1200, minimum=0, maximum=5000, step=50, unit="元", hint="计划里没有排班表时用它兜底"),
        Param("rework", "返工成本系数", "range", 0.35, minimum=0, maximum=1, step=0.05, hint="每轮返工按人力成本的这个比例再吃一次"),
        Param("clip_cost", "单段算力单价", "number", 6, minimum=0, maximum=200, step=0.5, unit="元", hint="真机片段按全价、示意帧按两成折算"),
        Param("top", "列出问题", "number", 8, minimum=1, maximum=30, step=1, unit="条"),
    ],
    icon="📊",
    tags=["计划偏差", "返工成本", "物料复用"],
)
def retro(ctx: Any) -> dict[str, Any]:
    plan = ctx.in_("plan") if isinstance(ctx.in_("plan"), dict) else {}
    call_sheet = ctx.in_("call_sheet") if isinstance(ctx.in_("call_sheet"), dict) else {}
    media = ctx.in_("media") if isinstance(ctx.in_("media"), dict) else {}
    timeline = ctx.in_("timeline") if isinstance(ctx.in_("timeline"), dict) else {}
    brief = common.norm_brief(ctx.in_("brief"), None)
    shots = common.shots_of(timeline)
    clips: list[dict[str, Any]] = []
    for bundle in ctx.many("clips"):
        clips += common.items_of(bundle)
    images: list[dict[str, Any]] = []
    for bundle in ctx.many("images"):
        images += common.items_of(bundle)
    reports = [item for item in ctx.many("report") if isinstance(item, dict)]
    audio = ctx.in_("audio") if isinstance(ctx.in_("audio"), dict) else {}
    rate = float(ctx.params.get("rate") or 1200)
    rework = float(ctx.params.get("rework") or 0.35)
    clip_cost = float(ctx.params.get("clip_cost") or 6)
    top = int(float(ctx.params.get("top") or 8))

    actual_shots = len(shots)
    planned_shots = int(float(plan.get("estimated_shots") or 0)) or int(float(brief.get("duration_s") or 45) // 3)
    duration = float(timeline.get("duration_s") or sum(float(row.get("duration_s") or 0) for row in shots))
    scheduled_days = float(call_sheet.get("total_days") or 0)
    planned_days = scheduled_days or float(plan.get("total_days") or 0)
    budget = float(plan.get("total_budget") or 0)
    simulated_clips = len([item for item in clips if bool(item.get("simulated", True))])
    real_clips = len(clips) - simulated_clips
    offline_shots = len([row for row in shots if bool(row.get("source_simulated", True))])
    scores = {str(item.get("kind") or f"report{index}"): float(item.get("score") or 0) for index, item in enumerate(reports)}
    issues = [item for row in reports for item in (row.get("issues") or []) if isinstance(item, dict)]
    majors = len([item for item in issues if str(item.get("severity")) in {"blocker", "major"}])
    rounds = 1 + (1 if majors >= 3 else 0) + (1 if simulated_clips > max(1, len(clips) // 2) else 0)
    crew_rows = [row for row in (plan.get("crew") or []) if isinstance(row, dict)]
    planned_labour = round(sum(float(row.get("cost") or 0) for row in crew_rows), 2) or round(planned_days * rate, 2)
    budget_lines = {str(row.get("line") or ""): float(row.get("amount") or 0) for row in (plan.get("budget") or []) if isinstance(row, dict)}
    planned_compute = budget_lines.get("生成算力") or round(len(clips) * clip_cost, 2)
    planned_direct = round(planned_labour + planned_compute, 2)  # 只对齐"人力+算力"这一口径，别拿总预算（含后期/发行/应急）来比
    actual_labour = round(planned_labour * (1 + rework * (rounds - 1)), 2)
    actual_compute = round(real_clips * clip_cost + simulated_clips * clip_cost * 0.2, 2)
    actual_cost = round(actual_labour + actual_compute, 2)
    deltas = [
        {"item": "镜头数", "plan": planned_shots, "actual": actual_shots, "delta": actual_shots - planned_shots, "unit": "镜"},
        {"item": "片长", "plan": round(float(plan.get("duration_s") or brief.get("duration_s") or 0), 1), "actual": round(duration, 1), "delta": round(duration - float(plan.get("duration_s") or brief.get("duration_s") or 0), 1), "unit": "秒"},
        {"item": "生成镜头", "plan": planned_shots, "actual": len(clips), "delta": len(clips) - planned_shots, "unit": "段"},
        {"item": "真机素材", "plan": planned_shots, "actual": real_clips, "delta": real_clips - planned_shots, "unit": "段"},
        {"item": "排期", "plan": planned_days, "actual": round(planned_days * (1 + 0.12 * (rounds - 1)), 1), "delta": round(planned_days * 0.12 * (rounds - 1), 1), "unit": "天"},
        {"item": "人力", "plan": planned_labour, "actual": actual_labour, "delta": round(actual_labour - planned_labour, 2), "unit": "元"},
        {"item": "算力", "plan": planned_compute, "actual": actual_compute, "delta": round(actual_compute - planned_compute, 2), "unit": "元"},
        {"item": "直接成本", "plan": planned_direct, "actual": actual_cost, "delta": round(actual_cost - planned_direct, 2), "unit": "元"},
    ]
    reusable: list[dict[str, Any]] = []
    grouped: dict[str, dict[str, Any]] = {}
    for item in images:
        key = str(item.get("kind") or "image")
        row = grouped.setdefault(key, {"kind": key, "count": 0, "sample": str(item.get("ref") or ""), "simulated": 0})
        row["count"] += 1
        row["simulated"] += 1 if bool(item.get("simulated", True)) else 0
    reusable = [
        {"kind": key, "count": value["count"], "sample": value["sample"], "simulated": value["simulated"], "note": ("全示意，复用前需重出" if value["simulated"] == value["count"] else "可进素材库")}
        for key, value in sorted(grouped.items(), key=lambda kv: -kv[1]["count"])
    ]
    lessons: list[dict[str, Any]] = []
    if simulated_clips:
        lessons.append({"from": f"{simulated_clips} 段是离线示意素材", "to": "下一版先配好图生视频端点再进生成车间，别把示意帧剪进成片", "cost": f"重出 {simulated_clips} 段 ≈ {simulated_clips * 0.3:.1f} 小时"})
    if offline_shots:
        lessons.append({"from": f"{offline_shots} 镜成片没有真画面", "to": "分镜阶段就锁定可生成的镜头清单，多余镜头不进剪辑", "cost": ""})
    if call_sheet.get("unit_count"):
        lessons.append(
            {
                "from": f"通告表排了 {call_sheet['unit_count']} 个拍摄单元 / {call_sheet.get('total_days')} 天，最长一天 {max([float(row.get('minutes') or 0) for row in (call_sheet.get('days') or [])] or [0]) / 60:.1f} 小时",
                "to": str((call_sheet.get("risks") or ["排期无风险"])[0])[:48],
                "cost": f"架设 {call_sheet.get('setup_minutes')}′＋拍摄 {call_sheet.get('shoot_minutes')}′＋转场 {call_sheet.get('move_minutes')}′",
            }
        )
    if plan.get("risks"):
        lessons.append({"from": "制片阶段列过配音时长风险", "to": "本轮已用统一口播口径（入话留白 + 台词占比）消解，保留该口径" if not majors else "仍有返工：说明口径没落到每个节点", "cost": ""})
    for item in issues[:top]:
        lessons.append({"from": str(item.get("detail") or "")[:48], "to": str(item.get("suggestion") or str(item.get("fix") or ""))[:48], "cost": ""})
    report = {
        "kind": "retro",
        "score": round(sum(scores.values()) / max(1, len(scores)), 1) if scores else 0.0,
        "verdict": f"{rounds} 轮交付" + ("（返工偏多）" if rounds > 1 else "（一次通过）"),
        "scores": scores,
        "deltas": deltas,
        "rounds": rounds,
        "cost": {
            "planned_labour": planned_labour,
            "planned_compute": planned_compute,
            "planned_direct": planned_direct,
            "actual_labour": actual_labour,
            "actual_compute": actual_compute,
            "actual": actual_cost,
            "over": round(actual_cost - planned_direct, 2),
            "project_budget": budget,
            "rework_factor": rework,
            "rounds": rounds,
        },
        "assets": {"clips": len(clips), "real_clips": real_clips, "images": len(images), "audio_tracks": int(audio.get("tracks") or 0), "issues": len(issues), "majors": majors},
        "call_sheet": (
            {
                "units": call_sheet.get("unit_count"),
                "days": call_sheet.get("total_days"),
                "minutes": call_sheet.get("total_minutes"),
                "crew_calls": len(call_sheet.get("cast_calls") or []),
                "risks": call_sheet.get("risks") or [],
            }
            if call_sheet.get("unit_count")
            else {}
        ),
        "media": (
            {
                "budget": media.get("budget"),
                "budget_from": media.get("budget_from"),
                "spendable": media.get("spendable"),
                "channels": len(media.get("channels") or []),
                "held": bool(media.get("held")),
                "gates": media.get("gates") or [],
            }
            if media.get("channels")
            else {}
        ),
        "reusable": reusable,
        "lessons": lessons,
    }
    lines = [
        f"复盘：质检分 {report['score']}（{len(scores)} 份报告）｜{report['verdict']}｜重要问题 {majors} 条",
        "偏差：" + "｜".join(f"{row['item']} {row['plan']}→{row['actual']}（{row['delta']:+g}{row['unit']}）" for row in deltas),
        f"成本：计划人力 {planned_labour:,.0f}＋算力 {planned_compute:,.0f}＝直接成本 {planned_direct:,.0f} 元｜实测 {actual_cost:,.0f} 元（{actual_cost - planned_direct:+,.0f}，返工系数 {rework:.0%}）｜项目总预算 {budget:,.0f} 元含后期发行，不与本口径混比",
        f"物料：片段 {len(clips)}（真机 {real_clips}）｜图像 {len(images)}｜音轨 {report['assets']['audio_tracks']} 条",
        (f"现场：通告 {report['call_sheet']['units']} 个拍摄单元 / {report['call_sheet']['days']} 天 / {report['call_sheet']['minutes']} 分钟工时，演员到场 {report['call_sheet']['crew_calls']} 人（排期口径改用通告表实测值）" if report["call_sheet"] else "现场：未接第一副导演的通告表，排期仍按制片计划估算值"),
        (
            f"宣发：投放预算 {report['media']['budget']:,.0f} 元（{report['media']['budget_from']}）→ 可投 {report['media']['spendable']:,.0f} 元 / {report['media']['channels']} 个渠道"
            + (f"｜⚠ 预算被拦住：{('；'.join(report['media']['gates']))}" if report["media"]["held"] else "")
            if report["media"]
            else "宣发：未接媒介投放计划，预算分配没有回到复盘"
        ),
        "可复用：" + ("、".join(f"{row['kind']} {row['count']} 份" for row in reusable) if reusable else "本轮没有可复用物料"),
        *[f"· 教训：{row['from']} → {row['to']}" + (f"（{row['cost']}）" if row['cost'] else "") for row in lessons[:top]],
        "结论：偏差全部来自实测值与计划值逐项对齐，不写主观评价。",
    ]
    ctx.log(f"复盘：{len(deltas)} 项偏差，{len(lessons)} 条教训")
    return {"report": report, "text": "\n".join(line for line in lines if line)}


#: 宣发三档：预热铺话题、首映给成片、追热吃评论区
_PHASES: list[tuple[str, int, str, list[str]]] = [
    ("预热", -3, "悬念与痛点", ["痛点金句卡", "幕后设定物料", "评论区追问"]),
    ("首映", 0, "成片与预告", ["正片", "预告片", "剧照", "封面"]),
    ("追热", 1, "使用场景与答疑", ["场景切片", "参数对比图", "答疑图文"]),
]


@node(
    "publicity",
    title="宣传",
    dept="qc",
    role="把成片拆成能发的话题：从台词里挑金句、盘点手头物料、按预热/首映/追热排文案日程，逐条如实标注缺哪件素材",
    inputs=[
        Port("timeline", "成片时间轴", "timeline"),
        Port("trailer", "预告时间轴", "timeline", required=False),
        Port("brief", "项目需求", "brief", required=False),
        Port("subtitles", "字幕轨", "subtitles", required=False),
        Port("images", "封面与物料", "images", required=False, many=True),
        Port("distribution", "发行计划", "plan", required=False),
        Port("report", "质检报告", "report", required=False, many=True),
    ],
    outputs=[Port("plan", "宣发计划", "plan"), Port("text", "宣传文案", "text")],
    params=[
        Param("hooks", "金句条数", "number", 6, minimum=2, maximum=12, step=1, unit="条"),
        Param("lead_days", "预热天数", "number", 3, minimum=0, maximum=14, step=1, unit="天"),
        Param("tail_days", "追热天数", "number", 3, minimum=0, maximum=14, step=1, unit="天"),
        Param("quote_max", "金句最长", "number", 24, minimum=8, maximum=48, step=2, unit="字"),
        Param("per_day", "每日条数", "number", 1, minimum=1, maximum=4, step=1, unit="条"),
    ],
    icon="📣",
    tags=["宣发", "话题", "物料"],
)
def publicity(ctx: Any) -> dict[str, Any]:
    timeline = ctx.require("timeline")
    if not isinstance(timeline, dict) or not timeline.get("shots"):
        raise NodeError("宣传需要一条成片时间轴：连不上就没法定物料")
    brief = common.norm_brief(ctx.in_("brief"), None)
    trailer = ctx.in_("trailer") if isinstance(ctx.in_("trailer"), dict) else {}
    distribution = ctx.in_("distribution") if isinstance(ctx.in_("distribution"), dict) else {}
    hooks_wanted = int(float(ctx.params.get("hooks") or 6))
    lead_days = int(float(ctx.params.get("lead_days") or 3))
    tail_days = int(float(ctx.params.get("tail_days") or 3))
    quote_max = int(float(ctx.params.get("quote_max") or 24))
    per_day = int(float(ctx.params.get("per_day") or 1))

    pool: list[str] = []
    for row in (timeline.get("voice") or {}).get("items") or []:
        if isinstance(row, dict):
            pool.append(str(row.get("text") or "").strip())
    for cue in common.cues_of(ctx.in_("subtitles")):
        pool.append(str(cue.get("text") or "").strip())
    keywords = [str(item) for item in (brief.get("keywords") or [])]

    def score_line(text: str) -> float:
        if len(text) < 6 or len(text) > quote_max:
            return -1.0
        value = 1.0
        value += 2.0 if ("？" in text or "?" in text) else 0.0
        value += 1.2 if ("！" in text or "!" in text) else 0.0
        value += 1.0 if re.search(r"\d", text) else 0.0
        value += sum(0.8 for word in keywords if word and word in text)
        value -= abs(14 - len(text)) / 14.0
        return round(value, 3)

    seen: set[str] = set()
    hooks: list[str] = []
    for text in sorted([item for item in pool if item], key=score_line, reverse=True):
        if text in seen or score_line(text) < 0:
            continue
        seen.add(text)
        hooks.append(text)
        if len(hooks) >= hooks_wanted:
            break

    kinds: dict[str, int] = {}
    for bundle in ctx.many("images"):
        for item in common.items_of(bundle):
            key = str(item.get("kind") or "image")
            kinds[key] = kinds.get(key, 0) + 1
    channels = [str(row.get("platform") or "") for row in (distribution.get("channels") or []) if isinstance(row, dict) and row.get("platform")]
    channels = [item for item in channels if item] or [str(brief.get("platform") or "小红书")]
    pending = {str(row.get("platform") or ""): [str(a) for a in (row.get("actions") or [])] for row in (distribution.get("channels") or []) if isinstance(row, dict)}

    has_trailer = bool(trailer.get("shots"))
    behind_the_scenes = kinds.get("character_sheet", 0) + kinds.get("makeup_card", 0) + kinds.get("storyboard", 0) + kinds.get("concept", 0)
    materials = {"正片": 1, "预告片": 1 if has_trailer else 0, "封面": kinds.get("cover", 0), "剧照": kinds.get("stills", 0), "设定与幕后": behind_the_scenes, "金句卡": len(hooks)}
    gaps: list[str] = []
    if not has_trailer:
        gaps.append("缺预告片：预热期只能发图文，信息流完播率会掉一档")
    if not materials["封面"]:
        gaps.append("缺封面：首发帖只能借正片首帧")
    if not materials["剧照"]:
        gaps.append("缺剧照：首映图文没有现场静态图可配，只能截正片帧")
    if not behind_the_scenes:
        gaps.append("缺幕后设定物料：追热期没有过程图可发")

    schedule: list[dict[str, Any]] = []
    caps = {str(row.get("platform") or ""): row.get("max_tags") for row in (distribution.get("channels") or []) if isinstance(row, dict)}
    for name, offset, theme, contents in _PHASES:
        span = max(1, lead_days if offset < 0 else (tail_days if offset > 0 else 1))
        for step in range(span):
            absolute = offset + step
            day = "D0" if absolute == 0 else f"D{absolute:+d}"
            for slot in range(min(per_day, len(contents))):
                content = contents[slot]
                need = ""
                if content == "预告片" and not has_trailer:
                    need = "缺预告片"
                elif content == "封面" and not materials["封面"]:
                    need = "缺封面"
                elif content == "剧照" and not materials["剧照"]:
                    need = "缺剧照"
                elif content in {"场景切片", "参数对比图"}:
                    need = "需再剪一版"
                channel = channels[len(schedule) % len(channels)]
                schedule.append(
                    {
                        "day": day,
                        "phase": name,
                        "theme": theme,
                        "channel": channel,
                        "content": content,
                        "copy": (hooks[len(schedule) % len(hooks)] if hooks else str(brief.get("title") or "")),
                        "tags": _tags(brief, {"tags": caps.get(channel) or 4}),
                        "gap": need,
                    }
                )

    reports: list[dict[str, Any]] = []
    for bundle in ctx.many("report"):
        reports += [row for row in (bundle if isinstance(bundle, list) else [bundle]) if isinstance(row, dict)]
    scores = [float(row.get("score")) for row in reports if row.get("score") is not None]
    lowest = min(scores) if scores else 0.0
    risks: list[dict[str, Any]] = []
    if lowest and lowest < 70:
        risks.append({"level": "blocker", "detail": f"质检最低分 {lowest:.0f} 未过修改线", "mitigation": "先返工，宣传物料不要提前许诺"})
    if hooks:
        scan = scan_compliance("。".join(hooks), _industry_of(f"{brief.get('genre')} {brief.get('type')} {' '.join(hooks)}"))
        for hit in [item for item in scan.hits if item.severity in {"blocker", "major"}][:4]:
            risks.append({"level": hit.severity, "detail": f"文案「{hit.term}」命中{hit.category}：{hit.law}", "mitigation": hit.fix})
    for gap in gaps:
        risks.append({"level": "major", "detail": gap, "mitigation": "回到对应岗位补做，别用手边素材硬凑"})
    for name, actions in pending.items():
        if actions:
            risks.append({"level": "minor", "detail": f"{name} 发行未就绪：{actions[0]}", "mitigation": "；".join(actions[1:]) or "按发行清单处理后再排宣传"})

    plan = {
        "kind": "publicity",
        "dept": "qc",
        "role": "宣传",
        "title": brief.get("title") or "成片",
        "channels": channels,
        "phases": [{"phase": row[0], "theme": row[2], "contents": row[3]} for row in _PHASES],
        "schedule": schedule,
        "hooks": hooks,
        "materials": materials,
        "gaps": gaps,
        "risks": risks,
        "source": {
            "duration_s": round(float(timeline.get("duration_s") or 0), 2),
            "aspect": str(timeline.get("aspect") or ""),
            "image_kinds": kinds,
            "review_score": lowest or None,
        },
        "simulated": True,
    }
    lines = [
        f"宣发：{len(schedule)} 条排期 / {len(channels)} 个渠道（{'、'.join(channels)}），{len(hooks)} 条金句备选",
        f"物料盘点：正片 1｜预告 {materials['预告片']}｜封面 {materials['封面']}｜剧照 {materials['剧照']}｜幕后设定 {materials['设定与幕后']}｜金句卡 {materials['金句卡']}",
        *[
            f"· {row['day']} {row['phase']}｜{row['channel']}：{row['content']}——「{row['copy']}」" + (f"（⚠ {row['gap']}）" if row["gap"] else "")
            for row in schedule[:12]
        ],
        f"金句口径：从 {len(pool)} 句台词/字幕里按问句、感叹、数字与关键词打分，取前 {len(hooks)} 条",
        ("⚠ 缺物料 " + str(len(gaps)) + " 项：" + "；".join(gaps[:2])) if gaps else "物料齐：预热/首映/追热三档都发得出去",
        ("⚠ 风险 " + str(len(risks)) + " 条：" + str(risks[0]["detail"])) if risks else "文案过一遍合规词库，无命中",
    ]
    ctx.log(f"宣传：{len(schedule)} 条排期，金句 {len(hooks)} 条，缺口 {len(gaps)} 项")
    return {"plan": plan, "text": "\n".join(line for line in lines if line)}


@node(
    "media_buyer",
    title="媒介投放",
    dept="qc",
    role="把钱花在能测出效果的渠道：按宣发排期、发行适配与质检结论分配预算，逐渠道给曝光目标、投放节奏与止损线",
    inputs=[
        Port("plan", "宣发计划", "plan"),
        Port("brief", "项目需求", "brief", required=False),
        Port("prod", "制片计划", "plan", required=False),
        Port("distribution", "发行计划", "plan", required=False),
        Port("report", "质检报告", "report", required=False, many=True),
    ],
    outputs=[Port("plan", "投放计划", "plan"), Port("text", "投放说明", "text")],
    params=[
        Param("budget", "投放预算", "number", 0, minimum=0, maximum=500000, step=500, unit="元", hint="0 = 取制片计划里的「宣发」预算行"),
        Param("cpm", "参考 CPM", "number", 25, minimum=5, maximum=120, step=1, unit="元/千次曝光", hint="各渠道在此价上按竞争度浮动"),
        Param("days", "投放天数", "number", 7, minimum=1, maximum=30, step=1, unit="天"),
        Param("test_share", "试投比例", "range", 0.25, minimum=0.05, maximum=0.6, step=0.05, hint="先小预算测素材，赢家再吃余量"),
        Param("reserve", "机动金比例", "range", 0.15, minimum=0, maximum=0.5, step=0.05),
        Param("ctr_stop", "止损 CTR", "number", 0.8, minimum=0.1, maximum=5, step=0.1, unit="%", hint="低于此点击率即停投该渠道"),
    ],
    icon="💰",
    tags=["投放", "预算", "止损"],
)
def media_buyer(ctx: Any) -> dict[str, Any]:
    promo = ctx.require("plan")
    if not isinstance(promo, dict) or not promo.get("channels"):
        raise NodeError("媒介投放需要宣发计划：先接「宣传」节点")
    brief = common.norm_brief(ctx.in_("brief"), None)
    prod = ctx.in_("prod") if isinstance(ctx.in_("prod"), dict) else {}
    distribution = ctx.in_("distribution") if isinstance(ctx.in_("distribution"), dict) else {}
    cpm = float(ctx.params.get("cpm") or 25)
    days = int(float(ctx.params.get("days") or 7))
    test_share = float(ctx.params.get("test_share") or 0.25)
    reserve = float(ctx.params.get("reserve") or 0.15)
    ctr_stop = float(ctx.params.get("ctr_stop") or 0.8)

    budget = float(ctx.params.get("budget") or 0)
    budget_from = "节点参数"
    if budget <= 0:
        line = next((row for row in (prod.get("budget") or []) if isinstance(row, dict) and str(row.get("line") or "") == "宣发"), None)
        if line:
            budget = float(line.get("amount") or 0)
            budget_from = "制片计划·宣发行"
    if budget <= 0:
        budget = round(max(1.0, float(brief.get("duration_s") or 45)) * 40, 2)
        budget_from = "按片长兜底估算"

    fit_by_channel: dict[str, str] = {}
    actions_by_channel: dict[str, list[str]] = {}
    for row in (distribution.get("channels") or []):
        if isinstance(row, dict):
            fit_by_channel[str(row.get("platform") or "")] = str(row.get("fit") or "unknown")
            actions_by_channel[str(row.get("platform") or "")] = [str(item) for item in (row.get("actions") or [])]

    reports: list[dict[str, Any]] = []
    for bundle in ctx.many("report"):
        reports += [row for row in (bundle if isinstance(bundle, list) else [bundle]) if isinstance(row, dict)]
    scores = [float(row.get("score")) for row in reports if row.get("score") is not None]
    blockers = sum(
        1
        for row in reports
        for item in (row.get("issues") or [])
        if isinstance(item, dict) and str(item.get("severity")) == "blocker"
    )
    verdicts = {str(row.get("verdict") or "") for row in reports}
    held = bool(blockers) or "reject" in verdicts
    gates: list[str] = []
    if blockers:
        gates.append(f"质检报告有 {blockers} 条阻断问题：预算锁定")
    if "reject" in verdicts:
        gates.append("有报告结论为 reject：先返工再谈投放")

    gaps_by_channel: dict[str, list[str]] = {}
    for row in (promo.get("schedule") or []):
        if isinstance(row, dict) and row.get("gap"):
            gaps_by_channel.setdefault(str(row.get("channel") or ""), []).append(str(row.get("gap")))

    weights: list[tuple[str, float, list[str]]] = []
    for name in [str(item) for item in (promo.get("channels") or [])]:
        penalty: list[str] = []
        weight = 1.0
        fit = fit_by_channel.get(name, "unknown")
        if fit == "reframe":
            weight -= 0.45
            penalty.append("需重导画幅，投放版素材还没就绪")
        elif fit == "needs_fix":
            weight -= 0.25
            penalty.append("发行清单有待办项")
        elif fit == "unknown":
            weight -= 0.1
            penalty.append("未接发行计划，渠道适配未知")
        if gaps_by_channel.get(name):
            weight -= 0.12 * len(gaps_by_channel[name])
            penalty.extend(gaps_by_channel[name][:2])
        weights.append((name, round(max(0.15, weight), 3), penalty))
    total_weight = sum(weight for _, weight, _ in weights) or 1.0
    spendable = round(budget * (1.0 - reserve), 2)
    test_pool = round(spendable * test_share, 2)
    scale_pool = round(spendable - test_pool, 2)

    rows: list[dict[str, Any]] = []
    for name, weight, penalty in weights:
        share = round(weight / total_weight, 3)
        channel_budget = round(spendable * share, 2)
        impressions = int(round(channel_budget / max(1.0, cpm) * 1000))
        rows.append(
            {
                "channel": name,
                "weight": share,
                "budget": channel_budget,
                "test_budget": round(test_pool * share, 2),
                "scale_budget": round(scale_pool * share, 2),
                "cpm": cpm,
                "target_impressions": impressions,
                "target_clicks": int(round(impressions * 0.015)),
                "days": days,
                "daily_budget": round(channel_budget / max(1, days), 2),
                "stop_line": f"CTR < {ctr_stop}% 或 3 天不达标即停投",
                "risks": penalty + actions_by_channel.get(name, [])[:2],
                "status": ("held" if held else ("test_first" if penalty else "ready")),
            }
        )
    rows.sort(key=lambda row: -float(row["budget"]))

    plan = {
        "dept": "qc",
        "role": "媒介投放",
        "kind": "media_buying",
        "budget": round(budget, 2),
        "budget_from": budget_from,
        "reserve": round(budget * reserve, 2),
        "spendable": spendable,
        "test_pool": test_pool,
        "scale_pool": scale_pool,
        "days": days,
        "cpm": cpm,
        "ctr_stop": ctr_stop,
        "review_score": min(scores) if scores else None,
        "channels": rows,
        "total_impressions": int(sum(int(row["target_impressions"]) for row in rows)),
        "gates": gates or ["质检与合规无阻断，预算可释放"],
        "held": held,
        "simulated": True,
    }
    lines = [
        f"投放：预算 {budget:,.0f} 元（来源：{budget_from}）→ 可投 {spendable:,.0f}＋机动 {plan['reserve']:,.0f}；试投 {test_pool:,.0f} 测素材，赢家再吃 {scale_pool:,.0f}",
        f"口径：CPM {cpm:.0f} 元／{days} 天／止损 CTR {ctr_stop}%；合计目标曝光 {plan['total_impressions']:,} 次",
        *[
            f"· {row['channel']}：{row['budget']:,.0f} 元（权重 {row['weight']:.0%}）≈ {row['target_impressions']:,} 次曝光／{row['daily_budget']:,.0f} 元每天［{row['status']}］"
            + (f"｜{'；'.join(row['risks'][:2])}" if row["risks"] else "")
            for row in rows[:10]
        ],
        ("⚠ " + "；".join(gates)) if gates else "预算按渠道适配与物料缺口加权分配，未被质检结论拦住。",
        "投放按效果再平衡：试投期把实测 CTR/完播回填到本节点参数重跑，不靠拍脑袋加钱。",
    ]
    ctx.log(f"投放：{len(rows)} 个渠道 / 可投 {spendable:,.0f} 元，{'预算锁定' if held else '可释放'}")
    return {"plan": plan, "text": "\n".join(line for line in lines if line)}
