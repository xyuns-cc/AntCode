"""Route registration helpers."""

from typing import Any

from fastapi import APIRouter


def _is_static(path: str) -> bool:
    return "{" not in path


def promote_static_routes(router: APIRouter, paths: set[str] | None = None) -> None:
    """把静态路由排到参数路由之前。

    Starlette 按注册顺序做首次匹配,因此先注册的参数路由(``/{worker_id}``)会
    遮蔽后注册的同段数静态路由(``/install-keys``),请求落到错误的 handler 上。
    这里统一前置所有不含路径参数的路由,而不是维护一份手工白名单——白名单
    每漏一条就是一个静默的 404/403。``paths`` 保留为显式优先集,用于在静态
    路由内部再指定次序;排序是稳定的,未列出的路由保持原有相对顺序。
    """

    def rank(route: Any) -> tuple[int, int]:
        path = getattr(route, "path", "") or ""
        return (0 if _is_static(path) else 1, 0 if paths and path in paths else 1)

    router.routes.sort(key=rank)


def find_shadowed_routes(router: APIRouter) -> list[tuple[str, str, str]]:
    """找出被更早注册的参数路由遮蔽的静态路由。

    返回 ``(method, static_path, shadowing_path)`` 三元组。正常情况下应为空;
    供测试断言使用,防止新增路由时重新引入遮蔽。
    """
    shadowed: list[tuple[str, str, str]] = []
    seen_params: list[tuple[str, str]] = []
    for route in router.routes:
        path = getattr(route, "path", "") or ""
        methods = getattr(route, "methods", None) or set()
        for method in sorted(methods):
            if method in ("HEAD", "OPTIONS"):
                continue
            if _is_static(path):
                for prev_method, prev_path in seen_params:
                    if prev_method == method and _segments_match(prev_path, path):
                        shadowed.append((method, path, prev_path))
            else:
                seen_params.append((method, path))
    return shadowed


def _segments_match(param_path: str, static_path: str) -> bool:
    """参数路由是否会吃掉该静态路由(段数相同且每段可匹配)。"""
    param_segments = param_path.strip("/").split("/")
    static_segments = static_path.strip("/").split("/")
    if len(param_segments) != len(static_segments):
        return False
    return all(
        param_seg.startswith("{") or param_seg == static_seg
        for param_seg, static_seg in zip(param_segments, static_segments, strict=True)
    )


__all__ = ["find_shadowed_routes", "promote_static_routes"]
