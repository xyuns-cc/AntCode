"""用户删除/资产移交必须把爬取批次的归属算进去。

``CrawlBatch.user_id`` 是批次 run 所有者的**唯一**真源
（``run_ownership.resolve_run_owner_id`` 正是从这里解析）。它并不总等于
``Project.user_id``：``crawl.py::_verify_project_access`` 放行管理员在他人项目下建
批次，``batch_service.create_batch`` 又把 ``user_id`` 盖成建批次的那个人。因此:

- 只查 Project/Task 的删除守卫，会放过"只拥有批次"的用户，批次留下悬空 user_id；
- 只迁 Task/Project 的移交路径，会把批次连同其 run 留在已停用账号下变成无主数据。

真表真查，不 mock 被测方法本身。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from antcode_core.application.services.users.user_service import user_service
from antcode_core.domain.models import CrawlBatch, Project, User
from antcode_core.domain.models.enums import ProjectType
from antcode_core.domain.models.user import UserRole
from antcode_core.infrastructure.cache import user_cache
from tortoise import Tortoise

ADMIN_ID = 1
VICTIM_ID = 2
RECEIVER_ID = 3
BATCH_PUBLIC_ID = "batch-owned-by-victim"


@pytest_asyncio.fixture
async def user_tables():
    """真实建表：归属判定要跨 User / Project / CrawlBatch。

    只挡掉缓存失效那一次 Redis 往返（与归属判定无关的外部 I/O，本身被
    ``_invalidate_user_cache`` try/except 兜住，不挡则每个用例白等一次连接超时）。
    被测的删除/移交逻辑与三张表全部是真的。
    """
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["antcode_core.domain.models"]},
    )
    await Tortoise.generate_schemas()
    try:
        with patch.object(user_cache, "clear_prefix", AsyncMock(return_value=0)):
            yield
    finally:
        await Tortoise.close_connections()


async def _create_user(user_id: int, *, is_admin: bool = False) -> User:
    # is_admin 由 User.save 从 role 派生，直接传布尔会被覆盖回 False。
    return await User.create(
        id=user_id,
        username=f"user-{user_id}",
        password_hash="x",
        role=UserRole.ADMIN if is_admin else UserRole.USER,
    )


async def _create_batch_owned_by(user_id: int, *, project: Project, public_id: str) -> CrawlBatch:
    return await CrawlBatch.create(
        public_id=public_id,
        project_id=project.id,
        name=f"batch-{public_id}",
        seed_urls=["https://example.invalid/seed"],
        user_id=user_id,
    )


@pytest.mark.asyncio
async def test_delete_user_refuses_when_only_crawl_batches_owned(user_tables):
    """摘掉修复即变红：只拥有批次（不拥有任何项目/任务）的用户不能被直接删掉。

    场景取自真实可达路径：管理员 VICTIM 在别人的项目下建了批次，
    自己名下没有任何 Project / Task。
    """
    await _create_user(ADMIN_ID, is_admin=True)
    victim = await _create_user(VICTIM_ID, is_admin=True)
    someone_else = await _create_user(RECEIVER_ID)
    foreign_project = await Project.create(name="not-victims", user_id=someone_else.id, type=ProjectType.RULE)
    await _create_batch_owned_by(victim.id, project=foreign_project, public_id=BATCH_PUBLIC_ID)

    # 前提锁死：victim 确实不拥有任何项目，旧守卫因此完全放行
    assert await Project.filter(user_id=victim.id).count() == 0

    with pytest.raises(ValueError, match="爬取批次"):
        await user_service.delete_user(str(victim.id), ADMIN_ID)

    # 拒绝必须是真拒绝：用户还在，批次也没变成无主数据
    assert await User.filter(id=victim.id).exists()
    batch = await CrawlBatch.get(public_id=BATCH_PUBLIC_ID)
    assert batch.user_id == victim.id


@pytest.mark.asyncio
async def test_delete_user_still_refuses_when_projects_owned(user_tables):
    """原有的项目守卫不能被改回归掉。"""
    await _create_user(ADMIN_ID, is_admin=True)
    victim = await _create_user(VICTIM_ID)
    await Project.create(name="victims-project", user_id=victim.id, type=ProjectType.RULE)

    with pytest.raises(ValueError, match="个项目"):
        await user_service.delete_user(str(victim.id), ADMIN_ID)


@pytest.mark.asyncio
async def test_delete_user_allows_clean_user(user_tables):
    """无任何资源的用户必须还能删——守卫不能宽到把干净用户也拦下。"""
    await _create_user(ADMIN_ID, is_admin=True)
    victim = await _create_user(VICTIM_ID)

    await user_service.delete_user(str(victim.id), ADMIN_ID)

    assert not await User.filter(id=victim.id).exists()


@pytest.mark.asyncio
async def test_delete_user_ignores_other_users_batches(user_tables):
    """守卫只看本人名下的批次，别人的批次不该拦住删除。"""
    await _create_user(ADMIN_ID, is_admin=True)
    victim = await _create_user(VICTIM_ID)
    other = await _create_user(RECEIVER_ID)
    project = await Project.create(name="others-project", user_id=other.id, type=ProjectType.RULE)
    await _create_batch_owned_by(other.id, project=project, public_id=BATCH_PUBLIC_ID)

    await user_service.delete_user(str(victim.id), ADMIN_ID)

    assert not await User.filter(id=victim.id).exists()


@pytest.mark.asyncio
async def test_reassign_migrates_crawl_batch_ownership(user_tables):
    """摘掉修复即变红：移交必须带走 CrawlBatch.user_id，否则批次成无主数据。"""
    victim = await _create_user(VICTIM_ID)
    receiver = await _create_user(RECEIVER_ID)
    project = await Project.create(name="victims-project", user_id=victim.id, type=ProjectType.RULE)
    await _create_batch_owned_by(victim.id, project=project, public_id=BATCH_PUBLIC_ID)

    handled = await user_service.delete_user_with_reassign(victim.id, reassign_to_user_id=receiver.id)

    assert handled is True
    batch = await CrawlBatch.get(public_id=BATCH_PUBLIC_ID)
    assert batch.user_id == receiver.id
    # 项目迁移是既有行为，一并锁住防回归
    migrated_project = await Project.get(id=project.id)
    assert migrated_project.user_id == receiver.id


@pytest.mark.asyncio
async def test_reassign_leaves_other_users_batches_alone(user_tables):
    """迁移必须按 user_id 精确圈定，不能把旁人的批次一起卷走。"""
    victim = await _create_user(VICTIM_ID)
    receiver = await _create_user(RECEIVER_ID)
    bystander = await _create_user(ADMIN_ID)
    project = await Project.create(name="victims-project", user_id=victim.id, type=ProjectType.RULE)
    await _create_batch_owned_by(victim.id, project=project, public_id=BATCH_PUBLIC_ID)
    await _create_batch_owned_by(bystander.id, project=project, public_id="batch-of-bystander")

    await user_service.delete_user_with_reassign(victim.id, reassign_to_user_id=receiver.id)

    assert (await CrawlBatch.get(public_id=BATCH_PUBLIC_ID)).user_id == receiver.id
    assert (await CrawlBatch.get(public_id="batch-of-bystander")).user_id == bystander.id
