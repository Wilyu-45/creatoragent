"""声音部门岗位：配音导演、配音演员、音效师、配乐、同期录音师、拟音师、对白补录监督、混音师。

声音是「离线也能真出声」的部门：本模块用标准库合成 WAV，
配置了 TTS 通道时逐句换成真配音，混音师把多轨压回一条时间线并给出可核对的响度。
现场（同期录音）与后期（拟音、补录）也各有岗位：录音师按场景给话筒方案与噪声底采样，
拟音师按动作逐镜铺脚步/衣料/道具层，补录监督拿现场底噪与配音工单判定哪几句必须重录。
"""

from __future__ import annotations

from typing import Any, Sequence

from .. import audio as audio_lib
from .. import canvas, craft
from ..spec import NodeError, Param, Port, node
from . import common

#: 节拍 → 该放的音效（音效师的常规直觉：转场要呼啸、高潮要撞击、CTA 要点击）
_SFX_BY_BEAT: dict[str, list[str]] = {
    "hook": ["impact", "riser"],
    "setup": ["pop"],
    "develop": ["click"],
    "tension": ["riser"],
    "payoff": ["impact", "chime"],
    "cta": ["click", "pop"],
    "ending": ["chime"],
    "chapter": ["paper"],
}

#: 场景时段 → 环境底噪（扫频区间、相对电平、口径说明）
_AMBIENT_BY_TIME: dict[str, tuple[tuple[float, float], float, str]] = {
    "dawn": ((700.0, 380.0), 0.22, "清晨低位与鸟隙"),
    "day": ((5200.0, 3400.0), 0.20, "白天街景与人声底"),
    "dusk": ((1400.0, 700.0), 0.22, "黄昏风声与远车"),
    "night": ((420.0, 180.0), 0.26, "夜间低频隆隆"),
    "indoors": ((300.0, 160.0), 0.22, "房间底噪（空调/冰箱）"),
    "studio": ((150.0, 90.0), 0.18, "棚内静音底"),
}


