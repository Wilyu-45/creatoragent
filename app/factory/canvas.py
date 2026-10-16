"""影视制作工厂 —— 纯标准库光栅画布（不引 Pillow）。

离线通道的图像产物在这里生成：分镜草图、概念帧、角色设定卡、布光图、
机位卡、封面、波形图。画法走「抽象构图」路线——按景别决定人物占比、
按色调表决定配色、按运镜决定动势线，让画面**如实表达该岗位写下的参数**，
而不是伪装成模型出图；真接了图片模型时由 ``media.py`` 覆盖这些帧。

文字用 5x7 点阵（ASCII），场记板/镜号/焦段这类片场标注天然是拉丁字符；
中文文案由前端播放器与检查器负责，避免在本机字体上依赖任何东西。
"""

from __future__ import annotations

import math
import struct
import zlib
from typing import Any, Iterable, Sequence

RGB = tuple[int, int, int]

#: 5x7 点阵（每列一个字节，bit0 = 第 1 行）
FONT_5X7: dict[str, tuple[int, ...]] = {
    " ": (0, 0, 0, 0, 0),
    "!": (0, 0, 95, 0, 0),
    '"': (0, 7, 0, 7, 0),
    "#": (20, 127, 20, 127, 20),
    "%": (35, 19, 8, 100, 98),
    "&": (54, 73, 85, 34, 80),
    "'": (0, 5, 3, 0, 0),
    "(": (0, 28, 34, 65, 0),
    ")": (0, 65, 34, 28, 0),
    "*": (20, 8, 62, 8, 20),
    "+": (8, 8, 62, 8, 8),
    ",": (0, 80, 48, 0, 0),
    "-": (8, 8, 8, 8, 8),
    ".": (0, 96, 96, 0, 0),
    "/": (32, 16, 8, 4, 2),
    "0": (62, 81, 73, 69, 62),
    "1": (0, 66, 127, 64, 0),
    "2": (66, 97, 81, 73, 70),
    "3": (33, 65, 69, 75, 51),
    "4": (24, 20, 18, 127, 16),
    "5": (39, 69, 69, 69, 57),
    "6": (60, 74, 73, 73, 48),
    "7": (1, 113, 9, 5, 3),
    "8": (54, 73, 73, 73, 54),
    "9": (6, 73, 73, 41, 30),
    ":": (0, 54, 54, 0, 0),
    ";": (0, 86, 54, 0, 0),
    "<": (8, 20, 34, 65, 0),
    "=": (20, 20, 20, 20, 20),
    ">": (0, 65, 34, 20, 8),
    "?": (2, 1, 81, 9, 6),
    "@": (50, 73, 121, 65, 62),
    "A": (126, 17, 17, 17, 126),
    "B": (127, 73, 73, 73, 54),
    "C": (62, 65, 65, 65, 34),
    "D": (127, 65, 65, 34, 28),
    "E": (127, 73, 73, 73, 65),
    "F": (127, 9, 9, 1, 1),
    "G": (62, 65, 65, 81, 113),
    "H": (127, 8, 8, 8, 127),
    "I": (0, 65, 127, 65, 0),
    "J": (32, 64, 65, 63, 1),
    "K": (127, 8, 20, 34, 65),
    "L": (127, 64, 64, 64, 64),
    "M": (127, 2, 4, 2, 127),
    "N": (127, 4, 8, 16, 127),
    "O": (62, 65, 65, 65, 62),
    "P": (127, 9, 9, 9, 6),
    "Q": (62, 65, 81, 33, 94),
    "R": (127, 9, 25, 41, 70),
    "S": (70, 73, 73, 73, 49),
    "T": (1, 1, 127, 1, 1),
    "U": (63, 64, 64, 64, 63),
    "V": (31, 32, 64, 32, 31),
    "W": (127, 32, 24, 32, 127),
    "X": (99, 20, 8, 20, 99),
    "Y": (3, 4, 120, 4, 3),
    "Z": (97, 81, 73, 69, 67),
    "[": (0, 127, 65, 65, 0),
    "\\": (2, 4, 8, 16, 32),
    "]": (0, 65, 65, 127, 0),
    "^": (4, 2, 1, 2, 4),
    "_": (64, 64, 64, 64, 64),
    "`": (0, 1, 2, 4, 0),
    "a": (32, 84, 84, 84, 120),
    "b": (127, 72, 68, 68, 56),
    "c": (56, 68, 68, 68, 32),
    "d": (56, 68, 68, 72, 127),
    "e": (56, 84, 84, 84, 24),
    "f": (8, 126, 9, 1, 2),
    "g": (12, 82, 82, 82, 62),
    "h": (127, 8, 4, 4, 120),
    "i": (0, 68, 125, 64, 0),
    "j": (32, 64, 68, 61, 0),
    "k": (0, 127, 16, 40, 68),
    "l": (0, 65, 127, 64, 0),
    "m": (124, 4, 24, 4, 120),
    "n": (124, 8, 4, 4, 120),
    "o": (28, 36, 36, 36, 28),
    "p": (124, 20, 20, 20, 8),
    "q": (8, 20, 20, 24, 124),
    "r": (124, 8, 4, 4, 8),
    "s": (72, 84, 84, 84, 32),
    "t": (4, 63, 68, 64, 32),
    "u": (60, 64, 64, 32, 124),
    "v": (28, 32, 64, 32, 28),
    "w": (60, 64, 48, 64, 60),
    "x": (68, 40, 16, 40, 68),
    "y": (12, 80, 80, 80, 60),
    "z": (68, 100, 84, 76, 68),
    "{": (0, 8, 54, 65, 0),
    "|": (0, 0, 119, 0, 0),
    "}": (0, 65, 54, 8, 0),
    "~": (2, 1, 2, 4, 2),
}

