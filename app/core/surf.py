"""灵感冲浪（Inspiration Surf）——像真人一样上网刷信息/看视频，消化成灵感库。

定位（务必如实理解）
--------------------
解决「创作时严重缺灵感」：本通道让系统像真人一样**上网冲浪**——检索、浏览页面、刷信息流、
记录视频信号——再把收集到的外部信号交给模型**消化成一批灵感卡**（点子 / 切入角度 /
开场钩子 / 为什么现在火 / 参考链接），**沉淀成一个跨任务的「灵感 idea 库」**，由用户自行
挑选、调整后再开始创作。灵感**不自动交付给智能体**，只入库供人选（这是产品刻意取舍）。

三条通道（``provider`` 可被请求体覆盖，``auto`` 择优解析成具体通道）：

* ``sample``（默认，零依赖、零外网）：离线把主题展开成查询，产**带 simulated 标记的占位
  灵感卡**（不联网、不调浏览器、走 mock 消化），保证 API/UI/回归可联调；
* ``browser``：真·无头浏览器（Playwright）navigate→滚动→抽取正文。**惰性** import，
  未安装或没配 ``SURF_ALLOWED_DOMAINS`` 白名单即**显式失败、绝不假装浏览过**；
* ``http``：复用系统既有联网检索网关（``app/core/web.py`` 的 web_search + page_fetch）做
  轻量冲浪，未配 ``WEB_SEARCH_API_URL`` 即显式失败；
* ``auto``：按 browser→http→sample 择优（装了 Playwright 且配了白名单→browser，否则配了
  搜索网关→http，否则 sample 并在卡里写明）。

诚实边界（项目铁律）
--------------------
* **离线只出自报的占位**：sample 或降级引擎消化出的灵感卡 ``simulated=True``，绝不冒充真实
  冲浪所得；真实灵感需配置真实 LLM 网关（``LLM_*``）+ browser/http 通道。
* **消化走系统既有模型网关**（``LLM_*``，不另持端点与密钥），与 skillgen llm 同一取舍，避免
  两处配置漂移。
* **安全**：browser 只访问 ``SURF_ALLOWED_DOMAINS`` 白名单内的公开站点，复用 web.py 的 SSRF
  拒绝口径（拒私网/环回/链路本地）；**不下载在线视频文件**。
* **看视频＝像真人在线看，不下载、不假装看懂**：在线视频通常**下载不下来**，所以走真人式在线看——
  ``watch_videos`` 开启后 browser 通道先记下起点页里可点进的视频链接（也接受请求里显式指定的
  ``video_urls``），逐条**进入视频页**：关掉 Cookie/登录遮罩→点播放按钮（点不动才静音调 ``play()``）→
  **回看播放状态确认真的在播**→播放中按间隔**截图抽帧**（只截播放器元素，帧与播放时刻一起记账）→
  把截图**按组**（``SURF_VLM_GROUP_FRAMES`` 张一组）逐组交 VLM（复用 ``LLM_*`` 多模态网关）只看该组画面
  并转写画面文字（OCR）→ 多组结论再交一次文本汇总成整段理解。
  同时**从 DOM 直抓**文字信息（标题 / 简介 / UP 主 / 播放量 / 发布日期 / 逐条评论，先点「展开更多」并
  滚到评论区触发懒加载）；DOM 抓不到的（含评论里的字）由截图 OCR 兜底。
  离线/mock、没配视觉网关、没播起来或画面时刻没推进时，**如实标注**（``simulated`` / ``played`` /
  ``stalled`` / 告警），绝不把封面页当「看懂了」；关闭时视频只作**信号**记录（标题/链接）。

接入形态：**旁路异步作业通道**（job store，镜像 videounderstand），产物**不落黑板**（避开
新增 Artifact 类型改四处契约，MEMORY #31）；另有独立的**灵感池**（跨任务 idea 库）持久化。
"""

from __future__ import annotations

import base64
import random
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from ..config import DATA_DIR, STORAGE_MODE, get_config
from ..llm.engine import _is_local_endpoint, chat, vision_enabled
from ..llm.json_utils import as_str, as_obj, extract_json
from ..llm.types import ChatMessage, ImagePart, LLMRequest
from ..logger import create_logger
from .clock import now_iso
from .events import event_bus, new_id
from .gen_jobs import FileJobBackend, PgJobBackend, make_backend
from .types import TaskRecord
from .web import (
    WebUnavailable,
    _ensure_public_url,
    fetch_page,
    search_configured,
    unavailable_reason,
    web_search,
)

log = create_logger("surf")

#: 会话状态机：queued（受理）→ surfing（浏览/抓取中）→ digesting（模型消化中）→ done / failed
STATUSES = ("queued", "surfing", "digesting", "done", "failed")

#: 会话存储（file 模式 JSON；pg 模式 surf_jobs 表，见 storage_contract.md）
STORE_FILE = DATA_DIR / "surf_sessions.json"
_TABLE = "surf_jobs"

#: 灵感池（跨任务 idea 库）存储：file JSON / pg surf_insights 表
POOL_STORE_FILE = DATA_DIR / "surf_pool.json"
_POOL_TABLE = "surf_insights"

#: 跨会话「读过哪些页」的浏览历史存储：file JSON / pg surf_history 表（同灵感池，不随任务删除）
HISTORY_STORE_FILE = DATA_DIR / "surf_history.json"
_HISTORY_TABLE = "surf_history"

#: 单次消化（一次 LLM 调用）的保守成本估算（美元），仅用于预算**事前**熔断，真实计费归网关侧。
_EST_COST_PER_DIGEST_USD = 0.01

#: sample 通道占位推进的总秒数（纯演示进度感；到点即完成，不调任何外部服务）
_SAMPLE_SECONDS = 4

#: 信号摘录进入消化提示词的字数上限（冲浪是旁路情报，不能挤占消化预算）
_SIGNAL_EXCERPT_CHARS = 320
#: 单次消化最多纳入的信号条数
_MAX_SIGNALS_FOR_DIGEST = 40

#: 单次「看懂一个视频」里**一次**模型调用的保守成本估算（美元）：分组截图每组一次多模态调用，
#: 多组再追加一次纯文本汇总调用，故按调用次数计（见 ``_estimate_cost``）。
_EST_COST_PER_VLM_CALL_USD = 0.01
#: 无论配置多少，单个视频实际抽帧/发给 VLM 的截图硬上限（护住多模态请求体积与成本）。
_MAX_FRAMES_FOR_VLM = 24
#: 一组截图的默认张数（真人是一段一段看的：每组一次多模态调用，最后汇成整段理解）
_DEFAULT_VLM_GROUP_FRAMES = 4
#: 单条评论进入消化提示词的长度上限（评论是旁证，不能挤占灵感消化的预算）
_COMMENT_ITEM_CHARS = 300


class SurfUnavailable(RuntimeError):
    """冲浪不可用（浏览器未装 / 未配白名单 / 搜索网关未配 / 抓取全失败）。

    受理或推进时抛出，会话**显式置 failed 并点名具体环境变量**，绝不假装浏览过。
    """


class SurfDigestError(RuntimeError):
    """消化环节失败：模型没有产出任何可解析的灵感卡。绝不拿空卡冒充有灵感。"""


# ------------------------------------------------------------------ #
# 后端单例（会话作业 + 灵感池）                                          #
# ------------------------------------------------------------------ #

_BACKEND: "FileJobBackend | PgJobBackend | None" = None
_BACKEND_LOCK = threading.RLock()


def _job_backend() -> "FileJobBackend | PgJobBackend":
    global _BACKEND
    if _BACKEND is None:
        with _BACKEND_LOCK:
            if _BACKEND is None:
                _BACKEND = make_backend(STORE_FILE, _TABLE)
    return _BACKEND


class FilePoolBackend:
    """灵感池 file 后端：进程内 {id: card} + 原子写盘（与 FileJobBackend 同行为，多 delete）。"""

    def __init__(self, store_file) -> None:
        self._file = store_file
        self._lock = threading.RLock()
        self._rows: dict[str, dict[str, Any]] | None = None
        self._dirty = False

    def _load_locked(self) -> dict[str, dict[str, Any]]:
        if self._rows is None:
            self._rows = {}
            try:
                import json

                raw = json.loads(self._file.read_text(encoding="utf-8"))
                for row in raw.get("cards") or []:
                    if isinstance(row, dict) and row.get("id"):
                        self._rows[str(row["id"])] = row
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as error:
                log.warn(f"灵感池读取失败（按空池继续）：{error}")
            self._dirty = False
        return self._rows

    def _save_locked(self) -> None:
        if not self._dirty:
            return
        import json

        payload = {"cards": list(self._rows.values())}
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        import os

        os.replace(tmp, self._file)
        self._dirty = False

    def add(self, rows: list[dict[str, Any]]) -> None:
        with self._lock:
            live = self._load_locked()
            for row in rows:
                live[str(row["id"])] = row
            self._dirty = True
            self._save_locked()

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(row) for row in self._load_locked().values()]

    def remove(self, card_id: str) -> bool:
        with self._lock:
            live = self._load_locked()
            if card_id in live:
                live.pop(card_id, None)
                self._dirty = True
                self._save_locked()
                return True
            return False

    def drop_task(self, task_id: str) -> int:
        with self._lock:
            live = self._load_locked()
            doomed = [cid for cid, row in live.items() if str(row.get("task_id")) == task_id]
            for cid in doomed:
                live.pop(cid, None)
            if doomed:
                self._dirty = True
                self._save_locked()
        return len(doomed)

    def prune(self, keep_max: int) -> int:
        with self._lock:
            live = self._load_locked()
            rows = sorted(live.values(), key=lambda r: str(r.get("created_at") or ""), reverse=True)
            doomed = [str(r["id"]) for r in rows[max(0, keep_max):]]
            for cid in doomed:
                live.pop(cid, None)
            if doomed:
                self._dirty = True
                self._save_locked()
        return len(doomed)


class PgPoolBackend:
    """灵感池 pg 后端：卡 dict 全量存 ``surf_insights.payload``（与 FilePoolBackend 同接口）。"""

    def __init__(self, table: str) -> None:
        self._table = table

    def add(self, rows: list[dict[str, Any]]) -> None:
        import json

        from .pg import pg_pool

        with pg_pool().connection() as conn:
            for row in rows:
                conn.execute(
                    f"""
                    INSERT INTO {self._table} (id, task_id, tenant, status, created_at, updated_at, payload)
                    VALUES (%s, %s, %s, %s, %s, now(), %s)
                    ON CONFLICT (id) DO UPDATE SET payload = EXCLUDED.payload, updated_at = now()
                    """,
                    (
                        str(row.get("id") or ""),
                        str(row.get("task_id") or ""),
                        str(row.get("tenant") or "default"),
                        "card",
                        str(row.get("created_at") or ""),
                        json.dumps(row, ensure_ascii=False),
                    ),
                )

    def snapshot(self) -> list[dict[str, Any]]:
        from .pg import pg_pool

        with pg_pool().connection() as conn:
            found = conn.execute(
                f"SELECT payload FROM {self._table} ORDER BY created_at DESC, id"
            ).fetchall()
        return [payload for (payload,) in found]

    def remove(self, card_id: str) -> bool:
        from .pg import pg_pool

        with pg_pool().connection() as conn:
            cursor = conn.execute(f"DELETE FROM {self._table} WHERE id = %s", (card_id,))
        return cursor.rowcount > 0

    def drop_task(self, task_id: str) -> int:
        from .pg import pg_pool

        with pg_pool().connection() as conn:
            cursor = conn.execute(
                f"DELETE FROM {self._table} WHERE task_id = %s", (task_id,)
            )
        return cursor.rowcount

    def prune(self, keep_max: int) -> int:
        from .pg import pg_pool

        keep = max(0, int(keep_max))
        with pg_pool().connection() as conn:
            cursor = conn.execute(
                f"""
                DELETE FROM {self._table} WHERE id IN (
                  SELECT id FROM {self._table}
                  ORDER BY created_at DESC, id DESC
                  OFFSET %s
                )
                """,
                (keep,),
            )
        return cursor.rowcount


_POOL: "FilePoolBackend | PgPoolBackend | None" = None
_POOL_LOCK = threading.RLock()


def _pool_backend():
    global _POOL
    if _POOL is None:
        with _POOL_LOCK:
            if _POOL is None:
                _POOL = (
                    PgPoolBackend(_POOL_TABLE)
                    if STORAGE_MODE == "pg"
                    else FilePoolBackend(POOL_STORE_FILE)
                )
    return _POOL


#: 浏览历史后端与灵感池同构（同样是 {id: row} 的 add/snapshot/remove），故复用同一对类
_HISTORY: "FilePoolBackend | PgPoolBackend | None" = None
_HISTORY_LOCK = threading.RLock()


def _history_backend():
    global _HISTORY
    if _HISTORY is None:
        with _HISTORY_LOCK:
            if _HISTORY is None:
                _HISTORY = (
                    PgPoolBackend(_HISTORY_TABLE)
                    if STORAGE_MODE == "pg"
                    else FilePoolBackend(HISTORY_STORE_FILE)
                )
    return _HISTORY


# ------------------------------------------------------------------ #
# 主题、查询展开与安全边界                                              #
# ------------------------------------------------------------------ #


def _topic_from_task(task: TaskRecord) -> str:
    """从 Brief 拼一条冲浪主题（品牌/产品/行业，退化到渠道），不引外部词表。"""
    brief = task.brief
    parts = [brief.brand, brief.product, brief.industry]
    topic = " ".join(part for part in parts if part).strip()
    return topic or str(brief.channel or "").strip()


def _expand_queries(topic: str, cfg) -> list[str]:
    """把主题确定性地展开成几条搜索查询（查询串不是创作产出，无需模型，也就不存在假装）。"""
    base = (topic or "").strip()
    if not base:
        return []
    angles = ("趋势 热点", "爆款 案例", "做法 技巧")
    queries = [base] + [f"{base} {angle}" for angle in angles]
    return [item for item in queries if item][: max(1, cfg.queries_per_topic)]


def _host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def _domain_allowed(url: str, allowed: set[str]) -> bool:
    host = _host_of(url)
    return bool(host) and any(host == dom or host.endswith("." + dom) for dom in allowed)


def _allowed_and_public(url: str, allowed: set[str]) -> bool:
    """browser 通道的安全闸门：必须在白名单域内，且复用 web.py 的 SSRF 私网/环回拒绝。"""
    if not _domain_allowed(url, allowed):
        return False
    try:
        _ensure_public_url(url)
    except WebUnavailable:
        return False
    return True


def _condense(text: str, limit: int = _SIGNAL_EXCERPT_CHARS) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


# ------------------------------------------------------------------ #
# 信号采集：sample / http / browser                                    #
# ------------------------------------------------------------------ #


def _gather_sample(topic: str, cfg) -> list[dict[str, Any]]:
    count = min(max(1, cfg.max_pages), 4)
    return [
        {
            "kind": "hot",
            "title": f"{topic}·热榜占位 {index}",
            "url": "",
            "excerpt": "（离线占位信号：未联网、未真实抓取，仅供联调与回归）",
        }
        for index in range(1, count + 1)
    ]


def _gather_http(topic: str, cfg) -> list[dict[str, Any]]:
    """复用既有检索网关：逐查询 web_search，再抓前若干结果页正文，汇成信号。"""
    if not search_configured():
        raise SurfUnavailable(f"http 冲浪需要先配置联网检索：{unavailable_reason()}")
    queries = _expand_queries(topic, cfg)
    if not queries:
        raise SurfUnavailable("http 冲浪没有可用主题（主题为空）")

    signals: list[dict[str, Any]] = []
    pending_urls: list[str] = []
    for query in queries:
        try:
            results = web_search(query)
        except WebUnavailable as error:
            raise SurfUnavailable(f"检索失败（{query}）：{error}") from error
        for item in results:
            signals.append(
                {
                    "kind": "search",
                    "title": as_str(item.get("title")),
                    "url": as_str(item.get("url")),
                    "excerpt": _condense(as_str(item.get("snippet"))),
                    "query": query,
                }
            )
            if item.get("url"):
                pending_urls.append(str(item["url"]))
        if len(signals) >= cfg.max_pages * 3:
            break

    # 抓页受 surf.max_pages 约束；web.py 的 fetch_page 未配/失败一律 WebUnavailable，
    # 单页抓不到不影响其它信号（如实少收，绝不假装抓到）。
    if pending_urls:
        fetched = 0
        for url in pending_urls:
            if fetched >= cfg.max_pages:
                break
            try:
                page = fetch_page(url)
            except WebUnavailable:
                continue  # 单页失败不影响其它信号，如实少收
            signals.append(
                {
                    "kind": "page",
                    "title": as_str(page.get("title")),
                    "url": as_str(page.get("url")),
                    "excerpt": _condense(as_str(page.get("text"))),
                }
            )
            fetched += 1

    if not signals:
        raise SurfUnavailable("http 冲浪没有拿到任何检索结果（网关返回为空或全部不可解析）")
    return signals


