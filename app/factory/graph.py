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


def topo_order(graph: Graph) -> list[str]:
    """Kahn 拓扑排序；成环时给出可定位的错误。"""
    indeg: dict[str, int] = {node.id: 0 for node in graph.nodes}
    adj: dict[str, list[str]] = {node.id: [] for node in graph.nodes}
    for edge in graph.edges:
        if edge.src in indeg and edge.dst in indeg:
            indeg[edge.dst] += 1
            adj[edge.src].append(edge.dst)
    #: 稳定顺序：先按坐标（列 → 行），让自动布局的图按画面顺序执行
    positions = {node.id: (node.pos[0] if node.pos else 0.0, node.pos[1] if node.pos else 0.0) for node in graph.nodes}
    ready = sorted(
        [node_id for node_id, degree in indeg.items() if degree == 0],
        key=lambda node_id: (positions[node_id][0], positions[node_id][1]),
    )
    order: list[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        unlocked: list[str] = []
        for nxt in adj[current]:
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                unlocked.append(nxt)
        if unlocked:
            ready = sorted(
                ready + unlocked,
                key=lambda node_id: (positions[node_id][0], positions[node_id][1]),
            )
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
    issues: list[GraphIssue] = []
    known: dict[str, str] = {}
    for node in graph.nodes:
        spec = get_spec(node.type)
        if spec is None:
            issues.append(GraphIssue(node_id=node.id, message=f"未知岗位类型：{node.type}"))
            continue
        known[node.id] = node.type

    for edge in graph.edges:
        src_spec, dst_spec = get_spec(known.get(edge.src, "")), get_spec(known.get(edge.dst, ""))
        if not src_spec or not dst_spec:
            issues.append(GraphIssue(edge_id=edge.id, message="连线端点指向未知岗位"))
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
