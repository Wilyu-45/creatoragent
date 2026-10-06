"""桌面宠物生成（旁路作业，与 ``core/imagegen.py`` 同一范式）。

定位（务必如实理解）
--------------------
「一只会在桌面上动的宠物」由两件事组成：**动作帧**与**运行器**。本模块负责前者——
把任务的 ``visual_brief``（风格 / 色板）翻译成一套动作帧 + ``pet.json`` 清单 + 宠物包
（zip）；运行器是仓库内置的 ``app/desktop_pet.py``（标准库 tkinter，透明置顶窗）。

与图片/视频生成一样走**旁路作业**（``gen_jobs`` 后端），不新增黑板产物：新增
Artifact type 要过「契约 + 产出方 + 前端 + 断言」四处（见 MEMORY #31/#37），而本能力
的产出是供下载的独立文件包，不是下游智能体要读的中间结论。

两条通道（``provider`` 可用请求体覆盖）：

* ``sample``（默认，零依赖）：离线确定性推进生命周期，并用**纯标准库 PNG 编码器**
  画出真实可用的动作帧。之所以不像 imagegen 那样「只出清单」：宠物包必须带真图才能
  当场跑起来，否则联调方拿不到可交付物。帧是程序绘制的简笔形象，**不是真实美术稿**，
  故清单里 ``simulated=true``；
* ``imagegen``：复用 ``IMAGEGEN_*`` 端点（openai/local 协议）**逐帧**出图，帧为真图。

诚实边界（不要误读能力范围）
----------------------------
* ``imagegen`` 通道每帧一次调用，跨帧形象一致性由 prompt 承担；**未实现抠图、
  sprite sheet 切片与帧对齐**（那要引图像处理依赖，与「零依赖离线可跑」冲突）。真实
  帧的尺寸与背景由网关决定，运行器按 ``size`` 自行降采样显示。
* ``IMAGEGEN_PROVIDER=sample`` 或未配 ``IMAGEGEN_BASE_URL`` 时，``imagegen`` 通道
  **显式失败，绝不假装出图**。
* sample 帧的背景固定为透明色 ``KEY_COLOR``：标准库 tkinter 的桌面透明只有
  ``-transparentcolor`` 这一条路，没有 alpha 通道可用。
"""

from __future__ import annotations

import base64
import colorsys
import hashlib
import json
import math
import shutil
import struct
import threading
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from ..config import ASSETS_DIR, DATA_DIR, ROOT_DIR, get_config
from ..logger import create_logger
from ..llm.engine import _is_local_endpoint
from .clock import now_iso
from .events import event_bus, new_id
from .gen_jobs import FileJobBackend, PgJobBackend, make_backend
from .tracing import tracer
from .types import TaskRecord

log = create_logger("petgen")

#: 作业状态机：queued → generating → done / failed
STATUSES = ("queued", "generating", "done", "failed")

#: 作业存储（file 模式 JSON；pg 模式 pet_jobs 表，见 storage_contract.md）
STORE_FILE = DATA_DIR / "petgen_jobs.json"
_TABLE = "pet_jobs"
#: 宠物包根目录：每个作业一个子目录（frames/ + pet.json + README），只写本目录
PETS_DIR = DATA_DIR / "pets"

#: 运行器源码：随宠物包交付，脱开本仓库也能 `python runner/pet.py` 跑起来
RUNNER_FILE = ROOT_DIR / "app" / "desktop_pet.py"

#: 动作表：名称 → (帧数, 每秒帧数, 是否循环)。运行器按此播放，总帧数受
#: PETGEN_MAX_FRAMES 约束（imagegen 通道每帧一次计费调用）。
ACTIONS: dict[str, dict[str, Any]] = {
    "idle": {"frames": 4, "fps": 6, "loop": True},
    "walk": {"frames": 4, "fps": 8, "loop": True},
    "sleep": {"frames": 2, "fps": 2, "loop": True},
    "happy": {"frames": 3, "fps": 10, "loop": False},
    "drag": {"frames": 1, "fps": 1, "loop": False},
}
ACTION_NAMES = tuple(ACTIONS)

#: 透明色：帧图背景必须就是它，运行器把它设为窗口 ``-transparentcolor``
KEY_COLOR = (255, 0, 254)
KEY_HEX = "#{:02X}{:02X}{:02X}".format(*KEY_COLOR)

#: 每帧的保守成本估算（美元），仅用于预算熔断的事前判断；真实计费归网关侧。
_EST_COST_PER_FRAME_USD = 0.04

_BACKEND: "FileJobBackend | PgJobBackend | None" = None
_BACKEND_LOCK = threading.RLock()


