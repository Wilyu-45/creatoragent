"""进程入口（对应 server/index.ts 的 bootstrap）。

本地启动：
    conda activate multi-agent-creator
    python -m app.main

或直接交给 uvicorn：
    uvicorn app.main:app --host 127.0.0.1 --port 8787

监听地址默认 127.0.0.1（仅本机，配合反向代理；见 deploy/ 下的反代样例），
可用 HOST 环境变量覆盖（容器清单固定 0.0.0.0）：容器端口发布要求进程
监听非 loopback，否则容器内探针照常通过、宿主机却连不上。
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
    log.info(f"启动 uvicorn：http://{config.host}:{config.port}")
    uvicorn.run(app, host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
