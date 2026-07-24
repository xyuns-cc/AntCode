from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
ROUTE_DIR = ROOT / "services/web_api/src/antcode_web_api/routes/v1"


def _read(name: str) -> str:
    return (ROUTE_DIR / name).read_text(encoding="utf-8")


def test_web_api_routes_use_bounded_control_stream_writer():
    source = "\n".join(
        [
            _read("runs.py"),
            _read("tasks.py"),
            _read("workers.py"),
        ]
    )

    assert ".xadd(" not in source
    assert "write_control_event" in source
    assert "control_stream(" in source


def test_log_routes_enforce_run_access():
    # P2 拆分后 /distributed-logs/{run_id} 挂在 workers_distributed.py, 契约
    # 未变 (register_distributed_routes 挂 @router), grep 扩到该模块。
    source = "\n".join(
        [
            _read("tasks.py"),
            _read("workers.py"),
            _read("workers_distributed.py"),
        ]
    )

    assert "/runs/{run_id}/logs/download" in source
    assert "get_execution_with_permission" in source
    assert "/distributed-logs/{run_id}" in source
    assert "_require_run_access" in source
