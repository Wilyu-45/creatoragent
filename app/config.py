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
class RuntimeConfig:
    """编排与质量门禁的运行时参数。"""

    port: int = 8787
    turn_budget: int = 25
    max_revisions: int = 2
    quality_threshold: int = 75
    auto_approve: bool = False
    llm: LLMSettings = field(default_factory=LLMSettings)


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
        llm=LLMSettings(
            provider=provider,  # type: ignore[arg-type]
            base_url=os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1",
            api_key=os.environ.get("OPENAI_API_KEY") or "",
            model=os.environ.get("OPENAI_MODEL") or "gpt-4o-mini",
            temperature=_num(os.environ.get("LLM_TEMPERATURE"), 0.7),
            max_tokens=_int(os.environ.get("LLM_MAX_TOKENS"), 2048),
            timeout_ms=_int(os.environ.get("LLM_TIMEOUT_MS"), 60_000),
        ),
    )


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
    llm_patch = patch.get("llm") or {}
    if llm_patch:
        for key, value in llm_patch.items():
            attr = _LLM_FIELD_BY_CAMEL.get(key)
            if attr is None or value is None:
                continue
            if attr == "provider" and value not in ("mock", "openai"):
                continue
            setattr(_config.llm, attr, value)
    return _config


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
    return {
        "port": _config.port,
        "turnBudget": _config.turn_budget,
        "maxRevisions": _config.max_revisions,
        "qualityThreshold": _config.quality_threshold,
        "autoApprove": _config.auto_approve,
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
    }


def ensure_dirs() -> None:
    """确保持久化目录存在。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TASK_DIR.mkdir(parents=True, exist_ok=True)
