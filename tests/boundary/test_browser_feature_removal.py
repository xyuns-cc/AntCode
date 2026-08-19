from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = [
    REPO_ROOT / "infra",
    REPO_ROOT / "packages",
    REPO_ROOT / "services",
    REPO_ROOT / "tests",
    REPO_ROOT / "web" / "antcode-frontend" / "src",
]
TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".md"}
DISALLOWED_PATTERNS = {
    'engine="browser"': "禁止重新引入 browser 规则引擎",
    "browser_config": "禁止重新引入 browser_config",
    "drissionpage": "禁止重新引入 DrissionPage 能力",
    "legacy_inline": "禁止重新引入 legacy_inline 代码来源",
}
ALLOWED_MATCH_FILES = {
    "tests/unit/test_browser_feature_removal.py",
    "tests/unit/web_api/test_project_browser_removal.py",
    "tests/unit/web_api/test_workers_security.py",
    "tests/unit/worker/test_render_plugin_removal.py",
    "tests/integration/worker/test_plugin_system_checkpoint.py",
    "tests/boundary/test_browser_feature_removal.py",
}


def test_browser_feature_residues_are_removed() -> None:
    violations: list[str] = []

    for scan_dir in SCAN_DIRS:
        if not scan_dir.exists():
            continue

        for file_path in scan_dir.rglob("*"):
            if not file_path.is_file() or file_path.suffix not in TEXT_SUFFIXES:
                continue
            if "migrations" in file_path.parts:
                continue

            rel_path = file_path.relative_to(REPO_ROOT).as_posix()
            if rel_path in ALLOWED_MATCH_FILES:
                continue

            content = file_path.read_text(encoding="utf-8", errors="ignore")
            for token, reason in DISALLOWED_PATTERNS.items():
                if token not in content:
                    continue
                for line_no, line in enumerate(content.splitlines(), start=1):
                    if token in line:
                        violations.append(f"{rel_path}:{line_no} 命中 `{token}`（{reason}）")

    assert not violations, "\n".join(violations)
