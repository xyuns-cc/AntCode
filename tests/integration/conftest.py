import pytest_asyncio


@pytest_asyncio.fixture(autouse=True)
async def close_s3_client_manager():
    yield
    try:
        from antcode_core.infrastructure.storage.s3_client import get_s3_client_manager
    except Exception:
        return

    await get_s3_client_manager().close()
