"""
gRPC 性能优化配置

提供 gRPC 通信的性能调优参数和优化建议。
基于基准测试结果进行配置。

**Validates: Requirements 13.5**
"""

from dataclasses import dataclass, field
from typing import Dict, Any, Optional
from loguru import logger


def _get_settings():
    """延迟导入 settings 以避免循环导入"""
    from src.core.config import settings
    return settings


@dataclass
class PerformanceConfig:
    """
    gRPC 性能配置
    
    基于基准测试结果优化的配置参数。
    目标: 1000+ 消息/秒吞吐量
    
    **Validates: Requirements 13.5**
    """
    
    # === 缓冲区配置 ===
    # 日志缓冲区最大大小（行数）
    # 建议: 2000-5000，根据内存和延迟要求调整
    log_buffer_max_size: int = field(
        default_factory=lambda: _get_settings().GRPC_LOG_BUFFER_MAX_SIZE
    )
    
    # 日志批次大小
    # 建议: 50-100，较大的批次减少网络开销但增加延迟
    log_batch_size: int = field(
        default_factory=lambda: _get_settings().GRPC_LOG_BATCH_SIZE
    )
    
    # 日志刷新间隔（秒）
    # 建议: 0.5-2.0，较短的间隔减少延迟但增加网络开销
    log_flush_interval: float = field(
        default_factory=lambda: _get_settings().GRPC_LOG_FLUSH_INTERVAL
    )
    
    # === 压缩配置 ===
    # 压缩阈值（字节）
    # 建议: 1024-4096，较小的阈值增加 CPU 开销但减少网络传输
    compress_threshold: int = field(
        default_factory=lambda: _get_settings().GRPC_COMPRESS_THRESHOLD
    )
    
    # === 队列配置 ===
    # 消息发送队列大小
    # 建议: 500-2000，根据消息产生速率调整
    send_queue_size: int = field(
        default_factory=lambda: _get_settings().GRPC_SEND_QUEUE_SIZE
    )
    
    # === 指标配置 ===
    # 延迟样本最大数量
    # 建议: 100-500，用于计算延迟统计
    metrics_max_latency_samples: int = field(
        default_factory=lambda: _get_settings().GRPC_METRICS_MAX_LATENCY_SAMPLES
    )
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "log_buffer_max_size": self.log_buffer_max_size,
            "log_batch_size": self.log_batch_size,
            "log_flush_interval": self.log_flush_interval,
            "compress_threshold": self.compress_threshold,
            "send_queue_size": self.send_queue_size,
            "metrics_max_latency_samples": self.metrics_max_latency_samples,
        }
    
    def validate(self) -> bool:
        """验证配置有效性"""
        errors = []
        
        if self.log_buffer_max_size < 100:
            errors.append("log_buffer_max_size 应至少为 100")
        if self.log_batch_size < 1:
            errors.append("log_batch_size 应至少为 1")
        if self.log_batch_size > self.log_buffer_max_size:
            errors.append("log_batch_size 不应大于 log_buffer_max_size")
        if self.log_flush_interval < 0.1:
            errors.append("log_flush_interval 应至少为 0.1 秒")
        if self.compress_threshold < 0:
            errors.append("compress_threshold 不应为负数")
        if self.send_queue_size < 10:
            errors.append("send_queue_size 应至少为 10")
        
        if errors:
            for error in errors:
                logger.error(f"性能配置验证失败: {error}")
            return False
        
        return True


@dataclass
class PerformanceProfile:
    """
    性能配置预设
    
    提供不同场景的预设配置。
    """
    name: str
    description: str
    config: PerformanceConfig


# 预设配置
PERFORMANCE_PROFILES: Dict[str, PerformanceProfile] = {}


