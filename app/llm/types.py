"""LLM 层数据结构（移植自 server/llm/types.ts）。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..core.util import js_round

ChatRole = str  # 'system' | 'user' | 'assistant'

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_DATA_URL_RE = re.compile(r"^data:([^;,]+)(;base64)?,", re.IGNORECASE)


@dataclass
class ImagePart:
    """随消息发送的一张图片（多模态输入）。

    两种引用形态，直接对应 OpenAI 协议的 ``image_url.url``：

    * **remote**——``https://...`` 公网图片地址，由提供方自行拉取；
    * **inline**——``data:image/png;base64,...`` 内联（本地素材在
      ``app/core/assets.py`` 里读成 data URL 后传入，模型无需访问本机文件）。

    ``alt`` 是这张图的**文字说明**：离线引擎与纯文本模型据此理解图片，
    同时也是「图片内容」进入响应缓存指纹与日志的可读部分。
    """

    url: str
    #: auto / low / high，透传给支持该字段的提供方
    detail: str = "auto"
    alt: str = ""

    @property
    def inline(self) -> bool:
        return bool(_DATA_URL_RE.match(self.url))

    def payload(self) -> dict[str, Any]:
        """OpenAI 协议的图片内容块。"""
        image_url: dict[str, Any] = {"url": self.url}
        if self.detail and self.detail != "auto":
            image_url["detail"] = self.detail
        return {"type": "image_url", "image_url": image_url}

    def fingerprint(self) -> str:
        """缓存指纹片段：内联 data URL 是长 base64，必须哈希后再进缓存键。"""
        digest = hashlib.sha1(self.url.encode("utf-8")).hexdigest()[:16]
        return f"{digest}:{self.detail}:{self.alt}"


@dataclass
class ChatMessage:
    role: ChatRole
    content: str
    #: 多模态附件；为空时协议层产出与纯文本时代完全一致的字符串 content
    images: list[ImagePart] = field(default_factory=list)

    @property
    def text(self) -> str:
        """纯文本视图：正文 + 各图的文字说明。

        token 估算、缓存可读部分、日志都用这里——它们只需要「这张图是什么」，
        不需要图片字节；body 不变时该视图与原实现逐字一致。
        """
        if not self.images:
            return self.content
        lines = [
            f"[图片{i + 1}]{'：' + image.alt if image.alt else ''}"
            for i, image in enumerate(self.images)
        ]
        return "\n".join([self.content, *lines]) if self.content else "\n".join(lines)

    def payload_content(self) -> str | list[dict[str, Any]]:
        """OpenAI 协议的 ``content``：无图时是字符串，有图时是内容块数组。"""
        if not self.images:
            return self.content
        blocks: list[dict[str, Any]] = []
        if self.content:
            blocks.append({"type": "text", "text": self.content})
        blocks.extend(image.payload() for image in self.images)
        return blocks

    def cache_fingerprint(self) -> list[Any]:
        """响应缓存指纹片段（图片按哈希计，不把 base64 塞进缓存键）。"""
        return [self.role, self.content, [image.fingerprint() for image in self.images]]


@dataclass
class LLMRequest:
    #: 调用用途，形如 ``A4.copy``。Mock 引擎据此选择生成器，真实模型据此打点。
    purpose: str
    messages: list[ChatMessage] = field(default_factory=list)
    #: 结构化上下文，Mock 引擎从中取材
    context: dict[str, Any] = field(default_factory=dict)
    temperature: float | None = None
    max_tokens: int | None = None
    #: 期望返回 JSON
    json: bool = False


@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    #: prompt_tokens 中**命中提供方前缀缓存**的部分。
    #: DeepSeek 等厂商的缓存命中输入价仅约为未命中的 1/50；
    #: 不区分会让成本严重高估（进而过早熔断），见 ``llm/pricing.py``。
    cached_tokens: int = 0


@dataclass
class LLMResponse:
    content: str = ""
    provider: str = ""
    model: str = ""
    usage: LLMUsage = field(default_factory=LLMUsage)
    latency_ms: int = 0
    #: True 表示来自内置离线模拟引擎或降级兜底
    simulated: bool = False
    #: True 表示命中响应缓存（未真正产生一次模型调用）
    cached: bool = False
    #: 提供方的结束原因。``length`` 表示**被 max_tokens 截断** ——
    #: 真实网关下这是最常见的失败原因，必须能识别出来才能给出可操作的提示。
    finish_reason: str = ""
    #: 降级说明（例如真实模型不可用、成本熔断）
    degraded_reason: str | None = None


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str
    simulated: bool

    def chat(self, request: LLMRequest) -> LLMResponse: ...


def estimate_tokens(text: str) -> int:
    """粗略 token 估算：中文按字符、英文按 4 字符/token。"""
    if not text:
        return 0
    cjk = len(_CJK_RE.findall(text))
    rest = len(text) - cjk
    return max(1, js_round(cjk + rest / 4))
