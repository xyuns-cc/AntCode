"""Unit coverage for the CI gating script."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "fail_on_high_vulns.py"


def _run(report_path: Path, tool: str = "pip-audit") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(report_path), "--tool", tool],
        capture_output=True,
        text=True,
        check=False,
    )


def _write(tmp_path: Path, name: str, payload: dict) -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_pip_audit_clean_report_exits_zero(tmp_path: Path) -> None:
    report = _write(
        tmp_path,
        "audit.json",
        {"dependencies": [{"name": "x", "version": "1.0", "vulns": []}]},
    )
    result = _run(report)
    assert result.returncode == 0
    assert "no vulnerabilities" in result.stdout


def test_pip_audit_high_severity_blocks(tmp_path: Path) -> None:
    report = _write(
        tmp_path,
        "audit.json",
        {
            "dependencies": [
                {
                    "name": "requests",
                    "version": "2.5.0",
                    "vulns": [{"id": "GHSA-x", "severity": "HIGH"}],
                }
            ]
        },
    )
    result = _run(report)
    assert result.returncode == 1
    assert "requests==2.5.0" in result.stdout
    assert "GHSA-x" in result.stdout


def test_pip_audit_critical_blocks(tmp_path: Path) -> None:
    report = _write(
        tmp_path,
        "audit.json",
        {
            "dependencies": [
                {
                    "name": "y",
                    "version": "0.1",
                    "vulns": [{"id": "CVE-1", "severity": "CRITICAL"}],
                }
            ]
        },
    )
    assert _run(report).returncode == 1


def test_pip_audit_fails_on_vulnerability_without_trusting_severity(tmp_path: Path) -> None:
    report = _write(
        tmp_path,
        "audit.json",
        {
            "dependencies": [
                {
                    "name": "y",
                    "version": "0.1",
                    "vulns": [{"id": "CVE-2", "severity": "MEDIUM"}],
                }
            ]
        },
    )
    assert _run(report).returncode == 1


def test_bandit_high_high_blocks(tmp_path: Path) -> None:
    report = _write(
        tmp_path,
        "bandit.json",
        {
            "results": [
                {
                    "test_id": "B101",
                    "filename": "a.py",
                    "line_number": 10,
                    "issue_severity": "HIGH",
                    "issue_confidence": "HIGH",
                    "issue_text": "use of assert",
                }
            ]
        },
    )
    result = _run(report, tool="bandit")
    assert result.returncode == 1
    assert "B101" in result.stdout


def test_bandit_low_confidence_ignored(tmp_path: Path) -> None:
    report = _write(
        tmp_path,
        "bandit.json",
        {
            "results": [
                {
                    "test_id": "B101",
                    "issue_severity": "HIGH",
                    "issue_confidence": "LOW",
                }
            ]
        },
    )
    assert _run(report, tool="bandit").returncode == 0


def test_missing_file_fails_closed(tmp_path: Path) -> None:
    result = _run(tmp_path / "does-not-exist.json")
    assert result.returncode == 1
    assert "fail-closed" in result.stdout.lower()


def test_malformed_pip_audit_report_fails_closed(tmp_path: Path) -> None:
    report = _write(tmp_path, "audit.json", {})

    result = _run(report)

    assert result.returncode == 1
    assert "malformed pip-audit report" in result.stdout


def test_malformed_bandit_report_fails_closed(tmp_path: Path) -> None:
    report = _write(tmp_path, "bandit.json", {})

    result = _run(report, tool="bandit")

    assert result.returncode == 1
    assert "malformed bandit report" in result.stdout
