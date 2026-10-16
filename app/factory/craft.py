"""影视制作工厂 —— 岗位专业知识表。

这里是「每个岗位会做的事」里的**行业口径**部分：景别与焦段的对应、布光色温与
光比、情绪到调色、节奏到时长、预算到工种配置。执行器（``nodes/*.py``）只调用
这里的表与函数，保证同一岗位在任何项目上的判断口径一致，且不依赖外部服务。

时长估算全部走「字数 / 语速」这类可复现规则；随机化统一用带 seed 的
``random.Random``，同参数重跑结果逐字节一致（与前端 ``src/vg/timeline.ts`` 的
mulberry32 同一哲学：可复现才是能交付的产线）。
"""

from __future__ import annotations

import random
import re
from typing import Any

# ------------------------------------------------------------------ #
# 景别 / 机位 / 运镜（分镜师与摄影指导的共同语言）                       #
# ------------------------------------------------------------------ #

#: 景别：代码 → 中文名、画面占比（离线分镜草图据此画人物大小）、常用焦段、建议时长
SHOT_SIZES: dict[str, dict[str, Any]] = {
    "extreme_wide": {"label": "大远景", "figure": 0.10, "lens": 18, "seconds": (3.0, 6.0), "use": "交代地理与环境"},
    "wide": {"label": "远景", "figure": 0.20, "lens": 24, "seconds": (2.5, 5.0), "use": "建立空间关系"},
    "full": {"label": "全景", "figure": 0.42, "lens": 35, "seconds": (2.5, 5.5), "use": "展示完整动作"},
    "medium": {"label": "中景", "figure": 0.55, "lens": 50, "seconds": (2.0, 4.5), "use": "对话与人物关系"},
    "medium_close": {"label": "中近景", "figure": 0.68, "lens": 70, "seconds": (2.0, 4.0), "use": "人物情绪起势"},
    "close": {"label": "近景", "figure": 0.82, "lens": 85, "seconds": (1.8, 3.5), "use": "表情与台词重点"},
    "extreme_close": {"label": "特写", "figure": 0.96, "lens": 100, "seconds": (1.2, 3.0), "use": "关键道具与情绪峰值"},
    "macro": {"label": "大特写", "figure": 1.0, "lens": 105, "seconds": (1.0, 2.5), "use": "细节钩子"},
}

CAMERA_ANGLES: dict[str, str] = {
    "eye": "平视",
    "high": "俯拍",
    "low": "仰拍",
    "overhead": "顶拍",
    "ots": "过肩",
    "pov": "主观",
    "dutch": "荷兰角",
}

CAMERA_MOVEMENTS: dict[str, dict[str, Any]] = {
    "static": {"label": "固定", "speed": 0.0, "prompt": "locked-off tripod shot, no camera movement"},
    "push_in": {"label": "推镜", "speed": 0.12, "prompt": "slow dolly in, tightening framing"},
    "pull_out": {"label": "拉镜", "speed": 0.12, "prompt": "slow dolly out, revealing the space"},
    "pan": {"label": "横摇", "speed": 0.10, "prompt": "horizontal pan across the scene"},
    "tilt": {"label": "纵摇", "speed": 0.10, "prompt": "vertical tilt revealing height"},
    "track": {"label": "移镜", "speed": 0.16, "prompt": "lateral tracking shot on dolly"},
    "follow": {"label": "跟镜", "speed": 0.18, "prompt": "steady follow shot behind the subject"},
    "crane": {"label": "升降", "speed": 0.14, "prompt": "crane shot rising vertically"},
    "handheld": {"label": "手持", "speed": 0.22, "prompt": "handheld shot with natural micro-shake"},
    "whip": {"label": "甩镜", "speed": 0.55, "prompt": "fast whip pan with motion blur"},
    "orbit": {"label": "环绕", "speed": 0.20, "prompt": "360 orbit around the subject"},
}

