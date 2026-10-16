"""美术部门岗位：美术指导、角色设计、场景设计、服化道、概念图、特效化妆。

美术是唯一把「导演的一句话」变成「能搭、能做、能买」的部门：
色板、材质、陈设、服装、化妆、道具都要落到数量与规格上，否则开机当天一定停工。
特效化妆再把妆面落到化妆台上：每人一张妆容卡，伤效件有位置、有接戏状态、有补妆时点。
"""

from __future__ import annotations

from typing import Any

from .. import canvas, craft
from ..spec import NodeError, Param, Port, node
from . import common

#: 时代 / 世界设定 → 材质、陈设、色板倾向（美术指导据此开料单）
ERA_PRESETS: dict[str, dict[str, Any]] = {
    "当代都市": {
        "materials": ["哑光金属", "Low-E 玻璃", "乳胶漆白墙", "木纹贴面", "环氧地坪"],
        "dressing": ["绿植盆栽", "外卖袋与咖啡杯", "充电线与插排", "快递箱", "冰箱贴与便签"],
        "palette": ["#2b3a55", "#8fa3bf", "#e4e7ec"],
        "note": "生活痕迹要具体到「谁刚在这里待过」，禁止空房间",
    },
    "复古胶片": {
        "materials": ["实木贴皮", "灯芯绒", "搪瓷", "白炽灯罩", "老式花纹墙纸"],
        "dressing": ["转盘电话", "磁带与收音机", "印花暖水瓶", "搪瓷缸", "胶片海报"],
        "palette": ["#7a5a3a", "#c99a5b", "#f2e2c4"],
        "note": "1980-1990s：所有电子件换成机械件，LOGO 检查年代",
    },
    "近未来": {
        "materials": ["磨砂亚克力", "白色曲面塑料", "镜面钢", "柔性灯带", "碳纤维"],
        "dressing": ["透明屏", "线束隐藏槽", "模块化家具", "无人配送箱", "极简标识"],
        "palette": ["#0d1522", "#3b6f8c", "#cfe8ff"],
        "note": "减法美学：不出现今天的插座、纸箱与杂乱线缆",
    },
    "乡土纪实": {
        "materials": ["夯土墙", "青砖", "竹木", "水泥地", "旧布帘"],
        "dressing": ["农具", "晒架与竹匾", "塑料凳", "搪瓷盆", "手写价签"],
        "palette": ["#5a5230", "#a58a5a", "#dcd2b0"],
        "note": "现场收音要留意风声与禽畜，美术别放会响的新物件",
    },
    "工业废土": {
        "materials": ["锈蚀钢板", "混凝土", "防尘布", "管道", "砂砾"],
        "dressing": ["油桶", "轮胎堆", "警示带", "破损指示牌", "临时照明"],
        "palette": ["#3a3128", "#8c6a4a", "#c9bda6"],
        "note": "做旧要分层：底层锈、中层灰、表层新scratch，否则像贴纸",
    },
    "极简棚拍": {
        "materials": ["无缝背景纸", "PVC 反光台", "磨砂玻璃", "匀光幕"],
        "dressing": ["单支产品台", "几何体道具", "投影LOGO板"],
        "palette": ["#f5f6f8", "#cfd6e0", "#1a1f2b"],
        "note": "产品片：画面里除产品与必要阴影，其他全部移除",
    },
}

#: 角色功能 → 服装基调（造型要服务叙事功能，不是好看）
WARDROBE_BY_ROLE: dict[str, dict[str, Any]] = {
    "protagonist": {"look": "轻薄外套 + 高领内搭 + 直筒裤 + 软底鞋", "layers": 3, "changes": 2, "note": "主角要能跑能动，口袋必须真能用"},
    "lead": {"look": "短西装 + 衬衫 + 九分裤 + 乐福鞋", "layers": 2, "changes": 1, "note": "明度比主角高一档，同框能分清"},
    "narrator": {"look": "深色便装（通常不入画）", "layers": 1, "changes": 0, "note": "只做口播，服装不影响画面"},
    "mentor": {"look": "沉稳外披 + 素色长衫，袖口磨损", "layers": 3, "changes": 1, "note": "旧料做真旧，不做新做旧"},
    "villain": {"look": "硬挺深色结构感外套 + 皮鞋", "layers": 2, "changes": 1, "note": "剪影要尖锐，避免圆角与柔软面料"},
    "friend": {"look": "亮色休闲装 + 帆布鞋 + 斜挎包", "layers": 2, "changes": 2, "note": "带一件可交互的小道具"},
    "child": {"look": "安全面料 + 无绳带设计 + 防滑鞋", "layers": 2, "changes": 1, "note": "儿童演员禁用小件配饰"},
    "brand": {"look": "品牌工服 / 标准制服，LOGO 位置固定", "layers": 1, "changes": 0, "note": "商标露出需与合规口径一致"},
}
_DEFAULT_WARDROBE = {"look": "基础生活装：外套 + 内搭 + 长裤 + 便鞋", "layers": 2, "changes": 1, "note": "无特殊设定，跟随场景时代"}

#: 情绪 → 妆发口径
MAKEUP_BY_MOOD: dict[str, dict[str, str]] = {
    "neutral": {"face": "裸妆哑光底", "hair": "自然梳理，保留碎发", "extra": "无需特殊效果"},
    "warm": {"face": "暖调腮红，水光底", "hair": "柔软蓬松", "extra": "无需特殊效果"},
    "cool": {"face": "冷调裸妆，唇色降低", "hair": "服帖整洁", "extra": "无需特殊效果"},
    "tense": {"face": "加深眼窝，唇色降饱和", "hair": "略显凌乱", "extra": "额头与鬓角做汗"},
    "punch": {"face": "高对比底妆，眼神光加强", "hair": "定型", "extra": "高光点涂鼻梁与颧骨"},
    "noir": {"face": "强阴影塑形，去色彩", "hair": "油头或极利落", "extra": "可配合雨戏湿身"},
    "dream": {"face": "珠光柔焦", "hair": "微卷飘散", "extra": "备发胶做湿发造型"},
    "vintage": {"face": "细眉 + 饱和唇", "hair": "年代感定型卷", "extra": "确认年代妆面不能出错"},
    "soft": {"face": "薄透底妆", "hair": "低打理感", "extra": "无需特殊效果"},
}

