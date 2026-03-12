"""FastAPI OpenAPI 相关配置。"""

from __future__ import annotations

import re

from fastapi.routing import APIRoute

from antcode_core.domain.schemas.common import ErrorResponse

API_TAGS = [
    {"name": "基础", "description": "系统状态、应用信息与认证相关基础接口"},
    {"name": "认证", "description": "登录、刷新令牌与权限查询接口"},
    {"name": "用户管理", "description": "用户与权限管理接口"},
    {"name": "项目管理", "description": "项目 CRUD 与配置相关接口"},
    {"name": "项目下载", "description": "项目分发、下载与增量同步接口"},
    {"name": "项目版本管理", "description": "项目版本发布、回滚与查询接口"},
    {"name": "任务", "description": "任务编排、执行与控制接口"},
    {"name": "任务运行", "description": "任务运行详情、取消与日志文件接口"},
    {"name": "运行时管理", "description": "Python 运行时、依赖包与环境接口"},
    {"name": "环境管理", "description": "共享/项目环境生命周期管理接口"},
    {"name": "日志管理", "description": "系统日志、运行日志与统计分析接口"},
    {"name": "WebSocket", "description": "实时日志推送与连接管理接口"},
    {"name": "仪表盘", "description": "监控总览与业务指标聚合接口"},
    {"name": "监控", "description": "系统监控、健康与资源指标接口"},
    {"name": "Worker 管理", "description": "Worker 注册、心跳与资源管理接口"},
    {"name": "系统配置", "description": "系统级配置管理接口"},
    {"name": "告警管理", "description": "告警规则、通知与统计接口"},
    {"name": "审计日志", "description": "审计日志查询、统计与清理接口"},
    {"name": "任务重试", "description": "失败重试、补偿与重试历史接口"},
    {"name": "分布式爬取", "description": "分布式爬取任务与节点管理接口"},
]

DEFAULT_ERROR_RESPONSES = {
    400: {"model": ErrorResponse, "description": "请求参数错误"},
    401: {"model": ErrorResponse, "description": "未认证或令牌无效"},
    403: {"model": ErrorResponse, "description": "权限不足"},
    404: {"model": ErrorResponse, "description": "资源不存在"},
    409: {"model": ErrorResponse, "description": "资源冲突"},
    422: {"model": ErrorResponse, "description": "请求体验证失败"},
    429: {"model": ErrorResponse, "description": "请求过于频繁"},
    500: {"model": ErrorResponse, "description": "服务器内部错误"},
    503: {"model": ErrorResponse, "description": "服务暂不可用"},
}


def _sanitize_segment(value: str) -> str:
    normalized = value.strip().lower().replace("-", "_")
    normalized = re.sub(r"\{([^}]+)\}", r"by_\1", normalized)
    normalized = re.sub(r"[^a-z0-9_]+", "_", normalized)
    return normalized.strip("_")


def generate_operation_id(route: APIRoute) -> str:
    """生成稳定且可读的 operationId（便于 SDK 代码生成）。"""
    method = sorted((route.methods or {"GET"}))[0].lower()
    tag = _sanitize_segment(route.tags[0] if route.tags else "default")
    path = _sanitize_segment(route.path_format)
    return "_".join(part for part in (method, tag, path) if part)


__all__ = ["API_TAGS", "DEFAULT_ERROR_RESPONSES", "generate_operation_id"]
