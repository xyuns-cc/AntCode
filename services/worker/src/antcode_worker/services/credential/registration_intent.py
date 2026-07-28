"""Durable, process-locked state for recoverable Worker registration."""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import secrets
import stat
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from antcode_worker.services.credential.private_io import write_all

_INTENT_FILE_NAME = "worker_registration_intent.json"
_LOCK_FILE_NAME = ".worker_registration.lock"
_INTENT_VERSION = 2
_MAX_INTENT_BYTES = 16 * 1024


@dataclass(frozen=True)
class RegistrationRequest:
    name: str
    host: str
    port: int
    region: str
    transport_mode: str
    api_base_url: str
    gateway_host: str
    gateway_port: int


@dataclass(frozen=True)
class RegistrationIntent:
    install_key: str
    registration_id: str
    recovery_secret: str
    request: RegistrationRequest
    created_at: str
    version: int = _INTENT_VERSION

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["request"] = asdict(self.request)
        return payload


class RegistrationIntentStore:
    def __init__(
        self,
        directory: Path,
        *,
        prepare: Callable[[], None],
        read: Callable[[str], str],
        write: Callable[[str, dict[str, Any]], None],
        remove: Callable[[str], None],
    ) -> None:
        self._directory = directory
        self._prepare = prepare
        self._read = read
        self._write = write
        self._remove = remove

    @contextlib.contextmanager
    def session(
        self,
        install_key: str | None = None,
        request: RegistrationRequest | None = None,
    ) -> Iterator[RegistrationIntent | None]:
        self._prepare()
        with _exclusive_file_lock(self._directory / _LOCK_FILE_NAME):
            intent = self._load()
            if intent is not None:
                _require_matching_key(intent, install_key)
                yield intent
                return
            if install_key is None:
                yield None
                return
            if request is None:
                raise ValueError("创建 Worker 注册意图时缺少请求快照")
            intent = _new_intent(install_key, request)
            self._write(_INTENT_FILE_NAME, intent.to_dict())
            yield intent

    def finish(self) -> None:
        self._remove(_INTENT_FILE_NAME)

    def _load(self) -> RegistrationIntent | None:
        try:
            content = self._read(_INTENT_FILE_NAME)
        except FileNotFoundError:
            return None
        if len(content.encode("utf-8")) > _MAX_INTENT_BYTES:
            raise ValueError("Worker 注册意图文件超过 16 KiB 上限")
        try:
            payload = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError("Worker 注册意图文件不是有效 JSON") from exc
        return _decode_intent(payload)


def _new_intent(install_key: str, request: RegistrationRequest) -> RegistrationIntent:
    from datetime import UTC, datetime

    intent = RegistrationIntent(
        install_key=install_key,
        registration_id=secrets.token_hex(16),
        recovery_secret=secrets.token_hex(32),
        request=request,
        created_at=datetime.now(UTC).isoformat(),
    )
    _validate_intent(intent)
    return intent


def _decode_intent(payload: Any) -> RegistrationIntent:
    if not isinstance(payload, dict) or payload.get("version") != _INTENT_VERSION:
        raise ValueError("Worker 注册意图版本无效")
    try:
        request = RegistrationRequest(**payload["request"])
        intent = RegistrationIntent(
            install_key=payload["install_key"],
            registration_id=payload["registration_id"],
            recovery_secret=payload["recovery_secret"],
            request=request,
            created_at=payload["created_at"],
            version=payload["version"],
        )
    except (KeyError, TypeError) as exc:
        raise ValueError("Worker 注册意图字段无效") from exc
    _validate_intent(intent)
    return intent


def _validate_intent(intent: RegistrationIntent) -> None:
    if not isinstance(intent.install_key, str) or not 1 <= len(intent.install_key) <= 64:
        raise ValueError("Worker 注册意图安装 Key 无效")
    _require_hex(intent.registration_id, 32, "registration_id")
    _require_hex(intent.recovery_secret, 64, "recovery_secret")
    if not isinstance(intent.created_at, str) or not intent.created_at:
        raise ValueError("Worker 注册意图创建时间无效")
    request = intent.request
    if request.transport_mode not in {"direct", "gateway"}:
        raise ValueError("Worker 注册意图 transport_mode 无效")
    if not 1 <= request.port <= 65535 or not 1 <= request.gateway_port <= 65535:
        raise ValueError("Worker 注册意图端口无效")


def _require_hex(value: Any, length: int, field: str) -> None:
    if not isinstance(value, str) or len(value) != length:
        raise ValueError(f"Worker 注册意图 {field} 无效")
    try:
        bytes.fromhex(value)
    except ValueError as exc:
        raise ValueError(f"Worker 注册意图 {field} 无效") from exc


def _require_matching_key(intent: RegistrationIntent, install_key: str | None) -> None:
    if install_key is None:
        return
    current = hashlib.sha256(install_key.encode("utf-8")).digest()
    stored = hashlib.sha256(intent.install_key.encode("utf-8")).digest()
    if not hmac.compare_digest(current, stored):
        raise RuntimeError("已有未完成注册意图与当前安装 Key 不一致")


@contextlib.contextmanager
def _exclusive_file_lock(path: Path) -> Iterator[None]:
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        _validate_lock_file(os.fstat(fd))
        _lock_fd(fd)
        try:
            yield
        finally:
            _unlock_fd(fd)
    finally:
        os.close(fd)


def _validate_lock_file(file_stat: os.stat_result) -> None:
    if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
        raise PermissionError("Worker 注册锁必须是单链接普通文件")
    if os.name != "nt" and stat.S_IMODE(file_stat.st_mode) != 0o600:
        raise PermissionError("Worker 注册锁权限必须为 0600")
    if hasattr(os, "geteuid") and file_stat.st_uid != os.geteuid():
        raise PermissionError("Worker 注册锁所有者不是当前进程用户")


def _lock_fd(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        if os.fstat(fd).st_size == 0:
            write_all(fd, b"\0")
        os.lseek(fd, 0, os.SEEK_SET)
        getattr(msvcrt, "locking")(fd, getattr(msvcrt, "LK_LOCK"), 1)
        return
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_EX)


def _unlock_fd(fd: int) -> None:
    if os.name == "nt":
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        getattr(msvcrt, "locking")(fd, getattr(msvcrt, "LK_UNLCK"), 1)
        return
    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)


__all__ = ["RegistrationIntent", "RegistrationIntentStore", "RegistrationRequest"]
