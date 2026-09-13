"""环境与依赖自检 + 离线 Mock 引擎全链路演练。

用法::

    python scripts/doctor.py

作用：
 1. 校验 Python 版本与关键依赖是否可导入
 2. 依次执行 A1→A11 的离线生成器，验证知识层与产出契约
 3. 打印各环节的关键信号（质量分、阻断项、裁决），便于定位回归
 4. 额外覆盖动态渠道（抖音）分支：分镜脚本与标题超限压缩
 5. 跑**真实 A11 智能体本体**验证记忆库 RAG 闭环（写入 → 跨任务召回）
 6. 校验混合检索（本地 hashing embedding）的确定性、归一化与相似度排序
 7. 校验发布排期的时段解析 / 跨天顺延 / 到期判断（自动投递的时间基础）
 8. 校验 ``CREATOR_API_TOKENS`` 的 Token → 租户解析
 9. 校验记忆库的**租户隔离**（写入归属 / 召回不越租户 / 同租户内幂等）
10. 校验断点续跑用的是真的 SQLite 检查点，而不是静默退回的内存实现
11. 校验 LLM-as-a-Judge 离线评估器：六维评分可复现、对合规风险敏感
12. 校验黄金数据集的**回归判定器**真的会判回归（含返工增加、门禁强度下降、
    全量缺失与部分运行两种语义），避免出现「永远显示通过」的假门禁
13. 校验追踪层：跑一个真实任务，确认 span 树**不是平铺列表**（根 span 少于总数）、
    llm span 正确嵌套在智能体之下、属性齐全，且能按 OTel 形状导出
14. 校验 **OTLP 导出链路**（用内存导出器，无需 collector）：本地与导出的
    trace_id/span_id 完全一致、父子关系与智能体归属保留、默认不加载 OTel

注意：本脚本默认写入**项目内**的 ``.doctor-data/`` 临时目录，避免自检污染开发环境的
``data/``；如需指定，可显式设置 ``CREATOR_DATA_DIR``。刻意不用系统 temp 目录 ——
Windows 沙箱下 ``sqlite3`` 在 ``%TEMP%`` 里会打不开数据库文件，检查点会静默退化，
自检就覆盖不到断点续跑那条路径了（第 10 项正是为了守住这一点）。

第 5 项同样是刻意设计的：生成器只覆盖「JSON 契约」，而 ``AgentRunContext`` 的字段漂移
（曾出现过缺失 ``artifacts`` 导致 A11 崩溃）只有真跑 ``run()`` 才会暴露。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.util import make_temp_dir  # noqa: E402 - 需先补好 sys.path


def isolate_data_dir() -> str:
    """自检默认使用**项目内**的临时数据目录，必须在导入 ``app.*`` 之前调用。

    用 ``make_temp_dir`` 而不是 ``tempfile.mkdtemp``：后者建出的 ``0o700`` 目录
    在受限沙箱下会拒绝写入（子进程 ``PermissionError``），于是记忆库落盘静默失败、
    SQLite 检查点退回内存实现 —— 自检跑的根本不是生产那条路径。
    详见 ``app/core/util.make_temp_dir`` 的说明。
    """
    current = os.environ.get("CREATOR_DATA_DIR")
    if current:
        return current
    created = make_temp_dir("run-", base=ROOT / ".doctor-data")
    os.environ["CREATOR_DATA_DIR"] = str(created)
    return str(created)


def check_python() -> None:
    print(f"Python      : {sys.version.split()[0]}  ({sys.executable})")
    if sys.version_info < (3, 12):
        print("  ! 建议使用 Python 3.12")


def check_deps() -> None:
    import fastapi
    import httpx
    import langgraph
    import pydantic
    import uvicorn

    print(f"fastapi     : {fastapi.__version__}")
    print(f"pydantic    : {pydantic.VERSION}")
    print(f"langgraph   : {getattr(langgraph, '__version__', 'unknown')}")
    print(f"uvicorn     : {uvicorn.__version__}")
    print(f"httpx       : {httpx.__version__}")

    from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: F401
    from langgraph.graph import END, START, StateGraph  # noqa: F401
    from langgraph.types import Command, interrupt  # noqa: F401

    print("langgraph   : StateGraph / interrupt / Command / SqliteSaver 均可用")


def exercise_mock() -> bool:
    """按真实流水线顺序喂数据，验证所有离线生成器与门禁信号。"""
    from app.llm.mock import GENERATORS

    brief = {
        "brand": "晨野",
        "product": "冷萃即饮咖啡",
        "objective": "转化",
        "audience": "早八通勤的都市白领",
        "channel": "小红书",
        "tone": "轻松、真实、有种草感",
        "industry": "食品饮料",
        "keywords": ["0 糖", "冷萃"],
        "constraints": ["不承诺减肥功效"],
        "deliverables": ["图文笔记 1 篇"],
        "notes": "",
        "priority": "normal",
        "deadline": None,
    }

    ctx: dict = {"brief": brief, "revision": 0}

    def run(purpose: str, label: str) -> dict:
        payload = GENERATORS[purpose](ctx)
        print(f"  {purpose:<16} {label:<12} ok  ({len(payload)} 个顶层字段)")
        return payload

    print("\n[Mock 引擎全链路]")
    ctx["strategy"] = run("A1.strategy", "策略洞察")
    ctx["creative"] = run("A2.creative", "创意方向")
    ctx["plan"] = run("A3.plan", "内容策划")
    ctx["draft"] = run("A4.copy", "文案创作")

    edit = run("A5.edit", "编辑审校")
    print(
        f"    → A5 verdict={edit['verdict']} overall={edit['scorecard']['overall']} "
        f"issues={len(edit['issues'])}"
    )

    fact = run("A6.factcheck", "事实核查")
    print(
        f"    → A6 verdict={fact['verdict']} risk={fact['risk_level']} "
        f"需修订={len(fact['required_fixes'])}"
    )

    comp = run("A7.compliance", "品牌合规")
    print(
        f"    → A7 verdict={comp['verdict']} score={comp['compliance_score']} "
        f"blocker={comp['summary']['blocker']} major={comp['summary']['major']}"
    )
    if comp["summary"]["blocker"] == 0:
        print("    ! 初稿未命中任何阻断级合规风险，门禁演示效果将减弱")

    ctx["visual"] = run("A8.visual", "视觉美术指导")
    print(
        f"    → A8 风格={ctx['visual']['visual_direction']['style']} "
        f"配图={len(ctx['visual']['image_prompts'])} 张 "
        f"图文一致={ctx['visual']['copy_visual_check']['aligned']}"
    )

    ctx["channel"] = run("A9.channel", "渠道适配")
    platforms = ctx["channel"]["platforms"]
    warns = [item for item in ctx["channel"]["channel_checklist"] if item["status"] != "pass"]
    print(
        f"    → A9 平台={len(platforms)} "
        f"标题={platforms[0]['title_length']}/{platforms[0]['title_limit']} 字 "
        f"长尾词={len(ctx['channel']['seo']['long_tail'])} 待确认项={len(warns)}"
    )

    ctx["analysis"] = run("A10.analyze", "效果预估")

    # 返工一轮：验证「补充来源 → 风险项消解」的收敛路径
    print("\n[Mock 引擎返工轮]")
    ctx["revision"] = 1
    ctx["feedback"] = comp["required_fixes"][:3] or ["补充来源标注"]
    ctx["draft"] = GENERATORS["A4.copy"](ctx)
    edit2 = GENERATORS["A5.edit"](ctx)
    fact2 = GENERATORS["A6.factcheck"](ctx)
    comp2 = GENERATORS["A7.compliance"](ctx)
    print(
        f"    → A5 {edit2['verdict']}/{edit2['scorecard']['overall']}  "
        f"A6 {fact2['verdict']}({fact2['risk_level']})  "
        f"A7 {comp2['verdict']}/{comp2['compliance_score']}"
    )

    # 返工后重跑投放侧链路，确认 A8/A9 会基于新定稿重新生成，而非复用旧稿
    ctx["visual"] = GENERATORS["A8.visual"](ctx)
    ctx["channel"] = GENERATORS["A9.channel"](ctx)
    ctx["analysis"] = GENERATORS["A10.analyze"](ctx)
    ctx["edit"] = edit2
    ctx["compliance"] = comp2
    ctx["artifact_count"] = 14
    memory = GENERATORS["A11.memory"](ctx)
    print(
        f"    → A11 卡片={len(memory['knowledge_cards'])} "
        f"模板={len(memory['templates'])} 知识缺口={len(memory['gaps'])} "
        f"记录返工={memory['archive']['revision_rounds']} 轮"
    )

    check_dynamic_channel(brief)
    return comp2["verdict"] == "pass", ctx, brief


def check_dynamic_channel(brief: dict) -> None:
    """动态渠道（抖音）覆盖「分镜脚本」与「标题超限压缩」两条分支。"""
    from app.llm.mock import GENERATORS

    video_brief = {**brief, "channel": "抖音", "deliverables": ["短视频脚本 1 支"]}
    ctx: dict = {"brief": video_brief, "revision": 0}
    for purpose, key in (
        ("A1.strategy", "strategy"),
        ("A2.creative", "creative"),
        ("A3.plan", "plan"),
        ("A4.copy", "draft"),
    ):
        ctx[key] = GENERATORS[purpose](ctx)

    visual = GENERATORS["A8.visual"](ctx)
    channel = GENERATORS["A9.channel"](ctx)
    platform = channel["platforms"][0]
    # 压缩作用在「搜索导向变体」上，所以基线是 seo_variant 而非原标题
    compressed = platform["title_length"] < len(platform["seo_variant"])

    print("\n[Mock 引擎：动态渠道（抖音）]")
    print(
        f"    → A8 分镜={len(visual['storyboard'])} 镜  "
        f"主画幅={visual['assets']['main_ratio']}  "
        f"封面={visual['assets']['cover_ratio']}"
    )
    print(
        f"    → A9 标题 {platform['title_length']}/{platform['title_limit']} 字"
        f"（搜索变体 {len(platform['seo_variant'])} 字，"
        f"已压缩={'是' if compressed else '否'}）"
    )
    if not visual["storyboard"]:
        print("    ! 动态渠道未产出分镜脚本")
    if not any(not item["seo_variant_ok"] for item in channel["platforms"]):
        print("    ! 未触发标题超限信号，压缩分支未被覆盖")


#: 真实 A11 智能体需要的上游产物：生成器 key -> (产物类型, 产出智能体)
CHAIN_ARTIFACTS: tuple[tuple[str, str, str], ...] = (
    ("strategy", "strategy_brief", "A1"),
    ("creative", "creative_concept", "A2"),
    ("plan", "content_plan", "A3"),
    ("draft", "copy_draft", "A4"),
    ("edit", "edited_copy", "A5"),
    ("compliance", "compliance_report", "A7"),
    ("visual", "visual_brief", "A8"),
    ("channel", "channel_adaptation", "A9"),
    ("analysis", "effect_report", "A10"),
)


def check_memory_recall(brief: dict, chain: dict) -> bool:
    """跑真实 A11 智能体本体，验证「写入知识库 → 下一个任务召回」闭环。

    这里刻意不走 ``GENERATORS``：``AgentRunContext`` 的字段漂移
    （曾出现过缺失 ``artifacts``）只有真跑 ``run()`` 才会暴露。
    """
    from app.agents.a11_memory import run as run_a11
    from app.agents.base import AgentRunContext
    from app.core.types import Artifact, Brief
    from app.knowledge.memory import memory_store

    model = Brief(**brief)
    upstream = {key: chain[key] for key, _, _ in CHAIN_ARTIFACTS}

    def build(task_id: str) -> AgentRunContext:
        artifacts = [
            Artifact(
                id=f"{task_id}-{key}",
                task_id=task_id,
                agent_id=agent_id,  # type: ignore[arg-type]
                type=artifact_type,  # type: ignore[arg-type]
                version=1,
                title=artifact_type,
                content=chain[key],
            )
            for key, artifact_type, agent_id in CHAIN_ARTIFACTS
        ]
        return AgentRunContext(
            task_id=task_id,
            brief=model,
            phase="MEMORY",
            revision=0,
            feedback=[],
            upstream=upstream,
            next_version=lambda _type: 1,
            emit=lambda *args, **kwargs: None,
            artifacts=artifacts,
        )

    def card_of(result) -> dict:
        return next(item.content for item in result.artifacts if item.type == "knowledge_card")

    print("\n[A11 智能体本体：记忆库 RAG 闭环]")
    first = card_of(run_a11(build("doctor-memory-1")))
    indexed = int(first["archive"]["indexed_cards"])
    print(f"    → 任务 1 入库 {indexed} 条新知识｜召回 {len(first['recalled'])} 条（应为 0）")

    second = card_of(run_a11(build("doctor-memory-2")))
    recalled = second["recalled"]
    print(f"    → 任务 2 召回 {len(recalled)} 条历史资产")
    if recalled:
        top = recalled[0]
        print(
            f"      示例：{top['title']}（score={top['score']}｜"
            f"语义={top.get('vector_score')}｜{'、'.join(top['reasons'])}）"
        )

    stats = memory_store.stats()
    print(
        f"    → 记忆库：{stats['total']} 条卡片，覆盖 {stats['tasks']} 个任务｜"
        f"向量提供方={stats['embedding']['provider']} 权重={stats['embedding']['weight']} "
        f"已建索引={stats['vector_indexed']}"
    )

    if indexed <= 0:
        print("    ! 任务 1 未向记忆库写入知识，跨任务复用将失效")
    if not recalled:
        print("    ! 任务 2 未召回任务 1 的知识，RAG 闭环未成立")
    return indexed > 0 and len(recalled) > 0


def check_embedding() -> bool:
    """校验混合检索的向量分量：确定性、L2 归一化、相似度排序。

    本地 hashing embedding 不需要模型文件，所以自检可以完全离线跑；
    重点验证「同文本同向量」与「异话题相似度更低」这两条检索正确性的地基。
    """
    from app.knowledge.embedding import cosine, describe, hashing_vector

    print("\n[记忆库向量检索]")
    info = describe()
    print(
        f"    provider={info['provider']} model={info['model']} "
        f"dim={info['dim']} weight={info['weight']}"
    )
    a = hashing_vector("冷萃咖啡 早八通勤 小红书", info["dim"])
    b = hashing_vector("冷萃咖啡 早八通勤 小红书", info["dim"])
    c = hashing_vector("工业设备 招投标 白皮书", info["dim"])
    norm = sum(value * value for value in a) ** 0.5
    same, diff = cosine(a, b), cosine(a, c)
    print(
        f"    确定性={a == b}｜归一化范数={norm:.4f}｜自相似={same:.3f}｜异话题相似={diff:.3f}"
    )
    if not (a == b and abs(norm - 1.0) < 1e-6 and same > 0.99 and diff < same):
        print("    ! 向量分量异常：检索可能退化为噪声")
        return False
    return True


def check_publisher() -> bool:
    """校验发布排期的时段解析、跨天顺延与到期判断（自动投递的时间基础）。"""
    from datetime import datetime, timedelta, timezone

    from app.core.publisher import compute_due_at, is_due, parse_slot_time

    print("\n[发布排期：时段解析与到期判断]")
    morning = datetime(2026, 9, 13, 6, 0, tzinfo=timezone.utc)
    night = datetime(2026, 9, 13, 23, 0, tzinfo=timezone.utc)

    ok = True
    for slot in ("07:30-08:30 通勤时段", "工作日 10:00-11:30", "20：00 晚间", "无固定时段"):
        parsed, due = parse_slot_time(slot), compute_due_at(slot, morning)
        print(f"    {slot:<18} → 解析={parsed} 到期={due}")
        if slot == "无固定时段":
            ok = ok and parsed is None and due is None
        else:
            ok = ok and parsed is not None and due is not None

    rollover = compute_due_at("07:30-08:30", night)
    print(f"    跨天顺延：23:00 生成 07:30 → {rollover}")
    ok = ok and bool(rollover) and str(rollover).startswith("2026-09-14")

    not_due = is_due({"due_at": compute_due_at("07:30-08:30", morning)}, morning)
    due_now = is_due({"due_at": compute_due_at("07:30-08:30", night - timedelta(days=1))}, night)
    no_time = is_due({"due_at": None}, night)
    print(f"    未到点={not_due}｜已到点={due_now}｜无可解析时段={no_time}")
    ok = ok and not not_due and due_now and not no_time

    if not ok:
        print("    ! 排期时间语义异常：到点自动投递会误触发或永不触发")
    return ok


def check_auth() -> bool:
    """校验 ``CREATOR_API_TOKENS`` 的 Token → 租户解析（plan.md D17）。"""
    from app.config import _parse_api_tokens

    print("\n[鉴权：Token → 租户解析]")
    cases = [
        ("", {}),
        ("tok1", {"tok1": "default"}),
        ("acme:tok1,beta:tok2", {"tok1": "acme", "tok2": "beta"}),
    ]
    ok = True
    for raw, expected in cases:
        parsed = _parse_api_tokens(raw)
        print(f"    {raw or '（空）':<22} → {parsed}")
        ok = ok and parsed == expected
    if not ok:
        print("    ! Token 解析异常：鉴权开启后可能出现租户串号")
    return ok


def check_memory_tenant(brief: dict) -> bool:
    """校验记忆库的租户隔离（plan.md D17：A11 的「权限」职责）。

    要点有三：写入按租户归属、召回不越租户、容量按租户计量；
    最后回落到 ``default`` 租户确认单机（不鉴权）行为不变。
    """
    from app.knowledge.memory import memory_store

    print("\n[记忆库：租户隔离]")
    common = {
        "brand": "隔离演示",
        "channel": "小红书",
        "industry": "消费品",
        "cards": [{"type": "lesson", "title": "共享标题", "content": "同一内容在不同租户下应各自存活"}],
        "templates": [],
        "revision": 0,
    }

    added_acme = memory_store.remember(task_id="doctor-tenant-acme", tenant="acme", **common)
    added_beta = memory_store.remember(task_id="doctor-tenant-beta", tenant="beta", **common)
    dup_acme = memory_store.remember(task_id="doctor-tenant-acme-2", tenant="acme", **common)

    acme_stats = memory_store.stats("acme")
    beta_stats = memory_store.stats("beta")
    acme_hits = memory_store.retrieve(
        "共享标题 同一内容", top_k=5, tenant="acme", exclude_task="doctor-tenant-acme"
    )
    leaked = [hit for hit in acme_hits if hit.card.tenant != "acme"]

    print(f"    acme 入库 {added_acme} 条｜beta 入库 {added_beta} 条｜acme 重复入库 {dup_acme} 条（应为 0）")
    print(
        f"    acme 可见 {acme_stats['total']} 条（全局 {acme_stats['global_total']}）｜"
        f"beta 可见 {beta_stats['total']} 条｜租户列表 {acme_stats['tenants']}"
    )
    print(f"    acme 召回 {len(acme_hits)} 条｜跨租户泄漏 {len(leaked)} 条（应为 0）")

    ok = (
        added_acme == 1
        and added_beta == 1          # 同名同内容在不同租户下必须各存一份
        and dup_acme == 0            # 同租户内仍然幂等
        and acme_stats["total"] >= 1
        and beta_stats["total"] >= 1
        and not leaked
    )
    if not ok:
        print("    ! 租户隔离异常：可能出现跨租户复用或重复入库")
    return ok


def check_checkpointer() -> bool:
    """校验断点续跑用的是 SQLite 检查点，而不是静默退回的内存检查点。

    「静默退化」是本项目最需要防守的失败模式之一：内存检查点下一切自检都通过，
    但进程一旦重启，运行中的任务就再也接不回来。
    """
    from app.core.orchestrator import orchestrator

    print("\n[断点续跑：检查点后端]")
    kind = orchestrator.checkpointer_kind
    print(f"    后端={kind}" + (f"｜失败原因：{orchestrator.checkpointer_error}" if kind != "sqlite" else ""))
    if kind != "sqlite":
        print("    ! 检查点退回内存实现：进程重启后中断任务无法续跑")
        return False
    return True


def check_judge(brief: dict, chain: dict) -> bool:
    """校验 LLM-as-a-Judge 离线评估器：可评分、可解释、口径可复现（plan.md D14）。"""
    from app.core.judge import RUBRIC_VERSION, judge_offline
    from app.core.types import Brief

    print("\n[LLM-as-a-Judge：离线评估器]")
    model = Brief(**brief)
    upstream = {
        "draft": chain["draft"],
        "edit": chain["edit"],
        "factcheck": {"summary": {"unverified": 0, "exaggerated": 0, "contradicted": 0}, "risk_level": "low"},
        "compliance": chain["compliance"],
    }

    first = judge_offline(model, upstream, pass_threshold=75.0)
    second = judge_offline(model, upstream, pass_threshold=75.0)
    axes = {axis.key: axis.score for axis in first.axes}
    print(f"    rubric={RUBRIC_VERSION}｜总分 {first.total:.1f}/100｜裁决 {first.verdict}｜置信度 {first.confidence:.2f}")
    print("    维度：" + "｜".join(f"{axis.label} {axis.score:.1f}" for axis in first.axes))
    print(f"    可复现={first.total == second.total and axes == {a.key: a.score for a in second.axes}}")

    # 合规风险必须能被压分：把阻断级用语塞进定稿正文，合规维度应显著下降
    risky = {
        **upstream,
        "edit": {
            "revised": {
                "title": "[编辑稿] 国家级最优冷萃咖啡，喝了立刻见效",
                "body": "本产品为国家级最佳选择，保证有效，无效退款，且可治疗便秘，欢迎加微信领取。",
                "cta": "点击下单",
                "hashtags": ["#咖啡", "#冷萃"],
            }
        },
    }
    bad = judge_offline(model, risky, pass_threshold=75.0)
    bad_compliance = next(axis.score for axis in bad.axes if axis.key == "compliance")
    print(f"    注入阻断用语后：总分 {bad.total:.1f}｜合规维度 {bad_compliance:.1f}（原 {axes['compliance']:.1f}）")

    ok = (
        bool(first.axes)
        and len(first.axes) == 6
        and first.verdict in ("pass", "review", "reject")
        and first.total == second.total
        and bad_compliance < axes["compliance"]
        and bool(first.issues or first.suggestions)
    )
    if not ok:
        print("    ! 评估器异常：分数不可复现或对合规风险不敏感")
    return ok


def check_golden() -> bool:
    """校验黄金数据集的**回归判定器**真的会判回归。

    为什么值得单独检查：一个「永远显示通过」的回归门禁比没有门禁更糟——
    它会让「基线被悄悄改宽」这类问题完全不可见。这里逐条构造偏差，
    确认每一种都应被判为问题：

    * 运行失败 / 全量运行缺用例 → 不通过
    * 质量分或评估分回退超过容差 → ``regressed``
    * 分数未动但返工轮次 +1 → ``regressed``（成本与稳定性变差）
    * 监管用例首轮合规不再被拦截 → ``regressed``（门禁强度下降）
    * 容差内的 2 分波动 → 持平（避免把噪声当回归）

    同时确认「部分运行」语义：只跑一条用例时，其余用例不应被算成缺失。
    """
    from app.core.golden import CaseResult, compare, load_baseline, load_dataset

    print("\n[黄金数据集：回归判定器]")
    cases = load_dataset()
    baseline = load_baseline()
    base_cases = dict(baseline.get("cases") or {})
    print(
        f"    用例 {len(cases)} 条｜基线 {len(base_cases)} 条"
        f"｜rubric={baseline.get('rubric') or '（无基线）'}"
    )
    if not cases or not base_cases:
        print("    ! 缺少用例或基线：请先跑 python scripts/golden_eval.py --update-baseline")
        return False

    ids = sorted(base_cases)[:3]

    def make(case_id: str, **override: object) -> CaseResult:
        payload = dict(base_cases[case_id])
        payload.update(override)
        return CaseResult(**payload)  # type: ignore[arg-type]

    same = [make(cid) for cid in ids]
    checks: list[tuple[str, bool]] = [
        ("完全一致应通过", compare(same, scope=ids)["ok"] is True),
    ]

    cases_to_try: list[tuple[str, str, float]] = [
        ("质量分回退应判回归", "quality_score", -10),
        ("评估分回退应判回归", "judge_final_total", -5),
    ]
    for label, metric, delta in cases_to_try:
        mutated = [make(ids[0], **{metric: base_cases[ids[0]][metric] + delta})] + [
            make(cid) for cid in ids[1:]
        ]
        checks.append((label, compare(mutated, scope=ids)["ok"] is False))

    more_revision = [make(ids[0], revision_round=base_cases[ids[0]]["revision_round"] + 1)] + [
        make(cid) for cid in ids[1:]
    ]
    checks.append(("返工轮次增加应判回归", compare(more_revision, scope=ids)["ok"] is False))

    within = [make(ids[0], quality_score=base_cases[ids[0]]["quality_score"] - 2)] + [
        make(cid) for cid in ids[1:]
    ]
    checks.append(("容差内波动应持平", compare(within, scope=ids)["ok"] is True))
    checks.append(("全量运行缺用例应不通过", compare([make(ids[0])])["ok"] is False))
    checks.append(
        ("部分运行缺范围外用例应通过", compare([make(ids[0])], scope=[ids[0]])["ok"] is True)
    )
    checks.append(
        (
            "运行失败应不通过",
            compare(
                [*same, CaseResult(id="bogus", status="failed", error="模拟失败")], scope=ids
            )["ok"]
            is False,
        )
    )

    blocked = next((cid for cid in base_cases if base_cases[cid].get("first_round_blocked")), None)
    if blocked:
        result = compare([make(blocked, first_round_blocked=False)], scope=[blocked])
        checks.append(("监管用例未被拦截应判回归", result["ok"] is False))

    ok = True
    for label, passed in checks:
        print(f"    {'✓' if passed else '✗'} {label}")
        ok = ok and passed
    if not ok:
        print("    ! 回归判定器异常：门禁可能把回归误判为通过")
    return ok


def check_negation() -> bool:
    """校验内容级断言的「否定语境」判定。

    为什么单独测这一条：合规文案里到处是「不得涉及疾病预防与治疗功能」这类**免责声明**，
    如果把它当成违规宣称，每一条监管用例都会误报 —— 而误报会让人开始忽略这套断言，
    等于把门禁废掉。反过来，如果否定判定过宽（把肯定性宣称也豁免掉），
    真正的违规就会漏出去。两个方向都危险，所以逐例固定行为。

    这个判定曾被改坏过两次：先是固定字符窗口太短（否定词与禁用词相隔 8 字就漏判），
    后是没把中文逗号当小句边界（前半句的否定豁免了后半句的肯定宣称）。
    """
    from app.core.golden import _is_negated

    print("\n[内容级断言：否定语境判定]")
    cases: list[tuple[str, str, bool, str]] = [
        ("【合规保障】不得涉及疾病预防与治疗功能", "治疗", True, "免责声明应豁免"),
        ("本品可治疗失眠，效果显著", "治疗", False, "肯定性宣称不得豁免"),
        ("不得用于治疗，但可治疗失眠", "治疗", False, "逗号后是新的小句，不得被前句豁免"),
        ("本产品不涉及治疗功能。可治疗失眠。", "治疗", False, "跨句不得豁免"),
        ("无副作用，不含激素", "激素", True, "「不含」属否定语境"),
        ("国家级最佳产品", "最佳", False, "绝对化用语必须判出来"),
        ("不得使用国家级用语", "国家级", True, "「不得使用」属否定语境"),
        ("严禁出现稳赚不赔的表述", "稳赚", True, "「严禁」属否定语境"),
        ("年化收益稳定，稳赚不赔", "稳赚", False, "收益承诺必须判出来"),
        ("", "治疗", False, "空文本不应判为否定"),
    ]
    ok = True
    for text, term, expected, why in cases:
        got = _is_negated(text, term)
        good = got == expected
        ok = ok and good
        mark = "✓" if good else "✗"
        print(f"    {mark} {why}｜negated={got}（期望 {expected}）")
    if not ok:
        print("    ! 否定语境判定异常：内容级断言会误报或漏报")
    return ok


def check_tracing(brief: dict, chain: dict) -> bool:
    """校验追踪层：span 树成型、层级正确、属性可用、落盘为 OTel 形状。

    追踪最容易「看起来有了但没用」：埋了点，但所有 span 都挂在根上，
    或者没有耗时占比，排障时依然看不出瓶颈在哪。因此这里断言的不只是
    「有 span」，而是**层级关系、子 span 嵌套、self-time 占比、导出形状**。
    """
    import json as _json
    import time as _time

    from app.core.events import event_bus
    from app.core.orchestrator import orchestrator
    from app.core.store import task_store
    from app.core.tracing import EXPORT_DIR, tracer
    from app.core.types import Brief

    print("\n[调用轨迹：span 树与导出]")
    model = Brief(**brief)
    task = orchestrator.create_task(model, auto_approve=True)

    deadline = _time.time() + 180
    status = "running"
    while _time.time() < deadline:
        current = task_store.get(task.id)
        if current is not None and current.status in (
            "completed",
            "rejected",
            "failed",
            "cancelled",
        ):
            status = current.status
            break
        _time.sleep(0.3)

    trace = tracer.trace(task.id)
    if trace is None:
        print("    ! 任务结束后没有 trace（追踪未开启）")
        return False

    by_id = {span.span_id: span for span in trace.spans}
    roots = [span for span in trace.spans if span.parent_span_id is None]
    llm_spans = [span for span in trace.spans if span.name.startswith("llm.")]
    agent_spans = [span for span in trace.spans if span.name.startswith("A") and span.agent_id]
    gate_spans = [span for span in trace.spans if span.name == "gate.review"]

    # 层级正确性：llm span 必须嵌在智能体/评估 span 之下，而不是直接挂根
    nested_ok = all(
        span.parent_span_id in by_id
        and by_id[span.parent_span_id].name.startswith(("A", "judge", "gate"))
        for span in llm_spans
    )
    # 根 span 不应等于 span 总数（那意味着树退化成了平铺列表）
    hierarchy_ok = len(roots) < len(trace.spans)

    kinds = {span.kind for span in trace.spans}
    statuses = {span.status for span in trace.spans}
    with_attrs = sum(1 for span in llm_spans if "llm.model" in span.attributes)

    print(f"    任务终态={status}｜trace={trace.trace_id[:8]}｜span={len(trace.spans)}"
          f"｜根={len(roots)}｜总耗时={trace.duration_ms}ms")
    print(f"    智能体 span={len(agent_spans)}｜门禁 span={len(gate_spans)}"
          f"｜llm span={len(llm_spans)}（带模型属性 {with_attrs}）")
    print(f"    span 类型={sorted(kinds)}｜状态={sorted(statuses)}")
    print(f"    层级成型={hierarchy_ok}｜llm 嵌套正确={nested_ok}")

    # 事件与 span 的关联：排查时不必靠时间戳猜「这条事件属于哪个 span」
    events = event_bus.history(task.id)
    linked = [event for event in events if event.trace_id]
    with_span = [event for event in events if event.span_id]
    same_trace = {event.trace_id for event in linked} == {trace.trace_id}
    print(f"    事件 {len(events)} 条｜带 trace_id {len(linked)}｜带 span_id {len(with_span)}"
          f"｜trace 一致={same_trace}")

    exported = EXPORT_DIR / f"{task.id}.json"
    otel_ok = False
    if exported.exists():
        payload = _json.loads(exported.read_text(encoding="utf-8"))
        otel = payload.get("otel") or []
        otel_ok = bool(otel) and {"traceId", "spanId", "kind"} <= set(otel[0])
    print(f"    OTel 形状导出={exported.exists()}｜形状字段齐全={otel_ok}")

    stats = tracer.stats()
    top = stats["by_name"][0] if stats["by_name"] else {}
    print(f"    全局聚合：traces={stats['traces']} spans={stats['spans']} "
          f"errors={stats['errors']}｜最贵 span={top.get('name')}（{top.get('total_ms')}ms）")

    ok = (
        len(trace.spans) >= 20
        and hierarchy_ok
        and nested_ok
        and len(agent_spans) >= 11
        and len(gate_spans) >= 1
        and len(llm_spans) >= 11
        and with_attrs == len(llm_spans)
        and same_trace
        and len(with_span) > 0
        and otel_ok
    )
    if not ok:
        print("    ! 追踪异常：span 树可能退化、层级错挂或导出形状不符")
    return ok


def check_otel(brief: dict) -> bool:
    """校验 OTLP 导出链路：id 一致、层级保留、属性齐全、默认不加载 OTel。

    这里用**内存导出器**代替真实 collector，因此不需要起 Jaeger 也能断言导出正确性 ——
    与项目其它能力一致：关键路径必须可离线验证。

    两个曾经踩到的坑正是本检查的重点（见 MEMORY 踩坑 53–54）：
    用 OTel API 创建 span 会让 SDK **重新生成 id**、层级也会丢，
    导致本地 trace JSON 与 Jaeger 里对不上。
    """
    import time as _time

    from app.core import otel
    from app.core.orchestrator import orchestrator
    from app.core.store import task_store
    from app.core.tracing import tracer
    from app.core.types import Brief

    print("\n[OTLP 导出：链路与 id 一致性]")

    # 未配置 endpoint 时不应加载 OTel（默认零依赖）
    default_off = not otel.enabled()
    print(f"    默认不启用导出={default_off}")

    normalized_ok = all(
        otel._normalize_endpoint(raw) == expected  # noqa: SLF001 - 自检直取内部纯函数
        for raw, expected in (
            ("http://localhost:4318", "http://localhost:4318/v1/traces"),
            ("http://localhost:4318/", "http://localhost:4318/v1/traces"),
            ("http://c:4318/v1/traces", "http://c:4318/v1/traces"),
            ("", ""),
        )
    )
    headers_ok = otel._parse_headers("a=1,b=2") == {"a": "1", "b": "2"}  # noqa: SLF001
    print(f"    endpoint 规整={normalized_ok}｜headers 解析={headers_ok}")

    try:
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    except Exception as error:  # noqa: BLE001 - 依赖缺失要能明确报出来
        print(f"    ! OpenTelemetry 依赖不可用：{type(error).__name__}: {error}")
        print("      请执行 pip install -r requirements.txt")
        return False

    memory = InMemorySpanExporter()
    if not otel.setup(processor=SimpleSpanProcessor(memory), service_name="creator-doctor"):
        print("    ! 注入内存导出器失败")
        return False

    task = orchestrator.create_task(Brief(**brief), auto_approve=True)
    deadline = _time.time() + 180
    while _time.time() < deadline:
        current = task_store.get(task.id)
        if current is not None and current.status in (
            "completed",
            "rejected",
            "failed",
            "cancelled",
        ):
            break
        _time.sleep(0.3)

    local = tracer.trace(task.id)
    exported = list(memory.get_finished_spans())

    local_traces = {span.trace_id for span in local.spans} if local else set()
    exported_traces = {format(span.context.trace_id, "032x") for span in exported}
    local_ids = {span.span_id for span in local.spans} if local else set()
    exported_ids = {format(span.context.span_id, "016x") for span in exported}

    exported_by_id = {format(span.context.span_id, "016x"): span for span in exported}
    llm_exported = [span for span in exported if span.name.startswith("llm.")]
    roots = [span for span in exported if span.parent is None]
    # 父子关系必须保留：否则在 Jaeger 里会变成一堆并列根 span（第七轮踩过的坑）
    nested_ok = bool(llm_exported) and all(
        span.parent is not None and format(span.parent.span_id, "016x") in exported_by_id
        for span in llm_exported
    )
    # 智能体归属必须沿树继承到 llm.* 这类子 span，否则按 agent 过滤会丢 span
    attributed = [
        span for span in llm_exported if (span.attributes or {}).get("creator.agent_id")
    ]

    print(f"    本地 span={len(local_ids)}｜导出 span={len(exported_ids)}")
    print(f"    trace_id 一致={local_traces == exported_traces}"
          f"｜span_id 全部命中={local_ids <= exported_ids}")
    print(f"    导出后根 span={len(roots)}｜llm span={len(llm_exported)}"
          f"｜嵌套保留={nested_ok}｜带智能体归属={len(attributed)}")

    sample = llm_exported[0] if llm_exported else None
    sample_attrs = dict(sample.attributes or {}) if sample else {}
    attrs_ok = all(
        key in sample_attrs
        for key in ("llm.provider", "llm.model", "llm.prompt_tokens", "creator.agent_id")
    )
    if sample is not None:
        print(f"    样本「{sample.name}」：provider={sample_attrs.get('llm.provider')} "
              f"model={sample_attrs.get('llm.model')} "
              f"agent={sample_attrs.get('creator.agent_id')}")

    stats = otel.stats()
    print(f"    导出统计：exported={stats['exported']} failed={stats['failed']}")
    otel.shutdown()

    ok = (
        default_off
        and normalized_ok
        and headers_ok
        and len(exported_ids) >= 20
        and local_traces == exported_traces
        and local_ids <= exported_ids
        and nested_ok
        and len(attributed) == len(llm_exported)
        and attrs_ok
        and stats["failed"] == 0
    )
    if not ok:
        print("    ! OTLP 导出异常：id 不一致、层级丢失或属性缺失会让远端排障失效")
    return ok


def check_multilingual() -> bool:
    """校验多语言本地化：语言识别、字数口径、原生创作、合规如实告知。

    最容易做假的地方是「英文 Brief 仍然产出中文」—— 看起来链路通了，
    实际只是把中文稿当英文交付。这里直接断言英文产物的**中文字符数为 0**。
    """
    import re as _re

    from app.core.types import Brief
    from app.knowledge.language import (
        compliance_coverage,
        localization_directive,
        normalize_language,
        title_measure,
    )
    from app.llm.mock import GENERATORS

    print("\n[多语言本地化]")
    cjk = _re.compile(r"[\u4e00-\u9fff]")

    # 1) 语言识别（含中文别名与地区子标签）
    cases = [
        ("en", "en"), ("en-US", "en"), ("English", "en"), ("英文", "en"),
        ("zh-CN", "zh"), ("中文", "zh"), ("日文", "ja"), ("ja-JP", "ja"),
        ("korean", "ko"), ("", "zh"), ("xx", "zh"),
    ]
    alias_ok = True
    for raw, expected in cases:
        got = normalize_language(raw)
        if got != expected:
            alias_ok = False
            print(f"    ! 语言识别异常：{raw!r} → {got}（期望 {expected}）")
    print(f"    语言识别：{len(cases)} 个用例 {'全部正确' if alias_ok else '存在错误'}")

    # 2) 标题字数口径：英文按词、中文按字
    en_words, en_unit = title_measure("Cold brew coffee for busy mornings", "en")
    zh_chars, zh_unit = title_measure("冷萃咖啡，早八通勤的好选择", "zh")
    unit_ok = (en_words, en_unit) == (6, "词") and (zh_chars, zh_unit) == (13, "字")
    print(f"    字数口径：英文 {en_words}{en_unit}｜中文 {zh_chars}{zh_unit}"
          f"（{'正确' if unit_ok else '异常'}）")

    # 3) 本地化指令：非中文必须包含「原生创作」与当地合规要点
    directive = localization_directive("Instagram", "en")
    directive_ok = "原生创作" in directive and "FTC" in directive and "#ad" in directive
    zh_directive_empty = localization_directive("小红书", "zh") == ""
    print(f"    本地化指令：英文含原生创作/合规要求={directive_ok}｜中文为空串={zh_directive_empty}")

    # 4) 合规覆盖：非中文必须**明确说明未覆盖**
    coverage = compliance_coverage("en")
    coverage_ok = coverage["lexicon_coverage"] is False and "人工复核" in str(coverage["message"])
    zh_coverage_ok = compliance_coverage("zh")["lexicon_coverage"] is True
    print(f"    合规覆盖：en 如实声明未覆盖={coverage_ok}｜zh 已接入词库={zh_coverage_ok}")

    # 5) 英文 Brief 必须产出英文文案（中文字符数为 0）
    brief_en = Brief(
        brand="Morning Field",
        product="Cold Brew Coffee",
        channel="Instagram",
        industry="Food & Beverage",
        language="en",
        audience="busy commuters",
        keywords=["cold brew"],
        tone="casual and honest",
    )
    out = GENERATORS["A4.copy"](
        {
            "brief": brief_en.model_dump(mode="json"),
            "strategy": {},
            "creative": {},
            "plan": {},
            "feedback": [],
        }
    )
    version = out["versions"][0]
    body_cjk = len(cjk.findall(version["body"]))
    title_cjk = len(cjk.findall(version["title"]))
    print(f"    英文产出：title={version['title']!r}")
    print(f"      正文字符数={len(version['body'])}｜其中中文 {body_cjk}｜标题中文 {title_cjk}")
    native_ok = body_cjk == 0 and title_cjk == 0 and len(version["body"]) > 100

    # 6) 中文链路回归：中文 Brief 仍产出中文
    brief_zh = Brief(
        brand="晨野", product="冷萃即饮咖啡", channel="小红书", industry="食品饮料",
        language="zh", audience="早八通勤白领", keywords=["冷萃"], tone="轻松",
    )
    out_zh = GENERATORS["A4.copy"](
        {
            "brief": brief_zh.model_dump(mode="json"),
            "strategy": {}, "creative": {}, "plan": {}, "feedback": [],
        }
    )
    zh_title = out_zh["versions"][0]["title"]
    zh_ok = len(cjk.findall(zh_title)) > 0
    print(f"    中文回归：标题={zh_title!r}｜含中文={zh_ok}")

    ok = (
        alias_ok and unit_ok and directive_ok and zh_directive_empty
        and coverage_ok and zh_coverage_ok and native_ok and zh_ok
    )
    if not ok:
        print("    ! 多语言异常：可能出现「英文 Brief 产出中文」或字数口径用错")
    return ok


def main() -> int:
    data_dir = isolate_data_dir()
    check_python()
    print(f"data dir    : {data_dir}")
    print()
    check_deps()

    try:
        converged, chain, brief = exercise_mock()
        recalled = check_memory_recall(brief, chain)
        embedded = check_embedding()
        scheduled = check_publisher()
        authed = check_auth()
        tenanted = check_memory_tenant(brief)
        checkpointed = check_checkpointer()
        judged = check_judge(brief, chain)
        golden = check_golden()
        negation = check_negation()
        multilingual = check_multilingual()
        traced = check_tracing(brief, chain)
        otlp = check_otel(brief)
    except Exception as error:  # noqa: BLE001
        print(f"\n[失败] 自检演练异常：{type(error).__name__}: {error}")
        import traceback

        traceback.print_exc()
        return 1

    checks = {
        "Mock 引擎收敛": converged,
        "记忆库 RAG 闭环": recalled,
        "向量检索": embedded,
        "发布排期": scheduled,
        "鉴权租户解析": authed,
        "记忆库租户隔离": tenanted,
        "断点续跑检查点": checkpointed,
        "LLM-as-a-Judge": judged,
        "黄金数据集判定器": golden,
        "否定语境判定": negation,
        "多语言本地化": multilingual,
        "调用轨迹追踪": traced,
        "OTLP 导出链路": otlp,
    }
    print()
    if all(checks.values()):
        print(
            "自检通过：环境可用；Mock 引擎可在返工 1 轮后收敛；"
            "记忆库 RAG（关键词 + 向量混合）闭环成立且按租户隔离；发布排期具备自动投递的时间基础；"
            "鉴权租户解析正确；SQLite 检查点可用（断点续跑有效）；离线评估器可复现且对合规风险敏感；"
            "黄金数据集回归判定器能正确识别回退、失败与门禁强度下降；"
            "调用轨迹 span 树成型、层级正确且可按 OTel 形状导出；"
            "OTLP 导出链路 id 一致、层级与智能体归属保留。"
        )
        return 0

    failed = [name for name, value in checks.items() if not value]
    print(f"自检未通过：{'、'.join(failed)}")
    if not converged:
        print("  · 返工后仍未通过合规门禁，请检查知识层与 Mock 生成器。")
    if not recalled:
        print("  · 记忆库 RAG 闭环未成立，请检查 app/knowledge/memory.py 与 A11。")
    if not embedded:
        print("  · 向量分量异常，请检查 app/knowledge/embedding.py。")
    if not scheduled:
        print("  · 排期时间语义异常，请检查 app/core/publisher.py。")
    if not authed:
        print("  · Token 解析异常，请检查 app/config.py 的 _parse_api_tokens。")
    if not tenanted:
        print("  · 记忆库租户隔离异常，请检查 MemoryStore.remember / retrieve 的 tenant 参数。")
    if not checkpointed:
        print("  · 检查点退化为内存实现，请确认数据目录可写（沙箱/权限）。")
    if not judged:
        print("  · 评估器异常，请检查 app/core/judge.py 的离线评估规则。")
    if not golden:
        print("  · 黄金数据集判定器异常，请检查 app/core/golden.py 的 compare 逻辑。")
    if not negation:
        print("  · 否定语境判定异常，请检查 app/core/golden.py 的 _is_negated。")
    if not multilingual:
        print("  · 多语言异常，请检查 app/knowledge/language.py 与 mock 的本地化分支。")
    if not traced:
        print("  · 追踪异常，请检查 app/core/tracing.py 与编排层的 span 埋点。")
    if not otlp:
        print("  · OTLP 导出异常，请检查 app/core/otel.py（或先 pip install -r requirements.txt）。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
