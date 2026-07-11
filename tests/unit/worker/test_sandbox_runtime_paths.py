import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from antcode_worker.executor.sandbox import BasicSandbox, SandboxConfig


@pytest.mark.asyncio
async def test_basic_sandbox_uses_root_data_temp_directory_by_default():
    sandbox = BasicSandbox(SandboxConfig(fs_isolated=True))
    context = await sandbox.prepare(MagicMock(), "/work")

    try:
        temp_work_dir = Path(context["temp_work_dir"])
        assert Path.cwd() / "data" in temp_work_dir.parents
        assert os.path.exists(temp_work_dir)
    finally:
        await sandbox.cleanup(context)
