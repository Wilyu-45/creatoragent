"""影视制作工厂 —— REST 接口。

挂载在 ``/api/factory``（见 ``app/server.py``）。这一层只做三件事：
把 HTTP 请求翻成 ``store`` 的方法调用、把 ``KeyError``/``ValueError`` 翻成
404/400、把租户从鉴权中间件读出来传给存储层。业务规则一律不写在这里——
跑图、体检、落盘、作业状态机都在 ``store.py``，接口挂了也不影响它们成立。

产物读取只承认 ``data/factory/<project>/artifacts/`` 之内的文件，且先做租户校验，
与 ``api/routes`` 的导出下载同一口径。
"""

from __future__ import annotations

import mimetypes
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import FileResponse

from . import presets as preset_module
from . import store as store_module
from .graph import Graph, validate_graph
from .nodes import source as source_module

router = APIRouter()

store = store_module.store


def _tenant_of(request: Request) -> str:
    """鉴权中间件写入的租户；未启用鉴权时统一为 ``default``（与 routes.py 同口径）。"""
    return str(getattr(request.state, "tenant", "default") or "default")


def _require_project(project_id: str, request: Request) -> dict[str, Any]:
    """跨租户与不存在都不区分：只说 404，不给探测别人项目的机会。"""
    record = store.get(project_id, _tenant_of(request))
    if record is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return record


def _graph_of(payload: dict[str, Any]) -> Graph:
    try:
        return Graph.model_validate(payload or {"nodes": [], "edges": []})
    except HTTPException:
        raise
    except Exception as error:  # noqa: BLE001 - 画布传来的脏数据要变成可读的 400
        raise HTTPException(status_code=400, detail=f"工作流数据不合法：{error}") from error


# ------------------------------------------------------------------ #
# 岗位目录与预设                                                       #
# ------------------------------------------------------------------ #


@router.get("/palette")
def get_palette() -> dict[str, Any]:
    """左侧面板的数据源：按部门分组的岗位规格（端口、参数、图标）。"""
    return store.palette()


@router.get("/presets")
def get_presets() -> dict[str, Any]:
    return {"presets": preset_module.catalogue()}


@router.get("/presets/{key}")
def get_preset(key: str) -> dict[str, Any]:
    try:
        return preset_module.preset_info(key)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error.args[0] if error.args else "预设不存在")) from error


# ------------------------------------------------------------------ #
# 项目                                                                #
# ------------------------------------------------------------------ #


@router.get("/projects")
def list_projects(request: Request) -> dict[str, Any]:
    return {"projects": store.list(_tenant_of(request))}


@router.post("/projects")
def create_project(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    tenant = _tenant_of(request)
    graph: dict[str, Any] | None = None
    preset_key = str(payload.get("preset") or "").strip()
    if preset_key:
        if preset_key not in preset_module.PRESETS:
            raise HTTPException(status_code=400, detail=f"未知预设：{preset_key}")
        # 预设里的「参考素材」要吃本机真实文件：先把示例色板播种进 data/assets
        source_module.ensure_demo_asset()
        graph = preset_module.graph_dict(preset_key)
    elif payload.get("graph") is not None:
        graph = _graph_of(payload["graph"]).to_dict()
    try:
        record = store.create(str(payload.get("title") or ""), tenant, graph=graph, note=str(payload.get("note") or ""))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"project": record, "issues": [issue.model_dump() for issue in validate_graph(_graph_of(record["graph"]))]}


@router.get("/projects/{project_id}")
def get_project(project_id: str, request: Request) -> dict[str, Any]:
    record = _require_project(project_id, request)
    graph = _graph_of(record.get("graph") or {})
    return {"project": record, "issues": [issue.model_dump() for issue in validate_graph(graph)]}


