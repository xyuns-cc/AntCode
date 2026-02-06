"""
日志分片接收器 - 存储通道

实现日志传输双通道架构中的 Master 端存储通道接收器。
负责接收 Worker 发送的日志分片，幂等写入到磁盘，并返回 ACK。

Requirements: 1.4, 1.5, 4.1, 4.2, 4.3, 4.4
"""

import asyncio
import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import aiofiles
from loguru import logger

from antcode_core.common.config import settings


@dataclass
class LogChunkAckResult:
    """日志分片 ACK 结果"""
    execution_id: str
    log_type: str
    ack_offset: int
    ok: bool
    error: str = ""


@dataclass
class ReceiverState:
    """接收器状态（每个 execution_id:log_type 一个）"""
    # 已连续写入的最大 offset
    contiguous_offset: int = 0
    # 已接收的分片 offset 集合（用于处理乱序）
    received_offsets: set = field(default_factory=set)
    # 文件总大小（is_final=true 时设置）
    total_size: int = -1
    # 是否已完成
    completed: bool = False


class LogChunkReceiver:
    """
    日志分片接收器 - 存储通道
    
    负责接收 Worker 发送的日志分片，幂等写入到磁盘，并返回 ACK。
    
    特性：
    - 幂等写入：按 offset 定位写入，重复分片不影响最终结果
    - 写锁保护：防止并发写入冲突
    - 完整性校验：is_final=true 时验证 total_size
    - ACK 返回：返回已连续写入的最大 offset
    
    Requirements: 1.4, 1.5, 4.1, 4.2, 4.3, 4.4
    """
    
    def __init__(self, storage_path: Optional[str] = None):
        """
        初始化日志分片接收器
        
        Args:
            storage_path: 日志存储根目录，默认使用 settings.TASK_LOG_DIR
        """
        self._storage_path = Path(storage_path) if storage_path else Path(settings.TASK_LOG_DIR)
        self._storage_path.mkdir(parents=True, exist_ok=True)
        
        # 写锁（按 execution_id 分组）
        self._write_locks: dict[str, asyncio.Lock] = {}
        
        # 接收器状态（按 execution_id:log_type 分组）
        self._states: dict[str, ReceiverState] = {}
        
        # 状态锁
        self._state_lock = asyncio.Lock()
    
    # ==================== 公共接口 ====================
    
    async def handle_chunk(
        self,
        execution_id: str,
        log_type: str,
        chunk: bytes,
        offset: int,
        is_final: bool,
        checksum: str = "",
        total_size: int = -1,
    ) -> LogChunkAckResult:
        """
        处理日志分片
        
        接收日志分片，幂等写入到磁盘，返回 ACK。
        
        Args:
            execution_id: 任务执行 ID
            log_type: 日志类型 (stdout/stderr)
            chunk: 分片数据
            offset: 文件偏移量
            is_final: 是否为最后一片
            checksum: 分片校验和（可选）
            total_size: 最终文件大小（仅 is_final=true 时有效）
            
        Returns:
            LogChunkAckResult: ACK 结果
            
        Requirements: 1.4, 1.5, 4.1, 4.2, 4.3, 4.4
        """
        state_key = f"{execution_id}:{log_type}"
        
        try:
            # 获取或创建状态
            state = await self._get_or_create_state(state_key)
            
            # 检查是否已完成
            if state.completed:
                logger.debug(
                    f"[{execution_id}/{log_type}] 传输已完成，忽略分片: offset={offset}"
                )
                return LogChunkAckResult(
                    execution_id=execution_id,
                    log_type=log_type,
                    ack_offset=state.contiguous_offset,
                    ok=True,
                )
            
            # 校验分片（如果提供了 checksum）
            if checksum and chunk:
                if not self._verify_checksum(chunk, checksum):
                    logger.warning(
                        f"[{execution_id}/{log_type}] 分片校验失败: offset={offset}"
                    )
                    return LogChunkAckResult(
                        execution_id=execution_id,
                        log_type=log_type,
                        ack_offset=state.contiguous_offset,
                        ok=False,
                        error="checksum mismatch",
                    )
            
            # 幂等写入分片
            write_result = await self._write_chunk(
                execution_id=execution_id,
                log_type=log_type,
                chunk=chunk,
                offset=offset,
            )
            
            if not write_result.ok:
                return write_result
            
            # 更新状态
            await self._update_state(
                state_key=state_key,
                offset=offset,
                chunk_size=len(chunk),
                is_final=is_final,
                total_size=total_size,
            )
            
            # 获取更新后的状态
            state = await self._get_state(state_key)
            
            # 如果是最终分片，验证完整性
            if is_final and total_size >= 0:
                verify_result = await self._verify_final(
                    execution_id=execution_id,
                    log_type=log_type,
                    total_size=total_size,
                )
                
                if not verify_result.ok:
                    return verify_result
                
                # 标记完成
                await self._mark_completed(state_key)
                
                logger.info(
                    f"[{execution_id}/{log_type}] 传输完成: total_size={total_size}"
                )
            
            logger.debug(
                f"[{execution_id}/{log_type}] 分片处理成功: "
                f"offset={offset}, size={len(chunk)}, "
                f"ack_offset={state.contiguous_offset}"
            )
            
            return LogChunkAckResult(
                execution_id=execution_id,
                log_type=log_type,
                ack_offset=state.contiguous_offset,
                ok=True,
            )
            
        except Exception as e:
            logger.error(
                f"[{execution_id}/{log_type}] 分片处理失败: "
                f"offset={offset}, error={e}"
            )
            
            # 获取当前状态的 ack_offset
            state = await self._get_state(state_key)
            ack_offset = state.contiguous_offset if state else 0
            
            return LogChunkAckResult(
                execution_id=execution_id,
                log_type=log_type,
                ack_offset=ack_offset,
                ok=False,
                error=str(e),
            )
    
    def get_log_file_path(self, execution_id: str, log_type: str) -> Path:
        """
        获取日志文件路径
        
        Args:
            execution_id: 任务执行 ID
            log_type: 日志类型 (stdout/stderr)
            
        Returns:
            日志文件路径
        """
        log_dir = self._storage_path / execution_id
        filename = f"{log_type}.log"
        return log_dir / filename
    
    async def get_file_size(self, execution_id: str, log_type: str) -> int:
        """
        获取日志文件大小
        
        Args:
            execution_id: 任务执行 ID
            log_type: 日志类型 (stdout/stderr)
            
        Returns:
            文件大小（字节），文件不存在返回 0
        """
        log_file = self.get_log_file_path(execution_id, log_type)
        if log_file.exists():
            return log_file.stat().st_size
        return 0
    
    async def get_contiguous_offset(self, execution_id: str, log_type: str) -> int:
        """
        获取已连续写入的最大 offset
        
        Args:
            execution_id: 任务执行 ID
            log_type: 日志类型 (stdout/stderr)
            
        Returns:
            已连续写入的最大 offset
        """
        state_key = f"{execution_id}:{log_type}"
        state = await self._get_state(state_key)
        return state.contiguous_offset if state else 0
    
    async def is_completed(self, execution_id: str, log_type: str) -> bool:
        """
        检查传输是否已完成
        
        Args:
            execution_id: 任务执行 ID
            log_type: 日志类型 (stdout/stderr)
            
        Returns:
            是否已完成
        """
        state_key = f"{execution_id}:{log_type}"
        state = await self._get_state(state_key)
        return state.completed if state else False
    
    def clear_state(self, execution_id: str) -> None:
        """
        清理指定执行 ID 的状态
        
        Args:
            execution_id: 任务执行 ID
        """
        for log_type in ["stdout", "stderr"]:
            state_key = f"{execution_id}:{log_type}"
            if state_key in self._states:
                del self._states[state_key]
        
        if execution_id in self._write_locks:
            del self._write_locks[execution_id]
    
    # ==================== 内部方法 ====================
    
    def _get_write_lock(self, execution_id: str) -> asyncio.Lock:
        """
        获取写锁
        
        Args:
            execution_id: 任务执行 ID
            
        Returns:
            写锁
        """
        if execution_id not in self._write_locks:
            self._write_locks[execution_id] = asyncio.Lock()
        return self._write_locks[execution_id]
    
    async def _get_or_create_state(self, state_key: str) -> ReceiverState:
        """
        获取或创建接收器状态
        
        Args:
            state_key: 状态键 (execution_id:log_type)
            
        Returns:
            接收器状态
        """
        async with self._state_lock:
            if state_key not in self._states:
                self._states[state_key] = ReceiverState()
            return self._states[state_key]
    
    async def _get_state(self, state_key: str) -> Optional[ReceiverState]:
        """
        获取接收器状态
        
        Args:
            state_key: 状态键 (execution_id:log_type)
            
        Returns:
            接收器状态，不存在返回 None
        """
        async with self._state_lock:
            return self._states.get(state_key)
    
    async def _update_state(
        self,
        state_key: str,
        offset: int,
        chunk_size: int,
        is_final: bool,
        total_size: int,
    ) -> None:
        """
        更新接收器状态
        
        Args:
            state_key: 状态键 (execution_id:log_type)
            offset: 分片偏移量
            chunk_size: 分片大小
            is_final: 是否为最终分片
            total_size: 最终文件大小
        """
        async with self._state_lock:
            state = self._states.get(state_key)
            if not state:
                return
            
            # 记录已接收的分片
            chunk_end = offset + chunk_size
            state.received_offsets.add((offset, chunk_end))
            
            # 更新连续 offset
            state.contiguous_offset = self._calculate_contiguous_offset(
                state.received_offsets
            )
            
            # 更新最终大小
            if is_final and total_size >= 0:
                state.total_size = total_size
    
    def _calculate_contiguous_offset(
        self,
        received_offsets: set,
    ) -> int:
        """
        计算已连续写入的最大 offset
        
        Args:
            received_offsets: 已接收的分片 offset 集合 (start, end)
            
        Returns:
            已连续写入的最大 offset
        """
        if not received_offsets:
            return 0
        
        # 按起始 offset 排序
        sorted_offsets = sorted(received_offsets, key=lambda x: x[0])
        
        # 计算连续区间
        contiguous_end = 0
        for start, end in sorted_offsets:
            if start <= contiguous_end:
                # 连续或重叠
                contiguous_end = max(contiguous_end, end)
            else:
                # 有间隙，停止
                break
        
        return contiguous_end
    
    async def _mark_completed(self, state_key: str) -> None:
        """
        标记传输完成
        
        Args:
            state_key: 状态键 (execution_id:log_type)
        """
        async with self._state_lock:
            state = self._states.get(state_key)
            if state:
                state.completed = True
    
    def _verify_checksum(self, chunk: bytes, expected_checksum: str) -> bool:
        """
        验证分片校验和
        
        Args:
            chunk: 分片数据
            expected_checksum: 期望的校验和（sha256 前 16 位）
            
        Returns:
            是否匹配
        """
        actual_checksum = hashlib.sha256(chunk).hexdigest()[:16]
        return actual_checksum == expected_checksum
    
    async def _write_chunk(
        self,
        execution_id: str,
        log_type: str,
        chunk: bytes,
        offset: int,
    ) -> LogChunkAckResult:
        """
        幂等写入分片到文件
        
        按 offset 定位写入，重复写入相同 offset 的分片不影响最终结果。
        
        Args:
            execution_id: 任务执行 ID
            log_type: 日志类型 (stdout/stderr)
            chunk: 分片数据
            offset: 文件偏移量
            
        Returns:
            写入结果
            
        Requirements: 4.1, 4.2
        """
        # 空分片（最终分片可能为空）
        if not chunk:
            return LogChunkAckResult(
                execution_id=execution_id,
                log_type=log_type,
                ack_offset=offset,
                ok=True,
            )
        
        # 获取日志文件路径
        log_dir = self._storage_path / execution_id
        log_dir.mkdir(parents=True, exist_ok=True)
        
        log_file = log_dir / f"{log_type}.log"
        
        # 获取写锁
        write_lock = self._get_write_lock(execution_id)
        
        async with write_lock:
            try:
                # 使用 r+b 模式打开（如果文件存在）或 wb 模式创建
                if log_file.exists():
                    async with aiofiles.open(log_file, "r+b") as f:
                        # 定位到指定 offset
                        await f.seek(offset)
                        # 写入分片
                        await f.write(chunk)
                else:
                    # 文件不存在，需要先创建并填充到 offset
                    async with aiofiles.open(log_file, "wb") as f:
                        if offset > 0:
                            # 填充空字节到 offset
                            await f.write(b"\x00" * offset)
                        # 写入分片
                        await f.write(chunk)
                
                return LogChunkAckResult(
                    execution_id=execution_id,
                    log_type=log_type,
                    ack_offset=offset + len(chunk),
                    ok=True,
                )
                
            except OSError as e:
                error_msg = f"disk error: {e}"
                if "No space left" in str(e) or e.errno == 28:
                    error_msg = "disk full"
                
                logger.error(
                    f"[{execution_id}/{log_type}] 写入分片失败: "
                    f"offset={offset}, error={e}"
                )
                
                return LogChunkAckResult(
                    execution_id=execution_id,
                    log_type=log_type,
                    ack_offset=0,
                    ok=False,
                    error=error_msg,
                )
    
    async def _verify_final(
        self,
        execution_id: str,
        log_type: str,
        total_size: int,
    ) -> LogChunkAckResult:
        """
        验证最终文件完整性
        
        检查文件大小是否与 total_size 匹配。
        
        Args:
            execution_id: 任务执行 ID
            log_type: 日志类型 (stdout/stderr)
            total_size: 期望的文件大小
            
        Returns:
            验证结果
            
        Requirements: 4.3, 4.4
        """
        log_file = self.get_log_file_path(execution_id, log_type)
        
        if not log_file.exists():
            # 文件不存在，如果 total_size 为 0 则正常
            if total_size == 0:
                return LogChunkAckResult(
                    execution_id=execution_id,
                    log_type=log_type,
                    ack_offset=0,
                    ok=True,
                )
            
            return LogChunkAckResult(
                execution_id=execution_id,
                log_type=log_type,
                ack_offset=0,
                ok=False,
                error=f"file not found, expected size={total_size}",
            )
        
        actual_size = log_file.stat().st_size
        
        if actual_size != total_size:
            logger.warning(
                f"[{execution_id}/{log_type}] 文件大小不匹配: "
                f"actual={actual_size}, expected={total_size}"
            )
            
            return LogChunkAckResult(
                execution_id=execution_id,
                log_type=log_type,
                ack_offset=actual_size,
                ok=False,
                error=f"size mismatch: actual={actual_size}, expected={total_size}",
            )
        
        return LogChunkAckResult(
            execution_id=execution_id,
            log_type=log_type,
            ack_offset=total_size,
            ok=True,
        )


# 全局实例
log_chunk_receiver = LogChunkReceiver()


def get_log_chunk_receiver() -> LogChunkReceiver:
    """获取全局 LogChunkReceiver 实例"""
    return log_chunk_receiver
