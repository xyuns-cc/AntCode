"""依赖注入"""

from typing import Optional
from fastapi import HTTPException

from ..core import WorkerEngine, get_worker_engine


# 全局引擎实例
_engine: Optional[WorkerEngine] = None


def get_engine() -> WorkerEngine:
    """获取引擎实例"""
    global _engine
    # 优先使用全局引擎
    engine = _engine or get_worker_engine()
    if engine is None:
        raise HTTPException(status_code=503, detail="引擎未初始化")
    return engine


def set_engine(engine: WorkerEngine) -> None:
    """设置引擎实例"""
    global _engine
    _engine = engine
