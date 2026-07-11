from datetime import datetime, timedelta

from antcode_core.application.services.users.user_service import UserService


def test_is_user_online_within_window_returns_true():
    now = datetime.now()
    recent_login = now - timedelta(minutes=5)

    assert UserService._is_user_online(recent_login) is True


def test_is_user_online_outside_window_returns_false():
    now = datetime.now()
    old_login = now - timedelta(minutes=30)

    assert UserService._is_user_online(old_login) is False


def test_is_user_online_without_last_login_returns_false():
    assert UserService._is_user_online(None) is False