# ------------------------------------------------------------------ #
# 看视频（仅 browser）：像真人在线看                                       #
#   进入视频页 → 关遮罩点播放 → 校验真的在播 → 播放中分组截图抽帧            #
#   → 每组截图交 VLM（多模态）→ 多组结果 + DOM 文字交文本汇总 → 看懂视频      #
# ------------------------------------------------------------------ #

#: 每组截图的多模态调用：只看画面，客观描述 + 转写画面文字（OCR）
_SURF_FRAMES_SYSTEM = (
    "你是内容创作台「灵感冲浪手」的**画面看懂**模块。给你的是**同一个在线视频在播放过程中截取的一组画面截图**"
    "（按播放时间顺序、不含音频，也不是完整视频）。任务：\n"
    "1) 用几句话**只描述你从这些画面里真实看到的内容**（场景 / 人物 / 动作 / 道具 / 关键镜头），"
    "看不到的绝不臆造，画面糊或信息不足就直说「画面信息不足」；\n"
    "2) 把画面里出现的**可读文字**（标题 / 字幕 / 贴纸 / 弹幕 / 评论区的字）原样转写出来，这就是 OCR；\n"
    "3) 判断这组画面里**视频是否处于播放状态**（画面在动 / 只是封面或暂停页）。\n"
    '只输出 JSON：{"segment_summary": "这组画面的客观描述", "on_screen_text": "转写到的画面文字", '
    '"is_playing": true|false, "confidence": "high|medium|low"}'
)

#: 整段汇总：把各组画面结论 + 页面直抓的标题/简介/评论合成「看懂结论 + 可借鉴创作点」
_SURF_MERGE_SYSTEM = (
    "你是内容创作台「灵感冲浪手」的**整段看懂**模块。给你的是：同一个在线视频按播放顺序**分组截图**后"
    "逐组看懂得到的画面结论（含每组转写的画面文字 OCR），以及从视频页 DOM 直接抓到的**标题 / 简介 / 评论文字**。\n"
    "任务：1) 汇成一段对整个视频的客观描述（只保留各组画面里真实出现的内容，不要补充想象）；"
    "2) 汇总画面文字（OCR）；3) 结合标题、简介与评论，凝练出**为什么可能火 / 可借鉴的创作点**"
    "（评论里的争议点、高赞梗、观众诉求都算线索）；4) 数据不足时如实说明并给低置信。\n"
    '只输出 JSON：{"visual_summary": "整段视频画面内容描述", "on_screen_text": "画面/截图里转写到的文字汇总", '
    '"comment_insights": "结合标题简介与评论的洞察", "confidence": "high|medium|low"}'
)

#: 截图 OCR 兜底：DOM 抓不到标题/简介/评论时，截整页图交模型**逐字转写**（读不到就留空，绝不补全）
_SURF_OCR_SYSTEM = (
    "你是内容创作台「灵感冲浪手」的**页面文字转写**模块。给你的是**一个在线视频页面的整页截图**"
    "（可能含标题、简介、UP 主、播放量、发布日期与评论区）。任务：\n"
    "1) **逐字转写**你在这张图里真实看到的文字；图上没有或看不清的字段留空字符串，"
    "绝不根据常识补全、绝不臆造；\n"
    "2) 评论按图里出现的顺序逐条列出（作者 / 内容 / 点赞数，缺哪项哪项留空）。\n"
    '只输出 JSON：{"title": "", "description": "", "uploader": "", "views": "", "publish_date": "", '
    '"comments": [{"author": "", "text": "", "likes": ""}], "confidence": "high|medium|low"}'
)

#: 常见播放按钮（真人会点的那个三角形）；顺序＝先站内播放器控件，再兜底点视频元素本身
_PLAY_SELECTORS = (
    ".vjs-big-play-button",
    "[class*='big-play' i]",
    "[class*='play-btn' i]",
    "[class*='playButton' i]",
    "button[aria-label*='play' i]",
    "button[aria-label*='播放' i]",
    ".bilibili-player-video-btn",
    "video",
)

#: 真人进页面先干的事：关掉 Cookie 同意 / 年龄确认 / 登录遮罩（关不掉就照常尝试播放，不假装关掉）
_CONSENT_SELECTORS = (
    "button[aria-label*='accept' i]",
    "button[aria-label*='close' i]",
    "button:has-text('同意')",
    "button:has-text('接受')",
    "button:has-text('我已阅读')",
    "button:has-text('Accept')",
    "button:has-text('AGREE')",
    "[id*='onetrust-accept' i]",
    ".consent-modal button",
)

#: 视频页链接特征（真人从信息流点进视频页；命中才纳入观看候选，非视频页链接不浪费预算）
_VIDEO_URL_RE = re.compile(
    r"(youtube\.com/watch|youtu\.be/|bilibili\.com/video|/[av][bvp]?1?\d{5,}"
    r"|/video/|/watch/|watch\?[a-z]*id=|douyin\.com/video|tiktok\.com/@[^/]+/video|\.mp4(\?|$))",
    re.I,
)

#: 逐条评论的候选容器（各站点类名不同，命中第一个有结果的口径就停；全落空则如实返回空列表）
_COMMENT_ITEM_SELECTORS = (
    "[data-testid='comment']",
    "ytd-comment-view-model",
    "ytd-comment-thread-renderer #content-text",
    ".comment-item",
    ".reply-item",
    "[class*='commentList' i] li",
    "[class*='comment' i] .content",
)

#: 页内主视频探测：找**可见面积最大**的 <video>，带回播放状态（真人「看一眼是否在播」）
_PROBE_VIDEO_JS = """() => {
  const vids = Array.from(document.querySelectorAll('video'));
  if (!vids.length) return {count: 0};
  const area = (v) => { const r = v.getBoundingClientRect(); return Math.max(0, r.width) * Math.max(0, r.height); };
  const v = vids.slice().sort((a, b) => area(b) - area(a))[0];
  const r = v.getBoundingClientRect();
  return {
    count: vids.length,
    paused: !!v.paused,
    ended: !!v.ended,
    muted: !!v.muted,
    current_time: Number(v.currentTime) || 0,
    duration: Number(isFinite(v.duration) ? v.duration : 0) || 0,
    ready_state: Number(v.readyState) || 0,
    width: Math.round(r.width),
    height: Math.round(r.height),
  };
}"""

#: 兜底播放：静音后调 play()（浏览器自动播放策略只放行静音，这是真人点不动时的等价动作）
_FORCE_PLAY_JS = """() => {
  const vids = Array.from(document.querySelectorAll('video'));
  if (!vids.length) return false;
  const area = (v) => { const r = v.getBoundingClientRect(); return Math.max(0, r.width) * Math.max(0, r.height); };
  const v = vids.slice().sort((a, b) => area(b) - area(a))[0];
  v.muted = true;
  if (v.paused) { const p = v.play(); if (p && p.catch) p.catch(() => {}); }
  return true;
}"""

#: 逐条评论收集：按候选容器取文本，附作者与点赞（有就取，没有留空，绝不编造）
_COLLECT_COMMENTS_JS = """(args) => {
  const selectors = args.selectors, limit = args.limit;
  const clean = (t) => (t || '').replace(/\\s+/g, ' ').trim();
  for (const sel of selectors) {
    const nodes = Array.from(document.querySelectorAll(sel));
    const out = [];
    for (const node of nodes) {
      const text = clean(node.innerText).slice(0, args.itemChars);
      if (!text) continue;
      const authorEl = node.querySelector("[class*='author' i],[class*='user' i],[class*='name' i]");
      const likeEl = node.querySelector("[class*='like' i],[class*='vote' i]");
      out.push({author: authorEl ? clean(authorEl.innerText).slice(0, 40) : '',
                text: text,
                likes: likeEl ? clean(likeEl.innerText).slice(0, 20) : ''});
      if (out.length >= limit) break;
    }
    if (out.length) return out;
  }
  return [];
}"""


def _find_video(page):
    """在主页与所有 iframe 里找播放器（很多站把播放器嵌在 iframe 内）。

    返回 ``(frame 或 None, probe dict)``；probe 为空 dict = 页面上确实没有视频，**不假装找到**。
    """
    for frame in list(getattr(page, "frames", []) or []):
        try:
            probe = frame.evaluate(_PROBE_VIDEO_JS)
        except Exception:  # noqa: BLE001 - 某个 frame 读不到继续下一个
            continue
        if isinstance(probe, dict) and int(probe.get("count") or 0) > 0:
            return frame, probe
    return None, {}


def _page_has_video(page) -> bool:
    return bool(_find_video(page)[1])


def _dismiss_blockers(page, attempts: int = 6) -> int:
    """像真人一样先把遮罩（Cookie 同意 / 登录弹窗 / 年龄确认）点掉，返回实际点掉的个数。"""
    closed = 0
    for sel in _CONSENT_SELECTORS:
        if closed >= attempts:
            break
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=1_500)
                closed += 1
                page.wait_for_timeout(300)
        except Exception:  # noqa: BLE001 - 一个遮罩点不掉不阻断其它
            continue
    return closed


def _click_play(page, frame, timeout_ms: int) -> None:
    """点常见播放按钮（页面与播放器所在 frame 各试一次），点不动再由调用方兜底 JS 播放。"""
    for scope in (page, frame):
        if scope is None:
            continue
        for sel in _PLAY_SELECTORS:
            try:
                loc = scope.locator(sel).first
                if loc.count() > 0:
                    loc.click(timeout=min(max(1_000, timeout_ms), 5_000))
                    page.wait_for_timeout(400)
                    return
            except Exception:  # noqa: BLE001 - 遮罩拦截/选择器不存在，换下一个
                continue


def _play_like_human(page, cfg) -> tuple[bool, dict[str, Any]]:
    """真人式播放：关遮罩→点播放按钮→静音兜底→**回看播放状态确认真的在播**。

    返回 ``(played, probe)``：``played`` 只描述「页面里主视频当前是否非暂停」，
    点不动/被自动播放策略拦住就如实 False——后续截图仍可作「封面/暂停页」证据，但**绝不声称在播**。
    """
    timeout_ms = max(1_000, cfg.timeout_ms)
    _dismiss_blockers(page)
    frame, probe = _find_video(page)
    if not probe:
        return False, {}
    if probe.get("paused"):
        _click_play(page, frame, timeout_ms)
        try:
            frame.evaluate(_FORCE_PLAY_JS)
        except Exception:  # noqa: BLE001 - 兜底播放失败也只是没播成
            pass
        try:
            frame.wait_for_function(
                "() => { const v = document.querySelector('video'); return !!v && !v.paused; }",
                timeout=min(max(2_000, cfg.settle_ms * 2), 8_000),
            )
        except Exception:  # noqa: BLE001 - 等不到 playing 就按「未播成」处理
            pass
        frame, probe = _find_video(page)
    played = bool(probe) and not bool(probe.get("paused")) and not bool(probe.get("ended"))
    return played, probe


def _grab_shot(page, frame, cfg) -> str | None:
    """截一张画面：``crop_to_video`` 时只截播放器元素（含 iframe 内播放器），否则整页截图。"""
    if cfg.crop_to_video and frame is not None:
        try:
            raw = frame.locator("video").first.screenshot(type="png", timeout=5_000)
            if raw:
                return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")
        except Exception:  # noqa: BLE001 - 元素截图失败（尺寸为 0/被遮挡）退回整页
            pass
    try:
        raw = page.screenshot(type="png")
    except Exception:  # noqa: BLE001 - 截图被页面拦截就如实少截，绝不编造帧
        return None
    if not raw:
        return None
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def _capture_frames(page, cfg) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """播放中按间隔抽帧（帧只在内存里交 VLM，**不落库、不下载视频文件**）。

    每帧记 ``at``（播放时刻秒）与 ``advancing``（相对上一帧是否前进）——这是「真的在看」的证据；
    播完（``ended``）提前收手，全部帧都没前进时如实标注 ``stalled``（可能根本没播起来）。
    返回 ``(frames, evidence)``。
    """
    target = max(1, min(int(cfg.video_frames or 1), _MAX_FRAMES_FOR_VLM))
    interval = max(0, int(cfg.video_frame_interval_ms))
    frames: list[dict[str, Any]] = []
    previous_time: float | None = None
    duration = 0.0
    for _ in range(target):
        frame, probe = _find_video(page)
        shot = _grab_shot(page, frame, cfg)
        if not shot:
            break
        current = float(probe.get("current_time") or 0.0) if probe else 0.0
        duration = float(probe.get("duration") or 0.0) or duration
        frames.append(
            {
                "data_url": shot,
                "at": round(current, 2),
                "advancing": previous_time is not None and current > previous_time + 0.05,
                "playing": bool(probe) and not bool(probe.get("paused")),
            }
        )
        previous_time = current
        if probe.get("ended"):
            break
        page.wait_for_timeout(interval)
    advancing_frames = sum(1 for item in frames if item["advancing"])
    moments = [float(item["at"]) for item in frames]
    evidence = {
        "frames": len(frames),
        "advancing_frames": advancing_frames,
        "watched_seconds": round(max(moments) - min(moments), 2) if len(moments) > 1 else 0.0,
        "duration": round(duration, 2),
        # 截了多张但播放时刻一次都没前进 = 根本没播起来（如实标注，不假装看过内容）
        "stalled": bool(len(frames) > 1 and advancing_frames == 0),
    }
    return frames, evidence


def _first_text(page, selectors: tuple[str, ...], limit: int) -> str:
    """DOM 里按候选选择器取第一段非空文字（各站类名不同，全落空就留空、绝不编造）。"""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() <= 0:
                continue
            txt = (
                (loc.get_attribute("content") or "")
                if sel.startswith("meta[")
                else (loc.inner_text() or "")
            ).strip()
            if txt:
                return _condense(txt, limit)
        except Exception:  # noqa: BLE001 - 选择器不存在/不可读，继续下一个
            continue
    return ""


def _expand_collapsed(page, labels: tuple[str, ...]) -> int:
    """真人会先点「展开更多」再读简介与评论——不点只会抓到被截断的第一行。返回点到的次数。"""
    clicked = 0
    for label in labels:
        try:
            loc = page.locator(f"text={label}").first
            if loc.count() > 0 and loc.is_visible():
                loc.click(timeout=1_500)
                clicked += 1
                page.wait_for_timeout(300)
        except Exception:  # noqa: BLE001 - 没有这个按钮/点不动，照常往下读
            continue
    return clicked


def _collect_comments(page, cfg) -> list[dict[str, str]]:
    """滚到评论区触发懒加载→逐条抓评论（作者/内容/点赞）。抓不到就空列表，绝不编造评论。"""
    _expand_collapsed(page, ("查看全部评论", "更多评论", "展开评论"))
    for _ in range(max(1, min(cfg.scroll_rounds, 4))):
        try:
            page.mouse.wheel(0, 2_400)
            page.wait_for_timeout(max(200, cfg.settle_ms))
        except Exception:  # noqa: BLE001 - 滚不动就按已加载到的部分如实收集
            break
    try:
        raw = page.evaluate(
            _COLLECT_COMMENTS_JS,
            {
                "selectors": list(_COMMENT_ITEM_SELECTORS),
                "limit": max(1, cfg.max_comments),
                "itemChars": _COMMENT_ITEM_CHARS,
            },
        )
    except Exception:  # noqa: BLE001 - 页面脚本不可注入 = 没抓到评论
        return []
    return _normalize_comments(raw if isinstance(raw, list) else [], cfg.max_comments)


def _normalize_comments(items: Any, limit: int) -> list[dict[str, str]]:
    """评论归一（纯函数、可离线回归）：字符串或 dict 都收，去空去重，按 limit 截断。"""
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items or []:
        if isinstance(item, str):
            row = {"author": "", "text": item, "likes": ""}
        elif isinstance(item, dict):
            row = {
                "author": _condense(as_str(item.get("author")), 40),
                "text": _condense(as_str(item.get("text")), _COMMENT_ITEM_CHARS),
                "likes": _condense(as_str(item.get("likes")), 20),
            }
        else:
            continue
        if not row["text"] or row["text"] in seen:
            continue
        seen.add(row["text"])
        rows.append(row)
        if len(rows) >= max(1, int(limit or 1)):
            break
    return rows


def _fill_missing_text(meta: dict[str, Any], ocr: dict[str, Any], limit: int) -> bool:
    """把截图 OCR 转写**只填进 DOM 没抓到的字段**（DOM 直抓的真实文字永远优先），返回是否用到 OCR。

    纯函数、可离线回归：绝不覆盖已有文字，也绝不在两边都没抓到时留下空壳字段。
    """
    if not ocr:
        return False
    used = False
    for key, cap in (("title", 200), ("description", 600), ("uploader", 60), ("views", 40), ("publish_date", 40)):
        if str(meta.get(key) or "").strip():
            continue
        value = _condense(str(ocr.get(key) or "").strip(), cap)
        if value:
            meta[key] = value
            used = True
    if not (meta.get("comments") or []):
        rows = _normalize_comments(ocr.get("comments") or [], limit)
        if rows:
            meta["comments"] = rows
            meta["comments_from_ocr"] = True
            used = True
    if used:
        meta["text_from_ocr"] = True
    return used