_LATIN = {
    "°": "*",
    "—": "-",
    "–": "-",
    "·": ".",
    "×": "x",
    "→": ">",
    "←": "<",
}


def ascii_safe(text: str) -> str:
    """把中文等非拉丁字符剔除，保留能进点阵字体的部分。"""
    out: list[str] = []
    for ch in text:
        if ch in FONT_5X7:
            out.append(ch)
        elif ch in _LATIN:
            out.append(_LATIN[ch])
        elif ord(ch) < 128:
            out.append(" ")
    return "".join(out)


def _clamp_byte(value: float) -> int:
    return 0 if value <= 0 else 255 if value >= 255 else int(value)


def hex_rgb(value: str, fallback: RGB = (120, 130, 150)) -> RGB:
    """``#rrggbb`` / ``rrggbb`` → RGB 元组。"""
    raw = (value or "").strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) != 6:
        return fallback
    try:
        return (int(raw[0:2], 16), int(raw[2:4], 16), int(raw[4:6], 16))
    except ValueError:
        return fallback


ASPECTS: dict[str, tuple[int, int]] = {
    "9:16": (9, 16),
    "2:3": (2, 3),
    "3:4": (3, 4),
    "1:1": (1, 1),
    "4:3": (4, 3),
    "16:9": (16, 9),
    "4:5": (4, 5),
}


def size_for(aspect: str, width: int) -> tuple[int, int]:
    num, den = ASPECTS.get(aspect or "9:16", (9, 16))
    height = int(round(width * den / num))
    return width, height - (height % 2)


