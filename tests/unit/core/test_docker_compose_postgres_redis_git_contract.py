import re
from pathlib import Path

import yaml

COMPOSE_PATH = Path("infra/docker/docker-compose.dev.yml")
ROOT_ENV_EXAMPLE_PATH = Path(".env.example")
DOCKER_ENV_EXAMPLE_PATH = Path("infra/docker/.env.example")
DOCKER_README_PATH = Path("infra/docker/README.md")
# 运维脚本用 "$ROOT/infra/docker/xxx.yml" 引用 Compose。路径段必须连 `$`/`{}` 一起
# 捕获：否则 "$ROOT/" 只截到 "ROOT/"，得到一条既不存在、也不是笔误的假路径，
# 让本文件的存在性断言对所有 shell 脚本恒假。
COMPOSE_REFERENCE = re.compile(r"(?:[A-Za-z0-9_.${}-]+/)*docker-compose[A-Za-z0-9_.-]*\.ya?ml")
DOCUMENTATION_ROOTS = (Path("infra/docker"), Path("migrations"), Path("tests/contracts"))
# 运维文本源集合的非空下界。运维说明收敛到各目录 README 时源集合缩小过一次；没有这
# 个下界，再有一次收敛把 README 也扫空时，存在性断言会静默变成恒真的空转。
MINIMUM_COMPOSE_REFERENCES = 20


def _active_operational_sources() -> tuple[Path, ...]:
    documentation = (path for root in DOCUMENTATION_ROOTS for path in root.rglob("*.md"))
    scripts = (*Path("infra/docker").glob("*.sh"), *Path("scripts").glob("*.sh"))
    return (Path("README.md"), Path("Makefile"), *documentation, *scripts)


def _strip_shell_variable_prefix(reference: str) -> str:
    """丢掉 "$ROOT" / "${ROOT}" 这类只有 shell 展开后才成立的前缀段。"""
    segments = reference.split("/")
    while len(segments) > 1 and "$" in segments[0]:
        segments.pop(0)
    return "/".join(segments)


def _compose_reference_candidates(source: Path, reference: str) -> tuple[Path, ...]:
    relative = _strip_shell_variable_prefix(reference)
    return (Path(relative), source.parent / relative, Path("infra/docker") / relative)


def test_docker_compose_does_not_define_redis_password_fallback():
    compose = COMPOSE_PATH.read_text(encoding="utf-8")

    assert "REDIS_PASSWORD:-" not in compose
    assert "redis_password" not in compose


def test_dev_redis_persists_acl_and_control_plane_enables_it():
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    services = compose["services"]
    redis = services["redis"]

    assert "--aclfile" in redis["command"][0]
    assert "/usr/local/bin/docker-entrypoint.sh" in redis["command"][0]
    assert "su-exec" not in redis["command"][0]
    assert redis["healthcheck"]["test"] == [
        "CMD-SHELL",
        'REDISCLI_AUTH="$${REDIS_PASSWORD}" redis-cli ping',
    ]
    assert "redis_acl:/etc/redis-acl" in redis["volumes"]
    for service_name in ("web-api", "master", "gateway"):
        assert "REDIS_ACL_ENABLED=true" in services[service_name]["environment"]


def test_direct_worker_waits_for_web_api_and_uses_service_url():
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    worker = compose["services"]["worker"]

    assert "WORKER_API_BASE_URL=http://web-api:8000" in worker["environment"]
    assert worker["depends_on"]["web-api"] == {"condition": "service_healthy"}
    assert "env_file" not in worker
    names = {entry.split("=", maxsplit=1)[0] for entry in worker["environment"]}
    # Worker 的后端边界是非对称的，两侧都要锁住：
    # Redis 侧只能拿 WORKER_REDIS_URL 指向的独立最小权限 ACL 账号，控制面的
    # REDIS_URL 一旦注入就绕过了整套 ACL 设计；PostgreSQL 侧相反，direct 模式的
    # 产物平面是 PostgresArtifactTransferStore，源码包下载与产物上传都直连 PG
    # blob 存储，缺 DATABASE_URL 会在第一次取源码时失败。
    assert "REDIS_URL" not in names
    assert "DATABASE_URL" in names
    assert "WORKER_DIRECT_SCOPED_REDIS=true" in worker["environment"]
    assert "WORKER_REDIS_URL=redis://redis:6379/0" in worker["environment"]
    assert "REDIS_ACL_ENABLED=true" in worker["environment"]
    assert (
        "ANTCODE_WORKER_KEY=${ANTCODE_WORKER_KEY:?generate a one-time Worker install key first}"
        in worker["environment"]
    )


