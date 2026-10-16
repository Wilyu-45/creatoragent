"""编剧部门岗位：文学策划、编剧、剧本医生、译制 · 本地化。

这些岗位在没有真模型时必须**真的能写出东西**：本模块用「节拍 → 场景 → 台词」
的规则剧本库产出可用文本（结构是行业口径，内容来自项目需求），并在产物上标注
``engine``。配了真模型时，编剧与剧本医生会把规则稿当草稿交给模型改写，
产物标注 ``engine="llm"``——两条通道都不假装自己是对方。
"""

from __future__ import annotations

import re
from typing import Any

from ...knowledge.industry import industry_profile
from ...knowledge.language import language_profile, localization_directive, normalize_language
from ...knowledge.video import script_skeleton
from .. import craft, media
from ..spec import Param, Port, node
from . import common

#: 节拍 → 台词写法（编剧的句式库：同一节拍给 2-3 种可替换句式，按 seed 轮换）
_DIALOGUE_BY_BEAT: dict[str, list[str]] = {
    "hook": [
        "先说结论：{pain}这件事，{kw}真的能解决一半。",
        "别急着划走，{product}要是连{pain}都搞不定，我也不会拍这条。",
        "我试过三种办法，只有{kw}让我{scene}的时候不再{pain}。",
    ],
    "setup": [
        "{audience}的日常其实就是这样：{scene}，然后被{pain}卡住。",
        "第一次遇到{pain}的时候，我第一反应是{scene}里根本没时间处理。",
        "说白了，问题不在你，在{kw}这件事一直没被认真解决。",
    ],
    "develop": [
        "做法很简单：{proof}，先把{pain}按下去。",
        "{product}的关键就一步——{scene}之前先把{kw}对齐。",
        "我把流程拆成三段，第一段专门对付{pain}。",
    ],
    "tension": [
        "但这里有个坑：便宜的那几款，{proof}根本拿不出来。",
        "我不信宣传，我只看{proof}这种能核对的东西。",
        "第一次用我踩了雷，{kw}用错了方向比不用还糟。",
    ],
    "payoff": [
        "连着用了两周，{scene}这件事确实顺了，{pain}少了大半。",
        "{proof}摆在这儿，我不需要形容词。",
        "结论跟开头一样：{kw}值，但只值在它解决{pain}的那部分。",
    ],
    "cta": [
        "如果{scene}也让你头疼，先从{kw}这一件改起。",
        "链接放在这儿，{product}先按自己的{scene}挑规格，别乱买。",
        "有问题评论区说，我把{proof}的核对清单一起给你。",
    ],
    "ending": [
        "就这样，{audience}不必把生活过成一场场{pain}。",
        "{product}不是万能，但它让{scene}这件事省了心。",
    ],
    "chapter": [
        "接下来这一段，讲{kw}最容易翻车的地方。",
        "第二部分：{scene}。",
    ],
}

#: 节拍 → 动作行写法（描述「画面里正在发生什么」，给分镜与生成岗位取材）
_ACTION_BY_BEAT: dict[str, list[str]] = {
    "hook": [
        "{who}直视镜头举起{prop}，画面在{where}定住一拍",
        "{where}的局部特写，{who}的手停在半空，{prop}被光勾出轮廓",
    ],
    "setup": [
        "{who}走进{where}，先把{prop}放下，动作里带着赶时间的劲儿",
        "{where}全景，{who}一边整理{prop}一边环顾四周",
    ],
    "develop": [
        "{who}在{where}演示{prop}的用法，手指逐项点过关键部位",
        "{prop}被拆开摆成一排，{who}依次对比，镜头跟着手走",
    ],
    "tension": [
        "{who}皱眉盯着{prop}，把它翻过来看背面，{where}的光偏到一侧",
        "两份{prop}并排放在{where}，{who}的手在两者间犹豫",
    ],
    "payoff": [
        "{who}在{where}顺利完成动作，把{prop}收好，肩膀松下来",
        "{where}的画面变亮，{who}对着{prop}点头",
    ],
    "cta": [
        "{who}把{prop}推向镜头，{where}退成虚化背景",
        "{where}空镜，{prop}留在画面中心，字幕压上来",
    ],
    "ending": ["{where}渐暗，{who}离场，只留下{prop}", "{who}回头看一眼{where}，画面收在{prop}上"],
    "chapter": ["{where}的招牌或物件先入画，作为段落起点", "{who}在{where}站定，段落从这里开始"],
}

