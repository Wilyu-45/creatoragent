"""岗位节点共用的取数、规范化与提示词拼装。

所有岗位读写的就是这几个 payload 形状（``shots`` / ``screenplay`` / ``images``
/ ``audio`` / ``timeline`` …），这里集中做「宽容入口 + 规范出口」：上游少给
字段时补默认值，绝不在节点里散落一堆 ``dict.get`` 兜底。
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

from ...knowledge.visual import VISUAL_STYLES, VisualStyle
from .. import craft, media
from ..spec import NodeError

DEFAULT_LOOK: dict[str, Any] = {
    "style_id": "documentary",
    "style_label": "真实场景纪实",
    "palette": ["#101a36", "#1a2c5e", "#0a0f1e"],
    "accent": "#22d3ee",
    "grain": 0.06,
    "vignette": 0.35,
    "mood": "neutral",
    "aspect": "9:16",
    "grade": {"temp": 5600, "contrast": 1.0, "sat": 1.0, "filter": "none"},
    "references": [],
    "notes": [],
}

_BEAT_ENERGY = {
    "hook": 0.95,
    "setup": 0.45,
    "develop": 0.6,
    "tension": 0.85,
    "payoff": 0.9,
    "cta": 0.8,
    "ending": 0.5,
    "chapter": 0.4,
}


def slug(text: str, fallback: str = "item") -> str:
    """中文名会被剥成空串，补一段稳定哈希，免得不同角色的产物文件撞成同一个路径。"""
    clean = re.sub(r"[^0-9A-Za-z]+", "_", (text or "").strip()).strip("_").lower()
    if clean:
        return clean[:40]
    return f"{fallback}-{hashlib.blake2b((text or '').encode('utf-8'), digest_size=3).hexdigest()}"


def energy_of(beat: str) -> float:
    return float(_BEAT_ENERGY.get(beat or "", 0.55))


def mood_of(beat: str, tone: str = "") -> str:
    text = f"{beat} {tone}"
    if any(key in text for key in ("tension", "conflict", "悬念", "冲突", "危机")):
        return "tense"
    if any(key in text for key in ("payoff", "cta", "转化", "高潮")):
        return "punch"
    if any(key in text for key in ("warm", "温暖", "治愈", "回忆")):
        return "warm"
    if any(key in text for key in ("hook", "钩子")):
        return "punch"
    if any(key in text for key in ("松弛", "氛围", "chill")):
        return "cool"
    return "neutral"


def norm_brief(value: Any, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """把项目输入规范化：节点参数覆盖上游 brief 的同名字段。"""
    base: dict[str, Any] = {
        "title": "",
        "genre": "剧情",
        "type": "短视频",
        "duration_s": 45.0,
        "aspect": "9:16",
        "platform": "抖音",
        "audience": "",
        "tone": "轻松、真实",
        "language": "zh",
        "budget": 0.0,
        "synopsis": "",
        "keywords": [],
        "brand": "",
        "product": "",
        "goal": "曝光",
    }
    if isinstance(value, dict):
        base.update({key: item for key, item in value.items() if item not in (None, "", [])})
    for key, item in (params or {}).items():
        if item in (None, "", []) or key.startswith("_"):
            continue
        base[key] = item
    base["duration_s"] = float(base.get("duration_s") or 45.0)
    base["budget"] = float(base.get("budget") or 0.0)
    keywords = base.get("keywords") or []
    if isinstance(keywords, str):
        keywords = [item.strip() for item in re.split(r"[,，、;；/]+", keywords) if item.strip()]
    base["keywords"] = [str(item) for item in keywords]
    return base


def look_of(value: Any) -> dict[str, Any]:
    look = dict(DEFAULT_LOOK)
    if isinstance(value, dict):
        look.update({key: item for key, item in value.items() if item not in (None, "", [])})
    grade = dict(look.get("grade") or {})
    grade.setdefault("temp", 5600)
    grade.setdefault("contrast", 1.0)
    grade.setdefault("sat", 1.0)
    grade.setdefault("filter", "none")
    look["grade"] = grade
    if not look.get("palette"):
        look["palette"] = list(DEFAULT_LOOK["palette"])
    return look


def require_screenplay(value: Any, node_title: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not value.get("scenes"):
        raise NodeError(f"{node_title} 需要「剧本」交付物：请先接一个编剧（或剧本导入）节点")
    return value


def require_shots(value: Any, node_title: str) -> dict[str, Any]:
    if isinstance(value, dict) and value.get("shots"):
        return value
    if isinstance(value, list) and value:
        return ensure_timeline({"shots": value, "aspect": "9:16"})
    raise NodeError(f"{node_title} 需要「分镜表」交付物：请先接一个分镜师节点")


def shots_of(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        return [item for item in (value.get("shots") or []) if isinstance(item, dict)]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def ensure_timeline(payload: dict[str, Any]) -> dict[str, Any]:
    """重算每个镜头的 start_s 与总时长，保证分镜表自洽。"""
    shots = shots_of(payload)
    cursor = 0.0
    for index, shot in enumerate(shots):
        shot["index"] = index
        shot["start_s"] = round(cursor, 3)
        shot["duration_s"] = round(max(0.4, float(shot.get("duration_s") or 2.0)), 3)
        cursor += shot["duration_s"]
    payload["shots"] = shots
    payload["total_s"] = round(cursor, 3)
    payload["count"] = len(shots)
    return payload


def characters_of(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = value.get("items") or value.get("characters") or []
    if isinstance(value, list):
        return [item if isinstance(item, dict) else {"name": str(item)} for item in value]
    return []


def locations_of(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = value.get("items") or value.get("locations") or []
    if isinstance(value, list):
        return [item if isinstance(item, dict) else {"name": str(item)} for item in value]
    return []


def items_of(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = value.get("items") or []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def cues_of(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = value.get("cues") or []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def dialogue_lines(scene: dict[str, Any]) -> list[dict[str, str]]:
    rows = scene.get("dialogue") or []
    out: list[dict[str, str]] = []
    for row in rows:
        if isinstance(row, dict):
            line = str(row.get("line") or row.get("text") or "")
            if line:
                out.append({"character": str(row.get("character") or "旁白"), "line": line})
        elif isinstance(row, str) and row.strip():
            out.append({"character": "旁白", "line": row.strip()})
    return out


def scene_summary(screenplay: dict[str, Any], limit: int = 12) -> list[str]:
    rows: list[str] = []
    for scene in (screenplay.get("scenes") or [])[:limit]:
        title = str(scene.get("title") or scene.get("location") or f"场景 {scene.get('index', 0) + 1}")
        action = str(scene.get("action") or "")
        rows.append(f"{title}｜{action[:60]}")
    return rows


# ------------------------------------------------------------------ #
# 风格与提示词                                                          #
# ------------------------------------------------------------------ #


def style_by_key(key: str) -> VisualStyle:
    return next((item for item in VISUAL_STYLES if item.key == key), VISUAL_STYLES[0])


def style_options() -> list[str]:
    return [item.key for item in VISUAL_STYLES]


def look_from_style(key: str, mood: str = "", aspect: str = "9:16") -> dict[str, Any]:
    style = style_by_key(key)
    look = dict(DEFAULT_LOOK)
    look.update(
        {
            "style_id": style.key,
            "style_label": style.name,
            "palette": [item.hex for item in style.palette][:4] or list(DEFAULT_LOOK["palette"]),
            "accent": (style.palette[-1].hex if style.palette else DEFAULT_LOOK["accent"]),
            "composition": style.composition,
            "lighting": style.lighting,
            "mood": mood or look.get("mood", "neutral"),
            "aspect": aspect,
            "prompt_fragments": list(style.prompt_fragments),
            "negative": style.negative,
            "references": [f"{style.name}：{style.composition}", f"布光：{style.lighting}"],
            "notes": [f"适用题材：{'、'.join(style.best_for)}"] if style.best_for else [],
        }
    )
    grade = craft.grade_for(look["mood"])
    look["grade"] = {"temp": grade["temp"], "contrast": grade["contrast"], "sat": grade["sat"], "filter": grade["filter"]}
    look["grain"] = 0.08 if grade["filter"] in {"vintage", "punch"} else 0.05
    look["vignette"] = 0.42 if mood in {"tense", "noir"} else 0.32
    return look


def shot_prompt(shot: dict[str, Any], look: dict[str, Any], characters: Iterable[dict[str, Any]] = ()) -> str:
    """把一镜的岗位参数拼成生成模型能吃的提示词（含风格与负面词）。"""
    style = style_by_key(str(look.get("style_id", "")))
    cast = " / ".join(str(item.get("name", "")) for item in characters if item.get("name"))[:120]
    size = craft.SHOT_SIZES.get(str(shot.get("size", "")), {})
    movement = craft.CAMERA_MOVEMENTS.get(str(shot.get("movement", "")), {})
    parts = [
        str(shot.get("description") or "").strip(),
        f"{size.get('label', '')} {size.get('lens', '')}mm lens" if size else "",
        movement.get("prompt", ""),
        f"scene: {shot.get('scene_title', '')}" if shot.get("scene_title") else "",
        f"cast: {cast}" if cast else "",
        f"mood: {shot.get('mood', look.get('mood', ''))}",
        style.composition,
        style.lighting,
        " ".join(style.prompt_fragments[:3]),
        f"aspect ratio {shot.get('aspect', look.get('aspect', '9:16'))}",
    ]
    text = ", ".join(part for part in parts if part)
    return text[:900]


def negative_prompt(look: dict[str, Any]) -> str:
    style = style_by_key(str(look.get("style_id", "")))
    return style.negative or "blurry, distorted anatomy, watermark"


# ------------------------------------------------------------------ #
# 真机 / 示意通道                                                      #
# ------------------------------------------------------------------ #


def image_artifact(ctx: Any, name: str, png: bytes, meta: dict[str, Any]) -> dict[str, Any]:
    saved = ctx.save_png(name, png)
    item = {
        key: value for key, value in meta.items()
        if key not in {"id", "kind", "shot_index", "character", "prompt", "provider", "simulated", "url", "ref", "w", "h"}
    }
    item.update(
        {
            "id": meta.get("id", slug(name)),
            "kind": meta.get("kind", "image"),
            "shot_index": meta.get("shot_index"),
            "character": meta.get("character", ""),
            "prompt": meta.get("prompt", ""),
            "w": saved.get("w", 0),
            "h": saved.get("h", 0),
            "ref": saved["ref"],
            "url": saved["url"],
            "provider": meta.get("provider", "sample"),
            "simulated": bool(meta.get("simulated", True)),
        }
    )
    return item


def generate_or_draw(ctx: Any, prompt: str, name: str, size: str, drawer: Any, meta: dict[str, Any]) -> dict[str, Any]:
    """优先走真机图片模型；未配置/失败则用离线画布出一帧示意图。"""
    result = media.generate_image(prompt, size=size, seed=int(meta.get("seed", 0) or 0))
    if result and result.get("bytes"):
        return image_artifact(
            ctx,
            name,
            result["bytes"],
            {**meta, "provider": result["provider"], "simulated": False, "url": result.get("url", "")},
        )
    png = drawer()
    return image_artifact(ctx, name, png, {**meta, "provider": "sample", "simulated": True})


def voice_bytes_or_sample(ctx: Any, text: str, preset: dict[str, Any], name: str) -> dict[str, Any]:
    from .. import audio as audio_lib

    blob = media.synthesize_voice(text, speed=float(preset.get("rate", 1.0)))
    if blob:
        saved = ctx.save_file(name, blob)
        return {
            "ref": saved["ref"],
            "url": saved["url"],
            "duration_s": round(max(0.6, craft.chinese_duration(text) * 0.95), 3),
            "provider": "elevenlabs",
            "simulated": False,
        }
    samples = audio_lib.voice(text, preset)
    saved = ctx.save_wav(name, audio_lib.wav_bytes(samples))
    return {
        "ref": saved["ref"],
        "url": saved["url"],
        "duration_s": audio_lib.seconds_of(samples),
        "provider": "sample",
        "simulated": True,
    }


def pick_locations(screenplay: dict[str, Any]) -> list[dict[str, Any]]:
    """从剧本里提取场景表（同名场景合并）。"""
    merged: dict[str, dict[str, Any]] = {}
    for scene in screenplay.get("scenes") or []:
        name = str(scene.get("location") or "未命名场景")
        row = merged.setdefault(
            name,
            {
                "id": slug(name, f"loc{len(merged)}"),
                "name": name,
                "time_of_day": str(scene.get("time_of_day") or "day"),
                "int_ext": str(scene.get("int_ext") or "INT"),
                "mood": str(scene.get("mood") or "neutral"),
                "characters": [],
                "scenes": [],
                "props": [],
            },
        )
        row["scenes"].append(str(scene.get("id") or scene.get("index")))
        for name_item in scene.get("characters") or []:
            if name_item not in row["characters"]:
                row["characters"].append(str(name_item))
        for prop in scene.get("props") or []:
            if prop not in row["props"]:
                row["props"].append(str(prop))
    return list(merged.values())


def pick_characters(screenplay: dict[str, Any]) -> list[dict[str, Any]]:
    """从剧本里提取角色表（出场次数 + 台词量）。"""
    merged: dict[str, dict[str, Any]] = {}
    for scene in screenplay.get("scenes") or []:
        for name in scene.get("characters") or []:
            key = str(name)
            row = merged.setdefault(
                key,
                {
                    "id": slug(key, f"ch{len(merged)}"),
                    "name": key,
                    "role_type": "",
                    "appearances": 0,
                    "lines": 0,
                    "scenes": [],
                },
            )
            row["appearances"] += 1
            row["scenes"].append(str(scene.get("id") or scene.get("index")))
        for line in dialogue_lines(scene):
            key = str(line.get("character") or "旁白")
            row = merged.setdefault(
                key,
                {"id": slug(key, f"ch{len(merged)}"), "name": key, "role_type": "narrator", "appearances": 0, "lines": 0, "scenes": []},
            )
            row["lines"] += 1
    rows = list(merged.values())
    rows.sort(key=lambda item: (-int(item["appearances"]) - int(item["lines"]), str(item["name"])))
    for index, row in enumerate(rows):
        if not row.get("role_type"):
            row["role_type"] = "protagonist" if index == 0 else ("lead" if index < 3 else "support")
        row["scenes"] = sorted(set(row["scenes"]))[:8]
    return rows


def dialogue_text(screenplay: dict[str, Any]) -> str:
    rows: list[str] = []
    for scene in screenplay.get("scenes") or []:
        for line in dialogue_lines(scene):
            rows.append(f"{line['character']}：{line['line']}")
    return "\n".join(rows)
