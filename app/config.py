"""运行时配置（移植自 server/config.ts）。

所有可调项均可通过根目录 `.env` 或进程环境变量覆盖，且可在运行期通过
`PUT /api/settings` 动态修改（`update_config`）。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
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
#: LangGraph checkpointer 的落地位置：用于人工审批中断后的断点续跑。
CHECKPOINT_FILE = DATA_DIR / "checkpoints.sqlite"

LLMProviderName = Literal["mock", "openai"]
EmbeddingProviderName = Literal["local", "openai"]


# ------------------------------------------------------------------ #
# 配置模型                                                            #
# ------------------------------------------------------------------ #


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
class RuntimeConfig:
    """编排与质量门禁的运行时参数。"""

    port: int = 8787
    turn_budget: int = 25
    max_revisions: int = 2
    quality_threshold: int = 75
    auto_approve: bool = False
    #: 单任务成本上限（美元）；超出即熔断到离线引擎（plan.md D12）
    cost_budget_usd: float = 0.5
    #: 单任务 token 上限，对应 plan.md 2.4「单 Session token 消耗 > 50K 触发审查」
    token_budget: int = 50_000
    #: 是否启用 LLM 响应缓存（plan.md 4.5）
    llm_cache: bool = True
    llm: LLMSettings = field(default_factory=LLMSettings)
    embedding: EmbeddingSettings = field(default_factory=EmbeddingSettings)
    publish: PublishSettings = field(default_factory=PublishSettings)


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
        turn_budget=_int(os.environ.get("TURN_BUDGET"), 25),
        max_revisions=_int(os.environ.get("MAX_REVISIONS"), 2),
        quality_threshold=_int(os.environ.get("QUALITY_THRESHOLD"), 75),
        auto_approve=_bool(os.environ.get("AUTO_APPROVE"), False),
        cost_budget_usd=_num(os.environ.get("COST_BUDGET_USD"), 0.5),
        token_budget=_int(os.environ.get("TOKEN_BUDGET"), 50_000),
        llm_cache=_bool(os.environ.get("LLM_CACHE"), True),
        llm=LLMSettings(
            provider=provider,  # type: ignore[arg-type]
            base_url=os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1",
            api_key=os.environ.get("OPENAI_API_KEY") or "",
            model=os.environ.get("OPENAI_MODEL") or "gpt-4o-mini",
            temperature=_num(os.environ.get("LLM_TEMPERATURE"), 0.7),
            max_tokens=_int(os.environ.get("LLM_MAX_TOKENS"), 2048),
            timeout_ms=_int(os.environ.get("LLM_TIMEOUT_MS"), 60_000),
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
    )


def _embedding_provider() -> str:
    raw = (os.environ.get("EMBEDDING_PROVIDER") or "local").strip().lower()
    return raw if raw in ("local", "openai") else "local"


_config: RuntimeConfig = _build_config()


def get_config() -> RuntimeConfig:
    """返回全局配置单例（就地修改）。"""
    return _config


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
    if patch.get("llmCache") is not None:
        _config.llm_cache = bool(patch["llmCache"])
    llm_patch = patch.get("llm") or {}
    if llm_patch:
        for key, value in llm_patch.items():
            attr = _LLM_FIELD_BY_CAMEL.get(key)
            if attr is None or value is None:
                continue
            if attr == "provider" and value not in ("mock", "openai"):
                continue
            setattr(_config.llm, attr, value)
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
        elif attr == "weight":
            value = min(1.0, max(0.0, float(value)))
        elif attr in ("retry", "tick_seconds"):
            value = max(1 if attr == "tick_seconds" else 0, int(value))
        setattr(target, attr, value)


_LLM_FIELD_BY_CAMEL = {
    "provider": "provider",
    "baseUrl": "base_url",
    "apiKey": "api_key",
    "model": "model",
    "temperature": "temperature",
    "maxTokens": "max_tokens",
    "timeoutMs": "timeout_ms",
}


def _mask(secret: str) -> str:
    if not secret:
        return ""
    return f"{secret[:4]}{'*' * max(0, len(secret) - 8)}{secret[-4:]}"


def public_config() -> dict[str, Any]:
    """对外输出配置，隐藏密钥明文（仅回传掩码与是否已设置）。"""
    llm = _config.llm
    embedding = _config.embedding
    publish = _config.publish
    return {
        "port": _config.port,
        "turnBudget": _config.turn_budget,
        "maxRevisions": _config.max_revisions,
        "qualityThreshold": _config.quality_threshold,
        "autoApprove": _config.auto_approve,
        "costBudgetUsd": _config.cost_budget_usd,
        "tokenBudget": _config.token_budget,
        "llmCache": _config.llm_cache,
        "authRequired": auth_enabled(),
        "llm": {
            "provider": llm.provider,
            "baseUrl": llm.base_url,
            "apiKey": "",
            "model": llm.model,
            "temperature": llm.temperature,
            "maxTokens": llm.max_tokens,
            "timeoutMs": llm.timeout_ms,
            "apiKeySet": len(llm.api_key) > 0,
            "apiKeyMasked": _mask(llm.api_key),
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
