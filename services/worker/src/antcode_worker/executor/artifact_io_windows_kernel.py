"""Injectable Kernel32 primitives for secure Windows artifact access."""

from __future__ import annotations

import ctypes
import os
import stat
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any, Protocol

FILE_ATTRIBUTE_REPARSE_POINT = 0x400
FILE_READ_ATTRIBUTES = 0x80
FILE_LIST_DIRECTORY = 0x1
GENERIC_READ = 0x80000000
FILE_SHARE_READ = 0x1
FILE_SHARE_WRITE = 0x2
OPEN_EXISTING = 3
FILE_FLAG_SEQUENTIAL_SCAN = 0x08000000
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
FILE_BASIC_INFO_CLASS = 0
FILE_STANDARD_INFO_CLASS = 1
FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
FILE_ID_INFO_CLASS = 18
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
FINAL_PATH_BUFFER_CHARS = 32768


@dataclass(frozen=True)
class ArtifactFileIdentity:
    device: int
    inode: int
    mode: int
    link_count: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True)
class WindowsEntryInfo:
    identity: ArtifactFileIdentity
    is_directory: bool
    is_reparse_point: bool


class WindowsKernel(Protocol):
    def open_directory(self, path: str) -> int: ...

    def open_entry(self, path: str) -> int: ...

    def entry_info(self, handle: int) -> WindowsEntryInfo: ...

    def final_path(self, handle: int) -> str: ...

    def read(self, handle: int, size: int) -> bytes: ...

    def close(self, handle: int) -> None: ...


class _FileId128(ctypes.Structure):
    _fields_ = [("identifier", ctypes.c_ubyte * 16)]


class _FileIdInfo(ctypes.Structure):
    _fields_ = [("volume_serial", ctypes.c_ulonglong), ("file_id", _FileId128)]


class _FileStandardInfo(ctypes.Structure):
    _fields_ = [
        ("allocation_size", ctypes.c_longlong),
        ("end_of_file", ctypes.c_longlong),
        ("number_of_links", wintypes.DWORD),
        ("delete_pending", wintypes.BOOLEAN),
        ("directory", wintypes.BOOLEAN),
    ]


class _FileBasicInfo(ctypes.Structure):
    _fields_ = [
        ("creation_time", ctypes.c_longlong),
        ("last_access_time", ctypes.c_longlong),
        ("last_write_time", ctypes.c_longlong),
        ("change_time", ctypes.c_longlong),
        ("attributes", wintypes.DWORD),
    ]


class _FileAttributeTagInfo(ctypes.Structure):
    _fields_ = [("attributes", wintypes.DWORD), ("reparse_tag", wintypes.DWORD)]


class Kernel32Api:
    """Small wrapper around the Win32 HANDLE APIs used by the backend."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise RuntimeError("Kernel32 artifact backend is only available on Windows")
        self._dll = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
        self._configure_signatures()

    def open_directory(self, path: str) -> int:
        access = FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES
        flags = FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT
        return self._create_file(
            path,
            access,
            share=FILE_SHARE_READ | FILE_SHARE_WRITE,
            flags=flags,
        )

    def open_entry(self, path: str) -> int:
        flags = FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN
        return self._create_file(
            path,
            GENERIC_READ | FILE_READ_ATTRIBUTES,
            share=FILE_SHARE_READ,
            flags=flags,
        )

    def entry_info(self, handle: int) -> WindowsEntryInfo:
        file_id = self._info(handle, FILE_ID_INFO_CLASS, _FileIdInfo)
        standard = self._info(handle, FILE_STANDARD_INFO_CLASS, _FileStandardInfo)
        basic = self._info(handle, FILE_BASIC_INFO_CLASS, _FileBasicInfo)
        tag = self._info(handle, FILE_ATTRIBUTE_TAG_INFO_CLASS, _FileAttributeTagInfo)
        mode = stat.S_IFDIR if standard.directory else stat.S_IFREG
        identity = ArtifactFileIdentity(
            device=int(file_id.volume_serial),
            inode=int.from_bytes(bytes(file_id.file_id.identifier), "little"),
            mode=mode,
            link_count=int(standard.number_of_links),
            size=int(standard.end_of_file),
            modified_ns=int(basic.last_write_time) * 100,
            changed_ns=int(basic.change_time) * 100,
        )
        is_reparse = bool(tag.attributes & FILE_ATTRIBUTE_REPARSE_POINT)
        return WindowsEntryInfo(identity, bool(standard.directory), is_reparse)

    def final_path(self, handle: int) -> str:
        buffer = ctypes.create_unicode_buffer(FINAL_PATH_BUFFER_CHARS)
        length = self._dll.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
        if not length or length >= len(buffer):
            self._raise_last_error("GetFinalPathNameByHandleW")
        value = buffer.value
        return value[4:] if value.startswith("\\\\?\\") else value

    def read(self, handle: int, size: int) -> bytes:
        buffer = ctypes.create_string_buffer(size)
        read = wintypes.DWORD()
        if not self._dll.ReadFile(handle, buffer, size, ctypes.byref(read), None):
            self._raise_last_error("ReadFile")
        return buffer.raw[: read.value]

    def close(self, handle: int) -> None:
        if not self._dll.CloseHandle(handle):
            self._raise_last_error("CloseHandle")

    def _create_file(self, path: str, access: int, *, share: int, flags: int) -> int:
        handle = self._dll.CreateFileW(path, access, share, None, OPEN_EXISTING, flags, None)
        if handle == INVALID_HANDLE_VALUE:
            self._raise_last_error("CreateFileW")
        return int(handle)

    def _info(self, handle: int, info_class: int, structure_type: type[Any]) -> Any:
        result = structure_type()
        size = ctypes.sizeof(result)
        if not self._dll.GetFileInformationByHandleEx(handle, info_class, ctypes.byref(result), size):
            self._raise_last_error("GetFileInformationByHandleEx")
        return result

    def _configure_signatures(self) -> None:
        self._dll.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self._dll.CreateFileW.restype = wintypes.HANDLE
        self._dll.GetFinalPathNameByHandleW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self._dll.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        self._dll.GetFileInformationByHandleEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        self._dll.GetFileInformationByHandleEx.restype = wintypes.BOOL
        self._dll.ReadFile.argtypes = [
            wintypes.HANDLE,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        self._dll.ReadFile.restype = wintypes.BOOL
        self._dll.CloseHandle.argtypes = [wintypes.HANDLE]
        self._dll.CloseHandle.restype = wintypes.BOOL

    @staticmethod
    def _raise_last_error(operation: str) -> None:
        error = getattr(ctypes, "get_last_error")()
        raise OSError(error, f"{operation} failed")


__all__ = ["ArtifactFileIdentity", "Kernel32Api", "WindowsEntryInfo", "WindowsKernel"]
