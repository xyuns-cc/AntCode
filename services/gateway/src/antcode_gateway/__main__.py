"""
Gateway 服务模块入口

支持通过 python -m antcode_gateway 运行服务。
"""

import asyncio
import sys

from loguru import logger

from antcode_gateway.main import main

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("服务已停止")
        sys.exit(0)