def _voice_preset_for(character: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    key = str(character.get("voice") or "")
    if key not in craft.VOICE_PRESETS:
        key = craft.ROLE_VOICE.get(str(character.get("role_type") or ""), "narrator_female")
    return key, craft.VOICE_PRESETS[key]


@node(
    "voice_director",
    title="配音导演",
    dept="sound",
    role="定声：给每个角色挑音色与语速，逐句核对台词时长与镜头时长，出录音工单",
    inputs=[
        Port("characters", "角色表", "characters"),
        Port("shots", "分镜表", "shots"),
        Port("brief", "项目需求", "brief", required=False),
    ],
    outputs=[Port("plan", "配音工单", "plan"), Port("text", "录音说明", "text")],
    params=[
        Param("rate", "整体语速", "number", 1.0, minimum=0.7, maximum=1.4, step=0.05, unit="倍", hint="乘在每个人预设语速上"),
        Param("head_gap", "入话留白", "number", 0.15, minimum=0.0, maximum=0.6, step=0.05, unit="秒", hint="镜头进来后多久开口"),
        Param("budget", "台词占镜头比例上限", "number", 0.85, minimum=0.5, maximum=1.0, step=0.05, hint="超过就会顶到转场，必须改词或延长镜头"),
        Param("vo_tag", "补旁白（VO）", "toggle", True, hint="无台词场次标一条 VO 任务给配音演员"),
    ],
    icon="🎙️",
    tags=["声音", "选角"],
)
def voice_director(ctx: Any) -> dict[str, Any]:
    roster = common.characters_of(ctx.in_("characters"))
    timeline = common.require_shots(ctx.in_("shots"), "配音导演")
    shots = common.shots_of(timeline)
    if not roster:
        raise NodeError("配音导演需要角色表：先接「选角导演」或「角色造型设计」")
    rate = float(ctx.params.get("rate") or 1.0)
    head_gap = float(ctx.params.get("head_gap") or 0.15)
    budget = float(ctx.params.get("budget") or 0.85)

    sheet: list[dict[str, Any]] = []
    free_at = 0.0  # 全局可用人声时刻：两句话压在一起是播出事故，不是风格
    for shot in shots:
        lines = shot.get("dialogue") or []
        span = float(shot.get("duration_s") or 0)
        shot_start = float(shot.get("start_s") or 0.0)
        cursor = max(head_gap, free_at - shot_start)
        for row in lines:
            who = str(row.get("character") or shot.get("speaker") or "旁白")
            text = str(row.get("line") or "")
            character = next((item for item in roster if str(item.get("name")) == who), None)
            key, preset = _voice_preset_for(character or {"role_type": "narrator"})
            est = craft.speech_seconds(text, float(preset["rate"]) * rate)
            start = round(shot_start + cursor, 3)
            sheet.append(
                {
                    "shot_id": shot.get("id"),
                    "slate": shot.get("slate"),
                    "start_s": start,
                    "shifted": round(cursor - head_gap, 2),
                    "character": who,
                    "voice": key,
                    "voice_label": preset["label"],
                    "rate": round(float(preset["rate"]) * rate, 2),
                    "text": text,
                    "est_s": round(est, 2),
                    "over": round(est - max(0.2, span - cursor), 2),
                    "fit": est <= max(0.2, span - cursor) * budget,
                }
            )
            free_at = start + est + 0.08
            cursor += est + 0.12
        if not lines and bool(ctx.params.get("vo_tag")):
            vo_key, vo_preset = _voice_preset_for({"role_type": "narrator"})
            vo_rate = round(float(vo_preset["rate"]) * rate, 2)
            vo_text = f"（VO 备选）{str(shot.get('description') or '')[:40]}"
            sheet.append(
                {
                    "shot_id": shot.get("id"),
                    "slate": shot.get("slate"),
                    "start_s": round(shot_start + cursor, 3),
                    "shifted": round(cursor - head_gap, 2),
                    "character": "旁白",
                    "voice": vo_key,
                    "voice_label": vo_preset["label"],
                    "rate": vo_rate,
                    "text": vo_text,
                    "est_s": round(craft.speech_seconds(vo_text, vo_rate), 2),
                    "over": 0.0,
                    "fit": True,
                    "vo": True,
                }
            )

    over = [row for row in sheet if not row["fit"] and not row.get("vo")]
    spoken = [row for row in sheet if not row.get("vo")]
    total_s = round(sum(float(row["est_s"]) for row in spoken), 2)
    plan = {
        "dept": "sound",
        "role": "配音导演",
        "items": sheet,
        "cast": [
            {"character": str(item.get("name")), "voice": _voice_preset_for(item)[0], "voice_label": _voice_preset_for(item)[1]["label"], "lines": sum(1 for row in spoken if row["character"] == str(item.get("name")))}
            for item in roster
        ],
        "lines": len(spoken),
        "spoken_s": total_s,
        "moved": sum(1 for row in spoken if float(row["shifted"]) > 0.05),
        "studio_hours": round(max(0.5, total_s / 60.0 * 6.0 + len({row["character"] for row in spoken}) * 0.25), 2),
        "risks": [f"{row['slate']} {row['character']}：{row['text'][:16]}… 超 {row['over']}s，需改词或延长镜头" for row in over[:8]],
        "simulated": True,
    }
    text = "\n".join(
        [
            f"配音工单｜{len(spoken)} 句口播 / {len({row['character'] for row in spoken})} 个声线｜总口播时长 {total_s}s｜录音棚时 {plan['studio_hours']}h",
            *[f"· {row['slate']}〔{row['voice_label']}〕{row['character']}：{row['text'][:28]}（{row['est_s']}s @{row['start_s']}s）{'⚠超时' if not row['fit'] else ''}" for row in sheet[:16]],
            (f"超时 {len(over)} 句，需改词或延长镜头：" + "；".join(f"{row['slate']} {row['character']}(+{row['over']}s)" for row in over[:4])) if over else "台词时长全部落在镜头预算内",
            (f"为避免压话，{plan['moved']} 句顺延到上一句说完之后进（J-cut 落位）" if plan["moved"] else "各句按镜头入话留白落位，无顺延"),
        ]
    )
    ctx.log(f"配音工单：{len(spoken)} 句，超时 {len(over)} 句")
    return {"plan": plan, "text": text}


@node(
    "dubber",
    title="配音演员",
    dept="sound",
    role="逐句把台词变成音频：配置 TTS 走真配音，否则出共振峰示意人声，按镜头时间线对齐偏移",
    inputs=[
        Port("shots", "分镜表", "shots"),
        Port("characters", "角色表", "characters", required=False),
        Port("work", "配音工单", "plan", required=False),
    ],
    outputs=[Port("audio", "人声轨", "audio"), Port("subtitles", "台词字幕", "subtitles"), Port("text", "配音说明", "text")],
    params=[
        Param("gain", "人声增益", "number", 0.9, minimum=0.2, maximum=1.6, step=0.05, unit="倍"),
        Param("head_gap", "入话留白", "number", 0.15, minimum=0.0, maximum=0.6, step=0.05, unit="秒"),
        Param("mode", "录制口径", "select", "按句录制", ["按句录制", "按场拼轨", "只录主角"], hint="按句便于后期挪位置，按场更接近真实气息"),
        Param("max_lines", "最多句数", "number", 24, minimum=1, maximum=120, step=1, unit="句"),
    ],
    icon="🗣️",
    tags=["声音", "录制"],
)
def dubber(ctx: Any) -> dict[str, Any]:
    timeline = common.require_shots(ctx.in_("shots"), "配音演员")
    shots = common.shots_of(timeline)
    roster = common.characters_of(ctx.in_("characters"))
    work = common.items_of(ctx.in_("work"))
    gain = float(ctx.params.get("gain") or 0.9)
    head_gap = float(ctx.params.get("head_gap") or 0.15)
    mode = str(ctx.params.get("mode") or "按句录制")
    limit = int(float(ctx.params.get("max_lines") or 24))

    cues: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    by_key = {(str(row.get("shot_id")), str(row.get("text"))): row for row in work}
    by_text: dict[str, dict[str, Any]] = {}
    for row in work:
        by_text.setdefault(str(row.get("text")), row)
    free_at = 0.0  # 没有工单时自己排队：人声不能叠在一起
    for shot in shots:
        shot_start = float(shot.get("start_s") or 0.0)
        for row in (shot.get("dialogue") or []):
            who = str(row.get("character") or shot.get("speaker") or "旁白")
            text = str(row.get("line") or "").strip()
            if not text or len(items) >= limit:
                continue
            character = next((item for item in roster if str(item.get("name")) == who), None)
            key, preset = _voice_preset_for(character or {"role_type": "narrator"})
            if mode == "只录主角" and str((character or {}).get("role_type") or "") != "protagonist":
                continue
            cue = by_key.get((str(shot.get("id")), text)) or by_text.get(text)
            # 语速按配音导演的工单来：演员录的是工单上那个速度
            if cue:
                preset = {**preset, "rate": float(cue.get("rate") or preset["rate"])}
                start = float(cue.get("start_s"))
            else:
                start = max(round(shot_start + head_gap, 3), round(free_at + 0.08, 3))
                free_at = start
            saved = common.voice_bytes_or_sample(ctx, text, preset, f"voice-{common.slug(str(shot.get('slate') or shot['id']))}-{len(items) + 1}.wav")
            if not cue:
                free_at = start + float(saved["duration_s"])
            items.append(
                {
                    "kind": "voice",
                    "id": f"voice-{len(items) + 1}",
                    "label": f"{who}：{text[:18]}",
                    "character": who,
                    "voice": key,
                    "offset": round(start, 3),
                    "gain": gain,
                    "sidechain": 1.0,
                    "text": text,
                    **saved,
                }
            )
            cues.append({"start": round(start, 3), "end": round(start + float(saved["duration_s"]), 3), "text": text, "character": who})
    cues.sort(key=lambda item: item["start"])

    real = [item for item in items if not item.get("simulated")]
    total = float(timeline.get("total_s") or 0)
    payload = {
        "kind": "voice",
        "items": items,
        "cues": cues,
        "total_s": total,
        "gain": gain,
        "provider": items[0]["provider"] if items else "sample",
        "simulated": not real,
        "overlaps": sum(1 for index in range(1, len(cues)) if cues[index]["start"] < cues[index - 1]["end"]),
    }
    text = "\n".join(
        [
            f"人声轨：{len(items)} 句（真配音 {len(real)} / 示意 {len(items) - len(real)}），口径：{mode}",
            f"覆盖时长 {round(sum(float(item['duration_s']) for item in items), 2)}s / 成片 {total}s",
            ("⚠ 有 " + str(payload["overlaps"]) + " 处台词重叠，需配音导演挪点或改词") if payload["overlaps"] else "台词之间无重叠",
            "偏移按配音导演工单落位；未接工单时按镜头起点 + 留白。" if not work else "已按配音工单的时间点落位。",
        ]
    )
    ctx.log(f"人声录制：{len(items)} 句，真配音 {len(real)} 句")
    return {"audio": payload, "subtitles": {"cues": cues, "style": "dialogue"}, "text": text}


@node(
    "sfx_designer",
    title="音效师",
    dept="sound",
    role="按节拍与转场铺音效：转场呼啸、高潮撞击、CTA 点击、环境底噪，逐条给时间点与电平",
    inputs=[
        Port("shots", "分镜表", "shots"),
        Port("camera_plan", "摄影方案", "camera_plan", required=False),
        Port("look", "视觉基调", "look", required=False),
    ],
    outputs=[Port("audio", "音效轨", "audio"), Port("text", "音效清单", "text")],
    params=[
        Param("gain", "音效增益", "number", 0.55, minimum=0.1, maximum=1.2, step=0.05, unit="倍"),
        Param("transition_sfx", "转场加呼啸", "toggle", True),
        Param("ambient", "铺环境底噪", "select", "每场景一次", ["不铺", "每场景一次", "每镜跟随"], hint="底噪长铺一条，别每镜都放"),
        Param("limit", "最多音效条数", "number", 40, minimum=4, maximum=160, step=1, unit="条"),
        Param("tail_gap", "转场提前量", "number", 0.25, minimum=0.0, maximum=1.0, step=0.05, unit="秒", hint="呼啸要比切点早进，听感才连得上"),
    ],
    icon="🔊",
    tags=["声音", "音效"],
)
def sfx_designer(ctx: Any) -> dict[str, Any]:
    timeline = common.require_shots(ctx.in_("shots"), "音效师")
    shots = common.shots_of(timeline)
    gain = float(ctx.params.get("gain") or 0.55)
    limit = int(float(ctx.params.get("limit") or 40))
    head = float(ctx.params.get("tail_gap") or 0.25)
    ambient_mode = str(ctx.params.get("ambient") or "每场景一次")

    cues: list[tuple[float, str, float, str]] = []
    for shot in shots:
        start = float(shot.get("start_s") or 0.0)
        duration = float(shot.get("duration_s") or 1.0)
        beat = str(shot.get("beat") or "")
        transition = str(shot.get("transition") or "cut")
        for kind in _SFX_BY_BEAT.get(beat, [])[:2]:
            cues.append((start + 0.05, kind, 1.0, f"{shot.get('slate')} 节拍 {beat}"))
        if transition != "cut" and bool(ctx.params.get("transition_sfx")):
            cues.append((max(0.0, start + duration - float(shot.get("transition_duration") or 0.4) - head), "whoosh", 0.9, f"切入 {shot.get('transition_label') or transition}"))
        if str(shot.get("movement")) in {"whip", "handheld"}:
            cues.append((start + duration * 0.35, "footstep", 0.5, f"{shot.get('movement_label')} 运动质感"))

    beds: list[dict[str, Any]] = []
    if ambient_mode != "不铺":
        spans: dict[str, list[float]] = {}
        tod_of: dict[str, str] = {}
        name_of: dict[str, str] = {}
        for shot in shots:
            key = str(shot.get("scene_id") or shot.get("location") or "scene")
            start = float(shot.get("start_s") or 0.0)
            end = start + float(shot.get("duration_s") or 1.0)
            if key not in spans:
                spans[key] = [start, end]
                tod_of[key] = str(shot.get("time_of_day") or "day")
                name_of[key] = str(shot.get("location") or key)
            spans[key][0] = min(spans[key][0], start)
            spans[key][1] = max(spans[key][1], end)
            if ambient_mode == "每镜跟随":
                beds.append({"start": start, "end": end, "tod": str(shot.get("time_of_day") or "day"), "where": name_of[key]})
        if ambient_mode != "每镜跟随":
            for key in spans:
                beds.append({"start": spans[key][0], "end": spans[key][1], "tod": tod_of[key], "where": name_of[key]})

    cues.sort(key=lambda item: item[0])
    items: list[dict[str, Any]] = []
    for offset, kind, weight, reason in cues[:limit]:
        spec = craft.SFX_LIBRARY.get(kind)
        if spec is None:
            continue
        samples = audio_lib.sfx(kind, gain=round(gain * weight, 3), seed=int(offset * 1000) % 9973 + 1)
        saved = ctx.save_wav(f"sfx-{kind}-{len(items) + 1}.wav", audio_lib.wav_bytes(samples))
        items.append(
            {
                "kind": "sfx",
                "id": f"sfx-{len(items) + 1}",
                "label": f"{spec['label']}",
                "sfx": kind,
                "offset": round(offset, 3),
                "gain": round(weight, 2),
                "sidechain": 0.6,
                "reason": reason,
                "duration_s": round(audio_lib.seconds_of(samples), 3),
                "provider": "sample",
                "simulated": True,
                **saved,
            }
        )

    for bed in beds:
        sweep, weight, label = _AMBIENT_BY_TIME.get(str(bed["tod"]), _AMBIENT_BY_TIME["day"])
        length = max(1.0, min(audio_lib.MAX_SECONDS, float(bed["end"]) - float(bed["start"])))
        samples = audio_lib.noise_burst(length, sweep, round(gain * weight, 3), "flat", seed=int(float(bed["start"]) * 1000) % 9973 + 5)
        saved = ctx.save_wav(f"amb-{len(items) + 1}.wav", audio_lib.wav_bytes(samples))
        items.append(
            {
                "kind": "sfx",
                "id": f"sfx-{len(items) + 1}",
                "label": f"底噪｜{label}",
                "sfx": "ambient",
                "offset": round(float(bed["start"]), 3),
                "gain": round(weight, 2),
                "sidechain": 0.35,
                "reason": f"{bed['where']}（{bed['tod']}）铺 {round(length, 1)}s",
                "duration_s": round(audio_lib.seconds_of(samples), 3),
                "provider": "sample",
                "simulated": True,
                **saved,
            }
        )
    items.sort(key=lambda item: float(item["offset"]))

    payload = {
        "kind": "sfx",
        "items": items,
        "total_s": float(timeline.get("total_s") or 0),
        "layers": len({str(item["sfx"]) for item in items}),
        "simulated": True,
    }
    text = "\n".join(
        [
            f"音效清单：{len(items)} 条 / {payload['layers']} 类音色（增益基准 {gain}）",
            *[f"· {item['offset']}s {item['label']}（{item['reason']}）" for item in items[:18]],
            "全部离线合成，无版权风险；接真音效库时在同名 cue 上替换 ref 即可。",
        ]
    )
    ctx.log(f"音效：{len(items)} 条 / {payload['layers']} 类")
    return {"audio": payload, "text": text}


@node(
    "composer",
    title="配乐",
    dept="sound",
    role="按幕写音乐：调 BPM/调式/和弦进行生成配乐段，与镜头节奏和情绪曲线对齐",
    inputs=[
        Port("shots", "分镜表", "shots"),
        Port("look", "视觉基调", "look", required=False),
        Port("brief", "项目需求", "brief", required=False),
    ],
    outputs=[Port("audio", "音乐轨", "audio"), Port("text", "配乐说明", "text")],
    params=[
        Param("mood", "音乐情绪", "select", "auto", ["auto", "uplift", "warm", "tense", "chill", "epic", "playful", "melancholy"], hint="auto 按各幕情绪自动切换"),
        Param("gain", "配乐增益", "number", 0.34, minimum=0.05, maximum=1.0, step=0.01, unit="倍", hint="口播下压到 -10dB 左右"),
        Param("segment", "分段口径", "select", "按幕", ["整片一段", "按幕", "按节拍"], hint="分段越细，情绪贴得越紧"),
        Param("duck", "给人声让路", "number", 0.5, minimum=0.2, maximum=1.0, step=0.05, hint="闪避深度：越小压得越狠"),
        Param("percussion", "打击密度", "select", "中", ["无", "轻", "中", "重"]),
    ],
    icon="🎵",
    tags=["声音", "音乐"],
)
def composer(ctx: Any) -> dict[str, Any]:
    timeline = common.require_shots(ctx.in_("shots"), "配乐")
    shots = common.shots_of(timeline)
    look = common.look_of(ctx.in_("look"))
    total = float(timeline.get("total_s") or 0)
    if total <= 0:
        raise NodeError("配乐需要分镜表总时长：先接「分镜师」")
    gain = float(ctx.params.get("gain") or 0.34)
    sidechain = float(ctx.params.get("duck") or 0.5)
    segment = str(ctx.params.get("segment") or "按幕")
    fixed_mood = str(ctx.params.get("mood") or "auto")

    groups: list[list[dict[str, Any]]] = []
    if segment == "整片一段":
        groups = [shots]
    elif segment == "按幕":
        order: list[str] = []
        buckets: dict[str, list[dict[str, Any]]] = {}
        for shot in shots:
            act = str(shot.get("act") or craft.act_of(0.5))
            if act not in buckets:
                buckets[act] = []
                order.append(act)
            buckets[act].append(shot)
        groups = [buckets[act] for act in order]
    else:
        for shot in shots:
            groups.append([shot])

    items: list[dict[str, Any]] = []
    for group in groups:
        if not group:
            continue
        start = float(group[0].get("start_s") or 0.0)
        end = float(group[-1].get("start_s") or 0.0) + float(group[-1].get("duration_s") or 1.0)
        length = max(1.0, min(audio_lib.MAX_SECONDS, end - start))
        beat = str(group[0].get("beat") or "")
        mood = fixed_mood if fixed_mood != "auto" else common.mood_of(beat, str(look.get("mood") or ""))
        if mood not in craft.MUSIC_MOODS:
            mood = "warm" if mood in {"neutral", "punch"} else ("tense" if mood == "tense" else "chill")
        spec = craft.music_for(mood)
        samples = audio_lib.music(length, spec, gain=gain, seed=int(start * 100) % 9973 + 3)
        saved = ctx.save_wav(f"music-{len(items) + 1}-{mood}.wav", audio_lib.wav_bytes(samples))
        items.append(
            {
                "kind": "music",
                "id": f"music-{len(items) + 1}",
                "label": f"{spec.get('label', mood)}｜{int(spec.get('bpm', 100))}BPM",
                "mood": mood,
                "offset": round(start, 3),
                "gain": gain,
                "sidechain": sidechain,
                "duration_s": round(audio_lib.seconds_of(samples), 3),
                "bpm": spec.get("bpm"),
                "provider": "sample",
                "simulated": True,
                **saved,
            }
        )

    payload = {
        "kind": "music",
        "items": items,
        "total_s": total,
        "segments": len(items),
        "bpm": items[0]["bpm"] if items else None,
        "duck": sidechain,
        "simulated": True,
    }
    text = "\n".join(
        [
            f"配乐：{len(items)} 段（{segment}）｜基准 {payload['bpm']}BPM｜增益 {gain}｜人声闪避 {sidechain}",
            *[f"· {item['offset']}s +{item['duration_s']}s〔{item['label']}〕情绪 {item['mood']}" for item in items],
            "离线合成器出真声（和弦垫 + 低音 + 琶音 + 打击），无版权风险；接真曲库时替换同名段落的 ref。",
        ]
    )
    ctx.log(f"配乐：{len(items)} 段 / 覆盖 {total}s")
    return {"audio": payload, "text": text}


#: 现场环境 → 噪声底（扫频区间、可听电平、环境声压级估算 dB SPL、口径说明）。
#: 声压级是录音师判断「这句同期能不能留」的原始依据，也是对白补录监督的红线输入。
_NOISE_FLOOR_BY_ENV: dict[str, tuple[tuple[float, float], float, float, str]] = {
    "transit": ((1800.0, 520.0), 0.26, 84.0, "地铁/车厢轮轨与通风低频"),
    "street": ((2600.0, 900.0), 0.22, 72.0, "街口交通与人声"),
    "market": ((3200.0, 1200.0), 0.20, 66.0, "商场空调整体底"),
    "office": ((420.0, 190.0), 0.14, 46.0, "办公室空调低频"),
    "home": ((300.0, 140.0), 0.12, 38.0, "居室冰箱与外机"),
    "stage": ((150.0, 80.0), 0.07, 26.0, "静音棚本底"),
}

#: 场景名关键词 → 环境类别（先匹配先赢：「地铁站出口」算轨道而不是街道）
_ENV_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("transit", ("地铁", "车厢", "轨道", "火车", "机场", "站台")),
    ("street", ("街", "路", "广场", "巷", "天桥", "出口", "门口", "外")),
    ("market", ("商场", "商店", "超市", "展厅", "咖啡")),
    ("office", ("办公室", "工位", "会议室", "公司")),
    ("home", ("家", "卧室", "客厅", "厨房", "居")),
)