class Raster:
    """最小 RGB 画布：够画分镜草图与设定卡，又不引入图像库。"""

    def __init__(self, width: int, height: int, bg: RGB = (10, 12, 18)) -> None:
        self.w = max(1, int(width))
        self.h = max(1, int(height))
        self.buf = bytearray(bytes(bg) * (self.w * self.h))

    # --------------------------- 基础像素 --------------------------- #

    def px(self, x: int, y: int, color: RGB, alpha: float = 1.0) -> None:
        if not (0 <= x < self.w and 0 <= y < self.h):
            return
        index = (y * self.w + x) * 3
        if alpha >= 1.0:
            self.buf[index] = _clamp_byte(color[0])
            self.buf[index + 1] = _clamp_byte(color[1])
            self.buf[index + 2] = _clamp_byte(color[2])
            return
        a = max(0.0, min(1.0, alpha))
        for shift in range(3):
            old = self.buf[index + shift]
            self.buf[index + shift] = _clamp_byte(old * (1 - a) + color[shift] * a)

    def row(self, y: int, x0: int, x1: int, color: RGB) -> None:
        x0, x1 = max(0, x0), min(self.w, x1)
        if x1 <= x0 or not (0 <= y < self.h):
            return
        self.buf[(y * self.w + x0) * 3:(y * self.w + x1) * 3] = bytes(color) * (x1 - x0)

    # --------------------------- 形状 --------------------------- #

    def fill_rect(self, x: int, y: int, w: int, h: int, color: RGB, alpha: float = 1.0) -> None:
        for row_y in range(int(y), int(y) + int(h)):
            if alpha >= 1.0:
                self.row(row_y, x, x + w, color)
            else:
                for col in range(int(x), int(x) + int(w)):
                    self.px(col, row_y, color, alpha)

    def stroke_rect(self, x: int, y: int, w: int, h: int, color: RGB, width: int = 2) -> None:
        for t in range(width):
            self.row(y + t, x, x + w, color)
            self.row(y + h - 1 - t, x, x + w, color)
            for row_y in range(y + t, y + h - t):
                self.px(x + t, row_y, color)
                self.px(x + w - 1 - t, row_y, color)

    def dashed_rect(self, x: int, y: int, w: int, h: int, color: RGB, dash: int = 8) -> None:
        for offset in range(0, w, dash * 2):
            self.row(y, x + offset, min(x + w, x + offset + dash), color)
            self.row(y + h - 1, x + offset, min(x + w, x + offset + dash), color)
        for offset in range(0, h, dash * 2):
            for row_y in range(y + offset, min(y + h, y + offset + dash)):
                self.px(x, row_y, color)
                self.px(x + w - 1, row_y, color)

    def line(
        self,
        x0: float,
        y0: float,
        x1: float,
        y1: float,
        color: RGB,
        width: int = 1,
        dash: int = 0,
    ) -> None:
        steps = max(1, int(max(abs(x1 - x0), abs(y1 - y0)) * 2) + 1)
        for step in range(steps + 1):
            if dash and (step // max(1, dash)) % 2:
                continue
            t = step / steps
            x = int(round(x0 + (x1 - x0) * t))
            y = int(round(y0 + (y1 - y0) * t))
            for dy in range(width):
                for dx in range(width):
                    self.px(x + dx, y + dy, color)

    def arrow(self, x0: float, y0: float, x1: float, y1: float, color: RGB, width: int = 2) -> None:
        self.line(x0, y0, x1, y1, color, width)
        angle = math.atan2(y1 - y0, x1 - x0)
        length = max(6.0, width * 4)
        for spread in (-0.42, 0.42):
            self.line(
                x1,
                y1,
                x1 - length * math.cos(angle + spread),
                y1 - length * math.sin(angle + spread),
                color,
                width,
            )

    def ellipse(self, cx: int, cy: int, rx: int, ry: int, color: RGB, filled: bool = True, alpha: float = 1.0) -> None:
        rx, ry = max(1, rx), max(1, ry)
        for y in range(cy - ry, cy + ry + 1):
            ny = (y - cy) / ry
            if abs(ny) > 1:
                continue
            half = int(rx * math.sqrt(max(0.0, 1 - ny * ny)))
            if filled:
                if alpha >= 1.0:
                    self.row(y, cx - half, cx + half + 1, color)
                else:
                    for x in range(cx - half, cx + half + 1):
                        self.px(x, y, color, alpha)
            else:
                self.px(cx - half, y, color, alpha)
                self.px(cx + half, y, color, alpha)

    def polygon(self, points: Sequence[tuple[float, float]], color: RGB, filled: bool = True, width: int = 2) -> None:
        if len(points) < 2:
            return
        if filled:
            min_y = int(min(p[1] for p in points))
            max_y = int(max(p[1] for p in points))
            for y in range(min_y, max_y + 1):
                xs: list[float] = []
                for i in range(len(points)):
                    ax, ay = points[i]
                    bx, by = points[(i + 1) % len(points)]
                    if ay == by:
                        continue
                    if min(ay, by) <= y < max(ay, by):
                        xs.append(ax + (y - ay) * (bx - ax) / (by - ay))
                if len(xs) >= 2:
                    xs.sort()
                    self.row(y, int(xs[0]), int(xs[-1]) + 1, color)
        else:
            for i in range(len(points)):
                ax, ay = points[i]
                bx, by = points[(i + 1) % len(points)]
                self.line(ax, ay, bx, by, color, width)

    def human(self, cx: int, base_y: int, height: int, color: RGB) -> None:
        """一个抽象人形：头 + 躯干 + 腿，景别决定它占画面多高。"""
        height = max(10, int(height))
        head = max(3, int(height * 0.13))
        self.ellipse(cx, base_y - height + head, head, head, color)
        torso_h = int(height * 0.42)
        torso_w = max(3, int(height * 0.18))
        self.fill_rect(cx - torso_w // 2, base_y - height + head * 2, torso_w, torso_h, color)
        leg_top = base_y - height + head * 2 + torso_h
        self.line(cx - torso_w // 4, leg_top, cx - torso_w // 2, base_y, color, 2)
        self.line(cx + torso_w // 4, leg_top, cx + torso_w // 2, base_y, color, 2)

    # --------------------------- 文字 --------------------------- #

    def glyph(self, x: int, y: int, char: str, color: RGB, scale: int = 2) -> int:
        pattern = FONT_5X7.get(char)
        if pattern is None:
            return 6 * scale
        for column, bits in enumerate(pattern):
            for row_y in range(7):
                if bits & (1 << row_y):
                    self.fill_rect(x + column * scale, y + row_y * scale, scale, scale, color)
        return 6 * scale

    def text(self, x: int, y: int, value: str, color: RGB = (235, 238, 245), scale: int = 2) -> int:
        cursor = x
        for char in ascii_safe(value or ""):
            cursor += self.glyph(cursor, y, char, color, scale)
        return cursor - x

    def blit(self, src: "Raster", x: int, y: int) -> None:
        for row_y in range(src.h):
            target = y + row_y
            if not (0 <= target < self.h):
                continue
            x0, x1 = max(0, x), min(self.w, x + src.w)
            if x1 <= x0:
                continue
            source_start = row_y * src.w * 3 + (x0 - x) * 3
            self.buf[(target * self.w + x0) * 3:(target * self.w + x1) * 3] = src.buf[source_start:source_start + (x1 - x0) * 3]

    # --------------------------- 效果 --------------------------- #

    def frame_zoom(self, zoom: float, cx: float = 0.5, cy: float = 0.5) -> "Raster":
        """取一帧的局部窗口再放回原尺寸——Ken Burns 运镜就是逐帧改这三个数。"""
        factor = max(1.0, min(6.0, float(zoom)))
        win_w = max(1, int(self.w / factor))
        win_h = max(1, int(self.h / factor))
        left = int(max(0.0, min(self.w - win_w, self.w * float(cx) - win_w / 2)))
        top = int(max(0.0, min(self.h - win_h, self.h * float(cy) - win_h / 2)))
        out = Raster(self.w, self.h)
        step_x = win_w / self.w
        step_y = win_h / self.h
        for y in range(self.h):
            source_row = ((top + int(y * step_y)) * self.w + left) * 3
            row = self.buf[source_row:source_row + win_w * 3]
            start = y * self.w * 3
            for x in range(self.w):
                base = int(x * step_x) * 3
                out.buf[start + x * 3:start + x * 3 + 3] = row[base:base + 3]
        return out

    def fit(self, width: int, height: int) -> "Raster":
        """双线性重采样到任意尺寸（会拉变形）：封面把剧照铺满版式时用。"""
        out_w, out_h = max(1, int(width)), max(1, int(height))
        if (out_w, out_h) == (self.w, self.h):
            clone = Raster(out_w, out_h)
            clone.buf = bytearray(self.buf)
            return clone
        out = Raster(out_w, out_h)
        for y in range(out_h):
            sy = (y + 0.5) * self.h / out_h - 0.5
            y0 = max(0, min(self.h - 1, int(math.floor(sy))))
            y1 = max(0, min(self.h - 1, y0 + 1))
            fy = min(1.0, max(0.0, sy - y0))
            line = bytearray(out_w * 3)
            row_a, row_b = y0 * self.w * 3, y1 * self.w * 3
            for x in range(out_w):
                sx = (x + 0.5) * self.w / out_w - 0.5
                x0 = max(0, min(self.w - 1, int(math.floor(sx))))
                x1 = max(0, min(self.w - 1, x0 + 1))
                fx = min(1.0, max(0.0, sx - x0))
                for shift in range(3):
                    top_value = row_a + x0 * 3 + shift
                    low_value = row_b + x0 * 3 + shift
                    top_right = row_a + x1 * 3 + shift
                    low_right = row_b + x1 * 3 + shift
                    a = self.buf[top_value] * (1 - fx) + self.buf[top_right] * fx
                    b = self.buf[low_value] * (1 - fx) + self.buf[low_right] * fx
                    line[x * 3 + shift] = _clamp_byte(a * (1 - fy) + b * fy)
            out.buf[y * out_w * 3:(y + 1) * out_w * 3] = bytes(line)
        return out

    def vgradient(self, top: RGB, bottom: RGB) -> None:
        for y in range(self.h):
            t = y / max(1, self.h - 1)
            color = (
                _clamp_byte(top[0] + (bottom[0] - top[0]) * t),
                _clamp_byte(top[1] + (bottom[1] - top[1]) * t),
                _clamp_byte(top[2] + (bottom[2] - top[2]) * t),
            )
            self.row(y, 0, self.w, color)

    def glow(self, cx: float, cy: float, radius: float, color: RGB, strength: float = 0.55) -> None:
        radius = max(1.0, radius)
        for y in range(max(0, int(cy - radius)), min(self.h, int(cy + radius) + 1)):
            dy = (y - cy) / radius
            span = radius * math.sqrt(max(0.0, 1 - dy * dy))
            for x in range(max(0, int(cx - span)), min(self.w, int(cx + span))):
                dx = (x - cx) / radius
                distance = math.sqrt(dx * dx + dy * dy)
                if distance > 1:
                    continue
                alpha = (1 - distance) ** 2 * strength
                self.px(x, y, color, alpha)

    def grain(self, amount: float, seed: int = 7) -> None:
        if amount <= 0:
            return
        state = seed & 0xFFFFFFFF
        limit = min(self.w * self.h, 260_000)
        for _ in range(int(limit * amount * 0.6)):
            state = (1103515245 * state + 12345) & 0xFFFFFFFF
            x = (state >> 5) % self.w
            state = (1103515245 * state + 12345) & 0xFFFFFFFF
            y = (state >> 5) % self.h
            index = (y * self.w + x) * 3
            delta = ((state >> 16) & 63) * amount - 32 * amount
            for shift in range(3):
                self.buf[index + shift] = _clamp_byte(self.buf[index + shift] + delta)

    def vignette(self, strength: float) -> None:
        if strength <= 0:
            return
        cx, cy = self.w / 2, self.h / 2
        max_r = math.hypot(cx, cy)
        for y in range(0, self.h, 2):
            for x in range(0, self.w, 2):
                distance = math.hypot(x - cx, y - cy) / max_r
                falloff = max(0.0, distance - 0.45) / 0.55
                shade = 1.0 - falloff * strength
                index = (y * self.w + x) * 3
                block = self.buf[index:index + 3]
                tinted = bytes(_clamp_byte(value * shade) for value in block)
                self.buf[index:index + 3] = tinted
                if x + 1 < self.w:
                    self.buf[index + 3:index + 6] = tinted
                if y + 1 < self.h:
                    next_index = ((y + 1) * self.w + x) * 3
                    self.buf[next_index:next_index + 3] = tinted
                    if x + 1 < self.w:
                        self.buf[next_index + 3:next_index + 6] = tinted

    def grade(self, contrast: float = 1.0, saturation: float = 1.0, temp_k: float = 5600.0, exposure: float = 0.0, lift: float = 0.0) -> None:
        """调色师的五个手柄：曝光（档）、对比、饱和、色温、黑场提升。

        曝光与黑场放在对比之前/之后各占一级，跟真实一级校色的信号链一致：
        先定亮度（exposure），再定反差（contrast），最后托起黑场（lift），
        否则同一帧上「提亮」和「加对比」互相抵消，肉眼看不出差别。
        """
        warm_shift = max(-40.0, min(40.0, (5600.0 - temp_k) / 60.0))
        gain = 2.0 ** max(-3.0, min(3.0, float(exposure)))
        lift = max(0.0, min(0.35, float(lift)))
        buf = self.buf
        for index in range(0, len(buf), 3):
            r, g, b = buf[index] * gain, buf[index + 1] * gain, buf[index + 2] * gain
            luma = 0.299 * r + 0.587 * g + 0.114 * b
            nr = luma + (r - luma) * saturation
            ng = luma + (g - luma) * saturation
            nb = luma + (b - luma) * saturation
            nr = (nr - 128) * contrast + 128 + warm_shift
            ng = (ng - 128) * contrast + 128
            nb = (nb - 128) * contrast + 128 - warm_shift
            buf[index] = _clamp_byte(nr * (1.0 - lift) + lift * 255)
            buf[index + 1] = _clamp_byte(ng * (1.0 - lift) + lift * 255)
            buf[index + 2] = _clamp_byte(nb * (1.0 - lift) + lift * 255)

    # --------------------------- 输出 --------------------------- #

    def to_png(self) -> bytes:
        def chunk(kind: bytes, data: bytes) -> bytes:
            return (
                struct.pack(">I", len(data))
                + kind
                + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
            )

        raw = b"".join(b"\x00" + bytes(self.buf[row * self.w * 3:(row + 1) * self.w * 3]) for row in range(self.h))
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6))
            + chunk(b"IEND", b"")
        )


# ------------------------------------------------------------------ #
# 构图：把岗位参数画成看得见的画面                                       #
# ------------------------------------------------------------------ #


def _palette_of(look: dict[str, Any] | None) -> tuple[RGB, RGB, RGB, RGB]:
    look = look or {}
    colors = [hex_rgb(item, (90, 100, 120)) for item in (look.get("palette") or ["#1a2338", "#2b3f66"])[:3]]
    while len(colors) < 3:
        colors.append((70, 84, 110))
    accent = hex_rgb(look.get("accent", "#8fb4ff"), (150, 175, 255))
    return colors[0], colors[1], colors[2], accent


def scene_raster(
    *,
    aspect: str = "9:16",
    width: int = 420,
    look: dict[str, Any] | None = None,
    figure: float = 0.42,
    horizon: float = 0.66,
    light_side: float = 0.28,
    motion: str = "",
    slate: Iterable[str] = (),
    extra: dict[str, Any] | None = None,
) -> Raster:
    """一帧抽象构图：天空/地面渐变 + 光源 + 地平线 + 人物占比 + 运镜指示 + 场记板。"""
    canvas_w, canvas_h = size_for(aspect, width)
    sky, mid, ground, accent = _palette_of(look)
    look = look or {}
    image = Raster(canvas_w, canvas_h, mid)
    image.vgradient(sky, ground)
    image.glow(
        canvas_w * light_side,
        canvas_h * 0.22,
        canvas_w * 0.85,
        (255, 240 + int(look.get("glow_bias", 0)), 210),
        0.45,
    )
    line_y = int(canvas_h * horizon)
    image.row(line_y, 0, canvas_w, (ground[0] // 2, ground[1] // 2, ground[2] // 2))
    image.fill_rect(0, line_y, canvas_w, canvas_h - line_y, (ground[0] // 3, ground[1] // 3, ground[2] // 3))

    #: 远景地标剪影（建筑/山形），让「场景」在离线帧里可辨
    rng_state = sum(ord(ch) for ch in str(extra.get("seed", ""))) if extra else 7
    for step in range(5):
        block_w = canvas_w // 7
        block_h = int((canvas_h - line_y) * (0.35 + ((rng_state + step * 13) % 5) / 10))
        x = (step * 2 + 1) * block_w // 2 % max(1, canvas_w - block_w)
        image.fill_rect(x, line_y - block_h, block_w, block_h, (sky[0] // 3, sky[1] // 3, sky[2] // 3))

    if figure > 0.02:
        body_h = int(canvas_h * figure)
        image.human(int(canvas_w * 0.5), min(canvas_h - 4, line_y + int(body_h * 0.06)), body_h, (8, 9, 12))

    if motion and motion != "static":
        center = (canvas_w / 2, canvas_h / 2)
        vectors = {
            "push_in": (-1, 0, 1, 0),
            "pull_out": (1, 0, -1, 0),
            "pan": (0, 0, 1, 0),
            "tilt": (0, 1, 0, -1),
            "track": (-1, 0, 1, 0),
            "follow": (0, 1, 0, -1),
            "crane": (0, 1, 0, -1),
            "handheld": (-1, -1, 1, 1),
            "whip": (-1, 0, 1, 0),
            "orbit": (1, 0, -1, 0),
        }
        x0, y0, x1, y1 = vectors.get(motion, (-1, 0, 1, 0))
        image.arrow(
            center[0] + x0 * canvas_w * 0.22,
            center[1] + y0 * canvas_h * 0.22,
            center[0] + x1 * canvas_w * 0.22,
            center[1] + y1 * canvas_h * 0.22,
            accent,
            2,
        )

    grade_spec = look.get("grade") or {}
    image.grade(
        contrast=float(grade_spec.get("contrast", 1.0)),
        saturation=float(grade_spec.get("sat", 1.0)),
        temp_k=float(grade_spec.get("temp", 5600)),
    )
    image.vignette(float(look.get("vignette", 0.35)))
    image.grain(float(look.get("grain", 0.06)))

    #: 场记板条：镜号 / 景别 / 焦段 / 时长
    label_y = 8
    for index, text in enumerate(slate):
        if index > 3:
            break
        image.fill_rect(8, label_y + index * 16, min(canvas_w - 16, 8 * len(ascii_safe(text)) + 12), 14, (6, 7, 10), 0.72)
        image.text(12, label_y + 4 + index * 16, text, (232, 236, 245), 1)
    return image


def scene_frame(**kwargs: Any) -> bytes:
    return scene_raster(**kwargs).to_png()


def storyboard_sheet(shots: list[dict[str, Any]], look: dict[str, Any], title: str, aspect: str = "9:16") -> bytes:
    """一页分镜表：缩略格 + 镜号 + 景别 + 焦段 + 时长（美术/导演通用的纸面产物）。"""
    per_row = 3
    rows = max(1, math.ceil(len(shots) / per_row))
    cell_w = 190
    cell_aspect = "16:9" if aspect != "9:16" else "9:16"
    cell_w, cell_h = size_for(cell_aspect, cell_w)
    pad = 12
    sheet = Raster(per_row * cell_w + pad * (per_row + 1), rows * (cell_h + 40) + 60 + pad, (14, 16, 22))
    sheet.text(16, 14, f"STORYBOARD  {title}", (226, 232, 244), 2)
    sheet.text(16, 34, f"{len(shots)} SHOTS  ASPECT {aspect}", (150, 165, 195), 1)
    for index, shot in enumerate(shots[: per_row * rows]):
        row, column = divmod(index, per_row)
        x = pad + column * (cell_w + pad)
        y = 56 + pad + row * (cell_h + 40)
        tile = scene_raster(
            aspect=cell_aspect,
            width=cell_w,
            look=look,
            figure=float(shot.get("figure", 0.4)),
            horizon=float(shot.get("horizon", 0.66)),
            motion=str(shot.get("movement", "")),
            extra={"seed": str(shot.get("id", index))},
        )
        sheet.blit(tile, x, y)
        sheet.stroke_rect(x, y, cell_w, cell_h, (52, 60, 78), 1)
        sheet.text(x + 4, y + cell_h + 4, f"{shot.get('shot_no', index + 1)} {shot.get('size_label', '')}", (214, 222, 238), 1)
        sheet.text(
            x + 4,
            y + cell_h + 18,
            f"{shot.get('lens_mm', 35)}MM  {shot.get('duration_s', 2)}S",
            (150, 165, 195),
            1,
        )
    return sheet.to_png()


def character_sheet(character: dict[str, Any], look: dict[str, Any]) -> bytes:
    """角色设定卡：剪影 + 配色条 + 服装/道具/音色标注。"""
    width, height = size_for("3:4", 420)
    card = Raster(width, height, (16, 18, 24))
    card.vgradient((30, 34, 46), (12, 13, 18))
    accent = hex_rgb(str(character.get("accent", "#8fb4ff")), (150, 175, 255))
    card.glow(width * 0.5, height * 0.34, width * 0.7, accent, 0.32)
    card.human(width // 2, int(height * 0.82), int(height * 0.52), (10, 11, 15))
    card.dashed_rect(width // 2 - 70, int(height * 0.14), 140, int(height * 0.68), (200, 210, 230), 6)
    card.text(18, 16, f"CHARACTER  {character.get('name', '')}", (236, 240, 248), 2)
    rows = [
        f"ROLE {character.get('role_type', '')}",
        f"AGE {character.get('age', '')}",
        f"WARDROBE {character.get('wardrobe', '')}",
        f"PROP {character.get('prop', '')}",
        f"VOICE {character.get('voice_label', character.get('voice', ''))}",
        f"ARC {character.get('arc', '')}",
    ]
    for index, row in enumerate(rows):
        card.text(18, 44 + index * 18, row, (186, 198, 220), 1)
    palette = [hex_rgb(item, (90, 100, 120)) for item in (character.get("palette") or [])[:6]]
    for index, color in enumerate(palette):
        card.fill_rect(18 + index * 30, height - 46, 24, 24, color)
        card.stroke_rect(18 + index * 30, height - 46, 24, 24, (210, 218, 235), 1)
    card.vignette(0.3)
    return card.to_png()


def lighting_diagram(plan: dict[str, Any], shot_label: str) -> bytes:
    """布光图（俯视）：主体 + 主/辅/轮廓灯位 + 光比与色温标注。"""
    width, height = size_for("4:3", 480)
    sheet = Raster(width, height, (12, 14, 20))
    sheet.vgradient((24, 28, 40), (9, 10, 14))
    cx, cy = width // 2, int(height * 0.56)
    temp = float(plan.get("temp_k", 5600))
    key_color = (255, 214, 150) if temp < 5000 else (180, 214, 255) if temp > 6000 else (245, 240, 230)
    sheet.ellipse(cx, cy, 26, 14, (220, 226, 240))
    sheet.text(cx + 34, cy - 8, "SUBJECT", (200, 210, 230), 1)
    lamps = [
        (-0.62, -0.42, "KEY", key_color),
        (0.52, -0.30, "FILL", (170, 190, 220)),
        (0.10, 0.55, "BACK", (250, 250, 255)),
    ]
    for dx, dy, label, color in lamps:
        lx, ly = cx + int(width * dx * 0.42), cy + int(height * dy * 0.42)
        sheet.ellipse(lx, ly, 12, 12, color)
        sheet.line(lx, ly, cx, cy, color, 1, dash=4)
        sheet.text(lx - 12, ly + 16, label, (216, 226, 244), 1)
    sheet.text(14, 12, f"LIGHTING  {shot_label}", (232, 238, 250), 2)
    sheet.text(14, 34, f"{int(temp)}K  RATIO {plan.get('ratio', '3:1')}  {plan.get('direction', '')}", (176, 190, 214), 1)
    sheet.text(14, 52, f"GELS {plan.get('gels', '')}", (176, 190, 214), 1)
    sheet.stroke_rect(6, 6, width - 12, height - 12, (54, 62, 80), 1)
    return sheet.to_png()


def camera_card(plan: dict[str, Any], look: dict[str, Any], shot_label: str) -> bytes:
    """机位卡：焦段视场扇形 + 画幅安全框。"""
    width, height = size_for("16:9", 520)
    card = Raster(width, height, (13, 15, 21))
    card.vgradient((22, 26, 38), (8, 9, 13))
    lens = float(plan.get("lens_mm", 35))
    fov = max(18.0, min(120.0, 2 * math.degrees(math.atan(21.6 / lens))))
    origin = (width * 0.12, height * 0.86)
    accent = hex_rgb((look or {}).get("accent", "#7dd3fc"), (125, 211, 252))
    for spread in (-fov / 2, fov / 2):
        angle = math.radians(-90 + spread)
        card.line(
            origin[0],
            origin[1],
            origin[0] + height * 1.1 * math.cos(angle),
            origin[1] + height * 1.1 * math.sin(angle),
            accent,
            2,
            dash=6,
        )
    card.ellipse(int(origin[0]), int(origin[1]), 10, 10, accent)
    card.text(int(origin[0]) + 16, int(origin[1]) - 6, f"{int(lens)}MM  FOV {int(fov)}", (224, 232, 246), 1)
    box_x, box_y = int(width * 0.42), int(height * 0.16)
    box_w, box_h = int(width * 0.46), int(height * 0.62)
    card.stroke_rect(box_x, box_y, box_w, box_h, (214, 222, 238), 2)
    card.dashed_rect(box_x + 12, box_y + 12, box_w - 24, box_h - 24, (110, 124, 152), 5)
    card.text(14, 12, f"CAMERA  {shot_label}", (234, 240, 250), 2)
    card.text(
        14,
        34,
        f"{plan.get('rig_label', '')}  {plan.get('angle_label', '')}  {plan.get('movement_label', '')}  {plan.get('fps', 24)}FPS  {plan.get('aspect', '9:16')}",
        (176, 190, 214),
        1,
    )
    return card.to_png()


def waveform_png(peaks: Sequence[float], color: RGB = (125, 211, 252), width: int = 720, height: int = 160) -> bytes:
    """波形图：剪辑师/混音师在时间轴上认声音。"""
    image = Raster(width, height, (12, 14, 20))
    image.fill_rect(0, height // 2 - 1, width, 2, (70, 80, 100))
    if not peaks:
        return image.to_png()
    step = max(1, len(peaks) // width)
    for x in range(width):
        value = max(abs(float(item)) for item in peaks[x * step:(x + 1) * step] or [0.0])
        bar = int(value * (height / 2 - 4))
        if bar > 0:
            image.fill_rect(x, height // 2 - bar, 1, bar * 2, color)
    return image.to_png()


def poster_frame(look: dict[str, Any], title_latin: str, aspect: str = "3:4", width: int = 640, note: str = "") -> bytes:
    """封面/海报底图：主色构图 + 标题留白区（中文标题由前端播放器叠加）。"""
    canvas_w, canvas_h = size_for(aspect, width)
    sky, mid, ground, accent = _palette_of(look)
    image = Raster(canvas_w, canvas_h, mid)
    image.vgradient(sky, ground)
    image.glow(canvas_w * 0.5, canvas_h * 0.36, canvas_w * 1.05, accent, 0.5)
    image.human(int(canvas_w * 0.5), int(canvas_h * 0.78), int(canvas_h * 0.42), (8, 9, 12))
    image.grade(
        contrast=float((look or {}).get("contrast", 1.12)),
        saturation=float((look or {}).get("sat", 1.16)),
        temp_k=float((look or {}).get("grade", {}).get("temp", 5400)),
    )
    image.fill_rect(0, int(canvas_h * 0.80), canvas_w, int(canvas_h * 0.2), (8, 9, 13), 0.62)
    image.stroke_rect(16, 16, canvas_w - 32, canvas_h - 32, (222, 230, 246), 2)
    image.text(28, 30, title_latin, (240, 244, 252), 3)
    if note:
        image.text(28, canvas_h - 52, note, (200, 212, 232), 1)
    image.vignette(0.42)
    image.grain(0.12)
    return image.to_png()


def avatar_frame(
    *,
    name: str = "",
    look: dict[str, Any] | None = None,
    mouth: float = 0.5,
    sway: float = 0.0,
    aspect: str = "9:16",
    width: int = 320,
    caption: str = "",
    slate: Iterable[str] = (),
) -> bytes:
    """数字人演员的一帧：胸像 + 随口型开合的嘴 + 身体摆动，供逐帧示意口播。"""
    look = look or {}
    canvas_w, canvas_h = size_for(aspect, width)
    sky, mid, ground, accent = _palette_of(look)
    image = Raster(canvas_w, canvas_h, mid)
    image.vgradient(sky, ground)
    image.glow(canvas_w * (0.24 + sway * 0.05), canvas_h * 0.24, canvas_w * 0.9, (255, 244, 214), 0.4)
    image.glow(canvas_w * 0.78, canvas_h * 0.6, canvas_w * 0.5, accent, 0.22)

    cx = int(canvas_w * (0.5 + sway * 0.02))
    head_r = int(canvas_h * 0.12)
    chin = int(canvas_h * 0.40)
    skin = hex_rgb(str(look.get("skin") or ""), (196, 166, 140))
    hair = hex_rgb(str(look.get("hair") or ""), (36, 30, 30))

    #: 肩线与上身
    image.polygon(
        [
            (cx - head_r * 2.4, canvas_h),
            (cx - head_r * 1.5, chin + head_r * 0.35),
            (cx + head_r * 1.5, chin + head_r * 0.35),
            (cx + head_r * 2.4, canvas_h),
        ],
        (int(ground[0] // 2), int(ground[1] // 2), int(ground[2] // 2)),
    )
    image.ellipse(cx, chin - head_r, head_r, int(head_r * 1.18), skin)
    image.ellipse(cx, int(chin - head_r * 1.72), int(head_r * 1.04), max(1, head_r * 62 // 100), hair)
    eye_y = int(chin - head_r * 1.06)
    for offset in (-1, 1):
        image.ellipse(cx + offset * int(head_r * 0.42), eye_y, max(2, head_r // 9), max(2, head_r // 12), (18, 20, 26))
    open_h = max(1, int(head_r * 0.42 * max(0.0, min(1.0, mouth))))
    image.ellipse(cx, int(chin - head_r * 0.28), max(2, head_r // 3), open_h, (92, 34, 40))
    image.line(cx - head_r * 0.5, chin + head_r * 0.1, cx + head_r * 0.5, chin + head_r * 0.1, (150, 120, 104), 1)

    grade_spec = look.get("grade") or {}
    image.grade(
        contrast=float(grade_spec.get("contrast", 1.05)),
        saturation=float(grade_spec.get("sat", 1.05)),
        temp_k=float(grade_spec.get("temp", 5400)),
    )
    image.vignette(float(look.get("vignette", 0.4)))
    image.grain(float(look.get("grain", 0.05)))

    bar_h = max(18, int(canvas_h * 0.055))
    image.fill_rect(0, canvas_h - bar_h, canvas_w, bar_h, (6, 7, 11), 0.72)
    image.text(10, canvas_h - bar_h + 5, f"{name} {caption}".strip(), (236, 240, 250), 1)
    label_y = 8
    for index, text in enumerate(slate):
        if index > 2:
            break
        image.fill_rect(8, label_y + index * 15, min(canvas_w - 16, 8 * len(ascii_safe(text)) + 12), 13, (6, 7, 10), 0.7)
        image.text(12, label_y + 3 + index * 15, text, (232, 236, 245), 1)
    return image.to_png()


def makeup_card(item: dict[str, Any], look: dict[str, Any]) -> bytes:
    """妆容/特效化妆卡：正脸底图 + 伤效标注 + 用色条，现场化妆台对着它上妆。"""
    width, height = size_for("3:4", 420)
    card = Raster(width, height, (14, 15, 20))
    card.vgradient((32, 30, 34), (10, 10, 14))
    skin = hex_rgb(str(item.get("skin") or "#d8b29a"), (216, 178, 154))
    hair = hex_rgb(str(item.get("hair_color") or "#2a2118"), (42, 33, 24))
    accent = hex_rgb(str(item.get("accent") or "#c2495a"), (194, 73, 90))
    cx, cy = width // 2, int(height * 0.42)
    rx, ry = int(width * 0.19), int(width * 0.25)
    card.glow(cx, cy, width * 0.62, accent, 0.16)
    card.ellipse(cx, cy - int(ry * 0.72), int(rx * 1.12), int(ry * 0.62), hair)
    card.ellipse(cx, cy, rx, ry, skin)
    eye_y = cy - int(ry * 0.18)
    for offset in (-1, 1):
        card.ellipse(cx + offset * int(rx * 0.42), eye_y, max(3, rx // 7), max(2, rx // 12), (20, 22, 28))
        card.line(cx + offset * int(rx * 0.62), eye_y - int(ry * 0.2), cx + offset * int(rx * 0.22), eye_y - int(ry * 0.26), (60, 46, 38), 2)
    card.line(cx - int(rx * 0.3), cy + int(ry * 0.42), cx + int(rx * 0.3), cy + int(ry * 0.42), (150, 108, 96), 2)

    for index, wound in enumerate((item.get("fx_rows") or [])[:5]):
        ratio_x = float(wound.get("x", 0.0))
        ratio_y = float(wound.get("y", 0.0))
        wx, wy = cx + int(ratio_x * rx), cy + int(ratio_y * ry)
        tone = hex_rgb(str(wound.get("color") or "#b3382f"), (179, 56, 47))
        if str(wound.get("kind") or "mark") == "cut":
            card.line(wx - 8, wy - 5, wx + 9, wy + 6, tone, 3)
            card.dashed_rect(wx - 11, wy - 9, 22, 18, (232, 236, 245), 4)
        elif str(wound.get("kind") or "mark") == "bruise":
            card.ellipse(wx, wy, 13, 10, tone, alpha=0.55)
            card.stroke_rect(wx - 15, wy - 12, 30, 24, (232, 236, 245), 1)
        else:
            card.ellipse(wx, wy, 7, 6, tone)
        card.text(wx + 17, wy - 6, ascii_safe(f"{index + 1} {wound.get('label', '')}")[:22], (240, 244, 250), 1)

    card.text(16, 14, f"MAKEUP  {ascii_safe(str(item.get('name', '')))}", (238, 242, 250), 2)
    rows = [
        f"FACE  {item.get('makeup', '')}",
        f"HAIR  {item.get('hair', '')}",
        f"FX    {item.get('fx', '')}",
        f"MATCH {item.get('continuity', '')}",
        f"SHOTS {item.get('shot_count', '')}  TOUCHUP {item.get('touchup', '')}",
    ]
    for index, row in enumerate(rows):
        card.text(16, int(height * 0.7) + index * 19, ascii_safe(row)[:56], (196, 206, 224), 1)
    swatches = [hex_rgb(value, (120, 110, 110)) for value in (item.get("makeup_palette") or [])[:6]]
    for index, color in enumerate(swatches):
        card.fill_rect(16 + index * 30, height - 42, 24, 24, color)
        card.stroke_rect(16 + index * 30, height - 42, 24, 24, (214, 220, 236), 1)
    card.grain(float(look.get("grain", 0.04)))
    card.vignette(0.34)
    return card.to_png()


def read_png(blob: bytes) -> Raster:
    """把 PNG 解回 Raster，让调色与包装岗位能在真机图上继续加工。

    支持 8 位灰度 / RGB / RGBA 与全部五种行滤波；其余（16 位、调色板）如实报错，
    不静默出一张错色图。
    """
    if blob[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("不是 PNG 数据")
    width = height = 0
    depth = color = -1
    idat = bytearray()
    pos = 8
    while pos + 8 <= len(blob):
        length = int.from_bytes(blob[pos:pos + 4], "big")
        kind = blob[pos + 4:pos + 8]
        data = blob[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, color = struct.unpack(">IIBB", data[:10])
        elif kind == b"IDAT":
            idat += data
        elif kind == b"IEND":
            break
    if depth != 8 or color not in (0, 2, 6):
        raise ValueError(f"只支持 8 位灰度/RGB/RGBA，收到 depth={depth} color={color}")
    channels = 1 if color == 0 else 3 if color == 2 else 4
    raw = zlib.decompress(bytes(idat))
    stride = width * channels
    image = Raster(width, height)
    previous = bytearray(stride)
    offset = 0
    for line_y in range(height):
        filter_type = raw[offset]
        offset += 1
        line = bytearray(raw[offset:offset + stride])
        offset += stride
        if filter_type == 1:
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif filter_type == 2:
            for i in range(stride):
                line[i] = (line[i] + previous[i]) & 0xFF
        elif filter_type == 3:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + previous[i]) >> 1)) & 0xFF
        elif filter_type == 4:
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                up = previous[i]
                upleft = previous[i - channels] if i >= channels else 0
                p = left + up - upleft
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - upleft)
                predictor = left if pa <= pb and pa <= pc else up if pb <= pc else upleft
                line[i] = (line[i] + predictor) & 0xFF
        elif filter_type != 0:
            raise ValueError(f"未知 PNG 行滤波：{filter_type}")
        start = line_y * width * 3
        if channels == 3:
            image.buf[start:start + stride] = bytes(line)
        elif channels == 1:
            for x in range(width):
                gray = line[x]
                image.buf[start + x * 3:start + x * 3 + 3] = bytes((gray, gray, gray))
        else:
            for x in range(width):
                image.buf[start + x * 3:start + x * 3 + 3] = bytes(line[x * 4:x * 4 + 3])
        previous = line
    return image
