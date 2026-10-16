"""开发 · 制片部门岗位：选题策划、制片人、版权评估、场地经理。"""

from __future__ import annotations

from typing import Any

from ...knowledge.copyright import copyright_checklist, detect_commentary
from ...knowledge.industry import channel_rule, industry_profile, seo_pattern
from ...knowledge.video import script_skeleton, video_spec
from .. import craft, media
from ..spec import NodeError, Param, Port, node
from . import common

#: 场地口径：命中关键词 → (许可名, 报批提前天数, 邻避/现场限制)
_LOCATION_KINDS: list[tuple[tuple[str, ...], str, int, str]] = [
    (("街", "路", "广场", "公园", "天台", "门口", "外", "market", "street"), "公共区域占道许可", 5, "限时占用，需围挡与引导员"),
    (("地铁", "车站", "机场", "商圈", "商场", "店铺", "门店"), "运营方拍摄许可", 7, "只能错峰进场，客流高峰禁拍"),
    (("家", "住宅", "公寓", "小区", "楼", "卧室", "客厅"), "物业与邻里报备", 3, "22:00 后禁大功率与喊话"),
    (("办公室", "公司", "工位", "车间", "实验室"), "企业内部许可", 2, "保密区需遮挡，工牌与门禁登记"),
    (("厨", "餐", "咖啡", "吧", "食"), "营业时段错开 + 卫生报备", 2, "明火区要灭火毯在场，食材留样"),
    (("河", "湖", "海", "水", "泳池", "雨", "桥"), "水域作业备案", 4, "湿滑区需防滑垫，水面镜头要有救生员"),
]

_DIRECTIONS: list[tuple[str, str, str]] = [
    ("冲突前置", "把最反常识的结果放在第一镜，再倒推过程", "适合信息密度高、需要 3 秒留人的短视频"),
    ("人物视角", "跟一个具体的人走完一天，用他的选择讲产品/主题", "适合纪实感、信任建设"),
    ("对比实验", "同条件两侧对照，让差异自己说话", "适合种草与功能证明"),
    ("悬念解谜", "先给线索再给答案，逐层揭开", "适合剧情向与品牌片"),
    ("群像切片", "三到四个人的碎片拼出同一主题", "适合情绪向与节日片"),
]


