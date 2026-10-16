"""影视制作工厂 —— 图执行引擎。

职责：按拓扑顺序驱动岗位节点，做**增量缓存**（参数与上游指纹没变就不重跑），
把每个岗位的产物写进项目工作区，并如实记录状态、日志与耗时。

缓存口径：节点指纹 = 岗位类型 + 参数 + 每条入线的（上游节点指纹, 端口）。
所以改了分镜表的一个参数，只有依赖它的下游会被判定为过期，
上游成果原样复用——这是 ComfyUI 用户真正需要的行为。
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from ..config import DATA_DIR
from ..logger import create_logger
from . import graph as graph_model
from .spec import NodeError, get_spec

log = create_logger("factory.engine")

MAX_LOG_ROWS = 40
_SAFE_NAME = re.compile(r"[^0-9A-Za-z._-]+")

NodeEvent = dict[str, Any]


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _digest(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:  # noqa: BLE001 - 不可序列化对象退化为字符串表示
        text = repr(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def safe_name(value: str, fallback: str = "artifact") -> str:
    """把岗位给的任意文件名压成安全名（防目录穿越，保留扩展名）。"""
    base = _SAFE_NAME.sub("_", (value or "").replace("/", "_").split("?")[0]).strip("_") or fallback
    if len(base) > 120:
        root, _, ext = base.rpartition(".")
        base = f"{root[:100]}.{ext}" if ext else base[:120]
    return base


@dataclass
class Workspace:
    """一个项目的落盘位置：工作流、缓存、产物目录。"""

    project_id: str
    root: Path
    url_prefix: str = ""

    def __post_init__(self) -> None:
        (self.artifacts_dir / "images").mkdir(parents=True, exist_ok=True)
        (self.artifacts_dir / "audio").mkdir(parents=True, exist_ok=True)
        (self.artifacts_dir / "files").mkdir(parents=True, exist_ok=True)

    @property
    def artifacts_dir(self) -> Path:
        return self.root / "artifacts"

    @property
    def cache_file(self) -> Path:
        return self.root / "cache.json"

    def url(self, rel: str) -> str:
        return f"{self.url_prefix}/{rel}" if self.url_prefix else rel

    def write(self, sub: str, name: str, blob: bytes) -> tuple[str, str]:
        """写入 ``artifacts/<sub>/<name>``，返回（相对路径，可访问 URL）。"""
        inside = (self.root / "artifacts").resolve()
        target_dir = (self.artifacts_dir / sub).resolve()
        if not str(target_dir).startswith(str(inside)):
            raise NodeError(f"非法产物目录：{sub}")
        path = target_dir / safe_name(name)
        path.write_bytes(blob)
        rel = f"{sub}/{path.name}"
        return rel, self.url(rel)

    def load_cache(self) -> dict[str, Any]:
        if not self.cache_file.is_file():
            return {}
        try:
            raw = json.loads(self.cache_file.read_text(encoding="utf-8"))
        except Exception as error:  # noqa: BLE001 - 缓存坏了不能影响重跑
            log.warn(f"忽略损坏的节点缓存：{error}")
            return {}
        return raw if isinstance(raw, dict) else {}

    def save_cache(self, payload: dict[str, Any]) -> None:
        tmp = self.cache_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(self.cache_file)


def open_workspace(project_id: str, url_prefix: str = "") -> Workspace:
    clean_id = safe_name(project_id, "project")
    return Workspace(project_id=clean_id, root=DATA_DIR / "factory" / clean_id, url_prefix=url_prefix)


@dataclass
class NodeContext:
    """岗位节点看到的唯一世界：参数、上游交付物、落盘与日志。"""

    node_id: str
    node_type: str
    title: str
    params: dict[str, Any]
    inputs: dict[str, Any]
    multi: dict[str, list[Any]]
    workspace: Workspace
    on_progress: Callable[[str, dict[str, Any]], None] | None = None

    def in_(self, port: str) -> Any:
        return self.inputs.get(port)

    def many(self, port: str) -> list[Any]:
        return self.multi.get(port) or []

    def require(self, port: str) -> Any:
        value = self.inputs.get(port)
        if value in (None, "", [], {}):
            raise NodeError(f"{self.title}：必需交付物「{port}」没有值")
        return value

    def log(self, message: str, level: str = "info", **payload: Any) -> None:
        if self.on_progress:
            self.on_progress(message, {"level": level, **payload})

    def progress(self, ratio: float, message: str = "") -> None:
        """长任务（批量生成、渲染）的进度回报，画布据此显示进度条。"""
        if self.on_progress:
            self.on_progress(message or f"{ratio:.0%}", {"level": "progress", "ratio": max(0.0, min(1.0, float(ratio)))})

    @property
    def project_id(self) -> str:
        return self.workspace.project_id

    def save_file(self, name: str, blob: bytes) -> dict[str, Any]:
        rel, url = self.workspace.write("files", name, blob)
        return {"ref": rel, "url": url, "bytes": len(blob)}

    def save_wav(self, name: str, blob: bytes) -> dict[str, Any]:
        stem = safe_name(name, "track.wav").rpartition(".")[0] or "track"
        rel, url = self.workspace.write("audio", f"{stem}.wav", blob)
        return {"ref": rel, "url": url, "bytes": len(blob)}

    def save_png(self, name: str, blob: bytes) -> dict[str, Any]:
        rel, url = self.workspace.write("images", name, blob)
        size = _png_size(blob)
        return {"ref": rel, "url": url, "w": size[0], "h": size[1], "bytes": len(blob)}

    def read_artifact(self, rel: str) -> bytes:
        parts = [safe_name(part) for part in str(rel or "").replace("\\", "/").split("/") if part not in ("", ".")]
        if not parts:
            raise NodeError("产物路径为空")
        root = self.workspace.artifacts_dir.resolve()
        path = root.joinpath(*parts).resolve()
        if not str(path).startswith(str(root)):
            raise NodeError(f"非法产物路径：{rel}")
        if not path.is_file():
            raise NodeError(f"产物不存在：{rel}")
        return path.read_bytes()


def _png_size(blob: bytes) -> tuple[int, int]:
    if len(blob) > 24 and blob[:8] == b"\x89PNG\r\n\x1a\n":
        return int.from_bytes(blob[16:20], "big"), int.from_bytes(blob[20:24], "big")
    return 0, 0


def _collect_inputs(
    node: graph_model.GraphNode,
    graph: graph_model.Graph,
    outputs: dict[str, dict[str, Any]],
    order_index: dict[str, int],
) -> tuple[dict[str, Any], dict[str, list[Any]]]:
    spec = get_spec(node.type)
    single: dict[str, Any] = {}
    multi: dict[str, list[Any]] = {}
    for port in spec.inputs if spec else []:
        edges = [edge for edge in graph.edges if edge.dst == node.id and edge.dst_port == port.name and edge.src in outputs]
        edges.sort(key=lambda edge: order_index.get(edge.src, 0))
        values: list[Any] = []
        for edge in edges:
            payload = outputs[edge.src].get(edge.src_port)
            if payload is None or payload == "" or payload == [] or payload == {}:
                continue
            values.append(payload)
        if port.many:
            multi[port.name] = values
        else:
            single[port.name] = values[-1] if values else None
    return single, multi


def _signature(
    node: graph_model.GraphNode,
    graph: graph_model.Graph,
    node_signatures: dict[str, str],
    order_index: dict[str, int],
) -> str:
    spec = get_spec(node.type)
    edges = sorted(
        ((order_index.get(edge.src, 0), edge.src, edge.src_port, edge.dst_port) for edge in graph.edges if edge.dst == node.id),
    )
    return _digest(
        {
            "type": node.type,
            "muted": bool(node.muted),
            "params": graph_model.merged_params(node),
            "ports": [port.name for port in (spec.inputs if spec else [])],
            "upstream": [(node_signatures.get(src, "?"), src_port, dst_port) for _, src, src_port, dst_port in edges],
        }
    )


@dataclass
class NodeRun:
    """一个岗位本轮的执行记录。"""

    node_id: str
    type: str
    title: str
    status: str = "idle"
    signature: str = ""
    summary: str = ""
    error: str = ""
    logs: list[dict[str, Any]] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""
    elapsed_ms: int = 0
    cached: bool = False
    progress: float = 1.0
    outputs: dict[str, Any] = field(default_factory=dict)
    output_types: dict[str, str] = field(default_factory=dict)


def _output_brief(spec: Any, outputs: dict[str, Any]) -> str:
    rows: list[str] = []
    for port in spec.outputs if spec else []:
        value = outputs.get(port.name)
        if isinstance(value, dict):
            for key in ("title", "summary", "total_s", "duration_s", "count", "score", "verdict"):
                if value.get(key) not in (None, ""):
                    rows.append(f"{port.label}·{key}={value[key]}")
                    break
            items = value.get("items") or value.get("shots") or value.get("scenes") or value.get("cues")
            if isinstance(items, list):
                rows.append(f"{port.label}×{len(items)}")
        elif isinstance(value, list):
            rows.append(f"{port.label}×{len(value)}")
        elif isinstance(value, str):
            rows.append(f"{port.label}:{value[:28]}")
        elif value is not None:
            rows.append(f"{port.label}={value}")
    return " ｜ ".join(rows[:6])


def run_graph(
    graph: graph_model.Graph,
    workspace: Workspace,
    *,
    targets: Iterable[str] | None = None,
    force: bool = False,
    on_event: Callable[[NodeEvent], None] | None = None,
) -> dict[str, Any]:
    """执行整图（或目标节点的反向闭包），返回本轮运行结果。"""
    target_list = [node_id for node_id in (targets or []) if graph.node(node_id)]
    plan = graph_model.subgraph_for(graph, target_list) if target_list else graph_model.topo_order(graph)
    order_index = {node_id: index for index, node_id in enumerate(plan)}
    emit = on_event or (lambda event: None)

    cache_raw = workspace.load_cache()
    cache: dict[str, Any] = dict(cache_raw)
    outputs: dict[str, dict[str, Any]] = {}
    node_signatures: dict[str, str] = {}
    runs: dict[str, NodeRun] = {}
    started = time.time()
    emit({"type": "run.start", "plan": plan, "targets": target_list, "ts": _now()})

    for node_id in plan:
        node = graph.node(node_id)
        if node is None:
            continue
        spec = get_spec(node.type)
        run = NodeRun(node_id=node_id, type=node.type, title=node.label())
        runs[node_id] = run
        if spec is None:
            run.status, run.error = "error", f"未知岗位类型：{node.type}"
            emit({"type": "node.error", "node_id": node_id, "error": run.error})
            continue

        run.signature = _signature(node, graph, node_signatures, order_index)
        node_signatures[node_id] = run.signature
        inputs, multi = _collect_inputs(node, graph, outputs, order_index)

        if node.muted:
            run.status, run.summary, run.progress = "skipped", "已静音，跳过执行", 0.0
            previous = cache.get(node_id) or {}
            run.outputs = previous.get("outputs") or {}
            outputs[node_id] = run.outputs
            emit({"type": "node.skipped", "node_id": node_id, "reason": "muted"})
            continue

        # 上游失败 → 明确标阻塞，而不是让节点拿到 None 后崩在细节里
        blocked = [
            edge.src
            for edge in graph.edges
            if edge.dst == node_id
            and edge.src in runs
            and runs[edge.src].status in {"error", "blocked"}
        ]
        if blocked:
            run.status, run.error, run.progress = "blocked", f"上游未产出：{'、'.join(sorted(set(blocked)))}", 0.0
            emit({"type": "node.blocked", "node_id": node_id, "upstream": sorted(set(blocked))})
            continue

        missing = [
            port.label
            for port in spec.inputs
            if port.required and not port.many and inputs.get(port.name) is None and port.name not in (node.params or {})
        ]
        if missing:
            run.status, run.error = "error", f"缺少必需交付物：{'、'.join(missing)}"
            emit({"type": "node.error", "node_id": node_id, "error": run.error})
            continue

        previous = cache.get(node_id) or {}
        if not force and previous.get("signature") == run.signature and previous.get("status") == "ok":
            run.status, run.cached, run.outputs = "ok", True, previous.get("outputs") or {}
            run.summary = previous.get("summary") or "复用缓存"
            outputs[node_id] = run.outputs
            emit({"type": "node.cached", "node_id": node_id, "title": run.title})
            continue

        def make_progress(current_id: str, current: NodeRun) -> Callable[[str, dict[str, Any]], None]:
            def handler(message: str, payload: dict[str, Any]) -> None:
                if payload.get("level") == "progress":
                    current.progress = float(payload.get("ratio") or 0.0)
                    emit({"type": "node.progress", "node_id": current_id, "ratio": current.progress, "message": message})
                    return
                row = {"ts": _now(), "level": str(payload.get("level") or "info"), "message": message}
                current.logs.append(row)
                current.logs = current.logs[-MAX_LOG_ROWS:]
                emit({"type": "node.log", "node_id": current_id, "level": row["level"], "message": message, "payload": payload})

            return handler

        ctx = NodeContext(
            node_id=node_id,
            node_type=node.type,
            title=run.title,
            params=graph_model.merged_params(node),
            inputs=inputs,
            multi=multi,
            workspace=workspace,
            on_progress=make_progress(node_id, run),
        )
        run.status, run.started_at, run.progress = "running", _now(), 0.0
        emit({"type": "node.start", "node_id": node_id, "title": run.title, "type": node.type})
        began = time.time()
        try:
            produced = spec.run(ctx)
            if not isinstance(produced, dict):
                raise NodeError(f"{run.title} 返回了 {type(produced).__name__}，岗位执行器必须返回端口字典")
            unexpected = [key for key in produced if spec.output(key) is None]
            if unexpected:
                log.warn(f"节点 {run.title} 输出了未声明端口（被丢弃）：{unexpected}")
            run.outputs = {port.name: produced.get(port.name) for port in spec.outputs if produced.get(port.name) is not None}
            run.output_types = {port.name: port.type for port in spec.outputs}
            run.status, run.error = "ok", ""
        except NodeError as error:
            run.status, run.error = "error", str(error)
        except Exception as error:  # noqa: BLE001 - 岗位内部异常要如实落到节点上
            run.status, run.error = "error", f"{type(error).__name__}: {error}"
            log.error(f"岗位 {run.title} 执行异常：{error}")
        finally:
            run.finished_at = _now()
            run.elapsed_ms = int((time.time() - began) * 1000)
            run.progress = 1.0 if run.status == "ok" else run.progress

        outputs[node_id] = run.outputs if run.status == "ok" else {}
        run.summary = _output_brief(spec, run.outputs) if run.status == "ok" else run.error
        if run.status == "ok":
            cache[node_id] = {
                "signature": run.signature,
                "status": "ok",
                "summary": run.summary,
                "outputs": run.outputs,
                "finished_at": run.finished_at,
            }
            emit({"type": "node.finish", "node_id": node_id, "title": run.title, "summary": run.summary, "elapsed_ms": run.elapsed_ms})
        else:
            cache.pop(node_id, None)
            emit({"type": "node.error", "node_id": node_id, "title": run.title, "error": run.error})

    workspace.save_cache(cache)
    stale = [node_id for node_id in cache if node_id not in runs]
    payload = {
        "project": workspace.project_id,
        "started_at": _now(),
        "elapsed_ms": int((time.time() - started) * 1000),
        "ok": all(run.status in {"ok", "skipped"} for run in runs.values()) if runs else False,
        "counts": {
            "ok": sum(1 for run in runs.values() if run.status == "ok" and not run.cached),
            "cached": sum(1 for run in runs.values() if run.cached),
            "error": sum(1 for run in runs.values() if run.status == "error"),
            "blocked": sum(1 for run in runs.values() if run.status == "blocked"),
            "skipped": sum(1 for run in runs.values() if run.status == "skipped"),
        },
        "runs": {node_id: _run_to_dict(run) for node_id, run in runs.items()},
        "stale_nodes": stale,
        "signatures": node_signatures,
    }
    emit({"type": "run.finish", "ok": payload["ok"], "counts": payload["counts"], "elapsed_ms": payload["elapsed_ms"]})
    return payload


def _run_to_dict(run: NodeRun) -> dict[str, Any]:
    return {
        "node_id": run.node_id,
        "type": run.type,
        "title": run.title,
        "status": run.status,
        "signature": run.signature,
        "summary": run.summary,
        "error": run.error,
        "logs": run.logs,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "elapsed_ms": run.elapsed_ms,
        "cached": run.cached,
        "progress": run.progress,
        "output_types": run.output_types,
        "outputs": {key: _shape(value) for key, value in run.outputs.items()},
    }


def _shape(value: Any) -> dict[str, Any]:
    """给前端的输出摘要：类型 + 规模 + 预览，不整包传大对象。"""
    if isinstance(value, dict):
        return {
            "kind": "object",
            "keys": sorted(value.keys())[:16],
            "count": len(value.get("items") or value.get("shots") or value.get("scenes") or value.get("cues") or []) or None,
            "preview": {key: item for key, item in list(value.items())[:8] if not isinstance(item, (list, dict))},
        }
    if isinstance(value, list):
        return {"kind": "array", "count": len(value), "preview": [_shape(item) for item in value[:2]]}
    return {"kind": "scalar", "preview": str(value)[:120]}