_PROPS = ["样品", "包装", "说明书", "工具", "杯子", "充电器", "笔记本", "对比卡"]


def _slots(brief: dict[str, Any], industry: Any, location: str, who: str, seed_text: str) -> dict[str, str]:
    rng = craft.rng_for(seed_text, location, who)
    keywords = brief.get("keywords") or [brief.get("genre") or "主题"]
    return {
        "kw": str(rng.choice([str(item) for item in keywords]) or "主题"),
        "product": str(brief.get("product") or brief.get("brand") or "这款产品"),
        "pain": str(rng.choice(industry.pain_points or ["信息不对称"])),
        "scene": str(rng.choice(industry.scenarios or ["日常使用"])),
        "proof": str(rng.choice(industry.proof_assets or ["可核对的参数"])),
        "audience": str(brief.get("audience") or "目标用户"),
        "where": location,
        "who": who,
        "prop": str(rng.choice(_PROPS)),
    }


def _fill(template: str, slots: dict[str, str]) -> str:
    try:
        return template.format(**slots)
    except KeyError:  # 模板里出现了未知占位符时不炸整条线
        return template


def _fit(text: str, seconds: float) -> str:
    """把台词删到剩余口播时长以内。

    编剧是按节拍时长写词的：写完指望后期加速，等于把返工丢给配音和剪辑。
    所以这里按逗号级切分逐句累加，塞不下的整句直接舍掉，不留半句话。
    """
    if seconds <= 0.6:
        return ""
    if craft.speech_seconds(text) <= seconds:
        return text
    kept: list[str] = []
    for piece in [item.strip() for item in re.split(r"[，,、；;：:]", text) if item.strip()]:
        candidate = "，".join(kept + [piece])
        if craft.speech_seconds(candidate) > seconds:
            break
        kept.append(piece)
    return "，".join(kept)


def _beats_of(outline: dict[str, Any], platform: str, total_seconds: float) -> list[dict[str, Any]]:
    """优先用策划给的节拍表；没有就按渠道骨架铺（保证编剧能独立跑）。"""
    rows = [item for item in (outline.get("beats") or []) if isinstance(item, dict)]
    if rows:
        scale = total_seconds / max(1.0, float(rows[-1].get("end_s") or total_seconds))
        out: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            start = float(row.get("start_s") or 0) * scale
            end = float(row.get("end_s") or start) * scale
            out.append(
                {
                    "index": index,
                    "name": str(row.get("name") or "段落"),
                    "intent": str(row.get("intent") or ""),
                    "beat": str(row.get("beat") or craft.beats_for(index, len(rows))),
                    "start_s": round(start, 2),
                    "end_s": round(end, 2),
                }
            )
        return out
    skeleton = script_skeleton(platform, duration_seconds=int(round(total_seconds)))
    return [
        {
            "index": index,
            "name": str(row.get("role") or "段落"),
            "intent": str(row.get("intent") or ""),
            "beat": craft.beats_for(index, len(skeleton)),
            "start_s": float(row.get("start_second") or 0),
            "end_s": float(row.get("end_second") or 0),
        }
        for index, row in enumerate(skeleton)
    ]


