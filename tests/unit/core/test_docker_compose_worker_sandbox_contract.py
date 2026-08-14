"""Worker 沙箱 fail-closed 的容器运行时前提契约。

``executor/process_limits.py`` 的 ``preexec_fn`` 对每个任务子进程施加 POSIX rlimit，
任一项设置失败就拒绝启动子进程（沙箱失效必须表现为启动失败，不能静默放行）。
其中 ``RLIMIT_NOFILE`` 取自 ``ExecutorConfig.default_max_open_files``，没有 env 覆盖。

容器如果不声明 ``ulimits``，nofile 就完全继承宿主 dockerd 的默认值——只要某台宿主的
hard limit 低于该值，``setrlimit`` 立刻抛 "current limit exceeds maximum limit"，
Worker 将拒绝执行任何任务。这是一个代码无法自证的隐式宿主依赖，只能在 compose 里
显式声明并由本契约锁死，防止后来者删掉。
"""

import re
from pathlib import Path

from tests.unit.core.compose_support import load_compose

COMPOSE_DIR = Path("infra/docker")
WORKER_EXECUTOR_BASE = Path("services/worker/src/antcode_worker/executor/base.py")
# 只有带 image/build 的定义才真正启动 worker 容器；其余 compose 是 overlay，
# 只叠 environment/secrets，ulimits 从基线定义继承。
WORKER_RUNTIME_KEYS = {"image", "build"}
# docker-compose.remote*.yml 是 .gitignore 排除的本机测试产物，CI 上不存在，
# 因此不列入必检集合——但只要本地存在就会被 glob 一并校验。
REQUIRED_WORKER_COMPOSE_NAMES = {
    "docker-compose.dev.yml",
    "docker-compose.prod.worker.yml",
    "docker-compose.prod.yml",
}
SANDBOX_NOFILE_PATTERN = re.compile(r"^\s*default_max_open_files:\s*int\s*=\s*(\d+)\s*$", re.MULTILINE)


def _sandbox_required_nofile() -> int:
    """直接从 executor 源码读出实际取值，避免 compose 与代码各写一份后悄悄漂移。"""
    source = WORKER_EXECUTOR_BASE.read_text(encoding="utf-8")
    match = SANDBOX_NOFILE_PATTERN.search(source)

    assert match is not None, "ExecutorConfig.default_max_open_files 已改名或改写，nofile 契约失效"
    return int(match.group(1))


def _worker_runtime_definitions() -> dict[str, dict]:
    """展开 extends 后，所有真正启动 worker 容器的 compose 定义。"""
    definitions = {}
    for path in sorted(COMPOSE_DIR.glob("docker-compose*.yml")):
        worker = load_compose(path)["services"].get("worker")
        if worker is None or not WORKER_RUNTIME_KEYS & worker.keys():
            continue
        definitions[path.name] = worker
    return definitions


def test_worker_compose_pins_nofile_ulimit_at_or_above_sandbox_rlimit() -> None:
    """每个 worker 容器都必须显式 pin ulimits.nofile，且 soft/hard 均不低于代码要求。"""
    required = _sandbox_required_nofile()
    definitions = _worker_runtime_definitions()

    assert REQUIRED_WORKER_COMPOSE_NAMES <= definitions.keys()
    for name, worker in definitions.items():
        nofile = worker.get("ulimits", {}).get("nofile")
        assert nofile is not None, f"{name}: worker 未声明 ulimits.nofile，沙箱 rlimit 会继承宿主默认值"
        assert nofile["soft"] >= required, f"{name}: nofile soft {nofile['soft']} < 代码要求 {required}"
        assert nofile["hard"] >= required, f"{name}: nofile hard {nofile['hard']} < 代码要求 {required}"


def test_worker_sandbox_rlimit_stays_fail_closed() -> None:
    """上面的 compose 契约只有在沙箱仍是 fail-closed 时才有意义，一并锁住前提。"""
    limits = Path("services/worker/src/antcode_worker/executor/process_limits.py").read_text(encoding="utf-8")

    assert "resource.setrlimit(limit_kind, (request.limit_value, request.limit_value))" in limits
    assert "RLIMIT_NOFILE" in limits
