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
19. 校验 **素材研读与长文分篇**：kind=document 素材的 map-reduce 研读
    （产物形状、要点取自原文、问题如实入档）、无文档时零调用（基线不受影响）、
    A4 字数区间检测与 ``A4.copy.version`` 单版本契约
20. 校验 **影视制作工厂**：60 岗位注册表与部门归档、五种预设展图并过静态体检
    （环路/端口不匹配/必需交付物可定位）、拓扑排序与增量缓存（二跑全命中）、
    离线通道真落盘（PNG/WAV 等标准库产物）且生成岗位自报 ``simulated``、
    异步作业生命周期（受理→后台线程→轮询）、未知目标点名拒绝、岗位边界取消
    （cancelling→cancelled、剩余岗位标 cancelled）、增量缓存提前落盘、重启后遗留作业回收、
    交付包 zip（manifest/README/sha256，重复打包覆盖同一份，无产物/跨租户/运行中拒绝）、
    跨租户不可读写、产物路径越界拒绝与项目删除回收

检查项的**计数会随合并/拆分变化**（第十轮新增「视频脚本」时是 17 项；第十二轮把
传播/采样与数字人样例并入既有项后回到 16 项；随后新增「Brief 素材（多模态）」为 17 项，
本轮新增「确定性计算沙箱」为 18 项、素材研读为 19 项），因此文档统一写「19 项」，
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

    # 与 check_otel 同一个坑：`duration_ms` 一算好就能被这里读到，而 `_export` 的
    # 序列化+落盘还在后面（十几万字的 JSON 还要改名），不等一下就偶发看到「文件不存在」的假失败。
    export_deadline = _time.time() + 10
    while _time.time() < export_deadline and not (EXPORT_DIR / f"{task.id}.json").exists():
        _time.sleep(0.05)

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


def check_imagegen() -> bool:
    """校验图片生成样例：清单、惰性推进、租户隔离、未配置网关显式失败与回收。

    与数字人同一原则：sample 零依赖可回归；openai/local 未配端点时**显式失败**，
    绝不假装成功；无 visual_brief 时拒绝创建（路由层据此转 409）。
    """
    from datetime import datetime, timedelta, timezone

    from app.core.imagegen import (
        build_manifest,
        create_job,
        drop_task,
        get_job,
        list_jobs,
    )
    from app.core.types import Artifact, TaskRecord

    print("\n[图片生成样例]")

    visual = {
        "image_prompts": [
            {"id": "IMG1", "usage": "封面", "scene": "夜色霓虹", "prompt": "赛博朋克城市封面", "negative": "模糊", "aspect_ratio": "16:9"},
            {"id": "IMG2", "usage": "B-roll", "scene": "战斗", "prompt": "主角挥剑特写", "aspect_ratio": "16:9"},
            {"id": "IMG3", "usage": "分镜", "scene": "空镜", "prompt": "", "aspect_ratio": "16:9"},
        ]
    }
    manifest = build_manifest(visual)
    manifest_ok = (
        len(manifest["segments"]) == 2
        and any("没有 prompt" in w for w in manifest["warnings"])
        and manifest["segments"][0]["prompt"] == "赛博朋克城市封面"
    )
    print(f"    生成清单：{len(manifest['segments'])} 张可生成｜告警 {len(manifest['warnings'])} 条｜{'正确' if manifest_ok else '异常'}")

    task = TaskRecord(
        id="doctor-img-1",
        tenant="default",
        artifacts=[Artifact(id="doctor-img-1-vb", task_id="doctor-img-1", type="visual_brief", content=visual)],
    )
    job = create_job(task, provider="sample")
    base = datetime.now(timezone.utc)
    queued = job["status"] == "queued"
    mid = list_jobs(task_id=task.id, now=base + timedelta(seconds=1))
    generating = len(mid) == 1 and mid[0]["status"] in ("queued", "generating")
    final = list_jobs(task_id=task.id, now=base + timedelta(seconds=60))
    done = (
        len(final) == 1
        and final[0]["status"] == "done"
        and final[0]["progress"] == 100
        and bool(final[0]["images"])
        and str(final[0]["images"][0]["url"]).startswith("sample://")
    )
    lifecycle_ok = queued and generating and done
    print(f"    样例引擎：排队={queued}｜推进={generating}｜完成出清单={done}")

    visible = get_job(job["id"], tenant="default") is not None
    leaked = get_job(job["id"], tenant="acme") is not None or bool(list_jobs(tenant="acme"))
    tenant_ok = visible and not leaked
    print(f"    租户隔离：本租户可见={visible}｜跨租户泄漏={leaked}")

    task2 = TaskRecord(
        id="doctor-img-2",
        tenant="acme",
        artifacts=[Artifact(id="doctor-img-2-vb", task_id="doctor-img-2", type="visual_brief", content=visual)],
    )
    job2 = create_job(task2, provider="openai")
    http_ok = (
        job2["status"] == "failed"
        and "未配置" in str(job2["error"])
        and "openai" in str(job2["error"])
    )
    print(f"    openai（未配端点）：status={job2['status']}｜error={str(job2['error'])[:40]}…")

    # openai 只返回 b64_json（无 url）时应落盘 assets、不丢图；直连 url 那么透传（真实缺陷回归）
    import base64 as _b64

    from app.config import ASSETS_DIR
    from app.core.imagegen import _openai_image

    fake_b64 = _b64.b64encode(b"\x89PNG\r\n fake-bytes-for-selfcheck").decode()
    persisted = _openai_image("doctor-img-b64", {"index": 1, "id": "IMGB"}, {"b64_json": fake_b64})
    written = ASSETS_DIR / persisted["url"].split("/", 1)[1] if persisted["url"].startswith("assets/") else None
    direct = _openai_image("doctor-img-b64", {"index": 2, "id": "IMGC"}, {"url": "https://cdn.example/x.png"})
    b64_ok = (
        persisted["url"].startswith("assets/")
        and persisted["b64"] is True
        and written is not None
        and written.exists()
        and direct["url"] == "https://cdn.example/x.png"
    )
    if written is not None and written.exists():
        written.unlink()
    print(f"    openai b64_json 落盘：b64→assets={persisted['url']}｜直连 url 透传={direct['url']}")

    guard_ok = False
    try:
        create_job(TaskRecord(id="doctor-img-3", tenant="default"), provider="sample")
    except ValueError:
        guard_ok = True
    print(f"    无 visual_brief 防护：拒绝创建={guard_ok}")

    cleanup_ok = drop_task(task.id) >= 1 and drop_task(task2.id) >= 1
    gone = not list_jobs(task_id=task.id) and not list_jobs(task_id=task2.id)
    cleanup_ok = cleanup_ok and gone
    print(f"    作业回收：recovered&清空={cleanup_ok}")

    ok = manifest_ok and lifecycle_ok and tenant_ok and http_ok and b64_ok and guard_ok and cleanup_ok
    if not ok:
        print("    ! 图片生成样例异常：请检查 app/core/imagegen.py 的状态机与存储。")
    return ok


def check_videogen() -> bool:
    """校验视频生成样例：复用分镜清单、惰性推进、未配置显式失败、成本熔断与回收。"""
    from datetime import datetime, timedelta, timezone

    from app.config import get_config
    from app.core.types import Artifact, TaskRecord
    from app.core.videogen import create_job, drop_task, get_job, list_jobs

    print("\n[视频生成样例]")

    script = {
        "channel": "B站",
        "aspect_ratio": "16:9",
        "duration_seconds": 120,
        "shots": [
            {"shot": 1, "role": "钩子", "start_second": 0, "duration_seconds": 60, "voiceover": "开场观点", "visual": "片头"},
            {"shot": 2, "role": "论证", "start_second": 60, "duration_seconds": 60, "voiceover": "分析", "visual": "游戏画面"},
        ],
    }
    task = TaskRecord(
        id="doctor-vid-1",
        tenant="default",
        artifacts=[Artifact(id="doctor-vid-1-script", task_id="doctor-vid-1", type="video_script", content=script)],
    )
    job = create_job(task, provider="sample")
    sample_ok = job["status"] == "queued" and job["shot_count"] == 2
    base = datetime.now(timezone.utc)
    final = list_jobs(task_id=task.id, now=base + timedelta(seconds=90))
    done = (
        len(final) == 1
        and final[0]["status"] == "done"
        and len(final[0]["segments_out"]) == 2
        and str(final[0]["segments_out"][0]["url"]).startswith("sample://")
    )
    lifecycle_ok = sample_ok and done
    print(f"    样例引擎：受理={sample_ok}｜按镜出画面段={done}")

    visible = get_job(job["id"], tenant="default") is not None
    leaked = get_job(job["id"], tenant="acme") is not None or bool(list_jobs(tenant="acme"))
    tenant_ok = visible and not leaked
    print(f"    租户隔离：本租户可见={visible}｜跨租户泄漏={leaked}")

    task2 = TaskRecord(
        id="doctor-vid-2",
        tenant="acme",
        artifacts=[Artifact(id="doctor-vid-2-script", task_id="doctor-vid-2", type="video_script", content=script)],
    )
    job2 = create_job(task2, provider="http")
    http_ok = job2["status"] == "failed" and "未配置" in str(job2["error"])
    print(f"    http（未配网关）：status={job2['status']}｜error={str(job2['error'])[:40]}…")

    # 成本熔断：配一个远端网关 + 极低预算 → 受理时直接 fail-loud，不提交、不假装
    cfg = get_config()
    saved_url, saved_budget = cfg.video_gen.api_url, cfg.cost_budget_usd
    try:
        cfg.video_gen.api_url = "https://remote-videogen.example/api"
        cfg.cost_budget_usd = 0.0001
        task3 = TaskRecord(
            id="doctor-vid-3",
            tenant="default",
            artifacts=[Artifact(id="doctor-vid-3-script", task_id="doctor-vid-3", type="video_script", content=script)],
        )
        job3 = create_job(task3, provider="http")
        fuse_ok = job3["status"] == "failed" and "成本熔断" in str(job3["error"])
    finally:
        cfg.video_gen.api_url = saved_url
        cfg.cost_budget_usd = saved_budget
    print(f"    成本熔断（超预算）：fail-loud={fuse_ok}")

    guard_ok = False
    try:
        create_job(TaskRecord(id="doctor-vid-4", tenant="default"), provider="sample")
    except ValueError:
        guard_ok = True
    print(f"    无 video_script 防护：拒绝创建={guard_ok}")

    cleanup_ok = drop_task(task.id) >= 1 and drop_task(task2.id) >= 1 and drop_task(task3.id) >= 1
    gone = not list_jobs(task_id=task.id) and not list_jobs(task_id=task2.id) and not list_jobs(task_id=task3.id)
    cleanup_ok = cleanup_ok and gone
    print(f"    作业回收：recovered&清空={cleanup_ok}")

    ok = lifecycle_ok and tenant_ok and http_ok and fuse_ok and guard_ok and cleanup_ok
    if not ok:
        print("    ! 视频生成样例异常：请检查 app/core/videogen.py 的状态机、成本口径与存储。")
    return ok


def check_video_understand() -> bool:
    """校验视频理解样例：sample 生命周期与占位骨架、租户隔离、real 三条诚实失败
    （未配网关 / 成本熔断 / 非本地或缺失视频）、无视频素材防护、本地路径边界与作业回收。

    real 通道现走**整集异步网关**（提交→轮询），不再依赖本机 ffmpeg / 多模态 LLM；
    故断言集中在「不触网、不假装看懂」的显式失败上（网关未配 / 预算 / 本地文件边界）。
    """
    from datetime import datetime, timedelta, timezone

    from app.config import get_config
    from app.core.types import Brief, BriefAsset, TaskRecord
    from app.core.videounderstand import (
        _estimate_cost,
        _resolve_video_path,
        create_job,
        drop_task,
        get_job,
        has_video_asset,
        list_jobs,
    )

    print("\n[视频理解样例]")

    def _vu_task(tid: str, tenant: str = "default") -> TaskRecord:
        brief = Brief(brand="测试", assets=[BriefAsset(kind="video", ref="doctor_vu.mp4", title="第一集")])
        return TaskRecord(id=tid, tenant=tenant, brief=brief)

    cfg = get_config()
    saved_api_url = cfg.video_understand.api_url
    saved_budget = cfg.cost_budget_usd
    try:
        cfg.video_understand.api_url = ""  # 默认样例态：无网关
        task = _vu_task("doctor-vu-1")
        job = create_job(task, provider="sample")
        sample_accepted = job["status"] == "queued" and job["provider"] == "sample"
        final = list_jobs(task_id=task.id, now=datetime.now(timezone.utc) + timedelta(seconds=90))
        summary = (final[0].get("summary") or {}) if final else {}
        sample_done = (
            len(final) == 1
            and final[0]["status"] == "done"
            and summary.get("simulated") is True
            and bool(summary.get("text_brief"))
            and "_issues" not in final[0]
        )
        lifecycle_ok = sample_accepted and sample_done
        print(f"    样例引擎：受理={sample_accepted}｜推进完成+占位骨架={sample_done}")

        visible = get_job(job["id"], tenant="default") is not None
        leaked = get_job(job["id"], tenant="acme") is not None
        tenant_ok = visible and not leaked
        print(f"    租户隔离：本租户可见={visible}｜跨租户泄漏={leaked}")

        # real + 无网关（默认态）→ 必须显式失败于「未配置」，绝不触网、绝不假装看懂
        real_mock = create_job(_vu_task("doctor-vu-2"), provider="real")
        gateway_fail = real_mock["status"] == "failed" and "未配置" in str(real_mock["error"])
        print(f"    real（未配网关）：status={real_mock['status']}｜error={str(real_mock['error'])[:34]}…")

        guard_ok = False
        try:
            create_job(TaskRecord(id="doctor-vu-3", tenant="default", brief=Brief(brand="测试")), provider="sample")
        except ValueError:
            guard_ok = True
        print(f"    无视频素材防护：拒绝创建={guard_ok}｜has_video_asset(有={has_video_asset(task)}/无={has_video_asset(TaskRecord(id='x', brief=Brief()))})")

        # 成本熔断：配一个非本地网关 URL + 极低预算 → 提交前熔断（按整集一次调用预估）
        cfg.video_understand.api_url = "https://vu-gateway.example.invalid/analyze"
        cfg.cost_budget_usd = 0.000001
        jf = create_job(_vu_task("doctor-vu-4"), provider="real")
        fuse_ok = jf["status"] == "failed" and "成本熔断" in str(jf["error"])
        print(f"    real 成本熔断：fail-loud={fuse_ok}｜error={str(jf['error'])[:34]}…")

        # 本地文件边界：正常预算但 assets/ 下无该文件 → 提交前失败、绝不触网
        cfg.cost_budget_usd = saved_budget
        jfg = create_job(_vu_task("doctor-vu-5"), provider="real")
        err5 = str(jfg["error"])
        file_guard = jfg["status"] == "failed" and ("视频文件" in err5 or "本地视频" in err5)
        print(f"    real 非本地/缺失视频防护：显式失败不触网={file_guard}")
    finally:
        cfg.video_understand.api_url = saved_api_url
        cfg.cost_budget_usd = saved_budget

    path_ok = _resolve_video_path("../secret.mp4")[0] is None and _resolve_video_path("nope.mp4")[0] is None
    est_ok = _estimate_cost("sample") == 0.0 and _estimate_cost("real") == 0.0
    print(f"    本地路径安全边界：越界/缺失拒绝={path_ok}｜sample 与无网关成本归零={est_ok}")

    touched = ["doctor-vu-1", "doctor-vu-2", "doctor-vu-4", "doctor-vu-5"]
    cleanup_ok = all(drop_task(t) >= 1 for t in touched)
    gone = all(not list_jobs(task_id=t) for t in touched)
    cleanup_ok = cleanup_ok and gone
    print(f"    作业回收：recovered&清空={cleanup_ok}")

    ok = (
        lifecycle_ok
        and tenant_ok
        and gateway_fail
        and guard_ok
        and fuse_ok
        and file_guard
        and path_ok
        and est_ok
        and cleanup_ok
    )
    if not ok:
        print("    ! 视频理解样例异常：请检查 app/core/videounderstand.py 的状态机、诚实失败与存储。")
    return ok