@node(
    "literary",
    title="文学策划",
    dept="writing",
    role="故事编辑：把选题落成世界观、人物小传与场景清单，规定人物弧光与主线冲突",
    inputs=[
        Port("brief", "项目需求", "brief"),
        Port("outline", "选题大纲", "outline", required=False),
        Port("text", "补充材料", "text", required=False, many=True),
    ],
    outputs=[
        Port("outline", "故事大纲", "outline"),
        Port("characters", "人物小传", "characters"),
        Port("locations", "场景清单", "locations"),
        Port("text", "策划文案", "text"),
    ],
    params=[
        Param("theme", "主题句", "text", "", hint="留空则由类型与关键词推导"),
        Param("character_count", "主要人物数", "number", 2, minimum=1, maximum=5, step=1),
        Param("location_count", "场景数", "number", 2, minimum=1, maximum=6, step=1),
        Param("arc", "人物弧光", "select", "从怀疑到信任", ["从怀疑到信任", "从妥协到反抗", "从回避到面对", "从独行到协作", "从将就到讲究"]),
    ],
    icon="📖",
    tags=["文学", "人物"],
)
def literary(ctx: Any) -> dict[str, Any]:
    brief = common.norm_brief(ctx.in_("brief"), {})
    outline_in = ctx.in_("outline") or {}
    extras = " ".join(str(item) for item in ctx.many("text"))
    industry = industry_profile(str(brief.get("genre") or "消费品"))
    total = float(brief.get("duration_s") or 45)
    beats = _beats_of(outline_in if outline_in.get("beats") else {}, str(brief.get("platform") or ""), total)
    theme = str(ctx.params.get("theme") or "").strip() or f"{brief['keywords'][0] if brief.get('keywords') else brief['genre']}：{ctx.params.get('arc') or '从怀疑到信任'}"
    conflict = f"{industry.pain_points[0] if industry.pain_points else '信息不对称'}"

    n_characters = int(float(ctx.params.get("character_count") or 2))
    roles = ["protagonist", "lead", "mentor", "friend", "narrator"]
    names = ["林夏", "陈默", "老周", "小满", "阿哲", "苏晴"]
    characters: list[dict[str, Any]] = []
    for index in range(n_characters):
        name = names[index % len(names)]
        want = (ctx.params.get("arc") or "从怀疑到信任").split("到")
        characters.append(
            {
                "id": common.slug(name, f"ch{index}"),
                "name": name,
                "role_type": roles[index % len(roles)] if index < 3 else "support",
                "age": [26, 34, 45, 19, 30][index % 5],
                "want": want[1] if len(want) > 1 else "信任",
                "need": want[0] if want else "怀疑",
                "flaw": conflict,
                "arc": str(ctx.params.get("arc") or "从怀疑到信任"),
                "voice_type": craft.ROLE_VOICE.get(roles[index % len(roles)], "narrator_female"),
                "traits": [str(brief.get("tone") or "真实"), industry.motivations[index % len(industry.motivations)]],
                "scenes": [],
            }
        )

    n_locations = int(float(ctx.params.get("location_count") or 2))
    pool = [str(item) for item in industry.scenarios] or ["日常场景"]
    locations: list[dict[str, Any]] = []
    for index in range(n_locations):
        name = pool[index % len(pool)]
        mood = common.mood_of(beats[min(index, len(beats) - 1)]["beat"], str(brief.get("tone")))
        locations.append(
            {
                "id": common.slug(name, f"loc{index}"),
                "name": name,
                "int_ext": "INT" if index % 2 == 0 else "EXT",
                "time_of_day": ["day", "dusk", "night", "dawn"][index % 4],
                "mood": mood,
                "props": [_PROPS[index % len(_PROPS)], _PROPS[(index + 3) % len(_PROPS)]],
                "note": industry.proof_assets[index % len(industry.proof_assets)] if industry.proof_assets else "",
                "palette_hint": str(brief.get("tone") or ""),
            }
        )

    acts = [
        {
            "name": label,
            "intent": intent,
            "low": low,
            "high": high,
            "beats": [row["name"] for row in beats if low <= (row["start_s"] + row["end_s"]) / 2 / max(1.0, total) < high],
        }
        for label, low, high, intent in [
            ("第一幕 · 建置", 0.0, 0.26, f"把{conflict}摆到台面上，让主角不得不处理"),
            ("第二幕 · 对抗", 0.26, 0.74, "尝试、踩坑、对比，冲突升级"),
            ("第三幕 · 解决", 0.74, 1.0, "给出可核对的结果与行动指令"),
        ]
    ]
    outline = {
        "title": outline_in.get("title") or brief.get("title") or "未命名项目",
        "logline": outline_in.get("logline") or f"{characters[0]['name']}在{locations[0]['name']}撞上{conflict}，只能用{brief.get('product') or '这条路'}走出{characters[0]['arc']}。",
        "theme": theme,
        "conflict": conflict,
        "arc": str(ctx.params.get("arc") or "从怀疑到信任"),
        "acts": acts,
        "beats": beats,
        "hook": outline_in.get("hook") or f"0-3 秒：{beats[0]['name']}",
        "directions": outline_in.get("directions") or [],
        "notes": [localization_directive(str(brief.get("platform") or ""), str(brief.get("language") or "zh"))],
        "extras": extras[:400],
        "simulated": not media.llm_ready(),
    }
    text = "\n".join(
        [
            f"主题：{theme}",
            f"主线冲突：{conflict}",
            f"钩子：{outline['hook']}",
            "人物：" + "；".join(f"{item['name']}（{item['role_type']}，{item['arc']}，缺陷：{item['flaw']}）" for item in characters),
            "场景：" + "；".join(f"{item['name']}（{item['int_ext']}/{item['time_of_day']}/{item['mood']}）" for item in locations),
            "幕次：" + " → ".join(f"{item['name']}[{('、'.join(item['beats']) or '待填')}]" for item in acts),
        ]
    )
    ctx.log(f"文学策划完成：{len(characters)} 个主要人物 / {len(locations)} 个场景 / {len(acts)} 幕")
    return {"outline": outline, "characters": characters, "locations": locations, "text": text}


