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

注意：本脚本默认写入临时数据目录，避免自检污染开发环境的 ``data/``；
如需指定，可显式设置 ``CREATOR_DATA_DIR``。

第 5 项是刻意设计的：生成器只覆盖「JSON 契约」，而 ``AgentRunContext`` 的字段漂移
（曾出现过缺失 ``artifacts`` 导致 A11 崩溃）只有真跑 ``run()`` 才会暴露。
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def isolate_data_dir() -> str:
    """自检默认使用临时数据目录，必须在导入 ``app.*`` 之前调用。"""
    current = os.environ.get("CREATOR_DATA_DIR")
    if current:
        return current
    created = tempfile.mkdtemp(prefix="creator-doctor-")
    os.environ["CREATOR_DATA_DIR"] = created
    return created


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
    }
    print()
    if all(checks.values()):
        print(
            "自检通过：环境可用；Mock 引擎可在返工 1 轮后收敛；"
            "记忆库 RAG（关键词 + 向量混合）闭环成立；发布排期具备自动投递的时间基础；"
            "鉴权租户解析正确。"
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
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
