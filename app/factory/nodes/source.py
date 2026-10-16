"""输入 · 输出岗位：项目输入、文本、剧本导入、参考素材、模型配置、放映室。"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from ...config import ASSETS_DIR
from ...core.assets import image_size, load_local
from .. import canvas, craft, media
from ..spec import NodeError, Param, Port, node
from . import common

_DURATION_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(秒|s|分|min|分钟|m)", re.IGNORECASE)

_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}


def _ext_of(ref: str, blob: bytes) -> str:
    """参考素材按真实图片扩展名落盘：接口靠后缀回 MIME，浏览器才肯把它当图片渲染。"""
    tail = urlparse(ref).path.rpartition(".")[2].lower()
    if tail and f".{tail}" in _IMAGE_EXTS:
        return f".{tail}"
    if blob[:4] == b"\x89PNG":
        return ".png"
    if blob[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if blob[8:12] == b"WEBP":
        return ".webp"
    if blob[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    return ".img"

#: 预设里的「参考素材」岗位要吃真实字节：这张示例色板由本机渲染器生成后落进
#: ``data/assets``，建项目时播种一次，节点走的仍是和普通上传素材同一条
#: ``load_local`` 路径（相对文件名、拒绝越界、8MB 上限）。
DEMO_ASSET_NAME = "factory-demo-look.png"
DEMO_ASSET_REF = f"assets/{DEMO_ASSET_NAME}"


def ensure_demo_asset() -> str:
    path = ASSETS_DIR / DEMO_ASSET_NAME
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(
            canvas.poster_frame(
                {"palette": ["#1a2338", "#2b3f66", "#6f86c9"], "accent": "#22d3ee"},
                "FACTORY DEMO LOOK",
                "16:9",
                640,
                note="sample reference plate",
            )
        )
    return DEMO_ASSET_REF
_SCENE_HEAD = re.compile(r"^\s*(?:#{1,4}\s*)?(场景|SCENE|场)\s*([0-9一二三四五六七八九十]*)[:：\.\s]?(.*)$", re.IGNORECASE)
_LINE_HEAD = re.compile(r"^\s*([^\s：:（(\[]【]{1,12})(?:（[^）]*】|\[[^\]]*\]|【[^】]*】)?\s*[:：]\s*(.+)$")


def _seconds_of(text: str, fallback: float) -> float:  # pragma: no cover - 供场次标注解析复用
    match = _DURATION_RE.search(text or "")
    if not match:
        return fallback
    value = float(match.group(1))
    unit = match.group(2).lower()
    return value * 60.0 if unit in {"分", "min", "分钟", "m"} else value


@node(
    "project_input",
    title="项目输入",
    dept="io",
    role="制片方需求单：片名、类型、时长、画幅、平台、预算与梗概，全厂唯一的需求源头",
    outputs=[Port("brief", "项目需求", "brief")],
    params=[
        Param("title", "片名", "text", "未命名项目"),
        Param("type", "片种", "select", "短视频", ["短视频", "广告片", "种草视频", "微电影", "纪录片", "MV", "动画短片"]),
        Param("genre", "类型", "text", "剧情"),
        Param("duration_s", "目标时长", "number", 45, minimum=5, maximum=900, step=5, unit="秒"),
        Param("aspect", "画幅", "select", "9:16", ["9:16", "16:9", "1:1", "3:4", "4:3", "2:3"]),
        Param("platform", "发行平台", "text", "抖音"),
        Param("audience", "目标受众", "text", "25-35 岁城市青年"),
        Param("tone", "基调", "text", "真实、松弛、有共鸣"),
        Param("language", "语言", "select", "zh", ["zh", "en", "ja", "ko", "es"]),
        Param("budget", "预算", "number", 30000, minimum=0, maximum=10_000_000, step=1000, unit="元"),
        Param("brand", "品牌 / 出品方", "text", ""),
        Param("goal", "传播目标", "select", "曝光", ["曝光", "种草", "转化", "品牌认知", "粉丝增长"]),
        Param("keywords", "关键词", "text", "", hint="逗号分隔，会进入选题、SEO 与话题标签"),
        Param("synopsis", "故事梗概 / 卖点", "textarea", "", hint="留空则由策划与编剧按类型与关键词自拟"),
    ],
    icon="📋",
    source=True,
    tags=["需求", "源头"],
)
def project_input(ctx: Any) -> dict[str, Any]:
    brief = common.norm_brief(None, ctx.params)
    ctx.log(
        f"需求单已建立：{brief['title']}｜{brief['type']}｜{brief['duration_s']}s｜{brief['aspect']}",
        duration_s=brief["duration_s"],
    )
    return {"brief": brief}


@node(
    "text_input",
    title="文本输入",
    dept="io",
    role="一段自由文本：灵感、口播稿、参考资料摘录，可直接喂给下游岗位",
    outputs=[Port("text", "文本", "text")],
    params=[Param("text", "正文", "textarea", ""), Param("label", "备注名", "text", "备注")],
    icon="📝",
    source=True,
    tags=["素材"],
)
def text_input(ctx: Any) -> dict[str, Any]:
    text = str(ctx.params.get("text") or "").strip()
    if not text:
        raise NodeError("文本输入节点为空：请填写正文，或改接上游岗位的输出")
    return {"text": text}


@node(
    "screenplay_input",
    title="剧本导入",
    dept="io",
    role="把已有文学剧本解析成结构化剧本：识别场次、内外景、时段、人物与对白",
    outputs=[Port("screenplay", "剧本", "screenplay")],
    params=[
        Param("text", "剧本文本", "textarea", "", hint="每场以「场景N 内/外 地点 日/夜」开头，对白写成「人物：台词」"),
        Param("title", "片名", "text", "导入剧本"),
        Param("default_duration", "无对白场次时长", "number", 3.5, minimum=1, maximum=12, step=0.5, unit="秒"),
    ],
    icon="📥",
    source=True,
    tags=["导入"],
)
def screenplay_input(ctx: Any) -> dict[str, Any]:
    raw = str(ctx.params.get("text") or "").strip()
    if not raw:
        raise NodeError("剧本导入需要文本：把文学剧本粘贴到参数里")
    default_seconds = float(ctx.params.get("default_duration") or 3.5)
    scenes: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def flush() -> None:
        if current and (current["action"] or current["dialogue"]):
            scenes.append(current)

    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        head = _SCENE_HEAD.match(line)
        if head:
            flush()
            rest = head.group(3) or ""
            int_ext = "INT" if ("内" in rest or " INT" in rest.upper()) else ("EXT" if ("外" in rest or "EXT" in rest.upper()) else "INT")
            time_of_day = "night"
            for key, label in (("日", "day"), ("夜", "night"), ("晨", "dawn"), ("昏", "dusk"), ("傍晚", "dusk"), ("白天", "day")):
                if key in rest:
                    time_of_day = label
                    break
            cleaned = re.sub(r"(内|外|INT|EXT|日|夜|晨|昏|白天|傍晚)", " ", rest, flags=re.IGNORECASE)
            location = " ".join(part for part in cleaned.split() if part).strip(" :：-—") or f"场景 {len(scenes) + 1}"
            number = head.group(2) or str(len(scenes) + 1)
            current = {
                "id": f"sc{len(scenes) + 1}",
                "index": len(scenes),
                "title": f"场 {number}｜{location}",
                "location": location,
                "int_ext": int_ext,
                "time_of_day": time_of_day,
                "characters": [],
                "props": [],
                "action": "",
                "dialogue": [],
                "beat": craft.beats_for(len(scenes), max(1, len(raw.splitlines()) // 8 + 1)),
                "mood": "neutral",
                "duration_s": default_seconds,
            }
            continue
        if current is None:
            flush()
            current = {
                "id": "sc1",
                "index": 0,
                "title": "场 1",
                "location": "未标注场景",
                "int_ext": "INT",
                "time_of_day": "day",
                "characters": [],
                "props": [],
                "action": line,
                "dialogue": [],
                "beat": "setup",
                "mood": "neutral",
                "duration_s": default_seconds,
            }
            continue
        speech = _LINE_HEAD.match(line)
        if speech:
            speaker = speech.group(1).strip()
            content = speech.group(2).strip()
            current["dialogue"].append({"character": speaker, "line": content})
            if speaker not in current["characters"] and speaker not in {"旁白", "画外音", "VO"}:
                current["characters"].append(speaker)
            current["duration_s"] = round(
                float(current["duration_s"]) + craft.chinese_duration(content), 2
            )
        else:
            current["action"] = f"{current['action']} {line}".strip()
            for name in re.findall(r"([\u4e00-\u9fa5A-Za-z]{2,6})(?:转身|走入|拿起|抬头|看向|推开|跑向)", line):
                if name not in current["characters"]:
                    current["characters"].append(name)
    flush()
    if not scenes:
        raise NodeError("没有解析出任何场次：请检查剧本格式（每场以「场景N …」开头）")
    for scene in scenes:
        scene["duration_s"] = round(max(default_seconds, float(scene["duration_s"])), 2)
    total = round(sum(float(scene["duration_s"]) for scene in scenes), 2)
    screenplay = {
        "title": str(ctx.params.get("title") or "导入剧本"),
        "logline": (scenes[0]["action"] or scenes[0]["title"])[:80],
        "scenes": scenes,
        "total_s": total,
        "source": "imported",
        "stats": {"scene_count": len(scenes), "dialogue_lines": sum(len(item["dialogue"]) for item in scenes)},
    }
    ctx.log(f"解析出 {len(scenes)} 场、约 {total}s", scenes=len(scenes))
    return {"screenplay": screenplay}


@node(
    "ref_images",
    title="参考素材",
    dept="io",
    role="导入本机参考图（data/assets 下的文件或图片 URL），供美术、分镜与生成岗位取材",
    outputs=[Port("images", "图像", "images")],
    params=[
        Param("refs", "素材清单", "textarea", "", hint="每行一个：data/assets 下的图片（assets/xxx.png 或 xxx.png）或 https://…"),
        Param("kind", "用途", "select", "reference", ["reference", "keyframe", "character", "prop", "logo"]),
        Param("note", "说明", "text", "美术参考"),
    ],
    icon="🖼️",
    source=True,
    tags=["素材"],
)
def ref_images(ctx: Any) -> dict[str, Any]:
    lines = [item.strip() for item in re.split(r"[\n,;，、]+", str(ctx.params.get("refs") or "")) if item.strip()]
    if not lines:
        raise NodeError("参考素材为空：请填写 assets/ 下的文件名或图片地址")
    items: list[dict[str, Any]] = []
    for index, ref in enumerate(lines):
        kind = str(ctx.params.get("kind") or "reference")
        if ref.startswith(("http://", "https://")):
            blob = media.download(ref, limit_mb=12)
            provider = "remote"
            if not blob:
                raise NodeError(f"参考图下载失败：{ref}")
        else:
            blob, issue = load_local(re.sub(r"^(\./)?assets/", "", ref, flags=re.IGNORECASE))
            provider = "local"
            if not blob:
                raise NodeError(f"参考图读取失败：{ref}（{issue or '仅支持 data/assets 下的相对文件名'}）")
        saved = ctx.save_file(f"ref-{index + 1}-{common.slug(ref)}{_ext_of(ref, blob)}", blob)
        size = image_size(blob) or (0, 0)
        items.append(
            {
                "id": f"ref{index + 1}",
                "kind": kind,
                "ref": saved["ref"],
                "url": saved["url"],
                "source": ref,
                "note": str(ctx.params.get("note") or ""),
                "w": size[0],
                "h": size[1],
                "bytes": saved["bytes"],
                "provider": provider,
                "simulated": False,
            }
        )
    ctx.log(f"导入 {len(items)} 张参考素材")
    return {"images": {"items": items}}


@node(
    "model_config",
    title="生成模型配置",
    dept="io",
    role="摄影棚的机器房：为图像 / 视频 / 数字人 / 语音岗位选定服务商、尺寸、时长与随机种子",
    outputs=[Port("model", "模型配置", "model")],
    params=[
        Param("task", "任务", "select", "i2v", ["t2i", "i2v", "t2v", "dh", "tts"]),
        Param("provider", "通道", "select", "auto", ["auto", "sample", "openai", "http", "local"]),
        Param("model", "模型名", "text", "", hint="留空用服务商默认；例如 kling-v1.5 / wanx2.1 / sdxl"),
        Param("size", "出图尺寸", "select", "1024x1024", ["1024x1024", "1024x1536", "1536x1024", "768x1024", "512x512"]),
        Param("duration_s", "片段时长", "number", 5, minimum=1, maximum=30, step=1, unit="秒"),
        Param("steps", "采样步数", "number", 28, minimum=8, maximum=80, step=1),
        Param("cfg", "提示词强度", "number", 6.5, minimum=1, maximum=20, step=0.5),
        Param("seed", "随机种子", "number", 20260, minimum=0, maximum=999999, step=1),
        Param("motion", "运动幅度", "range", 0.45, minimum=0, maximum=1, step=0.05),
    ],
    icon="🧠",
    source=True,
    needs_service=True,
    tags=["模型", "服务商"],
)
def model_config(ctx: Any) -> dict[str, Any]:
    image_ready, image_provider = media.image_provider()
    video_ready, video_provider = media.video_provider()
    voice_ready, voice_provider = media.voice_provider()
    task = str(ctx.params.get("task") or "i2v")
    requested = str(ctx.params.get("provider") or "auto")
    table = {
        "t2i": (image_ready, image_provider),
        "i2v": (video_ready, video_provider),
        "t2v": (video_ready, video_provider),
        "dh": (video_ready, video_provider),
        "tts": (voice_ready, voice_provider),
    }
    ready, detected = table.get(task, (False, "sample"))
    provider = detected if requested == "auto" else requested
    if provider == "auto":
        provider = "sample"
    model = {
        "task": task,
        "provider": provider if ready or requested != "auto" else "sample",
        "requested": requested,
        "model": str(ctx.params.get("model") or ""),
        "size": str(ctx.params.get("size") or "1024x1024"),
        "duration_s": float(ctx.params.get("duration_s") or 5),
        "steps": int(float(ctx.params.get("steps") or 28)),
        "cfg": float(ctx.params.get("cfg") or 6.5),
        "seed": int(float(ctx.params.get("seed") or 0)),
        "motion": float(ctx.params.get("motion") or 0.4),
        "endpoint_ready": ready,
        "detected_provider": detected,
    }
    ctx.log(
        f"模型通道：{task} → {model['provider']}"
        + ("（端点已配置）" if ready else "（未配置端点，走离线示意通道）"),
        ready=ready,
    )
    return {"model": model}


@node(
    "screening",
    title="放映室",
    dept="io",
    role="成片出口：接收剪辑时间轴，做完整性体检并给出可放映 / 可导出的规格摘要",
    inputs=[
        Port("timeline", "时间轴", "timeline"),
        Port("audio", "音频", "audio", required=False),
        Port("subtitles", "字幕轨", "subtitles", required=False),
        Port("report", "审看报告", "report", required=False),
    ],
    outputs=[Port("timeline", "成片时间轴", "timeline"), Port("text", "放映摘要", "text")],
    params=[Param("label", "版本名", "text", "成片 V1"), Param("note", "备注", "text", "")],
    icon="🎪",
    tags=["出口", "成片"],
)
def screening(ctx: Any) -> dict[str, Any]:
    timeline = ctx.require("timeline")
    if not isinstance(timeline, dict) or not timeline.get("shots"):
        raise NodeError("放映室需要一条时间轴：请把剪辑师 / 调色 / 字幕的产物接进来")
    audio = common.items_of(ctx.in_("audio"))
    bundle = ctx.in_("subtitles") if isinstance(ctx.in_("subtitles"), dict) else {}
    cues = common.cues_of(bundle)
    report = ctx.in_("report")
    missing = [
        str(shot.get("id") or shot.get("index"))
        for shot in timeline["shots"]
        if not (shot.get("image_ref") or shot.get("poster_ref") or shot.get("clip_url"))
    ]
    duration = float(timeline.get("duration_s") or 0.0)
    summary = (
        f"{ctx.params.get('label') or '成片'}：{len(timeline['shots'])} 个镜头 / {duration:.1f}s / "
        f"{timeline.get('aspect', '9:16')} @{timeline.get('fps', 24)}fps；"
        f"音轨 {len(audio)} 条，字幕 {len(cues)} 条"
        + (f"，缺画面镜头 {len(missing)} 个" if missing else "，画面齐全")
        + (f"；审片分 {report.get('score')}" if isinstance(report, dict) and report.get("score") is not None else "")
    )
    if missing:
        ctx.log(f"有 {len(missing)} 个镜头没有画面素材，放映时回落为色块底", missing=missing[:8], level="warn")
    timeline = {**timeline, "label": str(ctx.params.get("label") or "成片"), "note": str(ctx.params.get("note") or "")}
    # 放映用的成片时间轴要自带字幕轨：播放器只吃 timeline.subtitles.cues，字幕轨留在旁路上等于没上映
    if cues:
        timeline["subtitles"] = {
            "cues": cues,
            "count": len(cues),
            "style": bundle.get("style") or {},
            "total_s": bundle.get("total_s"),
            "coverage": bundle.get("coverage"),
        }
    else:
        ctx.log("没有接到字幕轨，放映时不叠字幕（字幕师的交付物没连进来）", level="warn")
    ctx.log(summary)
    return {"timeline": timeline, "text": summary}
