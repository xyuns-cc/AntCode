import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MARKER_NAME = "ANTCODE_DB_IMPORT_ENV_MARKER"


def test_importing_database_module_does_not_mutate_process_environment(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(f"{MARKER_NAME}=must-not-load\n", encoding="utf-8")
    environment = os.environ.copy()
    environment.pop(MARKER_NAME, None)
    pythonpath = str(ROOT / "packages" / "antcode_core" / "src")
    environment["PYTHONPATH"] = os.pathsep.join(filter(None, (pythonpath, environment.get("PYTHONPATH"))))
    command = f"import os; import antcode_core.infrastructure.db.tortoise; assert {MARKER_NAME!r} not in os.environ"

    result = subprocess.run(
        [sys.executable, "-c", command],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
