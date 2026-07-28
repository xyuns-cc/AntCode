"""Contract tests for the fail-closed npm audit gate."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_npm_audit.mjs"
POLICY_DATE = "2026-07-27"


def _run(tmp_path: Path, report: dict, policy_date: str = POLICY_DATE) -> subprocess.CompletedProcess[str]:
    report_path = tmp_path / "npm-audit.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return subprocess.run(
        ["node", str(SCRIPT), str(report_path), policy_date],
        capture_output=True,
        text=True,
        check=False,
    )


def _advisory() -> dict:
    return {
        "source": 1111111,
        "name": "react-router",
        "dependency": "react-router",
        "title": "CSRF in React Router RSC APIs",
        "url": "https://github.com/advisories/GHSA-qwww-vcr4-c8h2",
        "severity": "high",
        "range": ">=7.0.0",
    }


def _report(vulnerabilities: dict) -> dict:
    return {
        "auditReportVersion": 2,
        "vulnerabilities": vulnerabilities,
        "metadata": {"vulnerabilities": {"high": len(vulnerabilities)}},
    }


def _allowed_vulnerabilities() -> dict:
    return {
        "react-router": {
            "name": "react-router",
            "severity": "high",
            "isDirect": False,
            "via": [_advisory()],
            "effects": ["react-router-dom"],
        },
        "react-router-dom": {
            "name": "react-router-dom",
            "severity": "high",
            "isDirect": True,
            "via": ["react-router"],
            "effects": [],
        },
    }


def test_clean_report_passes(tmp_path: Path) -> None:
    result = _run(tmp_path, _report({}))

    assert result.returncode == 0
    assert "no unapproved" in result.stdout


def test_exact_rsc_advisory_and_derived_package_pass(tmp_path: Path) -> None:
    result = _run(tmp_path, _report(_allowed_vulnerabilities()))

    assert result.returncode == 0
    assert "GHSA-qwww-vcr4-c8h2" in result.stdout


def test_direct_advisory_on_derived_package_passes(tmp_path: Path) -> None:
    vulnerabilities = _allowed_vulnerabilities()
    vulnerabilities["react-router-dom"]["via"] = [_advisory()]

    assert _run(tmp_path, _report(vulnerabilities)).returncode == 0


def test_unknown_high_fails(tmp_path: Path) -> None:
    vulnerabilities = _allowed_vulnerabilities()
    vulnerabilities["unknown"] = {
        "name": "unknown",
        "severity": "high",
        "via": [{**_advisory(), "url": "https://github.com/advisories/GHSA-unknown"}],
    }

    result = _run(tmp_path, _report(vulnerabilities))

    assert result.returncode == 1
    assert "unknown: HIGH" in result.stderr
    assert "::error title=Unapproved npm audit findings::" in result.stderr


def test_extra_advisory_on_allowed_package_fails(tmp_path: Path) -> None:
    vulnerabilities = _allowed_vulnerabilities()
    vulnerabilities["react-router"]["via"].append({**_advisory(), "url": "https://example.invalid/other"})

    assert _run(tmp_path, _report(vulnerabilities)).returncode == 1


def test_derived_package_with_another_source_fails(tmp_path: Path) -> None:
    vulnerabilities = _allowed_vulnerabilities()
    vulnerabilities["react-router-dom"]["via"].append("another-package")

    assert _run(tmp_path, _report(vulnerabilities)).returncode == 1


def test_policy_expiry_fails_even_for_exact_advisory(tmp_path: Path) -> None:
    result = _run(tmp_path, _report(_allowed_vulnerabilities()), "2026-09-01")

    assert result.returncode == 1
    assert "exception expired" in result.stderr


def test_low_and_moderate_findings_do_not_block_high_gate(tmp_path: Path) -> None:
    vulnerabilities = {
        "low-package": {"name": "low-package", "severity": "low", "via": []},
        "moderate-package": {"name": "moderate-package", "severity": "moderate", "via": []},
    }

    assert _run(tmp_path, _report(vulnerabilities)).returncode == 0


def test_malformed_or_network_error_report_fails_closed(tmp_path: Path) -> None:
    result = _run(tmp_path, {"message": "audit endpoint returned an error", "error": {}})

    assert result.returncode == 1
    assert "malformed npm audit report" in result.stderr
