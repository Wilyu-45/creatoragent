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
15. 校验 **W3C traceparent 传播与采样**：头的解析/构造容错、跨进程 trace 延续
    （本地根 span 挂到远端父 span 之下）、采样决策矩阵（parentbased_* / ratio）
    以及「未采样 trace 不导出、进程内轨迹仍完整」的导出面语义
16. 校验 **数字人渲染样例**：渲染清单（分镜透传 / 无口播告警 / 时长偏差）、
    样例引擎按流逝时间的惰性推进（排队 → 渲染 → 完成）、租户隔离、
    http 适配样例在未配置网关时的显式失败路径与任务删除时的作业回收
17. 校验 **Brief 素材（多模态输入）**：五种图片格式的画幅实测、本地素材的
    目录约束与如实报错、离线/读图两种提示词口径、A8 素材规格比对工具，
    以及带素材任务在离线链路上的端到端可跑通
18. 校验 **确定性计算沙箱**：越权语法（导入/属性/下标/推导式/字符串/超大幂次/
    除零）逐条拦截、公式求值可复现，以及量级换算 / 排期推算 / 回填核算三个
    换算工具的结果可复核、缺失项如实列出

检查项的**计数会随合并/拆分变化**（第十轮新增「视频脚本」时是 17 项；第十二轮把
传播/采样与数字人样例并入既有项后回到 16 项；随后新增「Brief 素材（多模态）」为 17 项，
本轮新增「确定性计算沙箱」为 18 项），因此文档统一写「18 项」，
最终以本脚本实际输出的清单为准。

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


def force_offline_provider() -> str:
    """把自检固定到内置离线引擎。

    自检断言的是**契约与逻辑**（span 树层级、黄金判定器、租户隔离…），
    这些必须在秒级、可复现、无外部依赖的条件下成立。
    本机 ``.env`` 指向真实网关时，若自检跟着走真实模型，会同时踩三个坑：

    * 一条任务要 5 分钟，远超自检的等待上限（实测真实网关下 doctor 会超时）；
    * 真实模型输出发散，同一个断言可能今天过明天不过；
    * 每次自检都真的花钱。

    因此默认强制离线；需要验证真实链路时用 ``scripts/real_check.py``，
    或显式设 ``CREATOR_DOCTOR_PROVIDER=openai``。
    """
    override = (os.environ.get("CREATOR_DOCTOR_PROVIDER") or "").strip().lower()
    if override in ("openai", "mock"):
        os.environ["LLM_PROVIDER"] = override
    else:
        os.environ["LLM_PROVIDER"] = "mock"
    return os.environ["LLM_PROVIDER"]


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

    storage_mode = (os.environ.get("CREATOR_STORAGE") or "file").strip().lower()
    if storage_mode == "pg":
        import psycopg
        import psycopg_pool
        import redis

        from langgraph.checkpoint.postgres import PostgresSaver  # noqa: F401

        print(f"psycopg     : {psycopg.__version__}｜psycopg_pool: {psycopg_pool.__version__}")
        print(f"redis       : {redis.__version__}")
        print("langgraph   : PostgresSaver 可用（pg 存储模式）")
        return

    # 反向断言（零依赖防回归）：file 模式下 PG/Redis 依赖必须**从未被加载**。
    # PG 实现全部藏在条件分支的函数体内，若此处 sys.modules 里出现它们，
    # 说明有人在模块顶层加了 import —— 「零依赖离线可跑」的承诺即被打破。
    leaked = sorted(name for name in ("psycopg", "psycopg_pool", "redis") if name in sys.modules)
    if leaked:
        print(f"    ! file 模式依赖泄漏：{leaked} 已被加载（应只在 pg 模式的条件分支内延迟 import）")
        raise SystemExit(1)


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
    """校验断点续跑用的是真检查点，而不是静默退回的内存检查点。

    「静默退化」是本项目最需要防守的失败模式之一：内存检查点下一切自检都通过，
    但进程一旦重启，运行中的任务就再也接不回来。
    file 模式期望 ``sqlite``；pg 模式期望 ``postgres``（PostgresSaver 在
    orchestrator 导入期完成 setup，连不上会 fail-loud，走不到这一步）。
    """
    storage_mode = (os.environ.get("CREATOR_STORAGE") or "file").strip().lower()
    expected = "postgres" if storage_mode == "pg" else "sqlite"
    from app.core.orchestrator import orchestrator

    print("\n[断点续跑：检查点后端]")
    kind = orchestrator.checkpointer_kind
    print(f"    后端={kind}（期望 {expected}）"
          + (f"｜失败原因：{orchestrator.checkpointer_error}" if kind != expected else ""))
    if kind != expected:
        print(f"    ! 检查点退回内存实现：进程重启后中断任务无法续跑（期望 {expected}）")
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
    # 任务状态已经是终态，但编排线程可能还在 `finally` 里收尾（结账、释放租约、
    # finish_trace）。finish_trace 才计算总耗时与 self-time 占比，
    # 所以这里必须等它完成，否则会看到 duration=0 与未收尾的 span 状态 —— 那是竞态，不是缺陷。
    wait_deadline = _time.time() + 15
    while _time.time() < wait_deadline and trace is not None and trace.duration_ms == 0:
        _time.sleep(0.1)
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
    # OTel 里 `unset` 是合法的「未显式设置状态」，本项目只在极少数收尾路径上出现，
    # 因此只断言「没有 error」，而不是要求全部 ok。
    no_errors = "error" not in statuses
    with_attrs = sum(1 for span in llm_spans if "llm.model" in span.attributes)

    print(f"    任务终态={status}｜trace={trace.trace_id[:8]}｜span={len(trace.spans)}"
          f"｜根={len(roots)}｜总耗时={trace.duration_ms}ms")
    print(f"    智能体 span={len(agent_spans)}｜门禁 span={len(gate_spans)}"
          f"｜llm span={len(llm_spans)}（带模型属性 {with_attrs}）")
    print(f"    span 类型={sorted(kinds)}｜状态={sorted(statuses)}｜无 error={no_errors}")
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
        and no_errors
        and otel_ok
        and trace.duration_ms > 0
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
    from app.core.tracing import EXPORT_DIR, tracer
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

    # 状态终态 ≠ 追踪导出完成：orchestrator 先把状态落库、退出图后 _export 才
    # 做 OTel 转发与落盘。只等状态会与导出赛跑（偶发 exported=0 的假失败，
    # 第十四轮实测踩过）。落盘文件在 OTel 转发**之后**写出 —— 等它出现（或
    # 短暂宽限后放弃，未采样 trace 本就不落盘）即可确定导出已结束。
    export_deadline = _time.time() + 10
    while _time.time() < export_deadline and not (EXPORT_DIR / f"{task.id}.json").exists():
        _time.sleep(0.05)

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


