"""后期部门岗位：助理剪辑 / 剪辑 / 预告片剪辑 / 调色 / 字幕 / 视效 / 包装 / 封面 / 渲染导出。

这一层的出口是**前端播放器直接消费的时间轴契约** ``timeline``：镜头顺序、每镜
时长与起点、画面素材引用、转场、人声落位都在这里定稿。上游怎么写不重要，
剪辑师只认分镜表与素材——兼容性一律在 ``common`` 里收敛。

助理剪辑先把素材归条（剔废条、留替补、报缺口），剪辑师只在干净的素材单上剪；
预告片剪辑则反过来吃**已成片的时间轴**，压缩重剪并重新做卡点音效。

画面级岗位（调色、视效）真的读回前一帧 PNG、按信号链处理、再落盘，
并在报告里给出处理前后的实测数据；不写「已调色」这种无法验证的结论。
"""

from __future__ import annotations

import json
import math
from typing import Any

from .. import audio as audio_lib, canvas, craft
from ..spec import NodeError, Param, Port, node
from . import common

# ------------------------------------------------------------------ #
# 剪辑版本口径                                                          #
# ------------------------------------------------------------------ #

#: 版本口径 = 时长预算 + 单镜上限 + 挑镜策略。预告要钩子，正片要完整，切片要人声。
_VERSIONS: dict[str, dict[str, Any]] = {
    "正片": {"budget": 0.0, "max_shot_s": 8.0, "order": "story", "need_speech": False},
    "15s 预告": {"budget": 15.0, "max_shot_s": 3.2, "order": "hook", "need_speech": False},
    "口播切片": {"budget": 30.0, "max_shot_s": 8.0, "order": "story", "need_speech": True},
    "精华速览": {"budget": 24.0, "max_shot_s": 4.0, "order": "energy", "need_speech": False},
}

#: 预告与精华按节拍优先级挑镜：钩子与行动指令先上，铺垫后上
_BEAT_PRIORITY = {"hook": 0, "cta": 1, "payoff": 2, "tension": 3, "setup": 4, "develop": 5, "chapter": 6, "ending": 7}

_CLIP_RANK = {"done": 0, "pending": 1, "offline": 2, "failed": 3}

#: 封面底图优先级：现场剧照最像成片，其次是关键帧/生成帧，概念图与设定卡只作兜底
_COVER_BASE_RANK = {"stills": 0, "keyframe": 1, "frame": 1, "scene": 2, "concept": 2, "character_sheet": 3, "makeup_card": 3, "storyboard": 4, "cover": 5}


def _cover_base_rank(item: dict[str, Any]) -> int:
    """剧照师已标了封面候选，就优先用它；其余按画面类型排队。"""
    if str(item.get("kind") or "") == "stills" and str(item.get("use") or "") == "cover":
        return -1
    return _COVER_BASE_RANK.get(str(item.get("kind") or ""), 6)


def _cover_on_base(base: canvas.Raster, look: dict[str, Any], title_latin: str, aspect_w: int, aspect_h: int, note: str) -> bytes:
    """把选中的画面铺满封面版式，再压标题带、边框与角标——真人封面设计师就是这么排版底图的。"""
    grade = (look or {}).get("grade") or {}
    image = base.fit(aspect_w, aspect_h)
    image.grade(
        contrast=float((look or {}).get("contrast", 1.12)),
        saturation=float((look or {}).get("sat", 1.16)),
        temp_k=float(grade.get("temp", 5400)),
        exposure=0.02,
    )
    image.fill_rect(0, int(aspect_h * 0.80), aspect_w, int(aspect_h * 0.2), (8, 9, 13), 0.62)
    image.stroke_rect(16, 16, aspect_w - 32, aspect_h - 32, (222, 230, 246), 2)
    image.text(28, 30, title_latin, (240, 244, 252), 3 if aspect_w >= 420 else 2)
    if note:
        image.text(28, aspect_h - 52, note, (200, 212, 232), 1)
    image.vignette(0.34)
    image.grain(0.06)
    return image.to_png()


def _shot_speech(shot: dict[str, Any]) -> float:
    direct = float(shot.get("speech_s") or 0.0)
    if direct > 0:
        return round(direct, 3)
    return round(
        sum(
            craft.speech_seconds(str(row.get("line") or ""))
            for row in (shot.get("dialogue") or [])
            if isinstance(row, dict)
        ),
        3,
    )


def _speech_floor(shot: dict[str, Any]) -> float:
    """这一镜最短要留几秒才说得完台词——与分镜师、配音导演同一口径。"""
    speech = _shot_speech(shot)
    return round((craft.HEAD_GAP_S + speech) / craft.SPEECH_SHARE, 3) if speech > 0 else 0.8


def _tc(seconds: float, fps: float) -> str:
    """秒 → SMPTE 时码（EDL 与交付清单都用它）。"""
    frame_rate = max(1, int(round(fps)))
    frames = max(0, int(round(float(seconds) * frame_rate)))
    whole = frames // frame_rate
    return f"{whole // 3600:02d}:{whole // 60 % 60:02d}:{whole % 60:02d}:{frames % frame_rate:02d}"


def _srt_time(seconds: float) -> str:
    total = max(0.0, float(seconds))
    whole = int(total)
    millis = int(round((total - whole) * 1000))
    if millis >= 1000:
        millis -= 1000
        whole += 1
    return f"{whole // 3600:02d}:{whole // 60 % 60:02d}:{whole % 60:02d},{millis:03d}"


def _rescale(image: canvas.Raster, width: int) -> canvas.Raster:
    """最近邻缩小。只在岗位明确要求降采样时才付出这个代价。"""
    if width <= 0 or width >= image.w:
        return image
    height = max(2, int(round(image.h * width / image.w)))
    out = canvas.Raster(width, height, (0, 0, 0))
    buf = image.buf
    sx, sy = image.w / width, image.h / height
    for y in range(height):
        src_y = min(image.h - 1, int(y * sy)) * image.w
        for x in range(width):
            index = (src_y + min(image.w - 1, int(x * sx))) * 3
            out.px(x, y, (buf[index], buf[index + 1], buf[index + 2]))
    return out


