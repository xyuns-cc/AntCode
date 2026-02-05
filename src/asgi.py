"""ASGI entrypoint.

保持模块导入无副作用：仅在 ASGI 入口处创建 FastAPI app。
"""

from src.bootstrap import create_app

app = create_app()

