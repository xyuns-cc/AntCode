"""
AntCode Worker 入口

使用 HealthServer (aiohttp) 提供健康检查端点。

Requirements: 7.1
"""

from antcode_worker.cli import main

if __name__ == "__main__":
    main()
