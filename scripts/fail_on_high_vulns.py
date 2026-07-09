#!/usr/bin/env python3
"""Fail CI when pip-audit or bandit reports HIGH/CRITICAL severity findings.

Usage:
    python scripts/fail_on_high_vulns.py <report.json> [--tool pip-audit|bandit]

Behaviour:
    - pip-audit (default): exit 1 if any dependency vuln is HIGH or CRITICAL
    - bandit: exit 1 if any issue is severity HIGH and confidence HIGH

Always prints a human-readable summary regardless of outcome so the CI log
is self-explanatory.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


HIGH_SEVERITIES = {"HIGH", "CRITICAL"}


def _load(path: Path) -> Any:
    if not path.exists():
        print(f"[skip] report file not found: {path}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[error] failed to parse {path}: {exc}")
        sys.exit(2)


def _check_pip_audit(report: Any) -> list[str]:
    """pip-audit --format=json schema: {'dependencies': [{...}]}"""
    failures: list[str] = []
    if not isinstance(report, dict):
        return failures
    deps = report.get("dependencies") or []
    for dep in deps:
        name = dep.get("name", "?")
        version = dep.get("version", "?")
        for vuln in dep.get("vulns") or []:
            severity = (vuln.get("severity") or "").upper()
            vuln_id = vuln.get("id") or vuln.get("aliases", ["?"])[0]
            if severity in HIGH_SEVERITIES:
                failures.append(
                    f"  - {name}=={version}: {vuln_id} ({severity})"
                )
    return failures


def _check_bandit(report: Any) -> list[str]:
    """bandit -f json schema: {'results': [{issue_severity, issue_confidence, ...}]}"""
    failures: list[str] = []
    if not isinstance(report, dict):
        return failures
    for issue in report.get("results") or []:
        sev = (issue.get("issue_severity") or "").upper()
        conf = (issue.get("issue_confidence") or "").upper()
        if sev == "HIGH" and conf == "HIGH":
            failures.append(
                "  - {test}: {file}:{line} ({sev}/{conf}) {msg}".format(
                    test=issue.get("test_id"),
                    file=issue.get("filename"),
                    line=issue.get("line_number"),
                    sev=sev,
                    conf=conf,
                    msg=(issue.get("issue_text") or "").strip(),
                )
            )
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path, help="Path to scanner JSON report")
    parser.add_argument(
        "--tool",
        choices=("pip-audit", "bandit"),
        default="pip-audit",
        help="Which scanner produced the report (default: pip-audit)",
    )
    args = parser.parse_args()

    report = _load(args.report)
    if report is None:
        return 0

    if args.tool == "pip-audit":
        failures = _check_pip_audit(report)
    else:
        failures = _check_bandit(report)

    if not failures:
        print(f"[ok] {args.tool}: no HIGH/CRITICAL findings in {args.report}")
        return 0

    print(f"[fail] {args.tool}: {len(failures)} HIGH/CRITICAL finding(s):")
    for line in failures:
        print(line)
    return 1


if __name__ == "__main__":
    sys.exit(main())
