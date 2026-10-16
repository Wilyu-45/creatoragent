"""摄影部门岗位：摄影指导、灯光师、机械与特殊设备、跟焦员、DIT/数据管理、剧照师。

摄影组拿到分镜表后要做的是「怎么拍出来」：机位、焦段、承载方式、光圈快门、
布光图、设备清单与焦点标记。这些参数直接决定生成模型的运镜提示词与后期调色空间。
素材落地后由 DIT 逐文件校验哈希、尺寸与格式，出备份与代理计划——这是进剪辑间的最后一道闸。
剧照师在片场抓静态：从已落地的画面里挑瞬间、往里取景、定色调、压场记条，出宣发能直接取用的组照。
"""

from __future__ import annotations

import hashlib
import io
import wave
from typing import Any

from .. import canvas, craft
from ..spec import NodeError, Param, Port, node
from . import common

#: 景别 → 常规拍摄距离（米）：跟焦员据此算景深，摄影指导据此选承载方式
_FOCUS_DISTANCE: dict[str, float] = {
    "extreme_wide": 12.0,
    "wide": 8.0,
    "full": 5.0,
    "medium": 3.2,
    "medium_close": 2.0,
    "close": 1.2,
    "extreme_close": 0.55,
    "macro": 0.25,
}

#: 运动 → 承载方式（机械组的租单依据）
_RIG_BY_MOVEMENT: dict[str, str] = {
    "static": "tripod",
    "push_in": "slider",
    "pull_out": "slider",
    "pan": "tripod",
    "tilt": "tripod",
    "track": "dolly",
    "follow": "steadicam",
    "crane": "crane",
    "handheld": "handheld",
    "whip": "tripod",
    "orbit": "dolly",
}

#: 承载方式 → 铺设与人力（租期估算）
_RIG_SETUP: dict[str, dict[str, Any]] = {
    "tripod": {"crew": 1, "hours": 0.3, "note": "配液压云台，快速切换机位"},
    "slider": {"crew": 2, "hours": 0.8, "note": "地面不平需垫板，行程 800mm 起"},
    "dolly": {"crew": 3, "hours": 2.0, "note": "需铺轨道 6m 起，转角加转盘"},
    "crane": {"crew": 3, "hours": 2.5, "note": "配重与安全绳，需要清场半径 4m"},
    "drone": {"crew": 2, "hours": 1.5, "note": "空域报备 + 起飞前检查电池与风速"},
    "steadicam": {"crew": 2, "hours": 1.2, "note": "操作员体力受限，单条不超过 90s"},
    "handheld": {"crew": 1, "hours": 0.2, "note": "备防震背心与广角镜"},
    "gimbal": {"crew": 1, "hours": 0.3, "note": "竖屏专用云台，注意地平线锁定"},
}

#: 时段 → 需要的减光与控光（日光下大光圈必须靠 ND）
_ND_BY_TIME: dict[str, tuple[str, str]] = {
    "dawn": ("ND8", "晨光偏弱，减三档即可"),
    "day": ("ND64", "正午日光下 T2.0 必须减六档"),
    "dusk": ("ND16", "黄金时刻留一档余量"),
    "night": ("无", "夜景不减光，靠灯位与 ISO"),
    "indoors": ("无", "窗光可控，用黑旗而不是减光"),
    "studio": ("无", "棚内灯量自洽"),
}


def _dof(lens_mm: float, distance_m: float, f_stop: float, coc_mm: float = 0.03) -> dict[str, float]:
    """景深计算（薄透镜近似）：焦距 / 物距 / 光圈 → 超焦距、近远界、总景深。"""
    focal = max(4.0, float(lens_mm))
    subject = max(0.15, float(distance_m)) * 1000.0
    aperture = max(0.7, float(f_stop))
    hyperfocal = focal * focal / (aperture * coc_mm) + focal
    if hyperfocal <= 0:
        return {"hyperfocal_m": 0.0, "near_m": subject / 1000.0, "far_m": subject / 1000.0, "depth_m": 0.0}
    denominator_far = hyperfocal - (subject - focal)
    far = subject * (hyperfocal - focal) / denominator_far if denominator_far > 0 else float("inf")
    near = subject * (hyperfocal - focal) / (hyperfocal + (subject - focal))
    depth = (far - near) if far != float("inf") else (20.0 - near / 1000.0)
    return {
        "hyperfocal_m": round(hyperfocal / 1000.0, 2),
        "near_m": round(near / 1000.0, 3),
        "far_m": (round(far / 1000.0, 2) if far != float("inf") else 0.0),
        "depth_m": round(max(0.0, depth / 1000.0), 3),
    }