@router.put("/projects/{project_id}/graph")
def save_graph(
    project_id: str,
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    try:
        return store.save_graph(project_id, _tenant_of(request), _graph_of(payload.get("graph") or payload).to_dict() | {
            "title": payload.get("title"),
            "note": payload.get("note"),
        })
    except KeyError as error:
        raise HTTPException(status_code=404, detail="项目不存在") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.post("/projects/{project_id}/validate")
def validate_draft(project_id: str, request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """画布实时体检：校验尚未保存的草稿，不写盘。"""
    _require_project(project_id, request)
    graph = _graph_of(payload.get("graph") or payload)
    return {"issues": [issue.model_dump() for issue in validate_graph(graph)]}


@router.patch("/projects/{project_id}")
def rename_project(project_id: str, request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    title = str(payload.get("title") or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="标题为空")
    try:
        return {"project": store.rename(project_id, _tenant_of(request), title)}
    except KeyError as error:
        raise HTTPException(status_code=404, detail="项目不存在") from error


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, request: Request) -> dict[str, Any]:
    try:
        return store.delete(project_id, _tenant_of(request))
    except KeyError as error:
        raise HTTPException(status_code=404, detail="项目不存在") from error
    except store_module.ProjectBusy as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


# ------------------------------------------------------------------ #
# 运行                                                                #
# ------------------------------------------------------------------ #


@router.post("/projects/{project_id}/run")
def run_project(
    project_id: str,
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """受理一次跑图：整图，或 ``targets``/``node_id`` 指定的岗位闭包。

    ``force_nodes`` 只推翻计划内指定岗位的缓存（``force`` 仍是整轮强制作业）；
    ``param_overrides`` 只在本次运行叠加，不写入项目工作流，计划外的岗位如实忽略。
    """
    node_id = str(payload.get("node_id") or "").strip()
    # 不能用 ``or {}``：[] 会被静默当成空对象，畸形请求必须由 store 点名回 400
    param_overrides = payload["param_overrides"] if "param_overrides" in payload else None
    try:
        job = store.start_run(
            project_id,
            _tenant_of(request),
            targets=[str(item) for item in (payload.get("targets") or [])],
            force=bool(payload.get("force")),
            force_nodes=[str(item) for item in (payload.get("force_nodes") or [])],
            param_overrides=param_overrides,
            node_id=node_id,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="项目不存在") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"job": job}


@router.post("/projects/{project_id}/preview")
def preview_project(
    project_id: str,
    request: Request,
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    """只读预检：本轮会复用哪些缓存、哪些岗位会重跑、为什么。

    ``force_nodes`` 与 ``param_overrides`` 都与实跑同口径；计划外的岗位会在
    ``ignored_force_nodes`` / ``ignored_param_overrides`` 如实列出。
    """
    node_id = str(payload.get("node_id") or "").strip()
    param_overrides = payload["param_overrides"] if "param_overrides" in payload else None
    try:
        preview = store.preview(
            project_id,
            _tenant_of(request),
            targets=[str(item) for item in (payload.get("targets") or [])],
            force=bool(payload.get("force")),
            force_nodes=[str(item) for item in (payload.get("force_nodes") or [])],
            param_overrides=param_overrides,
            node_id=node_id,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail="项目不存在") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"preview": preview}


@router.get("/projects/{project_id}/runs")
def list_runs(project_id: str, request: Request, limit: int = Query(20, ge=1, le=100)) -> dict[str, Any]:
    _require_project(project_id, request)
    return {"runs": store.list_runs(project_id, _tenant_of(request), limit=limit)}


@router.get("/projects/{project_id}/stats")
def project_stats(project_id: str, request: Request, limit: int = Query(50, ge=1, le=100)) -> dict[str, Any]:
    """最近 N 次已结束运行的岗位画像；只读派生，不改作业与缓存。"""
    _require_project(project_id, request)
    try:
        return {"stats": store.run_stats(project_id, _tenant_of(request), limit=limit)}
    except KeyError as error:
        raise HTTPException(status_code=404, detail="项目不存在") from error


@router.get("/projects/{project_id}/runs/compare")
def compare_runs(
    project_id: str,
    request: Request,
    base: str = Query(..., min_length=1),
    target: str = Query(..., min_length=1),
) -> dict[str, Any]:
    """两次已结束运行的可回放差异；只读派生，不改作业与缓存。"""
    _require_project(project_id, request)
    try:
        return {"compare": store.compare_runs(project_id, _tenant_of(request), base, target)}
    except store_module.RunUnfinished as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail="运行记录不存在") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request) -> dict[str, Any]:
    job = store.job(job_id, _tenant_of(request))
    if job is None:
        raise HTTPException(status_code=404, detail="运行记录不存在")
    return {"job": store.public_job(job)}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, request: Request) -> dict[str, Any]:
    try:
        return {"job": store.cancel(job_id, _tenant_of(request))}
    except KeyError as error:
        raise HTTPException(status_code=404, detail="运行记录不存在") from error