def _init_profiles():
    """初始化性能配置预设"""
    global PERFORMANCE_PROFILES
    
    # 默认配置 - 平衡性能和资源使用
    PERFORMANCE_PROFILES["default"] = PerformanceProfile(
        name="default",
        description="默认配置 - 平衡性能和资源使用",
        config=PerformanceConfig(
            log_buffer_max_size=2000,
            log_batch_size=50,
            log_flush_interval=1.0,
            compress_threshold=1024,
            send_queue_size=1000,
            metrics_max_latency_samples=100,
        ),
    )
    
    # 高吞吐量配置 - 优化吞吐量，适合大量日志场景
    PERFORMANCE_PROFILES["high_throughput"] = PerformanceProfile(
        name="high_throughput",
        description="高吞吐量配置 - 优化吞吐量，适合大量日志场景",
        config=PerformanceConfig(
            log_buffer_max_size=5000,
            log_batch_size=100,
            log_flush_interval=2.0,
            compress_threshold=512,  # 更积极的压缩
            send_queue_size=2000,
            metrics_max_latency_samples=200,
        ),
    )
    
    # 低延迟配置 - 优化延迟，适合实时监控场景
    PERFORMANCE_PROFILES["low_latency"] = PerformanceProfile(
        name="low_latency",
        description="低延迟配置 - 优化延迟，适合实时监控场景",
        config=PerformanceConfig(
            log_buffer_max_size=1000,
            log_batch_size=20,
            log_flush_interval=0.5,
            compress_threshold=2048,  # 减少压缩开销
            send_queue_size=500,
            metrics_max_latency_samples=50,
        ),
    )
    
    # 资源受限配置 - 最小化资源使用
    PERFORMANCE_PROFILES["resource_constrained"] = PerformanceProfile(
        name="resource_constrained",
        description="资源受限配置 - 最小化内存和 CPU 使用",
        config=PerformanceConfig(
            log_buffer_max_size=500,
            log_batch_size=30,
            log_flush_interval=3.0,
            compress_threshold=4096,  # 减少压缩
            send_queue_size=200,
            metrics_max_latency_samples=50,
        ),
    )


# 初始化预设
_init_profiles()


def get_performance_config(profile_name: str = "default") -> PerformanceConfig:
    """
    获取性能配置
    
    Args:
        profile_name: 配置预设名称
        
    Returns:
        性能配置实例
    """
    if profile_name in PERFORMANCE_PROFILES:
        return PERFORMANCE_PROFILES[profile_name].config
    
    logger.warning(f"未知的性能配置预设: {profile_name}，使用默认配置")
    return PERFORMANCE_PROFILES["default"].config


def get_performance_profile(profile_name: str) -> Optional[PerformanceProfile]:
    """
    获取性能配置预设
    
    Args:
        profile_name: 配置预设名称
        
    Returns:
        性能配置预设，如果不存在则返回 None
    """
    return PERFORMANCE_PROFILES.get(profile_name)


def list_performance_profiles() -> Dict[str, str]:
    """
    列出所有性能配置预设
    
    Returns:
        预设名称到描述的映射
    """
    return {
        name: profile.description
        for name, profile in PERFORMANCE_PROFILES.items()
    }


# 全局性能配置实例
_performance_config: Optional[PerformanceConfig] = None


def get_global_performance_config() -> PerformanceConfig:
    """获取全局性能配置实例"""
    global _performance_config
    if _performance_config is None:
        _performance_config = PerformanceConfig()
    return _performance_config


def set_global_performance_config(config: PerformanceConfig) -> None:
    """设置全局性能配置实例"""
    global _performance_config
    if config.validate():
        _performance_config = config
        logger.info(f"已更新全局性能配置: {config.to_dict()}")
    else:
        raise ValueError("性能配置验证失败")


def apply_performance_profile(profile_name: str) -> PerformanceConfig:
    """
    应用性能配置预设
    
    Args:
        profile_name: 配置预设名称
        
    Returns:
        应用的性能配置
    """
    config = get_performance_config(profile_name)
    set_global_performance_config(config)
    logger.info(f"已应用性能配置预设: {profile_name}")
    return config
