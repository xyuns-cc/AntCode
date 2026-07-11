from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = [
    REPO_ROOT / "packages",
    REPO_ROOT / "services",
    REPO_ROOT / "web" / "antcode-frontend" / "src",
]
TEXT_SUFFIXES = {".py", ".ts", ".tsx", ".yaml", ".yml", ".proto"}
DISALLOWED_TOKENS = {
    "Scraper-Node-Default-01": "默认 Worker ID 命名必须使用 Worker 口径",
    '"Node-001"': "示例 Worker 名称必须使用 Worker 口径",
    'name: "Worker-Node"': "Worker 示例配置名称必须使用 Worker 口径",
}


def test_worker_term_consistency() -> None:
    violations: list[str] = []

    for scan_dir in SCAN_DIRS:
        if not scan_dir.exists():
            continue

        for file_path in scan_dir.rglob("*"):
            if not file_path.is_file() or file_path.suffix not in TEXT_SUFFIXES:
                continue
            if "migrations" in file_path.parts:
                continue

            content = file_path.read_text(encoding="utf-8", errors="ignore")
            for token, reason in DISALLOWED_TOKENS.items():
                if token not in content:
                    continue

                for line_no, line in enumerate(content.splitlines(), start=1):
                    if token in line:
                        rel_path = file_path.relative_to(REPO_ROOT)
                        violations.append(f"{rel_path}:{line_no} 命中 `{token}`（{reason}）")

    assert not violations, "\n".join(violations)