#: 摄影机位 / 承载方式（摄影指导据此写方案）
CAMERA_RIGS: dict[str, str] = {
    "tripod": "三脚架",
    "slider": "滑轨",
    "dolly": "轨道推车",
    "crane": "摇臂",
    "drone": "无人机",
    "steadicam": "稳定器",
    "handheld": "手持",
    "gimbal": "手机云台",
}

#: 转场：剪辑师按节奏与信息密度挑选
TRANSITIONS: dict[str, dict[str, Any]] = {
    "cut": {"label": "硬切", "cost": 0.0},
    "crossfade": {"label": "交叉溶解", "cost": 0.6},
    "push": {"label": "推移", "cost": 0.55},
    "wipe": {"label": "擦除", "cost": 0.55},
    "zoom": {"label": "缩放转场", "cost": 0.5},
    "blur": {"label": "模糊溶解", "cost": 0.6},
    "glitch": {"label": "故障", "cost": 0.45},
    "match_cut": {"label": "匹配剪辑", "cost": 0.4},
}

#: 情绪 → 调色（filter 代码与前端 src/vg/timeline.ts 的 FilterId 逐字一致）
MOOD_GRADE: dict[str, dict[str, Any]] = {
    "neutral": {"filter": "none", "label": "标准", "temp": 5600, "contrast": 1.00, "sat": 1.00},
    "warm": {"filter": "warm", "label": "暖阳", "temp": 4200, "contrast": 1.03, "sat": 1.12},
    "cool": {"filter": "cool", "label": "冷调", "temp": 7200, "contrast": 1.02, "sat": 0.92},
    "noir": {"filter": "mono", "label": "黑白", "temp": 5600, "contrast": 1.12, "sat": 0.0},
    "vintage": {"filter": "vintage", "label": "胶片", "temp": 4600, "contrast": 0.94, "sat": 0.85},
    "punch": {"filter": "punch", "label": "高对比", "temp": 6000, "contrast": 1.22, "sat": 1.25},
    "soft": {"filter": "soft", "label": "柔光", "temp": 5200, "contrast": 0.96, "sat": 0.95},
    "dream": {"filter": "soft", "label": "梦幻", "temp": 4800, "contrast": 0.92, "sat": 1.05},
    "tense": {"filter": "cool", "label": "紧张", "temp": 8200, "contrast": 1.18, "sat": 0.86},
}

#: 场景时段 → 布光（灯光师：色温 K、主辅光比、方向）
LIGHTING_BY_TIME: dict[str, dict[str, Any]] = {
    "dawn": {"temp_k": 3200, "ratio": "2:1", "direction": "低位侧光", "gels": "CTO 暖色纸", "note": "低角度晨光 + 薄雾"},
    "day": {"temp_k": 5600, "ratio": "3:1", "direction": "45° 侧顺光", "gels": "无 / 反光板补光", "note": "主光模拟太阳，逆光勾边"},
    "dusk": {"temp_k": 3800, "ratio": "4:1", "direction": "逆侧光", "gels": "CTO + 柔光屏", "note": "黄金时刻，长影"},
    "night": {"temp_k": 6500, "ratio": "6:1", "direction": "顶侧冷光", "gels": "CTB 蓝纸", "note": "路灯/月光为主，高反差"},
    "indoors": {"temp_k": 4300, "ratio": "2.5:1", "direction": "窗侧自然光", "gels": "柔光箱 + 黑旗控光", "note": "以窗为源，层次靠黑旗"},
    "studio": {"temp_k": 5600, "ratio": "2:1", "direction": "三点布光", "gels": "标准罩 + 蜂窝", "note": "主/辅/轮廓，背景分离"},
}