def _video_meta(page, cfg) -> dict[str, Any]:
    """视频页的文字信息（真人会读的那部分）：标题/简介/UP 主/播放量/日期 + 逐条评论。

    全部走 DOM 直抓（尽力而为）；抓不到的字段留空，交由截图 OCR（``_ocr_page_text``）补。
    """
    _expand_collapsed(page, ("展开更多", "显示更多", "See more", "Show more"))
    try:
        doc_title = page.title() or ""
    except Exception:  # noqa: BLE001
        doc_title = ""
    meta: dict[str, Any] = {
        "title": _condense(
            doc_title or _first_text(page, ("h1", "[class*='title' i]"), 200), 200
        ),
        "description": _first_text(
            page,
            (
                "meta[property='og:description']",
                "[class*='description' i]",
                "[class*='desc' i]",
                "[class*='summary' i]",
            ),
            600,
        ),
        "uploader": _first_text(
            page, ("[class*='uploader' i]", "[class*='author' i]", "a[rel='author']"), 60
        ),
        "views": _first_text(page, ("[class*='view' i]", "[class*='play-count' i]"), 40),
        "publish_date": _first_text(page, ("[class*='date' i]", "time"), 40),
        "comments": [],
    }
    if cfg.scrape_comments:
        meta["comments"] = _collect_comments(page, cfg)
    return meta


def _ocr_page_text(page, cfg) -> dict[str, Any]:
    """DOM 抓不到页面文字时的**截图 OCR 兜底**：滚到简介/评论区，截一张整页图交模型逐字转写。

    真人遇到「文字被折叠/本来就是图」时就是这样看的。没有图像输入能力时**根本不交模型**
    （文本模型读不到图，交图只会逼它编造），如实返回空。
    """
    if not vision_enabled():
        return {}
    _expand_collapsed(page, ("展开更多", "显示更多", "See more", "Show more"))
    for _ in range(max(1, min(cfg.scroll_rounds, 3))):
        try:
            page.mouse.wheel(0, 1_600)
            page.wait_for_timeout(max(200, cfg.settle_ms))
        except Exception:  # noqa: BLE001 - 滚不动就按当前画面转写
            break
    # 整页截图（不是播放器元素）：简介与评论区的文字在播放器之外
    shot = _grab_shot(page, None, cfg)
    if not shot:
        return {}
    response = chat(
        LLMRequest(
            purpose="SURF.videounderstand.ocr",
            json=True,
            messages=[
                ChatMessage(role="system", content=_SURF_OCR_SYSTEM),
                ChatMessage(
                    role="user",
                    content="这是一张在线视频页面的整页截图，请逐字转写其中真实可见的文字，只输出 JSON。",
                    images=[ImagePart(url=shot, alt="视频页整页截图（含简介与评论区）")],
                ),
            ],
            context={"ocr_fallback": True},
        )
    )
    if _simulated_of(response):
        # 离线/降级引擎给的「转写」不是真读图结果，一律不采信
        return {}
    parsed = as_obj(extract_json(response.content))
    return {
        "title": as_str(parsed.get("title")).strip(),
        "description": as_str(parsed.get("description")).strip(),
        "uploader": as_str(parsed.get("uploader") or parsed.get("author")).strip(),
        "views": as_str(parsed.get("views")).strip(),
        "publish_date": as_str(parsed.get("publish_date") or parsed.get("date")).strip(),
        "comments": parsed.get("comments") if isinstance(parsed.get("comments"), list) else [],
    }


def _comments_text(meta: dict[str, Any], limit: int = 12) -> str:
    """把逐条评论压成给模型看的一段文字（作者：内容｜点赞）。"""
    collected = meta.get("comments") or []
    if isinstance(collected, str):
        collected = [chunk.strip() for chunk in re.split(r"[；;\n]", collected) if chunk.strip()]
    rows = []
    for raw in collected[:limit]:
        item = raw if isinstance(raw, dict) else {"author": "", "text": str(raw), "likes": ""}
        author = str(item.get("author") or "").strip()
        likes = str(item.get("likes") or "").strip()
        body = f"{author}：{item.get('text', '')}" if author else str(item.get("text") or "")
        if likes:
            body = f"{body}（{likes}）"
        rows.append(body.strip())
    return "；".join(rows)


def _page_text_for_prompt(meta: dict[str, Any]) -> str:
    """页面直抓/截图 OCR 的文字拼成提示词片段；两边都没抓到就说明只能靠画面截图。"""
    comments = _comments_text(meta)
    comment_source = "截图 OCR 逐字转写" if meta.get("comments_from_ocr") else "DOM 直抓"
    lines = [
        f"视频标题：{meta.get('title', '')}",
        f"简介：{meta.get('description', '')}",
        f"UP 主/作者：{meta.get('uploader', '') or '（DOM 与截图 OCR 都没拿到）'}",
        f"播放量：{meta.get('views', '') or '（DOM 与截图 OCR 都没拿到）'}",
        f"发布日期：{meta.get('publish_date', '') or '（DOM 与截图 OCR 都没拿到）'}",
        f"页面评论（{comment_source}）：{comments or '（没拿到评论文字；若截图里有评论画面请据图转写）'}",
    ]
    if meta.get("text_from_ocr"):
        lines.insert(0, "（下列页面文字部分来自整页截图 OCR 逐字转写，非 DOM 抓取）")
    return "\n".join(line for line in lines if line.split("：", 1)[-1].strip())


def _chunk_frames(frames: list[dict[str, Any]], group_size: int) -> list[list[dict[str, Any]]]:
    """把抽到的帧按播放顺序切成**一组一组**（真人是一段一段看的）：每组一次多模态调用。"""
    size = max(1, min(int(group_size or _DEFAULT_VLM_GROUP_FRAMES), _MAX_FRAMES_FOR_VLM))
    return [frames[start : start + size] for start in range(0, len(frames), size)]


def _simulated_of(response) -> bool:
    return bool(getattr(response, "simulated", False)) or bool(
        getattr(response, "degraded_reason", None)
    )


def _confidence_of(value: Any) -> str:
    level = (as_str(value) or "").strip().lower()
    return level if level in ("high", "medium", "low") else "low"


def _read_group(
    group: list[dict[str, Any]], index: int, total: int, meta: dict[str, Any]
) -> dict[str, Any]:
    """一组截图交 VLM（复用 ``LLM_*`` 多模态网关）：只描述这组画面 + 转写画面文字。"""
    images = [
        ImagePart(
            url=item["data_url"],
            alt=f"第 {index + 1}/{total} 组第 {step + 1} 帧（播放 {item['at']}s）",
        )
        for step, item in enumerate(group)
    ]
    timeline = "、".join(f"{item['at']}s" for item in group)
    response = chat(
        LLMRequest(
            purpose="SURF.videounderstand.frames",
            json=True,
            messages=[
                ChatMessage(role="system", content=_SURF_FRAMES_SYSTEM),
                ChatMessage(
                    role="user",
                    content=(
                        f"这是同一个在线视频的第 {index + 1}/{total} 组画面截图，按播放时间顺序，"
                        f"各帧播放时刻分别为 {timeline}。页面标题供参考：{meta.get('title', '')}。"
                        "请只依据画面本身作答，只输出 JSON。"
                    ),
                    images=images,
                ),
            ],
            context={
                "video": meta.get("title", ""),
                "group": index + 1,
                "groups": total,
                "frame_times": [item["at"] for item in group],
            },
        )
    )
    parsed = as_obj(extract_json(response.content))
    summary = as_str(
        parsed.get("segment_summary") or parsed.get("visual_summary") or parsed.get("summary")
    ).strip()
    on_screen = as_str(parsed.get("on_screen_text") or parsed.get("ocr_text")).strip()
    simulated = _simulated_of(response)
    if not summary and not simulated:
        raise SurfDigestError(
            f"第 {index + 1}/{total} 组画面看懂失败：模型没有产出可解析的画面描述，绝不拿空摘要冒充看懂"
        )
    return {
        "index": index + 1,
        "frames": len(group),
        "from": group[0]["at"],
        "to": group[-1]["at"],
        "summary": summary or "（离线占位：未真实看懂画面）",
        "on_screen_text": on_screen,
        "is_playing": bool(parsed.get("is_playing")),
        "confidence": _confidence_of(parsed.get("confidence")),
        "simulated": simulated,
    }


def _merge_segments(
    segments: list[dict[str, Any]], meta: dict[str, Any], evidence: dict[str, Any]
) -> dict[str, Any]:
    """把各组画面结论 + 页面直抓文字汇成整段看懂结论（一次纯文本调用，不再带图）。"""
    lines = []
    for seg in segments:
        line = (
            f"第 {seg['index']} 组（播放 {seg['from']}s–{seg['to']}s，{seg['frames']} 帧）："
            f"{seg['summary']}"
        )
        if seg["on_screen_text"]:
            line += f"｜画面文字：{seg['on_screen_text']}"
        lines.append(line)
    watch_note = (
        f"观看方式：在线播放中截图 {evidence.get('frames', 0)} 帧"
        f"（播放推进 {evidence.get('advancing_frames', 0)} 帧、观看 {evidence.get('watched_seconds', 0)}s"
        f"{'，画面未推进=可能没播起来' if evidence.get('stalled') else ''}）"
    )
    response = chat(
        LLMRequest(
            purpose="SURF.videounderstand.merge",
            json=True,
            messages=[
                ChatMessage(role="system", content=_SURF_MERGE_SYSTEM),
                ChatMessage(
                    role="user",
                    content="\n".join(
                        [_page_text_for_prompt(meta), watch_note, *lines] + ["请只输出 JSON。"]
                    )
                ),
            ],
            context={
                "video": meta,
                "groups": len(segments),
                "watch_evidence": evidence,
            },
        )
    )
    parsed = as_obj(extract_json(response.content))
    summary = as_str(
        parsed.get("visual_summary") or parsed.get("summary") or segments[0]["summary"]
    ).strip()
    on_screen = as_str(parsed.get("on_screen_text") or parsed.get("ocr_text")).strip() or " ".join(
        seg["on_screen_text"] for seg in segments if seg["on_screen_text"]
    )
    insights = as_str(parsed.get("comment_insights")).strip()
    simulated = _simulated_of(response)
    if not summary and not simulated:
        raise SurfDigestError("视频整段汇总失败：模型没有产出可解析的画面描述，绝不拿空摘要冒充看懂")
    return {
        "visual_summary": summary or "（离线占位：未真实看懂视频画面）",
        "on_screen_text": on_screen,
        "comment_insights": insights,
        "confidence": _confidence_of(parsed.get("confidence")),
        "simulated": simulated,
    }