def check_trace_propagation() -> bool:
    """校验 W3C traceparent 传播与采样：头容错、跨进程延续、采样矩阵与导出面过滤。

    两个方向都必须守住：坏头绝不能打坏任务创建（传播是尽力而为的观测能力），
    而采样只作用于导出面 —— 未采样的 trace 在 Jaeger 里查不到是预期行为，
    但进程内轨迹必须完整，否则「采样」就变成了「丢数据」。
    """
    from app.config import get_config
    from app.core.tracing import (
        EXPORT_DIR,
        decide_sampling,
        format_traceparent,
        parse_traceparent,
        tracer,
    )

    print("\n[W3C 传播与采样]")

    # 1) 入站头解析：合法 / 未采样标记 / 各种坏输入
    good = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    unsampled = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-00"
    parse_cases = [
        (good, True),
        (unsampled, False),
        ("  00-4BF92F3577B34DA6A3CE929D0E0E4736-00F067AA0BA902B7-01  ", True),  # 大小写与空白容忍
        (None, None),
        ("", None),
        ("garbage", None),
        ("00-zzz-zzz-01", None),  # 非十六进制
        ("00-00000000000000000000000000000000-00f067aa0ba902b7-01", None),  # 全零 trace_id
        ("ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01", None),  # 版本 ff
    ]
    parse_ok = True
    for raw, expect_sampled in parse_cases:
        parsed = parse_traceparent(raw)
        if expect_sampled is None:
            ok = parsed is None
        else:
            ok = parsed is not None and parsed.sampled == expect_sampled
        parse_ok = parse_ok and ok
        if not ok:
            print(f"    ! 解析异常：{str(raw)[:44]!r} → {parsed}")
    print(f"    入站解析：{len(parse_cases)} 例 {'全部正确' if parse_ok else '存在错误'}")

    # 2) 出站构造与解析回环
    roundtrip = parse_traceparent(format_traceparent("4bf92f3577b34da6a3ce929d0e0e4736", "00f067aa0ba902b7"))
    build_ok = (
        roundtrip is not None and roundtrip.sampled is True
        and parse_traceparent(format_traceparent("4bf92f3577b34da6a3ce929d0e0e4736", "00f067aa0ba902b7", sampled=False)) is not None
        and format_traceparent("0" * 32, "00f067aa0ba902b7") == ""  # 非法入参 → 空串
    )
    print(f"    出站构造与回环：{'正确' if build_ok else '异常'}")

    # 3) 采样决策矩阵（与 OTel 采样器语义对齐）
    tid = "4bf92f3577b34da6a3ce929d0e0e4736"
    matrix = [
        ("always_on", 1.0, None, True),
        ("always_off", 1.0, None, False),
        ("traceidratio", 0.0, None, False),
        ("traceidratio", 1.0, None, True),
        ("parentbased_always_on", 1.0, True, True),
        ("parentbased_always_on", 1.0, False, False),
        ("parentbased_always_on", 1.0, None, True),
        ("parentbased_always_off", 1.0, None, False),
        ("parentbased_traceidratio", 0.5, False, False),  # 父方未采样 → 直接丢弃
        ("some_future_sampler", 1.0, None, True),  # 未知采样器宁可多留
    ]
    matrix_ok = all(
        decide_sampling(sampler, ratio, tid, parent) == expected
        for sampler, ratio, parent, expected in matrix
    )
    deterministic = decide_sampling("traceidratio", 0.3, tid, None) == decide_sampling(
        "traceidratio", 0.3, tid, None
    )
    print(
        f"    采样矩阵：{'正确' if matrix_ok else '异常'}｜同 trace 结论可复现={deterministic}"
    )

    # 4) 跨进程延续：本 trace 延续远端 trace_id，第一个根 span 挂到远端 span 之下
    parent = parse_traceparent(good)
    assert parent is not None
    trace_id = tracer.start_trace("doctor-prop-task", parent=parent)
    with tracer.span("doctor.propagation.root", kind="server") as root:
        outbound = tracer.current_traceparent()
    continuation_ok = (
        trace_id == parent.trace_id
        and root is not None and root.parent_span_id == parent.span_id
        and outbound.startswith(f"00-{parent.trace_id}-")
    )
    finished = tracer.finish_trace("doctor-prop-task")
    exported = (EXPORT_DIR / "doctor-prop-task.json").exists()
    tracer.drop("doctor-prop-task")
    print(
        f"    跨进程延续：trace_id 沿用={trace_id == parent.trace_id}"
        f"｜根 span 挂远端={continuation_ok}｜出站头={outbound[:24]}…"
        f"｜导出={exported}"
    )

    # 5) 未采样 trace：不导出（不落盘），但进程内轨迹完整
    cfg = get_config()
    original_sampler, original_ratio = cfg.tracing.sampler, cfg.tracing.sample_ratio
    try:
        cfg.tracing.sampler = "always_off"
        unsampled_id = tracer.start_trace("doctor-unsampled-task")
        with tracer.span("doctor.unsampled.probe"):
            pass
        unsampled_trace = tracer.finish_trace("doctor-unsampled-task")
        kept = unsampled_trace is not None and len(unsampled_trace.spans) == 1
        not_exported = not (EXPORT_DIR / "doctor-unsampled-task.json").exists()
    finally:
        cfg.tracing.sampler = original_sampler
        cfg.tracing.sample_ratio = original_ratio
    tracer.drop("doctor-unsampled-task")
    print(
        f"    导出面过滤：未采样 trace 仍有进程内 span={kept}"
        f"｜未落盘={not_exported}（Jaeger 查不到属预期）"
    )

    ok = (
        parse_ok and build_ok and matrix_ok and deterministic
        and continuation_ok and exported and kept and not_exported
    )
    if not ok:
        print("    ! 传播/采样异常：坏头会打坏任务创建，或采样把进程内轨迹也丢了")
    return ok


