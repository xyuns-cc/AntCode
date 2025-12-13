"""
工作节点配置模块
"""
import os
import hashlib
import platform
import socket
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

import yaml


# 节点根目录
NODE_ROOT = Path(__file__).parent
# 机器码存储文件路径
MACHINE_CODE_FILE = NODE_ROOT / ".machine_code"
# 节点配置文件路径
NODE_CONFIG_FILE = NODE_ROOT / "node_config.yaml"


def _generate_new_machine_code() -> str:
    """生成新的机器码（基于硬件信息）"""
    info_parts = []

    # MAC 地址
    try:
        mac = uuid.getnode()
        info_parts.append(str(mac))
    except Exception:
        pass

    # CPU 信息
    try:
        info_parts.append(platform.processor())
    except Exception:
        pass

    # 主机名
    try:
        info_parts.append(socket.gethostname())
    except Exception:
        pass

    # 系统信息
    info_parts.append(platform.system())
    info_parts.append(platform.machine())

    # 添加随机因子确保唯一性
    info_parts.append(str(uuid.uuid4()))

    # 生成哈希
    raw = "-".join(info_parts)
    hash_obj = hashlib.sha256(raw.encode())
    return hash_obj.hexdigest()[:16].upper()


def get_or_create_machine_code() -> str:
    """获取或创建机器码"""
    try:
        if MACHINE_CODE_FILE.exists():
            machine_code = MACHINE_CODE_FILE.read_text().strip()
            if machine_code and len(machine_code) == 16:
                return machine_code
    except Exception:
        pass

    machine_code = _generate_new_machine_code()

    try:
        MACHINE_CODE_FILE.write_text(machine_code)
    except Exception as e:
        print(f"[Warning] Cannot save machine code: {e}")

    return machine_code


def reset_machine_code() -> str:
    """重置机器码"""
    try:
        if MACHINE_CODE_FILE.exists():
            MACHINE_CODE_FILE.unlink()
    except Exception:
        pass

    return get_or_create_machine_code()