#: 剧情/情绪关键词 → 特效件组（坐标是脸部比例：x 左右、y 上下，±1 到脸缘）
FX_PACKS: list[tuple[tuple[str, ...], str, list[dict[str, Any]]]] = [
    (
        ("汗", "喘", "跑", "紧张", "急", "体力"),
        "汗湿",
        [
            {"label": "额角汗珠", "kind": "mark", "x": -0.5, "y": -0.62, "color": "#cfe3f5"},
            {"label": "鬓角湿痕", "kind": "wet", "x": 0.68, "y": -0.18, "color": "#a9c6e0"},
            {"label": "颈侧汗线", "kind": "wet", "x": -0.34, "y": 0.86, "color": "#b7cfe4"},
        ],
    ),
    (
        ("打", "撞", "摔", "伤", "血", "冲突", "倒地", "搏"),
        "伤效",
        [
            {"label": "颧骨擦伤", "kind": "bruise", "x": -0.52, "y": 0.08, "color": "#8d5b4a"},
            {"label": "嘴角裂口", "kind": "cut", "x": 0.26, "y": 0.46, "color": "#a32f28"},
            {"label": "下颌结痂", "kind": "cut", "x": -0.22, "y": 0.68, "color": "#7d3a2c"},
            {"label": "眼白充血", "kind": "mark", "x": 0.42, "y": -0.22, "color": "#c05a52"},
        ],
    ),
    (
        ("雨", "淋", "湿", "泪", "水", "汗"),
        "湿身",
        [
            {"label": "碎发贴额", "kind": "wet", "x": -0.16, "y": -0.72, "color": "#5a6b82"},
            {"label": "面水膜（去油光）", "kind": "wet", "x": 0.1, "y": 0.04, "color": "#9fb8cf"},
        ],
    ),
    (
        ("夜", "暗", "悬疑", "黑色", "冷"),
        "夜色",
        [
            {"label": "眼下青影", "kind": "bruise", "x": -0.4, "y": -0.1, "color": "#4a5578"},
            {"label": "唇色压暗", "kind": "mark", "x": 0.0, "y": 0.42, "color": "#6d3b45"},
        ],
    ),
    (
        ("梦", "幻", "回忆", "柔", "光"),
        "珠光",
        [
            {"label": "颧骨高光", "kind": "mark", "x": -0.46, "y": 0.12, "color": "#e8dcc6"},
            {"label": "鼻梁提亮", "kind": "mark", "x": 0.0, "y": -0.06, "color": "#f2eadb"},
        ],
    ),
    (
        ("年代", "复古", "胶片", "旧", "19"),
        "年代妆",
        [
            {"label": "细眉重描", "kind": "mark", "x": -0.42, "y": -0.34, "color": "#4a3a2c"},
            {"label": "饱和唇", "kind": "mark", "x": 0.0, "y": 0.44, "color": "#a33a3f"},
        ],
    ),
]

#: 档位 → 每组特效件数上限与额外上妆时长（分钟），化妆师据此排早班
FX_LEVEL_QUOTA: dict[str, tuple[int, int]] = {"素颜": (0, 0), "轻": (1, 15), "标准": (2, 40), "重": (4, 75)}

#: 道具关键词 → 备品数量与理由（服化道主管据此报数，一次拍多条是常态）
PROP_SPARES: list[tuple[tuple[str, ...], int, str]] = [
    (("水", "饮料", "杯", "瓶", "咖啡"), 6, "液体镜头通常一次拍多条，量要备足"),
    (("食物", "餐", "面", "菜", "粉"), 4, "同一场要重做，温度与形态必须一致"),
    (("手机", "屏幕", "屏", "电脑"), 3, "屏幕内容预录循环，避免反光穿帮"),
    (("样品", "产品", "货", "包装"), 8, "广告品需留样比对，色号必须一致"),
    (("纸", "文件", "单", "信"), 4, "手写类穿帮即换，多备同款"),
    (("玻璃", "酒", "杯具"), 4, "破碎镜头一律用糖玻璃，真玻璃禁止上台"),
    (("工具", "螺丝", "钳", "刀"), 2, "按剧情新旧度做旧，保留使用痕迹"),
]


def _pick_wardrobe(character: dict[str, Any]) -> dict[str, Any]:
    return WARDROBE_BY_ROLE.get(str(character.get("role_type") or ""), _DEFAULT_WARDROBE)


def _scene_appearances(screenplay: dict[str, Any], name: str) -> int:
    return sum(1 for scene in (screenplay.get("scenes") or []) if name in [str(item) for item in (scene.get("characters") or [])])


def _spares_for(prop: str) -> tuple[int, str]:
    for keys, count, reason in PROP_SPARES:
        if any(key in prop for key in keys):
            return count, reason
    return 2, "常规备品：一条两份，防损耗"