@node(
    "dop",
    title="摄影指导",
    dept="camera",
    role="逐镜定机位：焦段核验、承载方式、光圈快门、ND 与帧率，产出摄影方案与机位卡",
    inputs=[
        Port("shots", "分镜表", "shots"),
        Port("look", "视觉基调", "look", required=False),
        Port("brief", "项目需求", "brief", required=False),
        Port("art_plan", "美术方案", "plan", required=False),
    ],
    outputs=[Port("camera_plan", "摄影方案", "camera_plan"), Port("images", "机位卡", "images"), Port("text", "摄影阐述", "text")],
    params=[
        Param("aperture", "基准光圈", "select", "T2.8", ["T1.5", "T2.0", "T2.8", "T4.0", "T5.6", "T8.0"], hint="特写偏大光圈，全景收两档"),
        Param("shutter", "快门角度", "select", "180°", ["90°", "172.8°", "180°", "270°"], hint="180° 是标准电影感；90° 动作更 crisp"),
        Param("iso", "感光度", "number", 400, minimum=100, maximum=6400, step=100),
        Param("rig_bias", "承载偏好", "select", "按运动自动", ["按运动自动", "全部三脚架", "尽量手持", "轨道为主"], hint="低成本时可强制降级"),
        Param("cards", "出机位卡", "toggle", True, hint="每镜一张焦段视场图，现场对机位用"),
        Param("card_limit", "机位卡上限", "number", 8, minimum=1, maximum=40, step=1, unit="张"),
        Param("lens_set", "镜头组", "select", "定焦变焦混合", ["定焦组", "变焦组", "老蛙微距", "定焦变焦混合"], hint="决定现场换头次数与连续性风险"),
    ],
    icon="🎦",
    tags=["摄影", "机位"],
)
def dop(ctx: Any) -> dict[str, Any]:
    timeline = common.require_shots(ctx.in_("shots"), "摄影指导")
    shots = common.shots_of(timeline)
    look = common.look_of(ctx.in_("look"))
    brief = common.norm_brief(ctx.in_("brief"), {})
    aspect = str(timeline.get("aspect") or look.get("aspect") or "9:16")
    base_stop = float(str(ctx.params.get("aperture") or "T2.8").replace("T", ""))
    shutter = str(ctx.params.get("shutter") or "180°")
    iso = int(float(ctx.params.get("iso") or 400))
    rig_bias = str(ctx.params.get("rig_bias") or "按运动自动")
    fps = int(float(timeline.get("fps") or 24))

    rows: list[dict[str, Any]] = []
    for shot in shots:
        size = str(shot.get("size") or "medium")
        movement = str(shot.get("movement") or "static")
        lens = float(shot.get("lens_mm") or craft.SHOT_SIZES.get(size, {}).get("lens", 35))
        rig_key = _RIG_BY_MOVEMENT.get(movement, "tripod")
        if rig_bias == "全部三脚架":
            rig_key = "tripod"
        elif rig_bias == "尽量手持":
            rig_key = "handheld" if movement not in {"crane", "drone"} else rig_key
        elif rig_bias == "轨道为主" and movement in {"push_in", "pull_out", "orbit", "track"}:
            rig_key = "dolly"
        time_of_day = str(shot.get("time_of_day") or "day")
        stop = base_stop
        if size in {"full", "wide", "extreme_wide"}:
            stop = min(8.0, base_stop + 2.0)
        elif size in {"extreme_close", "macro"}:
            stop = max(1.4, base_stop - 0.5)
        nd, nd_note = _ND_BY_TIME.get(time_of_day, ("无", ""))
        if rig_key == "drone" and fps > 30:
            fps_note = "无人机高速需 1/100s 以上，注意卷帘"
        else:
            fps_note = ""
        row = {
            "shot_index": shot.get("index"),
            "shot_id": shot.get("id"),
            "slate": str(shot.get("slate") or shot.get("id")),
            "size": size,
            "size_label": str(shot.get("size_label") or craft.SHOT_SIZES.get(size, {}).get("label", size)),
            "lens_mm": lens,
            "distance_m": _FOCUS_DISTANCE.get(size, 3.0),
            "rig": rig_key,
            "rig_label": craft.CAMERA_RIGS[rig_key],
            "movement": movement,
            "movement_label": str(shot.get("movement_label") or craft.CAMERA_MOVEMENTS.get(movement, {}).get("label", movement)),
            "angle": str(shot.get("angle") or "eye"),
            "time_of_day": str(shot.get("time_of_day") or "day"),
            "location": str(shot.get("location") or ""),
            "angle_label": str(shot.get("angle_label") or craft.CAMERA_ANGLES.get(str(shot.get("angle") or "eye"), "平视")),
            "f_stop": round(stop, 1),
            "shutter": shutter,
            "iso": iso,
            "nd": nd,
            "nd_note": nd_note,
            "fps": fps,
            "aspect": aspect,
            "filter": str(shot.get("grade", {}).get("filter") or look.get("grade", {}).get("filter") or "none"),
            "note": (f"{craft.CAMERA_RIGS[rig_key]}：{_RIG_SETUP.get(rig_key, {}).get('note', '')}" + (f"｜{fps_note}" if fps_note else "")),
            "prompt_motion": craft.CAMERA_MOVEMENTS.get(movement, {}).get("prompt", ""),
        }
        rows.append(row)

    lenses = sorted({float(row["lens_mm"]) for row in rows})
    rigs = sorted({str(row["rig_label"]) for row in rows})
    total_hours = round(sum(float(_RIG_SETUP.get(row["rig"], {}).get("hours", 0.3)) for row in rows), 1)
    plan = {
        "dept": "camera",
        "role": "摄影指导",
        "items": rows,
        "count": len(rows),
        "fps": fps,
        "aspect": aspect,
        "lens_range": [lenses[0], lenses[-1]] if lenses else [],
        "lens_set": str(ctx.params.get("lens_set")),
        "rigs": rigs,
        "setup_hours": total_hours,
        "aperture": f"T{base_stop}",
        "shutter": shutter,
        "iso": iso,
        "risks": [
            "换镜头会破坏连续性与色偏，定焦组需预留 20 分钟/次",
            "运动镜头的生成模型提示词与现场承载方式必须一致，否则后期对不上",
            "无人机与摇臂镜头需提前报备空域与清场",
        ],
        "simulated": True,
    }

    images: list[dict[str, Any]] = []
    if bool(ctx.params.get("cards")):
        for row in rows[: int(float(ctx.params.get("card_limit") or 8))]:
            png = canvas.camera_card(row, look, row["slate"])
            images.append(
                common.image_artifact(
                    ctx,
                    f"camera-{common.slug(row['slate'])}.png",
                    png,
                    {"kind": "camera_card", "id": f"camera-{row['shot_id']}", "shot_index": row["shot_index"], "prompt": row["prompt_motion"], "seed": 0, "simulated": True, "provider": "canvas"},
                )
            )

    text = "\n".join(
        [
            f"摄影阐述｜{len(rows)} 镜｜画幅 {aspect}｜{fps}fps｜基准 T{base_stop}｜快门 {shutter}｜ISO {iso}",
            f"焦段范围：{plan['lens_range']}（{plan['lens_set']}）｜承载：{'、'.join(rigs)}",
            f"机位铺设工时估算：{total_hours} 小时（不含打灯）",
            "逐镜机位：" + " / ".join(f"{row['slate']} {row['size_label']} {int(row['lens_mm'])}mm T{row['f_stop']} {row['rig_label']}{' ND' + row['nd'] if row['nd'] != '无' else ''}" for row in rows[:12]),
            "风险提示：" + "；".join(plan["risks"]),
        ]
    )
    ctx.log(f"摄影方案：{len(rows)} 镜 / {len(rigs)} 种承载 / {len(images)} 张机位卡", title=brief.get("title"))
    return {"camera_plan": plan, "images": {"items": images}, "text": text}


