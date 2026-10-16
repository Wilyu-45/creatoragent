"""生成车间岗位：关键帧画师、视频生成模型、数字人演员。

这一车间是全厂最容易「假装做了」的地方，所以规矩写死在代码里：
配了真机端点就提交任务、轮询、把远端文件拉回本地落盘，产物标
``provider=<服务商>``、``simulated=False``；没配就老老实实用标准库把
逐帧示意图画出来，标 ``provider="canvas"``、``simulated=True``，
并在交付说明里写真机 0 帧 / 示意 N 帧。远端还没跑完的任务如实挂 ``pending``，
同时给一套示意帧顶上，绝不把「已提交」说成「已完成」。
"""

from __future__ import annotations

import base64
import math
import time
from typing import Any

from .. import canvas, craft, media
from ..spec import NodeError, Param, Port, node
from . import common

#: 景别 → 人物在画面里的高度占比（关键帧画师按分镜景别构图，不是随手一画）
_FIGURE_BY_SIZE = {
    "extreme_wide": 0.12,
    "wide": 0.22,
    "full": 0.4,
    "medium": 0.52,
    "close_up": 0.78,
    "extreme_close": 0.95,
    "macro": 0.95,
}

#: 运镜 → Ken Burns 窗口（起始缩放/中心 → 结束缩放/中心）
_MOTION_WINDOW = {
    "push_in": ((1.0, 0.5, 0.5), (1.34, 0.5, 0.48)),
    "pull_out": ((1.32, 0.5, 0.5), (1.0, 0.5, 0.5)),
    "pan": ((1.24, 0.34, 0.5), (1.24, 0.66, 0.5)),
    "tilt": ((1.22, 0.5, 0.36), (1.22, 0.5, 0.64)),
    "track": ((1.2, 0.36, 0.52), (1.2, 0.64, 0.5)),
    "follow": ((1.14, 0.44, 0.52), (1.26, 0.56, 0.48)),
    "crane": ((1.3, 0.5, 0.66), (1.05, 0.5, 0.38)),
    "handheld": ((1.1, 0.48, 0.5), (1.18, 0.53, 0.47)),
    "whip": ((1.4, 0.28, 0.5), (1.4, 0.72, 0.5)),
    "orbit": ((1.22, 0.4, 0.5), (1.22, 0.6, 0.5)),
}


def _model_of(ctx: Any, task: str) -> dict[str, Any]:
    """取上游「生成模型配置」；没接时自己探一次端点，不假装配置存在。"""
    raw = ctx.in_("model")
    if isinstance(raw, dict) and raw.get("task"):
        return dict(raw)
    ready_table = {"i2v": media.video_provider, "t2v": media.video_provider, "dh": media.video_provider, "t2i": media.image_provider}
    ready, detected = ready_table.get(task, media.video_provider)()
    return {
        "task": task,
        "provider": detected if ready else "sample",
        "requested": "auto",
        "model": "",
        "size": "1024x1024",
        "duration_s": 5.0,
        "steps": 28,
        "cfg": 6.5,
        "seed": 20260,
        "motion": 0.45,
        "endpoint_ready": ready,
        "detected_provider": detected,
    }


def _frames_of(payloads: list[Any], shot_id: str) -> list[dict[str, Any]]:
    """从多路上游图像里挑出属于这一镜的关键帧（首帧/中帧/尾帧）。"""
    rows: list[dict[str, Any]] = []
    for bundle in payloads:
        for item in common.items_of(bundle):
            if str(item.get("shot_id") or "") == shot_id:
                rows.append(item)
    order = {"first": 0, "middle": 1, "last": 2}
    rows.sort(key=lambda item: (order.get(str(item.get("role") or ""), 3), str(item.get("id") or "")))
    return rows


