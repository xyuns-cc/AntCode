"""compose 契约测试的共用加载器。

生产 compose 用 ``extends`` 把 worker 定义拆到独立文件（便于单机 worker 单独部署），
断言必须看到合并后的最终形态，否则会漏掉基线文件里声明的字段。这里把解析逻辑集中一份，
供各个 ``test_docker_compose_*_contract`` 复用。
"""

from pathlib import Path
from typing import Any

import yaml


def merge_mapping(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """按 compose 语义递归合并：overlay 的同名标量/列表整体覆盖，字典逐键下钻。"""
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_mapping(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_compose(path: Path) -> dict[str, Any]:
    """解析 compose 并就地展开 ``extends``，返回服务的最终形态。"""
    compose = yaml.safe_load(path.read_text(encoding="utf-8"))
    resolved_services = {}
    for name, service in compose.get("services", {}).items():
        extension = service.get("extends")
        if extension is None:
            resolved_services[name] = service
            continue
        source = load_compose(path.parent / extension["file"])["services"][extension["service"]]
        overlay = {key: value for key, value in service.items() if key != "extends"}
        resolved_services[name] = merge_mapping(source, overlay)
    compose["services"] = resolved_services
    return compose