def test_dev_startup_requires_successful_migration():
    services = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))["services"]
    migration = services["migration"]

    assert migration["command"] == ["python", "-m", "scripts.init_db"]
    assert migration["restart"] == "no"
    for service_name in ("web-api", "master", "gateway"):
        assert services[service_name]["depends_on"]["migration"] == {"condition": "service_completed_successfully"}


def test_active_operations_only_reference_existing_compose_files() -> None:
    scanned = 0
    for source in _active_operational_sources():
        content = source.read_text(encoding="utf-8")
        for reference in COMPOSE_REFERENCE.findall(content):
            scanned += 1
            candidates = _compose_reference_candidates(source, reference)
            assert any(path.is_file() for path in candidates), f"{source}: missing Compose file {reference}"
    assert scanned >= MINIMUM_COMPOSE_REFERENCES, f"只扫到 {scanned} 条 Compose 引用，运维文本源集合已失效"


def test_compose_reference_scan_covers_shell_variable_paths() -> None:
    """存在性断言不得因为正则漏匹配而变成空转。

    上一版正则把 "$ROOT/infra/docker/x.yml" 截成 "ROOT/infra/docker/x.yml"，
    既扫不到真实文件、也无法证伪，等于对所有用 $ROOT 的运维脚本全部失效。
    这里把"必须真的扫到 shell 变量前缀的引用"钉死。
    """
    assert _strip_shell_variable_prefix("$ROOT/infra/docker/docker-compose.prod.yml") == (
        "infra/docker/docker-compose.prod.yml"
    )
    assert _strip_shell_variable_prefix("${ROOT}/docker-compose.dev.yml") == "docker-compose.dev.yml"
    assert _strip_shell_variable_prefix("infra/docker/docker-compose.dev.yml") == (
        "infra/docker/docker-compose.dev.yml"
    )

    shell_variable_references = [
        (source, reference)
        for source in _active_operational_sources()
        if source.suffix == ".sh"
        for reference in COMPOSE_REFERENCE.findall(source.read_text(encoding="utf-8"))
        if "$" in reference
    ]
    assert shell_variable_references, "运维脚本里的 $VAR 前缀 Compose 引用一条都没扫到，正则已失效"
    for source, reference in shell_variable_references:
        candidates = _compose_reference_candidates(source, reference)
        assert any(path.is_file() for path in candidates), f"{source}: missing Compose file {reference}"


def test_web_api_uses_a_non_root_compatible_named_data_volume():
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))

    assert "web_data:/app/data" in compose["services"]["web-api"]["volumes"]
    assert compose["volumes"]["web_data"] == {"driver": "local"}


def test_worker_uses_unprivileged_read_only_sandbox_container():
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    worker = compose["services"]["worker"]

    assert worker.get("privileged") is not True
    assert worker.get("user") not in {"0", 0, "root"}
    assert worker["read_only"] is True
    assert worker["cap_drop"] == ["ALL"]
    # security_opt 的完整集合由 test_worker_sandbox_security_contract.py 断言：
    # bwrap 的 user namespace 在 Docker 下还需要定制 seccomp profile +
    # apparmor/systempaths 放宽，缺了任务会 100% 失败。这里只锁"绝不能出现"的项。
    assert "no-new-privileges:true" in worker["security_opt"]
    assert "seccomp=unconfined" not in worker["security_opt"]
    assert "cap_add" not in worker
    assert "/tmp:exec,uid=1000,gid=1000,mode=1777,size=4g" in worker["tmpfs"]


def test_trusted_proxy_examples_are_explicit_and_fail_closed() -> None:
    examples = (
        ROOT_ENV_EXAMPLE_PATH.read_text(encoding="utf-8"),
        DOCKER_ENV_EXAMPLE_PATH.read_text(encoding="utf-8"),
    )

    for example in examples:
        assert example.splitlines().count("ANTCODE_TRUSTED_PROXIES=") == 1
        assert "ANTCODE_TRUSTED_PROXIES=0.0.0.0/0" not in example
        assert "ANTCODE_TRUSTED_PROXIES=::/0" not in example

    documentation = DOCKER_README_PATH.read_text(encoding="utf-8")
    assert "与 Web API 直接建立" in documentation
    assert "不要填写 `0.0.0.0/0`、`::/0`" in documentation
