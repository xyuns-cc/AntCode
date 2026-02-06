"""
AntCode Web API 主入口

启动 FastAPI 应用服务器
"""

import socket
import sys

import uvicorn
from uvicorn.config import LOGGING_CONFIG

from antcode_core.common.config import settings


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

    if not _port_available(settings.SERVER_HOST, settings.SERVER_PORT):
        print(
            f"端口 {settings.SERVER_PORT} 已被占用，"
            "请停止占用进程或修改 .env 的 SERVER_PORT 后重试。",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # 使用配置文件中的主机和端口
    uvicorn.run(
        "antcode_web_api.app:app",
        host=settings.SERVER_HOST,
        port=settings.SERVER_PORT,
        reload=settings.SERVER_RELOAD,
        log_config=LOGGING_CONFIG,
    )


if __name__ == "__main__":
    main()