def get_local_ip() -> str:
    """获取本机 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


@dataclass
class NodeConfig:
    """节点配置类"""

    # 基本配置
    name: str = "Worker-Node"
    port: int = 8001
    host: str = field(default_factory=get_local_ip)
    region: str = "默认"
    version: str = "2.0.0"
    description: str = ""
    tags: List[str] = field(default_factory=list)

    # 机器码
    machine_code: str = field(default_factory=get_or_create_machine_code)

    # 主节点连接信息
    master_url: Optional[str] = None
    api_key: Optional[str] = None
    secret_key: Optional[str] = None
    is_connected: bool = False

    # 心跳配置
    heartbeat_interval: int = 30  # 心跳间隔（秒）
    heartbeat_timeout: int = 60   # 心跳超时时间（秒）

    # 任务配置
    max_concurrent_tasks: int = 0  # 最大并发任务数（0=自动计算）
    task_timeout: int = 3600       # 任务默认超时时间（秒）
    task_cpu_time_limit_sec: int = 0    # 单任务 CPU 时间上限（秒，0=自动）
    task_memory_limit_mb: int = 0       # 单任务内存上限（MB，0=自动）
    auto_resource_limit: bool = True    # 是否启用自适应资源限制

    # gRPC 配置 (Requirements: 8.3)
    grpc_enabled: bool = True           # 是否启用 gRPC 通信
    grpc_port: int = 50051              # gRPC 服务端口
    prefer_grpc: bool = True            # 是否优先使用 gRPC（否则使用 HTTP）
    grpc_reconnect_base_delay: float = 5.0   # gRPC 重连基础延迟（秒）
    grpc_reconnect_max_delay: float = 60.0   # gRPC 重连最大延迟（秒）

    # 存储配置
    data_dir: str = field(default_factory=lambda: str(NODE_ROOT / "data"))

    # 运行时信息
    start_time: datetime = field(default_factory=datetime.now)

    @property
    def projects_dir(self) -> str:
        """项目存储目录"""
        return os.path.join(self.data_dir, "projects")

    @property
    def venvs_dir(self) -> str:
        """虚拟环境存储目录"""
        return os.path.join(self.data_dir, "venvs")

    @property
    def logs_dir(self) -> str:
        """日志存储目录"""
        return os.path.join(self.data_dir, "logs")

    @property
    def executions_dir(self) -> str:
        """任务执行目录"""
        return os.path.join(self.data_dir, "executions")

    def ensure_directories(self):
        """确保所有存储目录存在"""
        for dir_path in [self.projects_dir, self.venvs_dir, self.logs_dir, self.executions_dir]:
            os.makedirs(dir_path, exist_ok=True)

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "name": self.name,
            "port": self.port,
            "host": self.host,
            "region": self.region,
            "version": self.version,
            "description": self.description,
            "tags": self.tags,
            "machine_code": self.machine_code,
            "master_url": self.master_url,
            "is_connected": self.is_connected,
            "heartbeat_interval": self.heartbeat_interval,
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "task_timeout": self.task_timeout,
            "task_cpu_time_limit_sec": self.task_cpu_time_limit_sec,
            "task_memory_limit_mb": self.task_memory_limit_mb,
            "auto_resource_limit": self.auto_resource_limit,
            "grpc_enabled": self.grpc_enabled,
            "grpc_port": self.grpc_port,
            "prefer_grpc": self.prefer_grpc,
            "grpc_reconnect_base_delay": self.grpc_reconnect_base_delay,
            "grpc_reconnect_max_delay": self.grpc_reconnect_max_delay,
            "data_dir": self.data_dir,
            "start_time": self.start_time.isoformat(),
        }

    def save_to_file(self, path: Optional[Path] = None):
        """保存配置到文件"""
        path = path or NODE_CONFIG_FILE
        config_data = {
            "name": self.name,
            "port": self.port,
            "region": self.region,
            "description": self.description,
            "tags": self.tags,
            "master_url": self.master_url,
            "heartbeat_interval": self.heartbeat_interval,
            "max_concurrent_tasks": self.max_concurrent_tasks,
            "task_timeout": self.task_timeout,
            "task_cpu_time_limit_sec": self.task_cpu_time_limit_sec,
            "task_memory_limit_mb": self.task_memory_limit_mb,
            "grpc_enabled": self.grpc_enabled,
            "grpc_port": self.grpc_port,
            "prefer_grpc": self.prefer_grpc,
            "grpc_reconnect_base_delay": self.grpc_reconnect_base_delay,
            "grpc_reconnect_max_delay": self.grpc_reconnect_max_delay,
            "data_dir": self.data_dir,
        }
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f, allow_unicode=True, default_flow_style=False)

    @classmethod
    def load_from_file(cls, path: Optional[Path] = None) -> "NodeConfig":
        """从文件加载配置"""
        path = path or NODE_CONFIG_FILE
        if not path.exists():
            return cls()

        try:
            with open(path, "r", encoding="utf-8") as f:
                config_data = yaml.safe_load(f) or {}
            return cls(**{k: v for k, v in config_data.items() if v is not None})
        except Exception as e:
            print(f"[Warning] Failed to load config: {e}")
            return cls()


# 全局配置实例
_node_config: Optional[NodeConfig] = None


def get_node_config() -> NodeConfig:
    """获取全局节点配置"""
    global _node_config
    if _node_config is None:
        _node_config = NodeConfig.load_from_file()
    return _node_config


def set_node_config(config: NodeConfig):
    """设置全局节点配置"""
    global _node_config
    _node_config = config


def calculate_adaptive_limits() -> Dict[str, int]:
    """
    根据系统资源自适应计算任务限制
    
    算法：
    - max_concurrent_tasks: min(CPU核心数, 可用内存GB / 2, 10)
    - task_memory_limit_mb: 可用内存 / (并发数 * 1.5)，预留 30% 给系统
    - task_cpu_time_limit_sec: 基于任务超时时间的 80%
    """
    import psutil

    cpu_count = psutil.cpu_count() or 4
    mem = psutil.virtual_memory()
    available_mem_gb = mem.available / (1024 ** 3)
    total_mem_gb = mem.total / (1024 ** 3)

    # 计算最大并发数：取 CPU 核心数、可用内存/2GB、硬上限 10 的最小值
    max_concurrent = min(
        cpu_count,
        max(1, int(available_mem_gb / 2)),
        10
    )

    # 计算单任务内存限制：总内存的 70% / 并发数，最小 512MB，最大 4GB
    usable_mem_mb = int(total_mem_gb * 0.7 * 1024)
    task_memory = max(512, min(4096, usable_mem_mb // max(1, max_concurrent)))

    # CPU 时间限制：默认 10 分钟，高性能机器可以更长
    task_cpu_time = min(1800, max(300, cpu_count * 60))

    return {
        "max_concurrent_tasks": max_concurrent,
        "task_memory_limit_mb": task_memory,
        "task_cpu_time_limit_sec": task_cpu_time,
    }


def init_node_config(
    name: str = "Worker-Node",
    port: int = 8001,
    region: str = "默认",
    **kwargs
) -> NodeConfig:
    """初始化节点配置"""
    config = NodeConfig(
        name=name,
        port=port,
        region=region,
        **kwargs
    )

    # 自适应资源限制
    if config.auto_resource_limit:
        adaptive = calculate_adaptive_limits()
        if config.max_concurrent_tasks <= 0:
            config.max_concurrent_tasks = adaptive["max_concurrent_tasks"]
        if config.task_memory_limit_mb <= 0:
            config.task_memory_limit_mb = adaptive["task_memory_limit_mb"]
        if config.task_cpu_time_limit_sec <= 0:
            config.task_cpu_time_limit_sec = adaptive["task_cpu_time_limit_sec"]

        print(f"[Adaptive] concurrent={config.max_concurrent_tasks}, "
              f"memory={config.task_memory_limit_mb}MB, cpu_time={config.task_cpu_time_limit_sec}s")

    config.ensure_directories()
    set_node_config(config)
    return config