@node(
    "art_director",
    title="美术指导",
    dept="art",
    role="把导演基调翻译成可搭建的世界：色板、色彩脚本、材质料单、陈设清单、景别空间与做旧方案",
    inputs=[
        Port("brief", "项目需求", "brief"),
        Port("look", "视觉基调", "look"),
        Port("screenplay", "剧本", "screenplay", required=False),
        Port("locations", "场景表", "locations", required=False),
    ],
    outputs=[Port("plan", "美术方案", "plan"), Port("text", "美术阐述", "text")],
    params=[
        Param("era", "时代 / 世界设定", "select", "当代都市", list(ERA_PRESETS.keys()), hint="决定材质、陈设与做旧口径"),
        Param("set_mode", "建景方式", "select", "实景+局部陈设", ["实景+局部陈设", "全搭景", "半景+虚拟延伸", "纯虚拟场景"], hint="预算与工期的第一变量"),
        Param("density", "陈设密度", "select", "适中", ["克制", "适中", "丰饶"], hint="画面里生活痕迹的多少"),
        Param("swatch", "主色板数量", "number", 5, minimum=3, maximum=8, step=1, unit="色"),
        Param("color_script", "按幕做色彩脚本", "toggle", True, hint="每一幕给一组主色与情绪，供调色与服装对齐"),
        Param("key_prop", "关键道具", "text", "", hint="承载叙事的那一件，需要特写与备品"),
    ],
    icon="🎨",
    tags=["美术", "世界设定"],
)
def art_director(ctx: Any) -> dict[str, Any]:
    brief = common.norm_brief(ctx.in_("brief"), {})
    look = common.look_of(ctx.in_("look"))
    screenplay = ctx.in_("screenplay") or {}
    locations = common.locations_of(ctx.in_("locations"))
    era = ERA_PRESETS[str(ctx.params.get("era") or "当代都市")]
    density = str(ctx.params.get("density") or "适中")
    cap = {"克制": 4, "适中": 8, "丰饶": 14}[density]

    props: list[str] = []
    for location in locations:
        props.extend(str(item) for item in (location.get("props") or []))
    for scene in screenplay.get("scenes") or []:
        props.extend(str(item) for item in (scene.get("props") or []))
    key_prop = str(ctx.params.get("key_prop") or "").strip()
    if key_prop:
        props.insert(0, key_prop)
    seen: list[str] = []
    for prop in props:
        if prop and prop not in seen:
            seen.append(prop)

    dressing = list(era["dressing"])
    if len(seen) < cap:
        dressing = dressing[: max(1, cap - len(seen))]

    acts: list[str] = []
    for scene in screenplay.get("scenes") or []:
        act = str(scene.get("act") or craft.act_of(0.5))
        if act not in acts:
            acts.append(act)
    mood_by_act = look.get("mood_by_act") or {}
    palette = [str(item) for item in (look.get("palette") or [])] + [str(item) for item in era["palette"]]
    dedup_palette: list[str] = []
    for hex_value in palette:
        if hex_value not in dedup_palette:
            dedup_palette.append(hex_value)
    swatch_count = int(float(ctx.params.get("swatch") or 5))
    color_script: list[dict[str, Any]] = []
    if bool(ctx.params.get("color_script")):
        for index, act in enumerate(acts or ["全片"]):
            mood = str(mood_by_act.get(act) or look.get("mood") or "neutral")
            grade = craft.grade_for(mood)
            color_script.append(
                {
                    "act": act,
                    "mood": mood,
                    "grade_label": grade["label"],
                    "dominant": dedup_palette[index % max(1, len(dedup_palette))],
                    "accent": str(look.get("accent") or era["palette"][-1]),
                    "note": f"{grade['label']}（{grade['temp']}K / 对比 {grade['contrast']} / 饱和 {grade['sat']}）",
                }
            )

    mode_days = {"实景+局部陈设": 1.0, "全搭景": 4.0, "半景+虚拟延伸": 2.0, "纯虚拟场景": 1.5}
    plan = {
        "dept": "art",
        "role": "美术指导",
        "era": str(ctx.params.get("era")),
        "set_mode": str(ctx.params.get("set_mode")),
        "density": density,
        "palette": dedup_palette[:swatch_count],
        "accent": str(look.get("accent") or era["palette"][-1]),
        "materials": list(era["materials"]),
        "dressing": dressing,
        "props": seen[: cap + 6],
        "key_prop": key_prop,
        "color_script": color_script,
        "concept": str(look.get("style_label") or ""),
        "art_days": round(mode_days.get(str(ctx.params.get("set_mode")), 1.0) * max(1.0, len(locations) or 1) * 0.6, 1),
        "risks": [
            era["note"],
            "陈设里的品牌 LOGO 与 artwork 需过版权岗，未授权一律替换",
            "做旧分层（底锈/中灰/新痕）不可省，否则画面一眼假",
        ],
        "simulated": True,
    }
    text = "\n".join(
        [
            f"美术方案｜时代：{plan['era']}｜建景：{plan['set_mode']}｜陈设密度：{density}",
            f"主色板：{' '.join(plan['palette'])}｜强调色：{plan['accent']}",
            "材质：" + "、".join(plan["materials"]),
            "陈设：" + "、".join(plan["dressing"]),
            "道具：" + "、".join(plan["props"]) + (f"｜关键道具：{key_prop}" if key_prop else ""),
            "色彩脚本：" + " / ".join(f"{row['act']}→{row['grade_label']}" for row in color_script),
            f"美术工期估算：{plan['art_days']} 天（不含厂库租赁）",
        ]
    )
    ctx.log(f"美术方案：{len(plan['props'])} 项道具 / {len(color_script)} 段色彩脚本", era=plan["era"])
    return {"plan": plan, "text": text}


