"""桌面版启动器（PyInstaller 打包入口，对应 ``make app``）。

打包（在仓库根目录执行，需先 ``npm run build`` 产出 dist/）：
    python -m PyInstaller --noconfirm --clean --onefile \
        --name CreatorStudio --distpath release --workpath build/pyinstaller \
        --add-data "dist;dist" app/desktop.py

frozen 环境关键处理（顺序敏感）：
- 数据目录指向 exe 旁 ``data/`` —— onefile 的 ``sys._MEIPASS`` 是临时解压目录，
  重启即失，绝不能让 config.py 把数据写进去；
- 可选加载 exe 旁 ``.env``（``override=False``：进程环境变量优先级不变）；
  以上两者都必须在 ``import app.config`` **之前**完成。
- 前端静态资源由 PyInstaller ``--add-data "dist;dist"`` 放进
  ``_MEIPASS/dist``，恰好等于 server.py 的 ``ROOT_DIR / "dist"``。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _prepare_frozen_env() -> None:
    """frozen（PyInstaller）模式下的路径自举，必须在 import app.* 之前调用。"""
    if not getattr(sys, "frozen", False):
        return
    exe_dir = Path(sys.executable).resolve().parent
    os.environ.setdefault("CREATOR_DATA_DIR", str(exe_dir / "data"))
    from dotenv import load_dotenv

    load_dotenv(exe_dir / ".env", override=False)


_prepare_frozen_env()

import threading  # noqa: E402
import time  # noqa: E402
import webbrowser  # noqa: E402

import uvicorn  # noqa: E402

from app.config import DATA_DIR, get_config  # noqa: E402
from app.main import app  # noqa: E402


def main() -> None:
    config = get_config()
    display_host = "127.0.0.1" if config.host in ("0.0.0.0", "::", "") else config.host
    url = f"http://{display_host}:{config.port}"
    server = uvicorn.Server(
        uvicorn.Config(app, host=config.host, port=config.port, log_level="info")
    )
    # 非主线程跑 uvicorn（signal 安装会被 uvicorn 自行跳过）；主线程负责
    # 开浏览器并等待退出。daemon=True：主线程结束（如 Ctrl+C）时随之终止。
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):  # 最多等 10s，端口就绪后再开浏览器
        if server.started:
            break
        time.sleep(0.1)
    webbrowser.open(url)
    print(f"Creator Agent Studio 已启动：{url}（数据目录：{DATA_DIR}）")
    try:
        while thread.is_alive():
            thread.join(timeout=1)
    except KeyboardInterrupt:
        server.should_exit = True
        thread.join(timeout=5)


if __name__ == "__main__":
    main()