#: 旁白不占话筒
_VOICEOVER_TAGS = {"旁白", "画外音", "VO", ""}

#: 承载方式里会带进机械噪声的轨道/摇臂/稳定器/车拍
_MECHANICAL_HINTS = ("轨道", "摇臂", "稳定器", "车", "升降", "滑轨", "dolly", "crane")

_MIC_LABELS = {"boom": "挑杆为主", "lav_plus_boom": "领夹+挑杆", "lav_only": "只用领夹", "boom_only": "只用挑杆"}

#: 拟音层触发词典：动作词 → 层名（拟音棚里靠这些动作找点位）
_FOLEY_ACTIONS: tuple[tuple[str, tuple[str, ...], int], ...] = (
    ("step", ("走", "挤", "冲", "跑", "迈", "踏", "进", "出", "上", "下", "台阶", "赶"), 6),
    ("cloth", ("坐", "起身", "凑", "靠", "抬手", "抹", "站", "弯腰", "整理", "外套"), 4),
    ("spot", ("拍", "放", "关", "扔", "敲", "按", "推", "拉", "拧", "抓"), 3),
)

#: 道具关键词 → （层名、合成规格、棚里用什么做）
_PROP_FOLEY: tuple[tuple[tuple[str, ...], str, dict[str, Any], str], ...] = (
    (("纸", "文件", "本", "说明", "包装"), "paper", {"sweep": (3600.0, 2400.0), "dur": 0.3, "gain": 0.42}, "纸张/翻页"),
    (("风扇", "空调", "扇", "吹风"), "motor", {"sweep": (330.0, 190.0), "dur": 1.1, "gain": 0.30}, "电机嗡鸣（吊扇实拍）"),
    (("手机", "遥控", "按键", "键盘", "安检"), "tap", {"freq": 2100.0, "dur": 0.06, "gain": 0.34}, "按键点击"),
    (("包", "袋", "盒", "箱"), "rustle", {"sweep": (4200.0, 2600.0), "dur": 0.2, "gain": 0.36}, "皮质/帆布摩擦"),
    (("杯", "水", "瓶", "茶", "咖啡"), "liquid", {"sweep": (1600.0, 700.0), "dur": 0.35, "gain": 0.30}, "液体与玻璃"),
    (("门", "抽屉", "柜", "栅栏"), "door", {"sweep": (900.0, 320.0), "dur": 0.42, "gain": 0.44}, "门轴与合页"),
)
_DEFAULT_PROP: tuple[str, dict[str, Any], str] = ("handle", {"sweep": (2800.0, 1500.0), "dur": 0.22, "gain": 0.34}, "道具拿放")

