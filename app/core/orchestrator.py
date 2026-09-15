"""编排器 Orchestrator（移植自 server/core/orchestrator.ts，改用 LangGraph 承载控制流）。

与 TS 版的关系
---------------
业务语义（任务卡拆解、门禁裁决、返工环路、人工介入、最终交付）与原实现逐条对齐，
因此前端 ``/api/*`` 与 SSE 契约完全不变。差异在于「控制流」不再由手写 ``for(;;)``
循环承担，而是编译成一张 LangGraph ``StateGraph``：

    START → A0 → A1 → A2 → A3 → A4 → 审核门禁(review)
                                        ├─ pass    → A10 → 人工审批(approval)
                                        ├─ revise  → revise → A4
                                        └─ escalate→ 升级裁决(escalation) → …

两点实打实的收益：

1. **人工介入用 ``interrupt()`` 表达**：图会在该节点挂起并写入检查点，
   ``Command(resume=decision)`` 再把人工裁决注回节点，节点不阻塞、进程可重启。
2. **断点续跑**：``SqliteSaver`` 把每一步状态落到 ``data/checkpoints.sqlite``，
   进程重启后可从最近检查点继续，而不是整单重跑。

关于「节点会重放」的约定
------------------------
LangGraph 恢复中断时会从头重放该节点，因此 **``interrupt()`` 之前不得有副作用**：
escalation / approval 两个节点把 ``interrupt()`` 放在第一行，后续的发布与落盘
只在拿到裁决后执行一次。
"""

from __future__ import annotations

import copy
import sqlite3
import threading
import time
from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from ..agents.base import AgentRunContext
from ..agents.registry import get_agent
from ..config import CHECKPOINT_FILE, RUBRIC_VERSION, get_config
from ..knowledge.industry import publish_slots, title_limit
from ..knowledge.language import (
    is_multilingual,
    language_label,
    localization_directive,
    title_limit_for,
)
from ..knowledge.memory import memory_store
from ..llm.cost import cost_guard
from ..logger import create_logger
from .blackboard import INTENT_TTL_MS, blackboard
from .clock import now_iso
from .evaluations import build_record, evaluation_store
from .events import event_bus, new_id
from .gatekeeper import build_scorecard, decide_gate, to_gate_records
from .judge import JudgeReport, evaluate as judge_evaluate
from .publisher import compute_due_at, dispatch as dispatch_webhook, is_due
from .store import task_store
from .tracing import RemoteParent, tracer
from .types import (
    AgentResult,
    ApprovalState,
    Artifact,
    Brief,
    IntentEntry,
    PipelineNode,
    QualityScorecard,
    RevisionRecord,
    TaskPacket,
    TaskRecord,
)
from .types import PHASE_LABEL

log = create_logger("orchestrator")

# ------------------------------------------------------------------ #
# 流水线定义                                                          #
# ------------------------------------------------------------------ #

PIPELINE: list[dict[str, str]] = [
    {"agent": "A0", "phase": "INIT"},
    {"agent": "A1", "phase": "STRATEGY"},
    {"agent": "A2", "phase": "CREATIVE"},
    {"agent": "A3", "phase": "PLANNING"},
    {"agent": "A4", "phase": "DRAFTING"},
    {"agent": "A5", "phase": "EDITING"},
    {"agent": "A6", "phase": "FACT_CHECK"},
    {"agent": "A7", "phase": "COMPLIANCE"},
    {"agent": "A8", "phase": "VISUAL_ADAPT"},
    {"agent": "A9", "phase": "CHANNEL_ADAPT"},
    {"agent": "A10", "phase": "ANALYZED"},
    {"agent": "A11", "phase": "MEMORY"},
]

REVIEW_AGENTS: list[str] = ["A5", "A6", "A7"]

#: 需要在创作前召回 A11 记忆库历史资产的智能体（RAG 注入点）。
MEMORY_RECALL_AGENTS: tuple[str, ...] = ("A1", "A2", "A4")

#: 单次召回的条数上限，避免提示词被历史资产淹没。
MEMORY_RECALL_TOP_K = 5

#: 安全阀：正常流程远低于此值，命中说明出现了未预期的环路。
RECURSION_LIMIT = 200

#: 黑板租约的重试策略（plan.md 4.5「租约机制 + 原子操作 + 冲突重试」）
INTENT_ATTEMPTS = 3
INTENT_BACKOFF_SECONDS = 0.15


class TurnBudgetExceeded(Exception):
    def __init__(self, limit: int) -> None:
        super().__init__(f"Turn Budget（{limit} 次）已耗尽，任务转入人工处理")
        self.name = "TurnBudgetExceeded"


class PipelineState(TypedDict, total=False):
    """LangGraph 状态：只承载控制流所需的最小信息。

    业务数据（产物、事件、记分卡）仍写在 ``TaskRecord`` 与黑板上，
    这样 REST 接口无需感知 LangGraph 内部结构。
    """

    task_id: str
    #: 交给 A4 的返工意见
    feedback: list[str]
    #: 当前返工轮次（与 task.revision_round 同步）
    revision_round: int
    #: 人工审批连续退回次数
    approval_cycles: int
    #: 门禁裁决：pass / revise / escalate
    gate_verdict: str
    gate_reason: str
    gate_requests: list[str]
    #: 终态：delivered / rejected / ""（未定）
    outcome: str
    #: 人工审批意见（交付时写入交付物）
    approval_comment: str


# ------------------------------------------------------------------ #
# 编排器                                                              #
# ------------------------------------------------------------------ #