def check_digital_human() -> bool:
    """校验数字人渲染样例：清单、惰性推进、租户隔离、http 失败路径与回收。

    样例引擎必须「零依赖可回归」：不联网、不花钱，用流逝时间推进状态机；
    http 适配样例在未配置网关时必须**显式失败**而不是假装成功。
    """
    from datetime import datetime, timedelta, timezone

    from app.core.digital_human import (
        build_manifest,
        create_job,
        drop_task,
        get_job,
        list_jobs,
    )
    from app.core.types import Artifact, TaskRecord

    print("\n[数字人渲染样例]")

    # 1) 渲染清单：分镜透传、无口播告警、时长偏差告警
    script = {
        "channel": "抖音",
        "aspect_ratio": "9:16",
        "duration_seconds": 30,
        "hook": "开头 3 秒钩子",
        "cta": "点击下单",
        "shots": [
            {
                "shot": 1, "role": "钩子", "start_second": 0, "duration_seconds": 5,
                "voiceover": "还在喝速溶咖啡？", "subtitle": "还在喝速溶咖啡？",
                "visual": "手持咖啡杯特写", "camera": "近景",
            },
            {
                "shot": 2, "role": "卖点", "start_second": 5, "duration_seconds": 20,
                "voiceover": "", "visual": "产品陈列",
            },
        ],
    }
    manifest = build_manifest(script)
    manifest_ok = (
        manifest["shot_count"] == 2
        and manifest["segments"][0]["spoken"] is True
        and manifest["segments"][1]["spoken"] is False
        and len(manifest["warnings"]) == 2  # 无口播 + 时长偏差各一条
        and manifest["duration_seconds"] == 30
    )
    print(
        f"    渲染清单：{manifest['shot_count']} 镜｜口播 {sum(1 for s in manifest['segments'] if s['spoken'])}"
        f"｜告警 {len(manifest['warnings'])} 条｜{'正确' if manifest_ok else '异常'}"
    )

    # 2) 样例引擎惰性推进：排队 → 渲染 → 完成（用未来时间戳模拟流逝）
    task = TaskRecord(
        id="doctor-dh-1",
        tenant="default",
        artifacts=[
            Artifact(id="doctor-dh-1-script", task_id="doctor-dh-1", type="video_script", content=script)
        ],
    )
    job = create_job(task, provider="sample")
    base = datetime.now(timezone.utc)
    queued = job["status"] == "queued" and job["render_seconds"] >= 6

    mid = list_jobs(task_id=task.id, now=base + timedelta(seconds=3))
    rendering = len(mid) == 1 and mid[0]["status"] == "rendering" and 0 < mid[0]["progress"] < 100

    final = list_jobs(task_id=task.id, now=base + timedelta(seconds=40))
    done = (
        len(final) == 1
        and final[0]["status"] == "done"
        and final[0]["progress"] == 100
        and final[0]["video_url"].startswith("sample://")
        and len(final[0]["history"]) >= 3  # 受理 → 渲染中 → 完成
    )
    lifecycle_ok = queued and rendering and done
    print(
        f"    样例引擎：{queued and '排队 OK' or '排队异常'} → "
        f"{rendering and '渲染中 OK' or '渲染中异常'} → "
        f"{done and '完成 OK' or '完成异常'}（video_url={final[0]['video_url'] if final else '—'}）"
    )

    # 3) 租户隔离：别的租户看不到这个作业
    visible = get_job(job["id"], tenant="default") is not None
    leaked = get_job(job["id"], tenant="acme") is not None or bool(list_jobs(tenant="acme"))
    tenant_ok = visible and not leaked
    print(f"    租户隔离：本租户可见={visible}｜跨租户泄漏={leaked}")

    # 4) http 适配样例：未配置网关时显式失败（绝不假装成功）
    #    该路径在发起任何远端尝试之前就失败，因此 attempts == 0 是正确语义
    task2 = TaskRecord(
        id="doctor-dh-2",
        tenant="acme",
        artifacts=[
            Artifact(id="doctor-dh-2-script", task_id="doctor-dh-2", type="video_script", content=script)
        ],
    )
    job2 = create_job(task2, provider="http")
    http_ok = job2["status"] == "failed" and "未配置" in str(job2["error"])
    print(f"    http 样例（未配网关）：status={job2['status']}｜error={str(job2['error'])[:40]}…")

    # 5) 任务删除时回收作业
    recovered = drop_task(task.id) == 1 and drop_task(task2.id) == 1
    gone = not list_jobs(task_id=task.id) and not list_jobs(task_id=task2.id)
    cleanup_ok = recovered and gone
    print(f"    作业回收：recovered={recovered}｜清单已清空={gone}")

    no_script = None
    try:
        create_job(TaskRecord(id="doctor-dh-3", tenant="default"), provider="sample")
    except ValueError:
        no_script = "raised"
    script_guard_ok = no_script == "raised"
    print(f"    无脚本防护：拒绝创建={script_guard_ok}")

    ok = manifest_ok and lifecycle_ok and tenant_ok and http_ok and cleanup_ok and script_guard_ok
    if not ok:
        print("    ! 数字人样例异常：请检查 app/core/digital_human.py 的状态机与存储。")
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

    # 7) 英文全链路：交付物 + 六个支撑产物的中文残留必须为 0。
    #    第九轮时支撑产物仍是中文模板（creative_concept 558 个中文等），
    #    降级到 mock 的英文任务会被用户误判为「不可用」；现在统一走 _english_* 模板。
    brief_en_full = {
        "brand": "Morning Field",
        "product": "Cold Brew Coffee",
        "objective": "转化",
        "audience": "busy commuters in their 20s and 30s",
        "channel": "Instagram",
        "tone": "casual, honest, no hype",
        "industry": "Food & Beverage",
        "language": "en",
        "keywords": ["cold brew", "morning routine"],
        "constraints": ["no health claims", "must disclose paid partnership"],
        "deliverables": ["1 Instagram caption", "5 headline options"],
        "notes": "",
        "priority": "normal",
        "deadline": None,
    }
    en_ctx: dict = {"brief": brief_en_full, "revision": 0}
    for purpose, key in (
        ("A1.strategy", "strategy"),
        ("A2.creative", "creative"),
        ("A3.plan", "plan"),
        ("A4.copy", "draft"),
        ("A5.edit", "edit"),
        ("A6.factcheck", "fact"),
        ("A7.compliance", "compliance"),
        ("A8.visual", "visual"),
        ("A9.channel", "channel"),
        ("A10.analyze", "analysis"),
        ("A11.memory", "memory"),
    ):
        en_ctx[key] = GENERATORS[purpose](en_ctx)

    #（key, 产物类型）：覆盖交付物 + 六个支撑产物 + 策略简报；
    # A6/A7 的门禁报告文案是内部评审语言，不在多语言范围内（已如实记入文档）
    support_types = (
        ("strategy", "strategy_brief"),
        ("creative", "creative_concept"),
        ("plan", "content_plan"),
        ("draft", "copy_draft"),
        ("edit", "edited_copy"),
        ("visual", "visual_brief"),
        ("channel", "channel_adaptation"),
        ("analysis", "effect_report"),
        ("memory", "knowledge_card"),
    )
    support_ok = True
    for key, artifact_type in support_types:
        leaked = len(cjk.findall(str(en_ctx.get(key) or "")))
        if leaked:
            support_ok = False
            print(f"    ! {artifact_type} 含 {leaked} 个中文字符（英文 Brief 不应夹中文）")
    print(
        f"    英文支撑产物：{len(support_types)} 类中文残留"
        f"={'0' if support_ok else '>0（异常）'}"
    )

    ok = (
        alias_ok and unit_ok and directive_ok and zh_directive_empty
        and coverage_ok and zh_coverage_ok and native_ok and zh_ok
        and support_ok
    )
    if not ok:
        print("    ! 多语言异常：可能出现「英文 Brief 产出中文」或字数口径用错")
    return ok