#: 拟音层中文名（工单与清单都用它）
_FOLEY_LABELS = {
    "step": "脚步",
    "cloth": "衣料",
    "spot": "动作点",
    "paper": "纸张",
    "motor": "电机",
    "tap": "按键",
    "rustle": "皮布",
    "liquid": "液体",
    "door": "门轴",
    "handle": "拿放",
}

#: 环境 → （地面材料、鞋底）：拟音师的鞋箱选择
_SHOES_BY_ENV: dict[str, tuple[str, str]] = {
    "transit": ("车厢地板胶", "中硬底胶鞋"),
    "street": ("水泥/石板", "硬底皮鞋"),
    "market": ("抛光地砖", "硬底皮鞋"),
    "office": ("地毯压地板", "软底鞋 + 毛刷"),
    "home": ("木地板", "软底鞋"),
    "stage": ("棚内软地胶", "软底鞋"),
}


def _env_of(shot: dict[str, Any]) -> str:
    text = f"{shot.get('location') or ''}{shot.get('scene_title') or ''}"
    for env, keys in _ENV_HINTS:
        if any(key in text for key in keys):
            return env
    return "stage" if str(shot.get("int_ext") or "INT").upper().startswith("INT") else "street"


def _speakers_of(shot: dict[str, Any]) -> list[str]:
    names = [str(row.get("character") or "") for row in (shot.get("dialogue") or []) if isinstance(row, dict)]
    names.append(str(shot.get("focus_character") or ""))
    out: list[str] = []
    for name in names:
        if name not in _VOICEOVER_TAGS and name not in out:
            out.append(name)
    return out


def _stamp(buf: Any, at_seconds: float, piece: Sequence[float], weight: float) -> None:
    start = int(max(0.0, at_seconds) * audio_lib.SR)
    for index, value in enumerate(piece):
        target = start + index
        if target >= len(buf):
            break
        buf[target] += value * weight


def _foley_piece(layer: str, spec: dict[str, Any], gain: float, seed: int) -> Sequence[float]:
    if layer == "step":
        return audio_lib.sfx("footstep", float(spec.get("dur") or 0.16), gain, seed)
    if "freq" in spec:
        return audio_lib.tone(float(spec["freq"]), float(spec["dur"]), gain, "out", "bright")
    return audio_lib.noise_burst(float(spec["dur"]), tuple(spec["sweep"]), gain, "in_out", seed)


