from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import generate_proto

COMMON_PROTO = """syntax = "proto3";
package antcode;
message Common { string value = 1; }
"""
GATEWAY_PROTO = """syntax = "proto3";
package antcode;
import "common.proto";
message Gateway { Common common = 1; }
"""


def _project_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "project"
    script = root / "scripts" / "generate_proto.py"
    script.parent.mkdir(parents=True)
    monkeypatch.setattr(generate_proto, "__file__", str(script))
    return root


def _write_proto(root: Path, name: str, content: str) -> Path:
    proto = root / "contracts" / "proto" / name
    proto.parent.mkdir(parents=True, exist_ok=True)
    proto.write_text(content, encoding="utf-8")
    return proto


def test_main_rejects_missing_proto_directory(tmp_path, monkeypatch) -> None:
    _project_root(tmp_path, monkeypatch)

    with pytest.raises(SystemExit) as exc_info:
        generate_proto.main()

    assert exc_info.value.code == 1


def test_main_rejects_empty_proto_directory(tmp_path, monkeypatch) -> None:
    root = _project_root(tmp_path, monkeypatch)
    (root / "contracts" / "proto").mkdir(parents=True)

    with pytest.raises(SystemExit) as exc_info:
        generate_proto.main()

    assert exc_info.value.code == 1


def test_main_generates_real_python_contracts_and_fixes_imports(tmp_path, monkeypatch) -> None:
    root = _project_root(tmp_path, monkeypatch)
    _write_proto(root, "common.proto", COMMON_PROTO)
    _write_proto(root, "gateway.proto", GATEWAY_PROTO)

    generate_proto.main()

    output = root / "packages" / "antcode_contracts" / "src" / "antcode_contracts"
    gateway_pb2 = (output / "gateway_pb2.py").read_text(encoding="utf-8")
    assert "from . import common_pb2 as common__pb2" in gateway_pb2
    assert (output / "common_pb2.pyi").is_file()
    assert (output / "gateway_pb2_grpc.py").is_file()


def test_main_exits_when_protoc_returns_failure(tmp_path, monkeypatch) -> None:
    root = _project_root(tmp_path, monkeypatch)
    proto = _write_proto(root, "broken.proto", COMMON_PROTO)
    commands: list[list[str]] = []

    def fail(command, *, capture_output, text):
        assert capture_output is True
        assert text is True
        commands.append(command)
        return subprocess.CompletedProcess(command, 2, stderr="protoc failed")

    monkeypatch.setattr(generate_proto.subprocess, "run", fail)

    with pytest.raises(SystemExit) as exc_info:
        generate_proto.main()

    assert exc_info.value.code == 1
    assert str(proto) in commands[0]
    assert "grpc_tools.protoc" in commands[0]


def test_main_propagates_process_launch_failure(tmp_path, monkeypatch) -> None:
    root = _project_root(tmp_path, monkeypatch)
    _write_proto(root, "common.proto", COMMON_PROTO)

    def fail(*_args, **_kwargs):
        raise OSError("python executable unavailable")

    monkeypatch.setattr(generate_proto.subprocess, "run", fail)

    with pytest.raises(OSError, match="python executable unavailable"):
        generate_proto.main()


def test_repository_generated_contracts_are_current(tmp_path, monkeypatch) -> None:
    repository_root = Path(__file__).resolve().parents[3]
    generated_root = _project_root(tmp_path, monkeypatch)
    shutil.copytree(repository_root / "contracts" / "proto", generated_root / "contracts" / "proto")

    generate_proto.main()

    expected_dir = repository_root / "packages" / "antcode_contracts" / "src" / "antcode_contracts"
    actual_dir = generated_root / "packages" / "antcode_contracts" / "src" / "antcode_contracts"
    expected_files = sorted(
        path.name for pattern in ("*_pb2.py", "*_pb2.pyi", "*_pb2_grpc.py") for path in expected_dir.glob(pattern)
    )
    assert expected_files
    for name in expected_files:
        assert (actual_dir / name).read_bytes() == (expected_dir / name).read_bytes(), name
