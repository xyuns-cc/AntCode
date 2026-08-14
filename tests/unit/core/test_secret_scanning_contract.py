import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
GITLEAKS_IGNORE_PATH = ROOT / ".gitleaksignore"
IGNORE_PATHS = (
    ROOT / ".gitignore",
    ROOT / ".dockerignore",
    ROOT / "infra" / "docker" / "Dockerfile.test.dockerignore",
    ROOT / "web" / "antcode-frontend" / ".dockerignore",
)
REQUIRED_CREDENTIAL_PATTERNS = frozenset(
    {
        "*.der",
        "*.kubeconfig",
        "*.ovpn",
        "*.pem",
        "*.key",
        "*.pkcs12",
        "**/.aws/",
        "**/.azure/",
        "**/.config/gcloud/",
        "**/.docker/config.json",
        "**/.kube/config",
        "**/.ssh/",
        ".authinfo",
        ".git-credentials",
        ".my.cnf",
        ".netrc",
        ".pypirc",
        ".pgpass",
        ".npmrc",
        ".vault-token",
        "*credentials*.json",
        "auth.json",
        "pip.conf",
        "pip.ini",
        "secret*.json",
        "secrets.yaml",
        "secrets.yml",
        "service-account*.json",
        "*.tfstate",
        "*.tfvars",
    }
)


def test_git_and_docker_contexts_ignore_credential_files() -> None:
    for path in IGNORE_PATHS:
        patterns = {
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        assert REQUIRED_CREDENTIAL_PATTERNS <= patterns, path


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