@node(
    "production_sound",
    title="同期录音师",
    dept="sound",
    role="现场拾声：逐镜定话筒方案（挑杆/无线/防风），按场景实测噪声底并采留白样本，出同期电平单与补录风险",
    inputs=[
        Port("shots", "分镜表", "shots"),
        Port("locations", "场景表", "locations", required=False),
        Port("camera_plan", "摄影方案", "camera_plan", required=False),
    ],
    outputs=[
        Port("plan", "录音工单", "plan"),
        Port("audio", "底噪轨", "audio"),
        Port("images", "底噪电平图", "images"),
        Port("text", "录音说明", "text"),
    ],
    params=[
        Param("boom", "挑杆口径", "select", "自动", ["自动", "只用挑杆", "只用领夹"], hint="运动镜与多人戏挑杆容易入画，自动口径会切领夹"),
        Param("target_db", "同期目标峰值", "number", -10.0, minimum=-24.0, maximum=-4.0, step=0.5, unit="dBFS", hint="现场把话调到这个峰值，给后期二压留 headroom"),
        Param("gate_spl", "底噪红线", "number", 55.0, minimum=35.0, maximum=85.0, step=1.0, unit="dB SPL", hint="超过这条线的场次，同期对白只能交给补录"),
        Param("print_s", "噪声底采样长度", "number", 5.0, minimum=2.0, maximum=20.0, step=0.5, unit="秒", hint="每场收一段干净底噪，后期降噪与铺底都要它"),
        Param("tone_gap", "留白时长", "number", 8.0, minimum=0.0, maximum=30.0, step=1.0, unit="秒", hint="每个机位收工后空录一段房间留白"),
        Param("print_limit", "最多采样场次", "number", 8, minimum=1, maximum=24, step=1, unit="场"),
    ],
    icon="🎚️",
    tags=["声音", "现场"],
)
def production_sound(ctx: Any) -> dict[str, Any]:
    timeline = common.require_shots(ctx.in_("shots"), "同期录音师")
    shots = common.shots_of(timeline)
    camera_rows = common.items_of(ctx.in_("camera_plan"))
    rig_by_slate = {str(row.get("slate")): str(row.get("rig_label") or "") for row in camera_rows}
    # 勘景表：场地名 → 环境类别。分镜只写「场景」时按置景给的内外景判底噪
    scouted: dict[str, str] = {}
    for item in common.locations_of(ctx.in_("locations")):
        name = str(item.get("name") or "")
        if name:
            scouted[name] = _env_of({"location": name, "int_ext": str(item.get("int_ext") or item.get("kind") or "")})

    boom_mode = str(ctx.params.get("boom") or "自动")
    target_db = float(ctx.params.get("target_db") or -10.0)
    gate = float(ctx.params.get("gate_spl") or 55.0)
    print_s = float(ctx.params.get("print_s") or 5.0)
    tone_gap = float(ctx.params.get("tone_gap") or 8.0)
    print_limit = int(float(ctx.params.get("print_limit") or 8))

    rows: list[dict[str, Any]] = []
    scenes: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for shot in shots:
        slate = str(shot.get("slate") or shot.get("id"))
        env = _env_of(shot)
        where = str(shot.get("location") or "")
        for name, guess in scouted.items():
            if name in where or (where and where in name):
                env = guess
                break
        sweep, level, spl, env_label = _NOISE_FLOOR_BY_ENV[env]
        speakers = _speakers_of(shot)
        moving = str(shot.get("movement") or "static") != "static"
        exterior = str(shot.get("int_ext") or "INT").upper().startswith("EXT")
        wind = exterior or env in {"street", "transit", "market"}
        rig = rig_by_slate.get(slate, "")
        mechanical = any(key in rig for key in _MECHANICAL_HINTS)

        if boom_mode == "只用挑杆":
            mic, mic_note = "boom_only", "挑杆全程：运动镜有入画风险，现场要回看监视器"
        elif boom_mode == "只用领夹":
            mic, mic_note = "lav_only", "只用无线：失去空间感，贴脸但干净"
        elif moving or len(speakers) >= 2:
            mic, mic_note = "lav_plus_boom", "走位或多人口径：无线为主、挑杆收空间（双备份）"
        else:
            mic, mic_note = "boom", "固定单人口径：超指向挑杆为主，无线做备份"

        risks: list[str] = []
        if speakers and spl > gate:
            risks.append(f"现场 {spl:.0f}dB SPL 超红线 {gate:.0f}：这句同期只能当参考轨")
        if wind and speakers:
            risks.append("外露话筒必须带防风罩 + 毛衣，风直冲会削掉低频")
        if mechanical:
            risks.append(f"{rig} 的机械噪声会进话筒：轨道垫胶轮、稳定器调静音模式")
        if str(shot.get("movement")) in {"whip", "handheld"}:
            risks.append("手持/甩镜的摩擦声靠后期拟音补，现场别要求同期全干净")

        rows.append(
            {
                "shot_id": shot.get("id"),
                "slate": slate,
                "scene_id": str(shot.get("scene_id") or ""),
                "location": str(shot.get("location") or ""),
                "int_ext": str(shot.get("int_ext") or "INT"),
                "env": env,
                "env_label": env_label,
                "floor_spl": round(spl, 1),
                "speakers": speakers,
                "mic": mic,
                "mic_note": mic_note,
                "wireless": max(1, len(speakers)),
                "wind": wind,
                "mechanical": mechanical,
                "rig": rig,
                "target_db": target_db,
                "record_s": round(float(shot.get("duration_s") or 1.0), 3),
                "over_gate": bool(speakers and spl > gate),
                "risks": risks,
            }
        )

        key = str(shot.get("scene_id") or shot.get("location") or "scene")
        if key not in scenes:
            scenes[key] = {
                "scene_id": key,
                "where": str(shot.get("location") or key),
                "env": env,
                "env_label": env_label,
                "floor_spl": round(spl, 1),
                "start_s": float(shot.get("start_s") or 0.0),
                "end_s": 0.0,
                "slates": [],
                "mic_set": set(),
            }
            order.append(key)
        bed = scenes[key]
        bed["start_s"] = min(float(bed["start_s"]), float(shot.get("start_s") or 0.0))
        bed["end_s"] = max(float(bed["end_s"]), float(shot.get("start_s") or 0.0) + float(shot.get("duration_s") or 1.0))
        bed["slates"].append(slate)
        bed["mic_set"].add(mic)
        bed["floor_spl"] = max(float(bed["floor_spl"]), spl)

    items: list[dict[str, Any]] = []
    loudest: tuple[float, list[float]] = (0.0, [])
    for key in order[:print_limit]:
        bed = scenes[key]
        sweep, level, spl, env_label = _NOISE_FLOOR_BY_ENV[str(bed["env"])]
        samples = audio_lib.noise_burst(print_s, sweep, level, "flat", seed=int(float(bed["start_s"]) * 1000) % 9973 + 7)
        saved = ctx.save_wav(f"floor-{common.slug(key)}.wav", audio_lib.wav_bytes(samples))
        peak = max((abs(value) for value in samples), default=0.0)
        bed["print_ref"] = saved["ref"]
        bed["print_url"] = saved["url"]
        bed["print_db"] = round(audio_lib.db_of(peak), 1)
        bed["tone_gap_s"] = round(tone_gap, 2)
        bed["mics"] = sorted(bed["mic_set"])
        del bed["mic_set"]
        items.append(
            {
                "kind": "floor",
                "id": f"floor-{len(items) + 1}",
                "label": f"噪声底｜{bed['where']}",
                "offset": round(float(bed["start_s"]), 3),
                "gain": round(level * 1.4, 3),
                "sidechain": 0.3,
                "env": str(bed["env"]),
                "scene_id": key,
                "duration_s": round(audio_lib.seconds_of(samples), 3),
                "reason": f"{env_label}（{spl:.0f}dB SPL）现场采样 {print_s}s + 收工留白 {tone_gap}s",
                "provider": "sample",
                "simulated": True,
                **saved,
            }
        )
        if peak > loudest[0]:
            loudest = (peak, audio_lib.peaks_of(samples, 320))
    items.sort(key=lambda item: float(item["offset"]))

    over_gate = [row for row in rows if row["over_gate"]]
    windy = [row for row in rows if row["wind"] and row["speakers"]]
    images: list[dict[str, Any]] = []
    if loudest[1]:
        png = canvas.waveform_png(loudest[1], (251, 191, 36), 760, 140)
        images.append(
            common.image_artifact(
                ctx,
                "noise-floor-level.png",
                png,
                {"kind": "waveform", "id": "noise-floor", "prompt": f"{len(items)} 场噪声底采样电平", "seed": 0, "simulated": True, "provider": "canvas"},
            )
        )
    mic_counts: dict[str, int] = {}
    for row in rows:
        label = _MIC_LABELS[str(row["mic"])]
        mic_counts[label] = mic_counts.get(label, 0) + 1
    plan = {
        "dept": "sound",
        "role": "同期录音师",
        "items": rows,
        "scenes": [scenes[key] for key in order[:print_limit]],
        "scouting": [
            {"where": name, "env": env, "env_label": _NOISE_FLOOR_BY_ENV[env][3], "floor_spl": _NOISE_FLOOR_BY_ENV[env][2]}
            for name, env in sorted(scouted.items())
        ],
        "shot_count": len(rows),
        "scene_count": len(order),
        "sampled": len(items),
        "mics": sorted({str(row["mic"]) for row in rows}),
        "mic_counts": mic_counts,
        "wireless_channels": max([int(row["wireless"]) for row in rows] or [1]),
        "target_db": target_db,
        "gate_spl": gate,
        "print_s": print_s,
        "tone_gap_s": tone_gap,
        "set_hours": round(sum(float(row["record_s"]) for row in rows) / 600.0 + len(order) * 0.3, 2),
        "risks": [f"{row['slate']}（{row['env_label']} {row['floor_spl']}dB SPL）：{'；'.join(row['risks'][:2])}" for row in rows if row["risks"]][:10],
        "simulated": True,
    }
    text = "\n".join(
        [
            f"录音工单｜{len(rows)} 镜 / {len(order)} 场（采样 {len(items)} 段）｜目标峰值 {target_db}dBFS｜底噪红线 {gate}dB SPL",
            "话筒口径：" + "、".join(f"{name} {count} 镜" for name, count in sorted(mic_counts.items())),
            *[f"· {row['slate']}〔{row['env_label']} {row['floor_spl']}dB〕{row['mic']}｜无线 {row['wireless']} 通道｜{'、'.join(row['risks'][:2]) or '现场可用'}" for row in rows[:14]],
            (f"⚠ {len(over_gate)} 镜底噪超红线需转后期补录：" + "、".join(str(row["slate"]) for row in over_gate[:6])) if over_gate else "全部对白镜底噪在红线内，同期可用",
            (f"外露话筒 {len(windy)} 镜必须带防风罩" if windy else "无外露风镜"),
        ]
    )
    ctx.log(f"同期录音：{len(rows)} 镜方案，{len(over_gate)} 镜超底噪红线")
    return {
        "plan": plan,
        "audio": {"kind": "floor", "items": items, "total_s": float(timeline.get("total_s") or 0), "scenes": len(items), "simulated": True},
        "images": {"items": images},
        "text": text,
    }