@node(
    "character_design",
    title="角色造型设计",
    dept="art",
    role="给每个人物定剪影、服装、妆发与配色，出设定卡，供选角、生成模型与服化道共用",
    inputs=[
        Port("characters", "角色表", "characters"),
        Port("look", "视觉基调", "look", required=False),
        Port("screenplay", "剧本", "screenplay", required=False),
    ],
    outputs=[Port("characters", "造型角色表", "characters"), Port("images", "角色设定卡", "images"), Port("text", "造型说明", "text")],
    params=[
        Param("sheets", "出设定卡", "toggle", True, hint="每个角色一张 PNG，离线可看剪影与配色"),
        Param("silhouette", "剪影倾向", "select", "按角色功能", ["按角色功能", "圆润亲和", "尖锐对抗", "低识别度"], hint="同框人物靠剪影区分"),
        Param("max_colors", "每人配色数", "number", 4, minimum=2, maximum=6, step=1, unit="色"),
    ],
    icon="🧍",
    tags=["美术", "造型"],
)
def character_design(ctx: Any) -> dict[str, Any]:
    look = common.look_of(ctx.in_("look"))
    screenplay = ctx.in_("screenplay") or {}
    roster = common.characters_of(ctx.in_("characters"))
    if not roster:
        raise NodeError("角色造型设计需要上游角色表：先接「文学策划」或「角色表」")
    base_palette = [str(item) for item in (look.get("palette") or [])] or ["#1a2c5e", "#8fa3bf", "#e4e7ec"]
    accent = str(look.get("accent") or "#7dd3fc")
    max_colors = int(float(ctx.params.get("max_colors") or 4))
    silhouette_mode = str(ctx.params.get("silhouette") or "按角色功能")
    _SIL_BY_ROLE = {"protagonist": "日常轮廓，重心略低", "lead": "挺拔直立", "narrator": "不入画", "mentor": "宽松层叠", "villain": "尖锐肩线", "friend": "松垮休闲", "child": "小比例大头", "brand": "规整制服"}

    cast: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    lines: list[str] = ["角色造型表："]
    for index, character in enumerate(roster):
        name = str(character.get("name") or f"角色{index + 1}")
        wardrobe = _pick_wardrobe(character)
        mood = str(character.get("mood") or look.get("mood") or "neutral")
        makeup = MAKEUP_BY_MOOD.get(mood, MAKEUP_BY_MOOD["neutral"])
        rng = craft.rng_for("art", name, str(look.get("style_id")))
        second = accent if index % 2 == 0 else base_palette[(index + 1) % len(base_palette)]
        palette = [base_palette[index % len(base_palette)], second]
        for hex_value in base_palette:
            if hex_value not in palette:
                palette.append(hex_value)
        times = _scene_appearances(screenplay, name)
        changes = int(wardrobe["changes"]) if times < 3 else int(wardrobe["changes"]) + 1
        item = {
            **character,
            "name": name,
            "wardrobe": str(character.get("wardrobe") or wardrobe["look"]),
            "layers": wardrobe["layers"],
            "costume_changes": changes,
            "makeup": makeup["face"],
            "hair": makeup["hair"],
            "fx_note": makeup["extra"],
            "silhouette": wardrobe["note"] if silhouette_mode == "按角色功能" else silhouette_mode,
            "body_line": _SIL_BY_ROLE.get(str(character.get("role_type") or ""), "常规人体比例"),
            "palette": palette[:max_colors],
            "accent": palette[1] if len(palette) > 1 else accent,
            "age": character.get("age") or rng.choice(["25", "28", "32", "36", "45"]),
            "prop": str(character.get("prop") or (character.get("props") or [""])[0] or "随身小物"),
            "appearances": times,
            "prompt_hint": f"{name}, {wardrobe['look']}, {makeup['hair']} hair, {palette[0]} costume with {palette[1]} accent",
        }
        cast.append(item)
        lines.append(f"· {name}〔{item['role_type'] or '-'}〕{item['wardrobe']}｜妆：{item['makeup']}｜换装 {changes} 套｜配色 {' '.join(item['palette'])}")
        if bool(ctx.params.get("sheets")):
            png = canvas.character_sheet(item, look)
            images.append(
                common.image_artifact(
                    ctx,
                    f"character-{common.slug(name)}-d1.png",
                    png,
                    {"kind": "character_sheet", "id": f"character-{common.slug(name)}", "character": name, "prompt": item["prompt_hint"], "seed": 0, "simulated": True, "provider": "canvas"},
                )
            )

    text = "\n".join(lines + [f"共 {len(cast)} 个角色，设定卡 {len(images)} 张；配色已与视觉基调对齐"])
    ctx.log(f"造型完成：{len(cast)} 个角色 / {len(images)} 张设定卡")
    return {"characters": cast, "images": {"items": images}, "text": text}