def _luma_stats(image: canvas.Raster) -> dict[str, float]:
    """实测亮度：均值 + 64 档直方图分位 + 削波比例。调色前后的差别要靠它说话。"""
    buf = image.buf
    count = len(buf) // 3
    if count == 0:
        return {"mean": 0.0, "p05": 0.0, "p95": 0.0, "clip_low": 1.0, "clip_high": 0.0}
    hist = [0] * 64
    total = 0.0
    low = 0
    high = 0
    for index in range(0, count * 3, 3):
        luma = 0.2126 * buf[index] + 0.7152 * buf[index + 1] + 0.0722 * buf[index + 2]
        total += luma
        hist[min(63, int(luma // 4))] += 1
        if luma < 3.0:
            low += 1
        elif luma > 252.0:
            high += 1

    def pct(ratio: float) -> float:
        target = max(1, int(count * ratio))
        acc = 0
        for slot, value in enumerate(hist):
            acc += value
            if acc >= target:
                return round((slot + 0.5) * 4.0, 1)
        return 255.0

    return {
        "mean": round(total / count, 1),
        "p05": round(pct(0.05), 1),
        "p50": round(pct(0.5), 1),
        "p95": round(pct(0.95), 1),
        "clip_low": round(low / count, 4),
        "clip_high": round(high / count, 4),
    }


def _pick_clip(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    """同一镜有多条素材时：真机成品 > 远端待查 > 示意帧。"""
    if not candidates:
        return None
    return sorted(candidates, key=lambda item: (_CLIP_RANK.get(str(item.get("status") or "offline"), 2), -len(item.get("frames") or [])))[0]


def _index_frames(items: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], dict[int, list[dict[str, Any]]]]:
    by_id: dict[str, list[dict[str, Any]]] = {}
    by_index: dict[int, list[dict[str, Any]]] = {}
    for item in items:
        by_id.setdefault(str(item.get("shot_id") or ""), []).append(item)
        raw = item.get("shot_index")
        if raw is not None:
            by_index.setdefault(int(raw), []).append(item)
    return by_id, by_index


# ------------------------------------------------------------------ #
# 剪辑师                                                               #
# ------------------------------------------------------------------ #


@node(
    "editor",
    title="剪辑师",
    dept="post",
    role="把分镜表与生成素材剪成一条时间轴：定版本口径、挑镜头、定时长与转场，再把人声逐句挂回新时间轴并核对声画对位",
    inputs=[
        Port("shots", "分镜表", "shots"),
        Port("clips", "视频片段", "clips", required=False, many=True),
        Port("images", "关键帧画面", "images", required=False, many=True),
        Port("audio", "音频轨", "audio", required=False, many=True),
        Port("brief", "项目需求", "brief", required=False),
    ],
    outputs=[Port("timeline", "时间轴", "timeline"), Port("text", "剪辑说明", "text")],
    params=[
        Param("version", "版本口径", "select", "正片", list(_VERSIONS), hint="预告与切片会重挑镜头、压短时长"),
        Param("target_s", "目标时长", "number", 0, minimum=0, maximum=600, step=1, unit="秒", hint="0=按版本默认（正片取 brief 时长）"),
        Param("max_shots", "最多镜头", "number", 40, minimum=1, maximum=120, step=1, unit="镜"),
        Param("max_shot_s", "单镜上限", "number", 0, minimum=0, maximum=30, step=0.1, unit="秒", hint="0=按版本口径；台词装不下时仍会给足"),
        Param("transition", "转场", "select", "按分镜", ["按分镜", "硬切", "交叉溶解", "匹配剪辑", "缩放转场", "擦除"]),
        Param("beat_cut", "卡点", "toggle", True, hint="接了配乐轨时把切点吸到最近的拍点"),
        Param("beat_tol", "卡点容差", "range", 0.3, minimum=0.05, maximum=0.5, step=0.05, hint="允许偏离的比例（0.3=三分之一拍），按当前 BPM 的拍长换算"),
        Param("j_cut", "J-cut 提前量", "range", 0.2, minimum=0, maximum=0.6, step=0.05, unit="秒", hint="下一镜人声允许比画面早进多少"),
        Param("label", "版本名", "text", ""),
        Param("note", "备注", "text", ""),
    ],
    icon="✂️",
    tags=["时间轴", "EDL", "声画对位"],
)
def editor(ctx: Any) -> dict[str, Any]:
    board = common.require_shots(ctx.require("shots"), ctx.title)
    brief = common.norm_brief(ctx.in_("brief"), None)
    version = str(ctx.params.get("version") or "正片")
    preset = _VERSIONS.get(version, _VERSIONS["正片"])
    aspect = str(board.get("aspect") or brief.get("aspect") or "9:16")
    fps = int(float(ctx.params.get("fps") or board.get("fps") or 24)) or 24
    target = float(ctx.params.get("target_s") or 0.0) or float(preset["budget"]) or float(brief.get("duration_s") or 0.0)
    max_shots = int(float(ctx.params.get("max_shots") or 40))
    max_shot_s = float(ctx.params.get("max_shot_s") or 0.0) or float(preset["max_shot_s"])

    clips: list[dict[str, Any]] = []
    for bundle in ctx.many("clips"):
        clips += common.items_of(bundle)
    frames: list[dict[str, Any]] = []
    for bundle in ctx.many("images"):
        frames += common.items_of(bundle)
    voice_in: list[dict[str, Any]] = []
    music_in: list[dict[str, Any]] = []
    masters: list[dict[str, Any]] = []
    bundle_kinds: list[str] = []
    for bundle in ctx.many("audio"):
        if not isinstance(bundle, dict):
            continue
        items = common.items_of(bundle)
        bundle_kinds.append(str(bundle.get("kind") or "audio"))
        voice_in += [item for item in items if str(item.get("kind") or "voice") == "voice" and str(item.get("text") or "").strip()]
        music_in += [item for item in items if item.get("bpm")]
        # 混音师给的成片音轨是一条打包好的总轨，播放器直接吃它；分轨包没有总轨
        if len(items) == 1 and str(bundle.get("kind") or "") in ("master", "audio"):
            masters.append({**items[0], "kind": str(bundle.get("kind") or "master"), "total_s": round(float(bundle.get("total_s") or 0.0), 3)})
    frame_by_id, frame_by_index = _index_frames(frames)
    clip_by_id: dict[str, list[dict[str, Any]]] = {}
    for clip in clips:
        clip_by_id.setdefault(str(clip.get("shot_id") or ""), []).append(clip)

    # ---- 挑镜：先按版本口径筛掉剪不进去的，再按策略排队吃预算 ---- #
    entries: list[dict[str, Any]] = []
    dropped: list[tuple[str, str]] = []
    for shot in common.shots_of(board):
        row = dict(shot)
        floor = _speech_floor(row)
        speech = _shot_speech(row)
        if preset["need_speech"] and speech <= 0:
            dropped.append((str(row.get("slate") or row.get("id")), "无口播"))
            continue
        if floor > max_shot_s + 0.01:
            dropped.append((str(row.get("slate") or row.get("id")), f"台词要 {floor:.1f}s > 单镜上限 {max_shot_s:.1f}s"))
            continue
        raw = float(row.get("duration_s") or 2.0)
        entries.append({"shot": row, "floor": floor, "speech": speech, "duration": round(max(floor, min(raw, max_shot_s)), 3)})

    order = str(preset["order"])
    if order == "hook":
        entries.sort(key=lambda item: (_BEAT_PRIORITY.get(str(item["shot"].get("beat") or ""), 8), int(item["shot"].get("index") or 0)))
    elif order == "energy":
        entries.sort(key=lambda item: (-float(item["shot"].get("energy") or 0.5), int(item["shot"].get("index") or 0)))
    chosen = entries[:max_shots]
    if target > 0:
        kept: list[dict[str, Any]] = []
        spent = 0.0
        for item in chosen:
            if spent + item["duration"] > target and kept:
                dropped.append((str(item["shot"].get("slate") or item["shot"].get("id")), "超出版本预算"))
                continue
            kept.append(item)
            spent += item["duration"]
        chosen = kept
    chosen.sort(key=lambda item: int(item["shot"].get("index") or 0))  # 挑镜按策略，成片按叙事顺序
    if not chosen:
        raise NodeError(f"剪辑师：{version} 口径下一镜不剩，请放宽目标时长或单镜上限")

    # ---- 人声挂回：按文本找回配音轨，落在新时间轴上，不重叠、不出画 ---- #
    pool: dict[str, list[dict[str, Any]]] = {}
    for item in voice_in:
        pool.setdefault(str(item.get("text") or "").strip(), []).append(item)
    head_gap = craft.HEAD_GAP_S
    j_cut = float(ctx.params.get("j_cut") or 0.2)
    beat_cut = bool(ctx.params.get("beat_cut", True))
    beat_tol = float(ctx.params.get("beat_tol") or 0.3)
    grid = [item for item in music_in if item.get("bpm")]
    cursor = 0.0
    free_at = 0.0
    shot_rows: list[dict[str, Any]] = []
    voice_rows: list[dict[str, Any]] = []
    for position, item in enumerate(chosen):
        shot = item["shot"]
        start = cursor
        if position and beat_cut and grid:
            # 取正在播的那段配乐（offset 最大且不超过切点的那段），不是第一段
            bar = max((row for row in grid if float(row.get("offset") or 0.0) <= start + 1e-6), key=lambda row: float(row.get("offset") or 0.0), default=grid[0])
            beat = 60.0 / max(1.0, float(bar.get("bpm") or 100))
            origin = float(bar.get("offset") or 0.0)
            snapped = origin + round((start - origin) / beat) * beat
            delta = snapped - start
            prev = shot_rows[-1]
            room = float(prev["duration_s"]) + delta  # 只改上一镜的时长：切点挪到拍点，上一镜起点不动，人声才不会错位
            tol = max(0.04, beat_tol * beat)
            # 压缩的底线是"上一镜最后一句仍在自己画内"；口播下限只留一点余量，因为实测句长比口径更准
            if abs(delta) <= tol and room >= 0.4 and room >= float(prev["floor_s"]) - 0.12 and float(prev["start_s"]) + room >= free_at:
                prev["duration_s"] = round(room, 3)
                start = round(start + delta, 3)
                item["snapped"] = round(delta, 3)
        slate = str(shot.get("slate") or shot.get("id") or f"shot-{position + 1}")
        line_cursor = max(head_gap, free_at - start - j_cut)
        for line in (shot.get("dialogue") or []):
            text = str(line.get("line") or "").strip() if isinstance(line, dict) else str(line or "").strip()
            if not text:
                continue
            track = pool.setdefault(text, [])
            source = track.pop(0) if track else None
            est = float((source or {}).get("duration_s") or 0.0) or craft.speech_seconds(text)
            offset = round(start + max(line_cursor, free_at - start - j_cut), 3)
            end = offset + est
            if end > start + item["duration"]:  # 台词比镜头长：宁可加长时间，也不剪掉人声
                item["duration"] = round(item["duration"] + (end - start - item["duration"]) + 0.1, 3)
            free_at = round(end + 0.08, 3)
            line_cursor = end - start + 0.12
            voice_rows.append(
                {
                    "id": str((source or {}).get("id") or f"voice-{len(voice_rows) + 1}"),
                    "kind": "voice",
                    "character": (str(line.get("character") or "") if isinstance(line, dict) else "") or str((source or {}).get("character") or ""),
                    "text": text,
                    "offset": offset,
                    "duration_s": round(est, 3),
                    "gain": float((source or {}).get("gain") or 0.9),
                    "shot_id": str(shot.get("id") or ""),
                    "slate": slate,
                    "ref": str((source or {}).get("ref") or ""),
                    "url": str((source or {}).get("url") or ""),
                    "provider": str((source or {}).get("provider") or "none"),
                    "simulated": bool((source or {}).get("simulated", True)),
                }
            )
        clip = _pick_clip(clip_by_id.get(str(shot.get("id"))) or [])
        candidates = frame_by_id.get(str(shot.get("id"))) or frame_by_index.get(int(shot.get("index") if shot.get("index") is not None else position), []) or []
        still_item = sorted(candidates, key=lambda entry: str(entry.get("kind") or ""))[0] if candidates else None
        shot.update(
            {
                "slate": slate,
                "index": position,
                "start_s": round(start, 3),
                "duration_s": round(item["duration"], 3),
                "floor_s": item["floor"],
                "speech_s": item["speech"],
                "clip_ref": str((clip or {}).get("clip_ref") or ""),
                "clip_url": str((clip or {}).get("clip_url") or ""),
                "frames": (clip or {}).get("frames") or [],
                "poster_ref": str((clip or {}).get("poster_ref") or (still_item or {}).get("ref") or ""),
                "poster_url": str((clip or {}).get("poster_url") or (still_item or {}).get("url") or ""),
                "image_ref": str((still_item or {}).get("ref") or (clip or {}).get("poster_ref") or ""),
                "image_url": str((still_item or {}).get("url") or (clip or {}).get("poster_url") or ""),
                "source": "clip" if clip else ("frame" if still_item else "none"),
                "source_status": str((clip or {}).get("status") or ""),
                "source_provider": str((clip or {}).get("provider") or (still_item or {}).get("provider") or ""),
                "source_simulated": bool((clip or {}).get("simulated", (still_item or {}).get("simulated", True))),
                "lines": len(shot.get("dialogue") or []),
                "beat_snap": item.get("snapped", 0.0),
            }
        )
        shot_rows.append(shot)
        cursor = round(start + float(shot["duration_s"]), 3)
    rows = shot_rows

    # ---- 转场：按分镜师的写法或统一口径，逐刀记录类型与成本 ---- #
    override = str(ctx.params.get("transition") or "按分镜")
    override_key = next((key for key, value in craft.TRANSITIONS.items() if value.get("label") == override), "")
    cuts: list[dict[str, Any]] = []
    for index in range(1, len(rows)):
        key = rows[index].get("transition") if override == "按分镜" else override_key
        key = str(key or "cut")
        if key not in craft.TRANSITIONS:
            key = "cut"
        cuts.append(
            {
                "at_s": rows[index]["start_s"],
                "from": rows[index - 1]["slate"],
                "to": rows[index]["slate"],
                "kind": key,
                "label": craft.TRANSITIONS[key]["label"],
                "cost_s": float(craft.TRANSITIONS[key]["cost"]),
                "across_scene": str(rows[index - 1].get("scene_id")) != str(rows[index].get("scene_id")),
            }
        )

    total = round(sum(float(row["duration_s"]) for row in rows), 3)
    ends = {str(row.get("id")): float(row["start_s"]) + float(row["duration_s"]) for row in rows}
    overflow = [row["slate"] for row in voice_rows if float(row["offset"]) + float(row["duration_s"]) > ends.get(row["shot_id"], 1e9) + 0.02]
    overlaps = [b["id"] for a, b in zip(voice_rows, voice_rows[1:]) if float(b["offset"]) < float(a["offset"]) + float(a["duration_s"]) - 0.001]
    edl = [
        {
            "reel": f"{index + 1:03d}",
            "slate": row["slate"],
            "src_in": _tc(0.0, fps),
            "src_out": _tc(float(row["duration_s"]), fps),
            "rec_in": _tc(float(row["start_s"]), fps),
            "rec_out": _tc(float(row["start_s"]) + float(row["duration_s"]), fps),
            "transition": (cuts[index - 1]["kind"].upper() if index and index - 1 < len(cuts) else "CUT"),
            "transition_label": (cuts[index - 1]["label"] if index and index - 1 < len(cuts) else "硬切"),
            "source": row["source"] or "none",
        }
        for index, row in enumerate(rows)
    ]
    orphan = [text for text, left in pool.items() if left and text]
    no_visual = [row["slate"] for row in rows if row["source"] == "none"]
    real_src = [row for row in rows if row["source"] == "clip" and not row["source_simulated"]]
    snapping = [row["slate"] for row in rows if row.get("beat_snap")]
    timeline = {
        "kind": "timeline",
        "version": version,
        "label": str(ctx.params.get("label") or f"{brief.get('title') or '成片'}｜{version}"),
        "note": str(ctx.params.get("note") or ""),
        "aspect": aspect,
        "fps": fps,
        "shots": rows,
        "count": len(rows),
        "duration_s": total,
        "target_s": round(target, 2),
        "voice": {"kind": "voice", "items": voice_rows, "count": len(voice_rows), "total_s": total, "provider": "conform", "simulated": True},
        "masters": masters,
        "audio_kinds": sorted(set(bundle_kinds)),
        "cuts": len(cuts),
        "transitions": cuts,
        "edl": edl,
        "missing_visual": no_visual,
        "dropped": dropped,
        "orphan_lines": orphan[:12],
        "simulated": not real_src,
        "provider": "canvas",
    }
    master_note = (
        f"总轨：{len(masters)} 条混音音轨随时间轴下发（{masters[0]['total_s']:.2f}s）"
        + (f"；⚠ 与成片时长差 {total - float(masters[0]['total_s']):+.2f}s，画面锁定后需回混音师重混" if abs(total - float(masters[0]["total_s"])) > 0.1 else "，时长对齐")
    ) if masters else "⚠ 没有混音总轨：播放器只有逐句人声，配乐与音效不会响"
    lines = [
        f"剪辑（{version}）：{len(rows)} 镜 / {total:.2f}s（目标 {target:.0f}s，偏差 {total - target:+.2f}s）｜{aspect} @{fps}fps",
        f"素材：真机 {len(real_src)} 镜 / 示意 {len(rows) - len(real_src) - len(no_visual)} 镜 / 无画面 {len(no_visual)} 镜",
        f"人声：{len(voice_rows)} 句挂回新时间轴，句间不重叠、不出画；最短镜头 {min(float(row['duration_s']) for row in rows):.2f}s",
        master_note,
        f"转场 {len(cuts)} 刀：{'、'.join(sorted({str(item['label']) for item in cuts})) or '无'}，手工量 {sum(float(item['cost_s']) for item in cuts):.1f}s",
        f"卡点：{len(snapping)}/{len(cuts)} 刀吸到拍点（{len(grid)} 段配乐给出 BPM，容差 {beat_tol:.0%} 拍，最大偏移 {max((abs(float(row['beat_snap'])) for row in rows), default=0.0):.3f}s）" if grid else "卡点：没有配乐轨，按叙事顺序切",
        f"弃用 {len(dropped)} 镜：{'；'.join(f'{slate}（{why}）' for slate, why in dropped[:6])}" if dropped else "分镜全部进片",
        f"⚠ {len(orphan)} 句配音在成片里找不到归属镜头：{'、'.join(item[:14] for item in orphan[:5])}——多半是对应的镜头被剪掉了。" if orphan else "",
        f"⚠ {len(no_visual)} 镜没有任何画面素材：{'、'.join(no_visual[:6])}。" if no_visual else "",
        f"⚠ {len(overflow)} 句台词说到镜头外、{len(overlaps)} 句相互重叠：需回分镜或配音环节修正。" if (overflow or overlaps) else "",
        "时间轴契约：shots[{start_s,duration_s,clip_url,frames,poster_ref,image_ref}] + voice + masters + transitions + edl，播放器与导出直接消费。",
    ]
    text = "\n".join(line for line in lines if line)
    ctx.log(f"剪辑完成：{len(rows)} 镜 / {total}s，弃用 {len(dropped)} 镜，挂回 {len(voice_rows)} 句人声")
    return {"timeline": timeline, "text": text}


# ------------------------------------------------------------------ #
# 助理剪辑                                                             #
# ------------------------------------------------------------------ #


@node(
    "assistant_editor",
    title="助理剪辑",
    dept="post",
    role="素材归条：把片段与关键帧按镜头归档、留出替补条、剔掉废条与无主素材，逐镜核对素材够不够长，给剪辑师一份能直接上轨的素材单",
    inputs=[
        Port("shots", "分镜表", "shots"),
        Port("clips", "生成片段", "clips", required=False, many=True),
        Port("images", "关键帧画面", "images", required=False, many=True),
        Port("data", "DIT 数据表", "plan", required=False),
    ],
    outputs=[
        Port("clips", "归条片段", "clips"),
        Port("images", "可用画面", "images"),
        Port("plan", "素材归条表", "plan"),
        Port("text", "素材日报", "text"),
    ],
    params=[
        Param("keep", "每镜留条数", "number", 2, minimum=1, maximum=6, step=1, unit="条", hint="第一条给剪辑师，其余留作替补"),
        Param("short_tol", "素材短缺红线", "number", 0.9, minimum=0.3, maximum=1.5, step=0.05, unit="倍", hint="片段时长 ÷ 镜头时长 低于此值即标为不够长"),
        Param("drop_failed", "剔除失败条", "toggle", True, hint="远端出片失败的片段不进剪辑台"),
        Param("backup_frames", "每镜留帧数", "number", 8, minimum=1, maximum=24, step=1, unit="帧"),
        Param("show", "列出镜头", "number", 12, minimum=1, maximum=40, step=1, unit="个"),
    ],
    icon="🗂️",
    tags=["后期", "素材", "归条"],
)
def assistant_editor(ctx: Any) -> dict[str, Any]:
    board = common.require_shots(ctx.in_("shots"), "助理剪辑")
    shots = common.shots_of(board)
    by_key: dict[str, dict[str, Any]] = {}
    for shot in shots:
        for key in (str(shot.get("id") or ""), str(shot.get("slate") or "")):
            if key:
                by_key.setdefault(key, shot)
    clips_in: list[dict[str, Any]] = []
    for bundle in ctx.many("clips"):
        clips_in += common.items_of(bundle)
    frames_in: list[dict[str, Any]] = []
    for bundle in ctx.many("images"):
        frames_in += common.items_of(bundle)
    data = ctx.in_("data") if isinstance(ctx.in_("data"), dict) else {}
    bad_refs = {str(ref) for ref in (data.get("bad_refs") or [])}
    keep_n = int(float(ctx.params.get("keep") or 2))
    short_tol = float(ctx.params.get("short_tol") or 0.9)
    drop_failed = bool(ctx.params.get("drop_failed"))
    frame_cap = int(float(ctx.params.get("backup_frames") or 8))
    show = int(float(ctx.params.get("show") or 12))

    grouped: dict[str, list[dict[str, Any]]] = {}
    frames_by_key: dict[str, list[dict[str, Any]]] = {}
    orphans: list[str] = []
    rejected: list[tuple[str, str]] = []
    for clip in clips_in:
        shot = by_key.get(str(clip.get("shot_id") or "")) or by_key.get(str(clip.get("slate") or ""))
        if shot is None:
            orphans.append(str(clip.get("slate") or clip.get("id") or "未标注镜头"))
            continue
        slate = str(shot.get("slate") or shot.get("id"))
        key = str(shot.get("id") or slate)
        if drop_failed and str(clip.get("status") or "") == "failed":
            rejected.append((slate, "远端出片失败"))
            continue
        broken = [str(ref) for ref in ([str(clip.get("clip_ref") or "")] + [str(row.get("ref") or "") for row in (clip.get("frames") or [])]) if ref in bad_refs]
        if broken:
            rejected.append((slate, f"DIT 判异常 {broken[0]}"))
            continue
        grouped.setdefault(key, []).append(clip)
    for item in frames_in:
        shot = by_key.get(str(item.get("shot_id") or ""))
        if shot is None and item.get("shot_index") is not None:
            shot = next((row for row in shots if int(row.get("index") or -1) == int(item["shot_index"])), None)
        if shot is None:
            orphans.append(str(item.get("id") or "未标注画面"))
            continue
        if str(item.get("ref") or "") in bad_refs:
            rejected.append((str(shot.get("slate") or shot.get("id")), f"DIT 判异常 {item.get('ref')}"))
            continue
        frames_by_key.setdefault(str(shot.get("id") or shot.get("slate")), []).append(item)

    rows: list[dict[str, Any]] = []
    kept_clips: list[dict[str, Any]] = []
    kept_frames: list[dict[str, Any]] = []
    gaps: list[str] = []
    short: list[str] = []
    for position, shot in enumerate(shots):
        key = str(shot.get("id") or shot.get("slate") or f"shot-{position + 1}")
        slate = str(shot.get("slate") or key)
        needed = round(float(shot.get("duration_s") or 2.0), 3)
        candidates = sorted(
            grouped.get(key) or [],
            key=lambda item: (_CLIP_RANK.get(str(item.get("status") or "offline"), 2), 0 if not item.get("simulated") else 1, -float(item.get("duration_s") or 0.0), -len(item.get("frames") or [])),
        )
        picks = candidates[:keep_n]
        for index, clip in enumerate(picks):
            clip["take"] = "primary" if index == 0 else f"backup-{index}"
            kept_clips.append(clip)
        for index, clip in enumerate(candidates[keep_n:], start=keep_n + 1):
            rejected.append((slate, f"第 {index} 条重复素材，留作库底"))
        frame_rows = (frames_by_key.get(key) or [])[:frame_cap]
        kept_frames += frame_rows
        coverage = round(max((float(clip.get("duration_s") or 0.0) for clip in picks), default=0.0), 3)
        ratio = round(coverage / needed, 3) if needed > 0 else 0.0
        if not picks:
            gaps.append(slate)
        elif ratio < short_tol:
            short.append(slate)
        rows.append(
            {
                "slate": slate,
                "index": position,
                "needed_s": needed,
                "takes": len(candidates),
                "kept": len(picks),
                "first_take": str((picks[0].get("status") or "offline") if picks else "无素材"),
                "simulated": bool(picks[0].get("simulated", True)) if picks else True,
                "clip_s": coverage,
                "ratio": ratio,
                "short": bool(picks) and ratio < short_tol,
                "frames": len(frame_rows),
                "prompt": str(shot.get("prompt") or "")[:80],
            }
        )

    real = [clip for clip in kept_clips if not clip.get("simulated")]
    total = round(sum(float(clip.get("duration_s") or 0.0) for clip in kept_clips), 2)
    clip_payload = {
        "kind": "clips",
        "items": kept_clips,
        "total_s": total,
        "fps": int(board.get("fps") or 24),
        "aspect": str(board.get("aspect") or "9:16"),
        "provider": str((real[0].get("provider") if real else "canvas")),
        "simulated": not real,
    }
    image_payload = {"kind": "images", "items": kept_frames, "provider": "canvas", "simulated": True}
    plan = {
        "dept": "post",
        "role": "助理剪辑",
        "items": rows,
        "shot_count": len(rows),
        "clips_in": len(clips_in),
        "clips_kept": len(kept_clips),
        "frames_in": len(frames_in),
        "frames_kept": len(kept_frames),
        "gaps": gaps[:20],
        "short": short[:20],
        "orphans": orphans[:20],
        "rejected": [{"slate": slate, "reason": why} for slate, why in rejected[:20]],
        "simulated": not real,
    }
    lines = [
        f"素材归条：{len(clips_in)} 段片段 → {len(kept_clips)} 段上轨（{len(real)} 段真机），{len(frames_in)} 张画面 → {len(kept_frames)} 张",
        f"{len(rows)} 个镜头：有素材 {len(rows) - len(gaps)} / 无素材 {len(gaps)} / 素材偏短 {len(short)}（红线 {short_tol} 倍）",
        f"每镜留 {keep_n} 条：主条 + 替补，剪辑师换条不用再回生成车间",
        *[f"· {row['slate']} 需 {row['needed_s']}s｜{row['kept']}/{row['takes']} 条｜最长 {row['clip_s']}s（{round(row['ratio'] * 100)}%）｜{row['frames']} 帧｜{row['first_take']}" for row in rows[:show]],
        f"⚠ {len(gaps)} 镜没有任何可用素材：{'、'.join(gaps[:6])}——要么补生成，要么在剪辑台删掉这一镜。" if gaps else "",
        f"⚠ {len(short)} 镜素材短于镜头时长：{'、'.join(short[:6])}——会冻帧或需要把镜头剪短。" if short else "",
        f"⚠ {len(orphans)} 段素材找不到对应镜头（多半是分镜改过号）：{'、'.join(orphans[:6])}。" if orphans else "",
        f"已剔除 {len(rejected)} 项：{'；'.join(f'{slate}（{why}）' for slate, why in rejected[:4])}" if rejected else "",
    ]
    text = "\n".join(line for line in lines if line)
    ctx.log(f"归条：{len(kept_clips)}/{len(clips_in)} 段上轨，缺口 {len(gaps)} 镜，废条 {len(rejected)}")
    return {"clips": clip_payload, "images": image_payload, "plan": plan, "text": text}


# ------------------------------------------------------------------ #
# 调色师                                                               #
# ------------------------------------------------------------------ #


@node(
    "colorist",
    title="调色师",
    dept="post",
    role="读回每镜画面实测亮度，先一级校色把曝光拉平，再按情绪做二级风格，逐镜给出调色前后的直方图数据",
    inputs=[Port("timeline", "时间轴", "timeline"), Port("look", "视觉基调", "look", required=False)],
    outputs=[Port("timeline", "调色后时间轴", "timeline"), Port("images", "调色帧", "images"), Port("text", "调色报告", "text")],
    params=[
        Param("style", "二级风格", "select", "按镜头情绪", ["按镜头情绪"] + [f"{value['label']}" for value in craft.MOOD_GRADE.values()], hint="按镜头情绪=逐镜套用分镜写下的 mood"),
        Param("strength", "风格强度", "range", 0.7, minimum=0, maximum=1, step=0.05, hint="0=只做曝光平衡，1=完整套用风格"),
        Param("target_luma", "目标中间调", "range", 0.46, minimum=0.25, maximum=0.75, step=0.01, hint="实测均值向它靠（一级校色）"),
        Param("lift", "黑场提升", "range", 0.0, minimum=0, maximum=0.15, step=0.01, hint="托起暗部，别把暗部压死"),
        Param("limit", "最多镜头", "number", 10, minimum=1, maximum=120, step=1, unit="镜"),
        Param("width", "输出宽度", "number", 0, minimum=0, maximum=720, step=20, unit="像素", hint="0=保持素材原尺寸"),
        Param("apply_look", "套用基调", "toggle", True, hint="再加一层美术指导的暗角与颗粒"),
        Param("write_back", "回写时间轴", "toggle", True, hint="把调色帧设为播放器首选画面"),
    ],
    icon="🎨",
    tags=["一级校色", "二级风格", "示波器"],
    needs_service=False,
)
def colorist(ctx: Any) -> dict[str, Any]:
    timeline = ctx.require("timeline")
    if not isinstance(timeline, dict) or not timeline.get("shots"):
        raise NodeError("调色师需要一条时间轴：请先接剪辑师")
    look = common.look_of(ctx.in_("look"))
    shots = [dict(item) for item in common.shots_of(timeline)]
    limit = int(float(ctx.params.get("limit") or 10))
    strength = float(ctx.params.get("strength") or 0.7)
    target_luma = float(ctx.params.get("target_luma") or 0.46) * 255.0
    lift = float(ctx.params.get("lift") or 0.0)
    width = int(float(ctx.params.get("width") or 0))
    style_label = str(ctx.params.get("style") or "按镜头情绪")
    fixed = next((value for value in craft.MOOD_GRADE.values() if value["label"] == style_label), None)

    items: list[dict[str, Any]] = []
    graded = 0
    skipped: list[str] = []
    means_before: list[float] = []
    means_after: list[float] = []
    for shot in shots:
        ref = str(shot.get("image_ref") or shot.get("poster_ref") or "")
        if graded >= limit or not ref:
            if not ref:
                skipped.append(str(shot.get("slate") or shot.get("id")))
            continue
        try:
            image = canvas.read_png(ctx.read_artifact(ref))
        except (NodeError, ValueError, IndexError) as error:
            skipped.append(f"{shot.get('slate')}（读不回：{str(error)[:24]}）")
            continue
        mood = str(shot.get("mood") or look.get("mood") or "neutral")
        grade_spec = fixed if fixed else craft.grade_for(mood)
        before = _luma_stats(image)
        exposure = 0.0
        if before["mean"] > 1:
            exposure = max(-0.9, min(0.9, math.log2(target_luma / before["mean"])))
        contrast = 1.0 + (float(grade_spec.get("contrast", 1.0)) - 1.0) * strength
        saturation = 1.0 + (float(grade_spec.get("sat", 1.0)) - 1.0) * strength
        temp = 5600.0 + (float(grade_spec.get("temp", 5600.0)) - 5600.0) * strength
        image.grade(contrast=contrast, saturation=saturation, temp_k=temp, exposure=exposure, lift=lift)
        if bool(ctx.params.get("apply_look", True)):
            image.vignette(float(look.get("vignette", 0.35)) * strength)
            image.grain(float(look.get("grain", 0.06)) * strength, seed=int(shot.get("index") or graded) + 3)
        out = _rescale(image, width)
        name = f"grade-{common.slug(str(shot.get('slate') or shot.get('id') or 'shot'))}.png"
        item = common.image_artifact(
            ctx,
            name,
            out.to_png(),
            {
                "id": f"grade-{graded + 1}",
                "kind": "graded",
                "shot_id": str(shot.get("id") or ""),
                "shot_index": shot.get("index"),
                "slate": str(shot.get("slate") or ""),
                "prompt": f"{mood} 风格：{grade_spec.get('label')}，曝光 {exposure:+.2f} 档，对比 {contrast:.2f}，饱和 {saturation:.2f}，{round(temp)}K",
                "provider": "canvas",
                "simulated": True,
                "stats_before": before,
                "stats_after": _luma_stats(out),
                "grade": {"mood": mood, "label": grade_spec.get("label"), "exposure_ev": round(exposure, 3), "contrast": round(contrast, 3), "saturation": round(saturation, 3), "temp_k": round(temp, 1), "lift": round(lift, 3), "strength": strength},
            },
        )
        items.append(item)
        means_before.append(before["mean"])
        means_after.append(item["stats_after"]["mean"])
        shot.update({"graded_ref": item["ref"], "graded_url": item["url"], "grade": item["grade"], "stats_before": before, "stats_after": item["stats_after"]})
        if bool(ctx.params.get("write_back", True)):
            shot["image_ref"], shot["image_url"] = item["ref"], item["url"]
        graded += 1
        ctx.progress(graded / max(1, min(limit, len(shots))), f"调色 {shot.get('slate')}")

    crushed = [item["slate"] for item in items if float(item["stats_after"]["clip_low"]) > 0.15]
    blown = [item["slate"] for item in items if float(item["stats_after"]["clip_high"]) > 0.05]
    payload = {"kind": "images", "items": items, "graded": graded, "provider": "canvas", "simulated": True}
    lines = [
        f"调色：{graded} 镜完成二级风格（{style_label}，强度 {strength:.2f}）",
        f"曝光平衡：均值 {sum(means_before) / len(means_before):.1f} → {sum(means_after) / len(means_after):.1f}（目标 {target_luma:.0f}/255）" if means_before else "没有可读回的画面帧",
        f"风格：{'、'.join(sorted({str(item['grade']['label']) for item in items}))}",
        f"示波器：暗部削波 {len(crushed)} 镜 / 高光削波 {len(blown)} 镜" if (crushed or blown) else "示波器：无死黑死白镜头（暗部削波 <15%，高光 <5%）",
        f"⚠ {len(skipped)} 镜没有可读回的画面，跳过低保真调色：{'、'.join(str(item) for item in skipped[:8])}" if skipped else "",
        "全流程纯标准库像素处理：读回 PNG → 一级曝光 → 二级风格 → 落盘，接真调色系统时替换同名产物 ref。",
    ]
    text = "\n".join(line for line in lines if line)
    ctx.log(f"调色：{graded} 镜，跳过 {len(skipped)} 镜")
    return {"timeline": {**timeline, "shots": shots, "graded": graded}, "images": payload, "text": text}


# ------------------------------------------------------------------ #
# 字幕师                                                               #
# ------------------------------------------------------------------ #


@node(
    "subtitle_master",
    title="字幕师",
    dept="post",
    role="按剪辑后的时间轴出字幕轨：断句到每行字数上限、按时长分配、逐条检查重叠与出画，并标出需要花字的关键词",
    inputs=[
        Port("timeline", "时间轴", "timeline", required=False),
        Port("audio", "音频轨", "audio", required=False, many=True),
        Port("shots", "分镜表", "shots", required=False),
        Port("brief", "项目需求", "brief", required=False),
    ],
    outputs=[Port("subtitles", "字幕轨", "subtitles"), Port("text", "字幕规范报告", "text")],
    params=[
        Param("max_chars", "每行字数", "number", 16, minimum=6, maximum=30, step=1, unit="字", hint="中文短视频口径：一行读完不返工"),
        Param("min_s", "单条最短时长", "number", 0.9, minimum=0.4, maximum=3, step=0.1, unit="秒"),
        Param("gap", "条间留白", "number", 0.06, minimum=0, maximum=0.4, step=0.02, unit="秒"),
        Param("speaker", "标注说话人", "toggle", False),
        Param("highlight", "关键词花字", "toggle", True, hint="品牌词、数字与行动指令标为花字，交给播放器强调"),
        Param("position", "字幕位置", "select", "底部安全区", ["底部安全区", "居中偏下", "顶部", "逐镜跟随"]),
        Param("limit", "最多条数", "number", 80, minimum=1, maximum=400, step=1, unit="条"),
    ],
    icon="💬",
    tags=["字幕规范", "断句", "花字"],
)
def subtitle_master(ctx: Any) -> dict[str, Any]:
    timeline = ctx.in_("timeline") if isinstance(ctx.in_("timeline"), dict) else {}
    brief = common.norm_brief(ctx.in_("brief"), None)
    rows: list[dict[str, Any]] = []
    for item in (timeline.get("voice") or {}).get("items") or []:
        rows.append(dict(item))
    if not rows:  # 没有剪辑时间轴时退回配音轨原始落位（此时字幕对齐的是配音而非成片）
        for bundle in ctx.many("audio"):
            for item in common.items_of(bundle):
                if str(item.get("kind") or "voice") == "voice" and item.get("text"):
                    rows.append({"text": str(item["text"]), "offset": float(item.get("offset") or 0), "duration_s": float(item.get("duration_s") or 0), "character": str(item.get("character") or "")})
    if not rows:
        rows = [{"text": str(line.get("line")), "offset": float(shot.get("start_s") or 0), "duration_s": float(shot.get("duration_s") or 2), "character": str(line.get("character") or "")} for shot in common.shots_of(ctx.in_("shots")) for line in common.dialogue_lines(shot)]
    if not rows:
        raise NodeError("字幕师拿不到台词：请接剪辑师（推荐）、配音导演或分镜表")
    rows.sort(key=lambda item: float(item.get("offset") or 0))

    max_chars = int(float(ctx.params.get("max_chars") or 16))
    min_s = float(ctx.params.get("min_s") or 0.9)
    gap = float(ctx.params.get("gap") or 0.06)
    highlight = bool(ctx.params.get("highlight", True))
    mark_words = [str(brief.get("brand") or ""), str(brief.get("product") or "")]
    position = str(ctx.params.get("position") or "底部安全区")
    rect = {"底部安全区": [0.06, 0.80, 0.88, 0.13], "居中偏下": [0.08, 0.62, 0.84, 0.16], "顶部": [0.06, 0.08, 0.88, 0.12], "逐镜跟随": [0.06, 0.74, 0.88, 0.16]}[position]

    cues: list[dict[str, Any]] = []
    too_long: list[str] = []
    for row in rows[: int(float(ctx.params.get("limit") or 80))]:
        text = str(row.get("text") or "").strip()
        if not text:
            continue
        parts = [piece.strip() for piece in craft.split_dialogue(text) if piece.strip()] or [text]
        pieces: list[str] = []
        for piece in parts:
            while len(piece) > max_chars:  # 长句找不到标点切点时硬切，宁可断两行也不超行宽
                pieces.append(piece[:max_chars])
                piece = piece[max_chars:]
            pieces.append(piece)
        chunks: list[str] = []
        current = ""
        for piece in pieces:
            if current and len(current) + len(piece) + 1 > max_chars:
                chunks.append(current)
                current = piece
            else:
                current = f"{current}，{piece}" if current else piece
        if current:
            chunks.append(current)
        if len(chunks) > 1:
            too_long.append(text[:18])
        start = float(row.get("offset") or 0.0)
        span = max(min_s, float(row.get("duration_s") or 0.0))
        total_chars = sum(len(chunk) for chunk in chunks) or 1
        cursor = start
        for line_index, chunk in enumerate(chunks):
            duration = round(max(0.4, span * len(chunk) / total_chars), 3)
            marks: list[str] = []
            if highlight:
                marks = [word for word in mark_words if word and word in chunk]
                if any(ch.isdigit() for ch in chunk):
                    marks.append("数字")
            cues.append(
                {
                    "id": f"cue-{len(cues) + 1}",
                    "start": round(cursor, 3),
                    "end": round(cursor + duration, 3),
                    "text": chunk,
                    "text_cn": chunk,
                    "character": str(row.get("character") or "") if bool(ctx.params.get("speaker", False)) else "",
                    "shot_id": str(row.get("shot_id") or ""),
                    "slate": str(row.get("slate") or ""),
                    "chars": len(chunk),
                    "highlight": marks,
                    "rect": rect,
                    "line_index": line_index,
                    "lines": len(chunks),
                }
            )
            cursor = round(cursor + duration + gap, 3)

    overlaps = [f"{a['id']}→{b['id']}" for a, b in zip(cues, cues[1:]) if float(b["start"]) < float(a["end"]) - 0.001]
    over_chars = [cue["id"] for cue in cues if int(cue["chars"]) > max_chars]
    tail = float(cues[-1]["end"]) if cues else 0.0
    duration = float(timeline.get("duration_s") or tail)
    style = {
        "position": position,
        "rect": rect,
        "safe_area": [0.05, 0.05, 0.9, 0.9],
        "max_chars": max_chars,
        "font_hint": "中文由播放器叠加（本机点阵字体不含汉字），字号按画面高度 5.2%",
        "punctuation": "行末去句末标点，保留顿逗；数字与单位不拆行",
        "lines_per_cue": 1,
    }
    payload = {
        "kind": "subtitles",
        "cues": cues,
        "count": len(cues),
        "style": style,
        "total_s": round(tail, 3),
        "coverage": round(tail / duration, 3) if duration else 0.0,
        "overlaps": overlaps,
        "provider": "canvas",
        "simulated": True,
    }
    lines = [
        f"字幕：{len(cues)} 条 / 覆盖 {tail:.2f}s（片长 {duration:.2f}s，字幕占比 {payload['coverage']:.0%}）",
        f"规范：每行 ≤{max_chars} 字，单条 ≥{min_s}s，条间 {gap}s，位置 {position}；超长拆条 {len(too_long)} 句",
        f"花字：{len([cue for cue in cues if cue['highlight']])} 条含强调词（品牌 / 产品 / 数字）" if highlight else "花字：关闭",
        f"⚠ {len(overlaps)} 处字幕重叠：{'、'.join(overlaps[:5])}" if overlaps else "逐条检查：无重叠、无出画",
        f"⚠ {len(over_chars)} 条超出行宽上限" if over_chars else "",
        f"⚠ 字幕终点超出片长 {tail - duration:+.2f}s，需回剪辑师压时长" if tail > duration + 0.01 else "",
        "说话人标注：" + ("开（信息条用角色名）" if bool(ctx.params.get("speaker", False)) else "关"),
    ]
    text = "\n".join(line for line in lines if line)
    ctx.log(f"字幕：{len(cues)} 条，重叠 {len(overlaps)} 处")
    return {"subtitles": payload, "text": text}


# ------------------------------------------------------------------ #
# 视效总监                                                             #
# ------------------------------------------------------------------ #

#: 元素 → 图层数 / 工时 / 单价，报价直接从表里算，不拍脑袋
_EFFECTS: dict[str, dict[str, Any]] = {
    "干净背景": {"layers": 1, "hours": 0.3, "note": "只做擦除与稳定"},
    "氛围粒子": {"layers": 3, "hours": 1.2, "note": "尘埃/光束粒子，需随镜头运动"},
    "镜头光斑": {"layers": 2, "hours": 0.8, "note": "逆光位加眩光与鬼影"},
    "雨丝": {"layers": 3, "hours": 1.6, "note": "斜向雨丝 + 地面积水反光"},
    "运动模糊": {"layers": 2, "hours": 0.9, "note": "甩镜方向拖影，掩盖生成抖动"},
    "故障撕裂": {"layers": 4, "hours": 1.4, "note": "横向撕裂 + 色差，转场强调"},
}


def _effect_for(shot: dict[str, Any]) -> tuple[str, str]:
    movement = str(shot.get("movement") or "")
    mood = str(shot.get("mood") or "")
    time_of_day = str(shot.get("time_of_day") or "day")
    if movement == "whip":
        return "故障撕裂", "甩镜转场，加撕裂与色差掩盖抖动"
    if movement in {"handheld", "follow"} and float(shot.get("energy") or 0.5) >= 0.8:
        return "运动模糊", "高能量手持镜，按运镜方向补拖影"
    if time_of_day in {"night", "dusk"}:
        return "镜头光斑", f"{time_of_day} 时段逆光位补眩光"
    if mood in {"tense", "warm"}:
        return "氛围粒子", f"{mood} 情绪镜加空气感粒子"
    if time_of_day in {"dawn", "day"} and str(shot.get("int_ext")) == "EXT":
        return "雨丝", "外景补天气元素，画面才有空气"
    return "干净背景", "无特效需求，只做擦除与稳定"


@node(
    "vfx_supervisor",
    title="视效总监",
    dept="post",
    role="逐镜判定特效类型并把元素真的合成到画面上：读回前一帧、叠加图层、落盘出特效帧，同时给出图层清单与工时报价",
    inputs=[Port("timeline", "时间轴", "timeline"), Port("look", "视觉基调", "look", required=False)],
    outputs=[Port("timeline", "特效后时间轴", "timeline"), Port("images", "特效帧", "images"), Port("text", "视效清单", "text")],
    params=[
        Param("effect", "元素口径", "select", "自动分配", ["自动分配"] + list(_EFFECTS)),
        Param("density", "元素密度", "range", 0.5, minimum=0.1, maximum=1, step=0.05),
        Param("limit", "最多镜头", "number", 8, minimum=1, maximum=120, step=1, unit="镜"),
        Param("rate", "工时单价", "number", 900, minimum=0, maximum=5000, step=50, unit="元/小时"),
        Param("write_back", "回写时间轴", "toggle", True),
    ],
    icon="🪄",
    tags=["合成", "图层", "报价"],
)
def vfx_supervisor(ctx: Any) -> dict[str, Any]:
    timeline = ctx.require("timeline")
    if not isinstance(timeline, dict) or not timeline.get("shots"):
        raise NodeError("视效总监需要一条时间轴：请先接剪辑师")
    look = common.look_of(ctx.in_("look"))
    shots = [dict(item) for item in common.shots_of(timeline)]
    limit = int(float(ctx.params.get("limit") or 8))
    density = float(ctx.params.get("density") or 0.5)
    rate = float(ctx.params.get("rate") or 900)
    forced = str(ctx.params.get("effect") or "自动分配")
    accent = canvas.hex_rgb(str(look.get("accent") or "#22d3ee"))

    items: list[dict[str, Any]] = []
    done = 0
    hours = 0.0
    skipped: list[str] = []
    for shot in shots:
        name, why = (forced, "统一口径") if forced != "自动分配" else _effect_for(shot)
        spec = _EFFECTS.get(name, _EFFECTS["干净背景"])
        shot["vfx"] = {"effect": name, "layers": spec["layers"], "hours": round(spec["hours"] * (0.6 + density), 2), "reason": why, "note": spec["note"]}
        if done >= limit:
            continue
        ref = str(shot.get("image_ref") or shot.get("graded_ref") or shot.get("poster_ref") or "")
        if not ref:
            skipped.append(str(shot.get("slate") or shot.get("id")))
            continue
        try:
            image = canvas.read_png(ctx.read_artifact(ref))
        except (NodeError, ValueError, IndexError) as error:
            skipped.append(f"{shot.get('slate')}（读不回：{str(error)[:20]}）")
            continue
        rng = craft.rng_for(shot.get("slate"), name, density)
        count = max(4, int(density * image.h * 0.35))
        if name == "氛围粒子":
            for _ in range(count * 2):
                x, y = int(rng.random() * image.w), int(rng.random() * image.h)
                r = 1 + int(rng.random() * 2)
                image.ellipse(x, y, r, r, (250, 250, 255), True, 0.25 + rng.random() * 0.4)
            image.glow(image.w * rng.random(), image.h * 0.2, image.w * 0.5, accent, 0.25 * density)
        elif name == "镜头光斑":
            cx, cy = image.w * (0.2 + rng.random() * 0.6), image.h * (0.12 + rng.random() * 0.2)
            image.glow(cx, cy, image.w * 0.75, (255, 240, 210), 0.35 + density * 0.4)
            for step in range(1, 5):
                ratio = step / 5
                image.ellipse(int(cx + (image.w / 2 - cx) * ratio * 1.8), int(cy + (image.h / 2 - cy) * ratio * 1.8), int(image.w * 0.02 * (5 - step)), int(image.w * 0.02 * (5 - step)), accent, True, 0.22)
        elif name == "雨丝":
            tilt = 0.25 + rng.random() * 0.3
            for _ in range(count * 6):
                x, y = int(rng.random() * image.w), int(rng.random() * image.h)
                length = int(image.h * (0.02 + rng.random() * 0.05))
                image.line(x, y, int(x + length * tilt), int(y + length), (200, 215, 235), 1)
            image.fill_rect(0, int(image.h * 0.86), image.w, int(image.h * 0.14), (120, 140, 170), 0.12 * density)
        elif name == "运动模糊":
            step = max(2, int(image.w * 0.02 * (0.5 + density)))
            buf = image.buf
            direction = -1 if str(shot.get("movement")) == "whip" else 1
            for y in range(image.h):
                base = y * image.w * 3
                for x in range(image.w):
                    src = min(image.w - 1, max(0, x + direction * step // 2))
                    index = base + src * 3
                    for ch in range(3):
                        buf[base + x * 3 + ch] = int((buf[base + x * 3 + ch] + buf[index + ch]) / 2)
        elif name == "故障撕裂":
            for _ in range(max(2, int(density * 8))):
                y = int(rng.random() * image.h)
                height = max(2, int(rng.random() * image.h * 0.03))
                shift = int((rng.random() - 0.5) * image.w * 0.12 * density)
                buf = image.buf
                for row_y in range(y, min(image.h, y + height)):
                    base = row_y * image.w * 3
                    row = bytearray(buf[base:base + image.w * 3])
                    for x in range(image.w):
                        src = (x + shift) % image.w
                        for ch in range(3):
                            buf[base + x * 3 + ch] = row[src * 3 + ch]
                image.fill_rect(0, y, image.w, 1, (255, 60, 120), 0.5)
                image.fill_rect(0, min(image.h - 1, y + height), image.w, 1, (60, 240, 255), 0.5)
        item = common.image_artifact(
            ctx,
            f"vfx-{common.slug(str(shot.get('slate') or 'shot'))}.png",
            image.to_png(),
            {
                "id": f"vfx-{len(items) + 1}",
                "kind": "vfx",
                "shot_id": str(shot.get("id") or ""),
                "shot_index": shot.get("index"),
                "slate": str(shot.get("slate") or ""),
                "prompt": f"{name}｜{spec['note']}｜{why}",
                "provider": "canvas",
                "simulated": True,
                "layers": spec["layers"],
                "effect": name,
                "hours": shot["vfx"]["hours"],
            },
        )
        items.append(item)
        shot["vfx_ref"] = item["ref"]
        shot["vfx_url"] = item["url"]
        shot["vfx"]["status"] = "comped"
        if bool(ctx.params.get("write_back", True)):
            shot["image_ref"], shot["image_url"] = item["ref"], item["url"]
        done += 1
        hours += float(shot["vfx"]["hours"])
        ctx.progress(done / max(1, min(limit, len(shots))), f"合成 {shot.get('slate')}")

    hours += sum(float(shot["vfx"]["hours"]) for shot in shots[done:])
    spread: dict[str, int] = {}
    for shot in shots:
        key = str(shot["vfx"]["effect"])
        spread[key] = spread.get(key, 0) + 1
    kinds = sorted(spread)
    lines = [
        f"视效：{len(shots)} 镜分类完成，{done} 镜已出合成帧",
        f"元素分布：{'、'.join(f'{key} {spread[key]} 镜' for key in sorted(spread, key=lambda item: -spread[item]))}",
        f"图层：{sum(int(_EFFECTS[key]['layers'] * spread[key]) for key in spread)} 层｜工时 {hours:.1f} 小时｜报价约 {hours * rate:,.0f} 元",
        f"⚠ {len(skipped)} 镜没有可合成画面：{'、'.join(str(item) for item in skipped[:8])}" if skipped else "",
        "合成在纯标准库画布上完成（像素级叠加/位移/撕裂），接 Nuke/AE 农场时把同名镜头换成远端任务。",
    ]
    text = "\n".join(line for line in lines if line)
    ctx.log(f"视效：{done} 镜合成，工时 {hours:.1f}h")
    return {
        "timeline": {**timeline, "shots": shots, "vfx": {"comped": done, "hours": round(hours, 2), "cost": round(hours * rate, 2), "effects": kinds}},
        "images": {"kind": "images", "items": items, "provider": "canvas", "simulated": True},
        "text": text,
    }


# ------------------------------------------------------------------ #
# 包装（片头 / 角标 / 字条 / 尾板）                                      #
# ------------------------------------------------------------------ #


@node(
    "packaging",
    title="包装师",
    dept="post",
    role="做栏目包装：片头卡、角标台标、说话人字条、CTA 尾板，逐条给出归一化安全区坐标，播放器按坐标叠字",
    inputs=[
        Port("timeline", "时间轴", "timeline"),
        Port("brief", "项目需求", "brief", required=False),
        Port("characters", "角色表", "characters", required=False),
        Port("look", "视觉基调", "look", required=False),
    ],
    outputs=[Port("timeline", "带包装时间轴", "timeline"), Port("images", "包装卡", "images"), Port("text", "包装说明", "text")],
    params=[
        Param("head_s", "片头时长", "number", 1.2, minimum=0, maximum=5, step=0.1, unit="秒"),
        Param("tail_s", "尾板时长", "number", 1.6, minimum=0, maximum=6, step=0.1, unit="秒"),
        Param("badge", "角标台标", "toggle", True),
        Param("thirds", "说话人字条", "toggle", True),
        Param("cta", "尾板文案", "text", ""),
        Param("safe", "安全区口径", "select", "竖版平台", ["竖版平台", "横版平台", "全屏无遮挡"]),
        Param("width", "卡片宽度", "number", 360, minimum=160, maximum=1024, step=20, unit="像素"),
    ],
    icon="🎁",
    tags=["片头", "角标", "安全区"],
)
def packaging(ctx: Any) -> dict[str, Any]:
    timeline = ctx.require("timeline")
    if not isinstance(timeline, dict) or not timeline.get("shots"):
        raise NodeError("包装师需要一条时间轴：请先接剪辑师")
    brief = common.norm_brief(ctx.in_("brief"), None)
    look = common.look_of(ctx.in_("look"))
    shots = [dict(item) for item in common.shots_of(timeline)]
    aspect = str(timeline.get("aspect") or "9:16")
    width = int(float(ctx.params.get("width") or 360))
    canvas_w, canvas_h = canvas.size_for(aspect, width)
    accent = canvas.hex_rgb(str(look.get("accent") or "#22d3ee"))
    ink = canvas.hex_rgb(str((look.get("palette") or ["#101a36"])[-1]))
    safe = str(ctx.params.get("safe") or "竖版平台")
    pad = {"竖版平台": (0.05, 0.06, 0.05, 0.14), "横版平台": (0.04, 0.05, 0.04, 0.08), "全屏无遮挡": (0.02, 0.02, 0.02, 0.02)}[safe]
    head_s = float(ctx.params.get("head_s") or 0)
    tail_s = float(ctx.params.get("tail_s") or 0)
    total = float(timeline.get("duration_s") or 0.0)
    title = str(brief.get("title") or "成片")
    brand = str(brief.get("brand") or brief.get("product") or "")
    overlays: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []

    def card(kind: str, label: str, note: str, fill: tuple[int, int, int], rect: list[float], start: float, end: float, text_cn: str) -> dict[str, Any]:
        image = canvas.Raster(canvas_w, canvas_h, fill)
        image.glow(canvas_w * 0.5, canvas_h * 0.42, canvas_w * 0.9, accent, 0.4 if kind in {"head", "tail"} else 0.12)
        image.stroke_rect(int(canvas_w * pad[0]), int(canvas_h * pad[1]), int(canvas_w * (1 - pad[0] - pad[2])), int(canvas_h * (1 - pad[1] - pad[3])), (235, 240, 250), 2)
        image.fill_rect(int(canvas_w * rect[0]), int(canvas_h * rect[1]), int(canvas_w * rect[2]), int(canvas_h * rect[3]), ink, 0.72)
        image.text(int(canvas_w * rect[0]) + 8, int(canvas_h * rect[1]) + 6, canvas.ascii_safe(label), (245, 248, 255), 2 if kind in {"head", "tail"} else 1)
        if note:
            image.text(int(canvas_w * pad[0]) + 8, int(canvas_h * (1 - pad[1] - pad[3])) - 14, canvas.ascii_safe(note), (205, 216, 235), 1)
        item = common.image_artifact(
            ctx,
            f"pack-{common.slug(kind)}-{common.slug(label, kind)}.png",
            image.to_png(),
            {"id": f"pack-{len(items) + 1}", "kind": f"pack-{kind}", "prompt": f"{kind} 卡：{label}", "provider": "canvas", "simulated": True, "card": kind},
        )
        items.append(item)
        row = {"id": item["id"], "kind": kind, "start_s": round(start, 3), "end_s": round(end, 3), "rect": rect, "text_cn": text_cn, "ref": item["ref"], "url": item["url"], "layer": 20, "provider": "canvas", "simulated": True}
        overlays.append(row)
        return row

    if head_s > 0:
        card("head", title, f"{brand} {aspect} {round(total, 1)}s".strip(), (10, 12, 20), [0.08, 0.40, 0.84, 0.18], 0.0, head_s, title)
    if tail_s > 0:
        cta = str(ctx.params.get("cta") or f"{brand} · 关注看实测" if brand else "关注看实测")
        card("tail", cta, "CTA", (8, 10, 16), [0.10, 0.44, 0.80, 0.14], round(max(0.0, total - tail_s), 3), round(total, 3), cta)
    if bool(ctx.params.get("badge", True)) and brand:
        card("badge", brand, "", (10, 12, 20), [1 - pad[0] - 0.20, pad[1] + 0.01, 0.19, 0.045], 0.0, round(total, 3), brand)
        overlays[-1]["layer"] = 30
    if bool(ctx.params.get("thirds", True)):
        roster = common.characters_of(ctx.in_("characters"))
        by_name = {str(row.get("name")): row for row in roster}
        seen: set[str] = set()
        for shot in shots:
            who = str((shot.get("dialogue") or [{}])[0].get("character") or "") if shot.get("dialogue") else ""
            if not who or who in seen or who == "旁白":
                continue
            seen.add(who)
            role_type = str((by_name.get(who) or {}).get("role_type") or "")
            card("thirds", who, role_type, (10, 12, 20), [pad[0] + 0.02, 0.70, 0.42, 0.075], float(shot.get("start_s") or 0.0), round(float(shot.get("start_s") or 0.0) + 2.2, 3), who)
    timeline_out = {**timeline, "shots": shots, "overlays": overlays, "safe_area": {"preset": safe, "pad": pad}}
    kinds = sorted({str(row["kind"]) for row in overlays})
    lines = [
        f"包装：{len(overlays)} 个图层（{'、'.join(kinds) or '无'}），卡片底图 {len(items)} 张 {canvas_w}x{canvas_h}",
        f"安全区：{safe}，上下左右留白 {pad[0]:.2f}/{pad[3]:.2f}/{pad[2]:.2f}/{pad[1]:.2f}",
        f"片头 {head_s}s｜尾板 {tail_s}s｜角标 {'开' if bool(ctx.params.get('badge', True)) else '关'}｜字条 {len([row for row in overlays if row['kind'] == 'thirds'])} 条",
        "汉字由播放器按 rect 坐标叠加（本机点阵字体无汉字），卡图只给版式与配色。",
    ]
    text = "\n".join(line for line in lines if line)
    ctx.log(f"包装：{len(overlays)} 个图层")
    return {"timeline": timeline_out, "images": {"kind": "images", "items": items, "provider": "canvas", "simulated": True}, "text": text}


# ------------------------------------------------------------------ #
# 封面设计                                                             #
# ------------------------------------------------------------------ #

_COVERS: dict[str, str] = {"竖版 3:4": "3:4", "方版 1:1": "1:1", "横版 16:9": "16:9"}


@node(
    "cover_designer",
    title="封面设计",
    dept="post",
    role="出平台封面/海报：按视觉基调构图，给出主副标题文案与文字安全区，一个口径可出竖版/方版/横版三套",
    inputs=[
        Port("brief", "项目需求", "brief", required=False),
        Port("timeline", "时间轴", "timeline", required=False),
        Port("look", "视觉基调", "look", required=False),
        Port("images", "参考画面", "images", required=False, many=True),
    ],
    outputs=[Port("images", "封面", "images"), Port("text", "封面说明", "text")],
    params=[
        Param("title", "主标题", "text", ""),
        Param("subtitle", "副标题", "text", ""),
        Param("layout", "版式", "select", "三尺寸", ["竖版 3:4", "方版 1:1", "横版 16:9", "三尺寸"]),
        Param("base", "底图口径", "select", "输入图优先", ["输入图优先", "纯构图底图"], hint="开=优先把上游剧照/关键帧铺满版式，关=只用程序构图的抽象底图"),
        Param("width", "图宽", "number", 480, minimum=200, maximum=1024, step=20, unit="像素"),
        Param("use_model", "走生成模型", "toggle", False, hint="开=写真机出图（需端点），关=离线构图底图"),
        Param("note", "角标文案", "text", ""),
    ],
    icon="🖼️",
    tags=["封面", "投放素材"],
    needs_service=True,
)
def cover_designer(ctx: Any) -> dict[str, Any]:
    brief = common.norm_brief(ctx.in_("brief"), None)
    look = common.look_of(ctx.in_("look"))
    timeline = ctx.in_("timeline") if isinstance(ctx.in_("timeline"), dict) else {}
    refs: list[dict[str, Any]] = []
    for bundle in ctx.many("images"):
        refs += common.items_of(bundle)
    title = str(ctx.params.get("title") or brief.get("title") or "Untitled")
    subtitle = str(ctx.params.get("subtitle") or brief.get("synopsis") or brief.get("tagline") or "")
    layout = str(ctx.params.get("layout") or "三尺寸")
    width = int(float(ctx.params.get("width") or 480))
    aspects = list(_COVERS.values()) if layout == "三尺寸" else [_COVERS[layout]]
    hook = str((common.shots_of(timeline) or [{}])[0].get("slate") or "")
    note = str(ctx.params.get("note") or "") or hook

    base_mode = str(ctx.params.get("base") or "输入图优先")
    candidates = sorted(
        [item for item in refs if str(item.get("ref") or "").lower().endswith(".png")],
        key=lambda item: (_cover_base_rank(item), 1 if item.get("simulated", True) else 0, -int(item.get("w") or 0)),
    ) if base_mode == "输入图优先" and not bool(ctx.params.get("use_model", False)) else []
    base_raster: canvas.Raster | None = None
    base_ref = ""
    base_note = f"{base_mode}（上游 {len(refs)} 张画面里挑不出可用 PNG）"
    base_warnings: list[str] = []
    for candidate in candidates[:3]:
        ref = str(candidate["ref"])
        try:
            base_raster = canvas.read_png(ctx.read_artifact(ref))
        except (NodeError, ValueError) as error:
            base_warnings.append(f"{ref} 解不出像素（{str(error)[:40]}）")
            continue
        base_ref = ref
        base_note = f"{candidate.get('kind')} ← {ref}（{base_raster.w}x{base_raster.h}）"
        break

    items: list[dict[str, Any]] = []
    for aspect in aspects:
        canvas_w, canvas_h = canvas.size_for(aspect, width)
        prompt = ", ".join(part for part in [canvas.ascii_safe(title), canvas.ascii_safe(subtitle)[:60], str(look.get("style_label") or ""), f"aspect ratio {aspect}"] if part)
        drawer = lambda a=aspect, n=note: canvas.poster_frame(look, canvas.ascii_safe(title) or "COVER", a, width, n)
        if bool(ctx.params.get("use_model", False)):
            item = common.generate_or_draw(ctx, prompt, f"cover-{aspect.replace(':', 'x')}.png", f"{canvas_w}x{canvas_h}", drawer, {"id": f"cover-{len(items) + 1}", "kind": "cover", "seed": 77 + len(items), "aspect": aspect, "prompt": prompt})
        else:
            png = _cover_on_base(base_raster, look, canvas.ascii_safe(title) or "COVER", canvas_w, canvas_h, canvas.ascii_safe(note)) if base_raster is not None else drawer()
            item = common.image_artifact(ctx, f"cover-{aspect.replace(':', 'x')}.png", png, {"id": f"cover-{len(items) + 1}", "kind": "cover", "aspect": aspect, "prompt": prompt, "provider": "canvas", "simulated": True})
        item.update(
            {
                "title_cn": title,
                "subtitle_cn": subtitle,
                "text_safe_rect": [0.08, 0.80, 0.84, 0.16] if aspect != "16:9" else [0.06, 0.62, 0.55, 0.3],
                "font_hint": "主标题占画面高度 8%，副标题 4.5%，由播放器叠加",
                "platform": str(brief.get("platform") or ""),
                "shot_ref": hook,
                "base_ref": base_ref if base_raster is not None and not bool(ctx.params.get("use_model", False)) else "",
            }
        )
        items.append(item)
    real = [item for item in items if not item.get("simulated")]
    lines = [
        f"封面：{len(items)} 张（{layout}，{width}px 起）｜通道：真机 {len(real)} / 示意 {len(items) - len(real)}",
        f"底图：{base_note}" if items and not real else "底图：真机出图，未叠本机底图",
        f"文案：主标题 {len(title)} 字｜副标题 {len(subtitle)} 字｜角标 {canvas.ascii_safe(note) or '无'}",
        "文字安全区已按版式给出，中文标题由播放器叠加；投放前按平台尺寸再各出一版。",
        ("⚠ 候选底图读不出来：" + "；".join(base_warnings[:2])) if base_warnings else "",
    ]
    ctx.log(f"封面：{len(items)} 张，真机 {len(real)} 张")
    return {"images": {"kind": "images", "items": items, "provider": real[0]["provider"] if real else "canvas", "simulated": not real}, "text": "\n".join(lines)}


# ------------------------------------------------------------------ #
# 渲染导出                                                             #
# ------------------------------------------------------------------ #


@node(
    "exporter",
    title="渲染导出",
    dept="post",
    role="把时间轴打成交付包：规格与码率清单、SRT/ASS 字幕、CMX3600 EDL、逐镜素材清单全部落盘，并诚实说明本机没有编码器",
    inputs=[
        Port("timeline", "时间轴", "timeline"),
        Port("subtitles", "字幕轨", "subtitles", required=False, many=True),
        Port("audio", "音轨", "audio", required=False, many=True),
        Port("clips", "视频片段", "clips", required=False, many=True),
        Port("images", "画面帧", "images", required=False, many=True),
        Port("look", "视觉基调", "look", required=False),
        Port("brief", "项目需求", "brief", required=False),
    ],
    outputs=[Port("timeline", "交付时间轴", "timeline"), Port("text", "交付清单", "text")],
    params=[
        Param("target", "交付口径", "select", "平台直传", ["平台直传", "本地母版", "两者"]),
        Param("width", "输出宽度", "number", 1080, minimum=360, maximum=2160, step=20, unit="像素"),
        Param("fps", "帧率", "number", 0, minimum=0, maximum=60, step=1, unit="fps", hint="0=沿用时间轴帧率"),
        Param("bitrate", "码率", "number", 6, minimum=1, maximum=40, step=0.5, unit="Mbps"),
        Param("srt", "字幕格式", "select", "两者", ["SRT", "ASS", "两者", "不打包"]),
        Param("edl", "打包 EDL", "toggle", True),
        Param("manifest", "打包交付清单", "toggle", True),
        Param("filename", "文件名", "text", ""),
    ],
    icon="📦",
    tags=["交付", "SRT", "EDL"],
)
def exporter(ctx: Any) -> dict[str, Any]:
    timeline = ctx.require("timeline")
    if not isinstance(timeline, dict) or not timeline.get("shots"):
        raise NodeError("渲染导出需要一条时间轴：请先接剪辑师")
    brief = common.norm_brief(ctx.in_("brief"), None)
    shots = common.shots_of(timeline)
    fps = int(float(ctx.params.get("fps") or 0) or int(timeline.get("fps") or 24))
    aspect = str(timeline.get("aspect") or "9:16")
    out_w, out_h = canvas.size_for(aspect, int(float(ctx.params.get("width") or 1080)))
    bitrate = float(ctx.params.get("bitrate") or 6)
    duration = float(timeline.get("duration_s") or sum(float(row.get("duration_s") or 0) for row in shots))
    stem = common.slug(str(ctx.params.get("filename") or brief.get("title") or "delivery"), "delivery")

    cues: list[dict[str, Any]] = []
    for bundle in ctx.many("subtitles"):
        if isinstance(bundle, dict):
            cues += common.cues_of(bundle)
        else:
            cues += common.cues_of({"cues": bundle})
    if not cues:  # 没有字幕师时按时间轴上挂回的人声出字幕，交付包不能缺字幕轨
        for row in (timeline.get("voice") or {}).get("items") or []:
            start = float(row.get("offset") or 0.0)
            cues.append({"start": start, "end": round(start + float(row.get("duration_s") or 1.0), 3), "text": str(row.get("text") or "")})
    cues.sort(key=lambda item: float(item.get("start") or 0.0))

    fmt = str(ctx.params.get("srt") or "两者")
    saved: list[dict[str, Any]] = []
    if fmt in {"SRT", "两者"} and cues:
        blocks: list[str] = []
        for index, cue in enumerate(cues, 1):
            who = str(cue.get("character") or "").strip()
            line = f"〔{who}〕 {str(cue.get('text') or '')}".strip() if who else str(cue.get("text") or "")
            blocks.append(f"{index}\r\n{_srt_time(float(cue['start']))} --> {_srt_time(float(cue['end']))}\r\n{line}\r\n")
        saved.append(ctx.save_file(f"{stem}.srt", "\r\n".join(blocks).encode("utf-8-sig")))
    if fmt in {"ASS", "两者"} and cues:
        header = (
            "[Script Info]\nTitle: factory delivery\nScriptType: v4.00+\n"
            f"PlayResX: {out_w}\nPlayResY: {out_h}\nWrapStyle: 0\n\n[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, Outline, Shadow, Alignment, MarginL, MarginR, MarginV\n"
            f"Style: Main,PingFang SC,{int(out_h * 0.052)},&H00FFFFFF,&H00000000,1,2,40,40,{int(out_h * 0.09)}\n\n[Events]\n"
            "Format: Layer, Start, End, Style, Text\n"
        )
        events = "".join(
            f"Dialogue: 0,{_ass_time(float(cue['start']))},{_ass_time(float(cue['end']))},Main,,,{cue.get('text', '')}\n" for cue in cues
        )
        saved.append(ctx.save_file(f"{stem}.ass", (header + events).encode("utf-8")))
    if bool(ctx.params.get("edl", True)):
        rows = timeline.get("edl") or []
        body = [f"TITLE {stem.upper()}", "FCM NON-DROP FRAME", ""]
        for index, row in enumerate(rows):
            channel = "V" if str(row.get("source") or "none") != "none" else "C"
            duration_s = float(row.get("duration_s") or 0.0)
            start_s = float(row.get("start_s") or 0.0)
            body.append(
                f"{index + 1:03d} {str(row.get('reel') or index + 1):>3s} {channel:<4s} {str(row.get('transition') or 'CUT').upper():<12s} "
                f"{str(row.get('src_in') or _tc(0.0, fps))} {str(row.get('src_out') or _tc(duration_s, fps))} "
                f"{str(row.get('rec_in') or _tc(start_s, fps))} {str(row.get('rec_out') or _tc(start_s + duration_s, fps))}"
            )
        saved.append(ctx.save_file(f"{stem}.edl", ("\n".join(body) + "\n").encode("ascii", "replace")))
    saved.append(ctx.save_file(f"{stem}-timeline.json", json.dumps(timeline, ensure_ascii=False, separators=(",", ":")).encode("utf-8")))

    video_mb = round(duration * bitrate / 8, 1)
    audio_mb = round(192 * duration / 8 / 1024, 1)
    manifest = {
        "title": str(ctx.params.get("filename") or brief.get("title") or "成片"),
        "target": str(ctx.params.get("target") or "平台直传"),
        "container": "mp4 (需外部编码器或前端播放器导出，本机不假装出 mp4)",
        "resolution": f"{out_w}x{out_h}",
        "aspect": aspect,
        "fps": fps,
        "duration_s": round(duration, 3),
        "video_bitrate_mbps": bitrate,
        "audio": {"codec": "aac", "bitrate_kbps": 192, "channels": 2, "sample_rate": 44100},
        "est_video_mb": video_mb,
        "est_audio_mb": audio_mb,
        "est_total_mb": round(video_mb + audio_mb, 1),
        "est_render_minutes": round(duration / max(1, fps) * 0.9 * max(1, (out_w * out_h) / (720 * 1280)), 1),
        "shots": [
            {
                "index": row.get("index"),
                "slate": row.get("slate"),
                "start_s": row.get("start_s"),
                "duration_s": row.get("duration_s"),
                "source": row.get("source"),
                "clip_url": row.get("clip_url") or "",
                "frames": len(row.get("frames") or []),
                "poster_ref": row.get("poster_ref") or "",
                "image_ref": row.get("image_ref") or "",
                "provider": row.get("source_provider") or "",
                "simulated": bool(row.get("source_simulated", True)),
            }
            for row in shots
        ],
        "subtitle_cues": len(cues),
        "audio_tracks": sum(len(common.items_of(bundle)) for bundle in ctx.many("audio")),
        "files": saved,
        "gaps": {
            "missing_visual": [str(item) for item in (timeline.get("missing_visual") or [])],
            "simulated_shots": len([row for row in shots if bool(row.get("source_simulated", True))]),
            "orphan_lines": [str(item) for item in (timeline.get("orphan_lines") or [])],
        },
    }
    if bool(ctx.params.get("manifest", True)):
        saved.append(ctx.save_file(f"{stem}-delivery.json", json.dumps(manifest, ensure_ascii=False, indent=1).encode("utf-8")))

    lines = [
        f"交付包：{out_w}x{out_h}｜{fps}fps｜{duration:.2f}s｜视频 {bitrate}Mbps ≈ {video_mb}MB + 音频 192kbps ≈ {audio_mb}MB",
        f"落盘 {len(saved)} 个文件：{'、'.join(str(item['ref'].rsplit('/', 1)[-1]) for item in saved)}",
        f"字幕 {len(cues)} 条（{fmt}）｜EDL {len(timeline.get('edl') or [])} 刀｜素材真机 {len([row for row in shots if not bool(row.get('source_simulated', True))])}/{len(shots)} 镜",
        f"⚠ 本机没有编码器：交付包给的是时间轴 + 素材清单 + 字幕 + EDL，WebM/MP4 需前端播放器（src/vg 帧序列编码）或外部编码器执行。" if manifest["container"].startswith("mp4 (") else "",
        f"⚠ {len(manifest['gaps']['missing_visual'])} 镜无画面、{manifest['gaps']['simulated_shots']} 镜为示意素材——上线前必须补真机镜头。" if (manifest["gaps"]["missing_visual"] or manifest["gaps"]["simulated_shots"]) else "",
        f"预估渲染 {manifest['est_render_minutes']} 分钟（按 720x1280 基准 0.9s/帧线性放大）。",
    ]
    text = "\n".join(line for line in lines if line)
    ctx.log(f"交付包：{len(saved)} 个文件，{video_mb}MB 估算")
    return {"timeline": {**timeline, "delivery": manifest, "files": saved}, "text": text}


def _ass_time(seconds: float) -> str:
    total = max(0.0, float(seconds))
    whole = int(total)
    centis = int(round((total - whole) * 100))
    if centis >= 100:
        centis -= 100
        whole += 1
    return f"{whole // 3600}:{whole // 60 % 60:02d}:{whole % 60:02d}.{centis:02d}"


# ------------------------------------------------------------------ #
# 预告片剪辑                                                           #
# ------------------------------------------------------------------ #


@node(
    "trailer_editor",
    title="预告片剪辑",
    dept="post",
    role="吃已成片的时间轴做卡点重剪：按节拍强度挑爆点、压单镜时长、重排人声落点，并合成与刀点同步的渐强/重击/低频下坠音效包",
    inputs=[
        Port("timeline", "成片时间轴", "timeline"),
        Port("audio", "成片音轨", "audio", required=False, many=True),
        Port("brief", "项目需求", "brief", required=False),
        Port("look", "视觉基调", "look", required=False),
    ],
    outputs=[
        Port("timeline", "预告时间轴", "timeline"),
        Port("audio", "卡点音效包", "audio"),
        Port("text", "预告说明", "text"),
    ],
    params=[
        Param("target", "预告时长", "number", 15, minimum=6, maximum=40, step=1, unit="秒"),
        Param("max_shot", "单镜上限", "number", 2.0, minimum=0.6, maximum=6, step=0.2, unit="秒", hint="预告片里几乎没有超过两秒的镜头"),
        Param("pace", "节奏", "select", "紧凑", ["紧凑", "叙事", "炸点"], hint="炸点＝每刀给呼啸；叙事＝只在重击处出声"),
        Param("first_slate", "开场镜头", "text", "", hint="填场次号强制开场，留空则自动挑钩子镜"),
        Param("keep_vo", "保留人声", "toggle", True, hint="装不下的台词如实丢掉，不假装说完了"),
        Param("impact_every", "重击间隔", "number", 2, minimum=1, maximum=6, step=1, unit="刀"),
        Param("label", "版本名", "text", ""),
    ],
    icon="🎞️",
    tags=["后期", "预告", "卡点"],
)
def trailer_editor(ctx: Any) -> dict[str, Any]:
    master = ctx.require("timeline")
    if not isinstance(master, dict) or not master.get("shots"):
        raise NodeError("预告片剪辑要吃一条已成片的时间轴：请接在调色/包装/导出之后")
    brief = common.norm_brief(ctx.in_("brief"), None)
    shots = common.shots_of(master)
    target = float(ctx.params.get("target") or 15)
    max_shot = float(ctx.params.get("max_shot") or 2.0)
    pace = str(ctx.params.get("pace") or "紧凑")
    first_slate = str(ctx.params.get("first_slate") or "").strip()
    keep_vo = bool(ctx.params.get("keep_vo"))
    impact_every = max(1, int(float(ctx.params.get("impact_every") or 2)))
    aspect = str(master.get("aspect") or "9:16")
    fps = int(master.get("fps") or 24)
    label = str(ctx.params.get("label") or f"{brief.get('title') or '成片'}｜{target:.0f}s 预告")

    lines_by_shot: dict[str, list[dict[str, Any]]] = {}
    for row in (master.get("voice") or {}).get("items") or []:
        if isinstance(row, dict):
            lines_by_shot.setdefault(str(row.get("shot_id") or row.get("slate") or ""), []).append(row)

    def rank(pair: tuple[int, dict[str, Any]]) -> tuple[Any, ...]:
        position, shot = pair
        forced = 0 if first_slate and str(shot.get("slate") or "") == first_slate else 1
        no_visual = 1 if str(shot.get("source") or "none") == "none" else 0
        return (forced, _BEAT_PRIORITY.get(str(shot.get("beat") or ""), 8), no_visual, -float(shot.get("energy") or 0.0), position)

    chosen: list[tuple[dict[str, Any], float]] = []
    dropped: list[tuple[str, str]] = []
    spent = 0.0
    for position, shot in sorted(enumerate(shots), key=rank):
        duration = round(min(max(0.4, float(shot.get("duration_s") or 2.0)), max_shot), 3)
        if chosen and spent + duration > target:
            dropped.append((str(shot.get("slate") or shot.get("id")), f"超出预告预算 {target:.0f}s"))
            continue
        chosen.append((shot, duration))
        spent += duration
    if not chosen:
        raise NodeError(f"预告片剪辑：{target:.0f}s 预算 / 单镜 {max_shot}s 上限下一镜不剩")

    rows: list[dict[str, Any]] = []
    voice_rows: list[dict[str, Any]] = []
    dropped_lines: list[str] = []
    cursor = 0.0
    for position, (shot, duration) in enumerate(chosen):
        row = dict(shot)
        key = str(shot.get("id") or shot.get("slate") or "")
        row.update(
            {
                "index": position,
                "orig_index": shot.get("index"),
                "start_s": round(cursor, 3),
                "duration_s": duration,
                "trailer": True,
            }
        )
        lead = 0.12
        for line in sorted(lines_by_shot.get(key) or [], key=lambda item: float(item.get("offset") or 0.0)):
            text = str(line.get("text") or "").strip()
            if not text:
                continue
            length = float(line.get("duration_s") or 1.0)
            if not keep_vo or lead + length > duration - 0.02:
                dropped_lines.append(f"{row['slate']}：{text[:16]}")
                continue
            voice_rows.append({**line, "offset": round(cursor + lead, 3), "shot_id": key, "slate": str(row.get("slate") or "")})
            lead = round(lead + length + 0.08, 3)
        row["lines"] = len([item for item in voice_rows if item["shot_id"] == key])
        rows.append(row)
        cursor = round(cursor + duration, 3)

    total = round(cursor, 3)
    cuts: list[dict[str, Any]] = []
    for index in range(1, len(rows)):
        key = "cut" if pace != "叙事" else str(rows[index].get("transition") or "cut")
        if key not in craft.TRANSITIONS:
            key = "cut"
        cuts.append(
            {
                "at_s": rows[index]["start_s"],
                "from": str(rows[index - 1].get("slate") or ""),
                "to": str(rows[index].get("slate") or ""),
                "kind": key,
                "label": craft.TRANSITIONS[key]["label"],
                "cost_s": float(craft.TRANSITIONS[key]["cost"]),
                "across_scene": str(rows[index - 1].get("scene_id")) != str(rows[index].get("scene_id")),
            }
        )
    ends = {str(row.get("id")): float(row["start_s"]) + float(row["duration_s"]) for row in rows}
    overflow = [row["slate"] for row in voice_rows if float(row["offset"]) + float(row["duration_s"]) > ends.get(row["shot_id"], 1e9) + 0.02]
    edl = [
        {
            "reel": f"{index + 1:03d}",
            "slate": str(row.get("slate") or ""),
            "src_in": _tc(0.0, fps),
            "src_out": _tc(float(row["duration_s"]), fps),
            "rec_in": _tc(float(row["start_s"]), fps),
            "rec_out": _tc(float(row["start_s"]) + float(row["duration_s"]), fps),
            "transition": (cuts[index - 1]["kind"].upper() if index else "CUT"),
            "transition_label": (cuts[index - 1]["label"] if index else "硬切"),
            "source": str(row.get("source") or "none"),
        }
        for index, row in enumerate(rows)
    ]

    events: list[dict[str, Any]] = []
    tracks: list[dict[str, Any]] = []

    def lay(kind: str, offset: float, gain: float, reason: str) -> None:
        at = round(max(0.0, min(total - 0.05, offset)), 3)
        samples = audio_lib.sfx(kind, gain=gain, seed=int(at * 1000) % 9973 + 7)
        saved = ctx.save_wav(f"trailer-{kind}-{len(events) + 1}.wav", audio_lib.wav_bytes(samples))
        spec = craft.SFX_LIBRARY[kind]
        events.append(
            {
                "kind": "sfx",
                "id": f"trl-sfx-{len(events) + 1}",
                "label": str(spec["label"]),
                "sfx": kind,
                "offset": at,
                "gain": round(gain, 2),
                "reason": reason,
                "duration_s": round(audio_lib.seconds_of(samples), 3),
                "provider": "sample",
                "simulated": True,
                **saved,
            }
        )
        tracks.append({"offset": at, "gain": gain, "kind": "sfx", "samples": samples})

    hits = [index for index in range(1, len(rows)) if index % impact_every == 0]
    if hits:
        lay("riser", max(0.0, float(rows[hits[0]]["start_s"]) - 1.5), 0.55, f"推到第 {hits[0]} 刀")
    for index in hits:
        lay("impact", float(rows[index]["start_s"]), 0.75, f"第 {index} 刀进 {rows[index].get('slate')}")
    if pace == "炸点":
        for cut in cuts:
            lay("whoosh", float(cut["at_s"]) - 0.18, 0.45, f"{cut['from']}→{cut['to']}")
    if len(rows) > 1:
        lay("sub_drop", float(rows[-1]["start_s"]), 0.8, f"落版 {rows[-1].get('slate')}")
    if pace == "叙事" and voice_rows:
        lay("chime", float(voice_rows[0]["offset"]) - 0.1, 0.3, "首句人声前置提示")

    bed = audio_lib.mix(tracks, total_seconds=total) if tracks else audio_lib.silence(total)
    peak = max((abs(float(value)) for value in bed), default=0.0)
    bed_saved = ctx.save_wav(f"trailer-bed-{common.slug(label)}.wav", audio_lib.wav_bytes(bed))
    master_item = {
        **bed_saved,
        "kind": "master",
        "id": "trailer-bed",
        "label": f"预告卡点总轨｜{len(events)} 层",
        "offset": 0.0,
        "gain": 1.0,
        "total_s": round(audio_lib.seconds_of(bed), 3),
        "peak_db": round(audio_lib.db_of(peak), 1),
        "provider": "sample",
        "simulated": True,
    }
    audio_payload = {
        "kind": "sfx",
        "items": events + [master_item],
        "total_s": total,
        "layers": len({str(item["sfx"]) for item in events}),
        "simulated": True,
    }

    no_visual = [str(row.get("slate") or "") for row in rows if str(row.get("source") or "none") == "none"]
    real_src = [row for row in rows if str(row.get("source") or "") == "clip" and not bool(row.get("source_simulated", True))]
    timeline = {
        "kind": "timeline",
        "version": "预告片",
        "label": label,
        "note": f"由成片时间轴（{master.get('label') or master.get('version') or '未命名'}，{float(master.get('duration_s') or 0):.2f}s）卡点重剪",
        "aspect": aspect,
        "fps": fps,
        "shots": rows,
        "count": len(rows),
        "duration_s": total,
        "target_s": round(target, 2),
        "voice": {"kind": "voice", "items": voice_rows, "count": len(voice_rows), "total_s": total, "provider": "trailer", "simulated": True},
        "masters": [master_item],
        "audio_kinds": ["sfx", "voice"],
        "cuts": len(cuts),
        "transitions": cuts,
        "edl": edl,
        "missing_visual": no_visual,
        "dropped": dropped,
        "orphan_lines": dropped_lines[:12],
        "source_timeline": str(master.get("label") or master.get("version") or ""),
        "simulated": not real_src,
        "provider": "trailer",
    }
    lines = [
        f"预告（{target:.0f}s）：{len(shots)} 镜成片 → {len(rows)} 镜 / {total:.2f}s，单镜上限 {max_shot}s，节奏 {pace}",
        f"挑镜口径：按节拍强度排序（{'、'.join(sorted({str(row.get('beat') or '-') for row in rows}))}），镜头素材与调色沿用成片时间轴，不重新生成",
        f"卡点音效：{len(events)} 层（riser {len([1 for item in events if item['sfx'] == 'riser'])}／重击 {len([1 for item in events if item['sfx'] == 'impact'])}／呼啸 {len([1 for item in events if item['sfx'] == 'whoosh'])}／下坠 {len([1 for item in events if item['sfx'] == 'sub_drop'])}）→ {master_item['total_s']}s 总轨，峰值 {master_item['peak_db']}dBFS",
        f"人声：留 {len(voice_rows)} 句挂回新落点，弃 {len(dropped_lines)} 句（{'、'.join(item[:14] for item in dropped_lines[:4])}）" if dropped_lines else f"人声：{len(voice_rows)} 句全部装进新镜头长度",
        f"弃用 {len(dropped)} 镜：{'；'.join(f'{slate}（{why}）' for slate, why in dropped[:5])}" if dropped else "成片镜头全部进预告",
        f"⚠ {len(overflow)} 句台词说到预告镜头外：需回分镜或再压落点。" if overflow else "",
        f"⚠ {len(no_visual)} 镜在成片里就没有画面素材：{'、'.join(no_visual[:5])}——预告不能掩盖缺片。" if no_visual else "",
        "预告片只重剪与做音效设计，画面引用成片素材；出片请接独立的渲染导出岗位。",
    ]
    text = "\n".join(line for line in lines if line)
    ctx.log(f"预告：{len(rows)} 镜 / {total}s，音效 {len(events)} 层，弃台词 {len(dropped_lines)} 句")
    return {"timeline": timeline, "audio": audio_payload, "text": text}