@node(
    "planner",
    title="选题策划",
    dept="dev",
    role="开发岗：从需求单里长出可拍的选题——一句话卖点、三种叙述方向与节拍表",
    inputs=[Port("brief", "项目需求", "brief"), Port("text", "补充灵感", "text", required=False, many=True)],
    outputs=[Port("outline", "选题大纲", "outline"), Port("text", "提案文本", "text")],
    params=[
        Param("direction", "叙述方向", "select", "auto", ["auto"] + [item[0] for item in _DIRECTIONS]),
        Param("count", "备选方向数", "number", 3, minimum=1, maximum=5, step=1),
        Param("industry", "行业口径", "text", "消费品", hint="决定痛点/场景/佐证素材库"),
        Param("use_llm", "接真模型润色", "toggle", False, hint="配置了 LLM 端点时，用模型改写 logline 与节拍"),
    ],
    icon="💡",
    tags=["开发", "选题"],
)
def planner(ctx: Any) -> dict[str, Any]:
    brief = common.norm_brief(ctx.in_("brief"), {})
    extra = " ".join(str(item) for item in ctx.many("text"))
    industry = industry_profile(str(ctx.params.get("industry") or "消费品"))
    spec = video_spec(str(brief.get("platform") or ""))
    rule = channel_rule(str(brief.get("platform") or ""))
    seo = seo_pattern(str(brief.get("platform") or ""))
    keywords = list(brief.get("keywords") or []) or [str(brief.get("genre") or "主题")]
    hook_word = keywords[0] if keywords else "主题"

    wanted = str(ctx.params.get("direction") or "auto")
    pool = [item for item in _DIRECTIONS if wanted == "auto" or item[0] == wanted]
    count = int(float(ctx.params.get("count") or 3))
    directions = [
        {
            "name": name,
            "idea": idea,
            "fit": fit,
            "pain": industry.pain_points[index % len(industry.pain_points)],
            "scene": industry.scenarios[index % len(industry.scenarios)],
            "proof": industry.proof_assets[index % len(industry.proof_assets)],
            "keywords": [mod.format(kw=hook_word) for mod in seo.modifiers[:3]],
        }
        for index, (name, idea, fit) in enumerate(pool[:count])
    ]

    total_seconds = int(brief.get("duration_s") or spec.duration_seconds)
    skeleton_rows = script_skeleton(str(brief.get("platform") or ""), duration_seconds=total_seconds)
    beats = [
        {
            "index": index,
            "name": str(row.get("role")),
            "intent": str(row.get("intent")),
            "start_s": float(row.get("start_second") or 0),
            "end_s": float(row.get("end_second") or 0),
            "beat": craft.beats_for(index, max(1, len(skeleton_rows))),
        }
        for index, row in enumerate(skeleton_rows)
    ]

    logline = (
        f"用{brief.get('type') or '短视频'}讲清一件事：{brief.get('synopsis') or f'{hook_word}到底值不值'}"
        f"——面向{brief.get('audience') or '泛人群'}，{total_seconds} 秒内给出"
        f"{beats[2]['name'] if len(beats) > 2 else '答案'}。"
    )
    outline = {
        "title": brief.get("title") or "未命名项目",
        "logline": logline,
        "hook": f"0-3 秒：{beats[0]['intent'] if beats else '先给结果'}",
        "directions": directions,
        "chosen": directions[0]["name"] if directions else "auto",
        "beats": beats,
        "themes": [f"{brief.get('genre')}｜{item}" for item in industry.motivations[:3]],
        "channel_notes": [rule.format, rule.title_style, rule.hashtag_policy],
        "simulated": not media.llm_ready(),
    }

    if bool(ctx.params.get("use_llm")):
        refined = media.ask_json(
            "planner",
            "你是资深选题策划，只输出 JSON。",
            f"需求：{brief}\n补充灵感：{extra}\n现有 logline：{outline['logline']}\n"
            "请输出 {logline, hook, directions:[{name, idea, fit}]}",
        )
        if refined:
            outline["logline"] = str(refined.get("logline") or outline["logline"])
            outline["hook"] = str(refined.get("hook") or outline["hook"])
            outline["llm_refined"] = True
            ctx.log("选题已由真实模型润色")

    proposal = "\n".join(
        [
            f"【{outline['title']}】{outline['logline']}",
            f"钩子：{outline['hook']}",
            *[f"· 方向{i + 1} {item['name']}：{item['idea']}（{item['fit']}）" for i, item in enumerate(directions)],
            "节拍：" + " → ".join(f"{item['name']}{item['end_s'] - item['start_s']:.0f}s" for item in beats),
        ]
    )
    ctx.log(f"选题大纲完成：{len(directions)} 个方向 / {len(beats)} 段节拍")
    return {"outline": outline, "text": proposal}


