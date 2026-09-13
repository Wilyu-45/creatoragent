"""进程入口（对应 server/index.ts 的 bootstrap）。

本地启动：
    conda activate multi-agent-creator
    python -m app.main

或直接交给 uvicorn：
    uvicorn app.main:app --host 127.0.0.1 --port 8787
"""

from __future__ import annotations

import uvicorn

from .config import get_config
from .logger import create_logger
from .server import create_app

log = create_logger("main")

app = create_app()


def main() -> None:
    config = get_config()
    log.info(f"启动 uvicorn：http://127.0.0.1:{config.port}")
    uvicorn.run(app, host="127.0.0.1", port=config.port, log_level="info")


if __name__ == "__main__":
    main()
