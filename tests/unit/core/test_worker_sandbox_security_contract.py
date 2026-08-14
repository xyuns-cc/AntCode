"""Worker 沙箱能否真正启动，取决于 compose 安全选项，必须被契约锁住。

真机实测：默认 compose 画像下**每一个任务**都以
``bwrap: No permissions to create new namespace`` 失败——不是隔离变弱，是执行面
整体不可用，而健康检查依然是 healthy，所以只有跑真任务才会暴露。

bwrap 需要三项容器级放宽（逐项二分验证过）：
  1. seccomp：Docker 内置 profile 在无 CAP_SYS_ADMIN 时禁止 clone/unshare 带
     CLONE_NEWUSER；
  2. apparmor：docker-default 直接 deny mount；
  3. systempaths：Docker 对 /proc 的屏蔽挂载让 userns 内挂 procfs 失败。

同时必须保住真正的边界：cap_drop ALL、no-new-privileges、只读根、非 root，
以及"用定制 seccomp profile 而不是 seccomp=unconfined"。
"""

import json
from pathlib import Path

import pytest
import yaml

SECCOMP_PROFILE_PATH = Path("infra/docker/seccomp/worker-userns.json")
WORKER_COMPOSE_PATHS = (
    Path("infra/docker/docker-compose.dev.yml"),
    Path("infra/docker/docker-compose.prod.worker.yml"),
)
REQUIRED_SECURITY_OPTS = (
    "no-new-privileges:true",
    "seccomp:./seccomp/worker-userns.json",
    "apparmor=unconfined",
    "systempaths=unconfined",
)
NAMESPACE_SYSCALLS = frozenset({"clone", "clone3", "unshare"})


def _worker(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))["services"]["worker"]


@pytest.mark.parametrize("compose_path", WORKER_COMPOSE_PATHS, ids=lambda p: p.name)
def test_worker_keeps_every_security_opt_bwrap_needs(compose_path: Path):
    security_opt = _worker(compose_path)["security_opt"]

    missing = [opt for opt in REQUIRED_SECURITY_OPTS if opt not in security_opt]
    assert not missing, f"{compose_path.name} 缺少 {missing}，Worker 的每个任务都会失败"


@pytest.mark.parametrize("compose_path", WORKER_COMPOSE_PATHS, ids=lambda p: p.name)
def test_worker_never_falls_back_to_blanket_relaxation(compose_path: Path):
    """放宽只能是定制 profile，绝不能退化成 seccomp=unconfined 或 SYS_ADMIN。"""
    worker = _worker(compose_path)

    assert "seccomp=unconfined" not in worker["security_opt"]
    assert worker["cap_drop"] == ["ALL"]
    assert "cap_add" not in worker
    assert worker.get("privileged") is not True


def test_seccomp_profile_is_the_builtin_one_plus_namespace_syscalls():
    profile = json.loads(SECCOMP_PROFILE_PATH.read_text(encoding="utf-8"))

    # 默认拒绝仍然成立 —— 这是它区别于 unconfined 的根本点。
    assert profile["defaultAction"] == "SCMP_ACT_ERRNO"

    allowed: set[str] = set()
    for group in profile["syscalls"]:
        if group["action"] != "SCMP_ACT_ALLOW":
            continue
        if group.get("args") or group.get("excludes") or group.get("includes"):
            continue
        allowed.update(group["names"])

    missing = NAMESPACE_SYSCALLS - allowed
    assert not missing, f"profile 未无条件放行 {sorted(missing)}，bwrap 建不了 user namespace"


def test_seccomp_profile_keeps_dangerous_syscalls_filtered():
    """放行 namespace 调用不等于放弃过滤：高危调用必须仍然被拦。"""
    profile = json.loads(SECCOMP_PROFILE_PATH.read_text(encoding="utf-8"))

    unconditionally_allowed: set[str] = set()
    for group in profile["syscalls"]:
        if group["action"] == "SCMP_ACT_ALLOW" and not (
            group.get("args") or group.get("excludes") or group.get("includes")
        ):
            unconditionally_allowed.update(group["names"])

    for dangerous in ("bpf", "perf_event_open", "kexec_load", "init_module"):
        assert dangerous not in unconditionally_allowed