@node(
    "producer",
    title="制片人",
    dept="dev",
    role="把创意变成可执行的盘子：阶段排期、工种与人力、预算分配、风险与预案",
    inputs=[Port("brief", "项目需求", "brief"), Port("outline", "选题大纲", "outline", required=False)],
    outputs=[Port("plan", "制片计划", "plan"), Port("text", "制片说明", "text")],
    params=[
        Param("scale", "制作规格", "select", "标准", ["轻量", "标准", "精品"]),
        Param("shot_count", "预估镜头数", "number", 0, minimum=0, maximum=200, step=1, hint="0 = 按片长自动估算"),
        Param("days_limit", "交付周期", "number", 10, minimum=2, maximum=120, step=1, unit="天"),
        Param("contingency", "应急金比例", "range", 0.1, minimum=0, maximum=0.4, step=0.05),
    ],
    icon="🧾",
    tags=["制片", "预算"],
)
def producer(ctx: Any) -> dict[str, Any]:
    brief = common.norm_brief(ctx.in_("brief"), {})
    outline = ctx.in_("outline") or {}
    duration = float(brief.get("duration_s") or 45)
    spec = video_spec(str(brief.get("platform") or ""))
    shots = int(float(ctx.params.get("shot_count") or 0)) or max(spec.shot_count, int(round(duration / 3.2)))
    scale = str(ctx.params.get("scale") or "标准")
    scale_factor = {"轻量": 0.55, "标准": 1.0, "精品": 1.9}[scale]
    budget_total = float(brief.get("budget") or 0) or round(shots * 900 * scale_factor, 2)

    crew = craft.crew_plan(shots, duration)
    crew_cost = round(sum(float(row["cost"]) for row in crew) * scale_factor, 2)
    contingency = float(ctx.params.get("contingency") or 0.1)
    compute_cost = round(shots * (2.4 if scale == "精品" else 1.2) * 3.0, 2)
    budget = craft.budget_split(max(budget_total, crew_cost + compute_cost))
    for row in budget:
        if row["line"] == "生成算力":
            row["amount"] = compute_cost
        if row["line"] == "主创":
            row["amount"] = crew_cost
    total_planned = round(sum(float(row["amount"]) for row in budget), 2)

    phases = [
        {"name": "开发", "days": round(1.2 * scale_factor, 1), "owner": "策划 / 编剧", "deliverables": ["选题大纲", "文学剧本"]},
        {"name": "前期", "days": round(1.6 * scale_factor, 1), "owner": "导演 / 分镜 / 美术", "deliverables": ["导演阐述", "分镜表", "设定图"]},
        {"name": "生成 · 拍摄", "days": round(shots * 0.28 * scale_factor, 1), "owner": "生成车间 / 摄影灯光", "deliverables": ["关键帧", "镜头片段"]},
        {"name": "声音", "days": round(0.9 * scale_factor, 1), "owner": "配音 / 音效 / 混音", "deliverables": ["配音轨", "配乐", "混音成品"]},
        {"name": "后期", "days": round(shots * 0.2 * scale_factor, 1), "owner": "剪辑 / 调色 / 视效 / 字幕", "deliverables": ["时间轴", "调色版", "字幕版"]},
        {"name": "质检 · 发行", "days": round(0.8 * scale_factor, 1), "owner": "审片 / 合规 / 发行", "deliverables": ["审片报告", "合规报告", "多平台物料"]},
    ]
    total_days = round(sum(float(row["days"]) for row in phases), 1)
    days_limit = float(ctx.params.get("days_limit") or 10)

    risks = [
        {
            "level": "blocker" if total_days > days_limit else "info",
            "detail": f"排期 {total_days} 天 > 交付周期 {days_limit} 天" if total_days > days_limit else f"排期 {total_days} 天，周期内可交付",
            "mitigation": "压缩生成规格或并行开两条镜头线" if total_days > days_limit else "按阶段验收，无需返工缓冲",
        },
        {
            "level": "major",
            "detail": "生成算力按镜头计费，重跑成本随镜头数线性上升",
            "mitigation": f"预留应急金 {contingency:.0%}（{round(total_planned * contingency, 2)} 元），关键镜头先出小样再批量",
        },
        {
            "level": "major",
            "detail": f"{brief.get('platform')} 对「{outline.get('chosen', '所选方向')}」类表述有合规要求",
            "mitigation": "台词与字幕过一遍合规审核节点，绝对化用语提前替换",
        },
        {"level": "minor", "detail": "配音与画面时长不匹配会引发返工", "mitigation": "分镜阶段即按语速锁时长，剪辑用闪避而非改词"},
    ]
    plan = {
        "title": brief.get("title"),
        "scale": scale,
        "duration_s": duration,
        "aspect": brief.get("aspect") or spec.aspect_ratio,
        "estimated_shots": shots,
        "phases": phases,
        "crew": crew,
        "budget": budget,
        "total_budget": total_planned,
        "budget_given": budget_total,
        "contingency": round(total_planned * contingency, 2),
        "total_days": total_days,
        "days_limit": days_limit,
        "risks": risks,
    }
    text = (
        f"《{plan['title']}》{scale}规格：{shots} 个镜头 / {duration:.0f}s / {plan['aspect']}，"
        f"排期 {total_days} 天（周期 {days_limit} 天），预算 {total_planned:,.0f} 元"
        f"（含应急 {plan['contingency']:,.0f}）。主创人力 {crew_cost:,.0f}，生成算力 {compute_cost:,.0f}。"
    )
    ctx.log(text)
    return {"plan": plan, "text": text}


