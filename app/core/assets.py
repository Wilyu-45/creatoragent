"""Brief 素材解析与多模态输入构造（plan.md v2.0「多模态理解」）。

一个任务的素材可能来自公网图片地址、内联 data URL，或 ``data/assets/`` 目录下的
本地文件。本模块把它们统一成 ``Asset``，并提供**两条**消费路径：

* ``assets_prompt_block()``——纯文本素材清单，无条件可用。它把素材类型、画幅、
  用途说明渲染进提示词，因此离线引擎与纯文本模型也能「知道素材是什么」，
  不会对着不存在的画面编内容；
* ``vision_parts()``——可随消息发送的图片块，仅在真实多模态模型下非空
  （判定见 ``app/llm/engine.py::vision_enabled``）。

三条硬规则
----------
* **本地素材只认 ``ASSETS_DIR`` 下的相对路径**：Brief 来自 API 调用方，放开绝对
  路径等于给模型一个本机文件读取原语（与 ``app/core/web.py`` 的 SSRF 防护同一取舍）；
* **失败如实说明**：文件不存在 / 超限 / 越界时条目带 ``issue``，清单里如实写出
  「不可用」，绝不静默丢弃——静默丢弃会让智能体误判「本次没有素材」；
* **能测的自己测**：图片像素尺寸从文件头解析（PNG/JPEG/GIF/WebP/BMP），
  不猜、不靠模型描述。A8 要用它判断「已有素材能否直接用作封面」。
"""

from __future__ import annotations

import base64
import math
import struct
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from ..config import ASSETS_DIR, get_config

if TYPE_CHECKING:  # 仅类型标注：避免 core → llm 的运行时反向依赖
    from ..llm.types import ImagePart

#: 单个素材的字节上限（超过则拒绝内联：base64 后膨胀 1/3，直接烧 token 与成本）
MAX_ASSET_BYTES = 4 * 1024 * 1024
#: 进入提示词的素材条数上限（素材清单是情报，不能挤占创作预算）
MAX_ASSETS = 12

_KIND_LABEL = {"image": "图片", "video": "视频", "document": "文档", "link": "链接"}
_SOURCE_LABEL = {"remote": "公网地址", "inline": "内联数据", "local": "本地素材"}

_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".txt": "text/plain",
}


@dataclass
class Asset:
    """一个解析后的素材条目。"""

    kind: str = "image"
    ref: str = ""
    title: str = ""
    note: str = ""
    #: remote（公网地址）| inline（data URL）| local（assets/ 下文件）
    source: str = "remote"
    mime: str = ""
    width: int = 0
    height: int = 0
    byte_size: int = 0
    #: 不可用原因；非空表示这条素材不能送入模型
    issue: str = ""
    #: 可直接作为 ``image_url`` 使用的内容（data URL）；remote 素材为空
    data_url: str = ""

    @property
    def usable(self) -> bool:
        return not self.issue and bool(self.ref)

    @property
    def is_image(self) -> bool:
        return self.kind == "image" and self.mime.startswith("image/")

    @property
    def aspect_ratio(self) -> str:
        """实测画幅比（如 ``3:4``）；尺寸未知时为空串。"""
        return aspect_ratio(self.width, self.height)

    def as_image_part(self, *, alt: str = "") -> "ImagePart | None":
        """转成可发送的图片块；不可用或非图片时返回 None。"""
        from ..llm.types import ImagePart

        if not self.usable or not self.is_image:
            return None
        url = self.data_url or self.ref
        if not self.data_url and self.source != "remote":
            return None
        return ImagePart(url=url, alt=alt or self.title or self.note)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ref": self.ref,
            "title": self.title,
            "note": self.note,
            "source": self.source,
            "mime": self.mime,
            "width": self.width,
            "height": self.height,
            "bytes": self.byte_size,
            "issue": self.issue,
            "aspect_ratio": self.aspect_ratio,
        }


# ------------------------------------------------------------------ #
# 图片文件头解析                                                      #
# ------------------------------------------------------------------ #


def _webp_size(data: bytes) -> tuple[int, int] | None:
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        return (
            int.from_bytes(data[24:27], "little") + 1,
            int.from_bytes(data[27:30], "little") + 1,
        )
    if chunk == b"VP8 " and len(data) >= 30:
        # 关键帧起始码固定 9d 01 2a，尺寸紧随其后（各 14 位有效）
        if data[23:26] != b"\x9d\x01\x2a":
            return None
        return (
            int.from_bytes(data[26:28], "little") & 0x3FFF,
            int.from_bytes(data[28:30], "little") & 0x3FFF,
        )
    if chunk == b"VP8L" and len(data) >= 25 and data[20] == 0x2F:
        bits = int.from_bytes(data[21:25], "little")
        return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    return None


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    size = len(data)
    index = 2
    while index + 3 < size:
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        if marker in (0x01, 0xD8) or 0xD0 <= marker <= 0xD7:
            index += 2
            continue
        segment = int.from_bytes(data[index + 2 : index + 4], "big")
        # SOF0–SOF15 携带尺寸；C4（DHT）/C8（JPG）/CC（DAC）不是尺寸段
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            if index + 9 > size:
                return None
            height = int.from_bytes(data[index + 5 : index + 7], "big")
            width = int.from_bytes(data[index + 7 : index + 9], "big")
            return width, height
        if segment <= 0:
            return None
        index += 2 + segment
    return None