@node(
    "foley",
    title="拟音师",
    dept="sound",
    role="后期补物理声：逐镜按动作铺脚步/衣料/道具 pass，标出点位与鞋底，出棚内道具与同步风险清单",
    inputs=[
        Port("shots", "分镜表", "shots"),
        Port("recording", "录音工单", "plan", required=False),
        Port("camera_plan", "摄影方案", "camera_plan", required=False),
    ],
    outputs=[Port("audio", "拟音轨", "audio"), Port("plan", "拟音工单", "plan"), Port("text", "拟音清单", "text")],
    params=[
        Param("gain", "拟音增益", "number", 0.5, minimum=0.1, maximum=1.2, step=0.05, unit="倍"),
        Param("cadence", "步频上限", "number", 3.0, minimum=1.0, maximum=6.0, step=0.5, unit="步/秒", hint="按镜头能量折算：跑戏贴上限，站戏减半"),
        Param("layer_s", "单层最长", "number", 2.4, minimum=0.6, maximum=6.0, step=0.2, unit="秒", hint="一条 pass 的时长上限，长镜头只铺最贴画的头一段"),
        Param("limit", "最多 pass 数", "number", 28, minimum=4, maximum=120, step=1, unit="条"),
        Param("footsteps", "铺脚步", "toggle", True),
        Param("hands", "铺衣料与道具", "toggle", True),
    ],
    icon="👞",
    tags=["声音", "拟音"],
)
def foley(ctx: Any) -> dict[str, Any]:
    timeline = common.require_shots(ctx.in_("shots"), "拟音师")
    shots = common.shots_of(timeline)
    gain = float(ctx.params.get("gain") or 0.5)
    cadence = float(ctx.params.get("cadence") or 3.0)
    layer_s = float(ctx.params.get("layer_s") or 2.4)
    limit = int(float(ctx.params.get("limit") or 28))
    do_steps = bool(ctx.params.get("footsteps"))
    do_hands = bool(ctx.params.get("hands"))

    rec_plan = ctx.in_("recording")
    rec_rows = rec_plan.get("items") if isinstance(rec_plan, dict) else None
    floor_by_slate = {str(row.get("slate")): row for row in (rec_rows or []) if isinstance(row, dict)}

    actions: list[tuple[str, list[str], str]] = []  # 每镜的动作依据与现场可用性
    passes: list[dict[str, Any]] = []
    surfaces: dict[str, str] = {}
    stage_props: dict[str, str] = {}

    for shot in shots:
        if len(passes) >= limit:
            break
        slate = str(shot.get("slate") or shot.get("id"))
        env = _env_of(shot)
        duration = float(shot.get("duration_s") or 1.0)
        start = float(shot.get("start_s") or 0.0)
        span = round(min(layer_s, duration), 3)
        energy = float(shot.get("energy") or 0.55)
        text = f"{shot.get('description') or ''}{' '.join(str(row.get('line') or '') for row in (shot.get('dialogue') or []) if isinstance(row, dict))}"
        props = [str(item) for item in (shot.get("props") or [])]
        rec = floor_by_slate.get(slate) or {}
        # 同期被底噪判死的镜，物理声必须全部后期做——这就是拟音师排在录音师之后的原因
        must_post = bool(rec.get("over_gate")) or bool(rec.get("mechanical"))

        layers: list[tuple[str, dict[str, Any], str, int]] = []
        hits: list[str] = []
        if do_steps:
            for layer, keys, cap in _FOLEY_ACTIONS:
                if layer != "step":
                    continue
                found = [key for key in keys if key in text]
                if found or must_post:
                    taps = min(cap, max(2, int(span * cadence * (0.6 + energy))))
                    spec = {"dur": 0.16, "gain": 0.55, "freq": 150.0}
                    layers.append((layer, spec, "步点（" + ("、".join(found[:3]) if found else "走位补点") + "）", taps))
                    hits.append(layer)
        if do_hands:
            for layer, keys, cap in _FOLEY_ACTIONS:
                if layer == "step":
                    continue
                found = [key for key in keys if key in text]
                if found:
                    spec = {"sweep": (2400.0, 1200.0), "dur": 0.24, "gain": 0.34} if layer == "cloth" else {"sweep": (1900.0, 700.0), "dur": 0.2, "gain": 0.42}
                    layers.append((layer, spec, f"{layer}（{'、'.join(found[:3])}）", min(cap, 3)))
                    hits.append(layer)
            for keys, layer, spec, tool in _PROP_FOLEY:
                matched = [prop for prop in props if any(key in prop for key in keys)]
                if matched:
                    layers.append((layer, spec, f"道具「{matched[0]}」→ {tool}", 2 if spec["dur"] < 0.5 else 1))
                    stage_props[matched[0]] = tool
                    hits.append(layer)
            leftover = [prop for prop in props if prop not in stage_props]
            if leftover:
                layer, spec, tool = _DEFAULT_PROP
                layers.append((layer, spec, f"道具「{leftover[0]}」→ {tool}", 1))
                stage_props[leftover[0]] = tool
                hits.append(layer)

        for layer, spec, reason, taps in layers:
            if len(passes) >= limit:
                break
            if layer == "step":
                material, shoe = _SHOES_BY_ENV[env]
                surfaces[material] = shoe
            buf = audio_lib.silence(span)
            step_at = span / max(1, taps)
            for tap in range(taps):
                seed = int((start + tap * step_at) * 1000) % 9973 + len(passes) + 1
                piece = _foley_piece(layer, spec, gain * float(spec.get("gain") or 0.4), seed)
                _stamp(buf, tap * step_at + (0.02 if layer != "step" else 0.0), piece, 1.0)
            saved = ctx.save_wav(f"foley-{common.slug(slate)}-{layer}-{len(passes) + 1}.wav", audio_lib.wav_bytes(buf))
            passes.append(
                {
                    "kind": "foley",
                    "id": f"foley-{len(passes) + 1}",
                    "label": f"{_FOLEY_LABELS.get(layer, layer)}｜{slate}",
                    "layer": layer,
                    "slate": slate,
                    "shot_id": shot.get("id"),
                    "taps": taps,
                    "offset": round(start, 3),
                    "gain": gain,
                    "sidechain": 0.5,
                    "reason": reason,
                    "must_post": must_post,
                    "duration_s": round(audio_lib.seconds_of(buf), 3),
                    "provider": "sample",
                    "simulated": True,
                    **saved,
                }
            )
        actions.append((slate, hits, "同期不可用，物理声全补" if must_post else "同期可用，只补话筒吃不到的细节"))

    payload = {
        "kind": "foley",
        "items": passes,
        "total_s": float(timeline.get("total_s") or 0),
        "layers": sorted({str(row["layer"]) for row in passes}),
        "simulated": True,
    }
    by_layer: dict[str, int] = {}
    for row in passes:
        by_layer[str(row["layer"])] = by_layer.get(str(row["layer"]), 0) + 1
    plan = {
        "dept": "sound",
        "role": "拟音师",
        "items": passes,
        "covers": [row for row in actions if row[1]],
        "passes": len(passes),
        "layer_counts": by_layer,
        "shoes": [{"surface": surface, "shoe": shoe} for surface, shoe in sorted(surfaces.items())],
        "stage_props": [{"prop": prop, "make": make} for prop, make in sorted(stage_props.items())][:14],
        "post_only": sum(1 for row in passes if row["must_post"]),
        "stage_hours": round(len(passes) * 0.06 + len(surfaces) * 0.2, 2),
        "risks": (
            [f"{row['slate']} 的 {row['layer']} pass 比镜头长 {row['duration_s']}s，剪到镜内或挪点位" for row in passes if float(row["duration_s"]) > 0.2][:4]
            if any(float(row["duration_s"]) > 0.2 for row in passes)
            else []
        ),
        "simulated": True,
    }
    text = "\n".join(
        [
            f"拟音工单｜{len(passes)} 条 pass / {len(by_layer)} 层｜鞋底 {len(surfaces)} 种｜棚时约 {plan['stage_hours']}h",
            "层分布：" + "、".join(f"{_FOLEY_LABELS.get(name, name)} {count}" for name, count in sorted(by_layer.items())),
            *[f"· {row['offset']}s +{row['duration_s']}s〔{row['label']}〕{row['taps']} 点｜{row['reason']}" for row in passes[:16]],
            (f"其中 {plan['post_only']} 条来自同期判废的镜（底噪/机械噪声），必须后期做" if plan["post_only"] else "同期可用，本岗只补话筒吃不到的细节"),
            "鞋箱：" + "、".join(f"{surface}→{shoe}" for surface, shoe in sorted(surfaces.items())),
        ]
    )
    ctx.log(f"拟音：{len(passes)} 条 pass / {len(by_layer)} 层")
    return {"audio": payload, "plan": plan, "text": text}