def check_video() -> bool:
    """校验视频脚本：需要判断、结构化产出、时间轴。

    两个容易做假的点：① 图文渠道也硬塞一份脚本（噪声）；② 脚本只有标题没有分镜/口播
    （无法开拍）。因此断言既检查「该出的出、不该出的不出」，也检查结构完整性。
    """
    from app.knowledge.video import needs_video_script, required_sections, script_skeleton, video_spec

    print("\n[视频脚本]")

    cases = [
        ("抖音", ["短视频脚本 1 支"], "短视频脚本（黄金 3 秒钩子 + 分镜 + 口播 + 字幕）", True),
        ("TikTok", ["1 video script"], "short vertical video", True),
        ("小红书", ["图文笔记 1 篇"], "图文笔记（封面 + 6-9 张图 + 正文）", False),
        ("公众号", ["长图文 1 篇"], "长图文", False),
        ("小红书", ["短视频脚本 1 支"], "图文笔记", True),
    ]
    decide_ok = True
    for channel, deliverables, fmt, expected in cases:
        need, _ = needs_video_script(channel=channel, deliverables=deliverables, channel_format=fmt)
        mark = "✓" if need == expected else "✗"
        if need != expected:
            decide_ok = False
        print(f"    {mark} {channel:<8}{str(deliverables[0])[:14]:<16}→ 需要脚本={need}（期望 {expected}）")

    spec = video_spec("抖音")
    shots = script_skeleton("抖音")
    timeline_ok = bool(shots) and [s["start_second"] for s in shots] == sorted(
        s["start_second"] for s in shots
    )
    covered = sum(int(s["duration_seconds"]) for s in shots)
    coverage_ok = abs(covered - spec.duration_seconds) <= 2
    print(f"    抖音规格：{spec.duration_seconds}s {spec.aspect_ratio} {spec.shot_count} 镜"
          f"｜骨架 {len(shots)} 镜｜时间轴有序={timeline_ok}｜时长覆盖={covered}s")
    print(f"    必备段落：{'、'.join(required_sections())}")

    if not decide_ok:
        print("    ! 需要判断异常：图文渠道会被塞入无关脚本，或短视频渠道漏出脚本")
    if not (timeline_ok and coverage_ok):
        print("    ! 分镜骨架异常：时间轴无序或时长与目标不匹配")
    return decide_ok and timeline_ok and coverage_ok


