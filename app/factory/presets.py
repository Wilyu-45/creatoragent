"""影视制作工厂 —— 岗位流程预设。

预设不是"示例图"，而是一条被验证过的生产脚本：每个 Stage 声明岗位类型、
画布列/行、参数与该岗位需要的上游交付物（``port=源岗位:端口``）。列/行会自动
换算成画布坐标，连线编号自动生成，因此调整岗位顺序或增删连线不会留下悬挂的
坐标与 id。派生的子流程预设由整图按岗位集合裁剪得到，剪掉的连线如实反映为
"必需交付物未接线"的体检告警，而不是悄悄补一条假连线。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .nodes.source import DEMO_ASSET_REF

X_STEP = 300.0
Y_STEP = 300.0


@dataclass(frozen=True)
class Stage:
    """一个岗位在流程里的位置：谁坐这一格、吃什么交付物。"""

    id: str
    type: str
    col: int
    row: int
    params: dict[str, Any] = field(default_factory=dict)
    #: 端口名 → 上游（"岗位:端口"，或列表表示多路汇入）
    feeds: dict[str, Any] = field(default_factory=dict)


#: 全链路：素材（需求单 / 灵感文本 / 参考图 / 出海底稿）→ 策划 → 文学 → 编剧 →
#: 剧本医生 → 导演 → 分镜 → 选角/场记/表演指导/场地经理/特技协调 → 制片/版权 →
#: 美术/角色/置景/服化道/概念/妆容 → 摄影/灯光/机械/跟焦/通告表 →
#: 配音/同期录音/拟音/补录/音效/配乐/混音 → 模型/关键帧/生成/数字人/剧照 →
#: 助理剪辑归条 → 剪辑 → 调色/视效/包装/字幕/封面/DIT 数据 → 预告片 → 导出（正片 + 预告两条）→
#: 审片/合规/事实核查/译制 → 发行/宣传/媒介投放 → 复盘 → 放映室
FULL: list[Stage] = [
    Stage("n-brief", "project_input", 0, 0, {
        "title": "夏日便携风扇实测", "type": "种草视频", "genre": "消费品", "duration_s": 48,
        "aspect": "9:16", "platform": "小红书", "audience": "通勤族", "keywords": "便携风扇,夏日通勤",
        "budget": 24000, "synopsis": "把通勤路上的闷热问题解决掉", "brand": "风语", "product": "挂颈风扇 Air3",
    }),
    Stage("n-note", "text_input", 0, 1, {
        "label": "网上冲浪灵感",
        "text": "刷到的高赞说法：早八挤地铁最烦后背黏脖子；试了三个挂颈风扇，只有出风口对着颈动脉的才真的凉；\n"
        "评论区追问：噪音多大？能不能过安检？——这三条要在一分钟内答完。",
    }),
    Stage("n-sp-in", "screenplay_input", 13, 7, {
        "title": "译制底稿 · 同题材短片", "default_duration": 4.0,
        "text": "场景1 外 地铁站出口 日\n林夏拎着公文包挤出人群，抬手抹了一下脖子上的汗。\n"
        "林夏：三十七度，早八，还得挤地铁。\n"
        "场景2 内 办公室 日\n工位上挂着一个小风扇，同事凑过来看。\n"
        "同事：这玩意儿真凉？\n林夏：对着颈动脉吹，三秒就降温。\n"
        "场景3 内 地铁车厢 夜\n车厢里她戴着风扇看书，旁边的人热得扇文件。\n"
        "林夏：一整天没出过汗。\n",
    }),
    Stage("n-plan", "planner", 1, 0, feeds={"brief": "n-brief:brief", "text": ["n-note:text"]}),
    Stage("n-lit", "literary", 2, 0, feeds={"outline": "n-plan:outline", "brief": "n-brief:brief", "text": ["n-note:text"]}),
    Stage("n-wri", "screenwriter", 3, 0, feeds={
        "outline": "n-lit:outline", "characters": "n-lit:characters",
        "locations": "n-lit:locations", "brief": "n-brief:brief",
    }),
    Stage("n-doc", "script_doctor", 4, 0, feeds={"screenplay": "n-wri:screenplay"}),
    Stage("n-dir", "director", 3, 1, feeds={"screenplay": "n-doc:screenplay", "brief": "n-brief:brief"}, params={"style": "documentary", "pacing": "快速"}),
    Stage("n-sb", "storyboard", 4, 1, feeds={"screenplay": "n-doc:screenplay", "look": "n-dir:look"}),
    Stage("n-cast", "casting", 5, 0, feeds={"characters": "n-lit:characters", "screenplay": "n-doc:screenplay"}),
    Stage("n-sup", "script_supervisor", 5, 1, feeds={"shots": "n-sb:shots", "characters": "n-cast:characters", "screenplay": "n-doc:screenplay"}),
    Stage("n-act", "acting_coach", 5, 3, feeds={"shots": "n-sb:shots", "characters": "n-cast:characters", "look": "n-dir:look"}, params={"max_lines": 18}),
    Stage("n-site", "location_manager", 5, 4, feeds={"locations": "n-lit:locations", "shots": "n-sb:shots", "plan": "n-prod:plan"}),
    Stage("n-stunt", "stunt_coordinator", 5, 5, feeds={"shots": "n-sb:shots", "characters": "n-cast:characters", "brief": "n-brief:brief"}, params={"takes": 2}),
    Stage("n-prod", "producer", 2, 2, feeds={"brief": "n-brief:brief", "outline": "n-plan:outline"}),
    Stage("n-rights", "rights", 3, 2, feeds={"brief": "n-brief:brief", "screenplay": "n-doc:screenplay"}),
    Stage("n-art", "art_director", 5, 2, feeds={
        "brief": "n-brief:brief", "look": "n-dir:look",
        "screenplay": "n-doc:screenplay", "locations": "n-lit:locations",
    }, params={"era": "当代都市", "key_prop": "挂颈风扇 Air3"}),
    Stage("n-char", "character_design", 6, 0, feeds={"characters": "n-lit:characters", "look": "n-dir:look", "screenplay": "n-doc:screenplay"}),
    Stage("n-set", "set_design", 6, 1, feeds={"locations": "n-site:locations", "look": "n-dir:look", "art_plan": "n-art:plan"}),
    Stage("n-ward", "wardrobe_props", 6, 2, feeds={
        "characters": "n-char:characters", "screenplay": "n-doc:screenplay",
        "locations": "n-set:locations", "shots": "n-sb:shots",
    }, params={"key_prop": "挂颈风扇 Air3"}),
    Stage("n-con", "concept_artist", 6, 3, feeds={"shots": "n-sb:shots", "look": "n-dir:look", "characters": "n-char:characters"}, params={"limit": 4}),
    Stage("n-ref", "ref_images", 6, 4, params={"refs": DEMO_ASSET_REF, "kind": "reference", "note": "本机示例色板（建项目时播种到 data/assets）"}),
    Stage("n-mua", "makeup_fx", 6, 5, feeds={
        "characters": "n-char:characters", "shots": "n-sb:shots",
        "look": "n-dir:look", "call_sheet": "n-ad1:plan",
    }, params={"level": "标准"}),
    Stage("n-dop", "dop", 7, 1, feeds={
        "shots": "n-sb:shots", "look": "n-dir:look",
        "brief": "n-brief:brief", "art_plan": "n-art:plan",
    }, params={"card_limit": 4}),
    Stage("n-focus", "focus_puller", 8, 0, feeds={"camera_plan": "n-dop:camera_plan"}),
    Stage("n-gaf", "gaffer", 8, 1, feeds={"camera_plan": "n-dop:camera_plan", "look": "n-dir:look", "locations": "n-set:locations"}, params={"diagram_limit": 3}),
    Stage("n-grip", "grip", 8, 2, feeds={"camera_plan": "n-dop:camera_plan", "shots": "n-sb:shots"}, params={"fx": "风机"}),
    Stage("n-ad1", "assistant_director", 8, 6, feeds={
        "shots": "n-sb:shots", "camera_plan": "n-dop:camera_plan", "characters": "n-cast:characters",
        "locations": "n-set:locations", "stunts": "n-stunt:plan",
    }),
    Stage("n-vd", "voice_director", 7, 3, feeds={"characters": "n-cast:characters", "shots": "n-sb:shots", "brief": "n-brief:brief"}),
    Stage("n-dub", "dubber", 8, 3, feeds={"shots": "n-sb:shots", "characters": "n-cast:characters", "work": "n-vd:plan"}, params={"max_lines": 14}),
    Stage("n-rec", "production_sound", 8, 5, feeds={"shots": "n-sb:shots", "locations": "n-set:locations", "camera_plan": "n-dop:camera_plan"}),
    Stage("n-mus", "composer", 9, 1, feeds={"shots": "n-sb:shots", "look": "n-dir:look", "brief": "n-brief:brief"}),
    Stage("n-sfx", "sfx_designer", 9, 2, feeds={"shots": "n-sb:shots", "camera_plan": "n-dop:camera_plan", "look": "n-dir:look"}, params={"limit": 24}),
    Stage("n-adr", "adr", 9, 3, feeds={"shots": "n-sb:shots", "voice": "n-dub:audio", "work": "n-vd:plan", "recording": "n-rec:plan", "report": "n-sup:report"}),
    Stage("n-fol", "foley", 9, 6, feeds={"shots": "n-sb:shots", "recording": "n-rec:plan", "camera_plan": "n-dop:camera_plan"}, params={"limit": 22}),
    Stage("n-stills", "stills_photographer", 9, 7, feeds={
        "shots": "n-sb:shots", "images": ["n-key:images", "n-con:images"],
        "clips": ["n-gen:clips", "n-ava:clips"], "look": "n-dir:look", "brief": "n-brief:brief",
    }, params={"limit": 14, "per_scene": 2}),
    Stage("n-mix", "mixer", 10, 2, feeds={"tracks": ["n-adr:audio", "n-rec:audio", "n-fol:audio", "n-sfx:audio", "n-mus:audio"], "shots": "n-sb:shots"}, params={"stem": True}),
    Stage("n-dit", "dit", 10, 5, feeds={
        "images": ["n-key:images", "n-con:images", "n-stills:images"],
        "clips": ["n-gen:clips", "n-ava:clips"],
        "audio": "n-mix:audio",
        "shots": "n-sb:shots",
        "camera_plan": "n-dop:camera_plan",
    }, params={"limit": 220}),
    Stage("n-model", "model_config", 7, 4, params={"task": "i2v", "provider": "auto", "duration_s": 6, "size": "768x1024"}),
    Stage("n-key", "keyframe_artist", 8, 4, feeds={"shots": "n-sb:shots", "look": "n-dir:look", "refs": ["n-con:images", "n-ref:images", "n-mua:images"], "model": "n-model:model"}, params={"frames": "首尾帧", "limit": 12, "width": 320}),
    Stage("n-gen", "video_generator", 9, 4, feeds={"shots": "n-sb:shots", "frames": "n-key:images", "look": "n-dir:look", "model": "n-model:model"}, params={"limit": 12, "frames_per_clip": 4, "width": 240}),
    Stage("n-ava", "avatar_actor", 9, 5, feeds={"shots": "n-sb:shots", "characters": "n-char:characters", "audio": "n-dub:audio", "look": "n-dir:look", "model": "n-model:model", "direction": "n-act:plan"}, params={"limit": 3, "fps": 12, "width": 240, "role": "有台词的镜头"}),
    Stage("n-assist", "assistant_editor", 10, 3, feeds={
        "shots": "n-sb:shots", "clips": ["n-gen:clips", "n-ava:clips"],
        "images": "n-key:images", "data": "n-dit:plan",
    }, params={"keep": 2}),
    Stage("n-cut", "editor", 11, 4, feeds={
        "shots": "n-sb:shots", "clips": "n-assist:clips", "images": "n-assist:images",
        "audio": ["n-dub:audio", "n-mus:audio", "n-mix:audio"], "brief": "n-brief:brief",
    }, params={"version": "正片", "j_cut": 0.2}),
    Stage("n-grd", "colorist", 12, 4, feeds={"timeline": "n-cut:timeline", "look": "n-dir:look"}, params={"limit": 12, "strength": 0.75}),
    Stage("n-vfx", "vfx_supervisor", 13, 4, feeds={"timeline": "n-grd:timeline", "look": "n-dir:look"}, params={"limit": 4, "density": 0.55}),
    Stage("n-pack", "packaging", 14, 4, feeds={"timeline": "n-vfx:timeline", "brief": "n-brief:brief", "characters": "n-char:characters", "look": "n-dir:look"}, params={"head_s": 1.2, "tail_s": 1.6}),
    Stage("n-sub", "subtitle_master", 12, 5, feeds={"timeline": "n-grd:timeline", "audio": "n-dub:audio", "brief": "n-brief:brief"}, params={"max_chars": 16}),
    Stage("n-cover", "cover_designer", 13, 5, feeds={"brief": "n-brief:brief", "timeline": "n-pack:timeline", "look": "n-dir:look", "images": ["n-con:images", "n-stills:images"]}, params={"layout": "三尺寸", "width": 320}),
    Stage("n-trl", "trailer_editor", 14, 3, feeds={
        "timeline": "n-pack:timeline", "audio": "n-mix:audio",
        "brief": "n-brief:brief", "look": "n-dir:look",
    }, params={"target": 15, "max_shot": 2.0, "pace": "炸点"}),
    Stage("n-trl-exp", "exporter", 15, 3, feeds={
        "timeline": "n-trl:timeline", "audio": "n-trl:audio",
        "look": "n-dir:look", "brief": "n-brief:brief",
    }, params={"width": 720, "bitrate": 4, "filename": "trailer", "srt": "不打包"}),
    Stage("n-exp", "exporter", 15, 4, feeds={
        "timeline": "n-pack:timeline", "subtitles": "n-sub:subtitles", "audio": "n-mix:audio",
        "clips": "n-gen:clips", "look": "n-dir:look", "brief": "n-brief:brief",
    }, params={"width": 1080, "bitrate": 6, "srt": "两者"}),
    Stage("n-rev", "reviewer", 15, 5, feeds={
        "timeline": "n-exp:timeline", "shots": "n-sb:shots", "screenplay": "n-doc:screenplay",
        "audio": "n-mix:audio", "subtitles": "n-sub:subtitles", "brief": "n-brief:brief",
    }, params={"strict": False}),
    Stage("n-comp", "compliance", 12, 6, feeds={"screenplay": "n-doc:screenplay", "timeline": "n-cut:timeline", "subtitles": "n-sub:subtitles", "brief": "n-brief:brief"}, params={"rewrite": True}),
    Stage("n-fact", "fact_checker", 13, 6, feeds={"screenplay": "n-doc:screenplay", "timeline": "n-cut:timeline", "brief": "n-brief:brief"}, params={"require_source": True}),
    Stage("n-loc", "localizer", 14, 7, feeds={"screenplay": "n-sp-in:screenplay", "subtitles": "n-sub:subtitles"}, params={"language": "en", "platform": "YouTube", "mode": "仅规格适配", "subtitle_cpl": 42, "dub_rate": 1.05}),
    Stage("n-dist", "distributor", 16, 4, feeds={
        "timeline": "n-pack:timeline", "brief": "n-brief:brief", "images": "n-cover:images",
        "subtitles": "n-sub:subtitles", "report": "n-rev:report",
    }, params={"platforms": "抖音,小红书,视频号,B站,快手"}),
    Stage("n-pub", "publicity", 17, 6, feeds={
        "timeline": "n-pack:timeline", "trailer": "n-trl:timeline", "brief": "n-brief:brief",
        "subtitles": "n-sub:subtitles", "images": ["n-cover:images", "n-stills:images", "n-mua:images", "n-char:images"],
        "distribution": "n-dist:plan", "report": ["n-rev:report", "n-comp:report"],
    }, params={"hooks": 8, "per_day": 3}),
    Stage("n-buy", "media_buyer", 18, 6, feeds={
        "plan": "n-pub:plan", "brief": "n-brief:brief", "prod": "n-prod:plan",
        "distribution": "n-dist:plan", "report": ["n-rev:report", "n-comp:report", "n-fact:report"],
    }, params={"budget": 0}),
    Stage("n-retro", "retro", 16, 5, feeds={
        "plan": "n-prod:plan", "call_sheet": "n-ad1:plan", "media": "n-buy:plan", "timeline": "n-exp:timeline",
        "report": ["n-rev:report", "n-comp:report", "n-fact:report", "n-dit:report", "n-stunt:report"],
        "clips": "n-gen:clips", "images": ["n-key:images", "n-grd:images", "n-cover:images", "n-stills:images"],
        "audio": "n-mix:audio", "brief": "n-brief:brief",
    }, params={"rate": 1200}),
    Stage("n-view", "screening", 17, 4, feeds={
        "timeline": "n-exp:timeline", "audio": "n-mix:audio",
        "subtitles": "n-sub:subtitles", "report": "n-rev:report",
    }, params={"label": "工厂成片试映", "note": "全链路预设"}),
]

#: 精简出片：砍掉美术/置景/服化道/摄影组/数字人/视效/包装/译制/现场组/数据/妆容/预告/场地/特技，保留能出成片的最短生产链
SHORT_SKIP = {
    "n-art", "n-set", "n-ward", "n-dop", "n-gaf", "n-grip", "n-focus",
    "n-sup", "n-prod", "n-rights", "n-ava", "n-vfx", "n-pack",
    "n-sp-in", "n-loc", "n-ad1", "n-act", "n-dit", "n-mua", "n-trl", "n-trl-exp",
    "n-site", "n-stunt",
}
#: 编剧台：只到分镜与选角，产出可拍脚本（附表演指导的逐句注释）
SCRIPT_KEEP = {
    "n-brief", "n-note", "n-plan", "n-lit", "n-wri", "n-doc", "n-dir", "n-sb",
    "n-cast", "n-act", "n-sup", "n-prod",
}
#: 声音台：选角 → 配音导演 → 配音 → 现场录音 → 拟音/补录 → 配乐 → 音效 → 混音
VOICE_KEEP = {
    "n-brief", "n-note", "n-plan", "n-lit", "n-wri", "n-doc", "n-dir", "n-sb", "n-cast",
    "n-vd", "n-dub", "n-rec", "n-fol", "n-adr", "n-sfx", "n-mus", "n-mix",
}
#: 生成台：分镜 → 声音（录音/拟音/补录）→ 模型/参考素材/关键帧/生成 → 归条 → 剪辑 → 调色 → 字幕 → 剧照 → 预告 → 导出 → DIT 数据校验 → 放映
GEN_KEEP = {
    "n-brief", "n-lit", "n-wri", "n-doc", "n-dir", "n-sb", "n-cast", "n-char", "n-con", "n-ref", "n-mua",
    "n-vd", "n-dub", "n-rec", "n-fol", "n-adr", "n-sfx", "n-mus", "n-mix",
    "n-model", "n-key", "n-gen", "n-assist", "n-cut", "n-grd", "n-sub", "n-stills", "n-trl", "n-trl-exp", "n-exp", "n-dit", "n-view",
}


def _subset(stages: list[Stage], keep: set[str], overrides: dict[str, dict[str, Any]] | None = None) -> list[Stage]:
    """按岗位集合裁剪整图：不在集合里的上游连线如实丢弃（体检会告警）。"""
    out: list[Stage] = []
    for stage in stages:
        if stage.id not in keep:
            continue
        feeds: dict[str, Any] = {}
        for port, src in stage.feeds.items():
            rows = src if isinstance(src, list) else [src]
            kept = [row for row in rows if row.split(":")[0] in keep]
            if not kept:
                continue
            feeds[port] = kept if isinstance(src, list) else kept[0]
        feeds.update((overrides or {}).get(stage.id, {}))
        out.append(Stage(id=stage.id, type=stage.type, col=stage.col, row=stage.row, params=dict(stage.params), feeds=feeds))
    return out


PRESETS: dict[str, dict[str, Any]] = {
    "full": {"title": "全链路", "note": "选题到放映，每个岗位都真的执行一次", "stages": FULL},
    "short": {
        "title": "精简出片",
        "note": "去掉美术组与视效包装，最短可跑通的成片链",
        "stages": _subset(FULL, {stage.id for stage in FULL} - SHORT_SKIP, {
            "n-exp": {"timeline": "n-grd:timeline"},
            "n-cover": {"timeline": "n-grd:timeline"},
            "n-dist": {"timeline": "n-exp:timeline"},
            "n-pub": {"timeline": "n-exp:timeline"},
        }),
    },
    "script": {"title": "编剧台 · 文学到分镜", "note": "选题 → 大纲 → 剧本 → 分镜表", "stages": _subset(FULL, SCRIPT_KEEP)},
    "voice": {"title": "声音台 · 选角到混音", "note": "配音导演 → 配音 → 配乐 → 音效 → 混音总轨", "stages": _subset(FULL, VOICE_KEEP)},
    "gen": {
        "title": "生成台 · 分镜到放映",
        "note": "模型配置 → 关键帧 → 图生视频 → 归条剪辑调色字幕 → 预告 → 放映",
        "stages": _subset(FULL, GEN_KEEP, {
            "n-exp": {"timeline": "n-grd:timeline"},
            "n-trl": {"timeline": "n-grd:timeline"},
        }),
    },
}


def keys() -> list[str]:
    return list(PRESETS)


def graph_dict(key: str) -> dict[str, Any]:
    """把预设展开成画布图数据（与前端保存的图同构）。"""
    row = PRESETS.get(key)
    if row is None:
        raise KeyError(f"未知预设：{key}")
    stages: list[Stage] = row["stages"]
    nodes = [
        {
            "id": stage.id,
            "type": stage.type,
            "title": "",
            "pos": [stage.col * X_STEP, stage.row * Y_STEP],
            "params": dict(stage.params),
        }
        for stage in stages
    ]
    edges: list[dict[str, str]] = []
    serial = 0
    for stage in stages:
        for port, src in stage.feeds.items():
            rows = src if isinstance(src, list) else [src]
            for item in rows:
                src_id, _, src_port = item.partition(":")
                serial += 1
                edges.append({"id": f"e{serial}", "src": src_id, "src_port": src_port, "dst": stage.id, "dst_port": port})
    return {"nodes": nodes, "edges": edges}


def catalogue() -> list[dict[str, Any]]:
    return [
        {
            "key": key,
            "title": row["title"],
            "note": row["note"],
            "node_count": len(row["stages"]),
            "edge_count": sum(len(v if isinstance(v, list) else [v]) for stage in row["stages"] for v in stage.feeds.values()),
        }
        for key, row in PRESETS.items()
    ]


def preset_info(key: str) -> dict[str, Any]:
    row = PRESETS.get(key)
    if row is None:
        raise KeyError(f"未知预设：{key}")
    info = next(item for item in catalogue() if item["key"] == key)
    info["graph"] = graph_dict(key)
    return info


__all__ = ["Stage", "FULL", "PRESETS", "keys", "graph_dict", "catalogue", "preset_info", "X_STEP", "Y_STEP"]
