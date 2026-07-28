"""Worker 凭证的原子文件存储，环境变量仅作为只读启动来源。"""

from __future__ import annotations

import json
import os
import secrets
import stat
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from antcode_worker.services.credential.base import CredentialStore
from antcode_worker.services.credential.env_store import EnvCredentialStore
from antcode_worker.services.credential.private_io import read_limited, write_all
from antcode_worker.services.credential.private_permissions import set_owner_only_permissions
from antcode_worker.services.credential.registration_intent import (
    RegistrationIntent,
    RegistrationIntentStore,
    RegistrationRequest,
)
from antcode_worker.services.credential.validation import decode_credentials, validate_credentials

_CREDENTIAL_DIR_NAME = "secrets"
_CREDENTIAL_FILE_NAME = "worker_credentials.json"
_WRITE_PROBE_FILE_NAME = ".credential-write-probe"
_MAX_CREDENTIAL_BYTES = 64 * 1024


class PersistentCredentialStore(CredentialStore):
    """优先读凭证文件，缺失时读取环境变量，保存时必须落盘。"""

    def __init__(self, data_root: Path, env_store: CredentialStore | None = None) -> None:
        self._data_root = data_root.expanduser().resolve(strict=False)
        self._secrets_dir = self._data_root / _CREDENTIAL_DIR_NAME
        self._path = self._secrets_dir / _CREDENTIAL_FILE_NAME
        self._env_store = env_store or EnvCredentialStore()
        self._registration_store = RegistrationIntentStore(
            self._secrets_dir,
            prepare=self._prepare_storage_directory,
            read=lambda name: _read_credential_file(self._secrets_dir, name),
            write=lambda name, payload: _atomic_write_json(self._secrets_dir, name, payload),
            remove=lambda name: _remove_credential_file(self._secrets_dir, name),
        )

    @property
    def path(self) -> Path:
        return self._path

    def ensure_durable_writable(self) -> None:
        self._prepare_storage_directory()
        if os.name == "nt":
            _atomic_write_windows(self._secrets_dir / _WRITE_PROBE_FILE_NAME, "probe\n")
        else:
            _atomic_write_unix(self._secrets_dir, _WRITE_PROBE_FILE_NAME, b"probe\n")
        _remove_credential_file(self._secrets_dir, _WRITE_PROBE_FILE_NAME)

    def registration_session(
        self,
        install_key: str | None = None,
        request: RegistrationRequest | None = None,
    ) -> AbstractContextManager[RegistrationIntent | None]:
        return self._registration_store.session(install_key, request)

    def finish_registration(self) -> None:
        self._registration_store.finish()

    def load(self) -> dict[str, Any] | None:
        if os.path.lexists(self._secrets_dir):
            self._validate_storage_path()
        try:
            content = _read_credential_file(self._secrets_dir, self._path.name)
        except FileNotFoundError:
            content = None
        if content is not None:
            return decode_credentials(content)
        env_credentials = self._env_store.load()
        if env_credentials is not None:
            return validate_credentials(env_credentials)
        self._prepare_storage_directory()
        return None

    async def load_async(self) -> dict[str, Any] | None:
        return self.load()

    def save(self, credentials: dict[str, Any]) -> bool:
        payload = validate_credentials(credentials)
        self._prepare_storage_directory()
        _atomic_write_json(self._secrets_dir, self._path.name, payload)
        return True

    async def save_async(self, credentials: dict[str, Any]) -> bool:
        return self.save(credentials)

    def clear(self) -> bool:
        if os.path.lexists(self._secrets_dir):
            self._validate_storage_path()
            _remove_credential_file(self._secrets_dir, self._path.name)
        if not self._env_store.clear():
            raise RuntimeError("清理凭证环境变量失败")
        return True

    async def clear_async(self) -> bool:
        return self.clear()

    def exists(self) -> bool:
        return os.path.lexists(self._path) or self._env_store.exists()

    def _prepare_storage_directory(self) -> None:
        self._data_root.mkdir(parents=True, exist_ok=True)
        self._secrets_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._validate_storage_path()
        set_owner_only_permissions(self._secrets_dir, 0o700)

    def _validate_storage_path(self) -> None:
        resolved_dir = self._secrets_dir.resolve(strict=False)
        try:
            resolved_dir.relative_to(self._data_root)
        except ValueError as exc:
            raise ValueError("Worker 凭证路径越过 data root") from exc
        if self._secrets_dir.is_symlink():
            raise ValueError("Worker 凭证目录不得是符号链接")
        if self._path.is_symlink():
            raise ValueError("Worker 凭证文件不得是符号链接")
        if self._secrets_dir.exists() and os.name != "nt":
            directory_stat = self._secrets_dir.stat()
            directory_mode = stat.S_IMODE(directory_stat.st_mode)
            if directory_mode & 0o077:
                raise PermissionError(f"Worker 凭证目录权限必须为 0700，当前为 {directory_mode:04o}")
            if hasattr(os, "geteuid") and directory_stat.st_uid != os.geteuid():
                raise PermissionError("Worker 凭证目录所有者不是当前进程用户")


