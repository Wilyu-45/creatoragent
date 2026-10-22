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

import hashlib
import json
import math
import re
import shutil
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

from ..config import DATA_DIR
from ..core.gen_jobs import make_backend
from ..logger import create_logger
from . import acceptance as acceptance_module
from . import engine
from . import graph as graph_model
from . import nodes  # noqa: F401 - 导入即注册岗位，路由与体检都要查注册表
from .spec import DEPARTMENTS, all_specs, get_spec

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


class RunUnfinished(ValueError):
    """参与对比的运行还没结束：结果仍会变，拒绝对比（接口层回 409）。"""


#: 进程重启后遗留作业的统一交代：不把「没跑完」说成「跑完了」，也不丢已完成岗位的缓存
INTERRUPTED = "进程重启，运行在收尾前中断；已交付岗位的产物与缓存保留，重跑会复用缓存"

#: 交付包在项目目录里的固定文件名：重复下载覆盖同一份，不随下载次数膨胀
BUNDLE_NAME = "delivery.zip"
#: 交付台账：每次打包（放行或被验收拦下）都追加一条留痕，与 project.json/artifacts 同口径
#: 保留文件不迁 pg。固定名 delivery.zip 只说明「现在这版包里有什么」，台账回答的是
#: 「这一版到底交没交出去、什么时候、验收量出什么结论、包多大、哪些是占位」——
#: 没有它就无法自证上周交付的内容，被拦下的尝试也无声消失。
DELIVERIES_NAME = "deliveries.json"
#: 台账只留最近 N 条（追加式，超出丢最旧的）：审计留痕不是历史数据库
MAX_DELIVERIES = 50

_UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|\r\n]+')


def _download_name(title: str, project_id: str) -> str:
    """下载文件名：保留中文（URL 编码交给 Content-Disposition），只剔掉路径分隔符。"""
    clean = _UNSAFE_FILENAME.sub("_", str(title or "").strip()).strip(" ._")[:60]
    return f"{clean or 'factory'}-{project_id}-delivery.zip"


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _film_fingerprint(value: Any) -> str:
    """成片时间轴的内容指纹：规范化 JSON（排序键、忽略字典顺序）的 sha256。

    用来诚实回答「放映室现在这条，和最近一次交付装箱的那条，剪辑内容有没有变」——
    交付包是固定名覆盖的，历史那版的字节盘上已不存在，所以不装作能回放旧包，
    只比对剪辑决定本身（逐镜引用/时长/顺序）。无成片时返回空串。
    """
    if value is None:
        return ""
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _simulated_nodes(cache: dict[str, Any]) -> list[str]:
    """扫缓存里哪些岗位的交付物带 ``simulated=true``；交付包必须把它们点名。"""

    def hit(value: Any) -> bool:
        if isinstance(value, dict):
            return any((key == "simulated" and item is True) or hit(item) for key, item in value.items())
        if isinstance(value, list):
            return any(hit(item) for item in value)
        return False

    return sorted(
        str(node_id)
        for node_id, row in cache.items()
        if isinstance(row, dict) and hit(row.get("outputs") or {})
    )