@node(
    "gaffer",
    title="灯光师",
    dept="camera",
    role="按时段与情绪出布光方案：灯位、光比、色温、灯纸与功率，附俯视布光图",
    inputs=[
        Port("camera_plan", "摄影方案", "camera_plan"),
        Port("look", "视觉基调", "look", required=False),
        Port("locations", "场景表", "locations", required=False),
    ],
    outputs=[Port("lighting_plan", "灯光方案", "lighting_plan"), Port("images", "布光图", "images"), Port("text", "灯光说明", "text")],
    params=[
        Param("style", "布光风格", "select", "三点布光", ["三点布光", "自然光为主", "高反差 noir", "平光带货"], hint="带货平光压光比，noir 拉光比"),
        Param("ratio", "主辅光比覆盖", "text", "", hint="留空按场景时段自动，例如「4:1」"),
        Param("mood_temp", "色温偏移", "number", 0, minimum=-1200, maximum=1200, step=100, unit="K", hint="整体偏暖/偏冷"),
        Param("diagrams", "出布光图", "toggle", True),
        Param("diagram_limit", "布光图上限", "number", 6, minimum=1, maximum=20, step=1, unit="张"),
        Param("power_w", "单灯功率", "number", 600, minimum=100, maximum=2500, step=100, unit="W", hint="用于算发电与配重"),
    ],
    icon="💡",
    tags=["灯光", "布光"],
)
def gaffer(ctx: Any) -> dict[str, Any]:
    camera = ctx.in_("camera_plan") or {}
    rows = common.items_of(camera)
    if not rows:
        raise NodeError("灯光师需要上游摄影方案：先接「摄影指导」")
    look = common.look_of(ctx.in_("look"))
    locations = {str(item.get("name") or ""): item for item in common.locations_of(ctx.in_("locations"))}
    style = str(ctx.params.get("style") or "三点布光")
    override_ratio = str(ctx.params.get("ratio") or "").strip()
    temp_shift = int(float(ctx.params.get("mood_temp") or 0))
    power = int(float(ctx.params.get("power_w") or 600))

    plans: list[dict[str, Any]] = []
    seen_scene: set[str] = set()
    for row in rows:
        location = locations.get(str(row.get("location") or "")) or {}
        scene_key = str(row.get("location") or row.get("slate") or "")
        time_of_day = str(row.get("time_of_day") or location.get("time_of_day") or "day")
        base = craft.lighting_for(time_of_day, str(location.get("mood") or look.get("mood") or "neutral"))
        ratio = override_ratio or base["ratio"]
        if style == "高反差 noir":
            ratio = "6:1"
        elif style == "平光带货":
            ratio = "1.5:1"
        elif style == "自然光为主":
            ratio = base["ratio"]
        temp = int(base["temp_k"]) + temp_shift + (0 if style != "高反差 noir" else -400)
        lamps = [
            {"name": "KEY", "position": base["direction"], "power_w": power, "gels": base["gels"], "color_temp_k": temp},
            {"name": "FILL", "position": "机位对侧低位", "power_w": max(60, int(power * 0.35)), "gels": "柔光 + 负补光按需", "color_temp_k": temp},
            {"name": "BACK", "position": "主体后上方 45°", "power_w": max(100, int(power * 0.5)), "gels": "无 / CTB（夜景）", "color_temp_k": temp + 600},
        ]
        if style == "平光带货":
            lamps[0]["position"] = "正面双柔光箱 45°（左右对称）"
            lamps[1]["power_w"] = max(60, int(power * 0.8))
        practicals = [str(item) for item in (location.get("props") or []) if any(key in str(item) for key in ("灯", "屏", "窗", "霓虹", "烛"))]
        for prop in practicals[:2]:
            lamps.append({"name": "PRACTICAL", "position": f"画面内可见光源：{prop}", "power_w": 0, "gels": "按实物色温", "color_temp_k": temp})
        plans.append(
            {
                "shot_id": row.get("shot_id"),
                "slate": row.get("slate"),
                "scope": "scene" if scene_key not in seen_scene else "reuse",
                "time_of_day": time_of_day,
                "temp_k": temp,
                "ratio": ratio,
                "direction": base["direction"],
                "gels": base["gels"],
                "note": base["note"],
                "lamps": lamps,
                "total_w": sum(int(lamp["power_w"]) for lamp in lamps),
            }
        )
        seen_scene.add(scene_key)

    unique = [item for item in plans if item["scope"] == "scene"]
    watts = sum(int(item["total_w"]) for item in unique)
    images: list[dict[str, Any]] = []
    if bool(ctx.params.get("diagrams")):
        for item in unique[: int(float(ctx.params.get("diagram_limit") or 6))]:
            png = canvas.lighting_diagram(item, str(item.get("slate") or ""))
            images.append(
                common.image_artifact(
                    ctx,
                    f"lighting-{common.slug(str(item.get('slate') or 'scene'))}.png",
                    png,
                    {"kind": "lighting", "id": f"lighting-{item.get('shot_id')}", "prompt": f"{item['direction']} {item['ratio']} {item['temp_k']}K", "seed": 0, "simulated": True, "provider": "canvas"},
                )
            )

    plan = {
        "dept": "camera",
        "role": "灯光师",
        "items": plans,
        "scenes": len(unique),
        "style": style,
        "total_w": watts,
        "gen_kw": round(watts * 1.25 / 1000.0, 2),
        "risks": [
            "外景日光需备柔光屏与黑旗，只加灯不减光一定会过曝",
            "色温混用要在摄影方案里先统一白平衡口径",
            "棚拍功率超过 3kW 需确认发电与配重",
        ],
        "simulated": True,
    }
    text = "\n".join(
        [
            f"布光方案｜风格：{style}｜涉及 {len(unique)} 个场景设定 / {len(plans)} 镜",
            *[f"· {item['slate']}〔{item['time_of_day']}〕{item['direction']}｜{item['temp_k']}K｜光比 {item['ratio']}｜灯纸 {item['gels']}｜{item['note']}" for item in unique[:8]],
            f"总功率 {watts}W（估算发电 {plan['gen_kw']}kW）",
            "风险：" + "；".join(plan["risks"]),
        ]
    )
    ctx.log(f"灯光方案：{len(unique)} 场 / {len(images)} 张布光图 / {watts}W")
    return {"lighting_plan": plan, "images": {"items": images}, "text": text}


@node(
    "grip",
    title="机械 · 特殊设备",
    dept="camera",
    role="把承载方式变成租单与工序：轨道米数、摇臂配重、空域报备、风机烟雾等特效设备与安全",
    inputs=[
        Port("camera_plan", "摄影方案", "camera_plan"),
        Port("shots", "分镜表", "shots", required=False),
        Port("brief", "项目需求", "brief", required=False),
    ],
    outputs=[Port("plan", "设备与工序清单", "plan"), Port("text", "机械单", "text")],
    params=[
        Param("days", "拍摄天数", "number", 1, minimum=1, maximum=30, step=1, unit="天"),
        Param("fx", "特效设备", "select", "无", ["无", "风机", "烟雾", "雨戏", "旋转台"], hint="按剧情氛围加，需与美术同步"),
        Param("budget_guard", "控成本降级", "toggle", False, hint="把轨道/摇臂降级为滑轨与手持"),
    ],
    icon="🛠️",
    tags=["机械", "现场"],
)
def grip(ctx: Any) -> dict[str, Any]:
    camera = ctx.in_("camera_plan") or {}
    rows = common.items_of(camera)
    if not rows:
        raise NodeError("机械组需要上游摄影方案：先接「摄影指导」")
    days = max(1, int(float(ctx.params.get("days") or 1)))
    fx = str(ctx.params.get("fx") or "无")

    counts: dict[str, int] = {}
    for row in rows:
        rig = str(row.get("rig") or "tripod")
        if bool(ctx.params.get("budget_guard")):
            rig = {"dolly": "slider", "crane": "slider", "drone": "slider"}.get(rig, rig)
        counts[rig] = counts.get(rig, 0) + 1

    items: list[dict[str, Any]] = []
    for rig, use in sorted(counts.items(), key=lambda kv: -kv[1]):
        spec = _RIG_SETUP.get(rig, {"crew": 1, "hours": 0.3, "note": "常规"})
        rail = round(6.0 + 2.0 * use, 1) if rig == "dolly" else (2.4 if rig == "slider" else 0.0)
        items.append(
            {
                "rig": rig,
                "label": craft.CAMERA_RIGS.get(rig, rig),
                "shots": use,
                "units": 1 if use < 8 else 2,
                "crew": int(spec["crew"]),
                "hours_per_setup": float(spec["hours"]),
                "rail_m": rail,
                "note": spec["note"],
                "days": days,
            }
        )

    fx_items = {
        "无": [],
        "风机": [{"name": " axial 风机 + 防风布", "note": "头发与衣摆动势，同步收音需降档"}],
        "烟雾": [{"name": "烟机 2 台 + 烟油", "note": "看体积光必备，注意消防与器材清洁"}],
        "雨戏": [{"name": "雨架 + 水泵 + 地漏", "note": "电气全部抬离地面，备防水胶带"}],
        "旋转台": [{"name": "电动转台（产品）", "note": "需与微距镜头一起测震动"}],
    }.get(fx, [])

    crew_total = sum(int(item["crew"]) for item in items) + 2
    setups = round(sum(float(item["hours_per_setup"]) * (1 if item["shots"] < 8 else 2) for item in items), 1)
    plan = {
        "dept": "camera",
        "role": "机械 · 特殊设备",
        "items": items,
        "fx": fx_items,
        "rail_total_m": round(sum(float(item["rail_m"]) for item in items), 1),
        "crew": crew_total,
        "setup_hours": setups,
        "days": days,
        "risks": [
            "摇臂/无人机镜头需清场与安全员，租金按天算，改期成本最高",
            "轨道铺设每 6m 约 2 小时，转场次数比镜头数更吃工期",
            "雨戏与烟雾会直接影响收音与器材，必须与灯光、录音同步",
        ],
        "simulated": True,
    }
    text = "\n".join(
        [
            f"机械单｜{days} 天｜人力 {crew_total} 人｜铺设 {setups} 小时｜轨道合计 {plan['rail_total_m']}m",
            *[f"· {item['label']}：{item['shots']} 镜 / {item['units']} 台 / {item['crew']} 人｜{item['note']}" for item in items],
            (f"特效设备：{'、'.join(str(row['name']) for row in fx_items)}" if fx_items else "特效设备：无"),
            "安全与工序：" + "；".join(plan["risks"]),
        ]
    )
    ctx.log(f"设备清单：{len(items)} 类承载 / {crew_total} 人 / {setups}h 铺设")
    return {"plan": plan, "text": text}


