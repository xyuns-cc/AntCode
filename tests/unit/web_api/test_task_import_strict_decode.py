import pytest
from antcode_web_api.routes.v1 import tasks
from fastapi import HTTPException


def test_decode_task_import_rejects_invalid_utf8():
    with pytest.raises(HTTPException) as exc_info:
        tasks._decode_task_import_bytes(b"\xff{invalid")

    assert exc_info.value.status_code == 400
    assert "UTF-8" in str(exc_info.value.detail)


def test_task_import_requires_pyyaml_when_yaml_input_is_used(monkeypatch):
    import builtins

    original_import = builtins.__import__

    def reject_yaml_import(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("missing yaml")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", reject_yaml_import)

    with pytest.raises(HTTPException) as exc_info:
        tasks._parse_task_import_payload("name: sample-task\n")

    assert exc_info.value.status_code == 500
    assert "PyYAML" in str(exc_info.value.detail)