def recover_interrupted(backend: Any) -> int:
    """把落盘里仍 ``queued``/``running``/``cancelling`` 的作业标记为中断。

    跑图靠进程内后台线程推进，进程一退线程就没了；作业行却还停在运行中。
    启动时统一收口：状态改为 ``error``、写一条 ``run.interrupted`` 事件、
    把仍挂在队列/运行/取消中的岗位标红——已完成岗位的产物与 ``cache.json`` 原样保留，
    用户重跑时按签名缓存复用，不必从头再来。返回回收条数。
    """
    now = _now()
    doomed: list[dict[str, Any]] = []
    for raw in backend.snapshot():
        if str(raw.get("status") or "") not in ("queued", "running", "cancelling"):
            continue
        job = dict(raw)
        job["status"] = "error"
        job["error"] = INTERRUPTED
        job["updated_at"] = now
        job["events"] = [
            *(job.get("events") or []),
            {"ts": now, "type": "run.interrupted", "level": "error", "message": INTERRUPTED},
        ][-MAX_JOB_EVENTS:]
        job["nodes"] = {
            node_id: ({**row, "status": "error", "progress": 1.0} if row.get("status") in ("queued", "running", "cancelling") else row)
            for node_id, row in (job.get("nodes") or {}).items()
        }
        doomed.append(job)
    if doomed:
        backend.write_back(doomed)
    return len(doomed)


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
        recovered = 0
        try:
            recovered = recover_interrupted(self._backend)
        except Exception as error:  # noqa: BLE001 - 回收失败不该拖垮整个服务启动
            log.warn(f"启动回收遗留运行失败（不阻塞启动）：{error}")
        if recovered:
            log.warn(f"回收 {recovered} 个进程重启遗留的运行（已交付岗位的产物与缓存保留）")

    # ------------------------------------------------------------------ #
    # 项目                                                                #
    # ------------------------------------------------------------------ #

    def _project_file(self, project_id: str) -> Path:
        return FACTORY_DIR / safe_project_id(project_id) / "project.json"

    def _busy_job_ids(self, project_id: str) -> list[str]:
        """还在写这个项目产物的运行（含已取消但线程未落地的那一轮）。"""
        with self._lock:
            return [
                job_id
                for job_id, job in self._live.items()
                if str(job.get("task_id")) == project_id
                and (
                    job.get("status") in ("queued", "running", "cancelling")
                    or (self._threads.get(job_id) is not None and self._threads[job_id].is_alive())
                )
            ]

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

    def _running_counts(self, tenant: str) -> dict[str, int]:
        """一次作业快照统计每个项目在跑的作业数。

        项目列表按项目逐个调 ``list_runs`` 会把作业库扫描 N 遍；这里先合并落盘行与
        内存活作业（按 job_id 去重），再按项目计数，成本从 O(项目×作业) 降到 O(项目+作业)。
        """
        rows: dict[str, dict[str, Any]] = {}
        for row in self._backend.snapshot():
            if str(row.get("tenant") or "default") == tenant:
                rows[str(row.get("id"))] = row
        with self._lock:
            for job_id, job in self._live.items():
                if str(job.get("tenant") or "") == tenant:
                    rows[job_id] = job
        counts: dict[str, int] = {}
        for job in rows.values():
            if str(job.get("status") or "") in ("queued", "running", "cancelling"):
                project_id = str(job.get("task_id") or "")
                counts[project_id] = counts.get(project_id, 0) + 1
        return counts

    def list(self, tenant: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        running_counts = self._running_counts(tenant)
        for path in sorted(FACTORY_DIR.glob("*/project.json")) if FACTORY_DIR.is_dir() else []:
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(record, dict) or str(record.get("tenant") or "default") != tenant:
                continue
            graph = record.get("graph") or {}
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
                    "running": running_counts.get(str(record.get("id") or ""), 0),
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
        busy = self._busy_job_ids(str(record["id"]))
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
    # 交付验收与交付包                                                     #
    # ------------------------------------------------------------------ #

    def acceptance(self, project_id: str, tenant: str) -> dict[str, Any]:
        """交付前验收报告（只读）：终端岗位、产物引用、时间轴与占位标记。"""
        record = self.get(project_id, tenant)
        if record is None:
            raise KeyError(project_id)
        return acceptance_module.inspect(record, workspace_for(str(record["id"])))

    def film(self, project_id: str, tenant: str) -> dict[str, Any]:
        """成片时间轴（只读）：拓扑序最下游那条 ``kind==timeline`` 的交付物。

        与验收报告里的 ``film``、交付包里的 ``timeline.json`` 同一口径（``select_film``）：
        放映室回看的、装进包里的、验收量出的必须是同一条片子。不回产成投影，只从缓存取原值。

        另附**回看对账**：把现在这条成片的内容指纹与最新一条已交付留痕里记的指纹对比，
        诚实回答「屏幕上这条还是不是刚交付装箱那条」——不回放旧包（旧版字节已被固定名覆盖），
        只说清内容有没有变。``matches``：一致 ``True``、之后又改过 ``False``、无成片或从未交付 ``None``。
        """
        record = self.get(project_id, tenant)
        if record is None:
            raise KeyError(project_id)
        workspace = workspace_for(str(record["id"]))
        graph = graph_model.Graph.model_validate(record.get("graph") or {"nodes": [], "edges": []})
        meta, value = acceptance_module.select_film(graph, workspace.load_cache())
        live_fp = _film_fingerprint(value)
        # 只读台账取最新一条已交付留痕（不新建作业）：拿它记的成片指纹和现在这条对账
        entries = self._load_deliveries(self._deliveries_file(str(record["id"])))
        delivered = [row for row in entries if row.get("event") == "delivered"]
        last = delivered[-1] if delivered else {}
        last_fp = str(last.get("film_fingerprint") or "")
        if not live_fp or not last_fp:
            # 现在没成片 / 从未交付过带成片的版：无从比对，如实为 null（不误报成「改过」）
            matches: bool | None = None
        else:
            matches = live_fp == last_fp
        return {
            "film": meta,
            "timeline": value,
            "delivery": {
                "delivered_at": str(last.get("at") or ""),
                "archive_sha256": str(last.get("archive_sha256") or ""),
                "download_name": str(last.get("download_name") or ""),
                "film_fingerprint": last_fp,
                "live_fingerprint": live_fp,
                "matches": matches,
            },
        }

    def build_bundle(self, project_id: str, tenant: str) -> tuple[Path, str]:
        """把工作流 + 全部产物打成一个 zip，返回 ``(包路径, 下载文件名)``。

        包固定落在项目 ``exports/delivery.zip``，重复下载覆盖同一份而不膨胀。
        **先过交付验收**（``acceptance.inspect``）：工作流有错、终端岗位没交付或
        缓存已过期、产物引用文件不在盘上，都抱 ``AcceptanceBlocked`` 拒打包——
        一个只有半成品或旧产物的 zip 会让人误以为交付完成了。
        没有产物时同样拒绝。运行还在写产物时也拒绝——先拿项目运行锁再检查，
        打包期间新一轮运行只能排队，zip 里不会出现写了一半的 PNG/WAV。
        """
        record = self.get(project_id, tenant)
        if record is None:
            raise KeyError(project_id)
        lock = self._project_lock(str(record["id"]))
        if not lock.acquire(blocking=False):
            raise ProjectBusy("项目正在运行：等这一轮结束再打包")
        try:
            busy = self._busy_job_ids(str(record["id"]))
            if busy:
                raise ProjectBusy(f"项目还有 {len(busy)} 个运行在写产物（{'、'.join(busy)}），等这一轮结束再打包")
            return self._assemble_bundle(record)
        finally:
            lock.release()

    def _assemble_bundle(self, record: dict[str, Any]) -> tuple[Path, str]:
        workspace = workspace_for(str(record["id"]))
        root = workspace.artifacts_dir
        files: list[dict[str, Any]] = []
        for path in sorted(root.rglob("*")) if root.is_dir() else []:
            if not path.is_file():
                continue
            files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": _sha256_of(path),
                }
            )
        # 验收放在统计之后、写包之前：半成品不许变成交付包（见 acceptance.py）
        report = acceptance_module.inspect(record, workspace)
        files_bytes = sum(row["bytes"] for row in files)
        if not report["ok"]:
            # 拦下的尝试也进台账：只留下成功交付的台账等于把「拒过多少次半成品」抹掉了
            self._append_delivery(
                record,
                self._delivery_entry(
                    "blocked",
                    report,
                    files_count=len(files),
                    total_bytes=files_bytes,
                    blockers=[str(row["message"]) for row in report["issues"] if row["level"] == "error"],
                ),
            )
            raise acceptance_module.AcceptanceBlocked(acceptance_module.blockers(report))
        if not files:
            self._append_delivery(
                record,
                self._delivery_entry(
                    "blocked", report, files_count=0, total_bytes=0,
                    blockers=["项目还没有产物：先跑一次图，再下载交付包"],
                ),
            )
            raise ValueError("项目还没有产物：先跑一次图，再下载交付包")

        graph = graph_model.Graph.model_validate(record.get("graph") or {"nodes": [], "edges": []})
        bundle_cache = workspace.load_cache()
        simulated = _simulated_nodes(bundle_cache)
        # 成片时间轴与验收/回看同一口径：acceptance.select_film 选轴并回拿原值
        film, film_value = acceptance_module.select_film(graph, bundle_cache)
        film_fp = _film_fingerprint(film_value)  # 内容指纹：装箱与台账都记它，回看时与实时值对账
        total_bytes = sum(row["bytes"] for row in files)
        now = _now()
        manifest = {
            "format": "creatoragent.factory.delivery",
            "version": 2,
            "generated_at": now,
            "project": {
                "id": record["id"],
                "title": record.get("title") or "",
                "note": record.get("note") or "",
                "created_at": record.get("created_at") or "",
                "updated_at": record.get("updated_at") or "",
                "node_count": len(graph.nodes),
                "edge_count": len(graph.edges),
            },
            "artifact_count": len(files),
            "total_bytes": total_bytes,
            "simulated_nodes": simulated,
            "acceptance": {
                "verdict": report["verdict"],
                "counts": report["counts"],
                "checked_at": report["checked_at"],
                "stats": report["stats"],
                "warnings": [row["message"] for row in report["issues"] if row["level"] in ("warn", "info")][:20],
            },
            "film": ({**film, "path": "timeline.json", "fingerprint": film_fp} if film_value else None),
            "files": files,
        }
        readme = self._bundle_readme(record, len(graph.nodes), len(graph.edges), files, total_bytes, simulated, now, report)

        exports = workspace.root / "exports"
        exports.mkdir(parents=True, exist_ok=True)
        archive = exports / BUNDLE_NAME
        tmp = exports / f"{BUNDLE_NAME}.tmp"
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("README.txt", readme)
            bundle.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))
            bundle.writestr("project.json", json.dumps(record, ensure_ascii=False, indent=1))
            if film_value is not None:
                # 把那条片子的剪辑决定与素材一起装箱：拆包的人拿到的不只是散件，还有「怎么拼成一片」
                bundle.writestr("timeline.json", json.dumps(film_value, ensure_ascii=False, indent=1))
            for row in files:
                bundle.write(root / row["path"], arcname=f"artifacts/{row['path']}")
        self._replace_into(tmp, archive)
        download_name = _download_name(str(record.get("title") or ""), str(record["id"]))
        self._append_delivery(
            record,
            self._delivery_entry(
                "delivered",
                report,
                files_count=len(files),
                total_bytes=total_bytes,
                archive_sha=_sha256_of(archive),
                archive_bytes=archive.stat().st_size,
                download_name=download_name,
                film_fingerprint=film_fp,
            ),
        )
        return archive, download_name

    @staticmethod
    def _replace_into(tmp: Path, archive: Path) -> None:
        """把 .tmp 换成正式交付包。

        Windows 上刚写好的 zip 常被杀毒/索引服务短暂占用，``os.replace`` 会抛
        PermissionError；这不是半成品，而是同一份内容的覆盖写入没排上队。
        重试几次仍失败就把真实错误抛出去，绝不静默留下 ``.tmp`` 冒充交付完成。
        """
        last: OSError | None = None
        for attempt in range(10):
            try:
                tmp.replace(archive)
                return
            except PermissionError as error:  # noqa: PERF203 - 短暂文件锁，退避后重试
                last = error
                time.sleep(0.5)
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise last if last else OSError("交付包写入失败")

    # ------------------------------------------------------------------ #
    # 交付台账（append-only 审计留痕）                                    #
    # ------------------------------------------------------------------ #

    def _deliveries_file(self, project_id: str) -> Path:
        return FACTORY_DIR / safe_project_id(project_id) / DELIVERIES_NAME

    @staticmethod
    def _load_deliveries(path: Path) -> list[dict[str, Any]]:
        """读回台账；文件不存在或脏了一律当空——留痕读不出不能拖垮打包与页面。"""
        if not path.is_file():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return raw if isinstance(raw, list) else []

    def _append_delivery(self, record: dict[str, Any], entry: dict[str, Any]) -> None:
        """向项目台账追加一条留痕（只留最近 ``MAX_DELIVERIES`` 条，原子写）。

        只在 ``build_bundle`` 拿到项目锁后调用，同一进程里不会并发写同一个文件；
        写台账失败不能反过来把一个已写好的交付包说成失败，故只告警不抛。
        """
        try:
            path = self._deliveries_file(str(record["id"]))
            path.parent.mkdir(parents=True, exist_ok=True)
            entries = self._load_deliveries(path)
            entries.append(entry)
            entries = entries[-MAX_DELIVERIES:]
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(path)
        except OSError as error:  # noqa: PERF203 - 留痕是旁路，写不进不能影响真正的交付
            log.warning(f"交付台账写入失败（忽略）：{error}")

    @staticmethod
    def _delivery_entry(
        event: str,
        report: dict[str, Any],
        *,
        files_count: int,
        total_bytes: int,
        blockers: list[str] | None = None,
        archive_sha: str = "",
        archive_bytes: int = 0,
        download_name: str = "",
        film_fingerprint: str = "",
    ) -> dict[str, Any]:
        """把一次打包的量数结果压成一条台账。字段全部来自验收报告，不另算一套口径。"""
        stats = report.get("stats") or {}
        counts = report.get("counts") or {}
        entry: dict[str, Any] = {
            "at": _now(),
            "event": event,
            "ok": event == "delivered",
            "verdict": str(report.get("verdict") or ""),
            "error_count": int(counts.get("error") or 0),
            "warn_count": int(counts.get("warn") or 0),
            "info_count": int(counts.get("info") or 0),
            "nodes": int(stats.get("nodes") or 0),
            "edges": int(stats.get("edges") or 0),
            "terminals": int(stats.get("terminals") or 0),
            "terminals_ready": int(stats.get("terminals_ready") or 0),
            "delivered": int(stats.get("delivered") or 0),
            "simulated_nodes": int(stats.get("simulated_nodes") or 0),
            "refs_missing": int(stats.get("refs_missing") or 0),
            "artifact_count": int(files_count),
            "total_bytes": int(total_bytes),
        }
        film = report.get("film") or {}
        if film.get("node_id"):
            # 台账记下这版交付带的成片（回看闭环）：几镜、多久、来自哪个岗位
            entry["film_node"] = str(film["node_id"])
            entry["film_shots"] = int(film.get("shots") or 0)
            entry["film_duration"] = float(film.get("duration_s") or 0.0)
        if film_fingerprint:
            # 内容指纹：让以后的回看能判断「现在这条是不是当时装箱那条」（不存旧包字节，只存指纹）
            entry["film_fingerprint"] = film_fingerprint
        if archive_sha:
            entry["archive_sha256"] = archive_sha
            entry["archive_bytes"] = int(archive_bytes)
            entry["download_name"] = download_name
        if blockers:
            entry["blockers"] = list(blockers)[:5]
        return entry

    def delivery_log(self, project_id: str, tenant: str) -> dict[str, Any]:
        """交付台账（只读、新的在前）：每次打包的放行/拦截留痕与总体统计。"""
        record = self.get(project_id, tenant)
        if record is None:
            raise KeyError(project_id)
        entries = self._load_deliveries(self._deliveries_file(str(record["id"])))
        delivered = [row for row in entries if row.get("event") == "delivered"]
        last = delivered[-1] if delivered else {}
        return {
            "project": {"id": str(record["id"]), "title": str(record.get("title") or "")},
            "entries": list(reversed(entries)),
            "summary": {
                "total": len(entries),
                "delivered": len(delivered),
                "blocked": len(entries) - len(delivered),
                "last_delivered_at": str(last.get("at") or ""),
                "last_archive_sha256": str(last.get("archive_sha256") or ""),
                "capped": MAX_DELIVERIES,
            },
        }

    @staticmethod
    def _bundle_readme(
        record: dict[str, Any],
        node_count: int,
        edge_count: int,
        files: list[dict[str, Any]],
        total_bytes: int,
        simulated: list[str],
        now: str,
        report: dict[str, Any] | None = None,
    ) -> str:
        """包内说明：怎么用、包里有什么、验收量出了什么，一次讲清。"""
        last = record.get("last_run") if isinstance(record.get("last_run"), dict) else {}
        run_bits: list[str] = []
        if last:
            run_bits.append(f"最近一次运行：{last.get('job_id') or ''} · {last.get('status') or ''}")
            if last.get("targets"):
                run_bits.append(f"目标：{'、'.join(str(item) for item in last['targets'])}")
            if last.get("force"):
                run_bits.append("整轮忽略缓存")
            if last.get("force_nodes"):
                run_bits.append(f"强跑：{'、'.join(str(item) for item in last['force_nodes'])}")
            if last.get("override_nodes"):
                run_bits.append(f"本轮改参：{'、'.join(str(item) for item in last['override_nodes'])}")
        film = (report or {}).get("film") or None
        usage_film = (
            [
                f"4. timeline.json 是这条片子的剪辑时间轴（{film['title']} {film['shots']} 镜 / "
                f"{round(float(film['duration_s']), 1)}s）：逐镜引用 artifacts/ 里的素材，配合影视制作工厂放映室可直接回看。"
            ]
            if film
            else []
        )
        lines = [
            "影视制作工厂 · 交付包",
            "=" * 24,
            "",
            f"项目：{record.get('title') or ''}（{record.get('id')}）",
            f"生成时间：{now}",
            f"岗位：{node_count} 个 · 连线：{edge_count} 条",
            f"产物：{len(files)} 个文件，合计 {round(total_bytes / 1024.0, 1)} KB",
            *(["运行溯源：" + " · ".join(run_bits)] if run_bits else []),
            "",
            "怎么用",
            "------",
            "1. project.json 是完整工作流（画布节点、连线与参数），可在影视制作工厂里导入还原。",
            "2. artifacts/ 按岗位保存各岗位最近一次成功交付的产物：图片、音频、字幕、时间轴等。",
            "3. manifest.json 逐文件记录大小与 sha256，可用于校验传输完整性。",
            *usage_film,
            "",
        ]
        # 验收结论随包走：拿到 zip 的人不看画布也知道这版是过了验收还是只带警告
        stats = (report or {}).get("stats") or {}
        counts = (report or {}).get("counts") or {}
        verdict_label = {"pass": "通过", "warn": "通过（带警告）", "blocked": "未过"}.get(str((report or {}).get("verdict") or ""), "未验收")
        lines += [
            "交付验收",
            "--------",
            f"结论：{verdict_label}（{(report or {}).get('checked_at') or '本包生成于验收之后'}）",
        ]
        if stats:
            lines.append(
                f"终端岗位：{stats.get('terminals_ready')}/{stats.get('terminals')} 有可复用交付｜"
                f"已交付岗位：{stats.get('delivered')}｜产物：{stats.get('artifact_files')} 个文件"
            )
            lines.append(
                f"产物引用核对：{stats.get('refs_checked')} 条（缺失 {stats.get('refs_missing')}、空文件 {stats.get('refs_empty')}）"
            )
        if counts:
            lines.append(f"警告 / 提示：{counts.get('warn', 0)} + {counts.get('info', 0)} 条")
        for row in ((report or {}).get("issues") or [])[:12]:
            if row.get("level") in ("warn", "info"):
                lines.append(f"  - [{row['level']}] {row['message']}")
        lines += [
            "",
            "诚实边界",
            "--------",
            "- 本包由本地工具链产出；带 simulated=true 的岗位是占位素材，不是真实模型输出。",
            "- 交付包只在工作流体检无错、终端岗位都有可复用交付、产物引用文件逐个在盘时才放行。",
            "- 成片容器（MP4）需要外部编码器或前端播放器导出，本包不会假装包含 mp4。",
            "- 交付包只含工作流与产物，不含运行日志与内部缓存（cache.json）。",
            "- 缓存按岗位单槽、产物同名覆盖：改参运行会按新签名覆盖该岗位的缓存与同名产物，"
            "历史版本不单独保留；改参本身只属于那一轮，不写入 project.json 的工作流。",
            "",
        ]
        if simulated:
            lines += [f"本次带模拟标记的岗位（{len(simulated)} 个）：", *(f"  - {node_id}" for node_id in simulated), ""]
        else:
            lines += ["本次没有带模拟标记的岗位。", ""]
        return "\n".join(lines)

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

    def _prepare_run(
        self,
        record: dict[str, Any],
        targets: list[str] | None,
        node_id: str,
        force_nodes: list[str] | None = None,
        param_overrides: dict[str, Any] | None = None,
    ) -> tuple[
        graph_model.Graph,
        list[str],
        list[str],
        list[str],
        dict[str, dict[str, Any]],
        list[str],
    ]:
        """运行与预检共用的一次目标/体检/强跑/改参收口：口径不一致会让预检变成善意的谎言。

        返回 ``(effective_graph, target_list, applied_force, ignored_force,
        applied_overrides, ignored_overrides)``。强跑与改参都必须真实存在于画布，否则点名
        拒绝；存在但不在本次计划闭包内的岗位无从执行，如实记为 ignored，不假装「已生效」。
        改参只叠加到返回的深拷贝上，保存的图保持不变。
        """
        graph = graph_model.Graph.model_validate(record.get("graph") or {"nodes": [], "edges": []})
        if not graph.nodes:
            raise ValueError("工作流是空的：先从左侧面板拖岗位进画布再跑")
        target_list = list(dict.fromkeys(targets or []))
        unknown = [item for item in target_list if not graph.node(item)]
        if node_id and not graph.node(node_id):
            unknown.append(node_id)
        unknown = list(dict.fromkeys(unknown))
        if unknown:
            # 画布可能刚被改过/项目被换过：静默丢掉目标会退化成「跑全图」，与用户点的不是一回事
            raise ValueError(f"画布上没有这些岗位：{'、'.join(unknown[:4])}")
        if node_id:
            target_list = list(dict.fromkeys([*target_list, node_id]))
        issues = [issue for issue in graph_model.validate_graph(graph) if issue.level == "error"]
        if issues:
            raise ValueError("工作流体检未通过：" + "；".join(issue.message for issue in issues[:4]))
        requested = list(dict.fromkeys(str(item) for item in (force_nodes or []) if str(item)))
        unknown_force = [item for item in requested if not graph.node(item)]
        if unknown_force:
            raise ValueError(f"画布上没有这些岗位：{'、'.join(unknown_force[:4])}")
        _, plan = engine.resolve_plan(graph, target_list)
        planned = set(plan)
        applied_force = [item for item in plan if item in set(requested)]
        ignored_force = [item for item in requested if item not in planned]

        if param_overrides is None:
            raw_overrides: dict[str, Any] = {}
        elif isinstance(param_overrides, dict):
            raw_overrides = param_overrides
        else:
            raise ValueError("param_overrides 必须是「岗位 → 参数补丁」的对象")
        patches: dict[str, dict[str, Any]] = {}
        for node_key, patch in raw_overrides.items():
            key = str(node_key)
            if not isinstance(patch, dict):
                raise ValueError(f"岗位 {key} 的改参必须是参数对象")
            if patch:
                patches[key] = dict(patch)
        unknown_override = [item for item in patches if not graph.node(item)]
        if unknown_override:
            raise ValueError(f"画布上没有这些岗位：{'、'.join(unknown_override[:4])}")
        applied_overrides = {item: patches[item] for item in plan if item in patches}
        ignored_overrides = [item for item in patches if item not in planned]
        muted_overrides = [item for item in applied_overrides if graph.node(item).muted]
        if muted_overrides:
            raise ValueError(f"已静音的岗位不能改参运行：{'、'.join(muted_overrides[:4])}")
        effective_graph = graph_model.with_param_overrides(graph, applied_overrides)
        return (
            effective_graph,
            target_list,
            applied_force,
            ignored_force,
            applied_overrides,
            ignored_overrides,
        )

    def start_run(
        self,
        project_id: str,
        tenant: str,
        *,
        targets: list[str] | None = None,
        force: bool = False,
        force_nodes: list[str] | None = None,
        param_overrides: dict[str, Any] | None = None,
        node_id: str = "",
    ) -> dict[str, Any]:
        """受理一次运行。``node_id`` 单独跑一个岗位（含其全部上游闭包）；
        ``force_nodes`` 只推翻计划内指定岗位的缓存，其余仍按签名复用；
        ``param_overrides`` 只在本次运行叠加，不写入项目工作流。"""
        record = self.get(project_id, tenant)
        if record is None:
            raise KeyError(project_id)
        _graph, target_list, applied_force, ignored_force, applied_overrides, ignored_overrides = self._prepare_run(
            record, targets, node_id, force_nodes, param_overrides
        )

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
            "force_nodes": applied_force,
            "ignored_force_nodes": ignored_force,
            "param_overrides": applied_overrides,
            "ignored_param_overrides": ignored_overrides,
            "progress": 0.0,
            "plan_size": 0,
            "counts": {},
            "elapsed_ms": 0,
            "events": [],
            "nodes": {},
            "error": "",
            "result": None,
            # 取消请求只在岗位边界生效；内部标记不落盘，状态本身（cancelling）会落盘
            "_cancel": False,
        }
        with self._lock:
            self._live[job["id"]] = job
        # 落盘用副本，不用活对象：后台线程会就地改 job，边改边序列化会写出半截状态
        self._backend.create(self._copy(job))
        # 受理响应取线程启动前的快照：否则后台线程可能已把状态改成 running，
        # 「已受理」的返回就变成看运气
        accepted = self.public_job(self._copy(job))
        thread = threading.Thread(target=self._worker, args=(job["id"],), daemon=True, name=f"factory-run-{job['id']}")
        with self._lock:
            self._threads[job["id"]] = thread
        thread.start()
        return accepted

    def preview(
        self,
        project_id: str,
        tenant: str,
        *,
        targets: list[str] | None = None,
        force: bool = False,
        force_nodes: list[str] | None = None,
        param_overrides: dict[str, Any] | None = None,
        node_id: str = "",
    ) -> dict[str, Any]:
        """只读运行预检：只算计划与缓存命中，不建作业、不跑岗位、不写缓存。"""
        record = self.get(project_id, tenant)
        if record is None:
            raise KeyError(project_id)
        graph, target_list, applied_force, ignored_force, applied_overrides, ignored_overrides = self._prepare_run(
            record, targets, node_id, force_nodes, param_overrides
        )
        preview = engine.preview_graph(
            graph,
            workspace_for(str(record["id"])),
            targets=target_list or None,
            force=force,
            force_nodes=applied_force,
            param_overrides=applied_overrides,
        )
        preview["ignored_force_nodes"] = ignored_force
        preview["param_overrides"] = applied_overrides
        preview["ignored_param_overrides"] = ignored_overrides
        return preview

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
                "param_overrides": {
                    node_id: dict(patch) for node_id, patch in (job.get("param_overrides") or {}).items()
                },
                "ignored_param_overrides": list(job.get("ignored_param_overrides") or []),
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
        saved_overrides = {
            str(node_id): dict(patch)
            for node_id, patch in (job.get("param_overrides") or {}).items()
            if isinstance(patch, dict)
        }
        missing_overrides = [node_id for node_id in saved_overrides if not graph.node(node_id)]
        applied_overrides = {node_id: patch for node_id, patch in saved_overrides.items() if graph.node(node_id)}
        graph = graph_model.with_param_overrides(graph, applied_overrides)
        workspace = workspace_for(project_id)
        lock = self._project_lock(project_id)
        if not lock.acquire(blocking=False):
            with self._lock:
                job["events"].append({"ts": _now(), "type": "run.wait", "message": "同一项目已有运行在跑，排队等待"})
            lock.acquire()
        try:
            with self._lock:
                # 排队期间已经请求取消：别把它翻回 running，直接交给引擎按取消收口
                if job.get("status") != "cancelling":
                    job["status"] = "running"
            self._touch(job, force=True)

            def on_event(event: dict[str, Any]) -> None:
                kind = str(event.get("type") or "")
                with self._lock:
                    job["events"].append({"ts": _now(), **event})
                    job["events"] = job["events"][-MAX_JOB_EVENTS:]
                    if kind == "run.start":
                        # 目标运行只跑闭包：进度分母必须是实际计划，不是画布全量
                        job["plan_size"] = max(1, len(event.get("plan") or []))
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
                                "node.cancelled": "cancelled",
                            }.get(kind, row.get("status") or "idle")
                            if kind in {"node.finish", "node.cached", "node.error", "node.blocked", "node.skipped", "node.cancelled"}:
                                row["progress"] = 1.0
                    terminal = {"ok", "cached", "error", "blocked", "skipped", "cancelled"}
                    done = sum(1 for item in job["nodes"].values() if item.get("status") in terminal)
                    total = max(1, int(job.get("plan_size") or len(graph.nodes)))
                    job["progress"] = round(min(0.99, done / total), 3)
                self._touch(job)

            if missing_overrides:
                on_event(
                    {
                        "type": "run.override_skipped",
                        "level": "warn",
                        "node_ids": missing_overrides,
                        "message": f"画布已变更，这些岗位不在了，本轮改参未应用：{'、'.join(missing_overrides[:4])}",
                    }
                )
            if applied_overrides:
                labels = [
                    f"{node_id}（{'、'.join(sorted(patch))}）" for node_id, patch in applied_overrides.items()
                ]
                on_event(
                    {
                        "type": "run.override",
                        "level": "info",
                        "node_ids": sorted(applied_overrides),
                        "message": f"本轮改参：{'；'.join(labels[:4])}{'…' if len(labels) > 4 else ''}（不写入工作流）",
                    }
                )

            result = engine.run_graph(
                graph,
                workspace,
                targets=list(job.get("targets") or []) or None,
                force=bool(job.get("force")),
                force_nodes=list(job.get("force_nodes") or []) or None,
                on_event=on_event,
                should_stop=lambda: bool(job.get("_cancel")),
            )
            with self._lock:
                job["counts"] = result.get("counts") or {}
                job["elapsed_ms"] = int(result.get("elapsed_ms") or 0)
                job["progress"] = 1.0
                if result.get("cancelled"):
                    job["status"] = "cancelled"
                elif job.get("status") == "cancelling":
                    # 取消请求没赶上最后一步：按真实结果收尾，不把跑完的一轮说成取消
                    job["status"] = "ok" if result.get("ok") else "error"
                    job["events"].append(
                        {
                            "ts": _now(),
                            "type": "run.cancel_missed",
                            "level": "warn",
                            "message": "取消请求到达时本轮已跑完，按实际结果收尾",
                        }
                    )
                    job["events"] = job["events"][-MAX_JOB_EVENTS:]
                elif job.get("status") != "cancelled":  # 用户取消过就别把它说成跑完了
                    job["status"] = "ok" if result.get("ok") else "error"
                job["result"] = {
                    "ok": bool(result.get("ok")),
                    "counts": job["counts"],
                    "elapsed_ms": job["elapsed_ms"],
                    "stale_nodes": result.get("stale_nodes") or [],
                    "nodes": {
                        node_id: {
                            "type": row.get("type") or "",
                            "title": row.get("title") or "",
                            "status": row.get("status"),
                            "signature": row.get("signature") or "",
                            "summary": row.get("summary"),
                            "error": row.get("error"),
                            "elapsed_ms": row.get("elapsed_ms"),
                            "cached": row.get("cached"),
                            "forced": row.get("forced"),
                            # 只有真正进入执行（started_at 已置）才算「补丁下跑过」；
                            # 缓存命中 / 静音 / 被挡住不能冒充改参执行
                            "overridden": bool(node_id in applied_overrides and row.get("started_at")),
                            "output_types": row.get("output_types") or {},
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
            "force": bool(job.get("force")),
            "force_nodes": list(job.get("force_nodes") or []),
            # 只记岗位名，不把大段参数值写进项目摘要；完整补丁在作业历史里
            "override_nodes": sorted(job.get("param_overrides") or {}),
            "ignored_override_nodes": list(job.get("ignored_param_overrides") or []),
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
        """请求取消：只在岗位边界生效，不硬杀线程（硬杀会留下半截产物）。

        状态先置 ``cancelling`` 并继续落盘；当前岗位收尾后引擎停下，未开工岗位记
        ``cancelled``，后台线程真正落地后才把作业置为 ``cancelled``——
        前端轮询看到的是真实进度，而不是一个立刻「已完成取消」的假终态。
        """
        job = self.job(job_id, tenant)
        if job is None:
            raise KeyError(job_id)
        if job.get("status") in ("queued", "running"):
            with self._lock:
                job["_cancel"] = True
                job["status"] = "cancelling"
                job["events"].append(
                    {
                        "ts": _now(),
                        "type": "run.cancel_requested",
                        "level": "warn",
                        "message": "已请求取消：当前岗位收尾后停止，其余岗位不再执行",
                    }
                )
                job["events"] = job["events"][-MAX_JOB_EVENTS:]
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

    def _project_jobs(self, project_id: str, tenant: str) -> list[dict[str, Any]]:
        """项目名下全部作业（落盘 + 内存中的在跑作业），按创建时间倒序。"""
        rows = [row for row in self._backend.snapshot() if str(row.get("task_id")) == project_id and str(row.get("tenant") or "default") == tenant]
        with self._lock:
            live = [
                job
                for job in self._live.values()
                if str(job.get("task_id")) == project_id and str(job.get("tenant")) == tenant
            ]
        merged = {str(row.get("id")): row for row in rows}
        merged.update({str(job.get("id")): job for job in live})
        return sorted(merged.values(), key=lambda row: str(row.get("created_at") or ""), reverse=True)

    def list_runs(self, project_id: str, tenant: str, *, limit: int = 20) -> list[dict[str, Any]]:
        ordered = self._project_jobs(project_id, tenant)
        return [self.public_job(row, detail=False) for row in ordered[: max(1, limit)]]

    @staticmethod
    def _rate(part: int, whole: int) -> float:
        return round(part / whole, 3) if whole else 0.0

    @staticmethod
    def _p95(values: list[int]) -> int:
        if not values:
            return 0
        ordered = sorted(values)
        return int(ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)])

    @staticmethod
    def _graph_labels(record: dict[str, Any]) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
        """当前画布的岗位标题/类型/部门，供统计与对比补全历史作业里的 node_id。"""
        graph = graph_model.Graph.model_validate(record.get("graph") or {"nodes": [], "edges": []})
        labels: dict[str, dict[str, str]] = {}
        for node in graph.nodes:
            spec = get_spec(node.type)
            labels[node.id] = {
                "title": node.label(),
                "type": node.type,
                "dept": spec.dept if spec else "",
            }
        return labels, {key: label for key, label, _ in DEPARTMENTS}

    def run_stats(self, project_id: str, tenant: str, *, limit: int = 50) -> dict[str, Any]:
        """最近 N 次**已结束**运行的岗位画像：成功率、复用率、耗时与失败热点。

        只读派生，不新增存储：所有数字都能回放到具体作业（``failures`` 带 job_id）。
        成功率按作业算；复用率 = 复用 ÷（交付 + 复用），失败/受阻不稀释它。
        节点标题与部门取自当前画布与岗位注册表，已从画布移除的岗位按 node_id 单列。
        """
        record = self.get(project_id, tenant)
        if record is None:
            raise KeyError(project_id)
        labels, dept_labels = self._graph_labels(record)

        window = [
            job
            for job in self._project_jobs(project_id, tenant)
            if str(job.get("status") or "") in ("ok", "error", "cancelled")
        ][: max(1, limit)]

        run_ok = sum(1 for job in window if job.get("status") == "ok")
        run_cancelled = sum(1 for job in window if job.get("status") == "cancelled")
        run_error = len(window) - run_ok - run_cancelled
        job_elapsed = [int(job.get("elapsed_ms") or 0) for job in window if int(job.get("elapsed_ms") or 0) > 0]
        delivered = sum(int((job.get("counts") or {}).get("ok") or 0) for job in window)
        cached = sum(int((job.get("counts") or {}).get("cached") or 0) for job in window)
        node_errors = sum(int((job.get("counts") or {}).get("error") or 0) for job in window)
        node_blocked = sum(int((job.get("counts") or {}).get("blocked") or 0) for job in window)
        node_cancelled = sum(int((job.get("counts") or {}).get("cancelled") or 0) for job in window)

        nodes: dict[str, dict[str, Any]] = {}
        depts: dict[str, dict[str, Any]] = {}
        failures: list[dict[str, Any]] = []

        def node_row(node_id: str) -> dict[str, Any]:
            row = nodes.get(node_id)
            if row is not None:
                return row
            meta = labels.get(node_id, {})
            dept = str(meta.get("dept") or "")
            row = {
                "node_id": node_id,
                "title": str(meta.get("title") or node_id),
                "type": str(meta.get("type") or ""),
                "dept": dept,
                "dept_label": dept_labels.get(dept, "已移除岗位" if not dept else dept),
                "appearances": 0,
                "ok": 0,
                "cached": 0,
                "error": 0,
                "blocked": 0,
                "cancelled": 0,
                "skipped": 0,
                "cache_rate": 0.0,
                "avg_ms": 0,
                "p95_ms": 0,
                "total_ms": 0,
                "last_error": "",
                "last_failed_at": "",
                "_durations": [],
            }
            nodes[node_id] = row
            return row

        def dept_row(dept: str) -> dict[str, Any]:
            row = depts.get(dept)
            if row is None:
                row = {
                    "dept": dept,
                    "label": dept_labels.get(dept, "已移除岗位" if not dept else dept),
                    "nodes": set(),
                    "appearances": 0,
                    "ok": 0,
                    "cached": 0,
                    "error": 0,
                    "blocked": 0,
                    "total_ms": 0,
                    "avg_ms": 0,
                    "cache_rate": 0.0,
                    "_timed": 0,
                }
                depts[dept] = row
            return row

        for job in window:
            created_at = str(job.get("created_at") or "")
            result_nodes = ((job.get("result") or {}).get("nodes") or {})
            live_nodes = job.get("nodes") or {}
            for node_id in sorted(set(result_nodes) | set(live_nodes)):
                result = result_nodes.get(node_id) or {}
                state = live_nodes.get(node_id) or {}
                status = str(result.get("status") or state.get("status") or "idle")
                is_cached = bool(result.get("cached")) or status == "cached"
                if status not in ("ok", "error", "blocked", "cancelled", "skipped") and not is_cached:
                    continue
                row = node_row(node_id)
                row["appearances"] += 1
                dept = dept_row(row["dept"])
                dept["nodes"].add(node_id)
                dept["appearances"] += 1
                if is_cached:
                    row["cached"] += 1
                    dept["cached"] += 1
                elif status == "ok":
                    row["ok"] += 1
                    dept["ok"] += 1
                elif status == "error":
                    row["error"] += 1
                    dept["error"] += 1
                elif status == "blocked":
                    row["blocked"] += 1
                    dept["blocked"] += 1
                elif status == "cancelled":
                    row["cancelled"] += 1
                else:
                    row["skipped"] += 1
                elapsed = int(result.get("elapsed_ms") or 0)
                if elapsed > 0 and not is_cached:
                    row["_durations"].append(elapsed)
                    row["total_ms"] += elapsed
                    dept["total_ms"] += elapsed
                    dept["_timed"] += 1
                error_text = str(result.get("error") or "")
                if status in ("error", "blocked") and error_text and not row["last_error"]:
                    row["last_error"] = error_text
                    row["last_failed_at"] = created_at
                if status in ("error", "blocked") and len(failures) < 10:
                    failures.append(
                        {
                            "job_id": str(job.get("id") or ""),
                            "created_at": created_at,
                            "node_id": node_id,
                            "title": row["title"],
                            "dept": row["dept"],
                            "dept_label": row["dept_label"],
                            "status": status,
                            "error": error_text,
                        }
                    )

        for row in nodes.values():
            durations = row.pop("_durations")
            executed = row["ok"] + row["error"]
            row["cache_rate"] = self._rate(row["cached"], row["cached"] + row["ok"])
            row["avg_ms"] = int(sum(durations) / len(durations)) if durations else 0
            row["p95_ms"] = self._p95(durations)
            row["executed"] = executed
        for row in depts.values():
            row["nodes"] = len(row["nodes"])
            row["cache_rate"] = self._rate(row["cached"], row["cached"] + row["ok"])
            timed = row.pop("_timed")
            row["avg_ms"] = int(row["total_ms"] / timed) if timed else 0

        ordered_nodes = sorted(nodes.values(), key=lambda row: (-row["total_ms"], row["node_id"]))
        ordered_depts = sorted(depts.values(), key=lambda row: (-row["total_ms"], row["dept"]))
        return {
            "project_id": project_id,
            "window": len(window),
            "generated_at": _now(),
            "runs": {
                "total": len(window),
                "ok": run_ok,
                "error": run_error,
                "cancelled": run_cancelled,
                "success_rate": self._rate(run_ok, len(window)),
                "forced": sum(1 for job in window if job.get("force")),
                "partial_forced": sum(
                    1 for job in window if job.get("force_nodes") and not job.get("force")
                ),
                "overridden": sum(1 for job in window if job.get("param_overrides")),
                "latest_at": str(window[0].get("created_at") or "") if window else "",
                "oldest_at": str(window[-1].get("created_at") or "") if window else "",
                "elapsed_avg_ms": int(sum(job_elapsed) / len(job_elapsed)) if job_elapsed else 0,
                "elapsed_p95_ms": self._p95(job_elapsed),
                "plan_nodes": sum(int(job.get("plan_size") or 0) for job in window),
                "delivered": delivered,
                "cached": cached,
                "cache_rate": self._rate(cached, cached + delivered),
                "node_errors": node_errors,
                "node_blocked": node_blocked,
                "node_cancelled": node_cancelled,
            },
            "nodes": ordered_nodes,
            "departments": ordered_depts,
            "failures": failures,
        }

    #: 对比列表按「先看问题」排序：回退、仍失败、状态变化排在恢复与缓存命中之前
    _COMPARE_RANK = {
        "regressed": 0,
        "still_failed": 1,
        "status_changed": 2,
        "recovered": 3,
        "cache_lost": 4,
        "added": 5,
        "removed": 6,
        "cache_hit": 7,
        "same": 8,
    }

    @staticmethod
    def _okish(status: str) -> bool:
        return status in ("ok", "cached")

    @staticmethod
    def _failish(status: str) -> bool:
        return status in ("error", "blocked", "cancelled")

    @classmethod
    def _change_kind(cls, base: dict[str, Any] | None, target: dict[str, Any] | None) -> str:
        """两次运行里同一岗位的迁移：恢复/回退优先于缓存口径。"""
        if base is None:
            return "added"
        if target is None:
            return "removed"
        before, after = str(base["status"]), str(target["status"])
        if cls._failish(before) and cls._okish(after):
            return "recovered"
        if cls._okish(before) and cls._failish(after):
            return "regressed"
        if cls._failish(before) and cls._failish(after):
            return "still_failed"
        if before == "ok" and after == "cached":
            return "cache_hit"
        if before == "cached" and after == "ok":
            return "cache_lost"
        if before == after:
            return "same"
        return "status_changed"

    @staticmethod
    def _compare_node_rows(job: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """把作业结果归一成可对比的一行；旧作业没有签名/类型时留空，由画布与注册表补。"""
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        result_nodes = result.get("nodes") if isinstance(result.get("nodes"), dict) else {}
        live_nodes = job.get("nodes") if isinstance(job.get("nodes"), dict) else {}
        rows: dict[str, dict[str, Any]] = {}
        for node_id in sorted(set(result_nodes) | set(live_nodes)):
            raw = result_nodes.get(node_id) if isinstance(result_nodes.get(node_id), dict) else {}
            live = live_nodes.get(node_id) if isinstance(live_nodes.get(node_id), dict) else {}
            status = str(raw.get("status") or live.get("status") or "idle")
            cached = bool(raw.get("cached")) or status == "cached"
            if cached:
                status = "cached"
            outputs = raw.get("outputs") if isinstance(raw.get("outputs"), dict) else {}
            digest = (
                hashlib.sha1(
                    json.dumps(outputs, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
                ).hexdigest()[:12]
                if outputs
                else ""
            )
            rows[node_id] = {
                "type": str(raw.get("type") or ""),
                "title": str(raw.get("title") or ""),
                "status": status,
                "cached": cached,
                "elapsed_ms": int(raw.get("elapsed_ms") or 0),
                "summary": str(raw.get("summary") or ""),
                "error": str(raw.get("error") or ""),
                "output_digest": digest,
                "output_ports": sorted(outputs.keys()),
            }
        return rows

    @staticmethod
    def _compare_totals(job: dict[str, Any]) -> dict[str, Any]:
        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        counts = job.get("counts") or result.get("counts") or {}
        totals = {
            key: int(counts.get(key) or 0)
            for key in ("ok", "cached", "error", "blocked", "skipped", "cancelled")
        }
        totals["delivered"] = totals["ok"]
        totals["failed"] = totals["error"] + totals["blocked"] + totals["cancelled"]
        totals["cache_rate"] = FactoryStore._rate(totals["cached"], totals["cached"] + totals["delivered"])
        return totals

    @staticmethod
    def _compare_meta(job: dict[str, Any], nodes: dict[str, Any], totals: dict[str, Any]) -> dict[str, Any]:
        return {
            "job_id": str(job.get("id") or ""),
            "status": str(job.get("status") or ""),
            "created_at": str(job.get("created_at") or ""),
            "elapsed_ms": int(job.get("elapsed_ms") or 0),
            "plan_size": int(job.get("plan_size") or 0),
            "force": bool(job.get("force")),
            "force_nodes": list(job.get("force_nodes") or []),
            "override_nodes": sorted(job.get("param_overrides") or {}),
            "targets": list(job.get("targets") or []),
            "node_count": len(nodes),
            **totals,
        }

    def _compare_job(self, project_id: str, tenant: str, job_id: str, label: str) -> dict[str, Any]:
        """只认本项目的租户可见作业；跨项目按不存在处理，未结束按冲突处理。"""
        job = self.job(job_id, tenant)
        if job is None or str(job.get("project_id") or job.get("task_id") or "") != project_id:
            raise KeyError(job_id)
        status = str(job.get("status") or "")
        if status not in ("ok", "error", "cancelled"):
            raise RunUnfinished(f"{label}运行还没结束（{status or '未知'}），结果还会变；结束后再对比")
        return job

    def compare_runs(self, project_id: str, tenant: str, base_id: str, target_id: str) -> dict[str, Any]:
        """两次**已结束**运行的可回放差异：岗位状态迁移、缓存命中、耗时与产物结构。

        与运行统计一样是纯读派生：不建作业、不写缓存、不碰项目文件。产物只比结构指纹
        （端口、规模、预览），不把完整交付物塞进对比响应——原值仍在 ``node_outputs``。
        """
        if base_id == target_id:
            raise ValueError("基准与目标是同一次运行：换一次运行再对比")
        record = self.get(project_id, tenant)
        if record is None:
            raise KeyError(project_id)
        base_job = self._compare_job(project_id, tenant, base_id, "基准")
        target_job = self._compare_job(project_id, tenant, target_id, "目标")
        labels, dept_labels = self._graph_labels(record)
        base_nodes = self._compare_node_rows(base_job)
        target_nodes = self._compare_node_rows(target_job)

        rows: list[dict[str, Any]] = []
        for node_id in sorted(set(base_nodes) | set(target_nodes)):
            base = base_nodes.get(node_id)
            target = target_nodes.get(node_id)
            meta = labels.get(node_id) or {}
            type_name = str(meta.get("type") or (target or {}).get("type") or (base or {}).get("type") or "")
            spec = get_spec(type_name)
            title = str(
                meta.get("title")
                or (target or {}).get("title")
                or (base or {}).get("title")
                or (spec.title if spec else node_id)
            )
            dept = str(meta.get("dept") or (spec.dept if spec else ""))
            base_digest = str((base or {}).get("output_digest") or "")
            target_digest = str((target or {}).get("output_digest") or "")
            base_summary = str((base or {}).get("summary") or "")
            target_summary = str((target or {}).get("summary") or "")
            base_error = str((base or {}).get("error") or "")
            target_error = str((target or {}).get("error") or "")
            if base is None:
                elapsed_delta = int(target.get("elapsed_ms") or 0)
            elif target is None:
                elapsed_delta = -int(base.get("elapsed_ms") or 0)
            else:
                elapsed_delta = int(target.get("elapsed_ms") or 0) - int(base.get("elapsed_ms") or 0)
            rows.append(
                {
                    "node_id": node_id,
                    "title": title,
                    "type": type_name,
                    "dept": dept,
                    "dept_label": dept_labels.get(dept, "已移除岗位" if not dept else dept),
                    "in_graph": node_id in labels,
                    "base": base,
                    "target": target,
                    "change": self._change_kind(base, target),
                    "elapsed_delta_ms": elapsed_delta,
                    "output_changed": bool(base_digest or target_digest) and base_digest != target_digest,
                    "summary_changed": base_summary != target_summary and bool(base_summary or target_summary),
                    "error_changed": base_error != target_error and bool(base_error or target_error),
                }
            )
        rows.sort(
            key=lambda row: (
                self._COMPARE_RANK.get(row["change"], 9),
                -abs(int(row["elapsed_delta_ms"])),
                row["node_id"],
            )
        )

        changes = {key: 0 for key in self._COMPARE_RANK}
        changes.update({"changed": 0, "output_changed": 0, "summary_changed": 0})
        for row in rows:
            changes[row["change"]] = changes.get(row["change"], 0) + 1
            if row["output_changed"]:
                changes["output_changed"] += 1
            if row["summary_changed"]:
                changes["summary_changed"] += 1
            if (
                row["change"] != "same"
                or row["output_changed"]
                or row["summary_changed"]
                or row["error_changed"]
            ):
                changes["changed"] += 1

        base_totals = self._compare_totals(base_job)
        target_totals = self._compare_totals(target_job)
        base_meta = self._compare_meta(base_job, base_nodes, base_totals)
        target_meta = self._compare_meta(target_job, target_nodes, target_totals)
        return {
            "project_id": project_id,
            "generated_at": _now(),
            "base": base_meta,
            "target": target_meta,
            "delta": {
                "elapsed_ms": target_meta["elapsed_ms"] - base_meta["elapsed_ms"],
                "delivered": target_meta["delivered"] - base_meta["delivered"],
                "cached": target_meta["cached"] - base_meta["cached"],
                "failed": target_meta["failed"] - base_meta["failed"],
                "cache_rate": round(target_meta["cache_rate"] - base_meta["cache_rate"], 3),
            },
            "changes": changes,
            "nodes": rows,
        }

    @staticmethod
    def public_job(job: dict[str, Any], *, detail: bool = True) -> dict[str, Any]:
        """给前端的作业视图：状态 + 进度 + 每节点状态；``detail=True`` 才带事件与结果。

        ``retry_nodes`` 是列表页也要用的续跑目标：失败 / 被上游挡住 / 取消未执行的岗位。
        前端按这些目标调 ``run``，闭包里的上游若已缓存就复用——不必把整张画布重跑一遍。
        """
        nodes = job.get("nodes") or {}
        row = {
            "id": job.get("id"),
            "project_id": job.get("project_id") or job.get("task_id"),
            "status": job.get("status"),
            "progress": round(float(job.get("progress") or 0.0), 3),
            "plan_size": int(job.get("plan_size") or 0),
            "counts": job.get("counts") or {},
            "elapsed_ms": job.get("elapsed_ms") or 0,
            "error": job.get("error") or "",
            "created_at": job.get("created_at"),
            "updated_at": job.get("updated_at"),
            "targets": job.get("targets") or [],
            "force": bool(job.get("force")),
            "force_nodes": job.get("force_nodes") or [],
            "ignored_force_nodes": job.get("ignored_force_nodes") or [],
            "param_overrides": job.get("param_overrides") or {},
            "ignored_param_overrides": job.get("ignored_param_overrides") or [],
            "nodes": nodes,
            "retry_nodes": [
                node_id
                for node_id, state in nodes.items()
                if str((state or {}).get("status") or "") in ("error", "blocked", "cancelled")
            ],
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

#: 验收不过（接口层据此回 409，与 ProjectBusy 同一口径）
AcceptanceBlocked = acceptance_module.AcceptanceBlocked

__all__ = [
    "AcceptanceBlocked",
    "FACTORY_DIR",
    "FactoryStore",
    "INTERRUPTED",
    "JOBS_FILE",
    "ProjectBusy",
    "recover_interrupted",
    "store",
    "workspace_for",
]