@node(
    "focus_puller",
    title="跟焦员",
    dept="camera",
    role="逐镜算景深与焦点标记：物距、近远界、总景深、拉风箱风险，给出收圈建议",
    inputs=[Port("camera_plan", "摄影方案", "camera_plan")],
    outputs=[Port("plan", "焦点标记表", "plan"), Port("text", "跟焦笔记", "text")],
    params=[
        Param("coc", "弥散圆", "number", 0.03, minimum=0.01, maximum=0.05, step=0.005, unit="mm", hint="全画幅常规 0.03，超 4K 裁切可收紧"),
        Param("tolerance", "可接受景深下限", "number", 0.06, minimum=0.01, maximum=0.5, step=0.01, unit="m", hint="低于此值判定容易失焦"),
        Param("mark_step", "标记间隔", "number", 0.25, minimum=0.05, maximum=1.0, step=0.05, unit="m", hint="运动镜头在焦点尺上打点的密度"),
    ],
    icon="🔍",
    tags=["摄影", "焦点"],
)
def focus_puller(ctx: Any) -> dict[str, Any]:
    camera = ctx.in_("camera_plan") or {}
    rows = common.items_of(camera)
    if not rows:
        raise NodeError("跟焦需要上游摄影方案：先接「摄影指导」")
    coc = float(ctx.params.get("coc") or 0.03)
    tolerance = float(ctx.params.get("tolerance") or 0.06)
    step = float(ctx.params.get("mark_step") or 0.25)

    marks: list[dict[str, Any]] = []
    warnings: list[str] = []
    for row in rows:
        distance = float(row.get("distance_m") or 3.0)
        lens = float(row.get("lens_mm") or 35)
        stop = float(row.get("f_stop") or 2.8)
        box = _dof(lens, distance, stop, coc)
        movement = str(row.get("movement") or "static")
        moving = movement in {"push_in", "pull_out", "follow", "track", "crane", "orbit", "handheld"}
        points = []
        cursor = max(0.2, box["near_m"])
        while cursor <= (box["far_m"] or box["near_m"]) and len(points) < 6:
            points.append(round(cursor, 2))
            cursor += max(0.05, step)
        risk = "低"
        if box["depth_m"] < tolerance:
            risk = "高（景深过浅，建议收一档或退半步）"
            warnings.append(f"{row.get('slate')}：{int(lens)}mm T{stop} @ {distance}m，景深仅 {round(box['depth_m'] * 100, 1)}cm")
        elif moving and box["depth_m"] < tolerance * 3:
            risk = "中（运动镜头焦点需打点，标记 " + str(len(points)) + " 个）"
        marks.append(
            {
                "shot_id": row.get("shot_id"),
                "slate": row.get("slate"),
                "lens_mm": lens,
                "f_stop": stop,
                "distance_m": distance,
                "near_m": box["near_m"],
                "far_m": box["far_m"],
                "depth_m": box["depth_m"],
                "hyperfocal_m": box["hyperfocal_m"],
                "marks": points,
                "moving": moving,
                "risk": risk,
            }
        )

    thin = [item for item in marks if float(item["depth_m"]) < tolerance]
    plan = {
        "dept": "camera",
        "role": "跟焦员",
        "items": marks,
        "coc_mm": coc,
        "tolerance_m": tolerance,
        "shallow_shots": len(thin),
        "risks": warnings[:8] or ["无高风险镜头"],
        "simulated": True,
    }
    text = "\n".join(
        [
            f"焦点标记表｜{len(marks)} 镜｜弥散圆 {coc}mm｜景深下限 {tolerance}m",
            *[f"· {item['slate']}：{int(item['lens_mm'])}mm T{item['f_stop']} 物距 {item['distance_m']}m → 近 {item['near_m']}m / 远 {item['far_m']}m，景深 {round(item['depth_m'] * 100, 1)}cm｜{item['risk']}" for item in marks[:12]],
            f"高风险镜头 {len(thin)} 个" + (f"：{'；'.join(warnings[:3])}" if warnings else ""),
        ]
    )
    ctx.log(f"跟焦：{len(marks)} 镜，{len(thin)} 镜景深过浅")
    return {"plan": plan, "text": text}


#: 扩展名 → 期望的真实格式（DIT 按文件头复核，不信任扩展名）
_EXPECT_BY_SUFFIX = {".png": "png", ".jpg": "jpg", ".jpeg": "jpg", ".wav": "wav", ".mp4": "mp4", ".mov": "mp4"}


def _sniff(blob: bytes) -> dict[str, Any]:
    """只读文件头判定真实格式：PNG 顺带解析宽高，WAV 用标准库解析采样率与时长。"""
    if blob.startswith(b"\x89PNG\r\n\x1a\n") and len(blob) >= 24:
        return {
            "format": "png",
            "w": int.from_bytes(blob[16:20], "big"),
            "h": int.from_bytes(blob[20:24], "big"),
        }
    if blob.startswith(b"\xff\xd8\xff"):
        return {"format": "jpg"}
    if blob[4:8] == b"ftyp":
        return {"format": "mp4", "brand": blob[8:12].decode("latin-1", "replace").strip("\x00")}
    if blob[:4] == b"RIFF" and blob[8:12] == b"WAVE":
        try:
            with wave.open(io.BytesIO(blob), "rb") as handle:
                frames, rate, channels, width = handle.getnframes(), handle.getframerate(), handle.getnchannels(), handle.getsampwidth()
            return {
                "format": "wav",
                "frames": frames,
                "sample_rate": rate,
                "channels": channels,
                "bit_depth": width * 8,
                "duration_s": round(frames / rate, 3) if rate else 0.0,
            }
        except Exception as error:  # noqa: BLE001 - 头是对的但体断了，正是要抓的情况
            return {"format": "wav", "error": str(error)[:70]}
    return {"format": "unknown"}


