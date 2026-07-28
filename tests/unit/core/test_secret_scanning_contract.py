import re
from pathlib import Path
from urllib.parse import urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[3]
CI_PATH = ROOT / ".github" / "workflows" / "ci.yml"
SECURITY_PATH = ROOT / ".github" / "workflows" / "security-scan.yml"
GITLEAKS_IGNORE_PATH = ROOT / ".gitleaksignore"
IGNORE_PATHS = (
    ROOT / ".gitignore",
    ROOT / ".dockerignore",
    ROOT / "infra" / "docker" / "Dockerfile.test.dockerignore",
    ROOT / "web" / "antcode-frontend" / ".dockerignore",
)
REQUIRED_CREDENTIAL_PATTERNS = frozenset(
    {
        "*.pem",
        "*.key",
        ".netrc",
        ".pypirc",
        ".npmrc",
        "*credentials*.json",
        "service-account*.json",
        "*.tfstate",
        "*.tfvars",
    }
)
FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{40}:[^:]+:[a-z0-9-]+:[1-9][0-9]*$")


def _workflow(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _step(job: dict, name: str) -> dict:
    return next(step for step in job["steps"] if step.get("name") == name)


def test_security_workflow_scans_git_history_with_pinned_gitleaks() -> None:
    job = _workflow(SECURITY_PATH)["jobs"]["gitleaks"]
    checkout = _step(job, "Checkout full repository history")
    scanner = _step(job, "Scan committed secrets")

    assert checkout["with"]["fetch-depth"] == 0
    action, revision = scanner["uses"].split("@", maxsplit=1)
    assert action == "gitleaks/gitleaks-action"
    assert re.fullmatch(r"[0-9a-f]{40}", revision)
    assert scanner["env"]["GITLEAKS_VERSION"] == "8.24.3"
    assert scanner["env"]["GITLEAKS_ENABLE_UPLOAD_ARTIFACT"] == "false"


def test_ci_does_not_commit_fixed_database_or_admin_passwords() -> None:
    jobs = _workflow(CI_PATH)["jobs"]
    backend = jobs["backend-test"]
    postgres = backend["services"]["postgres"]
    integration = _step(backend, "Run integration tests")
    generate = _step(jobs["e2e"], "Generate ephemeral stack secrets")["run"]

    assert postgres["env"]["POSTGRES_HOST_AUTH_METHOD"] == "trust"
    assert "POSTGRES_PASSWORD" not in postgres["env"]
    database_urls = (
        backend["env"]["DATABASE_URL"],
        integration["env"]["DATABASE_URL"],
        integration["env"]["TEST_DATABASE_URL"],
    )
    assert all(urlsplit(str(url)).password is None for url in database_urls)
    assert "env" not in _step(backend, "Prepare integration databases")
    assert 'admin_password="$(openssl rand -base64 24' in generate
    assert 'worker_key="$(openssl rand -hex 32)"' in generate
    assert 'echo "ANTCODE_WORKER_KEY=$worker_key"' in generate
    for variable in (
        "pg_password",
        "redis_password",
        "jwt_secret",
        "encryption_key",
        "encryption_salt",
        "admin_password",
        "worker_key",
    ):
        assert f'"${variable}"' in generate
    assert 'echo "::add-mask::$secret"' in generate


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


def test_gitleaks_exceptions_are_exact_historical_fingerprints() -> None:
    entries = [
        line
        for line in GITLEAKS_IGNORE_PATH.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]

    assert entries
    assert len(entries) == len(set(entries))
    assert all(FINGERPRINT_PATTERN.fullmatch(entry) for entry in entries)