#: 音乐：情绪 → 速度 / 调式 / 和弦进行（半音偏移，供离线合成器直接使用）
MUSIC_MOODS: dict[str, dict[str, Any]] = {
    "uplift": {"bpm": 112, "root": 57, "scale": "major", "progression": [0, 5, 7, 0], "label": "积极种草"},
    "warm": {"bpm": 92, "root": 60, "scale": "major", "progression": [0, -4, 3, 5], "label": "温暖叙事"},
    "tense": {"bpm": 128, "root": 45, "scale": "minor", "progression": [0, 3, 7, 5], "label": "紧张推进"},
    "chill": {"bpm": 80, "root": 53, "scale": "dorian", "progression": [0, 2, 7, 2], "label": "松弛氛围"},
    "epic": {"bpm": 100, "root": 48, "scale": "minor", "progression": [0, 5, 8, 7], "label": "宏大揭示"},
    "playful": {"bpm": 124, "root": 62, "scale": "major", "progression": [0, 7, 3, 5], "label": "俏皮快节奏"},
    "melancholy": {"bpm": 72, "root": 50, "scale": "minor", "progression": [0, -2, 5, 3], "label": "低落回忆"},
}

#: 音效库：类别 → 合成参数（离线可发真的声音，不需要素材文件）
SFX_LIBRARY: dict[str, dict[str, Any]] = {
    "whoosh": {"kind": "noise", "dur": 0.45, "sweep": (1800, 320), "envelope": "in_out", "label": "转场呼啸"},
    "impact": {"kind": "boom", "dur": 0.55, "freq": 92, "envelope": "out", "label": "重低音撞击"},
    "click": {"kind": "tone", "dur": 0.06, "freq": 2100, "envelope": "out", "label": "点击"},
    "pop": {"kind": "tone", "dur": 0.10, "freq": 660, "envelope": "out", "label": "弹出"},
    "chime": {"kind": "arp", "dur": 1.10, "freq": 880, "envelope": "out", "label": "提示铃"},
    "rain": {"kind": "noise", "dur": 2.00, "sweep": (5200, 4200), "envelope": "flat", "label": "雨声"},
    "footstep": {"kind": "boom", "dur": 0.16, "freq": 150, "envelope": "out", "label": "脚步"},
    "paper": {"kind": "noise", "dur": 0.30, "sweep": (3600, 2400), "envelope": "out", "label": "纸张/翻页"},
    "riser": {"kind": "noise", "dur": 1.60, "sweep": (220, 3200), "envelope": "in", "label": "渐强噪声"},
    "sub_drop": {"kind": "boom", "dur": 0.90, "freq": 58, "envelope": "in_out", "label": "低频下坠"},
}

#: 音色预设（配音导演的选角口径）
VOICE_PRESETS: dict[str, dict[str, Any]] = {
    "warm_female": {"label": "温柔女声", "pitch": 1.28, "rate": 1.00, "timbre": "soft"},
    "bright_female": {"label": "明快女声", "pitch": 1.42, "rate": 1.10, "timbre": "bright"},
    "narrator_female": {"label": "知性女声（旁白）", "pitch": 1.12, "rate": 0.96, "timbre": "mellow"},
    "deep_male": {"label": "低沉男声", "pitch": 0.78, "rate": 0.94, "timbre": "deep"},
    "energetic_male": {"label": "活力男声", "pitch": 0.95, "rate": 1.12, "timbre": "bright"},
    "reporter_male": {"label": "播报男声", "pitch": 0.88, "rate": 1.02, "timbre": "firm"},
    "child": {"label": "童声", "pitch": 1.62, "rate": 1.05, "timbre": "bright"},
}

#: 角色类型 → 默认音色
ROLE_VOICE: dict[str, str] = {
    "protagonist": "warm_female",
    "lead": "bright_female",
    "narrator": "narrator_female",
    "mentor": "deep_male",
    "villain": "deep_male",
    "friend": "energetic_male",
    "child": "child",
    "brand": "reporter_male",
}

