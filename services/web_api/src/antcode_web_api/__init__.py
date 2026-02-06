"""启动模块。"""

__all__ = ["create_app", "lifespan", "register_routes", "app"]


def __getattr__(name: str):
    if name in ("create_app", "lifespan", "register_routes"):
        import importlib

        module_map = {
            "create_app": "antcode_web_api.app_factory",
            "lifespan": "antcode_web_api.lifespan",
            "register_routes": "antcode_web_api.routes",
        }
        module = importlib.import_module(module_map[name])
        value = getattr(module, name)
        globals()[name] = value
        return value
    if name == "app":
        from antcode_web_api.app import get_app

        value = get_app()
        globals()[name] = value
        return value
    raise AttributeError(f"module 'antcode_web_api' has no attribute '{name}'")
