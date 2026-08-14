from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).parents[3]


def _read(path: str) -> str:
    target = REPO_ROOT / path
    if not target.exists():
        return ""
    return target.read_text(encoding="utf-8")


def test_runtime_contract_exposes_only_registered_mise_interpreters():
    assert not (REPO_ROOT / "services/web_api/src/antcode_web_api/routes/v1/envs.py").exists()

    checks = {
        "packages/antcode_core/src/antcode_core/domain/models/enums.py": ("InterpreterSource",),
        "packages/antcode_core/src/antcode_core/domain/models/runtime.py": (
            "class Interpreter",
            'table = "interpreters"',
            "interpreter_id",
            "InterpreterSource",
            "python_bin",
            "install_dir",
        ),
        "packages/antcode_core/src/antcode_core/domain/models/__init__.py": (
            "InterpreterSource",
            "Interpreter,",
            '"Interpreter"',
        ),
        "packages/antcode_core/src/antcode_core/domain/schemas/runtime.py": (
            "InterpreterInfo",
            "python_bin",
            "install_dir",
            "interpreter_source: str = Field",
            'python_bin: str = Field("", description="当来源为local时的python路径")',
            "mise/local",
            "来源为local",
        ),
        "packages/antcode_core/src/antcode_core/domain/schemas/__init__.py": ("InterpreterInfo",),
        "packages/antcode_core/src/antcode_core/domain/schemas/project.py": (
            "interpreter_source:",
            "python_bin:",
            "mise/local",
            "来源为local",
        ),
        "services/web_api/src/antcode_web_api/routes/v1/project.py": (
            "interpreter_source",
            "python_bin",
        ),
        "web/antcode-frontend/src/types/project.ts": (
            "worker/local",
            "interpreter_source",
            "python_bin",
        ),
        "web/antcode-frontend/src/services/envs.ts": (
            "interpreter_source?:",
            "python_bin?:",
            "createSharedVenv",
            "listVenvs",
        ),
        "web/antcode-frontend/src/pages/Envs/index.tsx": ("source: 'local'",),
        "web/antcode-frontend/src/config/displayConfig.ts": (
            "local:",
            "system:",
            "pyenv-win",
        ),
    }

    offenders = [f"{path}: {token}" for path, tokens in checks.items() for token in tokens if token in _read(path)]

    assert offenders == []


def test_frontend_environment_page_has_no_local_runtime_management():
    files = [
        "web/antcode-frontend/src/pages/Envs/index.tsx",
        "web/antcode-frontend/src/pages/Envs/components/CreateVenvDrawer.tsx",
        "web/antcode-frontend/src/pages/Envs/components/EditVenvKeyModal.tsx",
        "web/antcode-frontend/src/pages/Envs/components/InstallPackagesButton.tsx",
        "web/antcode-frontend/src/pages/Envs/components/InstallPackagesModal.tsx",
    ]
    banned_tokens = (
        "envService.listVenvs",
        "envService.listInterpreters",
        "envService.createSharedVenv",
        "envService.updateSharedVenv",
        "envService.installPackagesToVenv",
        "envService.deleteVenv",
        "envService.batchDeleteVenvs",
        "listVenvPackagesById",
        "isLocal",
        "本地环境",
        "本地",
        "return []",
        "setAllItems([])",
        "setInstalledInterpreters([])",
        "listInterpreters",
        "interpreterColumns",
        "interpreterRowKey",
        "解释器",
        "可执行文件",
        "安装目录",
    )

    offenders = []
    for path in files:
        if not (REPO_ROOT / path).exists():
            continue
        source = _read(path)
        for token in banned_tokens:
            if token in source:
                offenders.append(f"{path}: {token}")

    assert offenders == []


def test_worker_startup_uses_only_current_worker_env_names():
    checks = {
        "services/worker/src/antcode_worker/config.py": (
            '"TRANSPORT_MODE"',
            '"ANTCODE_TRANSPORT_MODE"',
            '"API_BASE_URL"',
            '"GATEWAY_ENDPOINT"',
            '"GATEWAY_HOST"',
            '"GATEWAY_PORT"',
            '"MAX_CONCURRENT_TASKS"',
            '"WORKER_KEY"',
        ),
        "services/worker/src/antcode_worker/cli.py": (
            '"ANTCODE_REDIS_URL"',
            '"ANTCODE_GATEWAY_ENDPOINT"',
            '"ANTCODE_GATEWAY_HOST"',
            '"ANTCODE_GATEWAY_PORT"',
            '"WORKER_KEY"',
        ),
        "services/worker/src/antcode_worker/app/wiring.py": (
            '"WORKER_KEY"',
            '"ANTCODE_API_BASE_URL"',
            '"API_BASE_URL"',
        ),
        "services/web_api/src/antcode_web_api/routes/v1/workers.py": ("ANTCODE_API_BASE_URL",),
        "scripts/run_worker.sh": (
            'TRANSPORT_MODE="${TRANSPORT_MODE',
            "export TRANSPORT_MODE",
            '"  TRANSPORT_MODE',
            "默认: direct",
        ),
        "services/worker/config/worker.example.yaml": (
            "file 或 env",
            'credential_store: "file"',
        ),
        "services/worker/README.md": ("默认 `direct`",),
    }

    offenders = [f"{path}: {token}" for path, tokens in checks.items() for token in tokens if token in _read(path)]

    assert offenders == []