@node(
    "set_design",
    title="场景设计",
    dept="art",
    role="逐个场景定空间、材质、陈设与光源方向，出场景概念帧，供美术搭景与摄影布光复用",
    inputs=[
        Port("locations", "场景表", "locations"),
        Port("look", "视觉基调", "look", required=False),
        Port("art_plan", "美术方案", "plan", required=False),
    ],
    outputs=[Port("locations", "美术场景表", "locations"), Port("images", "场景概念帧", "images"), Port("text", "场景说明", "text")],
    params=[
        Param("width", "概念帧宽", "number", 380, minimum=240, maximum=720, step=20, unit="px"),
        Param("render", "出概念帧", "toggle", True),
        Param("depth", "空间纵深强调", "select", "中", ["弱", "中", "强"], hint="决定地平线高度与前景遮挡"),
    ],
    icon="🏗️",
    tags=["美术", "场景"],
)
def set_design(ctx: Any) -> dict[str, Any]:
    look = common.look_of(ctx.in_("look"))
    art_plan = ctx.in_("art_plan") or {}
    locations = common.locations_of(ctx.in_("locations"))
    if not locations:
        raise NodeError("场景设计需要上游场景表：接「文学策划」的「场景清单」")
    aspect = str(look.get("aspect") or "9:16")
    width = int(float(ctx.params.get("width") or 380))
    depth = str(ctx.params.get("depth") or "中")
    horizon = {"弱": 0.58, "中": 0.66, "强": 0.74}[depth]
    materials = list(art_plan.get("materials") or []) or ERA_PRESETS["当代都市"]["materials"]
    global_dressing = [str(item) for item in (art_plan.get("dressing") or [])]

    enriched: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    lines: list[str] = ["场景设计表："]
    for index, location in enumerate(locations):
        name = str(location.get("name") or f"场景{index + 1}")
        time_of_day = str(location.get("time_of_day") or "day")
        lighting = craft.lighting_for(time_of_day, str(location.get("mood") or look.get("mood") or "neutral"))
        props = [str(item) for item in (location.get("props") or [])]
        dressing = props + [item for item in global_dressing if item not in props][: (2 if depth == "弱" else 4)]
        palette = [str(item) for item in (location.get("palette_hint") or [])] or [str(item) for item in (look.get("palette") or materials[:3])]
        rng = craft.rng_for("set", name)
        item = {
            **location,
            "name": name,
            "space": rng.choice(["单一空间纵深处", "前中后三层可调度", "狭长走位通道", "开阔多点包围"]),
            "materials": [materials[(index + offset) % len(materials)] for offset in range(min(3, len(materials)))],
            "dressing": dressing,
            "palette": palette[:4],
            "light_source": f"{lighting['direction']}｜{lighting['temp_k']}K｜{lighting['note']}",
            "horizon": horizon,
            "build_days": round(0.5 + len(dressing) * 0.12, 1),
        }
        enriched.append(item)
        lines.append(f"· {name}〔{item['int_ext'] or 'INT'}／{time_of_day}〕{item['space']}｜材质 {'、'.join(item['materials'])}｜陈设 {'、'.join(item['dressing']) or '无'}")
        if bool(ctx.params.get("render")):
            png = canvas.scene_frame(
                aspect=aspect,
                width=width,
                look=look,
                figure=0.0 if index % 3 == 2 else 0.28,
                horizon=horizon,
                light_side=0.28 if index % 2 == 0 else 0.7,
                slate=[f"SET {index + 1}", name[:18], f"{lighting['temp_k']}K", item["space"][:14]],
                extra={"seed": name},
            )
            images.append(
                common.image_artifact(
                    ctx,
                    f"set-{common.slug(name)}-v{ctx.params.get('depth')}.png",
                    png,
                    {"kind": "concept", "id": f"set-{common.slug(name)}", "character": "", "prompt": f"{name}, {item['space']}, {item['light_source']}", "seed": 0, "simulated": True, "provider": "canvas"},
                )
            )

    text = "\n".join(lines + [f"共 {len(enriched)} 个场景，搭景估算 {round(sum(float(row['build_days']) for row in enriched), 1)} 天"])
    ctx.log(f"场景设计：{len(enriched)} 场 / 概念帧 {len(images)} 张")
    return {"locations": enriched, "images": {"items": images}, "text": text}


@node(
    "wardrobe_props",
    title="服化道主管",
    dept="art",
    role="把造型方案变成可采购的执行清单：服装表、化妆表、道具备品数与连续性风险",
    inputs=[
        Port("characters", "造型角色表", "characters"),
        Port("screenplay", "剧本", "screenplay", required=False),
        Port("locations", "场景表", "locations", required=False),
        Port("shots", "分镜表", "shots", required=False),
    ],
    outputs=[Port("plan", "采购与备品清单", "plan"), Port("text", "三单明细", "text")],
    params=[
        Param("spare_mult", "备品倍率", "number", 1.0, minimum=0.5, maximum=2.0, step=0.1, unit="倍", hint="重拍概率高就调大"),
        Param("duplicates", "服装备份套数", "number", 2, minimum=1, maximum=4, step=1, unit="套"),
        Param("continuity", "连续性检查", "toggle", True, hint="跨场景同装/同妆/同伤口的核对"),
        Param("key_prop", "关键道具", "text", "", hint="需要特写与最高备品数的那一件，留空则不加"),
    ],
    icon="🧵",
    tags=["美术", "执行"],
)
def wardrobe_props(ctx: Any) -> dict[str, Any]:
    roster = common.characters_of(ctx.in_("characters"))
    screenplay = ctx.in_("screenplay") or {}
    locations = common.locations_of(ctx.in_("locations"))
    shots = common.shots_of(ctx.in_("shots"))
    if not roster:
        raise NodeError("服化道需要角色表：先接「角色造型设计」")
    mult = max(0.5, float(ctx.params.get("spare_mult") or 1.0))
    duplicates = int(float(ctx.params.get("duplicates") or 2))

    costume_rows: list[dict[str, Any]] = []
    makeup_rows: list[dict[str, Any]] = []
    for character in roster:
        name = str(character.get("name") or "角色")
        changes = max(1, int(float(character.get("costume_changes") or 1)))
        costume_rows.append(
            {
                "character": name,
                "items": str(character.get("wardrobe") or ""),
                "looks": changes,
                "sets": changes * duplicates,
                "note": str(character.get("silhouette") or ""),
            }
        )
        makeup_rows.append(
            {
                "character": name,
                "face": str(character.get("makeup") or ""),
                "hair": str(character.get("hair") or ""),
                "fx": str(character.get("fx_note") or ""),
                "touchups": max(1, int(round(changes * 1.5))),
            }
        )

    prop_pool: list[str] = []
    for row in list(screenplay.get("scenes") or []) + locations + shots:
        for prop in (row.get("props") or []):
            text_prop = str(prop)
            if text_prop and text_prop not in prop_pool:
                prop_pool.append(text_prop)
    key_prop = str(ctx.params.get("key_prop") or "").strip()
    if key_prop and key_prop not in prop_pool:
        prop_pool.insert(0, key_prop)

    prop_rows: list[dict[str, Any]] = []
    for prop in prop_pool:
        count, reason = _spares_for(prop)
        prop_rows.append({"prop": prop, "qty": max(1, int(round(count * mult))), "reason": reason})

    risks: list[str] = []
    if bool(ctx.params.get("continuity")):
        for character in roster:
            name = str(character.get("name") or "")
            times = _scene_appearances(screenplay, name)
            if times >= 3 and int(float(character.get("costume_changes") or 1)) < 2:
                risks.append(f"{name} 出场 {times} 场但只有 1 套造型：跨天镜头需备同款替换并记录穿着顺序")
        wet = [prop for prop in prop_pool if any(key in prop for key in ("水", "雨", "汗", "汤"))]
        if wet:
            risks.append(f"涉及液体的道具（{ '、'.join(wet[:3]) }）需防水标签与备份，拍摄中会变形")
    if not shots:
        risks.append("未接分镜表：备品数按剧本估算，开机前需按镜头量复核")

    total_sets = sum(int(row["sets"]) for row in costume_rows)
    total_props = sum(int(row["qty"]) for row in prop_rows)
    plan = {
        "dept": "art",
        "role": "服化道主管",
        "costume": costume_rows,
        "makeup": makeup_rows,
        "props": prop_rows,
        "counts": {"costume_sets": total_sets, "makeup_touchups": sum(int(row["touchups"]) for row in makeup_rows), "prop_items": total_props},
        "risks": risks,
        "simulated": True,
    }
    text = "\n".join(
        [
            "服装表：",
            *[f"· {row['character']}：{row['items']}｜{row['looks']} 个造型 / {row['sets']} 套备份" for row in costume_rows],
            "化妆表：",
            *[f"· {row['character']}：{row['face']}／{row['hair']}｜补妆 {row['touchups']} 次｜{row['fx']}" for row in makeup_rows],
            "道具表：",
            *[f"· {row['prop']} ×{row['qty']}（{row['reason']}）" for row in prop_rows],
            f"合计：服装 {total_sets} 套 / 道具 {total_props} 件｜风险 {len(risks)} 条",
        ]
    )
    ctx.log(f"三单：服装 {total_sets} 套 / 道具 {total_props} 件 / 风险 {len(risks)} 条")
    return {"plan": plan, "text": text}


