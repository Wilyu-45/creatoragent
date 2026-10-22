"""影视制作工厂 —— 交付验收（打包前那道门）。

回答一个问题：**这个项目现在能不能交**。它不执行岗位、不改缓存、不写盘，
只把已经在画布与磁盘上的事实量成一份可核对的报告：

* **终端岗位有没有真交付**：没有出线的岗位就是这张图的出口。出口没跑过、
  或者画布改过之后缓存已经过期，都算阻断 —— 那种状态下导出的交付包是半成品。
* **交付物引用的文件还在不在**：岗位产物里全是 ``ref``（``images/x.png``、
  ``audio/y.wav``）。缓存说成功了、磁盘上文件却没了（换机/手工删过/同机不同卷），
  播放器与交付包都会 404，这是阻断而不是告警。
* **时间轴是不是空的**：``kind == "timeline"`` 的交付物必须有镜头、时长也要大于 0。
* **哪些是占位产物**：带 ``simulated=True`` 的岗位如实列出来 —— 警告，不阻断，
  但要让人知道这版成片是示意而非真实模型输出。

原则与其余旁路通道一致：**不假装成功**。验收只报事实，绝不为了让交付包能下载
而把阻断降级成提醒；``acceptance.allow_warnings`` 之类的开关不存在，
警告本来就不拦，阻断本来就不放。
"""

from __future__ import annotations

import time
from typing import Any, Callable

from . import engine
from . import graph as graph_model

#: 报告里最多列多少个岗位明细（再多只汇总，不把响应变成第二份产物清单）
MAX_DETAIL = 24

LEVEL_ORDER = {"error": 0, "warn": 1, "info": 2}


def _now() -> str:
    """与引擎同一时间口径（本地秒级 ISO，报告只用于阅读与排序）。"""
    return time.strftime("%Y-%m-%d %H:%M:%S")


class AcceptanceBlocked(ValueError):
    """验收未过：交付包不放行。继承 ValueError，接口层因此仍按 400/409 口径转译。"""


def _walk(value: Any, visit: Callable[[dict[str, Any]], None]) -> None:
    """递归走遍交付物字典，把每一层 dict 都交给 visit（找 ref / simulated 用）。"""
    if isinstance(value, dict):
        visit(value)
        for item in value.values():
            _walk(item, visit)
    elif isinstance(value, list):
        for item in value:
            _walk(item, visit)


def _collect(value: Any) -> tuple[list[str], bool]:
    """一个岗位产物里引用到的相对文件路径 + 是否带模拟标记。"""
    refs: list[str] = []
    simulated = False

    def visit(row: dict[str, Any]) -> None:
        nonlocal simulated
        ref = row.get("ref")
        if isinstance(ref, str) and ref.strip():
            refs.append(ref.strip())
        if row.get("simulated") is True:
            simulated = True

    _walk(value, visit)
    return refs, simulated


def select_film(graph: graph_model.Graph, cache: dict[str, Any]) -> tuple[dict[str, Any] | None, Any]:
    """选拓扑序最靠后的那条成片交付物（``kind == "timeline"``、镜数>0、时长>0）。

    这是「成片」的唯一口径：验收报告、交付包里的 ``timeline.json``、放映室的
    回看都读它，不再各自一套启发式选轴。一张图里可能有分镜表/中间时间轴，
    交付面要的是剪辑后的那条。返回 ``(元数据, 原值)``；没有成片轴时为 ``(None, None)``。
    """
    try:
        rank = {node_id: index for index, node_id in enumerate(graph_model.topo_order(graph))}
    except Exception:  # noqa: BLE001 - 坏图拿不到拓扑序，交给体检报错阻断，不据此选片
        rank = {}
    best: dict[str, Any] | None = None
    value: Any = None
    for node_id, row in cache.items():
        if not isinstance(row, dict) or row.get("status") != "ok":
            continue
        node = graph.node(node_id)
        title = node.label() if node else node_id
        for port_name, port_value in (row.get("outputs") or {}).items():
            if not (isinstance(port_value, dict) and port_value.get("kind") == "timeline"):
                continue
            count = int(port_value.get("count") or len(port_value.get("shots") or []))
            duration = float(port_value.get("duration_s") or 0.0)
            if count <= 0 or duration <= 0:
                continue
            if best is None or rank.get(node_id, -1) >= rank.get(str(best["node_id"]), -1):
                best = {
                    "node_id": node_id,
                    "port": port_name,
                    "title": title,
                    "shots": count,
                    "duration_s": duration,
                    "refs": _collect(port_value)[0],
                }
                value = port_value
    return best, value


