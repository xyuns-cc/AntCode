from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.complexity_analysis import (
    ROOT,
    SOURCE_SCOPE,
    THRESHOLDS,
    Finding,
    collect_current_findings,
)

BASELINE_PATH = ROOT / "scripts/complexity_baseline.json"


@dataclass(frozen=True)
class Comparison:
    added: tuple[Finding, ...]
    worsened: tuple[tuple[Finding, Finding], ...]
    improved: tuple[tuple[Finding, Finding], ...]
    resolved: tuple[Finding, ...]

    @property
    def changed(self) -> bool:
        return any((self.added, self.worsened, self.improved, self.resolved))


def load_baseline(path: Path = BASELINE_PATH) -> tuple[Finding, ...]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != 1:
        raise ValueError("Unsupported complexity baseline version")
    if tuple(payload.get("scope", ())) != SOURCE_SCOPE:
        raise ValueError("Complexity baseline scope does not match the checker")
    if payload.get("thresholds") != dict(THRESHOLDS):
        raise ValueError("Complexity baseline thresholds do not match the checker")
    findings = tuple(Finding(**item) for item in payload.get("violations", ()))
    if len({finding.key for finding in findings}) != len(findings):
        raise ValueError("Complexity baseline contains duplicate identifiers")
    if any(finding.rule not in THRESHOLDS for finding in findings):
        raise ValueError("Complexity baseline contains an unknown rule")
    if any(finding.value <= THRESHOLDS[finding.rule] for finding in findings):
        raise ValueError("Complexity baseline contains a value within its threshold")
    return tuple(sorted(findings))


def compare_findings(current: Sequence[Finding], baseline: Sequence[Finding]) -> Comparison:
    current_by_key = {finding.key: finding for finding in current}
    baseline_by_key = {finding.key: finding for finding in baseline}
    added_keys = current_by_key.keys() - baseline_by_key.keys()
    resolved_keys = baseline_by_key.keys() - current_by_key.keys()
    shared_keys = current_by_key.keys() & baseline_by_key.keys()
    added = tuple(sorted(current_by_key[key] for key in added_keys))
    resolved = tuple(sorted(baseline_by_key[key] for key in resolved_keys))
    worsened = _changed_values(current_by_key, baseline_by_key, shared_keys, worsened=True)
    improved = _changed_values(current_by_key, baseline_by_key, shared_keys, worsened=False)
    return Comparison(added=added, worsened=worsened, improved=improved, resolved=resolved)


def _changed_values(
    current: dict[tuple[str, str, str], Finding],
    baseline: dict[tuple[str, str, str], Finding],
    shared_keys: set[tuple[str, str, str]],
    *,
    worsened: bool,
) -> tuple[tuple[Finding, Finding], ...]:
    def changed(key: tuple[str, str, str]) -> bool:
        if worsened:
            return current[key].value > baseline[key].value
        return current[key].value < baseline[key].value

    return tuple(sorted((baseline[key], current[key]) for key in shared_keys if changed(key)))


def write_baseline(findings: Sequence[Finding], path: Path = BASELINE_PATH) -> None:
    payload: dict[str, Any] = {
        "version": 1,
        "scope": list(SOURCE_SCOPE),
        "thresholds": dict(THRESHOLDS),
        "violations": [
            {
                "path": finding.path,
                "function": finding.function,
                "rule": finding.rule,
                "value": finding.value,
            }
            for finding in sorted(findings)
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def _print_comparison(comparison: Comparison) -> None:
    for finding in comparison.added:
        print(f"NEW {finding.path}::{finding.function} {finding.rule}={finding.value}")
    for old, new in comparison.worsened:
        print(f"WORSE {new.path}::{new.function} {new.rule}: {old.value} -> {new.value}")
    for old, new in comparison.improved:
        print(f"IMPROVED {new.path}::{new.function} {new.rule}: {old.value} -> {new.value}")
    for finding in comparison.resolved:
        print(f"RESOLVED {finding.path}::{finding.function} {finding.rule}")


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enforce the audited complexity baseline")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--initialize-baseline", action="store_true")
    actions.add_argument("--update-baseline", action="store_true")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    current = collect_current_findings()
    if args.initialize_baseline:
        if BASELINE_PATH.exists():
            raise FileExistsError(f"Baseline already exists: {BASELINE_PATH}")
        write_baseline(current, BASELINE_PATH)
        print(f"Initialized complexity baseline with {len(current)} findings")
        return 0
    baseline = load_baseline(BASELINE_PATH)
    comparison = compare_findings(current, baseline)
    if args.update_baseline:
        if comparison.added or comparison.worsened:
            _print_comparison(comparison)
            print("Baseline update refused: new or worsened complexity debt exists", file=sys.stderr)
            return 1
        write_baseline(current, BASELINE_PATH)
        print(f"Tightened complexity baseline to {len(current)} findings")
        return 0
    if comparison.changed:
        _print_comparison(comparison)
        print("Complexity baseline drift detected", file=sys.stderr)
        return 1
    print(f"Complexity gate passed with {len(current)} audited baseline findings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