@node(
    "concept_artist",
    title="概念图 · 关键帧",
    dept="art",
    role="按分镜逐镜出关键帧概念图：配置了图片模型走真机，否则离线画一帧构图示意",
    inputs=[
        Port("shots", "分镜表", "shots"),
        Port("look", "视觉基调", "look", required=False),
        Port("characters", "造型角色表", "characters", required=False),
        Port("model", "模型配置", "model", required=False),
    ],
    outputs=[Port("images", "关键帧概念图", "images"), Port("text", "出图说明", "text")],
    params=[
        Param("limit", "最多出图镜数", "number", 8, minimum=1, maximum=40, step=1, unit="镜"),
        Param("pick", "挑选口径", "select", "每场首镜", ["全部镜头", "每场首镜", "只出有台词", "只出钩子与高潮"]),
        Param("width", "画面宽", "number", 420, minimum=240, maximum=1024, step=20, unit="px"),
        Param("variant", "方案版本号", "number", 1, minimum=1, maximum=9, step=1, hint="同一镜多版对比时递增"),
    ],
    icon="🖼️",
    tags=["美术", "生成"],
)
def concept_artist(ctx: Any) -> dict[str, Any]:
    timeline = common.require_shots(ctx.in_("shots"), "概念图 · 关键帧")
    shots = common.shots_of(timeline)
    look = common.look_of(ctx.in_("look"))
    roster = common.characters_of(ctx.in_("characters"))
    model = ctx.in_("model") or {}
    aspect = str(timeline.get("aspect") or look.get("aspect") or "9:16")
    width = int(float(ctx.params.get("width") or 420))
    variant = int(float(ctx.params.get("variant") or 1))
    limit = int(float(ctx.params.get("limit") or 8))
    pick = str(ctx.params.get("pick") or "每场首镜")

    selected: list[dict[str, Any]] = []
    seen_scenes: set[str] = set()
    for shot in shots:
        scene_id = str(shot.get("scene_id") or "")
        if pick == "每场首镜" and scene_id in seen_scenes:
            continue
        if pick == "只出有台词" and not (shot.get("dialogue") or []):
            continue
        if pick == "只出钩子与高潮" and str(shot.get("beat") or "") not in {"hook", "payoff", "cta", "tension"}:
            continue
        if pick == "每场首镜":
            seen_scenes.add(scene_id)
        selected.append(shot)
        if len(selected) >= limit:
            break

    items: list[dict[str, Any]] = []
    for shot in selected:
        size_key = str(shot.get("size") or "medium")
        figure = float(craft.SHOT_SIZES.get(size_key, {}).get("figure", 0.55))
        angle = str(shot.get("angle") or "eye")
        light_side = {"high": 0.68, "low": 0.22, "overhead": 0.5, "dutch": 0.35}.get(angle, 0.3)
        prompt = str(shot.get("prompt") or common.shot_prompt(shot, look, roster))
        draw_w, draw_h = canvas.size_for(aspect, width)
        item = common.generate_or_draw(
            ctx,
            prompt,
            f"concept-{common.slug(str(shot.get('slate') or shot.get('id')))}-v{variant}.png",
            f"{draw_w}x{draw_h}",
            lambda shot=shot, figure=figure, light_side=light_side: canvas.scene_frame(
                aspect=aspect,
                width=width,
                look=look,
                figure=figure * 0.9,
                horizon=0.66 if figure < 0.7 else 0.52,
                light_side=light_side,
                motion=str(shot.get("movement") or ""),
                slate=[str(shot.get("slate") or shot["id"]), str(shot.get("size_label") or size_key), f"{shot.get('lens_mm', 35)}mm", f"{shot.get('duration_s')}s"],
                extra={"seed": str(shot.get("id"))},
            ),
            {
                "kind": "concept",
                "id": f"concept-{shot['id']}",
                "shot_index": shot.get("index"),
                "character": str(shot.get("focus_character") or ""),
                "prompt": prompt,
                "seed": int(shot.get("index") or 0) * 7 + variant,
                "simulated": True,
            },
        )
        items.append(item)

    real = [item for item in items if not item.get("simulated")]
    text = "\n".join(
        [
            f"关键帧概念图：{len(items)} 张（口径：{pick}）",
            f"真机出图 {len(real)} 张 / 离线示意 {len(items) - len(real)} 张",
            "构图按景别占比与机位角度生成，调色与颗粒沿用视觉基调；正式素材由「关键帧生成」节点替换。",
        ]
    )
    ctx.log(f"概念图 {len(items)} 张：真机 {len(real)}，示意 {len(items) - len(real)}", model=str(model.get("model") or ""))
    return {"images": {"items": items}, "text": text}


