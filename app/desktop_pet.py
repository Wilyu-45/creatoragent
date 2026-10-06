"""桌面宠物运行器（只用标准库 tkinter，随宠物包一起交付）。

用法::

    python -m app.desktop_pet <宠物包目录或 package.zip>
    python runner/pet.py .            # 包内自带的同一份源码

行为：无边框 + 置顶 + 透明背景（``pet.json`` 的 ``key_color`` 即透明色），按清单播放
动作；待机/走动/睡觉随机切换，走动时贴屏幕边界折返，可拖拽（拖拽时播 ``drag``），
双击播 ``happy``，右键菜单手动切动作与退出。

为什么不依赖图像库：帧是 PNG，Tk 8.6 的 ``PhotoImage`` 原生可读；桌面透明也只有
``-transparentcolor`` 这一条路（没有 alpha 合成），所以 sample 通道的帧背景固定为
透明色。非 Windows 平台不支持 ``-transparentcolor`` 时**退化为带背景色的窗口**并在
控制台说明一次——不假装透明成功。
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

DEFAULT_KEY_COLOR = "#FF00FE"
#: 单次状态的帧数上限系数：动作循环这么多轮后重新挑状态（让状态机有节奏感）
MAX_STATE_LOOPS = 3


def load_package(target: Path) -> tuple[dict, Path]:
    """解析宠物包（目录或 zip），返回 ``(pet.json 内容, 包根目录)``。

    zip 会解到临时目录（运行期需要文件路径给 PhotoImage，不能直接从 zip 读）。
    """
    root = target
    if target.is_file() and zipfile.is_zipfile(target):
        staging = Path(tempfile.mkdtemp(prefix="desktop-pet-"))
        with zipfile.ZipFile(target) as bundle:
            bundle.extractall(staging)
        root = staging
        if not (root / "pet.json").is_file():
            children = [item for item in root.iterdir() if item.is_dir()]
            root = next((item for item in children if (item / "pet.json").is_file()), root)
    manifest_path = root / "pet.json"
    if not manifest_path.is_file():
        raise SystemExit(f"宠物包缺少 pet.json：{target}")
    return json.loads(manifest_path.read_text(encoding="utf-8")), root


class PetWindow:
    """一只桌宠：帧缓存 + 状态机 + 窗口移动。"""

    def __init__(self, manifest: dict, root: Path, display_size: int) -> None:
        import tkinter as tk  # 延迟 import：无显示环境时给可读错误而不是 ImportError

        self.manifest = manifest
        self.root = tk.Tk()
        self.root.title(str(manifest.get("name") or "桌面宠物"))
        self.root.overrideredirect(True)
        self.topmost_ok = True
        try:
            self.root.attributes("-topmost", True)
        except tk.TclError:
            self.topmost_ok = False
        self.key_color = str(manifest.get("key_color") or DEFAULT_KEY_COLOR)
        try:
            self.root.attributes("-transparentcolor", self.key_color)
            self.transparent = True
        except tk.TclError:  # 非 Windows：透明不可用，如实退化
            self.transparent = False
            print("提示：当前平台不支持 -transparentcolor，宠物将带背景色显示")

        size = max(32, int(manifest.get("size") or display_size or 96))
        self.actions = self._load_actions(root, tk, min(size, display_size) if display_size else size)
        if not self.actions:
            raise SystemExit("宠物包里没有任何可播放的动作帧（检查 pet.json 的 actions 与 frames/）")
        self.default = str(manifest.get("default_action") or "idle")
        if self.default not in self.actions:
            self.default = next(iter(self.actions))
        first = self.actions[self.default]
        self.width, self.height = first["images"][0].width(), first["images"][0].height()

        self.canvas = tk.Canvas(
            self.root, width=self.width, height=self.height,
            bg=self.key_color, highlightthickness=0, bd=0,
        )
        self.canvas.pack()
        self.sprite = self.canvas.create_image(0, 0, anchor="nw", image=first["images"][0])

        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.x = int(screen_w * 0.72)
        self.y = int(screen_h * 0.7)
        self.root.geometry(f"{self.width}x{self.height}+{self.x}+{self.y}")

        self.frame_index = 0
        self.loops_left = 1
        self.direction = 1
        self.dragging = False
        self.drag_offset = (0, 0)
        self.state_name = self.default
        self.after_id: int | None = None

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Double-Button-1>", lambda _event: self.play("happy"))
        self.menu = tk.Menu(self.root, tearoff=0)
        for name in self.actions:
            self.menu.add_command(label=name, command=lambda value=name: self.play(value))
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self.root.destroy)
        self.canvas.bind("<Button-3>", lambda _event: self.menu.tk_popup(*self.root.winfo_pointerxy()))
        if not self.topmost_ok:
            print("提示：当前窗口管理器不支持置顶，宠物可能被其他窗口盖住")

    def _load_actions(self, package: Path, tk, display_size: int) -> dict:
        """读取并缓存帧；真实通道的帧可能很大，按显示尺寸整数倍降采样。"""
        actions: dict[str, dict] = {}
        for entry in self.manifest.get("actions") or []:
            images = []
            for item in entry.get("frames") or []:
                path = package / str(item.get("file") or "")
                if not path.is_file():
                    continue
                image = tk.PhotoImage(file=str(path))
                factor = max(1, image.width() // display_size) if display_size else 1
                if factor > 1:
                    image = image.subsample(factor)
                images.append(image)
            if images:
                actions[str(entry.get("name") or "idle")] = {
                    "images": images,
                    "interval": max(40, int(1000 / max(1, int(entry.get("fps") or 6)))),
                    "loop": bool(entry.get("loop", True)),
                }
        return actions

    def play(self, name: str) -> None:
        if name in self.actions and name != self.state_name:
            self.state_name = name
            self.frame_index = 0
            self.loops_left = MAX_STATE_LOOPS if self.actions[name]["loop"] else 1

    def _pick_next(self) -> None:
        """状态机：走动后大概率回到待机，待机久了会去睡，睡觉会自己醒。"""
        weights = {"idle": 3, "walk": 2, "sleep": 1}
        pool = [name for name, weight in weights.items() if name in self.actions for _ in range(weight)]
        if self.state_name == "sleep":
            pool = [name for name in pool if name != "sleep"] or ["idle"]
        self.play(random.choice(pool) if pool else self.default)

    def _tick(self) -> None:
        spec = self.actions[self.state_name]
        images = spec["images"]
        if self.state_name == "walk" and not self.dragging:
            self._move()
        self.canvas.itemconfig(self.sprite, image=images[self.frame_index % len(images)])
        self.frame_index += 1
        if self.frame_index >= len(images):
            self.frame_index = 0
            self.loops_left -= 1
            if self.loops_left <= 0:
                if spec["loop"]:
                    self._pick_next()
                else:  # 单次动作（happy/drag）播完回待机
                    self.play(self.default)
        self.after_id = self.root.after(spec["interval"], self._tick)

    def _move(self) -> None:
        screen_w = self.root.winfo_screenwidth()
        self.x += self.direction * 3
        if self.x <= 0 or self.x + self.width >= screen_w:
            self.direction *= -1
            self.x = max(0, min(self.x, screen_w - self.width))
        self.root.geometry(f"{self.width}x{self.height}+{self.x}+{self.y}")

    def _on_press(self, event) -> None:
        self.dragging = True
        self.drag_offset = (event.x_root - self.x, event.y_root - self.y)
        if "drag" in self.actions:
            self.play("drag")

    def _on_drag(self, event) -> None:
        self.x, self.y = event.x_root - self.drag_offset[0], event.y_root - self.drag_offset[1]
        self.root.geometry(f"{self.width}x{self.height}+{self.x}+{self.y}")

    def _on_release(self, _event) -> None:
        self.dragging = False
        self.play(self.default)

    def run(self) -> None:
        self.after_id = self.root.after(60, self._tick)
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            pass
        finally:
            if self.after_id is not None:
                self.root.after_cancel(self.after_id)
            self.root.destroy()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行一只桌面宠物（宠物包目录或 zip）")
    parser.add_argument("package", nargs="?", default=".", help="宠物包路径（默认当前目录）")
    parser.add_argument("--size", type=int, default=0, help="显示边长（像素），默认取包内 size")
    args = parser.parse_args(argv)

    target = Path(args.package).expanduser().resolve()
    if not target.exists():
        print(f"宠物包不存在：{target}")
        return 2
    manifest, root = load_package(target)
    try:
        pet = PetWindow(manifest, root, args.size)
    except Exception as error:  # noqa: BLE001 - 无显示环境要给出人话，而不是栈
        print(f"桌宠启动失败：{type(error).__name__}: {error}")
        return 1
    print(f"已启动桌面宠物：{manifest.get('name')}（动作 {'、'.join(pet.actions)}；右键退出）")
    pet.run()
    if root.parent == Path(tempfile.gettempdir()):
        shutil.rmtree(root, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
