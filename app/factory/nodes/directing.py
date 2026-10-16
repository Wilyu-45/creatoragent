"""导演部门岗位：导演阐述、分镜师、场记、选角导演、第一副导演、表演指导、特技协调。

分镜师是全厂的核心枢纽：它把文学剧本翻成「一镜一卡」的可执行工单，
下游美术、摄影、灯光、生成、声音、后期全部按这份 ``shots`` 取数。
第一副导演把这张表排成通告表（拍摄单元、架设与转场工时、演员集合时间），
表演指导则逐句给出意图、潜台词、重音与气口——数字人演员直接消费这份注释。
特技协调从同一张表里挑出动作镜，配替身、护具、威亚与医护，把动作工时写回通告表。
"""

from __future__ import annotations

import re
from typing import Any

from ...knowledge.visual import visual_styles_for
from .. import canvas, craft, media
from ..spec import NodeError, Param, Port, node
from . import common

#: 一条对白超过这个字数就拆成正反打两镜（分镜师的常规做法）
_REVERSE_SHOT_CHARS = 26


@node(
    "director",
    title="导演阐述",
    dept="directing",
    role="定调整：视觉风格、镜头节奏、色彩与声音意向、参考片，产出全厂统一的美学契约（look）",
    inputs=[
        Port("brief", "项目需求", "brief"),
        Port("outline", "故事大纲", "outline", required=False),
        Port("screenplay", "剧本", "screenplay", required=False),
        Port("text", "参考片笔记", "text", required=False, many=True),
    ],
    outputs=[Port("look", "视觉基调", "look"), Port("text", "导演阐述", "text")],
    params=[
        Param("style", "视觉风格", "select", "documentary", common.style_options(), hint="来自知识库的可视化风格：构图、布光、配色、提示词片段"),
        Param("pacing", "镜头节奏", "select", "中速", ["慢速", "中速", "快速", "混合"], hint="决定平均镜头时长与转场密度"),
        Param("mood", "情绪基调", "select", "auto", ["auto", "neutral", "warm", "cool", "noir", "vintage", "punch", "soft", "dream", "tense"]),
        Param("aspect", "画幅覆盖", "text", "", hint="留空则跟随项目输入"),
        Param("camera_bias", "机位偏好", "select", "auto", ["auto", "静态为主", "手持呼吸感", "轨道运动", "无人机大景"]),
        Param("refs", "参考片", "textarea", "", hint="每行一部，写进阐述里给全组对齐"),
        Param("sound_intent", "声音意向", "text", "", hint="例如「只做低频氛围，不铺旋律」"),
    ],
    icon="🎥",
    tags=["导演", "定调"],
)
def director(ctx: Any) -> dict[str, Any]:
    brief = common.norm_brief(ctx.in_("brief"), {})
    outline = ctx.in_("outline") or {}
    screenplay = ctx.in_("screenplay") or {}
    notes = [str(item) for item in ctx.many("text")]
    style_key = str(ctx.params.get("style") or "documentary")
    candidates = visual_styles_for(str(brief.get("genre") or "消费品"))
    if style_key == "auto" and candidates:
        style_key = candidates[0].key
    mood = str(ctx.params.get("mood") or "auto")
    if mood == "auto":
        beats = [str(scene.get("beat") or "") for scene in screenplay.get("scenes") or []]
        mood = common.mood_of(beats[len(beats) // 2] if beats else str(outline.get("conflict") or ""), str(brief.get("tone")))
    aspect = str(ctx.params.get("aspect") or "").strip() or str(brief.get("aspect") or "9:16")

    look = common.look_from_style(style_key, mood, aspect)
    pacing = str(ctx.params.get("pacing") or "中速")
    look["avg_shot_s"] = {"慢速": 4.6, "中速": 3.1, "快速": 1.9, "混合": 2.8}[pacing]
    look["cut_rate"] = {"慢速": 0.18, "中速": 0.42, "快速": 0.78, "混合": 0.55}[pacing]
    look["camera_bias"] = str(ctx.params.get("camera_bias") or "auto")
    look["sound_intent"] = str(ctx.params.get("sound_intent") or "").strip() or "口播清晰优先，配乐压在 -12dB，转场点放音效"
    look["aspect"] = aspect
    look["title"] = outline.get("title") or brief.get("title") or ""
    look["references"] = [item for item in (line.strip() for line in str(ctx.params.get("refs") or "").splitlines()) if item] + notes[:3]
    look["mood_by_act"] = {
        str(scene.get("act") or craft.act_of(0.5)): common.mood_of(str(scene.get("beat") or ""), str(brief.get("tone")))
        for scene in screenplay.get("scenes") or []
    }
    look["engine"] = "knowledge+rules"
    look["simulated"] = not media.llm_ready()

    text = "\n".join(
        [
            f"风格：{look['style_label']}（{look['composition']}；布光 {look['lighting']}）",
            f"节奏：{pacing}，平均镜头 {look['avg_shot_s']}s，转场密度 {look['cut_rate']:.0%}",
            f"色彩：{' / '.join(look['palette'])}，主色 {look['accent']}，调色 {look['grade'].get('filter')}",
            f"机位：{look['camera_bias']}；颗粒 {look['grain']}，暗角 {look['vignette']}",
            f"声音：{look['sound_intent']}",
            "参考：" + ("；".join(look["references"]) or "未指定"),
            "分幕情绪：" + ("；".join(f"{k}→{v}" for k, v in look["mood_by_act"].items()) or "跟随全局基调 " + str(look["mood"])),
        ]
    )
    ctx.log(f"导演阐述完成：{look['style_label']}｜{look['mood']}｜{aspect}")
    return {"look": look, "text": text}


@node(
    "storyboard",
    title="分镜师",
    dept="directing",
    role="把剧本拆成一镜一卡：景别、焦段、机位角度、运镜、时长、转场与提示词，并出分镜草图页",
    inputs=[
        Port("screenplay", "剧本", "screenplay"),
        Port("look", "视觉基调", "look", required=False),
        Port("brief", "项目需求", "brief", required=False),
        Port("characters", "人物", "characters", required=False),
    ],
    outputs=[
        Port("shots", "分镜表", "shots"),
        Port("images", "分镜草图", "images"),
        Port("text", "分镜表文本", "text"),
    ],
    params=[
        Param("max_shot_s", "单镜最长", "number", 5, minimum=1.5, maximum=12, step=0.5, unit="秒"),
        Param("split_dialogue", "长对白拆正反打", "toggle", True),
        Param("lock_to_script", "锁回剧本时长", "toggle", True, hint="按剧本总长等比微调每镜时长，避免分镜超片"),
        Param("sketch", "画分镜草图页", "toggle", True),
        Param("sketch_width", "草图宽度", "number", 560, minimum=240, maximum=1200, step=40, unit="px"),
        Param("draft", "本数", "number", 1, minimum=1, maximum=9, step=1),
    ],
    icon="🎞️",
    tags=["分镜", "核心"],
)
def storyboard(ctx: Any) -> dict[str, Any]:
    screenplay = common.require_screenplay(ctx.in_("screenplay"), "分镜师")
    look = common.look_of(ctx.in_("look"))
    brief = common.norm_brief(ctx.in_("brief"), {})
    characters = common.characters_of(ctx.in_("characters"))
    known = [str(item.get("name") or "") for item in characters if item.get("name")]
    aspect = str(look.get("aspect") or brief.get("aspect") or "9:16")
    max_shot = float(ctx.params.get("max_shot_s") or 5)
    seed_text = f"{screenplay.get('title')}|{len(screenplay.get('scenes') or [])}|{ctx.params.get('draft')}"
    total_scenes = max(1, len(screenplay.get("scenes") or []))

    shots: list[dict[str, Any]] = []
    for scene in screenplay.get("scenes") or []:
        beat = str(scene.get("beat") or craft.beats_for(len(shots), total_scenes * 2))
        energy = common.energy_of(beat)
        dialogue = common.dialogue_lines(scene)
        location = str(scene.get("location") or "场景")
        cast = [str(item) for item in scene.get("characters") or []]
        base = {
            "scene_id": str(scene.get("id") or ""),
            "scene_title": str(scene.get("title") or location),
            "location": location,
            "int_ext": str(scene.get("int_ext") or "INT"),
            "time_of_day": str(scene.get("time_of_day") or "day"),
            "beat": beat,
            "act": str(scene.get("act") or craft.act_of(0.5)),
            "mood": str(scene.get("mood") or look.get("mood") or "neutral"),
            "characters": cast,
            "props": [str(item) for item in scene.get("props") or []],
            "energy": energy,
        }
        plan: list[dict[str, Any]] = []

        def solo_of(name: str) -> str:
            return name if name not in {"旁白", "画外音", "VO"} else ""

        if dialogue:
            for line in dialogue:
                long_line = len(line["line"]) > _REVERSE_SHOT_CHARS
                if bool(ctx.params.get("split_dialogue")) and long_line:
                    chunks = craft.split_dialogue(line["line"]) or [line["line"]]
                    for chunk_index, chunk in enumerate(chunks):
                        plan.append(
                            {
                                "dialogue": [{"character": line["character"], "line": chunk}],
                                "who": line["character"],
                                "solo": solo_of(line["character"]),
                                "tail": chunk_index == len(chunks) - 1,
                            }
                        )
                else:
                    plan.append({"dialogue": [line], "who": line["character"], "solo": solo_of(line["character"]), "tail": True})
        else:
            lead = next((str(item) for item in known if item in cast), cast[0] if cast else "")
            plan.append({"dialogue": [], "who": "", "solo": lead, "tail": True})
            narration = craft.sentences(str(scene.get("action") or ""))
            if narration:
                plan.append({"dialogue": [{"character": "旁白", "line": narration[0]}], "who": "旁白", "solo": "", "tail": True, "vo_only": True})

        previous_size = ""
        for index, item in enumerate(plan):
            size = craft.shot_size_for(index, len(plan), beat, bool(item["dialogue"]), seed_text)
            if look.get("camera_bias") == "静态为主" and size in {"extreme_wide", "wide"}:
                size = "medium"
            movement = craft.movement_for(size, beat, energy, seed_text)
            if look.get("camera_bias") == "静态为主":
                movement = "static" if index % 2 == 0 else movement
            elif look.get("camera_bias") == "手持呼吸感":
                movement = "handheld"
            elif look.get("camera_bias") == "无人机大景" and size in {"extreme_wide", "wide"}:
                movement = "crane"
            angle = craft.angle_for(beat, seed_text, index)
            speech = round(sum(craft.speech_seconds(str(row["line"])) for row in item["dialogue"]), 2)
            # 台词时长是硬下限：压镜头等于把返工留给后期配音，宁可超目标时长也要让人声说得完
            # 余量按配音导演的同一口径给（入话留白 + 台词占比上限），转场镜再多留一口气
            duration = round(max(1.6, (craft.HEAD_GAP_S + speech) / craft.SPEECH_SHARE + 0.2 + (0.35 if item["tail"] else 0.0)), 2) if speech > 0 else round(max(1.6, min(max_shot, float(look.get("avg_shot_s") or 3.1))), 2)
            transition = craft.transition_for(previous_size, size, beat, energy, seed_text) if index else "cut"
            previous_size = size
            speech_lines = item["dialogue"]
            shot = {
                **base,
                "id": f"shot-{len(shots) + 1}",
                "size": size,
                "size_label": craft.SHOT_SIZES[size]["label"],
                "lens_mm": craft.SHOT_SIZES[size]["lens"],
                "angle": angle,
                "angle_label": craft.CAMERA_ANGLES[angle],
                "movement": movement,
                "movement_label": craft.CAMERA_MOVEMENTS[movement]["label"],
                "duration_s": duration,
                "speech_s": speech,
                "transition": transition,
                "transition_label": craft.TRANSITIONS[transition]["label"],
                "transition_duration": craft.TRANSITIONS[transition]["cost"],
                "dialogue": speech_lines,
                "speaker": item["who"],
                "focus_character": item["solo"],
                "description": (
                    str(scene.get("action") or location)
                    if index == 0 and not speech_lines
                    else (f"{item['who']}：{speech_lines[0]['line']}" if speech_lines else str(scene.get("action") or location))
                )[:220],
                "slate": f"{base['scene_id'] or 'SC'}-{index + 1:02d}",
                "aspect": aspect,
                "mood": common.mood_of(beat, str(brief.get("tone"))) if item.get("vo_only") else str(scene.get("mood") or look.get("mood") or "neutral"),
            }
            shots.append(shot)

    # 锁时长：往短收时不得跌破台词下限，收不下就如实报冲突
    script_total = float(screenplay.get("total_s") or 0)
    lock_conflict = 0.0
    if bool(ctx.params.get("lock_to_script")) and script_total > 0 and shots:
        raw = sum(float(shot["duration_s"]) for shot in shots)
        factor = script_total / max(0.1, raw)
        if factor < 1.0:
            for shot in shots:
                floor = round(max(0.8, (craft.HEAD_GAP_S + float(shot.get("speech_s") or 0.0)) / craft.SPEECH_SHARE), 2)
                shot["duration_s"] = round(max(floor, float(shot["duration_s"]) * factor), 2)
            lock_conflict = round(max(0.0, sum(float(shot["duration_s"]) for shot in shots) - script_total), 2)

    for shot in shots:
        shot["prompt"] = common.shot_prompt(shot, look, [{"name": shot.get("focus_character") or ""}])
        shot["negative"] = common.negative_prompt(look)
        grade = craft.grade_for(str(shot.get("mood") or look.get("mood") or "neutral"))
        shot["grade"] = {"filter": grade["filter"], "temp": grade["temp"], "contrast": grade["contrast"], "sat": grade["sat"]}
        rng = craft.rng_for(seed_text, shot["id"], "kb")
        shot["kenburns"] = {"dir": rng.choice(["in", "out", "left", "right"]), "amount": round(0.03 + rng.random() * float(craft.CAMERA_MOVEMENTS[shot["movement"]]["speed"]), 3)}
        shot["fps"] = 24
    timeline_payload = common.ensure_timeline({"shots": shots, "aspect": aspect})
    target = float(screenplay.get("target_s") or brief.get("duration_s") or 0)
    drift = round(float(timeline_payload["total_s"]) - target, 2) if target else 0.0

    images: list[dict[str, Any]] = []
    if bool(ctx.params.get("sketch")):
        png = canvas.storyboard_sheet(shots, look, str(timeline_payload.get("title") or screenplay.get("title") or "分镜表"), aspect)
        item = common.image_artifact(
            ctx,
            f"storyboard-{common.slug(str(screenplay.get('title') or 'sheet'))}-d{ctx.params.get('draft')}.png",
            png,
            {"kind": "storyboard", "id": "storyboard-sheet", "prompt": f"{len(shots)} 镜分镜表", "seed": 0, "simulated": True, "provider": "canvas"},
        )
        images.append(item)

    timeline_payload.update(
        {
            "title": screenplay.get("title") or brief.get("title") or "未命名",
            "fps": 24,
            "count": len(shots),
            "sheet_url": images[0]["url"] if images else "",
            "sheet_ref": images[0]["ref"] if images else "",
            "target_s": target,
            "drift_s": drift,
            "lock_conflict_s": lock_conflict,
            "engine": "storyboard-rules",
            "simulated": True,
        }
    )
    text = "\n".join(
        [
            f"分镜表：{len(shots)} 镜 / {timeline_payload['total_s']}s（目标 {target:.0f}s，偏差 {drift:+.1f}s）",
            *(
                [
                    f"⚠ 锁时长冲突：台词下限合计比剧本多 {lock_conflict}s，需回编剧减词或接受成片变长"
                ]
                if lock_conflict > 0.05
                else []
            ),
            *[
                f"{shot['slate']} {shot['size_label']}|{craft.CAMERA_ANGLES[shot['angle']]}|{shot['movement_label']}|{shot['lens_mm']}mm|{shot['duration_s']}s|{shot['transition_label']}｜{shot['description'][:34]}"
                for shot in shots
            ],
        ]
    )
    ctx.log(f"分镜完成：{len(shots)} 镜 / {timeline_payload['total_s']}s", shots=len(shots))
    return {"shots": timeline_payload, "images": {"items": images}, "text": text}


@node(
    "script_supervisor",
    title="场记",
    dept="directing",
    role="连贯性守门人：查轴线、跳切、道具与服装接续、时段跳跃，出正式场记单与镜头清单",
    inputs=[Port("shots", "分镜表", "shots"), Port("screenplay", "剧本", "screenplay", required=False), Port("characters", "人物", "characters", required=False)],
    outputs=[Port("report", "连贯性报告", "report"), Port("shots", "标注分镜表", "shots"), Port("text", "场记单", "text")],
    params=[
        Param("axis_check", "轴线检查", "toggle", True),
        Param("prop_check", "道具接续", "toggle", True),
        Param("warn_same_size", "同景别提示阈值", "number", 2, minimum=1, maximum=6, step=1, unit="连镜"),
    ],
    icon="📑",
    tags=["场记", "连贯"],
)
def script_supervisor(ctx: Any) -> dict[str, Any]:
    payload = common.require_shots(ctx.in_("shots"), "场记")
    shots = common.shots_of(payload)
    screenplay = ctx.in_("screenplay") or {}
    characters = {str(item.get("name")): item for item in common.characters_of(ctx.in_("characters"))}
    issues: list[dict[str, Any]] = []
    notes: list[str] = []
    axis = ""
    same_run = 0

    for index, shot in enumerate(shots):
        shot["continuity"] = []
        previous = shots[index - 1] if index else None
        if previous:
            if previous.get("scene_id") != shot.get("scene_id"):
                axis = ""
                notes.append(f"{shot['slate']}：换场（{previous.get('location')} → {shot.get('location')}），轴线重置")
                if previous.get("time_of_day") != shot.get("time_of_day") and previous.get("beat") == shot.get("beat"):
                    issues.append(
                        {
                            "severity": "major",
                            "category": "时间",
                            "detail": f"{previous['slate']}→{shot['slate']} 时段从 {previous.get('time_of_day')} 跳到 {shot.get('time_of_day')} 但仍在同一节拍",
                            "suggestion": "插入过渡镜（窗外光线变化）或把该镜归入下一场",
                        }
                    )
            else:
                if bool(ctx.params.get("axis_check")):
                    prev_side = str(previous.get("focus_character") or "")
                    here_side = str(shot.get("focus_character") or "")
                    if prev_side and here_side and prev_side != here_side:
                        if axis and axis != here_side:
                            issues.append(
                                {
                                    "severity": "major",
                                    "category": "轴线",
                                    "detail": f"{shot['slate']} 反打跨过 180° 轴线（{prev_side} ↔ {here_side}）",
                                    "suggestion": "加中立镜（正面或空镜）过渡，或统一对话双方的画面左右关系",
                                }
                            )
                        axis = here_side
                if bool(ctx.params.get("prop_check")):
                    lost = [item for item in (previous.get("props") or []) if item not in (shot.get("props") or [])]
                    if lost and shot.get("beat") == previous.get("beat"):
                        issues.append(
                            {
                                "severity": "minor",
                                "category": "道具",
                                "detail": f"{shot['slate']} 少了上一镜在场的道具：{'、'.join(str(item) for item in lost[:3])}",
                                "suggestion": "补回道具或在分镜里写明它被收走",
                            }
                        )
            if previous.get("size") == shot.get("size"):
                same_run += 1
                limit = int(float(ctx.params.get("warn_same_size") or 2))
                if same_run >= limit:
                    issues.append(
                        {
                            "severity": "minor",
                            "category": "跳切",
                            "detail": f"自 {shots[index - same_run]['slate']} 起连续 {same_run + 1} 镜同为 {shot.get('size_label')}",
                            "suggestion": "拉开一档景别或改变机位角度，避免视觉跳切",
                        }
                    )
                    same_run = 0
            else:
                same_run = 0
        who = str(shot.get("focus_character") or "")
        if who and who in characters:
            shot["wardrobe"] = str(characters[who].get("wardrobe") or characters[who].get("look") or "")
        if str(shot.get("beat") or "") in {"hook", "cta"} and shot.get("duration_s", 0) > 4.5:
            issues.append(
                {"severity": "minor", "category": "节奏", "detail": f"{shot['slate']} 处在 {shot['beat']} 却长达 {shot['duration_s']}s", "suggestion": "钩子与行动指令要短，拆成两镜或压到 3s 内"}
            )
        shot["continuity"].append(f"接 {previous['slate'] if previous else '开机'}")

    for scene in screenplay.get("scenes") or []:
        covered = [shot for shot in shots if shot.get("scene_id") == str(scene.get("id"))]
        if not covered:
            issues.append(
                {"severity": "blocker", "category": "漏拍", "detail": f"场 {scene.get('index')}（{scene.get('location')}）没有任何分镜覆盖", "suggestion": "回到分镜师节点补该场，或确认它已被删场"}
            )

    score = max(0, min(100, 100 - sum({"blocker": 30, "major": 12, "minor": 4, "info": 0}[str(item["severity"])] for item in issues)))
    report = {
        "score": score,
        "verdict": "pass" if score >= 85 else ("revise" if score >= 60 else "reject"),  # type: ignore[assignment]
        "issues": issues,
        "summary": f"场记检查：{len(shots)} 镜，{len(issues)} 处连贯性问题",
        "notes": notes[:20],
        "simulated": True,
    }
    payload = {**payload, "continuity_score": score, "continuity_issues": len(issues)}
    text = "\n".join(
        [
            report["summary"],
            *[f"! [{item['severity']}] {item['detail']} → {item['suggestion']}" for item in issues],
            "机位备注：" + ("；".join(notes[:6]) or "无跨场备注"),
        ]
    )
    ctx.log(report["summary"], level="warn" if issues else "info")
    return {"report": report, "shots": payload, "text": text}


@node(
    "casting",
    title="选角导演",
    dept="directing",
    role="把人物小传落成可选的演员方案：真人 / 数字人两条路，配音色、戏份统计与通告表",
    inputs=[Port("characters", "人物", "characters"), Port("screenplay", "剧本", "screenplay", required=False), Port("shots", "分镜表", "shots", required=False)],
    outputs=[Port("characters", "演员表", "characters"), Port("text", "通告表", "text")],
    params=[
        Param("mode", "演出形式", "select", "混合", ["真人实拍", "数字人", "混合"]),
        Param("max_cast", "最多角色数", "number", 6, minimum=1, maximum=12, step=1),
        Param("voice_by_role", "按行当配音色", "toggle", True),
        Param(
            "pick_voice",
            "指定旁白音色",
            "select",
            "auto",
            ["auto"] + list(craft.VOICE_PRESETS.keys()),
            hint="、".join(f"{key}={value['label']}" for key, value in craft.VOICE_PRESETS.items()),
        ),
    ],
    icon="🎭",
    tags=["选角", "配音"],
)
def casting(ctx: Any) -> dict[str, Any]:
    screenplay = ctx.in_("screenplay") or {}
    from_screenplay = common.pick_characters(screenplay) if screenplay.get("scenes") else []
    characters = common.characters_of(ctx.in_("characters")) or from_screenplay
    if not characters:
        characters = [{"name": "主角", "role_type": "protagonist", "lines": 0, "appearances": 0}]
    shots = common.shots_of(ctx.in_("shots"))
    mode = str(ctx.params.get("mode") or "混合")
    limit = int(float(ctx.params.get("max_cast") or 6))
    lines_by_name: dict[str, int] = {}
    for group in (shots or screenplay.get("scenes") or []):
        for row in group.get("dialogue") or []:
            name = str(row.get("character") or "")
            lines_by_name[name] = lines_by_name.get(name, 0) + 1

    cast: list[dict[str, Any]] = []
    for index, raw in enumerate(characters[:limit]):
        item = dict(raw)
        name = str(item.get("name") or f"角色{index + 1}")
        spoken = int(lines_by_name.get(name, 0) or item.get("lines") or 0)
        appearances = int(item.get("appearances") or len([shot for shot in shots if name in (shot.get("characters") or [])]) or 1)
        role_type = str(item.get("role_type") or ("narrator" if name in {"旁白", "画外音", "VO"} else "support"))
        picked = str(ctx.params.get("pick_voice") or "")
        if role_type == "narrator" and picked and picked != "auto":
            voice_key = picked
        elif bool(ctx.params.get("voice_by_role")) and item.get("voice_type"):
            voice_key = str(item["voice_type"])
        else:
            voice_key = craft.ROLE_VOICE.get(role_type, "narrator_female" if role_type == "narrator" else "warm_female")
        preset = craft.VOICE_PRESETS.get(voice_key, craft.VOICE_PRESETS["warm_female"])
        kind = "digital" if mode == "数字人" else ("live" if mode == "真人实拍" else ("digital" if name in {"旁白", "画外音", "VO"} or spoken == 0 else "live"))
        cast.append(
            {
                **item,
                "name": name,
                "role_type": role_type,
                "cast_kind": kind,
                "spoken_lines": spoken,
                "appearances": appearances,
                "weight": round(min(1.0, (spoken * 0.06 + appearances * 0.08)), 3),
                "voice": {"key": voice_key, **preset},
                "avatar_id": f"dh-{common.slug(name)}" if kind == "digital" else "",
                "call": f"通告：{name}｜{'配音棚' if kind == 'digital' else '现场'}｜{max(1, round(spoken / 12 + 1))} 小时",
            }
        )
    text = "\n".join(
        [
            f"演员表（{mode}）：{len(cast)} 人",
            *[
                f"· {item['name']}〔{item['role_type']}/{item['cast_kind']}〕台词 {item['spoken_lines']} 句 出场 {item['appearances']} 次 音色 {item['voice']['label']} 权重 {item['weight']}"
                for item in cast
            ],
            *[item["call"] for item in cast],
        ]
    )
    ctx.log(f"选角完成：{len(cast)} 个角色（数字人 {sum(1 for item in cast if item['cast_kind'] == 'digital')}）")
    return {"characters": cast, "text": text}


#: 承载方式 → 铺设与转场耗时（分钟）：第一副导演据此算每个拍摄单元的架设时间
_RIG_EXTRA_MIN: dict[str, float] = {
    "tripod": 3,
    "handheld": 5,
    "gimbal": 5,
    "slider": 15,
    "steadicam": 20,
    "dolly": 35,
    "crane": 45,
    "drone": 60,
}

#: 没接摄影方案时按镜头运动估算铺设时间（口径与摄影指导的承载表对齐）
_MOVE_EXTRA_MIN: dict[str, float] = {
    "static": 3,
    "pan": 4,
    "tilt": 4,
    "whip": 6,
    "handheld": 5,
    "push_in": 15,
    "pull_out": 15,
    "follow": 20,
    "track": 35,
    "orbit": 35,
    "crane": 45,
}

#: 一条镜头拍完后的复位、回放与换机位固定开销（分钟/条）
_PER_TAKE_MIN = 0.8

#: 节拍 → （台词意图, 体态口径）
_INTENT_BY_BEAT: dict[str, tuple[str, str]] = {
    "hook": ("三秒内把问题抛出来，让人没法划走", "身体前倾、直视镜头、句首就砸重音"),
    "setup": ("交代人物与处境，让观众跟得上", "站定放松、手势指认环境、语速沉下来"),
    "develop": ("一层层加码，把信息推到位", "重心前移、语速抬半档、句间不留缝"),
    "tension": ("制造压力与悬念", "收紧下颌、缩短气口、句尾微微上扬"),
    "payoff": ("给出答案，把爽点落地", "打开胸腔、笑容落地、句尾往下砸"),
    "cta": ("说清下一步动作，让人照着做", "直视镜头、放慢半拍、指令短促"),
}

#: 节拍 → （嘴角基线, 重心摆动, 表情口径）：数字人演员按这三项驱动胸像
_FACE_BY_BEAT: dict[str, tuple[float, float, str]] = {
    "hook": (0.18, 0.55, "眉峰挑起、嘴角压平，先给一个「等等」的表情"),
    "setup": (0.32, 0.35, "表情松开，目光跟着被介绍的物体走一遍"),
    "develop": (0.36, 0.45, "边说边点头，眉毛在关键词上抬一下"),
    "tension": (0.08, 0.62, "下颌收紧、少眨，身体前倾半寸"),
    "payoff": (0.62, 0.42, "笑在答案之后半拍落地，肩线打开"),
    "cta": (0.48, 0.28, "正对镜头，收尾定住不动，只留一句"),
}

#: 潜台词底稿，再按句尾语气改写
_SUBTEXT_BY_BEAT: dict[str, str] = {
    "hook": "心里想的是「你信不信」，不是在念稿",
    "setup": "心里在替观众问「这跟我有什么关系」",
    "develop": "心里想的是「还没说完，别急」",
    "tension": "心里在赌对方会不会先眨眼",
    "payoff": "心里清楚自己已经赢了，不用再证明",
    "cta": "心里只关心观众会不会真的去按",
}

#: 台词里天然的重音词
_PUNCH_WORDS = ("别", "不要", "一定", "必须", "现在", "马上", "其实", "真正", "只有", "才是", "所有", "一次", "记住", "为什么", "怎么", "自己")

_QUESTION = re.compile(r"[?？]\s*$|(吗|呢)\s*[?？。]?\s*$|^(为什么|怎么|怎样|是否|能不能)")
_CALL = re.compile(r"(请|记得|别|赶紧|马上|试试|点|关注|收藏|跟着做)")
_FORCE = re.compile(r"(一定|必须|其实|真正|只有|才是|根本|绝对)")
_TONE_NOTE = {
    "疑问": "要的是一个态度，不是一个答案",
    "号召": "话落地就要观众动手",
    "强调": "重音压住，别让它飘",
    "迸发": "情绪比理智快半拍",
    "陈述": "把信息说干净，别加戏",
}


def _clock(minutes: float) -> str:
    """把「当天第几分钟」写成通告表上的钟点（09:00 这种）。"""
    total = int(round(max(0.0, float(minutes))))
    return f"{total // 60:02d}:{total % 60:02d}"


def _speech_act(text: str) -> str:
    if _QUESTION.search(text):
        return "疑问"
    if _FORCE.search(text):
        return "强调"
    if _CALL.search(text):
        return "号召"
    if text.rstrip().endswith(("！", "!")):
        return "迸发"
    return "陈述"


def _emphasis_of(text: str, energy: float) -> list[str]:
    hits = [word for word in _PUNCH_WORDS if word in text]
    if not hits:
        longest = max(craft.sentences(text) or [text], key=len)
        hits = [longest.rstrip("，、。！？")[-3:]]
    keep = max(1, min(3, 1 + int(energy * 2)))
    return [item for item in hits if item][:keep]


def _subtext_of(text: str, beat: str) -> str:
    base = _SUBTEXT_BY_BEAT.get(beat, _SUBTEXT_BY_BEAT["develop"])
    tone = _TONE_NOTE.get(_speech_act(text), "")
    tail = text.rstrip()[-1:]
    return f"{base}｜{tone}" + ("（句尾收住，往下砸）" if tail in {"。", "！"} else "")


@node(
    "assistant_director",
    title="第一副导演",
    dept="directing",
    role="把分镜表排成通告表：并场成拍摄单元、按承载方式算架设与转场工时、切分工作日，出演员集合时间与超时风险",
    inputs=[
        Port("shots", "分镜表", "shots"),
        Port("camera_plan", "摄影方案", "camera_plan", required=False),
        Port("characters", "演员表", "characters", required=False),
        Port("locations", "场景表", "locations", required=False),
        Port("stunts", "动作段落", "plan", required=False),
    ],
    outputs=[Port("plan", "通告表", "plan"), Port("text", "通告说明", "text")],
    params=[
        Param("day_hours", "每个工作日", "number", 10, minimum=6, maximum=14, step=0.5, unit="小时", hint="超过 12 小时的日排会被标成工时风险"),
        Param("call_hour", "每日开工", "number", 9, minimum=5, maximum=13, step=0.5, unit="时", hint="通告表上的 0 号钟点"),
        Param("setup_min", "单元基础架设", "number", 20, minimum=0, maximum=90, step=5, unit="分钟", hint="机器进场、收线、安全交底等固定开销"),
        Param("takes", "每镜条数", "number", 3, minimum=1, maximum=8, step=1, unit="条"),
        Param("move_min", "转场", "number", 25, minimum=0, maximum=90, step=5, unit="分钟", hint="相邻拍摄单元换地点的固定代价"),
        Param("hair_min", "妆发", "number", 45, minimum=0, maximum=180, step=5, unit="分钟", hint="演员集合时间 = 单元开工时间往前推这段"),
        Param("merge", "并场拍摄", "toggle", True, hint="按场地/时段重排单元以减少转场；关掉则严格按分镜顺序"),
        Param("show", "列出单元", "number", 14, minimum=1, maximum=60, step=1, unit="个"),
    ],
    icon="⏱️",
    tags=["现场", "排期", "通告表"],
)
def assistant_director(ctx: Any) -> dict[str, Any]:
    timeline = common.require_shots(ctx.in_("shots"), "第一副导演")
    shots = common.shots_of(timeline)
    camera_rows = {str(row.get("shot_id") or row.get("slate") or ""): row for row in common.items_of(ctx.in_("camera_plan"))}
    stunts = ctx.in_("stunts") if isinstance(ctx.in_("stunts"), dict) else {}
    stunt_min = {str(key): float(value or 0) for key, value in (stunts.get("minutes_by_slate") or {}).items()}
    roster = common.characters_of(ctx.in_("characters"))
    scouted = {str(row.get("name") or ""): row for row in common.locations_of(ctx.in_("locations"))}
    day_hours = float(ctx.params.get("day_hours") or 10)
    call_hour = float(ctx.params.get("call_hour") or 9)
    setup_min = float(ctx.params.get("setup_min") or 20)
    takes = int(float(ctx.params.get("takes") or 3))
    move_min = float(ctx.params.get("move_min") or 25)
    hair_min = float(ctx.params.get("hair_min") or 45)
    merge = bool(ctx.params.get("merge"))
    show = int(float(ctx.params.get("show") or 14))
    day_cap = call_hour * 60 + day_hours * 60

    units: dict[str, dict[str, Any]] = {}
    for index, shot in enumerate(shots):
        scene_id = str(shot.get("scene_id") or "S0")
        int_ext = str(shot.get("int_ext") or "")
        time_of_day = str(shot.get("time_of_day") or "day")
        location = str(shot.get("location") or "")
        slate = str(shot.get("slate") or shot.get("id") or f"shot-{index + 1}")
        unit = units.setdefault(
            f"{scene_id}|{int_ext}|{time_of_day}|{location}",
            {
                "unit": "",
                "scene_id": scene_id,
                "scene_title": str(shot.get("scene_title") or ""),
                "int_ext": int_ext,
                "time_of_day": time_of_day,
                "location": location,
                "slates": [],
                "shot_ids": [],
                "duration_s": 0.0,
                "rigs": [],
                "extra_min": 0.0,
                "stunt_min": 0.0,
                "lines_by_char": {},
                "present": [],
                "first_index": index,
            },
        )
        unit["slates"].append(slate)
        unit["shot_ids"].append(str(shot.get("id") or slate))
        unit["duration_s"] = round(unit["duration_s"] + float(shot.get("duration_s") or 2.0), 2)
        row = camera_rows.get(str(shot.get("id") or "")) or camera_rows.get(slate) or {}
        rig_key = str(row.get("rig") or "")
        if rig_key:
            extra = _RIG_EXTRA_MIN.get(rig_key, 10.0)
            unit["rigs"].append(str(row.get("rig_label") or rig_key))
        else:
            extra = _MOVE_EXTRA_MIN.get(str(shot.get("movement") or "static"), 8.0)
            unit["rigs"].append(str(shot.get("movement_label") or "固定"))
        unit["extra_min"] = max(unit["extra_min"], extra)
        unit["stunt_min"] = round(float(unit["stunt_min"]) + float(stunt_min.get(slate) or 0), 1)
        for name in [str(item or "") for item in (shot.get("characters") or []) if str(item or "")]:
            if name not in unit["present"]:
                unit["present"].append(name)
        for line in shot.get("dialogue") or []:
            name = str(line.get("character") or "")
            if name:
                unit["lines_by_char"][name] = int(unit["lines_by_char"].get(name, 0)) + 1

    ordered = list(units.values())
    if merge:
        ordered.sort(key=lambda item: (str(item["int_ext"]), str(item["location"]), str(item["time_of_day"]), int(item["first_index"])))
    for position, unit in enumerate(ordered):
        unit["unit"] = f"U{position + 1:02d}"
        unit["rigs"] = sorted(set(unit["rigs"]))
        unit["cast"] = sorted(unit["present"]) or sorted(unit["lines_by_char"])
        unit["lines"] = sum(unit["lines_by_char"].values())
        unit["setup_min"] = round(setup_min + float(unit["extra_min"]) + float(unit["stunt_min"]), 1)
        unit["shoot_min"] = round(unit["duration_s"] * takes / 60.0 + len(unit["slates"]) * takes * _PER_TAKE_MIN, 1)
        unit["est_min"] = round(float(unit["setup_min"]) + float(unit["shoot_min"]), 2)
        scene = scouted.get(str(unit["location"])) or {}
        unit["night_ext"] = unit["int_ext"].upper().startswith("EXT") and unit["time_of_day"] in {"night", "dusk"}
        access = float(scene.get("access_min") or 0)
        permit = scene.get("permit") if isinstance(scene.get("permit"), dict) else {}
        unit["access_min"] = round(access, 1)
        unit["setup_min"] = round(float(unit["setup_min"]) + access, 1)
        unit["est_min"] = round(float(unit["setup_min"]) + float(unit["shoot_min"]), 2)
        unit["location_status"] = str(scene.get("status") or "未勘")
        unit["note"] = "；".join(
            [
                part
                for part in [
                    str(scene.get("mood") or ""),
                    f"动作排练与保护 {float(unit['stunt_min']):.0f} 分钟已计入架设" if unit["stunt_min"] else "",
                    f"进场装卸 {access:.0f} 分钟已计入架设" if access else "",
                    f"许可：{permit.get('kind')}（{permit.get('lead_days', 0)} 天）" if permit.get("kind") else "",
                    str(scene.get("noise") or "") if unit["night_ext"] else "",
                    "夜外景：灯光组按夜景布光" if unit["night_ext"] else "",
                ]
                if part
            ]
        )

    days: list[dict[str, Any]] = []
    day: dict[str, Any] = {"day": 1, "start_clock": _clock(call_hour * 60), "units": [], "minutes": 0.0, "moves": 0.0}
    previous_location = ""
    for unit in ordered:
        move = move_min if (previous_location and unit["location"] and unit["location"] != previous_location) else 0.0
        if day["units"] and float(day["minutes"]) + move + float(unit["est_min"]) > day_hours * 60:
            day["wrap_clock"] = _clock(float(day["end_min"]))
            days.append(day)
            day = {"day": len(days) + 1, "start_clock": _clock(call_hour * 60), "units": [], "minutes": 0.0, "moves": 0.0}
            move = 0.0
            previous_location = ""
        unit["day"] = int(day["day"])
        unit["move_min"] = round(move, 1)
        unit["start_min"] = round(call_hour * 60 + float(day["minutes"]) + move, 1)
        unit["end_min"] = round(float(unit["start_min"]) + float(unit["est_min"]), 1)
        unit["call_clock"] = _clock(float(unit["start_min"]))
        unit["wrap_clock"] = _clock(float(unit["end_min"]))
        unit["set_call_clock"] = _clock(max(0.0, float(unit["start_min"]) - hair_min))
        day["units"].append(str(unit["unit"]))
        day["minutes"] = round(float(day["minutes"]) + move + float(unit["est_min"]), 2)
        day["moves"] = round(float(day["moves"]) + move, 1)
        day["end_min"] = float(unit["end_min"])
        previous_location = str(unit["location"]) or previous_location
    if day["units"]:
        day["wrap_clock"] = _clock(float(day["end_min"]))
        days.append(day)

    by_name: dict[str, list[dict[str, Any]]] = {}
    for unit in ordered:
        for name in unit["cast"]:
            by_name.setdefault(name, []).append(unit)
    cast_calls: list[dict[str, Any]] = []
    for name, rows in by_name.items():
        item = next((row for row in roster if str(row.get("name")) == name), {})
        span = max(float(row["end_min"]) for row in rows) - min(float(row["start_min"]) for row in rows)
        cast_calls.append(
            {
                "character": name,
                "kind": str(item.get("cast_kind") or ""),
                "voice": str((item.get("voice") or {}).get("label") or ""),
                "units": len(rows),
                "days": sorted({int(row["day"]) for row in rows}),
                "lines": sum(int(row["lines_by_char"].get(name, 0)) for row in rows),
                "set_call": min(str(row["set_call_clock"]) for row in rows),
                "first_call": str(rows[0]["call_clock"]),
                "last_wrap": max(str(row["wrap_clock"]) for row in rows),
                "on_set_min": round(span, 1),
            }
        )
    cast_calls.sort(key=lambda row: (row["days"][0], row["set_call"]))

    total_min = round(sum(float(unit["est_min"]) for unit in ordered) + sum(float(unit["move_min"]) for unit in ordered), 1)
    long_days = [row for row in days if float(row["minutes"]) > 12 * 60]
    scene_days: dict[str, set[int]] = {}
    for unit in ordered:
        scene_days.setdefault(str(unit["scene_id"]), set()).add(int(unit["day"]))
    split_scenes = sorted(key for key, value in scene_days.items() if len(value) > 1)
    risks: list[str] = []
    for row in long_days[:3]:
        risks.append(f"第 {row['day']} 天排到 {round(float(row['minutes']) / 60, 1)} 小时，超 12 小时工时红线")
    for key in split_scenes[:3]:
        risks.append(f"场 {key} 被拆到第 {sorted(scene_days[key])} 天拍摄：连贯性由场记盯，服装/道具要封存")
    if merge and ordered and [int(item["first_index"]) for item in ordered] != sorted(int(item["first_index"]) for item in ordered):
        risks.append("已并场重排：拍摄顺序与成片顺序不同，分镜表里的接戏备注要带到现场")
    drone_night = [str(unit["unit"]) for unit in ordered if unit["night_ext"] and "无人机" in unit["rigs"]]
    if drone_night:
        risks.append(f"无人机夜航单元 {('、'.join(drone_night[:3]))}：空域报备与额外照明另计")
    if not camera_rows:
        risks.append("没接摄影方案：架设工时按镜头运动粗估，接上「摄影指导」后会更贴近现场")
    if not scouted:
        risks.append("没接场地经理的勘景表：进场装卸与许可窗口都按零算，现场容易卡在手续上")
    else:
        pending_sites = sorted({str(unit["location"]) for unit in ordered if str(unit.get("location_status")) in {"待许可", "本轮无镜头"}})
        if pending_sites:
            risks.append(f"场地手续未落：{'、'.join(pending_sites[:4])}——本表按能批下来排，批不下要整天重排")
    ctx.progress(1.0, "通告表已出")
    plan = {
        "dept": "directing",
        "role": "第一副导演",
        "items": ordered,
        "days": days,
        "cast_calls": cast_calls,
        "unit_count": len(ordered),
        "total_days": len(days),
        "total_minutes": total_min,
        "setup_minutes": round(sum(float(unit["setup_min"]) for unit in ordered), 1),
        "access_minutes": round(sum(float(unit.get("access_min") or 0) for unit in ordered), 1),
        "stunt_minutes": round(sum(float(unit.get("stunt_min") or 0) for unit in ordered), 1),
        "shoot_minutes": round(sum(float(unit["shoot_min"]) for unit in ordered), 1),
        "move_minutes": round(sum(float(unit["move_min"]) for unit in ordered), 1),
        "hair_minutes": hair_min,
        "day_hours": day_hours,
        "call_hour": call_hour,
        "takes": takes,
        "merged": merge,
        "night_ext_units": len([unit for unit in ordered if unit["night_ext"]]),
        "risks": risks or ["排期在工时红线内，无跨天分场"],
        "simulated": True,
    }
    text = "\n".join(
        [
            f"通告表｜{len(ordered)} 个拍摄单元 / {len(days)} 天 / 共 {round(total_min / 60, 1)} 工时（每日 {_clock(call_hour * 60)} 开工，{day_hours} 小时，每镜 {takes} 条）",
            *[
                f"· 第 {row['day']} 天：{row['start_clock']}–{row['wrap_clock']}｜{len(row['units'])} 个单元 {row['minutes']} 分钟｜{'、'.join(row['units'])}"
                for row in days
            ],
            *[
                f"· {unit['unit']}〔场 {unit['scene_id']} {unit['int_ext']}·{unit['time_of_day']}〕{unit['location'] or '未定场地'}｜{len(unit['slates'])} 镜 {unit['duration_s']}s｜架设 {unit['setup_min']}′ 拍摄 {unit['shoot_min']}′｜{unit['call_clock']}→{unit['wrap_clock']}｜演员集合 {unit['set_call_clock']}｜{'、'.join(unit['cast']) or '空镜'}"
                for unit in ordered[:show]
            ],
            ("演员通告：" + "｜".join(f"{row['character']} {row['set_call']} 集合（{row['units']} 个单元 {row['lines']} 句，在场 {row['on_set_min']}′）" for row in cast_calls[:8]) if cast_calls else "本轮无演员到场需求"),
            "⚠ " + "；".join(risks[:4] or ["无排期风险"]),
            (f"还有 {max(0, len(ordered) - show)} 个单元未列出（调「列出单元」参数）" if len(ordered) > show else ""),
        ]
    )
    ctx.log(f"通告表：{len(ordered)} 个单元 / {len(days)} 天，演员 {len(cast_calls)} 人")
    return {"plan": plan, "text": text}


@node(
    "acting_coach",
    title="表演指导",
    dept="directing",
    role="逐句给表演注释：台词意图、潜台词、重音、气口、体态与表情口径，并核对台词能不能装进这一镜",
    inputs=[
        Port("shots", "分镜表", "shots"),
        Port("characters", "演员表", "characters", required=False),
        Port("look", "视觉基调", "look", required=False),
    ],
    outputs=[Port("plan", "表演注释", "plan"), Port("text", "排练工单", "text")],
    params=[
        Param("style", "表演体系", "select", "方法派", ["方法派", "表现派", "纪实", "口播主播"], hint="决定给的是情绪记忆还是外部动作口径"),
        Param("energy", "能量加成", "range", 0.5, minimum=0, maximum=1, step=0.05, hint="0 收着演、1 放开：影响重音密度与气口取舍"),
        Param("head_gap", "句头留白", "number", 0.15, minimum=0, maximum=0.6, step=0.05, unit="秒"),
        Param("tempo_cap", "语速上限", "number", 1.25, minimum=0.8, maximum=1.8, step=0.05, unit="×", hint="超过这个倍速观众听不清，判定为装不进镜头"),
        Param("max_lines", "最多标注", "number", 24, minimum=1, maximum=80, step=1, unit="句"),
    ],
    icon="🎯",
    tags=["表演", "排练", "台词"],
)
def acting_coach(ctx: Any) -> dict[str, Any]:
    timeline = common.require_shots(ctx.in_("shots"), "表演指导")
    shots = common.shots_of(timeline)
    roster = {str(row.get("name") or ""): row for row in common.characters_of(ctx.in_("characters"))}
    look = common.look_of(ctx.in_("look"))
    style = str(ctx.params.get("style") or "方法派")
    energy = float(ctx.params.get("energy") or 0.5)
    head_gap = float(ctx.params.get("head_gap") or 0.15)
    cap = float(ctx.params.get("tempo_cap") or 1.25)
    limit = int(float(ctx.params.get("max_lines") or 24))
    style_note = {
        "方法派": "先找动机再找动作：每句问一遍「我要对方做什么」",
        "表现派": "外部先行：手势与走位按口径摆，情绪靠节拍托出来",
        "纪实": "不许演：像没在镜头前说话，允许停顿和半个词",
        "口播主播": "对视镜头说话，句子之间不留空白，信息密度优先",
    }.get(style, "先找动机再找动作")

    rows: list[dict[str, Any]] = []
    for shot_index, shot in enumerate(shots):
        beat = str(shot.get("beat") or craft.beats_for(shot_index, len(shots)))
        intent, posture = _INTENT_BY_BEAT.get(beat, _INTENT_BY_BEAT["develop"])
        smile, sway, face = _FACE_BY_BEAT.get(beat, _FACE_BY_BEAT["develop"])
        mood = str(shot.get("mood") or look.get("mood") or "neutral")
        duration = float(shot.get("duration_s") or 2.4)
        slate = str(shot.get("slate") or shot.get("id") or f"shot-{shot_index + 1}")
        for line in shot.get("dialogue") or []:
            text = str(line.get("line") or "").strip()
            if not text:
                continue
            name = str(line.get("character") or shot.get("speaker") or "旁白")
            voice = roster.get(name, {}).get("voice") or {}
            rate = float(voice.get("rate") or 1.0)
            est = round(craft.speech_seconds(text, rate) + head_gap, 2)
            available = round(max(0.4, duration - head_gap), 2)
            needed = round(est / available, 3)
            clauses = [item for item in craft.sentences(text) if item]
            breaths = max(0, len(clauses) - 1)
            tight = needed > 1.0
            tempo = min(cap, max(1.0, needed))
            rows.append(
                {
                    "slate": slate,
                    "shot_id": str(shot.get("id") or slate),
                    "shot_index": shot_index,
                    "character": name,
                    "line": text[:60],
                    "beat": beat,
                    "mood": mood,
                    "speech_act": _speech_act(text),
                    "intent": intent,
                    "subtext": _subtext_of(text, beat),
                    "posture": posture,
                    "emphasis": _emphasis_of(text, energy),
                    "breath": f"{breaths} 个气口（每段约 {round(est / max(1, len(clauses)), 2)}s）" + ("｜压掉气口" if tempo > 1.05 else ""),
                    "est_s": est,
                    "available_s": available,
                    "needed": needed,
                    "tempo": round(tempo, 3),
                    "fit": not tight,
                    "smile": round(min(1.0, max(0.0, smile + (energy - 0.5) * 0.18)), 3),
                    "sway": round(min(1.0, max(0.0, sway * (0.75 + energy * 0.5))), 3),
                    "face": face,
                    "style_note": style_note,
                }
            )
        if len(rows) >= limit:
            break
    if not rows:
        raise NodeError("表演指导没有可指导的台词：分镜表里一句台词都没有")
    rows = rows[:limit]

    tight_rows = [row for row in rows if not row["fit"]]
    by_beat: dict[str, int] = {}
    for row in rows:
        by_beat[str(row["beat"])] = by_beat.get(str(row["beat"]), 0) + 1
    plan = {
        "dept": "directing",
        "role": "表演指导",
        "items": rows,
        "style": style,
        "energy": energy,
        "tempo_cap": cap,
        "lines": len(rows),
        "shots": len({str(row["slate"]) for row in rows}),
        "tight": len(tight_rows),
        "by_beat": by_beat,
        "simulated": True,
    }
    text = "\n".join(
        [
            f"排练工单｜{len(rows)} 句 / {plan['shots']} 镜｜体系：{style}｜能量 {energy:.2f}｜口径：{style_note}",
            *[
                f"· {row['slate']}〔{row['character']}〕{row['line'][:20]}……｜{row['beat']}·{row['speech_act']}｜意图：{row['intent']}"
                f"｜潜台词：{row['subtext']}｜重音：{'、'.join(row['emphasis'])}｜{row['posture']}｜{row['breath']}"
                for row in rows[:12]
            ],
            ("⚠ " + f"{len(tight_rows)} 句装不进镜头：" + "；".join(f"{row['slate']} {row['needed']}×（{row['est_s']}s 塞进 {row['available_s']}s）" for row in tight_rows[:5]) if tight_rows else "台词长度都在镜头容差内，不用减词"),
            ("数字人演员会按这份注释逐镜改嘴角与重心（表情口径见 face 字段）" if any(row["smile"] for row in rows) else "表情基线全为闭合，数字人按默认口径"),
            (f"还有 {max(0, len(rows) - 12)} 句未列出（调「最多标注」参数）" if len(rows) > 12 else ""),
        ]
    )
    ctx.log(f"表演注释：{len(rows)} 句，{len(tight_rows)} 句超镜头容量")
    return {"plan": plan, "text": text}


#: 动作口径表：命中关键词 → 风险档、装备、保护方式、单镜额外架设秒数与现场规矩
_ACTION_KINDS: list[tuple[tuple[str, ...], dict[str, Any]]] = [
    (("跑", "追", "冲", "奔"), {"name": "奔跑追逐", "risk": "medium", "gear": ["防滑鞋", "跟拍稳定器"], "rig": "无", "setup_s": 60, "rule": "路面先清障，跟拍路线与演员路线分开走"}),
    (("跳", "跃", "跨", "落"), {"name": "跳跃落地", "risk": "high", "gear": ["落地垫", "护膝", "护腕"], "rig": "低威亚保护", "setup_s": 180, "rule": "落点铺垫并反复试跳，落地瞬间一律替身"}),
    (("翻", "滚", "摔", "倒", "绊"), {"name": "翻滚摔跌", "risk": "high", "gear": ["摔跌垫", "护脊", "头盔内衬"], "rig": "垫上保护", "setup_s": 210, "rule": "摔点做标记，一条一复位，禁止连拍"}),
    (("打", "拳", "脚", "斗", "搏", "砍", "推搡", "扭"), {"name": "近身打斗", "risk": "medium", "gear": ["护具", "软质道具"], "rig": "无", "setup_s": 240, "rule": "走位排练成型才开机，机位避开真接触角度"}),
    (("枪", "刀", "刃", "弓", "棍"), {"name": "器械动作", "risk": "high", "gear": ["道具管制单", "护目"], "rig": "无", "setup_s": 150, "rule": "锐器由道具师点名收发，不得带出场外"}),
    (("车", "摩托", "自行车", "驾驶", "漂移", "撞"), {"name": "载具动作", "risk": "high", "gear": ["灭火器", "安全带切割器", "引导车"], "rig": "特技车 + 拖车", "setup_s": 420, "rule": "封路与引导车到位才动车，驾驶动作由特技替身完成"}),
    (("水", "溺", "泳", "潜", "雨", "湿"), {"name": "水域 / 湿身", "risk": "high", "gear": ["救生员", "保温毯", "防滑垫"], "rig": "水下保护绳", "setup_s": 300, "rule": "岸边救生员在场，演员单次入水限时，镜头分段拍"}),
    (("火", "烟", "爆", "炸", "燃"), {"name": "烟火 / 爆破", "risk": "critical", "gear": ["防火服", "灭火毯", "安全员"], "rig": "持证烟火作业", "setup_s": 600, "rule": "持证烟火师点火，拍摄区与观演区隔离，一次只爆一个点"}),
    (("高空", "楼顶", "天台", "攀", "悬", "梯", "窗", "吊"), {"name": "高空作业", "risk": "critical", "gear": ["双钩安全带", "防坠器", "地面警戒"], "rig": "高空威亚", "setup_s": 540, "rule": "威亚由持证师傅挂点，地面拉警戒线，禁止在垂直下方走位"}),
]
_RISK_MULT = {"medium": 1.0, "high": 1.4, "critical": 2.0}
_RISK_LABEL = {"medium": "中风险", "high": "高风险", "critical": "极高风险"}
_RISK_INSURANCE = {"medium": 0.0, "high": 1200.0, "critical": 2600.0}
_SEVERITY_PENALTY = {"blocker": 30, "major": 12, "minor": 4, "info": 0}


@node(
    "stunt_coordinator",
    title="特技协调",
    dept="directing",
    role="把分镜里的动作变成能安全拍出来的段落：分派替身、护具与威亚，排排练与安全交底，配医护与保险，并把动作工时回灌通告表",
    inputs=[
        Port("shots", "分镜表", "shots"),
        Port("characters", "演员表", "characters", required=False),
        Port("brief", "项目需求", "brief", required=False),
    ],
    outputs=[
        Port("plan", "动作段落清单", "plan"),
        Port("report", "安全评估", "report"),
        Port("text", "动作说明", "text"),
    ],
    params=[
        Param("takes", "动作镜条数", "number", 2, minimum=1, maximum=6, step=1, unit="条", hint="动作镜比对话镜更吃条数，一次只推进一个动作"),
        Param("rehearse_min", "段落排练", "number", 40, minimum=0, maximum=180, step=5, unit="分钟", hint="按风险档放大：高风险 1.4×、极高风险 2×"),
        Param("briefing_min", "安全交底", "number", 10, minimum=0, maximum=60, step=5, unit="分钟"),
        Param("medic", "医护待命", "select", "高危段落", ["全程", "高危段落", "不需要"]),
        Param("rig_height_m", "威亚起吊高度", "number", 4.0, minimum=0, maximum=20, step=0.5, unit="米", hint="超过 3 米按高空作业算，需双钩与地面警戒"),
        Param("max_seq", "列出段落", "number", 8, minimum=1, maximum=30, step=1, unit="个"),
    ],
    icon="🤸",
    tags=["特技", "动作", "安全"],
)
def stunt_coordinator(ctx: Any) -> dict[str, Any]:
    board = common.require_shots(ctx.in_("shots"), "特技协调")
    shots = common.shots_of(board)
    brief = common.norm_brief(ctx.in_("brief"), {})
    roster = {str(row.get("name")): row for row in common.characters_of(ctx.in_("characters"))}
    takes = int(float(ctx.params.get("takes") or 2))
    rehearse_base = float(ctx.params.get("rehearse_min") or 40)
    briefing = float(ctx.params.get("briefing_min") or 10)
    medic = str(ctx.params.get("medic") or "高危段落")
    rig_height = float(ctx.params.get("rig_height_m") or 4)

    flagged: list[dict[str, Any]] = []
    for position, shot in enumerate(shots):
        words = " ".join(
            [
                str(shot.get("description") or ""),
                str(shot.get("mood") or ""),
                " ".join(str(item) for item in (shot.get("props") or [])),
                str(shot.get("location") or ""),
            ]
        )
        hits = [row[1] for row in _ACTION_KINDS if any(key in words for key in row[0])]
        if not hits:
            continue
        flagged.append(
            {
                "index": int(shot.get("index") if shot.get("index") is not None else position),
                "slate": str(shot.get("slate") or shot.get("id") or f"shot-{position + 1}"),
                "scene_id": str(shot.get("scene_id") or "S0"),
                "duration_s": round(float(shot.get("duration_s") or 2.0), 2),
                "cast": [str(item) for item in (shot.get("characters") or [])],
                "kinds": hits,
            }
        )

    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for row in flagged:
        if current and (row["scene_id"] != current[-1]["scene_id"] or row["index"] != current[-1]["index"] + 1):
            groups.append(current)
            current = []
        current.append(row)
    if current:
        groups.append(current)

    minutes_by_slate: dict[str, float] = {}
    seq_rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for position, group in enumerate(groups, start=1):
        kinds: list[dict[str, Any]] = []
        for row in group:
            for hit in row["kinds"]:
                if not any(str(item["name"]) == str(hit["name"]) for item in kinds):
                    kinds.append(hit)
        risk = max((str(item["risk"]) for item in kinds), key=lambda key: {"medium": 1, "high": 2, "critical": 3}[key])
        mult = _RISK_MULT[risk]
        cast = sorted({name for row in group for name in row["cast"]})
        doubles: list[dict[str, Any]] = []
        for name in cast:
            kind = str((roster.get(name) or {}).get("cast_kind") or "live")
            if kind == "digital":
                doubles.append({"character": name, "mode": "CG 演绎", "note": "数字人不承担真身风险，动作由关键帧与运镜合成，防护预算转给渲染工时"})
            else:
                doubles.append({"character": name, "mode": "主演 + 特技替身", "note": "全景用主演，落地/入水/近身接触一律替身"})
        real_doubles = len([row for row in doubles if row["mode"] == "主演 + 特技替身"])
        rehearse = round(rehearse_base * mult * max(1, len(kinds)), 1)
        setup_min = round(sum(float(item["setup_s"]) for item in kinds) / 60.0 * len(group), 1)
        shoot_min = round(sum(float(row["duration_s"]) for row in group) * takes * (1.0 + 0.35 * (len(kinds) - 1)) / 60.0 + len(group) * takes * 0.6, 1)
        insurance = round(_RISK_INSURANCE[risk] * max(1, real_doubles), 2)
        per_shot = round((rehearse + briefing) / max(1, len(group)), 1)
        for row in group:
            minutes_by_slate[row["slate"]] = round(minutes_by_slate.get(row["slate"], 0.0) + per_shot + setup_min / max(1, len(group)), 1)
        if risk == "critical" and medic == "不需要":
            issues.append({"severity": "blocker", "category": "安全", "detail": f"段落 {position}（{'、'.join(str(item['name']) for item in kinds)}）属极高风险，但医护待命设为不需要", "suggestion": "改为高危段落或全程待命，否则不能开机"})
        if rig_height >= 3 and not any(str(item["rig"]) != "无" for item in kinds):
            issues.append({"severity": "minor", "category": "威亚", "detail": f"起吊高度设到 {rig_height} 米，但段落 {position} 没有需要威亚的动作", "suggestion": "把高度降到实际挂点，避免虚增架设时间"})
        if len(kinds) > 1:
            issues.append({"severity": "major", "category": "复合动作", "detail": f"段落 {position} 同时含 {'、'.join(str(item['name']) for item in kinds)}，一次交底覆盖不住", "suggestion": "拆成两个段落分别排练，或按风险从高到低逐个开机"})
        if not roster and cast:
            issues.append({"severity": "minor", "category": "数据", "detail": f"段落 {position} 有 {len(cast)} 名演员但没接演员表，替身只能按全真人估算", "suggestion": "把选角导演的演员表接到本节点"})
        seq_rows.append(
            {
                "seq": position,
                "scene_id": group[0]["scene_id"],
                "slates": [row["slate"] for row in group],
                "kinds": [str(item["name"]) for item in kinds],
                "risk": risk,
                "risk_label": _RISK_LABEL[risk],
                "shot_count": len(group),
                "screen_s": round(sum(float(row["duration_s"]) for row in group), 2),
                "cast": cast,
                "doubles": doubles,
                "gear": sorted({str(g) for item in kinds for g in item["gear"]}),
                "rig": " / ".join(sorted({str(item["rig"]) for item in kinds if str(item["rig"]) != "无"})) or "无",
                "rehearse_min": rehearse,
                "briefing_min": briefing,
                "setup_min": setup_min,
                "shoot_min": shoot_min,
                "insurance": insurance,
                "medic": medic == "全程" or (medic == "高危段落" and risk in {"high", "critical"}),
                "rules": [str(item["rule"]) for item in kinds],
            }
        )

    if not flagged:
        issues.append({"severity": "info", "category": "覆盖", "detail": "本轮分镜里没有命中任何动作口径，无需特技组进场", "suggestion": "若确有动作，请在分镜描述里写清动作动词，特技协调才能识别"})
    total_min = round(sum(float(row["rehearse_min"]) + float(row["briefing_min"]) + float(row["setup_min"]) for row in seq_rows), 1)
    score = max(0, min(100, 100 - sum(_SEVERITY_PENALTY[str(item["severity"])] for item in issues)))
    report = {
        "kind": "safety",
        "score": score,
        "verdict": "pass" if score >= 85 else ("revise" if score >= 60 else "reject"),
        "issues": issues,
        "summary": f"动作安全评估：{len(seq_rows)} 个段落 / {len(flagged)} 镜，最高风险 {max((row['risk'] for row in seq_rows), key=lambda key: {'medium': 1, 'high': 2, 'critical': 3}[key], default='medium')}",
        "simulated": True,
    }
    plan = {
        "dept": "directing",
        "role": "特技协调",
        "items": seq_rows[: int(float(ctx.params.get("max_seq") or 8))],
        "sequences": len(seq_rows),
        "stunt_shots": len(flagged),
        "shot_count": len(shots),
        "kinds": sorted({str(name) for row in seq_rows for name in row["kinds"]}),
        "doubles": sum(len([d for d in row["doubles"] if d["mode"] == "主演 + 特技替身"]) for row in seq_rows),
        "digital_actions": sum(len([d for d in row["doubles"] if d["mode"] == "CG 演绎"]) for row in seq_rows),
        "rehearse_minutes": round(sum(float(row["rehearse_min"]) for row in seq_rows), 1),
        "briefing_minutes": round(sum(float(row["briefing_min"]) for row in seq_rows), 1),
        "setup_minutes": round(sum(float(row["setup_min"]) for row in seq_rows), 1),
        "shoot_minutes": round(sum(float(row["shoot_min"]) for row in seq_rows), 1),
        "extra_minutes": total_min,
        "insurance": round(sum(float(row["insurance"]) for row in seq_rows), 2),
        "medic_units": len([row for row in seq_rows if row["medic"]]),
        "rig_height_m": rig_height,
        "takes": takes,
        "minutes_by_slate": minutes_by_slate,
        "genre": str(brief.get("genre") or ""),
        "risks": [str(item["detail"]) for item in issues if item["severity"] in {"blocker", "major"}][:6] or ["动作段落都有对应保护与排练"],
        "simulated": True,
    }
    risk_mix = "、".join(f"{row['risk_label']}×{row['shot_count']} 镜" for row in seq_rows) or "无"
    text = "\n".join(
        [
            f"动作设计：{len(shots)} 镜里识别出 {len(flagged)} 个动作镜，并为 {len(seq_rows)} 个段落配保护（排练+交底+架设共 {total_min:.0f} 分钟）",
            f"风险档：{risk_mix}｜保险加项估算 {plan['insurance']:,.0f} 元｜医护待命 {plan['medic_units']} 段",
            f"工时口径：动作镜按 {takes} 条计，单镜另加架设（挂威亚/铺垫/封路），这些分钟数已写进 minutes_by_slate 供第一副导演排进通告表",
            *[f"· 段落 {row['seq']}（场 {row['scene_id']}）{('、'.join(row['kinds']))}｜{row['shot_count']} 镜 {row['screen_s']}s｜{row['risk_label']}｜替身 {len([d for d in row['doubles'] if d['mode'] == '主演 + 特技替身'])} 人｜{row['rig']}｜护具 {'、'.join(row['gear'])}" for row in seq_rows[: int(float(ctx.params.get("max_seq") or 8))]],
            *[f"   规矩：{rule}" for row in seq_rows[:3] for rule in row["rules"][:2]],
            ("⚠ " + "；".join(str(item["detail"]) for item in issues if item["severity"] in {"blocker", "major"})[:2]) if plan["risks"] != ["动作段落都有对应保护与排练"] else "无高危动作缺口，特技组可按本表进场。",
            "结论：数字人演员的动作不进替身与保险口径，改记渲染工时。" if plan["digital_actions"] else "结论：全部动作段落按真人替身与现场保护估算。",
        ]
    )
    ctx.log(f"特技：{len(seq_rows)} 段 / {len(flagged)} 动作镜，额外工时 {total_min:.0f} 分钟，保险 {plan['insurance']:,.0f} 元")
    return {"plan": plan, "report": report, "text": text}
