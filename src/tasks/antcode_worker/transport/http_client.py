"""
HTTP 传输客户端

基于 httpx 实现的 HTTP 传输协议。
作为 gRPC 不可用时的回退方案。

Requirements: 11.2
"""

import asyncio
import hashlib
import hmac
import platform
import time
import uuid
from typing import Optional, Dict, Any, Callable, Awaitable, List

import httpx
from loguru import logger

from ..domain.models import (
    ConnectionConfig,
    Heartbeat,
    LogEntry,
    TaskStatus,
    TaskDispatch,
    TaskCancel,
    GrpcMetrics,
    OSInfo,
    Metrics,
)
from .protocol import TransportProtocol, ConnectionError, SendError


class HttpClient(TransportProtocol):
    """
    HTTP 传输客户端
    
    实现 TransportProtocol 接口，使用 HTTP/REST API 与 Master 通信。
    支持 HTTP/2（如果安装了 h2 库）。
    
    特性:
    - HTTP/2 连接池
    - HMAC 签名验证
    - 指数退避重试
    - 请求指标统计
    
    Requirements: 11.2
    """

    # 配置常量
    MAX_RETRY_COUNT = 3
    RETRY_BASE_DELAY = 1.0
    REQUEST_TIMEOUT = 10.0
    CONNECT_TIMEOUT = 5.0

    def __init__(self):
        self._config: Optional[ConnectionConfig] = None
        self._http_client: Optional[httpx.AsyncClient] = None
        self._connected = False
        self._metrics = GrpcMetrics()
        
        # 回调函数
        self._on_task_dispatch: Optional[Callable[[TaskDispatch], Awaitable[None]]] = None
        self._on_task_cancel: Optional[Callable[[TaskCancel], Awaitable[None]]] = None
        
        # OS 信息缓存
        self._os_info_cache: Optional[Dict[str, str]] = None

    @property
    def protocol_name(self) -> str:
        return "http"

    @property
    def is_connected(self) -> bool:
        return self._connected and self._config is not None

    @property
    def metrics(self) -> GrpcMetrics:
        return self._metrics

    async def connect(self, config: ConnectionConfig) -> bool:
        """建立 HTTP 连接"""
        self._config = config

        if not config.access_token:
            raise ConnectionError("缺少访问令牌")
        
        try:
            client = await self._get_http_client()
            response = await client.get(
                f"{config.master_url}/api/v1/health",
                headers={"Authorization": f"Bearer {config.access_token}"},
            )
            
            if response.status_code != 200:
                raise ConnectionError(f"连接失败: HTTP {response.status_code}")
            
            self._connected = True
            self._metrics.connected_at = __import__('datetime').datetime.now()
            
            logger.info(f"HTTP 连接成功: {config.master_url}")
            return True
            
        except httpx.ConnectError as e:
            raise ConnectionError(f"无法连接: {e}")
        except httpx.TimeoutException:
            raise ConnectionError("连接超时")
        except Exception as e:
            raise ConnectionError(f"连接异常: {e}")

    async def disconnect(self):
        """断开 HTTP 连接"""
        await self._close_http_client()
        self._connected = False
        self._config = None
        logger.info("HTTP 连接已断开")

    async def send_heartbeat(self, heartbeat: Heartbeat) -> bool:
        """发送心跳"""
        if not self.is_connected or not self._config:
            return False

        try:
            payload = {
                "node_id": heartbeat.node_id,
                "api_key": self._config.api_key,
                "status": heartbeat.status,
                "metrics": heartbeat.metrics.to_dict(),
                **heartbeat.os_info.to_dict(),
                "capabilities": heartbeat.capabilities,
            }

            response = await self._request_with_retry(
                "POST",
                f"{self._config.master_url}/api/v1/nodes/heartbeat",
                payload,
                max_retries=1
            )

            if response and response.status_code == 200:
                self._metrics.messages_sent += 1
                logger.debug(f"心跳发送成功")
                return True
            else:
                logger.warning(f"心跳发送失败: {response.status_code if response else '无响应'}")
                return False
                
        except Exception as e:
            logger.warning(f"心跳发送异常: {e}")
            return False

    async def send_logs(self, logs: List[LogEntry]) -> bool:
        """发送日志批次"""
        if not self.is_connected or not self._config or not logs:
            return False

        try:
            payload = {
                "logs": [log.to_dict() for log in logs],
                "machine_code": self._config.machine_code,
            }

            response = await self._request_with_retry(
                "POST",
                f"{self._config.master_url}/api/v1/nodes/report-logs-batch",
                payload,
                max_retries=2
            )

            if response and response.status_code == 200:
                self._metrics.messages_sent += 1
                return True
            else:
                # 批量失败，尝试单条发送
                logger.warning(f"批量日志发送失败，尝试单条发送")
                for log in logs:
                    await self._send_single_log(log)
                return True
                
        except Exception as e:
            logger.warning(f"日志发送异常: {e}")
            return False

    async def _send_single_log(self, log: LogEntry):
        """发送单条日志"""
        if not self._config:
            return
            
        try:
            payload = {
                "execution_id": log.execution_id,
                "log_type": log.log_type,
                "content": log.content,
                "machine_code": self._config.machine_code,
            }
            response = await self._request_with_retry(
                "POST",
                f"{self._config.master_url}/api/v1/nodes/report-log",
                payload,
                max_retries=1
            )
            if response and response.status_code != 200:
                logger.debug(f"单条日志发送失败: HTTP {response.status_code}")
        except Exception as e:
            logger.debug(f"单条日志发送异常: {e}")

    async def send_task_status(self, status: TaskStatus) -> bool:
        """发送任务状态"""
        if not self.is_connected or not self._config:
            return False

        try:
            payload = {
                "execution_id": status.execution_id,
                "status": status.status,
                "exit_code": status.exit_code,
                "error_message": status.error_message,
                "machine_code": self._config.machine_code,
            }

            response = await self._request_with_retry(
                "POST",
                f"{self._config.master_url}/api/v1/nodes/report-task",
                payload
            )

            if response and response.status_code == 200:
                self._metrics.messages_sent += 1
                return True
            return False
            
        except Exception as e:
            logger.warning(f"任务状态发送异常: {e}")
            return False

    def on_task_dispatch(self, callback: Callable[[TaskDispatch], Awaitable[None]]):
        """注册任务分发回调"""
        self._on_task_dispatch = callback

    def on_task_cancel(self, callback: Callable[[TaskCancel], Awaitable[None]]):
        """注册任务取消回调"""
        self._on_task_cancel = callback

    # ==================== 内部方法 ====================

    async def _get_http_client(self) -> httpx.AsyncClient:
        """获取或创建 HTTP 客户端"""
        if self._http_client is None or self._http_client.is_closed:
            http2_enabled = False
            try:
                import h2  # noqa: F401
                http2_enabled = True
            except ImportError:
                logger.debug("未安装 h2，使用 HTTP/1.1")
                
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=self.CONNECT_TIMEOUT,
                    read=self.REQUEST_TIMEOUT,
                    write=self.REQUEST_TIMEOUT,
                    pool=self.REQUEST_TIMEOUT
                ),
                limits=httpx.Limits(
                    max_connections=10,
                    max_keepalive_connections=5,
                    keepalive_expiry=30.0
                ),
                trust_env=False,
                http2=http2_enabled,
            )
        return self._http_client

    async def _close_http_client(self):
        """关闭 HTTP 客户端"""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    def _generate_signature(self, payload: Dict[str, Any], timestamp: int, nonce: str) -> str:
        """生成 HMAC 签名"""
        if not self._config or not self._config.secret_key:
            return ""
            
        import json
        sorted_payload = json.dumps(dict(sorted(payload.items())), separators=(',', ':'))
        sign_string = f"{timestamp}.{nonce}.{sorted_payload}"
        return hmac.new(
            self._config.secret_key.encode(),
            sign_string.encode(),
            hashlib.sha256
        ).hexdigest()

    def _build_headers(self, payload: Dict[str, Any] = None) -> Dict[str, str]:
        """构建请求头"""
        if not self._config:
            return {}
            
        timestamp = int(time.time())
        nonce = uuid.uuid4().hex[:16]

        headers = {
            "Authorization": f"Bearer {self._config.access_token}",
            "X-Timestamp": str(timestamp),
            "X-Nonce": nonce,
            "X-Node-ID": self._config.node_id or "",
            "X-Machine-Code": self._config.machine_code or "",
            "Accept-Encoding": "gzip",
        }

        if payload and self._config.secret_key:
            headers["X-Signature"] = self._generate_signature(payload, timestamp, nonce)

        return headers

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        json_data: Dict = None,
        max_retries: int = None
    ) -> Optional[httpx.Response]:
        """带重试的请求"""
        max_retries = max_retries or self.MAX_RETRY_COUNT
        client = await self._get_http_client()

        for attempt in range(max_retries):
            try:
                start_time = time.time()
                headers = self._build_headers(json_data)

                if method.upper() == "GET":
                    response = await client.get(url, headers=headers)
                else:
                    response = await client.post(url, json=json_data, headers=headers)

                latency = (time.time() - start_time) * 1000
                self._metrics.record_latency(latency)

                return response
                
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                if attempt < max_retries - 1:
                    delay = self.RETRY_BASE_DELAY * (2 ** attempt)
                    logger.debug(f"重试 {attempt + 1}/{max_retries} ({delay:.1f}s): {e}")
                    await asyncio.sleep(delay)
                else:
                    logger.warning(f"请求失败 (已重试 {max_retries} 次): {e}")
                    return None
            except Exception as e:
                logger.error(f"请求异常: {e}")
                return None

        return None

    async def fetch_pending_tasks(self) -> List[Dict[str, Any]]:
        """获取待执行任务"""
        if not self.is_connected or not self._config:
            return []

        try:
            response = await self._request_with_retry(
                "GET",
                f"{self._config.master_url}/api/v1/nodes/pending-tasks?machine_code={self._config.machine_code}"
            )
            if response and response.status_code == 200:
                tasks = response.json().get("data", {}).get("tasks", [])
                # 触发回调
                if self._on_task_dispatch and tasks:
                    for task_data in tasks:
                        task = TaskDispatch.from_dict(task_data)
                        await self._on_task_dispatch(task)
                return tasks
            return []
        except Exception as e:
            logger.warning(f"获取待执行任务异常: {e}")
            return []

    def get_os_info(self) -> Dict[str, str]:
        """获取操作系统信息"""
        if self._os_info_cache is None:
            self._os_info_cache = {
                "os_type": platform.system(),
                "os_version": platform.release(),
                "python_version": platform.python_version(),
                "machine_arch": platform.machine(),
            }
        return self._os_info_cache
