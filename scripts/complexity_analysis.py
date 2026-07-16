from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = ("packages", "services", "scripts", "tests")
SOURCE_SCOPE = tuple(f"{root}/**/*.py" for root in SOURCE_ROOTS)
RUFF_RULES = ("C901", "PLR0911", "PLR0912", "PLR0915")
THRESHOLDS = MappingProxyType(
    {
        "C901": 10,
        "PLR0911": 6,
        "PLR0912": 12,
        "PLR0915": 50,
        "POSITIONAL_ARGS": 3,
    }
)
GENERATED_SUFFIXES = ("_pb2.py", "_pb2_grpc.py")
METRIC_PATTERN = re.compile(r"\((\d+) > \d+\)")


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    function: str
    rule: str
    value: int

    @property
    def key(self) -> tuple[str, str, str]:
        return self.path, self.function, self.rule


@dataclass(frozen=True)
class FunctionInfo:
    line: int
    end_line: int
    qualified_name: str
    positional_count: int


class FunctionIndex(ast.NodeVisitor):
    def __init__(self) -> None:
        self.functions: list[FunctionInfo] = []
        self._scope: list[str] = []
        self._scope_kinds: list[str] = []
        self._name_counts: list[dict[str, int]] = [{}]

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_named_scope(node, "class")

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def find(self, row: int) -> FunctionInfo:
        exact = [item for item in self.functions if item.line == row]
        if exact:
            return max(exact, key=lambda item: item.qualified_name.count("."))
        containing = [item for item in self.functions if item.line <= row <= item.end_line]
        if not containing:
            raise ValueError(f"Ruff finding at line {row} is not attached to a function")
        return max(containing, key=lambda item: (item.line, item.qualified_name.count(".")))

    def _visit_named_scope(self, node: ast.ClassDef, kind: str) -> None:
        segment = self._next_segment(node.name)
        self._push_scope(segment, kind)
        for statement in node.body:
            self.visit(statement)
        self._pop_scope()

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        segment = self._next_segment(node.name)
        qualified_name = ".".join((*self._scope, segment))
        self.functions.append(
            FunctionInfo(
                line=node.lineno,
                end_line=node.end_lineno or node.lineno,
                qualified_name=qualified_name,
                positional_count=_positional_count(node, self._scope_kinds),
            )
        )
        self._push_scope(segment, "function")
        for statement in node.body:
            self.visit(statement)
        self._pop_scope()

    def _next_segment(self, name: str) -> str:
        count = self._name_counts[-1].get(name, 0) + 1
        self._name_counts[-1][name] = count
        return name if count == 1 else f"{name}#{count}"

    def _push_scope(self, segment: str, kind: str) -> None:
        self._scope.append(segment)
        self._scope_kinds.append(kind)
        self._name_counts.append({})

    def _pop_scope(self) -> None:
        self._scope.pop()
        self._scope_kinds.pop()
        self._name_counts.pop()


def _positional_count(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    scope_kinds: Sequence[str],
) -> int:
    positional = [*node.args.posonlyargs, *node.args.args]
    count = len(positional) + int(node.args.vararg is not None)
    if not positional or not scope_kinds or scope_kinds[-1] != "class":
        return count
    decorators = {_decorator_name(item) for item in node.decorator_list}
    if "staticmethod" not in decorators and positional[0].arg in {"self", "cls"}:
        return count - 1
    return count


def _decorator_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def discover_source_files(root: Path = ROOT) -> tuple[Path, ...]:
    files = (path for source_root in SOURCE_ROOTS for path in (root / source_root).rglob("*.py"))
    return tuple(
        sorted(path for path in files if not path.name.endswith(GENERATED_SUFFIXES) and ".venv" not in path.parts)
    )


def build_function_index(path: Path) -> FunctionIndex:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    index = FunctionIndex()
    index.visit(tree)
    return index


def collect_positional_findings(root: Path, files: Sequence[Path]) -> tuple[Finding, ...]:
    findings: list[Finding] = []
    for path in files:
        relative_path = path.relative_to(root).as_posix()
        for function in build_function_index(path).functions:
            if function.positional_count > THRESHOLDS["POSITIONAL_ARGS"]:
                findings.append(
                    Finding(
                        path=relative_path,
                        function=function.qualified_name,
                        rule="POSITIONAL_ARGS",
                        value=function.positional_count,
                    )
                )
    return tuple(sorted(findings))


def collect_ruff_findings(root: Path, files: Sequence[Path]) -> tuple[Finding, ...]:
    adjacent_executable = Path(sys.executable).with_name("ruff")
    executable = str(adjacent_executable) if adjacent_executable.is_file() else shutil.which("ruff")
    if executable is None:
        raise RuntimeError("ruff executable is required; run `uv sync --dev`")
    command = _ruff_command(executable, root, files)
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"ruff complexity scan failed:\n{result.stderr.strip()}")
    return _parse_ruff_output(root, result.stdout)


def _ruff_command(executable: str, root: Path, files: Sequence[Path]) -> list[str]:
    relative_files = [path.relative_to(root).as_posix() for path in files]
    return [
        executable,
        "check",
        "--isolated",
        "--target-version=py311",
        f"--select={','.join(RUFF_RULES)}",
        "--config=lint.mccabe.max-complexity=10",
        "--config=lint.pylint.max-returns=6",
        "--config=lint.pylint.max-branches=12",
        "--config=lint.pylint.max-statements=50",
        "--output-format=json",
        "--exit-zero",
        *relative_files,
    ]


def _parse_ruff_output(root: Path, output: str) -> tuple[Finding, ...]:
    raw_findings = json.loads(output)
    indexes: dict[Path, FunctionIndex] = {}
    findings: list[Finding] = []
    for raw in raw_findings:
        rule = raw.get("code")
        if rule not in RUFF_RULES:
            raise ValueError(f"Unexpected Ruff finding: {raw}")
        path = Path(raw["filename"]).resolve()
        index = indexes.setdefault(path, build_function_index(path))
        function = index.find(raw["location"]["row"])
        match = METRIC_PATTERN.search(raw["message"])
        if match is None:
            raise ValueError(f"Cannot parse Ruff metric: {raw['message']}")
        findings.append(
            Finding(
                path=path.relative_to(root).as_posix(),
                function=function.qualified_name,
                rule=rule,
                value=int(match.group(1)),
            )
        )
    return tuple(sorted(findings))


def collect_current_findings(root: Path = ROOT) -> tuple[Finding, ...]:
    files = discover_source_files(root)
    findings = (*collect_ruff_findings(root, files), *collect_positional_findings(root, files))
    by_key = {finding.key: finding for finding in findings}
    if len(by_key) != len(findings):
        raise ValueError("Duplicate complexity finding identifiers detected")
    return tuple(sorted(findings))