def check_multimodal() -> bool:
    """校验 Brief 素材（多模态输入）链路。

    素材是最容易出现「看起来支持、实际不可用」的能力，因此这里逐层验证：

    ① 图片画幅来自**文件头实测**（PNG/JPEG/GIF/WebP/BMP），不是猜的或模型说的；
    ② 本地素材只认 ``assets/`` 下的相对路径，越界/缺失如实报错而非静默消失；
    ③ 离线路径下素材清单仍进提示词（否则智能体根本不知道有素材），并如实写明
       「本模型不读图」；只有打开 ``LLM_VISION`` 且提供方非离线时才真的有图片块；
    ④ 带素材的任务能在离线链路上跑完——素材不能成为创作链路的新失败面。
    """
    import base64
    import struct
    import time

    from app.config import ASSETS_DIR, get_config
    from app.core.assets import assets_prompt_block, image_size, parse_assets, vision_parts
    from app.core.types import Brief, BriefAsset

    print("\n[Brief 素材 / 多模态输入]")

    png = (
        b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR"
        + struct.pack(">II", 1200, 1600) + b"\x08\x06\x00\x00\x00"
    )
    gif = b"GIF89a" + struct.pack("<HH", 640, 480) + b"\x00" * 4
    bmp = b"BM" + b"\x00" * 16 + struct.pack("<ii", 800, 600) + b"\x00" * 4
    webp = (
        b"RIFF" + b"\x00" * 4 + b"WEBP" + b"VP8X" + (10).to_bytes(4, "little")
        + b"\x00" * 4 + (1199).to_bytes(3, "little") + (1599).to_bytes(3, "little") + b"\x00" * 4
    )
    jpeg = (
        b"\xff\xd8\xff\xe0" + struct.pack(">H", 16) + b"\x00" * 14
        + b"\xff\xc0" + struct.pack(">H", 17) + b"\x08"
        + struct.pack(">HH", 900, 720) + b"\x03" + b"\x00" * 6
    )
    expected = {"PNG": (png, (1200, 1600)), "GIF": (gif, (640, 480)), "BMP": (bmp, (800, 600)),
                "WebP": (webp, (1200, 1600)), "JPEG": (jpeg, (720, 900))}
    header_ok = True
    for name, (data, size) in expected.items():
        actual = image_size(data)
        mark = "✓" if actual == size else "✗"
        if actual != size:
            header_ok = False
        print(f"    {mark} {name:<5}文件头 -> {actual}（期望 {size}）")
    unknown_ok = image_size(b"not an image") is None
    print(f"    {'✓' if unknown_ok else '✗'} 未知格式不猜尺寸（返回 None）")

    # 真实写一个本地素材，验证「可读 + 内联 + 实测算幅」
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    local = ASSETS_DIR / "doctor_asset.png"
    local.write_bytes(png)
    raw = [
        "doctor_asset.png",
        {"kind": "image", "ref": "https://cdn.example.com/p.jpg", "title": "远程图", "note": "参考"},
        {"kind": "image", "ref": "../../etc/passwd", "title": "越界"},
        {"kind": "image", "ref": "C:/Windows/win.ini", "title": "绝对路径"},
        {"kind": "image", "ref": "不存在的图.png", "title": "缺失"},
        {"kind": "image", "ref": "data:image/gif;base64," + base64.b64encode(gif).decode()},
        {"kind": "video", "ref": "https://cdn.example.com/a.mp4", "title": "花絮"},
        "   ",
    ]
    assets = parse_assets(raw)
    local_ok = assets[0].width == 1200 and assets[0].data_url.startswith("data:image/png;base64,")
    guard_ok = all(assets[i].issue for i in (2, 3, 4)) and not assets[1].data_url
    kinds_ok = assets[5].kind == "image" and assets[6].kind == "video"
    blank_dropped = len(assets) == 7
    print(
        f"    {'✓' if local_ok else '✗'} 本地素材：实测算幅 {assets[0].width}×{assets[0].height}"
        f"（{assets[0].aspect_ratio}）｜已内联为 data URL"
    )
    print(f"    {'✓' if guard_ok else '✗'} 路径防护：越界/绝对路径/缺失 分别如实报错")
    print(f"    {'✓' if kinds_ok and blank_dropped else '✗'} 形态识别：data URL=图片、mp4=视频｜空白行已剔除")
    for index in (2, 3, 4):
        print(f"      - {assets[index].title}：{assets[index].issue}")

    cfg = get_config()
    block = assets_prompt_block(raw)
    honest_offline = "本模型不读图" in block and "1200×1600（3:4）" in block
    no_parts_offline = vision_parts(raw) == []
    print(f"    {'✓' if honest_offline else '✗'} 离线提示词：素材清单 {len(assets)} 条进提示词并写明「本模型不读图」")
    print(f"    {'✓' if no_parts_offline else '✗'} 离线不发图片块（避免污染 token 口径与黄金基线）")

    saved = (cfg.llm.provider, cfg.llm.base_url, cfg.llm.api_key, cfg.llm.vision)
    try:
        cfg.llm.provider, cfg.llm.base_url, cfg.llm.api_key = "openai", "http://localhost:9/v1", "doctor"
        cfg.llm.vision = True
        parts = vision_parts(raw, limit=2)
        vision_ok = len(parts) == 2 and parts[0].url.startswith("data:image/png;base64,") and parts[1].url.startswith("http")
        honest_online = "多模态形式提供" in assets_prompt_block(raw)
        print(f"    {'✓' if vision_ok else '✗'} 打开 LLM_VISION：取到 {len(parts)} 张图块（本地=data URL，远程=原地址）")
        print(f"    {'✓' if honest_online else '✗'} 提示词随能力变化（读图时不再声称「不读图」）")
    finally:
        cfg.llm.provider, cfg.llm.base_url, cfg.llm.api_key, cfg.llm.vision = saved

    # 工具层：A8 的 asset_spec_check 必须给出实测画幅与渠道规格的比对
    from app.agents.base import AgentRunContext
    from app.tools.a8_art_director import asset_spec_check

    brief = Brief(
        brand="自检品牌", product="自检产品", channel="小红书", industry="消费品",
        assets=[BriefAsset(kind="image", ref="doctor_asset.png", title="主图素材")],
    )
    ctx = AgentRunContext(
        task_id="doctor-assets", brief=brief, phase="VISUAL_ADAPT", revision=0,
        feedback=[], upstream={}, next_version=lambda _t: 1, emit=lambda *a, **k: None,
    )
    outcome = asset_spec_check(ctx)
    tool_ok = "1200×1600" in outcome.detail and "3:4" in outcome.detail
    print(f"    {'✓' if tool_ok else '✗'} A8 asset_spec_check：{outcome.summary}")

    # 端到端：带素材的任务在离线链路上仍要跑完
    from app.core.orchestrator import orchestrator
    from app.core.store import task_store

    task = orchestrator.create_task(brief, auto_approve=True)
    deadline = time.monotonic() + 60
    current = None
    while time.monotonic() < deadline:
        current = task_store.get(task.id)
        if current is not None and current.status in ("completed", "rejected", "failed", "cancelled"):
            break
        time.sleep(0.2)
    e2e_ok = current is not None and current.status == "completed"
    print(
        f"    {'✓' if e2e_ok else '✗'} 带素材的任务端到端：终态="
        f"{getattr(current, 'status', 'timeout')}｜产物={len(getattr(current, 'artifacts', []) or [])}"
    )

    local.unlink(missing_ok=True)
    ok = all([header_ok, unknown_ok, local_ok, guard_ok, kinds_ok, blank_dropped,
              honest_offline, no_parts_offline, vision_ok, honest_online, tool_ok, e2e_ok])
    if not ok:
        print("    ! 素材链路异常：可能静默丢弃素材、把离线路径当读图，或尺寸是猜的")
    return ok