def _real_clip(ctx: Any, model: dict[str, Any], prompt: str, first_ref: str, duration_s: float, aspect: str, seed: int, wait_s: float, poll_s: float) -> dict[str, Any]:
    """真机通道：受理 → 轮询 → 拉回本地落盘。没跑完就返回 pending，不谎报完成。"""
    image_b64 = ""
    if first_ref:
        try:
            image_b64 = base64.b64encode(ctx.read_artifact(first_ref)).decode("ascii")
        except Exception as error:  # noqa: BLE001 - 垫图读不到就退化成文生视频，但要说出来
            ctx.log(f"垫图读取失败，本镜改走文生视频：{error}", level="warn")
    submitted = media.submit_video(prompt, image_b64, duration_s, aspect, seed)
    if not submitted:
        return {"status": "unavailable"}
    remote_id = str(submitted.get("remote_id") or "")
    deadline = time.monotonic() + max(1.0, wait_s)
    while remote_id and time.monotonic() < deadline:
        state = media.poll_video(remote_id) or {}
        status = str(state.get("status") or "").lower()
        url = str(state.get("url") or "")
        if url and status in {"", "succeeded", "success", "completed", "done", "finished"}:
            blob = media.download(url)
            if not blob:
                return {"status": "failed", "remote_id": remote_id, "provider": submitted["provider"], "note": "远端给了地址但拉不回文件"}
            saved = ctx.save_file(f"clip-{common.slug(prompt[:24])}-{seed}.mp4", blob)
            return {"status": "done", "remote_id": remote_id, "provider": str(submitted["provider"]), "url": url, **saved}
        if status in {"failed", "error", "canceled"}:
            return {"status": "failed", "remote_id": remote_id, "provider": submitted["provider"], "note": f"远端状态 {status}"}
        time.sleep(max(0.5, poll_s))
    return {"status": "pending", "remote_id": remote_id, "provider": str(submitted.get("provider") or model.get("provider") or "")}