@node(
    "dit",
    title="DIT / 数据管理",
    dept="camera",
    role="素材进剪辑间前的最后一道闸：逐文件算 SHA-256、按文件头复核格式、核对尺寸与时长，出 3-2-1 备份计划、代理清单和数据日报",
    inputs=[
        Port("images", "画面物料", "images", required=False, many=True),
        Port("clips", "视频片段", "clips", required=False, many=True),
        Port("audio", "音频轨", "audio", required=False, many=True),
        Port("shots", "分镜表", "shots", required=False),
        Port("camera_plan", "摄影方案", "camera_plan", required=False),
    ],
    outputs=[Port("report", "数据校验报告", "report"), Port("plan", "备份与转码计划", "plan"), Port("text", "数据日报", "text")],
    params=[
        Param("verify", "逐文件校验", "toggle", True, hint="关掉只清点不读盘"),
        Param("limit", "最多校验", "number", 240, minimum=10, maximum=2000, step=10, unit="个文件"),
        Param("max_mb", "单文件体积上限", "number", 8, minimum=0.5, maximum=200, step=0.5, unit="MiB", hint="超限说明分辨率或码率失控，代理成本会涨"),
        Param("dur_tol", "时长容差", "number", 0.15, minimum=0.01, maximum=1, step=0.01, unit="秒", hint="WAV 实测时长与交付单声明值的允许偏差"),
        Param("backups", "热备份份数", "number", 1, minimum=1, maximum=4, step=1, unit="份", hint="编辑机 RAID 上的额外副本数（3-2-1 里的第二份）"),
        Param("proxy", "出代理清单", "toggle", True),
        Param("proxy_scale", "代理比例", "select", "1/4", ["1/2", "1/4", "1/8"], hint="按面积折算：1/4 ≈ 原体积的 1/16"),
        Param("show", "列出问题", "number", 8, minimum=1, maximum=30, step=1, unit="条"),
    ],
    icon="💾",
    tags=["数据", "校验", "备份"],
)
def dit(ctx: Any) -> dict[str, Any]:
    verify = bool(ctx.params.get("verify"))
    limit = int(float(ctx.params.get("limit") or 240))
    max_mb = float(ctx.params.get("max_mb") or 8)
    dur_tol = float(ctx.params.get("dur_tol") or 0.15)
    backups = int(float(ctx.params.get("backups") or 1))
    proxy = bool(ctx.params.get("proxy"))
    scale = float({"1/2": 0.5, "1/4": 0.25, "1/8": 0.125}.get(str(ctx.params.get("proxy_scale") or "1/4"), 0.25))
    show = int(float(ctx.params.get("show") or 8))

    wanted: list[dict[str, Any]] = []

    def add(ref: str, expect: str, owner: str, role: str, declared: dict[str, Any]) -> None:
        if ref and len(wanted) < limit:
            wanted.append({"ref": str(ref), "expect": expect, "owner": owner, "role": role, **declared})

    covered: set[str] = set()
    for bundle in ctx.many("images"):
        for item in common.items_of(bundle):
            ref = str(item.get("ref") or "")
            index = item.get("shot_index")
            owner = str(item.get("slate") or (f"#{index}" if index not in (None, "") else "") or item.get("character") or item.get("kind") or "")
            add(ref, str(item.get("format") or _EXPECT_BY_SUFFIX.get(ref.lower()[-4:], "png")), owner, str(item.get("kind") or "image"), {"w": item.get("w"), "h": item.get("h")})
            if ref:
                covered.add(owner)

    clip_refs: list[str] = []
    for bundle in ctx.many("clips"):
        for item in common.items_of(bundle):
            slate = str(item.get("slate") or item.get("shot_id") or "")
            covered.add(slate)
            ref = str(item.get("clip_ref") or "")
            if ref:
                clip_refs.append(ref)
                add(ref, "mp4", slate, "clip", {"duration_s": item.get("duration_s")})
            for frame in item.get("frames") or []:
                add(str(frame.get("ref") or ""), "png", slate, "frame", {"w": frame.get("w"), "h": frame.get("h")})
            poster = str(item.get("poster_ref") or "")
            if poster and not (item.get("frames") or []):
                add(poster, "png", slate, "poster", {})

    for bundle in ctx.many("audio"):
        for item in common.items_of(bundle):
            add(
                str(item.get("ref") or ""),
                _EXPECT_BY_SUFFIX.get(str(item.get("ref") or "").lower()[-4:], "wav"),
                str(item.get("label") or item.get("id") or ""),
                str(item.get("kind") or "audio"),
                {"duration_s": item.get("duration_s"), "name": str(item.get("name") or "")},
            )

    rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    total_bytes = 0
    formats: dict[str, int] = {}
    png_sizes: dict[str, int] = {}
    sizes_by_unit: dict[str, set[str]] = {}
    sample_rates: set[int] = set()
    for item in wanted:
        ref, expect, owner, role = item["ref"], item["expect"], item["owner"], item["role"]
        blob = b""
        error = ""
        if verify:
            try:
                blob = ctx.read_artifact(ref)
            except Exception as read_error:  # noqa: BLE001 - 缺文件是数据事故，必须报出来
                error = str(read_error)[:70]
        digest = hashlib.sha256(blob).hexdigest() if blob else ""
        info = _sniff(blob) if blob else {}
        size = len(blob)
        total_bytes += size
        mb = round(size / 1024 / 1024, 3)
        got = str(info.get("format") or "")
        row: dict[str, Any] = {
            "ref": ref,
            "role": role,
            "owner": owner,
            "expect": expect,
            "format": got or ("缺失" if verify else "未校验"),
            "bytes": size,
            "mb": mb,
            "sha256": digest[:16],
            "status": "ok",
        }
        formats[row["format"]] = formats.get(row["format"], 0) + 1
        if error:
            row["status"] = "missing"
            issues.append({"severity": "blocker", "category": "数据", "detail": f"{ref} 读不到：{error}", "suggestion": "确认上游节点真的落过盘；重跑该节点后再生成本报告"})
        elif verify and size == 0:
            row["status"] = "empty"
            issues.append({"severity": "blocker", "category": "数据", "detail": f"{ref} 是 0 字节", "suggestion": "写入被截断，重出该素材并检查磁盘余量"})
        elif verify and got == "unknown":
            row["status"] = "unknown"
            issues.append({"severity": "major", "category": "格式", "detail": f"{ref} 文件头无法识别（前 4 字节 {blob[:4]!r}）", "suggestion": "扩展名与内容都可能被改过，按原始卡内文件重导一次"})
        elif verify and got and expect and got != expect and not (got == "jpg" and expect == "png"):
            row["status"] = "mismatch"
            issues.append({"severity": "major", "category": "格式", "detail": f"{ref} 声明 {expect} 实际是 {got}", "suggestion": "别让剪辑软件猜格式，改回真实扩展名或重转码"})
        if got == "png":
            box = f"{info.get('w')}x{info.get('h')}"
            png_sizes[box] = png_sizes.get(box, 0) + 1
            sizes_by_unit.setdefault(f"{role}/{owner}", set()).add(box)
            declared_w = item.get("w")
            if verify and declared_w and int(declared_w or 0) not in (0, int(info.get("w") or 0)):
                row["status"] = "size"
                issues.append({"severity": "minor", "category": "尺寸", "detail": f"{ref} 交付单写 {declared_w}px，实测 {info.get('w')}px", "suggestion": "以实测值为准，回写交付单"})
        if got == "wav":
            sample_rates.add(int(info.get("sample_rate") or 0))
            declared = float(item.get("duration_s") or 0.0)
            actual = float(info.get("duration_s") or 0.0)
            row["duration_s"] = actual
            if verify and declared and abs(actual - declared) > dur_tol:
                row["status"] = "dur"
                issues.append({"severity": "major", "category": "时长", "detail": f"{ref} 声明 {declared}s，实测 {actual}s（差 {round(actual - declared, 3)}s）", "suggestion": "混音与字幕的落点会偏，按实测时长重对"})
            if info.get("error"):
                issues.append({"severity": "blocker", "category": "数据", "detail": f"{ref} WAV 头能读但数据段异常：{info['error']}", "suggestion": "该轨不可用，重新渲染"})
        if mb > max_mb:
            row["status"] = "heavy"
            issues.append({"severity": "minor", "category": "体积", "detail": f"{ref} 体积 {mb}MiB 超上限 {max_mb}MiB", "suggestion": "进代理前先降码率，否则备份与转码时间都会翻倍"})
        rows.append(row)

    bad = [row for row in rows if row["status"] != "ok"]
    ok_rows = [row for row in rows if row["status"] == "ok"]
    volume = round(total_bytes / 1024 / 1024, 2)
    missing_shots: list[str] = []
    if ctx.in_("shots") is not None:
        for shot in common.shots_of(ctx.in_("shots")):
            slate = str(shot.get("slate") or shot.get("id") or "")
            if slate and slate not in covered:
                missing_shots.append(slate)
        if missing_shots:
            issues.append({"severity": "major", "category": "缺片", "detail": f"{len(missing_shots)} 镜没有任何素材落盘（{'、'.join(missing_shots[:6])}）", "suggestion": "补跑生成/关键帧节点，或在剪辑台前确认这些镜头被删除"})
    mixed = {unit: sorted(value) for unit, value in sizes_by_unit.items() if len(value) > 1}
    if mixed:
        sample = "；".join(f"{unit} {'、'.join(value)}" for unit, value in sorted(mixed.items())[:3])
        issues.append({"severity": "minor", "category": "一致性", "detail": f"{len(mixed)} 组素材内部分辨率不统一：{sample}", "suggestion": "同一条素材统一到同一帧宽，否则编码器要在素材内部做缩放，画质会抖"})
    if len(sample_rates) > 1:
        issues.append({"severity": "minor", "category": "一致性", "detail": f"音轨采样率不统一：{'、'.join(str(value) for value in sorted(sample_rates))}Hz", "suggestion": "混音前统一到同一采样率，避免重采样失真"})
    if not verify:
        issues.append({"severity": "info", "category": "口径", "detail": "本轮只清点未逐文件校验", "suggestion": "开机「逐文件校验」才会算哈希"})
    if not rows:
        issues.append({"severity": "blocker", "category": "数据", "detail": "没有任何素材接到 DIT：画面/片段/音轨三个端口都空", "suggestion": "把生成车间与混音师的产物接到本节点"})

    manifest = "\n".join(
        ["ref,role,owner,expect,format,bytes,mb,sha256,status"]
        + [",".join(str(row.get(key, "")) for key in ("ref", "role", "owner", "expect", "format", "bytes", "mb", "sha256", "status")) for row in rows]
    )
    saved = ctx.save_file(f"dit-manifest-{ctx.project_id}.csv", manifest.encode("utf-8-sig")) if rows else {"ref": "", "url": ""}
    hot = max(1, backups)
    backups_plan = [
        {"tier": "现场存储卡", "where": "摄影卡原封不动，直到双备份校验通过才格式化", "copies": 1, "mb": volume, "verify": "对拍 manifest 的 SHA-256"},
        {"tier": "剪辑室 RAID", "where": "收工当天拷完，按单元号 U01… 分目录", "copies": hot, "mb": round(volume * hot, 2), "verify": "逐卷校验哈希后再删卡"},
        {"tier": "冷备 / 云端", "where": "保留到成片交付后 90 天", "copies": 1, "mb": volume, "verify": "季度抽检一次可读性"},
    ]
    proxies = [
        {"ref": row["ref"], "owner": row["owner"], "role": row["role"], "from_mb": row["mb"], "to_mb": round(row["mb"] * scale * scale, 3)}
        for row in ok_rows
        if row["role"] in {"clip", "frame", "image", "poster"}
    ]
    proxies = proxies[:limit] if proxy else []
    proxy_mb = round(sum(float(row["to_mb"]) for row in proxies), 2)

    score = max(0, min(100, 100 - sum({"blocker": 30, "major": 12, "minor": 4, "info": 0}[str(item["severity"])] for item in issues)))
    report = {
        "kind": "data",
        "score": score,
        "verdict": "pass" if score >= 85 else ("revise" if score >= 60 else "reject"),
        "issues": issues,
        "summary": f"DIT 数据校验：{len(rows)} 个文件 / {volume} MiB，异常 {len(bad)} 个，质检分 {score}",
        "simulated": False,
    }
    plan = {
        "dept": "camera",
        "role": "DIT / 数据管理",
        "items": rows[: show * 8],
        "files": len(rows),
        "verified": len(ok_rows),
        "volume_mb": volume,
        "formats": {key: value for key, value in sorted(formats.items(), key=lambda kv: -kv[1])},
        "sizes": png_sizes,
        "mixed_sizes": mixed,
        "sample_rates": sorted(sample_rates),
        "missing_shots": missing_shots[:20],
        "bad_refs": [str(row["ref"]) for row in bad][:limit],
        "backups": backups_plan,
        "backup_mb": round(sum(float(row["mb"]) for row in backups_plan), 2),
        "proxy_enabled": proxy,
        "proxy_scale": str(ctx.params.get("proxy_scale") or "1/4"),
        "proxy_items": len(proxies),
        "proxy_mb": proxy_mb,
        "manifest_ref": str(saved.get("ref") or ""),
        "manifest_url": str(saved.get("url") or ""),
        "risks": [str(item["detail"]) for item in issues if str(item["severity"]) in {"blocker", "major"}][:6] or ["文件全部可读、格式与时长对得上"],
        "simulated": False,
    }
    text = "\n".join(
        [
            f"数据日报｜{len(rows)} 个文件 / {volume} MiB｜校验通过 {len(ok_rows)}，异常 {len(bad)}｜质检分 {score}（{report['verdict']}）",
            "格式：" + "、".join(f"{key} {value}" for key, value in plan["formats"].items()),
            "分辨率：" + ("、".join(f"{key}×{value}" for key, value in sorted(png_sizes.items(), key=lambda kv: -kv[1])[:5]) if png_sizes else "无图像文件") + (f"｜{len(sample_rates)} 种采样率 {'、'.join(str(v) for v in sorted(sample_rates))}Hz" if sample_rates else ""),
            *[f"· {row['ref']}〔{row['role']}〕{row['format']} {row['mb']}MiB sha256:{row['sha256']}{'｜' + row['status'] if row['status'] != 'ok' else ''}" for row in (bad or rows)[:show]],
            "备份：" + "｜".join(f"{row['tier']} {row['copies']}×{row['mb']}MiB" for row in backups_plan) + f"（合计 {plan['backup_mb']} MiB，逐卷对拍 manifest）",
            (f"代理：{len(proxies)} 个文件按 {plan['proxy_scale']} 比例出，预计 {proxy_mb} MiB" if proxy else "代理：本轮不出，剪辑直接用原素材"),
            (f"⚠ {len(missing_shots)} 镜无素材落盘：{'、'.join(missing_shots[:6])}" if missing_shots else "分镜表里的每一镜都有对应素材"),
            "数据清单（含哈希）已落盘：" + (plan["manifest_ref"] or "本轮无文件"),
        ]
    )
    ctx.log(f"DIT：{len(rows)} 个文件 / {volume}MiB，异常 {len(bad)} 个")
    return {"report": report, "plan": plan, "text": text}


