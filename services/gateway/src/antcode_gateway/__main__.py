"""
Gateway 服务模块入口

支持通过 python -m antcode_gateway 运行服务。
"""

import asyncio
import sys
from pathlib import Path

# GatewayConfig 用 os.getenv 直接读；启动时先把项目根 .env 载入 os.environ
try:
    from dotenv import load_dotenv

    _env_file = Path(__file__).resolve().parents[4] / ".env"
    if _env_file.exists():
        load_dotenv(_env_file, override=False)
except ImportError:
    pass

from loguru import logger

from antcode_gateway.main import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("服务已停止")
        sys.exit(0)
