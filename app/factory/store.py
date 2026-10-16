"""影视制作工厂 —— 项目、工作流与运行作业存储。

三件事
------
1. **项目落盘**：画布上的图（节点 + 连线 + 参数）是用户的编辑成果，必须原子写盘，
   重启后能接着改。每个项目一个目录 ``data/factory/<project_id>/``，里面同时放
   ``project.json``（工作流）与 ``artifacts/``（岗位产物），删项目就是删这个目录。
2. **跑图是长任务**：全链路四十多个岗位跑一遍要几十秒，接了真机生成通道更久。所以运行
   走作业制：受理 → 后台线程推进 → 前端轮询。作业存储直接复用
   ``core/gen_jobs.make_backend``，与图片 / 视频 / 数字人同一契约（file 原子写盘、
   pg ``payload jsonb``），不另起一套。
3. **租户隔离**：项目与作业都带 ``tenant``；跨租户读取一律按「不存在」处理，
   不给探测他人项目的机会（与 ``api/routes._require_task`` 同口径）。

**不假装成功**：校验没过、岗位报错、产物读不回，全部原样落在作业与节点状态上，
接口不会把一个失败运行说成完成。
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ..config import DATA_DIR
from ..core.gen_jobs import make_backend
from ..logger import create_logger
from . import engine
from . import graph as graph_model
from . import nodes  # noqa: F401 - 导入即注册岗位，路由与体检都要查注册表
from .spec import DEPARTMENTS, all_specs

log = create_logger("factory.store")

#: 项目根目录（与 ``engine.open_workspace`` 同一位置，别各写一份）
FACTORY_DIR = DATA_DIR / "factory"
#: file 模式作业落盘位置
JOBS_FILE = DATA_DIR / "factory_jobs.json"
#: 一个作业保留的画布事件条数（进度条 + 岗位日志够用就行，不无限涨）
MAX_JOB_EVENTS = 160
#: 进度落盘节流：状态变化立刻写，纯进度最多 0.5s 写一次
PERSIST_INTERVAL = 0.5


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class ProjectBusy(RuntimeError):
    """项目仍有运行在跑，删除会被拒绝（接口层回 409）。"""


def safe_project_id(value: str) -> str:
    """项目 id 直接决定目录名，必须过 ``engine.safe_name``。"""
    return engine.safe_name(value or "", "project")


def new_project_id(title: str) -> str:
    slug = engine.safe_name((title or "project").lower(), "project")[:28].strip("_-").lower()
    return f"{slug or 'project'}-{uuid.uuid4().hex[:8]}"


def workspace_for(project_id: str) -> engine.Workspace:
    """项目工作区；URL 前缀与 ``api.py`` 的产物路由保持一致。"""
    clean = safe_project_id(project_id)
    return engine.Workspace(
        project_id=clean,
        root=FACTORY_DIR / clean,
        url_prefix=f"/api/factory/projects/{clean}/artifacts",
    )


class FactoryStore:
    """项目 + 运行的统一入口（进程内单例，路由只认它）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._backend = make_backend(JOBS_FILE, "factory_jobs")
        #: 正在跑的作业对象（活引用，进度就地更新；pg 模式只做内存镜像）
        self._live: dict[str, dict[str, Any]] = {}
        self._threads: dict[str, threading.Thread] = {}
        #: 同一项目串行跑，避免两个运行争抢同一份 cache.json
        self._project_locks: dict[str, threading.Lock] = {}

    # ------------------------------------------------------------------ #
    # 项目                                                                #
    # ------------------------------------------------------------------ #

    def _project_file(self, project_id: str) -> Path:
        return FACTORY_DIR / safe_project_id(project_id) / "project.json"

    def create(self, title: str, tenant: str, graph: dict[str, Any] | None = None, note: str = "") -> dict[str, Any]:
        project_id = new_project_id(title)
        record: dict[str, Any] = {
            "id": project_id,
            "tenant": tenant,
            "title": (title or "未命名项目").strip()[:80],
            "note": note or "",
            "created_at": _now(),
            "updated_at": _now(),
            "graph": graph_model.Graph.model_validate(graph or {"nodes": [], "edges": []}).to_dict(),
            "last_run": None,
        }
        workspace_for(project_id)  # 建好 artifacts 目录，产物写入前不必再判空
        self._write_project(record)
        return record

    def _write_project(self, record: dict[str, Any]) -> None:
        path = self._project_file(str(record["id"]))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(path)

    def get(self, project_id: str, tenant: str) -> dict[str, Any] | None:
        """读项目；不存在或不属于该租户都返回 None（调用方按 404 处理）。"""
        path = self._project_file(project_id)
        if not path.is_file():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            log.warn(f"项目文件读取失败 {project_id}：{error}")
            return None
        if not isinstance(record, dict) or str(record.get("tenant") or "default") != tenant:
            return None
        return record

    def list(self, tenant: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for path in sorted(FACTORY_DIR.glob("*/project.json")) if FACTORY_DIR.is_dir() else []:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(record, dict) or str(record.get("tenant") or "default") != tenant:
                continue
            graph = record.get("graph") or {}
            runs = self.list_runs(str(record.get("id") or ""), tenant)
            rows.append(
                {
                    "id": record.get("id"),
                    "title": record.get("title"),
                    "note": record.get("note") or "",
                    "created_at": record.get("created_at"),
                    "updated_at": record.get("updated_at"),
                    "node_count": len(graph.get("nodes") or []),
                    "edge_count": len(graph.get("edges") or []),
                    "last_run": record.get("last_run"),
                    "running": len([job for job in runs if job.get("status") in ("queued", "running")]),
                }
            )
        rows.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return rows

    def save_graph(self, project_id: str, tenant: str, payload: dict[str, Any]) -> dict[str, Any]:
        """整体覆盖工作流，返回项目 + 静态体检结果（问题不阻塞保存，只如实回报）。"""
        record = self.get(project_id, tenant)
        if record is None:
            raise KeyError(project_id)
        graph = graph_model.Graph.model_validate(payload or {"nodes": [], "edges": []})
        record["graph"] = graph.to_dict()
        if isinstance(payload.get("title"), str) and payload["title"].strip():
            record["title"] = payload["title"].strip()[:80]
        if isinstance(payload.get("note"), str):
            record["note"] = payload["note"]
        record["updated_at"] = _now()
        self._write_project(record)
        return {"project": record, "issues": [issue.model_dump() for issue in graph_model.validate_graph(graph)]}

    def rename(self, project_id: str, tenant: str, title: str) -> dict[str, Any]:
        record = self.get(project_id, tenant)
        if record is None:
            raise KeyError(project_id)
        record["title"] = (title or record["title"]).strip()[:80]
        record["updated_at"] = _now()
        self._write_project(record)
        return record

    def delete(self, project_id: str, tenant: str) -> dict[str, Any]:
        record = self.get(project_id, tenant)
        if record is None:
            raise KeyError(project_id)
        root = (FACTORY_DIR / safe_project_id(project_id)).resolve()
        if FACTORY_DIR.resolve() not in root.parents:  # 双保险：绝不删到 data 之外
            raise ValueError(f"非法项目目录：{project_id}")
        with self._lock:
            mine = {job_id: job for job_id, job in self._live.items() if str(job.get("task_id")) == record["id"]}
            # 取消只是改状态，那一轮还在写产物：线程没落地就不许删目录
            busy = [
                job_id
                for job_id, job in mine.items()
                if job.get("status") in ("queued", "running") or (self._threads.get(job_id) is not None and self._threads[job_id].is_alive())
            ]
        if busy:
            raise ProjectBusy(f"项目还有 {len(busy)} 个运行在写产物（{'、'.join(busy)}），等这一轮结束再删")
        doomed = list(mine)
        for job_id in doomed:
            self.cancel(job_id, tenant)
        shutil.rmtree(root, ignore_errors=True)
        self._backend.drop_task(str(record["id"]))
        with self._lock:
            for job_id in doomed:
                self._live.pop(job_id, None)
                self._threads.pop(job_id, None)
        return {"deleted": record["id"]}

    # ------------------------------------------------------------------ #
    # 岗位目录与预设                                                       #
    # ------------------------------------------------------------------ #

    @staticmethod
    def palette() -> dict[str, Any]:
        specs = all_specs()
        groups = [
            {
                "dept": key,
                "label": label,
                "note": note,
                "nodes": [spec.to_dict() for spec in sorted((item for item in specs if item.dept == key), key=lambda s: s.title)],
            }
            for key, label, note in DEPARTMENTS
        ]
        return {
            "departments": [group for group in groups if group["nodes"]],
            "orphan_nodes": [spec.to_dict() for spec in specs if not any(spec.dept == key for key, _, _ in DEPARTMENTS)],
            "port_types": sorted({port.type for spec in specs for port in (*spec.inputs, *spec.outputs)}),
            "node_count": len(specs),
        }

    # ------------------------------------------------------------------ #
    # 运行作业                                                             #
    # ------------------------------------------------------------------ #

    def start_run(
        self,
        project_id: str,
        tenant: str,
        *,
        targets: list[str] | None = None,
        force: bool = False,
        node_id: str = "",
    ) -> dict[str, Any]:
        """受理一次运行。``node_id`` 单独跑一个岗位（含其全部上游闭包）。"""
        record = self.get(project_id, tenant)
        if record is None:
            raise KeyError(project_id)
        graph = graph_model.Graph.model_validate(record.get("graph") or {"nodes": [], "edges": []})
        if not graph.nodes:
            raise ValueError("工作流是空的：先从左侧面板拖岗位进画布再跑")
        target_list = list(dict.fromkeys([*(item for item in (targets or []) if graph.node(item)), *([node_id] if node_id and graph.node(node_id) else [])]))
        if node_id and not graph.node(node_id):
            raise ValueError(f"画布上没有节点：{node_id}")
        issues = [issue for issue in graph_model.validate_graph(graph) if issue.level == "error"]
        if issues:
            raise ValueError("工作流体检未通过：" + "；".join(issue.message for issue in issues[:4]))

        job: dict[str, Any] = {
            "id": f"frun-{uuid.uuid4().hex[:10]}",
            "task_id": str(record["id"]),  # gen_jobs 按 task_id 归类，drop_task 直接可用
            "tenant": tenant,
            "project_id": str(record["id"]),
            "kind": "factory_run",
            "status": "queued",
            "created_at": _now(),
            "updated_at": _now(),
            "targets": target_list,
            "force": bool(force),
            "progress": 0.0,
            "counts": {},
            "elapsed_ms": 0,
            "events": [],
            "nodes": {},
            "error": "",
            "result": None,
        }
        with self._lock:
            self._live[job["id"]] = job
        # 落盘用副本，不用活对象：后台线程会就地改 job，边改边序列化会写出半截状态
        self._backend.create(self._copy(job))
        thread = threading.Thread(target=self._worker, args=(job["id"],), daemon=True, name=f"factory-run-{job['id']}")
        with self._lock:
            self._threads[job["id"]] = thread
        thread.start()
        return self.public_job(self._copy(job))

    def _touch(self, job: dict[str, Any], *, force: bool = False) -> None:
        """刷新时间戳并按节流落盘（状态变化 force=True 立刻写）。"""
        job["updated_at"] = _now()
        stamp = float(job.get("_persisted_at") or 0.0)
        if not force and (time.time() - stamp) < PERSIST_INTERVAL:
            return
        job["_persisted_at"] = time.time()
        self._backend.write_back([self._copy(job)])

    @staticmethod
    def serialisable(job: dict[str, Any]) -> dict[str, Any]:
        """落盘副本：去掉内部计时字段。调用方必须已经持锁或传入快照。"""
        return {key: value for key, value in job.items() if not str(key).startswith("_")}

    def _copy(self, job: dict[str, Any]) -> dict[str, Any]:
        """深一层复制作业（events/nodes/result 各拷一份），落盘与读出都走它，
        避免后台线程边改边序列化。"""
        with self._lock:
            return {
                **self.serialisable(job),
                "events": list(job.get("events") or []),
                "nodes": {node_id: dict(row) for node_id, row in (job.get("nodes") or {}).items()},
                "result": job.get("result"),
            }

    def _worker(self, job_id: str) -> None:
        with self._lock:
            job = self._live.get(job_id) or {}
        if not job:
            return
        project_id = str(job.get("project_id") or "")
        try:
            record = self.get(project_id, str(job.get("tenant") or "default"))
        except Exception as error:  # noqa: BLE001 - 读盘异常要如实落到作业上
            record, job["error"] = None, f"项目读取失败：{error}"
        if record is None:
            with self._lock:
                job["status"], job["error"] = "error", job.get("error") or "项目不存在或无权访问"
            self._touch(job, force=True)
            return
        graph = graph_model.Graph.model_validate(record.get("graph") or {"nodes": [], "edges": []})
        workspace = workspace_for(project_id)
        lock = self._project_lock(project_id)
        if not lock.acquire(blocking=False):
            with self._lock:
                job["events"].append({"ts": _now(), "type": "run.wait", "message": "同一项目已有运行在跑，排队等待"})
            lock.acquire()
        try:
            with self._lock:
                job["status"] = "running"
            self._touch(job, force=True)

            def on_event(event: dict[str, Any]) -> None:
                kind = str(event.get("type") or "")
                with self._lock:
                    job["events"].append({"ts": _now(), **event})
                    job["events"] = job["events"][-MAX_JOB_EVENTS:]
                    node_id = str(event.get("node_id") or "")
                    if node_id:
                        row = job["nodes"].setdefault(node_id, {})
                        if kind == "node.progress":
                            row["progress"] = float(event.get("ratio") or 0.0)
                        else:
                            row["status"] = {
                                "node.start": "running",
                                "node.finish": "ok",
                                "node.cached": "cached",
                                "node.error": "error",
                                "node.blocked": "blocked",
                                "node.skipped": "skipped",
                            }.get(kind, row.get("status") or "idle")
                            if kind in {"node.finish", "node.cached", "node.error", "node.blocked", "node.skipped"}:
                                row["progress"] = 1.0
                    done = sum(1 for item in job["nodes"].values() if item.get("status") in {"ok", "cached", "error", "blocked", "skipped"})
                    job["progress"] = round(min(0.99, done / max(1, len(graph.nodes))), 3)
                self._touch(job)

            result = engine.run_graph(
                graph,
                workspace,
                targets=list(job.get("targets") or []) or None,
                force=bool(job.get("force")),
                on_event=on_event,
            )
            with self._lock:
                job["counts"] = result.get("counts") or {}
                job["elapsed_ms"] = int(result.get("elapsed_ms") or 0)
                job["progress"] = 1.0
                if job.get("status") != "cancelled":  # 用户取消过就别把它说成跑完了
                    job["status"] = "ok" if result.get("ok") else "error"
                job["result"] = {
                    "ok": bool(result.get("ok")),
                    "counts": job["counts"],
                    "elapsed_ms": job["elapsed_ms"],
                    "stale_nodes": result.get("stale_nodes") or [],
                    "nodes": {
                        node_id: {
                            "status": row.get("status"),
                            "summary": row.get("summary"),
                            "error": row.get("error"),
                            "elapsed_ms": row.get("elapsed_ms"),
                            "cached": row.get("cached"),
                            "outputs": row.get("outputs") or {},
                        }
                        for node_id, row in (result.get("runs") or {}).items()
                    },
                }
                for node_id, row in (result.get("runs") or {}).items():
                    live = job["nodes"].setdefault(node_id, {})
                    live["status"] = "cached" if row.get("cached") else row.get("status")
                    live["progress"] = 1.0
            self._remember_run(record, job)
        except Exception as error:  # noqa: BLE001 - 后台线程的异常必须落在作业上而不是吞掉
            log.error(f"工厂运行异常 {job_id}", error)
            with self._lock:
                job["status"], job["error"] = "error", f"{type(error).__name__}: {error}"
        finally:
            lock.release()
            self._touch(job, force=True)
            with self._lock:
                self._threads.pop(job_id, None)
                # 跑完就从内存回收：之后前端轮询读的是落盘快照，进程不再为历史运行养对象
                self._live.pop(job_id, None)

    def _remember_run(self, record: dict[str, Any], job: dict[str, Any]) -> None:
        record["last_run"] = {
            "job_id": job["id"],
            "status": job["status"],
            "counts": job.get("counts") or {},
            "elapsed_ms": job.get("elapsed_ms") or 0,
            "finished_at": _now(),
            "targets": job.get("targets") or [],
        }
        record["updated_at"] = _now()
        try:
            self._write_project(record)
        except OSError as error:  # noqa: BLE001 - 运行结果已经拿到，记不下摘要只警告
            log.warn(f"项目 {record.get('id')} 运行摘要写入失败：{error}")

    def _project_lock(self, project_id: str) -> threading.Lock:
        with self._lock:
            return self._project_locks.setdefault(project_id, threading.Lock())

    def cancel(self, job_id: str, tenant: str) -> dict[str, Any]:
        """标记取消：正在跑的那轮会自然结束（岗位不宜硬杀，产物会残缺），
        但作业状态立刻变 cancelled，前端不再等它。"""
        job = self.job(job_id, tenant)
        if job is None:
            raise KeyError(job_id)
        if job.get("status") in ("queued", "running"):
            with self._lock:
                job["status"] = "cancelled"
            self._touch(job, force=True)
        return self.public_job(job)

    def job(self, job_id: str, tenant: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._live.get(job_id)
        if job is not None and str(job.get("tenant")) != tenant:
            return None
        if job is not None:
            return job
        for row in self._backend.snapshot():
            if str(row.get("id")) == job_id and str(row.get("tenant") or "default") == tenant:
                return row
        return None

    def list_runs(self, project_id: str, tenant: str, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = [row for row in self._backend.snapshot() if str(row.get("task_id")) == project_id and str(row.get("tenant") or "default") == tenant]
        with self._lock:
            live_ids = set(self._live)
        merged = {str(row.get("id")): row for row in rows}
        for job_id in live_ids:
            job = self._live[job_id]
            if str(job.get("task_id")) == project_id and str(job.get("tenant")) == tenant:
                merged[job_id] = job
        ordered = sorted(merged.values(), key=lambda row: str(row.get("created_at") or ""), reverse=True)
        return [self.public_job(row, detail=False) for row in ordered[: max(1, limit)]]

    @staticmethod
    def public_job(job: dict[str, Any], *, detail: bool = True) -> dict[str, Any]:
        """给前端的作业视图：状态 + 进度 + 每节点状态；``detail=True`` 才带事件与结果。"""
        row = {
            "id": job.get("id"),
            "project_id": job.get("project_id") or job.get("task_id"),
            "status": job.get("status"),
            "progress": round(float(job.get("progress") or 0.0), 3),
            "counts": job.get("counts") or {},
            "elapsed_ms": job.get("elapsed_ms") or 0,
            "error": job.get("error") or "",
            "created_at": job.get("created_at"),
            "updated_at": job.get("updated_at"),
            "targets": job.get("targets") or [],
            "force": bool(job.get("force")),
            "nodes": job.get("nodes") or {},
        }
        if detail:
            row["events"] = (job.get("events") or [])[-60:]
            result = job.get("result")
            row["has_result"] = bool(result)
            row["result"] = result
        return row

    # ------------------------------------------------------------------ #
    # 产物                                                                #
    # ------------------------------------------------------------------ #

    def node_outputs(self, project_id: str, tenant: str, node_id: str) -> dict[str, Any]:
        """某个岗位最近一次成功交付的**完整**产物。

        作业接口只携带 ``engine._shape`` 摘要（全链路四十多个岗位整包传太沉），但放映室要
        时间轴的 ``shots[]``、检查面板要剧本正文，这些只在项目 ``cache.json`` 里。
        缓存按签名淘汰，岗位没跑成功就没有行——回 ``KeyError`` 让接口说 404，
        不返回空字典把「还没交付」伪装成「交付了空产物」。
        """
        record = self.get(project_id, tenant)
        if record is None:
            raise KeyError(project_id)
        graph = graph_model.Graph.model_validate(record.get("graph") or {"nodes": [], "edges": []})
        node = graph.node(node_id)
        if node is None:
            raise KeyError(node_id)
        row = workspace_for(str(record["id"])).load_cache().get(node_id)
        if not isinstance(row, dict) or not isinstance(row.get("outputs"), dict) or not row["outputs"]:
            raise KeyError(node_id)
        return {
            "node_id": node_id,
            "type": node.type,
            "title": node.title or "",
            "summary": str(row.get("summary") or ""),
            "finished_at": str(row.get("finished_at") or ""),
            "outputs": row["outputs"],
        }

    def artifact_path(self, project_id: str, rel: str) -> Path:
        """把 ``images/xxx.png`` 这种引用解析成项目产物目录内的真实路径。

        只认项目自己的 ``artifacts/``，越界、绝对路径、``..`` 一律拒绝——
        这与 ``routes.export_delivery`` 的「只信任受控目录」口径一致。
        """
        root = (FACTORY_DIR / safe_project_id(project_id) / "artifacts").resolve()
        parts = [engine.safe_name(part) for part in str(rel or "").replace("\\", "/").split("/") if part not in ("", ".")]
        if not parts:
            raise ValueError("产物路径为空")
        path = root.joinpath(*parts).resolve()
        if root not in path.parents or not path.is_file():
            raise ValueError(f"产物不存在：{rel}")
        return path


store = FactoryStore()

__all__ = ["FactoryStore", "ProjectBusy", "store", "workspace_for", "FACTORY_DIR", "JOBS_FILE"]
