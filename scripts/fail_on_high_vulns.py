#!/usr/bin/env python3
"""Fail CI when pip-audit or bandit reports vulnerabilities.

Usage:
    python scripts/fail_on_high_vulns.py <report.json> [--tool pip-audit|bandit]

Behaviour:
    - pip-audit (default): exit 1 if the report contains ANY vulnerability.
      Rationale: pip-audit 2.10.x JSON schema no longer emits a ``severity``
      field per vuln (only ``id`` / ``fix_versions`` / optional ``aliases`` /
      ``description``). Filtering by HIGH/CRITICAL would silently pass every
      real CVE. The safest default is fail-on-any; suppress false positives
      via an explicit allow-list (``PIP_AUDIT_IGNORE`` env var, comma-sep IDs)
      rather than by pretending "no severity" means "not severe".
    - bandit: exit 1 if any issue is severity HIGH and confidence HIGH.

Fail-closed on missing / unparsable report: if the scanner crashed and the
JSON file was never written, this script MUST exit non-zero. Otherwise a
scanner failure paired with ``|| true`` in CI is indistinguishable from a
clean scan.

Always prints a human-readable summary regardless of outcome so the CI log
is self-explanatory.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def _load(path: Path) -> Any:
    """Load a scanner JSON report. Fail-closed on missing/invalid file.

    Returning None here (as the old version did) let CI green-light runs
    where the scanner itself had crashed — the exact fail-open we want to
    avoid.
    """
    if not path.exists():
        print(f"[fail] report file not found: {path}")
        print("       scanner likely crashed; treating as failure (fail-closed)")
        sys.exit(1)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[fail] failed to parse {path}: {exc}")
        sys.exit(1)


def _pip_audit_ignore_ids() -> set[str]:
    """Read PIP_AUDIT_IGNORE env var (comma-separated vuln IDs)."""
    raw = os.environ.get("PIP_AUDIT_IGNORE", "").strip()
    if not raw:
        return set()
    return {token.strip() for token in raw.split(",") if token.strip()}


def _check_pip_audit(report: Any) -> list[str]:
    """pip-audit --format=json schema: {'dependencies': [{...}]}.

    New (2.10.x) schema drops per-vuln ``severity``; any listed vuln is a
    real finding. Suppress noise via the ``PIP_AUDIT_IGNORE`` env var
    (comma-separated GHSA-/PYSEC- IDs) instead of trusting a missing field.
    """
    failures: list[str] = []
    if not isinstance(report, dict) or not isinstance(report.get("dependencies"), list):
        return ["  - malformed pip-audit report: missing dependencies list"]
    ignored = _pip_audit_ignore_ids()
    deps = report.get("dependencies") or []
    for dep in deps:
        name = dep.get("name", "?")
        version = dep.get("version", "?")
        for vuln in dep.get("vulns") or []:
            vuln_id = vuln.get("id") or ""
            aliases = vuln.get("aliases") or []
            all_ids = {vuln_id, *aliases}
            if ignored & all_ids:
                # explicitly allow-listed, skip
                continue
            display_id = vuln_id or (aliases[0] if aliases else "?")
            fix_versions = vuln.get("fix_versions") or []
            fix_hint = f" (fix: {', '.join(fix_versions)})" if fix_versions else ""
            failures.append(f"  - {name}=={version}: {display_id}{fix_hint}")
    return failures


def _check_bandit(report: Any) -> list[str]:
    """bandit -f json schema: {'results': [{issue_severity, issue_confidence, ...}]}."""
    failures: list[str] = []
    if not isinstance(report, dict) or not isinstance(report.get("results"), list):
        return ["  - malformed bandit report: missing results list"]
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

    if args.tool == "pip-audit":
        failures = _check_pip_audit(report)
        singular, plural = "vulnerability", "vulnerabilities"
    else:
        failures = _check_bandit(report)
        singular, plural = "HIGH/HIGH finding", "HIGH/HIGH findings"

    if not failures:
        print(f"[ok] {args.tool}: no {plural} in {args.report}")
        return 0

    label = singular if len(failures) == 1 else plural
    print(f"[fail] {args.tool}: {len(failures)} {label}:")
    for line in failures:
        print(line)
    return 1


if __name__ == "__main__":
    sys.exit(main())