def _understand_video(
    frames: list[dict[str, Any]],
    meta: dict[str, Any],
    cfg,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """**分组截图交 VLM**：每组一次多模态调用，多组再交一次文本汇总；离线/降级自报 ``simulated``。

    ``frames`` 为 ``_capture_frames`` 的产物（含播放时刻与推进证据），``meta`` 为 DOM 直抓的
    标题/简介/评论。没截到图 = 诚实失败（无法看懂）；真实网关没产出可解析画面描述 = 失败，
    **绝不拿空摘要冒充看懂**。当前模型不具备图像输入能力（``LLM_VISION`` 未开或离线引擎）时
    **根本不调模型描述画面**——那只会逼它编造——而是如实返回 ``vision_skipped`` 占位。
    """
    if not frames:
        raise SurfUnavailable(
            "视频抽帧失败：一张画面都没截到（可能播放按钮未响应或页面拦截截图），无法看懂"
        )
    evidence = dict(evidence or {})
    evidence.setdefault("frames", len(frames))
    if not vision_enabled():
        # 没有图像输入能力就不交 VLM：文本模型收不到画面却让它「描述画面」= 逼它编造。
        # 页面 DOM 文字（标题/简介/评论）仍是真实信号，随信号一起交给灵感消化。
        return {
            "visual_summary": (
                "（未看懂画面：当前模型不具备图像输入能力，需配支持图像输入的多模态网关"
                "并打开 LLM_VISION）"
            ),
            "on_screen_text": "",
            "comment_insights": "",
            "confidence": "low",
            "frames_used": len(frames),
            "groups_used": 0,
            "segments": [],
            "merged": False,
            "simulated": True,
            "vision_skipped": True,
        }
    groups = _chunk_frames(frames, _group_frames(cfg))
    segments = [
        _read_group(group, index, len(groups), meta)
        for index, group in enumerate(groups)
    ]
    page_text = _page_text_for_prompt(meta)
    merged = len(groups) > 1 or bool(page_text.strip())
    if merged:
        final = _merge_segments(segments, meta, evidence)
    else:
        head = segments[0]
        final = {
            "visual_summary": head["summary"],
            "on_screen_text": head["on_screen_text"],
            "comment_insights": "",
            "confidence": head["confidence"],
            "simulated": head["simulated"],
        }
    return {
        **final,
        "frames_used": len(frames),
        "groups_used": len(groups),
        "segments": segments,
        "merged": merged,
        "simulated": bool(final["simulated"]) or any(seg["simulated"] for seg in segments),
    }


#: 把页面上所有链接连**标题**一起读回来（真人点视频前先看的是这条链接写的什么，不是它的地址）
_LINK_TEXT_HREF_JS = """() => {
  const out = [];
  for (const a of Array.from(document.querySelectorAll('a[href]')).slice(0, 400)) {
    const raw = (a.innerText || a.textContent || a.getAttribute('aria-label')
      || a.getAttribute('title') || '').trim();
    out.push({ text: raw.split(' ').filter(Boolean).join(' ').slice(0, 160), href: a.href || '' });
  }
  return out;
}"""


def _discover_video_links(
    page, allowed: set[str], limit: int, *, topic_tokens: tuple[str, ...] = ()
) -> list[tuple[str, str, str]]:
    """像真人刷信息流时那样记下「可点进去看的视频」：返回 ``(链接, 列表里看到的标题, 凭哪几个词点的)``。

    只收白名单域内的链接（越界不收）；给了 ``topic_tokens`` 就**先扫标题**——认出主题词的排到前面
    （真人是从标题决定点哪个视频看的），一个都没认出也照样按页面顺序记前几条，但 ``picked_for`` 留空，
    由调用方如实说明「这几条是顺路看到的」，不假装都是照着主题挑的。
    """
    rows: Any
    try:
        rows = page.eval_on_selector_all("a[href]", _LINK_TEXT_HREF_JS)
    except Exception:  # noqa: BLE001 - 取不到链接就当没发现，不编造
        return []
    found: list[tuple[int, int, str, str, str]] = []
    seen: set[str] = set()
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        href = str(row.get("href") or "").strip()
        if not href or href in seen or not _VIDEO_URL_RE.search(href):
            continue
        if not _allowed_and_public(href, allowed):
            continue
        seen.add(href)
        title = _condense(str(row.get("text") or ""), 120)
        score, hits = _relevance(title, topic_tokens)
        found.append((-score, len(found), href, title, "、".join(hits[:4])))
    found.sort(key=lambda item: (item[0], item[1]))
    return [(href, title, why) for _score, _order, href, title, why in found[: max(1, int(limit))]]


def _watch_one_video(page, url: str, cfg, *, via: str = "") -> tuple[dict[str, Any], bool]:
    """在线看**一个**视频页：进页→像真人点播放并校验真的在播→播放中分组抽帧→逐组交 VLM→整段汇总。

    返回 ``(信号 dict, 是否看懂成功)``。看懂失败不拖垮整轮：如实记为 ``video_error`` 信号。
    全程**不下载视频文件**，截图只在内存里交模型。``via`` 记录这条视频是怎么找到的
    （direct=请求里指定的链接 / seed=起点站本页有播放器 / feed=从信息流里点进的链接）。
    """
    timeout_ms = max(1_000, cfg.timeout_ms)
    try:
        page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
    except Exception as error:  # noqa: BLE001 - 进不去这页就如实记失败
        return (
            {
                "kind": "video_error",
                "title": f"视频页访问失败：{url}",
                "url": url,
                "via": via,
                "excerpt": f"{type(error).__name__}: {error}",
            },
            False,
        )

    try:
        meta = _video_meta(page, cfg)
    except Exception:  # noqa: BLE001 - 文字信息抓不到不阻断看画面
        meta = {"title": url, "description": "", "comments": []}

    played, probe = _play_like_human(page, cfg)
    frames, evidence = _capture_frames(page, cfg)
    evidence["played"] = played
    evidence["duration"] = evidence.get("duration") or float(probe.get("duration") or 0.0)
    if not frames:
        return (
            {
                "kind": "video_error",
                "title": f"视频抽帧失败：{meta.get('title') or url}",
                "url": url,
                "via": via,
                "excerpt": (
                    "播放后一张画面都没截到（可能没有播放器元素、被反爬拦截或页面拒绝截图）。"
                    "以下只有页面文字信息可供参考。" + _page_text_for_prompt(meta)
                ),
                "title_only": True,
            },
            False,
        )

    # 页面文字（标题/简介/评论）先 DOM 直抓；抓不到时像真人那样截一张整页图交模型**逐字转写**（截图 OCR）
    ocr_used = False
    ocr_note = ""
    if cfg.scrape_comments and (not meta.get("comments") or not meta.get("description")):
        if vision_enabled():
            try:
                ocr_used = _fill_missing_text(meta, _ocr_page_text(page, cfg), cfg.max_comments)
            except Exception as error:  # noqa: BLE001 - OCR 兜底失败只少一份文字，不阻断看画面
                ocr_note = f"页面文字截图 OCR 失败：{type(error).__name__}: {error}"
        else:
            ocr_note = (
                "页面文字仍有缺口，但截图 OCR 兜底需要读图网关（配多模态网关并打开 LLM_VISION），"
                "本次只使用 DOM 抓到的文字"
            )

    try:
        vu = _understand_video(frames, meta, cfg, evidence)
    except (SurfUnavailable, SurfDigestError) as error:
        return (
            {
                "kind": "video_error",
                "title": f"视频看懂失败：{meta.get('title') or url}",
                "url": url,
                "via": via,
                "excerpt": str(error),
                "description": meta.get("description", ""),
                "comments": meta.get("comments", []),
            },
            False,
        )

    warnings: list[str] = []
    if vu.get("vision_skipped"):
        warnings.append(
            f"截到 {vu['frames_used']} 张画面但**没有看懂**：模型不具备图像输入能力，"
            "请配支持图像输入的多模态网关并打开 LLM_VISION（此时只有页面文字信息是真实的）"
        )
    if not played:
        warnings.append("未能确认视频真的在播放（点播放后画面时刻未推进），截图可能为封面或暂停页")
    elif evidence.get("stalled"):
        warnings.append("截图期间播放时刻没有推进，这段画面不算真看过")
    if not meta.get("comments"):
        warnings.append("评论区既没从 DOM 抓到、截图 OCR 也没转写出文字（画面里若出现评论只能据图描述）")
    if ocr_note:
        warnings.append(ocr_note)

    excerpt = _condense(
        "画面：" + vu["visual_summary"]
        + ("｜屏上文字(OCR)：" + vu["on_screen_text"] if vu["on_screen_text"] else "")
        + ("｜评论洞察：" + vu["comment_insights"] if vu["comment_insights"] else "")
        + ("｜简介：" + str(meta.get("description") or "") if meta.get("description") else "")
        + ("｜评论：" + _comments_text(meta, 6) if meta.get("comments") else ""),
        1_600,
    )
    signal = {
        "kind": "video",
        "title": meta.get("title") or url,
        "url": url,
        "via": via,
        "excerpt": excerpt,
        "played": bool(played),
        "frames": vu["frames_used"],
        "groups": vu["groups_used"],
        "group_frames": _group_frames(cfg),
        "watch_seconds": evidence.get("watched_seconds", 0.0),
        "duration": evidence.get("duration", 0.0),
        "advancing_frames": evidence.get("advancing_frames", 0),
        "stalled": bool(evidence.get("stalled")),
        "visual_summary": vu["visual_summary"],
        "on_screen_text": vu["on_screen_text"],
        "comment_insights": vu["comment_insights"],
        "segments": vu["segments"],
        "merged": vu["merged"],
        "vision_skipped": bool(vu.get("vision_skipped")),
        #: 页面文字（标题/简介/评论）里是否有来自整页截图 OCR 的转写（区别于 DOM 直抓）
        "ocr_texts": bool(ocr_used),
        "description": meta.get("description", ""),
        "uploader": meta.get("uploader", ""),
        "views": meta.get("views", ""),
        "publish_date": meta.get("publish_date", ""),
        "comments": meta.get("comments", []),
        "confidence": vu["confidence"],
        "simulated": vu["simulated"],
        "warnings": warnings,
    }
    return signal, True


# ------------------------------------------------------------------ #
# 真人式浏览：随机节奏 / 站内搜索 / 信息流多跳                           #
# ------------------------------------------------------------------ #

#: 机器节奏基准：关掉 ``human_pace`` 时每轮就滚这么多像素、等这么久（真人的手不会这么准）
_BASE_SCROLL_PX = 1_200

#: 站内搜索框候选选择器（各站实现不同，命中第一个可见的就用；全落空就如实不搜）
_SEARCH_INPUT_SELECTORS = (
    "input[type='search']",
    "input[role='searchbox']",
    "input[name='q']",
    "input[name='query']",
    "input[name='keyword']",
    "input[name='wd']",
    "input[name='search_query']",
    "input[placeholder*='搜索']",
    "input[placeholder*='search' i]",
    "form input[type='text']",
)
#: 回车不管用时的提交按钮候选
_SEARCH_SUBMIT_SELECTORS = (
    "button[type='submit']",
    "button[aria-label*='搜索']",
    "button[aria-label*='search' i]",
)

#: 搜索框打字时**站点自己弹出来的联想词**候选（真人打两三个字就会看一眼下拉，
#: 从站点给的现成说法里挑一条最像自己要找的）。读不到就**照原词回车**，绝不把页面上别的
#: 文字（热门搜索/相关推荐）当联想，也绝不凭空造一个「更常见的说法」。
_SUGGESTION_SELECTORS = (
    "[role='listbox'] [role='option']",
    "ul[role='listbox'] li",
    "div[role='listbox'] li",
    "[data-testid*='suggestion'] li",
    "ul.search-suggest li",
    "ul.suggestions li",
    ".autocomplete__option",
    ".search-suggest-item",
    "li.searchsug-item",
)

#: 「下一页 / 加载更多」候选（真人不会只看列表第一页）：各站文案与标签都不同，
#: 命中第一个可见的就点，全落空就**如实只刷第一页**（不硬拼 ?page=2 这种伪链接）
_NEXT_PAGE_SELECTORS = (
    "a[rel='next']",
    "a:has-text('下一页')",
    "button:has-text('下一页')",
    "a[aria-label*='下一页']",
    "button[aria-label*='下一页']",
    "a:has-text('Next')",
    "button:has-text('Load more')",
    "button:has-text('加载更多')",
    "a:has-text('更多')",
)

#: 正文页的折叠开关（真人会点开再读，不点只抓得到被截断的第一段）
_ARTICLE_EXPAND_LABELS = (
    "展开全文",
    "展开更多",
    "查看全部内容",
    "完整阅读",
    "显示全部",
    "Read more",
    "See more",
)

#: 真人读到「要看全文请先登录」「验证一下你不是机器人」这种**门口**就会退出来：
#: 不绕墙（绕不过验证码也不该绕），也不把墙上那几句话当成正文喂给模型，只把「被什么挡住了」如实记下来
_WALL_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "登录墙",
        (
            "请先登录", "登录后可", "登录后查看", "注册查看更多", "登录后继续",
            "sign in to continue", "log in to continue", "sign up to read",
            "create an account to", "members only",
        ),
    ),
    (
        "订阅墙",
        (
            "开通会员才能看", "付费阅读", "订阅后可读", "加入订阅", "正文需要订阅",
            "subscribe to read", "paywall", "premium content", "become a member to read",
        ),
    ),
    (
        "人机验证",
        (
            "请输入验证码", "安全验证", "请完成下方验证", "滑动验证", "拖动滑块",
            "checking your browser", "verify you are a human", "are you a robot",
        ),
    ),
    (
        "维护/出错页",
        (
            "站点正在维护", "系统升级中", "内容已被删除", "内容不存在", "文章不存在",
            "页面不存在", "page not found", "not found", "service unavailable",
        ),
    ),
)

#: 只有「短到像一扇门」的页面才可能判成墙（正常文章开头也可能提一句「登录账号同步收藏」）
_WALL_MAX_CHARS = 600

#: 墙上那几句话只留这么长（够看出是哪种墙就停，不拿它冒充正文）
_WALL_EXCERPT_CHARS = 260

#: 点进去不算「读一条内容」的直链（图片/音视频/压缩包/文档/安装包）
_NON_CONTENT_RE = re.compile(
    r"\.(png|jpe?g|gif|webp|svg|ico|bmp|mp3|m4a|wav|flac|zip|rar|7z|gz|pdf|docx?|xlsx?|pptx?|exe|dmg)"
    r"(\?|$)",
    re.I,
)

#: 主题里的中文词块（真人扫标题不会精确匹配整句，认的是「咖啡」「冲煮」这样的词）
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")

#: 主题里的西文/数字词（至少两个字符，避免一个字母到处命中）
_LATIN_RUN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.\-_]+|\d{3,}")

#: 标题里的「数字型钩子」（2026 年 / 3 个方法 / Top10 / 50%）：真人刷信息流时确实更容易点这种
_NUMBERY_RE = re.compile(r"(19|20)\d{2}年?|Top\s?\d+\+?|\d+(?:\.\d+)?%|\d+\s*[个种条件款家招]")

#: 主题里能切开的地方的（真人把一长串想法搜不成，就挑其中半句再搜）
_QUERY_SPLIT_RE = re.compile(r"[、,，。；;：:/｜|\-—＋+*#＆&]+|[（）()\[\]【】\"“”']+")

#: 判断搜索结果页贴不贴主题时，最多扫几条标题（真人也只看前面那几条）
_SEARCH_RESULT_PROBE = 12

#: 把页面读成「一条条标题 + 链接」：真人看到的是条目标题，不是一整段裸文本
_LINK_ITEMS_JS = """(args) => {
  const out = [];
  const seen = new Set();
  for (const a of Array.from(document.querySelectorAll('a[href]'))) {
    const raw = (a.innerText || a.textContent || a.getAttribute('aria-label') || '').trim();
    const text = raw.split(' ').filter(Boolean).join(' ');
    if (text.length < args.min_text || text.length > 160) continue;
    const href = a.href || '';
    if (!href || href.indexOf('javascript:') === 0 || seen.has(href)) continue;
    const rect = a.getBoundingClientRect ? a.getBoundingClientRect() : null;
    if (rect && (rect.width === 0 || rect.height === 0)) continue;
    seen.add(href);
    out.push({ text: text, href: href });
    if (out.length >= args.limit) break;
  }
  return out;
}"""


def _pace_rng(cfg, seed: str):
    """真人节奏的随机源；``human_pace`` 关掉就返回 None（等距滚 + 固定等 = 机器节奏）。

    用与本次会话相关的字符串播种：同一主题同一批起点站重跑，节奏一模一样（回归可断言），
    换主题/换站就换一套——避免每次冲浪看起来都像同一段脚本。
    """
    if not getattr(cfg, "human_pace", True):
        return None
    return random.Random(f"surf:{seed}")


def _dwell(cfg, rng) -> tuple[int, int]:
    """这一轮「滚多少像素、停多少毫秒」：真人的手不匀速，读到有意思的段落会多停一会儿。"""
    px, ms = _BASE_SCROLL_PX, max(0, int(cfg.settle_ms))
    if rng is None:
        return px, ms
    return int(px * rng.uniform(0.55, 1.9)), int(ms * rng.uniform(0.4, 1.9))


def _scroll_like_human(page, cfg, *, rounds: int, rng=None) -> dict[str, Any]:
    """像真人刷信息流那样下滑：每轮幅度与停留都浮动，偶尔**原地停一下**接着读（不滚）。

    滚不动就按已滚到的位置如实收手。返回证据（滚了几轮 / 原地停了几次 / 停留合计毫秒），
    这些数字随信号一起记账，「这条情报是看了多少页面得来的」才可核对。
    """
    scrolls = pauses = 0
    dwell_ms = 0
    for _ in range(max(0, int(rounds))):
        px, wait = _dwell(cfg, rng)
        wait = min(8_000, max(120, wait))
        if rng is not None and rng.random() < 0.22:
            # 停在这儿先读完这一段：真人不是每一下都在动鼠标
            try:
                page.wait_for_timeout(wait)
            except Exception:  # noqa: BLE001 - 等不动就收手，不假装看完了
                break
            pauses += 1
            dwell_ms += wait
            continue
        try:
            page.mouse.wheel(0, max(150, min(3_000, px)))
            page.wait_for_timeout(wait)
        except Exception:  # noqa: BLE001 - 滚不动就按已加载到的部分如实继续
            break
        scrolls += 1
        dwell_ms += wait
    return {"scrolls": scrolls, "pauses": pauses, "dwell_ms": dwell_ms}


def _search_box(page):
    """找站点自己的搜索框（各站选择器不同，逐个试；找不到返回 None，绝不猜一个别的框填进去）。"""
    for sel in _SEARCH_INPUT_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0 and loc.is_visible():
                return loc
        except Exception:  # noqa: BLE001 - 这个选择器在这个站不存在，换下一个
            continue
    return None


def _read_suggestions(page, limit: int = 8) -> list[tuple[str, Any]]:
    """读站点打字时弹出的**联想词**（下拉候选项），返回 ``[(词, 元素句柄)]``。

    只认候选选择器命中的下拉项；一个都读不到就返回空列表（**没有联想层就是没有**，
    不拿页面上别的文字凑，也不假装看到过建议）。顺序保持站点给的原顺序（那就是站点的排序）。
    """
    rows: list[tuple[str, Any]] = []
    for sel in _SUGGESTION_SELECTORS:
        try:
            loc = page.locator(sel)
            count = int(loc.count())
        except Exception:  # noqa: BLE001 - 这个选择器在这个站不存在，换下一个
            continue
        if count <= 0:
            continue
        for index in range(min(count, max(1, int(limit)))):
            try:
                item = loc.nth(index)
                text = " ".join(str(item.inner_text(timeout=800) or "").split())
            except Exception:  # noqa: BLE001 - 这一条读不到就跳过它，不影响别的
                continue
            if not text or any(text == have for have, _ in rows):
                continue
            rows.append((text, item))
        if rows:
            return rows
    return []


def _pick_suggestion(
    rows: list[tuple[str, Any]], tokens: tuple[str, ...]
) -> tuple[str, Any, list[str]]:
    """从站点给的联想里挑一条**认得出本次主题词**的（纯函数、离线可断言）。

    真人不会点第一条联想，而是扫一眼挑像的那条：按「标题里认出几个主题词」从高到低，
    同分时**保持站点给的顺序**（站点排序本身是信息）；一条都认不出就返回空句柄，
    调用方照原词回车——**绝不为了一条联想把主题改成站点更想让我们搜的东西**。
    """
    best_word, best_handle, best_hits = "", None, []
    best_score = 0
    for word, handle in rows:
        score, hits = _relevance(word, tokens)
        if not hits:
            continue
        distinct = len(set(hits))
        if distinct > best_score:
            best_word, best_handle, best_hits, best_score = word, handle, hits, distinct
    return best_word, best_handle, best_hits


def _wall_reason(title: str, body: str) -> tuple[str, str]:
    """这一页是不是**根本没让读**（登录墙/订阅墙/人机验证/维护出错页）——纯函数、离线可断言。

    返回 ``(是哪类墙, 门口那句原话)``，不是墙就给 ``("", "")``。判得保守：页面得**短到像一扇门**
    （正文 ≤ ``_WALL_MAX_CHARS`` 字）且墙话出现在标题或正文开头，免得一篇正常文章里提了一句
    「登录账号同步收藏」就被误当成墙。真是墙也**绝不绕**（真人绕不过验证码，我们也不该绕），
    只把门口那几句话如实记下来，不拿去当正文喂模型。
    """
    text = " ".join(str(body or "").split())
    if len(text) > _WALL_MAX_CHARS:
        return "", ""
    head = " ".join(str(title or "").split())
    probe = f"{head} {text[:300]}".lower()
    for label, markers in _WALL_MARKERS:
        for marker in markers:
            if marker.lower() not in probe:
                continue
            spoken = text.lower().find(marker.lower())
            snippet = text[max(0, spoken - 20) : spoken + 120] if spoken >= 0 else text[:120]
            return label, _condense(snippet, 160)
    return "", ""