@node(
    "screenwriter",
    title="编剧",
    dept="writing",
    role="按大纲写文学剧本：分场、动作行、对白，逐场锁时长，产出一线可直接拍的本子",
    inputs=[
        Port("brief", "项目需求", "brief"),
        Port("outline", "故事大纲", "outline"),
        Port("characters", "人物小传", "characters", required=False),
        Port("locations", "场景清单", "locations", required=False),
        Port("text", "补充台词", "text", required=False, many=True),
    ],
    outputs=[Port("screenplay", "剧本", "screenplay"), Port("text", "剧本梗概", "text")],
    params=[
        Param("scene_per_beat", "每节拍场次数", "number", 1, minimum=1, maximum=3, step=1),
        Param("dialogue_density", "台词密度", "select", "适中", ["极少", "适中", "密集"]),
        Param("voiceover", "旁白口播", "toggle", True, hint="短视频常用：无对白场次补一条 VO 台词"),
        Param("lines_per_scene", "每场最多句数", "number", 3, minimum=1, maximum=6, step=1),
        Param("use_llm", "接真模型润色台词", "toggle", False, hint="配置 LLM 端点时把规则稿交给模型改写"),
    ],
    icon="✍️",
    tags=["剧本", "对白"],
)
def screenwriter(ctx: Any) -> dict[str, Any]:
    brief = common.norm_brief(ctx.in_("brief"), {})
    outline = ctx.in_("outline") or {}
    characters = common.characters_of(ctx.in_("characters")) or common.pick_characters({"scenes": []})
    locations = common.locations_of(ctx.in_("locations"))
    industry = industry_profile(str(brief.get("genre") or "消费品"))
    platform = str(brief.get("platform") or "")
    total = float(brief.get("duration_s") or 45)
    beats = _beats_of(outline, platform, total)
    if not characters:
        characters = [{"name": "林夏", "role_type": "protagonist", "arc": "从怀疑到信任"}]
    if not locations:
        locations = [{"name": str(beats[0]["name"]), "int_ext": "INT", "time_of_day": "day", "mood": "neutral", "props": [_PROPS[0]]}]

    per_beat = int(float(ctx.params.get("scene_per_beat") or 1))
    density = str(ctx.params.get("dialogue_density") or "适中")
    max_lines = int(float(ctx.params.get("lines_per_scene") or 3))
    lines_by_beat = {"极少": 1, "适中": 2, "密集": 3}[density]
    speak_counts = [item.get("name") for item in characters if item.get("role_type") != "narrator"] or [characters[0]["name"]]

    scenes: list[dict[str, Any]] = []
    for beat_index, beat in enumerate(beats):
        for sub in range(max(1, per_beat)):
            location = locations[(beat_index + sub) % len(locations)]
            speaker = speak_counts[(beat_index + sub) % len(speak_counts)]
            narrator = next((item for item in characters if item.get("role_type") == "narrator"), None)
            slots = _slots(brief, industry, str(location.get("name") or "场景"), str(speaker), f"{outline.get('title')}|{beat['beat']}|{sub}")
            action_pool = _ACTION_BY_BEAT.get(beat["beat"], _ACTION_BY_BEAT["chapter"])
            action = _fill(action_pool[(beat_index + sub) % len(action_pool)], slots)
            if beat.get("intent"):
                action = f"{action}（本场任务：{beat['intent']}）"
            dialogue_pool = _DIALOGUE_BY_BEAT.get(beat["beat"], _DIALOGUE_BY_BEAT["chapter"])
            use = min(max_lines, len(dialogue_pool), lines_by_beat + (1 if density == "密集" else 0))
            span = max(1.4, (float(beat["end_s"]) - float(beat["start_s"])) / max(1, per_beat))
            budget = max(0.8, span * craft.SPEECH_SHARE - craft.HEAD_GAP_S)  # 口播预算：入话留白与表演呼吸先扣掉
            dialogue: list[dict[str, str]] = []
            spent = 0.0
            for line_index in range(use):
                who = speaker if line_index % 2 == 0 or narrator is None else str(narrator.get("name") or speaker)
                room = budget - spent
                pool = [
                    _fill(dialogue_pool[(beat_index + line_index + offset) % len(dialogue_pool)], slots)
                    for offset in range(len(dialogue_pool))
                ]
                # 先在句式库里挑一句能整句说完的；挑不到才动笔删句
                line = next((item for item in pool if craft.speech_seconds(item) <= room), "") or _fit(pool[0], room)
                if not line:
                    break
                dialogue.append({"character": who, "line": line})
                spent += craft.speech_seconds(line)
            if bool(ctx.params.get("voiceover")) and not any(item["character"] == "旁白" for item in dialogue):
                vo = _fit(_fill(dialogue_pool[len(dialogue_pool) - 1], slots), max(0.0, budget - spent))
                if vo:
                    dialogue.insert(0, {"character": "旁白", "line": vo})
                    spent += craft.speech_seconds(vo)
            # 场次时长：有台词按口播口径给足呼吸，纯动作场保留最低 1.8s 的表演时间
            duration = round(max(1.4, min(span, max(1.8, (craft.HEAD_GAP_S + spent) / craft.SPEECH_SHARE + 0.2))), 2)
            position = (float(beat["start_s"]) + span / 2) / max(1.0, total)
            scenes.append(
                {
                    "id": f"sc{len(scenes) + 1}",
                    "index": len(scenes),
                    "title": f"场 {len(scenes) + 1}｜{location.get('name')}",
                    "location": str(location.get("name") or "场景"),
                    "int_ext": str(location.get("int_ext") or "INT"),
                    "time_of_day": str(location.get("time_of_day") or "day"),
                    "beat": beat["beat"],
                    "beat_name": beat["name"],
                    "act": craft.act_of(position),
                    "mood": common.mood_of(beat["beat"], str(brief.get("tone"))),
                    "characters": [str(speaker)] + ([str(narrator["name"])] if narrator else []),
                    "props": list(location.get("props") or [])[:2],
                    "action": action,
                    "dialogue": dialogue,
                    "duration_s": duration,
                }
            )

    screenplay = {
        "title": outline.get("title") or brief.get("title") or "未命名项目",
        "logline": outline.get("logline") or "",
        "theme": outline.get("theme") or "",
        "conflict": outline.get("conflict") or "",
        "language": normalize_language(str(brief.get("language") or "zh")),
        "scenes": scenes,
        "acts": outline.get("acts") or [],
        "total_s": round(sum(float(item["duration_s"]) for item in scenes), 2),
        "target_s": total,
        "source": "screenwriter",
        "engine": "rules",
        "stats": {
            "scene_count": len(scenes),
            "dialogue_lines": sum(len(item["dialogue"]) for item in scenes),
            "speakers": sorted({line["character"] for item in scenes for line in item["dialogue"]}),
        },
        "simulated": True,
    }

    if bool(ctx.params.get("use_llm")):
        refined = media.ask_json(
            "screenwriter",
            "你是职业短片编剧，只输出 JSON。台词要口语、具体、可核对，禁止绝对化用语。",
            f"项目：{brief}\n大纲 logline：{outline.get('logline')}\n规则稿：{craft.flatten(screenplay, 8)}\n"
            "输出 {logline, scenes:[{index, action, dialogue:[{character,line}]}]}，只给改写后的动作行与台词。",
        )
        if refined:
            applied = 0
            for row in refined.get("scenes") or []:
                if not isinstance(row, dict):
                    continue
                target = next((item for item in scenes if item["index"] == int(float(row.get("index") or 0))), None)
                if not target:
                    continue
                if row.get("action"):
                    target["action"] = str(row["action"])
                lines = [item for item in (row.get("dialogue") or []) if isinstance(item, dict) and item.get("line")]
                if lines:
                    target["dialogue"] = [{"character": str(item.get("character") or "旁白"), "line": str(item["line"])} for item in lines]
                target["duration_s"] = round(
                    max(1.5, sum(craft.speech_seconds(str(item["line"])) for item in target["dialogue"])), 2
                )
                applied += 1
            screenplay["logline"] = str(refined.get("logline") or screenplay["logline"])
            screenplay["engine"] = "llm" if applied else "rules"
            screenplay["simulated"] = not applied
            screenplay["total_s"] = round(sum(float(item["duration_s"]) for item in scenes), 2)
            ctx.log(f"编剧：{applied} 场台词由真模型改写")

    ctx.log(
        f"剧本完成：{len(scenes)} 场 / {screenplay['stats']['dialogue_lines']} 句台词 / {screenplay['total_s']}s"
        f"（目标 {total:.0f}s）",
        scenes=len(scenes),
    )
    return {"screenplay": screenplay, "text": f"{screenplay['logline']}\n\n" + "\n".join(f"· {item['title']}：{item['action'][:40]}" for item in scenes)}