def check_surf() -> bool:
    """校验灵感冲浪（旁路作业铁律的义不容辞回归）：

    ① sample 端到端离线：受理→时间推进→产**占位灵感卡**（simulated=True）→入跨任务灵感
       池→注入简报文本命中；② 灵感池读/租户隔离/删除；③ browser 未装 Playwright / 未配白名单
       →**受理即诚实失败**并点名安装命令与 SURF_ALLOWED_DOMAINS；④ http 未配检索网关→诚实失败点名搜索配置；
    ⑤ **在线看视频**（真人式：点播放→播放中逐段截图→**一组一组**交 VLM→逐组小结→整段汇总）的纯逻辑：
       分组切帧与按调用次数估成本、评论去空去重截断、没开读图网关时**只截帧不交模型**（vision_skipped）、
       空帧诚实失败、sample 通道要求看视频时如实降级、指定 videoUrls 的会话入口；
    ⑥ 配了读图网关（以 stub 网关代真调用，离线可跑）：**每组一次多模态调用**、多组再**一次文本汇总**，
       逐组小结带播放时刻；真网关返回不可解析内容即 `SurfDigestError`，**绝不拿空摘要冒充看懂**；
    ⑦ **截图 OCR 兜底**（DOM 抓不到标题/简介/评论时截整页图逐字转写）：没读图网关时连图都不截、
       离线/降级给的「转写」一律不采信、真转写只填 DOM 空缺的字段（真实 DOM 文字优先）；
    ⑧ **真人式浏览**（用假浏览器离线跑通 browser 通道控制流）：随机节奏（滚动幅度与停留浮动、关掉后
       退回等距机器节奏、开新页前先犹豫一下而会话第一页不硬停）、先关遮罩再读、用**站点自己的搜索框**逐字打字搜主题（没搜索框就如实不搜）、
       点站点自己的「下一页/加载更多」**接着刷列表**（没这个控件、点了页面没变、页数用尽都如实停下并写进说明）、
       内容页先点开「展开全文」再读并**逐条抓评论区**（关掉开关就不抓，抓不到留空而不编造）、
       挑条目先**扫标题认主题词**（贴主题的排到前面并记下凭哪几个词点进去；整版都认不出就如实说明按列表顺序读的；
       要在线看的视频同样从贴主题的标题里挑）、搜不对题就**换个更短的说法再搜**（清空搜索框重打、只读贴主题那版结果，
       再没更短的说法就照旧读最后一版，搜过哪几版词全记在信号上）、
       打字时站点弹出的**搜索联想下拉**也照真人那样扫一眼：只点认得出本次主题词的那条（这一版关键词是
       **站点自己的叫法**，记在信号的 suggested 上；同分时保持站点给的顺序）、一条都认不出就照原词回车且
       如实说明「不为一条联想改主题」、联想项点不动就退回把词打回搜索框再回车、关掉开关连下拉都不读、
       这站根本没弹下拉就一个字都不提（绝不凭空造一条建议）、
       读到门口就**退出来**（认出登录墙/订阅墙/人机验证/维护出错页：墙上那几句话照记进摘录但标成 wall、
       不冒充正文也不绕过去，汇总说明有几页被挡，被挡的页不算「读过的页」；正文长到不像一扇门就不误判，
       关掉开关照旧把墙话当正文读）、
       **读过的不重读**（跨会话浏览历史：上几轮真读进去的条目这轮跳过、改读没见过的并说明跳了几条；
       整版都读过时照样按原顺序重读，且不许谎称「跳过了几条」；看过的视频不再重复点进去，
       但用户显式指定的链接照样看；历史只记真读到的条目页/视频页，按租户隔离、同一页再读只加遍数、
       超 SURF_HISTORY_MAX 先忘最旧的、关掉开关既不记也不跳、清掉即归零）、
       信息流多跳只点白名单内的同域条目（外站/内网/视频/图片直链一律不收）、
       起点站+搜索页+列表后续页+条目页+视频页**合计不越 SURF_MAX_PAGES**、浏览器在 finally 里一定关掉；
    ⑨ 作业回收与 doctor 残留清理（不污染真实灵感池）。
    """
    from datetime import datetime, timedelta, timezone

    from app.config import get_config
    from app.core.surf import (
        SurfUnavailable,
        create_session,
        drop_pool_card,
        drop_task,
        get_pool_card,
        list_pool,
        list_sessions,
        render_inspiration,
    )
    from app.core.types import Brief, TaskRecord

    print("\n[灵感冲浪]")

    cfg = get_config()
    saved_provider = cfg.surf.provider
    saved_pool_max = cfg.surf.pool_max
    future = datetime.now(timezone.utc) + timedelta(seconds=60)
    topic = "冲浪回归主题"
    sample_task_id = "doctor-surf-sample"
    browser_task_id = "doctor-surf-browser"
    http_task_id = "doctor-surf-http"
    watch_task_id = "doctor-surf-watch"
    urls_task_id = "doctor-surf-urls"
    created_card_ids: list[str] = []
    accepted = sample_done = render_ok = in_pool = pool_ok = False
    browser_fail = http_fail = False
    vu_ok = vu_empty = watch_degrade = bwatch = False
    chunk_ok = calls_ok = comments_ok = urls_ok = False
    ocr_ok = False
    ocr_no_shot_ok = ocr_placeholder_ok = ocr_real_ok = False
    group_calls_ok = no_fabricate_ok = False
    pace_ok = machine_ok = consent_ok = search_ok = close_ok = False
    hop_ok = budget_ok = boundary_ok = no_box_ok = hop_off_ok = watch_ok = False
    gap_ok = browse_gap_ok = paged_ok = tight_ok = stuck_ok = no_next_ok = False
    page_comments_ok = comments_off_ok = False
    try:
        cfg.surf.provider = "sample"
        # ① sample 端到端：受理为 queued → 时间推进到 done，产占位灵感并沉淀入池
        task = TaskRecord(id=sample_task_id, tenant="default", brief=Brief(brand="测试"))
        session = create_session(task, topic=topic, provider="sample")
        accepted = session["status"] == "queued" and session["provider"] == "sample"
        final = list_sessions(task_id=sample_task_id, tenant="default", now=future)
        done = final[0] if final else {}
        cards = done.get("cards") or []
        created_card_ids = [str(card["id"]) for card in cards]
        sample_done = (
            done.get("status") == "done"
            and bool(cards)
            and done.get("simulated") is True
            and all(card.get("simulated") for card in cards)
        )
        brief_text = render_inspiration(cards)
        render_ok = bool(cards) and brief_text.startswith("【灵感冲浪】") and bool(cards[0].get("idea"))
        in_pool = bool(cards) and all(
            get_pool_card(cid, tenant="default") is not None for cid in created_card_ids
        )
        print(
            f"    样例引擎：受理={accepted}｜推进完成+占位灵感={sample_done}｜"
            f"入池={in_pool}｜注入简报={render_ok}"
        )

        # ② 灵感池：主题命中、租户隔离、删除生效
        pool_hit = any(
            str(card.get("id")) in created_card_ids for card in list_pool(tenant="default", topic=topic)
        )
        cross_leak = any(
            str(card.get("id")) in created_card_ids for card in list_pool(tenant="acme")
        )
        removed = drop_pool_card(created_card_ids[0]) if created_card_ids else False
        gone = bool(created_card_ids) and get_pool_card(created_card_ids[0], tenant="default") is None
        pool_ok = pool_hit and not cross_leak and removed and gone
        print(
            f"    灵感池：主题命中={pool_hit}｜跨租户不泄漏={not cross_leak}｜删除生效={removed and gone}"
        )

        # ③ browser：未装 Playwright / 未配白名单→受理即诚实失败并点名（绝不假装浏览过）
        bsession = create_session(
            TaskRecord(id=browser_task_id, tenant="default", brief=Brief(brand="测试")),
            topic=topic,
            provider="browser",
            watch_videos=True,
        )
        berr = str(bsession.get("error") or "")
        browser_fail = bsession.get("status") == "failed" and (
            "playwright" in berr.lower()
            or "SURF_ALLOWED_DOMAINS" in berr
            or "SURF_SEED_SITES" in berr
        )
        print(f"    browser 诚实失败：status={bsession.get('status')}｜点名安装/配置={browser_fail}")

        # ④ http：未配检索网关→受理即诚实失败并点名配置（绝不假装上网冲过）
        hsession = create_session(
            TaskRecord(id=http_task_id, tenant="default", brief=Brief(brand="测试")),
            topic=topic,
            provider="http",
        )
        herr = str(hsession.get("error") or "")
        http_fail = hsession.get("status") == "failed" and (
            "WEB_SEARCH" in herr or "检索" in herr or "搜索" in herr
        )
        print(f"    http 诚实失败：status={hsession.get('status')}｜点名搜索配置={http_fail}")

        # ⑤ 在线看视频（不下载文件）：分组抽帧→逐组交 VLM→整段汇总的**纯逻辑**与诚实边界
        import base64 as _b64

        from app.core.surf import (
            _chunk_frames,
            _fill_missing_text,
            _group_frames,
            _normalize_comments,
            _understand_video,
            _video_calls_per_video,
        )

        frame = "data:image/png;base64," + _b64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii")
        # 帧带播放时刻与推进证据（真人「在看」的凭据），一组一组切给 VLM
        shots = [
            {"data_url": frame, "at": float(index * 2), "advancing": index > 0, "playing": True}
            for index in range(6)
        ]
        groups = _chunk_frames(shots, 2)
        chunk_ok = [len(item) for item in groups] == [2, 2, 2] and [
            group[0]["at"] for group in groups
        ] == [0.0, 4.0, 8.0]
        expect_groups = -(-cfg.surf.video_frames // _group_frames(cfg.surf))
        calls_ok = _video_calls_per_video(cfg.surf) == expect_groups + (
            1 if expect_groups > 1 else 0
        ) + (1 if cfg.surf.scrape_comments else 0)
        # 截图 OCR 兜底：只填 DOM 没抓到的字段，真实 DOM 文字一律优先、绝不被转写覆盖
        dom_meta = {
            "title": "DOM 抓到的标题",
            "description": "",
            "uploader": "",
            "views": "",
            "publish_date": "",
            "comments": [],
        }
        ocr_ok = _fill_missing_text(
            dom_meta,
            {
                "title": "OCR 转写的标题",
                "description": "OCR 转写的简介",
                "comments": [{"author": "甲", "text": "OCR 评论", "likes": "3"}, {"text": "  "}],
            },
            5,
        ) and (
            dom_meta["title"] == "DOM 抓到的标题"
            and dom_meta["description"] == "OCR 转写的简介"
            and [row["text"] for row in dom_meta["comments"]] == ["OCR 评论"]
            and dom_meta.get("comments_from_ocr") is True
        )
        kept = {"title": "", "comments": [{"author": "", "text": "DOM 评论", "likes": ""}]}
        ocr_ok = ocr_ok and not _fill_missing_text(kept, {"comments": [{"text": "OCR 评论"}]}, 5) and (
            kept["comments"][0]["text"] == "DOM 评论"
        )
        # 评论归一：去空、去重、按上限截断（DOM 直抓的评论是真实文字，绝不编造）
        rows = _normalize_comments(
            ["好看", "", {"author": "某人", "text": "好看", "likes": "12"}, {"text": "第三条"}], 5
        )
        comments_ok = [row["text"] for row in rows] == ["好看", "第三条"]
        # 没启用图像输入（LLM_VISION 默认关）时：抽了帧也**不交模型**，如实标 vision_skipped + simulated
        saved_vision = cfg.llm.vision
        cfg.llm.vision = False
        try:
            vu = _understand_video(
                shots,
                {"title": "回归视频", "description": "简介", "comments": rows},
                cfg.surf,
                {"frames": 6, "advancing_frames": 5, "watched_seconds": 10.0, "played": True},
            )
        finally:
            cfg.llm.vision = saved_vision
        vu_ok = (
            vu["simulated"] is True
            and bool(vu["visual_summary"])
            and vu["frames_used"] == 6
            and vu.get("vision_skipped") is True
            and vu["groups_used"] == 0
        )
        try:
            _understand_video([], {"title": "x"}, cfg.surf)
            vu_empty = False
        except SurfUnavailable:
            vu_empty = True
        wsession = create_session(
            TaskRecord(id=watch_task_id, tenant="default", brief=Brief(brand="测试")),
            topic=topic,
            provider="sample",
            watch_videos=True,
        )
        watch_degrade = wsession.get("watch_videos") is False and any(
            "看懂视频" in str(w) for w in wsession.get("warnings") or []
        )
        bwatch = bsession.get("watch_videos") is True
        # 指定视频链接（真人「就是要看这条」）：带 urls 即视为要在线看，且落进会话供审计
        usession = create_session(
            TaskRecord(id=urls_task_id, tenant="default", brief=Brief(brand="测试")),
            topic=topic,
            provider="browser",
            video_urls=["https://example.com/watch?v=1"],
        )
        urls_ok = usession.get("video_urls") == ["https://example.com/watch?v=1"] and bool(
            usession.get("watch_videos")
        )

        # ⑥ 配了读图网关时：**一组一组**交 VLM（每组一次多模态调用）→再一次纯文本汇总；
        #    真网关没产出可解析画面描述 = 诚实失败，绝不拿空摘要冒充看懂。
        import json as _json

        import app.core.surf as surf_mod
        from app.core.surf import SurfDigestError
        from app.llm.types import LLMResponse

        calls: list[tuple[str, int]] = []

        def _stub_chat(request):
            picked = max((len(msg.images or []) for msg in request.messages), default=0)
            calls.append((request.purpose, picked))
            payload = (
                {
                    "segment_summary": f"第 {len(calls)} 组画面：博主在演示产品",
                    "on_screen_text": "画面字幕文字",
                    "is_playing": True,
                    "confidence": "high",
                }
                if request.purpose.endswith("frames")
                else {
                    "visual_summary": "整段：开箱→上手试用→给出结论",
                    "on_screen_text": "画面字幕文字",
                    "comment_insights": "评论集中在价格与续航",
                    "confidence": "high",
                }
            )
            return LLMResponse(content=_json.dumps(payload, ensure_ascii=False), provider="openai")

        saved_chat, saved_vision_fn = surf_mod.chat, surf_mod.vision_enabled
        saved_group = cfg.surf.vlm_group_frames
        surf_mod.chat = _stub_chat
        surf_mod.vision_enabled = lambda *a, **k: True
        cfg.surf.vlm_group_frames = 2
        try:
            vr = _understand_video(
                shots,
                {"title": "回归视频", "description": "简介", "uploader": "UP主", "comments": rows},
                cfg.surf,
                {"frames": 6, "advancing_frames": 5, "watched_seconds": 10.0, "played": True},
            )
            group_calls_ok = (
                [purpose.split(".")[-1] for purpose, _ in calls]
                == ["frames", "frames", "frames", "merge"]
                and max(size for purpose, size in calls if purpose.endswith("frames")) == 2
                and vr["groups_used"] == 3
                and len(vr["segments"]) == 3
                and vr["merged"] is True
                and vr["simulated"] is False
                and vr["segments"][2]["from"] == 8.0
                and bool(vr["comment_insights"])
            )
            surf_mod.chat = lambda request: LLMResponse(
                content="模型跑题了，一个 JSON 都没给", provider="openai"
            )
            try:
                _understand_video(shots, {"title": "回归视频"}, cfg.surf, {"frames": 6})
                no_fabricate_ok = False
            except SurfDigestError:
                no_fabricate_ok = True
        finally:
            surf_mod.chat = saved_chat
            surf_mod.vision_enabled = saved_vision_fn
            cfg.surf.vlm_group_frames = saved_group

        # ⑦ 截图 OCR 兜底（DOM 抓不到标题/简介/评论时）：没读图网关**连截都不截**；
        #    离线/降级引擎给的「转写」一律不采信；真网关给的转写只填 DOM 空缺的字段。
        class _FakePage:
            """假页面：只用来验证「该不该截图、该不该采信」，一被越界调用就报错。"""

            def __init__(self, allow_shot: bool) -> None:
                self.allow_shot = allow_shot
                self.frames: list[str] = []
                self.mouse = self

            def wheel(self, *_args) -> None:
                pass

            def wait_for_timeout(self, _ms: int) -> None:
                pass

            def locator(self, _sel: str):
                raise LookupError("没有展开按钮")

            def screenshot(self, **_kwargs) -> bytes:
                if not self.allow_shot:
                    raise AssertionError("没有读图网关时不应该截任何一张图")
                return b"\x89PNG\r\n\x1a\n"

        ocr_no_shot_ok = surf_mod._ocr_page_text(_FakePage(False), cfg.surf) == {}
        saved_chat = surf_mod.chat
        saved_vision_fn = surf_mod.vision_enabled
        surf_mod.vision_enabled = lambda *a, **k: True
        try:
            surf_mod.chat = lambda request: LLMResponse(
                content=_json.dumps({"title": "OCR 标题", "comments": [{"text": "OCR 评论"}]}),
                provider="openai",
                simulated=True,
            )
            ocr_placeholder_ok = surf_mod._ocr_page_text(_FakePage(True), cfg.surf) == {}
            surf_mod.chat = lambda request: LLMResponse(
                content=_json.dumps(
                    {
                        "title": "OCR 标题",
                        "description": "OCR 简介",
                        "comments": [{"author": "甲", "text": "OCR 评论", "likes": "3"}],
                    },
                    ensure_ascii=False,
                ),
                provider="openai",
            )
            transcribed = surf_mod._ocr_page_text(_FakePage(True), cfg.surf)
            filled = {"title": "DOM 标题", "description": "", "uploader": "", "views": "", "publish_date": "", "comments": []}
            ocr_real_ok = bool(transcribed.get("comments")) and _fill_missing_text(filled, transcribed, 5) and (
                filled["title"] == "DOM 标题"
                and filled["description"] == "OCR 简介"
                and filled["comments"][0]["text"] == "OCR 评论"
            )
        finally:
            surf_mod.chat = saved_chat
            surf_mod.vision_enabled = saved_vision_fn
        print(
            f"    在线看视频：分组切帧={chunk_ok}｜成本按组估={calls_ok}｜评论归一={comments_ok}｜"
            f"OCR 只补空={ocr_ok}｜无视觉网关不假装看懂={vu_ok}｜空帧诚实失败={vu_empty}"
        )
        print(
            f"    看视频入口：sample 不假装={watch_degrade}｜browser 记录 watch={bwatch}｜"
            f"指定视频链接={urls_ok}"
        )
        print(
            f"    分组交 VLM：逐组调用后汇总={group_calls_ok}｜真网关空产出诚实失败={no_fabricate_ok}"
        )
        print(
            f"    截图 OCR 兜底：无读图网关不截图={ocr_no_shot_ok}｜离线转写不采信={ocr_placeholder_ok}｜"
            f"真转写只补 DOM 空缺={ocr_real_ok}"
        )

        # ⑧ 真人式浏览（随机节奏 / 站内搜索 / 列表翻页 / 信息流多跳 / 展开全文与评论区）：
        #    用**假浏览器**跑通 browser 通道的整条控制流（全程不联网、不装 Playwright），断言：
        #    滚动幅度与停留确有浮动且开新页前先犹豫、站内搜索走站点自己的搜索框并如实反映搜成没搜、
        #    翻页只点站点自己的「下一页/加载更多」（没控件、点了没变、页数用尽都如实停下）、
        #    多跳只点白名单内的同域条目、挑条目按标题认出主题词的排序（并记下凭哪几个词点进去）、
        #    访问页数一律不越 SURF_MAX_PAGES。
        import types as _types
        from types import SimpleNamespace as _Ns
        from urllib.parse import urlparse as _urlparse

        def _fake_public(url: str) -> str:
            """假 DNS：只按主机名判公网/内网，让 SSRF 拒绝口径在离线环境也能被验到。"""
            host = (_urlparse(url).hostname or "").lower()
            if host in ("localhost", "127.0.0.1", "10.0.0.1") or host.endswith(".local"):
                raise surf_mod.WebUnavailable(f"拒绝抓取内网/保留地址：{host}")
            return url

        class _FakeLoc:
            """假元素句柄：只有「本真有搜索框/遮罩按钮/下一页/展开全文」时才对相应选择器给出可点的结果。"""

            def __init__(self, page, sel: str) -> None:
                self.page = page
                self.sel = sel

            @property
            def first(self):
                return self

            def _hit(self) -> bool:
                if self.sel in surf_mod._SEARCH_INPUT_SELECTORS:
                    return self.page.has_box
                if self.sel in surf_mod._CONSENT_SELECTORS:
                    return self.page.has_consent
                if self.sel in surf_mod._NEXT_PAGE_SELECTORS:
                    return self.page.has_next
                if self.sel.startswith("text=") and self.sel[5:] in surf_mod._ARTICLE_EXPAND_LABELS:
                    return self.page.has_expand
                return False

            def count(self) -> int:
                if self.sel in surf_mod._SUGGESTION_SELECTORS:
                    self.page.sug_probes += 1
                    return len(self.page.suggestions)
                return 1 if self._hit() else 0

            def nth(self, index: int):
                return _FakeSuggestion(self.page, index)

            def is_visible(self) -> bool:
                return self._hit()

            def click(self, **_kwargs) -> None:
                if not self._hit():
                    raise AssertionError(f"不该点一个不存在的元素：{self.sel}")
                self.page.clicks.append(self.sel)
                if self.sel in surf_mod._NEXT_PAGE_SELECTORS:
                    self.page.advance_list()  # 真翻了下一页（地址与正文都变）
                elif self.sel.startswith("text="):
                    self.page.expanded += 1  # 展开了折叠的全文

            def press_sequentially(self, text: str, delay: int = 0) -> None:
                self.page.typed.append((text, delay))
                self.page.last_query = text  # 框里现在是什么词，回车就搜哪个词

            def fill(self, text: str) -> None:
                self.page.filled.append(text)
                self.page.last_query = text

            def press(self, key: str) -> None:
                self.page.keys.append(key)
                if key == "Enter" and self.page.results_url:
                    # 搜哪个词就跳到哪个词的结果页（同一个词重复搜还是同一个地址）
                    self.page._url = self.page.results_for(self.page.last_query)

            def inner_text(self) -> str:
                return ""

            def get_attribute(self, _name: str) -> str:
                return ""

        class _FakeSuggestion:
            """搜索框打字时站点弹出的**联想下拉里的一条**：读得到文字，点下去就跳那个词的结果页。

            `sug_click_fails=True` 模拟那种「只有样式、没有真实点击处理」的下拉——点不动，
            真人会把这个词抄进框里再回车，引擎也该这样退回，而不是假装点了。
            """

            def __init__(self, page, index: int) -> None:
                self.page = page
                self.index = index

            def inner_text(self, **_kwargs) -> str:
                return self.page.suggestions[self.index]

            def click(self, **_kwargs) -> None:
                if self.page.sug_click_fails:
                    raise AssertionError("联想项点不动（模拟没有点击处理的下拉）")
                word = self.page.suggestions[self.index]
                self.page.sug_clicked.append(word)
                self.page.last_query = word
                if self.page.results_url:
                    self.page._url = self.page.results_for(word)

        class _FakeSurfPage:
            """假浏览器页：把「访问了什么、滚了多少、打了什么字、翻了第几页」全部记账，供事后核对。"""

            def __init__(
                self,
                *,
                has_box,
                links,
                results_url,
                has_consent=True,
                has_next=False,
                next_changes=True,
                has_comments=False,
                has_expand=False,
                result_links=None,
                suggestions=(),
                sug_click_fails=False,
                body_text="",
            ) -> None:
                self.has_box = has_box
                self.has_consent = has_consent
                self.has_next = has_next
                self.next_changes = next_changes
                self.has_comments = has_comments
                self.has_expand = has_expand
                self.links = list(links)
                self.results_url = results_url
                self.result_links = result_links  # 每版搜索词对应的结果页条目（None=结果页照旧显示 links）
                self.suggestions = list(suggestions)  # 打字时站点弹出的联想下拉（空 = 这站根本没有联想层）
                self.sug_click_fails = sug_click_fails
                self.body_text = body_text  # 指定页面正文（用来模拟「读到门口就被挡住」那种短页）
                self.sug_clicked: list[str] = []  # 真点过哪几条联想项（按点下去的顺序）
                self.sug_probes = 0  # 探过几次联想下拉（关掉开关时必须一次都没有）
                self.last_query = ""  # 搜索框里现在挂着哪个词
                self.query_urls: dict[str, str] = {}
                self.frames: list = []
                self.visited: list[str] = []
                self.clicks: list[str] = []
                self.typed: list[tuple[str, int]] = []
                self.filled: list[str] = []
                self.keys: list[str] = []
                self.deltas: list[int] = []
                self.waits: list[int] = []
                self._url = "about:blank"
                self._base_url = ""
                self.page_index = 1
                self.expanded = 0
                self.mouse = self

            @property
            def url(self) -> str:
                return self._url

            def goto(self, url: str, **_kwargs) -> None:
                self.visited.append(url)
                self._url = url
                self._base_url = url
                self.page_index = 1

            def advance_list(self) -> None:
                """点「下一页」：`next_changes=False` 模拟那种点了没反应的控件（页面一律不许变样）。"""
                if not self.next_changes:
                    return
                self.page_index += 1
                self._url = f"{self._base_url}?page={self.page_index}"

            def inner_text(self, _sel: str) -> str:
                if self.body_text:  # 指定了就用它当正文（模拟登录墙/订阅墙那种「短到像一扇门」的页面）
                    return self.body_text
                return (
                    f"正文 {self._url}｜列表第 {self.page_index} 页"
                    + "｜折叠起来的全文内容。" * self.expanded
                )

            def title(self) -> str:
                return "页面标题"

            def wait_for_timeout(self, ms: int) -> None:
                self.waits.append(int(ms))

            def wait_for_load_state(self, _state: str, **_kwargs) -> None:
                pass

            def wheel(self, _dx: int, dy: int) -> None:
                self.deltas.append(int(dy))

            def locator(self, sel: str):
                return _FakeLoc(self, sel)

            def eval_on_selector_all(self, _sel, _js, *_args):
                return [{"text": text, "href": href} for text, href in self.current_links]

            def evaluate(self, script, _arg=None):
                if script is surf_mod._LINK_ITEMS_JS:
                    return [{"text": text, "href": href} for text, href in self.current_links]
                if script is surf_mod._COLLECT_COMMENTS_JS:
                    return list(self._FAKE_COMMENTS) if self.has_comments else []
                return []

            @property
            def current_links(self) -> list:
                """结果页显示的是**这一版关键词**搜出来的条目，信息流页还是那几条 feed links。"""
                if not self.result_links or not self.results_url or not self._url.startswith(
                    self.results_url
                ):
                    return self.links
                index = max(0, min(len(self.result_links) - 1, len(self.typed) - 1))
                return self.result_links[index]

            def results_for(self, query: str) -> str:
                """搜哪个词就有一个属于那个词的结果页地址（同一个词重复搜仍是同一个地址）。"""
                if query not in self.query_urls:
                    step = len(self.query_urls)
                    self.query_urls[query] = (
                        self.results_url if step == 0 else f"{self.results_url}&r={step}"
                    )
                return self.query_urls[query]

            _FAKE_COMMENTS = [
                {"author": "网友甲", "text": "这个我之前踩过坑，实测更靠谱", "likes": "128"},
                {"author": "网友乙", "text": "反对 up 主的结论，价格差太多了", "likes": "77"},
                {"author": "", "text": "求链接", "likes": ""},
            ]

        class _FakeBrowser:
            def __init__(self, page) -> None:
                self.page = page
                self.closed = False
                self.headless: bool | None = None

            def new_page(self):
                return self.page

            def close(self) -> None:
                self.closed = True

        def _install_fake_playwright(page):
            browser = _FakeBrowser(page)

            class _Chromium:
                def launch(self, headless=True):
                    browser.headless = headless
                    return browser

            class _FakePlaywright:
                def __init__(self) -> None:
                    self.chromium = _Chromium()

                def __enter__(self):
                    return self

                def __exit__(self, *_args):
                    return False

            sync_mod = _types.ModuleType("playwright.sync_api")
            sync_mod.sync_playwright = lambda: _FakePlaywright()
            pkg_mod = _types.ModuleType("playwright")
            pkg_mod.sync_api = sync_mod
            saved_modules = {
                "playwright": sys.modules.get("playwright"),
                "playwright.sync_api": sys.modules.get("playwright.sync_api"),
            }
            sys.modules["playwright"] = pkg_mod
            sys.modules["playwright.sync_api"] = sync_mod
            return browser, saved_modules

        feed_links = [
            ("白名单内第一条", "https://example.com/post/1"),
            ("白名单内第二条", "https://example.com/post/2"),
            ("白名单内第三条", "https://example.com/post/3"),
            ("外站条目", "https://other.com/post/x"),
            ("内网条目", "http://127.0.0.1/post"),
            ("视频条目", "https://example.com/video/888"),
            ("图片直链", "https://example.com/a.png"),
        ]
        # 真人挑条目是**扫标题**：同域之内先点标题认得出主题词的那几条，认不出主题的往后排
        focus_links = [
            ("本站热门推荐", "https://example.com/post/1"),
            ("露营装备避坑指南", "https://example.com/post/2"),
            ("本周编辑精选", "https://example.com/post/3"),
            ("测评：3 件帐篷实测", "https://example.com/post/4"),
        ]
        # 要在线看的视频也一样先扫标题：单会话只看 1 条时，看点哪条正是这一眼决定的
        video_links = [
            ("随便一个视频", "https://example.com/video/1"),
            ("露营装备实测视频", "https://example.com/video/2"),
        ]
        surf_cfg = _Ns(
            provider="browser",
            headless=True,
            allowed_domains=["example.com"],
            seed_sites=["https://example.com/feed"],
            topics=["露营装备测评"],
            max_pages=4,
            scroll_rounds=2,
            settle_ms=800,
            timeout_ms=5_000,
            queries_per_topic=1,
            cards_per_session=3,
            pool_max=50,
            auto_enabled=False,
            tick_seconds=3_600,
            watch_videos=False,
            video_frames=2,
            video_frame_interval_ms=100,
            vlm_group_frames=2,
            max_videos_per_session=1,
            scrape_comments=False,
            max_comments=5,
            crop_to_video=False,
            click_into_videos=False,
            human_pace=True,
            search_on_seeds=True,
            hops_per_page=2,
            list_pages=2,
            page_comments=False,
            page_gap_ms=1_500,
            topic_focus=True,
            search_retries=0,
            use_suggestions=True,
            suggestions_max=8,
            avoid_walls=True,
        )
        saved_public = surf_mod._ensure_public_url
        try:
            surf_mod._ensure_public_url = _fake_public

            def _run_surf(
                *,
                has_box: bool = True,
                results_url: str = "https://example.com/search?q=x",
                watch_videos: bool = False,
                cfg_used=None,
                has_next: bool = False,
                next_changes: bool = True,
                has_comments: bool = False,
                has_expand: bool = False,
                links=None,
                result_links=None,
                read_before: frozenset = frozenset(),
                video_urls: tuple = (),
                suggestions: tuple = (),
                sug_click_fails: bool = False,
                body_text: str = "",
            ):
                page = _FakeSurfPage(
                    has_box=has_box,
                    links=links or feed_links,
                    results_url=results_url,
                    has_next=has_next,
                    next_changes=next_changes,
                    has_comments=has_comments,
                    has_expand=has_expand,
                    result_links=result_links,
                    suggestions=suggestions,
                    sug_click_fails=sug_click_fails,
                    body_text=body_text,
                )
                browser, saved_modules = _install_fake_playwright(page)
                try:
                    got = surf_mod._gather_browser(
                        "露营装备测评",
                        cfg_used or surf_cfg,
                        watch_videos=watch_videos,
                        video_urls=video_urls,
                        read_before=read_before,
                    )
                finally:
                    for name, value in saved_modules.items():
                        if value is None:
                            sys.modules.pop(name, None)
                        else:
                            sys.modules[name] = value
                return got, page, browser

            browse_signals, browse_page, browse_browser = _run_surf()
            by_kind: dict[str, list[dict]] = {}
            for item in browse_signals:
                by_kind.setdefault(str(item.get("kind")), []).append(item)
            content = [item for item in browse_signals if item.get("kind") in ("page", "search", "item")]
            notes_text = "；".join(str(item.get("excerpt") or "") for item in by_kind.get("note", []))
            all_signal_urls = " ".join(str(item.get("url") or "") for item in browse_signals)
            pace_ok = (
                len(set(browse_page.deltas)) > 1
                and all(150 <= dy <= 3_000 for dy in browse_page.deltas)
                and len(set(browse_page.waits)) > 1
                and any(w != surf_cfg.settle_ms for w in browse_page.waits)
            )
            machine_ok = True
            saved_pace = cfg.surf.human_pace
            cfg.surf.human_pace = False
            try:
                from app.core.surf import _dwell as _dwell_plain, _pace_rng as _pace_rng_plain

                strict_rng = _pace_rng_plain(cfg.surf, "doctor")
                px_samples = {_dwell_plain(cfg.surf, strict_rng) for _ in range(6)}
                machine_ok = strict_rng is None and px_samples == {(1_200, max(0, cfg.surf.settle_ms))}
            finally:
                cfg.surf.human_pace = saved_pace
            search_ok = (
                bool(by_kind.get("search"))
                and by_kind["search"][0].get("via") == "search"
                and by_kind["search"][0]["url"] == browse_page.results_url
                and browse_page.typed
                and browse_page.typed[0][0] == "露营装备测评"
                and browse_page.typed[0][1] > 0
                and "Enter" in browse_page.keys
            )
            hop_ok = (
                len(by_kind.get("item", [])) == 2
                and all(item.get("via") == "feed" for item in by_kind.get("item", []))
                and [item.get("listed_as") for item in by_kind.get("item", [])]
                == ["白名单内第一条", "白名单内第二条"]
                and all("example.com" in str(item.get("url")) for item in by_kind.get("item", []))
                and {item["url"] for item in by_kind["item"]} <= set(browse_page.visited)
            )
            budget_ok = len(content) == surf_cfg.max_pages and "SURF_MAX_PAGES" in notes_text
            boundary_ok = (
                "other.com" not in all_signal_urls
                and "127.0.0.1" not in all_signal_urls
                and "/video/888" not in all_signal_urls
                and "a.png" not in all_signal_urls
                and "白名单外" in notes_text
            )
            consent_ok = any(sel in browse_page.clicks for sel in surf_mod._CONSENT_SELECTORS)
            close_ok = browse_browser.closed is True
            no_box_signals, no_box_page, _ = _run_surf(has_box=False, results_url="")
            no_box_ok = (
                not any(item.get("kind") == "search" for item in no_box_signals)
                and not no_box_page.typed
                and "搜索框" in "；".join(
                    str(item.get("excerpt") or "") for item in no_box_signals if item.get("kind") == "note"
                )
            )
            picks, offsite = surf_mod._pick_hops(
                [{"text": "外站条目", "href": "https://other.com/x"}],
                {"example.com"},
                2,
                home_url="https://example.com/feed",
                visited=set(),
            )
            hop_off_ok = picks == [] and offsite == 1
            # 看视频与浏览共用同一份页数预算：从信息流记下的视频链接要**点进去看**，
            # 假页面没有播放器 → 如实落 video_error（带入口 via=feed），绝不假装看懂
            watch_cfg = _Ns(
                **{
                    **vars(surf_cfg),
                    "hops_per_page": 0,
                    "click_into_videos": True,
                    "max_videos_per_session": 1,
                }
            )
            watch_signals, watch_page, _ = _run_surf(
                results_url="", watch_videos=True, cfg_used=watch_cfg
            )
            video_rows = [
                item for item in watch_signals if str(item.get("kind") or "").startswith("video")
            ]
            watch_ok = (
                bool(video_rows)
                and video_rows[0].get("via") == "feed"
                and video_rows[0].get("kind") == "video_error"
                and "https://example.com/video/888" in {item.get("url") for item in video_rows}
                and "https://example.com/video/888" in watch_page.visited
            )
            browse_ok = (
                bool(video_rows)
                and {item.get("url") for item in video_rows}
                == {"https://example.com/video/888"}
                and all(item.get("via") == "feed" for item in video_rows)
                and len(video_rows) == 1
                and sum(
                    1 for item in browse_signals if item.get("kind") != "note"
                )
                <= surf_cfg.max_pages
            )

            # ⑧b 列表翻页 / 展开全文 / 页面评论区 / 页间停顿：翻页翻的是站点自己的控件，
            #     做不到的（没控件、点了没反应、页数用尽）一律如实停下并写进说明，绝不假装多刷了页
            gap_samples = {
                surf_mod._gap_like_human(surf_cfg, surf_mod._pace_rng(surf_cfg, f"gap-{i}"))
                for i in range(8)
            }
            gap_ok = (
                len(gap_samples) > 1
                and all(
                    int(surf_cfg.page_gap_ms * 0.35) <= g <= min(20_000, int(surf_cfg.page_gap_ms * 1.8))
                    for g in gap_samples
                )
                and surf_mod._gap_like_human(surf_cfg, None) == surf_cfg.page_gap_ms
            )
            browse_gap_ok = (
                bool(content)
                and content[0].get("gap_ms") == 0  # 会话第一页没什么可犹豫的，不该硬停
                and all(int(item.get("gap_ms") or 0) > 0 for item in by_kind.get("item", []))
            )
            no_next_ok = not any(item.get("list_page") for item in browse_signals) and (
                "下一页" in notes_text
            )
            paged_cfg = _Ns(
                **{
                    **vars(surf_cfg),
                    "list_pages": 3,
                    "hops_per_page": 0,
                    "search_on_seeds": False,
                    "max_pages": 6,
                }
            )
            paged_signals, paged_page, _ = _run_surf(
                results_url="", has_next=True, cfg_used=paged_cfg
            )
            paged_rows = [item for item in paged_signals if item.get("kind") == "page"]
            paged_notes = "；".join(
                str(item.get("excerpt") or "")
                for item in paged_signals
                if item.get("kind") == "note"
            )
            paged_ok = (
                [item.get("list_page") for item in paged_rows] == [None, 2, 3]
                and all(item.get("paged_by") for item in paged_rows[1:])
                and any(sel in paged_page.clicks for sel in surf_mod._NEXT_PAGE_SELECTORS)
                and len(paged_rows) <= paged_cfg.max_pages
                and "列表第 2 页" in surf_mod._digest_prompt("露营装备测评", paged_signals, 3)
                and not paged_notes
            )
            stuck_signals, _stuck_page, _stuck_browser = _run_surf(
                results_url="", has_next=True, next_changes=False, cfg_used=paged_cfg
            )
            stuck_ok = (
                not any(item.get("list_page") for item in stuck_signals)
                and "页面没变化"
                in "；".join(
                    str(item.get("excerpt") or "")
                    for item in stuck_signals
                    if item.get("kind") == "note"
                )
            )
            tight_cfg = _Ns(**{**vars(paged_cfg), "max_pages": 2, "list_pages": 4})
            tight_signals, _tight_page, _tight_browser = _run_surf(
                results_url="", has_next=True, cfg_used=tight_cfg
            )
            tight_notes = "；".join(
                str(item.get("excerpt") or "")
                for item in tight_signals
                if item.get("kind") == "note"
            )
            tight_ok = (
                len([item for item in tight_signals if item.get("kind") == "page"])
                <= tight_cfg.max_pages
                and "列表后面的页没有再翻" in tight_notes
            )
            text_cfg = _Ns(
                **{
                    **vars(surf_cfg),
                    "list_pages": 1,
                    "hops_per_page": 1,
                    "search_on_seeds": False,
                    "page_comments": True,
                    "max_pages": 3,
                }
            )
            text_kwargs = {"results_url": "", "has_comments": True, "has_expand": True}
            text_signals, _text_page, _text_browser = _run_surf(cfg_used=text_cfg, **text_kwargs)
            read_rows = [item for item in text_signals if item.get("kind") in ("page", "item")]
            page_comments_ok = (
                bool(read_rows)
                and all(item.get("comments") for item in read_rows)
                and {
                    row.get("text") for item in read_rows for row in item.get("comments") or []
                }
                == {row["text"] for row in _FakeSurfPage._FAKE_COMMENTS}
                and all(item.get("expanded") for item in read_rows)
                and "评论区：" in surf_mod._digest_prompt("露营装备测评", text_signals, 3)
            )
            comments_off_signals, _co_page, _co_browser = _run_surf(
                cfg_used=_Ns(**{**vars(text_cfg), "page_comments": False}), **text_kwargs
            )
            comments_off_ok = not any(
                item.get("comments")
                for item in comments_off_signals
                if item.get("kind") in ("page", "item")
            )

            # ⑧c 扫标题挑条目：同域之内先点标题认得出主题词的那几条（凭哪几个词点进去写在 picked_for），
            #     整版一个都认不出就如实说明「是按列表原顺序顺路读的」，绝不假装条条都贴主题
            tokens_ok = (
                surf_mod._topic_tokens("露营装备测评")[:2] == ("露营", "营装")
                and surf_mod._topic_tokens("") == ()
                and surf_mod._relevance("随便一条标题", ("露营",)) == (0, [])
                and surf_mod._relevance("露营 3 件装备清单", ("露营", "装备"))[0] == 3
            )
            ranked, _ = surf_mod._pick_hops(
                [{"text": text, "href": href} for text, href in focus_links],
                {"example.com"},
                2,
                home_url="https://example.com/feed",
                visited=set(),
                topic_tokens=surf_mod._topic_tokens("露营装备测评"),
            )
            rank_ok = (
                [str(item.get("url") or "") for item in ranked]
                == ["https://example.com/post/2", "https://example.com/post/4"]
                and "露营" in str(ranked[0].get("why") or "")
            )
            plain_picks, _ = surf_mod._pick_hops(
                [{"text": text, "href": href} for text, href in focus_links],
                {"example.com"},
                2,
                home_url="https://example.com/feed",
                visited=set(),
            )
            plain_rank_ok = [str(item.get("url") or "") for item in plain_picks] == [
                "https://example.com/post/1",
                "https://example.com/post/2",
            ] and not any(item.get("why") for item in plain_picks)
            focus_cfg = _Ns(
                **{**vars(surf_cfg), "list_pages": 1, "search_on_seeds": False, "max_pages": 4}
            )
            focus_kwargs = {"links": focus_links, "results_url": ""}
            video_kwargs = {"links": video_links, "results_url": ""}
            focus_signals, _focus_page, _focus_browser = _run_surf(cfg_used=focus_cfg, **focus_kwargs)
            focus_items = [item for item in focus_signals if item.get("kind") == "item"]
            focus_ok = (
                [item.get("listed_as") for item in focus_items]
                == ["露营装备避坑指南", "测评：3 件帐篷实测"]
                and all(item.get("picked_for") for item in focus_items)
                and "因标题含" in surf_mod._digest_prompt("露营装备测评", focus_signals, 3)
            )
            focus_off_signals, _off_page, _off_browser = _run_surf(
                cfg_used=_Ns(**{**vars(focus_cfg), "topic_focus": False}), **focus_kwargs
            )
            focus_off_ok = (
                [
                    item.get("listed_as")
                    for item in focus_off_signals
                    if item.get("kind") == "item"
                ]
                == ["本站热门推荐", "露营装备避坑指南"]
                and not any(item.get("picked_for") for item in focus_off_signals)
            )
            blind_ok = "没认出本次主题的词" in notes_text and not any(
                item.get("picked_for") for item in browse_signals
            )
            # 要在线看的视频也先扫标题：贴主题的排到前面（单会话只看 1 条时看点哪条就是这一眼决定的）
            video_cfg = _Ns(
                **{
                    **vars(surf_cfg),
                    "hops_per_page": 0,
                    "list_pages": 1,
                    "search_on_seeds": False,
                    "click_into_videos": True,
                    "max_videos_per_session": 1,
                }
            )
            video_signals, _video_page, _video_browser = _run_surf(
                watch_videos=True, cfg_used=video_cfg, **video_kwargs
            )
            vid_rows = [
                item for item in video_signals if str(item.get("kind") or "").startswith("video")
            ]
            video_focus_ok = (
                len(vid_rows) == 1
                and vid_rows[0].get("url") == "https://example.com/video/2"
                and vid_rows[0].get("via") == "feed"
                and "露营" in str(vid_rows[0].get("picked_for") or "")
                and vid_rows[0].get("listed_as") == "露营装备实测视频"
                and "因标题含" in surf_mod._digest_prompt("露营装备测评", video_signals, 3)
            )
            video_blind_ok = (
                not any(item.get("picked_for") for item in video_rows)
                and "视频链接标题都没认出"
                in "；".join(
                    str(item.get("excerpt") or "")
                    for item in watch_signals
                    if item.get("kind") == "note"
                )
            )

            # ⑧d 搜不对题就换个说法再搜：整句搜出来的结果标题里一个主题词都认不出时，**清空搜索框把词说短**
            #     再搜一次；再没有更短的说法可换就照旧读最后一版，并如实写明换过哪几版词（绝不偷偷多搜、也不假装搜到了）
            variant_ok = (
                surf_mod._query_variants("露营装备测评", 2) == ("露营装备测评", "露营装备")
                and surf_mod._query_variants("露营装备测评", 1) == ("露营装备测评",)
                and surf_mod._query_variants("咖啡，入门推荐/避坑", 3)
                == ("咖啡，入门推荐/避坑", "咖啡", "入门推荐")
                and surf_mod._query_variants("露营", 3) == ("露营",)
                and surf_mod._query_variants("", 3) == ()
            )
            off_topic_results = [
                ("本站热门推荐", "https://example.com/post/9"),
                ("编辑精选", "https://example.com/post/8"),
            ]
            retry_base = "https://example.com/search?q="
            retry_cfg = _Ns(
                **{
                    **vars(surf_cfg),
                    "hops_per_page": 0,
                    "list_pages": 1,
                    "max_pages": 3,
                    "search_retries": 1,
                }
            )
            retry_kwargs = {
                "results_url": retry_base,
                "result_links": [off_topic_results, focus_links],
            }
            retry_signals, retry_page, _ = _run_surf(cfg_used=retry_cfg, **retry_kwargs)
            retry_rows = [item for item in retry_signals if item.get("kind") == "search"]
            retry_notes = "；".join(
                str(item.get("excerpt") or "")
                for item in retry_signals
                if item.get("kind") == "note"
            )
            retry_ok = (
                [query for query, _delay in retry_page.typed] == ["露营装备测评", "露营装备"]
                and "" in retry_page.filled  # 第二次搜之前真把框里的旧词清掉了
                and len(retry_rows) == 1  # 第一版那页没读（不贴主题，只留下说明）
                and retry_rows[0].get("url") == f"{retry_base}&r=1"
                and retry_rows[0].get("query") == "露营装备"
                and retry_rows[0].get("search_tried") == ["露营装备测评", "露营装备"]
                and "换个更短的说法再搜" in retry_notes
                and "换了 1 次说法才搜到" in retry_notes
                and "站内搜索关键词" in surf_mod._digest_prompt("露营装备测评", retry_signals, 3)
            )
            exhausted_signals, exhausted_page, _ = _run_surf(
                cfg_used=_Ns(**{**vars(retry_cfg), "search_retries": 2}),
                results_url=retry_base,
                result_links=[off_topic_results, off_topic_results],
            )
            exhausted_rows = [
                item for item in exhausted_signals if item.get("kind") == "search"
            ]
            exhausted_notes = "；".join(
                str(item.get("excerpt") or "")
                for item in exhausted_signals
                if item.get("kind") == "note"
            )
            exhausted_ok = (
                [query for query, _delay in exhausted_page.typed] == ["露营装备测评", "露营装备"]
                and len(exhausted_rows) == 1
                and exhausted_rows[0].get("url") == f"{retry_base}&r=1"
                and "才搜到" not in exhausted_notes  # 没搜到对题的东西就不说「搜到了」
                and "换个更短的说法再搜" in exhausted_notes
            )
            no_retry_ok = (
                len(browse_page.typed) == 1
                and by_kind["search"][0].get("query") == "露营装备测评"
                and not any(item.get("search_tried") for item in browse_signals)
            )

            # ⑧e 读过的不重读：上几轮真读进去的条目记在跨会话浏览历史里，这一轮挑条目就跳过它们、
            #     改读没见过的新条目；整版都是读过的照样按原顺序重读并如实说明；用户指定的链接永远照看
            memo_cfg = _Ns(**{**vars(focus_cfg), "search_on_seeds": False})
            read_once = frozenset(
                {"https://example.com/post/2", "https://example.com/post/4"}
            )
            memo_signals, _memo_page, _memo_browser = _run_surf(
                cfg_used=memo_cfg, links=focus_links, read_before=read_once
            )
            memo_items = [item for item in memo_signals if item.get("kind") == "item"]
            memo_notes = "；".join(
                str(item.get("excerpt") or "")
                for item in memo_signals
                if item.get("kind") == "note"
            )
            memo_ok = (
                [item.get("url") for item in memo_items]
                == ["https://example.com/post/1", "https://example.com/post/3"]
                and not any(item.get("url") in read_once for item in memo_items)
                and "跳过 2 条上几轮就读过的条目" in memo_notes
                and "改读没见过的新条目" in memo_notes
            )
            all_read = frozenset(href for _text, href in focus_links)
            reread_signals, _reread_page, _reread_browser = _run_surf(
                cfg_used=memo_cfg, links=focus_links, read_before=all_read
            )
            reread_notes = "；".join(
                str(item.get("excerpt") or "")
                for item in reread_signals
                if item.get("kind") == "note"
            )
            reread_ok = (
                bool([item for item in reread_signals if item.get("kind") == "item"])
                and "上几轮都读过" in reread_notes
                and "没找到没见过的新条目" in reread_notes
                and "跳过" not in reread_notes  # 全都重读了，就不能说「跳过了几条」
            )
            memo_video_signals, _mv_page, _mv_browser = _run_surf(
                watch_videos=True,
                cfg_used=video_cfg,
                links=video_links,
                results_url="",
                read_before=frozenset({"https://example.com/video/2"}),
            )
            memo_video_rows = [
                item
                for item in memo_video_signals
                if str(item.get("kind") or "").startswith("video")
            ]
            memo_video_notes = "；".join(
                str(item.get("excerpt") or "")
                for item in memo_video_signals
                if item.get("kind") == "note"
            )
            memo_video_ok = (
                bool(memo_video_rows)
                and memo_video_rows[0].get("url") == "https://example.com/video/1"
                and all(item.get("url") != "https://example.com/video/2" for item in memo_video_rows)
                and "1 条顺路发现的视频页上几轮已经看过" in memo_video_notes
            )
            # 用户显式点名要看的视频，哪怕上几轮看过也照样看（记忆不能盖过用户的明确要求）
            direct_signals, _d_page, _d_browser = _run_surf(
                watch_videos=True,
                cfg_used=video_cfg,
                links=video_links,
                results_url="",
                read_before=frozenset({"https://example.com/video/2"}),
                video_urls=("https://example.com/video/2",),
            )
            direct_rows = [
                item for item in direct_signals if str(item.get("kind") or "").startswith("video")
            ]
            direct_ok = (
                bool(direct_rows)
                and direct_rows[0].get("url") == "https://example.com/video/2"
                and direct_rows[0].get("via") == "direct"
                and "上几轮已经看过"
                not in "；".join(
                    str(item.get("excerpt") or "")
                    for item in direct_signals
                    if item.get("kind") == "note"
                )
            )
            # 历史存储本身：只记真读进去的条目/视频、按租户隔离、同一页再读只 bump 遍数、
            # 超上限先忘最旧的、开关关掉既不记也不跳、清掉即归零
            saved_remember = cfg.surf.remember_read
            saved_history_max = cfg.surf.history_max
            hist_tenant, other_tenant = "doctor-hist", "doctor-hist-other"
            try:
                surf_mod.clear_history(hist_tenant)
                surf_mod.clear_history(other_tenant)
                first = [
                    {
                        "kind": "item",
                        "url": "https://example.com/post/1",
                        "title": "露营装备避坑指南",
                        "via": "feed",
                    },
                    {
                        "kind": "video",
                        "url": "https://example.com/video/2",
                        "title": "露营装备实测视频",
                        "via": "feed",
                    },
                    {"kind": "page", "url": "https://example.com/feed", "title": "起点站首页", "via": "seed"},
                    {"kind": "error", "url": "https://example.com/post/7", "title": "访问失败", "via": "feed"},
                    {"kind": "note", "title": "本轮浏览说明", "excerpt": "说明"},
                ]
                stored = surf_mod.remember_read_pages(
                    first, topic="露营装备测评", tenant=hist_tenant
                )
                urls = surf_mod.read_history(hist_tenant)
                leaked = surf_mod.read_history(other_tenant)
                again = surf_mod.remember_read_pages(
                    [dict(first[0])], topic="露营装备测评", tenant=hist_tenant
                )
                reads = {row["url"]: row["reads"] for row in surf_mod.list_history(hist_tenant)}
                titles = {row["url"]: row.get("title") for row in surf_mod.list_history(hist_tenant)}
                store_ok = (
                    stored == 2  # 只记条目页与视频页：首页/失败页/说明都不算「读过」
                    and urls
                    == frozenset(
                        {"https://example.com/post/1", "https://example.com/video/2"}
                    )
                    and not leaked  # 别的租户看不见这个租户读过的页
                    and again == 1
                    and reads == {"https://example.com/post/1": 2, "https://example.com/video/2": 1}
                    and titles["https://example.com/post/1"] == "露营装备避坑指南"
                )
                merged = [
                    {
                        "kind": "item",
                        "url": "https://example.com/post/1",
                        "title": "露营装备避坑指南",
                        "via": "search",
                    },
                    {"kind": "note", "title": "本轮浏览说明", "excerpt": "原有说明"},
                ]
                surf_mod._remember_read_note(merged, topic="露营装备测评", tenant=hist_tenant)
                note_rows = [item for item in merged if item.get("kind") == "note"]
                merge_ok = len(note_rows) == 1 and "记进浏览历史" in str(note_rows[0].get("excerpt"))
                cfg.surf.history_max = 20
                many = [
                    {
                        "kind": "item",
                        "url": f"https://example.com/post/m{i}",
                        "title": f"条目 m{i}",
                        "via": "feed",
                    }
                    for i in range(25)
                ]
                surf_mod.remember_read_pages(many, topic="露营装备测评", tenant=hist_tenant)
                prune_ok = len(surf_mod.list_history(hist_tenant)) == 20
                before_off = len(surf_mod.list_history(hist_tenant))
                cfg.surf.remember_read = False
                off_urls = surf_mod.read_history(hist_tenant)
                off_written = surf_mod.remember_read_pages(
                    [dict(first[0])], topic="露营装备测评", tenant=hist_tenant
                )
                cfg.surf.remember_read = True
                off_ok = (
                    not off_urls  # 关掉开关=没有记忆，挑条目时什么都不会跳过
                    and off_written == 0
                    and len(surf_mod.list_history(hist_tenant)) == before_off  # 也没偷偷记
                )
                cleared = surf_mod.clear_history(hist_tenant)
                clear_ok = bool(cleared) and not surf_mod.list_history(hist_tenant)
            finally:
                cfg.surf.remember_read = saved_remember
                cfg.surf.history_max = saved_history_max
                surf_mod.clear_history(hist_tenant)
                surf_mod.clear_history(other_tenant)

            # ⑧f 站内搜索也瞄一眼打字时站点弹出的**联想下拉**：认得出本次主题词的才点上（这一版词是
            #     站点自己的叫法，不是我们切的词）；一条都认不出就照原词回车（绝不为了点联想改主题）；
            #     联想项点不动就退回「把词打回搜索框再回车」；关掉开关连下拉都不读；这站没有下拉就一字不提
            sug_cfg = _Ns(**{**vars(retry_cfg), "search_retries": 0})
            sug_words = surf_mod._topic_tokens("露营装备测评")
            sug_pure_ok = (
                surf_mod._pick_suggestion(
                    [("露营好物", "h1"), ("露营装备清单", "h2"), ("全站热搜榜", "h3")], sug_words
                )[0]
                == "露营装备清单"  # 不点第一条，挑认出主题词最多的那条
                and surf_mod._pick_suggestion(
                    [("露营装备清单", "h1"), ("露营装备推荐", "h2")], sug_words
                )[0]
                == "露营装备清单"  # 同分时保持站点给的原顺序（站点排序本身是信息）
                and surf_mod._pick_suggestion([("全站热搜榜", "h1")], sug_words) == ("", None, [])
            )
            hot_words = ("露营好物", "露营装备清单", "全站热搜榜")

            def _sug_case(**kwargs):
                signals, page, _browser = _run_surf(
                    cfg_used=kwargs.pop("cfg_used", sug_cfg), links=focus_links, **kwargs
                )
                rows = [item for item in signals if item.get("kind") == "search"]
                notes = "；".join(
                    str(item.get("excerpt") or "")
                    for item in signals
                    if item.get("kind") == "note"
                )
                return signals, page, rows, notes

            picked_signals, picked_page, picked_rows, picked_notes = _sug_case(
                suggestions=hot_words
            )
            sug_hit_ok = (
                picked_page.sug_clicked == ["露营装备清单"]  # 真点下去的就是那条
                # 打进搜索框的仍是原词，只是没回车：点中联想项直接就跳了结果页
                and [query for query, _delay in picked_page.typed] == ["露营装备测评"]
                and "Enter" not in picked_page.keys
                and len(picked_rows) == 1
                and picked_rows[0].get("query") == "露营装备清单"
                and not picked_rows[0].get("search_tried")  # 只搜了一版词，不硬凑「换过说法」的记录
                and picked_rows[0].get("suggested") == "露营装备清单"
                and picked_rows[0].get("url") == picked_page.results_url
                and "站点自己给的联想词" in picked_notes
                and "因为里面认出了露营、营装、装备" in picked_notes
                and "站点打字时自己弹出来的联想词"
                in surf_mod._digest_prompt("露营装备测评", picked_signals, 3)
            )
            _cap_signals, cap_page, cap_rows, _cap_notes = _sug_case(
                cfg_used=_Ns(**{**vars(sug_cfg), "suggestions_max": 1}),
                suggestions=hot_words,
            )
            sug_cap_ok = (
                cap_page.sug_clicked == ["露营好物"]  # 只读回第一条联想，挑无可挑
                and bool(cap_rows)
                and cap_rows[0].get("suggested") == "露营好物"
            )
            blind_signals, blind_page, blind_rows, blind_notes = _sug_case(
                suggestions=("全站热搜榜", "编辑精选推荐")
            )
            sug_blind_ok = (
                not blind_page.sug_clicked
                and blind_page.filled == [""]  # 只清了框里的旧词，没把哪条联想打回去
                and "Enter" in blind_page.keys  # 照原词回车
                and [query for query, _delay in blind_page.typed] == ["露营装备测评"]
                and bool(blind_rows)
                and blind_rows[0].get("query") == "露营装备测评"
                and not blind_rows[0].get("suggested")
                and "一条都没认出本次主题的词" in blind_notes
                and "不为一条联想改主题" in blind_notes
                and "联想" not in surf_mod._digest_prompt("露营装备测评", blind_signals, 3)
            )
            noop_signals, noop_page, noop_rows, noop_notes = _sug_case(
                suggestions=hot_words, sug_click_fails=True
            )
            sug_stuck_ok = (
                not noop_page.sug_clicked  # 那种只有样式、没有点击处理的下拉：点就是没点
                and noop_page.filled == ["", "露营装备清单"]  # 退回真人做法：词打回框里再回车
                and "Enter" in noop_page.keys
                and bool(noop_rows)
                and noop_rows[0].get("query") == "露营装备清单"
                and noop_rows[0].get("suggested") == "露营装备清单"
                and "联想项点不动，是把这个词打回搜索框再回车的" in noop_notes
            )
            off_signals, off_page, off_rows, off_notes = _sug_case(
                cfg_used=_Ns(**{**vars(sug_cfg), "use_suggestions": False}),
                suggestions=hot_words,
            )
            sug_off_ok = (
                off_page.sug_probes == 0  # 关掉开关连联想下拉都不读一眼
                and not off_page.sug_clicked
                and [query for query, _delay in off_page.typed] == ["露营装备测评"]
                and "Enter" in off_page.keys
                and bool(off_rows)
                and off_rows[0].get("query") == "露营装备测评"
                and not any(item.get("suggested") for item in off_signals)
                and "联想" not in off_notes
            )
            plain_signals, plain_page, plain_rows, plain_notes = _sug_case()
            sug_plain_ok = (
                plain_page.sug_probes > 0  # 读过了，只是这站根本没弹联想下拉
                and not plain_page.sug_clicked
                and plain_page.filled == [""]
                and bool(plain_rows)
                and plain_rows[0].get("query") == "露营装备测评"
                and not any(item.get("suggested") for item in plain_signals)
                and "联想" not in plain_notes  # 没看到下拉就一个字都不提，绝不假装看到过建议
            )

            # ⑧g 读到门口就退出来：登录墙/订阅墙/人机验证/维护页都如实标出来（不绕墙，也不把墙上的话当正文），
            #     判得保守（长正文里顺口提一句「登录」不误判），关掉开关照旧当正文读；被挡住的页不算「读过的页」
            wall_pure_ok = (
                surf_mod._wall_reason("露营装备清单", "本站露营装备测评文章，请先登录后阅读完整内容")[0]
                == "登录墙"
                and surf_mod._wall_reason("付费专栏", "开通会员才能看全文")[0] == "订阅墙"
                and surf_mod._wall_reason("安全验证", "Checking your browser, verify you are a human")[0]
                == "人机验证"
                and surf_mod._wall_reason("页面不存在", "内容已被删除")[0] == "维护/出错页"
                # 正文长到不像一扇门 → 就算里面提了一句「先登录同步收藏」也不误判成墙
                and surf_mod._wall_reason(
                    "露营装备清单", "这篇稿子很长。" + "＊" * 700 + " 顺带一提登录后可同步收藏"
                ) == ("", "")
                # 短页面但没有墙话 = 只是一篇短帖子，不是墙
                and surf_mod._wall_reason("露营装备清单", "三种帐篷杆的对比，编辑部的实测") == ("", "")
            )
            wall_body = "本站露营装备测评文章，请先登录后阅读完整内容。"
            wall_cfg = _Ns(**{**vars(memo_cfg), "hops_per_page": 1})
            wall_signals, _wall_page, _wall_browser = _run_surf(
                cfg_used=wall_cfg, links=focus_links, results_url="", body_text=wall_body
            )
            wall_rows = [item for item in wall_signals if item.get("kind") in ("page", "item")]
            wall_notes = "；".join(
                str(item.get("excerpt") or "")
                for item in wall_signals
                if item.get("kind") == "note"
            )
            wall_ok = (
                len(wall_rows) == 2  # 起点页与点进去的那条条目都被同一扇门挡住
                and all(item.get("wall") == "登录墙" for item in wall_rows)
                and all(item.get("excerpt") == wall_body for item in wall_rows)  # 门口原话照记，但不冒充正文
                and all("没有绕墙" in "；".join(item.get("warnings") or []) for item in wall_rows)
                and "有 2 页读到门口就被挡住" in wall_notes
                and "这一页被登录墙挡住"
                in surf_mod._digest_prompt("露营装备测评", wall_signals, 3)
                and "绝不能拿它当内容证据出卡" in surf_mod._SURF_SYSTEM
            )
            long_body = "露营装备测评要点：请先登录" + "＊" * 400
            long_signals, _long_page, _long_browser = _run_surf(
                cfg_used=wall_cfg, links=focus_links, results_url="", body_text=long_body
            )
            long_rows = [item for item in long_signals if item.get("kind") in ("page", "item")]
            wall_trim_ok = (
                bool(long_rows)
                and all(item.get("wall") == "登录墙" for item in long_rows)
                # 门口那几句话只留这么长（够看出是哪种墙就停）
                and all(
                    len(str(item.get("excerpt") or "")) == surf_mod._WALL_EXCERPT_CHARS
                    for item in long_rows
                )
                and all(str(item.get("excerpt") or "").endswith("…") for item in long_rows)
            )
            off_signals2, _o2_page, _o2_browser = _run_surf(
                cfg_used=_Ns(**{**vars(wall_cfg), "avoid_walls": False}),
                links=focus_links,
                results_url="",
                body_text=wall_body,
            )
            off_rows2 = [item for item in off_signals2 if item.get("kind") in ("page", "item")]
            off_notes2 = "；".join(
                str(item.get("excerpt") or "")
                for item in off_signals2
                if item.get("kind") == "note"
            )
            wall_off_ok = (
                bool(off_rows2)
                and not any(item.get("wall") for item in off_signals2)  # 关掉开关就不判墙
                and "门口" not in off_notes2
                and all(item.get("excerpt") == wall_body for item in off_rows2)  # 墙话照旧当正文（用户选的口径）
            )
            # 被墙挡住的页**没读到内容**，不装成「上几轮读过的」写进浏览历史
            wall_tenant = "doctor-wall"
            try:
                surf_mod.clear_history(wall_tenant)
                stored_wall = surf_mod.remember_read_pages(
                    [
                        {
                            "kind": "item",
                            "url": "https://example.com/post/wall",
                            "title": "要登录才看的那篇",
                            "wall": "登录墙",
                            "via": "feed",
                        },
                        {
                            "kind": "item",
                            "url": "https://example.com/post/read",
                            "title": "真读完的那篇",
                            "via": "feed",
                        },
                    ],
                    topic="露营装备测评",
                    tenant=wall_tenant,
                )
                wall_hist_ok = (
                    stored_wall == 1
                    and surf_mod.read_history(wall_tenant)
                    == frozenset({"https://example.com/post/read"})
                )
            finally:
                surf_mod.clear_history(wall_tenant)
        finally:
            surf_mod._ensure_public_url = saved_public
        print(
            f"    真人式浏览：随机节奏={pace_ok}｜关掉后机器节奏={machine_ok}｜先关遮罩={consent_ok}｜"
            f"站内搜索={search_ok}"
        )
        print(
            f"    信息流多跳：只点白名单同域条目={hop_ok}｜页数不越上限={budget_ok}｜"
            f"没搜索框就如实不搜={no_box_ok}｜越界条目不收且不假装={boundary_ok}｜"
            f"纯函数拒绝越界={hop_off_ok}｜浏览器一定关掉={close_ok}"
        )
        print(
            f"    列表翻页与页间停顿：翻到第 3 页={paged_ok}｜页数用尽就停={tight_ok}｜"
            f"点了没变即停={stuck_ok}｜没控件只刷第一页={no_next_ok}｜"
            f"停顿随机={gap_ok}｜第一页不硬停={browse_gap_ok}"
        )
        print(
            f"    展开全文与页面评论区：点开折叠全文并逐条抓评论={page_comments_ok}｜"
            f"关掉开关就不抓={comments_off_ok}"
        )
        print(
            f"    扫标题挑条目：主题切词={tokens_ok}｜贴主题条目排在前={rank_ok}｜"
            f"关掉就按列表顺序={plain_rank_ok}｜端到端点进贴题条目={focus_ok}｜"
            f"开关关掉即退回原顺序={focus_off_ok}｜认不出主题词即如实说明={blind_ok}｜"
            f"信号写明凭哪几个词点进={bool(focus_items) and all(i.get('picked_for') for i in focus_items)}｜"
            f"要在线看的视频也先扫标题={video_focus_ok}｜"
            f"视频标题认不出即如实说明={video_blind_ok}"
        )
        print(
            f"    搜不对题就换个说法再搜：把主题说短={variant_ok}｜"
            f"清空搜索框换词并只读贴主题那版={retry_ok}｜"
            f"没有更短的说法就照旧读最后一版={exhausted_ok}｜"
            f"关掉开关（retries=0）只搜一次={no_retry_ok}"
        )
        print(
            f"    读过的不重读（跨会话浏览历史）：这轮跳过读过的条目改读新的={memo_ok}｜"
            f"整版都读过仍按原顺序重读并如实说明={reread_ok}｜"
            f"看过的视频不重复点进={memo_video_ok}｜用户指定的链接照样看={direct_ok}｜"
            f"只记真读到的页·按租户隔离·再读只加遍数={store_ok}｜"
            f"记了几条并进同一条浏览说明={merge_ok}｜超上限先忘最旧的={prune_ok}｜"
            f"关掉开关既不记也不跳={off_ok}｜清掉即归零={clear_ok}"
        )
        print(
            f"    站内搜索也看站点弹出的联想词：只挑认出主题词的那条、同分保持站点顺序={sug_pure_ok}｜"
            f"点中贴主题的联想词并把这版词记成站点给的={sug_hit_ok}｜"
            f"只读回前几条（上限生效）={sug_cap_ok}｜"
            f"一条都认不出就照原词搜且不为联想改主题={sug_blind_ok}｜"
            f"联想项点不动就退回打回框里再回车={sug_stuck_ok}｜"
            f"关掉开关连下拉都不读={sug_off_ok}｜这站没下拉就一个字都不提={sug_plain_ok}"
        )
        print(
            f"    读到门口就退出来：认出四种墙且长正文不误判={wall_pure_ok}｜"
            f"墙上原话照记但不冒充正文、汇总点名被挡页数={wall_ok}｜"
            f"门口摘录按长度截断={wall_trim_ok}｜"
            f"关掉开关照旧当正文读={wall_off_ok}｜被挡的页不写进读过的历史={wall_hist_ok}"
        )
        print(f"    浏览与看视频同预算：信息流点进视频页={browse_ok}｜看片诚实失败={watch_ok}")
    finally:
        cfg.surf.provider = saved_provider
        cfg.surf.pool_max = saved_pool_max
        # 回收 doctor 触发的会话与残留灵感卡，不污染真实灵感池
        for tid in (sample_task_id, browser_task_id, http_task_id, watch_task_id, urls_task_id):
            drop_task(tid)
        for cid in created_card_ids:
            drop_pool_card(cid)

    cleanup_ok = not list_sessions(task_id=sample_task_id) and all(
        get_pool_card(cid, tenant="default") is None for cid in created_card_ids
    )
    print(f"    作业/灵感回收：sessions&pool 清空={cleanup_ok}")

    ok = (
        accepted
        and sample_done
        and render_ok
        and in_pool
        and pool_ok
        and browser_fail
        and http_fail
        and chunk_ok
        and calls_ok
        and comments_ok
        and ocr_ok
        and urls_ok
        and group_calls_ok
        and no_fabricate_ok
        and ocr_no_shot_ok
        and ocr_placeholder_ok
        and ocr_real_ok
        and vu_ok
        and vu_empty
        and watch_degrade
        and bwatch
        and pace_ok
        and machine_ok
        and consent_ok
        and search_ok
        and hop_ok
        and budget_ok
        and boundary_ok
        and no_box_ok
        and hop_off_ok
        and gap_ok
        and browse_gap_ok
        and paged_ok
        and tight_ok
        and stuck_ok
        and no_next_ok
        and page_comments_ok
        and comments_off_ok
        and tokens_ok
        and rank_ok
        and plain_rank_ok
        and focus_ok
        and focus_off_ok
        and blind_ok
        and video_focus_ok
        and variant_ok
        and retry_ok
        and exhausted_ok
        and no_retry_ok
        and memo_ok
        and reread_ok
        and memo_video_ok
        and direct_ok
        and store_ok
        and merge_ok
        and prune_ok
        and off_ok
        and clear_ok
        and sug_pure_ok
        and sug_hit_ok
        and sug_cap_ok
        and sug_blind_ok
        and sug_stuck_ok
        and sug_off_ok
        and sug_plain_ok
        and wall_pure_ok
        and wall_ok
        and wall_trim_ok
        and wall_off_ok
        and wall_hist_ok
        and video_blind_ok
        and browse_ok
        and watch_ok
        and close_ok
        and cleanup_ok
    )
    if not ok:
        print("    ! 灵感冲浪异常：请检查 app/core/surf.py 的状态机、诚实失败与灵感池存储。")
    return ok


def check_dubbing() -> bool:
    """校验全自动配音（**真实功能**，非样例）：台词切分与说话人识别、sample 离线
    通道生命周期、**引擎通道真实合成**（Windows 上真跑一次 sapi，产出可播放的 wav、
    按实长重建时间轴、拼整轨）、缺配置/缺参考音频 fail-loud（点名环境变量与说话人）、
    合成音频引用安全边界、成本计费口径、租户隔离与作业回收（含落盘音频目录）；
    并验证**跨语言译制配音**（逐句译成目标语言、原句保留、离线占位译文必须自报）。

    诚实口径是这里最硬的断言：sapi 不消费参考音频就必须在汇总里写明「未克隆」，
    拿不到真音频只能失败，绝不交一份静音或占位成品冒充配音。
    """
    import time
    from datetime import datetime, timedelta, timezone

    from app.config import DATA_DIR, get_config
    from app.core.dubbing import (
        DEFAULT_NARRATOR,
        _estimate_cost,
        build_manifest,
        create_job,
        detect_language,
        drop_task,
        get_job,
        list_jobs,
        render_timeline_text,
        resolve_audio_path,
        resolve_provider,
        resolve_voice_path,
        translate_manifest,
    )
    from app.core.types import Brief, TaskRecord

    print("\n[全自动配音（引擎真实合成）]")

    cfg = get_config()

    def _dub_task(tid: str, tenant: str = "default") -> TaskRecord:
        return TaskRecord(id=tid, tenant=tenant, brief=Brief(brand="测试"))

    script = "甲：你终于来了。\n乙（低声）：别出声，跟我走。\n雨越下越大，没有人回答。"
    # 故意不给旁白配参考音频，验证缺音点名
    voices = {"甲": "assets/jia.wav", "乙": "assets/yi.wav"}

    # 1) 清单：切出 3 句、3 位说话人（无标记行归旁白），旁白缺音
    manifest = build_manifest(script, voices=voices, provider="sample")
    manifest_ok = (
        manifest["line_count"] == 3
        and set(manifest["speakers"]) == {"甲", "乙", DEFAULT_NARRATOR}
        and manifest["segments"][0]["speaker"] == "甲"
        and manifest["segments"][2]["speaker"] == DEFAULT_NARRATOR
        and manifest["missing_voices"] == [DEFAULT_NARRATOR]
        and all(seg["duration_seconds"] > 0 for seg in manifest["segments"])
    )
    print(
        f"    台词切分/说话人识别：{manifest['line_count']} 句／"
        f"{len(manifest['speakers'])} 人｜缺音点名={manifest['missing_voices']}｜"
        f"{'正确' if manifest_ok else '异常'}"
    )

    # 1b) 小说式句式：有标记（前置/后置）要认得，没标记的叙述句归旁白不冒充
    novel = '甲说：“你终于来了。”\n「别出声。」乙低声说。\n他叹了口气，转身离开。\n“谁？”\n他叹了口气说：“走吧。”'
    nm = build_manifest(novel, voices={}, provider="sample")
    novel_ok = (
        nm["line_count"] == 5
        and nm["segments"][0]["speaker"] == "甲"  # 前置：剥离「说」取名字，引号已剥
        and nm["segments"][0]["text"] == "你终于来了。"
        and nm["segments"][1]["speaker"] == "乙"  # 后置：「「台词」乙低声说。」
        and nm["segments"][1]["text"] == "别出声。"
        and nm["segments"][2]["speaker"] == DEFAULT_NARRATOR  # 叙述句不冒充说话人
        and bool(nm["segments"][2]["issue"])
        and nm["segments"][3]["text"] == "谁？"  # 纯引号行：剥引号后归旁白并标注
        and bool(nm["segments"][3]["issue"])
        and nm["segments"][4]["speaker"] == DEFAULT_NARRATOR  # 「了」字叙述句前置也不采信
        and bool(nm["segments"][4]["issue"])
        and set(nm["speakers"]) == {"甲", "乙", DEFAULT_NARRATOR}
    )
    print(f"    小说式句式识别：前置/后置/叙述句/纯引号 全部归位={novel_ok}")

    saved_api_url = cfg.dubbing.api_url
    saved_budget = cfg.cost_budget_usd
    try:
        cfg.dubbing.api_url = ""  # 默认离线态：无网关

        task = _dub_task("doctor-dub-1")
        job = create_job(task, script_text=script, voices=voices, provider="sample")
        accepted = job["status"] == "queued" and job["provider"] == "sample"
        final = list_jobs(task_id=task.id, now=datetime.now(timezone.utc) + timedelta(seconds=90))
        row = final[0] if final else {}
        summary = row.get("summary") or {}
        done_ok = (
            row.get("status") == "done"
            and summary.get("simulated") is True
            and summary.get("line_count") == 3
            and all(
                str(seg.get("audio_url", "")).startswith("sample://")
                for seg in summary.get("segments") or []
            )
            and bool(row.get("timeline_path"))
        )
        lifecycle_ok = accepted and done_ok
        print(f"    sample 离线通道：受理={accepted}｜推进完成+时间轴汇总={done_ok}")

        # 2) 时间轴汇总正文：每段含源文本 + 参考音频 + 配音（用户要的「汇总」）
        timeline_text = render_timeline_text(summary)
        timeline_ok = (
            "源文本" in timeline_text
            and "配音" in timeline_text
            and "参考音频" in timeline_text
            and "时长口径" in timeline_text
            and "你终于来了" in timeline_text
        )
        print(f"    时间轴汇总 txt：含源文本／参考音频／配音={timeline_ok}")

        # 3) 租户隔离
        visible = get_job(job["id"], tenant="default") is not None
        leaked = get_job(job["id"], tenant="acme") is not None
        tenant_ok = visible and not leaked
        print(f"    租户隔离：本租户可见={visible}｜跨租户泄漏={leaked}")

        # 4) real + 无网关（默认态）→ 显式失败，绝不触网、绝不假装配音
        real_no_gw = create_job(
            _dub_task("doctor-dub-2"), script_text=script, voices=voices, provider="real"
        )
        gateway_fail = real_no_gw["status"] == "failed" and "未配置" in str(real_no_gw["error"])
        print(f"    real（未配网关）：status={real_no_gw['status']}｜error={str(real_no_gw['error'])[:30]}…")

        # 5) real + 配了网关但旁白缺参考音频 → 提交前失败并点名说话人（不触网）
        cfg.dubbing.api_url = "https://dub-gw.example.invalid/tts"
        cfg.cost_budget_usd = 100.0  # 排除成本熔断，确保命中缺音分支
        real_missing = create_job(
            _dub_task("doctor-dub-3"), script_text=script, voices=voices, provider="real"
        )
        missing_fail = real_missing["status"] == "failed" and DEFAULT_NARRATOR in str(real_missing["error"])
        print(f"    real（缺参考音频）：status={real_missing['status']}｜点名说话人={missing_fail}")
    finally:
        cfg.dubbing.api_url = saved_api_url
        cfg.cost_budget_usd = saved_budget

    # 6) 空文本（解析不出台词）→ 直接拒绝（路由层转 409）
    empty_ok = False
    try:
        create_job(
            TaskRecord(id="doctor-dub-4", tenant="default", brief=Brief(brand="测试")),
            script_text="   ",
            provider="sample",
        )
    except ValueError:
        empty_ok = True
    print(f"    空文本防护：拒绝={empty_ok}")

    # 7) 本地参考音频路径边界 + 各通道计费口径（系统/自建音色零成本、云端按字符）
    path_ok = (
        resolve_voice_path("../secret.wav")[0] is None and resolve_voice_path("nope.wav")[0] is None
    )
    est_ok = (
        _estimate_cost(manifest, "sample") == 0.0
        and _estimate_cost(manifest, "sapi") == 0.0
        and _estimate_cost(manifest, "sovits") == 0.0
        and _estimate_cost(manifest, "elevenlabs") > 0.0
    )
    print(
        f"    参考音频路径安全边界：越界/缺失拒绝={path_ok}"
        f"｜计费口径（本机/自建归零、云端按字符）={est_ok}"
    )

    # 8) 合成音频引用边界：只认 dubbing/<本作业>/<文件名>，跨作业与越路径一律拒绝
    ref_ok = (
        resolve_audio_path("dubbing/dub_other/seg_0001.wav", "dub_mine")[0] is None
        and resolve_audio_path("dubbing/dub_mine/../../secret.wav", "dub_mine")[0] is None
        and resolve_audio_path("/etc/passwd", "dub_mine")[0] is None
        and resolve_audio_path("https://gw.example/a.wav", "dub_mine")[0] is None
        and resolve_audio_path("dubbing/dub_mine/", "dub_mine")[0] is None
    )
    print(f"    合成音频引用边界：跨作业/越界/绝对路径/远端/目录 全部拒绝={ref_ok}")

    # 9) 通道选择：auto 只在真引擎里择优，未知通道不静默变身
    chosen, auto_note = resolve_provider("auto")
    provider_ok = (
        chosen in ("sample", "sapi", "sovits", "elevenlabs", "real")
        and resolve_provider("not-a-provider")[0] == "sample"
    )
    print(
        f"    通道选择：auto→{chosen}（{auto_note or '未配置任何引擎，回退 sample 模拟'}）"
        f"｜未知通道归位 sample={provider_ok}"
    )

    # 10) 引擎通道 fail-loud：缺 key/端点在**受理时**就点名环境变量，绝不静默换通道
    saved_sovits = cfg.dubbing.sovits_base_url
    saved_el_key = cfg.dubbing.elevenlabs_api_key
    cfg.dubbing.sovits_base_url = ""
    cfg.dubbing.elevenlabs_api_key = ""
    cfg.cost_budget_usd = 100.0  # 排除成本熔断，确保命中缺配置分支
    try:
        sovits_job = create_job(
            _dub_task("doctor-dub-5"), script_text=script, voices=voices, provider="sovits"
        )
        eleven_job = create_job(
            _dub_task("doctor-dub-6"), script_text=script, voices=voices, provider="elevenlabs"
        )
    finally:
        cfg.dubbing.sovits_base_url = saved_sovits
        cfg.dubbing.elevenlabs_api_key = saved_el_key
        cfg.cost_budget_usd = saved_budget
    missing_engine_ok = (
        sovits_job["status"] == "failed"
        and "DUBBING_SOVITS_BASE_URL" in str(sovits_job["error"])
        and eleven_job["status"] == "failed"
        and "DUBBING_ELEVENLABS_API_KEY" in str(eleven_job["error"])
    )
    print(f"    sovits／elevenlabs 缺配置：受理即失败并点名变量={missing_engine_ok}")

    # 11) sapi 端到端：Windows 上真的合成可播放 wav（真功能要拿真音频当证据）
    sapi_job = create_job(
        _dub_task("doctor-dub-7"), script_text=script, voices=voices, provider="sapi"
    )
    row = sapi_job
    deadline = time.time() + 240
    while row.get("status") in ("queued", "synthesizing") and time.time() < deadline:
        time.sleep(2)
        row = get_job(str(sapi_job["id"]), tenant="default") or row
    if row.get("status") == "failed":
        # 本机不具备条件（非 Windows / 无 PowerShell / 无中文音色）：显式失败，绝不交占位成品
        sapi_ok = bool(str(row.get("error") or "")) and not row.get("summary")
        print(f"    sapi 通道：本机不可用→显式失败无汇总={sapi_ok}｜{str(row.get('error'))[:46]}…")
    else:
        engine_summary = row.get("summary") or {}
        segs = engine_summary.get("segments") or []
        sapi_ok = (
            row.get("status") == "done"
            and engine_summary.get("simulated") is False
            and len(segs) == 3
            and all(str(seg.get("audio_ref") or "") for seg in segs)
            and all(
                resolve_audio_path(str(seg.get("audio_ref")), str(sapi_job["id"]))[0] is not None
                and (DATA_DIR / str(seg.get("audio_ref"))).stat().st_size > 200
                for seg in segs
            )
            and all(float(seg.get("duration_seconds") or 0) > 0 for seg in segs)  # 静音即不合格
            and all(seg.get("estimated") is False for seg in segs)  # wav 走实测
            and bool(engine_summary.get("full_audio"))
            and any("克隆" in str(x) for x in engine_summary.get("issues") or [])
            and bool(row.get("timeline_path"))
        )
        print(
            f"    sapi 本机真合成：status={row.get('status')}"
            f"｜真音频+实长时间轴+整轨+不克隆标注={sapi_ok}"
        )

    # 12) 跨语言译制：离线引擎产出**占位译文**——原文必须保留、必须自报非真实翻译；
    #     目标语言＝源语言时不翻译（直接配原文），绝不默默拿原文冒充译文
    zh_manifest = build_manifest(script, voices=voices, provider="sample")
    translate_manifest(zh_manifest, target_lang="en")
    trans_ok = (
        zh_manifest.get("translated") is True
        and zh_manifest.get("source_lang") == "zh"
        and zh_manifest.get("target_lang") == "en"
        and zh_manifest.get("translation_simulated") is True  # 离线：占位译文必须自报
        and all(
            seg.get("source_text") and seg.get("text") != seg.get("source_text")
            for seg in zh_manifest["segments"]
        )
        and detect_language("你好，世界") == "zh"
        and detect_language("hello world") == "en"
    )
    same_manifest = build_manifest(script, voices=voices, provider="sample")
    translate_manifest(same_manifest, target_lang="zh")
    same_ok = (
        same_manifest.get("translated") is False
        and not same_manifest["segments"][0].get("source_text")
    )
    trans_task = _dub_task("doctor-dub-8")
    create_job(trans_task, script_text=script, voices=voices, provider="sample", target_lang="en")
    trans_rows = list_jobs(task_id=trans_task.id, now=datetime.now(timezone.utc) + timedelta(seconds=90))
    trow = trans_rows[0] if trans_rows else {}
    tsum = trow.get("summary") or {}
    trans_e2e_ok = (
        trow.get("status") == "done"
        and tsum.get("translated") is True
        and tsum.get("translation_simulated") is True
        and all(s.get("source_text") for s in tsum.get("segments") or [])
        and "译制配音" in render_timeline_text(tsum)
        and "原文" in render_timeline_text(tsum)
        and "译配" in render_timeline_text(tsum)
    )
    print(
        f"    跨语言译制：占位译文+原文保留+自报={trans_ok}"
        f"｜目标=源不译={same_ok}｜sample 端到端对照={trans_e2e_ok}"
    )

    touched = [
        "doctor-dub-1",
        "doctor-dub-2",
        "doctor-dub-3",
        "doctor-dub-5",
        "doctor-dub-6",
        "doctor-dub-7",
        "doctor-dub-8",
    ]
    sapi_dir = DATA_DIR / "dubbing" / str(sapi_job["id"])
    cleanup_ok = (
        all(drop_task(t) >= 1 for t in touched)
        and all(not list_jobs(task_id=t) for t in touched)
        and not sapi_dir.exists()
    )
    print(f"    作业回收：记录清空 + 已落盘音频目录回收={cleanup_ok}")

    ok = (
        manifest_ok
        and novel_ok
        and lifecycle_ok
        and timeline_ok
        and tenant_ok
        and gateway_fail
        and missing_fail
        and empty_ok
        and path_ok
        and ref_ok
        and est_ok
        and provider_ok
        and missing_engine_ok
        and sapi_ok
        and trans_ok
        and same_ok
        and trans_e2e_ok
        and cleanup_ok
    )
    if not ok:
        print(
            "    ! 全自动配音异常：请检查 app/core/dubbing.py 的台词切分、引擎合成、"
            "诚实失败与时间轴汇总。"
        )
    return ok


def check_petgen() -> bool:
    """校验桌面宠物样例：清单与色板、sample **真落盘**的可运行宠物包、租户隔离、
    imagegen 未配置显式失败与逐帧成本熔断、帧读取路径边界与作业回收。

    与图片/视频生成同一原则，但多一条：sample 不只出清单，而是用纯标准库画出真 PNG 帧、
    装成真 zip（交付形态本身要可回归）；同时 ``simulated=true`` 如实说明帧不是美术稿。
    """
    import json
    import zipfile
    from datetime import datetime, timedelta, timezone

    from app.config import get_config
    from app.core.petgen import (
        ACTIONS,
        KEY_HEX,
        PETS_DIR,
        build_manifest,
        build_package,
        create_job,
        drop_task,
        frame_bytes,
        get_job,
        list_jobs,
    )
    from app.core.types import Artifact, TaskRecord

    print("\n[桌面宠物样例]")

    visual = {
        "visual_direction": {
            "style": "圆润扁平",
            "mood": "治愈",
            "palette": [{"hex": "#FF8A3D"}, {"hex": "#2E3A59"}, {"hex": "#F2E8DC"}],
        },
        "image_prompts": [
            {"id": "IMG1", "usage": "封面", "prompt": "橙色仓鼠吉祥物全身像"},
            {"id": "IMG2", "usage": "B-roll", "prompt": ""},
        ],
    }
    picked = ["idle", "walk"]
    cfg = get_config()
    saved = (
        cfg.pet_gen.max_frames,
        cfg.image_gen.provider,
        cfg.image_gen.base_url,
        cfg.cost_budget_usd,
    )
    try:
        cfg.pet_gen.max_frames = 16  # 帧数口径固定，避免环境夹帧让落盘断言随配置漂移
        manifest = build_manifest(visual, provider="sample", brand="测试品牌", actions=picked)
        want_frames = sum(ACTIONS[action]["frames"] for action in picked)
        manifest_ok = (
            manifest["actions"] == picked  # 未知动作被剔除、顺序按服务端动作目录
            and manifest["frame_count"] == want_frames
            and manifest["key_color"] == KEY_HEX
            and manifest["size"] == cfg.pet_gen.frame_size
            and manifest["palette"]["body"].upper() == "#FF8A3D"  # 色板优先跟随 visual_brief
            and manifest["palette_source"] == "visual_brief"
            and manifest["simulated"] is True
            and all(segment["prompt"] for segment in manifest["segments"])
        )
        print(
            f"    生成清单：{manifest['actions']}｜{manifest['frame_count']} 帧｜"
            f"色板来源={manifest['palette_source']}｜{'正确' if manifest_ok else '异常'}"
        )

        def _pet_task(tid: str, tenant: str) -> TaskRecord:
            return TaskRecord(
                id=tid,
                tenant=tenant,
                artifacts=[
                    Artifact(id=f"{tid}-vb", task_id=tid, type="visual_brief", content=visual)
                ],
            )

        task = _pet_task("doctor-pet-1", "default")
        job = create_job(task, provider="sample", name="小仓", actions=picked)
        base = datetime.now(timezone.utc)
        queued = job["status"] == "queued" and job["provider"] == "sample"
        mid = list_jobs(task_id=task.id, now=base + timedelta(seconds=1))
        generating = len(mid) == 1 and mid[0]["status"] in ("queued", "generating")
        final = list_jobs(task_id=task.id, now=base + timedelta(seconds=120))
        row = final[0] if final else {}
        done = row.get("status") == "done" and row.get("progress") == 100
        lifecycle_ok = queued and generating and done
        print(f"    样例引擎：排队={queued}｜推进={generating}｜完成={done}")

        # sample 必须真的把帧与包落盘：交付形态本身要能被回归，而不是只在内存里编一个清单
        pet_dir = PETS_DIR / str(job["id"])
        frames_dir = pet_dir / "frames"
        on_disk = sorted(path.name for path in frames_dir.glob("*.png")) if frames_dir.is_dir() else []
        png_ok = len(on_disk) == want_frames and all(
            (frames_dir / name).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" for name in on_disk
        )
        try:
            pet_json = json.loads((pet_dir / "pet.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pet_json = {}
        package_ok = (
            pet_json.get("name") == "小仓"
            and pet_json.get("key_color") == KEY_HEX
            and pet_json.get("simulated") is True
            and [action["name"] for action in pet_json.get("actions") or []] == picked
            and sum(len(action["frames"]) for action in pet_json.get("actions") or []) == want_frames
            and pet_json.get("runner") == "runner/pet.py"
        )
        readme = (pet_dir / "README.md")
        readme_ok = readme.is_file() and "python" in readme.read_text(encoding="utf-8")
        artifacts_ok = png_ok and package_ok and readme_ok and str(row.get("pet_ref", "")).startswith("pets/")
        print(
            f"    帧与包落盘：PNG {len(on_disk)}/{want_frames}｜pet.json 动作帧={package_ok}｜"
            f"README={readme_ok}"
        )

        visible = get_job(job["id"], tenant="default") is not None
        leaked = get_job(job["id"], tenant="acme") is not None or bool(list_jobs(tenant="acme"))
        tenant_ok = visible and not leaked
        print(f"    租户隔离：本租户可见={visible}｜跨租户泄漏={leaked}")

        # 帧读取不放开任意路径：只认已知动作、正整数帧号，且必须仍在 pets/ 目录下
        blob = frame_bytes(row, "idle", 1)
        frame_ok = blob is not None and blob[:4] == b"\x89PNG"
        refused = (
            frame_bytes(row, "fly", 1) is None
            and frame_bytes(row, "idle", 0) is None
            and frame_bytes(row, "idle", want_frames + 9) is None
            and frame_bytes({**row, "dir": "../assets"}, "idle", 1) is None
        )
        print(f"    帧读取边界：可读帧={frame_ok}｜未知动作/越界帧号/越界目录一律拒={refused}")

        archive = build_package(row)
        with zipfile.ZipFile(archive) as bundle:
            names = bundle.namelist()
        zip_ok = (
            "pet.json" in names
            and "README.md" in names
            and "runner/pet.py" in names  # 包内自带运行器，脱离仓库也能跑
            and sum(1 for name in names if name.startswith("frames/")) == want_frames
        )
        print(f"    宠物包 zip：{len(names)} 项｜含运行器={'runner/pet.py' in names}")

        task2 = _pet_task("doctor-pet-2", "acme")
        cfg.image_gen.provider = "sample"
        cfg.image_gen.base_url = ""
        unconfigured = create_job(task2, provider="imagegen", actions=picked)
        honest_fail = (
            unconfigured["status"] == "failed"
            and "未配置" in str(unconfigured["error"])
            and "imagegen" in str(unconfigured["error"])
        )
        print(f"    imagegen（未配端点）：status={unconfigured['status']}｜error={str(unconfigured['error'])[:32]}…")

        # 成本熔断：逐帧计费 → 受理前按帧数预估，超预算不提交（attempts/submitted 保持为 0/False）
        cfg.image_gen.provider = "openai"
        cfg.image_gen.base_url = "https://imagegen.example.invalid/v1"
        cfg.cost_budget_usd = 0.0001
        fused = create_job(task2, provider="imagegen", actions=picked)
        fuse_ok = (
            fused["status"] == "failed"
            and "成本熔断" in str(fused["error"])
            and fused["estimated_cost_usd"] > 0
            and not fused["attempts"]
            and fused["submitted"] is False
        )
        print(
            f"    imagegen 成本熔断：fail-loud 且未提交={fuse_ok}｜"
            f"预估=${fused['estimated_cost_usd']:.2f}（{want_frames} 帧）"
        )
    finally:
        (
            cfg.pet_gen.max_frames,
            cfg.image_gen.provider,
            cfg.image_gen.base_url,
            cfg.cost_budget_usd,
        ) = saved

    guard_ok = False
    try:
        create_job(TaskRecord(id="doctor-pet-3", tenant="default"), provider="sample")
    except ValueError:
        guard_ok = True
    print(f"    无 visual_brief 防护：拒绝创建={guard_ok}")

    touched = [task.id, task2.id]
    cleanup_ok = all(drop_task(t) >= 1 for t in touched)
    gone = all(not list_jobs(task_id=t) for t in touched)
    dir_gone = not pet_dir.exists()  # 作业回收要连帧目录一起清掉，不留孤儿素材
    cleanup_ok = cleanup_ok and gone and dir_gone
    print(f"    作业回收：recovered={cleanup_ok}｜帧目录已清={dir_gone}")

    ok = (
        manifest_ok
        and lifecycle_ok
        and artifacts_ok
        and tenant_ok
        and frame_ok
        and refused
        and zip_ok
        and honest_fail
        and fuse_ok
        and guard_ok
        and cleanup_ok
    )
    if not ok:
        print("    ! 桌面宠物样例异常：请检查 app/core/petgen.py 的状态机、帧落盘与诚实失败。")
    return ok


def check_skillgen() -> bool:
    """校验创作技能提炼样例：作品采集口径（文档 / 字幕 / 文本产物，含伪字幕防误判）、
    rules **真落盘**的 SKILL.md 结构、作品上限与租户隔离、llm 未配网关显式失败与成本熔断、
    作业与技能目录回收。

    这条通道的产品主张是「数字来自正文的真实统计」，所以断言只盯两件事：统计口径不能被
    行内转述的时间戳带偏，产物必须真的落在 ``skills/`` 下（空转不写文件的约定同样要能回归）。
    """
    import json
    import zipfile
    from datetime import datetime, timedelta, timezone

    from app.config import get_config
    from app.core.assets import ASSETS_DIR
    from app.core.skillgen import (
        SKILLS_DIR,
        build_package,
        collect_materials,
        create_job,
        drop_task,
        get_job,
        list_jobs,
        profile_text,
        skill_document,
    )
    from app.core.types import Artifact, Brief, BriefAsset, TaskRecord

    print("\n[创作技能提炼样例]")

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    prose = (
        "# 往期成稿：便携耳机\n\n"
        "别急着买，先听完这三句。第一天就把话说完。\n"
        "研读摘要里转述过上期字幕的时间戳：00:00:00,000 --> 00:00:03,500；"
        "这种行内引用不是字幕文件，若被当成一条巨型字幕，全篇的段落与语速就一起失真了。\n"
        "所以先把需求写下来再下单。\n"
    )
    srt = (
        "1\n00:00:00,000 --> 00:00:03,500\n别急着买，先听完这三句\n\n"
        "2\n00:00:03,500 --> 00:00:07,000\n我用三个月踩了三次坑才敢说这句话\n\n"
        "3\n00:00:07,000 --> 00:00:12,000\n你不是需要它，你是需要它解决的问题\n"
    )
    copy_text = (
        "第一天就把话说完：别急着买。\n"
        "我把三次踩坑的顺序讲清楚，先给结论。\n"
        "通勤路上连戴两小时不胀，这才是每天戴的前提。\n"
        "所以先把需求写下来再下单。\n"
    )
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    (ASSETS_DIR / "doctor_skill_prose.md").write_text(prose, encoding="utf-8")
    (ASSETS_DIR / "doctor_skill_copy.srt").write_text(srt, encoding="utf-8")

    def _skill_task(tid: str, tenant: str) -> TaskRecord:
        return TaskRecord(
            id=tid,
            tenant=tenant,
            brief=Brief(
                brand="测试品牌",
                channel="小红书",
                keywords=["每天戴"],
                constraints=["不得承诺疗效"],
                assets=[
                    BriefAsset(kind="document", ref="doctor_skill_prose.md", title="往期成稿"),
                    BriefAsset(kind="document", ref="doctor_skill_copy.srt", title="上期口播字幕"),
                ],
            ),
            artifacts=[
                Artifact(id=f"{tid}-cd", task_id=tid, type="copy_draft", title="文案初稿", text=copy_text)
            ],
        )

    cfg = get_config()
    saved = (cfg.skill_gen.max_works, cfg.llm.provider, cfg.llm.base_url, cfg.cost_budget_usd)
    try:
        task = _skill_task("doctor-skill-1", "default")
        works = collect_materials(task)["works"]
        by_title = {work["title"]: work for work in works}
        prose_profile = by_title.get("往期成稿", {}).get("profile", {})
        srt_profile = by_title.get("上期口播字幕", {}).get("profile", {})
        cues = srt_profile.get("subtitle") or {}
        intake_ok = (
            len(works) == 3
            and any(str(work["origin"]) == "artifact:copy_draft" for work in works)
            and "subtitle" not in prose_profile  # 行内时间戳不算字幕
            and cues.get("cues") == 3
            and float(cues.get("cue_len_median") or 99) <= 20
            and float(cues.get("chars_per_minute") or 0) > 0
        )
        print(f"    作品采集：{len(works)} 份｜伪字幕未被当成字幕={intake_ok}")

        probe = profile_text("你真的需要它吗？还是只是想要一个理由。\n")
        stat_ok = probe["sentences"] == 2 and abs(probe["question_ratio"] - 0.5) < 1e-9
        print(f"    断句口径：句数={probe['sentences']}｜疑问句占比={probe['question_ratio']}｜{'正确' if stat_ok else '异常'}")

        job = create_job(task, provider="rules", now=base)
        mid = list_jobs(task_id=task.id, now=base + timedelta(seconds=2))[0]
        row = list_jobs(task_id=task.id, now=base + timedelta(seconds=120))[0]
        lifecycle_ok = (
            job["status"] == "queued"
            and not job["estimated_cost_usd"]
            and mid["status"] == "distilling"
            and 0 < mid["progress"] < 99
            and row["status"] == "done"
            and str(row["skill_ref"]).startswith("skills/")
        )
        print(f"    rules 状态机：排队→提炼中（{mid['progress']}%）→完成={lifecycle_ok}")

        skill_dir = SKILLS_DIR / str(job["id"])
        names = sorted(path.name for path in skill_dir.iterdir()) if skill_dir.is_dir() else []
        document = skill_document(row)
        try:
            payload = json.loads((skill_dir / "skill.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            payload = {}
        files_ok = (
            names == ["README.md", "SKILL.md", "skill.json"]
            and document.startswith("---\nname: ")
            and all(section in document for section in ("## 步骤", "## 交付前自检", "## 反例"))
            and "simulated=true" in document
            and payload.get("provider") == "rules"
            and payload.get("skill", {}).get("simulated") is True
        )
        print(f"    真落盘：{names}｜frontmatter 与章节={files_ok}")

        steps = row["skill"]["steps"]
        steps_ok = bool(steps) and all(step.get("evidence") for step in steps)
        wording_ok = "每 1 句" not in document and "0.0 字" not in document
        print(f"    可追溯：每条步骤带依据={steps_ok}｜文案数字口径={wording_ok}")

        with zipfile.ZipFile(build_package(row)) as bundle:
            members = sorted(bundle.namelist())
        zip_ok = members == ["README.md", "SKILL.md", "skill.json"]
        print(f"    技能包 zip：{members}｜可直接交给扩展插件={zip_ok}")

        visible = get_job(job["id"], tenant="default") is not None
        leaked = get_job(job["id"], tenant="acme") is not None or bool(list_jobs(tenant="acme"))
        tenant_ok = visible and not leaked
        print(f"    租户隔离：本租户可见={visible}｜跨租户泄漏={leaked}")

        cfg.skill_gen.max_works = 1
        create_job(task, provider="rules", now=base + timedelta(hours=1))
        capped = list_jobs(task_id=task.id, now=base + timedelta(hours=1, seconds=120))[0]
        cap_ok = len(capped["sources"]) == 1 and any(
            "超出单作业上限" in issue for issue in capped["issues"]
        )
        single_ok = "不构成通用方法" in str(capped["skill"]["anti_patterns"][0])
        print(f"    作品上限：只纳入 {len(capped['sources'])} 份并如实记 issues={cap_ok}｜单样本反例={single_ok}")

        cfg.llm.provider = "mock"
        cfg.llm.base_url = ""
        unfixed = create_job(task, provider="llm", now=base)
        honest_ok = unfixed["status"] == "failed" and "未配置" in str(unfixed["error"])
        print(f"    llm（未配网关）：status={unfixed['status']}｜error={str(unfixed['error'])[:34]}…")

        cfg.llm.provider = "openai"
        cfg.llm.base_url = "https://llm-gateway.example.invalid/v1"
        cfg.cost_budget_usd = 0.0001
        fused = create_job(task, provider="llm", now=base)
        fuse_ok = (
            fused["status"] == "failed"
            and "成本熔断" in str(fused["error"])
            and fused["estimated_cost_usd"] > 0
            and not fused["attempts"]
        )
        print(f"    llm 成本熔断：fail-loud 且未提交={fuse_ok}｜预估=${fused['estimated_cost_usd']:.3f}")
    finally:
        (
            cfg.skill_gen.max_works,
            cfg.llm.provider,
            cfg.llm.base_url,
            cfg.cost_budget_usd,
        ) = saved

    guard_ok = False
    try:
        create_job(TaskRecord(id="doctor-skill-2", tenant="default"), provider="rules")
    except ValueError:
        guard_ok = True
    print(f"    无作品防护：拒绝创建={guard_ok}")

    recovered = drop_task(task.id)
    gone = not list_jobs(task_id=task.id)
    dir_gone = not skill_dir.exists()  # 作业回收要连技能目录一起清掉，不留孤儿产物
    cleanup_ok = recovered >= 3 and gone and dir_gone
    print(f"    作业回收：recovered={recovered}｜技能目录已清={dir_gone}")

    ok = (
        intake_ok
        and stat_ok
        and lifecycle_ok
        and files_ok
        and steps_ok
        and wording_ok
        and zip_ok
        and tenant_ok
        and cap_ok
        and single_ok
        and honest_ok
        and fuse_ok
        and guard_ok
        and cleanup_ok
    )
    if not ok:
        print("    ! 创作技能提炼样例异常：请检查 app/core/skillgen.py 的采集口径、落盘与诚实失败。")
    return ok


def check_copyright() -> bool:
    """校验解说/二创版权清单：解说语境触发风险项且强制人工复核，普通品牌不误伤。"""
    from app.knowledge.copyright import copyright_checklist, detect_commentary

    print("\n[二创版权清单]")

    commentary_brief = {
        "channel": "B站",
        "industry": "文化娱乐",
        "product": "动漫解说",
        "deliverables": ["视频脚本 1 支"],
        "constraints": ["不得整段搬运", "标注剧透"],
    }
    review = copyright_checklist(commentary_brief, "这是一期动漫剧情解说")
    applies_ok = review["applies"] is True and len(review["items"]) >= 5 and review["needs_human_review"] is True
    topics = {str(item.get("topic")) for item in review["items"]}
    coverage_ok = {"片源引用比例", "BGM/配乐版权", "平台搬运判定", "剧透标注"} <= topics
    print(f"    解说语境：applies={review['applies']}｜风险项 {len(review['items'])} 条｜强制人工复核={review['needs_human_review']}")

    plain_brief = {"channel": "小红书", "industry": "美妆个护", "product": "面霜", "deliverables": ["种草笔记"], "constraints": []}
    plain_review = copyright_checklist(plain_brief, "一支面霜的质地与使用说明")
    no_false_positive = plain_review["applies"] is False and plain_review["needs_human_review"] is False
    detected, _signals = detect_commentary(plain_brief, "")
    no_false_positive = no_false_positive and detected is False
    print(f"    普通品牌不误伤：applies={plain_review['applies']}｜需人工={plain_review['needs_human_review']}")

    ok = applies_ok and coverage_ok and no_false_positive
    if not ok:
        print("    ! 二创版权清单异常：请检查 app/knowledge/copyright.py 的判定与清单。")
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


def check_digest_export() -> bool:
    """校验素材文档研读通路与 A4 长文分篇。

    ① ``kind=document`` 素材走 ``digest_documents``：产物形状完整、要点取自
       原文（不是生成器凭空造的）、无问题入档，``DOC.digest.map/reduce`` 已注册；
    ② 无文档素材时 digest **零调用零产物**——这是黄金基线不受新通路影响的前提；
    ③ A4 长文分篇：字数区间检测命中/不命中，``A4.copy.version`` 单版本契约
       （version 而非 versions，id 按需选取）。
    """
    from app.agents.a4_copywriter import VERSION_STYLES, _word_range
    from app.config import ASSETS_DIR
    from app.core.blackboard import blackboard
    from app.core.digest import digest_documents
    from app.core.types import Artifact, Brief
    from app.llm.mock import GENERATORS

    print("\n[素材研读与成品导出]")

    purposes_ok = all(
        name in GENERATORS for name in ("DOC.digest.map", "DOC.digest.reduce", "A4.copy.version")
    )
    print(f"    {'✓' if purposes_ok else '✗'} mock 用途注册：DOC.digest.map / DOC.digest.reduce / A4.copy.version")

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    sample = ASSETS_DIR / "doctor_digest.txt"
    sample.write_text(
        "第一章 臣虎的伪装\n臣虎在匣庭扮作番长，直到第三封信被互换。\n"
        "他说：「臣服不是软弱，是我选的姿态。」\n"
        "第二章 米芙露的选择\n米芙露亲吻了左脸颊，Kingmaker 的棋局自此翻面。\n",
        encoding="utf-8",
    )
    doc_brief = Brief(
        brand="自检品牌", product="自检产品", channel="小红书", industry="消费品",
        assets=[{"kind": "document", "ref": "doctor_digest.txt", "title": "自检文档"}],
    )
    content = digest_documents(doc_brief)
    docs = [d for d in (content.get("documents") or []) if isinstance(d, dict)]
    facts = [str(f) for f in (docs[0].get("key_facts") if docs else [])]
    digest_ok = (
        len(docs) == 1
        and bool(content.get("creative_brief"))
        and bool(content.get("sources"))
        and content["sources"][0].get("chunks") == 1
        and not content.get("issues")
        and any("臣虎" in fact or "米芙露" in fact for fact in facts)
    )
    print(
        f"    {'✓' if digest_ok else '✗'} 文档研读：1 份文档 1 块 → 事实要点 {len(facts)} 条"
        f"（取自原文）+ 创作参考，无问题入档"
    )

    # 研读产物必须能作为 Artifact 通过黑板写入（Literal 类型校验是真实失败面）
    from app.core.events import new_id

    stored = blackboard.put_artifact(
        Artifact(task_id="doctor-selfcheck", agent_id="A0", type="document_digest",
                 id=new_id("art"), title="自检研读", content=content)
    )
    blackboard_ok = bool(stored.id) and blackboard.latest_artifact(
        "doctor-selfcheck", "document_digest"
    ) is not None
    print(f"    {'✓' if blackboard_ok else '✗'} 研读产物可写入黑板：type=document_digest 通过契约校验")

    plain_brief = Brief(brand="自检品牌", product="自检产品", channel="小红书", industry="消费品")
    empty = digest_documents(plain_brief)
    zero_ok = (
        not empty.get("documents")
        and not empty.get("issues")
        and (empty.get("stats") or {}).get("map_calls") == 0
        and (empty.get("stats") or {}).get("reduce_calls") == 0
    )
    print(f"    {'✓' if zero_ok else '✗'} 无文档素材：零调用零产物（提示词与历史逐字一致）")

    long_brief = Brief(
        brand="自检品牌", product="自检产品", channel="小红书", industry="消费品",
        constraints=["连载三篇，每篇 2500-3200 字"],
    )
    wr = _word_range(long_brief)
    wr_none = _word_range(plain_brief)
    range_ok = wr == (2500, 3200) and wr_none is None
    print(f"    {'✓' if range_ok else '✗'} 长文检测：2500-3200 字 → 分篇 {wr}；无字数约束 → 整版路径（{wr_none}）")

    version_out = GENERATORS["A4.copy.version"](
        {"brief": long_brief.model_dump(mode="json"), "version_style": "V2"}
    )
    version = version_out.get("version") if isinstance(version_out.get("version"), dict) else {}
    version_ok = (
        "versions" not in version_out
        and str(version.get("id") or "") == "V2"
        and isinstance(version_out.get("claims"), list)
        and bool(str(version.get("body") or "").strip())
    )
    styles_ok = [style for _, style in VERSION_STYLES] == ["主推版", "理性版", "感性版"]
    print(
        f"    {'✓' if version_ok and styles_ok else '✗'} 分篇单版本契约："
        f"version.id={version.get('id')}｜claims {len(version_out.get('claims') or [])} 条｜正文 {len(str(version.get('body') or ''))} 字"
    )

    ok = all([purposes_ok, digest_ok, blackboard_ok, zero_ok, range_ok, version_ok, styles_ok])
    if not ok:
        print("    ! 研读/分篇异常：产物形状或缺零调用保证不成立，黄金基线可能被新通路扰动")
    return ok


def check_factory() -> bool:
    """校验影视制作工厂（旁路作业范式的新实例）：岗位注册表与部门归档、预设展开成图、
    拓扑/端口/环路/必需交付物的静态体检、增量缓存、**离线通道真落盘**（PNG/WAV/SRT
    等标准库产物）与生成岗位的 ``simulated`` 自报、异步作业生命周期（受理→后台线程推进→
    轮询）、**未知目标点名拒绝**、**岗位边界取消**（剩余岗位标 cancelled、cancelling 状态机）、
    **增量缓存提前落盘**（首岗交付即可在 cache.json 读到）、**进程重启遗留运行回收**
    （含 cancelling，标中断但保留已完成岗位缓存）、租户隔离、产物路径边界与项目删除回收。

    本轮补强两类口径：**结构身份体检**（岗位 id 为空/重复不得误报成环路，悬空端点、
    自环、重复连线各自点名）与**交付验收**（打包前那道门：终端岗位未交付、缓存过期、
    产物引用文件不在盘都阻断交付包，占位产物只告警——见 app/factory/acceptance.py）；
    验收之外再加**交付台账**（append-only 留痕：每次打包的放行/拦下都落一条到项目目录
    里的 deliveries.json，只读不建作业——固定名交付包只说「现在这版」，台账才回答「交过没、何时、当时量出什么」）；
    台账之外再钉一条**回看闭环**（交付包内含最下游那条成片时间轴 timeline.json，它引用的素材必须逐个在包内——
    拆包的人不只拿到散件，还能拼回那条片子）；闭环之上再做**回看对账**（成片区内容指纹存进台账与 manifest，
    刚交付时屏幕这条与装箱那条一致、篡改缓存后应翻脸判不一致——不比旧包字节只比内容，诚实回答“改了没”）。

    诚实口径与其余旁路通道一致：离线路径只用标准库把产物落到项目工作区并一律带
    ``simulated=True``，绝不假装接了真机；缺素材/缺交付物如实落到节点状态而不是补一条假连线。
    """
    import shutil
    import time

    from app.factory import engine, presets
    from app.factory import graph as graph_model
    from app.factory import store as factory_store
    from app.factory.nodes import source as source_module
    from app.factory.spec import DEPARTMENTS, NodeError, all_specs

    print("\n[影视制作工厂（岗位图引擎 + 异步作业）]")

    # 1) 注册表：60 个岗位、每个岗位都归进已知部门（无孤儿）、按部门出面板
    specs = all_specs()
    dept_keys = {key for key, _, _ in DEPARTMENTS}
    registry_ok = (
        len(specs) == 60
        and all(spec.run is not None for spec in specs)
        and not [spec for spec in specs if spec.dept not in dept_keys]
    )
    panel = factory_store.store.palette()
    panel_ok = bool(panel["departments"]) and not panel["orphan_nodes"] and panel["node_count"] == len(specs)
    print(f"    岗位注册表：{len(specs)} 个岗位｜全部有执行器且归部={registry_ok}｜面板分组无孤儿={panel_ok}")

    # 2) 预设展开成图 + 静态体检（都不许带 error 级问题）
    preset_ok = True
    row_counts: dict[str, int] = {}
    for key in presets.PRESETS:
        graph = graph_model.Graph.model_validate(presets.graph_dict(key))
        issues = graph_model.validate_graph(graph)
        errs = [issue for issue in issues if issue.level == "error"]
        row_counts[key] = len(graph.nodes)
        if not graph.nodes or not graph.edges or errs:
            preset_ok = False
            print(f"    预设 {key}: ✗ 节点 {len(graph.nodes)} 条 error {len(errs)}：{errs[:1]}")
    print(f"    预设展开+体检无 error：{list(row_counts.items())}={preset_ok}")

    # 3) 环路 + 端口类型不匹配：拓扑排序点名成环，体检对 text→timeline 只告警不判死
    cycle_graph = graph_model.Graph.model_validate(
        {
            "nodes": [
                {"id": "a", "type": "text_input", "pos": [0, 0], "params": {"text": "x"}},
                {"id": "b", "type": "text_input", "pos": [1, 1], "params": {"text": "y"}},
            ],
            "edges": [
                {"id": "e1", "src": "a", "src_port": "text", "dst": "b", "dst_port": "text"},
                {"id": "e2", "src": "b", "src_port": "text", "dst": "a", "dst_port": "text"},
            ],
        }
    )
    try:
        graph_model.topo_order(cycle_graph)
        cycle_ok = False
    except NodeError:
        cycle_ok = True
    mismatch_graph = graph_model.Graph.model_validate(
        {
            "nodes": [
                {"id": "a", "type": "text_input", "pos": [0, 0], "params": {"text": "x"}},
                {"id": "s", "type": "screening", "pos": [1, 1]},
            ],
            "edges": [{"id": "e1", "src": "a", "src_port": "text", "dst": "s", "dst_port": "timeline"}],
        }
    )
    mismatch = [issue for issue in graph_model.validate_graph(mismatch_graph) if issue.level == "warn"]
    mismatch_ok = bool(mismatch) and not [i for i in graph_model.validate_graph(mismatch_graph) if i.level == "error"]
    print(f"    环路拒执行={cycle_ok}｜端口类型不匹配只告警不判死={mismatch_ok}")

    # 3b) 结构身份体检：id 为空/重复不能被误报成环路；悬空端点、自环、重复连线各归各位
    dup_graph = graph_model.Graph.model_validate(
        {
            "nodes": [
                {"id": "n1", "type": "text_input", "pos": [0, 0], "params": {"text": "a"}},
                {"id": "n1", "type": "text_input", "pos": [1, 1], "params": {"text": "b"}},
            ],
            "edges": [],
        }
    )
    dup_issues = graph_model.validate_graph(dup_graph)
    dup_msg_ok = any("岗位 id 重复" in i.message for i in dup_issues) and not any("环路" in i.message for i in dup_issues)
    try:
        graph_model.topo_order(dup_graph)
        dup_topo_ok = False
    except NodeError as error:
        dup_topo_ok = "id 冲突" in str(error) and "环路" not in str(error)
    emptyid_graph = graph_model.Graph.model_validate(
        {"nodes": [{"id": "", "type": "text_input", "pos": [0, 0], "params": {"text": "a"}}], "edges": []}
    )
    emptyid_ok = any("id 为空" in i.message for i in graph_model.validate_graph(emptyid_graph))
    dangling_graph = graph_model.Graph.model_validate(
        {
            "nodes": [{"id": "a", "type": "text_input", "pos": [0, 0], "params": {"text": "x"}}],
            "edges": [{"id": "e1", "src": "a", "src_port": "text", "dst": "ghost", "dst_port": "text"}],
        }
    )
    dangling = [i for i in graph_model.validate_graph(dangling_graph) if i.level == "error"]
    dangling_ok = any("已不存在" in i.message for i in dangling) and not any("未知岗位" in i.message for i in dangling)
    selfloop_graph = graph_model.Graph.model_validate(
        {
            "nodes": [{"id": "a", "type": "text_input", "pos": [0, 0], "params": {"text": "x"}}],
            "edges": [{"id": "e1", "src": "a", "src_port": "text", "dst": "a", "dst_port": "text"}],
        }
    )
    selfloop = [i for i in graph_model.validate_graph(selfloop_graph) if i.level == "error"]
    selfloop_ok = any("连回了自己" in i.message for i in selfloop)
    dupe_edge_graph = graph_model.Graph.model_validate(
        {
            "nodes": [
                {"id": "a", "type": "text_input", "pos": [0, 0], "params": {"text": "x"}},
                {"id": "b", "type": "text_input", "pos": [1, 1], "params": {"text": "y"}},
            ],
            "edges": [
                {"id": "e1", "src": "a", "src_port": "text", "dst": "b", "dst_port": "text"},
                {"id": "e2", "src": "a", "src_port": "text", "dst": "b", "dst_port": "text"},
            ],
        }
    )
    dup_edge_ok = any(
        i.level == "warn" and "重复连线" in i.message for i in graph_model.validate_graph(dupe_edge_graph)
    )
    structure_ok = dup_msg_ok and dup_topo_ok and emptyid_ok and dangling_ok and selfloop_ok and dup_edge_ok
    print(
        f"    结构体检补强：id 重复点名不误报环路={dup_msg_ok and dup_topo_ok}｜空 id 定位={emptyid_ok}"
        f"｜悬空端点说「已不存在」={dangling_ok}｜自环点名={selfloop_ok}｜重复连线告警={dup_edge_ok}={structure_ok}"
    )

    # 4) 引擎 + 增量缓存：编剧台整图跑通，再跑一次全部命中缓存（未重跑）
    script_ws = engine.open_workspace("doctor-factory-script", "")
    script_graph = graph_model.Graph.model_validate(presets.graph_dict("script"))
    first = engine.run_graph(script_graph, script_ws)
    second = engine.run_graph(script_graph, script_ws)
    engine_ok = (
        first["ok"]
        and first["counts"]["ok"] == len(script_graph.nodes)
        and first["counts"]["error"] == 0
        and second["counts"]["cached"] == len(script_graph.nodes)
        and second["counts"]["ok"] == 0
    )
    print(
        f"    引擎整图+增量缓存：首跑 ok={first['ok']}（{first['counts']['ok']} 岗位）"
        f"｜二跑全缓存={second['counts']['cached']}={engine_ok}"
    )

    # 4b) 运行预检：与 run_graph 同一套指纹口径，只读算出复用/重跑。
    #     预检若和实跑口径不一致，就是善意的谎言——所以这里把三种关键结论都钉死。
    preview_all = engine.preview_graph(script_graph, script_ws)
    preview_all_ok = (
        preview_all["counts"]["cached"] == len(script_graph.nodes)
        and preview_all["counts"]["run"] == 0
        and preview_all["counts"]["total"] == len(script_graph.nodes)
    )
    changed_dict = script_graph.model_dump()
    for node in changed_dict["nodes"]:
        if node["id"] == "n-sb":
            node["params"] = {**node.get("params", {}), "__probe": 1}
    changed_graph = graph_model.Graph.model_validate(changed_dict)
    preview_changed = engine.preview_graph(changed_graph, script_ws)
    preview_changed_ids = {row["node_id"] for row in preview_changed["nodes"] if row["state"] == "run"}
    cache_before = script_ws.cache_file.stat().st_mtime_ns
    engine.preview_graph(script_graph, script_ws)
    preview_readonly_ok = script_ws.cache_file.stat().st_mtime_ns == cache_before
    preview_force = engine.preview_graph(script_graph, script_ws, force=True)
    preview_target = engine.preview_graph(script_graph, script_ws, targets=["n-sup"])
    preview_node_force = engine.preview_graph(
        script_graph, script_ws, targets=["n-sb"], force_nodes=["n-sb"]
    )
    preview_node_row = {row["node_id"]: row for row in preview_node_force["nodes"]}["n-sb"]
    preview_ok = (
        preview_all_ok
        and preview_changed["counts"]["cached"] == len(script_graph.nodes) - 3
        and preview_changed_ids == {"n-sb", "n-sup", "n-act"}
        and any("上游" in row["reason"] for row in preview_changed["nodes"] if row["node_id"] == "n-act")
        and preview_force["counts"]["cached"] == 0
        and preview_force["counts"]["run"] == len(script_graph.nodes)
        and set(preview_target["plan"]) < set(preview_all["plan"])
        and "n-sup" in preview_target["plan"]
        and preview_target["counts"]["run"] == 0
        and preview_node_force["counts"]["total"] == len(preview_node_force["plan"])
        and preview_node_force["counts"]["cached"] == len(preview_node_force["plan"]) - 1
        and preview_node_force["counts"]["run"] == 1
        and preview_node_force["force_nodes"] == ["n-sb"]
        and preview_node_row.get("state") == "run"
        and "强制重跑" in str(preview_node_row.get("reason") or "")
        and preview_node_row.get("forced") is True
        and preview_readonly_ok
    )
    print(
        f"    运行预检：全缓存={preview_all['counts']['cached']}｜改分镜只波及"
        f"{sorted(preview_changed_ids)}｜force 全重跑={preview_force['counts']['run']}"
        f"｜岗位强跑={preview_node_force['counts']['run']}/{preview_node_force['counts']['total']}"
        f"｜目标闭包全缓存且不写盘={preview_readonly_ok}={preview_ok}"
    )

    # 5) 诚实失败落到节点：空文本岗位报错、缺必需交付物点名端口、整轮判失败
    empty_graph = graph_model.Graph.model_validate(
        {"nodes": [{"id": "t", "type": "text_input", "pos": [0, 0], "params": {"text": ""}}], "edges": []}
    )
    empty_run = engine.run_graph(empty_graph, engine.open_workspace("doctor-factory-empty", ""))
    empty_ok = (
        empty_run["ok"] is False
        and empty_run["runs"]["t"]["status"] == "error"
        and "文本输入节点为空" in empty_run["runs"]["t"]["error"]
    )
    need_graph = graph_model.Graph.model_validate(
        {"nodes": [{"id": "s", "type": "screening", "pos": [0, 0]}], "edges": []}
    )
    need_run = engine.run_graph(need_graph, engine.open_workspace("doctor-factory-need", ""))
    need_ok = need_run["runs"]["s"]["status"] == "error" and "时间轴" in need_run["runs"]["s"]["error"]
    print(f"    诚实失败：空文本拒跑={empty_ok}｜缺必需交付物点名端口={need_ok}")

    # 6) 异步作业全生命周期（生成台：图像/视频/音频离线真落盘 + simulated 自报）
    source_module.ensure_demo_asset()
    rec = factory_store.store.create("自检工厂", "default", presets.graph_dict("gen"))
    project_id = str(rec["id"])
    # 指定了画布上不存在的岗位必须点名拒绝：静默丢掉目标会退化成「跑全图」
    try:
        factory_store.store.start_run(project_id, "default", targets=["n-ghost"])
        missing_target_ok = False
    except ValueError as error:
        missing_target_ok = "n-ghost" in str(error)
    print(f"    运行边界：未知目标点名拒绝（不退化成跑全图）={missing_target_ok}")
    accepted = factory_store.store.start_run(project_id, "default")
    deadline = time.time() + 180
    job = factory_store.store.job(accepted["id"], "default") or {}
    while job.get("status") in ("queued", "running") and time.time() < deadline:
        time.sleep(0.3)
        job = factory_store.store.job(accepted["id"], "default") or job
    workspace = factory_store.workspace_for(project_id)
    artifact_files = [p for p in workspace.artifacts_dir.rglob("*") if p.is_file()]
    cached = workspace.load_cache()
    key_images = (cached.get("n-key", {}).get("outputs", {}) or {}).get("images", {}) or {}
    gen_clips = (cached.get("n-gen", {}).get("outputs", {}) or {}).get("clips", {}) or {}
    model_cfg = (cached.get("n-model", {}).get("outputs", {}) or {}).get("model", {}) or {}
    image_items = key_images.get("items") or []
    clip_items = gen_clips.get("items") or []
    job_ok = (
        accepted["status"] == "queued"
        and job.get("status") == "ok"
        and job.get("counts", {}).get("error") == 0
        and job.get("progress") == 1.0
        and len(artifact_files) > 50  # 图像/音频/字幕/时间轴真落盘，不是内存清单
        and any(p.suffix == ".png" for p in artifact_files)
        and any(p.suffix == ".wav" for p in artifact_files)
        and bool(image_items) and all(item.get("simulated") is True for item in image_items)
        and bool(clip_items) and all(item.get("simulated") is True for item in clip_items)
        and model_cfg.get("endpoint_ready") is False and model_cfg.get("provider") == "sample"
    )
    print(
        f"    异步作业（生成台）：受理 queued={accepted['status'] == 'queued'}"
        f"｜推进 ok={job.get('status') == 'ok'}｜落盘产物 {len(artifact_files)} 个（PNG+WAV）"
        f"｜生成岗位自报 simulated={bool(image_items) and bool(clip_items)}={job_ok}"
    )

    # 6b) 存储层预检：跑完的生成台整图全缓存；预检不建作业、不跑岗位；
    #     未知目标与实跑共用 _prepare_run，一样点名拒绝。
    runs_before_preview = len(factory_store.store.list_runs(project_id, "default", limit=100))
    gen_preview = factory_store.store.preview(project_id, "default")
    gen_preview_target = factory_store.store.preview(project_id, "default", targets=["n-cut"])
    runs_after_preview = len(factory_store.store.list_runs(project_id, "default", limit=100))
    try:
        factory_store.store.preview(project_id, "default", targets=["n-ghost"])
        preview_unknown_ok = False
    except ValueError as error:
        preview_unknown_ok = "n-ghost" in str(error)
    preview_store_ok = (
        gen_preview["counts"]["total"] == len(gen_preview["plan"])
        and gen_preview["counts"]["cached"] == gen_preview["counts"]["total"]
        and gen_preview["counts"]["run"] == 0
        and set(gen_preview_target["plan"]) < set(gen_preview["plan"])
        and gen_preview_target["counts"]["run"] == 0
        and runs_before_preview == runs_after_preview
        and preview_unknown_ok
    )
    print(
        f"    存储层预检：整图全缓存={gen_preview['counts']['cached']}/{gen_preview['counts']['total']}"
        f"｜目标闭包收窄={len(gen_preview_target['plan'])}<{len(gen_preview['plan'])}"
        f"｜未新建作业={runs_before_preview == runs_after_preview}"
        f"｜未知目标点名拒绝={preview_unknown_ok}={preview_store_ok}"
    )

    # 6c) 运行历史摘要：列表页要能直接点名「续跑哪些岗位」，所以 retry_nodes 必须跟着摘要走；
    #     成功运行没有待续跑岗位，失败/受阻/取消才进名单，且摘要不夹带事件与结果。
    failed_summary = factory_store.store.public_job(
        {
            "id": "frun-doctor-history",
            "nodes": {
                "a": {"status": "ok"},
                "b": {"status": "error"},
                "c": {"status": "blocked"},
                "d": {"status": "cancelled"},
                "e": {"status": "running"},
            },
        },
        detail=False,
    )
    gen_runs = factory_store.store.list_runs(project_id, "default", limit=10)
    history_ok = (
        failed_summary.get("retry_nodes") == ["b", "c", "d"]
        and "events" not in failed_summary
        and "result" not in failed_summary
        and bool(gen_runs)
        and gen_runs[0].get("retry_nodes") == []
    )
    print(
        f"    运行历史摘要：未完成名单={failed_summary.get('retry_nodes')}"
        f"｜摘要不带事件/结果={'events' not in failed_summary and 'result' not in failed_summary}"
        f"｜成功运行无可续跑={bool(gen_runs) and gen_runs[0].get('retry_nodes') == []}={history_ok}"
    )

    # 6d) 运行统计：只读聚合最近已结束运行。同一生成台再跑一次（应全命中缓存），
    #     断言成功率、复用率、岗位画像与部门汇总都能从作业回放；统计本身不新建作业。
    runs_before_stats = len(factory_store.store.list_runs(project_id, "default", limit=100))
    restat = factory_store.store.start_run(project_id, "default")
    restat_job = factory_store.store.job(restat["id"], "default") or {}
    deadline = time.time() + 180
    while restat_job.get("status") in ("queued", "running") and time.time() < deadline:
        time.sleep(0.2)
        restat_job = factory_store.store.job(restat["id"], "default") or restat_job
    gen_stats = factory_store.store.run_stats(project_id, "default", limit=50)
    gen_stats_window1 = factory_store.store.run_stats(project_id, "default", limit=1)
    gen_stats_nodes = {row["node_id"]: row for row in gen_stats["nodes"]}
    cut_stats = gen_stats_nodes.get("n-cut") or {}
    try:
        factory_store.store.run_stats(project_id, "acme")
        stats_tenant_ok = False
    except KeyError:
        stats_tenant_ok = True
    runs_after_stats = len(factory_store.store.list_runs(project_id, "default", limit=100))
    stats_ok = (
        restat_job.get("status") == "ok"
        and gen_stats.get("window") == 2
        and gen_stats["runs"]["ok"] == 2
        and gen_stats["runs"]["error"] == 0
        and gen_stats["runs"]["success_rate"] == 1.0
        and gen_stats["runs"]["cached"] == gen_stats["runs"]["delivered"] > 0
        and gen_stats["runs"]["cache_rate"] == 0.5
        and cut_stats.get("appearances") == 2
        and cut_stats.get("ok") == 1
        and cut_stats.get("cached") == 1
        and cut_stats.get("cache_rate") == 0.5
        and any(int(row.get("avg_ms") or 0) > 0 for row in gen_stats["nodes"])
        and sum(row["appearances"] for row in gen_stats["departments"]) == 2 * len(gen_stats["nodes"])
        and gen_stats_window1.get("window") == 1
        and stats_tenant_ok
        and runs_before_stats + 1 == runs_after_stats  # 多出来的只有二跑，统计本身不建作业
    )
    print(
        f"    运行统计：窗口={gen_stats.get('window')}｜成功率={gen_stats['runs']['success_rate']}"
        f"｜复用率={gen_stats['runs']['cache_rate']}（{gen_stats['runs']['cached']}/{gen_stats['runs']['delivered']}）"
        f"｜岗位 n-cut 出现 {cut_stats.get('appearances')} 轮/复用率 {cut_stats.get('cache_rate')}"
        f"｜窗口收窄={gen_stats_window1.get('window')}｜跨租户不可读={stats_tenant_ok}"
        f"｜统计不新建作业={runs_before_stats + 1 == runs_after_stats}={stats_ok}"
    )

    # 6e) 运行对比：首跑（真交付）→ 二跑（全复用），逐岗应记「命中缓存」且产物结构不变；
    #     对比是纯读派生，不新建作业；同一次运行/跨租户按口径拒绝。
    runs_before_compare = len(factory_store.store.list_runs(project_id, "default", limit=100))
    gen_compare = factory_store.store.compare_runs(project_id, "default", accepted["id"], restat["id"])
    gen_compare_nodes = {row["node_id"]: row for row in gen_compare["nodes"]}
    compare_cut = gen_compare_nodes.get("n-cut") or {}
    try:
        factory_store.store.compare_runs(project_id, "default", accepted["id"], accepted["id"])
        compare_same_ok = False
    except ValueError as error:
        compare_same_ok = "同一次运行" in str(error)
    try:
        factory_store.store.compare_runs(project_id, "acme", accepted["id"], restat["id"])
        compare_tenant_ok = False
    except KeyError:
        compare_tenant_ok = True
    runs_after_compare = len(factory_store.store.list_runs(project_id, "default", limit=100))
    compare_ok = (
        gen_compare["base"]["status"] == "ok"
        and gen_compare["target"]["status"] == "ok"
        and gen_compare["base"]["delivered"] > 0
        and gen_compare["target"]["cached"] > 0
        and gen_compare["changes"]["cache_hit"] == len(gen_compare["nodes"])
        and gen_compare["changes"]["regressed"] == 0
        and gen_compare["changes"]["still_failed"] == 0
        and gen_compare["changes"]["output_changed"] == 0  # 全缓存，产物结构应逐岗一致
        and compare_cut.get("change") == "cache_hit"
        and (compare_cut.get("target") or {}).get("cached") is True
        and int(compare_cut.get("elapsed_delta_ms") or 0) <= 0
        and compare_same_ok
        and compare_tenant_ok
        and runs_before_compare == runs_after_compare
    )
    print(
        f"    运行对比：{gen_compare['base']['status']}→{gen_compare['target']['status']}"
        f"｜命中缓存={gen_compare['changes']['cache_hit']}/{len(gen_compare['nodes'])}"
        f"｜回退/仍失败={gen_compare['changes']['regressed']}/{gen_compare['changes']['still_failed']}"
        f"｜产物结构变化={gen_compare['changes']['output_changed']}"
        f"｜同一次拒绝={compare_same_ok}｜跨租户不可读={compare_tenant_ok}"
        f"｜对比不新建作业={runs_before_compare == runs_after_compare}={compare_ok}"
    )

    # 6f) 未结束的运行不能拿来对比：结果还会变。用临时项目 + 一条假的 running 作业钉住口径，
    #     最后删项目一并回收，不污染真实作业。
    busy_rec = factory_store.store.create(
        "自检对比运行中",
        "default",
        graph={"nodes": [{"id": "t", "type": "text_input", "pos": [0, 0], "params": {"text": "正文"}}], "edges": []},
    )
    busy_project = str(busy_rec["id"])
    busy_accepted = factory_store.store.start_run(busy_project, "default")
    busy_job = factory_store.store.job(busy_accepted["id"], "default") or {}
    deadline = time.time() + 30
    while busy_job.get("status") in ("queued", "running") and time.time() < deadline:
        time.sleep(0.1)
        busy_job = factory_store.store.job(busy_accepted["id"], "default") or busy_job
    fake_running = {
        "id": "frun-doctor-busy",
        "task_id": busy_project,
        "project_id": busy_project,
        "tenant": "default",
        "kind": "factory_run",
        "status": "running",
        "created_at": "2026-01-01 00:00:00",
        "updated_at": "2026-01-01 00:00:00",
        "targets": [],
        "force": False,
        "progress": 0.1,
        "plan_size": 1,
        "counts": {},
        "elapsed_ms": 0,
        "events": [],
        "nodes": {},
        "error": "",
        "result": None,
    }
    factory_store.store._backend.create(fake_running)
    try:
        factory_store.store.compare_runs(busy_project, "default", busy_accepted["id"], "frun-doctor-busy")
        compare_busy_ok = False
    except factory_store.RunUnfinished:
        compare_busy_ok = True
    finally:
        try:
            factory_store.store.delete(busy_project, "default")
        except factory_store.ProjectBusy:
            compare_busy_ok = False
    print(f"    对比运行中：未结束拒绝对比（409 口径）={compare_busy_ok}")

    # 6g) 失败热点：跑一个空正文项目，统计必须点名失败岗位、最近错误与现场 job_id。
    fail_rec = factory_store.store.create(
        "自检统计失败",
        "default",
        graph={"nodes": [{"id": "t", "type": "text_input", "pos": [0, 0], "params": {"text": ""}}], "edges": []},
    )
    stats_fail_project = str(fail_rec["id"])
    fail_accepted = factory_store.store.start_run(stats_fail_project, "default")
    fail_job = factory_store.store.job(fail_accepted["id"], "default") or {}
    deadline = time.time() + 60
    while fail_job.get("status") in ("queued", "running") and time.time() < deadline:
        time.sleep(0.1)
        fail_job = factory_store.store.job(fail_accepted["id"], "default") or fail_job
    fail_stats = factory_store.store.run_stats(stats_fail_project, "default")
    failure_row = (fail_stats.get("failures") or [{}])[0]
    fail_node = (fail_stats.get("nodes") or [{}])[0]
    stats_fail_ok = (
        fail_job.get("status") == "error"
        and fail_stats["runs"]["total"] == 1
        and fail_stats["runs"]["error"] == 1
        and fail_stats["runs"]["success_rate"] == 0.0
        and fail_node.get("node_id") == "t"
        and fail_node.get("error") == 1
        and "文本输入节点为空" in str(fail_node.get("last_error") or "")
        and failure_row.get("job_id") == fail_accepted["id"]
        and failure_row.get("status") == "error"
        and bool(fail_stats["departments"])
        and fail_stats["departments"][0]["nodes"] == 1
    )
    # 修好文本再跑一次：同一岗位从 error → ok，对比必须点名「恢复」；跨项目/未知作业不可比。
    factory_store.store.save_graph(
        stats_fail_project,
        "default",
        {
            "nodes": [{"id": "t", "type": "text_input", "pos": [0, 0], "params": {"text": "现在有正文"}}],
            "edges": [],
        },
    )
    fix_accepted = factory_store.store.start_run(stats_fail_project, "default")
    fix_job = factory_store.store.job(fix_accepted["id"], "default") or {}
    deadline = time.time() + 60
    while fix_job.get("status") in ("queued", "running") and time.time() < deadline:
        time.sleep(0.1)
        fix_job = factory_store.store.job(fix_accepted["id"], "default") or fix_job
    recovered = factory_store.store.compare_runs(
        stats_fail_project, "default", fail_accepted["id"], fix_accepted["id"]
    )
    recovered_row = (recovered.get("nodes") or [{}])[0]
    try:
        factory_store.store.compare_runs(project_id, "default", fail_accepted["id"], fix_accepted["id"])
        compare_cross_ok = False
    except KeyError:
        compare_cross_ok = True
    try:
        factory_store.store.compare_runs(stats_fail_project, "default", "frun-doctor-missing", fix_accepted["id"])
        compare_missing_ok = False
    except KeyError:
        compare_missing_ok = True
    compare_recovered_ok = (
        fix_job.get("status") == "ok"
        and recovered["base"]["failed"] == 1
        and recovered["target"]["failed"] == 0
        and recovered["changes"]["recovered"] == 1
        and recovered["changes"]["regressed"] == 0
        and recovered_row.get("change") == "recovered"
        and recovered_row.get("error_changed") is True
        and (recovered_row.get("target") or {}).get("error") == ""
        and compare_cross_ok
        and compare_missing_ok
    )
    print(
        f"    对比恢复：{recovered_row.get('change')}（error→ok）"
        f"｜错误消失={recovered_row.get('error_changed')}"
        f"｜跨项目/未知作业不可比={compare_cross_ok and compare_missing_ok}={compare_recovered_ok}"
    )
    try:
        factory_store.store.delete(stats_fail_project, "default")
    except factory_store.ProjectBusy:
        stats_fail_ok = False
    print(
        f"    失败统计：失败运行={fail_stats['runs']['error']}/{fail_stats['runs']['total']}"
        f"｜失败热点={failure_row.get('node_id')}（{failure_row.get('job_id')}）"
        f"｜最近错误点名空文本={'文本输入节点为空' in str(fail_node.get('last_error') or '')}={stats_fail_ok}"
    )

    # 6h) 岗位级强制重跑：只跳过指定岗位的缓存，上游与其余岗位照常复用；
    #     不存在的岗位点名拒绝，存在但不在本次计划闭包内的强跑如实报 ignored。
    #     预检与实跑必须同口径，且统计能把局部强跑与整轮强制区分开。
    runs_before_force = len(factory_store.store.list_runs(project_id, "default", limit=100))
    force_preview = factory_store.store.preview(
        project_id, "default", targets=["n-cut"], force_nodes=["n-cut"]
    )
    force_preview_rows = {row["node_id"]: row for row in force_preview["nodes"]}
    force_preview_cut = force_preview_rows.get("n-cut") or {}
    force_preview_upstream = force_preview_rows.get("n-sb") or {}
    ignored_preview = factory_store.store.preview(
        project_id, "default", targets=["n-sb"], force_nodes=["n-cut"]
    )
    try:
        factory_store.store.preview(project_id, "default", force_nodes=["n-ghost"])
        force_unknown_ok = False
    except ValueError as error:
        force_unknown_ok = "n-ghost" in str(error)
    runs_after_force_preview = len(factory_store.store.list_runs(project_id, "default", limit=100))
    force_accepted = factory_store.store.start_run(
        project_id, "default", targets=["n-cut"], force_nodes=["n-cut"]
    )
    force_job = factory_store.store.job(force_accepted["id"], "default") or {}
    deadline = time.time() + 180
    while force_job.get("status") in ("queued", "running") and time.time() < deadline:
        time.sleep(0.2)
        force_job = factory_store.store.job(force_accepted["id"], "default") or force_job
    force_counts = force_job.get("counts") or {}
    force_result_nodes = (force_job.get("result") or {}).get("nodes") or {}
    force_cut_result = force_result_nodes.get("n-cut") or {}
    force_sb_result = force_result_nodes.get("n-sb") or {}
    force_stats = factory_store.store.run_stats(project_id, "default", limit=10)
    force_ok = (
        force_preview["counts"]["total"] == len(force_preview["plan"])
        and force_preview["counts"]["cached"] == len(force_preview["plan"]) - 1
        and force_preview["counts"]["run"] == 1
        and force_preview["force_nodes"] == ["n-cut"]
        and force_preview_cut.get("state") == "run"
        and "强制重跑" in str(force_preview_cut.get("reason") or "")
        and force_preview_upstream.get("state") == "cached"
        and ignored_preview.get("force_nodes") == []
        and ignored_preview.get("ignored_force_nodes") == ["n-cut"]
        and force_unknown_ok
        and runs_after_force_preview == runs_before_force
        and force_job.get("status") == "ok"
        and force_job.get("force") is False
        and force_job.get("force_nodes") == ["n-cut"]
        and force_job.get("ignored_force_nodes") == []
        and force_job.get("plan_size") == len(force_preview["plan"])
        and force_counts.get("ok") == 1
        and force_counts.get("cached") == len(force_preview["plan"]) - 1
        and force_counts.get("error") == 0
        and force_cut_result.get("cached") is False
        and force_cut_result.get("forced") is True
        and force_sb_result.get("cached") is True
        and (force_stats["runs"].get("forced") or 0) == 0
        and (force_stats["runs"].get("partial_forced") or 0) >= 1
    )
    print(
        f"    岗位强跑：预检重跑={force_preview['counts']['run']}/{force_preview['counts']['total']}"
        f"｜实跑 交付 {force_counts.get('ok')}/复用 {force_counts.get('cached')}"
        f"｜强跑岗位={force_job.get('force_nodes')}｜上游仍复用={force_sb_result.get('cached') is True}"
        f"｜计划外忽略={ignored_preview.get('ignored_force_nodes')}｜未知强跑拒绝={force_unknown_ok}"
        f"｜统计区分局部强跑={(force_stats['runs'].get('partial_forced') or 0) >= 1}={force_ok}"
    )

    # 6i) 本轮参数覆盖：补丁只叠加到有效图，保存的画布逐字节不变；预检、实跑与
    #     作业回显同口径。缓存仍按签名单槽命中：no-op 补丁照实复用（不假装重跑），
    #     改签名则自然重跑，计划外补丁如实 ignored，静音岗位拒绝改参。
    runs_before_override = len(factory_store.store.list_runs(project_id, "default", limit=100))
    noop_preview = factory_store.store.preview(
        project_id,
        "default",
        targets=["n-cut"],
        param_overrides={"n-cut": {"transition": "按分镜"}},
    )
    noop_row = {row["node_id"]: row for row in noop_preview["nodes"]}.get("n-cut") or {}
    changed_preview = factory_store.store.preview(
        project_id,
        "default",
        targets=["n-cut"],
        param_overrides={"n-cut": {"transition": "硬切"}},
    )
    changed_rows = {row["node_id"]: row for row in changed_preview["nodes"]}
    changed_cut = changed_rows.get("n-cut") or {}
    changed_upstream = changed_rows.get("n-sb") or {}
    ignored_preview = factory_store.store.preview(
        project_id,
        "default",
        targets=["n-sb"],
        param_overrides={"n-cut": {"transition": "硬切"}},
    )
    rejected: dict[str, bool] = {}
    for label, kwargs in (
        ("unknown", {"param_overrides": {"n-ghost": {"transition": "硬切"}}}),
        ("shape", {"param_overrides": []}),
        ("patch", {"param_overrides": {"n-cut": "硬切"}}),
    ):
        try:
            factory_store.store.preview(project_id, "default", targets=["n-cut"], **kwargs)
            rejected[label] = False
        except ValueError:
            rejected[label] = True
    muted_rec = factory_store.store.create(
        "自检静音改参",
        "default",
        {"nodes": [{"id": "t", "type": "text_input", "pos": [0, 0], "muted": True}], "edges": []},
    )
    muted_project = str(muted_rec["id"])
    try:
        factory_store.store.start_run(muted_project, "default", param_overrides={"t": {"text": "改"}})
        rejected["muted"] = False
    except ValueError as error:
        rejected["muted"] = "已静音" in str(error)
    finally:
        try:
            factory_store.store.delete(muted_project, "default")
        except factory_store.ProjectBusy:
            rejected["muted"] = False
    runs_after_override_preview = len(factory_store.store.list_runs(project_id, "default", limit=100))
    override_preview_ok = (
        noop_preview.get("counts", {}).get("total") == len(noop_preview["plan"])
        and noop_preview.get("counts", {}).get("cached") == len(noop_preview["plan"])
        and noop_preview.get("param_overrides") == {"n-cut": {"transition": "按分镜"}}
        and noop_row.get("state") == "cached"
        and noop_row.get("override_keys") == ["transition"]
        and "与缓存签名一致" in str(noop_row.get("reason") or "")
        and changed_preview.get("counts", {}).get("run") == 1
        and changed_preview.get("counts", {}).get("cached") == len(changed_preview["plan"]) - 1
        and changed_cut.get("state") == "run"
        and changed_cut.get("override_keys") == ["transition"]
        and "改变签名" in str(changed_cut.get("reason") or "")
        and changed_upstream.get("state") == "cached"
        and ignored_preview.get("param_overrides") == {}
        and ignored_preview.get("ignored_param_overrides") == ["n-cut"]
        and all(rejected.values())
        and runs_after_override_preview == runs_before_override
    )
    print(
        f"    本轮改参预检：no-op 仍复用缓存={noop_row.get('state') == 'cached'}"
        f"｜改签名重跑={changed_preview.get('counts', {}).get('run')}/{changed_preview.get('counts', {}).get('total')}"
        f"｜上游仍复用={changed_upstream.get('state') == 'cached'}"
        f"｜计划外如实忽略={ignored_preview.get('ignored_param_overrides')}"
        f"｜拒绝未知/结构/补丁/静音={all(rejected.values())}={override_preview_ok}"
    )

    override_accepted = factory_store.store.start_run(
        project_id,
        "default",
        targets=["n-cut"],
        param_overrides={"n-cut": {"transition": "硬切"}},
    )
    override_job = factory_store.store.job(override_accepted["id"], "default") or {}
    deadline = time.time() + 180
    while override_job.get("status") in ("queued", "running") and time.time() < deadline:
        time.sleep(0.2)
        override_job = factory_store.store.job(override_accepted["id"], "default") or override_job
    override_counts = override_job.get("counts") or {}
    override_result_nodes = (override_job.get("result") or {}).get("nodes") or {}
    override_cut_result = override_result_nodes.get("n-cut") or {}
    override_sb_result = override_result_nodes.get("n-sb") or {}
    override_stats = factory_store.store.run_stats(project_id, "default", limit=10)
    saved_project = factory_store.store.get(project_id, "default") or {}
    saved_cut = next(
        (node for node in (saved_project.get("graph") or {}).get("nodes") or [] if node.get("id") == "n-cut"),
        {},
    )
    saved_last = saved_project.get("last_run") or {}
    # 缓存最新语义：改参跑完后缓存槽是「硬切」签名，普通预检会如实要求重跑，不暗中回滚
    latest_preview = factory_store.store.preview(project_id, "default", targets=["n-cut"])
    latest_row = {row["node_id"]: row for row in latest_preview["nodes"]}.get("n-cut") or {}
    param_override_ok = (
        override_preview_ok
        and override_job.get("status") == "ok"
        and override_job.get("param_overrides") == {"n-cut": {"transition": "硬切"}}
        and override_job.get("ignored_param_overrides") == []
        and any(
            row.get("type") == "run.override" and row.get("node_ids") == ["n-cut"]
            for row in (override_job.get("events") or [])
        )
        and override_counts.get("ok") == 1
        and override_counts.get("cached") == len(changed_preview["plan"]) - 1
        and override_counts.get("error") == 0
        and override_cut_result.get("cached") is False
        and override_cut_result.get("overridden") is True
        and override_cut_result.get("forced") is False
        and override_sb_result.get("cached") is True
        and (override_stats.get("runs", {}).get("overridden") or 0) >= 1
        and saved_last.get("override_nodes") == ["n-cut"]
        and saved_last.get("ignored_override_nodes") == []
        and "transition" not in (saved_cut.get("params") or {})
        and latest_row.get("state") == "run"
    )
    print(
        f"    本轮改参实跑：交付 {override_counts.get('ok')}/复用 {override_counts.get('cached')}"
        f"｜改参执行={override_cut_result.get('overridden')}｜上游仍复用={override_sb_result.get('cached') is True}"
        f"｜统计含改参 {(override_stats.get('runs', {}).get('overridden') or 0)} 轮"
        f"｜画布未被改写={'transition' not in (saved_cut.get('params') or {})}"
        f"｜缓存已换新签名（普通预检重跑）={latest_row.get('state') == 'run'}={param_override_ok}"
    )

    # 7) 完整产物回读（作业只给摘要，node_outputs 才带时间轴全量）
    try:
        node_out = factory_store.store.node_outputs(project_id, "default", "n-cut")
        readback_ok = "timeline" in node_out["outputs"] and isinstance(node_out["outputs"]["timeline"], dict)
    except KeyError:
        readback_ok = False
    try:
        factory_store.store.node_outputs(project_id, "default", "no-such-node")
        missing_node_ok = False
    except KeyError:
        missing_node_ok = True
    print(f"    产物回读：剪辑时间轴可取={readback_ok}｜未知岗位回 404（不返回空产物）={missing_node_ok}")

    # 8) 交付包：工作流 + 全部产物 + manifest 打进一个 zip；重复打包覆盖同一份；
    #    无产物 / 跨租户 / 运行中一律拒绝，而不是打出半成品让人误以为交付完成。
    import json
    import zipfile

    archive, bundle_name = factory_store.store.build_bundle(project_id, "default")
    with zipfile.ZipFile(archive) as bundle:
        entries = set(bundle.namelist())
        manifest = json.loads(bundle.read("manifest.json"))
        packed_project = json.loads(bundle.read("project.json"))
        readme = bundle.read("README.txt").decode("utf-8")
        has_film = "timeline.json" in entries
        film_json = json.loads(bundle.read("timeline.json")) if has_film else {}
    manifest_film = manifest.get("film") or {}
    packed_artifacts = sorted(name for name in entries if name.startswith("artifacts/"))
    bundle_simulated = set(manifest.get("simulated_nodes") or [])
    project_nodes = len((factory_store.store.get(project_id, "default") or {}).get("graph", {}).get("nodes") or [])
    manifest_files = manifest.get("files") or []
    bundle_ok = (
        bundle_name.endswith("-delivery.zip")
        and {"README.txt", "manifest.json", "project.json"} <= entries
        and bool(packed_artifacts)
        and manifest.get("format") == "creatoragent.factory.delivery"
        and (manifest.get("project") or {}).get("id") == project_id
        and manifest.get("artifact_count") == len(packed_artifacts) == len(manifest_files)
        and manifest.get("total_bytes") == sum(int(row.get("bytes") or 0) for row in manifest_files)
        and all(len(str(row.get("sha256") or "")) == 64 for row in manifest_files)
        and len((packed_project.get("graph") or {}).get("nodes") or []) == project_nodes
        and {"n-key", "n-gen"} <= bundle_simulated
        and "诚实边界" in readme
        and "本轮改参：n-cut" in readme
    )
    # 8a) 回看闭环：成片时间轴与它引用的素材一起装箱，拆包就能拼回那条片子
    project_node_ids = {str(row.get("id")) for row in (packed_project.get("graph") or {}).get("nodes") or []}
    film_refs = [str(row) for row in (manifest_film.get("refs") or [])]
    film_ok = (
        has_film
        and bool(manifest_film)
        and str(manifest_film.get("node_id") or "") in project_node_ids
        and len(film_json.get("shots") or []) > 0
        and float(film_json.get("duration_s") or 0) > 0
        and int(manifest_film.get("shots") or 0) == len(film_json.get("shots") or [])
        and bool(film_refs)
        and all(f"artifacts/{ref}" in entries for ref in film_refs)
        and "timeline.json" in readme
    )
    # 8a-2) 回看口径单一真相：放映室用的 film 端点与包内 timeline.json 必须是同一条片子
    jobs_before_film = len(factory_store.store._project_jobs(project_id, "default"))
    live = factory_store.store.film(project_id, "default")
    live_film = live.get("film") or {}
    live_timeline = live.get("timeline") or {}
    film_live_ok = (
        live_film.get("node_id") == manifest_film.get("node_id")
        and live_film.get("port") == manifest_film.get("port")
        and int(live_film.get("shots") or 0) == int(manifest_film.get("shots") or 0)
        and float(live_film.get("duration_s") or 0) == float(manifest_film.get("duration_s") or 0)
        and live_timeline == film_json
    )
    try:
        factory_store.store.film(project_id, "acme")
        film_tenant_ok = False
    except KeyError:
        film_tenant_ok = True
    film_readonly_ok = len(factory_store.store._project_jobs(project_id, "default")) == jobs_before_film
    # 8a-3) 回看对账：刚交付完屏幕这条==刚装箱那条（matches True）；改过内容后须翻脸判 False
    #     交付包固定名覆盖、旧版字节盘上不存在——对账不比旧包字节，只比成片内容指纹，诚实回答“改了没”
    film_fp = str(manifest_film.get("fingerprint") or "")
    recon_delivered = next(
        (row for row in factory_store.store.delivery_log(project_id, "default")["entries"]
         if row.get("event") == "delivered"),
        {},
    )
    live_delivery = live.get("delivery") or {}
    film_reconcile_ok = (
        len(film_fp) == 64
        and str(recon_delivered.get("film_fingerprint") or "") == film_fp
        and str(live_delivery.get("film_fingerprint") or "") == film_fp
        and len(str(live_delivery.get("live_fingerprint") or "")) == 64
        and live_delivery.get("matches") is True
    )
    # 篡改缓存（把这条成片时长改一下）→ 对账应立刻判“不一致”；随后原样还回，不污染后续 bundle/台账断言
    ws_film = factory_store.workspace_for(project_id)
    cache_orig = ws_film.load_cache()
    cache_drift = json.loads(json.dumps(cache_orig))
    _fnode, _fport = str(live_film.get("node_id")), str(live_film.get("port"))
    cache_drift[_fnode]["outputs"][_fport]["duration_s"] = float(
        (cache_drift[_fnode]["outputs"][_fport] or {}).get("duration_s") or 0) + 0.5
    ws_film.save_cache(cache_drift)
    drift_matches = (factory_store.store.film(project_id, "default").get("delivery") or {}).get("matches")
    ws_film.save_cache(cache_orig)
    film_drift_ok = drift_matches is False
    film_ok = (
        film_ok and film_live_ok and film_tenant_ok and film_readonly_ok
        and film_reconcile_ok and film_drift_ok
    )
    # 再打一次：固定文件名覆盖，exports 里只留一份 delivery.zip，不留 .tmp
    archive_again, _ = factory_store.store.build_bundle(project_id, "default")
    exports = sorted(path.name for path in archive.parent.iterdir())
    bundle_stable_ok = archive_again == archive and exports == [factory_store.BUNDLE_NAME]

    empty_rec = factory_store.store.create("自检未跑项目", "default", presets.graph_dict("script"))
    # 从没跑过的工作流：图本身没错，但终端岗位未交付——验收门必须拦下，不许打出半成品 zip
    try:
        factory_store.store.build_bundle(str(empty_rec["id"]), "default")
        bundle_empty_ok = False
    except factory_store.AcceptanceBlocked as error:
        bundle_empty_ok = "验收未通过" in str(error) and "终端岗位" in str(error)
    finally:
        factory_store.store.delete(str(empty_rec["id"]), "default")
    try:
        factory_store.store.build_bundle(project_id, "acme")
        bundle_tenant_ok = False
    except KeyError:
        bundle_tenant_ok = True
    # 运行中拒打包：在活作业表里挂一个 running 行，稳定命中守卫（不真排队跑一轮）
    with factory_store.store._lock:
        factory_store.store._live["frun-doctor-busy"] = {"id": "frun-doctor-busy", "task_id": project_id, "status": "running"}
    try:
        factory_store.store.build_bundle(project_id, "default")
        bundle_busy_ok = False
    except factory_store.ProjectBusy:
        bundle_busy_ok = True
    finally:
        with factory_store.store._lock:
            factory_store.store._live.pop("frun-doctor-busy", None)
    print(
        f"    交付包：zip 条目 {len(entries)}（产物 {len(packed_artifacts)}）｜manifest/README 校验={bundle_ok}"
        f"｜成片回看闭环（timeline.json {len(film_json.get('shots') or [])} 镜·引用全在包内·与 film 端点同一条"
        f"·回看对账（刚交付一致={film_reconcile_ok}/改过翻脸={film_drift_ok}））={film_ok}"
        f"｜重复打包覆盖同一份={bundle_stable_ok}｜未跑拒打包={bundle_empty_ok}"
        f"｜跨租户拒绝={bundle_tenant_ok}｜运行中拒打包={bundle_busy_ok}"
    )

    # 8b) 交付验收（打包前那道门）：跑完的生成台可交付（带 simulated 警告不阻断）；
    #     从没跑过的项目终端未交付即阻断；删掉一个被引用的产物文件后验收改判阻断且拒打包。
    acc_report = factory_store.store.acceptance(project_id, "default")
    victim = ""
    for row in acc_report["deliverables"]:
        if row.get("refs"):
            victim = row["refs"][0]
            break
    victim_ok = False
    if victim:
        victim_path = workspace.artifacts_dir / victim
        backup = victim_path.read_bytes()
        try:
            victim_path.unlink()
            acc_missing = factory_store.store.acceptance(project_id, "default")
            try:
                factory_store.store.build_bundle(project_id, "default")
                gate_ok = False
            except factory_store.AcceptanceBlocked:
                gate_ok = True
            victim_ok = (
                acc_missing["verdict"] == "blocked"
                and acc_missing["stats"]["refs_missing"] >= 1
                and any(r["code"] == "artifact_missing" for r in acc_missing["issues"])
                and gate_ok
            )
        finally:
            victim_path.write_bytes(backup)
    notrun_rec = factory_store.store.create("自检未跑验收", "default", presets.graph_dict("gen"))
    notrun_project = str(notrun_rec["id"])
    acc_blocked = factory_store.store.acceptance(notrun_project, "default")
    factory_store.store.delete(notrun_project, "default")
    st = acc_report["stats"]
    acceptance_ok = (
        acc_report["ok"] is True
        and acc_report["verdict"] == "warn"
        and st["refs_missing"] == 0
        and st["simulated_nodes"] > 0
        and st["terminals_ready"] == st["terminals"]
        and acc_blocked["verdict"] == "blocked"
        and acc_blocked["counts"]["error"] >= 1
        and any(r["code"] == "terminal_not_delivered" for r in acc_blocked["issues"])
        and victim_ok
    )
    # 8c) 交付台账（append-only 留痕）：前面在 project_id 上打过包——两次放行（首次 +
    #     重复覆盖）各一条 delivered，删产物那次一条 blocked；只读且跨租户不可读。
    jobs_before = len(factory_store.store._project_jobs(project_id, "default"))
    ledger = factory_store.store.delivery_log(project_id, "default")
    led_entries = ledger["entries"]
    led_delivered = [row for row in led_entries if row["event"] == "delivered"]
    led_blocked = [row for row in led_entries if row["event"] == "blocked"]
    # 新在前：最后一次打包是删产物那轮的 blocked（victim 还原后未再打包）
    ledger_new_first_ok = bool(led_entries) and led_entries[0]["event"] == "blocked"
    ledger_delivered_ok = (
        len(led_delivered) >= 2
        and all(len(str(row.get("archive_sha256") or "")) == 64 for row in led_delivered)
        and all(int(row.get("archive_bytes") or 0) > 0 for row in led_delivered)
        and all(row.get("ok") is True and row.get("verdict") in ("pass", "warn") for row in led_delivered)
    )
    ledger_blocked_ok = (
        len(led_blocked) >= 1
        and all(row.get("ok") is False and row.get("verdict") == "blocked" for row in led_blocked)
        and any(row.get("blockers") and int(row.get("refs_missing") or 0) >= 1 for row in led_blocked)
    )
    ledger_summary_ok = (
        ledger["summary"]["delivered"] == len(led_delivered)
        and ledger["summary"]["blocked"] == len(led_blocked)
        and ledger["summary"]["total"] == len(led_entries)
        and ledger["summary"]["capped"] == factory_store.MAX_DELIVERIES
        and ledger["summary"]["last_archive_sha256"]
    )
    try:
        factory_store.store.delivery_log(project_id, "acme")
        ledger_tenant_ok = False
    except KeyError:
        ledger_tenant_ok = True
    ledger_readonly_ok = len(factory_store.store._project_jobs(project_id, "default")) == jobs_before
    ledger_ok = (
        ledger_new_first_ok
        and ledger_delivered_ok
        and ledger_blocked_ok
        and ledger_summary_ok
        and ledger_tenant_ok
        and ledger_readonly_ok
    )
    print(
        f"    交付验收：跑完生成台={acc_report['verdict']}（终端 {st['terminals_ready']}/{st['terminals']}"
        f"·引用核对 {st['refs_checked']} 缺 {st['refs_missing']}·占位 {st['simulated_nodes']}）"
        f"｜未跑项目阻断={acc_blocked['verdict'] == 'blocked'}｜删产物后阻断且拒打包={victim_ok}={acceptance_ok}"
    )
    print(
        f"    交付台账：共 {ledger['summary']['total']} 条（放行 {ledger['summary']['delivered']}·拦下 {ledger['summary']['blocked']}）"
        f"｜新在前={ledger_new_first_ok}·放行带 sha/大小={ledger_delivered_ok}·拦下带 blockers={ledger_blocked_ok}"
        f"｜汇总={ledger_summary_ok}·跨租户拒读={ledger_tenant_ok}·只读不建作业={ledger_readonly_ok}={ledger_ok}"
    )

    # 9) 租户隔离 + 产物路径边界 + 删除回收（目录与作业一起清）
    cross_get = factory_store.store.get(project_id, "acme") is None
    cross_run = False
    try:
        factory_store.store.start_run(project_id, "acme")
    except KeyError:
        cross_run = True
    escape_ok = False
    try:
        factory_store.store.artifact_path(project_id, "../../etc/passwd")
    except ValueError:
        escape_ok = True
    root_dir = factory_store.FACTORY_DIR / project_id
    deleted = factory_store.store.delete(project_id, "default")
    reclaim_ok = deleted.get("deleted") == project_id and not root_dir.exists()
    tenant_path_ok = cross_get and cross_run and escape_ok
    print(f"    租户隔离+路径边界：跨租户不可读/不可跑/越界拒绝={tenant_path_ok}｜删除回收目录={reclaim_ok}")

    # 10) 旁路作业 pg 表登记完整性（静态守卫，防「新通道忘了建表」这一类 bug）：
    #    扫 app/ 源码收集所有 make_backend(...) / _TABLE = "<name>" 引用的 *_jobs 表名，
    #    逐个断言已在 pg_schema.SCHEMA_STATEMENTS 建表。工厂曾漏登记即由此拦截。
    import re as _re
    from pathlib import Path as _Path

    from app.core import pg_schema as _pg_schema

    app_root = _Path(__file__).resolve().parent.parent / "app"
    job_tables: set[str] = set()
    for py in app_root.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="ignore")
        job_tables.update(_re.findall(r'_TABLE\s*=\s*"(\w+_jobs)"', text))
        job_tables.update(_re.findall(r'make_backend\([^)]*"(\w+_jobs)"', text))
    ddl_blob = "\n".join(_pg_schema.SCHEMA_STATEMENTS)
    unregistered = sorted(
        table for table in job_tables
        if f"CREATE TABLE IF NOT EXISTS {table}" not in ddl_blob
    )
    pg_tables_ok = bool(job_tables) and not unregistered
    print(
        f"    旁路作业 pg 表登记：{len(job_tables)} 张 *_jobs 表全部建表={pg_tables_ok}"
        + (f"｜漏登记={unregistered}" if unregistered else "")
    )

    # 11) 重启回收：进程一退后台线程就没了，落盘里还挂在 queued/running 的作业必须如实
    #     标成中断（写 run.interrupted），已完成岗位的行与产物原样保留、重跑可复用缓存。
    class _StaleJobs:
        def __init__(self, rows: list[dict]) -> None:
            self.rows, self.wrote = rows, []

        def snapshot(self) -> list[dict]:
            return [dict(row) for row in self.rows]

        def write_back(self, rows: list[dict]) -> None:
            self.wrote = rows

    stale = _StaleJobs([
        {
            "id": "frun-doctor-stale",
            "status": "running",
            "error": "",
            "events": [],
            "nodes": {"a": {"status": "running"}, "b": {"status": "ok"}},
        },
        {
            "id": "frun-doctor-cancelling",
            "status": "cancelling",
            "error": "",
            "events": [],
            "nodes": {"c": {"status": "cancelling"}},
        },
        {"id": "frun-doctor-done", "status": "ok", "events": []},
    ])
    moved = factory_store.recover_interrupted(stale)
    row = stale.wrote[0] if stale.wrote else {}
    recover_ok = (
        moved == 2
        and len(stale.wrote) == 2
        and row.get("status") == "error"
        and "进程重启" in str(row.get("error") or "")
        and (row.get("events") or [])[-1].get("type") == "run.interrupted"
        and (row.get("nodes") or {}).get("a", {}).get("status") == "error"
        and (row.get("nodes") or {}).get("b", {}).get("status") == "ok"
        and (stale.wrote[1].get("nodes") or {}).get("c", {}).get("status") == "error"
    )
    print(f"    重启回收：遗留运行（含 cancelling）标中断={moved == 2}｜写 run.interrupted 事件={recover_ok}")

    # 12) 取消只在岗位边界生效 + 增量缓存：首岗交付时 cache.json 已落盘；停止后剩余岗位
    #     如实标 cancelled。存储层用闸门卡住引擎，确定性验证 cancelling → cancelled 状态机。
    import threading

    cancel_ws = engine.open_workspace("doctor-factory-cancel", "")
    shutil.rmtree(cancel_ws.root, ignore_errors=True)
    cancel_ws = engine.open_workspace("doctor-factory-cancel", "")
    cancel_graph = graph_model.Graph.model_validate(presets.graph_dict("script"))
    stop_after_first: dict[str, bool] = {"stop": False, "cache_saved": False}

    def _watch_first(event: dict) -> None:
        if event.get("type") == "node.finish" and not stop_after_first["stop"]:
            # node.finish 在增量落盘之后才发出：此刻 cache.json 必须已经包含首个岗位
            stop_after_first["cache_saved"] = str(event.get("node_id")) in cancel_ws.load_cache()
            stop_after_first["stop"] = True

    stop_run = engine.run_graph(
        cancel_graph,
        cancel_ws,
        on_event=_watch_first,
        should_stop=lambda: stop_after_first["stop"],
    )
    stop_counts = stop_run.get("counts") or {}
    stop_cache_ok = (
        stop_run.get("cancelled") is True
        and stop_counts.get("ok") == 1
        and stop_counts.get("cancelled") == len(cancel_graph.nodes) - 1
        and stop_after_first["cache_saved"]
        and sum(1 for row in stop_run["runs"].values() if row["status"] == "cancelled") == len(cancel_graph.nodes) - 1
    )

    gate = {"entered": threading.Event(), "release": threading.Event()}
    real_run_graph = engine.run_graph

    def _gated_run(graph, workspace, **kwargs):
        gate["entered"].set()
        gate["release"].wait(10)
        return real_run_graph(graph, workspace, **kwargs)

    cancel_rec = factory_store.store.create("自检取消", "default", presets.graph_dict("script"))
    cancel_project = str(cancel_rec["id"])
    engine.run_graph = _gated_run
    try:
        cancel_accepted = factory_store.store.start_run(cancel_project, "default")
        gate_entered = gate["entered"].wait(10)
        requested = factory_store.store.cancel(cancel_accepted["id"], "default")
        cancelling_now = requested.get("status") == "cancelling"
        gate["release"].set()
        deadline = time.time() + 30
        final_cancel = factory_store.store.job(cancel_accepted["id"], "default") or {}
        while (
            final_cancel.get("status") in ("queued", "running", "cancelling")
            or cancel_accepted["id"] in factory_store.store._threads
        ) and time.time() < deadline:
            time.sleep(0.05)
            final_cancel = factory_store.store.job(cancel_accepted["id"], "default") or final_cancel
        cancel_counts = final_cancel.get("counts") or {}
        cancel_retry = factory_store.store.public_job(final_cancel, detail=False).get("retry_nodes") or []
        cancel_job_ok = (
            gate_entered
            and cancelling_now
            and final_cancel.get("status") == "cancelled"
            and cancel_counts.get("ok") == 0
            and cancel_counts.get("cancelled") == len(cancel_graph.nodes)
            and final_cancel.get("plan_size") == len(cancel_graph.nodes)
            and final_cancel.get("progress") == 1.0
            and any(row.get("type") == "run.cancel_requested" for row in (final_cancel.get("events") or []))
            and set(cancel_retry) == {node.id for node in cancel_graph.nodes}
        )
    finally:
        gate["release"].set()
        engine.run_graph = real_run_graph
        try:
            factory_store.store.delete(cancel_project, "default")
        except factory_store.ProjectBusy:
            pass

    print(
        f"    取消边界+增量缓存：首岗交付即落盘={stop_after_first['cache_saved']}"
        f"｜剩余岗位标 cancelled={stop_counts.get('cancelled')}/{len(cancel_graph.nodes) - 1}={stop_cache_ok}"
        f"｜cancelling→cancelled 状态机={cancel_job_ok}｜进度按计划岗位数={final_cancel.get('plan_size')}"
        f"｜续跑目标={len(cancel_retry)}"
    )

    # 兜底清掉自检临时工作区，不污染后续运行
    for extra in ("doctor-factory-script", "doctor-factory-empty", "doctor-factory-need", "doctor-factory-cancel"):
        shutil.rmtree(engine.open_workspace(extra, "").root, ignore_errors=True)

    ok = bool(
        registry_ok
        and panel_ok
        and preset_ok
        and cycle_ok
        and mismatch_ok
        and structure_ok
        and engine_ok
        and empty_ok
        and need_ok
        and missing_target_ok
        and job_ok
        and history_ok
        and stats_ok
        and stats_fail_ok
        and compare_ok
        and compare_busy_ok
        and compare_recovered_ok
        and force_ok
        and param_override_ok
        and readback_ok
        and missing_node_ok
        and bundle_ok
        and film_ok
        and bundle_stable_ok
        and bundle_empty_ok
        and bundle_tenant_ok
        and bundle_busy_ok
        and acceptance_ok
        and ledger_ok
        and tenant_path_ok
        and reclaim_ok
        and pg_tables_ok
        and recover_ok
        and stop_cache_ok
        and cancel_job_ok
    )
    if not ok:
        print("    ! 影视制作工厂异常：请检查 app/factory 的注册表、图校验（含岗位 id 冲突/悬空端点/自环）、引擎缓存、取消状态机、异步作业、运行预检/历史/统计/对比/岗位强跑/本轮改参、交付验收与交付台账/交付包、存储回收。")
    return ok


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
        imagegen = check_imagegen()
        videogen = check_videogen()
        videounderstand = check_video_understand()
        surf = check_surf()
        dubbing = check_dubbing()
        petgen = check_petgen()
        skillgen = check_skillgen()
        factory = check_factory()
        copyright_check = check_copyright()
        multimodal = check_multimodal()
        computed = check_compute()
        digest = check_digest_export()
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
        "图片生成样例": imagegen,
        "视频生成样例": videogen,
        "视频理解样例": videounderstand,
        "灵感冲浪（自主浏览）": surf,
        "全自动配音（真实引擎）": dubbing,
        "桌面宠物样例": petgen,
        "创作技能提炼样例": skillgen,
        "影视制作工厂（岗位图引擎）": factory,
        "二创版权清单": copyright_check,
        "Brief 素材（多模态）": multimodal,
        "确定性计算沙箱": computed,
        "素材研读与长文分篇": digest,
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
            "全自动配音的台词切分与说话人识别、时间轴汇总（含源文本/参考音频/配音）、"
            "引擎通道真实合成（sapi 本机出真 wav）、缺配置/缺参考音频受理即点名变量与说话人；"
            "桌面宠物样例的真帧落盘、色键透明、宠物包内运行器与诚实失败"
            "（未配图片端点 / 成本熔断 / 无视觉指导）均符合预期；"
            "创作技能提炼样例的作品采集口径、SKILL.md 真落盘与诚实失败"
            "（行内时间戳不误判为字幕 / 未配模型网关 / 成本熔断 / 无作品）均符合预期；"
            "影视制作工厂的 60 岗位注册表与部门归档、预设展图与静态体检（环路拒执行、端口不匹配只告警）、"
            "增量缓存（二跑全命中不重跑）、离线通道真落盘（PNG/WAV）与生成岗位 simulated 自报、"
            "异步作业生命周期、未知目标点名拒绝、岗位边界取消（首岗交付即落盘缓存、剩余岗位标 cancelled、"
            "cancelling→cancelled、进度按实际计划岗位数）、重启遗留作业回收（保留已完成岗位缓存）、"
            "交付包（manifest/sha256 与无产物/运行中拒绝）、"
            "跨租户不可读写、产物路径越界拒绝与项目删除回收均符合预期；"
            "灵感冲浪的会话生命周期与诚实失败（未装 Playwright / 未配白名单 / 未配检索网关均点名）、"
            "在线看视频的**分组截图逐组交 VLM 再一次汇总**与「没配读图网关就只截帧不交模型」（vision_skipped）、"
            "真人式浏览（随机节奏与机器节奏分野、站内搜索成功与「没搜索框就不搜」、点站点自己的「下一页」接着刷列表、"
            "展开全文与页面评论区逐条抓取、挑条目先扫标题认出主题词（并记下凭哪几个词点进去，"
            "要在线看的视频也一样从贴主题的标题里挑）、搜不对题就清空搜索框换个更短的说法再搜"
            "（换过哪几版关键词全记在信号上）、"
            "打字时也用站点弹出的搜索联想（只点认得出主题词的那条、这一版关键词记成站点给的 suggested、"
            "认不出就照原词回车不为联想改主题、点不动就退回打回搜索框再回车、没下拉就一字不提）、"
            "读到门口就被登录/订阅/人机验证/维护页挡住时只记下门口那几句话（标成 wall，不冒充正文也不绕墙，"
            "被挡的页不算读过的页）、"
            "读过的条目下一轮改读没见过的（跨会话浏览历史按租户隔离、只记真读到的页、"
            "整版都读过仍按原顺序重读并如实说明、用户指定的链接永远照看、关掉开关即不记也不跳）、"
            "信息流多跳只点同域内容页、"
            "起点/搜索/列表后续页/条目/视频共用 SURF_MAX_PAGES 预算并把没刷到的部分写进浏览说明、越界与环回链接一律不访问）、"
            "跨任务灵感池的租户隔离与残留回收均符合预期；"
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
    if not imagegen:
        print("  · 图片生成样例异常，请检查 app/core/imagegen.py 的清单构建与状态机。")
    if not videogen:
        print("  · 视频生成样例异常，请检查 app/core/videogen.py 的清单、成本熔断与状态机。")
    if not videounderstand:
        print("  · 视频理解样例异常，请检查 app/core/videounderstand.py 的状态机、诚实失败与存储。")
    if not dubbing:
        print("  · 全自动配音异常，请检查 app/core/dubbing.py 的台词切分、引擎合成、时间轴汇总与诚实失败。")
    if not petgen:
        print("  · 桌面宠物样例异常，请检查 app/core/petgen.py 的状态机、帧落盘与诚实失败。")
    if not skillgen:
        print("  · 创作技能提炼样例异常，请检查 app/core/skillgen.py 的采集口径、落盘与诚实失败。")
    if not factory:
        print("  · 影视制作工厂异常，请检查 app/factory 的注册表、图校验、引擎缓存、异步作业、运行预检/历史/统计与存储回收。")
    if not copyright_check:
        print("  · 二创版权清单异常，请检查 app/knowledge/copyright.py 与 A7 的接线。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
