"""
产物收集模块

实现输出文件收集和 ArtifactRef metadata 生成。

Requirements: 7.5
"""

import asyncio
import fnmatch
import hashlib
import mimetypes
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from antcode_worker.domain.enums import ArtifactType
from antcode_worker.domain.models import ArtifactRef


@dataclass
class ArtifactCollectorConfig:
    """
    产物收集器配置

    Requirements: 7.5
    """

    # 最大单个文件大小（字节）
    max_file_size: int = 100 * 1024 * 1024  # 100MB

    # 最大总大小（字节）
    max_total_size: int = 500 * 1024 * 1024  # 500MB

    # 最大文件数量
    max_file_count: int = 1000

    # 是否计算校验和
    compute_checksum: bool = True

    # 校验和算法
    checksum_algorithm: str = "sha256"

    # 是否检测 MIME 类型
    detect_mime_type: bool = True

    # 默认排除模式
    exclude_patterns: list[str] = field(
        default_factory=lambda: [
            "*.pyc",
            "__pycache__/*",
            ".git/*",
            ".venv/*",
            "*.egg-info/*",
            ".pytest_cache/*",
            ".mypy_cache/*",
            ".ruff_cache/*",
        ]
    )


@dataclass
class CollectionResult:
    """收集结果"""

    artifacts: list[ArtifactRef]
    total_size: int
    file_count: int
    skipped_count: int
    errors: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifacts": [a.to_dict() for a in self.artifacts],
            "total_size": self.total_size,
            "file_count": self.file_count,
            "skipped_count": self.skipped_count,
            "errors": self.errors,
        }