def image_size(data: bytes) -> tuple[int, int] | None:
    """从文件头解析图片像素尺寸；不认识的格式返回 ``None``（不猜）。"""
    if not data:
        return None
    try:
        if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
            return struct.unpack(">II", data[16:24])
        if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
            return struct.unpack("<HH", data[6:10])
        if data[:2] == b"BM" and len(data) >= 26:
            width, height = struct.unpack("<ii", data[18:26])
            return abs(width), abs(height)
        if data[:4] == b"RIFF" and data[8:12] == b"WEBP" and len(data) >= 20:
            return _webp_size(data)
        if data[:2] == b"\xff\xd8":
            return _jpeg_size(data)
    except (struct.error, ValueError):
        return None
    return None


def aspect_ratio(width: int, height: int) -> str:
    """把像素尺寸化简成 ``3:4`` 这样的画幅比；无法化简时返回空串。"""
    if not width or not height:
        return ""
    divisor = math.gcd(width, height)
    left, right = width // divisor, height // divisor
    # 化简后仍是怪比例（如 1023:767）时没有参考价值，按最接近的常见画幅给出近似
    if left > 32 or right > 32:
        for common_left, common_right in ((1, 1), (4, 3), (3, 4), (16, 9), (9, 16), (3, 2), (2, 3)):
            if abs(width * common_right - height * common_left) <= max(width, height) * 0.04:
                return f"{common_left}:{common_right}"
        return ""
    return f"{left}:{right}"


# ------------------------------------------------------------------ #
# 解析                                                                #
# ------------------------------------------------------------------ #


def _mime_of(ref: str) -> str:
    path = urlparse(ref).path or ref
    dot = path.rfind(".")
    if dot < 0:
        return ""
    return _MIME_BY_EXT.get(path[dot:].lower(), "")


def _load_local(ref: str) -> tuple[bytes | None, str]:
    """读取 ``ASSETS_DIR`` 下的本地素材，返回 ``(字节, 不可用原因)``。"""
    parts = [seg for seg in ref.replace("\\", "/").split("/") if seg not in ("", ".")]
    if not parts or any(seg == ".." for seg in parts):
        return None, "本地素材只接受 assets/ 目录内的相对文件名（拒绝绝对路径与 .. 越界）"
    try:
        root = ASSETS_DIR.resolve()
        path = ASSETS_DIR.joinpath(*parts).resolve()
        if not path.is_relative_to(root):
            return None, "素材路径越出 assets/ 目录"
    except OSError:
        return None, "素材路径无法解析"
    if not path.is_file():
        return None, f"assets/ 目录下没有文件「{ref}」"
    size = path.stat().st_size
    if size > MAX_ASSET_BYTES:
        return None, f"素材 {size // 1024}KB 超过 {MAX_ASSET_BYTES // 1024 // 1024}MB 上限"
    try:
        return path.read_bytes(), ""
    except OSError as error:
        return None, f"读取失败：{error}"


def parse_asset(raw: Any) -> Asset:
    """把一条宽容输入（字符串 / 字典 / ``BriefAsset``）解析成 ``Asset``。"""
    if isinstance(raw, str):
        payload: dict[str, Any] = {"ref": raw}
    elif isinstance(raw, dict):
        payload = dict(raw)
    else:  # BriefAsset 或任何带属性的对象
        payload = {
            key: getattr(raw, key, "")
            for key in ("kind", "ref", "title", "note")
        }
    ref = str(payload.get("ref") or "").strip()
    kind = str(payload.get("kind") or "").strip().lower()
    asset = Asset(
        ref=ref,
        title=str(payload.get("title") or "").strip(),
        note=str(payload.get("note") or "").strip(),
    )
    if not ref:
        asset.issue = "未填写素材地址"
        return asset

    mime = _mime_of(ref)
    if ref.lower().startswith("data:"):
        asset.source = "inline"
        head, _, body = ref.partition(",")
        asset.mime = head[5:].split(";")[0].strip().lower() or mime
        try:
            data = base64.b64decode(body, validate=False)
        except (ValueError, TypeError):
            asset.issue = "data URL 无法解码"
            return asset
        asset.byte_size = len(data)
        size = image_size(data)
        if size:
            asset.width, asset.height = size
        if asset.byte_size > MAX_ASSET_BYTES:
            asset.issue = f"内联素材 {asset.byte_size // 1024}KB 超过上限"
    elif urlparse(ref).scheme in ("http", "https"):
        # 公网素材只做登记：由多模态提供方自行拉取，本进程不下载（避免 SSRF 与额外延迟）
        asset.source = "remote"
        asset.mime = mime or "image/*"
    else:
        asset.source = "local"
        data, issue = _load_local(ref)
        asset.issue = issue
        if data is None:
            return asset
        asset.mime = mime or "image/*"
        asset.byte_size = len(data)
        size = image_size(data)
        if size:
            asset.width, asset.height = size
        asset.data_url = f"data:{asset.mime};base64,{base64.b64encode(data).decode('ascii')}"

    # kind：显式声明优先，其次按 MIME 推断，都没有才当普通链接
    if kind in _KIND_LABEL:
        asset.kind = kind
    elif asset.mime.startswith("image/"):
        asset.kind = "image"
    elif asset.mime.startswith("video/"):
        asset.kind = "video"
    else:
        asset.kind = "document" if asset.mime else "link"
    return asset


