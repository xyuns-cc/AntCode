import pytest
from antcode_worker.domain.enums import TaskType
from antcode_worker.domain.models import RunContext, RuntimeSpec, TaskPayload
from antcode_worker.plugins.render.plugin import RenderPlugin


def test_render_plugin_rejects_playwright():
    plugin = RenderPlugin()
    payload = TaskPayload(
        task_type=TaskType.RENDER,
        entry_point="https://example.com",
        kwargs={"engine": "playwright"},
    )

    assert plugin.validate(payload) == ["不支持的 render engine: playwright"]


@pytest.mark.asyncio
async def test_render_plugin_keeps_script_plan():
    plugin = RenderPlugin()
    context = RunContext(
        run_id="run-001",
        task_id="task-001",
        project_id="project-001",
        runtime_spec=RuntimeSpec(python_path="/opt/antcode/runtime/bin/python"),
    )
    payload = TaskPayload(
        task_type=TaskType.RENDER,
        entry_point="render_script.py",
        kwargs={"engine": "script", "output_file": "result.txt"},
        project_cwd="/tmp/project",
    )

    plan = await plugin.build_plan(context, payload)

    assert plan.args[0] == "render_script.py"
    assert "result.txt" in plan.artifact_patterns