@node(
    "adr",
    title="对白补录监督",
    dept="sound",
    role="判定哪句同期留不住：按底噪红线、配音工单超时与压话点出补录单，逐句按贴合语速重录并拼回对白总轨",
    inputs=[
        Port("shots", "分镜表", "shots"),
        Port("voice", "人声轨", "audio", required=False),
        Port("work", "配音工单", "plan", required=False),
        Port("recording", "录音工单", "plan", required=False),
        Port("report", "场记报告", "report", required=False),
    ],
    outputs=[Port("audio", "对白总轨", "audio"), Port("plan", "补录单", "plan"), Port("text", "补录说明", "text")],
    params=[
        Param("gate_spl", "底噪红线", "number", 55.0, minimum=35.0, maximum=85.0, step=1.0, unit="dB SPL", hint="与同期录音师同一条线：超过就该重录"),
        Param("scope", "补录口径", "select", "只补问题句", ["只补问题句", "全剧重录", "只出清单不录音"], hint="只出清单时不动声音，先给导演看判定"),
        Param("head_gap", "入话留白", "number", 0.15, minimum=0.0, maximum=0.6, step=0.05, unit="秒"),
        Param("rate_cap", "补录语速上限", "number", 1.35, minimum=1.0, maximum=1.8, step=0.05, unit="倍", hint="再快就贴不住口型，只能回去减词"),
        Param("allow_trim", "允许减词贴画面", "toggle", True, hint="语速超上限时按句砍到能进为止，并在单上标改动"),
        Param("max_lines", "最多补录句数", "number", 20, minimum=1, maximum=120, step=1, unit="句"),
    ],
    icon="🔁",
    tags=["声音", "补录"],
)
def adr(ctx: Any) -> dict[str, Any]:
    timeline = common.require_shots(ctx.in_("shots"), "对白补录监督")
    shots = common.shots_of(timeline)
    gate = float(ctx.params.get("gate_spl") or 55.0)
    scope = str(ctx.params.get("scope") or "只补问题句")
    head_gap = float(ctx.params.get("head_gap") or 0.15)
    rate_cap = float(ctx.params.get("rate_cap") or 1.35)
    allow_trim = bool(ctx.params.get("allow_trim"))
    limit = int(float(ctx.params.get("max_lines") or 20))

    voice_payload = ctx.in_("voice")
    base_items = [row for row in (voice_payload.get("items") if isinstance(voice_payload, dict) else []) or [] if isinstance(row, dict)]
    dialogue_items = [row for row in base_items if str(row.get("kind") or "voice") in {"voice", "dialogue"}]
    other_items = [row for row in base_items if str(row.get("kind") or "voice") not in {"voice", "dialogue"}]

    work_rows = common.items_of(ctx.in_("work"))
    cue_by_key = {(str(row.get("shot_id")), str(row.get("text"))): row for row in work_rows}
    cue_by_text: dict[str, dict[str, Any]] = {}
    for row in work_rows:
        cue_by_text.setdefault(str(row.get("text")), row)

    rec_plan = ctx.in_("recording")
    rec_rows = rec_plan.get("items") if isinstance(rec_plan, dict) else None
    rec_by_slate = {str(row.get("slate")): row for row in (rec_rows or []) if isinstance(row, dict)}
    no_evidence = 0

    report = ctx.in_("report")
    sync_slates: set[str] = set()
    for issue in (report.get("issues") or []) if isinstance(report, dict) else []:
        if str(issue.get("category") or "") not in {"节奏", "时间"}:
            continue
        detail = str(issue.get("detail") or "")
        for slate in {str(row.get("slate")) for row in shots}:
            if slate and slate in detail:
                sync_slates.add(slate)

    # 压话：同一时间线上人声重叠的那一句必须分开补录，否则两条嘴糊在一起
    busy = sorted(dialogue_items, key=lambda row: float(row.get("offset") or 0.0))
    overlap_ids: set[str] = set()
    overlap_texts: set[str] = set()
    end_at = 0.0
    for row in busy:
        start = float(row.get("offset") or 0.0)
        if start < end_at - 0.02:
            overlap_ids.add(str(row.get("id")))
            overlap_texts.add(str(row.get("text") or ""))
        end_at = max(end_at, start + float(row.get("duration_s") or 0.0))

    sheet: list[dict[str, Any]] = []
    for shot in shots:
        slate = str(shot.get("slate") or shot.get("id"))
        lines = [row for row in (shot.get("dialogue") or []) if isinstance(row, dict) and str(row.get("line") or "").strip()]
        if not lines:
            continue
        duration = float(shot.get("duration_s") or 1.0)
        start = float(shot.get("start_s") or 0.0)
        rec = rec_by_slate.get(slate) or {}
        if not rec:
            no_evidence += 1
        for row in lines:
            who = str(row.get("character") or shot.get("speaker") or "旁白")
            text = str(row.get("line") or "").strip()
            cue = cue_by_key.get((str(shot.get("id")), text)) or cue_by_text.get(text)
            voice_key = str((cue or {}).get("voice") or "")
            preset = craft.VOICE_PRESETS.get(voice_key) or craft.VOICE_PRESETS["narrator_female"]
            reasons: list[str] = []
            if scope == "全剧重录":
                reasons.append("口径要求全剧统一重录")
            if rec.get("over_gate"):
                reasons.append(f"同期底噪 {float(rec.get('floor_spl') or 0):.0f}dB SPL 超红线 {gate:.0f}")
            if rec.get("mechanical"):
                reasons.append(f"{rec.get('rig')} 机械噪声进话筒")
            if rec.get("wind") and str(shot.get("int_ext") or "").upper().startswith("EXT") and float(rec.get("floor_spl") or 0) > gate - 8:
                reasons.append("外景风噪直冲高频")
            if cue and not cue.get("fit") and not cue.get("vo"):
                reasons.append(f"台词超镜头 {float(cue.get('over') or 0):.2f}s")
            if cue and str(cue.get("shifted") or 0) not in ("0.0", "0", "") and float(cue.get("shifted") or 0) > 0.05:
                reasons.append(f"配音导演顺延 {float(cue['shifted']):.2f}s 进话，口型需重贴")
            if text in overlap_texts:
                reasons.append("与上一句压话，必须分开补录")
            if slate in sync_slates:
                reasons.append("场记改时长/时段，同期口型对不上")

            available = max(0.4, duration - head_gap)
            base_rate = float(preset["rate"])
            est = craft.speech_seconds(text, base_rate)
            needed = est / available
            trimmed = ""
            final_text = text
            if needed > rate_cap and allow_trim and scope != "只出清单不录音":
                keep: list[str] = []
                acc = 0.0
                for sentence in craft.sentences(text) or [text]:
                    cost = craft.speech_seconds(sentence, rate_cap * base_rate)
                    if acc + cost > available and keep:
                        break
                    keep.append(sentence)
                    acc += cost
                final_text = "".join(keep) or text[: max(4, int(len(text) * 0.6))]
                trimmed = f"减词 {len(text) - len(final_text)} 字后按 {rate_cap}× 录"
                reasons.append(f"按 {rate_cap}× 语速仍超 {round(needed, 2)}×，需减词")
            rate = round(min(rate_cap, max(1.0, needed)) * base_rate, 2) if scope != "只出清单不录音" else round(base_rate, 2)

            if not reasons:
                continue
            if len(sheet) >= limit:
                break
            sheet.append(
                {
                    "shot_id": shot.get("id"),
                    "slate": slate,
                    "character": who,
                    "voice": voice_key or "narrator_female",
                    "voice_label": preset["label"],
                    "original": text,
                    "text": final_text,
                    "start_s": round(float((cue or {}).get("start_s") or start + head_gap), 3),
                    "available_s": round(available, 2),
                    "est_s": round(est, 2),
                    "rate": rate,
                    "trim": trimmed,
                    "reasons": reasons,
                    "replaces": next((str(item.get("id")) for item in dialogue_items if str(item.get("text")) == text), ""),
                }
            )
        if len(sheet) >= limit:
            break

    replaced_ids: set[str] = set()
    new_items: list[dict[str, Any]] = []
    for index, row in enumerate(sheet):
        if scope == "只出清单不录音":
            continue
        preset = {**(craft.VOICE_PRESETS.get(str(row["voice"])) or craft.VOICE_PRESETS["narrator_female"]), "rate": float(row["rate"])}
        saved = common.voice_bytes_or_sample(
            ctx, str(row["text"]), preset, f"adr-{index + 1}-{common.slug(str(row['slate']))}-{common.slug(str(row['character']))}.wav"
        )
        replaced_ids.add(str(row["replaces"]))
        new_items.append(
            {
                "kind": "voice",
                "id": f"adr-{index + 1}",
                "label": f"补录｜{row['character']}：{str(row['text'])[:18]}",
                "character": row["character"],
                "voice": row["voice"],
                "offset": row["start_s"],
                "gain": next((float(item.get("gain") or 0.9) for item in dialogue_items if str(item.get("id")) == str(row["replaces"])), 0.9),
                "sidechain": 1.0,
                "text": row["text"],
                "adr": True,
                "replaces": row["replaces"],
                "reasons": row["reasons"],
                **saved,
            }
        )

    kept = [row for row in dialogue_items if str(row.get("id")) not in replaced_ids]
    combined = sorted(kept + new_items, key=lambda row: float(row.get("offset") or 0.0))
    cues = [
        {"start": float(row.get("offset") or 0.0), "end": round(float(row.get("offset") or 0.0) + float(row.get("duration_s") or 0.0), 3), "text": str(row.get("text") or ""), "character": str(row.get("character") or "")}
        for row in combined
    ]
    real = [row for row in new_items if not row.get("simulated")]
    evidence_gap = f"{no_evidence} 个有台词的镜头没接到录音工单：底噪判定缺现场依据" if no_evidence else ""
    payload = {
        "kind": "voice",
        "items": (combined + other_items) if (dialogue_items or new_items) else base_items,
        "cues": cues,
        "total_s": float(timeline.get("total_s") or 0),
        "lines": len(combined),
        "adr_lines": len(new_items) if scope != "只出清单不录音" else len(sheet),
        "overlaps": len(overlap_ids),
        "provider": new_items[0]["provider"] if new_items else (dialogue_items[0]["provider"] if dialogue_items else "sample"),
        "simulated": not real and not any(not row.get("simulated") for row in kept),
        "no_voice_input": not dialogue_items,
    }
    plan = {
        "dept": "sound",
        "role": "对白补录监督",
        "items": sheet,
        "candidates": len(sheet),
        "recorded": len(new_items),
        "kept": len(kept),
        "gate_spl": gate,
        "rate_cap": rate_cap,
        "trimmed": len([row for row in sheet if row["trim"]]),
        "studio_hours": round(len(sheet) * 0.12 + len({row["character"] for row in sheet}) * 0.25, 2),
        "risks": [f"{row['slate']} {row['character']}：{'；'.join(row['reasons'][:2])}" for row in sheet[:10]] + ([evidence_gap] if evidence_gap else []),
        "simulated": True,
    }
    text = "\n".join(
        [
            f"补录单｜判定 {len(sheet)} 句，重录 {len(new_items)} 句，保留同期/原配音 {len(kept)} 句｜口径：{scope}",
            *[f"· {row['slate']}〔{row['voice_label']}〕{row['character']}：{row['original'][:16]}… → {row['rate']}×（{row['available_s']}s 内要说 {row['est_s']}s）｜{'；'.join(row['reasons'][:2])}" for row in sheet[:12]],
            (evidence_gap or "现场底噪依据齐全"),
            (f"⚠ {len(overlap_ids)} 句在上游就压话，已按落点分开补录" if overlap_ids else "人声轨上各句落点不重叠"),
            ("未接人声轨：只出补录条，不合成对白总轨" if not dialogue_items else "对白总轨已把补录条替换回原位置，混音师拿到的仍是一条完整人声"),
            (f"其中 {plan['trimmed']} 句按 {rate_cap}× 上限仍进不去，已减词，需回编剧确认" if plan["trimmed"] else "没有句子需要减词"),
        ]
    )
    ctx.log(f"补录判定：{len(sheet)} 句，重录 {len(new_items)} 句")
    return {"audio": payload, "plan": plan, "text": text}


