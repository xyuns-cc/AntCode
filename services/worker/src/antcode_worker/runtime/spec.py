"""
运行时规格定义

定义 RuntimeSpec，包含 Python 规格、依赖锁定和约束条件。
确保 non-deterministic fields 不影响 hash 计算。

Requirements: 6.2
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class PythonSpec:
    """
    Python 规格

    定义 Python 解释器的版本和路径要求。
    """

    # Python 版本要求（如 "3.11", "3.12.1"）
    version: str | None = None

    # 指定 Python 可执行文件路径（优先于 version）
    path: str | None = None

    # 是否使用 uv 管理的 Python
    uv_managed: bool = True

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, PythonSpec):
            return False
        return (
            self.version == other.version
            and self.path == other.path
            and self.uv_managed == other.uv_managed
        )

    def __hash__(self) -> int:
        return hash((self.version, self.path, self.uv_managed))

    def to_dict(self) -> dict[str, Any]:
        """转换为字典"""
        return {
            "version": self.version,
            "path": self.path,
            "uv_managed": self.uv_managed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PythonSpec":
        """从字典创建"""
        return cls(
            version=data.get("version"),
            path=data.get("path"),
            uv_managed=data.get("uv_managed", True),
        )


@dataclass
class LockSource:
    """
    依赖锁定源

    定义依赖的锁定方式，支持多种来源。
    """

    # 锁定类型: "hash" | "uri" | "inline" | "requirements"
    source_type: str = "requirements"

    # uv.lock 内容哈希（当 source_type="hash" 时）
    content_hash: str | None = None

    # 锁文件 URI（当 source_type="uri" 时）
    uri: str | None = None

    # 内联锁文件内容（当 source_type="inline" 时）
    inline_content: str | None = None

    # requirements.txt 内容（当 source_type="requirements" 时）
    requirements: list[str] = field(default_factory=list)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, LockSource):
            return False
        return (
            self.source_type == other.source_type
            and self.content_hash == other.content_hash
            and self.uri == other.uri
            and self.inline_content == other.inline_content
            and sorted(self.requirements) == sorted(other.requirements)
        )

    def __hash__(self) -> int:
        return hash((
            self.source_type,
            self.content_hash,
            self.uri,
            self.inline_content,
            tuple(sorted(self.requirements)),
        ))

    def to_dict(self, sort_requirements: bool = False) -> dict[str, Any]:
        """
        转换为字典

        Args:
            sort_requirements: 是否对 requirements 排序（用于哈希计算）
        """
        return {
            "source_type": self.source_type,
            "content_hash": self.content_hash,
            "uri": self.uri,
            "inline_content": self.inline_content,
            "requirements": sorted(self.requirements) if sort_requirements else self.requirements,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LockSource":
        """从字典创建"""
        return cls(
            source_type=data.get("source_type", "requirements"),
            content_hash=data.get("content_hash"),
            uri=data.get("uri"),
            inline_content=data.get("inline_content"),
            requirements=data.get("requirements", []),
        )

    @classmethod
    def from_requirements(cls, requirements: list[str]) -> "LockSource":
        """从 requirements 列表创建"""
        return cls(source_type="requirements", requirements=requirements)

    @classmethod
    def from_hash(cls, content_hash: str) -> "LockSource":
        """从内容哈希创建"""
        return cls(source_type="hash", content_hash=content_hash)

    @classmethod
    def from_uri(cls, uri: str) -> "LockSource":
        """从 URI 创建"""
        return cls(source_type="uri", uri=uri)


@dataclass
class RuntimeSpec:
    """
    运行时规格

    定义执行环境的确定性字段，用于计算 runtime_hash。
    non-deterministic fields（如 env_vars、secrets）不参与 hash 计算。

    Requirements: 6.2

    确定性字段（影响 hash）：
    - python_spec: Python 版本和路径
    - lock_source: 依赖锁定信息
    - constraints: 版本约束
    - extras: 额外依赖组

    非确定性字段（不影响 hash）：
    - env_vars: 环境变量
    - secrets: 密钥引用
    - metadata: 元数据
    """

    # 确定性字段
    python_spec: PythonSpec = field(default_factory=PythonSpec)
    lock_source: LockSource = field(default_factory=LockSource)
    constraints: list[str] = field(default_factory=list)
    extras: list[str] = field(default_factory=list)

    # 非确定性字段（不影响 runtime_hash）
    env_vars: dict[str, str] = field(default_factory=dict)
    secrets: list[str] = field(default_factory=list)  # 密钥名称列表
    metadata: dict[str, Any] = field(default_factory=dict)

    def get_deterministic_fields(self) -> dict[str, Any]:
        """
        获取确定性字段

        返回用于计算 runtime_hash 的字段。
        """
        return {
            "python_spec": self.python_spec.to_dict(),
            "lock_source": self.lock_source.to_dict(sort_requirements=True),
            "constraints": sorted(self.constraints),
            "extras": sorted(self.extras),
        }

    def to_dict(self) -> dict[str, Any]:
        """转换为完整字典"""
        return {
            "python_spec": self.python_spec.to_dict(),
            "lock_source": self.lock_source.to_dict(),
            "constraints": self.constraints,
            "extras": self.extras,
            "env_vars": self.env_vars,
            "secrets": self.secrets,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeSpec":
        """从字典创建"""
        python_spec_data = data.get("python_spec", {})
        lock_source_data = data.get("lock_source", {})

        return cls(
            python_spec=PythonSpec.from_dict(python_spec_data) if python_spec_data else PythonSpec(),
            lock_source=LockSource.from_dict(lock_source_data) if lock_source_data else LockSource(),
            constraints=data.get("constraints", []),
            extras=data.get("extras", []),
            env_vars=data.get("env_vars", {}),
            secrets=data.get("secrets", []),
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def simple(
        cls,
        python_version: str | None = None,
        requirements: list[str] | None = None,
    ) -> "RuntimeSpec":
        """
        创建简单的运行时规格

        便捷方法，用于快速创建常见配置。
        """
        return cls(
            python_spec=PythonSpec(version=python_version),
            lock_source=LockSource.from_requirements(requirements or []),
        )

    def with_env_vars(self, env_vars: dict[str, str]) -> "RuntimeSpec":
        """
        添加环境变量（返回新实例）

        环境变量是非确定性字段，不影响 runtime_hash。
        """
        return RuntimeSpec(
            python_spec=self.python_spec,
            lock_source=self.lock_source,
            constraints=self.constraints.copy(),
            extras=self.extras.copy(),
            env_vars={**self.env_vars, **env_vars},
            secrets=self.secrets.copy(),
            metadata=self.metadata.copy(),
        )

    def with_secrets(self, secrets: list[str]) -> "RuntimeSpec":
        """
        添加密钥引用（返回新实例）

        密钥是非确定性字段，不影响 runtime_hash。
        """
        return RuntimeSpec(
            python_spec=self.python_spec,
            lock_source=self.lock_source,
            constraints=self.constraints.copy(),
            extras=self.extras.copy(),
            env_vars=self.env_vars.copy(),
            secrets=list(set(self.secrets + secrets)),
            metadata=self.metadata.copy(),
        )

    def __eq__(self, other: object) -> bool:
        """
        比较两个 RuntimeSpec 是否相等

        只比较确定性字段。
        """
        if not isinstance(other, RuntimeSpec):
            return False
        return (
            self.python_spec == other.python_spec
            and self.lock_source == other.lock_source
            and sorted(self.constraints) == sorted(other.constraints)
            and sorted(self.extras) == sorted(other.extras)
        )

    def __hash__(self) -> int:
        """
        计算哈希值

        只使用确定性字段。
        """
        return hash((
            self.python_spec,
            self.lock_source,
            tuple(sorted(self.constraints)),
            tuple(sorted(self.extras)),
        ))