@node(
    "rights",
    title="版权 · IP 评估",
    dept="dev",
    role="法务前置：素材授权、音乐版权、二创解说风险与需要留档的授权清单",
    inputs=[Port("brief", "项目需求", "brief"), Port("text", "文本", "text", required=False, many=True), Port("screenplay", "剧本", "screenplay", required=False)],
    outputs=[Port("report", "评估报告", "report"), Port("text", "授权清单", "text")],
    params=[Param("source", "素材来源", "select", "混合", ["全自制", "混合", "搬运二创", "采购"]), Param("music_license", "音乐授权", "select", "未确认", ["已授权", "免版权库", "未确认"])],
    icon="⚖️",
    tags=["法务", "版权"],
)
def rights(ctx: Any) -> dict[str, Any]:
    brief = common.norm_brief(ctx.in_("brief"), {})
    text = "\n".join(str(item) for item in ctx.many("text"))
    screenplay = ctx.in_("screenplay") or {}
    if screenplay:
        text = "\n".join(
            [text, common.dialogue_text(screenplay), *[str(row.get("action") or "") for row in screenplay.get("scenes") or []]]
        )
    knowledge_brief = {
        "channel": brief.get("platform"),
        "industry": brief.get("genre"),
        "product": brief.get("brand"),
        "constraints": brief.get("constraints") or [],
        "deliverables": brief.get("deliverables") or [],
    }
    checklist = copyright_checklist(knowledge_brief, text)
    commentary, reasons = detect_commentary(knowledge_brief, text)

    issues: list[dict[str, Any]] = []
    for item in checklist.get("items") or []:
        issues.append(
            {
                "severity": str(item.get("severity") or "minor"),
                "category": "版权",
                "detail": f"{item.get('topic')}：{item.get('risk')}",
                "suggestion": str(item.get("suggestion") or ""),
            }
        )
    if str(ctx.params.get("music_license")) == "未确认":
        issues.append(
            {
                "severity": "blocker",
                "category": "音乐",
                "detail": "背景音乐授权未确认",
                "suggestion": "改用免版权曲库，或让配乐节点输出原创旋律并留档生成记录",
            }
        )
    elif str(ctx.params.get("music_license")) == "免版权库":
        issues.append(
            {
                "severity": "info",
                "category": "音乐",
                "detail": "使用免版权曲库",
                "suggestion": "把曲目链接与授权条款截图存入素材档案",
            }
        )
    if commentary and not checklist.get("applies"):
        issues.append(
            {
                "severity": "minor",
                "category": "二创",
                "detail": f"命中解说/二创特征：{'、'.join(reasons[:3])}",
                "suggestion": "控制引用比例（单段 ≤30 秒）、加入原创评论与二次创作结构，保留原片来源说明",
            }
        )
    source = str(ctx.params.get("source") or "混合")
    if source == "采购":
        issues.append(
            {
                "severity": "info",
                "category": "素材",
                "detail": "素材来自采购",
                "suggestion": "核对采购协议是否覆盖商用、二次修改与跨平台投放",
            }
        )
    elif source == "混合":
        issues.append(
            {
                "severity": "minor",
                "category": "素材",
                "detail": "自制与外部素材混用，边界易糊",
                "suggestion": "在分镜表逐镜标注素材来源，外部素材集中留档授权文件",
            }
        )

    weight = {"blocker": 25, "major": 8, "minor": 3, "info": 0}
    score = max(0, min(100, 100 - sum(weight.get(str(item["severity"]), 0) for item in issues)))
    verdict: str = "reject" if source == "搬运二创" else ("revise" if any(i["severity"] in {"blocker", "major"} for i in issues) else "pass")
    report = {
        "score": score,
        "verdict": verdict,  # type: ignore[assignment]
        "issues": issues,
        "needs_human_review": bool(checklist.get("needs_human_review")) or verdict != "pass",
        "summary": f"版权评估 {score} 分：{len(issues)} 项需处理" if issues else "版权评估通过：无风险项",
        "checklist": checklist,
        "commentary": commentary,
        "signals": reasons,
        "simulated": True,
    }
    checklist_lines = [
        "□ 参考图/素材来源与授权文件",
        "□ 音乐（原创或免版权凭证）",
        "□ 字体商用授权",
        "□ 出镜人物肖像授权",
        "□ 品牌商标使用许可",
        "□ 二创引用比例记录",
    ]
    ctx.log(report["summary"], level="warn" if issues else "info")
    return {"report": report, "text": "\n".join(checklist_lines)}


