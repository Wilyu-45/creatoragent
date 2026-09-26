"""运行时配置（移植自 server/config.ts）。

所有可调项均可通过根目录 `.env` 或进程环境变量覆盖，且可在运行期通过
`PUT /api/settings` 动态修改（`update_config`）。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv

# ------------------------------------------------------------------ #
# 路径与 .env 加载（不覆盖已存在的进程环境变量）                        #
# ------------------------------------------------------------------ #

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env", override=False)

#: 持久化根目录。默认 ``<项目根>/data``，可用 ``CREATOR_DATA_DIR`` 覆盖
#: （测试与多实例部署时避免互相污染）。
_DATA_DIR_ENV = (os.environ.get("CREATOR_DATA_DIR") or "").strip()
DATA_DIR = Path(_DATA_DIR_ENV) if _DATA_DIR_ENV else (ROOT_DIR / "data")
TASK_DIR = DATA_DIR / "tasks"
BLACKBOARD_FILE = DATA_DIR / "blackboard.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
#: A11 记忆库（跨任务知识卡片），供其他智能体做 RAG 召回。
MEMORY_FILE = DATA_DIR / "memory.json"
#: Brief 素材目录（plan.md v2.0「多模态理解」）：本地图片素材只允许放在这里，
#: 由 ``app/core/assets.py`` 读取并内联为 data URL。**不接受任意绝对路径** ——
#: Brief 可来自 API 调用方，放开绝对路径等于给模型一个本机文件读取原语。
ASSETS_DIR = DATA_DIR / "assets"
#: 成品导出目录：审批通过后由交付环节把全部版本渲染成 txt 落盘，
#: 供 ``GET /api/tasks/{id}/export`` 下载（只写本目录，不碰任意路径）。
EXPORTS_DIR = DATA_DIR / "exports"
#: LLM-as-a-Judge 评估历史（plan.md 4.3 D14），用于 Prompt 回归与黄金集评测。
EVAL_FILE = DATA_DIR / "evaluations.json"
#: LangGraph checkpointer 的落地位置：用于人工审批中断后的断点续跑。
CHECKPOINT_FILE = DATA_DIR / "checkpoints.sqlite"

LLMProviderName = Literal["mock", "openai"]
#: 数字人渲染提供方：sample = 内置样例引擎（离线可跑）；http = 通用 HTTP 适配样例
DigitalHumanProviderName = Literal["sample", "http"]
EmbeddingProviderName = Literal["local", "openai"]
JudgeModeName = Literal["off", "advisory", "blocking"]
JudgeProviderName = Literal["offline", "llm"]
#: 联网检索提供方：none = 不联网（默认，离线可跑）；http = 走自建搜索网关
SearchProviderName = Literal["none", "http"]

#: 存储后端：file（默认，JSON + SQLite，零依赖离线可跑）| pg（PostgreSQL + Redis，支持多副本）。
#: 替换契约与实现指引见 storage_contract.md；PG 实现只在 ``pg`` 模式下被 import（零依赖保证）。
StorageModeName = Literal["file", "pg"]


def _storage_mode() -> str:
    raw = (os.environ.get("CREATOR_STORAGE") or "file").strip().lower()
    if raw in ("file", "pg"):
        return raw
    import sys

    print(f"[config] CREATOR_STORAGE={raw!r} 非法（只支持 file/pg），回落 file", file=sys.stderr)
    return "file"


#: 存储后端选择在 **import 期固化**：单例构造、checkpointer 分支都依赖它，
#: 进程生命周期内不可变（与 DATA_DIR 同口径）。
STORAGE_MODE: StorageModeName = _storage_mode()  # type: ignore[assignment]

#: PG / Redis 连接配置（仅 ``STORAGE_MODE == "pg"`` 时被 app/core/pg.py 等读取）。
#: 留空时 pg.py 使用本地默认值（见各模块 docstring）。
DATABASE_URL = (os.environ.get("CREATOR_DATABASE_URL") or "").strip()
REDIS_URL = (os.environ.get("CREATOR_REDIS_URL") or "").strip()

#: 评估口径版本号。改动权重或规则时递增，便于「同一批数据跨版本对比」时区分。
#: 放在 config 而非 judge 模块，是为了让 ``public_config()`` 不必反向 import 评估器。
RUBRIC_VERSION = "2026-09-13.judge-v1"


# ------------------------------------------------------------------ #
# 配置模型                                                            #
# ------------------------------------------------------------------ #


@dataclass
class AgentModelOverride:
    """单个智能体的模型覆盖（``LLMSettings.agent_models`` 的值）。

    三个字段**空串都表示继承全局** ``llm`` 设置；用途是让某个智能体改用
    不同的模型或端点（例如 A10 分析走本地 Ollama，其余走云端 DeepSeek）。
    """

    model: str = ""
    base_url: str = ""
    api_key: str = ""


@dataclass
class LLMSettings:
    """模型接入设置。`base_url` 兼容一切 OpenAI 协议服务（DeepSeek/通义/豆包/vLLM/Ollama）。"""

    provider: LLMProviderName = "mock"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    temperature: float = 0.7
    max_tokens: int = 2048
    timeout_ms: int = 60_000
    #: 是否把 Brief 素材里的图片作为多模态输入发给模型（plan.md v2.0「多模态理解」）。
    #: **默认关闭**：绝大多数 OpenAI 兼容文本模型（DeepSeek chat/flash、通义 qwen-plus、
    #: 本地 Ollama 文本模型…）收到图片内容块会直接返回 400，而这是不可重试的确定性失败，
    #: 只会让该智能体降级到离线引擎——默认开启等于给按默认配置跑通的链路埋雷。
    #: 确认所用模型支持读图后打开；素材的**文字清单**不受此项影响，始终可用。
    vision: bool = False
    #: 单次创作最多注入的图片数：多模态输入按图片计费，且图多了会挤占文本预算
    vision_max_images: int = 6
    #: DeepSeek 思考模式开关（``{"thinking": {"type": ...}}``，仅 DeepSeek 网关识别，
    #: 其他 OpenAI 兼容实现会忽略未知字段）。**默认 disabled**：
    #: 官方默认 enabled，思维链输出在 ``reasoning_content`` 且按 output 价计费——
    #: 长提示词下思维链会耗尽 ``max_tokens`` 导致 ``content`` 为空（real_check 实测
    #: A5 稳定复现），且思考模式下 temperature 等采样参数不生效。
    #: 本项目是提示词已含完整推理脚手架的结构化 JSON 流水线，默认关闭；要换更强
    #: 推理时设 ``LLM_THINKING=enabled`` 并调大 ``LLM_MAX_TOKENS``。
    thinking: str = "disabled"
    #: 每智能体模型覆盖：键为 ``A1``–``A99`` 形态的智能体 id，仅 model / base_url /
    #: api_key 三项可覆盖，空字段继承全局。与 site_urls 同属「用户在设置界面维护的
    #: 资产」，持久化到 ``SETTINGS_FILE``（见 ``_load_persisted_settings``）。
    agent_models: dict[str, AgentModelOverride] = field(default_factory=dict)


@dataclass
class EmbeddingSettings:
    """A11 记忆库的向量化设置（plan.md 2.2.3「RAG Server」）。

    默认 ``local``：用确定性 hashing embedding，零依赖、可离线；
    ``openai`` 时走任意 OpenAI 兼容 ``/embeddings`` 端点，失败自动回退本地向量。
    """

    provider: EmbeddingProviderName = "local"
    #: 留空则复用 ``llm.base_url`` / ``llm.api_key``
    base_url: str = ""
    api_key: str = ""
    model: str = "text-embedding-3-small"
    #: 本地向量维度（hashing trick 的桶数）
    dim: int = 256
    #: 混合检索中语义分量权重：0 = 纯关键词（与旧行为一致），越大越依赖向量相似度
    weight: float = 0.35


@dataclass
class PublishSettings:
    """多平台自动排期与投递设置（plan.md v2.0「自动发布」）。

    系统不内置各平台私有 SDK：统一用 webhook 把「到点该投什么」推给运营侧
    的发布网关（或用 n8n/Zapier 转接平台开放接口），本进程只负责排期与回执记账。
    """

    #: 投递 webhook 地址；为空时 dispatch 只做「登记发布」（离线可用）
    webhook_url: str = ""
    #: webhook 失败重试次数
    retry: int = 2
    #: 是否由后台定时器自动投递到期排期
    auto_dispatch: bool = False
    #: 后台自动投递的检查间隔（秒）
    tick_seconds: int = 60


@dataclass
class JudgeSettings:
    """LLM-as-a-Judge 评估流水线设置（plan.md 4.3 D14）。

    评估是**旁路**能力：默认 ``advisory``，只产出报告与建议，不改动门禁结论；
    置为 ``blocking`` 才会让评估分数参与门禁判定。这样「引入评估」不会在
    使用者不知情的情况下改变流水线行为。
    """

    #: off = 不评估；advisory = 只打分不影响门禁；blocking = 低分触发返工
    mode: JudgeModeName = "advisory"
    #: offline = 确定性规则评估器；llm = 走模型网关（失败自动回退 offline）
    provider: JudgeProviderName = "offline"
    #: 可选模型名（provider=llm 时生效，留空用当前 LLM 配置的模型）
    model: str = ""
    #: 通过线：total ≥ 该值为 pass
    pass_threshold: float = 75.0
    #: 评估分在最终质量分中的权重（0 = 不影响 scorecard.overall）
    weight: float = 0.2


@dataclass
class DigitalHumanSettings:
    """数字人视频渲染接入设置（plan.md v2.0「数字人」开发样例）。

    数字人渲染**不在本系统内实现**：各家服务的授权、形象库与回调协议差异极大，
    这里只提供接入样例（``app/core/digital_human.py``）：

    * ``sample``（默认）：内置离线样例引擎，确定性模拟「排队 → 渲染 → 完成」，
      并按视频脚本产出渲染清单 —— 未购买任何服务也能联调 API 与 UI；
    * ``http``：对接「POST 建任务 → GET 查状态」最小契约的任意网关
      （自建渲染农场 / n8n 编排的服务均可），按 ``DIGITAL_HUMAN_API_URL`` 启用。
    """

    provider: DigitalHumanProviderName = "sample"
    #: http 样例适配器的建任务端点（POST）；为空时 http provider 直接报「未配置」
    api_url: str = ""
    #: 调用上述端点的鉴权头（以 ``Bearer `` 前缀拼接）
    api_key: str = ""
    #: 数字人形象标识（avatar id），随任务透传给渲染服务
    avatar: str = ""
    #: http 样例适配器的单次轮询超时（毫秒）
    timeout_ms: int = 10_000


@dataclass
class SearchSettings:
    """联网检索接入设置（A1/A2/A6/A10 的 ``web_search`` / ``page_fetch`` 工具）。

    检索**不内置任何搜索服务**：各家的配额、计费与返回结构差异极大，
    这里只定义「POST 查询 → JSON 结果」的最小契约（``app/core/web.py``），
    由使用者指向自己的搜索网关（SerpAPI / Bing / 自建聚合均可）。

    * ``none``（默认）：完全离线。工具会如实返回「未配置联网检索」，
      智能体据此**不得声称已联网核实**——离线可跑通的默认体验不受影响；
    * ``http``：按 ``WEB_SEARCH_API_URL`` 启用真实检索。
    """

    provider: SearchProviderName = "none"
    #: http 适配器的检索端点（POST）；为空时 http provider 直接报「未配置」
    api_url: str = ""
    #: 调用上述端点的鉴权头（以 ``Bearer `` 前缀拼接）
    api_key: str = ""
    #: 单次检索返回的结果条数上限（同时限制进入提示词的篇幅）
    max_results: int = 8
    #: 单次请求超时（毫秒）；检索是旁路能力，超时必须短于创作链路可接受时延
    timeout_ms: int = 15_000
    #: 是否允许 ``page_fetch`` 抓取检索结果页正文（关掉则只给标题与摘要）
    fetch_pages: bool = True
    #: 单次 ``page_fetch`` 最多抓取的页面数
    max_pages: int = 4
    #: 「站点监控」：用户在运行时设置里配置的待爬页面 URL 列表，
    #: ``site_monitor`` 工具创作时现场抓取正文（与搜索网关无关，独立开关）。
    #: 仅此字段持久化到 ``SETTINGS_FILE``（界面设置不在 storage_contract.md 的
    #: PostgreSQL 实体清单内，pg 模式同样走本地文件，不做迁移）。
    site_urls: list[str] = field(default_factory=list)


@dataclass
class TracingSettings:
    """分布式追踪设置（plan.md 2.4）。

    默认 **不导出**：进程内 span 树完全自实现，因此「零外部依赖、离线可跑通」
    的默认体验不受影响。配置 ``OTLP_ENDPOINT`` 后才加载 OTel SDK 并转发。
    采样只作用于**导出面**（OTLP 与落盘 JSON），进程内轨迹始终完整（UI 排障不受影响）。
    """

    #: 如 http://localhost:4318（OTLP/HTTP）；为空则只做进程内追踪
    otlp_endpoint: str = ""
    #: 资源里的服务名
    service_name: str = "creator-agent-studio"
    #: 形如 ``key1=value1,key2=value2``，供带鉴权的托管 collector 使用
    otlp_headers: str = ""
    #: 采样器，环境变量名与 OTel 规范对齐（OTEL_TRACES_SAMPLER）：
    #: parentbased_always_on（默认）/ parentbased_traceidratio /
    #: always_on / always_off / traceidratio
    sampler: str = "parentbased_always_on"
    #: traceidratio 的采样比例（0-1），对应 OTEL_TRACES_SAMPLER_ARG
    sample_ratio: float = 1.0


@dataclass
class RuntimeConfig:
    """编排与质量门禁的运行时参数。"""

    port: int = 8787
    #: 监听地址（HOST）。默认 127.0.0.1（仅本机，配合反向代理使用）；
    #: 容器端口发布要求进程监听非 loopback（容器清单已显式设 0.0.0.0），
    #: 局域网直连同样设 0.0.0.0 —— 暴露到本机以外前务必设置访问令牌。
    host: str = "127.0.0.1"
    turn_budget: int = 25
    max_revisions: int = 2
    quality_threshold: int = 75
    auto_approve: bool = False
    #: 单任务成本上限（美元）；超出即熔断到离线引擎（plan.md D12）
    cost_budget_usd: float = 0.5
    #: 单任务 token 上限，对应 plan.md 2.4「单 Session token 消耗 > 50K 触发审查」
    token_budget: int = 50_000
    #: 单任务文档研读（digest）的 LLM 调用上限：块数超出时按序截断并在产物里如实记录
    digest_max_calls: int = 12
    #: 是否启用 LLM 响应缓存（plan.md 4.5）
    llm_cache: bool = True
    llm: LLMSettings = field(default_factory=LLMSettings)
    embedding: EmbeddingSettings = field(default_factory=EmbeddingSettings)
    publish: PublishSettings = field(default_factory=PublishSettings)
    judge: JudgeSettings = field(default_factory=JudgeSettings)
    digital_human: DigitalHumanSettings = field(default_factory=DigitalHumanSettings)
    search: SearchSettings = field(default_factory=SearchSettings)
    tracing: TracingSettings = field(default_factory=TracingSettings)


def _num(value: str | None, fallback: float) -> float:
    if value is None:
        return fallback
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed == parsed and parsed not in (float("inf"), float("-inf")) else fallback


def _int(value: str | None, fallback: int) -> int:
    return int(_num(value, fallback))


def _bool(value: str | None, fallback: bool) -> bool:
    if value is None:
        return fallback
    return value.strip().lower() in ("1", "true", "yes", "on")


def _build_config() -> RuntimeConfig:
    provider = (os.environ.get("LLM_PROVIDER") or "mock").strip()
    if provider not in ("mock", "openai"):
        provider = "mock"
    return RuntimeConfig(
        port=_int(os.environ.get("PORT"), 8787),
        host=(os.environ.get("HOST") or "127.0.0.1").strip() or "127.0.0.1",
        turn_budget=_int(os.environ.get("TURN_BUDGET"), 25),
        max_revisions=_int(os.environ.get("MAX_REVISIONS"), 2),
        quality_threshold=_int(os.environ.get("QUALITY_THRESHOLD"), 75),
        auto_approve=_bool(os.environ.get("AUTO_APPROVE"), False),
        cost_budget_usd=_num(os.environ.get("COST_BUDGET_USD"), 0.5),
        token_budget=_int(os.environ.get("TOKEN_BUDGET"), 50_000),
        digest_max_calls=max(1, _int(os.environ.get("DIGEST_MAX_CALLS"), 12)),
        llm_cache=_bool(os.environ.get("LLM_CACHE"), True),
        llm=LLMSettings(
            provider=provider,  # type: ignore[arg-type]
            base_url=os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1",
            api_key=os.environ.get("OPENAI_API_KEY") or "",
            model=os.environ.get("OPENAI_MODEL") or "gpt-4o-mini",
            temperature=_num(os.environ.get("LLM_TEMPERATURE"), 0.7),
            max_tokens=_int(os.environ.get("LLM_MAX_TOKENS"), 2048),
            timeout_ms=_int(os.environ.get("LLM_TIMEOUT_MS"), 60_000),
            vision=_bool(os.environ.get("LLM_VISION"), False),
            vision_max_images=min(12, max(0, _int(os.environ.get("LLM_VISION_MAX_IMAGES"), 6))),
            thinking=(
                "enabled"
                if (os.environ.get("LLM_THINKING") or "").strip().lower() == "enabled"
                else "disabled"
            ),
        ),
        embedding=EmbeddingSettings(
            provider=_embedding_provider(),
            base_url=os.environ.get("EMBEDDING_BASE_URL") or "",
            api_key=os.environ.get("EMBEDDING_API_KEY") or "",
            model=os.environ.get("EMBEDDING_MODEL") or "text-embedding-3-small",
            dim=max(16, _int(os.environ.get("EMBEDDING_DIM"), 256)),
            weight=min(1.0, max(0.0, _num(os.environ.get("EMBEDDING_WEIGHT"), 0.35))),
        ),
        publish=PublishSettings(
            webhook_url=os.environ.get("PUBLISH_WEBHOOK_URL") or "",
            retry=max(0, _int(os.environ.get("PUBLISH_RETRY"), 2)),
            auto_dispatch=_bool(os.environ.get("PUBLISH_AUTO_DISPATCH"), False),
            tick_seconds=max(5, _int(os.environ.get("PUBLISH_TICK_SECONDS"), 60)),
        ),
        judge=JudgeSettings(
            mode=_judge_mode(),
            provider=_judge_provider(),
            model=os.environ.get("JUDGE_MODEL") or "",
            pass_threshold=min(
                100.0, max(0.0, _num(os.environ.get("JUDGE_PASS_THRESHOLD"), 75.0))
            ),
            weight=min(1.0, max(0.0, _num(os.environ.get("JUDGE_WEIGHT"), 0.2))),
        ),
        digital_human=DigitalHumanSettings(
            provider=_digital_human_provider(),
            api_url=(os.environ.get("DIGITAL_HUMAN_API_URL") or "").strip(),
            api_key=(os.environ.get("DIGITAL_HUMAN_API_KEY") or "").strip(),
            avatar=(os.environ.get("DIGITAL_HUMAN_AVATAR") or "").strip(),
            timeout_ms=max(1_000, _int(os.environ.get("DIGITAL_HUMAN_TIMEOUT_MS"), 10_000)),
        ),
        search=SearchSettings(
            provider=_search_provider(),
            api_url=(os.environ.get("WEB_SEARCH_API_URL") or "").strip(),
            api_key=(os.environ.get("WEB_SEARCH_API_KEY") or "").strip(),
            max_results=min(20, max(1, _int(os.environ.get("WEB_SEARCH_MAX_RESULTS"), 5))),
            timeout_ms=max(1_000, _int(os.environ.get("WEB_SEARCH_TIMEOUT_MS"), 8_000)),
            fetch_pages=_bool(os.environ.get("WEB_SEARCH_FETCH_PAGES"), True),
            max_pages=min(10, max(0, _int(os.environ.get("WEB_SEARCH_MAX_PAGES"), 2))),
        ),
        tracing=TracingSettings(
            otlp_endpoint=(os.environ.get("OTLP_ENDPOINT") or "").strip(),
            service_name=(os.environ.get("OTEL_SERVICE_NAME") or "creator-agent-studio").strip()
            or "creator-agent-studio",
            otlp_headers=(os.environ.get("OTLP_HEADERS") or "").strip(),
            sampler=_sampler(),
            sample_ratio=min(1.0, max(0.0, _num(os.environ.get("OTEL_TRACES_SAMPLER_ARG"), 1.0))),
        ),
    )


def _judge_mode() -> str:
    raw = (os.environ.get("JUDGE_MODE") or "advisory").strip().lower()
    return raw if raw in ("off", "advisory", "blocking") else "advisory"


def _judge_provider() -> str:
    raw = (os.environ.get("JUDGE_PROVIDER") or "offline").strip().lower()
    return raw if raw in ("offline", "llm") else "offline"


def _embedding_provider() -> str:
    raw = (os.environ.get("EMBEDDING_PROVIDER") or "local").strip().lower()
    return raw if raw in ("local", "openai") else "local"


def _digital_human_provider() -> str:
    raw = (os.environ.get("DIGITAL_HUMAN_PROVIDER") or "sample").strip().lower()
    return raw if raw in ("sample", "http") else "sample"


def _search_provider() -> str:
    raw = (os.environ.get("WEB_SEARCH_PROVIDER") or "none").strip().lower()
    return raw if raw in ("none", "http") else "none"


_SAMPLERS = (
    "parentbased_always_on",
    "parentbased_traceidratio",
    "always_on",
    "always_off",
    "traceidratio",
)


def _sampler() -> str:
    raw = (os.environ.get("OTEL_TRACES_SAMPLER") or "parentbased_always_on").strip().lower()
    return raw if raw in _SAMPLERS else "parentbased_always_on"


_config: RuntimeConfig = _build_config()


# ------------------------------------------------------------------ #
# 设置持久化：字段 → 环境变量映射（环境变量已设置的字段不读回快照）     #
# ------------------------------------------------------------------ #

_RUNTIME_KEYS = (
    "port",
    "host",
    "turnBudget",
    "maxRevisions",
    "qualityThreshold",
    "autoApprove",
    "costBudgetUsd",
    "tokenBudget",
    "digestMaxCalls",
    "llmCache",
)

_FLAT_PERSIST_ENV: dict[str, str] = {
    "port": "PORT",
    "host": "HOST",
    "turnBudget": "TURN_BUDGET",
    "maxRevisions": "MAX_REVISIONS",
    "qualityThreshold": "QUALITY_THRESHOLD",
    "autoApprove": "AUTO_APPROVE",
    "costBudgetUsd": "COST_BUDGET_USD",
    "tokenBudget": "TOKEN_BUDGET",
    "digestMaxCalls": "DIGEST_MAX_CALLS",
    "llmCache": "LLM_CACHE",
    "embeddingProvider": "EMBEDDING_PROVIDER",
    "embeddingBaseUrl": "EMBEDDING_BASE_URL",
    "embeddingApiKey": "EMBEDDING_API_KEY",
    "embeddingModel": "EMBEDDING_MODEL",
    "embeddingDim": "EMBEDDING_DIM",
    "embeddingWeight": "EMBEDDING_WEIGHT",
    "publishWebhookUrl": "PUBLISH_WEBHOOK_URL",
    "publishRetry": "PUBLISH_RETRY",
    "publishAutoDispatch": "PUBLISH_AUTO_DISPATCH",
    "publishTickSeconds": "PUBLISH_TICK_SECONDS",
    "judgeMode": "JUDGE_MODE",
    "judgeProvider": "JUDGE_PROVIDER",
    "judgeModel": "JUDGE_MODEL",
    "judgePassThreshold": "JUDGE_PASS_THRESHOLD",
    "judgeWeight": "JUDGE_WEIGHT",
    "dhProvider": "DIGITAL_HUMAN_PROVIDER",
    "dhApiUrl": "DIGITAL_HUMAN_API_URL",
    "dhApiKey": "DIGITAL_HUMAN_API_KEY",
    "dhAvatar": "DIGITAL_HUMAN_AVATAR",
    "dhTimeoutMs": "DIGITAL_HUMAN_TIMEOUT_MS",
    "searchProvider": "WEB_SEARCH_PROVIDER",
    "searchApiUrl": "WEB_SEARCH_API_URL",
    "searchApiKey": "WEB_SEARCH_API_KEY",
    "searchMaxResults": "WEB_SEARCH_MAX_RESULTS",
    "searchTimeoutMs": "WEB_SEARCH_TIMEOUT_MS",
    "searchFetchPages": "WEB_SEARCH_FETCH_PAGES",
    "searchMaxPages": "WEB_SEARCH_MAX_PAGES",
    "tracingOtlpEndpoint": "OTLP_ENDPOINT",
    "tracingServiceName": "OTEL_SERVICE_NAME",
    "tracingOtlpHeaders": "OTLP_HEADERS",
    "tracingSampler": "OTEL_TRACES_SAMPLER",
    "tracingSampleRatio": "OTEL_TRACES_SAMPLER_ARG",
}

_LLM_PERSIST_ENV: dict[str, str] = {
    "provider": "LLM_PROVIDER",
    "baseUrl": "OPENAI_BASE_URL",
    "apiKey": "OPENAI_API_KEY",
    "model": "OPENAI_MODEL",
    "temperature": "LLM_TEMPERATURE",
    "maxTokens": "LLM_MAX_TOKENS",
    "timeoutMs": "LLM_TIMEOUT_MS",
    "vision": "LLM_VISION",
    "visionMaxImages": "LLM_VISION_MAX_IMAGES",
    "thinking": "LLM_THINKING",
}


def _load_persisted_settings() -> None:
    """启动时从 ``data/settings.json`` 读回设置界面保存的全量快照。

    优先级：**进程环境变量 > settings.json > .env > 内置默认** —— 环境变量是
    部署方的显式意志（例如 CI 注入 ``LLM_PROVIDER=mock``），必须压过界面保存值，
    门禁才能零 token 验证；因此对应环境变量已设置的字段跳过读回。
    v1 旧文件（仅顶层 siteUrls / agentModels）同样兼容。
    """
    if not SETTINGS_FILE.exists():
        return
    try:
        payload = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if not isinstance(payload, dict):
        return

    flat: dict[str, Any] = {}
    runtime = payload.get("runtime")
    if isinstance(runtime, dict):
        for key in _RUNTIME_KEYS:
            if key in runtime and not os.environ.get(_FLAT_PERSIST_ENV[key]):
                flat[key] = runtime[key]

    llm_raw = payload.get("llm")
    if isinstance(llm_raw, dict):
        llm_patch = {
            key: value
            for key, value in llm_raw.items()
            if key in _LLM_PERSIST_ENV and not os.environ.get(_LLM_PERSIST_ENV[key])
        }
        if llm_patch:
            flat["llm"] = llm_patch
        if isinstance(llm_raw.get("agentModels"), dict):
            flat["agentModels"] = llm_raw["agentModels"]
    elif isinstance(payload.get("agentModels"), dict):  # v1 旧文件
        flat["agentModels"] = payload["agentModels"]

    for section, prefix in (
        ("embedding", "embedding"),
        ("publish", "publish"),
        ("judge", "judge"),
        ("digitalHuman", "dh"),
        ("search", "search"),
        ("tracing", "tracing"),
    ):
        data = payload.get(section)
        if not isinstance(data, dict):
            continue
        for key, value in data.items():
            flat_key = f"{prefix}{key[0].upper()}{key[1:]}"
            env_var = _FLAT_PERSIST_ENV.get(flat_key)
            if env_var is None or os.environ.get(env_var):
                continue
            flat[flat_key] = value

    site_urls = payload.get("search", {}).get("siteUrls") if isinstance(payload.get("search"), dict) else None
    if not isinstance(site_urls, list):
        site_urls = payload.get("siteUrls")  # v1 旧文件
    if isinstance(site_urls, list):
        flat["siteUrls"] = site_urls

    update_config(flat)


def save_persisted_settings() -> None:
    """把设置界面维护的全量快照原子写入 ``SETTINGS_FILE``（PUT /api/settings 调用）。

    密钥随快照落盘：本文件就是「用户的设置界面」的存储，与本机数据同目录
    （``data/``，不入版本库）；对外 API 仍只回掩码（``public_config``）。
    """
    c = _config
    llm = c.llm
    payload = json.dumps(
        {
            "version": 2,
            "runtime": {
                "port": c.port,
                "host": c.host,
                "turnBudget": c.turn_budget,
                "maxRevisions": c.max_revisions,
                "qualityThreshold": c.quality_threshold,
                "autoApprove": c.auto_approve,
                "costBudgetUsd": c.cost_budget_usd,
                "tokenBudget": c.token_budget,
                "digestMaxCalls": c.digest_max_calls,
                "llmCache": c.llm_cache,
            },
            "llm": {
                "provider": llm.provider,
                "baseUrl": llm.base_url,
                "apiKey": llm.api_key,
                "model": llm.model,
                "temperature": llm.temperature,
                "maxTokens": llm.max_tokens,
                "timeoutMs": llm.timeout_ms,
                "vision": llm.vision,
                "visionMaxImages": llm.vision_max_images,
                "thinking": llm.thinking,
                "agentModels": {
                    aid: {"model": ov.model, "baseUrl": ov.base_url, "apiKey": ov.api_key}
                    for aid, ov in sorted(llm.agent_models.items())
                },
            },
            "embedding": {
                "provider": c.embedding.provider,
                "baseUrl": c.embedding.base_url,
                "apiKey": c.embedding.api_key,
                "model": c.embedding.model,
                "dim": c.embedding.dim,
                "weight": c.embedding.weight,
            },
            "publish": {
                "webhookUrl": c.publish.webhook_url,
                "retry": c.publish.retry,
                "autoDispatch": c.publish.auto_dispatch,
                "tickSeconds": c.publish.tick_seconds,
            },
            "judge": {
                "mode": c.judge.mode,
                "provider": c.judge.provider,
                "model": c.judge.model,
                "passThreshold": c.judge.pass_threshold,
                "weight": c.judge.weight,
            },
            "digitalHuman": {
                "provider": c.digital_human.provider,
                "apiUrl": c.digital_human.api_url,
                "apiKey": c.digital_human.api_key,
                "avatar": c.digital_human.avatar,
                "timeoutMs": c.digital_human.timeout_ms,
            },
            "search": {
                "provider": c.search.provider,
                "apiUrl": c.search.api_url,
                "apiKey": c.search.api_key,
                "maxResults": c.search.max_results,
                "timeoutMs": c.search.timeout_ms,
                "fetchPages": c.search.fetch_pages,
                "maxPages": c.search.max_pages,
                "siteUrls": list(c.search.site_urls),
            },
            "tracing": {
                "otlpEndpoint": c.tracing.otlp_endpoint,
                "serviceName": c.tracing.service_name,
                "otlpHeaders": c.tracing.otlp_headers,
                "sampler": c.tracing.sampler,
                "sampleRatio": c.tracing.sample_ratio,
            },
        },
        ensure_ascii=False,
        indent=2,
    )
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(payload + "\n", encoding="utf-8")
    os.replace(tmp, SETTINGS_FILE)


def get_config() -> RuntimeConfig:
    """返回全局配置单例（就地修改）。"""
    return _config


def llm_settings_for(agent_id: str) -> LLMSettings:
    """按智能体合并 LLM 设置：该 id 无覆盖（或覆盖字段全空）时返回全局设置。"""
    override = _config.llm.agent_models.get((agent_id or "").strip().upper())
    if override is None:
        return _config.llm
    patch: dict[str, str] = {}
    if override.model:
        patch["model"] = override.model
    if override.base_url:
        patch["base_url"] = override.base_url
    if override.api_key:
        patch["api_key"] = override.api_key
    return replace(_config.llm, **patch) if patch else _config.llm


def update_config(patch: dict[str, Any]) -> RuntimeConfig:
    """按 patch 就地更新配置；`llm` 子对象做浅合并。"""
    if patch.get("turnBudget") is not None:
        _config.turn_budget = int(patch["turnBudget"])
    if patch.get("maxRevisions") is not None:
        _config.max_revisions = int(patch["maxRevisions"])
    if patch.get("qualityThreshold") is not None:
        _config.quality_threshold = int(patch["qualityThreshold"])
    if patch.get("autoApprove") is not None:
        _config.auto_approve = bool(patch["autoApprove"])
    if patch.get("costBudgetUsd") is not None:
        _config.cost_budget_usd = max(0.0, float(patch["costBudgetUsd"]))
    if patch.get("tokenBudget") is not None:
        _config.token_budget = max(0, int(patch["tokenBudget"]))
    if patch.get("digestMaxCalls") is not None:
        _config.digest_max_calls = max(1, min(60, int(patch["digestMaxCalls"])))
    if patch.get("llmCache") is not None:
        _config.llm_cache = bool(patch["llmCache"])
    # host / port 是启动期参数：写入后随快照持久化，**下次启动生效**
    if isinstance(patch.get("host"), str) and patch["host"].strip():
        _config.host = patch["host"].strip()
    if patch.get("port") is not None:
        try:
            _config.port = max(1, min(65535, int(patch["port"])))
        except (TypeError, ValueError):
            pass
    llm_patch = patch.get("llm") or {}
    if llm_patch:
        for key, value in llm_patch.items():
            attr = _LLM_FIELD_BY_CAMEL.get(key)
            if attr is None or value is None:
                continue
            if attr == "provider" and value not in ("mock", "openai"):
                continue
            if attr == "thinking" and value not in ("enabled", "disabled"):
                continue
            if attr == "vision":
                value = bool(value)
            elif attr == "vision_max_images":
                value = min(8, max(0, _int(str(value), 3))) if isinstance(value, (int, float, str)) else 3
            setattr(_config.llm, attr, value)
    # 每智能体模型覆盖：整体替换（前端恒传全量，缺失的条目即被清除）。
    # apiKey 缺失 = 保留已存值；显式 "" = 清除（继承全局）——前端只在用户
    # 输入非空时才带该键，防止「打开设置点保存」误清已存的覆盖密钥。
    if isinstance(patch.get("agentModels"), dict):
        merged: dict[str, AgentModelOverride] = {}
        for raw_id, raw_spec in patch["agentModels"].items():
            aid = str(raw_id).strip().upper()
            if not (aid.startswith("A") and aid[1:].isdigit()):
                continue
            spec = raw_spec if isinstance(raw_spec, dict) else {}
            existing = _config.llm.agent_models.get(aid)
            api_key = (
                str(spec["apiKey"]).strip()
                if "apiKey" in spec
                else (existing.api_key if existing else "")
            )
            entry = AgentModelOverride(
                model=str(spec.get("model") or "").strip(),
                base_url=str(spec.get("baseUrl") or "").strip(),
                api_key=api_key,
            )
            if entry.model or entry.base_url or entry.api_key:
                merged[aid] = entry
        _config.llm.agent_models = merged
    _apply_flat(
        patch,
        _config.embedding,
        {
            "embeddingProvider": ("provider", ("local", "openai")),
            "embeddingBaseUrl": ("base_url", None),
            "embeddingApiKey": ("api_key", None),
            "embeddingModel": ("model", None),
            "embeddingDim": ("dim", None),
            "embeddingWeight": ("weight", None),
        },
    )
    _apply_flat(
        patch,
        _config.publish,
        {
            "publishWebhookUrl": ("webhook_url", None),
            "publishRetry": ("retry", None),
            "publishAutoDispatch": ("auto_dispatch", None),
            "publishTickSeconds": ("tick_seconds", None),
        },
    )
    _apply_flat(
        patch,
        _config.judge,
        {
            "judgeMode": ("mode", ("off", "advisory", "blocking")),
            "judgeProvider": ("provider", ("offline", "llm")),
            "judgeModel": ("model", None),
            "judgePassThreshold": ("pass_threshold", None),
            "judgeWeight": ("weight", None),
        },
    )
    _apply_flat(
        patch,
        _config.digital_human,
        {
            "dhProvider": ("provider", ("sample", "http")),
            "dhApiUrl": ("api_url", None),
            "dhApiKey": ("api_key", None),
            "dhAvatar": ("avatar", None),
            "dhTimeoutMs": ("timeout_ms", None),
        },
    )
    _apply_flat(
        patch,
        _config.search,
        {
            "searchProvider": ("provider", ("none", "http")),
            "searchApiUrl": ("api_url", None),
            "searchApiKey": ("api_key", None),
            "searchMaxResults": ("max_results", None),
            "searchTimeoutMs": ("timeout_ms", None),
            "searchFetchPages": ("fetch_pages", None),
            "searchMaxPages": ("max_pages", None),
            # site_urls 的逐项校验与去重在 routes 层做，这里原样写入（空数组 = 清空）
            "siteUrls": ("site_urls", None),
        },
    )
    _apply_flat(
        patch,
        _config.tracing,
        {
            "tracingOtlpEndpoint": ("otlp_endpoint", None),
            "tracingServiceName": ("service_name", None),
            "tracingOtlpHeaders": ("otlp_headers", None),
            "tracingSampler": ("sampler", _SAMPLERS),
            "tracingSampleRatio": ("sample_ratio", None),
        },
    )
    return _config


def _apply_flat(patch: dict[str, Any], target: Any, mapping: dict[str, tuple[str, tuple[str, ...] | None]]) -> None:
    """把扁平的 camelCase patch 写进目标设置对象，非法值静默跳过。"""
    for key, (attr, allowed) in mapping.items():
        value = patch.get(key)
        if value is None:
            continue
        if allowed is not None and value not in allowed:
            continue
        if attr == "dim":
            value = max(16, int(value))
        elif attr in ("weight", "pass_threshold"):
            # 0–1 的权重与 0–100 的分数线都按上界收敛，越界值直接夹紧而不是报错
            value = min(100.0 if attr == "pass_threshold" else 1.0, max(0.0, float(value)))
        elif attr == "retry":
            value = max(0, int(value))
        elif attr == "tick_seconds":
            value = max(1, int(value))
        elif attr == "max_results":
            value = min(20, max(1, int(value)))
        elif attr == "timeout_ms":
            value = max(1_000, int(value))
        elif attr == "max_pages":
            value = min(10, max(0, int(value)))
        elif attr == "sample_ratio":
            value = min(1.0, max(0.0, float(value)))
        setattr(target, attr, value)


_LLM_FIELD_BY_CAMEL = {
    "provider": "provider",
    "baseUrl": "base_url",
    "apiKey": "api_key",
    "model": "model",
    "temperature": "temperature",
    "maxTokens": "max_tokens",
    "timeoutMs": "timeout_ms",
    "vision": "vision",
    "visionMaxImages": "vision_max_images",
    "thinking": "thinking",
}

# 模块尾部统一执行：读回持久化快照（update_config 在上方已定义）
_load_persisted_settings()


def _mask(secret: str) -> str:
    if not secret:
        return ""
    return f"{secret[:4]}{'*' * max(0, len(secret) - 8)}{secret[-4:]}"


def public_config() -> dict[str, Any]:
    """对外输出配置，隐藏密钥明文（仅回传掩码与是否已设置）。"""
    llm = _config.llm
    embedding = _config.embedding
    publish = _config.publish
    judge = _config.judge
    digital_human = _config.digital_human
    tracing = _config.tracing
    return {
        "port": _config.port,
        "host": _config.host,
        "turnBudget": _config.turn_budget,
        "maxRevisions": _config.max_revisions,
        "qualityThreshold": _config.quality_threshold,
        "autoApprove": _config.auto_approve,
        "costBudgetUsd": _config.cost_budget_usd,
        "tokenBudget": _config.token_budget,
        "digestMaxCalls": _config.digest_max_calls,
        "llmCache": _config.llm_cache,
        "authRequired": auth_enabled(),
        # 存储后端概要：mode 用于前端/运维确认当前落库方式；
        # 只暴露「是否已配置连接串」的布尔值，URL 含密钥，绝不回传明文（与 _mask 同口径）
        "storage": {
            "mode": STORAGE_MODE,
            "databaseConfigured": bool(DATABASE_URL),
        },
        "llm": {
            "provider": llm.provider,
            "baseUrl": llm.base_url,
            "apiKey": "",
            "model": llm.model,
            "temperature": llm.temperature,
            "maxTokens": llm.max_tokens,
            "timeoutMs": llm.timeout_ms,
            "vision": llm.vision,
            "visionMaxImages": llm.vision_max_images,
            "thinking": llm.thinking,
            "apiKeySet": len(llm.api_key) > 0,
            "apiKeyMasked": _mask(llm.api_key),
            # 每智能体模型覆盖：只回显 model / baseUrl 与密钥掩码，绝不回明文
            "agentModels": {
                aid: {
                    "model": ov.model,
                    "baseUrl": ov.base_url,
                    "apiKeySet": len(ov.api_key) > 0,
                    "apiKeyMasked": _mask(ov.api_key),
                }
                for aid, ov in sorted(llm.agent_models.items())
            },
        },
        "embedding": {
            "provider": embedding.provider,
            "baseUrl": embedding.base_url,
            "model": embedding.model,
            "dim": embedding.dim,
            "weight": embedding.weight,
            "apiKey": "",
            "apiKeySet": len(embedding.api_key) > 0,
            "apiKeyMasked": _mask(embedding.api_key),
        },
        "publish": {
            "webhookUrl": publish.webhook_url,
            "retry": publish.retry,
            "autoDispatch": publish.auto_dispatch,
            "tickSeconds": publish.tick_seconds,
            "webhookSet": bool(publish.webhook_url),
        },
        "judge": {
            "mode": judge.mode,
            "provider": judge.provider,
            "model": judge.model,
            "passThreshold": judge.pass_threshold,
            "weight": judge.weight,
            "rubric": RUBRIC_VERSION,
        },
        "digitalHuman": {
            "provider": digital_human.provider,
            "apiUrl": digital_human.api_url,
            "avatar": digital_human.avatar,
            "apiKey": "",
            "apiKeySet": len(digital_human.api_key) > 0,
            "apiKeyMasked": _mask(digital_human.api_key),
            "timeoutMs": digital_human.timeout_ms,
        },
        "search": {
            "provider": _config.search.provider,
            "apiUrl": _config.search.api_url,
            "apiKey": "",
            "apiKeySet": len(_config.search.api_key) > 0,
            "apiKeyMasked": _mask(_config.search.api_key),
            "maxResults": _config.search.max_results,
            "timeoutMs": _config.search.timeout_ms,
            "fetchPages": _config.search.fetch_pages,
            "maxPages": _config.search.max_pages,
            "siteUrls": list(_config.search.site_urls),
            "configured": _config.search.provider == "http" and bool(_config.search.api_url),
        },
        "tracing": {
            "otlpEndpoint": tracing.otlp_endpoint,
            "serviceName": tracing.service_name,
            "otlpConfigured": bool(tracing.otlp_endpoint),
            "otlpHeadersSet": bool(tracing.otlp_headers),
            "sampler": tracing.sampler,
            "sampleRatio": tracing.sample_ratio,
        },
    }


# ------------------------------------------------------------------ #
# API 鉴权（plan.md D17「安全加固」）                                  #
# ------------------------------------------------------------------ #


def _parse_api_tokens(raw: str) -> dict[str, str]:
    """解析 ``CREATOR_API_TOKENS``，返回 ``token → 租户名`` 映射。

    支持两种写法：``tok1,tok2``（租户均为 ``default``）与
    ``acme:tok1,beta:tok2``（显式指定租户，用于数据隔离）。
    """
    tokens: dict[str, str] = {}
    for chunk in (raw or "").split(","):
        item = chunk.strip()
        if not item:
            continue
        if ":" in item:
            tenant, _, token = item.partition(":")
            tenant, token = tenant.strip(), token.strip()
        else:
            tenant, token = "default", item
        if token:
            tokens[token] = tenant or "default"
    return tokens


#: API Token → 租户名。为空表示不鉴权（单机默认；见 plan.md D17）
API_TOKENS: dict[str, str] = _parse_api_tokens(os.environ.get("CREATOR_API_TOKENS") or "")


def auth_enabled() -> bool:
    """是否已通过 ``CREATOR_API_TOKENS`` 开启 API 鉴权。"""
    return bool(API_TOKENS)


def ensure_dirs() -> None:
    """确保持久化目录存在。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TASK_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
