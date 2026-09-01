import ast
import os
import stat
import subprocess
from pathlib import Path

SCRIPT = Path("infra/docker/verify-backup-restore.sh").resolve()
SCHEMA_CHECK = Path("infra/docker/verify-backup-schema.sql")
DATA_CHECK = Path("infra/docker/verify-backup-critical-data.sql")
INIT_DB = Path("scripts/init_db.py")
TEST_DATABASE = "antcode_restore_test_round10"
TEST_DATABASE_URL = f"postgresql://restore@localhost/{TEST_DATABASE}"


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _fake_toolchain(tmp_path: Path) -> tuple[Path, Path]:
    binary_dir = tmp_path / "bin"
    binary_dir.mkdir()
    log_path = tmp_path / "commands.log"
    _write_executable(binary_dir / "timeout", '#!/bin/sh\nshift\nexec "$@"\n')
    _write_executable(binary_dir / "sha256sum", "#!/bin/sh\nexit 0\n")
    _write_executable(
        binary_dir / "psql",
        "#!/bin/sh\n"
        'case "$*" in\n'
        "  *'SELECT current_database()'*) printf '%s\\n' \"$FAKE_DATABASE\" ;;\n"
        "  *) cat >/dev/null; printf '%s\\n' '{\"critical_counts\":\"verified\"}' ;;\n"
        "esac\n",
    )
    _write_executable(binary_dir / "pg_restore", '#!/bin/sh\nprintf "pg_restore %s\\n" "$*" >> "$FAKE_LOG"\n')
    _write_executable(binary_dir / "migration", '#!/bin/sh\nprintf "migration %s\\n" "$DATABASE_URL" >> "$FAKE_LOG"\n')
    _write_executable(binary_dir / "curl", '#!/bin/sh\nprintf "curl %s\\n" "$*" >> "$FAKE_LOG"\n')
    return binary_dir, log_path


def _run_script(
    tmp_path: Path,
    *,
    expected_database: str = TEST_DATABASE,
    actual_database: str = TEST_DATABASE,
    include_migration: bool = True,
    readiness_url: str = "",
    checksum_name: str = "backup.dump",
) -> tuple[subprocess.CompletedProcess[str], Path]:
    binary_dir, log_path = _fake_toolchain(tmp_path)
    backup = tmp_path / "backup.dump"
    backup.write_bytes(b"custom-dump")
    checksum_hash = "0" * 64
    backup.with_suffix(".dump.sha256").write_text(f"{checksum_hash}  {checksum_name}\n", encoding="utf-8")
    args = [str(SCRIPT), str(backup), TEST_DATABASE_URL, expected_database]
    if include_migration:
        args.extend(["--", "migration"])
    environment = {
        "PATH": f"{binary_dir}:{os.environ['PATH']}",
        "FAKE_DATABASE": actual_database,
        "FAKE_LOG": str(log_path),
        "RESTORE_READINESS_URL": readiness_url,
    }
    result = subprocess.run(args, check=False, capture_output=True, text=True, env=environment)
    return result, log_path


def test_restore_refuses_non_test_database_name_before_destructive_command(tmp_path: Path) -> None:
    result, log_path = _run_script(tmp_path, expected_database="antcode")

    assert result.returncode != 0
    assert "target name must match" in result.stderr
    assert not log_path.exists()


def test_restore_refuses_url_that_resolves_to_another_database(tmp_path: Path) -> None:
    result, log_path = _run_script(tmp_path, actual_database="antcode_restore_test_wrong")

    assert result.returncode != 0
    assert "does not equal expected" in result.stderr
    assert not log_path.exists()


def test_restore_requires_explicit_real_migration_command(tmp_path: Path) -> None:
    result, log_path = _run_script(tmp_path, include_migration=False)

    assert result.returncode != 0
    assert "usage:" in result.stderr
    assert not log_path.exists()


def test_restore_refuses_checksum_for_another_file(tmp_path: Path) -> None:
    result, log_path = _run_script(tmp_path, checksum_name="different.dump")

    assert result.returncode != 0
    assert "not bound to the requested backup" in result.stderr
    assert not log_path.exists()


def test_database_restore_is_atomic_and_does_not_claim_application_readiness(tmp_path: Path) -> None:
    result, log_path = _run_script(tmp_path)

    assert result.returncode == 0, result.stderr
    commands = log_path.read_text(encoding="utf-8")
    assert "--clean" in commands
    assert "--exit-on-error" in commands
    assert "--single-transaction" in commands
    assert f"migration {TEST_DATABASE_URL}" in commands
    assert "application readiness NOT VERIFIED" in result.stdout


def test_readiness_probe_promotes_result_to_complete_restore_drill(tmp_path: Path) -> None:
    readiness_url = "http://127.0.0.1:18080/api/v1/health/ready"
    result, log_path = _run_script(tmp_path, readiness_url=readiness_url)

    assert result.returncode == 0, result.stderr
    commands = log_path.read_text(encoding="utf-8")
    assert readiness_url in commands
    assert "application readiness verified" in result.stdout


def test_post_restore_sql_covers_schema_security_and_critical_data() -> None:
    schema = SCHEMA_CHECK.read_text(encoding="utf-8")
    critical_data = DATA_CHECK.read_text(encoding="utf-8")

    assert "worker_heartbeats" in schema
    assert "task_run_lease_generations" in schema
    assert "indisvalid" in schema and "indisready" in schema
    assert "fk_scheduled_tasks_project_id" in schema
    assert "secret_key_encrypted" in schema
    assert "COUNT(DISTINCT config_key)" in critical_data
    assert "restored_critical_row_counts" in critical_data


def test_schema_postcheck_covers_every_runtime_required_table() -> None:
    module = ast.parse(INIT_DB.read_text(encoding="utf-8"))
    assignment = next(
        node
        for node in module.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "REQUIRED_TABLES"
    )
    required_tables = ast.literal_eval(assignment.value)
    schema = SCHEMA_CHECK.read_text(encoding="utf-8")

    for table_name in required_tables:
        assert f"'{table_name}'" in schema, table_name
