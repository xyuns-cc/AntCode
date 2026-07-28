import pytest
from antcode_web_api.routes.v1 import tasks
from antcode_web_api.utils import safe_yaml
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


@pytest.mark.parametrize(
    "payload",
    [
        "base: &base\n  name: task\ncopy: *base\n",
        "base: &base\n  name: task\n",
    ],
)
def test_task_import_rejects_yaml_anchors_and_aliases(payload):
    with pytest.raises(HTTPException, match="anchor 或 alias"):
        tasks._parse_task_import_payload(payload)


def test_yaml_import_enforces_expanded_node_limit(monkeypatch):
    monkeypatch.setattr(safe_yaml, "MAX_YAML_IMPORT_NODES", 3)
    with pytest.raises(ValueError, match="节点数"):
        safe_yaml.load_untrusted_yaml("root:\n  - one\n  - two\n", max_input_bytes=1024)


def test_yaml_import_enforces_depth_limit(monkeypatch):
    monkeypatch.setattr(safe_yaml, "MAX_YAML_IMPORT_DEPTH", 2)
    with pytest.raises(ValueError, match="深度"):
        safe_yaml.load_untrusted_yaml("a:\n  b:\n    c: value\n", max_input_bytes=1024)
