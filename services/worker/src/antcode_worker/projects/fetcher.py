"""
项目拉取与缓存

提供基于 file_hash 的缓存与安全解压。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tarfile
import time
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class ProjectCacheEntry:
    """项目缓存条目"""

    cache_key: str
    project_id: str
    file_hash: str
    local_path: str
    created_at: float = field(default_factory=time.time)
    last_access: float = field(default_factory=time.time)
    size_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectCacheEntry:
        return cls(**data)


class ProjectCache:
    """项目缓存索引"""

    INDEX_FILE = "index.json"

    def __init__(
        self,
        cache_dir: str,
        max_entries: int = 200,
        ttl_hours: int = 24 * 7,
    ):
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._cache_dir / self.INDEX_FILE
        self._max_entries = max_entries
        self._ttl_seconds = ttl_hours * 3600
        self._entries: dict[str, ProjectCacheEntry] = {}
        self._lock = asyncio.Lock()
        self._load_index()

    def _load_index(self) -> None:
        if not self._index_path.exists():
            return
        try:
            data = json.loads(self._index_path.read_text(encoding="utf-8"))
            for key, entry in data.items():
                self._entries[key] = ProjectCacheEntry.from_dict(entry)
        except Exception as exc:
            logger.warning(f"读取项目缓存索引失败: {exc}")

    def _save_index(self) -> None:
        data = {k: v.to_dict() for k, v in self._entries.items()}
        self._index_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    async def get(self, cache_key: str) -> str | None:
        async with self._lock:
            entry = self._entries.get(cache_key)
            if not entry:
                return None

            now = time.time()
            if now - entry.created_at > self._ttl_seconds:
                self._entries.pop(cache_key, None)
                self._save_index()
                return None

            if not os.path.exists(entry.local_path):
                self._entries.pop(cache_key, None)
                self._save_index()
                return None

            entry.last_access = now
            self._save_index()
            return entry.local_path

    async def put(self, entry: ProjectCacheEntry) -> None:
        async with self._lock:
            if len(self._entries) >= self._max_entries:
                self._evict_locked()
            self._entries[entry.cache_key] = entry
            self._save_index()

    def _evict_locked(self) -> None:
        if not self._entries:
            return
        oldest = sorted(self._entries.values(), key=lambda e: e.last_access)
        evict_count = max(1, len(self._entries) - self._max_entries + 1)
        for entry in oldest[:evict_count]:
            self._entries.pop(entry.cache_key, None)


class ArtifactFetcher:
    """项目文件获取器"""

    def __init__(self, cache: ProjectCache):
        self._cache = cache

    async def fetch(
        self,
        project_id: str,
        download_url: str,
        file_hash: str | None = None,
        is_compressed: bool | None = None,
        entry_point: str | None = None,
    ) -> str:
        cache_key = self._build_cache_key(project_id, file_hash, download_url)
        cached = await self._cache.get(cache_key)
        if cached:
            return cached

        project_dir = self._build_project_dir(project_id, cache_key)
        project_dir.mkdir(parents=True, exist_ok=True)

        filename = self._guess_filename(download_url)
        file_path = project_dir / filename

        await self._download_file(download_url, file_path)

        if file_hash:
            algo = self._detect_hash_algo(file_hash)
            actual = await asyncio.to_thread(self._hash_file, file_path, algo)
            if actual.lower() != file_hash.lower():
                raise RuntimeError(f"项目文件哈希不一致: expected={file_hash}, actual={actual}")

        # 判断是否需要解压
        # 1. 如果明确指定 is_compressed=False，则不解压
        # 2. 如果是压缩包扩展名，则解压
        should_extract = True
        if is_compressed is False:
            should_extract = False

        if should_extract:
            extracted_path = await self._extract_if_needed(file_path, project_dir)
        else:
            extracted_path = None
            # 对于单个文件，将其移动到 extracted 目录以保持一致的目录结构
            extract_dir = project_dir / "extracted"
            extract_dir.mkdir(parents=True, exist_ok=True)
            # 使用 entry_point 作为文件名（如果提供），否则使用原始文件名
            target_name = entry_point if entry_point else filename
            target_path = extract_dir / target_name
            await asyncio.to_thread(self._copy_file, file_path, target_path)
            extracted_path = str(extract_dir)

        final_path = extracted_path or str(project_dir)

        size_bytes = file_path.stat().st_size if file_path.exists() else 0
        entry = ProjectCacheEntry(
            cache_key=cache_key,
            project_id=project_id,
            file_hash=file_hash or "",
            local_path=final_path,
            size_bytes=size_bytes,
        )
        await self._cache.put(entry)
        return final_path

    def _build_cache_key(self, project_id: str, file_hash: str | None, url: str) -> str:
        safe_project = self._safe_slug(project_id)
        if file_hash:
            return f"{safe_project}:{file_hash}"
        url_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
        return f"{safe_project}:{url_hash}"

    def _build_project_dir(self, project_id: str, cache_key: str) -> Path:
        safe_project = self._safe_slug(project_id)
        safe_key = self._safe_slug(cache_key)
        return self._cache._cache_dir / safe_project / safe_key

    def _safe_slug(self, value: str) -> str:
        return re.sub(r"[^a-zA-Z0-9._-]", "_", value)

    def _guess_filename(self, url: str) -> str:
        name = url.split("?")[0].split("#")[0].rstrip("/").split("/")[-1]
        return name or "project.zip"

    async def _download_file(self, url: str, file_path: Path) -> None:
        if url.startswith("file://"):
            src = Path(url.removeprefix("file://"))
            if not src.exists():
                raise FileNotFoundError(f"本地文件不存在: {src}")
            await asyncio.to_thread(self._copy_file, src, file_path)
            return

        import httpx

        async with (
            httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client,
            client.stream("GET", url) as response,
        ):
            response.raise_for_status()
            with open(file_path, "wb") as f:
                async for chunk in response.aiter_bytes():
                    f.write(chunk)

    def _copy_file(self, src: Path, dest: Path) -> None:
        import shutil

        shutil.copy2(src, dest)

    def _detect_hash_algo(self, file_hash: str) -> str:
        length = len(file_hash)
        if length == 32:
            return "md5"
        if length == 64:
            return "sha256"
        return "sha256"

    def _hash_file(self, file_path: Path, algo: str) -> str:
        hasher = hashlib.new(algo)
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    async def _extract_if_needed(self, file_path: Path, project_dir: Path) -> str | None:
        name = file_path.name.lower()
        if not (name.endswith(".zip") or name.endswith(".tar.gz") or name.endswith(".tgz")):
            return None

        extract_dir = project_dir / "extracted"
        extract_dir.mkdir(parents=True, exist_ok=True)

        if name.endswith(".zip"):
            await asyncio.to_thread(self._safe_extract_zip, file_path, extract_dir)
        else:
            await asyncio.to_thread(self._safe_extract_tar, file_path, extract_dir)

        return str(extract_dir)

    def _safe_extract_zip(self, file_path: Path, dest: Path) -> None:
        base_dir = dest.resolve()
        with zipfile.ZipFile(file_path, "r") as zf:
            for member in zf.infolist():
                if self._is_unsafe_zip_member(member, base_dir):
                    raise RuntimeError(f"不安全的压缩路径: {member.filename}")
            for member in zf.infolist():
                zf.extract(member, base_dir)

    def _safe_extract_tar(self, file_path: Path, dest: Path) -> None:
        mode = "r:gz" if file_path.name.endswith((".tar.gz", ".tgz")) else "r"
        base_dir = dest.resolve()
        with tarfile.open(file_path, mode) as tf:
            members = tf.getmembers()
            for member in members:
                if self._is_unsafe_tar_member(member, base_dir):
                    raise RuntimeError(f"不安全的压缩路径: {member.name}")
            for member in members:
                tf.extract(member, base_dir)

    def _is_unsafe_zip_member(self, member: zipfile.ZipInfo, base_dir: Path) -> bool:
        if self._is_zip_symlink(member):
            return True
        return not self._is_safe_member_path(member.filename, base_dir)

    def _is_unsafe_tar_member(self, member: tarfile.TarInfo, base_dir: Path) -> bool:
        if member.issym() or member.islnk():
            return True
        return not self._is_safe_member_path(member.name, base_dir)

    def _is_zip_symlink(self, member: zipfile.ZipInfo) -> bool:
        mode = member.external_attr >> 16
        return (mode & 0o120000) == 0o120000

    def _is_safe_member_path(self, name: str, base_dir: Path) -> bool:
        if not name:
            return False
        try:
            base = base_dir.resolve()
            target = (base / name).resolve()
            return os.path.commonpath([str(base), str(target)]) == str(base)
        except (ValueError, RuntimeError):
            return False