def _job_backend() -> "FileJobBackend | PgJobBackend":
    global _BACKEND
    if _BACKEND is None:
        with _BACKEND_LOCK:
            if _BACKEND is None:
                _BACKEND = make_backend(STORE_FILE, _TABLE)
    return _BACKEND


def _num(value: Any, fallback: float = 0.0) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return fallback


def _clamp(value: Any, low: int, high: int, fallback: int) -> int:
    return max(low, min(high, int(_num(value, fallback))))


# ------------------------------------------------------------------ #
# 帧绘制：纯标准库 PNG（不引 Pillow）                                   #
# ------------------------------------------------------------------ #


def _png_bytes(size: int, buf: bytearray) -> bytes:
    """RGB 字节缓冲 → PNG（8bit truecolor、无 alpha，背景即透明色）。"""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    raw = b"".join(
        b"\x00" + bytes(buf[row * size * 3:(row + 1) * size * 3]) for row in range(size)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


class _Canvas:
    """最小光栅画布：只画实心椭圆与短条，够拼出吉祥物，又不需要图像库。"""

    def __init__(self, size: int, background: tuple[int, int, int]) -> None:
        self.size = size
        self.buf = bytearray(bytes(background) * (size * size))

    def _put(self, x: int, y: int, rgb: tuple[int, int, int]) -> None:
        if 0 <= x < self.size and 0 <= y < self.size:
            offset = (y * self.size + x) * 3
            self.buf[offset:offset + 3] = bytes(rgb)

    def oval(
        self,
        cx: float,
        cy: float,
        rx: float,
        ry: float,
        rgb: tuple[int, int, int],
        *,
        taper_top: float = 0.0,
    ) -> None:
        """填充椭圆；``taper_top`` 让上半部收窄（0.15 = 顶部横半径缩到 85%）。"""
        if rx <= 0 or ry <= 0:
            return
        for y in range(max(0, int(cy - ry) - 1), min(self.size, int(cy + ry) + 2)):
            vertical = (y - cy) / ry
            width = rx * (1.0 - taper_top * max(0.0, -vertical))
            if width <= 0:
                continue
            for x in range(max(0, int(cx - width) - 1), min(self.size, int(cx + width) + 2)):
                if ((x - cx) / width) ** 2 + vertical**2 <= 1.0:
                    self._put(x, y, rgb)

    def band(self, cx: float, cy: float, length: float, thickness: float, rgb: tuple[int, int, int]) -> None:
        """横向短条（闭眼、嘴）：比画线粗一点才在 96px 下看得见。"""
        for y in range(int(cy), int(cy) + max(1, int(thickness))):
            for x in range(int(cx - length / 2), int(cx + length / 2) + 1):
                self._put(x, y, rgb)


def _mix(rgb: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    """按 ``amount``（-1..1）压暗或提亮：负数偏黑、正数偏白。"""
    if amount >= 0:
        return tuple(int(round(c + (255 - c) * amount)) for c in rgb)  # type: ignore[return-value]
    factor = 1.0 + amount
    return tuple(int(round(c * factor)) for c in rgb)  # type: ignore[return-value]


def _hex_rgb(raw: Any) -> tuple[int, int, int] | None:
    text = str(raw or "").strip().lstrip("#")
    if len(text) == 3:
        text = "".join(char * 2 for char in text)
    if len(text) != 6:
        return None
    try:
        value = int(text, 16)
    except ValueError:
        return None
    return (value >> 16 & 255, value >> 8 & 255, value & 255)


def _avoid_key(rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    """撞色保护：配色恰好等于透明色时整块会被擦掉，微调一个通道。"""
    return (rgb[0], rgb[1], 253) if rgb == KEY_COLOR else rgb


def _brand_rgb(seed: str) -> tuple[int, int, int]:
    """品牌名 → 稳定色相。用 blake2b 而非内置 ``hash()``：后者随进程盐变化，断言会漂。"""
    digest = hashlib.blake2b(seed.encode("utf-8"), digest_size=2).digest()
    hue = int.from_bytes(digest, "big") / 65535.0
    red, green, blue = colorsys.hsv_to_rgb(hue, 0.55, 0.85)
    return (int(red * 255), int(green * 255), int(blue * 255))


def _resolve_palette(visual: dict[str, Any], brand: str) -> dict[str, str]:
    """色板：优先 visual_brief 的 palette（品牌 VI 一致性），缺失时按品牌名派生。"""
    direction = visual.get("visual_direction") if isinstance(visual.get("visual_direction"), dict) else {}
    found: list[tuple[int, int, int]] = []
    for item in direction.get("palette") or []:
        parsed = _hex_rgb(item.get("hex")) if isinstance(item, dict) else None
        if parsed:
            found.append(parsed)
    seed = _brand_rgb(brand or "creator")
    body = _avoid_key(found[0] if found else seed)
    return {
        "body": "#{:02X}{:02X}{:02X}".format(*body),
        "belly": "#{:02X}{:02X}{:02X}".format(*_mix(body, 0.45)),
        "ear": "#{:02X}{:02X}{:02X}".format(*_avoid_key(found[1] if len(found) > 1 else _mix(body, 0.28))),
        "line": "#{:02X}{:02X}{:02X}".format(*_mix(body, -0.7)),
        "pop": "#{:02X}{:02X}{:02X}".format(*_avoid_key(found[2] if len(found) > 2 else _mix(body, -0.2))),
        "key": KEY_HEX,
    }


def _draw_frame(size: int, action: str, index: int, total: int, palette: dict[str, str]) -> bytes:
    """画一帧：椭圆拼出身体，动作与序号决定弹跳、摆尾、四肢与眼睛状态。"""
    # 缺色时用主色推导，保证任何清单都能画出帧（不靠调用方一定填满色板）
    body = _hex_rgb(palette.get("body")) or _brand_rgb("creator")
    rgb = {
        "body": body,
        "belly": _hex_rgb(palette.get("belly")) or _mix(body, 0.45),
        "ear": _hex_rgb(palette.get("ear")) or _mix(body, 0.28),
        "line": _hex_rgb(palette.get("line")) or _mix(body, -0.7),
        "pop": _hex_rgb(palette.get("pop")) or _mix(body, -0.2),
    }
    if rgb["body"] == KEY_COLOR:
        rgb["body"] = (250, 250, 250)
    canvas = _Canvas(size, KEY_COLOR)
    s = size / 96.0  # 坐标按 96px 基准设计，其他尺寸等比缩放
    angle = math.sin((index / max(1, total)) * math.tau)

    bounce = {
        "idle": angle * 2.0,
        "walk": -abs(math.sin((index / max(1, total)) * math.tau)) * 3.0,
        "sleep": 1.5,
        "happy": -abs(math.sin((index / max(1, total)) * math.tau)) * 9.0,
        "drag": -4.0,
    }.get(action, angle * 2.0)
    lying = action == "sleep"
    cx = 48.0 * s + (4.0 * s if action == "drag" else 0.0)
    cy = (58.0 + bounce) * s
    rx = (26.0 if lying else 20.0) * s
    ry = (13.0 if lying else 22.0) * s

    # 影子贴地，弹跳越高越小
    canvas.oval(48.0 * s, 82.0 * s, 16.0 * s * max(0.4, 1.0 - abs(bounce) / 24.0), 3.0 * s, (228, 228, 238))

    tail_wag = angle * (6.0 if action == "walk" else 3.0)
    canvas.oval(cx + rx * 0.95, cy - 4.0 * s + tail_wag * s, 7.0 * s, 5.0 * s, rgb["pop"])
    step = angle * 4.0 if action == "walk" else 0.0
    for side, offset in ((-1, step), (1, -step)):
        canvas.oval(cx + side * 9.0 * s, cy + ry * 0.9 * s + offset * s, 6.0 * s, 4.5 * s, rgb["body"])

    canvas.oval(cx, cy, rx, ry, rgb["body"], taper_top=0.12)
    canvas.oval(cx, cy + ry * 0.25, rx * 0.6, ry * 0.55, rgb["belly"])

    ear_lift = -3.0 if action in ("happy", "drag") else 0.0
    for side in (-1, 1):
        canvas.oval(cx + side * rx * 0.55, cy - ry + ear_lift * s, 6.0 * s, 9.0 * s, rgb["body"])
        canvas.oval(cx + side * rx * 0.55, cy - ry * 0.95 + ear_lift * s, 3.0 * s, 5.5 * s, rgb["ear"])

    head_cy = cy - ry * 0.75
    closed = lying or (action == "idle" and index == total)
    if closed:
        for side in (-1, 1):
            canvas.band(cx + side * rx * 0.42, head_cy, 7.0 * s, 2.0 * s, rgb["line"])
    elif action == "happy":
        for side in (-1, 1):  # ^_^：三条递减短横拼出弧形眼
            for row in range(3):
                canvas.band(
                    cx + side * rx * 0.42,
                    head_cy - (2 - row) * s,
                    (3 + row * 2) * s,
                    1.4 * s,
                    rgb["line"],
                )
    else:
        for side in (-1, 1):
            canvas.oval(cx + side * rx * 0.42, head_cy, 3.4 * s, 4.0 * s, rgb["line"])
            canvas.oval(cx + side * rx * 0.42 + 1.2 * s, head_cy - 1.4 * s, 1.2 * s, 1.4 * s, (255, 255, 255))

    canvas.oval(cx, head_cy + 7.0 * s, 2.6 * s, 2.0 * s, rgb["pop"])
    if action in ("happy", "walk"):
        canvas.band(cx, head_cy + 11.0 * s, 9.0 * s, 2.0 * s, rgb["line"])
    if lying:
        for rank, radius in enumerate((2.2, 1.6, 1.0)):  # 睡觉的 zzz：三个递减小圆
            canvas.oval(cx + (18 + rank * 6) * s, cy - (26 + rank * 7) * s, radius * s, radius * s, rgb["line"])

    return _png_bytes(size, canvas.buf)


# ------------------------------------------------------------------ #
# 清单：把 visual_brief 翻译成「该出哪些帧」                            #
# ------------------------------------------------------------------ #


def _requested_actions(value: Any) -> list[str]:
    """请求体里的动作子集：只认已知名称、保持 ACTIONS 顺序（前端列表与断言才稳定）。"""
    raw = value if isinstance(value, list) else []
    wanted = {str(item).strip().lower() for item in raw}
    picked = [name for name in ACTION_NAMES if name in wanted]
    return picked or list(ACTION_NAMES)


def build_manifest(
    visual: dict[str, Any],
    *,
    provider: str,
    brand: str = "",
    name: str = "",
    actions: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """构造宠物清单：动作/帧数/尺寸/色板 + imagegen 通道的逐帧 prompt。"""
    cfg = get_config().pet_gen
    size = _clamp(cfg.frame_size, 32, 192, 96)
    max_frames = _clamp(cfg.max_frames, 1, 24, 16)
    picked = _requested_actions(actions)

    direction = visual.get("visual_direction") if isinstance(visual.get("visual_direction"), dict) else {}
    style = str(direction.get("style") or "").strip()
    mood = str(direction.get("mood") or "").strip()
    prompts = [
        str(item.get("prompt") or "").strip()
        for item in (visual.get("image_prompts") or [])
        if isinstance(item, dict) and str(item.get("prompt") or "").strip()
    ]

    warnings: list[str] = []
    if not prompts:
        warnings.append("visual_brief 没有 image_prompts，真实通道的 prompt 与品牌无关")

    segments: list[dict[str, Any]] = []
    truncated = False
    for action in picked:
        spec = ACTIONS[action]
        for index in range(1, int(spec["frames"]) + 1):
            if len(segments) >= max_frames:
                truncated = True
                break
            base = prompts[(index - 1) % len(prompts)] if prompts else ""
            segments.append(
                {
                    "action": action,
                    "index": index,
                    "fps": spec["fps"],
                    "loop": spec["loop"],
                    "prompt": "，".join(
                        part
                        for part in (
                            base,
                            "一只可作为桌面宠物的吉祥物角色，全身像，正视角，纯色简洁背景",
                            f"动作：{action}，第 {index} 帧",
                            f"风格：{style}" if style else "",
                            f"情绪：{mood}" if mood else "",
                        )
                        if part
                    ),
                }
            )
        if truncated:
            break
    if truncated:
        warnings.append(f"受 PETGEN_MAX_FRAMES={max_frames} 限制，本次仅出前 {max_frames} 帧，余下动作未生成")

    return {
        "provider": provider,
        "name": (name or "").strip() or (f"{brand}·吉祥物" if brand else "小宠物"),
        "size": size,
        "fps": 6,
        "key_color": KEY_HEX,
        "actions": picked,
        "frame_count": len(segments),
        "segments": segments,
        "palette": _resolve_palette(visual, brand),
        "palette_source": "visual_brief" if direction.get("palette") else "brand_hash",
        "brand": brand,
        # 帧是不是真图，取决于通道；sample 画的是简笔形象，必须让下游看得见
        "simulated": provider == "sample",
        "warnings": warnings,
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
    }


# ------------------------------------------------------------------ #
# 宠物包落盘：pet.json + frames/ + README                              #
# ------------------------------------------------------------------ #


def _pet_dir(job_id: str) -> Path:
    return PETS_DIR / str(job_id)


def _runner_source() -> str | None:
    """读运行器源码；读不到（打包 exe 未带源码）返回 None，由清单如实标注 ``runner=""``。"""
    for candidate in (RUNNER_FILE, Path(__file__).with_name("desktop_pet.py")):
        try:
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8")
        except OSError:
            continue
    return None


def _pet_json(job: dict[str, Any], manifest: dict[str, Any], frames: list[dict[str, Any]]) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    for action in manifest.get("actions") or []:
        entries = sorted(
            (
                {"index": int(frame["index"]), "file": f"frames/{Path(frame['ref']).name}"}
                for frame in frames
                if frame.get("action") == action
            ),
            key=lambda item: item["index"],
        )
        if entries:
            spec = ACTIONS[action]
            actions.append({"name": action, "fps": spec["fps"], "loop": spec["loop"], "frames": entries})
    return {
        "name": manifest.get("name") or "小宠物",
        "job_id": job.get("id"),
        "task_id": job.get("task_id"),
        "provider": job.get("provider"),
        "size": manifest.get("size"),
        "fps": manifest.get("fps"),
        "key_color": manifest.get("key_color"),
        "simulated": bool(manifest.get("simulated")),
        "palette": manifest.get("palette"),
        "actions": actions,
        "default_action": "idle",
        "warnings": manifest.get("warnings") or [],
        "runner": "runner/pet.py" if _runner_source() is not None else "",
        "created_at": now_iso(),
    }


def _readme(manifest: dict[str, Any]) -> str:
    lines = [
        f"# {manifest.get('name')}（桌面宠物包）",
        "",
        f"- 通道：`{manifest.get('provider')}`｜帧数：{manifest.get('frame_count')}｜设计尺寸：{manifest.get('size')}px",
        f"- 透明色：`{manifest.get('key_color')}`（sample 帧的背景即此色，运行器用 -transparentcolor 抠掉）",
        f"- 动作：{'、'.join(str(item) for item in manifest.get('actions') or [])}",
        "",
        "## 运行",
        "",
        "```bash",
        "python -m app.desktop_pet <本目录或本目录的 zip>",
        "# 或包内自带运行器：",
        "python runner/pet.py .",
        "```",
        "",
        "运行器只用标准库 tkinter：透明置顶无边框窗、按 `pet.json` 播放动作、可拖拽、"
        "右键菜单切换动作与退出。",
    ]
    if manifest.get("simulated"):
        lines += [
            "",
            "> 注意：帧由 `sample` 离线样例引擎绘制（`pet.json` 里 `simulated=true`），",
            "> 用于打通交付形态，**不是真实美术稿**。配好 `IMAGEGEN_*` 后用",
            "> `provider=imagegen` 重新生成即可换成真图帧。",
        ]
    for warning in manifest.get("warnings") or []:
        lines += ["", f"> ⚠ {warning}"]
    return "\n".join(lines) + "\n"


def _write_package(job: dict[str, Any], frames: list[dict[str, Any]]) -> None:
    """写 pet.json 与 README（帧已由通道各自落盘）；调用方负责把 OSError 转成失败。"""
    manifest = job.get("manifest") or {}
    directory = _pet_dir(str(job["id"]))
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pet.json").write_text(
        json.dumps(_pet_json(job, manifest, frames), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (directory / "README.md").write_text(_readme(manifest), encoding="utf-8")


# ------------------------------------------------------------------ #
# 样例引擎（sample）：按流逝时间惰性推进，完成时真画帧                   #
# ------------------------------------------------------------------ #


def _sample_seconds(manifest: dict[str, Any]) -> int:
    return min(20, max(5, 2 + int(manifest.get("frame_count") or 0) // 2))


def _advance_sample(job: dict[str, Any], now: datetime) -> None:
    started = _parse_iso(str(job.get("started_at") or ""))
    if started is None:
        return
    elapsed = (now - started).total_seconds()
    manifest = job.get("manifest") or {}
    seconds = int(job.get("render_seconds") or _sample_seconds(manifest))
    if elapsed < 1.0:
        _transition(job, "queued", "样例引擎已受理（排队中）")
        job["progress"] = 0
        return
    if elapsed < seconds:
        _transition(job, "generating", f"样例绘制进行中（{seconds}s 总时长）")
        job["progress"] = min(99, int(elapsed / seconds * 100))
        return

    try:
        directory = _pet_dir(str(job["id"]))
        frames_dir = directory / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        size = _clamp(manifest.get("size"), 32, 192, 96)
        palette = manifest.get("palette") if isinstance(manifest.get("palette"), dict) else {}
        per_action = {
            action: sum(1 for item in manifest.get("segments") or [] if item.get("action") == action)
            for action in (manifest.get("actions") or [])
        }
        frames: list[dict[str, Any]] = []
        for segment in manifest.get("segments") or []:
            action = str(segment.get("action") or "idle")
            index = int(_num(segment.get("index"), 1))
            blob = _draw_frame(size, action, index, per_action.get(action, 1), palette)
            (frames_dir / f"{action}-{index}.png").write_bytes(blob)
            frames.append(
                {
                    "action": action,
                    "index": index,
                    "ref": f"pets/{job['id']}/frames/{action}-{index}.png",
                    "bytes": len(blob),
                    "simulated": True,
                }
            )
        _write_package(job, frames)
    except OSError as error:  # 落盘失败不能报成功（MEMORY #24 同源：目录不可写要看得见）
        _transition(job, "failed", f"帧落盘失败：{type(error).__name__}: {error}")
        return
    _transition(job, "done", f"样例帧已绘制（{len(frames)} 帧）")
    job["progress"] = 100
    job["finished_at"] = now_iso()
    job["frames"] = frames
    job["pet_ref"] = f"pets/{job['id']}/pet.json"


# ------------------------------------------------------------------ #
# imagegen 通道：复用 IMAGEGEN_* 端点逐帧出图（受理时同步执行）          #
# ------------------------------------------------------------------ #


def _image_headers() -> dict[str, str]:
    cfg = get_config().image_gen
    headers: dict[str, str] = {}
    if cfg.api_key:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    traceparent = tracer.current_traceparent()
    if traceparent:
        headers["traceparent"] = traceparent
    return headers


def _image_channel_usable() -> str:
    """真实出帧通道是否可用：返回空串表示可用，否则返回失败原因（不假装成功）。"""
    cfg = get_config().image_gen
    if cfg.provider == "sample":
        return "未配置真实图片通道：IMAGEGEN_PROVIDER=sample 没有出帧端点，请设为 openai 或 local"
    if not cfg.base_url:
        return "未配置 IMAGEGEN_BASE_URL，无法出帧"
    return ""


def _estimate_cost(frame_count: int, provider: str) -> float:
    """按帧数保守估算成本；sample 与本地图片网关归 0（无云单价依据，见 MEMORY #29）。"""
    cfg = get_config().image_gen
    if provider == "sample" or _image_channel_usable() or _is_local_endpoint(cfg.base_url):
        return 0.0
    return round(frame_count * _EST_COST_PER_FRAME_USD, 6)


def _fetch_frame(url: Any, *, timeout_ms: int) -> bytes:
    """取回帧字节：远端 url 下载；``assets/`` 引用直读（限 ASSETS_DIR 内）；其余不认。"""
    text = str(url or "").strip()
    if text.startswith("assets/"):
        path = (ASSETS_DIR / text[len("assets/"):]).resolve()
        return path.read_bytes() if path.parent == ASSETS_DIR.resolve() and path.is_file() else b""
    if text.startswith(("http://", "https://")):
        resp = httpx.get(text, timeout=max(1.0, timeout_ms / 1000.0), follow_redirects=True)
        return resp.content if resp.status_code < 400 else b""
    return b""


def _render_real(job: dict[str, Any]) -> None:
    """逐帧走图片生成协议（openai/local）；任一帧拿不到图即整体失败——缺帧的宠物包不算交付。"""
    image_cfg = get_config().image_gen
    pet_cfg = get_config().pet_gen
    manifest = job.get("manifest") or {}
    segments = manifest.get("segments") or []
    reason = _image_channel_usable()
    if reason:
        _transition(job, "failed", f"provider=imagegen {reason}")
        return
    if not segments:
        _transition(job, "failed", "没有可生成的动作帧（清单为空）")
        return

    directory = _pet_dir(str(job["id"]))
    frames_dir = directory / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    frames: list[dict[str, Any]] = []
    try:
        timeout = max(1.0, pet_cfg.timeout_ms / 1000.0)
        for position, segment in enumerate(segments):
            prompt = str(segment.get("prompt") or "")
            blob = b""
            if image_cfg.provider == "local":
                width, _, height = image_cfg.size.partition("x")
                resp = httpx.post(
                    image_cfg.base_url.rstrip("/") + "/sdapi/v1/txt2img",
                    json={
                        "prompt": prompt,
                        "negative_prompt": "",
                        "width": _clamp(width, 64, 2048, 512),
                        "height": _clamp(height, 64, 2048, 512),
                    },
                    headers=_image_headers(),
                    timeout=timeout,
                )
                if resp.status_code < 400:
                    images = (resp.json() or {}).get("images") or []
                    if images:
                        blob = base64.b64decode(str(images[0]).split(",", 1)[-1])
            else:
                resp = httpx.post(
                    image_cfg.base_url.rstrip("/") + "/images/generations",
                    json={
                        "prompt": prompt,
                        "size": image_cfg.size,
                        "n": 1,
                        **({"model": image_cfg.model} if image_cfg.model else {}),
                    },
                    headers=_image_headers(),
                    timeout=timeout,
                )
                if resp.status_code < 400:
                    data = (resp.json() or {}).get("data") or [{}]
                    item = data[0] if isinstance(data, list) and data else {}
                    if item.get("b64_json"):
                        blob = base64.b64decode(str(item["b64_json"]).split(",", 1)[-1])
                    else:
                        blob = _fetch_frame(item.get("url"), timeout_ms=image_cfg.timeout_ms)
            if not blob:
                status = getattr(resp, "status_code", "?")
                _transition(job, "failed", f"第 {position + 1} 帧未取得图像（HTTP {status}），已停止组装")
                return
            name = f"{segment.get('action')}-{segment.get('index')}.png"
            (frames_dir / name).write_bytes(blob)
            frames.append(
                {
                    "action": str(segment.get("action") or "idle"),
                    "index": int(_num(segment.get("index"), 1)),
                    "ref": f"pets/{job['id']}/frames/{name}",
                    "bytes": len(blob),
                    "simulated": False,
                }
            )
        _write_package(job, frames)
    except Exception as error:  # noqa: BLE001 - 网络/解析/落盘异常转成作业失败，不向上抛
        _transition(job, "failed", f"出帧失败：{type(error).__name__}: {error}")
        return
    _transition(job, "done", f"已生成 {len(frames)} 帧真实图片")
    job["progress"] = 100
    job["finished_at"] = now_iso()
    job["frames"] = frames
    job["pet_ref"] = f"pets/{job['id']}/pet.json"


# ------------------------------------------------------------------ #
# 宠物包 zip 与帧读取（供路由层）                                       #
# ------------------------------------------------------------------ #


def _pet_dir_of(job: dict[str, Any]) -> Path | None:
    """把作业里的目录引用解析成实际路径，并守住「只能在 PETS_DIR 下」的不变量。"""
    ref = str(job.get("dir") or f"pets/{job.get('id')}")
    if not ref.startswith("pets/"):
        return None
    path = (DATA_DIR / ref).resolve()
    if path.parent != PETS_DIR.resolve() or not path.is_dir():
        return None
    return path


def frame_bytes(job: dict[str, Any], action: str, index: int) -> bytes | None:
    """读取某动作某帧的字节（供浏览器预览）。只认已知动作与正整数帧号，不放开任意路径。"""
    directory = _pet_dir_of(job)
    name = str(action or "")
    if directory is None or name not in ACTION_NAMES or int(_num(index, 0)) < 1:
        return None
    frames_dir = directory / "frames"
    candidate = (frames_dir / f"{name}-{int(_num(index))}.png").resolve()
    if candidate.parent != frames_dir.resolve() or not candidate.is_file():
        return None
    return candidate.read_bytes()


def build_package(job: dict[str, Any]) -> Path:
    """把作业目录装配成 zip（pet.json + frames + README + 运行器），返回落盘路径。"""
    directory = _pet_dir_of(job)
    if directory is None:
        raise ValueError("宠物包目录不存在，无法装配")
    archive = directory / "package.zip"
    runner = _runner_source()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(directory.rglob("*")):
            if not path.is_file() or path.name == archive.name:
                continue
            bundle.write(path, path.relative_to(directory).as_posix())
        if runner is not None:
            bundle.writestr("runner/pet.py", runner)
    return archive


# ------------------------------------------------------------------ #
# 状态推进与公共 API                                                   #
# ------------------------------------------------------------------ #


def _parse_iso(raw: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _transition(job: dict[str, Any], status: str, note: str) -> None:
    if job.get("status") == status:
        job["error"] = note if status == "failed" else job.get("error", "")
        return
    previous = str(job.get("status") or "queued")
    job["status"] = status
    job["error"] = note if status == "failed" else ""
    job["history"] = list(job.get("history") or []) + [
        {"ts": now_iso(), "from": previous, "to": status, "note": note[:200]}
    ]
    job["updated_at"] = now_iso()
    event_bus.publish(
        task_id=str(job.get("task_id") or ""),
        type="log",
        message=f"桌面宠物：{note}",
        level="warn" if status == "failed" else "info",
        payload={"job_id": job.get("id"), "status": status, "provider": job.get("provider")},
    )


def _latest_visual_brief(task: TaskRecord) -> dict[str, Any] | None:
    artifact = next((a for a in reversed(task.artifacts) if a.type == "visual_brief"), None)
    return artifact.content if artifact is not None else None


def create_job(
    task: TaskRecord,
    *,
    provider: str = "",
    name: str = "",
    actions: Any = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """为任务创建一个桌面宠物作业（``POST /api/tasks/{id}/pets``）。

    任务必须已产出 ``visual_brief``（风格与色板的唯一来源）；否则抛 ``ValueError``
    由路由层转 409。
    """
    visual = _latest_visual_brief(task)
    if visual is None:
        raise ValueError("该任务没有视觉指导产物（visual_brief），桌宠没有可用的风格与色板")

    cfg = get_config().pet_gen
    # 以请求体实际生效的通道为准，而非全局配置（MEMORY #38 的同类坑）
    chosen = provider if provider in ("sample", "imagegen") else cfg.provider
    moment = now or datetime.now(timezone.utc)
    manifest = build_manifest(
        visual,
        provider=chosen,
        brand=task.brief.brand,
        name=str(name or "").strip(),
        actions=actions,
        now=moment,
    )
    estimated_cost = _estimate_cost(int(manifest.get("frame_count") or 0), chosen)
    job_id = new_id("pet")
    job: dict[str, Any] = {
        "id": job_id,
        "task_id": task.id,
        "tenant": task.tenant,
        "provider": chosen,
        "status": "queued",
        "progress": 0,
        "name": manifest.get("name"),
        "size": manifest.get("size"),
        "visual_artifact_id": next(
            (a.id for a in reversed(task.artifacts) if a.type == "visual_brief"), ""
        ),
        "actions": manifest.get("actions"),
        "frame_count": manifest.get("frame_count"),
        "render_seconds": _sample_seconds(manifest),
        "estimated_cost_usd": estimated_cost,
        "dir": f"pets/{job_id}",
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "started_at": now_iso(),
        "finished_at": "",
        "frames": [],
        "pet_ref": "",
        "error": "",
        "attempts": 0,
        "submitted": False,
        "manifest": manifest,
        "history": [{"ts": now_iso(), "from": "", "to": "queued", "note": "桌面宠物作业已受理"}],
    }

    # 成本熔断：真实通道逐帧计费，受理前先按帧数估算，超预算直接 fail-loud 不提交
    budget = get_config().cost_budget_usd
    if chosen == "imagegen" and budget > 0 and estimated_cost >= budget:
        _transition(
            job,
            "failed",
            f"成本熔断：预估 ${estimated_cost:.3f}（{manifest.get('frame_count')} 帧）已达预算 "
            f"${budget:.2f}，未提交出帧（可只选部分动作，或调高 COST_BUDGET_USD）",
        )
        _job_backend().create(job)
        return dict(job)

    _job_backend().create(job)

    with tracer.span_on_task(
        task.id,
        "petgen.render",
        kind="client",
        agent_id="A8",
        attributes={
            "petgen.job_id": job_id,
            "petgen.provider": chosen,
            "petgen.frames": manifest.get("frame_count"),
            "petgen.actions": ",".join(str(item) for item in manifest.get("actions") or []),
        },
    ):
        event_bus.publish(
            task_id=task.id,
            type="log",
            message=f"桌面宠物作业已创建（{chosen}，{manifest.get('frame_count')} 帧）",
            agent_id="A8",
            payload={"job_id": job_id},
        )
        if chosen == "imagegen":
            job["attempts"] = int(job.get("attempts") or 0) + 1
            job["submitted"] = True
            _render_real(job)
            _job_backend().write_back([job])
    log.info(f"桌面宠物作业已创建：{job_id}（task={task.id}, provider={chosen}）")
    return dict(job)


def _refresh(job: dict[str, Any], now: datetime) -> None:
    if job.get("status") in ("done", "failed"):
        return
    # imagegen 在受理时已同步终结；只有 sample 需要按流逝时间推进
    if job.get("provider") == "sample":
        _advance_sample(job, now)


def list_jobs(
    task_id: str | None = None, *, tenant: str | None = None, now: datetime | None = None
) -> list[dict[str, Any]]:
    """列出作业（读取时惰性推进 sample 状态并落盘帧），新→旧排序。"""
    backend = _job_backend()
    moment = now or datetime.now(timezone.utc)
    snapshot = [dict(job) for job in backend.snapshot()]
    changed: list[dict[str, Any]] = []
    for job in snapshot:
        before = str(job.get("status"))
        _refresh(job, moment)
        if str(job.get("status")) != before:
            changed.append(job)
    if changed:
        backend.write_back(changed)
    rows = [
        job
        for job in snapshot
        if (task_id is None or str(job.get("task_id")) == task_id)
        and (tenant is None or str(job.get("tenant")) == tenant)
    ]
    rows.sort(key=lambda job: job.get("created_at") or "", reverse=True)
    return rows


def get_job(job_id: str, *, tenant: str | None = None) -> dict[str, Any] | None:
    return next((row for row in list_jobs(tenant=tenant) if row.get("id") == job_id), None)


def drop_task(task_id: str) -> int:
    """任务删除时回收其桌宠作业，并删掉帧目录——持久文件只增不减同样是缺陷。"""
    doomed = [job for job in list_jobs() if str(job.get("task_id")) == task_id]
    purged = _job_backend().drop_task(task_id)
    for job in doomed:
        directory = _pet_dir_of(job)
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)
    return purged


__all__ = [
    "ACTION_NAMES",
    "ACTIONS",
    "KEY_HEX",
    "PETS_DIR",
    "STATUSES",
    "STORE_FILE",
    "build_manifest",
    "build_package",
    "create_job",
    "drop_task",
    "frame_bytes",
    "get_job",
    "list_jobs",
]
