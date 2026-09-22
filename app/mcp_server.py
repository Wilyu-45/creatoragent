"""Creator Agent Studio —— MCP 服务器（让 harness / agent 把本项目当插件用）。

Model Context Protocol 是 agent harness（DeepSeek harness、Claude Code、Trae、
Cursor 等）接入外部工具的事实标准。本模块把创作流水线的核心操作暴露为 9 个
MCP 工具，全部**代理到运行中的 Creator REST 服务**——状态唯一归属服务进程，
harness 起的任务在 Web 界面同步可见，鉴权与租户判定也全部复用上游。

两种传输方式（同一套工具实现）：

- stdio（默认，本机 harness 以子进程拉起）::

      python -m app.mcp_server

  要求 Creator 服务已启动（``python -m app.main``）。上游地址用
  ``CREATOR_MCP_BASE_URL`` 覆盖（默认 ``http://127.0.0.1:8787``）。

- streamable-http（远程 harness 直接连 URL）::

      python -m app.mcp_server --http [--host 0.0.0.0] [--port 8766]

  本进程只透传请求携带的 Authorization / X-API-Token 头，租户判定由上游
  完成；**暴露到本机以外前务必给上游服务配置 CREATOR_API_TOKENS**（与
  REST API 同一防线，见 .env.example）。

harness 配置示例（mcpServers JSON）见 USER_GUIDE.md「MCP 接入」。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

# ------------------------------------------------------------------ #
# 进程配置（与 app.config 同约定：进程环境变量优先，其次项目根 .env）  #
# ------------------------------------------------------------------ #

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env", override=False)

#: 上游 Creator 服务的根地址（不含 /api 后缀）
BASE_URL = ((os.environ.get("CREATOR_MCP_BASE_URL") or "") or "http://127.0.0.1:8787").rstrip("/")
#: stdio 模式下可选择的固定令牌（HTTP 模式优先透传请求自带的凭据）
MCP_TOKEN = (os.environ.get("CREATOR_MCP_TOKEN") or "").strip()
MCP_HOST = (os.environ.get("CREATOR_MCP_HOST") or "127.0.0.1").strip()
MCP_PORT = int((os.environ.get("CREATOR_MCP_PORT") or "").strip() or 8766)

#: creator_wait_task 轮询的终止状态（与编排器状态机对齐）
_TERMINAL_STATUS = ("completed", "failed", "rejected", "awaiting_approval")


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


# ------------------------------------------------------------------ #
# 上游 HTTP 访问                                                       #
# ------------------------------------------------------------------ #


async def _forward_headers(ctx: Any) -> dict[str, str]:
    """组装转发给上游的鉴权头。

    HTTP 传输时优先透传调用方请求自带的 Authorization / X-API-Token
    （租户隔离随之生效）；两者都没有时回落到 CREATOR_MCP_TOKEN。
    """
    headers: dict[str, str] = {}
    try:
        raw = ctx.headers if ctx is not None else None
    except Exception:  # noqa: BLE001 - stdio 传输下 Context 无 headers
        raw = None
    if raw:
        for name in ("authorization", "x-api-token"):
            value = raw.get(name)
            if value:
                headers[name] = value
    if not headers and MCP_TOKEN:
        headers["X-API-Token"] = MCP_TOKEN
    return headers


async def _request(
    method: str,
    path: str,
    ctx: Any,
    *,
    json_body: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> Any:
    """调用上游 REST API；非 2xx 一律抛错（错误信息就是给 LLM 看的排障提示）。"""
    url = BASE_URL + path
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=5.0)) as client:
            resp = await client.request(method, url, json=json_body, headers=await _forward_headers(ctx))
    except httpx.ConnectError as error:
        raise RuntimeError(
            f"无法连接 Creator 服务 {BASE_URL} —— 请先启动服务：python -m app.main"
        ) from error
    except httpx.HTTPError as error:
        raise RuntimeError(f"调用 Creator 服务失败：{error}") from error

    if resp.status_code >= 400:
        detail = ""
        try:
            payload = resp.json()
            detail = payload.get("detail") if isinstance(payload, dict) else ""
        except Exception:  # noqa: BLE001
            detail = resp.text[:200]
        raise RuntimeError(f"Creator API {method} {path} → HTTP {resp.status_code}：{detail or '无详情'}")
    if not resp.content:
        return {}
    try:
        return resp.json()
    except ValueError:
        return {"text": resp.text}


def _snapshot(task: dict[str, Any]) -> dict[str, Any]:
    """任务的紧凑快照：agent 等待/决策所需的全部状态，不携带大产物正文。"""
    scorecard = task.get("scorecard")
    return {
        "id": task.get("id"),
        "status": task.get("status"),
        "phase": task.get("phase"),
        "phase_label": task.get("phase_label"),
        "revision_round": task.get("revision_round"),
        "turn_used": task.get("turn_used"),
        "overall_score": (scorecard or {}).get("overall") if isinstance(scorecard, dict) else None,
        "artifact_count": task.get("artifact_count"),
        "approval": task.get("approval"),
        "error": task.get("error"),
    }


def _compact_task(data: dict[str, Any], *, include_events: bool) -> dict[str, Any]:
    """GET /api/tasks/{id} 全量响应 → 紧凑结构（产物只留标题与截断正文）。"""
    task = data.get("task") or data
    compact: dict[str, Any] = _snapshot(task)
    artifacts = []
    for artifact in task.get("artifacts") or []:
        text = str(artifact.get("text") or "")
        artifacts.append(
            {
                "id": artifact.get("id"),
                "type": artifact.get("type"),
                "title": artifact.get("title"),
                "version": artifact.get("version"),
                "text": text[:2000] + ("…（截断，全文见 creator_export）" if len(text) > 2000 else ""),
            }
        )
    compact["artifacts"] = artifacts
    if include_events:
        events = data.get("events") or []
        compact["recent_events"] = [
            {"seq": e.get("seq"), "kind": e.get("kind"), "message": e.get("message")}
            for e in events[-20:]
        ]
    return compact


# ------------------------------------------------------------------ #
# MCP 服务器与工具                                                     #
# ------------------------------------------------------------------ #

from mcp.server.mcpserver import Context, MCPServer  # noqa: E402 - 配置就绪后再 import

mcp = MCPServer(
    name="creator",
    instructions=(
        "Creator Agent Studio：多智能体内容创作流水线（策略→创意→策划→文案→审校→事实核查→"
        "合规→视觉→渠道→评估→人工审批→发布→复盘）。典型用法：creator_create_task 提交 Brief → "
        "反复 creator_wait_task 等到 status 变为 awaiting_approval 或 completed → "
        "awaiting_approval 时用 creator_decide(approve/revise/reject) 拍板 → "
        "creator_export 取成品全文。creator_search_memory 可先召回历史知识辅助撰写 Brief。"
    ),
)


@mcp.tool()
async def creator_health(ctx: Context = None) -> dict[str, Any]:  # type: ignore[assignment]
    """服务健康检查：上游是否可达、当前 LLM 提供方（mock 为内置离线引擎）、门禁阈值。

    首次接入时先调用本工具确认配置，再创建任务。
    """
    data = await _request("GET", "/api/health", ctx)
    provider = data.get("provider") or {}
    config = data.get("config") or {}
    return {
        "ok": data.get("ok"),
        "upstream": BASE_URL,
        "llm_provider": provider.get("name"),
        "llm_model": provider.get("model"),
        "simulated": provider.get("simulated"),
        "checkpointer": (data.get("checkpointer") or {}).get("kind"),
        "quality_threshold": config.get("qualityThreshold"),
        "max_revisions": config.get("maxRevisions"),
        "turn_budget": config.get("turnBudget"),
    }


@mcp.tool()
async def creator_list_tasks(limit: int = 20, ctx: Context = None) -> dict[str, Any]:  # type: ignore[assignment]
    """列出当前租户的最近任务（紧凑快照，新任务在前）。

    Args:
        limit: 最多返回多少条（1-100）。
    """
    data = await _request("GET", "/api/tasks", ctx)
    tasks = data.get("tasks") or []
    limit_i = int(_clamp(limit, 1, 100))
    return {"count": len(tasks), "tasks": [_snapshot(t) for t in tasks[:limit_i]]}


@mcp.tool()
async def creator_create_task(
    brief: dict[str, Any], auto_approve: bool = False, ctx: Context = None  # type: ignore[assignment]
) -> dict[str, Any]:
    """提交创作 Brief，启动流水线。返回 task_id，用 creator_wait_task 等待进展。

    Args:
        brief: Brief 字段（均可选，建议至少给 brand/objective）：
            brand 品牌 | product 产品 | objective 创作目标 | audience 受众 |
            channel 渠道（如 小红书/抖音/公众号） | tone 语气 | industry 行业 |
            language 语言（zh/en/ja/ko/es） | keywords 关键词（字符串或数组） |
            constraints 约束（字符串或数组，可放长素材原文，每条一个字符串） |
            deliverables 交付物要求 | notes 补充说明 |
            priority low/normal/high/urgent | deadline 截止时间 |
            assets 素材清单（[{kind: image/video/document/link, ref, title, note}]）
        auto_approve: true 时跳过最终人工审批（质量门禁仍然生效）。

    Returns:
        task_id 与初始状态。后续用 creator_wait_task 轮询。
    """
    data = await _request(
        "POST",
        "/api/tasks",
        ctx,
        json_body={"brief": brief, "autoApprove": bool(auto_approve)},
        timeout=30.0,
    )
    task = data.get("task") or {}
    return {
        "task_id": task.get("id"),
        "status": task.get("status"),
        "phase_label": task.get("phase_label"),
        "note": "任务已创建并后台执行。调用 creator_wait_task 等待其到达 awaiting_approval 或终态。",
    }


@mcp.tool()
async def creator_get_task(
    task_id: str,
    include_events: bool = False,
    verbose: bool = False,
    ctx: Context = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """读取任务详情：状态、评分卡、产物列表（紧凑模式默认只留产物标题与截断正文）。

    Args:
        task_id: 任务 ID（creator_create_task 返回）。
        include_events: 附带最近 20 条流水线事件（排障用）。
        verbose: true 时返回上游全量结构（含事件与黑板快照，体积大）。
    """
    data = await _request("GET", f"/api/tasks/{task_id}", ctx)
    if verbose:
        return data
    return _compact_task(data, include_events=include_events)


@mcp.tool()
async def creator_wait_task(
    task_id: str,
    timeout_seconds: float = 120,
    interval_seconds: float = 5,
    ctx: Context = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """轮询等待任务到达 awaiting_approval（等人工拍板）或终态（completed/failed/rejected）。

    流水线是分钟级的：本工具最多阻塞 timeout_seconds 秒；超时返回
    finished=false 的进度快照，**稍后再次调用本工具继续等待即可**。

    Args:
        task_id: 任务 ID。
        timeout_seconds: 本次调用最长等待秒数（5-1800，默认 120）。
        interval_seconds: 轮询间隔秒数（2-30，默认 5）。
    """
    timeout = _clamp(float(timeout_seconds), 5, 1800)
    interval = _clamp(float(interval_seconds), 2, 30)
    deadline = time.monotonic() + timeout
    while True:
        data = await _request("GET", f"/api/tasks/{task_id}", ctx, timeout=15.0)
        task = data.get("task") or data
        status = task.get("status")
        snapshot = _snapshot(task)
        if status in _TERMINAL_STATUS:
            snapshot["finished"] = True
            if status == "awaiting_approval":
                snapshot["note"] = "任务挂起等待人工拍板：用 creator_decide(approve/revise/reject) 决策"
            return snapshot
        if time.monotonic() >= deadline:
            snapshot["finished"] = False
            snapshot["note"] = "仍在执行中，稍后再次调用 creator_wait_task 继续等待"
            return snapshot
        await asyncio.sleep(interval)


@mcp.tool()
async def creator_decide(
    task_id: str, decision: str, comment: str = "", ctx: Context = None  # type: ignore[assignment]
) -> dict[str, Any]:
    """人工裁决：对挂起中的任务拍板（门禁升级与最终审批都用它）。

    Args:
        task_id: 任务 ID（须处于 awaiting_approval）。
        decision: approve=通过放行 / revise=退回返工（附 comment 说明问题）/ reject=否决。
        comment: 给流水线的裁决意见（revise 时会驱动下一轮修订）。

    Returns:
        裁决后的任务快照；若触发新一轮返工，继续用 creator_wait_task 等待。
    """
    data = await _request(
        "POST",
        f"/api/tasks/{task_id}/decide",
        ctx,
        json_body={"decision": decision, "comment": comment},
    )
    return _snapshot(data.get("task") or {})


@mcp.tool()
async def creator_export(task_id: str, ctx: Context = None) -> str:  # type: ignore[assignment]
    """下载审批通过后的成品 txt 全文（全部版本，连载类内容不丢篇）。

    仅在任务 completed 且交付环节已导出后可用；尚未导出会返回明确错误。
    """
    url = BASE_URL + f"/api/tasks/{task_id}/export"
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0)) as client:
            resp = await client.get(url, headers=await _forward_headers(ctx))
    except httpx.ConnectError as error:
        raise RuntimeError(
            f"无法连接 Creator 服务 {BASE_URL} —— 请先启动服务：python -m app.main"
        ) from error
    if resp.status_code >= 400:
        raise RuntimeError(
            f"导出失败（HTTP {resp.status_code}）：任务可能尚未完成审批，"
            "先用 creator_get_task 确认 status=completed"
        )
    return resp.text


@mcp.tool()
async def creator_search_memory(
    query: str,
    brand: str = "",
    channel: str = "",
    industry: str = "",
    top_k: int = 5,
    ctx: Context = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """按自然语言检索 A11 记忆库的历史知识卡片（与智能体召回同源，按租户隔离）。

    撰写 Brief 前调用可复用历史最佳实践（爆款结构、禁用教训、渠道经验）。

    Args:
        query: 自然语言描述，如「小红书美妆新品起量文案结构」。
        brand/channel/industry: 可选过滤维度。
        top_k: 返回条数（1-20）。
    """
    return await _request(
        "POST",
        "/api/memory/search",
        ctx,
        json_body={
            "query": query,
            "brand": brand,
            "channel": channel,
            "industry": industry,
            "topK": int(_clamp(top_k, 1, 20)),
        },
    )


@mcp.tool()
async def creator_trace(task_id: str, ctx: Context = None) -> dict[str, Any]:  # type: ignore[assignment]
    """任务的调用轨迹摘要：span 聚合排行（哪一层最贵）、总耗时与调用次数。排障用。"""
    data = await _request("GET", f"/api/tasks/{task_id}/trace", ctx)
    summary = data.get("summary") or {}
    return {
        "task_id": task_id,
        "trace_id": data.get("trace_id"),
        "span_count": summary.get("span_count"),
        "duration_ms": summary.get("duration_ms"),
        "top_spans": (summary.get("by_name") or [])[:12],
        "notes": data.get("notes"),
    }


# ------------------------------------------------------------------ #
# 入口                                                                 #
# ------------------------------------------------------------------ #


def main() -> None:
    argv = sys.argv[1:]
    if "--http" in argv:
        host = MCP_HOST
        port = MCP_PORT
        if "--host" in argv:
            host = argv[argv.index("--host") + 1]
        if "--port" in argv:
            port = int(argv[argv.index("--port") + 1])
        print(
            f"Creator MCP(streamable-http) → 上游 {BASE_URL}，监听 http://{host}:{port}/mcp",
            file=sys.stderr,
        )
        mcp.run("streamable-http", host=host, port=port, stateless_http=True)
    else:
        print(f"Creator MCP(stdio) → 上游 {BASE_URL}", file=sys.stderr)
        mcp.run("stdio")


if __name__ == "__main__":
    main()
