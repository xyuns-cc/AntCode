"""In-memory Windows kernel adapter for platform-independent artifact tests."""

from __future__ import annotations

import ntpath
import stat

from antcode_worker.executor.artifact_io_windows_kernel import (
    ArtifactFileIdentity,
    WindowsEntryInfo,
)


def identity(
    inode: int,
    *,
    directory: bool = False,
    links: int = 1,
    size: int = 0,
    version: int = 1,
) -> ArtifactFileIdentity:
    mode = stat.S_IFDIR if directory else stat.S_IFREG
    return ArtifactFileIdentity(7, inode, mode, links, size, version, version)


class FakeWindowsKernel:
    def __init__(self) -> None:
        self.entries: dict[str, WindowsEntryInfo] = {}
        self.contents: dict[str, bytes] = {}
        self.final_paths: dict[str, str] = {}
        self.info_sequences: dict[str, list[WindowsEntryInfo]] = {}
        self.handles: dict[int, str] = {}
        self.positions: dict[int, int] = {}
        self.opened_paths: list[str] = []
        self.closed_handles: list[int] = []
        self._next_handle = 100

    def add_directory(self, path: str, inode: int, *, reparse: bool = False) -> ArtifactFileIdentity:
        entry_identity = identity(inode, directory=True)
        self._add(path, WindowsEntryInfo(entry_identity, True, reparse))
        return entry_identity

    def add_file(
        self,
        path: str,
        inode: int,
        content: bytes,
        *,
        links: int = 1,
        reparse: bool = False,
    ) -> ArtifactFileIdentity:
        entry_identity = identity(inode, links=links, size=len(content))
        key = self._add(path, WindowsEntryInfo(entry_identity, False, reparse))
        self.contents[key] = content
        return entry_identity

    def set_info_sequence(self, path: str, *entries: WindowsEntryInfo) -> None:
        self.info_sequences[self._key(path)] = list(entries)

    def set_final_path(self, path: str, final_path: str) -> None:
        self.final_paths[self._key(path)] = final_path

    def open_directory(self, path: str) -> int:
        handle = self._open(path)
        if not self.entries[self.handles[handle]].is_directory:
            raise NotADirectoryError(path)
        return handle

    def open_entry(self, path: str) -> int:
        return self._open(path)

    def entry_info(self, handle: int) -> WindowsEntryInfo:
        key = self.handles[handle]
        sequence = self.info_sequences.get(key)
        if sequence:
            return sequence.pop(0) if len(sequence) > 1 else sequence[0]
        return self.entries[key]

    def final_path(self, handle: int) -> str:
        key = self.handles[handle]
        return self.final_paths.get(key, ntpath.normpath(key))

    def read(self, handle: int, size: int) -> bytes:
        key = self.handles[handle]
        start = self.positions[handle]
        chunk = self.contents.get(key, b"")[start : start + size]
        self.positions[handle] += len(chunk)
        return chunk

    def close(self, handle: int) -> None:
        self.closed_handles.append(handle)
        self.handles.pop(handle)
        self.positions.pop(handle)

    def assert_all_closed(self) -> None:
        assert not self.handles
        assert len(self.closed_handles) == len(self.opened_paths)

    def _add(self, path: str, entry: WindowsEntryInfo) -> str:
        key = self._key(path)
        self.entries[key] = entry
        return key

    def _open(self, path: str) -> int:
        key = self._key(path)
        if key not in self.entries:
            raise FileNotFoundError(path)
        handle = self._next_handle
        self._next_handle += 1
        self.handles[handle] = key
        self.positions[handle] = 0
        self.opened_paths.append(ntpath.normpath(path))
        return handle

    @staticmethod
    def _key(path: str) -> str:
        return ntpath.normcase(ntpath.normpath(path))


def populated_kernel(content: bytes = b"artifact-data") -> FakeWindowsKernel:
    kernel = FakeWindowsKernel()
    kernel.add_directory("C:\\", 1)
    kernel.add_directory("C:\\work", 2)
    kernel.add_directory("C:\\work\\nested", 3)
    kernel.add_file("C:\\work\\nested\\result.bin", 4, content)
    return kernel
