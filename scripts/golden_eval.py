"""黄金数据集回归：跑固定 Brief 集，把分数与基线对比，判定「改动是变好还是变差」。

它补的是哪一环
--------------
`doctor.py` 验证「契约没坏」，`smoke_api.py` 验证「接口都对」，`stress_llm.py` 量「并发与延迟」，
但没有一个能回答「我把 Prompt 改了，质量是变好了还是变差了」——因为每次输入的 Brief 不同，
分数差异分不清是改进还是噪声。本脚本把输入固定，把输出压缩成标量，于是这个问题可判定。

它怎么跑
--------
拉一个真实服务实例（自带 ``AUTO_APPROVE=true``，跳过人工裁决），
通过 ``POST /api/golden/run`` 触发**服务端**的后台执行，再轮询进度。
执行逻辑与 Web 界面「跑一次回归」完全同源，避免两边对「什么算通过」产生分歧。

用法::

    python scripts/golden_eval.py                    # 跑全部用例并与基线对比
    python scripts/golden_eval.py -n 3               # 只跑前 3 条（快速自查）
    python scripts/golden_eval.py --case douyin_short_video
    python scripts/golden_eval.py --update-baseline  # 把本次结果写为新基线
    python scripts/golden_eval.py --coverage         # 只看渠道×行业覆盖矩阵
    python scripts/golden_eval.py --tolerance 5      # 放宽回归判定容差

退出码：0 = 无回归；1 = 存在回归/失败/用例缺失或运行异常。

关于 ``--update-baseline`` 的使用纪律
------------------------------------
更新基线等于「把当前分数认定为新的合格线」，因此它必须是一次**有意识的决定**：
只看 ``regressed`` 列表、确认每条回退都能解释，再更新。
脚本会在存在回归时打印警告并要求 ``--force``，避免顺手把回归固化成基线。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.util import force_offline_provider, free_port, make_temp_dir  # noqa: E402

#: 端口交给系统挑（Windows 会保留成片端口区间，写死会在个别机器上 bind 失败）
PORT = free_port(8811)
BASE = f"http://127.0.0.1:{PORT}"

VERDICT_LABEL = {
    "ok": "持平",
    "improved": "提升",
    "regressed": "回退",
    "new": "新增",
    "missing": "缺失",
    "failed": "失败",
}

#: CaseResult 的全部字段名，用于把接口返回的 JSON 还原成对象
_CASE_FIELDS = {
    "id", "status", "quality_score", "judge_total", "judge_final_total", "axis_scores",
    "revision_round", "artifact_count", "fact_accuracy", "brand_consistency",
    "compliance_verdicts", "first_round_blocked", "predicted_ctr", "duration_ms",
    "task_id", "error", "checks", "delivered_text", "artifact_types",
    "video_shot_count", "video_voiceover_count", "video_timeline_ok",
}


def _to_cases(results: list[dict[str, Any]]) -> list[Any]:
    """把 ``/api/golden`` 返回的结果 JSON 还原成 ``CaseResult``。

    只挑已知字段：接口未来新增字段时这里不会因为「意外的关键字参数」而崩掉。
    """
    from app.core.golden import CaseResult

    return [
        CaseResult(**{key: value for key, value in item.items() if key in _CASE_FIELDS})
        for item in results
    ]


def call(method: str, path: str, **kwargs: Any) -> httpx.Response:
    with httpx.Client(base_url=BASE, timeout=120.0) as client:
        return client.request(method, path, **kwargs)


def wait_for(predicate, timeout: float, label: str) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if predicate():
                return True
        except Exception:  # noqa: BLE001 - 轮询期间的瞬态错误直接重试
            pass
        time.sleep(0.4)
    print(f"  ! 超时：{label}")
    return False


def print_coverage(data: dict[str, Any]) -> None:
    cov = data["coverage"]
    print(f"用例总数 {cov['total']}｜覆盖渠道 {len(cov['channels'])}｜覆盖行业 {len(cov['industries'])}")
    print(f"{'渠道':<12}{'用例':<28}{'覆盖行业'}")
    print("-" * 72)
    for row in cov["matrix"]:
        print(f"{row['channel']:<12}{'、'.join(row['cases']):<28}{'、'.join(row['industries'])}")
    if cov["uncovered_channels"]:
        print(f"\n! 尚未覆盖的渠道：{'、'.join(cov['uncovered_channels'])}")
    else:
        print("\n所有渠道均已至少覆盖一条用例。")


def print_comparison(comparison: dict[str, Any], results: list[dict[str, Any]]) -> None:
    by_id = {item["id"]: item for item in results}
    print(f"\n{'用例':<28}{'状态':<8}{'质量分':>8}{'评估分':>8}{'返工':>6}{'产物':>6}  结论")
    print("-" * 96)
    for diff in comparison["differences"]:
        result = by_id.get(diff["id"], {})
        quality = f"{result.get('quality_score', 0):.1f}" if result else "—"
        judge = f"{result.get('judge_final_total', 0):.1f}" if result else "—"
        revisions = f"{result.get('revision_round', 0):.0f}" if result else "—"
        artifacts = f"{result.get('artifact_count', 0):.0f}" if result else "—"
        label = VERDICT_LABEL.get(diff["verdict"], diff["verdict"])
        detail = "；".join(diff["reasons"][:2])
        print(
            f"{diff['id']:<28}{label:<8}{quality:>8}{judge:>8}{revisions:>6}{artifacts:>6}  {detail}"
        )

    counts = comparison["counts"]
    print(
        f"\n汇总：{counts.get('ok', 0)} 持平｜{counts.get('improved', 0)} 提升｜"
        f"{counts.get('regressed', 0)} 回退｜{counts.get('new', 0)} 新增｜"
        f"{counts.get('missing', 0)} 缺失｜{counts.get('failed', 0)} 失败"
    )

    # 内容级期望：分数之外的硬约束（品牌名 / 关键词 / 阻断用语 / 标题字数）
    checked = [item for item in results if item.get("checks")]
    failed_total = sum(int(item.get("checks_failed") or 0) for item in checked)
    violations = sum(len(item.get("checks") or []) for item in checked)
    print(f"\n内容级期望：{len(checked)} 条用例共 {violations} 项断言，未通过 {failed_total} 项")
    if checked:
        sample = next((item for item in checked if item.get("checks")), None)
        if sample:
            for item in sample["checks"][:6]:
                mark = "✓" if item["ok"] else "✗"
                print(f"    {mark} {item['rule']}：{item['detail']}")
    for item in checked:
        for check_item in item.get("checks") or []:
            if not check_item.get("ok"):
                print(f"    ✗ [{item['id']}] {check_item['rule']}：{check_item['detail']}")

    if comparison.get("partial"):
        print(f"\n注意：这是**部分运行**（范围 {len(comparison.get('scope') or [])} 条用例），"
              "结论仅对范围内用例成立；发布前请跑一次全量。")
    if comparison["rubric_mismatch"]:
        print(
            f"! 口径不一致：基线 rubric={comparison['baseline_rubric']}，"
            f"当前 rubric={comparison['rubric']} —— 分数不可直接比较，请先评估是否需要重建基线"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="黄金数据集回归")
    parser.add_argument("-n", "--limit", type=int, default=None, help="只跑前 N 条用例")
    parser.add_argument("--case", action="append", default=None, help="只跑指定用例 id（可多次）")
    parser.add_argument("--tolerance", type=float, default=None, help="回归判定容差（默认取后端值）")
    parser.add_argument("--update-baseline", action="store_true", help="把本次结果写为新基线")
    parser.add_argument("--force", action="store_true", help="存在回归时仍允许更新基线")
    parser.add_argument("--coverage", action="store_true", help="只打印覆盖矩阵后退出")
    parser.add_argument("--timeout", type=float, default=900.0, help="整体等待上限（秒）")
    parser.add_argument("--port", type=int, default=PORT, help="服务端口")
    args = parser.parse_args()

    global BASE
    BASE = f"http://127.0.0.1:{args.port}"

    data_dir = make_temp_dir("golden-", base=ROOT / ".doctor-data")
    # ⚠️ 必须在构造 env 之前调用：它写的是 os.environ，
    # 而下面的 env 是从 os.environ 展开出来的 —— 顺序反了就白设了。
    provider = force_offline_provider()
    print(f"验证提供方：{provider}（真实链路请用 scripts/real_check.py）")
    env = {
        **os.environ,
        "CREATOR_DATA_DIR": str(data_dir),
        "PORT": str(args.port),
        # 跳过人工裁决：黄金回归关注的是自动链路的质量，不是审批交互
        "AUTO_APPROVE": "true",
        "PYTHONNOUSERSITE": "1",
        "LLM_PROVIDER": provider,
        # 编排预算同样必须钉死（与 .env.example 默认一致）：.env 的临时调参若泄漏，
        # 返工轮次会跟着变，曾把「返工 ≤2」期望的失败检查固化进基线
        "TURN_BUDGET": "25",
        "MAX_REVISIONS": "2",
        "QUALITY_THRESHOLD": "75",
    }
    log_handle = (data_dir / "server.log").open("w", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.main"],
        cwd=str(ROOT),
        env=env,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
    )

    try:
        if not wait_for(lambda: call("GET", "/api/health").status_code == 200, 60, "服务启动"):
            return 1

        view = call("GET", "/api/golden").json()
        if args.coverage:
            print_coverage(view)
            return 0

        print(f"黄金数据集：{len(view['cases'])} 条用例｜口径 rubric={view['rubric']}")
        baseline = view["baseline"]
        print(
            f"基线：{len(baseline['cases'])} 条用例"
            f"｜更新时间 {baseline['updated_at'] or '（尚无基线）'}"
            f"｜引擎 {baseline['engine'] or '—'}｜rubric {baseline['rubric'] or '—'}"
        )

        payload: dict[str, Any] = {}
        if args.limit is not None:
            payload["limit"] = args.limit
        if args.case:
            payload["caseIds"] = args.case

        started = call("POST", "/api/golden/run", json=payload)
        if started.status_code != 202:
            print(f"启动失败：{started.status_code} {started.text[:200]}")
            return 1
        total = started.json()["state"]["total"]
        print(f"\n开始运行（{total} 条，串行；Mock 引擎约 {total * 6}s）…")

        last_progress = ""

        def progressed() -> bool:
            nonlocal last_progress
            state = call("GET", "/api/golden").json()["state"]
            if state["running"]:
                mark = f"{state['completed']}/{state['total']} {state['current']}"
                if mark != last_progress:
                    last_progress = mark
                    print(f"  … {mark}")
                return False
            return True

        if not wait_for(progressed, args.timeout, "黄金数据集运行完成"):
            return 1

        view = call("GET", "/api/golden").json()
        state = view["state"]
        if state["error"]:
            print(f"运行出错：{state['error']}")
            return 1

        results = state["results"]
        # 部分运行时按「本次有意跑的范围」比对，否则单跑一条会被其余用例判成缺失
        scope = state.get("scope")
        comparison = view.get("comparison") or {}
        if args.tolerance is not None:
            from app.core.golden import compare

            comparison = compare(_to_cases(results), tolerance=args.tolerance, scope=scope)

        if not baseline["cases"]:
            print("\n尚无基线：请用 --update-baseline 生成首版基线。")
            if not comparison:
                from app.core.golden import compare

                comparison = compare(_to_cases(results), scope=scope)
            print_comparison(comparison, results)
        else:
            print_comparison(comparison, results)

        if args.update_baseline:
            from app.core.golden import save_baseline

            if not comparison.get("ok", False) and not args.force:
                print(
                    "\n! 本次存在回归/失败/缺失，已拒绝更新基线。"
                    "确认这些变化都是预期的之后加 --force 重跑。"
                )
                return 1
            saved = save_baseline(
                _to_cases(results), engine=os.environ.get("LLM_PROVIDER", "mock")
            )
            print(f"\n基线已更新：{len(saved['cases'])} 条用例 → golden/baseline.json")
            return 0

        passed = bool(comparison.get("ok")) if baseline["cases"] else False
        print("\n结果：", "通过（无回归）" if passed else "未通过（存在回归 / 失败 / 缺失，或尚无基线）")
        return 0 if passed else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log_handle.close()
        lines = (data_dir / "server.log").read_text(encoding="utf-8", errors="replace").splitlines()
        errors = [line for line in lines if "ERROR" in line or "Traceback" in line]
        if errors:
            print("\n[服务端异常]")
            print("\n".join(errors[:8]))


if __name__ == "__main__":
    raise SystemExit(main())
