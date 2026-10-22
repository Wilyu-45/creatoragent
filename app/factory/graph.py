"""影视制作工厂 —— 工作流图（节点 + 连线）数据模型与校验。

图是用户在前端画布上的编辑结果，与 ComfyUI 的工作流 JSON 同构：
``nodes``（岗位实例 + 坐标 + 参数）与 ``edges``（端口到端口的数据流）。
执行前必须先过 ``validate_graph``：未知岗位、端口类型不匹配、必需输入缺失、
成环——都作为可定位的问题返回，让画布能标红而不是运行到一半才失败。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .spec import NodeError, get_spec, ports_compatible

NodeStatus = Literal["idle", "queued", "running", "ok", "error", "skipped", "stale"]


class GraphNode(BaseModel):
    """画布上的一个岗位实例。"""

    model_config = ConfigDict(extra="allow")

    id: str = ""
    type: str = ""
    #: 用户自定义标题（留空 = 用岗位默认名）
    title: str = ""
    pos: list[float] = Field(default_factory=lambda: [0.0, 0.0])
    size: list[float] = Field(default_factory=lambda: [248.0, 0.0])
    params: dict[str, Any] = Field(default_factory=dict)
    #: 折叠只显示标题（画布排线用）
    collapsed: bool = False
    #: 静音：连线保留但不执行，下游拿到上一次的缓存值
    muted: bool = False

    def label(self) -> str:
        if self.title:
            return self.title
        spec = get_spec(self.type)
        return spec.title if spec else self.type


class GraphEdge(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str = ""
    src: str = ""
    src_port: str = ""
    dst: str = ""
    dst_port: str = ""


class Graph(BaseModel):
    model_config = ConfigDict(extra="allow")

    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)

    def node(self, node_id: str) -> GraphNode | None:
        return next((item for item in self.nodes if item.id == node_id), None)

    def incoming(self, node_id: str) -> list[GraphEdge]:
        return [edge for edge in self.edges if edge.dst == node_id]

    def outgoing(self, node_id: str) -> list[GraphEdge]:
        return [edge for edge in self.edges if edge.src == node_id]

    def dependents(self, node_id: str) -> list[str]:
        return sorted({edge.dst for edge in self.outgoing(node_id)})

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [node.model_dump() for node in self.nodes],
            "edges": [edge.model_dump() for edge in self.edges],
        }

    def terminals(self) -> list[str]:
        """终端岗位：没有任何出线的岗位（交付面看的就是这些岗位的产出）。"""
        sources = {edge.src for edge in self.edges}
        return [node.id for node in self.nodes if node.id not in sources]

    def is_terminal(self, node_id: str) -> bool:
        return not any(edge.src == node_id for edge in self.edges)


class GraphIssue(BaseModel):
    model_config = ConfigDict(extra="allow")

    level: Literal["error", "warn"] = "error"
    node_id: str = ""
    edge_id: str = ""
    port: str = ""
    message: str = ""


def merged_params(node: GraphNode) -> dict[str, Any]:
    """节点参数 = 规格默认值 + 用户覆盖。"""
    spec = get_spec(node.type)
    base = spec.param_defaults() if spec else {}
    base.update(node.params or {})
    return base


def with_param_overrides(graph: Graph, overrides: dict[str, dict[str, Any]]) -> Graph:
    """返回叠加了本轮参数补丁的深拷贝；保存的图不受影响。"""
    if not overrides:
        return graph
    clone = graph.model_copy(deep=True)
    for node in clone.nodes:
        patch = overrides.get(node.id)
        if patch:
            node.params = {**(node.params or {}), **patch}
    return clone


def id_issues(graph: Graph) -> list[GraphIssue]:
    """结构身份体检：id 为空或重复会让缓存与连线错位，必须比环路更早报出来。"""
    issues: list[GraphIssue] = []
    seen: dict[str, int] = {}
    for index, node in enumerate(graph.nodes):
        node_id = str(node.id or "")
        if not node_id:
            issues.append(
                GraphIssue(node_id=node_id, message=f"第 {index + 1} 个岗位缺少唯一标识（id 为空），连线与缓存都无法定位")
            )
            continue
        if node_id in seen:
            issues.append(
                GraphIssue(
                    node_id=node_id,
                    message=f"岗位 id 重复：{node_id}（第 {seen[node_id] + 1} 与第 {index + 1} 个）——交付物会互相覆盖，请给其中一个换 id",
                )
            )
        else:
            seen[node_id] = index
    return issues


def _stable_order(ids: list[str], indeg: dict[str, int], adj: dict[str, list[str]], positions: dict[str, tuple[float, float]]) -> list[str]:
    """Kahn 主体：就绪队列按坐标（列 → 行）稳定排序。"""
    degree = dict(indeg)
    ready = sorted([node_id for node_id in ids if degree[node_id] == 0], key=lambda node_id: positions[node_id])
    order: list[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        unlocked: list[str] = []
        for nxt in adj.get(current, []):
            degree[nxt] -= 1
            if degree[nxt] == 0:
                unlocked.append(nxt)
        if unlocked:
            ready = sorted(ready + unlocked, key=lambda node_id: positions[node_id])
    return order


def topo_order(graph: Graph) -> list[str]:
    """Kahn 拓扑排序；id 冲突与成环都抛可定位的错误（而不是误报成环路）。"""
    if not graph.nodes:
        return []
    conflicts = id_issues(graph)
    if conflicts:
        stuck = sorted({str(issue.node_id) for issue in conflicts if issue.node_id})
        raise NodeError(f"工作流岗位 id 冲突，无法执行（涉及节点：{', '.join(stuck) or '存在空 id'}）")
    indeg: dict[str, int] = {node.id: 0 for node in graph.nodes}
    adj: dict[str, list[str]] = {node.id: [] for node in graph.nodes}
    for edge in graph.edges:
        if edge.src in indeg and edge.dst in indeg:
            indeg[edge.dst] += 1
            adj[edge.src].append(edge.dst)
    #: 稳定顺序：先按坐标（列 → 行），让自动布局的图按画面顺序执行
    positions = {node.id: (node.pos[0] if node.pos else 0.0, node.pos[1] if node.pos else 0.0) for node in graph.nodes}
    order = _stable_order([node.id for node in graph.nodes], indeg, adj, positions)
    if len(order) != len(graph.nodes):
        stuck = sorted(set(indeg) - set(order))
        raise NodeError(f"工作流存在环路，无法执行（涉及节点：{', '.join(stuck)}）")
    return order


def subgraph_for(graph: Graph, target_ids: list[str]) -> list[str]:
    """从目标节点反向闭包：跑单个节点时只需要它自己 + 全部上游。"""
    parents: dict[str, list[str]] = {node.id: [] for node in graph.nodes}
    for edge in graph.edges:
        if edge.dst in parents and edge.src in parents:
            parents[edge.dst].append(edge.src)
    seen: set[str] = set()
    stack = [node_id for node_id in target_ids if node_id in parents]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(parents[current])
    order = [node_id for node_id in topo_order(graph) if node_id in seen]
    return order


def validate_graph(graph: Graph) -> list[GraphIssue]:
    """执行前的静态体检：返回逐条可定位的问题。"""
    issues: list[GraphIssue] = id_issues(graph)
    known: dict[str, str] = {}
    present: set[str] = set()
    labels: dict[str, str] = {}
    for node in graph.nodes:
        present.add(node.id)
        labels[node.id] = f"{node.label()}（{node.id}）"
        spec = get_spec(node.type)
        if spec is None:
            issues.append(GraphIssue(node_id=node.id, message=f"未知岗位类型：{node.type}"))
            continue
        known[node.id] = node.type

    #: 同一条数据流只该有一个箭头：重复连线会让多入端口收到两份一样的交付物
    flows: dict[tuple[str, str, str, str], int] = {}
    edge_ids: dict[str, int] = {}
    for index, edge in enumerate(graph.edges):
        if edge.id:
            if edge.id in edge_ids:
                issues.append(
                    GraphIssue(level="warn", edge_id=edge.id, message=f"连线 id 重复：{edge.id}（第 {edge_ids[edge.id] + 1} 与第 {index + 1} 条）——删除时可能一次去掉两条")
                )
            else:
                edge_ids[edge.id] = index
        missing = [role for role in ("src", "dst") if getattr(edge, role) not in present]
        if missing:
            # 画布删岗位后残留的悬空线：端点根本没这个岗位，不能说成「岗位类型未知」
            dangling = "、".join(f"{('起点 ' if role == 'src' else '终点 ')}{getattr(edge, role) or '（空）'}" for role in missing)
            issues.append(GraphIssue(edge_id=edge.id, node_id=edge.dst if edge.dst in present else "", message=f"连线 {edge.id or f'第 {index + 1} 条'} 的{dangling}在画布上已不存在"))
            continue
        if edge.src == edge.dst:
            issues.append(
                GraphIssue(edge_id=edge.id, node_id=edge.src, port=edge.dst_port, message=f"{labels.get(edge.src, edge.src)} 连回了自己：自己不能是自己的上游")
            )
            continue
        key = (edge.src, edge.src_port, edge.dst, edge.dst_port)
        if key in flows:
            issues.append(
                GraphIssue(
                    level="warn",
                    edge_id=edge.id,
                    node_id=edge.dst,
                    port=edge.dst_port,
                    message=f"重复连线：{labels.get(edge.src, edge.src)}·{edge.src_port} → {labels.get(edge.dst, edge.dst)}·{edge.dst_port} 已经连过一次",
                )
            )
        else:
            flows[key] = index
        src_spec, dst_spec = get_spec(known.get(edge.src, "")), get_spec(known.get(edge.dst, ""))
        if not src_spec or not dst_spec:
            continue
        out_port = src_spec.output(edge.src_port)
        in_port = dst_spec.input(edge.dst_port)
        if out_port is None:
            issues.append(
                GraphIssue(edge_id=edge.id, node_id=edge.src, port=edge.src_port, message=f"{src_spec.title} 没有输出端口 {edge.src_port}")
            )
            continue
        if in_port is None:
            issues.append(
                GraphIssue(edge_id=edge.id, node_id=edge.dst, port=edge.dst_port, message=f"{dst_spec.title} 没有输入端口 {edge.dst_port}")
            )
            continue
        if not ports_compatible(out_port.type, in_port.type):
            issues.append(
                GraphIssue(
                    level="warn",
                    edge_id=edge.id,
                    node_id=edge.dst,
                    port=edge.dst_port,
                    message=f"{src_spec.title}·{out_port.label}（{out_port.type}）接到 {dst_spec.title}·{in_port.label}（{in_port.type}），类型不匹配",
                )
            )
        if not in_port.many:
            duplicates = [item for item in graph.edges if item.dst == edge.dst and item.dst_port == edge.dst_port]
            if len(duplicates) > 1 and duplicates[0].id == edge.id:
                issues.append(
                    GraphIssue(
                        level="warn",
                        edge_id=edge.id,
                        node_id=edge.dst,
                        port=edge.dst_port,
                        message=f"{dst_spec.title} 的 {in_port.label} 只接受一条连线，多余的会被忽略",
                    )
                )

    for node in graph.nodes:
        spec = get_spec(node.type)
        if spec is None:
            continue
        if not spec.inputs and not spec.source:
            issues.append(GraphIssue(level="warn", node_id=node.id, message=f"{spec.title} 没有输入也没有独立取值能力"))
        for port in spec.inputs:
            if not port.required:
                continue
            linked = any(edge.dst == node.id and edge.dst_port == port.name for edge in graph.edges)
            has_inline = port.name in node.params or f"{port.name}_inline" in node.params
            if not linked and not has_inline and not spec.source:
                issues.append(
                    GraphIssue(level="warn", node_id=node.id, port=port.name, message=f"{spec.title} 的必需交付物「{port.label}」未接线")
                )

    try:
        topo_order(graph)
    except Exception as error:  # noqa: BLE001 - 环路作为问题返回而不是抛出
        issues.append(GraphIssue(level="error", message=str(error)))
    return issues
