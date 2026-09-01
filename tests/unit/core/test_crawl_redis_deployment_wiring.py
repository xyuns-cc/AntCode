from pathlib import Path

from tests.unit.core.test_docker_compose_prod_contract import _compose, _script

DEPLOY_SCRIPT = Path("infra/docker/deploy-production.sh")
WEB_API_DOCKERFILE = Path("infra/docker/Dockerfile.web_api")
DEV_COMPOSE = Path("infra/docker/docker-compose.dev.yml")


def test_crawl_redis_upgrade_uses_candidate_image_and_minimum_secret() -> None:
    services = _compose()["services"]
    upgrade = services["crawl-redis-upgrade"]

    assert upgrade["image"] == services["migration"]["image"]
    assert upgrade["profiles"] == ["crawl-redis-upgrade"]
    assert upgrade["restart"] == "no"
    assert upgrade["read_only"] is True
    assert upgrade["cap_drop"] == ["ALL"]
    assert upgrade["security_opt"] == ["no-new-privileges:true"]
    assert upgrade["secrets"] == ["redis_url"]
    assert upgrade["environment"] == {
        "REDIS_URL_FILE": "/run/secrets/redis_url",
        "REDIS_NAMESPACE": "${REDIS_NAMESPACE:?REDIS_NAMESPACE is required}",
    }
    assert upgrade["networks"] == ["antcode-control"]


def test_development_upgrade_profile_uses_the_runtime_image_contract() -> None:
    upgrade = _compose(DEV_COMPOSE)["services"]["crawl-redis-upgrade"]

    assert upgrade["build"]["dockerfile"] == "infra/docker/Dockerfile.web_api"
    assert upgrade["profiles"] == ["crawl-redis-upgrade"]
    assert upgrade["read_only"] is True
    assert upgrade["command"] == ["python", "-m", "scripts.migrate_crawl_redis"]
    assert "${REDIS_NAMESPACE:?" in upgrade["environment"]["REDIS_NAMESPACE"]


def test_crawl_redis_upgrade_modules_are_in_runtime_image() -> None:
    dockerfile = _script(WEB_API_DOCKERFILE)

    for module in (
        "crawl_redis_upgrade_contract.py",
        "crawl_redis_upgrade_execution.py",
        "crawl_redis_upgrade_scan.py",
        "migrate_crawl_redis.py",
    ):
        assert f"scripts/{module}" in dockerfile


def test_deploy_script_stops_writers_before_the_read_only_redis_gate() -> None:
    deploy = _script(DEPLOY_SCRIPT)
    upgrade_run = "run --rm --no-deps crawl-redis-upgrade"

    # 停服集合与依赖图一致：边缘两层的 healthcheck 是穿透式链路探针，控制面一停它们
    # 必然 unhealthy，而 compose 的 `--wait` 对 unhealthy 是快速失败——留着不停会让
    # 部署在收尾那步稳定退出 1（真机实测）。
    assert "readonly STOPPED_SERVICES=(reverse-proxy frontend web-api master gateway worker)" in deploy
    assert 'stop --timeout "$STOP_TIMEOUT" "${STOPPED_SERVICES[@]}"' in deploy
    assert deploy.index('stop --timeout "$STOP_TIMEOUT"') < deploy.index(upgrade_run)
    # 门禁没有写开关；URL / namespace 由生产部署自己拥有，命令行一律拒绝覆盖。
    assert "unsupported argument" in deploy
    assert "rotate-encryption-key requires --confirm-writers-stopped" in deploy
    assert "writers remain stopped" in deploy
    assert deploy.index(upgrade_run) < deploy.rindex("up -d --wait --wait-timeout")


def test_crawl_redis_deployment_readme_documents_the_guarded_entrypoint() -> None:
    deployment = _script(Path("infra/docker/README.md"))

    assert "deploy-production.sh .env.production fresh-deploy" in deployment
    assert "--confirm-writers-stopped" in deployment
    assert "存量 Redis 不受支持" in deployment
    assert "writer" in deployment
    docker_environment = _script(Path("infra/docker/.env.example"))
    assert "REDIS_NAMESPACE=antcode" in docker_environment