#: 肤色 / 发色候选（离线示意用，真机出图时由参考素材覆盖）
SKIN_TONES = ["#e6c3a8", "#d8b29a", "#c49a78", "#a97a56", "#7d5334"]
HAIR_TONES = ["#2a2118", "#3b2a1e", "#5a3b22", "#1d1b20", "#6e5b47", "#8c8f96"]

#: 没接通告表时，用「1 秒成片 ≈ 2 分钟在场」粗估演员占用工时
_ON_SET_MIN_PER_SEC = 2.0


@node(
    "makeup_fx",
    title="特效化妆",
    dept="art",
    role="把造型落到化妆台：定妆面档位、伤效与特殊件位置、逐镜接戏妆态与补妆时点，出每人一张能照着上妆的妆容卡",
    inputs=[
        Port("characters", "角色造型表", "characters"),
        Port("shots", "分镜表", "shots", required=False),
        Port("look", "视觉基调", "look", required=False),
        Port("call_sheet", "通告表", "plan", required=False),
    ],
    outputs=[
        Port("images", "妆容卡", "images"),
        Port("plan", "妆效方案", "plan"),
        Port("text", "妆效说明", "text"),
    ],
    params=[
        Param("level", "特效档位", "select", "标准", ["素颜", "轻", "标准", "重"], hint="决定每组特效件数量与上妆时长"),
        Param("cards", "出妆容卡", "toggle", True, hint="每人一张 PNG：伤效位置 + 用色条 + 接戏口径"),
        Param("touchup_min", "补妆间隔", "number", 90, minimum=30, maximum=180, step=15, unit="分钟", hint="演员在场多久回一次化妆台"),
        Param("prep_min", "基础上妆", "number", 35, minimum=10, maximum=120, step=5, unit="分钟", hint="底妆与发型固定开销，档位越重在此之上加"),
        Param("max_chars", "最多人数", "number", 8, minimum=1, maximum=20, step=1, unit="人"),
    ],
    icon="💄",
    tags=["美术", "化妆", "接戏"],
)
def makeup_fx(ctx: Any) -> dict[str, Any]:
    look = common.look_of(ctx.in_("look"))
    roster = common.characters_of(ctx.in_("characters"))
    if not roster:
        raise NodeError("特效化妆需要上游角色表：先接「角色造型设计」或「选角导演」")
    shots = common.shots_of(ctx.in_("shots"))
    level = str(ctx.params.get("level") or "标准")
    quota, extra_prep = FX_LEVEL_QUOTA.get(level, FX_LEVEL_QUOTA["标准"])
    touchup_min = float(ctx.params.get("touchup_min") or 90)
    prep_min = float(ctx.params.get("prep_min") or 35) + extra_prep
    max_chars = int(float(ctx.params.get("max_chars") or 8))
    call_sheet = ctx.in_("call_sheet") if isinstance(ctx.in_("call_sheet"), dict) else {}
    hair_window = float(call_sheet.get("hair_minutes") or 0)
    calls = {str(row.get("character") or ""): row for row in (call_sheet.get("cast_calls") or []) if isinstance(row, dict)}

    def matched(words: str) -> list[tuple[str, list[dict[str, Any]]]]:
        return [(pack, pieces) for keys, pack, pieces in FX_PACKS if any(key in words for key in keys)]

    rows: list[dict[str, Any]] = []
    images: list[dict[str, Any]] = []
    conflicts: list[str] = []
    risks: list[str] = []
    lines: list[str] = [f"妆效方案（档位：{level}，基础上妆 {prep_min:.0f} 分钟）："]
    for index, character in enumerate(roster[:max_chars]):
        name = str(character.get("name") or f"角色{index + 1}")
        char_words = " ".join(
            str(character.get(key) or "") for key in ("wardrobe", "makeup", "hair", "fx_note", "prop", "role_type", "description")
        )
        appearances: list[dict[str, Any]] = []
        for position, shot in enumerate(shots):
            cast = [str(item or "") for item in (shot.get("characters") or [])]
            spoken = [str(line.get("character") or "") for line in (shot.get("dialogue") or []) if isinstance(line, dict)]
            if name not in cast and name != str(shot.get("focus_character") or "") and name not in spoken:
                continue
            words = f"{shot.get('description') or ''} {shot.get('mood') or ''} {char_words}"
            packs = matched(words)
            signature = "、".join(str(item[0]) for item in packs) or "干净妆面"
            appearances.append(
                {
                    "slate": str(shot.get("slate") or shot.get("id") or f"shot-{position + 1}"),
                    "index": int(shot.get("index") if shot.get("index") is not None else position),
                    "scene_id": str(shot.get("scene_id") or "S0"),
                    "mood": str(shot.get("mood") or look.get("mood") or "neutral"),
                    "state": signature,
                    "packs": packs,
                }
            )
        by_scene: dict[str, list[dict[str, Any]]] = {}
        for item in appearances:
            by_scene.setdefault(item["scene_id"], []).append(item)
        for scene_id, items in sorted(by_scene.items()):
            states = sorted({str(item["state"]) for item in items})
            if len(states) > 1:
                detail = "｜".join(f"{item['slate']}={item['state']}" for item in items)
                conflicts.append(f"{name} 场 {scene_id} 妆态不一：{detail}")
        mood_count: dict[str, int] = {}
        for item in appearances:
            mood_count[item["mood"]] = int(mood_count.get(item["mood"], 0)) + 1
        mood = max(mood_count, key=lambda key: mood_count[key]) if mood_count else str(character.get("mood") or look.get("mood") or "neutral")
        face = MAKEUP_BY_MOOD.get(mood, MAKEUP_BY_MOOD["neutral"])
        pack_rows: list[dict[str, Any]] = []
        pack_names: list[str] = []
        for item in appearances:
            if item["state"] != "干净妆面" and item["state"] not in pack_names:
                pack_names.append(item["state"])
        wanted = {str(row["state"]): row["packs"] for row in appearances}
        for pack_name in pack_names[:3]:
            groups = [g for _, g in wanted.get(pack_name, [])]
            for group in groups:
                for piece in group[:quota]:
                    if len(pack_rows) >= 5:
                        break
                    if any(str(done["label"]) == str(piece["label"]) for done in pack_rows):
                        continue
                    pack_rows.append(dict(piece))
        rng = craft.rng_for("makeup", name, level)
        palette = [str(item) for item in (character.get("palette") or [])] or list(look.get("palette") or [])
        swatches = [str(character.get("accent") or look.get("accent") or "#c2495a")] + [
            str(piece["color"]) for piece in pack_rows
        ] + palette[:2]
        call = calls.get(name) or {}
        finished_s = sum(float(shot.get("duration_s") or 2.0) for shot in shots if str(shot.get("slate") or "") in {str(item["slate"]) for item in appearances})
        on_set = float(call.get("on_set_min") or 0) or round(finished_s * _ON_SET_MIN_PER_SEC, 1)
        touchups = max(0, int(on_set // touchup_min)) if on_set else 0
        row = {
            "id": f"makeup-{common.slug(name)}",
            "character": name,
            "level": level,
            "mood": mood,
            "face": face["face"],
            "hair": face["hair"],
            "fx": face["extra"] if not pack_rows else f"{'、'.join(pack_names)}（{len(pack_rows)} 件）",
            "pieces": pack_rows,
            "prep_min": round(prep_min, 1),
            "shots": len(appearances),
            "slates": [str(item["slate"]) for item in appearances],
            "states": [{"slate": item["slate"], "scene_id": item["scene_id"], "state": item["state"]} for item in appearances],
            "scenes": len(by_scene),
            "continuity": "同场妆态必须一致" if by_scene else "无分镜可核对",
            "on_set_min": on_set,
            "touchups": touchups,
            "set_call": str(call.get("set_call") or ""),
            "simulated": True,
        }
        rows.append(row)
        lines.append(
            f"· {name}〔{mood}〕底：{row['face']}｜发：{row['hair']}｜特效：{row['fx']}｜上妆 {row['prep_min']:.0f}′｜镜 {row['shots']} 场 {row['scenes']}｜补妆 {touchups} 次"
        )
        if bool(ctx.params.get("cards")):
            card = {
                "name": name,
                "skin": rng.choice(SKIN_TONES),
                "hair_color": rng.choice(HAIR_TONES),
                "accent": str(character.get("accent") or look.get("accent") or "#c2495a"),
                "makeup": row["face"],
                "hair": row["hair"],
                "fx": row["fx"],
                "continuity": f"{row['scenes']} scenes must match",
                "shot_count": row["shots"],
                "touchup": f"{touchups} x {touchup_min:.0f}min",
                "fx_rows": pack_rows,
                "makeup_palette": swatches,
            }
            images.append(
                common.image_artifact(
                    ctx,
                    f"makeup-{common.slug(name)}-{common.slug(level, 'look')}.png",
                    canvas.makeup_card(card, look),
                    {
                        "kind": "makeup_card",
                        "id": row["id"],
                        "character": name,
                        "prompt": f"{name}, {row['face']}, {row['hair']}, fx: {row['fx']}",
                        "seed": index,
                        "simulated": True,
                        "provider": "canvas",
                    },
                )
            )

    if not bool(ctx.params.get("cards")):
        risks.append("未出妆容卡：化妆台只能靠文字口径上妆")
    if conflicts:
        risks.append(f"{len(conflicts)} 处接戏冲突（同场妆态不一致），需按拍摄顺序锁定最重的一版")
    if hair_window and rows:
        over = [str(row["character"]) for row in rows if float(row["prep_min"]) > hair_window]
        if over:
            risks.append(f"通告表只留了 {hair_window:.0f} 分钟妆发：{('、'.join(over[:4]))} 需要 {prep_min:.0f} 分钟，得让演员更早集合")
    if not call_sheet:
        risks.append("没接通告表：补妆次数按镜头时长粗估，接上「第一副导演」后按在场工时计算")
    if not shots:
        risks.append("没接分镜表：无法逐镜核对妆态连续性")

    plan = {
        "dept": "art",
        "role": "特效化妆",
        "items": rows,
        "level": level,
        "prep_min": round(prep_min, 1),
        "touchup_min": touchup_min,
        "character_count": len(rows),
        "piece_count": sum(len(row["pieces"]) for row in rows),
        "card_count": len(images),
        "touchup_total": int(sum(int(row["touchups"]) for row in rows)),
        "continuity_conflicts": conflicts[:12],
        "risks": risks or ["妆效与通告窗口匹配，无接戏冲突"],
        "simulated": True,
    }
    text = "\n".join(
        [
            *lines,
            f"共 {len(rows)} 人：妆容卡 {len(images)} 张｜特效件 {plan['piece_count']} 件｜补妆合计 {plan['touchup_total']} 次",
            ("接戏冲突：" + "；".join(conflicts[:3])) if conflicts else "接戏：同场各镜妆态一致",
            "⚠ " + "；".join(risks[:4] or ["无风险"]),
        ]
    )
    ctx.log(f"妆效：{len(rows)} 人 / {len(images)} 张卡 / {plan['piece_count']} 件特效，冲突 {len(conflicts)}")
    return {"images": {"items": images}, "plan": plan, "text": text}