@node(
    "script_doctor",
    title="剧本医生",
    dept="writing",
    role="结构诊断与改写：查三幕配比、节奏超载、台词超长与断裂的冲突线，给出可拍的修订稿",
    inputs=[Port("screenplay", "剧本", "screenplay"), Port("brief", "项目需求", "brief", required=False)],
    outputs=[Port("screenplay", "修订剧本", "screenplay"), Port("report", "诊断报告", "report"), Port("text", "修改说明", "text")],
    params=[
        Param("max_line_chars", "单句台词上限", "number", 22, minimum=10, maximum=40, step=1, unit="字"),
        Param("trim_overrun", "超时自动压缩", "toggle", True),
        Param("add_tension", "补冲突", "toggle", True, hint="中段没有对抗场面时插入一场质疑/踩坑"),
        Param("rewrite_narration", "旁白改口播", "toggle", True),
    ],
    icon="🩺",
    tags=["诊断", "修订"],
)
def script_doctor(ctx: Any) -> dict[str, Any]:
    screenplay = common.require_screenplay(ctx.in_("screenplay"), "剧本医生")
    brief = common.norm_brief(ctx.in_("brief"), {})
    scenes = [dict(item) for item in screenplay.get("scenes") or []]
    issues: list[dict[str, Any]] = []
    fixes: list[str] = []
    total = float(screenplay.get("total_s") or sum(float(item.get("duration_s") or 0) for item in scenes))
    target = float(screenplay.get("target_s") or brief.get("duration_s") or total)
    max_chars = int(float(ctx.params.get("max_line_chars") or 22))

    act_load: dict[str, float] = {}
    for scene in scenes:
        position = float(scene.get("index") or 0) / max(1, len(scenes))
        key = str(scene.get("act") or craft.act_of(position))
        act_load[key] = act_load.get(key, 0.0) + float(scene.get("duration_s") or 0)
    for label, low, high in craft.ACT_RATIOS:
        got = act_load.get(label, 0.0) / max(1.0, total)
        if got < low * 0.6:
            issues.append({"severity": "major", "category": "结构", "detail": f"{label}只占 {got:.0%}，低于 {low:.0%} 的口径", "suggestion": "补齐该幕场次或把相邻段落前移"})

    long_lines = 0
    for scene in scenes:
        scene["dialogue"] = [dict(item) for item in scene.get("dialogue") or []]
        for row in scene["dialogue"]:
            text = str(row.get("line") or "")
            if len(text) > max_chars:
                long_lines += 1
                parts = craft.split_dialogue(text)
                row["line"] = parts[0] if parts else text[:max_chars]
                for extra in parts[1:]:
                    scene["dialogue"].append({"character": row.get("character") or "旁白", "line": extra})
    if long_lines:
        fixes.append(f"拆出 {long_lines} 句超长台词，保证单屏字幕 ≤{max_chars} 字")

    silent = [scene for scene in scenes if not scene.get("dialogue")]
    if silent and bool(ctx.params.get("rewrite_narration")):
        for scene in silent:
            sentences = craft.sentences(str(scene.get("action") or ""))
            line = sentences[0] if sentences else str(scene.get("location") or "")
            scene["dialogue"] = [{"character": "旁白", "line": line}]
            scene["duration_s"] = round(max(1.5, float(scene.get("duration_s") or 2.0), craft.speech_seconds(line)), 2)
        fixes.append(f"为 {len(silent)} 个无对白场次补了口播旁白")

    beats = [str(scene.get("beat") or "") for scene in scenes]
    if bool(ctx.params.get("add_tension")) and "tension" not in beats and len(scenes) >= 3:
        anchor = scenes[len(scenes) // 2]
        inserted = {
            "id": "sc-tension",
            "index": anchor["index"],
            "title": f"场 {anchor['index'] + 1}（新增）｜{anchor.get('location')}",
            "location": str(anchor.get("location") or "场景"),
            "int_ext": str(anchor.get("int_ext") or "INT"),
            "time_of_day": str(anchor.get("time_of_day") or "day"),
            "beat": "tension",
            "beat_name": "对抗",
            "act": craft.act_of(0.5),
            "mood": "tense",
            "characters": list(anchor.get("characters") or [])[:1],
            "props": list(anchor.get("props") or [])[:1],
            "action": f"{anchor.get('characters', ['主角'])[0]}翻查{anchor.get('props', ['样品'])[0]}的背面与参数，发现与宣传不符，眉头拧起来",
            "dialogue": [{"character": str((anchor.get("characters") or ["主角"])[0]), "line": "这里对不上，宣传写的那条我不买。"}],
            "duration_s": 2.6,
            "added_by": "script_doctor",
        }
        scenes.insert(len(scenes) // 2 + 1, inserted)
        fixes.append("中段缺对抗：新增一场质疑戏，冲突线才连得上")

    new_total = round(sum(float(scene.get("duration_s") or 0) for scene in scenes), 2)
    if new_total > target * 1.08:
        factor = target / max(0.1, new_total)
        if bool(ctx.params.get("trim_overrun")):
            for scene in scenes:
                scene["duration_s"] = round(max(1.2, float(scene.get("duration_s") or 0) * factor), 2)
            trimmed = round(sum(float(scene["duration_s"]) for scene in scenes), 2)
            issues.append(
                {
                    "severity": "minor",
                    "category": "节奏",
                    "detail": f"本稿 {new_total}s 超出目标 {target:.0f}s",
                    "suggestion": f"已按 {factor:.2f} 倍等比压缩场次时长（现 {trimmed}s），台词未删",
                }
            )
            new_total = trimmed
        else:
            issues.append(
                {"severity": "major", "category": "节奏", "detail": f"本稿 {new_total}s 超出目标 {target:.0f}s", "suggestion": "删掉重复信息的场次，或打开「超时自动压缩」"}
            )
    elif new_total < target * 0.6:
        issues.append({"severity": "minor", "category": "节奏", "detail": f"本稿 {new_total}s 明显短于目标 {target:.0f}s", "suggestion": "在 develop/payoff 段补细节场次"})

    for scene in scenes:
        if len(str(scene.get("action") or "")) > 90:
            scene["action"] = str(scene["action"])[:88] + "…"
    for index, scene in enumerate(scenes):
        scene["index"] = index

    score = max(0, min(100, 100 - sum({"blocker": 30, "major": 12, "minor": 5, "info": 0}[str(item["severity"])] for item in issues)))
    report = {
        "score": score,
        "verdict": "pass" if not issues else ("revise" if score >= 55 else "reject"),  # type: ignore[assignment]
        "issues": issues,
        "summary": f"剧本诊断：{len(issues)} 个结构问题，已自动修复 {len(fixes)} 处" if fixes or issues else "剧本诊断：结构与节奏均在口径内",
        "fixes": fixes,
        "simulated": True,
    }
    revised = {**screenplay, "scenes": scenes, "total_s": new_total, "engine": "script_doctor", "diagnosis": report}
    text = "\n".join([f"修订后 {len(scenes)} 场 / {new_total}s（目标 {target:.0f}s）", *[f"· {item}" for item in fixes], *[f"! [{item['severity']}] {item['detail']} → {item['suggestion']}" for item in issues]])
    ctx.log(report["summary"], level="warn" if issues else "info")
    return {"screenplay": revised, "report": report, "text": text}


@node(
    "localizer",
    title="译制 · 本地化",
    dept="writing",
    role="多语言本地化：按目标语言的原生口径重写标题与字幕长度，落实广告标注与计量惯例",
    inputs=[Port("screenplay", "剧本", "screenplay"), Port("subtitles", "字幕轨", "subtitles", required=False)],
    outputs=[Port("screenplay", "本地化剧本", "screenplay"), Port("text", "本地化说明", "text")],
    params=[
        Param("language", "目标语言", "select", "en", ["zh", "en", "ja", "ko", "es"]),
        Param("platform", "目标渠道", "text", "", hint="留空则用该语言的主流渠道口径"),
        Param("mode", "处理方式", "select", "原生改写", ["原生改写", "直译", "仅规格适配"], hint="原生改写需要真模型；无模型时如实回落为规格适配"),
        Param("subtitle_cpl", "字幕每行字符", "number", 42, minimum=16, maximum=60, step=2),
        Param("dub_rate", "口播语速", "range", 1.0, minimum=0.7, maximum=1.4, step=0.05),
    ],
    icon="🌐",
    tags=["本地化", "多语言"],
)
def localizer(ctx: Any) -> dict[str, Any]:
    screenplay = common.require_screenplay(ctx.in_("screenplay"), "本地化")
    code = normalize_language(str(ctx.params.get("language") or "en"))
    profile = language_profile(code)
    platform = str(ctx.params.get("platform") or "") or (profile.native_channels[0] if profile.native_channels else "")
    mode = str(ctx.params.get("mode") or "原生改写")
    cpl = int(float(ctx.params.get("subtitle_cpl") or 42))
    rate = float(ctx.params.get("dub_rate") or 1.0)

    scenes = [dict(item) for item in screenplay.get("scenes") or []]
    applied_llm = False
    if mode == "原生改写" and media.llm_ready():
        payload = {
            "scenes": [
                {"index": scene.get("index"), "dialogue": [str(row.get("line") or "") for row in scene.get("dialogue") or []]}
                for scene in scenes
            ]
        }
        refined = media.ask_json(
            "localizer",
            f"你是母语为 {profile.label} 的本地化编剧。不是翻译，而是用 {platform} 的原生表达重写台词。",
            f"口径：{localization_directive(platform, code)}\n语气惯例：{profile.tone_conventions}\n红线：{profile.compliance_notes}\n"
            f"每行 ≤{cpl} 字符。输入：{craft.flatten(payload, 20)}\n输出 {{scenes:[{{index, lines:[str]}}]}}",
        )
        if refined:
            for row in refined.get("scenes") or []:
                if not isinstance(row, dict):
                    continue
                target = next((item for item in scenes if item.get("index") == int(float(row.get("index") or -1))), None)
                lines = [str(item).strip() for item in (row.get("lines") or []) if str(item).strip()]
                if not target or not lines:
                    continue
                speakers = [str(item.get("character") or "旁白") for item in (target.get("dialogue") or [])]
                target["dialogue"] = [
                    {"character": speakers[j % len(speakers)] if speakers else "旁白", "line": line}
                    for j, line in enumerate(lines)
                ]
            applied_llm = True

    warnings: list[str] = []
    for scene in scenes:
        for row in scene.get("dialogue") or []:
            text = str(row.get("line") or "")
            if profile.title_unit == "word":
                size = len(text.split())
                budget = max(6, int(cpl / 2.2))
                if size > budget:
                    warnings.append(f"场 {scene.get('index')}：{size} 词 > {budget} 词口播上限")
            if len(text) > cpl:
                warnings.append(f"场 {scene.get('index')}：单行 {len(text)} 字符 > {cpl}")
            row["line"] = text
        if scene.get("dialogue"):
            speech = sum(craft.speech_seconds(str(row.get("line") or ""), rate) for row in scene["dialogue"])
            scene["duration_s"] = round(max(1.2, speech), 2)

    result = {
        **screenplay,
        "scenes": scenes,
        "language": code,
        "platform_target": platform,
        "localization": {
            "language": profile.label,
            "mode": mode,
            "engine": "llm" if applied_llm else "spec_only",
            "subtitle_cpl": cpl,
            "dub_rate": rate,
            "title_unit": profile.title_unit,
            "title_limit": profile.title_limit,
            "tone_conventions": list(profile.tone_conventions),
            "compliance_notes": list(profile.compliance_notes),
            "locale_notes": list(profile.locale_notes),
            "ad_disclosure": (profile.ad_disclosure_text if profile.ad_disclosure_required else "该语言区未强制标注"),
            "lexicon_coverage": profile.lexicon_coverage,
            "directive": localization_directive(platform, code),
        },
        "total_s": round(sum(float(item.get("duration_s") or 0) for item in scenes), 2),
    }
    text = "\n".join(
        [
            f"目标语言：{profile.label}（{code}）｜渠道口径：{platform or '通用'}",
            f"处理：{mode}" + ("（真模型原生改写）" if applied_llm else "（未配置真模型：只做时长/字幕/标注的规格适配，台词保持原文）"),
            f"字幕每行 ≤{cpl} 字符，口播语速 {rate:.2f}x，本稿 {result['total_s']}s",
            f"标题长度：≤{profile.title_limit} {profile.title_unit}",
            f"广告标注：{result['localization']['ad_disclosure']}",
            "语气惯例：" + "；".join(profile.tone_conventions),
            "合规红线：" + "；".join(profile.compliance_notes),
            "度量与日期：" + "；".join(profile.locale_notes),
            *([f"! {item}" for item in warnings[:6]] if warnings else ["字幕长度全部达标"]),
        ]
    )
    if warnings:
        ctx.log(f"本地化：{len(warnings)} 处字幕/口播超长", level="warn")
    else:
        ctx.log("本地化规格适配完成")
    return {"screenplay": result, "text": text}