#: 叙事节奏骨架：三幕在各幕的位置占比
ACT_RATIOS: list[tuple[str, float, float]] = [
    ("第一幕 · 建置", 0.00, 0.26),
    ("第二幕 · 对抗", 0.26, 0.74),
    ("第三幕 · 解决", 0.74, 1.00),
]

#: 制片预算分配口径（占总额比例，含各工种）
BUDGET_LINES: list[tuple[str, float, str]] = [
    ("开发 · 剧本", 0.06, "策划/编剧/版权"),
    ("主创", 0.14, "导演/摄影/美术"),
    ("演员 · 配音", 0.12, "选角/配音/数字人"),
    ("拍摄 · 制作", 0.22, "灯光/机械/场务"),
    ("生成算力", 0.14, "图像/视频模型调用"),
    ("后期", 0.18, "剪辑/调色/视效/字幕"),
    ("声音", 0.08, "配乐/音效/混音"),
    ("宣发", 0.06, "封面/投放/物料"),
]

#: 排期口径：每岗位每工作日的产出量（用于制片人的天数估算）
CREW_DAILY_RATE: dict[str, float] = {
    "编剧": 6000.0,
    "分镜师": 4500.0,
    "导演": 12000.0,
    "美术指导": 8000.0,
    "摄影指导": 10000.0,
    "灯光师": 5000.0,
    "配音演员": 3000.0,
    "剪辑师": 4000.0,
    "调色师": 3500.0,
    "视效": 6000.0,
    "混音": 2500.0,
}

#: 审片评分维度（审片员 rubric）
REVIEW_DIMENSIONS: list[str] = [
    "structure",
    "shot_language",
    "pacing",
    "sound",
    "subtitle",
    "compliance",
]

REVIEW_LABEL: dict[str, str] = {
    "structure": "叙事结构",
    "shot_language": "镜头语言",
    "pacing": "节奏时长",
    "sound": "声画配合",
    "subtitle": "字幕规范",
    "compliance": "合规安全",
}

# ------------------------------------------------------------------ #
# 规则函数                                                             #
# ------------------------------------------------------------------ #

_SENT_SPLIT = re.compile(r"(?<=[。！？；!?;])|(?<=[，,、])\s*(?=$)")
_LINES = re.compile(r"[。！？；!?;\n]+")


def rng_for(*parts: Any) -> random.Random:
    """由字符串派生稳定随机源（同输入 → 同输出）。"""
    text = "|".join(str(part) for part in parts)
    seed = 0
    for ch in text:
        seed = (seed * 131 + ord(ch)) & 0xFFFFFFFF
    return random.Random(seed)


def chinese_duration(text: str, chars_per_second: float = 4.6) -> float:
    """按语速估台词秒数（与 ``DubbingSettings.chars_per_second`` 同一口径）。"""
    clean = re.sub(r"\s+", "", text or "")
    if not clean:
        return 0.0
    return max(0.6, round(len(clean) / max(1.0, chars_per_second), 2))


#: 一句口播除正文之外必须占的气口与收尾秒数（``audio.voice`` 用它铺尾，全片按同一口径排时长）
VOICE_TAIL_S = 0.4

#: 台词可以占满镜头的比例上限，以及开口前的入话留白。
#: 编剧删词、分镜定时长、配音落位三处共用这一口径，否则各自留余量，最后必然对不齐。
SPEECH_SHARE = 0.85
HEAD_GAP_S = 0.15


def speech_seconds(text: str, rate: float = 1.0, chars_per_second: float = 4.6) -> float:
    """这句台词真说出来要几秒：正文字数 + 每屏一个句间停顿 + 收尾气口。

    编剧删词、分镜锁时长、配音落位都用这一个模型，否则三处各估一遍，
    后期一定对不上（字数少算 0.4s，十五镜累积就是十几秒的返工）。
    """
    clean = re.sub(r"\s+", "", text or "")
    if not clean:
        return 0.0
    cps = max(1.0, chars_per_second) * max(0.6, min(2.0, float(rate) or 1.0))
    clauses = max(1, len(split_dialogue(clean)))
    return round((len(clean) + clauses) / cps + VOICE_TAIL_S, 2)