@node(
    "mixer",
    title="混音师",
    dept="sound",
    role="多轨压成一条：人声闪避配乐、按偏移叠加、峰值归一化，出成片 WAV、波形图与响度报告",
    inputs=[
        Port("tracks", "音频轨", "audio", many=True),
        Port("shots", "分镜表", "shots", required=False),
    ],
    outputs=[Port("audio", "成片音轨", "audio"), Port("images", "波形图", "images"), Port("text", "混音报告", "text")],
    params=[
        Param("master", "总线增益", "number", 1.0, minimum=0.3, maximum=1.6, step=0.05, unit="倍"),
        Param("normalize", "峰值归一化", "toggle", True, hint="归一到 -0.7dBFS 左右"),
        Param("ceiling", "峰值上限", "number", 0.92, minimum=0.5, maximum=0.99, step=0.01, hint="平台二压前留 headroom 就调低"),
        Param("target_s", "输出时长覆盖", "number", 0, minimum=0, maximum=600, step=0.5, unit="秒", hint="留 0 则跟随分镜总时长"),
        Param("stem", "导出分轨", "toggle", False, hint="逐轨另存一份，方便外部 DAW 复查"),
    ],
    icon="🎛️",
    tags=["声音", "混音"],
)
def mixer(ctx: Any) -> dict[str, Any]:
    payloads = [item for item in ctx.many("tracks") if isinstance(item, dict)]
    if not payloads:
        raise NodeError("混音师需要至少一条上游音频轨：接配音 / 音效 / 配乐")
    timeline = ctx.in_("shots") or {}
    shots_total = float(timeline.get("total_s") or 0)
    override = float(ctx.params.get("target_s") or 0)
    total = override or shots_total or max((float(item.get("total_s") or 0) for item in payloads), default=0.0)
    if total <= 0:
        total = max([float(row.get("offset") or 0) + float(row.get("duration_s") or 0) for item in payloads for row in (item.get("items") or [])] or [1.0])
    master = float(ctx.params.get("master") or 1.0)
    ceiling = float(ctx.params.get("ceiling") or 0.92)

    tracks: list[dict[str, Any]] = []
    missing: list[str] = []
    for payload in payloads:
        for row in payload.get("items") or []:
            ref = str(row.get("ref") or "")
            if not ref:
                missing.append(str(row.get("id") or row.get("label") or "无引用轨"))
                continue
            try:
                samples = audio_lib.read_wav(ctx.read_artifact(ref))
            except Exception as error:  # noqa: BLE001 - 单轨读不回不能拖垮整条混音
                missing.append(f"{row.get('id')}（{error}）")
                continue
            tracks.append(
                {
                    "kind": str(row.get("kind") or payload.get("kind") or "sfx"),
                    "samples": [value * master for value in samples],
                    "offset": float(row.get("offset") or 0.0),
                    "gain": float(row.get("gain") or 1.0),
                    "sidechain": float(row.get("sidechain") or 1.0),
                }
            )
    if not tracks:
        raise NodeError("没有任何音轨读得回来：" + "、".join(missing[:4]))

    mixed = audio_lib.mix(tracks, total_seconds=round(total, 3), normalize=bool(ctx.params.get("normalize")))
    if not bool(ctx.params.get("normalize")):
        peak = max((abs(value) for value in mixed), default=0.0)
        if peak > ceiling:
            mixed = [value * (ceiling / peak) for value in mixed]
    blob = audio_lib.wav_bytes(mixed)
    saved = ctx.save_wav("mix-master.wav", blob)
    peaks = audio_lib.peaks_of(mixed, 600)
    png = canvas.waveform_png(peaks, (125, 211, 252), 760, 160)
    sheet = common.image_artifact(
        ctx,
        "mix-waveform.png",
        png,
        {"kind": "waveform", "id": "mix-waveform", "prompt": f"{len(tracks)} 轨混音波形", "seed": 0, "simulated": True, "provider": "canvas"},
    )

    stem_refs: list[str] = []
    if bool(ctx.params.get("stem")):
        for index, track in enumerate(tracks):
            item = ctx.save_wav(f"stem-{index + 1}-{track['kind']}.wav", audio_lib.wav_bytes(track["samples"]))
            stem_refs.append(item["url"])

    peak = max((abs(value) for value in mixed), default=0.0)
    rms = audio_lib.rms_of(mixed)
    kinds: dict[str, int] = {}
    for track in tracks:
        kinds[track["kind"]] = kinds.get(track["kind"], 0) + 1
    payload = {
        "kind": "master",
        "items": [{"kind": "voice", "id": "master", "label": "成片音轨", "offset": 0.0, "gain": 1.0, "sidechain": 1.0, "duration_s": round(audio_lib.seconds_of(mixed), 3), "provider": "sample", "simulated": True, **saved}],
        "total_s": round(audio_lib.seconds_of(mixed), 3),
        "tracks": len(tracks),
        "kinds": kinds,
        "peak_db": round(audio_lib.db_of(peak), 1),
        "rms_db": round(audio_lib.db_of(rms), 1),
        "peaks": peaks[:240],
        "waveform": sheet,
        "stems": stem_refs,
        "missing": missing,
        "simulated": True,
    }
    text = "\n".join(
        [
            f"混音报告：{len(tracks)} 轨 → {round(audio_lib.seconds_of(mixed), 2)}s（目标 {round(total, 2)}s）",
            "轨道构成：" + "、".join(f"{kind} {count}" for kind, count in sorted(kinds.items())),
            f"峰值 {payload['peak_db']}dBFS｜RMS {payload['rms_db']}dBFS｜总线 {master}×｜归一化 {'开' if ctx.params.get('normalize') else '关（上限 ' + str(ceiling) + '）'}",
            ("⚠ 读不回：" + "、".join(missing[:6])) if missing else "全部轨道读回正常",
            (f"分轨 {len(stem_refs)} 条已另存" if stem_refs else "未导出分轨"),
        ]
    )
    ctx.log(f"混音：{len(tracks)} 轨 / {len(mixed)} 采样 / 峰值 {payload['peak_db']}dBFS")
    return {"audio": payload, "images": {"items": [sheet]}, "text": text}
