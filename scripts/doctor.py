"""环境与依赖自检 + 离线 Mock 引擎全链路演练。

用法::

    python scripts/doctor.py

作用：
 1. 校验 Python 版本与关键依赖是否可导入
 2. 依次执行 A1→A11 的离线生成器，验证知识层与产出契约
 3. 打印各环节的关键信号（质量分、阻断项、裁决），便于定位回归
 4. 额外覆盖动态渠道（抖音）分支：分镜脚本与标题超限压缩
 5. 跑**真实 A11 智能体本体**验证记忆库 RAG 闭环（写入 → 跨任务召回）

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
        print(f"      示例：{top['title']}（score={top['score']}｜{'、'.join(top['reasons'])}）")

    stats = memory_store.stats()
    print(f"    → 记忆库：{stats['total']} 条卡片，覆盖 {stats['tasks']} 个任务")

    if indexed <= 0:
        print("    ! 任务 1 未向记忆库写入知识，跨任务复用将失效")
    if not recalled:
        print("    ! 任务 2 未召回任务 1 的知识，RAG 闭环未成立")
    return indexed > 0 and len(recalled) > 0


def main() -> int:
    data_dir = isolate_data_dir()
    check_python()
    print(f"data dir    : {data_dir}")
    print()
    check_deps()

    try:
        converged, chain, brief = exercise_mock()
        recalled = check_memory_recall(brief, chain)
    except Exception as error:  # noqa: BLE001
        print(f"\n[失败] 自检演练异常：{type(error).__name__}: {error}")
        import traceback

        traceback.print_exc()
        return 1

    print()
    if converged and recalled:
        print("自检通过：环境可用，Mock 引擎可在返工 1 轮后收敛，记忆库 RAG 闭环成立。")
        return 0
    if not converged:
        print("自检警告：返工后仍未通过合规门禁，请检查知识层与 Mock 生成器。")
    if not recalled:
        print("自检警告：记忆库 RAG 闭环未成立，请检查 app/knowledge/memory.py 与 A11。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