def split_dialogue(text: str) -> list[str]:
    """把一段台词切成适合上屏的字幕句。"""
    parts = [part.strip(" ，,、") for part in _LINES.split(text or "") if part and part.strip()]
    out: list[str] = []
    for part in parts:
        if len(part) <= 22:
            out.append(part)
            continue
        # 长句按逗号再切，并合并到每屏不超过 20 字
        buf = ""
        for chunk in re.split(r"[，,、；;：:]", part):
            chunk = chunk.strip()
            if not chunk:
                continue
            if len(buf) + len(chunk) + 1 <= 20:
                buf = f"{buf}{chunk}" if not buf else f"{buf}，{chunk}"
            else:
                if buf:
                    out.append(buf)
                buf = chunk
        if buf:
            out.append(buf)
    return out


def sentences(text: str) -> list[str]:
    parts = [item.strip() for item in _LINES.split(text or "") if item and item.strip()]
    return parts


def shot_size_for(index: int, total: int, beat: str, has_dialogue: bool, seed_text: str) -> str:
    """分镜师景别选择规则：开场交代 → 中段对话 → 结尾情绪峰值。"""
    rng = rng_for(seed_text, index, beat)
    position = index / max(1, total - 1)
    if index == 0:
        return rng.choice(["extreme_wide", "wide", "full"])
    if position > 0.85:
        return rng.choice(["close", "extreme_close", "macro"])
    if has_dialogue:
        return rng.choice(["medium", "medium_close", "close", "medium_close"])
    if beat in {"hook", "opening"}:
        return rng.choice(["wide", "full", "medium"])
    if beat in {"payoff", "cta", "ending"}:
        return rng.choice(["close", "extreme_close"])
    return rng.choice(["full", "medium", "medium_close", "wide"])


def movement_for(size: str, beat: str, energy: float, seed_text: str) -> str:
    """运镜规则：景别越大越可用位移镜头，情绪高点用推镜。"""
    rng = rng_for(seed_text, "move", size, beat)
    if energy >= 0.8:
        return rng.choice(["whip", "follow", "handheld", "orbit"])
    if size in {"extreme_wide", "wide"}:
        return rng.choice(["crane", "track", "push_in", "static"])
    if size in {"close", "extreme_close", "macro"}:
        return rng.choice(["push_in", "static", "handheld"])
    return rng.choice(["static", "pan", "tilt", "track"])


def angle_for(beat: str, seed_text: str, index: int) -> str:
    rng = rng_for(seed_text, "angle", beat, index)
    if beat in {"payoff", "cta"}:
        return rng.choice(["eye", "low", "pov"])
    if beat in {"tension", "conflict"}:
        return rng.choice(["high", "dutch", "ots"])
    return rng.choice(["eye", "eye", "high", "ots"])


def grade_for(mood: str) -> dict[str, Any]:
    return MOOD_GRADE.get(mood, MOOD_GRADE["neutral"])


def music_for(mood: str) -> dict[str, Any]:
    return MUSIC_MOODS.get(mood, MUSIC_MOODS["uplift"])


def transition_for(prev_size: str, size: str, beat: str, energy: float, seed_text: str) -> str:
    """转场规则：同景别相邻多用硬切，时空跳跃用溶解，高能段落用故障/推移。"""
    rng = rng_for(seed_text, prev_size, size, beat)
    if energy >= 0.85:
        return rng.choice(["glitch", "push", "zoom", "cut"])
    if prev_size == size:
        return "cut"
    if size in {"extreme_wide", "wide"} and prev_size in {"close", "extreme_close", "macro"}:
        return "crossfade"
    if beat in {"opening", "chapter"}:
        return rng.choice(["wipe", "zoom", "match_cut"])
    return rng.choice(["cut", "cut", "crossfade", "push"])


