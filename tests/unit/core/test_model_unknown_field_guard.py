"""ORM 未定义字段写入必须报错，不允许静默丢弃。

Tortoise 的 ``Model._set_kwargs`` 对未知 kwarg 六个分支都不匹配，既不 setattr
也不抛错。``TaskRun.create(created_by=...)`` 就是这样把批次派发的所有者整个丢掉，
线上表现为每个 seed URL 派发失败、整批零数据，而创建/启动接口仍返回 200。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from antcode_core.domain.models import TaskRun
from antcode_core.domain.models.base import StrictFieldsModel, UnknownModelFieldError
from antcode_core.domain.models.enums import DispatchStatus, TaskStatus
from antcode_core.domain.models.task_run import TASK_ID_ABSENT
from tortoise import Tortoise

MODELS_APP = "models"
GUARDED_RUN_ID = "guarded-run"


@pytest_asyncio.fixture
async def model_registry():
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={MODELS_APP: ["antcode_core.domain.models"]},
    )
    await Tortoise.generate_schemas()
    try:
        yield Tortoise.apps[MODELS_APP]
    finally:
        await Tortoise.close_connections()


def test_every_registered_model_inherits_the_strict_base(model_registry) -> None:
    """新增模型必须继承 StrictFieldsModel，否则又能静默吞掉未定义字段。"""
    opted_out = sorted(name for name, model in model_registry.items() if not issubclass(model, StrictFieldsModel))
    assert opted_out == []


@pytest.mark.asyncio
async def test_create_with_undefined_field_raises(model_registry) -> None:
    with pytest.raises(UnknownModelFieldError, match="created_by"):
        await TaskRun.create(
            run_id=GUARDED_RUN_ID,
            task_id=TASK_ID_ABSENT,
            status=TaskStatus.PENDING,
            dispatch_status=DispatchStatus.PENDING,
            created_by=1,
        )
    assert await TaskRun.filter(run_id=GUARDED_RUN_ID).exists() is False


@pytest.mark.asyncio
async def test_update_from_dict_with_undefined_field_raises(model_registry) -> None:
    """``update_or_create(defaults=...)`` 也经由这条路径，一并挡住。"""
    run = await TaskRun.create(
        run_id=GUARDED_RUN_ID,
        task_id=TASK_ID_ABSENT,
        status=TaskStatus.PENDING,
        dispatch_status=DispatchStatus.PENDING,
    )
    with pytest.raises(UnknownModelFieldError, match="project_id"):
        run.update_from_dict({"project_id": 1})


@pytest.mark.asyncio
async def test_defined_fields_still_round_trip(model_registry) -> None:
    await TaskRun.create(
        run_id=GUARDED_RUN_ID,
        task_id=TASK_ID_ABSENT,
        status=TaskStatus.PENDING,
        dispatch_status=DispatchStatus.PENDING,
        result_data={"crawl_batch_id": "batch-1"},
    )
    stored = await TaskRun.get(run_id=GUARDED_RUN_ID)
    assert stored.result_data == {"crawl_batch_id": "batch-1"}
