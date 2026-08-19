import subprocess
from pathlib import Path

from pathspec import GitIgnoreSpec

from tests.unit.core.dockerignore_support import CREDENTIAL_PROBE_PATHS

ROOT = Path(__file__).resolve().parents[3]
GITLEAKS_IGNORE_PATH = ROOT / ".gitleaksignore"


def test_gitignore_ignores_credential_files_at_every_depth() -> None:
    """判据是**行为**不是字面量。

    这里原先断言的是"这些模式字符串必须逐字出现在四份 ignore 文件里"，既证明不了任何
    路径真的被排除，又把 dockerignore 锁死在根锚定的坏形态上（`*.key` 只管 context 根）。
    改成对代表性路径求值；dockerignore 侧的同类判据在 test_dockerignore_semantics.py，
    那里用的是 Docker 语义的匹配器，不与本用例重复。
    """
    spec = GitIgnoreSpec.from_lines((ROOT / ".gitignore").read_text(encoding="utf-8").splitlines())

    leaked = [path for path in CREDENTIAL_PROBE_PATHS if not spec.match_file(path)]
    assert not leaked, f".gitignore 未挡住凭据：{leaked}"


def test_test_machine_compose_files_remain_outside_git() -> None:
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "infra/docker/docker-compose.remote*.yml" in ignore
    assert "!infra/docker/docker-compose.prod.local-backup.yml" in ignore


def test_gitignore_does_not_hide_nested_source_lib_directories() -> None:
    probe = "web/antcode-frontend/src/lib/round10-ignore-probe.ts"
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", probe],
        cwd=ROOT,
        check=False,
    )

    assert result.returncode == 1


def test_gitleaks_has_no_repository_exceptions() -> None:
    assert not GITLEAKS_IGNORE_PATH.exists()