def lighting_for(time_of_day: str, mood: str) -> dict[str, Any]:
    base = dict(LIGHTING_BY_TIME.get(time_of_day, LIGHTING_BY_TIME["day"]))
    if mood in {"tense", "noir"}:
        base["ratio"] = "8:1"
        base["note"] = f"{base['note']}；提高反差压暗背景"
    if mood in {"warm", "dream"}:
        base["temp_k"] = int(base["temp_k"] * 0.92)
        base["note"] = f"{base['note']}；加柔光屏保肤色"
    base["time_of_day"] = time_of_day
    base["mood"] = mood
    return base


def beats_for(index: int, total: int) -> str:
    """按位置给镜头贴叙事节拍标签，供节奏与转场规则使用。"""
    if total <= 1:
        return "hook"
    position = index / (total - 1)
    if position < 0.08:
        return "hook"
    if position < 0.26:
        return "setup"
    if position < 0.5:
        return "develop"
    if position < 0.68:
        return "tension"
    if position < 0.86:
        return "payoff"
    return "cta"


def act_of(position: float) -> str:
    for label, low, high in ACT_RATIOS:
        if low <= position < high:
            return label
    return ACT_RATIOS[-1][0]


def budget_split(total: float) -> list[dict[str, Any]]:
    rows = [
        {"line": line, "ratio": ratio, "amount": round(total * ratio, 2), "note": note}
        for line, ratio, note in BUDGET_LINES
    ]
    return rows


def crew_plan(shot_count: int, duration_s: float) -> list[dict[str, Any]]:
    """按镜头数与片长推各岗位工作量（人日），给出可执行的排班依据。"""
    scale = max(1.0, duration_s / 60.0)
    plan = [
        {"role": "编剧", "days": round(1.0 + shot_count * 0.06, 1)},
        {"role": "分镜师", "days": round(0.5 + shot_count * 0.05, 1)},
        {"role": "导演", "days": round(0.8 + scale * 1.2, 1)},
        {"role": "美术指导", "days": round(0.6 + shot_count * 0.03, 1)},
        {"role": "摄影指导", "days": round(0.5 + shot_count * 0.04, 1)},
        {"role": "灯光师", "days": round(0.4 + shot_count * 0.03, 1)},
        {"role": "配音演员", "days": round(0.3 + scale * 0.5, 1)},
        {"role": "剪辑师", "days": round(0.8 + shot_count * 0.05, 1)},
        {"role": "调色师", "days": round(0.3 + scale * 0.3, 1)},
        {"role": "混音", "days": round(0.3 + scale * 0.3, 1)},
    ]
    for row in plan:
        rate = CREW_DAILY_RATE.get(row["role"], 4000.0)
        row["cost"] = round(row["days"] * rate, 2)
    return plan


def flatten(value: Any, limit: int = 6) -> list[str]:
    """把上游结构化产出压成提示词/展示用的短句列表。"""
    out: list[str] = []
    if isinstance(value, str):
        out.extend(sentences(value)[:limit])
    elif isinstance(value, dict):
        for key in ("logline", "title", "summary", "text", "theme", "hook"):
            raw = value.get(key)
            if isinstance(raw, str) and raw:
                out.append(raw)
        for item in (value.get("scenes") or value.get("shots") or value.get("items") or [])[:limit]:
            if isinstance(item, dict):
                out.append(str(item.get("description") or item.get("action") or item.get("text") or ""))
            elif isinstance(item, str):
                out.append(item)
    elif isinstance(value, list):
        for item in value[:limit]:
            if isinstance(item, dict):
                out.append(str(item.get("name") or item.get("text") or item.get("line") or ""))
            elif isinstance(item, str):
                out.append(item)
    return [item for item in out if item]