def _atomic_write_json(directory: Path, filename: str, payload: dict[str, Any]) -> None:
    content = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    encoded = content.encode("utf-8")
    if len(encoded) > _MAX_CREDENTIAL_BYTES:
        raise ValueError("Worker 凭证文件超过 64 KiB 上限")
    if os.name == "nt":
        _atomic_write_windows(directory / filename, content)
        return
    _atomic_write_unix(directory, filename, encoded)


def _atomic_write_unix(directory: Path, filename: str, encoded: bytes) -> None:
    directory_fd = _open_private_directory(directory)
    temp_name = f".{filename}.{secrets.token_hex(16)}"
    temp_fd: int | None = None
    try:
        _validate_existing_destination(directory_fd, filename)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        temp_fd = os.open(temp_name, flags, 0o600, dir_fd=directory_fd)
        os.fchmod(temp_fd, 0o600)
        write_all(temp_fd, encoded)
        os.fsync(temp_fd)
        _validate_private_file(os.fstat(temp_fd))
        os.close(temp_fd)
        temp_fd = None
        os.replace(temp_name, filename, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
        os.fsync(directory_fd)
    except Exception:
        if temp_fd is not None:
            os.close(temp_fd)
        try:
            os.unlink(temp_name, dir_fd=directory_fd)
        except FileNotFoundError:
            pass
        raise
    finally:
        os.close(directory_fd)


def _atomic_write_windows(path: Path, content: str) -> None:
    temp_path = path.with_name(f".{path.name}.{secrets.token_hex(16)}")
    fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        set_owner_only_permissions(temp_path, 0o600)
        os.replace(temp_path, path)
        set_owner_only_permissions(path, 0o600)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def _read_credential_file(directory: Path, filename: str) -> str:
    if os.name == "nt":
        from antcode_worker.services.credential.windows_io import read_private_windows_file

        content = read_private_windows_file(directory / filename, _MAX_CREDENTIAL_BYTES)
    else:
        content = _read_credential_file_unix(directory, filename)
    if len(content) > _MAX_CREDENTIAL_BYTES:
        raise ValueError("Worker 凭证文件超过 64 KiB 上限")
    return content.decode("utf-8")


def _read_credential_file_unix(directory: Path, filename: str) -> bytes:
    directory_fd = _open_private_directory(directory)
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        file_fd = os.open(filename, flags, dir_fd=directory_fd)
        try:
            _validate_private_file(os.fstat(file_fd))
            return read_limited(file_fd, _MAX_CREDENTIAL_BYTES)
        finally:
            os.close(file_fd)
    finally:
        os.close(directory_fd)


def _remove_credential_file(directory: Path, filename: str) -> None:
    if os.name == "nt":
        (directory / filename).unlink(missing_ok=True)
        return
    directory_fd = _open_private_directory(directory)
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            file_fd = os.open(filename, flags, dir_fd=directory_fd)
        except FileNotFoundError:
            return
        try:
            _validate_private_file(os.fstat(file_fd))
            os.unlink(filename, dir_fd=directory_fd)
            os.fsync(directory_fd)
        finally:
            os.close(file_fd)
    finally:
        os.close(directory_fd)


def _open_private_directory(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    try:
        directory_stat = os.fstat(fd)
        if not stat.S_ISDIR(directory_stat.st_mode):
            raise PermissionError("Worker 凭证目录必须是目录")
        if stat.S_IMODE(directory_stat.st_mode) != 0o700:
            raise PermissionError("Worker 凭证目录权限必须为 0700")
        if hasattr(os, "geteuid") and directory_stat.st_uid != os.geteuid():
            raise PermissionError("Worker 凭证目录所有者不是当前进程用户")
        return fd
    except Exception:
        os.close(fd)
        raise


def _validate_private_file(file_stat: os.stat_result) -> None:
    if not stat.S_ISREG(file_stat.st_mode):
        raise PermissionError("Worker 凭证文件必须是普通文件")
    if file_stat.st_nlink != 1:
        raise PermissionError("Worker 凭证文件不允许硬链接")
    if stat.S_IMODE(file_stat.st_mode) != 0o600:
        raise PermissionError("Worker 凭证文件权限必须为 0600")
    if hasattr(os, "geteuid") and file_stat.st_uid != os.geteuid():
        raise PermissionError("Worker 凭证文件所有者不是当前进程用户")


def _validate_existing_destination(directory_fd: int, filename: str) -> None:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        file_fd = os.open(filename, flags, dir_fd=directory_fd)
    except FileNotFoundError:
        return
    try:
        _validate_private_file(os.fstat(file_fd))
    finally:
        os.close(file_fd)
