from pathlib import Path

DEPLOY_SCRIPT = Path("infra/docker/deploy-production.sh")
DOCKER_README = Path("infra/docker/README.md")


def test_production_upgrade_runs_schema_init_before_starting_services() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    stop_writers = '"${compose[@]}" stop --timeout'
    crawl_preflight = "python -m scripts.crawl_redis_preflight"
    schema_init = '"${compose[@]}" run --rm --no-deps migration\n'
    start_services = '"${compose[@]}" up -d --wait --wait-timeout "$WAIT_TIMEOUT"'

    assert script.startswith("#!/usr/bin/env bash\nset -euo pipefail\n")
    assert script.index(stop_writers) < script.index(crawl_preflight)
    assert script.index(crawl_preflight) < script.index(schema_init)
    assert script.index(schema_init) < script.rindex(start_services)


def test_production_runbook_documents_fail_closed_schema_init() -> None:
    readme = DOCKER_README.read_text(encoding="utf-8")

    assert "数据库 migration 执行标准 `scripts.init_db`" in readme
    assert "schema 初始化失败都会返回非零，writer 保持停止" in readme


def test_production_rotation_mode_keeps_writers_stopped_through_primary_verification() -> None:
    script = DEPLOY_SCRIPT.read_text(encoding="utf-8")
    marker = 'if [[ "$UPGRADE_MODE" == "rotate-encryption-key" ]]'
    block = script[script.index(marker) : script.index("\nfi", script.index(marker))]

    dry_run = "python -m scripts.rotate_encryption_key --confirm-writers-stopped"
    apply = "python -m scripts.rotate_encryption_key --apply --confirm-writers-stopped"
    verify = "python -m scripts.rotate_encryption_key --verify-primary-only --confirm-writers-stopped"
    assert block.index(dry_run) < block.index(apply) < block.index(verify)
    assert 'up -d --wait --wait-timeout "$WAIT_TIMEOUT"' not in block.split(verify, maxsplit=1)[1]