@node(
    "keyframe_artist",
    title="关键帧画师",
    dept="gen",
    role="按分镜把每一镜的起始/结束画面定下来：接了图像模型走真机出图，否则用标准库画构图示意图，供图生视频当垫图",
    inputs=[
        Port("shots", "分镜表", "shots"),
        Port("look", "视觉基调", "look"),
        Port("refs", "参考图", "images", required=False, many=True),
        Port("model", "模型配置", "model", required=False),
    ],
    outputs=[Port("images", "关键帧", "images"), Port("text", "出帧说明", "text")],
    params=[
        Param("frames", "出帧口径", "select", "首尾帧", ["首帧", "首尾帧", "首中尾"], hint="图生视频至少要首帧，首尾帧才能约束动作起止"),
        Param("limit", "最多镜头", "number", 8, minimum=1, maximum=60, step=1, unit="镜"),
        Param("width", "出图宽度", "number", 420, minimum=160, maximum=1024, step=20, unit="像素"),
        Param("pad_ref", "垫图取材", "select", "用概念图", ["不用垫图", "用概念图", "用角色卡"], hint="决定提示词里引用哪一路参考"),
        Param("motion", "起止差异幅度", "range", 0.5, minimum=0, maximum=1, step=0.05, hint="首尾帧之间的构图位移"),
    ],
    icon="🎨",
    needs_service=True,
    tags=["生成", "关键帧"],
)
def keyframe_artist(ctx: Any) -> dict[str, Any]:
    timeline = common.require_shots(ctx.in_("shots"), "关键帧画师")
    shots = common.shots_of(timeline)
    look = common.look_of(ctx.in_("look"))
    refs = ctx.many("refs")
    model = _model_of(ctx, "t2i")
    roles = {"首帧": ("first",), "首尾帧": ("first", "last"), "首中尾": ("first", "middle", "last")}[str(ctx.params.get("frames") or "首尾帧")]
    limit = int(float(ctx.params.get("limit") or 8))
    width = int(float(ctx.params.get("width") or 420))
    motion = float(ctx.params.get("motion") or 0.5)
    aspect = str(timeline.get("aspect") or look.get("aspect") or "9:16")

    pad_pool = [] if str(ctx.params.get("pad_ref") or "用概念图") == "不用垫图" else [row for bundle in refs for row in common.items_of(bundle)]
    items: list[dict[str, Any]] = []
    for shot_index, shot in enumerate(shots[:limit]):
        ctx.progress((shot_index + 1) / max(1, min(limit, len(shots))), f"关键帧 {shot_index + 1}/{min(limit, len(shots))}")
        slate = str(shot.get("slate") or shot.get("id") or f"shot-{shot_index + 1}")
        seed = int(craft.rng_for(slate, str(model.get("seed") or "")).randrange(1, 99999))
        prompt = str(shot.get("prompt") or common.shot_prompt(shot, look, [{"name": shot.get("focus_character") or ""}]))
        negative = str(shot.get("negative") or common.negative_prompt(look))
        pad = next((row for row in pad_pool if str(row.get("shot_index")) == str(shot_index) or str(row.get("shot_id") or "") == str(shot.get("id") or "")), None)
        window = _MOTION_WINDOW.get(str(shot.get("movement") or "static"), _MOTION_WINDOW["push_in"])
        for role_index, role in enumerate(roles):
            ratio = 0.0 if len(roles) == 1 else role_index / (len(roles) - 1)
            start, end = window[0], window[1]
            zoom = start[0] + (end[0] - start[0]) * ratio
            cx = start[1] + (end[1] - start[1]) * ratio
            cy = start[2] + (end[2] - start[2]) * ratio
            shift = (zoom - 1.0) * motion

            def drawer(role=role, ratio=ratio, shift=shift) -> bytes:
                return canvas.scene_frame(
                    aspect=aspect,
                    width=width,
                    look=look,
                    figure=max(0.06, float(_FIGURE_BY_SIZE.get(str(shot.get("size") or "medium"), 0.4)) - shift * 0.12),
                    horizon=min(0.86, max(0.28, 0.62 + (0.14 if role == "last" and "up" in str(shot.get("movement") or "") else -0.04 * ratio))),
                    light_side=max(0.06, min(0.94, 0.3 + 0.3 * ratio)),
                    motion=str(shot.get("movement") or ""),
                    slate=[f"{slate} {role.upper()}", str(shot.get("size_label") or shot.get("size") or ""), f"{shot.get('lens_mm', '')}MM"],
                    extra={"seed": seed},
                )

            item = common.generate_or_draw(
                ctx,
                f"{prompt}. {('起始帧' if role == 'first' else '结束帧' if role == 'last' else '中间帧')}，构图缩放 {zoom:.2f}×，机位中心 ({cx:.2f},{cy:.2f})。{negative}",
                f"key-{common.slug(slate)}-{role}.png",
                str(model.get("size") or "1024x1024"),
                drawer,
                {
                    "id": f"key-{len(items) + 1}",
                    "kind": "keyframe",
                    "role": role,
                    "shot_id": str(shot.get("id") or ""),
                    "shot_index": shot_index,
                    "slate": slate,
                    "seed": seed,
                    "prompt": prompt,
                    "negative": negative,
                    "zoom": round(zoom, 3),
                    "center": [round(cx, 3), round(cy, 3)],
                    "pad_ref": str((pad or {}).get("ref") or ""),
                },
            )
            items.append(item)

    real = [item for item in items if not item.get("simulated")]
    payload = {
        "kind": "images",
        "items": items,
        "shots": min(limit, len(shots)),
        "roles": list(roles),
        "provider": model["provider"] if real else "canvas",
        "simulated": not real,
        "total_s": float(timeline.get("total_s") or 0),
    }
    text = "\n".join(
        [
            f"关键帧：{len(items)} 张 / {payload['shots']} 镜（口径：{'/'.join(roles)}）",
            f"通道：真机 {len(real)} 张 / 示意 {len(items) - len(real)} 张（{model['provider']}）",
            *[f"· {item['slate']} {item['role']}：{item['prompt'][:44]}" for item in items[:10]],
            "示意帧是标准库画的构图占位（缩放/机位/人物占比按分镜推），接了图像模型即在同名端口换真图。",
        ]
    )
    ctx.log(f"关键帧：{len(items)} 张，真机 {len(real)} 张")
    return {"images": payload, "text": text}