def _site_search(
    page, query: str, cfg, allowed: set[str], rng, topic_tokens: tuple[str, ...] = ()
) -> tuple[str, str, dict[str, str]]:
    """用**站点自己的搜索框**搜主题：点框→逐字打字（真人不会一次粘贴）→瞄一眼联想下拉→回车→等结果渲染。

    返回 ``(结果页 URL, 失败说明, 这一版到底搜了什么词)``。第三项形如
    ``{"query": 最终搜的词, "suggested": 站点联想给的词, "note": 给人看的说明}``——没用上联想就只带 ``query``。
    找不到搜索框、打不进字、回车与提交按钮都没触发、回车后页面没变、结果页越出白名单——
    URL 一律给空 + 如实写明原因，调用方退回信息流继续看，**绝不假装搜过**。
    """
    box = _search_box(page)
    if box is None:
        return "", "这个起点站没找到搜索框（候选选择器全落空），本轮按首页信息流浏览", {"query": query}
    home = str(getattr(page, "url", "") or "")
    try:
        box.click(timeout=2_000)
    except Exception as error:  # noqa: BLE001 - 点不动就是不搜
        return "", f"搜索框点不动，没有发起站内搜索：{type(error).__name__}", {"query": query}
    try:
        box.fill("")  # 真人先把框里上一轮的词清掉，不然打下去的是两个词粘在一起
    except Exception:  # noqa: BLE001 - 清不掉也只是词串在一起，搜没搜成看后面的页面变化
        pass
    delay = int(rng.uniform(45, 150)) if rng is not None else 0
    try:
        box.press_sequentially(query, delay=delay)
    except Exception:  # noqa: BLE001 - 打不了字就直接填：打字只是节奏，不影响搜没搜
        try:
            box.fill(query)
        except Exception as error:  # noqa: BLE001
            return "", f"关键词打不进搜索框，没有发起站内搜索：{type(error).__name__}", {"query": query}

    # 打字时站点往往已经把联想下拉弹出来了：真人扫一眼，挑一条认得出本次主题词的点上
    trail: dict[str, str] = {"query": query}
    submitted = False  # 点中联想项通常直接就跳结果页了，不用再回车
    if bool(getattr(cfg, "use_suggestions", True)) and topic_tokens:
        rows = _read_suggestions(page, max(1, int(getattr(cfg, "suggestions_max", 8) or 8)))
        if rows:
            word, handle, hits = _pick_suggestion(rows, topic_tokens)
            if handle is None:
                trail["note"] = (
                    f"打字时站点弹出 {len(rows)} 条搜索联想，一条都没认出本次主题的词，"
                    f"就按原词「{query}」回车（不为一条联想改主题）"
                )
            else:
                how = ""
                try:
                    handle.click(timeout=1_500)
                    submitted, how = True, "点了站点弹出来的联想项"
                except Exception:  # noqa: BLE001 - 点不动就把词打回框里再回车，同样没凭空造词
                    try:
                        box.fill(word)
                        how = "联想项点不动，是把这个词打回搜索框再回车的"
                    except Exception:  # noqa: BLE001 - 连填都填不进就照原词搜，如实说明
                        how = ""
                if how:
                    trail["suggested"] = word
                    trail["query"] = word
                    trail["note"] = (
                        f"站内搜索用了**站点自己给的联想词**「{word}」（{how}，"
                        f"因为里面认出了{'、'.join(hits)}）"
                    )
                else:
                    trail["note"] = (
                        f"站点给的联想词「{word}」点不动、也打不回搜索框，仍按原词「{query}」搜索"
                    )
    if not submitted:
        try:
            box.press("Enter")
        except Exception:
            clicked = False
            for sel in _SEARCH_SUBMIT_SELECTORS:
                try:
                    btn = page.locator(sel).first
                    if btn.count() > 0:
                        btn.click(timeout=1_500)
                        clicked = True
                        break
                except Exception:  # noqa: BLE001 - 这个提交按钮不存在/点不动，换下一个
                    continue
            if not clicked:
                return "", "回车与提交按钮都没触发，没有真的发起站内搜索", trail
    try:
        page.wait_for_load_state("domcontentloaded", timeout=min(max(2_000, cfg.timeout_ms), 15_000))
    except Exception:  # noqa: BLE001 - 等不到加载完也照样读现状，越界判断在后面
        pass
    _scroll_like_human(page, cfg, rounds=2, rng=rng)  # 结果页多是懒加载，像真人往下滑一滑
    here = str(getattr(page, "url", "") or "")
    if not here or here == home:
        return "", (
            "点了站点给的联想项后页面没有变化（这个站的联想可能只是个展示），本轮按首页信息流浏览"
            if submitted
            else "回车后页面没有变化（这个站的搜索可能不走这个入口），本轮按首页信息流浏览"
        ), trail
    if not _allowed_and_public(here, allowed):
        try:
            page.goto(home, timeout=max(1_000, cfg.timeout_ms), wait_until="domcontentloaded")
        except Exception:  # noqa: BLE001 - 退不回去也不读越界页，调用方下一步会 goto
            pass
        return "", (
            f"搜索结果页不在 SURF_ALLOWED_DOMAINS 白名单内或无法确认是公网，已退回起点站：{here}"
        ), trail
    return here, "", trail