class ArtifactCollector:
    """
    产物收集器

    收集执行产生的输出文件，生成 ArtifactRef metadata。

    Requirements: 7.5
    """

    def __init__(self, config: ArtifactCollectorConfig | None = None):
        """
        初始化产物收集器

        Args:
            config: 收集器配置
        """
        self.config = config or ArtifactCollectorConfig()

    async def collect(
        self,
        work_dir: str,
        patterns: list[str],
        run_id: str | None = None,
    ) -> CollectionResult:
        """
        收集产物

        Args:
            work_dir: 工作目录
            patterns: 匹配模式列表（glob 格式）
            run_id: 运行 ID（用于日志）

        Returns:
            CollectionResult 收集结果

        Requirements: 7.5
        """
        artifacts: list[ArtifactRef] = []
        total_size = 0
        file_count = 0
        skipped_count = 0
        errors: list[str] = []

        if not patterns:
            return CollectionResult(
                artifacts=artifacts,
                total_size=total_size,
                file_count=file_count,
                skipped_count=skipped_count,
                errors=errors,
            )

        work_path = Path(work_dir)

        if not work_path.exists():
            errors.append(f"工作目录不存在: {work_dir}")
            return CollectionResult(
                artifacts=artifacts,
                total_size=total_size,
                file_count=file_count,
                skipped_count=skipped_count,
                errors=errors,
            )

        # 收集匹配的文件
        matched_files: set[Path] = set()

        for pattern in patterns:
            try:
                # 使用 glob 匹配
                for file_path in work_path.glob(pattern):
                    if file_path.is_file():
                        matched_files.add(file_path)
            except Exception as e:
                errors.append(f"匹配模式失败: {pattern}, error={e}")

        # 处理匹配的文件
        for file_path in sorted(matched_files):
            # 检查是否应该排除
            rel_path = str(file_path.relative_to(work_path))
            if self._should_exclude(rel_path):
                skipped_count += 1
                continue

            # 检查文件数量限制
            if file_count >= self.config.max_file_count:
                skipped_count += 1
                errors.append(f"文件数量超限: {self.config.max_file_count}")
                break

            try:
                # 获取文件信息
                stat = file_path.stat()
                file_size = stat.st_size

                # 检查单个文件大小限制
                if file_size > self.config.max_file_size:
                    skipped_count += 1
                    errors.append(
                        f"文件过大: {rel_path} ({file_size} > {self.config.max_file_size})"
                    )
                    continue

                # 检查总大小限制
                if total_size + file_size > self.config.max_total_size:
                    skipped_count += 1
                    errors.append(f"总大小超限: {self.config.max_total_size}")
                    break

                # 创建 ArtifactRef
                artifact = await self._create_artifact_ref(
                    file_path, rel_path, file_size
                )

                artifacts.append(artifact)
                total_size += file_size
                file_count += 1

            except Exception as e:
                errors.append(f"处理文件失败: {rel_path}, error={e}")
                skipped_count += 1

        logger.debug(
            f"产物收集完成: run_id={run_id}, "
            f"files={file_count}, size={total_size}, skipped={skipped_count}"
        )

        return CollectionResult(
            artifacts=artifacts,
            total_size=total_size,
            file_count=file_count,
            skipped_count=skipped_count,
            errors=errors,
        )

    async def _create_artifact_ref(
        self,
        file_path: Path,
        rel_path: str,
        file_size: int,
    ) -> ArtifactRef:
        """
        创建 ArtifactRef

        Args:
            file_path: 文件路径
            rel_path: 相对路径
            file_size: 文件大小

        Returns:
            ArtifactRef
        """
        # 确定产物类型
        artifact_type = self._detect_artifact_type(rel_path)

        # 计算校验和
        checksum = None
        if self.config.compute_checksum:
            checksum = await self._compute_checksum(file_path)

        # 检测 MIME 类型
        mime_type = None
        if self.config.detect_mime_type:
            mime_type = self._detect_mime_type(rel_path)

        return ArtifactRef(
            name=rel_path,
            artifact_type=artifact_type,
            local_path=str(file_path),
            size_bytes=file_size,
            checksum=checksum,
            mime_type=mime_type,
            created_at=datetime.now(),
        )

    def _should_exclude(self, rel_path: str) -> bool:
        """
        检查是否应该排除

        Args:
            rel_path: 相对路径

        Returns:
            是否排除
        """
        return any(fnmatch.fnmatch(rel_path, pattern) for pattern in self.config.exclude_patterns)

    def _detect_artifact_type(self, rel_path: str) -> ArtifactType:
        """
        检测产物类型

        Args:
            rel_path: 相对路径

        Returns:
            ArtifactType
        """
        lower_path = rel_path.lower()

        # 日志文件
        if lower_path.endswith(".log") or "log" in lower_path:
            return ArtifactType.LOG

        # 报告文件
        if any(
            ext in lower_path
            for ext in [".html", ".pdf", ".md", "report", "summary"]
        ):
            return ArtifactType.REPORT

        # 数据文件
        if any(
            ext in lower_path
            for ext in [".json", ".csv", ".xml", ".yaml", ".yml", ".parquet"]
        ):
            return ArtifactType.DATA

        # 压缩包
        if any(
            ext in lower_path
            for ext in [".zip", ".tar", ".gz", ".bz2", ".xz", ".7z"]
        ):
            return ArtifactType.ARCHIVE

        return ArtifactType.FILE

    def _detect_mime_type(self, rel_path: str) -> str | None:
        """
        检测 MIME 类型

        Args:
            rel_path: 相对路径

        Returns:
            MIME 类型
        """
        mime_type, _ = mimetypes.guess_type(rel_path)
        return mime_type

    async def _compute_checksum(self, file_path: Path) -> str:
        """
        计算文件校验和

        Args:
            file_path: 文件路径

        Returns:
            校验和（十六进制字符串）
        """
        algorithm = self.config.checksum_algorithm

        if algorithm == "sha256":
            hasher = hashlib.sha256()
        elif algorithm == "sha1":
            hasher = hashlib.sha1()
        elif algorithm == "md5":
            hasher = hashlib.md5()
        else:
            hasher = hashlib.sha256()

        # 异步读取文件
        loop = asyncio.get_event_loop()

        def read_and_hash():
            with open(file_path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    hasher.update(chunk)
            return hasher.hexdigest()

        return await loop.run_in_executor(None, read_and_hash)


class ArtifactManager:
    """
    产物管理器

    管理产物的收集、存储和清理。

    Requirements: 7.5
    """

    def __init__(
        self,
        collector: ArtifactCollector | None = None,
        storage_dir: str | None = None,
    ):
        """
        初始化产物管理器

        Args:
            collector: 产物收集器
            storage_dir: 存储目录
        """
        self._collector = collector or ArtifactCollector()
        self._storage_dir = storage_dir

        if storage_dir:
            os.makedirs(storage_dir, exist_ok=True)

    @property
    def collector(self) -> ArtifactCollector:
        """获取收集器"""
        return self._collector

    async def collect_artifacts(
        self,
        work_dir: str,
        patterns: list[str],
        run_id: str | None = None,
    ) -> CollectionResult:
        """
        收集产物

        Args:
            work_dir: 工作目录
            patterns: 匹配模式列表
            run_id: 运行 ID

        Returns:
            CollectionResult
        """
        return await self._collector.collect(work_dir, patterns, run_id)

    async def store_artifact(
        self,
        artifact: ArtifactRef,
        run_id: str,
    ) -> ArtifactRef:
        """
        存储产物

        将产物复制到存储目录，更新 URI。

        Args:
            artifact: 产物引用
            run_id: 运行 ID

        Returns:
            更新后的 ArtifactRef
        """
        if not self._storage_dir or not artifact.local_path:
            return artifact

        # 创建运行目录
        run_dir = os.path.join(self._storage_dir, run_id)
        os.makedirs(run_dir, exist_ok=True)

        # 目标路径
        dest_path = os.path.join(run_dir, artifact.name)

        # 确保目标目录存在
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)

        # 复制文件
        loop = asyncio.get_event_loop()

        def copy_file():
            import shutil

            shutil.copy2(artifact.local_path, dest_path)

        await loop.run_in_executor(None, copy_file)

        # 更新 URI
        artifact.uri = f"file://{dest_path}"

        logger.debug(f"产物已存储: {artifact.name} -> {dest_path}")

        return artifact

    async def cleanup_artifacts(self, run_id: str) -> None:
        """
        清理产物

        Args:
            run_id: 运行 ID
        """
        if not self._storage_dir:
            return

        run_dir = os.path.join(self._storage_dir, run_id)

        if os.path.exists(run_dir):
            loop = asyncio.get_event_loop()

            def remove_dir():
                import shutil

                shutil.rmtree(run_dir)

            await loop.run_in_executor(None, remove_dir)

            logger.debug(f"产物已清理: {run_id}")

    async def get_artifact(
        self,
        run_id: str,
        artifact_name: str,
    ) -> ArtifactRef | None:
        """
        获取产物

        Args:
            run_id: 运行 ID
            artifact_name: 产物名称

        Returns:
            ArtifactRef 或 None
        """
        if not self._storage_dir:
            return None

        artifact_path = os.path.join(self._storage_dir, run_id, artifact_name)

        if not os.path.exists(artifact_path):
            return None

        stat = os.stat(artifact_path)

        return ArtifactRef(
            name=artifact_name,
            local_path=artifact_path,
            uri=f"file://{artifact_path}",
            size_bytes=stat.st_size,
            created_at=datetime.fromtimestamp(stat.st_ctime),
        )

    async def list_artifacts(self, run_id: str) -> list[ArtifactRef]:
        """
        列出产物

        Args:
            run_id: 运行 ID

        Returns:
            ArtifactRef 列表
        """
        if not self._storage_dir:
            return []

        run_dir = os.path.join(self._storage_dir, run_id)

        if not os.path.exists(run_dir):
            return []

        artifacts: list[ArtifactRef] = []

        for root, _, files in os.walk(run_dir):
            for file_name in files:
                file_path = os.path.join(root, file_name)
                rel_path = os.path.relpath(file_path, run_dir)
                stat = os.stat(file_path)

                artifacts.append(
                    ArtifactRef(
                        name=rel_path,
                        local_path=file_path,
                        uri=f"file://{file_path}",
                        size_bytes=stat.st_size,
                        created_at=datetime.fromtimestamp(stat.st_ctime),
                    )
                )

        return artifacts


# 便捷函数


async def collect_artifacts(
    work_dir: str,
    patterns: list[str],
    run_id: str | None = None,
    config: ArtifactCollectorConfig | None = None,
) -> list[ArtifactRef]:
    """
    收集产物（便捷函数）

    Args:
        work_dir: 工作目录
        patterns: 匹配模式列表
        run_id: 运行 ID
        config: 收集器配置

    Returns:
        ArtifactRef 列表
    """
    collector = ArtifactCollector(config)
    result = await collector.collect(work_dir, patterns, run_id)
    return result.artifacts


def compute_file_checksum(
    file_path: str,
    algorithm: str = "sha256",
) -> str:
    """
    计算文件校验和（同步版本）

    Args:
        file_path: 文件路径
        algorithm: 算法

    Returns:
        校验和
    """
    if algorithm == "sha256":
        hasher = hashlib.sha256()
    elif algorithm == "sha1":
        hasher = hashlib.sha1()
    elif algorithm == "md5":
        hasher = hashlib.md5()
    else:
        hasher = hashlib.sha256()

    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)

    return hasher.hexdigest()