def check_compute() -> bool:
    """校验确定性计算沙箱与换算工具。

    这层最容易「看起来能算、其实能执行代码」，因此分四段验证：

    ① **越权语法一律拦截**：导入、属性访问、下标、推导式、lambda、字符串运算、
       超大幂次、除零、未定义变量——白名单按 AST 节点类型判定，逐条实测；
    ② 公式求值与工具换算**可复现**（同一输入必得同一结果，不是模型心算）；
    ③ 排期时刻由共享的时段解析产生，口径与发布网关一致；
    ④ 回填数据能换算成派生指标并与预估对照；**算不出的项如实列出**而不是补数。
    """
    from datetime import datetime, timezone

    from app.agents.base import AgentRunContext
    from app.core.publisher import parse_slot_time
    from app.core.sandbox import SandboxError, apply_formula, evaluate
    from app.core.types import Brief
    from app.tools.base import tool_catalog
    from app.tools.compute import (
        actuals_audit,
        brief_keyword_audit,
        funnel_sensitivity,
        publish_timeline,
    )

    print("\n[确定性计算沙箱]")

    escapes = [
        "__import__('os').system('echo x')",
        "open('x')",
        "(1).__class__",
        "[x for x in (1, 2)]",
        "lambda: 1",
        "1 if 2 else 3",
        "'a' + 'b'",
        "2 ** 99",
        "1 / 0",
        "no_such_var",
    ]
    unblocked = []
    for expression in escapes:
        try:
            evaluate(expression, {"x": 1})
        except SandboxError:
            continue
        unblocked.append(expression)
    print(
        f"    {'✓' if not unblocked else '✗'} 越权语法拦截："
        f"{len(escapes) - len(unblocked)}/{len(escapes)} 条被拒"
        + (f"｜漏网：{unblocked}" if unblocked else "（导入/属性/下标/推导式/字符串/超大幂次/除零）")
    )
    math_ok = (
        abs(evaluate("impressions * ctr", {"impressions": 10000, "ctr": 0.03}) - 300) < 1e-9
        and abs(apply_formula("growth", {"current": 6, "previous": 4}) - 0.5) < 1e-9
        and abs(evaluate("min(3, 4) + max(1, 2)") - 5) < 1e-9
    )
    print(f"    {'✓' if math_ok else '✗'} 公式表求值可复现（clicks / growth / 白名单函数）")

    def ctx_of(brief: Brief, upstream: dict | None = None) -> AgentRunContext:
        return AgentRunContext(
            task_id="doctor-compute",
            brief=brief,
            phase="PLANNING",
            revision=0,
            feedback=[],
            upstream=upstream or {},
            next_version=lambda _t: 1,
            emit=lambda *a, **k: None,
        )

    brief = Brief(
        brand="自检品牌", product="自检产品", channel="小红书", industry="消费品",
        keywords=["冷萃咖啡", "0糖", "办公室提神"],
    )

    funnel = funnel_sensitivity(ctx_of(brief))
    rows = funnel.data.get("funnel") or []
    funnel_ok = bool(rows) and rows[0]["clicks"] == [300, 800] and rows[0]["engagement"] == [400, 1000]
    print(
        f"    {'✓' if funnel_ok else '✗'} 曝光档位换算：曝光 1 万 → 点击 "
        f"{rows[0]['clicks'][0] if rows else '?'}–{rows[0]['clicks'][1] if rows else '?'}"
        f"（CTR 3.0%–8.0%，实测值）"
    )

    def hour_minute(iso: str) -> tuple[int, int]:
        moment = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(timezone.utc)
        return moment.hour, moment.minute

    timeline = publish_timeline(ctx_of(brief))
    items = timeline.data.get("items") or []
    stamps = [item["due_at"] for item in items]
    timeline_ok = (
        bool(items)
        and all(hour_minute(item["due_at"]) == parse_slot_time(item["slot"]) for item in items)
        and stamps == sorted(stamps)
    )
    print(
        f"    {'✓' if timeline_ok else '✗'} 排期推算：{len(items)} 个时刻，"
        f"HH:MM 与时段解析一致且时间严格递增"
    )

    audit = actuals_audit(
        ctx_of(
            brief,
            {
                "actuals": {
                    "window": "发布后 72 小时", "exposure": "12.8 万", "clicks": "6,400",
                    "interactions": "9000", "conversions": "320",
                },
                "predicted": {"ctr": {"low": 3.0, "mid": 5.5, "high": 8.0, "unit": "%"}},
            },
        )
    )
    derived = audit.data.get("derived") or {}
    actuals_ok = (
        abs(derived.get("ctr", 0) - 0.05) < 1e-9
        and audit.data.get("raw", {}).get("exposure") == 128000
        and (audit.data.get("comparison") or {}).get("verdict") == "落在预估区间内"
    )
    print(
        f"    {'✓' if actuals_ok else '✗'} 回填换算："
        f"「12.8 万」→ 曝光 {audit.data.get('raw', {}).get('exposure')}、"
        f"CTR {derived.get('ctr', 0) * 100:.2f}%、"
        f"结论 {(audit.data.get('comparison') or {}).get('verdict')}"
    )

    partial = actuals_audit(ctx_of(brief, {"actuals": {"exposure": "10000"}}))
    partial_ok = partial.data.get("missing") == ["点击", "互动", "转化"] and not partial.data.get("derived")
    print(
        f"    {'✓' if partial_ok else '✗'} 算不出的项如实列出："
        f"仅填曝光时缺失项 ={partial.data.get('missing')}（未补估算值）"
    )

    draft = {
        "recommended_version": "V1",
        "versions": [
            {
                "id": "V1", "title": "冷萃咖啡的日常", "body": "一段正文",
                "cta": "去看看", "hashtags": ["#0糖"],
            }
        ],
    }
    keyword = brief_keyword_audit(ctx_of(brief, {"draft": draft}))
    keyword_ok = keyword.data.get("missing") == ["办公室提神"] and keyword.data.get("tag_only") == ["0糖"]
    print(
        f"    {'✓' if keyword_ok else '✗'} 关键词布局核对：缺失={keyword.data.get('missing')}｜"
        f"仅标签命中={keyword.data.get('tag_only')}"
    )

    catalog = tool_catalog()
    names = {item["name"] for item in catalog}
    catalog_ok = len(catalog) == 49 and {
        "funnel_sensitivity", "publish_timeline", "actuals_audit", "brief_keyword_audit", "site_monitor"
    } <= names
    print(f"    {'✓' if catalog_ok else '✗'} 工具注册表：共 {len(catalog)} 个工具（含 4 个确定性计算工具与站点监控）")

    ok = all([not unblocked, math_ok, funnel_ok, timeline_ok, actuals_ok, partial_ok, keyword_ok, catalog_ok])
    if not ok:
        print("    ! 计算层异常：可能沙箱白名单有漏、换算不可复现，或缺失项被补成了估算值")
    return ok


