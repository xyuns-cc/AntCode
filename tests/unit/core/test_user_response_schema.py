from datetime import UTC, datetime
from types import SimpleNamespace

from antcode_core.domain.schemas.user import UserResponse, UserSimpleResponse


def _orm_user() -> SimpleNamespace:
    timestamp = datetime.now(UTC)
    return SimpleNamespace(
        id=1,
        public_id="usr_public_1",
        username="admin",
        email="admin@example.com",
        is_active=True,
        is_admin=True,
        role="super_admin",
        created_at=timestamp,
        updated_at=timestamp,
        last_login_at=None,
        is_online=False,
    )


def test_user_response_prefers_public_id_from_orm_model() -> None:
    response = UserResponse.model_validate(_orm_user())

    assert response.id == "usr_public_1"


def test_user_response_normalizes_nullable_email_from_orm_model() -> None:
    user = _orm_user()
    user.email = None

    response = UserResponse.model_validate(user)

    assert response.email == ""


def test_user_simple_response_prefers_public_id_from_orm_model() -> None:
    response = UserSimpleResponse.model_validate(_orm_user())

    assert response.id == "usr_public_1"


def test_user_response_still_accepts_explicit_id() -> None:
    user = _orm_user()
    response = UserResponse(
        id="usr_explicit",
        username=user.username,
        email=user.email,
        is_active=user.is_active,
        is_admin=user.is_admin,
        role=user.role,
        created_at=user.created_at,
        updated_at=user.updated_at,
    )

    assert response.id == "usr_explicit"