#: 剧照用途：钩子镜当封面候选，行动指令镜给投放，其余进组照
_STILLS_USE: dict[str, tuple[str, str]] = {
    "cover": ("COVER", "封面候选"),
    "ads": ("ADS", "投放素材"),
    "group": ("STILLS", "剧照组照"),
    "bts": ("BTS", "幕后花絮"),
}
_USE_BY_BEAT = {"hook": "cover", "cta": "ads"}
#: 片段抽帧最接近现场定格的瞬间，其次是关键帧，最后才是概念图
_SOURCE_RANK = {"frame": 0.0, "keyframe": 1.0, "image": 2.0}
_STILLS_BEAT = {"hook": 0, "cta": 1, "payoff": 2, "tension": 3, "setup": 4, "develop": 5, "chapter": 6, "ending": 7}


@node(
    "stills_photographer",
    title="剧照师",
    dept="camera",
    role="现场抓静态：按分镜覆盖每一场的可用瞬间，从素材里挑帧、往里取景、定剧照色调、压场记条，出宣发能直接取用的剧照与组照清单",
    inputs=[
        Port("shots", "分镜表", "shots"),
        Port("images", "画面素材", "images", required=False, many=True),
        Port("clips", "片段", "clips", required=False, many=True),
        Port("look", "视觉基调", "look", required=False),
        Port("brief", "项目需求", "brief", required=False),
    ],
    outputs=[Port("images", "剧照", "images"), Port("plan", "剧照清单", "plan"), Port("text", "剧照说明", "text")],
    params=[
        Param("limit", "最多张数", "number", 14, minimum=2, maximum=40, step=1, unit="张"),
        Param("per_scene", "每场保底", "number", 2, minimum=1, maximum=4, step=1, unit="张"),
        Param("crop", "取景推近", "range", 1.18, minimum=1.0, maximum=2.2, step=0.02, hint="大于 1 等于把取景往里推，削掉生成图的边缘脏边"),
        Param("warm_k", "色温偏移", "number", 0, minimum=-800, maximum=800, step=50, unit="K", hint="剧照比成片稍暖或稍冷，投放点击率会差一档"),
        Param("contact", "出组照清单", "toggle", True),
    ],
    icon="📸",
    tags=["剧照", "宣发", "现场"],
)
def stills_photographer(ctx: Any) -> dict[str, Any]:
    board = common.require_shots(ctx.in_("shots"), "剧照师")
    shots = common.shots_of(board)
    look = common.look_of(ctx.in_("look"))
    limit = int(float(ctx.params.get("limit") or 14))
    per_scene = int(float(ctx.params.get("per_scene") or 2))
    crop = float(ctx.params.get("crop") or 1.18)
    warm = float(ctx.params.get("warm_k") or 0)

    by_key: dict[str, dict[str, Any]] = {}
    for shot in shots:
        for key in (str(shot.get("id") or ""), str(shot.get("slate") or "")):
            if key:
                by_key.setdefault(key, shot)

    pool: dict[str, list[dict[str, Any]]] = {}
    bts: list[dict[str, Any]] = []

    def register(key: str, row: dict[str, Any]) -> None:
        ref = str(row.get("ref") or "")
        if not ref.lower().endswith(".png"):
            return
        bucket = pool.setdefault(key, [])
        if not any(str(item["ref"]) == ref for item in bucket):
            bucket.append(row)

    for bundle in ctx.many("images"):
        for item in common.items_of(bundle):
            row = {
                "ref": str(item.get("ref") or ""),
                "kind": str(item.get("kind") or "image"),
                "simulated": bool(item.get("simulated", True)),
                "t": item.get("t"),
                "caption": str(item.get("character") or item.get("prompt") or ""),
            }
            shot = by_key.get(str(item.get("shot_id") or "")) or by_key.get(str(item.get("slate") or ""))
            if shot is None and item.get("shot_index") is not None:
                shot = next((reference for reference in shots if int(reference.get("index") or -1) == int(item["shot_index"])), None)
            if shot is not None:
                register(str(shot.get("id") or shot.get("slate")), row)
            elif str(row["kind"]) in {"concept", "character_sheet", "makeup_card", "storyboard", "cover"}:
                row["use"] = "bts"
                bts.append(row)
    for bundle in ctx.many("clips"):
        for clip in common.items_of(bundle):
            shot = by_key.get(str(clip.get("shot_id") or "")) or by_key.get(str(clip.get("slate") or ""))
            if shot is None:
                continue
            key = str(shot.get("id") or shot.get("slate"))
            for frame in clip.get("frames") or []:
                if isinstance(frame, dict):
                    register(
                        key,
                        {
                            "ref": str(frame.get("ref") or ""),
                            "kind": "frame",
                            "simulated": bool(clip.get("simulated", True)),
                            "t": frame.get("t"),
                            "caption": str(frame.get("role") or ""),
                        },
                    )

    def score_of(row: dict[str, Any]) -> float:
        return _SOURCE_RANK.get(str(row.get("kind")), 2.0) + (0.0 if not row.get("simulated") else 0.35)

    taken: set[str] = set()
    picked: list[tuple[dict[str, Any], dict[str, Any]]] = []

    def take(shot: dict[str, Any]) -> None:
        bucket = sorted(pool.get(str(shot.get("id") or shot.get("slate"))) or [], key=score_of)
        for row in bucket:
            if str(row["ref"]) in taken:
                continue
            taken.add(str(row["ref"]))
            picked.append((shot, row))
            return

    scenes: dict[str, list[dict[str, Any]]] = {}
    for shot in shots:
        scenes.setdefault(str(shot.get("scene_id") or "S0"), []).append(shot)
    for rows in scenes.values():
        for shot in sorted(rows, key=lambda item: (_STILLS_BEAT.get(str(item.get("beat") or ""), 5), -float(item.get("duration_s") or 0.0)))[:per_scene]:
            take(shot)
    picked_shots = {id(shot) for shot, _ in picked}
    for shot in sorted([item for item in shots if id(item) not in picked_shots], key=lambda item: (_STILLS_BEAT.get(str(item.get("beat") or ""), 5), -float(item.get("duration_s") or 0.0))):
        if len(picked) >= limit:
            break
        take(shot)
    for row in bts[: max(0, min(3, limit - len(picked)))]:
        picked.append(({"slate": str(row["kind"]), "scene_id": "", "beat": "", "description": str(row.get("caption") or "")}, row))

    palette = [str(item) for item in (look.get("palette") or [])]
    accent = canvas.hex_rgb(str(look.get("accent") or (palette[0] if palette else "#c2495a")), (194, 73, 90))
    issues: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    rows_out: list[dict[str, Any]] = []
    for index, (shot, source) in enumerate(picked[:limit]):
        slate = str(shot.get("slate") or shot.get("id") or f"shot-{index + 1}")
        use_key = str(source.get("use") or _USE_BY_BEAT.get(str(shot.get("beat") or ""), "group"))
        tag, use_label = _STILLS_USE[use_key]
        try:
            raster = canvas.read_png(ctx.read_artifact(str(source["ref"])))
        except (NodeError, ValueError) as error:
            issues.append({"severity": "minor", "category": "取源", "detail": f"{slate} 的源图 {source['ref']} 解不出像素：{str(error)[:48]}", "suggestion": "该帧不能当剧照，回关键帧画师重出一版"})
            continue
        rng = craft.rng_for("stills", slate, str(shot.get("size") or "medium"))
        wide = str(shot.get("size") or "") in {"extreme_wide", "wide", "full"}
        raster = raster.frame_zoom(crop, 0.5 + rng.uniform(-0.06, 0.06), 0.54 if wide else 0.45)
        raster.grade(contrast=1.06, saturation=1.08, temp_k=5600.0 + warm, exposure=0.03)
        raster.vignette(0.18)
        raster.grain(0.02, seed=int(rng.random() * 9973))
        bar = max(26, int(raster.h * 0.075))
        raster.fill_rect(0, raster.h - bar, raster.w, bar, (10, 12, 18), 0.82)
        raster.row(raster.h - bar - 1, 0, raster.w, accent)
        stamp = canvas.ascii_safe(f"{slate} {int(float(shot.get('lens_mm') or 0))}MM {shot.get('movement') or ''} {shot.get('time_of_day') or ''}")[:36]
        raster.text(10, raster.h - bar + max(4, bar // 4), stamp or "STILLS", (238, 242, 250), 2 if raster.w >= 420 else 1)
        raster.text(max(6, raster.w - 84), 10, tag, accent, 1)
        png = raster.to_png()
        saved = common.image_artifact(
            ctx,
            f"stills-{index + 1:02d}-{common.slug(slate)}.png",
            png,
            {
                "kind": "stills",
                "id": f"stills-{index + 1:02d}",
                "character": str(shot.get("focus_character") or ""),
                "prompt": canvas.ascii_safe(str(shot.get("description") or ""))[:90],
                "slate": slate,
                "shot_id": str(shot.get("id") or ""),
                "use": use_key,
                "use_label": use_label,
                "source_ref": str(source["ref"]),
                "at_s": source.get("t"),
                "crop": round(crop, 2),
                "provider": "stills",
                "simulated": bool(source["simulated"]),
            },
        )
        items.append(saved)
        rows_out.append(
            {
                "no": index + 1,
                "slate": slate,
                "scene": str(shot.get("scene_id") or "-"),
                "use": use_label,
                "source": str(source["ref"]),
                "at_s": source.get("t"),
                "size_kb": round(len(png) / 1024.0, 1),
                "real": not bool(source["simulated"]),
                "ref": str(saved["ref"]),
                "url": str(saved["url"]),
            }
        )

    no_source = [str(shot.get("slate") or shot.get("id")) for shot in shots if not (pool.get(str(shot.get("id") or shot.get("slate"))) or [])]
    if no_source:
        issues.append({"severity": "major", "category": "覆盖", "detail": f"{len(no_source)} 镜一张剧照都截不出（{'、'.join(no_source[:6])}）", "suggestion": "这些镜头没有画面素材，回生成车间或助理剪辑补齐"})
    cold = [scene_id for scene_id, rows in scenes.items() if not any(str(row["scene"]) == scene_id for row in rows_out)]
    if cold:
        issues.append({"severity": "minor", "category": "覆盖", "detail": f"{len(cold)} 场在组照里一张都没有（{'、'.join(cold[:5])}）", "suggestion": "提高每场保底张数，或给这场补一条镜头素材"})
    if not items:
        issues.append({"severity": "blocker", "category": "数据", "detail": "剧照师手上没有任何可截的 PNG 素材", "suggestion": "把关键帧/片段与画面素材接到本节点"})
    covered = len({row["scene"] for row in rows_out if row["scene"] != "-"})

    csv = "\n".join(
        ["no,slate,scene,use,source,at_s,size_kb,real,out"]
        + [",".join(str(row[key]) for key in ("no", "slate", "scene", "use", "source", "at_s", "size_kb", "real", "ref")) for row in rows_out]
    )
    saved_sheet = ctx.save_file(f"stills-sheet-{ctx.project_id}.csv", csv.encode("utf-8-sig")) if rows_out and bool(ctx.params.get("contact")) else {"ref": "", "url": ""}
    by_use: dict[str, int] = {}
    for row in rows_out:
        by_use[row["use"]] = by_use.get(row["use"], 0) + 1

    plan = {
        "dept": "camera",
        "role": "剧照师",
        "items": rows_out[:24],
        "stills": len(items),
        "real_stills": len([row for row in rows_out if row["real"]]),
        "sources": sum(len(value) for value in pool.values()),
        "scenes_covered": covered,
        "scene_count": len(scenes),
        "per_scene": per_scene,
        "crop": round(crop, 2),
        "warm_k": warm,
        "by_use": by_use,
        "no_source": no_source[:20],
        "cold_scenes": cold[:10],
        "issues": issues,
        "sheet_ref": str(saved_sheet.get("ref") or ""),
        "sheet_url": str(saved_sheet.get("url") or ""),
        "simulated": not any(row["real"] for row in rows_out),
    }
    text = "\n".join(
        [
            f"剧照：{len(shots)} 镜 / {len(scenes)} 场 → 出 {len(items)} 张（真机 {plan['real_stills']} 张），覆盖 {covered} 场，取景推近 {crop:.2f}×，色温 {warm:+.0f}K",
            "用途：" + "、".join(f"{key} {value} 张" for key, value in sorted(by_use.items(), key=lambda kv: -kv[1])),
            f"挑帧口径：片段抽帧 > 关键帧 > 概念图，同镜内先取真机图；每张往里取景后压一条场记条（号/焦段/运镜/时段）",
            *[f"· {row['no']:02d} {row['slate']}〔{row['scene']}〕{row['use']} ← {row['source']}{'｜真机' if row['real'] else '｜示意'}" for row in rows_out[:12]],
            (f"⚠ {len(no_source)} 镜无素材可截：{'、'.join(no_source[:6])}" if no_source else "分镜表里每一镜都有剧照来源"),
            (f"⚠ {len(cold)} 场整场无剧照：{'、'.join(cold[:5])}" if cold else "每一场都至少有一张进组照"),
            "组照清单：" + (plan["sheet_ref"] or "本轮不出清单"),
        ]
    )
    ctx.log(f"剧照：出 {len(items)} 张，覆盖 {covered}/{len(scenes)} 场，缺源 {len(no_source)} 镜")
    return {"images": {"kind": "images", "items": items, "count": len(items), "simulated": plan["simulated"]}, "plan": plan, "text": text}
