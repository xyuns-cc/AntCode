"""扫描失败必须带着原因回到用户手里，而不是逃逸成 500。

走查实测（mn 栈，2026-08-19）：``POST /api/v1/repositories/{id}/scan`` 传一个
不存在的分支名，接口回 500「服务器内部错误」，web-api 日志里是一条
``ValueError: 无法解析 Git 引用版本`` 的 Traceback。扫描抽屉补上 ref 输入框之后，
用户填错分支名是常态，这条路径不能只给一句和输入无关的话。
"""

import subprocess
from collections.abc import Callable

import pytest
import pytest_asyncio
from antcode_core.application.services.projects.repository_service import (
    RepositoryScanError,
    RepositoryService,
)
from antcode_core.domain.models import GitRepository
from antcode_core.domain.schemas.repository import RepositoryUpdateRequest
from tortoise import Tortoise

OWNER_USER_ID = 7


@pytest_asyncio.fixture
async def repository():
    await Tortoise.init(
        db_url="sqlite://:memory:",
        modules={"models": ["antcode_core.domain.models.git_repository"]},
    )
    await Tortoise.generate_schemas()
    try:
        yield await GitRepository.create(
            name="source",
            url="https://example.test/source.git",
            default_ref="main",
            owner_user_id=OWNER_USER_ID,
        )
    finally:
        await Tortoise.close_connections()


def _failing_clone(error: BaseException) -> Callable[..., list[dict[str, object]]]:
    def _raise(*_args, **_kwargs):
        raise error

    return _raise


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        ValueError("无法解析 Git 引用版本"),
        subprocess.CalledProcessError(128, ["git", "ls-remote", "--refs"]),
    ],
    ids=["unknown-ref", "git-subprocess-failure"],
)
async def test_scan_reports_repository_config_failures_as_scan_error(monkeypatch, repository, failure) -> None:
    monkeypatch.setattr(RepositoryService, "_clone_and_scan", _failing_clone(failure))

    with pytest.raises(RepositoryScanError) as exc_info:
        await RepositoryService().scan_for_user(repository.public_id, OWNER_USER_ID, "no-such-branch")

    await repository.refresh_from_db()
    assert repository.last_scan_status == "failed"
    # 接口报错与列表里的 last_scan_error 必须是同一句话，否则用户看到两套说辞。
    assert str(exc_info.value) == repository.last_scan_error
    assert str(failure) in str(exc_info.value)


@pytest.mark.asyncio
async def test_scan_lets_unexpected_failures_escape_unwrapped(monkeypatch, repository) -> None:
    """守住捕获范围：真缺陷不许被伪装成"用户输入错误"而降级成 4xx。"""
    monkeypatch.setattr(RepositoryService, "_clone_and_scan", _failing_clone(RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        await RepositoryService().scan_for_user(repository.public_id, OWNER_USER_ID, None)

    await repository.refresh_from_db()
    assert repository.last_scan_status == "failed"


@pytest.mark.asyncio
async def test_scan_ref_reaches_git_without_becoming_the_new_default(monkeypatch, repository) -> None:
    """分支切换的两种意图必须分开：扫描带的 ref 只影响这一次，不改 default_ref。"""
    used_refs: list[str] = []
    service = RepositoryService()

    # 打在实例上而不是类上：绑定方法少一个 self，签名保持三个位置参数。
    def _record(_repository, ref, _auth):
        used_refs.append(ref)
        return []

    monkeypatch.setattr(service, "_clone_and_scan", _record)

    await service.scan_for_user(repository.public_id, OWNER_USER_ID, "feature/spiders")

    await repository.refresh_from_db()
    assert used_refs == ["feature/spiders"]
    assert repository.default_ref == "main"


@pytest.mark.asyncio
async def test_update_unbinds_credential_when_null_is_sent(repository) -> None:
    """``credential_id: null`` 是"解绑凭证"，不是"没传这个字段"。

    按 ``is not None`` 判定时，前端把凭证清空后请求照样 200，凭证却还挂着。
    """
    repository.credential_id = "cred-1"
    await repository.save()
    payload = RepositoryUpdateRequest.model_validate({"credential_id": None})

    updated = await RepositoryService().update_for_user(repository.public_id, OWNER_USER_ID, payload)

    assert updated is not None
    assert updated.credential_id is None


@pytest.mark.asyncio
async def test_update_keeps_credential_when_field_is_omitted(repository) -> None:
    repository.credential_id = "cred-1"
    await repository.save()
    payload = RepositoryUpdateRequest.model_validate({"default_ref": "develop"})

    updated = await RepositoryService().update_for_user(repository.public_id, OWNER_USER_ID, payload)

    assert updated is not None
    assert updated.credential_id == "cred-1"
    assert updated.default_ref == "develop"
