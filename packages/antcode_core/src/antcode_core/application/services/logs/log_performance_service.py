"""
日志性能监控服务
提供详细的性能指标和统计分析
"""

import asyncio
import time
from collections import defaultdict, deque
from dataclasses import dataclass, asdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict

import psutil
from loguru import logger


@dataclass
class PerformanceMetric:
    """性能指标数据类"""
    timestamp: float
    operation: str
    duration: float
    bytes_processed: int = 0
    lines_processed: int = 0
    memory_usage: float = 0.0
    cpu_usage: float = 0.0
    success: bool = True
    error_message = None


class LogPerformanceMonitor:
    """日志性能监控器"""
    
    def __init__(self, max_history = 10000):
        self.max_history = max_history
        self.metrics_history = deque(maxlen=max_history)
        
        # 实时统计
        self.operation_stats = defaultdict(lambda: {
            "count": 0,
            "total_duration": 0.0,
            "total_bytes": 0,
            "total_lines": 0,
            "errors": 0,
            "avg_duration": 0.0,
            "min_duration": float('inf'),
            "max_duration": 0.0
        })
        
        # 系统资源监控
        self.system_metrics = {}
        self._monitor_task = None
        # 不在初始化时启动监控，而是在需要时启动
    
    def _start_system_monitoring(self):
        """启动系统监控"""
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(self._monitor_system_resources())
    
    async def _monitor_system_resources(self):
        """监控系统资源"""
        while True:
            try:
                # CPU使用率
                cpu_percent = psutil.cpu_percent(interval=1)
                
                # 内存使用情况
                memory = psutil.virtual_memory()
                
                # 磁盘I/O
                disk_io = psutil.disk_io_counters()
                
                self.system_metrics = {
                    "cpu_percent": cpu_percent,
                    "memory_percent": memory.percent,
                    "memory_available_mb": memory.available / 1024 / 1024,
                    "memory_used_mb": memory.used / 1024 / 1024,
                    "disk_read_mb": disk_io.read_bytes / 1024 / 1024 if disk_io else 0,
                    "disk_write_mb": disk_io.write_bytes / 1024 / 1024 if disk_io else 0,
                    "timestamp": time.time()
                }
                
                await asyncio.sleep(30)  # 每30秒更新一次
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"系统监控异常: {e}")
                await asyncio.sleep(60)  # 出错时等待更长时间
    
    async def record_metric(self, operation, duration, 
                          bytes_processed = 0, lines_processed = 0,
                          success = True, error_message = None):
        """记录性能指标"""
        
        # 获取当前系统资源使用情况
        try:
            process = psutil.Process()
            memory_usage = process.memory_info().rss / 1024 / 1024  # MB
            cpu_usage = process.cpu_percent()
        except Exception:
            memory_usage = 0.0
            cpu_usage = 0.0
        
        metric = PerformanceMetric(
            timestamp=time.time(),
            operation=operation,
            duration=duration,
            bytes_processed=bytes_processed,
            lines_processed=lines_processed,
            memory_usage=memory_usage,
            cpu_usage=cpu_usage,
            success=success,
            error_message=error_message
        )
        
        # 添加到历史记录
        self.metrics_history.append(metric)
        
        # 更新操作统计
        stats = self.operation_stats[operation]
        stats["count"] += 1
        stats["total_duration"] += duration
        stats["total_bytes"] += bytes_processed
        stats["total_lines"] += lines_processed
        
        if not success:
            stats["errors"] += 1
        
        # 更新平均值和极值
        stats["avg_duration"] = stats["total_duration"] / stats["count"]
        stats["min_duration"] = min(stats["min_duration"], duration)
        stats["max_duration"] = max(stats["max_duration"], duration)
        
        # 如果性能异常，记录警告
        if duration > 5.0:  # 超过5秒的操作
            logger.warning(f"检测到慢操作: {operation}, 耗时: {duration:.2f}s")
        
        if memory_usage > 500:  # 超过500MB内存使用
            logger.warning(f"检测到高内存使用: {operation}, 内存: {memory_usage:.2f}MB")
    
    def get_operation_stats(self, operation = None):
        """获取操作统计"""
        if operation:
            return dict(self.operation_stats.get(operation, {}))
        else:
            return {op: dict(stats) for op, stats in self.operation_stats.items()}
    
    def get_recent_metrics(self, minutes = 5):
        """获取最近N分钟的指标"""
        cutoff_time = time.time() - (minutes * 60)
        
        recent_metrics = [
            asdict(metric) for metric in self.metrics_history
            if metric.timestamp > cutoff_time
        ]
        
        return recent_metrics
    
    def get_performance_summary(self):
        """获取性能摘要"""
        if not self.metrics_history:
            return {"message": "暂无性能数据"}
        
        recent_metrics = self.get_recent_metrics(60)  # 最近1小时
        
        total_operations = len(recent_metrics)
        successful_operations = sum(1 for m in recent_metrics if m["success"])
        failed_operations = total_operations - successful_operations
        
        if total_operations > 0:
            avg_duration = sum(m["duration"] for m in recent_metrics) / total_operations
            total_bytes = sum(m["bytes_processed"] for m in recent_metrics)
            total_lines = sum(m["lines_processed"] for m in recent_metrics)
            
            # 计算吞吐量
            throughput_mb_per_sec = (total_bytes / 1024 / 1024) / 3600  # MB/s over 1 hour
            lines_per_sec = total_lines / 3600
        else:
            avg_duration = 0
            total_bytes = 0
            total_lines = 0
            throughput_mb_per_sec = 0
            lines_per_sec = 0
        
        return {
            "time_window": "最近1小时",
            "total_operations": total_operations,
            "successful_operations": successful_operations,
            "failed_operations": failed_operations,
            "success_rate": successful_operations / total_operations if total_operations > 0 else 0,
            "avg_duration_ms": round(avg_duration * 1000, 2),
            "total_bytes_processed": total_bytes,
            "total_lines_processed": total_lines,
            "throughput_mb_per_sec": round(throughput_mb_per_sec, 3),
            "lines_per_sec": round(lines_per_sec, 1),
            "system_metrics": self.system_metrics,
            "operation_breakdown": self.get_operation_stats()
        }
    
    def get_slow_operations(self, threshold_seconds = 1.0, 
                          limit = 10):
        """获取慢操作列表"""
        slow_ops = [
            asdict(metric) for metric in self.metrics_history
            if metric.duration > threshold_seconds
        ]
        
        # 按耗时降序排序
        slow_ops.sort(key=lambda x: x["duration"], reverse=True)
        
        return slow_ops[:limit]
    
    async def shutdown(self):
        """关闭监控"""
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        
        logger.info("性能监控已关闭")