@node(
    "video_generator",
    title="视频生成模型",
    dept="gen",
    role="把关键帧变成会动的镜头：i2v/t2v 提交远端任务并轮询取回；离线通道按运镜窗口生成逐帧示意片段",
    inputs=[
        Port("shots", "分镜表", "shots"),
        Port("frames", "关键帧", "images", required=False, many=True),
        Port("look", "视觉基调", "look", required=False),
        Port("model", "模型配置", "model", required=False),
    ],
    outputs=[Port("clips", "视频片段", "clips"), Port("images", "补帧关键帧", "images"), Port("text", "生成日报", "text")],
    params=[
        Param("limit", "最多镜头", "number", 8, minimum=1, maximum=60, step=1, unit="镜"),
        Param("fps", "帧率", "number", 24, minimum=8, maximum=60, step=1, unit="fps"),
        Param("frames_per_clip", "每片关键帧数", "number", 4, minimum=2, maximum=12, step=1, unit="帧"),
        Param("width", "帧宽", "number", 260, minimum=120, maximum=720, step=20, unit="像素"),
        Param("motion_mode", "运镜口径", "select", "按分镜运镜", ["按分镜运镜", "固定推进", "固定拉远", "固定平移"]),
        Param("wait_s", "远端最长等待", "number", 20, minimum=1, maximum=180, step=1, unit="秒"),
        Param("poll_s", "轮询间隔", "number", 3, minimum=0.5, maximum=15, step=0.5, unit="秒"),
        Param("fallback", "远端未完成时", "select", "出示意帧顶上", ["出示意帧顶上", "只留占位标记"], hint="示意帧会如实标注 simulated"),
    ],
    icon="🎥",
    needs_service=True,
    tags=["生成", "视频模型"],
)
def video_generator(ctx: Any) -> dict[str, Any]:
    timeline = common.require_shots(ctx.in_("shots"), "视频生成模型")
    shots = common.shots_of(timeline)
    look = common.look_of(ctx.in_("look"))
    frame_pool = ctx.many("frames")
    model = _model_of(ctx, "i2v")
    limit = int(float(ctx.params.get("limit") or 8))
    fps = int(float(ctx.params.get("fps") or 24))
    per_clip = int(float(ctx.params.get("frames_per_clip") or 4))
    width = int(float(ctx.params.get("width") or 260))
    aspect = str(timeline.get("aspect") or look.get("aspect") or "9:16")
    max_clip = float(model.get("duration_s") or 6)
    forced = {
        "固定推进": "push_in",
        "固定拉远": "pull_out",
        "固定平移": "pan",
    }.get(str(ctx.params.get("motion_mode") or "按分镜运镜"), "")

    clips: list[dict[str, Any]] = []
    drawn: list[dict[str, Any]] = []
    pending: list[str] = []
    unavailable = 0
    for shot_index, shot in enumerate(shots[:limit]):
        ctx.progress((shot_index + 1) / max(1, min(limit, len(shots))), f"镜头生成 {shot_index + 1}/{min(limit, len(shots))}")
        slate = str(shot.get("slate") or shot.get("id") or f"shot-{shot_index + 1}")
        duration = min(max_clip, max(0.8, float(shot.get("duration_s") or 2)))
        movement = forced or str(shot.get("movement") or "static")
        keys = _frames_of(frame_pool, str(shot.get("id") or ""))
        first = keys[0] if keys else None
        if first is None:
            # 没接关键帧也要能出片：本节点自己补一帧首帧，并说明是补的
            def drawer(shot=shot, slate=slate) -> bytes:
                return canvas.scene_frame(
                    aspect=aspect,
                    width=width,
                    look=look,
                    figure=_FIGURE_BY_SIZE.get(str(shot.get("size") or "medium"), 0.4),
                    motion=movement,
                    slate=[slate, str(shot.get("size_label") or "")],
                    extra={"seed": slate},
                )

            first = common.generate_or_draw(
                ctx,
                str(shot.get("prompt") or ""),
                f"gen-{common.slug(slate)}-auto.png",
                str(model.get("size") or "1024x1024"),
                drawer,
                {"id": f"auto-{len(drawn) + 1}", "kind": "keyframe", "role": "first", "shot_id": str(shot.get("id") or ""), "shot_index": shot_index, "slate": slate, "seed": shot_index},
            )
            drawn.append(first)
        seed = int(first.get("seed") or craft.rng_for(slate).randrange(1, 99999))

        remote: dict[str, Any] = {"status": "unavailable"}
        if model.get("endpoint_ready"):
            remote = _real_clip(ctx, model, str(shot.get("prompt") or ""), str(first.get("ref") or ""), duration, aspect, seed, float(ctx.params.get("wait_s") or 20), float(ctx.params.get("poll_s") or 3))
            if remote["status"] == "unavailable":
                unavailable += 1

        window = _MOTION_WINDOW.get(movement, _MOTION_WINDOW["push_in"])
        frames: list[dict[str, Any]] = []
        if remote["status"] != "done":
            try:
                base = canvas.read_png(ctx.read_artifact(str(first.get("ref") or "")))
            except Exception as error:  # noqa: BLE001 - 上游帧读不回就当场重画一帧
                ctx.log(f"{slate} 关键帧读不回，本镜重画底帧：{error}", level="warn")
                base = canvas.scene_raster(aspect=aspect, width=width, look=look, figure=_FIGURE_BY_SIZE.get(str(shot.get("size") or "medium"), 0.4), motion=movement, slate=[slate], extra={"seed": seed})
            for step in range(max(2, per_clip)):
                ratio = step / max(1, per_clip - 1)
                start, end = window[0], window[1]
                zoom = start[0] + (end[0] - start[0]) * ratio
                cx = start[1] + (end[1] - start[1]) * ratio
                cy = start[2] + (end[2] - start[2]) * ratio
                saved = ctx.save_png(f"{common.slug(slate)}-f{step + 1}.png", base.frame_zoom(zoom, cx, cy).to_png())
                frames.append({"url": saved["url"], "ref": saved["ref"], "t": round(ratio * duration, 3), "zoom": round(zoom, 3)})

        clip = {
            "id": f"clip-{len(clips) + 1}",
            "kind": "clip",
            "shot_id": str(shot.get("id") or ""),
            "shot_index": shot_index,
            "slate": slate,
            "duration_s": round(duration, 3),
            "fps": fps,
            "aspect": aspect,
            "motion": movement,
            "motion_label": craft.CAMERA_MOVEMENTS.get(movement, {}).get("label", movement),
            "poster_ref": str(first.get("ref") or ""),
            "poster_url": str(first.get("url") or ""),
            "frames": frames,
            "prompt": str(shot.get("prompt") or ""),
            "seed": seed,
        }
        if remote["status"] == "done":
            clip.update({"clip_ref": remote["ref"], "clip_url": remote["url"], "remote_id": remote["remote_id"], "provider": remote["provider"], "simulated": False, "status": "done"})
        elif remote["status"] == "pending":
            pending.append(slate)
            clip.update({"remote_id": remote.get("remote_id", ""), "provider": remote.get("provider", ""), "status": "pending", "simulated": True, "note": "远端未出片，本条为示意帧"})
        elif remote["status"] == "failed":
            pending.append(slate)
            clip.update({"provider": remote.get("provider", ""), "status": "failed", "simulated": True, "note": remote.get("note", "远端失败")})
        else:
            clip.update({"provider": "canvas", "status": "offline", "simulated": True})
        if str(ctx.params.get("fallback") or "出示意帧顶上") == "只留占位标记" and clip["simulated"]:
            clip["frames"] = []
        clips.append(clip)

    real = [clip for clip in clips if not clip.get("simulated")]
    total = round(sum(float(clip["duration_s"]) for clip in clips), 2)
    payload = {
        "kind": "clips",
        "items": clips,
        "total_s": total,
        "fps": fps,
        "aspect": aspect,
        "provider": model["provider"] if real else "canvas",
        "simulated": not real,
    }
    lines = [
        f"生成日报：{len(clips)} 段 / {total}s（口径：{'/'.join(sorted({str(clip['motion_label']) for clip in clips}))}）",
        f"通道：真机 {len(real)} 段 / 示意 {len(clips) - len(real)} 段（模型 {model['provider']}，端点{'已配置' if model.get('endpoint_ready') else '未配置'}）",
        *[f"· {clip['slate']} {clip['duration_s']}s {clip['motion_label']}：{len(clip['frames']) or '远端'}帧 ← {clip['prompt'][:34]}" for clip in clips[:12]],
        f"⚠ {len(pending)} 镜挂在远端队列或失败：{'、'.join(pending[:6])}——重跑本节点即续查，不会当成已完成。" if pending else "",
        f"有 {unavailable} 镜端点受理失败，按未配置处理。" if unavailable else "",
        "示意段是逐帧 Ken Burns，前端按 fps 播；接了真模型即在同名端口替换为远端视频。",
    ]
    text = "\n".join(line for line in lines if line)
    ctx.log(f"视频生成：{len(clips)} 段，真机 {len(real)} 段，待查 {len(pending)} 段")
    return {"clips": payload, "images": {"kind": "images", "items": drawn, "provider": "canvas", "simulated": True}, "text": text}