def _mask_url_for_print() -> str:
    """掩码后的目标库连接串（绝不回显明文密码）。"""
    from app.core.pg import database_url, mask_url

    return mask_url(database_url())


def main() -> int:
    storage_mode = (os.environ.get("CREATOR_STORAGE") or "file").strip().lower()
    if storage_mode == "pg" and (os.environ.get("CREATOR_DOCTOR_PG") or "") != "1":
        print("PG 存储模式下，自检会把测试数据写入 CREATOR_DATABASE_URL 指向的数据库。")
        print("如确认要跑：$env:CREATOR_DOCTOR_PG='1' 后重试（默认 file 模式无需配置）。")
        return 1
    data_dir = isolate_data_dir()
    # 必须在导入 app.* 之前固定提供方：config 在 import 时读取环境变量
    provider = force_offline_provider()
    check_python()
    print(f"data dir    : {data_dir}")
    print(f"存储层      : {storage_mode}" + (
        f"（{_mask_url_for_print()}）" if storage_mode == "pg" else "（JSON + SQLite，零依赖）"
    ))
    print(f"LLM 提供方  : {provider}（自检固定离线，保证秒级可复现；真实链路用 scripts/real_check.py）")
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
        video = check_video()
        traced = check_tracing(brief, chain)
        otlp = check_otel(brief)
        propagated = check_trace_propagation()
        dhuman = check_digital_human()
        multimodal = check_multimodal()
        computed = check_compute()
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
        "视频脚本": video,
        "调用轨迹追踪": traced,
        "OTLP 导出链路": otlp,
        "W3C 传播与采样": propagated,
        "数字人渲染样例": dhuman,
        "Brief 素材（多模态）": multimodal,
        "确定性计算沙箱": computed,
    }
    print()
    if all(checks.values()):
        checkpoint_label = (
            "SQLite 检查点可用（断点续跑有效）"
            if storage_mode == "file"
            else "PostgresSaver 检查点可用（断点续跑有效，多副本可共享）"
        )
        print(
            "自检通过：环境可用；Mock 引擎可在返工 1 轮后收敛；"
            "记忆库 RAG（关键词 + 向量混合）闭环成立且按租户隔离；发布排期具备自动投递的时间基础；"
            f"鉴权租户解析正确；{checkpoint_label}；离线评估器可复现且对合规风险敏感；"
            "黄金数据集回归判定器能正确识别回退、失败与门禁强度下降；"
            "调用轨迹 span 树成型、层级正确且可按 OTel 形状导出；"
            "OTLP 导出链路 id 一致、层级与智能体归属保留；"
            "W3C traceparent 可跨进程延续、采样只作用于导出面；"
            "数字人渲染样例的清单、惰性推进、租户隔离与失败路径均符合预期；"
            "Brief 素材的画幅为文件头实测值、本地素材路径受限于 assets/ 目录、"
            "离线链路如实声明「不读图」且带素材的任务仍能跑完；"
            "确定性计算沙箱在受限语法内可复现求值、越权语法一律拦截，"
            "量级换算/排期推算/回填核算均给出可复核的结果与来源。"
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
    if not video:
        print("  · 视频脚本异常，请检查 app/knowledge/video.py 的判断与分镜骨架。")
    if not traced:
        print("  · 追踪异常，请检查 app/core/tracing.py 与编排层的 span 埋点。")
    if not otlp:
        print("  · OTLP 导出异常，请检查 app/core/otel.py（或先 pip install -r requirements.txt）。")
    if not propagated:
        print("  · 传播/采样异常，请检查 app/core/tracing.py 的 parse_traceparent / decide_sampling。")
    if not dhuman:
        print("  · 数字人样例异常，请检查 app/core/digital_human.py 的清单构建与状态机。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
