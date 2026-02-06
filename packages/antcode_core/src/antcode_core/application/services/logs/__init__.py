"""日志服务"""

__all__ = [
    "LogChunkReceiver",
    "log_chunk_receiver",
    "LogCleanupService",
    "log_cleanup_service",
    "LogSecurityService",
    "TaskLogService",
]


def __getattr__(name: str):
    module_map = {
        "LogChunkReceiver": "antcode_core.application.services.logs.log_chunk_receiver",
        "log_chunk_receiver": "antcode_core.application.services.logs.log_chunk_receiver",
        "LogCleanupService": "antcode_core.application.services.logs.log_cleanup_service",
        "log_cleanup_service": "antcode_core.application.services.logs.log_cleanup_service",
        "LogSecurityService": "antcode_core.application.services.logs.log_security_service",
        "TaskLogService": "antcode_core.application.services.logs.task_log_service",
    }
    if name in module_map:
        import importlib

        module = importlib.import_module(module_map[name])
        value = getattr(module, name)
        globals()[name] = value
        return value
    raise AttributeError(f"module 'antcode_core.application.services.logs' has no attribute '{name}'")