# ------------------------------------------------------------------ #
# 产物                                                                #
# ------------------------------------------------------------------ #


@router.get("/projects/{project_id}/outputs/{node_id}")
def get_node_outputs(project_id: str, node_id: str, request: Request) -> dict[str, Any]:
    """岗位最近一次成功交付的完整产物（作业接口只回摘要，放映室与面板要真数据）。"""
    try:
        return store.node_outputs(project_id, _tenant_of(request), node_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=f"该岗位还没有成功交付的产物：{node_id}") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@router.get("/projects/{project_id}/artifacts")
def list_artifacts(project_id: str, request: Request) -> dict[str, Any]:
    """产物清单（分组 + 大小 + 可直接访问的 URL），供交付面板与放映室取用。"""
    record = _require_project(project_id, request)
    workspace = store_module.workspace_for(str(record["id"]))
    root = workspace.artifacts_dir
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")) if root.is_dir() else []:
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        rows.append(
            {
                "rel": rel,
                "group": rel.split("/", 1)[0],
                "name": path.name,
                "size_kb": round(path.stat().st_size / 1024.0, 1),
                "url": workspace.url(rel),
            }
        )
    groups: dict[str, int] = {}
    for row in rows:
        groups[row["group"]] = groups.get(row["group"], 0) + 1
    return {"artifacts": rows, "groups": groups, "count": len(rows)}


@router.get("/projects/{project_id}/acceptance")
def get_acceptance(project_id: str, request: Request) -> dict[str, Any]:
    """交付验收（只读）：终端岗位、产物引用文件、时间轴与占位标记。

    只把已经在画布与磁盘上的事实量成报告，不跑图也不改缓存；同一个报告也是
    交付包的放行条件，所以面板上看到的与 zip 被拦下时后端算的是同一口径。
    """
    record = _require_project(project_id, request)
    return {"acceptance": store.acceptance(str(record["id"]), _tenant_of(request))}


@router.get("/projects/{project_id}/film")
def get_film(project_id: str, request: Request) -> dict[str, Any]:
    """成片时间轴（只读）：放映室直接回看的那条片子。

    与验收报告的 ``film``、交付包里的 ``timeline.json`` 同一口径，一次取回就免去前端
    逐岗位拉取。没跑过或无成片时间轴时 ``film/timeline`` 都为 null。
    """
    record = _require_project(project_id, request)
    return store.film(str(record["id"]), _tenant_of(request))


@router.get("/projects/{project_id}/deliveries")
def get_deliveries(project_id: str, request: Request) -> dict[str, Any]:
    """交付台账（只读、新的在前）：每次打包的放行/拦截留痕。

    与验收报告不同口径：验收回答「现在能不能交」，台账回答「交过没、何时、
    当时量出什么结论」。只读派生自项目目录里的 deliveries.json，不新建作业、不改缓存。
    """
    record = _require_project(project_id, request)
    return {"deliveries": store.delivery_log(str(record["id"]), _tenant_of(request))}


@router.get("/projects/{project_id}/bundle")
def download_bundle(project_id: str, request: Request) -> FileResponse:
    """工作流 + 全部产物打成一个 zip 交付包（验收未过、没产物或还在跑时拒绝）。"""
    record = _require_project(project_id, request)
    try:
        archive, name = store.build_bundle(str(record["id"]), _tenant_of(request))
    except (store_module.ProjectBusy, ValueError) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"交付包打包失败：{error}") from error
    return FileResponse(archive, media_type="application/zip", filename=name)


#: guess_type 对配音/字幕这类产物给不出浏览器想内联显示的类型，这里补上
ARTIFACT_TYPES = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".srt": "text/plain",
    ".ass": "text/plain",
    ".vtt": "text/plain",
    ".edl": "text/plain",
    ".json": "application/json",
}


@router.get("/projects/{project_id}/artifacts/{rel:path}")
def get_artifact(project_id: str, rel: str, request: Request, download: bool = Query(False)) -> FileResponse:
    record = _require_project(project_id, request)
    try:
        path = store.artifact_path(str(record["id"]), rel)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    media_type = ARTIFACT_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name if download else None)


__all__ = ["router"]
