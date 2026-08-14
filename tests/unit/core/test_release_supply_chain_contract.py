from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
TRIVY_IGNORE_PATH = ROOT / ".trivyignore.yaml"
FRONTEND_SOURCE_PATH = ROOT / "web/antcode-frontend/src"


def test_trivy_rsc_advisory_exception_is_narrow_and_expires() -> None:
    policy = yaml.safe_load(TRIVY_IGNORE_PATH.read_text(encoding="utf-8"))
    assert policy == {
        "vulnerabilities": [
            {
                "id": "GHSA-qwww-vcr4-c8h2",
                "paths": ["web/antcode-frontend/package-lock.json"],
                "expired_at": date(2026, 8, 31),
                "statement": (
                    "Not affected: AntCode is a React 19 SPA using react-router 8 browser APIs. "
                    "The advisory explicitly affects only unstable React Server Components APIs, "
                    "which are not imported or enabled. Re-evaluate before this exception expires."
                ),
            }
        ]
    }
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in FRONTEND_SOURCE_PATH.rglob("*") if path.suffix in {".ts", ".tsx"}
    )
    assert "react-server" not in source
    assert "unstable_RSC" not in source
    assert "RSCHydratedRouter" not in source
    assert "RSCStaticRouter" not in source