def performance_monitor(operation):
    """性能监控装饰器"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            bytes_processed = 0
            lines_processed = 0
            success = True
            error_message = None
            
            try:
                result = await func(*args, **kwargs)
                
                # 尝试从结果中提取处理信息
                if isinstance(result, dict):
                    if "bytes_processed" in result:
                        bytes_processed = result["bytes_processed"]
                    if "lines_processed" in result:
                        lines_processed = result["lines_processed"]
                
                return result
                
            except Exception as e:
                success = False
                error_message = str(e)
                raise
                
            finally:
                duration = time.time() - start_time
                
                # 记录性能指标
                await log_performance_monitor.record_metric(
                    operation=operation,
                    duration=duration,
                    bytes_processed=bytes_processed,
                    lines_processed=lines_processed,
                    success=success,
                    error_message=error_message
                )
        
        return wrapper
    return decorator


class LogStatisticsService:
    """日志统计服务"""
    
    def __init__(self):
        self.daily_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {
            "requests": 0,
            "bytes_served": 0,
            "lines_served": 0,
            "errors": 0,
            "unique_users": 0,
            "websocket_connections": 0
        })
        
        self.user_activity: Dict[int, Dict[str, Any]] = defaultdict(lambda: {
            "last_seen": None,
            "requests_today": 0,
            "total_bytes": 0,
            "errors": 0
        })
    
    def record_user_activity(self, user_id, bytes_processed = 0, 
                           is_error = False):
        """记录用户活动"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        
        # 更新日统计
        self.daily_stats[today]["requests"] += 1
        self.daily_stats[today]["bytes_served"] += bytes_processed
        
        if is_error:
            self.daily_stats[today]["errors"] += 1
        
        # 更新用户活动
        user_stats = self.user_activity[user_id]
        user_stats["last_seen"] = datetime.now(timezone.utc)
        user_stats["requests_today"] += 1
        user_stats["total_bytes"] += bytes_processed
        
        if is_error:
            user_stats["errors"] += 1
    
    def get_daily_statistics(self, days = 7):
        """获取最近N天的统计"""
        stats = {}
        
        for i in range(days):
            date = (datetime.now(timezone.utc) - timedelta(days=i)).strftime("%Y-%m-%d")
            stats[date] = dict(self.daily_stats[date])
        
        return stats
    
    def get_user_statistics(self):
        """获取用户统计"""
        active_users_today = 0
        total_requests_today = 0
        
        for user_id, stats in self.user_activity.items():
            if stats["last_seen"]:
                last_seen = stats["last_seen"]
                if (datetime.now(timezone.utc) - last_seen).days == 0:
                    active_users_today += 1
                    total_requests_today += stats["requests_today"]
        
        return {
            "total_registered_users": len(self.user_activity),
            "active_users_today": active_users_today,
            "total_requests_today": total_requests_today,
            "avg_requests_per_user": total_requests_today / max(active_users_today, 1)
        }


# 创建全局实例
log_performance_monitor = LogPerformanceMonitor()
log_statistics_service = LogStatisticsService()