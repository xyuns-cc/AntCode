"""
AntCode Web API 主入口

启动 FastAPI 应用服务器
"""

import socket
import sys

import uvicorn
from antcode_core.common.config import settings
from uvicorn.config import LOGGING_CONFIG


def _port_available(host: str, port: int) -> bool:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    address = (host, port, 0, 0) if family == socket.AF_INET6 else (host, port)
    with socket.socket(family, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(address)
        except OSError:
            return False
    return True


def main():
    """主函数"""
    # 配置日志格式
    LOGGING_CONFIG["formatters"]["default"]["fmt"] = "%(asctime)s %(levelprefix)s %(message)s"
    LOGGING_CONFIG["formatters"]["default"]["datefmt"] = "%Y-%m-%d %H:%M:%S"
    LOGGING_CONFIG["formatters"]["access"]["fmt"] = (
        '%(asctime)s %(levelprefix)s %(message)s - "%(request_line)s" %(status_code)s'
    )
    LOGGING_CONFIG["formatters"]["access"]["datefmt"] = "%Y-%m-%d %H:%M:%S"

    if not _port_available(settings.BIND_HOST, settings.SERVER_PORT):
        print(
            f"端口 {settings.SERVER_PORT} 已被占用，请停止占用进程或修改 .env 的 SERVER_PORT 后重试。",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # workers>1 和 reload 互斥（reload 需要单进程持有 socket + inotify watcher）
    # dev 场景保持 reload=True + workers=1；生产 workers=SERVER_WORKERS
    run_kwargs: dict = {
        "host": settings.BIND_HOST,
        "port": settings.SERVER_PORT,
        "log_config": LOGGING_CONFIG,
        "timeout_graceful_shutdown": 20,
    }
    if settings.SERVER_WORKERS > 1:
        run_kwargs["workers"] = settings.SERVER_WORKERS
    else:
        run_kwargs["reload"] = settings.SERVER_RELOAD

    uvicorn.run("antcode_web_api.app:app", **run_kwargs)


if __name__ == "__main__":
    main()
