"""联调核验：拉起真实服务，逐条比对「本轮新增能力」的实际响应与契约。

与 ``smoke_api.py`` 的分工
-------------------------
``smoke_api.py`` 是**端到端验收**（跑完整流水线、覆盖全部接口与错误分支，一次几十秒）；
本脚本是**快速契约核验**（约 1 分钟），只盯住最容易在重构中悄悄回归的几处：
断点续跑后端、评估配置与聚合、记忆库租户视角、六维评分结构、`judge.scored` 事件、
**调用轨迹的 span 树层级与 OTel 形状导出**、**入站 traceparent 的跨进程延续**、
**数字人 / 桌宠 / 视频理解样例的创建 → 惰性推进 → 完成闭环**、
**创作技能提炼的量化落盘 → SKILL.md 预览 → 技能包下载 → 沉淀记忆库**、
**灵感冲浪的占位灵感 → 灵感池沉淀 → 选卡注入 Brief → 跨会话浏览历史（没真读就不记）**、
**影视制作工厂的岗位目录 → 建项目 → 异步跑图 → 产物越界/未知节点 404 → 交付包 zip（manifest/sha256）→ 二跑全缓存 → 删除回收**.

用途：改完评估/租户/追踪相关代码后先跑它，比直接上 smoke 更快拿到「哪里断了」。

用法::

    python scripts/verify_contracts.py      # 退出码 0 表示全部符合预期
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.util import force_offline_provider, free_port, make_temp_dir  # noqa: E402

DATA = make_temp_dir("verify-", base=ROOT / ".doctor-data")
PORT = free_port(8813)
BASE = f"http://127.0.0.1:{PORT}"

env = {**os.environ, "CREATOR_DATA_DIR": str(DATA), "PORT": str(PORT), "PYTHONNOUSERSITE": "1"}
env["LLM_PROVIDER"] = force_offline_provider()
log = (DATA / "server.log").open("w", encoding="utf-8", errors="replace")
proc = subprocess.Popen(
    [sys.executable, "-m", "app.main"],
    cwd=str(ROOT),
    env=env,
    stdout=log,
    stderr=subprocess.STDOUT,
)

failures: list[str] = []


def check(label: str, actual: object, expected: object, detail: str = "") -> None:
    """断言 ``actual == expected``。

    ``detail`` 是**失败时的补充说明**，与 ``expected`` 分开 ——
    把说明误传进 ``expected`` 会让断言与字符串比较，从而永远失败。
    """
    good = actual == expected
    message = "" if good else f"（期望 {expected!r}）"
    if not good and detail:
        message += f" {detail}"
    print(f"  {'OK  ' if good else 'FAIL'} {label}: {actual!r}{message}")
    if not good:
        failures.append(label)


try:
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            with httpx.Client(base_url=BASE, timeout=3.0) as client:
                if client.get("/api/health").status_code == 200:
                    break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(0.4)
    else:
        print("服务未在 60s 内就绪")
        raise SystemExit(1)

    with httpx.Client(base_url=BASE, timeout=30.0) as client:
        print("[GET /api/health]")
        health = client.get("/api/health").json()
        # 存储模式决定检查点后端：file → sqlite；pg → postgres（子进程环境已透传）
        storage_mode = (os.environ.get("CREATOR_STORAGE") or "file").strip().lower()
        expected_kind = "postgres" if storage_mode == "pg" else "sqlite"
        check("checkpointer.kind", health["checkpointer"]["kind"], expected_kind)
        check("provider.name", health["provider"]["name"], "mock")

        print("[GET /（静态托管，含新构建的前端）]")
        page = client.get("/")
        exists = (ROOT / "dist" / "index.html").exists()
        check(
            "dist/index.html 存在",
            exists,
            True,
            "请先执行 npm run build（CI 中该步骤必须排在契约核验之前）",
        )
        check("status", page.status_code, 200)
        check("index.html 挂载点", 'id="root"' in page.text, True)
        if not exists:
            print("    ! 后端只在 dist/ 存在时才挂载静态托管与 SPA 回落路由；")
            print("      缺 dist/ 会让 GET / 返回 404 —— 这正是 CI 上曾失败的原因。")

        print("[GET /api/settings → judge]")
        judge = client.get("/api/settings").json()["judge"]
        check("mode", judge["mode"], "advisory")
        check("provider", judge["provider"], "offline")
        check("passThreshold", judge["passThreshold"], 75.0)
        check("weight", judge["weight"], 0.2)
        check("rubric 非空", bool(judge["rubric"]), True)

        print("[GET /api/evaluations（空库）]")
        evals = client.get("/api/evaluations").json()
        check("records", evals["records"], [])
        check("stats.total", evals["stats"]["total"], 0)
        check("config.mode", evals["config"]["mode"], "advisory")

        print("[GET /api/memory（租户视角）]")
        mem = client.get("/api/memory").json()
        check("stats.global_total", mem["stats"]["global_total"], 0)
        # 未启用鉴权时租户固定为 default（与 TaskRecord.tenant 口径一致）
        check("stats.tenant（未鉴权 → default）", mem["stats"]["tenant"], "default")
        check("stats.capacity", mem["stats"]["capacity"], 500)

        print("[GET /api/metrics → system.judge]")
        system = client.get("/api/metrics").json()["system"]
        check("judge.mode", system["judge"]["mode"], "advisory")
        check("judge.total", system["judge"]["total"], 0)

        print("[POST /api/tasks → 评估落地 → POST /evaluate]")
        task = client.post("/api/tasks", json={"brief": {
            "brand": "核验品牌", "product": "核验产品", "channel": "小红书",
            "industry": "消费品", "keywords": ["核验"], "constraints": ["不夸大功效"],
        }, "autoApprove": True}).json()["task"]
        tid = task["id"]
        deadline = time.time() + 120
        while time.time() < deadline:
            status = client.get(f"/api/tasks/{tid}").json()["task"]["status"]
            if status in ("completed", "rejected", "failed"):
                break
            time.sleep(0.5)
        check("任务终态", status, "completed")

        evals = client.get(f"/api/evaluations?taskId={tid}").json()
        check("评估记录数（门禁 + 交付两次）", len(evals["records"]) >= 2, True)
        first = evals["records"][0]
        check("六维维度", len(first["axes"]), 6)
        check("trigger 取值", first["trigger"] in ("review", "delivery"), True)
        check("分数区间", 0 < first["total"] <= 100, True)

        manual = client.post(f"/api/tasks/{tid}/evaluate", json={"provider": "offline"}).json()["record"]
        check("按需评估 trigger", manual["trigger"], "manual")
        check("按需评估 mode", manual["mode"], "offline")

        print("[评估事件与门禁事件]")
        events = client.get(f"/api/tasks/{tid}").json()["events"]
        types = [e["type"] for e in events]
        check("含 judge.scored 事件", "judge.scored" in types, True)
        gates = [e for e in events if e["type"] == "gate.decision"]
        check("门禁事件携带 evaluator 结论", bool((gates[0]["payload"] or {}).get("judge")), True)

        print("[GET /api/tasks/{id}/trace（调用轨迹）]")
        trace = client.get(f"/api/tasks/{tid}/trace").json()
        spans = trace["spans"]
        roots = [s for s in spans if s["parent_span_id"] is None]
        check("trace_id 非空", bool(trace["trace_id"]), True)
        check("span 数量", len(spans) >= 20, True)
        # 关键断言：树而不是平铺列表
        check("根 span 少于总数（层级成型）", len(roots) < len(spans), True)
        llm_spans = [s for s in spans if s["name"].startswith("llm.")]
        by_id = {s["span_id"]: s for s in spans}
        check("llm span 数量", len(llm_spans) >= 11, True)
        check(
            "llm span 嵌套在智能体/评估之下",
            all(
                s["parent_span_id"] in by_id
                and by_id[s["parent_span_id"]]["name"].startswith(("A", "judge", "gate"))
                for s in llm_spans
            ),
            True,
        )
        check("span 带模型属性", all("llm.model" in s["attributes"] for s in llm_spans), True)
        # OTel 的 `unset` 是合法的「未显式设置状态」，项目里只在个别收尾路径出现；
        # 这里断言「没有 error」而不是要求全是 ok —— 后者会在收尾竞态下偶发失败。
        span_statuses = {s["status"] for s in spans}
        check("span 无 error 状态", "error" not in span_statuses, True, f"实际状态 {span_statuses}")
        check("耗时聚合非空", len(trace["summary"]["by_name"]) > 0, True)
        check("导出格式为 OTel 形状", trace["export"]["format"], "otel-shaped-json")
        # 事件与 span 的关联
        linked = [e for e in events if e.get("span_id")]
        check("事件带 span_id", len(linked) > 0, True)
        check("事件 trace_id 一致", {e["trace_id"] for e in events if e.get("trace_id")} == {trace["trace_id"]}, True)

        print("[GET /api/tasks/{id}/export（成品导出）]")
        missing = client.get("/api/tasks/verify-no-such-task/export")
        check("无任务 → 404", missing.status_code, 404)
        export = client.get(f"/api/tasks/{tid}/export")
        check("有 final_delivery → 200", export.status_code, 200)
        check("导出为 text/plain", "text/plain" in export.headers.get("content-type", ""), True)
        check("导出内容非空", len(export.content) > 0, True)

        print("[GET /api/metrics → system.tracing]")
        tracing = client.get("/api/metrics").json()["system"]["tracing"]
        check("tracing.traces", tracing["traces"] >= 1, True)
        check("tracing.spans", tracing["spans"] >= 20, True)
        check("tracing.errors", tracing["errors"], 0)
        check("tracing.by_name 非空", len(tracing["by_name"]) > 0, True)
        sampling = tracing["sampling"]
        check("sampling.sampler 非空", bool(sampling["sampler"]), True)
        check("采样计数齐备", {"traces_sampled", "traces_unsampled"} <= set(sampling), True)

        print("[health → tracing 采样与传播可见]")
        health_tracing = client.get("/api/health").json()["tracing"]
        check("health.sampler", bool(health_tracing["sampler"]), True)
        check("health.propagation", "traceparent" in health_tracing["propagation"], True)

        print("[入站 traceparent → trace 跨进程延续]")
        remote_tid = "1234567890abcdef1234567890abcdef"
        remote_sid = "1234567890abcdef"
        video_task = client.post(
            "/api/tasks",
            json={"brief": {
                "brand": "核验品牌", "product": "核验产品", "channel": "抖音",
                "industry": "消费品", "keywords": ["核验"], "deliverables": ["短视频脚本 1 支"],
            }, "autoApprove": True},
            headers={"traceparent": f"00-{remote_tid}-{remote_sid}-01"},
        )
        check("携带 traceparent 创建任务", video_task.status_code, 201)
        vid = video_task.json()["task"]["id"]
        prop_trace = client.get(f"/api/tasks/{vid}/trace").json()
        check("trace 延续远端 trace_id", prop_trace["trace_id"], remote_tid)
        check(
            "根 span 挂在远端 span 之下",
            any(s["parent_span_id"] == remote_sid for s in prop_trace["spans"]),
            True,
        )
        check("坏头不阻塞任务创建",
              client.post("/api/tasks", json={"brief": {
                  "brand": "核验品牌", "product": "核验产品", "channel": "小红书", "industry": "消费品",
              }, "autoApprove": True}, headers={"traceparent": "garbage"}).status_code,
              201)

        print("[POST /api/tasks/{id}/digital-human（无脚本 → 409）]")
        no_script = client.post(f"/api/tasks/{tid}/digital-human", json={})
        check("无视频脚本拒绝创建", no_script.status_code, 409)

        print("[抖音任务的数字人样例闭环]")
        deadline = time.time() + 120
        video_status = "running"
        while time.time() < deadline:
            video_status = client.get(f"/api/tasks/{vid}").json()["task"]["status"]
            if video_status in ("completed", "rejected", "failed"):
                break
            time.sleep(0.5)
        check("视频任务终态", video_status, "completed")

        dh_view = client.get(f"/api/tasks/{vid}/digital-human").json()
        check("has_video_script", dh_view["has_video_script"], True)
        check("初始无作业", dh_view["jobs"], [])
        created_job = client.post(f"/api/tasks/{vid}/digital-human", json={})
        check("创建样例渲染作业", created_job.status_code, 201)
        job = created_job.json()["job"]
        check("provider 为 sample", job["provider"], "sample")
        check("渲染清单来自分镜", job["shot_count"] >= 1, True)
        check("初始状态 queued", job["status"], "queued")

        deadline = time.time() + 40
        final_status = job["status"]
        while time.time() < deadline:
            jobs = client.get(f"/api/tasks/{vid}/digital-human").json()["jobs"]
            final_status = next(j["status"] for j in jobs if j["id"] == job["id"])
            if final_status in ("done", "failed"):
                break
            time.sleep(1.0)
        check("样例引擎完成渲染", final_status, "done")
        final_job = next(j for j in jobs if j["id"] == job["id"])
        check("产出成片地址", str(final_job["video_url"]).startswith("sample://"), True)
        check("分镜段落齐备", len(final_job["manifest"]["segments"]), job["shot_count"])

        print("[POST /api/tasks/{id}/images 与 /videos（无脚本 → 409）]")
        check("无视频脚本拒绝创建视频作业", client.post(f"/api/tasks/{tid}/videos", json={}).status_code, 409)

        print("[图片生成通道 HTTP 契约]")
        img_view = client.get(f"/api/tasks/{vid}/images").json()
        check("图片 has_visual_brief", img_view["has_visual_brief"], True)
        check("图片初始无作业", img_view["jobs"], [])
        img_created = client.post(f"/api/tasks/{vid}/images", json={})
        check("创建样例图片作业", img_created.status_code, 201)
        img_job = img_created.json()["job"]
        check("图片 provider 为 sample", img_job["provider"], "sample")
        check("图片生成清单非空", len(img_job["manifest"]["segments"]) >= 1, True)
        deadline = time.time() + 40
        img_status = img_job["status"]
        while time.time() < deadline:
            img_jobs = client.get(f"/api/tasks/{vid}/images").json()["jobs"]
            img_status = next(j["status"] for j in img_jobs if j["id"] == img_job["id"])
            if img_status in ("done", "failed"):
                break
            time.sleep(1.0)
        check("图片样例引擎完成", img_status, "done")
        img_final = next(j for j in img_jobs if j["id"] == img_job["id"])
        check("图片产出清单", str(img_final["images"][0]["url"]).startswith("sample://"), True)

        print("[视频生成通道 HTTP 契约]")
        vid_created = client.post(f"/api/tasks/{vid}/videos", json={})
        check("创建样例视频作业", vid_created.status_code, 201)
        vjob = vid_created.json()["job"]
        check("视频 provider 为 sample", vjob["provider"], "sample")
        check("视频渲染清单来自分镜", vjob["shot_count"] >= 1, True)
        deadline = time.time() + 40
        v_status = vjob["status"]
        while time.time() < deadline:
            v_jobs = client.get(f"/api/tasks/{vid}/videos").json()["jobs"]
            v_status = next(j["status"] for j in v_jobs if j["id"] == vjob["id"])
            if v_status in ("done", "failed"):
                break
            time.sleep(1.0)
        check("视频样例引擎完成", v_status, "done")
        v_final = next(j for j in v_jobs if j["id"] == vjob["id"])
        check("视频按镜出画面段", len(v_final["segments_out"]) >= 1, True)

        print("[桌面宠物通道 HTTP 契约]")
        pet_view = client.get(f"/api/tasks/{vid}/pets").json()
        check("宠物 has_visual_brief", pet_view["has_visual_brief"], True)
        check("宠物初始无作业", pet_view["jobs"], [])
        pet_created = client.post(
            f"/api/tasks/{vid}/pets", json={"provider": "sample", "name": "核验宠", "actions": ["idle", "walk"]}
        )
        check("创建样例宠物作业", pet_created.status_code, 201)
        pet_job = pet_created.json()["job"]
        check("宠物 provider 为 sample", pet_job["provider"], "sample")
        check("宠物按所选动作出帧", pet_job["manifest"]["actions"], ["idle", "walk"])
        check("宠物帧数与清单一致", pet_job["frame_count"], len(pet_job["manifest"]["segments"]))
        deadline = time.time() + 40
        p_status = pet_job["status"]
        p_jobs: list = []
        while time.time() < deadline:
            p_jobs = client.get(f"/api/tasks/{vid}/pets").json()["jobs"]
            p_status = next(j["status"] for j in p_jobs if j["id"] == pet_job["id"])
            if p_status in ("done", "failed"):
                break
            time.sleep(1.0)
        check("宠物样例引擎完成", p_status, "done")
        p_final = next(j for j in p_jobs if j["id"] == pet_job["id"])
        check("帧已落盘（数量与清单一致）", len(p_final["frames"]), pet_job["frame_count"])
        check("帧标注为样例绘制", p_final["frames"][0]["simulated"], True)
        frame = client.get(f"/api/tasks/{vid}/pets/{pet_job['id']}/frames/idle/1")
        check("帧路由回传 PNG", (frame.status_code, frame.headers.get("content-type", "").split(";")[0], frame.content[:8] == b"\x89PNG\r\n\x1a\n"), (200, "image/png", True))
        check("未知动作帧号 → 404",
              client.get(f"/api/tasks/{vid}/pets/{pet_job['id']}/frames/fly/1").status_code, 404)
        package = client.get(f"/api/tasks/{vid}/pets/{pet_job['id']}/package")
        check("宠物包可下载", package.status_code, 200)
        names = zipfile.ZipFile(io.BytesIO(package.content)).namelist()
        check("宠物包含清单/说明/运行器", {"pet.json", "README.md", "runner/pet.py"} <= set(names), True)
        check("宠物包帧数与作业一致", sum(1 for name in names if name.startswith("frames/")), pet_job["frame_count"])
        pet_real = client.post(f"/api/tasks/{vid}/pets", json={"provider": "imagegen"}).json()["job"]
        check("imagegen 未配置端点显式失败", pet_real["status"], "failed")
        check("失败原因点明未配置", "未配置" in str(pet_real["error"]), True)
        check("未完成作业拒绝出包 → 409",
              client.get(f"/api/tasks/{vid}/pets/{pet_real['id']}/package").status_code, 409)
        # 帧/包只认「该任务名下、且属于该租户」的作业：不存在的作业与借他人 job_id 同为 404
        check("不存在的宠物作业 → 404",
              client.get(f"/api/tasks/{vid}/pets/pet_missing/frames/idle/1").status_code, 404)
        check("作业不属于该任务 → 404",
              client.get(f"/api/tasks/{tid}/pets/{pet_job['id']}/frames/idle/1").status_code, 404)

        print("[视频理解通道 HTTP 契约]")
        vu_task = client.post("/api/tasks", json={"brief": {
            "brand": "核验品牌", "product": "动漫解说", "channel": "B站", "industry": "文化娱乐",
            "assets": [{"kind": "video", "ref": "verify_ep01.mp4", "title": "第一集"}],
        }}).json()
        vu_id = vu_task["task"]["id"]
        vu_view = client.get(f"/api/tasks/{vu_id}/video-understanding").json()
        check("视频理解 has_video_asset", vu_view["has_video_asset"], True)
        check("视频理解初始无作业", vu_view["jobs"], [])
        check("无视频素材任务拒绝创建 → 409",
              client.post(f"/api/tasks/{tid}/video-understanding", json={}).status_code, 409)
        vu_created = client.post(f"/api/tasks/{vu_id}/video-understanding", json={"provider": "sample"})
        check("创建样例视频理解作业", vu_created.status_code, 201)
        vu_job = vu_created.json()["job"]
        check("视频理解 provider 为 sample", vu_job["provider"], "sample")
        check("real（未配视频理解网关）显式失败不假装看懂",
              client.post(f"/api/tasks/{vu_id}/video-understanding", json={"provider": "real"}).json()["job"]["status"],
              "failed")
        deadline = time.time() + 40
        vu_status = vu_job["status"]
        vu_jobs: list = []
        while time.time() < deadline:
            vu_jobs = client.get(f"/api/tasks/{vu_id}/video-understanding").json()["jobs"]
            vu_status = next(j["status"] for j in vu_jobs if j["id"] == vu_job["id"])
            if vu_status in ("done", "failed"):
                break
            time.sleep(1.0)
        check("视频理解样例引擎完成", vu_status, "done")
        vu_final = next(j for j in vu_jobs if j["id"] == vu_job["id"])
        check("视觉摘要标注为占位", vu_final["summary"]["simulated"], True)
        check("视觉摘要含可注入文本", bool(vu_final["summary"]["text_brief"]), True)
        applied = client.post(f"/api/tasks/{vu_id}/video-understanding/apply", json={"job_id": vu_job["id"]})
        check("注入 Brief 成功", applied.status_code, 200)
        check("Brief 已含视频理解约束",
              any("【视频理解】" in str(c) for c in applied.json()["constraints"]), True)

        print("[创作技能提炼通道 HTTP 契约]")
        (DATA / "assets").mkdir(parents=True, exist_ok=True)
        (DATA / "assets" / "verify_works.md").write_text(
            "# 往期成稿\n\n"
            "别急着买，先听完这三句。\n我用了三个月，踩过三次坑，才敢说这句话。\n"
            "你不是需要它，你是需要它解决的问题。\n"
            "先问自己三个问题：真的每天在用吗？坏的次数多吗？替代品有吗？\n"
            "如果三个答案都是否，那你的钱应该留着。\n去店里拿起来，手会告诉你答案。\n",
            encoding="utf-8",
        )
        sk_task = client.post("/api/tasks", json={"brief": {
            "brand": "核验品牌", "product": "便携耳机", "channel": "小红书", "industry": "消费电子",
            "keywords": ["每天戴"], "constraints": ["不得承诺疗效"],
            "assets": [{"kind": "document", "ref": "verify_works.md", "title": "往期成稿"}],
        }, "autoApprove": True}).json()["task"]
        skid = sk_task["id"]
        sk_view = client.get(f"/api/tasks/{skid}/skills").json()
        check("技能 has_materials（文档作品）", sk_view["has_materials"], True)
        check("技能初始无作业", sk_view["jobs"], [])
        check("不存在的技能作业 → 404",
              client.get(f"/api/tasks/{skid}/skills/skill_missing/document").status_code, 404)
        check("作业不属于该任务 → 404",
              client.get(f"/api/tasks/{tid}/skills/skill_missing/package").status_code, 404)

        sk_created = client.post(f"/api/tasks/{skid}/skills", json={"provider": "rules"})
        check("创建 rules 技能作业", sk_created.status_code, 201)
        sk_job = sk_created.json()["job"]
        check("rules 通道零 token 预估", sk_job["estimated_cost_usd"], 0.0)
        check("文档素材算一份作品", sk_job["sources"][0]["origin"], "document")
        check("量化画像真统计", sk_job["stats"]["works"] >= 1, True)
        check("受理时状态 queued", sk_job["status"], "queued")
        sk_llm = client.post(f"/api/tasks/{skid}/skills", json={"provider": "llm"}).json()["job"]
        check("llm 未配真实网关显式失败", sk_llm["status"], "failed")
        check("失败原因点明未配置网关", "未配置" in str(sk_llm["error"]), True)
        check("失败作业不可预览 → 409",
              client.get(f"/api/tasks/{skid}/skills/{sk_llm['id']}/document").status_code, 409)
        check("失败作业不可出包 → 409",
              client.get(f"/api/tasks/{skid}/skills/{sk_llm['id']}/package").status_code, 409)

        deadline = time.time() + 40
        sk_status = sk_job["status"]
        sk_jobs: list = []
        while time.time() < deadline:
            sk_jobs = client.get(f"/api/tasks/{skid}/skills").json()["jobs"]
            sk_status = next(j["status"] for j in sk_jobs if j["id"] == sk_job["id"])
            if sk_status in ("done", "failed"):
                break
            time.sleep(1.0)
        check("rules 作业完成", sk_status, "done")
        sk_final = next(j for j in sk_jobs if j["id"] == sk_job["id"])
        check("技能正文已落盘", str(sk_final["skill_ref"]).startswith("skills/"), True)
        check("rules 产物标注未经模型推理", sk_final["skill"]["simulated"], True)
        check("每条步骤都带证据", bool(sk_final["skill"]["steps"]) and all(
            step.get("evidence") for step in sk_final["skill"]["steps"]), True)
        check("逐字样例都标注来源", all(
            item.get("source") and item.get("quote") for item in sk_final["skill"]["examples"]), True)
        check("样本仅 1 份时如实告警", any(
            "不构成通用方法" in str(item) for item in sk_final["skill"]["anti_patterns"]) or sk_final["stats"]["works"] > 1, True)
        doc = client.get(f"/api/tasks/{skid}/skills/{sk_job['id']}/document")
        check("SKILL.md 可预览",
              (doc.status_code, doc.headers.get("content-type", "").split(";")[0]),
              (200, "text/markdown"))
        check("正文以 frontmatter 开头", doc.text.startswith("---\nname: "), True)
        check("正文含步骤与量化画像", ("## 步骤" in doc.text, "## 量化画像" in doc.text), (True, True))
        pkg = client.get(f"/api/tasks/{skid}/skills/{sk_job['id']}/package")
        check("技能包可下载", pkg.status_code, 200)
        archive = zipfile.ZipFile(io.BytesIO(pkg.content))
        check("技能包三件套", sorted(archive.namelist()), ["README.md", "SKILL.md", "skill.json"])
        payload = json.loads(archive.read("skill.json").decode("utf-8"))
        check("skill.json 与作业同源", (payload["id"], payload["provider"]), (sk_job["id"], "rules"))
        applied = client.post(f"/api/tasks/{skid}/skills/apply", json={"job_id": sk_job["id"]})
        check("技能沉淀进记忆库", applied.status_code, 200)
        check("首次沉淀新增 1 张卡", applied.json()["added"], 1)
        check("重复沉淀幂等（内容指纹）",
              client.post(f"/api/tasks/{skid}/skills/apply", json={"job_id": sk_job["id"]}).json()["added"], 0)
        check("记忆库可按 template 取到该卡", any(
            "创作技能｜" in str(card["title"]) for card in client.get("/api/memory?kind=template").json()["cards"]), True)
        check("未完成作业不可沉淀 → 409",
              client.post(f"/api/tasks/{skid}/skills/apply", json={"job_id": sk_llm["id"]}).status_code, 409)

        vu_skills = client.post(f"/api/tasks/{vu_id}/skills", json={"provider": "rules"}).json()["job"]
        check("视频理解摘要可作为作品提炼",
              "video_understanding" in [item["origin"] for item in vu_skills["sources"]], True)

        # 删除回收要真实服务下的终态任务：技能作业与文件随任务一起消失，持久文件才不只在增
        deadline = time.time() + 120
        sk_task_status = "running"
        while time.time() < deadline:
            sk_task_status = client.get(f"/api/tasks/{skid}").json()["task"]["status"]
            if sk_task_status in ("completed", "rejected", "failed"):
                break
            time.sleep(0.5)
        check("技能任务终态", sk_task_status, "completed")
        purged = client.delete(f"/api/tasks/{skid}").json()
        check("删任务回收技能作业", purged["purged_skillgen_jobs"], 2)
        check("技能目录随任务回收",
              sorted(p.name for p in (DATA / "skills").iterdir()) if (DATA / "skills").exists() else [], [])

        print("[灵感冲浪 HTTP 契约：sample 产占位灵感、浏览记忆只认真读到的页]")
        surf_cfg_view = client.get("/api/settings").json()["surf"]
        hist = client.get("/api/surf/history").json()
        # 本轮没跑 browser 通道 → 一份读过的页都没记；空库必须如实返回空，不拿别的存储凑数
        check("浏览历史（空库）items", hist["items"], [])
        check("浏览历史 remember 跟随配置", hist["remember"], surf_cfg_view["rememberRead"])
        check("浏览历史上限 max 跟随配置", hist["max"], surf_cfg_view["historyMax"])
        # 「读到门口就退出（登录/订阅/验证墙）」的开关也得在设置快照里，前端才有地方关它
        check("挡墙开关在设置快照里", isinstance(surf_cfg_view.get("avoidWalls"), bool), True)
        check("浏览历史 limit 生效", client.get("/api/surf/history?limit=1").json()["items"], [])
        surf_job = client.post(f"/api/tasks/{tid}/surf", json={
            "provider": "sample", "topic": "核验露营装备",
        }).json()["session"]
        check("样例会话受理为 queued", surf_job["status"], "queued")
        deadline = time.time() + 40
        surf_final: dict = surf_job
        while time.time() < deadline:
            listed = client.get(f"/api/tasks/{tid}/surf").json()["sessions"]
            surf_final = next((s for s in listed if s["id"] == surf_job["id"]), {})
            if surf_final.get("status") in ("done", "failed"):
                break
            time.sleep(0.5)
        check("样例会话推进到 done", surf_final.get("status"), "done")
        surf_cards = surf_final.get("cards") or []
        check("样例灵感全部标注为占位", bool(surf_cards) and all(
            bool(card.get("simulated")) for card in surf_cards), True)
        surf_card_ids = {str(card["id"]) for card in surf_cards}
        pool_ids = {str(card["id"]) for card in client.get("/api/surf/pool").json()["cards"]}
        check("样例灵感沉淀进跨任务灵感池", bool(surf_card_ids) and surf_card_ids <= pool_ids, True)
        check("sample 通道不写浏览记忆", client.get("/api/surf/history").json()["items"], [])
        check("清浏览记忆（本来就没记）", client.delete("/api/surf/history").json(),
              {"ok": True, "removed": 0, "tenant": "default"})
        applied_surf = client.post(f"/api/tasks/{tid}/surf/apply",
                                   json={"cardIds": [str(surf_cards[0]["id"])]})
        check("选中灵感注入 Brief", applied_surf.status_code, 200)
        check("注入的是【灵感冲浪】块", any(
            str(c).startswith("【灵感冲浪】") for c in applied_surf.json()["constraints"]), True)
        check("注入的简报如实带占位标注", "占位" in next(
            str(c) for c in applied_surf.json()["constraints"] if str(c).startswith("【灵感冲浪】")), True)
        check("重复注入不堆叠（覆盖同一块）", sum(
            1 for c in client.post(f"/api/tasks/{tid}/surf/apply",
                                   json={"cardIds": [str(surf_cards[0]["id"])]}).json()["constraints"]
            if str(c).startswith("【灵感冲浪】")), 1)
        check("一张卡都没选 → 409",
              client.post(f"/api/tasks/{tid}/surf/apply", json={"cardIds": []}).status_code, 409)
        check("池里没有的卡 → 404",
              client.post(f"/api/tasks/{tid}/surf/apply", json={"cardIds": ["si_missing"]}).status_code, 404)

        print("[影视制作工厂：岗位目录 → 建项目 → 异步跑图 → 产物 → 缓存 → 删除]")
        palette = client.get("/api/factory/palette").json()
        check("岗位目录 60 个岗位", palette["node_count"], 60)
        check("部门分组非空", bool(palette["departments"]), True)
        check("岗位无孤儿", palette["orphan_nodes"], [])
        presets = client.get("/api/factory/presets").json()["presets"]
        check("预设五套", sorted(row["key"] for row in presets), ["full", "gen", "script", "short", "voice"])
        check("预设都带岗位数", all(row["node_count"] > 0 and row["edge_count"] > 0 for row in presets), True)

        made = client.post("/api/factory/projects", json={"title": "契约核验工厂", "preset": "script"})
        check("建项目 200", made.status_code, 200)
        made_body = made.json()
        project = made_body["project"]
        factory_id = str(project["id"])
        check("预设落图 12 岗位", len(project["graph"]["nodes"]), 12)
        check("建项目体检无 error", [issue for issue in made_body["issues"] if issue["level"] == "error"], [])
        check("项目列表可见",
              factory_id in [row["id"] for row in client.get("/api/factory/projects").json()["projects"]], True)
        check("未知项目 404", client.get("/api/factory/projects/no-such-project").status_code, 404)

        draft = {"nodes": [{"id": "s", "type": "screening", "pos": [0, 0], "params": {}}], "edges": []}
        draft_issues = client.post(f"/api/factory/projects/{factory_id}/validate", json={"graph": draft}).json()["issues"]
        check("草稿体检点名缺失的必需交付物",
              any(issue["level"] == "warn" and "时间轴" in issue["message"] for issue in draft_issues), True)
        check("坏图 400",
              client.post(f"/api/factory/projects/{factory_id}/validate",
                          json={"graph": {"nodes": "nope", "edges": []}}).status_code, 400)
        check("未知 target 400（不退化成跑全图）",
              client.post(f"/api/factory/projects/{factory_id}/run",
                          json={"targets": ["n-ghost"]}).status_code, 400)

        first_run = client.post(f"/api/factory/projects/{factory_id}/run", json={}).json()["job"]
        check("跑图受理为 queued", first_run["status"], "queued")
        deadline = time.time() + 90
        finished = first_run
        while finished.get("status") in ("queued", "running") and time.time() < deadline:
            time.sleep(0.4)
            finished = client.get(f"/api/factory/jobs/{first_run['id']}").json()["job"]
        check("首跑 ok", finished["status"], "ok")
        check("作业计划岗位数=12（进度分母）", finished.get("plan_size"), 12)
        check("12 岗位全部交付",
              (finished["counts"].get("ok", 0) + finished["counts"].get("cached", 0)), 12)
        check("事件流带 run.finish", any(row.get("type") == "run.finish" for row in finished["events"]), True)
        check("未知作业 404", client.get("/api/factory/jobs/frun-missing").status_code, 404)

        runs_before_preview = client.get(f"/api/factory/projects/{factory_id}/runs").json()["runs"]
        preview = client.post(f"/api/factory/projects/{factory_id}/preview", json={}).json()["preview"]
        preview_target = client.post(f"/api/factory/projects/{factory_id}/preview",
                                     json={"targets": ["n-sup"]}).json()["preview"]
        preview_force = client.post(f"/api/factory/projects/{factory_id}/preview",
                                    json={"force": True}).json()["preview"]
        runs_after_preview = client.get(f"/api/factory/projects/{factory_id}/runs").json()["runs"]
        graph_node_ids = {node["id"] for node in project["graph"]["nodes"]}
        check("预检整图全缓存",
              (preview["counts"].get("cached"), preview["counts"].get("total")), (12, 12))
        check("预检计划覆盖全图", set(preview["plan"]), graph_node_ids)
        check("预检目标闭包收窄且全缓存",
              "n-sup" in preview_target["plan"]
              and set(preview_target["plan"]) < graph_node_ids
              and preview_target["counts"].get("cached") == len(preview_target["plan"])
              and preview_target["counts"].get("run", 0) == 0, True)
        check("预检 force 全部重跑",
              (preview_force["counts"].get("cached"), preview_force["counts"].get("run")), (0, 12))
        check("预检只读不建作业", len(runs_after_preview), len(runs_before_preview))
        check("预检未知目标 400",
              client.post(f"/api/factory/projects/{factory_id}/preview",
                          json={"targets": ["n-ghost"]}).status_code, 400)
        check("预检未知项目 404",
              client.post("/api/factory/projects/no-such-project/preview", json={}).status_code, 404)
        check("运行历史摘要带续跑目标",
              all("retry_nodes" in row for row in runs_after_preview), True)
        check("运行历史摘要不带事件",
              all("events" not in row for row in runs_after_preview), True)
        check("成功运行没有待续跑岗位", runs_after_preview[0]["retry_nodes"], [])

        artifacts = client.get(f"/api/factory/projects/{factory_id}/artifacts").json()
        check("产物清单非空", artifacts["count"] > 0, True)
        check("产物带分组计数", bool(artifacts["groups"]), True)
        first_file = artifacts["artifacts"][0]
        check("产物可直接读取", client.get(first_file["url"]).status_code, 200)
        check("产物越界 404",
              client.get(f"/api/factory/projects/{factory_id}/artifacts/%2e%2e%2f%2e%2e%2fetc%2fpasswd").status_code, 404)
        check("岗位完整产物可回读",
              bool(client.get(f"/api/factory/projects/{factory_id}/outputs/n-sb").json()["outputs"]), True)
        check("未知岗位产物 404",
              client.get(f"/api/factory/projects/{factory_id}/outputs/no-such-node").status_code, 404)

        bundle = client.get(f"/api/factory/projects/{factory_id}/bundle")
        check("交付包 200", bundle.status_code, 200)
        check("交付包是 zip", bundle.headers.get("content-type", "").startswith("application/zip"), True)
        with zipfile.ZipFile(io.BytesIO(bundle.content)) as zipped:
            bundle_names = set(zipped.namelist())
            bundle_manifest = json.loads(zipped.read("manifest.json"))
            bundle_readme = zipped.read("README.txt").decode("utf-8")
        packed_artifacts = [name for name in bundle_names if name.startswith("artifacts/")]
        check("交付包含工作流/产物/清单/说明",
              {"project.json", "manifest.json", "README.txt"} <= bundle_names and bool(packed_artifacts), True)
        check("manifest 记录项目与文件数",
              bundle_manifest.get("format") == "creatoragent.factory.delivery"
              and (bundle_manifest.get("project") or {}).get("id") == factory_id
              and bundle_manifest.get("artifact_count") == len(packed_artifacts) == len(bundle_manifest.get("files") or []),
              True)
        check("manifest 逐文件带 sha256 与总字节",
              all(len(str(row.get("sha256") or "")) == 64 for row in bundle_manifest.get("files") or [])
              and bundle_manifest.get("total_bytes") == sum(int(row.get("bytes") or 0) for row in bundle_manifest.get("files") or []),
              True)
        check("manifest 点名模拟岗位", bool(bundle_manifest.get("simulated_nodes")), True)
        check("README 写明诚实边界", "诚实边界" in bundle_readme, True)
        film_meta = bundle_manifest.get("film") or {}
        check("交付包成片自洽（有片才附 timeline.json，它引用的素材全在包内）",
              (("timeline.json" in bundle_names) == bool(film_meta))
              and all(f"artifacts/{row}" in bundle_names for row in (film_meta.get("refs") or [])),
              True)

        accept_report = client.get(f"/api/factory/projects/{factory_id}/acceptance").json()["acceptance"]
        check("验收 HTTP 契约：全交付无阻断",
              (accept_report["ok"], accept_report["verdict"] in ("pass", "warn"),
               accept_report["counts"]["error"] == 0,
               accept_report["stats"]["terminals_ready"] == accept_report["stats"]["terminals"] >= 1),
              (True, True, True, True))
        check("验收 HTTP 契约：口径字段自洽",
              accept_report["counts"]["error"] == sum(1 for row in accept_report["issues"] if row["level"] == "error")
              and accept_report["counts"]["warn"] == sum(1 for row in accept_report["issues"] if row["level"] == "warn")
              and accept_report["stats"]["refs_missing"] == 0
              and {row["node_id"] for row in accept_report["deliverables"]} <= graph_node_ids,
              True)
        check("验收未知项目 404",
              client.get("/api/factory/projects/no-such-project/acceptance").status_code, 404)

        film_res = client.get(f"/api/factory/projects/{factory_id}/film")
        check("成片端点 200", film_res.status_code, 200)
        film_body = film_res.json()
        film_meta_http = film_body.get("film") or {}
        check("成片契约：film 与 timeline 同生同灭、镜数时长>0、岗位在图内",
              (bool(film_meta_http) == bool(film_body.get("timeline")))
              and (not film_meta_http
                   or (int(film_meta_http.get("shots") or 0) > 0
                       and float(film_meta_http.get("duration_s") or 0) > 0
                       and film_meta_http.get("node_id") in graph_node_ids)),
              True)
        check("成片端点与验收报告同口径（都读 select_film）",
              (film_meta_http.get("node_id"), film_meta_http.get("shots")),
              ((accept_report.get("film") or {}).get("node_id"), (accept_report.get("film") or {}).get("shots")))
        film_delivery = film_body.get("delivery") or {}
        check("成片回看对账契约：字段齐、无成片时 matches/diff 为 null 且 live_fingerprint 空",
              {"delivered_at", "archive_sha256", "film_fingerprint", "live_fingerprint", "matches", "diff"} <= set(film_delivery)
              and (bool(film_meta_http)
                   or (film_delivery.get("matches") is None and film_delivery.get("diff") is None
                       and not film_delivery.get("live_fingerprint"))),
              True)
        check("成片未知项目 404",
              client.get("/api/factory/projects/no-such-project/film").status_code, 404)

        ledger_runs_before = len(client.get(f"/api/factory/projects/{factory_id}/runs?limit=100").json()["runs"])
        deliveries = client.get(f"/api/factory/projects/{factory_id}/deliveries").json()["deliveries"]
        check("台账 HTTP 契约：项目对得上", deliveries["project"]["id"], factory_id)
        led_delivered = [row for row in deliveries["entries"] if row["event"] == "delivered"]
        check("台账 HTTP 契约：打包成功留痕放行且带存档指纹",
              deliveries["summary"]["delivered"] >= 1 and bool(led_delivered)
              and len(str(led_delivered[0].get("archive_sha256") or "")) == 64
              and int(led_delivered[0].get("archive_bytes") or 0) > 0 and led_delivered[0]["ok"], True)
        at_seq = [row["at"] for row in deliveries["entries"]]
        check("台账 HTTP 契约：新条目在前", at_seq, sorted(at_seq, reverse=True))
        ledger_runs_after = len(client.get(f"/api/factory/projects/{factory_id}/runs?limit=100").json()["runs"])
        check("台账 HTTP 契约：只读不建作业", ledger_runs_after, ledger_runs_before)
        check("台账未知项目 404",
              client.get("/api/factory/projects/no-such-project/deliveries").status_code, 404)

        empty_project = client.post("/api/factory/projects", json={"title": "契约核验空项目"}).json()["project"]
        check("无产物项目打包 409",
              client.get(f"/api/factory/projects/{empty_project['id']}/bundle").status_code, 409)
        empty_ledger = client.get(
            f"/api/factory/projects/{empty_project['id']}/deliveries").json()["deliveries"]
        blocked_rows = [row for row in empty_ledger["entries"] if row["event"] == "blocked"]
        check("拒打包也在真实服务留痕",
              empty_ledger["summary"]["blocked"] == 1 and bool(blocked_rows)
              and not blocked_rows[0]["ok"] and blocked_rows[0]["verdict"] == "blocked"
              and bool(blocked_rows[0].get("blockers")), True)
        check("清理空项目", client.delete(f"/api/factory/projects/{empty_project['id']}").status_code, 200)

        broken = client.post("/api/factory/projects", json={
            "title": "契约核验失败历史",
            "graph": {"nodes": [{"id": "t", "type": "text_input", "pos": [0, 0], "params": {"text": ""}}], "edges": []},
        }).json()["project"]
        broken_id = str(broken["id"])
        broken_run = client.post(f"/api/factory/projects/{broken_id}/run", json={}).json()["job"]
        deadline = time.time() + 60
        broken_done = broken_run
        while broken_done.get("status") in ("queued", "running") and time.time() < deadline:
            time.sleep(0.3)
            broken_done = client.get(f"/api/factory/jobs/{broken_run['id']}").json()["job"]
        check("失败运行如实收尾", broken_done["status"], "error")
        check("失败运行点名可续跑岗位", broken_done["retry_nodes"], ["t"])
        broken_runs = client.get(f"/api/factory/projects/{broken_id}/runs").json()["runs"]
        check("失败运行历史摘要带续跑目标", broken_runs[0]["retry_nodes"], ["t"])
        retry_run = client.post(f"/api/factory/projects/{broken_id}/run",
                                json={"targets": broken_done["retry_nodes"]}).json()["job"]
        check("按历史续跑目标可受理", retry_run["targets"], ["t"])
        deadline = time.time() + 60
        retry_done = retry_run
        while retry_done.get("status") in ("queued", "running") and time.time() < deadline:
            time.sleep(0.3)
            retry_done = client.get(f"/api/factory/jobs/{retry_run['id']}").json()["job"]
        check("续跑只跑未完成岗位", retry_done.get("plan_size"), 1)
        check("续跑仍失败也如实落盘", retry_done["status"], "error")
        broken_stats = client.get(f"/api/factory/projects/{broken_id}/stats").json()["stats"]
        broken_stats_node = broken_stats["nodes"][0]
        check("失败统计按作业计数", (broken_stats["runs"]["total"], broken_stats["runs"]["error"]), (2, 2))
        check("失败统计成功率归零", broken_stats["runs"]["success_rate"], 0.0)
        check("失败统计点名岗位与错误",
              (broken_stats_node["node_id"], broken_stats_node["error"], "文本输入节点为空" in broken_stats_node["last_error"]),
              ("t", 2, True))
        check("失败统计带可回放现场",
              {row["job_id"] for row in broken_stats["failures"]}, {broken_run["id"], retry_run["id"]})
        check("对比跨项目作业 404",
              client.get(f"/api/factory/projects/{factory_id}/runs/compare?base={broken_run['id']}&target={first_run['id']}").status_code, 404)
        check("清理失败历史项目", client.delete(f"/api/factory/projects/{broken_id}").status_code, 200)

        second_run = client.post(f"/api/factory/projects/{factory_id}/run", json={}).json()["job"]
        deadline = time.time() + 90
        second_done = second_run
        while second_done.get("status") in ("queued", "running") and time.time() < deadline:
            time.sleep(0.4)
            second_done = client.get(f"/api/factory/jobs/{second_run['id']}").json()["job"]
        check("二跑全命中缓存", second_done["counts"].get("cached"), 12)
        check("二跑没有重跑岗位", second_done["counts"].get("ok", 0), 0)

        stats_runs_before = len(client.get(f"/api/factory/projects/{factory_id}/runs?limit=100").json()["runs"])
        stats = client.get(f"/api/factory/projects/{factory_id}/stats").json()["stats"]
        stats_limited = client.get(f"/api/factory/projects/{factory_id}/stats?limit=1").json()["stats"]
        stats_node = {row["node_id"]: row for row in stats["nodes"]}["n-sb"]
        check("统计窗口=2 次已结束运行", stats["window"], 2)
        check("统计成功率按作业算", stats["runs"]["success_rate"], 1.0)
        check("统计复用率=复用/(交付+复用)",
              (stats["runs"]["cached"], stats["runs"]["delivered"], stats["runs"]["cache_rate"]),
              (12, 12, 0.5))
        check("岗位画像按轮次聚合",
              (stats_node["appearances"], stats_node["ok"], stats_node["cached"], stats_node["cache_rate"]),
              (2, 1, 1, 0.5))
        check("部门汇总覆盖全部岗位出现",
              sum(row["appearances"] for row in stats["departments"]), 24)
        check("统计窗口可收窄", stats_limited["window"], 1)
        check("统计 limit 越界 422",
              client.get(f"/api/factory/projects/{factory_id}/stats?limit=0").status_code, 422)
        check("统计未知项目 404",
              client.get("/api/factory/projects/no-such-project/stats").status_code, 404)
        stats_runs_after = len(client.get(f"/api/factory/projects/{factory_id}/runs?limit=100").json()["runs"])
        check("统计只读不建作业", stats_runs_after, stats_runs_before)

        compare_runs_before = len(client.get(f"/api/factory/projects/{factory_id}/runs?limit=100").json()["runs"])
        compared = client.get(
            f"/api/factory/projects/{factory_id}/runs/compare?base={first_run['id']}&target={second_run['id']}"
        )
        check("运行对比 200", compared.status_code, 200)
        compare = compared.json()["compare"]
        compare_sb = {row["node_id"]: row for row in compare["nodes"]}["n-sb"]
        check("对比两侧都已完成", (compare["base"]["status"], compare["target"]["status"]), ("ok", "ok"))
        check("对比逐岗命中缓存", (compare["changes"]["cache_hit"], len(compare["nodes"])), (12, 12))
        check("对比没有回退或仍失败", (compare["changes"]["regressed"], compare["changes"]["still_failed"]), (0, 0))
        check("对比产物结构稳定", compare["changes"]["output_changed"], 0)
        check("对比点名岗位迁移", (compare_sb["change"], compare_sb["target"]["cached"]), ("cache_hit", True))
        compare_runs_after = len(client.get(f"/api/factory/projects/{factory_id}/runs?limit=100").json()["runs"])
        check("对比只读不建作业", compare_runs_after, compare_runs_before)
        check("对比同一次运行 400",
              client.get(f"/api/factory/projects/{factory_id}/runs/compare?base={first_run['id']}&target={first_run['id']}").status_code, 400)
        check("对比未知作业 404",
              client.get(f"/api/factory/projects/{factory_id}/runs/compare?base=frun-missing&target={second_run['id']}").status_code, 404)
        check("对比未知项目 404",
              client.get("/api/factory/projects/no-such-project/runs/compare?base=a&target=b").status_code, 404)
        check("对比缺参数 422",
              client.get(f"/api/factory/projects/{factory_id}/runs/compare?base={first_run['id']}").status_code, 422)

        force_preview_res = client.post(
            f"/api/factory/projects/{factory_id}/preview",
            json={"targets": ["n-sb"], "force_nodes": ["n-sb"]},
        )
        check("岗位强跑预检 200", force_preview_res.status_code, 200)
        force_preview = force_preview_res.json()["preview"]
        force_preview_sb = {row["node_id"]: row for row in force_preview["nodes"]}["n-sb"]
        check("强跑预检只重跑指定岗位",
              (force_preview["counts"]["run"], force_preview["counts"]["cached"], len(force_preview["plan"])),
              (1, len(force_preview["plan"]) - 1, len(force_preview["plan"])))
        check("强跑预检点名原因",
              (force_preview_sb["state"], "强制重跑" in force_preview_sb["reason"]), ("run", True))
        check("强跑预检记录名单", force_preview["force_nodes"], ["n-sb"])
        check("未知强跑岗位 400",
              client.post(f"/api/factory/projects/{factory_id}/preview",
                          json={"force_nodes": ["n-ghost"]}).status_code, 400)
        ignored_preview = client.post(
            f"/api/factory/projects/{factory_id}/preview",
            json={"targets": ["n-sb"], "force_nodes": ["n-sup"]},
        ).json()["preview"]
        check("计划外强跑如实忽略",
              (ignored_preview["force_nodes"], ignored_preview["ignored_force_nodes"]), ([], ["n-sup"]))

        force_accepted = client.post(
            f"/api/factory/projects/{factory_id}/run",
            json={"targets": ["n-sb"], "force_nodes": ["n-sb"]},
        ).json()["job"]
        deadline = time.time() + 90
        force_job = force_accepted
        while force_job.get("status") in ("queued", "running") and time.time() < deadline:
            time.sleep(0.4)
            force_job = client.get(f"/api/factory/jobs/{force_accepted['id']}").json()["job"]
        check("强跑作业受理记录",
              (force_job.get("force"), force_job.get("force_nodes"), force_job.get("ignored_force_nodes")),
              (False, ["n-sb"], []))
        force_counts = force_job.get("counts") or {}
        check("强跑只重跑指定岗位",
              (force_counts.get("ok"), force_counts.get("cached"), force_job.get("plan_size")),
              (1, len(force_preview["plan"]) - 1, len(force_preview["plan"])))
        force_nodes = (force_job.get("result") or {}).get("nodes") or {}
        check("强跑岗位标记与上游复用",
              (force_nodes["n-sb"].get("cached"), force_nodes["n-sb"].get("forced"),
               force_nodes["n-brief"].get("cached")),
              (False, True, True))

        override_preview_res = client.post(
            f"/api/factory/projects/{factory_id}/preview",
            json={"targets": ["n-sb"], "param_overrides": {"n-sb": {"max_shot_s": 6.5}}},
        )
        check("本轮改参预检 200", override_preview_res.status_code, 200)
        override_preview = override_preview_res.json()["preview"]
        override_plan_size = len(override_preview["plan"])
        override_preview_sb = {row["node_id"]: row for row in override_preview["nodes"]}["n-sb"]
        check("改参预检只重跑该岗位与下游",
              (override_preview["counts"]["run"], override_preview["counts"]["cached"]),
              (1, override_plan_size - 1))
        check("改参预检回显补丁与参数名",
              (override_preview["param_overrides"], override_preview_sb["override_keys"]),
              ({"n-sb": {"max_shot_s": 6.5}}, ["max_shot_s"]))
        check("改参预检点名签名变化",
              (override_preview_sb["state"], "改变签名" in override_preview_sb["reason"]), ("run", True))
        ignored_override_preview = client.post(
            f"/api/factory/projects/{factory_id}/preview",
            json={"targets": ["n-sb"], "param_overrides": {"n-sup": {"strict": True}}},
        ).json()["preview"]
        check("计划外改参如实忽略",
              (ignored_override_preview["param_overrides"], ignored_override_preview["ignored_param_overrides"]),
              ({}, ["n-sup"]))
        check("未知改参岗位 400",
              client.post(f"/api/factory/projects/{factory_id}/preview",
                          json={"param_overrides": {"n-ghost": {"max_shot_s": 6}}}).status_code, 400)
        check("改参顶层不是对象 400",
              client.post(f"/api/factory/projects/{factory_id}/preview",
                          json={"param_overrides": []}).status_code, 400)
        check("改参补丁不是对象 400",
              client.post(f"/api/factory/projects/{factory_id}/preview",
                          json={"param_overrides": {"n-sb": "硬切"}}).status_code, 400)

        override_accepted = client.post(
            f"/api/factory/projects/{factory_id}/run",
            json={"targets": ["n-sb"], "param_overrides": {"n-sb": {"max_shot_s": 6.5}}},
        ).json()["job"]
        deadline = time.time() + 90
        override_job = override_accepted
        while override_job.get("status") in ("queued", "running") and time.time() < deadline:
            time.sleep(0.4)
            override_job = client.get(f"/api/factory/jobs/{override_accepted['id']}").json()["job"]
        check("改参作业受理记录",
              (override_job.get("param_overrides"), override_job.get("ignored_param_overrides")),
              ({"n-sb": {"max_shot_s": 6.5}}, []))
        override_counts = override_job.get("counts") or {}
        check("改参只重跑该岗位",
              (override_counts.get("ok"), override_counts.get("cached"), override_job.get("plan_size")),
              (1, override_plan_size - 1, override_plan_size))
        override_nodes = (override_job.get("result") or {}).get("nodes") or {}
        check("改参岗位标记与上游复用",
              (override_nodes["n-sb"].get("cached"), override_nodes["n-sb"].get("overridden"),
               override_nodes["n-sb"].get("forced"), override_nodes["n-brief"].get("cached")),
              (False, True, False, True))
        override_stats = client.get(f"/api/factory/projects/{factory_id}/stats").json()["stats"]
        check("统计记录含改参轮次", override_stats["runs"].get("overridden", 0) >= 1, True)
        override_runs = client.get(f"/api/factory/projects/{factory_id}/runs").json()["runs"]
        check("历史摘要保留改参补丁",
              override_runs[0].get("param_overrides"), {"n-sb": {"max_shot_s": 6.5}})
        saved_factory = client.get(f"/api/factory/projects/{factory_id}").json()["project"]
        saved_sb = {node["id"]: node for node in saved_factory["graph"]["nodes"]}["n-sb"]
        check("改参不写入保存的画布", "max_shot_s" in saved_sb.get("params", {}), False)

        check("删项目回收", client.delete(f"/api/factory/projects/{factory_id}").json()["deleted"], factory_id)
        check("删除后项目 404", client.get(f"/api/factory/projects/{factory_id}").status_code, 404)
        check("删除后交付包 404", client.get(f"/api/factory/projects/{factory_id}/bundle").status_code, 404)
        check("删除后作业 404", client.get(f"/api/factory/jobs/{first_run['id']}").status_code, 404)

        print("[生成通道 span 对齐]")
        gen_trace = client.get(f"/api/tasks/{vid}/trace").json()
        span_names = {s.get("name") for s in gen_trace.get("spans", [])}
        check("imagegen.render span 存在", "imagegen.render" in span_names, True)
        check("videogen.render span 存在", "videogen.render" in span_names, True)
        check("petgen.render span 存在", "petgen.render" in span_names, True)
        vu_span_names = {s.get("name") for s in client.get(f"/api/tasks/{vu_id}/trace").json().get("spans", [])}
        check("videounderstand.understand span 存在", "videounderstand.understand" in vu_span_names, True)
        check("skillgen.distill span 存在", "skillgen.distill" in vu_span_names, True)
finally:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
    log.close()

print()
if failures:
    print(f"核验未通过：{len(failures)} 项 → {'、'.join(failures)}")
    raise SystemExit(1)
print("核验通过：新增接口在真实服务下响应符合契约。")