class Orchestrator:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        #: task_id → 人工裁决结果（由 HTTP 线程写入、工作线程读取）
        self._decisions: dict[str, dict[str, str]] = {}
        #: task_id → 等待人工裁决的信号量
        self._waiters: dict[str, threading.Event] = {}
        self._running: set[str] = set()
        self._auto_approve: set[str] = set()
        #: task_id → 入站 W3C traceparent 解析结果（跨进程传播，见 tracing.RemoteParent）
        self._trace_parents: dict[str, RemoteParent] = {}
        #: 检查点后端类型（sqlite / memory）。断点续跑是否真的可用，全看这个字段，
        #: 因此把它暴露出来供 /api/health 与自检断言 —— 「静默退回内存检查点」
        #: 是最容易在几个月后才被发现的那类退化。
        self.checkpointer_kind = "memory"
        self.checkpointer_error = ""
        self._graph = self._build_graph()

    # ---------------------------------------------------------------- #
    # 图构建                                                           #
    # ---------------------------------------------------------------- #

    def _checkpointer(self) -> Any:
        """按存储模式选择检查点后端。

        * **file 模式**：优先 SQLite（可跨重启），失败退回内存实现并留下明确痕迹。
          两个容易踩的点：
          1. 检查点在**导入期**就要打开数据库，而 ``ensure_dirs()`` 要等 lifespan
             才执行，因此这里必须先自己建目录；
          2. 建目录成功 ≠ 数据库能打开（例如目录被沙箱/权限限制时 ``sqlite3`` 会抛
             ``unable to open database file``）。失败必须留下明确痕迹 —— 否则
             「断点续跑」会静默失效，直到某天进程重启才发现任务全都没了。
        * **pg 模式**：``PostgresSaver``（检查点落库，多副本可共享续跑）。
          连接失败**直接终止启动**（契约 storage_contract.md「移除静默退回」）——
          多副本下退回内存检查点会让各副本各自为政，比单机不可用更危险。
        """
        from ..config import STORAGE_MODE

        if STORAGE_MODE == "pg":
            from .pg import pg_pool

            try:
                from langgraph.checkpoint.postgres import PostgresSaver

                saver = PostgresSaver(pg_pool())
                saver.setup()  # 建齐检查点表（幂等，可重入）
                self.checkpointer_kind = "postgres"
                self.checkpointer_error = ""
                return saver
            except Exception as error:  # noqa: BLE001
                self.checkpointer_kind = "memory"
                self.checkpointer_error = f"{type(error).__name__}: {error}"
                raise RuntimeError(
                    "PG 模式下初始化 PostgresSaver 检查点失败（fail-loud，拒绝静默退回内存）："
                    f"{self.checkpointer_error}；请确认 CREATOR_DATABASE_URL 可达"
                ) from error

        try:
            CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(CHECKPOINT_FILE), check_same_thread=False)
            self.checkpointer_kind = "sqlite"
            self.checkpointer_error = ""
            return SqliteSaver(conn)
        except Exception as error:  # noqa: BLE001
            self.checkpointer_kind = "memory"
            self.checkpointer_error = f"{type(error).__name__}: {error}"
            log.warn(
                "初始化 SQLite 检查点失败，退回内存检查点（重启后无法续跑）："
                f"{self.checkpointer_error}；数据目录 {CHECKPOINT_FILE.parent}"
            )
            return InMemorySaver()

    def _build_graph(self) -> Any:
        builder = StateGraph(PipelineState)

        builder.add_node("a0", self._node_a0)
        builder.add_node("a1", self._node_agent("A1"))
        builder.add_node("a2", self._node_agent("A2"))
        builder.add_node("a3", self._node_agent("A3"))
        builder.add_node("a4", self._node_a4)
        builder.add_node("review", self._node_review)
        builder.add_node("revise", self._node_revise)
        builder.add_node("escalation", self._node_escalation)
        builder.add_node("a8", self._node_agent("A8"))
        builder.add_node("a9", self._node_agent("A9"))
        builder.add_node("a10", self._node_agent("A10"))
        builder.add_node("a11", self._node_agent("A11"))
        builder.add_node("approval", self._node_approval)
        builder.add_node("publish", self._node_publish)
        builder.add_node("delivery", self._node_delivery)

        builder.add_edge(START, "a0")
        builder.add_edge("a0", "a1")
        builder.add_edge("a1", "a2")
        builder.add_edge("a2", "a3")
        builder.add_edge("a3", "a4")
        builder.add_edge("a4", "review")

        builder.add_conditional_edges(
            "review",
            self._route_review,
            {"pass": "a8", "revise": "revise", "escalate": "escalation"},
        )
        builder.add_edge("revise", "a4")

        builder.add_conditional_edges(
            "escalation",
            self._route_escalation,
            {"pass": "a8", "revise": "a4", "rejected": END},
        )

        builder.add_edge("a8", "a9")
        builder.add_edge("a9", "a10")
        builder.add_edge("a10", "a11")
        builder.add_edge("a11", "approval")
        builder.add_conditional_edges(
            "approval",
            self._route_approval,
            {"delivered": "publish", "revise": "a4", "rejected": END},
        )
        # 审批通过后先生成发布排期（creator.md §5 第 12 步、门禁表「发布 → A9」），再归档交付
        builder.add_edge("publish", "delivery")
        builder.add_edge("delivery", END)

        return builder.compile(checkpointer=self._checkpointer())

    # ---------------------------------------------------------------- #
    # 任务创建                                                          #
    # ---------------------------------------------------------------- #

    def create_task(
        self, brief: Brief, auto_approve: bool = False, tenant: str = "default",
        trace_parent: RemoteParent | None = None,
    ) -> TaskRecord:
        stamp = now_iso()
        task = TaskRecord(
            id=new_id("task"),
            brief=brief,
            status="running",
            phase="INIT",
            revision_round=0,
            turn_used=0,
            created_at=stamp,
            updated_at=stamp,
            finished_at=None,
            tenant=tenant or "default",
            pipeline=[
                PipelineNode(
                    agent_id=stage["agent"],  # type: ignore[arg-type]
                    phase=stage["phase"],  # type: ignore[arg-type]
                    status="pending",
                    runs=0,
                )
                for stage in PIPELINE
            ],
            approval=ApprovalState(
                required=True,
                decision="pending",
                comment="",
                decided_at=None,
            ),
        )
        task_store.schedule_save(task, True)
        self._publish(task, "task.created", f"已创建任务 {task.id}，开始解析 Brief", {})
        log.info(f"任务 {task.id} 已创建：{brief.brand} / {brief.product} / {brief.channel}")

        if auto_approve:
            self._auto_approve.add(task.id)
        # 跨进程传播：调用方（编排/网关上游）带的 W3C traceparent 交给后台线程，
        # 使任务 trace 延续远端 trace_id（in-memory 传递即可：重启后远端上下文本就失效）
        if trace_parent is not None:
            self._trace_parents[task.id] = trace_parent
        self._spawn(task)
        return task

    def _spawn(self, task: TaskRecord, resuming: bool = False) -> None:
        """在后台线程里驱动编排图；SSE/HTTP 仍跑在事件循环上。"""
        if task.id in self._running:
            log.warn(f"任务 {task.id} 已在运行，忽略重复调度")
            return
        self._running.add(task.id)
        thread = threading.Thread(
            target=self._drive,
            args=(task, resuming),
            name=f"orchestrator-{task.id}",
            daemon=True,
        )
        thread.start()

    # ---------------------------------------------------------------- #
    # 驱动循环：invoke → 遇 interrupt 等人工 → resume → invoke …        #
    # ---------------------------------------------------------------- #

    @staticmethod
    def _initial_state(task: TaskRecord) -> PipelineState:
        return {
            "task_id": task.id,
            "feedback": [],
            "revision_round": task.revision_round,
            "approval_cycles": 0,
            "gate_verdict": "",
            "gate_reason": "",
            "gate_requests": [],
            "outcome": "",
            "approval_comment": "",
        }

    def _drive(self, task: TaskRecord, resuming: bool = False) -> None:
        config = {
            "configurable": {"thread_id": task.id},
            "recursion_limit": RECURSION_LIMIT,
        }
        # resuming 时传 None，让 LangGraph 从检查点继续；否则注入初始状态。
        payload: Any = None if resuming else self._initial_state(task)
        cfg = get_config()
        # 成本账本绑定到本任务的执行线程，llm.chat 据此计费并在超预算时熔断
        cost_guard.begin(task.id, budget_usd=cfg.cost_budget_usd, token_budget=cfg.token_budget)
        # 追踪：一个任务一个 trace，覆盖整个 Agent session（plan.md 2.4），
        # 而不是只追踪单次模型调用。span 树在终态时落盘为 OTel 形状的 JSON。
        # 若创建请求携带了 W3C traceparent，trace 延续调用方的 trace_id（跨进程传播）。
        parent = self._trace_parents.pop(task.id, None)
        trace_id = tracer.start_trace(task.id, tenant=task.tenant, parent=parent)
        self._publish(
            task, "log", f"追踪已开启（trace {trace_id[:8]}）", {"level": "debug"}
        )
        try:
            while True:
                # 每次 graph.invoke 包一个 span：LangGraph 的节点各自独立执行，
                # 若不包一层，所有节点 span 都会成为**根 span**，trace 树退化成
                # 「13 个并列根节点」——看不出谁属于哪条链路，self-time 也失真。
                with tracer.span(
                    f"graph.invoke#{task.turn_used}",
                    attributes={"graph.resuming": payload is None},
                ) as invoke_span:
                    output = self._graph.invoke(payload, config)
                    tracer.finish_span(
                        invoke_span,
                        status="ok",
                        attributes={
                            "graph.interrupted": bool((output or {}).get("__interrupt__")),
                        },
                    )
                interrupts = (output or {}).get("__interrupt__") or ()
                if not interrupts:
                    break
                pending = dict(interrupts[0].value or {})
                # 人工等待不应计入耗时：否则排障时会把「人在犹豫」误读成「系统很慢」
                with tracer.span("human.wait", kind="internal"):
                    decision = self._wait_for_human(
                        task,
                        str(pending.get("milestone") or "approval"),
                        str(pending.get("message") or "等待人工裁决"),
                    )
                payload = Command(resume=decision)
        except Exception as error:  # noqa: BLE001 - 任何异常都要落成 failed，不能吞掉
            self._handle_fatal(task, error)
        finally:
            self._settle_cost(task)
            released = blackboard.release_task_intents(task.id)
            if released:
                log.info(f"任务 {task.id} 进入终态，回收 {released} 条黑板租约")
            trace = tracer.finish_trace(task.id)
            if trace is not None:
                log.info(
                    f"任务 {task.id} 追踪完成：{len(trace.spans)} 个 span，"
                    f"总耗时 {trace.duration_ms}ms"
                )
            with self._lock:
                self._running.discard(task.id)
                self._waiters.pop(task.id, None)
                self._decisions.pop(task.id, None)

    # ---------------------------------------------------------------- #
    # 节点实现                                                          #
    # ---------------------------------------------------------------- #

    def _node_a0(self, state: PipelineState) -> PipelineState:
        self._stage_a0(self._task(state))
        return {}

    def _node_agent(self, agent_id: str):
        def node(state: PipelineState) -> PipelineState:
            self._run_agent_step(self._task(state), agent_id)
            return {}

        return node

    def _node_a4(self, state: PipelineState) -> PipelineState:
        self._run_agent_step(
            self._task(state), "A4", feedback=list(state.get("feedback") or [])
        )
        return {}

    def _node_review(self, state: PipelineState) -> PipelineState:
        task = self._task(state)
        # 门禁整体作为一个 span，其下挂 A5/A6/A7 三个智能体 span + judge span，
        # 于是「审核链路一共花了多久」是一个可读的数字，而不是三次求和。
        with tracer.span("gate.review", attributes={"gate.revision": task.revision_round}) as span:
            return self._review_body(task, state, span)

    def _review_body(
        self, task: TaskRecord, state: PipelineState, span: Any
    ) -> PipelineState:
        results = [self._run_agent_step(task, reviewer) for reviewer in REVIEW_AGENTS]

        scorecard = self._current_scorecard(task)
        task.scorecard = scorecard

        # LLM-as-a-Judge：评估是旁路能力，默认只产出报告与建议（plan.md D14）。
        # 放在门禁之前，是为了让 blocking 模式下的评估结论能参与本次裁决。
        report = self._judge_step(task, "final", "review")
        if report is not None:
            self._apply_judge_to_scorecard(task, scorecard, report)

        cfg = get_config()
        decision = decide_gate(
            results=results,
            revision=task.revision_round,
            max_revisions=cfg.max_revisions,
            quality_threshold=cfg.quality_threshold,
            scorecard=scorecard,
            judge=report,
            judge_mode=cfg.judge.mode,
        )
        task.gates.extend(to_gate_records(decision, task.revision_round))

        self._publish(
            task,
            "gate.decision",
            f"门禁裁决：{decision.reason}",
            {
                "verdict": decision.verdict,
                "scorecard": scorecard.model_dump(mode="json"),
                "blocking": decision.blocking,
                "judge": report.to_dict() if report else None,
            },
            "info" if decision.verdict == "pass" else "warn",
        )
        self._save(task)

        tracer.finish_span(
            span,
            status="ok",
            attributes={
                "gate.verdict": decision.verdict,
                "gate.blocking": len(decision.blocking),
                "gate.requests": len(decision.requests),
                "gate.overall_score": scorecard.overall,
            },
        )
        return {
            "gate_verdict": decision.verdict,
            "gate_reason": decision.reason,
            "gate_requests": list(decision.requests),
        }

    def _node_revise(self, state: PipelineState) -> PipelineState:
        """门禁退回：递增轮次、登记返工记录，然后把意见交给 A4。"""
        task = self._task(state)
        requests = list(state.get("gate_requests") or [])
        task.revision_round += 1
        self._record_revision(task, "REVIEW", str(state.get("gate_reason") or ""), requests)
        return {"feedback": requests, "revision_round": task.revision_round}

    def _node_escalation(self, state: PipelineState) -> PipelineState:
        task = self._task(state)
        # ⚠️ interrupt 必须是本节点第一处副作用点，恢复时节点会从头重放。
        human = interrupt(
            {
                "milestone": "escalation",
                "message": str(state.get("gate_reason") or "需要人工裁决"),
                "blocking": state.get("gate_requests") or [],
            }
        )
        decision = str((human or {}).get("decision") or "revise")
        comment = str((human or {}).get("comment") or "")
        self._publish(
            task,
            "approval.decided",
            f"人工裁决：{decision}" + (f"（{comment}）" if comment else ""),
            {},
        )

        if decision == "approve":
            self._publish(
                task,
                "log",
                "人工覆盖门禁：允许继续进入发布流程",
                {"overridden": state.get("gate_requests") or [], "level": "warn"},
                "warn",
            )
            return {"gate_verdict": "pass"}

        if decision == "reject":
            self._finish_rejected(task, comment or "人工驳回")
            return {"outcome": "rejected"}

        feedback = self._merge_feedback(comment, list(state.get("gate_requests") or []))
        task.revision_round += 1
        self._record_revision(
            task, "REVIEW", str(state.get("gate_reason") or ""), feedback
        )
        return {
            "gate_verdict": "revise",
            "feedback": feedback,
            "revision_round": task.revision_round,
            "gate_requests": feedback,
        }

    def _node_approval(self, state: PipelineState) -> PipelineState:
        task = self._task(state)
        score = task.scorecard.overall if task.scorecard else "—"
        # ⚠️ 同上：interrupt 之前不做任何副作用。
        human = interrupt(
            {
                "milestone": "approval",
                "message": f"内容已通过全部审核，等待人工审批（综合质量分 {score}）",
            }
        )
        decision = str((human or {}).get("decision") or "revise")
        comment = str((human or {}).get("comment") or "")
        self._publish(
            task,
            "approval.decided",
            f"人工审批：{decision}" + (f"（{comment}）" if comment else ""),
            {},
        )

        if decision == "approve":
            return {"outcome": "delivered", "approval_comment": comment}

        if decision == "reject":
            self._finish_rejected(task, comment or "人工驳回")
            return {"outcome": "rejected"}

        cycles = int(state.get("approval_cycles") or 0) + 1
        if cycles > get_config().max_revisions:
            self._finish_rejected(task, f"审批连续退回 {cycles} 次，任务终止")
            return {"outcome": "rejected"}

        feedback = self._merge_feedback(comment, [])
        task.revision_round += 1
        self._record_revision(task, "APPROVAL", f"人工审批退回：{comment}", feedback)
        return {
            "gate_verdict": "revise",
            "feedback": feedback,
            "revision_round": task.revision_round,
            "approval_cycles": cycles,
        }

    def _node_delivery(self, state: PipelineState) -> PipelineState:
        task = self._task(state)
        # 交付前再评一次：此时定稿已冻结，分数可用于跨任务的 Prompt 回归对比。
        # review 节点那一份记录的是「门禁时刻」的中间态，两者差值本身也是信号。
        self._judge_step(task, "final", "delivery")
        self._publish_delivery(task, str(state.get("approval_comment") or ""))
        return {}

    def _node_publish(self, state: PipelineState) -> PipelineState:
        """审批通过 → 生成多平台发布排期（creator.md §5 第 12 步、门禁表「发布 → A9」）。"""
        self._stage_publish(self._task(state))
        return {}

    # ---------------------------------------------------------------- #
    # 条件边路由                                                        #
    # ---------------------------------------------------------------- #

    @staticmethod
    def _route_review(state: PipelineState) -> str:
        return str(state.get("gate_verdict") or "pass")

    @staticmethod
    def _route_escalation(state: PipelineState) -> str:
        if state.get("outcome") == "rejected":
            return "rejected"
        return "revise" if state.get("gate_verdict") == "revise" else "pass"

    @staticmethod
    def _route_approval(state: PipelineState) -> str:
        outcome = state.get("outcome")
        if outcome == "delivered":
            return "delivered"
        if outcome == "rejected":
            return "rejected"
        return "revise"

    # ---------------------------------------------------------------- #
    # A0 阶段                                                          #
    # ---------------------------------------------------------------- #

    def _stage_a0(self, task: TaskRecord) -> None:
        node = self._node(task, "A0")
        node.status = "running"
        node.started_at = now_iso()
        task.turn_used += 1
        self._enter_phase(task, "INIT")
        self._publish(
            task, "agent.start", "A0 总控：解析 Brief、拆解任务卡、编排流水线", {"agent_id": "A0"}
        )

        brief = task.brief
        objective_base = f"{brief.brand}｜{brief.product}｜{brief.channel}｜{brief.objective}"
        cfg = get_config()
        language_label_text = language_label(brief.language)
        localized = is_multilingual(brief.language)

        stages: list[dict[str, str]] = [
            {
                "agent": "A1",
                "phase": "STRATEGY",
                "schema": "StrategyBrief",
                "objective": f"为「{objective_base}」输出受众洞察与核心信息屋",
            },
            {
                "agent": "A2",
                "phase": "CREATIVE",
                "schema": "CreativeConcept",
                "objective": "基于策略给出 Big Idea 与 2-3 个创意方向",
            },
            {
                "agent": "A3",
                "phase": "PLANNING",
                "schema": "ContentPlan",
                "objective": "拆解选题、内容大纲与渠道适配表",
            },
            {
                "agent": "A4",
                "phase": "DRAFTING",
                "schema": "CopyDraft",
                "objective": f"撰写 {brief.channel} 多版本文案",
            },
            {
                "agent": "A5",
                "phase": "EDITING",
                "schema": "EditedCopy",
                "objective": "审校结构、表达与品牌语气，输出质量评分",
            },
            {
                "agent": "A6",
                "phase": "FACT_CHECK",
                "schema": "FactCheckReport",
                "objective": "核查数据、引用与来源，行使否决权",
            },
            {
                "agent": "A7",
                "phase": "COMPLIANCE",
                "schema": "ComplianceReport",
                "objective": "检查广告法与品牌规范，行使否决权",
            },
            {
                "agent": "A8",
                "phase": "VISUAL_ADAPT",
                "schema": "VisualBrief",
                "objective": "输出视觉方向、配图 Prompt 与图文一致性校验",
            },
            {
                "agent": "A9",
                "phase": "CHANNEL_ADAPT",
                "schema": "ChannelAdaptation",
                "objective": "按平台规则适配标题标签并布局搜索关键词",
            },
            {
                "agent": "A10",
                "phase": "ANALYZED",
                "schema": "EffectReport",
                "objective": "预估效果区间并给出优化建议",
            },
            {
                "agent": "A11",
                "phase": "MEMORY",
                "schema": "KnowledgeCard",
                "objective": "沉淀知识卡片与模板，登记知识缺口",
            },
        ]

        stamp = now_iso()
        task.packets = [
            TaskPacket(
                task_id=f"{task.id}_p{index + 1}",
                parent_id=task.id,
                phase=item["phase"],  # type: ignore[arg-type]
                assigned_agent=item["agent"],  # type: ignore[arg-type]
                objective=item["objective"],
                context={
                    "brand": brief.brand,
                    "audience": brief.audience,
                    "channel": brief.channel,
                    "tone": brief.tone,
                    "industry": brief.industry,
                    "language": brief.language,
                    "keywords": brief.keywords,
                    "objective": brief.objective,
                    "product": brief.product,
                },
                constraints=brief.constraints,
                expected_output_schema=item["schema"],
                priority=brief.priority,
                deadline=brief.deadline,
                created_at=stamp,
            )
            for index, item in enumerate(stages)
        ]

        plan_content: dict[str, Any] = {
            "brief_summary": objective_base,
            "goal": brief.objective,
            "audience": brief.audience,
            "channel": brief.channel,
            "language": brief.language,
            "language_label": language_label_text,
            "localized": localized,
            "stages": [
                {
                    "agent": stage["agent"],
                    "phase": stage["phase"],
                    "label": PHASE_LABEL.get(stage["phase"], stage["phase"]),
                    "parallel_group": "REVIEW" if stage["agent"] in REVIEW_AGENTS else "SEQUENTIAL",
                }
                for stage in PIPELINE
                if stage["agent"] != "A0"
            ],
            "gates": [
                {"after": "A5", "rule": f"质量分 ≥ {cfg.quality_threshold} 方可通过"},
                {"after": "A6", "rule": "高事实风险未修正则否决，禁止发布"},
                {"after": "A7", "rule": "合规不通过则否决，禁止发布"},
                {"after": "A10", "rule": "效果预估低于目标区间需人工确认"},
                {"after": "A11", "rule": "知识沉淀完成后进入人工审批，批准方可交付"},
            ],
            "constraints": brief.constraints,
            "deliverables": brief.deliverables,
            "turn_budget": cfg.turn_budget,
            "max_revisions": cfg.max_revisions,
        }

        artifact = self._write_artifact(
            task,
            agent_id="A0",
            type_="task_plan",
            title="任务卡与流程编排",
            content=plan_content,
            text="\n".join(
                f"{p.assigned_agent} {p.phase}｜{p.objective}" for p in task.packets
            ),
            tags=["编排", "任务卡"],
        )

        node.status = "done"
        node.finished_at = now_iso()
        node.summary = f"拆解出 {len(task.packets)} 张任务卡，编排 {len(PIPELINE) - 1} 个阶段"

        task.results.append(
            AgentResult(
                task_id=task.id,
                agent_id="A0",
                status="success",
                summary=node.summary,
                artifacts=[artifact],
                confidence=0.95,
                handoff={"to": "A1", "reason": "任务卡已下发，进入策略洞察"},
                metrics={
                    "provider": "orchestrator",
                    "model": "builtin",
                    "simulated": True,
                },
                created_at=now_iso(),
            )
        )

        self._publish(task, "agent.finish", node.summary, {"agent_id": "A0"})
        self._save(task)

    # ---------------------------------------------------------------- #
    # 单步执行                                                          #
    # ---------------------------------------------------------------- #

    def _acquire_lease(
        self, task: TaskRecord, agent_id: str, direction: str
    ) -> tuple[IntentEntry | None, str]:
        """获取黑板工作租约，冲突时退避重试（plan.md 2.2.2 / 4.5）。

        同一任务内的租约冲突只可能来自「节点重放 / 断点续跑残留的旧租约」，
        ``acquire_intent`` 对同源租约做续期（``renewed``），所以正常情况第 1 次即可拿到；
        极端情况下记录 ``intent_conflicts`` 后放行执行 —— 不让一个「观测性机制」
        把流水线彻底卡死，冲突次数则作为可观测指标留给 /api/metrics。
        """
        for attempt in range(1, INTENT_ATTEMPTS + 1):
            entry, outcome = blackboard.acquire_intent(
                task.id, agent_id, direction, INTENT_TTL_MS
            )
            if entry is not None:
                if outcome == "renewed":
                    log.info(f"{agent_id} 续期黑板租约 {direction}（节点重放或断点续跑）")
                return entry, outcome
            if attempt < INTENT_ATTEMPTS:
                time.sleep(INTENT_BACKOFF_SECONDS * attempt)

        task.intent_conflicts += 1
        self._publish(
            task,
            "log",
            f"{agent_id} 未取得黑板租约（{direction} 已被占用），已记录冲突并继续执行",
            {"agent_id": agent_id, "direction": direction, "conflicts": task.intent_conflicts},
            "warn",
        )
        return None, "conflict"

    def _run_agent_step(
        self, task: TaskRecord, agent_id: str, feedback: list[str] | None = None
    ) -> AgentResult:
        definition = get_agent(agent_id)
        if definition is None:
            raise RuntimeError(f"智能体 {agent_id} 未实现")

        cfg = get_config()
        if task.turn_used >= cfg.turn_budget:
            raise TurnBudgetExceeded(cfg.turn_budget)

        # 一个智能体一次执行 = 一个 span（内部再嵌 llm.* 子 span）。
        # 这样「这一步花了多久、其中模型占多少」可以直接从 trace 树读出来。
        with tracer.span(
            f"{agent_id}.{definition.meta.produces}",
            agent_id=agent_id,
            attributes={"agent.phase": definition.meta.phase, "agent.revision": task.revision_round},
        ) as span:
            return self._run_agent_body(task, agent_id, definition, feedback, span)

    def _run_agent_body(
        self,
        task: TaskRecord,
        agent_id: str,
        definition: Any,
        feedback: list[str] | None,
        span: Any,
    ) -> AgentResult:
        node = self._node(task, agent_id)
        task.turn_used += 1
        node.status = "running"
        node.started_at = now_iso()
        node.runs += 1

        self._enter_phase(task, definition.meta.phase)
        pending_feedback = list(feedback or [])
        self._publish(
            task,
            "agent.start",
            f"{definition.meta.id} {definition.meta.name}：{definition.meta.description}",
            {
                "agent_id": agent_id,
                "round": task.revision_round,
                "feedback_count": len(pending_feedback),
            },
        )

        upstream = self._upstream_for(task, agent_id)
        memory_hits = self._memory_hits(task, agent_id)
        if agent_id == "A4" and pending_feedback:
            blackboard.add_activity(
                task.id, "A4", "收到返工意见", f"revision:{task.revision_round}"
            )
        lease, _ = self._acquire_lease(
            task, agent_id, f"{definition.meta.produces}@r{task.revision_round}"
        )

        ctx = AgentRunContext(
            task_id=task.id,
            brief=task.brief,
            phase=definition.meta.phase,
            revision=task.revision_round,
            feedback=pending_feedback,
            upstream=upstream,
            next_version=lambda type_: blackboard.next_version(task.id, type_),
            emit=lambda message, payload=None, _aid=agent_id: self._publish(
                task, "agent.progress", message, {**(payload or {}), "agent_id": _aid}
            ),
            artifacts=list(task.artifacts),
            memory=memory_hits,
            tenant=task.tenant,
        )

        try:
            result = self._with_retry(lambda: definition.run(ctx), agent_id)
        except Exception as error:  # noqa: BLE001
            node.status = "blocked"
            node.finished_at = now_iso()
            message = str(error)
            node.summary = f"执行失败：{message}"
            self._publish(
                task, "agent.finish", f"{agent_id} 执行失败：{message}", {"agent_id": agent_id}, "error"
            )
            self._save(task)
            raise
        finally:
            # 租约必须成对释放：残留租约会让同一方向的后续 revision 轮次拿不到租约
            if lease is not None:
                blackboard.release_intent(lease.key)
            self._sync_cost(task)

        for artifact in result.artifacts:
            blackboard.put_artifact(artifact)
            task.artifacts.append(artifact)
            self._publish(
                task,
                "blackboard.write",
                f"产物写入黑板：{artifact.title} v{artifact.version}",
                {"artifact_id": artifact.id, "artifact_type": artifact.type},
            )
        self._index_facts(task, result)
        blackboard.add_activity(
            task.id, agent_id, result.summary, f"{agent_id}:r{task.revision_round}:{result.status}"
        )

        task.results.append(result)
        task.tokens.prompt += result.metrics.prompt_tokens
        task.tokens.completion += result.metrics.completion_tokens
        task.tokens.cost_usd += result.metrics.cost_usd

        if result.gate_result == "reject":
            node.status = "blocked"
        elif result.gate_result in (None, "pass"):
            node.status = "done"
        else:
            node.status = "revise"
        node.finished_at = now_iso()
        node.confidence = result.confidence
        node.gate_result = result.gate_result
        node.summary = result.summary

        if result.artifacts:
            primary = result.artifacts[-1]
            blackboard.add_review(
                task_id=task.id,
                agent_id=agent_id,
                target_artifact_id=primary.id,
                verdict=result.gate_result or "pass",
                score=round(result.confidence * 100),
                items=[],
            )

        self._publish(
            task,
            "agent.finish",
            f"{definition.meta.id} 完成：{result.summary}",
            {
                "agent_id": agent_id,
                "confidence": result.confidence,
                "gate_result": result.gate_result,
                "risks": result.risks,
                "latency_ms": result.metrics.latency_ms,
                "provider": result.metrics.provider,
                "simulated": result.metrics.simulated,
                "handoff": result.handoff.model_dump(mode="json") if result.handoff else None,
            },
            "warn" if result.needs_human_review else "info",
        )

        if result.handoff and result.handoff.to:
            first = result.artifacts[0] if result.artifacts else None
            message = {
                "a2a_version": "1.0",
                "message_id": new_id("msg"),
                "from_agent": agent_id,
                "to_agent": result.handoff.to,
                "intent": (
                    "request_revision"
                    if result.gate_result and result.gate_result != "pass"
                    else "handoff"
                ),
                "payload": {
                    "task_id": task.id,
                    "artifact_type": first.type if first else None,
                    "artifact_ref": (
                        f"blackboard://artifacts/{task.id}/{first.type}_v{first.version}"
                        if first
                        else None
                    ),
                    "handoff_reason": result.handoff.reason,
                    "priority": task.brief.priority,
                },
                "trace_id": f"trace_{task.id}",
                "timestamp": now_iso(),
            }
            self._publish(
                task,
                "log",
                f"{agent_id} → {result.handoff.to}：{result.handoff.reason}",
                {"level": "debug", "a2a": message},
            )

        self._save(task)
        # 把这一步的关键结论写进 span：错误率、门禁不通过率因此可以直接按 span 聚合
        tracer.finish_span(
            span,
            status="ok",
            attributes={
                "agent.confidence": result.confidence,
                "agent.gate_result": result.gate_result or "pass",
                "agent.status": result.status,
                "agent.risks": len(result.risks),
                "agent.needs_human_review": result.needs_human_review,
                "agent.artifact_count": len(result.artifacts),
            },
        )
        return result

    @staticmethod
    def _with_retry(fn, agent_id: str):
        """单次重试（plan.md D10 容错）。

        分成两种情形，处置不同：

        * **输出被 ``max_tokens`` 截断** → 先把输出上限放大再重试。
          真实网关下这是最常见的失败：提示词没问题，只是结构化输出太长装不下，
          原样重试必然再截断一次。
        * 其它异常（偶发非 JSON）→ 原样再给一次机会。
        """
        from ..agents.base import TruncatedOutputError
        from ..llm.engine import boost_max_tokens, reset_max_tokens

        try:
            result = fn()
            reset_max_tokens()
            return result
        except TruncatedOutputError as error:
            boosted = boost_max_tokens()
            log.warn(f"{agent_id} 输出被截断，放大输出上限至 {boosted} tokens 后重试")
            try:
                result = fn()
                reset_max_tokens()
                return result
            except Exception:  # noqa: BLE001 - 再失败就走原有失败路径
                reset_max_tokens()
                raise
        except Exception as error:  # noqa: BLE001
            reset_max_tokens()
            log.warn(f"{agent_id} 首次执行失败，重试一次：{error}")
            return fn()

    # ---------------------------------------------------------------- #
    # 上下文装配                                                        #
    # ---------------------------------------------------------------- #

    def _upstream_for(self, task: TaskRecord, agent_id: str) -> dict[str, dict[str, Any]]:
        def get(type_: str) -> dict[str, Any]:
            artifact = blackboard.latest_artifact(task.id, type_)
            return dict(artifact.content) if artifact else {}

        if agent_id == "A1":
            return {}
        if agent_id == "A2":
            return {"strategy": get("strategy_brief")}
        if agent_id == "A3":
            return {"strategy": get("strategy_brief"), "creative": get("creative_concept")}
        if agent_id == "A4":
            return {
                "strategy": get("strategy_brief"),
                "creative": get("creative_concept"),
                "plan": get("content_plan"),
            }
        if agent_id == "A5":
            return {"plan": get("content_plan"), "draft": self._effective_draft(task, False)}
        if agent_id in ("A6", "A7"):
            return {"draft": self._effective_draft(task, True)}
        if agent_id == "A8":
            return {
                "strategy": get("strategy_brief"),
                "creative": get("creative_concept"),
                "draft": self._effective_draft(task, True),
            }
        if agent_id == "A9":
            return {
                "strategy": get("strategy_brief"),
                "plan": get("content_plan"),
                "draft": self._effective_draft(task, True),
            }
        if agent_id == "A10":
            upstream = {
                "strategy": get("strategy_brief"),
                "plan": get("content_plan"),
                "draft": self._effective_draft(task, True),
            }
            # 效果数据回填后重跑 A10：带上真实数据与发布前的预估基线，
            # A10 会据此切到「发布后复盘」模式，而不是再算一次预估。
            actuals = getattr(task, "feedback_actuals", None)
            if isinstance(actuals, dict) and actuals:
                upstream["actuals"] = actuals
                upstream["predicted"] = next(
                    (
                        dict(artifact.content)
                        for artifact in task.artifacts
                        if artifact.type == "effect_report"
                        and artifact.content.get("mode") == "pre_publish_estimate"
                    ),
                    {},
                )
            return upstream
        if agent_id == "A11":
            # A11 是收敛型智能体，需要看到全链路产物才能提炼可复用知识
            return {
                "strategy": get("strategy_brief"),
                "creative": get("creative_concept"),
                "plan": get("content_plan"),
                "draft": self._effective_draft(task, True),
                "edit": get("edited_copy"),
                "factcheck": get("fact_check_report"),
                "compliance": get("compliance_report"),
                "visual": get("visual_brief"),
                "channel": get("channel_adaptation"),
                "analysis": get("effect_report"),
            }
        return {}

    def _memory_hits(self, task: TaskRecord, agent_id: str) -> list[dict[str, Any]]:
        """为创作类智能体注入 A11 记忆库召回结果（RAG）。

        这是「A11 为其他智能体提供检索」这一职责的落地方式：
        编排层在创作智能体执行前统一检索，智能体只需消费 ``ctx.memory``，
        既避免每个智能体各写一份检索逻辑，也保证召回口径一致。
        """
        if agent_id not in MEMORY_RECALL_AGENTS:
            return []

        brief = task.brief
        query = " ".join(
            part
            for part in (
                brief.brand,
                brief.product,
                brief.industry,
                brief.channel,
                brief.audience,
                brief.objective,
                " ".join(brief.keywords),
            )
            if part
        )
        with tracer.span(
            "memory.retrieve",
            agent_id=agent_id,
            attributes={"recall.agent": agent_id, "recall.top_k": MEMORY_RECALL_TOP_K},
        ) as span:
            hits = memory_store.retrieve(
                query,
                brand=brief.brand,
                channel=brief.channel,
                industry=brief.industry,
                top_k=MEMORY_RECALL_TOP_K,
                exclude_task=task.id,
                # 只召回本租户的历史资产：跨租户复用会直接污染品牌调性（plan.md D17）
                tenant=task.tenant,
                # 只召回同语言资产：英文基调用在中文任务上比不用更糟（plan.md v2.0）
                language=brief.language,
            )
            tracer.finish_span(
                span, status="ok", attributes={"recall.hits": len(hits)}
            )
        if not hits:
            return []

        payload = [hit.to_dict() for hit in hits]
        self._publish(
            task,
            "log",
            f"记忆库为 {agent_id} 召回 {len(payload)} 条历史资产",
            {
                "level": "debug",
                "agent_id": agent_id,
                "memory": [
                    {"title": hit["title"], "score": hit["score"], "reasons": hit["reasons"]}
                    for hit in payload
                ],
            },
        )
        return payload

    # ---------------------------------------------------------------- #
    # LLM-as-a-Judge 评估（plan.md 4.3 D14 / 2.5）                        #
    # ---------------------------------------------------------------- #

    def _judge_step(
        self, task: TaskRecord, kind: str = "final", trigger: str = "review"
    ) -> JudgeReport | None:
        """对「当前有效文案」跑一次评估并落库。

        评估失败**绝不允许**影响流水线：这里兜住所有异常，只记一条 warn 事件。
        这与 embedding 的「可失败」原则一致——旁路能力不能成为新的失败面。
        """
        cfg = get_config()
        if cfg.judge.mode == "off":
            return None

        with tracer.span(
            "judge.evaluate",
            attributes={"judge.mode": cfg.judge.mode, "judge.provider": cfg.judge.provider},
        ) as span:
            try:
                report = judge_evaluate(
                    task.brief,
                    self._upstream_for_judge(task),
                    mode=cfg.judge.provider,
                    pass_threshold=cfg.judge.pass_threshold,
                    kind=kind,
                    revision=task.revision_round,
                )
            except Exception as error:  # noqa: BLE001 - 评估是旁路，不能拖垮创作链路
                log.warn(f"任务 {task.id} 评估失败（已忽略）", error)
                tracer.finish_span(
                    span, status="error", status_message=f"{type(error).__name__}: {error}"
                )
                self._publish(
                    task,
                    "log",
                    f"质量评估失败，已跳过（不影响交付）：{type(error).__name__}",
                    {"level": "warn", "error": str(error)},
                    "warn",
                )
                return None
            tracer.finish_span(
                span,
                status="ok",
                attributes={
                    "judge.total": round(report.total, 1),
                    "judge.verdict": report.verdict,
                    "judge.used_llm": report.mode == "llm",
                    "judge.fallback": report.fallback,
                },
            )

        evaluation_store.record(
            build_record(
                task_id=task.id,
                tenant=task.tenant,
                report=report,
                brand=task.brief.brand,
                channel=task.brief.channel,
                industry=task.brief.industry,
                trigger=trigger,
            )
        )
        self._publish(
            task,
            "judge.scored",
            f"质量评估：{report.total:.1f}/100（{report.verdict}），"
            f"最弱维度 {min(report.axes, key=lambda a: a.score).label if report.axes else '—'}",
            {
                "total": round(report.total, 1),
                "verdict": report.verdict,
                "mode": report.mode,
                "axes": report.axis_scores,
                "issues": report.issues[:4],
                "suggestions": report.suggestions[:4],
                "rubric": report.rubric,
                "fallback": report.fallback,
            },
            "warn" if report.verdict != "pass" else "info",
        )
        return report

    def _upstream_for_judge(self, task: TaskRecord) -> dict[str, dict[str, Any]]:
        """评估所需的最小上游集合（与 A10/A11 的口径一致，取最新产物）。"""
        def get(type_: str) -> dict[str, Any]:
            artifact = blackboard.latest_artifact(task.id, type_)
            return dict(artifact.content) if artifact else {}

        return {
            "strategy": get("strategy_brief"),
            "creative": get("creative_concept"),
            "plan": get("content_plan"),
            "draft": self._effective_draft(task, True),
            "edit": get("edited_copy"),
            "factcheck": get("fact_check_report"),
            "compliance": get("compliance_report"),
        }

    @staticmethod
    def _apply_judge_to_scorecard(
        task: TaskRecord, scorecard: QualityScorecard, report: JudgeReport
    ) -> None:
        """把评估总分按 ``judge.weight`` 混入综合质量分。

        混入而不是替换：A5/A6/A7 的门禁结论仍然是主体，评估只做微调。
        ``weight=0`` 时综合分与历史行为逐位一致，便于回滚。
        """
        weight = get_config().judge.weight
        if weight <= 0 or not report.axes:
            return
        blended = scorecard.overall * (1.0 - weight) + report.total * weight
        scorecard.overall = int(round(blended))
        task.scorecard = scorecard

    def _effective_draft(self, task: TaskRecord, include_editor: bool) -> dict[str, Any]:
        """装配「当前有效文案」。

        A5 的修订稿会覆盖 A4 草稿中被选中的那个版本，
        保证 A6/A7/A10 审查的是真正要发布的文本。
        """
        copy_artifact = blackboard.latest_artifact(task.id, "copy_draft")
        if copy_artifact is None:
            return {}
        content = copy.deepcopy(copy_artifact.content)

        if not include_editor:
            return content

        edit = blackboard.latest_artifact(task.id, "edited_copy")
        if edit is None or edit.revision != copy_artifact.revision:
            return content

        revised = (edit.content or {}).get("revised")
        if not isinstance(revised, dict) or not revised.get("body"):
            return content

        target_id = str(
            (edit.content or {}).get("target_version")
            or content.get("recommended_version")
            or "V1"
        )
        versions = content.get("versions")
        versions = versions if isinstance(versions, list) else []
        index = next(
            (i for i, v in enumerate(versions) if isinstance(v, dict) and str(v.get("id")) == target_id),
            -1,
        )
        base = dict(versions[index]) if index >= 0 else {}
        merged = {
            **base,
            "id": target_id,
            "title": revised.get("title"),
            "body": revised.get("body"),
            "cta": revised.get("cta"),
            "hashtags": revised.get("hashtags"),
        }
        if index >= 0:
            versions[index] = merged
        else:
            versions.append(merged)

        content["versions"] = versions
        content["recommended_version"] = target_id
        return content

    def _current_scorecard(self, task: TaskRecord) -> QualityScorecard:
        def pick(type_: str) -> dict[str, Any] | None:
            artifact = blackboard.latest_artifact(task.id, type_)
            return dict(artifact.content) if artifact else None

        return build_scorecard(
            editor=pick("edited_copy"),
            fact_check=pick("fact_check_report"),
            compliance=pick("compliance_report"),
        )

    def _index_facts(self, task: TaskRecord, result: AgentResult) -> None:
        if result.agent_id == "A1" and result.artifacts:
            house = (result.artifacts[0].content or {}).get("message_house")
            points = (house or {}).get("support_points") if isinstance(house, dict) else None
            if isinstance(points, list):
                for point in points[:4]:
                    blackboard.add_fact(
                        task_id=task.id,
                        agent_id="A1",
                        claim=str(point),
                        source="A1 策略推断（待验证）",
                        status="candidate",
                        confidence=0.6,
                    )

        if result.agent_id == "A6" and result.artifacts:
            checks = (result.artifacts[0].content or {}).get("checks")
            if isinstance(checks, list):
                for check in checks:
                    if not isinstance(check, dict):
                        continue
                    status = str(check.get("status"))
                    raw_conf = check.get("confidence")
                    confidence = (
                        float(raw_conf)
                        if isinstance(raw_conf, (int, float)) and not isinstance(raw_conf, bool)
                        else 0.6
                    ) or 0.6
                    blackboard.add_fact(
                        task_id=task.id,
                        agent_id="A6",
                        claim=str(check.get("claim")),
                        source=str(check.get("source") or "未提供"),
                        status=(
                            "verified"
                            if status == "verified"
                            else "rejected"
                            if status == "contradicted"
                            else "candidate"
                        ),
                        confidence=confidence,
                    )

    # ---------------------------------------------------------------- #
    # 最终交付 / 驳回 / 失败                                            #
    # ---------------------------------------------------------------- #

    @staticmethod
    def _schedule_item(
        *,
        order: int,
        channel: str,
        slot: str,
        title: str,
        keywords: Any,
        slots: list[str],
    ) -> dict[str, Any]:
        """构造一条排期项。

        ``due_at`` 由建议时段换算而来，供后台自动投递判断「是否到点」；
        ``dispatch_status`` / ``attempts`` / ``last_error`` 记录投递回执，
        与人工「登记发布」共用同一条状态机。
        """
        return {
            "order": order,
            "channel": channel,
            "slot": slot,
            "recommended_slots": list(slots),
            "title": title,
            "keywords": keywords or [],
            "status": "scheduled",
            "published_at": None,
            "url": "",
            # 自动投递相关字段（plan.md v2.0「自动发布」）
            "due_at": compute_due_at(slot),
            "dispatch_status": "pending",
            "attempts": 0,
            "last_error": "",
        }

    def _stage_publish(self, task: TaskRecord) -> None:
        """生成发布排期：把 A9 的渠道适配稿变成可执行的「渠道 × 时段」清单。

        creator.md 的门禁表把「发布」划给 A9，并要求人工审批通过后才发布，
        因此这一步放在 approval 之后、delivery 之前。这里只产出排期与投放清单，
        真正的平台投放仍由运营执行，随后通过 ``/api/tasks/{id}/feedback`` 回填效果数据。
        """
        self._enter_phase(task, "PUBLISHED")
        channel_plan = self._content_of(task, "channel_adaptation")
        platforms = channel_plan.get("platforms")
        platforms = platforms if isinstance(platforms, list) else []

        primary_slots = publish_slots(task.brief.channel)
        schedule: list[dict[str, Any]] = []
        for index, item in enumerate(platforms):
            if not isinstance(item, dict):
                continue
            channel = str(item.get("channel") or task.brief.channel)
            slots = publish_slots(channel)
            schedule.append(
                self._schedule_item(
                    order=index + 1,
                    channel=channel,
                    # 优先用 A9 给出的建议时段，缺失时按渠道经验值兜底
                    slot=str(item.get("publish_slot") or (slots[0] if slots else "")),
                    title=str(item.get("title") or ""),
                    keywords=item.get("keywords") or [],
                    slots=slots,
                )
            )
        if not schedule:
            schedule.append(
                self._schedule_item(
                    order=1,
                    channel=task.brief.channel,
                    slot=primary_slots[0] if primary_slots else "",
                    title="",
                    keywords=[],
                    slots=primary_slots,
                )
            )

        stamp = now_iso()
        task.published_at = stamp
        content: dict[str, Any] = {
            "brand": task.brief.brand,
            "primary_channel": task.brief.channel,
            "approved_at": stamp,
            "mode": "manual",
            "schedule": schedule,
            "checklist": [
                "确认各渠道标题 / 正文 / 话题标签已按适配稿替换",
                "确认配图与文案口径一致（以 A8 的图文对照结论为准）",
                "投放后 24~72 小时内回填曝光 / 点击 / 互动数据，触发复盘",
            ],
            "notes": "排期为建议时段，不构成效果承诺",
        }

        artifact = self._write_artifact(
            task,
            agent_id="A9",
            type_="publish_plan",
            title="多平台发布排期",
            content=content,
            text="\n".join(
                f"{item['order']}. {item['channel']}｜{item['slot']}｜{item['status']}"
                for item in schedule
            ),
            tags=["发布", "排期", task.brief.channel],
        )
        task.results.append(
            AgentResult(
                task_id=task.id,
                agent_id="A9",
                status="success",
                summary=f"生成 {len(schedule)} 条渠道发布排期，等待投放与效果回填",
                artifacts=[artifact],
                confidence=0.9,
                metrics={"provider": "orchestrator", "model": "builtin", "simulated": True},
                created_at=now_iso(),
            )
        )
        self._publish(
            task,
            "log",
            f"发布排期已生成：{len(schedule)} 个渠道待投放（审批通过后自动排期）",
            {"artifact_id": artifact.id, "channels": [item["channel"] for item in schedule]},
        )

    # ---------------------------------------------------------------- #
    # 发布与效果回填（creator.md §5 第 12 步、plan.md v2.0 自动发布）      #
    # ---------------------------------------------------------------- #

    @staticmethod
    def _latest_publish_plan(task: TaskRecord) -> Artifact | None:
        return next((a for a in reversed(task.artifacts) if a.type == "publish_plan"), None)

    def publish_schedule(self, task_id: str) -> dict[str, Any]:
        """读取当前发布排期（``GET /api/tasks/{id}/publish``）。"""
        task = task_store.get(task_id)
        if task is None:
            raise KeyError("任务不存在")
        artifact = self._latest_publish_plan(task)
        return {
            "task_id": task.id,
            "phase": task.phase,
            "status": task.status,
            "published_at": task.published_at,
            "artifact_id": artifact.id if artifact else None,
            "schedule": list(artifact.content.get("schedule") or []) if artifact else [],
        }

    def mark_published(self, task_id: str, channel: str = "", url: str = "") -> dict[str, Any]:
        """把排期中的一个渠道（``channel`` 为空表示全部）登记为已发布。

        系统不代运营点「发布」按钮（各平台开放接口差异大且需要授权），
        但必须记录「哪个渠道、什么时候投出去」这份事实 —— 否则后续回填的
        效果数据没有可对齐的基线，A/B 结论也就无从谈起。
        """
        task = task_store.get(task_id)
        if task is None:
            raise KeyError("任务不存在")
        artifact = self._latest_publish_plan(task)
        if artifact is None:
            raise ValueError("该任务还没有发布排期，请先完成审批")

        target = channel.strip()
        schedule = [
            dict(item)
            for item in (artifact.content.get("schedule") or [])
            if isinstance(item, dict)
        ]
        stamp = now_iso()
        matched: list[str] = []
        for item in schedule:
            if target and str(item.get("channel")) != target:
                continue
            if str(item.get("status")) == "published":
                continue
            item["status"] = "published"
            item["published_at"] = stamp
            if url:
                item["url"] = url
            matched.append(str(item.get("channel")))

        if not matched:
            raise ValueError(f"渠道「{target or '全部'}」没有待发布的排期项（可能已登记过）")

        content = dict(artifact.content)
        content["schedule"] = schedule
        content["last_published_at"] = stamp
        updated = self._write_artifact(
            task,
            agent_id="A9",
            type_="publish_plan",
            title="多平台发布排期（已更新）",
            content=content,
            text="\n".join(
                f"{item.get('order')}. {item.get('channel')}｜{item.get('slot')}｜{item.get('status')}"
                for item in schedule
            ),
            tags=["发布", "排期", task.brief.channel],
        )
        if not task.published_at:
            task.published_at = stamp
        self._publish(
            task,
            "log",
            f"已登记发布：{len(matched)} 个渠道（{'、'.join(matched)}）",
            {"artifact_id": updated.id, "channels": matched},
        )
        self._save(task, True)

        return {
            "task_id": task.id,
            "published": matched,
            "published_at": stamp,
            "artifact_id": updated.id,
            "schedule": schedule,
        }

    def dispatch_publish(
        self,
        task_id: str,
        channel: str = "",
        *,
        force: bool = True,
    ) -> dict[str, Any]:
        """按排期把内容投递给发布 webhook（``POST /api/tasks/{id}/publish/dispatch``）。

        * ``force=True``（手动触发）：忽略 ``due_at``，立即投递；
        * ``force=False``（后台定时器）：只投递已到期且未发布的条目。

        投递通道是平台无关的（见 ``core/publisher.py``）；未配置 ``PUBLISH_WEBHOOK_URL``
        时退化为「登记发布」，让离线环境也走同一条状态机。
        """
        task = task_store.get(task_id)
        if task is None:
            raise KeyError("任务不存在")
        artifact = self._latest_publish_plan(task)
        if artifact is None:
            raise ValueError("该任务还没有发布排期，请先完成审批")

        cfg = get_config()
        target = channel.strip()
        stamp = now_iso()
        schedule = [
            dict(item)
            for item in (artifact.content.get("schedule") or [])
            if isinstance(item, dict)
        ]
        platform_body = {
            str(item.get("channel")): str(item.get("body") or "")
            for item in (self._content_of(task, "channel_adaptation").get("platforms") or [])
            if isinstance(item, dict)
        }

        dispatched: list[str] = []
        failed: list[str] = []
        skipped: list[str] = []
        # 投递按渠道各自成 span：失败重试的耗时与 last_error 因此可精确定位到渠道
        for item in schedule:
            name = str(item.get("channel"))
            if target and name != target:
                continue
            if str(item.get("status")) == "published":
                continue
            if not force and not is_due(item):
                skipped.append(name)
                continue

            with tracer.span(
                "publish.dispatch",
                kind="client",
                attributes={"publish.channel": name, "publish.due_at": item.get("due_at")},
            ) as span:
                # W3C 传播：让发布网关把这次投递与任务的调用轨迹对齐
                traceparent = tracer.current_traceparent()
                ok, detail = dispatch_webhook(
                    cfg.publish.webhook_url,
                    {
                        "task_id": task.id,
                        "brand": task.brief.brand,
                        "product": task.brief.product,
                        "channel": name,
                        "slot": item.get("slot"),
                        "due_at": item.get("due_at"),
                        "title": item.get("title"),
                        "keywords": item.get("keywords") or [],
                        "body": platform_body.get(name, ""),
                    },
                    retry=cfg.publish.retry,
                    timeout_ms=cfg.llm.timeout_ms,
                    headers={"traceparent": traceparent} if traceparent else None,
                )
                # 未配置 webhook 时退化为「登记发布」，是既定离线语义而非故障
                tracer.finish_span(
                    span,
                    status="ok" if (ok or not cfg.publish.webhook_url) else "error",
                    status_message="" if ok else detail,
                    attributes={
                        "publish.webhook_configured": bool(cfg.publish.webhook_url),
                        "publish.result": "dispatched"
                        if ok
                        else ("skipped" if not cfg.publish.webhook_url else "failed"),
                    },
                )
            item["attempts"] = int(item.get("attempts") or 0) + 1
            item["last_error"] = "" if ok else detail
            if ok:
                item["dispatch_status"] = "dispatched"
            elif not cfg.publish.webhook_url:
                # 未配置 webhook：与人工「登记发布」等价，保证离线可用
                item["dispatch_status"] = "skipped"
            else:
                item["dispatch_status"] = "failed"
                failed.append(name)
                continue
            item["status"] = "published"
            item["published_at"] = stamp
            dispatched.append(name)

        if not dispatched and not failed:
            if skipped:
                # 后台定时器扫描时未到点，属正常情况：不落盘、不改版本
                return {
                    "task_id": task.id,
                    "dispatched": [],
                    "failed": [],
                    "skipped": skipped,
                    "published_at": task.published_at,
                    "artifact_id": artifact.id,
                    "schedule": schedule,
                }
            raise ValueError(f"渠道「{target or '全部'}」没有待发布的排期项（可能已登记过）")

        content = dict(artifact.content)
        content["schedule"] = schedule
        content["mode"] = "auto" if cfg.publish.webhook_url else "manual"
        content["last_published_at"] = stamp
        updated = self._write_artifact(
            task,
            agent_id="A9",
            type_="publish_plan",
            title="多平台发布排期（已投递）",
            content=content,
            text="\n".join(
                f"{item.get('order')}. {item.get('channel')}｜{item.get('slot')}｜"
                f"{item.get('dispatch_status')}"
                for item in schedule
            ),
            tags=["发布", "排期", "自动投递", task.brief.channel],
        )
        if dispatched and not task.published_at:
            task.published_at = stamp
        self._publish(
            task,
            "log",
            f"发布投递：成功 {len(dispatched)}｜失败 {len(failed)}"
            + (f"（{'、'.join(dispatched)}）" if dispatched else ""),
            {"artifact_id": updated.id, "dispatched": dispatched, "failed": failed},
        )
        self._save(task, True)

        return {
            "task_id": task.id,
            "dispatched": dispatched,
            "failed": failed,
            "skipped": skipped,
            "published_at": task.published_at,
            "artifact_id": updated.id,
            "schedule": schedule,
        }

    def publish_queue(
        self, *, due_only: bool = False, tenant: str | None = None
    ) -> dict[str, Any]:
        """跨任务的待发布队列（``GET /api/publish/queue``），按到期时间升序。

        ``due_only=True`` 只返回已到期的条目，供后台定时器消费；
        ``tenant`` 用于鉴权开启时把队列限定在当前租户内。
        """
        items: list[dict[str, Any]] = []
        for task in task_store.list():
            if task.status in ("rejected", "failed"):
                continue
            if tenant and task.tenant != tenant:
                continue
            artifact = self._latest_publish_plan(task)
            if artifact is None:
                continue
            for item in artifact.content.get("schedule") or []:
                if not isinstance(item, dict) or str(item.get("status")) == "published":
                    continue
                if due_only and not is_due(item):
                    continue
                items.append(
                    {
                        "task_id": task.id,
                        "brand": task.brief.brand,
                        "product": task.brief.product,
                        "channel": item.get("channel"),
                        "slot": item.get("slot"),
                        "due_at": item.get("due_at"),
                        "dispatch_status": item.get("dispatch_status") or "pending",
                        "attempts": item.get("attempts") or 0,
                        "last_error": item.get("last_error") or "",
                        "title": item.get("title") or "",
                        "task_status": task.status,
                    }
                )
        # 没有 due_at 的条目（时段文案无法解析）排到最后，仍需人工触发
        items.sort(key=lambda row: str(row.get("due_at") or "~"))
        return {"total": len(items), "items": items}

    def dispatch_due(self, tenant: str | None = None) -> dict[str, Any]:
        """后台定时器入口：投递所有已到期的排期项（``POST /api/publish/tick``）。"""
        queue = self.publish_queue(due_only=True, tenant=tenant)
        dispatched: list[str] = []
        failed: list[str] = []
        for row in queue["items"]:
            try:
                outcome = self.dispatch_publish(
                    str(row["task_id"]), str(row["channel"]), force=False
                )
            except (KeyError, ValueError) as error:
                log.warn(f"自动投递跳过 {row['task_id']}/{row['channel']}：{error}")
                continue
            dispatched.extend(outcome["dispatched"])
            failed.extend(outcome["failed"])
        return {"due": queue["total"], "dispatched": dispatched, "failed": failed}

    def record_feedback(self, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """回填发布后的真实效果数据，并触发 A10 复盘（A/B 闭环回填）。

        做法是把真实数据挂到任务上再重跑 A10：上游一旦出现 ``actuals``，
        A10 就从「发布前预估」切到「发布后复盘」，产出 ``effect_report`` 新版本。
        复盘与预估因此共用同一个智能体与同一种产物类型，不必再造一个 A12。
        """
        task = task_store.get(task_id)
        if task is None:
            raise KeyError("任务不存在")
        if task.status in ("running", "awaiting_approval"):
            raise ValueError("任务仍在执行中，暂时不能回填效果数据")

        raw = payload.get("metrics")
        metrics = raw if isinstance(raw, dict) else {}
        cleaned = {
            key: value
            for key, value in metrics.items()
            if isinstance(value, (int, float, str)) and not isinstance(value, bool)
        }
        if not cleaned:
            raise ValueError("metrics 不能为空，至少需要 exposure 与 clicks")

        channel = str(payload.get("channel") or task.brief.channel)
        actuals: dict[str, Any] = {
            "channel": channel,
            "window": str(payload.get("window") or "发布后 72 小时"),
            **cleaned,
        }
        task.feedback_actuals = actuals  # type: ignore[attr-defined]

        try:
            self.mark_published(task_id, channel, str(payload.get("url") or ""))
        except ValueError:
            # 排期里没有该渠道也要允许回填，避免运营被流程细节挡住
            log.warn(f"任务 {task.id} 回填时未找到渠道 {channel} 的待发布排期项，仅记录数据")

        result = self._run_agent_step(task, "A10")
        self._publish(
            task,
            "log",
            f"效果数据已回填（{channel}），A10 完成复盘并给出 A/B 结论",
            {"channel": channel, "actuals": actuals},
        )
        self._save(task, True)

        return {
            "task": task,
            "result": result,
            "actuals": actuals,
            "artifact": result.artifacts[-1] if result.artifacts else None,
        }

    def _publish_delivery(self, task: TaskRecord, comment: str) -> None:
        draft = self._effective_draft(task, True)
        versions = draft.get("versions")
        versions = versions if isinstance(versions, list) else []
        target = next(
            (
                v
                for v in versions
                if isinstance(v, dict) and str(v.get("id")) == str(draft.get("recommended_version"))
            ),
            None,
        )
        if target is None:
            target = versions[0] if versions else {}
        hashtags = target.get("hashtags")
        hashtags = hashtags if isinstance(hashtags, list) else []

        # 交付标题必须符合主渠道的字数上限：这是「能不能直接发出去」的硬条件。
        # A5 只在 小红书 分支做了压缩，其它渠道的基础标题可能超限；A9 的渠道标题是
        # **关键词前置的搜索变体**（语义不同，不能拿来当交付标题），因此必须在这里兜底压缩。
        # 原始标题一并保留在 base_title，避免压缩丢信息后无法追溯。
        #
        # 上限按**目标语言的口径**取：英文按词、中日韩按字。用错口径会让
        # 「标题合规」的判断失去意义（12 个词的英文标题早已超出信息流截断点）。
        base_title = str(target.get("title") or "")
        delivery_limit = title_limit_for(task.brief.channel, task.brief.language)
        title = (
            base_title
            if len(base_title) <= delivery_limit
            else f"{base_title[: delivery_limit - 1]}…"
        )

        # 交付物把视觉、渠道、知识三类「投放侧资产」一并带上，
        # 让交付件可直接交给运营执行，而不是只有一篇正文。
        visual_brief = self._content_of(task, "visual_brief")
        channel_plan = self._content_of(task, "channel_adaptation")
        memory = self._content_of(task, "knowledge_card")
        direction = visual_brief.get("visual_direction")
        direction = direction if isinstance(direction, dict) else {}
        assets = visual_brief.get("assets")
        assets = assets if isinstance(assets, dict) else {}
        aligned = visual_brief.get("copy_visual_check")
        aligned = aligned if isinstance(aligned, dict) else {}
        platforms = channel_plan.get("platforms")
        platforms = platforms if isinstance(platforms, list) else []
        cards = memory.get("knowledge_cards")
        cards = cards if isinstance(cards, list) else []

        content: dict[str, Any] = {
            "channel": task.brief.channel,
            "brand": task.brief.brand,
            "product": task.brief.product,
            "title": title,
            # 压缩前的原始标题：便于追溯「发出去的标题」与「写给运营的完整标题」的差异
            "base_title": base_title,
            "title_limit": delivery_limit,
            "title_trimmed": title != base_title,
            "body": target.get("body") or "",
            "cta": target.get("cta") or "",
            "hashtags": hashtags,
            "channels": [
                {
                    "channel": item.get("channel"),
                    "title": item.get("title"),
                    "keywords": item.get("keywords") or [],
                    "publish_slot": item.get("publish_slot"),
                }
                for item in platforms
                if isinstance(item, dict)
            ],
            "visual": {
                "style": direction.get("style") or "",
                "mood": direction.get("mood") or "",
                "palette": direction.get("palette") or [],
                "cover_ratio": assets.get("cover_ratio") or "",
                "image_prompt_count": len(visual_brief.get("image_prompts") or []),
                "copy_aligned": aligned.get("aligned", True),
            },
            "knowledge_cards": cards,
            # 交付件直接带上发布排期，运营拿到即可执行；status/published_at 由回填接口更新
            "publish_schedule": [
                {
                    "channel": item.get("channel"),
                    "slot": item.get("slot"),
                    "status": item.get("status"),
                    "published_at": item.get("published_at"),
                    "url": item.get("url"),
                }
                for item in (
                    self._content_of(task, "publish_plan").get("schedule") or []
                )
                if isinstance(item, dict)
            ],
            "published_at": task.published_at,
            "scorecard": task.scorecard.model_dump(mode="json") if task.scorecard else None,
            "review_summary": {
                "editor": self._summary_of(task, "A5"),
                "fact_check": self._summary_of(task, "A6"),
                "compliance": self._summary_of(task, "A7"),
                "visual": self._summary_of(task, "A8"),
                "channel": self._summary_of(task, "A9"),
                "memory": self._summary_of(task, "A11"),
            },
            "revision_rounds": task.revision_round,
            "approved_by": "human",
            "approval_comment": comment,
        }

        artifact = self._write_artifact(
            task,
            agent_id="A0",
            type_="final_delivery",
            title="最终交付物",
            content=content,
            text=f"{target.get('title') or ''}\n\n{target.get('body') or ''}\n\n"
            f"{' '.join(str(h) for h in hashtags)}",
            tags=["交付", "已审批"],
        )

        task.results.append(
            AgentResult(
                task_id=task.id,
                agent_id="A0",
                status="success",
                summary="人工审批通过，生成最终交付物并归档",
                artifacts=[artifact],
                confidence=0.95,
                metrics={
                    "provider": "orchestrator",
                    "model": "builtin",
                    "simulated": True,
                },
                created_at=now_iso(),
            )
        )

        task.approval.required = True
        task.approval.decision = "approved"
        task.approval.comment = comment
        task.approval.decided_at = now_iso()

        task.status = "completed"
        task.phase = "ARCHIVED"
        task.finished_at = now_iso()
        task.updated_at = task.finished_at

        node = self._find_node(task, "A11")
        if node is not None:
            node.status = "done"

        self._publish(task, "task.status", "任务已完成并归档", {"status": "completed"})
        self._publish(
            task,
            "task.completed",
            f"任务完成：{task.brief.brand} / {task.brief.channel}，共 {task.revision_round} 轮返工",
            {
                "scorecard": task.scorecard.model_dump(mode="json") if task.scorecard else None,
                "tokens": task.tokens.model_dump(mode="json"),
                "turns": task.turn_used,
            },
        )
        task_store.schedule_save(task, True)
        blackboard.flush()
        log.info(
            f"任务 {task.id} 完成，综合质量分 "
            f"{task.scorecard.overall if task.scorecard else '—'}"
        )

    @staticmethod
    def _summary_of(task: TaskRecord, agent_id: str) -> str:
        summary = ""
        for result in task.results:
            if result.agent_id == agent_id:
                summary = result.summary
        return summary

    @staticmethod
    def _content_of(task: TaskRecord, artifact_type: str) -> dict[str, Any]:
        """读取某类产物的最新内容（不存在时返回空字典，便于交付物装配）。"""
        artifact = blackboard.latest_artifact(task.id, artifact_type)
        if artifact is None or not isinstance(artifact.content, dict):
            return {}
        return artifact.content

    def _sync_cost(self, task: TaskRecord) -> None:
        """把成本账本同步进任务记录，让前端与 /api/metrics 看到实时花费。

        账本是唯一事实来源：缓存命中不计费但计入 ``cached``，熔断后 ``cut_off`` 置位。
        """
        ledger = cost_guard.ledger(task.id)
        if ledger is None:
            return
        task.tokens.calls = ledger.calls
        task.tokens.cached = ledger.cached
        task.tokens.cut_off = ledger.cut_off
        task.tokens.cost_usd = round(ledger.cost_usd, 6)

    def _settle_cost(self, task: TaskRecord) -> None:
        """任务终态时结算账本；发生熔断时补一条可解释的事件。"""
        self._sync_cost(task)
        ledger = cost_guard.end(task.id)
        if ledger is None:
            return
        if ledger.cut_off:
            self._publish(
                task,
                "log",
                f"成本熔断生效：调用 {ledger.calls} 次（缓存命中 {ledger.cached} 次），"
                f"花费 ${ledger.cost_usd:.4f}，超出预算的部分已由离线引擎兜底",
                {"cost": ledger.to_dict()},
                "warn",
            )
        self._save(task, True)

    def _finish_rejected(self, task: TaskRecord, reason: str) -> None:
        task.status = "rejected"
        task.phase = "REJECTED"
        task.finished_at = now_iso()
        task.error = reason
        task.approval.required = True
        task.approval.decision = "rejected"
        task.approval.comment = reason
        task.approval.decided_at = task.finished_at
        blackboard.release_task_intents(task.id)
        self._publish(task, "task.status", f"任务被驳回：{reason}", {"status": "rejected"}, "warn")
        task_store.schedule_save(task, True)

    def _handle_fatal(self, task: TaskRecord, error: BaseException) -> None:
        message = str(error)
        task.status = "failed"
        task.phase = "FAILED"
        task.error = message
        task.finished_at = now_iso()
        self._publish(task, "task.failed", f"任务执行失败：{message}", {"error": message}, "error")
        log.error(f"任务 {task.id} 失败", error)
        blackboard.release_task_intents(task.id)
        task_store.schedule_save(task, True)

    # ---------------------------------------------------------------- #
    # 人工介入                                                          #
    # ---------------------------------------------------------------- #

    def _wait_for_human(self, task: TaskRecord, milestone: str, message: str) -> dict[str, str]:
        if task.id in self._auto_approve:
            self._publish(
                task,
                "approval.required",
                f"{message}（自动审批已开启，直接通过）",
                {"milestone": milestone, "auto": True},
            )
            return {"decision": "approve", "comment": "自动审批模式"}

        event = threading.Event()
        with self._lock:
            self._waiters[task.id] = event
        task.status = "awaiting_approval"
        task.approval.required = True
        task.approval.decision = "pending"
        task_store.schedule_save(task, True)
        self._publish(task, "approval.required", message, {"milestone": milestone})

        event.wait()
        with self._lock:
            decision = self._decisions.pop(task.id, None)
            self._waiters.pop(task.id, None)
        return decision or {"decision": "revise", "comment": ""}

    def decide(self, task_id: str, decision: str, comment: str = "") -> TaskRecord:
        task = task_store.get(task_id)
        if task is None:
            raise KeyError(f"任务不存在：{task_id}")

        with self._lock:
            event = self._waiters.get(task_id)
        if event is None:
            raise RuntimeError("当前任务不处于等待人工裁决状态")

        with self._lock:
            self._decisions[task_id] = {"decision": decision, "comment": comment}
        if task.status == "awaiting_approval":
            task.status = "running"
        self._update(task)
        event.set()
        return task

    # ---------------------------------------------------------------- #
    # 断点续跑                                                          #
    # ---------------------------------------------------------------- #

    def resume_interrupted(self, task_ids: list[str]) -> int:
        """进程重启后尝试用 checkpointer 续跑中断任务；返回实际恢复的数量。"""
        resumed = 0
        for task_id in task_ids:
            task = task_store.get(task_id)
            if task is None:
                continue
            config = {
                "configurable": {"thread_id": task.id},
                "recursion_limit": RECURSION_LIMIT,
            }
            try:
                snapshot = self._graph.get_state(config)
            except Exception as error:  # noqa: BLE001
                log.warn(f"任务 {task.id} 读取检查点失败", error)
                continue

            if not snapshot.next and not snapshot.values:
                self._handle_fatal(
                    task, RuntimeError("进程重启且无可用检查点，无法断点续跑")
                )
                continue

            log.info(f"任务 {task.id} 从检查点续跑")
            self._publish(task, "log", "服务重启，从最近检查点继续执行", {"level": "warn"}, "warn")
            self._spawn(task, resuming=True)
            resumed += 1
        return resumed

    def purge_checkpoints(self, task_id: str) -> int:
        """清理某个任务的 LangGraph 检查点，返回删除的行数。

        ``checkpoints.sqlite`` 只增不减，长期运行会随任务数线性膨胀；
        任务被显式删除后其检查点已无续跑价值，因此随删除一并回收。
        表结构由 LangGraph 维护，这里做「存在即删、缺失即跳过」的防御式处理。
        """
        if not CHECKPOINT_FILE.exists():
            return 0
        removed = 0
        try:
            conn = sqlite3.connect(str(CHECKPOINT_FILE))
            try:
                tables = {
                    row[0]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
                }
                for table in ("writes", "checkpoints"):  # 先删子表，避免留下孤儿写入
                    if table not in tables:
                        continue
                    cursor = conn.execute(
                        f"DELETE FROM {table} WHERE thread_id = ?", (task_id,)  # noqa: S608
                    )
                    removed += cursor.rowcount or 0
                conn.commit()
            finally:
                conn.close()
        except Exception as error:  # noqa: BLE001 - 清理失败不应影响删除接口
            log.warn(f"清理任务 {task_id} 的检查点失败", error)
            return 0
        if removed:
            log.info(f"已清理任务 {task_id} 的 {removed} 行检查点")
        return removed

    def is_running(self, task_id: str) -> bool:
        return task_id in self._running

    def is_waiting(self, task_id: str) -> bool:
        """任务是否已挂起、正在等待人工裁决（``decide`` 可被接受）。"""
        with self._lock:
            return task_id in self._waiters

    # ---------------------------------------------------------------- #
    # 工具方法                                                          #
    # ---------------------------------------------------------------- #

    def _record_revision(
        self, task: TaskRecord, phase: str, reason: str, requests: list[str]
    ) -> None:
        task.revisions.append(
            RevisionRecord(
                round=task.revision_round,
                phase=phase,  # type: ignore[arg-type]
                reason=reason,
                requests=requests,
                created_at=now_iso(),
            )
        )

        for node in task.pipeline:
            if node.phase == "DRAFTING" or node.agent_id in REVIEW_AGENTS:
                node.status = "pending"
                node.gate_result = None

        self._publish(
            task,
            "revision.requested",
            f"第 {task.revision_round} 轮返工：{reason}",
            {"round": task.revision_round, "requests": requests[:6]},
            "warn",
        )
        task_store.schedule_save(task, True)

    @staticmethod
    def _merge_feedback(comment: str, requests: list[str]) -> list[str]:
        merged = list(requests)
        stripped = comment.strip()
        if stripped:
            merged.insert(0, f"[人工意见] {stripped}")
        return merged

    def _enter_phase(self, task: TaskRecord, phase: str) -> None:
        if task.phase == phase:
            return
        task.phase = phase  # type: ignore[assignment]
        self._publish(
            task,
            "phase.enter",
            f"进入阶段：{PHASE_LABEL.get(phase, phase)}",
            {"phase": phase},
        )
        self._save(task)

    @staticmethod
    def _task(state: PipelineState) -> TaskRecord:
        task = task_store.get(str(state.get("task_id") or ""))
        if task is None:
            raise RuntimeError(f"任务不存在：{state.get('task_id')}")
        return task

    @staticmethod
    def _find_node(task: TaskRecord, agent_id: str) -> PipelineNode | None:
        return next((n for n in task.pipeline if n.agent_id == agent_id), None)

    def _node(self, task: TaskRecord, agent_id: str) -> PipelineNode:
        node = self._find_node(task, agent_id)
        if node is not None:
            return node

        # 兼容历史任务：旧版本创建的流水线可能缺少后来新增的节点（例如 A8/A9/A11）。
        # 按蓝图补齐而不是直接报错，避免「代码升级后旧任务无法续跑」的隐式数据迁移问题。
        stage = next((item for item in PIPELINE if item["agent"] == agent_id), None)
        if stage is None:
            raise RuntimeError(f"流水线蓝图缺少节点 {agent_id}")
        node = PipelineNode(
            agent_id=stage["agent"],  # type: ignore[arg-type]
            phase=stage["phase"],  # type: ignore[arg-type]
            status="pending",
            runs=0,
        )
        task.pipeline.append(node)
        order = {item["agent"]: index for index, item in enumerate(PIPELINE)}
        task.pipeline.sort(key=lambda item: order.get(item.agent_id, len(PIPELINE)))
        return node

    def _write_artifact(
        self,
        task: TaskRecord,
        *,
        agent_id: str,
        type_: str,
        title: str,
        content: dict[str, Any],
        text: str,
        tags: list[str],
    ) -> Artifact:
        artifact = Artifact(
            id=new_id("art"),
            task_id=task.id,
            agent_id=agent_id,  # type: ignore[arg-type]
            type=type_,  # type: ignore[arg-type]
            version=blackboard.next_version(task.id, type_),
            title=title,
            content=content,
            text=text,
            revision=task.revision_round,
            created_at=now_iso(),
            tags=tags,
        )
        blackboard.put_artifact(artifact)
        task.artifacts.append(artifact)
        self._publish(
            task,
            "blackboard.write",
            f"产物写入黑板：{artifact.title} v{artifact.version}",
            {"artifact_id": artifact.id, "artifact_type": artifact.type},
        )
        return artifact

    def _update(self, task: TaskRecord) -> None:
        task.updated_at = now_iso()
        self._save(task)

    @staticmethod
    def _save(task: TaskRecord, immediate: bool = False) -> None:
        task.updated_at = now_iso()
        task_store.schedule_save(task, immediate)

    @staticmethod
    def _publish(
        task: TaskRecord,
        type_: str,
        message: str,
        payload: dict[str, Any],
        level: str = "info",
    ) -> None:
        event_bus.publish(
            task_id=task.id,
            type=type_,
            message=message,
            level=level,
            agent_id=payload.get("agent_id"),
            phase=task.phase,
            payload=payload,
        )


orchestrator = Orchestrator()

__all__ = [
    "Orchestrator",
    "orchestrator",
    "PIPELINE",
    "REVIEW_AGENTS",
    "MEMORY_RECALL_AGENTS",
    "TurnBudgetExceeded",
    "PipelineState",
]