def test_removed_migration_compatibility_scripts_and_cli_are_absent():
    removed_scripts = [
        "scripts/migrate_local_strategy.py",
        "scripts/apply_transport_mode_migration.py",
        "scripts/apply_worker_install_keys_migration.py",
        "scripts/migrate_imports.py",
        "scripts/fix_web_api_imports.py",
    ]
    for path in removed_scripts:
        assert not (REPO_ROOT / path).exists()

    db_migrate = _read("scripts/db_migrate.py")
    assert '"migrate"' not in db_migrate
    assert "--name" not in db_migrate
    assert "--delete" not in db_migrate
    assert "命令已移除" not in db_migrate


def test_worker_flow_control_rejects_invalid_strategy_without_fallback():
    from antcode_worker.app.engine_wiring import _create_flow_controller

    config = SimpleNamespace(flow_control_enabled=True, flow_control_strategy="unsupported")
    with pytest.raises(ValueError):
        _create_flow_controller(config)


def test_aerich_migration_tooling_is_removed():
    checks = {
        "pyproject.toml": (
            "aerich",
            "[tool.aerich]",
        ),
        "packages/antcode_core/src/antcode_core/infrastructure/db/tortoise.py": (
            "include_aerich",
            "aerich.models",
            "Aerich",
        ),
        "uv.lock": ('name = "aerich"',),
    }

    offenders = [f"{path}: {token}" for path, tokens in checks.items() for token in tokens if token in _read(path)]

    assert offenders == []


def test_frontend_environment_loading_does_not_swallow_errors():
    checks = {
        "web/antcode-frontend/src/components/runtimes/EnvSelector.tsx": (".catch(() => setEnvOptions([]))",),
        "web/antcode-frontend/src/components/projects/ProjectCreateDrawer.tsx": (".catch(() => setWorkerList([]))",),
    }

    offenders = [f"{path}: {token}" for path, tokens in checks.items() for token in tokens if token in _read(path)]

    assert offenders == []


def test_frontend_data_loading_does_not_degrade_to_empty_data():
    checks = {
        "web/antcode-frontend/src/pages/Dashboard/index.tsx": (
            "getAggregateStats().catch(() => null)",
            "getClusterSpiderStats().catch(() => null)",
            "getHourlyTrend().catch(() => [])",
        ),
        "web/antcode-frontend/src/pages/Monitor/index.tsx": (
            "getAggregateStats().catch(() => null)",
            "静默失败",
            "setWorkerHistory([])",
        ),
        "web/antcode-frontend/src/components/workers/SpiderStatsTab.tsx": (".catch(() => setHistoryData([]))",),
        "web/antcode-frontend/src/pages/Projects/ProjectList.tsx": ("错误由拦截器处理",),
        "web/antcode-frontend/src/pages/Tasks/TaskEdit.tsx": ("setProjects([])",),
    }

    offenders = [f"{path}: {token}" for path, tokens in checks.items() for token in tokens if token in _read(path)]

    assert offenders == []

    project_list_source = _read("web/antcode-frontend/src/pages/Projects/ProjectList.tsx")
    fetch_projects_source = project_list_source[project_list_source.index("const fetchProjects") :]
    fetch_projects_source = fetch_projects_source.split(
        "\n  // 同步分页信息到 store",
        1,
    )[0]

    assert "setAllProjects([])" not in fetch_projects_source
    assert "setProjects([])" not in fetch_projects_source


def test_scheduler_has_no_local_execution_state_path():
    checks = {
        "services/master/src/antcode_master/loops/scheduler_loop.py": ("local_execution",),
        "packages/antcode_core/src/antcode_core/application/services/scheduler/scheduler_service.py": (
            "local_execution",
        ),
    }

    offenders = [f"{path}: {token}" for path, tokens in checks.items() for token in tokens if token in _read(path)]

    assert offenders == []


def test_core_spider_dispatcher_requires_current_rule_detail_contract():
    source = _read("packages/antcode_core/src/antcode_core/application/services/scheduler/spider_dispatcher.py")
    function_source = source[source.index("def _serialize_rule_detail") :]

    assert "to_dispatch_dict" in function_source
    assert "raise TypeError" in function_source
    assert "target_url" not in function_source
    assert "callback_type" not in function_source
    assert "request_method" not in function_source


def test_refactored_runtime_and_project_detail_files_stay_focused():
    files = (
        "services/web_api/src/antcode_web_api/routes/v1/runtimes.py",
        "services/web_api/src/antcode_web_api/routes/v1/runtime_models.py",
        "services/web_api/src/antcode_web_api/routes/v1/runtime_access.py",
        "web/antcode-frontend/src/pages/Projects/ProjectDetail.tsx",
        "web/antcode-frontend/src/pages/Projects/ProjectDetailCards.tsx",
    )

    offenders = []
    for path in files:
        line_count = len(_read(path).splitlines())
        if line_count > 300:
            offenders.append(f"{path}: {line_count}")

    assert offenders == []
