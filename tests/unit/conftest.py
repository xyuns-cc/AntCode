"""单元测试共享 fixture。"""

from datetime import UTC, datetime

import pytest_asyncio
from antcode_core.domain.models.scheduler_authority import (
    SCHEDULER_AUTHORITY_NAME,
    SchedulerAuthority,
)
from tortoise import Tortoise

# B12: 所有派发入口都会回读 scheduler_authority 的权威 Master 代际，
# 因此涉及派发的用例需要一份真表，而不是把读取接口 mock 掉。
ACTIVE_SCHEDULER_EPOCH = 23


@pytest_asyncio.fixture
async def active_scheduler_authority():
    """在内存 sqlite 建一条活跃 Master 权威记录，返回其 fencing_token。"""
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["antcode_core.domain.models.scheduler_authority"]},
    )
    await Tortoise.generate_schemas()
    try:
        await SchedulerAuthority.create(
            name=SCHEDULER_AUTHORITY_NAME,
            fencing_token=ACTIVE_SCHEDULER_EPOCH,
            activated_at=datetime.now(UTC),
        )
        yield ACTIVE_SCHEDULER_EPOCH
    finally:
        await Tortoise.close_connections()


@pytest_asyncio.fixture
async def empty_scheduler_authority():
    """只有空表、没有任何活跃 Master 权威记录的场景。"""
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["antcode_core.domain.models.scheduler_authority"]},
    )
    await Tortoise.generate_schemas()
    try:
        yield
    finally:
        await Tortoise.close_connections()