@node(
    "avatar_actor",
    title="数字人演员",
    dept="gen",
    role="让虚拟主播把台词说出来：按口播音节的口型曲线逐帧驱动胸像，长度对齐配音轨",
    inputs=[
        Port("shots", "分镜表", "shots"),
        Port("characters", "角色表", "characters"),
        Port("audio", "人声轨", "audio", required=False),
        Port("look", "视觉基调", "look", required=False),
        Port("model", "模型配置", "model", required=False),
        Port("direction", "表演注释", "plan", required=False),
    ],
    outputs=[Port("clips", "数字人片段", "clips"), Port("text", "出镜说明", "text")],
    params=[
        Param("limit", "最多镜头", "number", 6, minimum=1, maximum=40, step=1, unit="镜"),
        Param("fps", "帧率", "number", 12, minimum=6, maximum=30, step=1, unit="fps"),
        Param("role", "出镜范围", "select", "有台词的镜头", ["有台词的镜头", "只出主角", "全部镜头"], hint="数字人一般只承担口播镜"),
        Param("width", "帧宽", "number", 260, minimum=120, maximum=640, step=20, unit="像素"),
        Param("smile", "表情基线", "range", 0.3, minimum=0, maximum=1, step=0.05, hint="闭口时的嘴角高度；接了表演指导则按逐镜口径覆盖"),
        Param("sway", "身体摆动", "range", 0.4, minimum=0, maximum=1, step=0.05, hint="说话时的重心移动幅度"),
        Param("follow_direction", "跟表演注释", "toggle", True, hint="关掉则无视表演指导的逐镜表情口径"),
    ],
    icon="🧍",
    needs_service=True,
    tags=["生成", "数字人"],
)
def avatar_actor(ctx: Any) -> dict[str, Any]:
    timeline = common.require_shots(ctx.in_("shots"), "数字人演员")
    shots = common.shots_of(timeline)
    roster = common.characters_of(ctx.in_("characters"))
    if not roster:
        raise NodeError("数字人演员需要角色表：先接「选角导演」或「角色造型设计」")
    look = common.look_of(ctx.in_("look"))
    voice_items = [item for item in common.items_of(ctx.in_("audio")) if str(item.get("kind") or "") == "voice"]
    model = _model_of(ctx, "dh")
    limit = int(float(ctx.params.get("limit") or 6))
    fps = int(float(ctx.params.get("fps") or 12))
    width = int(float(ctx.params.get("width") or 260))
    smile = float(ctx.params.get("smile") or 0.3)
    sway_gain = float(ctx.params.get("sway") or 0.4)
    aspect = str(timeline.get("aspect") or look.get("aspect") or "9:16")
    scope = str(ctx.params.get("role") or "有台词的镜头")
    follow = bool(ctx.params.get("follow_direction"))
    by_slate: dict[str, dict[str, Any]] = {}
    for row in common.items_of(ctx.in_("direction")):
        key = str(row.get("slate") or "")
        if key:
            by_slate.setdefault(key, row)

    clips: list[dict[str, Any]] = []
    for shot_index, shot in enumerate(shots):
        if scope == "有台词的镜头" and not (shot.get("dialogue") or []):
            continue
        if len(clips) >= limit:
            break
        speaker = str(shot.get("speaker") or (shot.get("dialogue") or [{}])[0].get("character") or "")
        character = next((item for item in roster if str(item.get("name")) == speaker), None)
        if scope == "只出主角" and str((character or roster[0]).get("role_type") or "") != "protagonist":
            continue
        character = character or roster[0]
        ctx.progress(min(1.0, (len(clips) + 1) / limit), f"数字人出镜 {len(clips) + 1}/{limit}")
        who = str(character.get("name") or speaker)
        line = " ".join(str(row.get("line") or "") for row in (shot.get("dialogue") or []))
        audio_row = next((item for item in voice_items if str(item.get("text") or "") and str(item.get("text") or "") in line), None)
        duration = float(audio_row["duration_s"]) if audio_row else min(9.0, max(1.2, float(shot.get("duration_s") or 2.4)))
        slate = str(shot.get("slate") or shot.get("id") or f"shot-{shot_index + 1}")
        seed = int(craft.rng_for(slate, who).randrange(1, 9973))
        rng = craft.rng_for(slate, line[:24])
        syllables = max(2, int(round(duration * 4.6))) if line else max(2, int(round(duration * 2.2)))
        steps = max(2, min(18, int(round(duration * fps))))

        note = (by_slate.get(slate) or {}) if follow else {}
        shot_smile = round(smile * 0.4 + float(note["smile"]) * 0.6, 3) if note.get("smile") is not None else smile
        shot_sway = round(sway_gain * 0.4 + float(note["sway"]) * 0.6, 3) if note.get("sway") is not None else sway_gain
        acting = f"〔{note['beat']}·{note['speech_act']}〕{note['intent']}｜潜台词：{note['subtext']}｜重音：{'、'.join(note.get('emphasis') or [])}" if note else ""

        mouth_curve: list[float] = []
        for step in range(steps):
            phase = step / max(1, steps - 1)
            hit = syllables * phase
            if hit >= syllables:
                mouth_curve.append(round(shot_smile * 0.6, 3))
                continue
            openness = shot_smile * 0.5 + (0.35 + 0.45 * abs(math.sin(hit * math.pi))) * (0.55 + 0.45 * rng.random())
            mouth_curve.append(round(max(0.05, min(1.0, openness)), 3))

        remote: dict[str, Any] = {"status": "unavailable"}
        if model.get("endpoint_ready"):
            remote = _real_clip(
                ctx,
                model,
                f"数字人 {character.get('name')} 口播：{line[:60]}，正面胸像，口型与音节同步，背景 {look.get('style_label') or '棚内'}"
                + (f"；表演：{acting}；{note.get('posture')}；表情：{note.get('face')}" if note else ""),
                "",
                duration,
                aspect,
                seed,
                20.0,
                3.0,
            )

        frames: list[dict[str, Any]] = []
        if remote["status"] != "done":
            palette = character.get("palette")
            avatar_look = {
                **look,
                "skin": str(character.get("skin") or ""),
                "hair": str(character.get("hair") or (palette[0] if isinstance(palette, list) and palette else "")),
            }
            for step, openness in enumerate(mouth_curve):
                phase = step / max(1, len(mouth_curve) - 1)
                saved = ctx.save_png(
                    f"avatar-{common.slug(slate)}-{step + 1}.png",
                    canvas.avatar_frame(
                        name=str(character.get("name") or ""),
                        look=avatar_look,
                        mouth=openness,
                        sway=round(math.sin(phase * math.pi * 1.5) * shot_sway, 3),
                        aspect=aspect,
                        width=width,
                        caption=f"{phase * duration:.1f}s",
                        slate=[slate, f"MOUTH {openness:.2f}"] + ([note["beat"]] if note else []),
                    ),
                )
                frames.append({"url": saved["url"], "ref": saved["ref"], "t": round(phase * duration, 3), "mouth": openness})

        clip = {
            "id": f"avatar-{len(clips) + 1}",
            "kind": "clip",
            "shot_id": str(shot.get("id") or ""),
            "shot_index": shot_index,
            "slate": slate,
            "duration_s": round(duration, 3),
            "fps": fps,
            "aspect": aspect,
            "motion": "static",
            "motion_label": "固定机位",
            "frames": frames,
            "poster_ref": str((frames[0].get("ref") if frames else "")),
            "poster_url": str((frames[0].get("url") if frames else "")),
            "avatar": {
                "character": str(character.get("name") or who),
                "voice": str(character.get("voice") or ""),
                "line": line[:120],
                "syllables": syllables,
                "lip_sync": "音节曲线（audio.voice 同一口型口径）" if not audio_row else "对齐配音轨长度",
                "smile": shot_smile,
                "sway": shot_sway,
                "direction": acting,
                "face": str(note.get("face") or ""),
            },
            "prompt": f"数字人 {character.get('name')}：{line[:60]}" + (f"｜{acting}" if acting else ""),
            "seed": seed,
        }
        if remote["status"] == "done":
            clip.update({"clip_ref": remote["ref"], "clip_url": remote["url"], "remote_id": remote["remote_id"], "provider": remote["provider"], "simulated": False, "status": "done"})
        else:
            clip.update({"provider": "canvas", "status": remote["status"], "simulated": True})
        clips.append(clip)

    real = [clip for clip in clips if not clip.get("simulated")]
    total = round(sum(float(clip["duration_s"]) for clip in clips), 2)
    payload = {
        "kind": "clips",
        "items": clips,
        "total_s": total,
        "fps": fps,
        "aspect": aspect,
        "provider": "canvas" if not real else str(model["provider"]),
        "simulated": not real,
    }
    text = "\n".join(
        [
            f"数字人出镜：{len(clips)} 段 / {total}s，口径：{scope}",
            f"通道：真机 {len(real)} 段 / 逐帧示意 {len(clips) - len(real)} 段（数字人端点{'已配置' if model.get('endpoint_ready') else '未配置'}）",
            *[f"· {clip['slate']} {clip['avatar']['character']}：{clip['avatar']['line'][:30]}（{clip['duration_s']}s / {len(clip['frames'])} 帧，{clip['avatar']['lip_sync']}）" for clip in clips[:10]],
            (f"表演口径：{len([clip for clip in clips if clip['avatar']['direction']])} 段按表演指导的逐镜注释改过嘴角与重心" if by_slate and follow else "未接表演指导（或已关掉跟表演注释）：表情与重心走节点参数基线"),
            "逐帧口型按台词音节数铺开，长度对齐配音轨；接了数字人服务商即在同名端口替换为口播视频。",
        ]
    )
    ctx.log(f"数字人：{len(clips)} 段，真机 {len(real)} 段")
    return {"clips": payload, "text": text}