def inspect(record: dict[str, Any], workspace: engine.Workspace) -> dict[str, Any]:
    """量出这个项目的交付状态：``ok`` 只由阻断问题决定，警告不改判。"""
    graph = graph_model.Graph.model_validate(record.get("graph") or {"nodes": [], "edges": []})
    cache = workspace.load_cache()
    issues: list[dict[str, Any]] = []

    def add(level: str, code: str, message: str, node_id: str = "") -> None:
        issues.append({"level": level, "code": code, "message": message, "node_id": node_id})

    # 1) 结构与体检：error 一律阻断，warn 如实提醒（未接的可选交付物、类型不匹配…）
    for issue in graph_model.validate_graph(graph):
        add(issue.level, "graph", issue.message, issue.node_id)
    if not graph.nodes:
        add("error", "empty", "工作流是空的：先从左侧面板拖岗位进画布再谈交付")

    terminals = graph.terminals()
    muted = [node.id for node in graph.nodes if node.muted]
    ran_nodes = sorted(node_id for node_id, row in cache.items() if isinstance(row, dict) and row.get("status") == "ok")

    # 2) 缓存过期判定复用引擎的签名口径：验收与实跑不同口径，报告就成了误导
    states: dict[str, dict[str, Any]] = {}
    if graph.nodes and not any(issue["code"] == "graph" and issue["level"] == "error" for issue in issues):
        try:
            preview = engine.preview_graph(graph, workspace)
            states = {str(row["node_id"]): row for row in preview["nodes"]}
        except Exception:  # noqa: BLE001 - 预检算不出来就不据此下结论，交给引用与缓存检查兜底
            states = {}
    stale = [
        node_id
        for node_id, row in states.items()
        if row.get("state") in ("run", "error") and node_id in cache
    ]
    #: 本轮需要跑的岗位（包含从没跑过的）；与「跑过但过期」分开报，语义不混
    needs_run = [node_id for node_id, row in states.items() if row.get("state") in ("run", "error")]

    # 3) 终端岗位：这张图的出口必须有可复用的成功交付
    for node_id in terminals:
        node = graph.node(node_id)
        if node is None:
            continue
        title = node.label()
        if node.muted:
            add("warn", "terminal_muted", f"终端岗位「{title}」被静音：这一版交付里没有它的产出", node_id)
            continue
        row = cache.get(node_id)
        if not isinstance(row, dict) or row.get("status") != "ok":
            add("error", "terminal_not_delivered", f"终端岗位「{title}」还没有成功交付：跑一次图再验收", node_id)
        elif node_id in stale:
            add(
                "error",
                "terminal_stale",
                f"终端岗位「{title}」的缓存已过期：{states[node_id].get('reason') or '参数或上游已变更'}，需要重跑",
                node_id,
            )

    # 4) 产物引用：逐岗位走遍交付物，文件不在盘上或 0 字节都点名
    simulated_nodes: list[str] = []
    checked_refs = 0
    missing_refs: list[str] = []
    empty_refs: list[str] = []
    for node_id in ran_nodes:
        node = graph.node(node_id)
        if node is None:
            continue
        outputs = (cache.get(node_id) or {}).get("outputs") or {}
        refs, simulated = _collect(outputs)
        if simulated:
            simulated_nodes.append(node_id)
        title = node.label()
        for rel in refs:
            path = workspace.artifacts_dir / rel
            checked_refs += 1
            if not path.is_file():
                missing_refs.append(rel)
                add("error", "artifact_missing", f"{title} 交付物引用的文件不在项目里：{rel}", node_id)
            elif path.stat().st_size == 0:
                empty_refs.append(rel)
                add("error", "artifact_empty", f"{title} 交付物是空文件：{rel}", node_id)
        # 时间轴只认剪辑口径（kind == timeline）：分镜表/镜头单里也有 shots，但不该按成片判
        for port_name, value in outputs.items():
            if isinstance(value, dict) and value.get("kind") == "timeline":
                count = int(value.get("count") or len(value.get("shots") or []))
                duration = float(value.get("duration_s") or 0.0)
                if count <= 0:
                    add("error", "timeline_empty", f"{title} 的时间轴是空的（{port_name}）：没有任何镜头", node_id)
                elif duration <= 0:
                    add("error", "timeline_zero", f"{title} 的时间轴时长为 0（{count} 镜 / {duration}s）：播放器与交付包都是空片", node_id)

    # 5) 磁盘口径与图上口径必须对得上：图改小之后旧产物仍会混进交付包
    artifact_files = [path for path in sorted(workspace.artifacts_dir.rglob("*")) if path.is_file()] if workspace.artifacts_dir.is_dir() else []
    if artifact_files and not ran_nodes:
        add("warn", "artifact_orphan", f"项目里有 {len(artifact_files)} 个产物文件，但当前工作流没有成功交付的岗位（多半是改过图没重跑）")
    if not artifact_files and not issues:
        add("error", "no_artifact", "项目还没有任何产物：先跑一次图")

    for node_id in muted:
        node = graph.node(node_id)
        add("info", "muted", f"{(node.label() if node else node_id)}：已静音，本轮沿用上一次交付", node_id)
    if simulated_nodes:
        names = ", ".join(f"{(graph.node(item).label() if graph.node(item) else item)}（{item}）" for item in simulated_nodes[:MAX_DETAIL])
        add(
            "warn",
            "simulated",
            f"{len(simulated_nodes)} 个岗位产出带占位标记（simulated）：{names}"
            + (f" 等 {len(simulated_nodes)} 个" if len(simulated_nodes) > MAX_DETAIL else ""),
        )

    film, _film_value = select_film(graph, cache)
    if film:
        add(
            "info",
            "film_timeline",
            f"交付将内含成片时间轴：{film['title']} {film['shots']} 镜 / {film['duration_s']:.1f}s（timeline.json，随包附上它引用的素材）",
            str(film["node_id"]),
        )

    issues.sort(key=lambda row: LEVEL_ORDER.get(str(row["level"]), 9))

    errors = [row for row in issues if row["level"] == "error"]
    warns = [row for row in issues if row["level"] == "warn"]
    blocked_terminals = {str(row["node_id"]) for row in errors if row["code"] in ("terminal_not_delivered", "terminal_stale")}
    ok = not errors
    return {
        "ok": ok,
        "verdict": "blocked" if errors else ("warn" if warns else "pass"),
        "project": str(record.get("id") or workspace.project_id),
        "title": str(record.get("title") or ""),
        "checked_at": _now(),
        "counts": {"error": len(errors), "warn": len(warns), "info": len(issues) - len(errors) - len(warns)},
        "issues": issues,
        "stats": {
            "nodes": len(graph.nodes),
            "edges": len(graph.edges),
            "delivered": len(ran_nodes),
            "terminals": len(terminals),
            "terminals_ready": len([node_id for node_id in terminals if node_id not in blocked_terminals and node_id not in muted]),
            "muted": len(muted),
            "stale": len(stale),
            "needs_run": len(needs_run),
            "artifact_files": len(artifact_files),
            "artifact_bytes": sum(path.stat().st_size for path in artifact_files),
            "refs_checked": checked_refs,
            "refs_missing": len(missing_refs),
            "refs_empty": len(empty_refs),
            "simulated_nodes": len(simulated_nodes),
        },
        "deliverables": [
            {
                "node_id": node_id,
                "title": (graph.node(node_id).label() if graph.node(node_id) else node_id),
                "type": str(graph.node(node_id).type if graph.node(node_id) else ""),
                "terminal": node_id in terminals,
                "delivered_at": str((cache.get(node_id) or {}).get("finished_at") or ""),
                "summary": str((cache.get(node_id) or {}).get("summary") or ""),
                "ports": sorted(((cache.get(node_id) or {}).get("outputs") or {}).keys()),
                "refs": _collect((cache.get(node_id) or {}).get("outputs") or {})[0][:20],
                "simulated": node_id in simulated_nodes,
            }
            for node_id in ran_nodes[:MAX_DETAIL]
        ],
        "film": film,
    }


def blockers(report: dict[str, Any], limit: int = 3) -> str:
    """把阻断项压成一句话，给交付包的 409 当 detail（接口里只能带一条消息）。"""
    rows = [row for row in (report.get("issues") or []) if row.get("level") == "error"]
    if not rows:
        return ""
    detail = "；".join(str(row.get("message") or "") for row in rows[:limit])
    more = f"（另有 {len(rows) - limit} 项）" if len(rows) > limit else ""
    return f"交付验收未通过：{len(rows)} 个阻断问题 —— {detail}{more}"


__all__ = ["AcceptanceBlocked", "blockers", "inspect", "select_film"]