def parse_assets(raw: Any) -> list[Asset]:
    """解析 Brief 的素材列表（宽容输入，最多 ``MAX_ASSETS`` 条）。

    完全空白的条目（表单多打的一行回车）直接丢掉：它既没有地址也没有名字，
    留在清单里只是噪声。**有名字但没地址**的条目保留并标注 issue ——
    「使用者写了素材但地址无效」是需要让智能体知道的信息。
    """
    if not isinstance(raw, (list, tuple)):
        return []
    assets: list[Asset] = []
    for item in list(raw)[:MAX_ASSETS]:
        asset = parse_asset(item)
        if not asset.ref and not asset.title and not asset.note:
            continue
        assets.append(asset)
    return assets


# ------------------------------------------------------------------ #
# 消费：提示词块 / 图片块 / 结构化摘要                                 #
# ------------------------------------------------------------------ #


def describe_asset(asset: Asset, index: int) -> str:
    """渲染一条素材的可读描述（一行 + 可选的用途与不可用原因）。"""
    label = _KIND_LABEL.get(asset.kind, asset.kind)
    line = f"{index}. [{label}] {asset.title or asset.ref}｜{_SOURCE_LABEL.get(asset.source, asset.source)}"
    if asset.width and asset.height:
        line += f"｜{asset.width}×{asset.height}" + (f"（{asset.aspect_ratio}）" if asset.aspect_ratio else "")
    if asset.byte_size:
        line += f"｜{max(1, asset.byte_size // 1024)}KB"
    if asset.note:
        line += f"\n   用途：{asset.note}"
    if asset.issue:
        line += f"\n   本条不可用：{asset.issue}（不得引用其中内容）"
    return line


def assets_prompt_block(raw: Any, *, title: str = "参考素材") -> str:
    """渲染素材清单提示词块；**没有素材时返回空串**（提示词与历史逐字一致）。

    末行如实区分「图片已随消息附带」与「本模型不读图」——两种情况下智能体
    可用的信息不同，含糊其辞会诱导模型描述它其实看不到的画面。
    """
    assets = parse_assets(raw)
    if not assets:
        return ""
    from ..llm.engine import vision_enabled

    seen_vision = vision_enabled() and any(asset.as_image_part() for asset in assets)
    lines = [f"【{title}】本次任务附带 {len(assets)} 条素材（由使用者提供，非系统生成）："]
    lines.extend(describe_asset(asset, index) for index, asset in enumerate(assets, start=1))
    lines.append(
        "（图片已随本消息以多模态形式提供，请直接依据画面内容工作；"
        "画幅与用途以上述标注为准。）"
        if seen_vision
        else "（本模型不读图，只能依据上述文字描述与用途说明使用素材；"
        "不得凭空描述画面细节，也不得引用标注为不可用的素材。）"
    )
    return "\n".join(lines) + "\n\n"


def vision_parts(raw: Any, *, limit: int | None = None) -> list["ImagePart"]:
    """取可随消息发送的图片块；多模态不可用时返回空列表。

    ``limit`` 默认取 ``LLM_VISION_MAX_IMAGES``——多模态输入按图片计费，
    且图多了会挤占文本预算。
    """
    from ..llm.engine import vision_enabled

    if not vision_enabled():
        return []
    if limit is None:
        raw_limit = getattr(get_config().llm, "vision_max_images", 3)
        limit = int(raw_limit) if isinstance(raw_limit, (int, float)) else 3
    parts: list["ImagePart"] = []
    for asset in parse_assets(raw):
        part = asset.as_image_part()
        if part is not None:
            parts.append(part)
        if len(parts) >= max(0, limit):
            break
    return parts


def inventory(raw: Any) -> dict[str, Any]:
    """结构化素材清单（供工具层 ``data`` 与追踪排障）。"""
    assets = parse_assets(raw)
    return {
        "count": len(assets),
        "usable": sum(1 for asset in assets if asset.usable),
        "items": [asset.as_dict() for asset in assets],
    }


__all__ = [
    "Asset",
    "MAX_ASSETS",
    "MAX_ASSET_BYTES",
    "aspect_ratio",
    "assets_prompt_block",
    "describe_asset",
    "image_size",
    "inventory",
    "parse_asset",
    "parse_assets",
    "vision_parts",
]