def _page_links(page, limit: int) -> list[dict[str, str]]:
    """把页面读成「一条条标题 + 链接」（真人点的是条目，不是整页文本）。读不到就空列表。"""
    want = max(1, int(limit)) * 8
    try:
        raw = page.evaluate(_LINK_ITEMS_JS, {"limit": want, "min_text": 6})
    except Exception:  # noqa: BLE001 - 读不到链接就当这页没有可点进的条目，不编造
        return []
    rows: list[dict[str, str]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        text = _condense(str(item.get("text") or ""), 120)
        href = str(item.get("href") or "").strip()
        if not text or not href.startswith("http"):
            continue
        rows.append({"text": text, "href": href})
        if len(rows) >= want:
            break
    return rows


def _topic_tokens(topic: str) -> tuple[str, ...]:
    """把主题切成「真人扫标题时会认出来的词块」：中文按两字滑窗，西文按词，纯函数、离线可断言。

    不用分词器也不引外部词表：真人点进一条靠的是标题里认出一两个词，精确切分反而不像人。
    """
    text = " ".join(str(topic or "").split())
    if not text:
        return ()
    tokens: list[str] = [item.lower() for item in _LATIN_RUN_RE.findall(text)]
    for run in _CJK_RUN_RE.findall(text):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    kept: list[str] = []
    for token in tokens:
        if token not in kept:
            kept.append(token)
    return tuple(kept[:16])


def _relevance(title: str, tokens: tuple[str, ...]) -> tuple[int, list[str]]:
    """这一条标题里认出几个主题词——真人挑条目靠的就是这一眼。返回 (分数, 命中的词)。

    命中的词按**在标题里出现的先后**排序，原样落到信号上供人核对（凭什么点这条）；
    标题带年份/数量这类钩子只加分不记名，真人刷信息流时确实更容易停在这种条目上。
    """
    if not tokens:
        return 0, []
    flat = " ".join(str(title or "").split())
    if not flat:
        return 0, []
    low = flat.lower()
    hits = []
    for token in tokens:
        at = low.find(token)
        if at >= 0:
            hits.append((at, token))
    hits.sort()
    found = [token for _, token in hits]
    if not found:
        return 0, []
    return len(found) + (1 if _NUMBERY_RE.search(flat) else 0), found


def _query_variants(topic: str, limit: int = 3) -> tuple[str, ...]:
    """把「想搜的主题」排成真人会依次试的几版关键词（纯函数、离线可断言）。

    真人搜不到不会原地放弃，而是**把话说短**：先用整句（最贴原来的想法），再退到顿号/逗号/斜杠
    切开的词组，最后只留最前面那一两个词。换出来的每个字都是主题里本来就写着的——
    **不引同义词表也不凭空造词**，免得「换个说法」变成瞎编。
    """
    text = " ".join(str(topic or "").split())
    if not text:
        return ()
    out: list[str] = []

    def _add(candidate: str) -> bool:
        """收下一版候选；返回「够数了，可以收手」。"""
        if len(out) >= max(1, int(limit)):
            return True
        flat = " ".join(str(candidate or "").split())
        if len(flat) < 2 or flat in out:
            return False
        out.append(flat)
        return len(out) >= max(1, int(limit))

    _add(text)
    for segment in _QUERY_SPLIT_RE.split(text):
        if _add(segment):
            return tuple(out)
    runs = _CJK_RUN_RE.findall(text)
    if runs:
        longest = max(runs, key=len)
        if len(longest) >= 4:
            _add(longest[:4])
    else:
        words = text.split()
        if len(words) > 2:
            _add(" ".join(words[:2]))
    return tuple(out)


def _results_look_relevant(page, tokens: tuple[str, ...]) -> tuple[bool, str]:
    """扫一眼结果页的标题，判断这页值不值得像真人那样接着读（只看页面上真读到的东西）。

    返回 ``(贴不贴主题, 一句人话说明)``：认出一个主题词就算贴；一条链接都没读到、或读到了但
    标题里一个主题词都不剩，就算这页没搜到对的东西。**读不到就说不，绝不拿「搜过了」冒充「搜到了」**。
    """
    rows = _page_links(page, _SEARCH_RESULT_PROBE)
    if not rows:
        return False, "结果页上一条条目都没读到（可能这个站搜不出东西）"
    if not tokens:
        return True, ""
    hits = sum(1 for row in rows if _relevance(row["text"], tokens)[1])
    if hits:
        return True, f"读到 {len(rows)} 条，其中 {hits} 条标题认得出主题词"
    return False, f"读到 {len(rows)} 条但标题里没认出本次主题的任何词"


def _pick_hops(
    links: list[dict[str, str]],
    allowed: set[str],
    limit: int,
    *,
    home_url: str,
    visited: set[str],
    topic_tokens: tuple[str, ...] = (),
) -> tuple[list[dict[str, str]], int]:
    """从页面条目里挑「点进去能读一条」的普通内容页（纯函数，离线可回归）。

    口径：只收白名单域内的链接（**越界不收**，数量如实返回给调用方写进说明）；视频链接不收
    （走「在线看片」那条路径）；图片/压缩包/文档直链不收（点进去不算读一条）；当前页与读过的不重复点；
    **同域优先**——真人是在一个站里刷完信息流才跳去别处；同域之内再按**标题认不认得出主题词**排序
    （给了 ``topic_tokens`` 时认得出的先点，认不出的仍按列表原顺序垫后），每条带上 ``why`` 说明
    是照着标题里哪几个词点进去的。
    """
    if limit <= 0:
        return [], 0
    home_host = urlparse(home_url).netloc.lower()
    same_host: list[tuple[int, int, dict[str, str]]] = []
    elsewhere: list[tuple[int, int, dict[str, str]]] = []
    offsite = 0
    seen: set[str] = set()
    order = 0
    for row in links:
        href = str(row.get("href") or "")
        if not href or href in seen:
            continue
        seen.add(href)
        if href == home_url or href in visited:
            continue
        if _VIDEO_URL_RE.search(href) or _NON_CONTENT_RE.search(href):
            continue
        if not _domain_allowed(href, allowed):
            offsite += 1
            continue
        title = _condense(str(row.get("text") or href), 120)
        score, hits = _relevance(title, topic_tokens)
        item = {"title": title, "url": href}
        if hits:
            item["why"] = "、".join(hits[:4])
        (same_host if urlparse(href).netloc.lower() == home_host else elsewhere).append(
            (-score, order, item)
        )
        order += 1
    picks = [item for _, _, item in sorted(same_host) + sorted(elsewhere)]
    return picks[: max(1, limit)], offsite


def _gap_like_human(cfg, rng) -> int:
    """开新页之前先停一停——真人是「看一眼、想一下、再点」，不是脚本连着 goto。

    ``SURF_PAGE_GAP_MS`` 给基准，``human_pace`` 开启时按它随机浮动（0 表示不刻意停顿）。
    """
    base = max(0, int(getattr(cfg, "page_gap_ms", 1_500) or 0))
    if base <= 0:
        return 0
    if rng is None:
        return base
    return min(20_000, int(base * rng.uniform(0.35, 1.8)))


def _page_fingerprint(page) -> tuple[str, int, int]:
    """这一页此刻的样子：地址 + 正文字数 + 能读到的条目数。翻页/懒加载到底有没有发生，比这三项就知道。"""
    try:
        body = str(page.inner_text("body") or "")
    except Exception:  # noqa: BLE001 - 读不到正文就按空串比，绝不当成「翻到了新页」
        body = ""
    try:
        raw = page.evaluate(_LINK_ITEMS_JS, {"limit": 200, "min_text": 6})
        links = len(raw) if isinstance(raw, list) else 0
    except Exception:  # noqa: BLE001 - 注入不了脚本就只比地址与字数
        links = 0
    return (str(getattr(page, "url", "") or ""), len(body), links)


def _click_next(page, timeout_ms: int) -> str:
    """像真人那样点「下一页 / 加载更多」。返回点到的控件文字；找不到可点控件就返回空串（不假装翻过页）。"""
    for sel in _NEXT_PAGE_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() <= 0 or not loc.is_visible():
                continue
            try:
                label = str(loc.inner_text() or "").strip()
            except Exception:  # noqa: BLE001 - 取不到文字不影响「翻没翻」，用选择器名如实标注
                label = ""
            loc.click(timeout=min(3_000, max(1_000, int(timeout_ms))))
            return _condense(label or sel, 24)
        except Exception:  # noqa: BLE001 - 这个候选在这个站不存在/点不动，换下一个
            continue
    return ""


def _gather_browser(
    topic: str,
    cfg,
    *,
    watch_videos: bool = False,
    video_urls: tuple[str, ...] = (),
    read_before: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """真·无头浏览器冲浪：像真人一样开浏览器、刷信息流、用站内搜索、点进条目读，只访问白名单站点。

    真人式浏览十一件事（都可配、都可离线回归）：**随机节奏**（每轮滚动幅度与停留浮动，偶尔原地停一下
    读完再滚，``SURF_HUMAN_PACE``；开新页之前还先停一停，``SURF_PAGE_GAP_MS``）；**站内搜索**（用起点站
    自己的搜索框逐字打字搜主题再读结果页，``SURF_SEARCH_ON_SEEDS``）；**从站点给的联想里挑一条**（打字时
    弹出的搜索联想下拉，只点**认得出本次主题词**的那条，``SURF_USE_SUGGESTIONS``；一条都认不出就照原词回车，
    绝不为了点一条联想把主题改掉，也不凭空造一个「更常见的说法」）；**搜不对题就换个说法再搜**（结果页
    标题里一个主题词都认不出，就清空搜索框把话说短再搜，最多换 ``SURF_SEARCH_RETRIES`` 次，搜过的每版
    关键词都如实记在信号上）；**列表翻页**（点站点自己的
    「下一页/加载更多」接着刷，``SURF_LIST_PAGES``）；**信息流多跳**（从条目里挑同域优先的内容页点进去
    读一层，``SURF_HOPS_PER_PAGE``）；**扫标题挑条目也挑视频**（同域之内先点标题认得出主题词的那几条，
    要在线看的视频同样从贴主题的标题里挑，凭哪几个词点进去都写成 ``picked_for``，``SURF_TOPIC_FOCUS``）；
    **读全文与评论区**（点开「展开全文」
    再读、评论区逐条抓，``SURF_PAGE_COMMENTS``）；**读过的不重读**（``read_before`` 是上几轮在这个租户
    读过的页面地址（``SURF_REMEMBER_READ``），列表里那些条目这次就跳过、改读没见过的，视频页也不重复点进去
    重看——但**整版都读过时照样按原顺序重读**并如实说明「没找到新条目」，用户显式指定的 ``video_urls``
    永远照看）。每条信号带 ``via``（seed/search/feed/direct）说明是
    怎么来的，``list_page``/``gap_ms``/``scrolls`` 记下浏览证据；起点站、搜索页、列表后续页、条目页、
    视频页**合计不越 ``SURF_MAX_PAGES``**。做不到的一律如实写进 ``note`` 信号（没搜索框、没弹出联想下拉、
    点了下一页但页面没变、页数用尽、条目越界、标题里没认出主题词、这一版条目全是读过的），**绝不假装做过**。
    **读到门口就退出来**（``SURF_AVOID_WALLS``）：页面短到像一扇门且写着「请先登录/付费阅读/请输入验证码/
    页面不存在」时，信号标 ``wall``、摘录只留门口那几句话并如实写明「没有绕墙」——这类页**不算读到了内容**，
    ``_digest_prompt`` 会带着这句说明交给你（见系统提示第 11 条）；正常文章里顺口提一句「登录同步收藏」不误判。

    ``watch_videos`` 时额外做真人式「在线看视频」：刷页面时顺手记下可点进的视频链接，浏览完再
    逐条**进入视频页**（显式 ``video_urls`` 优先），像真人点播放→分组截图→逐组交 VLM→整段汇总。
    """
    try:
        from playwright.sync_api import sync_playwright  # 惰性：只有真用 browser 通道才需要
    except Exception as error:  # noqa: BLE001 - 未安装/未装浏览器都归「不可用」，点名安装命令
        raise SurfUnavailable(
            "browser 冲浪引擎未安装：请先执行 `pip install playwright`，再执行 "
            f"`playwright install chromium`（导入失败：{type(error).__name__}: {error}）"
        ) from error

    allowed = {d.lower().lstrip(".") for d in cfg.allowed_domains if d}
    if not allowed:
        raise SurfUnavailable(
            "browser 冲浪出于安全只访问白名单站点：请先配置 SURF_ALLOWED_DOMAINS（逗号分隔域名）"
        )
    targets = [s for s in cfg.seed_sites if s]
    if not targets and not video_urls:
        raise SurfUnavailable(
            "browser 冲浪没有可访问的起点：请配置 SURF_SEED_SITES（白名单内的公开 URL）"
            "或在请求里带上 videoUrls"
        )

    timeout_ms = max(1_000, cfg.timeout_ms)
    budget = _pages_per_session(cfg)
    list_pages = max(1, int(getattr(cfg, "list_pages", 2) or 1))
    page_comments = bool(getattr(cfg, "page_comments", True))
    avoid_walls = bool(getattr(cfg, "avoid_walls", True))  # 读到门口就退出来，不把墙话当正文
    topic_tokens = _topic_tokens(topic) if getattr(cfg, "topic_focus", True) else ()
    signals: list[dict[str, Any]] = []
    notes: list[str] = []
    # 看片队列：(视频页 URL, 怎么找到它的, 在列表里看到的标题, 标题里认出的主题词)
    watch_queue: list[tuple[str, str, str, str]] = []
    visited: set[str] = set()
    pages_used = 0
    videos_done = 0
    no_next_told = False  # 「没有下一页控件」只说明一次，别每页都念一遍
    blind_told = False  # 「这一列表标题里没认出主题词」也只说明一次
    video_blind_told = False  # 「视频链接都是顺路看到的」同样只说明一次
    stale_told = False  # 「这一版条目全是上几轮读过的」也只说明一次
    wall_pages = 0  # 读到门口就被登录/订阅/验证/维护页挡住的页数（如实汇总，不绕过去）
    stale_items = 0  # 因为「上几轮读过」而跳过的条目数
    stale_videos = 0  # 因为「上几轮看过」而没点进去的视频页数
    rng = _pace_rng(cfg, f"{topic}|{'|'.join(targets)}")

    def _skip(url: str, why: str) -> None:
        signals.append({"kind": "skipped", "title": f"越界拒绝：{url}", "url": url, "excerpt": why})

    def _queue_video(url: str, via: str, listed_as: str = "", picked_for: str = "") -> None:
        nonlocal stale_videos
        if not url or any(item[0] == url for item in watch_queue):
            return
        # 用户显式指定要看的链接永远照看；顺路发现的才按「上几轮看过没有」挑新的
        if via != "direct" and url in read_before:
            stale_videos += 1
            return
        watch_queue.append((url, via, listed_as, picked_for))

    for url in video_urls:
        if _allowed_and_public(url, allowed):
            _queue_video(url, "direct")
        else:
            _skip(url, "指定的视频链接不在 SURF_ALLOWED_DOMAINS 白名单内或指向内网，已按安全策略跳过")

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=cfg.headless)
            try:
                page = browser.new_page()

                def _read(
                    url: str,
                    *,
                    kind: str,
                    via: str,
                    on_page: bool = False,
                    listed_as: str = "",
                    list_page: int = 0,
                    paged_by: str = "",
                    picked_for: str = "",
                    query: str = "",
                    search_tried: tuple[str, ...] = (),
                    suggested: str = "",
                ) -> dict[str, Any] | None:
                    """进（或已在）一个页面，像真人那样读一遍：先停一下→关遮罩→随机节奏下滑→
                    点开折叠的全文→取标题与正文→顺手翻一翻评论区。

                    读成一页就是一条信号，带 ``via``（从哪儿进来的）、翻页证据（``list_page``/``paged_by``）
                    与滚动/停留证据；进不去或正文空都**如实记下**，不拿标题冒充读过内容。
                    读到的是登录墙/订阅墙/人机验证/维护页也一样如实记下（``wall``）——**不绕墙**，
                    也不把门口那几句话当正文交给模型。
                    """
                    nonlocal pages_used, wall_pages
                    first_page = pages_used == 0
                    pages_used += 1
                    visited.add(url)
                    # 会话第一页没什么可犹豫的；之后每开一页都先停一下（同页继续读的 on_page 也不停）
                    gap_ms = 0 if (on_page or first_page) else _gap_like_human(cfg, rng)
                    if gap_ms:
                        try:
                            page.wait_for_timeout(gap_ms)  # 决定点哪个、开哪一页之间的停顿
                        except Exception:  # noqa: BLE001 - 停不住照样往下读
                            gap_ms = 0
                    try:
                        if not on_page:
                            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                        _dismiss_blockers(page)
                        pace = _scroll_like_human(page, cfg, rounds=cfg.scroll_rounds, rng=rng)
                        expanded = _expand_collapsed(page, _ARTICLE_EXPAND_LABELS)
                        body = str(page.inner_text("body") or "")
                        title = page.title() or url
                        landed = str(getattr(page, "url", "") or "")
                        comments = _collect_comments(page, cfg) if page_comments else []
                    except Exception as error:  # noqa: BLE001 - 单页失败如实记为 error 信号
                        signals.append(
                            {
                                "kind": "error",
                                "title": f"访问失败：{url}",
                                "url": url,
                                "excerpt": f"{type(error).__name__}: {error}",
                                "via": via,
                            }
                        )
                        return None
                    signal: dict[str, Any] = {
                        "kind": kind,
                        "title": title,
                        "url": url,
                        "via": via,
                        "excerpt": _condense(body, 1_500),
                        **pace,
                        "gap_ms": gap_ms,
                    }
                    if expanded:
                        signal["expanded"] = expanded  # 点开了几处「展开全文」——不点只抓得到截断的第一段
                    if comments:
                        signal["comments"] = comments  # 评论区抓到的原文（一条都没有就不写这个字段）
                    if landed and landed != url:
                        signal["landed_on"] = landed  # 被跳转到别处就如实记下落在哪
                    if listed_as:
                        signal["listed_as"] = listed_as  # 真人是照着信息流里这个标题点进来的
                    if picked_for:
                        signal["picked_for"] = picked_for  # 点这条是认出了标题里哪几个主题词
                    if query:
                        signal["query"] = query  # 站内搜索用的哪版关键词（真人是先搜整句再换短词）
                    if suggested:
                        # 这一版关键词是**站点打字时自己弹出来的联想词**，不是我们把主题切短切出来的
                        signal["suggested"] = suggested
                    if len(search_tried) > 1:
                        signal["search_tried"] = list(search_tried)  # 为这页结果换过几种说法，按顺序全记下
                    if list_page > 1:
                        signal["list_page"] = list_page  # 这是列表的第几页（点了「下一页」才读到的）
                        if paged_by:
                            signal["paged_by"] = paged_by
                    if not body.strip():
                        signal["warnings"] = ["这页正文为空（可能被反爬拦截或需要登录），只有标题可用"]
                    elif avoid_walls:
                        # 读到的是登录/订阅/验证/维护页就如实标出来：不绕墙，也不把门口几句话当正文
                        wall_kind, wall_said = _wall_reason(title, body)
                        if wall_kind:
                            wall_pages += 1
                            signal["wall"] = wall_kind
                            signal["excerpt"] = _condense(body, _WALL_EXCERPT_CHARS)
                            signal["warnings"] = [
                                f"这一页是{wall_kind}（门口写着「{wall_said}」），"
                                "只记下这几句话就退出来了——没有绕墙，也没把墙上的话当成正文"
                            ]
                    signals.append(signal)
                    return signal

                def _harvest(home_url: str, via: str) -> list[dict[str, str]]:
                    """刷完一页顺手记下「能点进去看的视频」，并挑出「能点进去读的条目」。

                    挑条目、挑要在线看的视频都像真人那样**先扫标题**：认得出主题词的先点
                    （并把凭哪几个词点进去写在 ``why`` 上）；一个都认不出来就照页面原顺序读/看，
                    并如实说明「这些是按原顺序顺路看到的」，不假装都是讲主题的。
                    给了 ``read_before``（上几轮读过的地址）就**先挑没见过的**——真人刷老站不会
                    把同一批条目重读一遍；整版都是读过的就按原顺序重读并如实说明「没找到新条目」。
                    """
                    nonlocal blind_told, video_blind_told, stale_told, stale_items
                    if watch_videos:
                        if _page_has_video(page):
                            _queue_video(home_url, via)
                        if cfg.click_into_videos:
                            # 有浏览记忆就多扫几条标题：真人是从更长的列表里挑出没看过的那条
                            found = _discover_video_links(
                                page,
                                allowed,
                                max(1, cfg.max_videos_per_session) * (4 if read_before else 1),
                                topic_tokens=topic_tokens,
                            )
                            for href, listed, why in found:
                                _queue_video(href, "feed", listed, why)
                            if topic_tokens and found and not video_blind_told:
                                if not any(why for _h, _l, why in found):
                                    video_blind_told = True
                                    notes.append(
                                        "信息流里的视频链接标题都没认出本次主题的词，"
                                        "点进去看的那几条是按页面顺序顺路看到的"
                                    )
                    if cfg.hops_per_page <= 0:
                        return []
                    rows = _page_links(page, cfg.hops_per_page)
                    if read_before:
                        fresh = [
                            row for row in rows if str(row.get("href") or "") not in read_before
                        ]
                        if len(fresh) < len(rows):
                            if fresh:  # 有没见过的新条目就读新的，读过的这条就不重复点
                                stale_items += len(rows) - len(fresh)
                                rows = fresh
                            elif not stale_told:  # 整版都是读过的：真人也会回头重看，但得说清楚
                                stale_told = True
                                notes.append(
                                    f"{home_url} 这一版列表里的条目上几轮都读过，没找到没见过的新条目，"
                                    "本轮按页面原顺序重读"
                                )
                    picks, offsite = _pick_hops(
                        rows,
                        allowed,
                        cfg.hops_per_page,
                        home_url=home_url,
                        visited=visited,
                        topic_tokens=topic_tokens,
                    )
                    if offsite:
                        notes.append(
                            f"{home_url} 上有 {offsite} 条指向白名单外的条目链接，按安全策略没有点进去"
                        )
                    if topic_tokens and picks and not blind_told:
                        if not any(str(item.get("why") or "") for item in picks):
                            blind_told = True
                            notes.append(
                                "列表条目的标题里没认出本次主题的词，点进去读的那几条是按列表原顺序挑的"
                            )
                    return picks

                def _search_like_human(term: str) -> tuple[str, list[str], str]:
                    """站内搜索：整句先搜，结果页对不上主题就**清空搜索框换个更短的说法**再搜。

                    返回 ``(值得读的结果页 URL, 依次搜过哪几版关键词, 最后那版用了站点给的哪个联想词)``，
                    一版都没搜出页面就前两项给空、第三项空字符串。
                    每版结果都照真人那样扫一眼标题判贴不贴题；只有**还有更短的说法可换**时才换词重搜，
                    换到底还是不对题就照样读最后一版（说明由挑条目那步如实给，不在这里重复念）。
                    打字时站点弹出的**联想词**（``SURF_USE_SUGGESTIONS``）也走同一只眼：认得出本次主题词的
                    才点上，一条都认不出就照原词回车——**换联想词是把话说得更像站点的叫法，不是换主题**。
                    搜不动（没搜索框、回车后页面没变、结果页越界）照旧停下写说明、不硬换个词再试——
                    换词只解决「搜到了但不对题」，不解决「这个站根本搜不出东西」。
                    """
                    queries = _query_variants(
                        term, max(1, int(getattr(cfg, "search_retries", 1)) + 1)
                    ) or (term,)
                    tried: list[str] = []
                    suggested = ""
                    for index, query in enumerate(queries):
                        found, note, trail = _site_search(
                            page, query, cfg, allowed, rng, topic_tokens=topic_tokens
                        )
                        if note:
                            notes.append(note)
                        if not found:
                            return "", tried, ""
                        tried.append(str(trail.get("query") or query))
                        suggested = str(trail.get("suggested") or "")
                        # 联想词这一眼只在**真搜出了页面**时才值得说；没搜出页面时由上面那条失败原因负责
                        if trail.get("note"):
                            notes.append(str(trail["note"]))
                        if not topic_tokens:  # 不扫标题（SURF_TOPIC_FOCUS=false）就没什么可判的，也不换词
                            return found, tried, suggested
                        ok, why = _results_look_relevant(page, topic_tokens)
                        if ok:
                            if index:
                                notes.append(
                                    f"站内搜索换了 {index} 次说法才搜到贴主题的条目："
                                    + " → ".join(tried)
                                )
                            return found, tried, suggested
                        if index + 1 >= len(queries):
                            return found, tried, suggested  # 再没有更短的说法可换，这版结果就照着读
                        notes.append(f"搜「{query}」{why}，已清空搜索框换个更短的说法再搜")
                    return "", tried, suggested

                def _surf_page(
                    url: str,
                    *,
                    kind: str,
                    via: str,
                    on_page: bool = False,
                    listed_as: str = "",
                    query: str = "",
                    search_tried: tuple[str, ...] = (),
                    suggested: str = "",
                ) -> None:
                    """读一页 + 像真人那样往下翻几页 + 挑好条目，然后真的点进去读（多跳只走一层）。

                    翻页是**点站点自己的「下一页/加载更多」**，不是拼 URL：没这个控件、点了页面没变、
                    翻过去落到白名单外、页数预算用尽——都如实停下并写进 ``note``，绝不假装多刷了几页。
                    """
                    nonlocal no_next_told
                    opened = _read(
                        url,
                        kind=kind,
                        via=via,
                        on_page=on_page,
                        listed_as=listed_as,
                        query=query,
                        search_tried=search_tried,
                        suggested=suggested,
                    )
                    if opened is None:
                        return  # 这页根本没打开成功（已落 error 信号），不去刷它的列表也不点它的条目
                    picks = _harvest(url, via)
                    seen = {str(item.get("url") or "") for item in picks}
                    page_no = 1
                    while page_no < list_pages:
                        if pages_used >= budget:
                            notes.append(
                                f"已达单会话页数上限 SURF_MAX_PAGES={budget}，列表后面的页没有再翻"
                            )
                            break
                        before = _page_fingerprint(page)
                        label = _click_next(page, timeout_ms)
                        if not label:
                            if not no_next_told:
                                no_next_told = True
                                notes.append(
                                    "这些列表页没找到「下一页/加载更多」控件，本轮只刷了每站的第一页"
                                )
                            break
                        try:
                            page.wait_for_load_state(
                                "domcontentloaded", timeout=min(15_000, max(2_000, cfg.timeout_ms))
                            )
                        except Exception:  # noqa: BLE001 - 等不到加载完也照样比一比页面变没变
                            pass
                        if _page_fingerprint(page) == before:
                            notes.append(
                                f"点了「{label}」但页面没变化（可能是无限滚动或已到末页），列表翻页到此为止"
                            )
                            break
                        here = str(getattr(page, "url", "") or "") or url
                        if not _allowed_and_public(here, allowed):
                            notes.append(f"翻页后落到白名单外的地址，已停止翻页且没有读它：{here}")
                            break
                        page_no += 1
                        if (
                            _read(
                                here,
                                kind=kind,
                                via=via,
                                on_page=True,
                                list_page=page_no,
                                paged_by=label,
                                query=query,
                            )
                            is None
                        ):
                            break  # 翻过去了但没读成（已落 error 信号），别再往下翻
                        for target in _harvest(here, via):
                            href = str(target.get("url") or "")
                            if href and href not in seen:
                                seen.add(href)
                                picks.append(target)
                    for target in picks:
                        if pages_used >= budget:
                            notes.append(
                                f"已达单会话页数上限 SURF_MAX_PAGES={budget}，后面的条目没有点进去读"
                            )
                            break
                        href = str(target.get("url") or "")
                        if not _allowed_and_public(href, allowed):
                            _skip(
                                href,
                                "信息流条目不在 SURF_ALLOWED_DOMAINS 白名单内或指向内网，已按安全策略跳过",
                            )
                            continue
                        _read(
                            href,
                            kind="item",
                            via="search" if via == "search" else "feed",
                            listed_as=str(target.get("title") or ""),
                            picked_for=str(target.get("why") or ""),
                        )

                for url in targets:
                    if pages_used >= budget:
                        notes.append(
                            f"已达单会话页数上限 SURF_MAX_PAGES={budget}，剩下的起点站没有访问"
                        )
                        break
                    if not _allowed_and_public(url, allowed):
                        _skip(url, "非 SURF_ALLOWED_DOMAINS 白名单或指向内网，已按安全策略跳过")
                        continue
                    _surf_page(url, kind="page", via="seed")
                    # 真人进了站不会只刷首页：用站点自己的搜索框搜本次主题，再读结果页
                    if cfg.search_on_seeds and topic and pages_used < budget:
                        searched, tried, suggestion = _search_like_human(topic)
                        if searched:
                            _surf_page(
                                searched,
                                kind="search",
                                via="search",
                                on_page=True,
                                query=tried[-1],
                                search_tried=list(tried),
                                suggested=suggestion,
                            )

                # 在线看视频：真人是「点进去看」，故逐条进入视频页（不下载视频文件）
                if watch_videos:
                    for url, via, listed_as, picked_for in watch_queue:
                        if videos_done >= max(1, cfg.max_videos_per_session):
                            break
                        if pages_used >= budget:
                            notes.append(
                                f"已达单会话页数上限 SURF_MAX_PAGES={budget}，剩下的视频没有点进去看"
                            )
                            break
                        pages_used += 1
                        signal, ok = _watch_one_video(page, url, cfg, via=via)
                        if listed_as:
                            signal["listed_as"] = listed_as  # 真人是照着信息流里这个标题点进去看的
                        if picked_for:
                            signal["picked_for"] = picked_for  # 这条视频是标题认出主题词才挑来看的
                        signals.append(signal)
                        if ok:
                            videos_done += 1

                if stale_items:
                    notes.append(
                        f"这一轮跳过 {stale_items} 条上几轮就读过的条目（读过的记在冲浪浏览历史里），"
                        "改读没见过的新条目"
                    )
                if stale_videos:
                    notes.append(
                        f"{stale_videos} 条顺路发现的视频页上几轮已经看过，本轮没有重复点进去看"
                        "（用户显式指定要看的链接不受这条影响）"
                    )
                if wall_pages:
                    notes.append(
                        f"有 {wall_pages} 页读到门口就被挡住（要登录/要订阅/要验证/页面已不存在），"
                        "只记下门口那几句话就退出来了——没有绕墙，这些页的信号不算读到了内容"
                    )

                if notes:
                    signals.append(
                        {"kind": "note", "title": "本轮浏览说明", "excerpt": "；".join(notes)}
                    )
            finally:
                browser.close()

    except SurfUnavailable:
        raise
    except Exception as error:  # noqa: BLE001 - 浏览器启动/连接失败
        raise SurfUnavailable(f"browser 冲浪启动失败：{type(error).__name__}: {error}") from error

    if not any(
        item["kind"] in ("page", "hot", "search", "item", "video", "video_error")
        for item in signals
    ):
        raise SurfUnavailable(
            "browser 冲浪没有访问到任何可抽取的白名单页面（检查 SURF_SEED_SITES / SURF_ALLOWED_DOMAINS）"
        )
    return signals


# ------------------------------------------------------------------ #
# 消化：一次 LLM 调用把信号汇成灵感卡                                   #
# ------------------------------------------------------------------ #

_SURF_SYSTEM = (
    "你是内容创作台的「灵感冲浪手」。用户给你一个主题和一批刚从网上冲浪收集到的外部信号"
    "（网页/热榜/视频的标题与摘录）。你的任务是**读懂这些信号**，产出一批能直接用于创作的"
    "灵感卡，而不是复述信号原文。\n"
    "要求：1) 每张卡是一个可执行的创作切入点，落到具体场景/情绪/钩子，不喊空口号；"
    "2) 结合信号的来源，标注「为什么现在值得做」；3) sources 只放信号里真实存在的链接，"
    "没有对应链接就留空数组，绝不编造链接；4) 数量不超过给定上限，宁少而实，不多而空；"
    "5) [video] 信号的摘录是**在线看视频**得到的画面结论 + 页面文字（标题/简介/评论），"
    "若其中写明「未看懂画面 / 占位」就只依据页面文字部分，不把占位描述当事实写进灵感卡；"
    "6) 标签里的入口说明这条信号是**怎么来的**：seed=起点站首页、search=站内搜索结果页、"
    "feed=从信息流点进的条目页、direct=用户指定要看的链接；"
    "[item] 的 listed_as 是冲浪时在列表里看到的标题，正文可能与之不符，以摘录为准；"
    "标了「列表第 N 页」的信号是点了「下一页/加载更多」之后读到的同一列表的后续页；\n"
    "7) 「评论区」那几行是页面上真实抓到的网友原话（不是我们的总结），"
    "里面的吐槽、追问、反对意见往往是最好的切入角度，但**不要把它们当成事实陈述**写进灵感卡。\n"
    "8) 标了「因标题含…点进」的条目或视频，是冲浪时**扫标题认出主题词**才点进去看/读的，通常最贴主题，优先据此出卡；"
    "没标这条的只是顺路读读，别把顺路读到的东西当成主题的直接证据。\n"
    "9) 标了「站内搜索关键词」的页是照着这些词在站点自己的搜索框里搜出来的结果页；"
    "写明「换短的说法才搜到」的是整句搜不出对题的东西、把关键词缩短重搜一轮才读到的，最贴主题。\n"
    "10) 关键词后标了「站点打字时自己弹出来的联想词」的，那一版词**不是我们造的词，是站点给的叫法**——"
    "说明站内真有这个话题的搜索需求，据此出的卡更贴近这个站的人群在搜什么；没标这条的就是我们自己切的词。\n"
    "11) 标了「这一页被…挡住」的信号**根本没读到正文**（登录墙/订阅墙/人机验证/页面已不存在），"
    "摘录只是门口那几句话——绝不能拿它当内容证据出卡，最多用来说明「这个话题在有些站要登录/付费才看得到」；"
    "冲浪手不会绕墙（绕不过验证码，也不该绕）。\n"
    '只输出 JSON：{"cards": [{"idea": "点子一句话", "angle": "切入角度", "hook": "开场钩子", '
    '"why_now": "为什么现在", "sources": ["链接"]}]}'
)


def _digest_prompt(topic: str, signals: list[dict[str, Any]], max_cards: int) -> str:
    lines = [
        f"主题：{topic}",
        f"请基于下列信号产出至多 {max_cards} 张灵感卡：",
        "冲浪收集到的外部信号（类型｜入口｜标题｜链接｜摘录，条目另标了点进来的理由与站内搜索用的关键词）：",
    ]
    taken = 0
    for item in signals:
        # note 是「这一轮浏览过程说明」，只给会话告警看，不进消化提示词（不是外部情报）
        if item.get("kind") == "note":
            continue
        if taken >= _MAX_SIGNALS_FOR_DIGEST:
            break
        taken += 1
        kind = str(item.get("kind", "?"))
        via = str(item.get("via") or "")
        page_no = int(item.get("list_page") or 0)
        tag = f"{kind}{'/' + via if via else ''}" + (f"｜列表第 {page_no} 页" if page_no > 1 else "")
        row = (
            f"{taken}. [{tag}] "
            f"{item.get('title', '')}｜{item.get('url', '')}\n"
            f"   摘录：{item.get('excerpt', '')}"
        )
        picked_for = str(item.get("picked_for") or "")
        if picked_for:
            row += f"\n   因标题含「{picked_for}」点进（冲浪时扫标题认出的主题词）"
        wall = str(item.get("wall") or "")
        if wall:
            row += f"\n   这一页被{wall}挡住：摘录只是门口那几句话，没有读到正文，也没有绕过去"
        query = str(item.get("query") or "")
        if query:
            row += f"\n   站内搜索关键词：{query}"
            suggested = str(item.get("suggested") or "")
            if suggested:
                row += "（这版词是**站点打字时自己弹出来的联想词**，就是站内的真实叫法）"
            trail = item.get("search_tried")
            if isinstance(trail, list) and len(trail) > 1:
                row += "（第一版搜不出对题的东西，换短的说法才搜到）"
        comments = item.get("comments")
        if isinstance(comments, list) and comments:
            row += "\n   评论区：" + " ／ ".join(
                _condense(str(c.get("text") if isinstance(c, dict) else c), 90)
                for c in comments[:5]
                if _condense(str(c.get("text") if isinstance(c, dict) else c), 90)
            )
        lines.append(row)
    return "\n".join(lines)


def _parse_cards(parsed: Any) -> list[dict[str, Any]]:
    """宽容解析消化结果：顶层数组或 cards/ideas/insights/results/items 键都收。"""
    if isinstance(parsed, list):
        arr: list[Any] = parsed
    elif isinstance(parsed, dict):
        arr = []
        for key in ("cards", "ideas", "insights", "results", "items", "data"):
            if isinstance(parsed.get(key), list):
                arr = parsed[key]
                break
    else:
        arr = []
    cards: list[dict[str, Any]] = []
    for item in arr:
        rec = as_obj(item)
        idea = as_str(rec.get("idea") or rec.get("title") or rec.get("concept"))
        if not idea.strip():
            continue
        cards.append(
            {
                "idea": idea.strip(),
                "angle": as_str(rec.get("angle") or rec.get("perspective")).strip(),
                "hook": as_str(rec.get("hook") or rec.get("opening")).strip(),
                "why_now": as_str(rec.get("why_now") or rec.get("whyNow") or rec.get("trend")).strip(),
                "sources": _as_str_list(rec.get("sources")),
            }
        )
    return cards


def _as_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [as_str(v) for v in value if as_str(v)]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _digest(
    topic: str, signals: list[dict[str, Any]], *, provider: str, task_id: str, cfg
) -> tuple[list[dict[str, Any]], bool]:
    """调一次模型（SURF.digest）把信号汇成灵感卡；离线/降级消化如实标 ``simulated=True``。"""
    payload_signals = [
        {
            "kind": item.get("kind"),
            "title": item.get("title"),
            "url": item.get("url"),
            "excerpt": item.get("excerpt"),
        }
        for item in signals[:_MAX_SIGNALS_FOR_DIGEST]
    ]
    response = chat(
        LLMRequest(
            purpose="SURF.digest",
            json=True,
            messages=[
                ChatMessage(role="system", content=_SURF_SYSTEM),
                ChatMessage(role="user", content=_digest_prompt(topic, signals, cfg.cards_per_session)),
            ],
            context={
                "topic": topic,
                "signals": payload_signals,
                "max_cards": cfg.cards_per_session,
            },
        )
    )
    raw_cards = _parse_cards(extract_json(response.content))
    if not raw_cards:
        raise SurfDigestError(
            "消化失败：模型没有产出任何可解析的灵感卡（空输出或 JSON 不可解析），"
            "绝不拿空卡冒充有灵感"
        )

    simulated = (
        provider == "sample"
        or bool(getattr(response, "simulated", False))
        or bool(getattr(response, "degraded_reason", None))
    )
    moment = now_iso()
    cards: list[dict[str, Any]] = []
    for card in raw_cards[: cfg.cards_per_session]:
        cards.append(
            {
                "id": new_id("si"),
                "topic": topic,
                "idea": card["idea"],
                "angle": card["angle"],
                "hook": card["hook"],
                "why_now": card["why_now"],
                "sources": card["sources"],
                "provider": provider,
                "simulated": simulated,
                "task_id": task_id or "",
                "created_at": moment,
            }
        )
    return cards, simulated


# ------------------------------------------------------------------ #
# 成本估算与通道择优                                                   #
# ------------------------------------------------------------------ #


def _vision_image_limit() -> int:
    """一次多模态调用实际能带的截图张数（``LLM_VISION_MAX_IMAGES``，至少 1 张）。"""
    raw = getattr(get_config().llm, "vision_max_images", _DEFAULT_VLM_GROUP_FRAMES)
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        limit = _DEFAULT_VLM_GROUP_FRAMES
    return max(1, min(limit, _MAX_FRAMES_FOR_VLM))


def _group_frames(cfg) -> int:
    """每组实际交 VLM 的截图张数：配置值与网关单次图片上限取小。"""
    return max(1, min(int(cfg.vlm_group_frames or _DEFAULT_VLM_GROUP_FRAMES), _vision_image_limit()))


def _video_calls_per_video(cfg) -> int:
    """一个视频最多调几次模型：分组截图每组一次多模态调用 +（多组时）一次文本汇总 + 一次截图 OCR 兜底。"""
    frames = max(1, min(int(cfg.video_frames or 1), _MAX_FRAMES_FOR_VLM))
    groups = -(-frames // _group_frames(cfg))
    return groups + (1 if groups > 1 else 0) + (1 if cfg.scrape_comments else 0)


def _pages_per_session(cfg) -> int:
    """browser 通道一轮会话**允许访问**的页面数上限（就是 SURF_MAX_PAGES，防一次把预算烧穿）。

    真人式浏览会连带多点几下（站内搜索结果页、从信息流点进的内容页），所以这个数
    **覆盖全部访问**：起点站 + 搜索页 + 多跳条目页 + 视频页都算页数，超了就停。
    """
    return max(1, int(cfg.max_pages))


def _estimate_cost(provider: str, *, watch_videos: bool = False) -> float:
    """sample/auto 归 0；browser/http 的浏览本身不计费（自建/本机），消化按 LLM 调用次数估。

    看视频时**每组截图一次多模态调用、多组再汇总一次**，故按 ``max_videos_per_session ×
    (组数 + 汇总)`` 上限保守预估一并纳入。仅当真配了非本地 LLM 网关时才非零
    （与 dubbing 译制、LLM 引擎成本口径一致）。
    """
    if provider in ("sample", "auto"):
        return 0.0
    llm = get_config().llm
    if llm.provider != "openai" or not llm.base_url or _is_local_endpoint(llm.base_url):
        return 0.0
    cfg = get_config().surf
    cost = _EST_COST_PER_DIGEST_USD
    if watch_videos and provider == "browser" and vision_enabled():
        cost += (
            _EST_COST_PER_VLM_CALL_USD
            * _video_calls_per_video(cfg)
            * max(1, cfg.max_videos_per_session)
        )
    return round(cost, 6)


def _browser_effectively_available(cfg, *, has_video_urls: bool = False) -> bool:
    """auto 择优判断 browser 是否真能用：装了 Playwright、配了白名单，且有起点站或指定视频链接。"""
    if not cfg.allowed_domains:
        return False
    if not (cfg.seed_sites or has_video_urls):
        return False
    import importlib.util

    return importlib.util.find_spec("playwright") is not None


def _resolve_provider(requested: str, cfg, *, has_video_urls: bool = False) -> tuple[str, str]:
    """把 requested（含 auto）解析成具体通道，并返回一句可读的择优说明（可为空）。"""
    raw = (requested or cfg.provider or "sample").strip().lower()
    if raw not in ("sample", "auto", "browser", "http"):
        raw = "sample"
    if raw != "auto":
        return raw, ""
    if _browser_effectively_available(cfg, has_video_urls=has_video_urls):
        return "browser", "auto 已选用 browser（Playwright 可用且配了白名单站点）"
    if search_configured():
        return "http", "auto 回退 http（Playwright 不可用，改用已配置的联网检索网关冲浪）"
    return "sample", "auto 回退 sample（浏览器与搜索网关都未配置，产离线占位灵感）"


# ------------------------------------------------------------------ #
# 会话推进                                                            #
# ------------------------------------------------------------------ #


def _parse_iso(raw: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _transition(session: dict[str, Any], status: str, note: str) -> None:
    if session.get("status") == status:
        session["error"] = note if status == "failed" else session.get("error", "")
        return
    previous = str(session.get("status") or "queued")
    session["status"] = status
    session["error"] = note if status == "failed" else ""
    session["history"] = list(session.get("history") or []) + [
        {"ts": now_iso(), "from": previous, "to": status, "note": note[:200]}
    ]
    session["updated_at"] = now_iso()
    event_bus.publish(
        task_id=str(session.get("task_id") or ""),
        type="log",
        message=f"灵感冲浪：{note}",
        level="warn" if status == "failed" else "info",
        agent_id="A2",
        payload={
            "session_id": session.get("id"),
            "status": status,
            "provider": session.get("provider"),
        },
    )


def _finish(session: dict[str, Any], cards: list[dict[str, Any]], simulated: bool) -> None:
    session["cards"] = cards
    session["simulated"] = simulated
    warnings = list(session.get("warnings") or [])
    if simulated:
        warnings.append(
            "灵感卡由离线/降级引擎产出，是占位（非真实冲浪消化所得）；"
            "配真实 LLM 网关（LLM_*）并选 browser/http 通道后即为真实灵感"
        )
    signals = session.get("signals") or []
    video_signals = [s for s in signals if s.get("kind") == "video"]
    failed_videos = [s for s in signals if s.get("kind") == "video_error"]
    if video_signals and all(s.get("simulated") for s in video_signals):
        warnings.append(
            f"本会话在线看了 {len(video_signals)} 个视频，但画面摘要由离线/无视觉网关产出（占位、未真实看懂）；"
            "配真实多模态网关（LLM_* + 支持图像输入）后才是真实看懂"
        )
    # 每条信号的诚实边界（没播起来 / 画面没推进 / 评论没抓到 / 页面正文空）汇总到会话告警
    for signal in signals:
        for note in signal.get("warnings") or []:
            text = f"「{signal.get('title') or signal.get('url') or '信号'}」{note}"
            if text not in warnings:
                warnings.append(text)
    if failed_videos:
        warnings.append(
            f"{len(failed_videos)} 条视频没能看懂（原因见该条视频条目），已如实记为失败信号而非编造画面"
        )
    # browser 通道的浏览过程说明（搜索框没找到 / 页数预算用完 / 越界条目没收）只作告警，不进消化
    for signal in signals:
        if signal.get("kind") == "note":
            note = str(signal.get("excerpt") or "").strip()
            if note and note not in warnings:
                warnings.append(note)
    session["warnings"] = warnings
    add_pool_cards(cards, tenant=str(session.get("tenant") or "default"))
    _transition(
        session,
        "done",
        f"已产出 {len(cards)} 张灵感卡并沉淀入灵感池"
        + ("（占位）" if simulated else ""),
    )
    session["progress"] = 100
    session["finished_at"] = now_iso()


def _run_surf(session: dict[str, Any]) -> None:
    """真正跑一轮冲浪：采集信号 → 模型消化 → 完成/失败。browser/http 在受理时同步执行。"""
    cfg = get_config().surf
    topic = str(session.get("topic") or "")
    tenant = str(session.get("tenant") or "default")
    provider = str(session.get("provider") or "sample")
    watch_videos = bool(session.get("watch_videos"))
    video_urls = tuple(str(item) for item in (session.get("video_urls") or []) if item)
    _transition(session, "surfing", "开始上网冲浪：检索 / 浏览 / 刷信息流")
    try:
        if provider == "http":
            signals = _gather_http(topic, cfg)
        elif provider == "browser":
            signals = _gather_browser(
                topic,
                cfg,
                watch_videos=watch_videos,
                video_urls=video_urls,
                read_before=read_history(tenant),
            )
        else:
            signals = _gather_sample(topic, cfg)
    except SurfUnavailable as error:
        _transition(session, "failed", str(error))
        return

    if provider == "browser":
        _remember_read_note(signals, topic=topic, tenant=tenant)
    session["signals"] = signals
    session["progress"] = 40
    _transition(
        session, "digesting", f"已收集 {len(signals)} 条外部信号，交模型消化成灵感卡"
    )
    try:
        cards, simulated = _digest(
            topic, signals, provider=provider, task_id=str(session.get("task_id") or ""), cfg=cfg
        )
    except (SurfUnavailable, SurfDigestError) as error:
        _transition(session, "failed", str(error))
        return
    except Exception as error:  # noqa: BLE001 - 消化环节其它异常也如实失败
        _transition(session, "failed", f"消化失败：{type(error).__name__}: {error}")
        return
    _finish(session, cards, simulated)


def _advance_sample(session: dict[str, Any], now: datetime) -> None:
    started = _parse_iso(str(session.get("started_at") or ""))
    if started is None:
        return
    elapsed = (now - started).total_seconds()
    if elapsed < 1.0:
        _transition(session, "surfing", "样例引擎开始上网（不联网，产占位信号）")
        session["progress"] = 10
    elif elapsed < _SAMPLE_SECONDS:
        if session.get("status") == "surfing":
            _transition(session, "digesting", "样例引擎把占位信号交离线模型消化")
        session["progress"] = int(min(1.0, elapsed / _SAMPLE_SECONDS) * 90)
    else:
        cfg = get_config().surf
        signals = session.get("signals") or _gather_sample(str(session.get("topic") or ""), cfg)
        session["signals"] = signals
        try:
            cards, _ = _digest(
                str(session.get("topic") or ""),
                signals,
                provider="sample",
                task_id=str(session.get("task_id") or ""),
                cfg=cfg,
            )
        except Exception as error:  # noqa: BLE001 - 样例消化失败也要如实呈现，不静默
            _transition(session, "failed", f"样例消化失败：{type(error).__name__}: {error}")
            return
        _finish(session, cards, True)


def _advance(session: dict[str, Any], now: datetime) -> None:
    if session.get("status") in ("done", "failed"):
        return
    if session.get("provider") == "sample":
        _advance_sample(session, now)
    else:
        _run_surf(session)


# ------------------------------------------------------------------ #
# 公共 API：建会话 / 列表 / 灵感池 / 注入 / 自主 tick                    #
# ------------------------------------------------------------------ #


def create_session(
    task: TaskRecord | None = None,
    *,
    topic: str = "",
    provider: str = "",
    watch_videos: bool | None = None,
    video_urls: tuple[str, ...] | list[str] = (),
    now: datetime | None = None,
) -> dict[str, Any]:
    """发起一次灵感冲浪会话（``POST /api/tasks/{id}/surf`` 或 ``POST /api/surf/session``）。

    ``topic`` 为空时依次回退：任务 Brief 主题 → 配置 topics 首个 → 报错。会话完成后其灵感卡
    自动沉淀入灵感池。**诚实失败**：真引擎通道缺配置在受理时即置 failed 并点名环境变量。

    ``watch_videos``：是否像真人一样在线看视频（仅 browser 通道会真播放、分组抽帧、交 VLM 看懂）；
    为 None 时取配置 ``SURF_WATCH_VIDEOS``。非 browser 通道要求看视频时**不假装**，写告警并自动降级为只看信号。

    ``video_urls``：指定要在线看的视频页链接（真人「我就是想看这条」的入口）；给了它就自动视为
    要在线看视频。仍在 ``SURF_ALLOWED_DOMAINS`` 白名单内才访问，越界链接如实记为 skipped 信号。
    """
    cfg = get_config().surf
    resolved_topic = (topic or "").strip()
    if not resolved_topic and task is not None:
        resolved_topic = _topic_from_task(task)
    if not resolved_topic and cfg.topics:
        resolved_topic = str(cfg.topics[0]).strip()
    if not resolved_topic:
        raise ValueError(
            "灵感冲浪需要一条主题：请在请求里带 topic，或给任务填 Brief，或配置 SURF_TOPICS"
        )

    chosen, note = _resolve_provider(provider, cfg, has_video_urls=bool(video_urls))
    tenant = task.tenant if task is not None else "default"
    task_id = task.id if task is not None else ""
    moment = now or datetime.now(timezone.utc)
    urls = [str(item).strip() for item in (video_urls or []) if str(item).strip()]

    # 看视频只在 browser 通道能真正播放+截图；其它通道要求看视频时不假装，写告警并降级。
    want_watch = (cfg.watch_videos or bool(urls)) if watch_videos is None else bool(watch_videos)
    if urls:
        want_watch = True
    effective_watch = want_watch and chosen == "browser"
    estimated_cost = _estimate_cost(chosen, watch_videos=effective_watch)

    session: dict[str, Any] = {
        "id": new_id("surf"),
        "task_id": task_id,
        "tenant": tenant,
        "provider": chosen,
        "requested_provider": (provider or cfg.provider or "sample").strip().lower(),
        "topic": resolved_topic,
        "watch_videos": effective_watch,
        "video_urls": urls,
        "status": "queued",
        "progress": 0,
        "signals": [],
        "cards": [],
        "simulated": False,
        "estimated_cost_usd": estimated_cost,
        "warnings": [],
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "started_at": now_iso(),
        "finished_at": "",
        "error": "",
        "history": [{"ts": now_iso(), "from": "", "to": "queued", "note": "灵感冲浪会话已受理"}],
    }
    if note:
        session["warnings"].append(note)
    if want_watch and not effective_watch:
        session["warnings"].append(
            f"看懂视频仅在 browser 通道生效（当前为 {chosen}，不会真正播放与截图）；"
            "如需看视频请选 browser 并装 Playwright + 配 SURF_ALLOWED_DOMAINS"
        )

    # 成本熔断：browser/http 的消化按一次真实 LLM 调用估，超预算直接 fail-loud，绝不开跑
    budget = get_config().cost_budget_usd
    if chosen in ("browser", "http") and budget > 0 and estimated_cost >= budget:
        _transition(
            session,
            "failed",
            f"成本熔断：单次消化预估 ${estimated_cost:.3f} 已达预算 ${budget:.2f}，未开始冲浪"
            f"（可下调估算口径或调高 COST_BUDGET_USD）",
        )
        _job_backend().create(session)
        return dict(session)

    _job_backend().create(session)
    event_bus.publish(
        task_id=task_id,
        type="log",
        message=f"灵感冲浪会话已创建（{chosen}，主题「{resolved_topic}」）",
        agent_id="A2",
        payload={"session_id": session["id"], "estimated_cost_usd": estimated_cost},
    )

    # 真引擎在受理时同步跑完（浏览 + 消化）；sample 走惰性时间推进（读取时完成）
    if chosen in ("browser", "http"):
        _advance(session, moment)
        _job_backend().write_back([session])

    log.info(
        f"灵感冲浪会话已创建：{session['id']}（task={task_id or '—'}, "
        f"provider={chosen}, status={session['status']}）"
    )
    return dict(session)


def _refresh(session: dict[str, Any], now: datetime) -> None:
    _advance(session, now)


def list_sessions(
    task_id: str | None = None, *, tenant: str | None = None, now: datetime | None = None
) -> list[dict[str, Any]]:
    """列出会话（读取时惰性推进状态机），新→旧排序。"""
    backend = _job_backend()
    moment = now or datetime.now(timezone.utc)
    snapshot = [dict(session) for session in backend.snapshot()]
    changed: list[dict[str, Any]] = []
    for session in snapshot:
        before = str(session.get("status"))
        _refresh(session, moment)
        if str(session.get("status")) != before:
            changed.append(session)
    if changed:
        backend.write_back(changed)
    rows = [
        session
        for session in snapshot
        if (task_id is None or str(session.get("task_id")) == task_id)
        and (tenant is None or str(session.get("tenant")) == tenant)
    ]
    rows.sort(key=lambda session: session.get("created_at") or "", reverse=True)
    return rows


def get_session(session_id: str, *, tenant: str | None = None) -> dict[str, Any] | None:
    return next((row for row in list_sessions(tenant=tenant) if row.get("id") == session_id), None)


# ------------------------------------------------------------------ #
# 灵感池（idea 库）：跨任务沉淀，用户挑选后再创作                        #
# ------------------------------------------------------------------ #


def add_pool_cards(cards: list[dict[str, Any]], *, tenant: str = "default") -> int:
    """把灵感卡沉淀进灵感池（补 tenant/status 后落库，并按 pool_max 裁剪最旧的）。"""
    if not cards:
        return 0
    rows: list[dict[str, Any]] = []
    for card in cards:
        row = dict(card)
        row["tenant"] = row.get("tenant") or tenant
        row["status"] = "card"
        rows.append(row)
    backend = _pool_backend()
    backend.add(rows)
    backend.prune(get_config().surf.pool_max)
    return len(rows)


def list_pool(
    *, tenant: str | None = None, topic: str = "", limit: int = 0
) -> list[dict[str, Any]]:
    """列出灵感池（新→旧）；可按租户/主题过滤，limit>0 截断。"""
    rows = [dict(row) for row in _pool_backend().snapshot()]
    rows.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    if tenant is not None:
        rows = [row for row in rows if str(row.get("tenant")) == tenant]
    if topic:
        needle = topic.strip().lower()
        rows = [row for row in rows if needle in str(row.get("topic") or "").lower()]
    if limit and limit > 0:
        rows = rows[:limit]
    return rows


def get_pool_card(card_id: str, *, tenant: str | None = None) -> dict[str, Any] | None:
    for row in _pool_backend().snapshot():
        if str(row.get("id")) == card_id and (tenant is None or str(row.get("tenant")) == tenant):
            return dict(row)
    return None


def drop_pool_card(card_id: str) -> bool:
    return _pool_backend().remove(card_id)


# ------------------------------------------------------------------ #
# 跨会话浏览历史（真人不会每轮把同一个站的同一批条目重读一遍）            #
# ------------------------------------------------------------------ #

#: 只有「真读进去的条目页」与「在线看完的视频页」才算读过；起点站首页与列表页不算
_HISTORY_KINDS = ("item", "video")


def _history_rows(tenant: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in _history_backend().snapshot()
        if str(row.get("tenant")) == tenant and row.get("url")
    ]


def read_history(tenant: str) -> frozenset[str]:
    """这个租户上几轮读过的页面地址；关掉 ``SURF_REMEMBER_READ`` 就是空集（等于没有记忆）。"""
    if not bool(getattr(get_config().surf, "remember_read", True)):
        return frozenset()
    return frozenset(str(row.get("url") or "") for row in _history_rows(tenant))


def _prune_history(tenant: str, keep_max: int) -> int:
    """只留本租户最近读过的若干条（真人也记不住几百个页面，忘了的下一轮会重新读）。"""
    rows = _history_rows(tenant)
    rows.sort(key=lambda row: str(row.get("last_seen") or ""), reverse=True)
    backend = _history_backend()
    doomed = [str(row["id"]) for row in rows[max(0, int(keep_max)):]]
    for row_id in doomed:
        backend.remove(row_id)
    return len(doomed)


def remember_read_pages(signals: list[dict[str, Any]], *, topic: str, tenant: str) -> int:
    """把这轮真读到/看完的页面记进浏览历史；同一页再读只 bump ``last_seen``/``reads``，不重复记账。"""
    cfg = get_config().surf
    if not bool(getattr(cfg, "remember_read", True)):
        return 0
    backend = _history_backend()
    live = {str(row.get("id")): row for row in backend.snapshot()}
    moment = now_iso()
    rows: list[dict[str, Any]] = []
    for signal in signals or []:
        if str(signal.get("kind") or "") not in _HISTORY_KINDS:
            continue
        if signal.get("wall"):
            # 被登录/订阅/验证墙挡住的页**没读到内容**，不装成「读过」（下轮再撞上再说）
            continue
        url = str(signal.get("url") or "").strip()
        if not url:
            continue
        key = f"{tenant}|{url}"
        before = live.get(key) or {}
        row = {
            "id": key,
            "tenant": tenant,
            "task_id": "",  # 跨会话记忆，与灵感池一样**不随任务删除**
            "status": "seen",
            "created_at": str(before.get("created_at") or moment),
            "url": url,
            "topic": topic,
            "title": _condense(str(signal.get("title") or ""), 160),
            "via": str(signal.get("via") or ""),
            "first_seen": str(before.get("first_seen") or moment),
            "last_seen": moment,
            "reads": int(before.get("reads") or 0) + 1,
        }
        live[key] = row
        rows.append(row)
    if rows:
        backend.add(rows)
        _prune_history(tenant, max(20, int(getattr(cfg, "history_max", 300) or 300)))
    return len(rows)


def _remember_read_note(signals: list[dict[str, Any]], *, topic: str, tenant: str) -> int:
    """记下这轮读到的页，并把「记了几条」并进同一条「本轮浏览说明」（开关关掉就一条都不记、也不写）。"""
    remembered = remember_read_pages(signals, topic=topic, tenant=tenant)
    if not remembered:
        return 0
    text = (
        f"这轮读到的 {remembered} 个页面已记进浏览历史，下几轮会优先改读没见过的新条目"
        "（想让它忘掉重看，清掉浏览历史即可）"
    )
    for item in signals:
        if item.get("kind") == "note":
            item["excerpt"] = f"{item['excerpt']}；{text}" if item.get("excerpt") else text
            break
    else:
        signals.append({"kind": "note", "title": "本轮浏览说明", "excerpt": text})
    return remembered


def list_history(tenant: str, *, limit: int = 0) -> list[dict[str, Any]]:
    """本租户的浏览历史（最近读过→最早），给 API 与面板看：读过什么、什么时候、读过几遍。"""
    rows = _history_rows(tenant)
    rows.sort(key=lambda row: str(row.get("last_seen") or ""), reverse=True)
    return rows[:limit] if limit and limit > 0 else rows


def clear_history(tenant: str) -> int:
    """清掉本租户的浏览记忆（别的租户不受影响）——用户有权让冲浪手「忘掉」重看。"""
    backend = _history_backend()
    return sum(1 for row in _history_rows(tenant) if backend.remove(str(row.get("id"))))


def drop_task(task_id: str) -> int:
    """任务删除时回收其会话记录（灵感池为跨任务 idea 库，不随任务删除）。"""
    return _job_backend().drop_task(task_id)


def render_inspiration(cards: list[dict[str, Any]]) -> str:
    """把选中的灵感卡汇成一段可注入 Brief.constraints 的灵感简报文本。"""
    lines = ["【灵感冲浪】选中的灵感："]
    for index, card in enumerate(cards, start=1):
        tag = "（占位，非真实冲浪所得）" if card.get("simulated") else ""
        lines.append(f"{index}. {card.get('idea') or '（缺点子）'}{tag}")
        if card.get("angle"):
            lines.append(f"   角度：{card['angle']}")
        if card.get("hook"):
            lines.append(f"   钩子：{card['hook']}")
        if card.get("why_now"):
            lines.append(f"   为什么现在：{card['why_now']}")
        for src in (card.get("sources") or [])[:3]:
            lines.append(f"   参考：{src}")
    return "\n".join(lines)


def autonomous_tick(now: datetime | None = None) -> int:
    """后台调度入口：按配置的 topics 各跑一轮冲浪囤灵感（无任务归属），返回新增会话数。"""
    cfg = get_config().surf
    moment = now or datetime.now(timezone.utc)
    topics = [str(item).strip() for item in cfg.topics if str(item).strip()]
    if not topics:
        return 0
    created = 0
    for topic in topics:
        try:
            session = create_session(None, topic=topic, provider="", now=moment)
        except (ValueError, Exception) as error:  # noqa: BLE001 - 单个主题失败不拖垮整轮
            log.warn(f"自主冲浪主题「{topic}」失败：{type(error).__name__}: {error}")
            continue
        if str(session.get("status")) != "failed":
            created += 1
    # 无人盯面板时，把本轮仍按演示计时器排队的 sample 会话直接推进到完成（沉淀灵感）
    if created:
        list_sessions(now=moment + timedelta(seconds=_SAMPLE_SECONDS + 1))
    return created


__all__ = [
    "STATUSES",
    "STORE_FILE",
    "POOL_STORE_FILE",
    "HISTORY_STORE_FILE",
    "SurfUnavailable",
    "autonomous_tick",
    "clear_history",
    "create_session",
    "drop_pool_card",
    "drop_task",
    "get_pool_card",
    "get_session",
    "list_history",
    "list_pool",
    "list_sessions",
    "read_history",
    "render_inspiration",
]
