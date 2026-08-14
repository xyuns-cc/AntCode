import json
import tomllib
from pathlib import Path

import scripts.check_complexity as gate
from scripts.complexity_analysis import Finding, build_function_index

ROOT = Path(__file__).resolve().parents[3]


def _finding(name: str, value: int, rule: str = "C901") -> Finding:
    return Finding(path="services/example.py", function=name, rule=rule, value=value)


def test_ast_counts_only_caller_supplied_fixed_positional_parameters(tmp_path):
    source = tmp_path / "sample.py"
    source.write_text(
        """
class Service:
    def method(self, first, second, third, *, dependency, enabled=True):
        pass

    @classmethod
    def factory(cls, first, second, third, *, dependency):
        pass

    @staticmethod
    def convert(first, second, third, fourth, *, dependency):
        pass

def valid(first, second, /, third, *, dependency, enabled=True):
    pass

def invalid(first, second, /, third, fourth, *, dependency):
    pass

def variadic(first, second, third, *remaining, dependency):
    pass
""",
        encoding="utf-8",
    )

    counts = {item.qualified_name: item.positional_count for item in build_function_index(source).functions}

    assert counts == {
        "Service.method": 3,
        "Service.factory": 3,
        "Service.convert": 4,
        "valid": 3,
        "invalid": 4,
        "variadic": 4,
    }


def test_comparison_distinguishes_added_worsened_improved_and_resolved():
    baseline = (_finding("worse", 12), _finding("better", 15), _finding("gone", 11))
    current = (_finding("worse", 13), _finding("better", 14), _finding("new", 11))

    comparison = gate.compare_findings(current, baseline)

    assert comparison.added == (_finding("new", 11),)
    assert comparison.worsened == ((_finding("worse", 12), _finding("worse", 13)),)
    assert comparison.improved == ((_finding("better", 15), _finding("better", 14)),)
    assert comparison.resolved == (_finding("gone", 11),)


def test_baseline_update_refuses_new_or_worsened_debt(tmp_path, monkeypatch):
    baseline_path = tmp_path / "baseline.json"
    baseline = (_finding("existing", 12),)
    gate.write_baseline(baseline, baseline_path)
    monkeypatch.setattr(gate, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(gate, "collect_current_findings", lambda: (_finding("existing", 13),))

    assert gate.main(["--update-baseline"]) == 1
    assert gate.load_baseline(baseline_path) == baseline


def test_baseline_update_persists_improvement_and_removal(tmp_path, monkeypatch):
    baseline_path = tmp_path / "baseline.json"
    baseline = (_finding("better", 15), _finding("gone", 11))
    current = (_finding("better", 13),)
    gate.write_baseline(baseline, baseline_path)
    monkeypatch.setattr(gate, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(gate, "collect_current_findings", lambda: current)

    assert gate.main(["--update-baseline"]) == 0
    assert gate.load_baseline(baseline_path) == current


def test_default_check_requires_improvement_to_tighten_baseline(tmp_path, monkeypatch):
    baseline_path = tmp_path / "baseline.json"
    gate.write_baseline((_finding("better", 15),), baseline_path)
    monkeypatch.setattr(gate, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(gate, "collect_current_findings", lambda: (_finding("better", 14),))

    assert gate.main([]) == 1


def test_complexity_gate_configuration_is_wired_without_directory_bypass():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    per_file_ignores = pyproject["tool"]["ruff"]["lint"]["per-file-ignores"]

    assert all("C901" not in ignored for ignored in per_file_ignores.values())
    assert "complexity:\n\tuv run python -m scripts.check_complexity" in makefile
    assert "check: lint format-check complexity type-check" in makefile


def test_committed_baseline_is_exact_and_auditable():
    baseline_path = ROOT / "scripts/complexity_baseline.json"
    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    violations = payload["violations"]
    thresholds = payload["thresholds"]

    # P2 §4.6: 门禁扩栅 —— 嵌套(PLR1702)、魔法数字计数(PLR2004)、
    # 文件行数(FILE_LINES, Python + 前端 TS/TSX)。
    assert payload["scope"] == [
        "packages/**/*.py",
        "services/**/*.py",
        "scripts/**/*.py",
        "tests/**/*.py",
        "web/antcode-frontend/src/**/*.ts",
        "web/antcode-frontend/src/**/*.tsx",
    ]
    assert thresholds == {
        "C901": 10,
        "PLR0911": 6,
        "PLR0912": 12,
        "PLR0915": 50,
        "PLR1702": 3,
        "PLR2004": 0,
        "POSITIONAL_ARGS": 3,
        "FILE_LINES": 300,
    }
    assert violations == sorted(violations, key=lambda item: (item["path"], item["function"], item["rule"]))
    assert all(item["value"] > thresholds[item["rule"]] for item in violations)
    assert all(set(item) == {"path", "function", "rule", "value"} for item in violations)