@node(
    "location_manager",
    title="场地经理",
    dept="dev",
    role="把场景表变成能拍的地方：逐场勘景，算许可与报批提前期、场租押金、发电与邻避时段、装卸进场工时，给出可拍/待批结论",
    inputs=[
        Port("locations", "场景表", "locations"),
        Port("shots", "分镜表", "shots", required=False),
        Port("plan", "制片计划", "plan", required=False),
    ],
    outputs=[
        Port("locations", "可拍场景表", "locations"),
        Port("plan", "场地台账", "plan"),
        Port("text", "勘景说明", "text"),
    ],
    params=[
        Param("day_rate", "日均场租", "number", 1800, minimum=0, maximum=20000, step=100, unit="元/天"),
        Param("shots_per_day", "单日产能", "number", 8, minimum=2, maximum=24, step=1, unit="镜/天", hint="勘景排天用：一日内能拍完的镜头数"),
        Param("permit_days", "许可提前期", "number", 5, minimum=0, maximum=30, step=1, unit="天", hint="需要盖章的场地，报批要占掉这么多天"),
        Param("overtime", "超时加价", "range", 0.15, minimum=0, maximum=0.5, step=0.05, hint="场地方对超过约定时段加收的比例"),
        Param("deposit", "押金比例", "range", 0.3, minimum=0, maximum=1.0, step=0.05),
        Param("access_min", "装卸进场", "number", 30, minimum=0, maximum=120, step=5, unit="分钟", hint="停车点到机位的单程搬运时间，会加进通告表的架设工时"),
        Param("rain_plan", "外景备雨", "toggle", True),
    ],
    icon="📍",
    tags=["场地", "许可", "预算"],
)
def location_manager(ctx: Any) -> dict[str, Any]:
    scouted_in = common.locations_of(ctx.in_("locations"))
    if not scouted_in:
        raise NodeError("场地经理需要场景表：连场景名都没有，勘景无从谈起")
    prod = ctx.in_("plan") if isinstance(ctx.in_("plan"), dict) else {}
    shots = common.shots_of(ctx.in_("shots"))
    day_rate = float(ctx.params.get("day_rate") or 1800)
    per_day = max(1, int(float(ctx.params.get("shots_per_day") or 8)))
    permit_lead = int(float(ctx.params.get("permit_days") or 5))
    overtime = float(ctx.params.get("overtime") or 0.15)
    deposit_ratio = float(ctx.params.get("deposit") or 0.3)
    access_min = float(ctx.params.get("access_min") or 30)
    rain_plan = bool(ctx.params.get("rain_plan"))

    by_location: dict[str, list[dict[str, Any]]] = {}
    for shot in shots:
        by_location.setdefault(str(shot.get("location") or ""), []).append(shot)

    pre_days = 0.0
    for phase in prod.get("phases") or []:
        if str(phase.get("name") or "") == "生成 · 拍摄":
            break
        pre_days += float(phase.get("days") or 0)
    shoot_line = next((float(row.get("amount") or 0) for row in (prod.get("budget") or []) if str(row.get("line") or "") == "拍摄 · 制作"), 0.0)

    rows: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    known_names: set[str] = set()
    for item in scouted_in:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        known_names.add(name)
        covered = by_location.get(name) or [shot for shot in shots if name in str(shot.get("location") or "")]
        int_ext = str(item.get("int_ext") or (covered[0].get("int_ext") if covered else "INT") or "INT").upper()
        times = sorted({str(shot.get("time_of_day") or "") for shot in covered if shot.get("time_of_day")}) or [str(item.get("time_of_day") or "day")]
        exterior = int_ext.startswith("EXT")
        night = any(value in {"night", "dusk"} for value in times)
        words = " ".join(
            [
                name,
                str(item.get("mood") or ""),
                " ".join(str(prop) for prop in (item.get("props") or [])),
                " ".join(str(shot.get("description") or "") for shot in covered[:6]),
            ]
        )
        kind = next((row for row in _LOCATION_KINDS if any(key in words for key in row[0])), None)
        need_permit = kind is not None
        lead = permit_lead if need_permit else 0
        late = need_permit and lead > pre_days

        days = round(max(0.5, (len(covered) or 1) / per_day), 1)
        fee = round(day_rate * days, 2)
        deposit = round(fee * deposit_ratio, 2)
        genset = exterior and night
        extras = round(day_rate * 0.35 * days, 2) if genset else 0.0
        if rain_plan and exterior:
            extras += round(day_rate * 0.2, 2)
        overrun = round(fee * overtime, 2) if days >= 1.0 else 0.0
        status = "待许可" if late else ("可拍" if covered else "本轮无镜头")
        if late:
            issues.append(
                {
                    "severity": "blocker",
                    "category": "许可",
                    "detail": f"{name} 要办「{kind[1]}」，报批需 {lead} 天，但距开机只剩 {pre_days:.1f} 天前期窗口",
                    "suggestion": "换成无需许可的自有场地，或让制片人把前期阶段拉长并同步第一副导演改通告",
                }
            )
        if need_permit and kind[3]:
            issues.append({"severity": "minor", "category": "现场", "detail": f"{name}：{kind[3]}", "suggestion": "把这条限制写进通告表，别让摄影组到现场才发现"})
        if night and exterior:
            issues.append(
                {"severity": "major", "category": "电力", "detail": f"{name} 夜外没有市电口径，需发电车（按 {days} 天估）", "suggestion": "提前订发电车与线缆，或改内景借光拍"}
            )
        rows.append(
            {
                **item,
                "name": name,
                "int_ext": int_ext,
                "time_of_day": "/".join(times),
                "shot_count": len(covered),
                "scenes": sorted({str(shot.get("scene_id") or "") for shot in covered if shot.get("scene_id")}),
                "screen_s": round(sum(float(shot.get("duration_s") or 0) for shot in covered), 2),
                "days": days,
                "fee": fee,
                "deposit": deposit,
                "extras": round(extras, 2),
                "overrun_fee": overrun,
                "cash": round(fee + deposit + extras, 2),
                "power": {"genset": genset, "kva": round(25 + 8 * len(covered), 1) if genset else 0, "note": "发电车 + 分线箱" if genset else "场地方配电，需确认可用容量"},
                "permit": {"kind": kind[1] if kind else "无需许可", "lead_days": lead, "window_days": round(pre_days, 1), "ok": not late},
                "noise": kind[3] if kind else "无特殊限制",
                "weather": ("备雨棚与改期缓冲，已计 0.2 天场租" if rain_plan and exterior else "内景，不受天气影响"),
                "access_min": round(access_min * 2, 1),
                "status": status,
            }
        )

    missed = sorted({str(shot.get("location") or "") for shot in shots if shot.get("location")} - known_names)
    if missed:
        issues.append(
            {"severity": "major", "category": "勘景", "detail": f"分镜里出现但场景表没有的场地：{'、'.join(missed[:6])}", "suggestion": "回文学策划补场景，或在分镜师处改场，别让现场临时找地方"}
        )
    spend = round(sum(float(row["fee"]) + float(row["extras"]) + float(row["overrun_fee"]) for row in rows), 2)
    cash = round(sum(float(row["cash"]) for row in rows), 2)
    if shoot_line and spend > shoot_line:
        issues.append(
            {
                "severity": "blocker" if spend > shoot_line * 1.3 else "major",
                "category": "预算",
                "detail": f"场地实测 {spend:,.0f} 元超「拍摄 · 制作」预算行 {shoot_line:,.0f} 元（{spend - shoot_line:+,.0f}）",
                "suggestion": "砍天数、并场，或让制片人把预算行提到实测口径",
            }
        )
    if not rows:
        issues.append({"severity": "blocker", "category": "数据", "detail": "场景表里一个可用场景名都没有", "suggestion": "确认文学策划的 locations 有 name 字段"})

    plan = {
        "dept": "dev",
        "role": "场地经理",
        "items": rows,
        "locations": len(rows),
        "shot_cover": sum(int(row["shot_count"]) for row in rows),
        "days_total": round(sum(float(row["days"]) for row in rows), 1),
        "fee_total": spend,
        "cash_total": cash,
        "deposit_total": round(sum(float(row["deposit"]) for row in rows), 2),
        "budget_line": shoot_line,
        "over": round(spend - shoot_line, 2) if shoot_line else 0.0,
        "need_permit": len([row for row in rows if not row["permit"]["ok"] or row["permit"]["lead_days"]]),
        "blocked": [str(row["name"]) for row in rows if row["status"] == "待许可"],
        "no_cover": [str(row["name"]) for row in rows if row["status"] == "本轮无镜头"],
        "genset": [str(row["name"]) for row in rows if row["power"]["genset"]],
        "access_min_total": round(sum(float(row["access_min"]) for row in rows), 1),
        "pre_window_days": round(pre_days, 1),
        "issues": issues,
        "risks": [str(item["detail"]) for item in issues if item["severity"] in {"blocker", "major"}][:6] or ["全部场地可拍，手续与预算都在盘子里"],
        "simulated": True,
    }
    text = "\n".join(
        [
            f"勘景：{len(rows)} 个场地 / {plan['days_total']} 个拍摄日，覆盖 {plan['shot_cover']} 镜｜场租+杂费 {spend:,.0f} 元，押金占用 {plan['deposit_total']:,.0f} 元（现金口径 {cash:,.0f} 元）",
            (f"预算：对「拍摄 · 制作」行 {shoot_line:,.0f} 元，差 {plan['over']:+,.0f} 元" if shoot_line else "预算：上游没给制片计划，场租只算绝对值不做超支判断"),
            f"手续：前期窗口 {pre_days:.1f} 天，需要许可的 {plan['need_permit']} 处，卡住的 {'、'.join(plan['blocked']) or '无'}",
            f"进场：装卸单程 {access_min:.0f} 分钟＝每单元 {plan['access_min_total'] / max(1, len(rows)):.0f} 分钟工时，已写进场景表供第一副导演排通告",
            ("夜外需发电车：" + "、".join(plan["genset"])) if plan["genset"] else "电力：无需发电车",
            *[f"· {row['name']}〔{row['int_ext']} {row['time_of_day']}〕{row['status']}｜{row['shot_count']} 镜 {row['days']} 天｜场租 {row['fee']:,.0f} 押金 {row['deposit']:,.0f}｜{row['permit']['kind']}（{row['permit']['lead_days']} 天）" for row in rows[:12]],
            (f"⚠ 分镜用到但没勘的场地：{'、'.join(missed[:6])}" if missed else "分镜里的场地都在勘景清单内"),
            "结论：可拍口径只看许可天数与预算行实测，不看场地方口头承诺。",
        ]
    )
    ctx.log(f"场地：{len(rows)} 处 / {plan['days_total']} 天，卡手续 {len(plan['blocked'])} 处，超支 {plan['over']:+,.0f} 元")
    return {"locations": rows, "plan": plan, "text": text}
