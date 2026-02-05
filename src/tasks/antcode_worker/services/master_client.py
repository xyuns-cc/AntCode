"""
主节点 HTTP 客户端

特性:
- HTTP/2 连接池
- 日志批量上报
- 动态心跳间隔
- HMAC 签名验证
- 指数退避重试
"""
import asyncio
import hashlib
import hmac
import platform
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any, Callable, List, Deque

import httpx
import psutil
from loguru import logger

from ..utils.serialization import Serializer


@dataclass
class LogEntry:
    execution_id: str
    log_type: str
    content: str
    timestamp: float = field(default_factory=time.time)


@dataclass
class RequestMetrics:
    total_requests: int = 0
    success_requests: int = 0
    failed_requests: int = 0
    total_latency_ms: float = 0
    last_request_time: float = 0

    @property
    def success_rate(self) -> float:
        return (self.success_requests / self.total_requests) * 100 if self.total_requests else 100.0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.success_requests if self.success_requests else 0


class MasterClient:
    """HTTP 客户端"""

    MIN_HEARTBEAT_INTERVAL = 10
    MAX_HEARTBEAT_INTERVAL = 60
    DEFAULT_HEARTBEAT_INTERVAL = 30
    LOG_BATCH_SIZE = 50
    LOG_FLUSH_INTERVAL = 2.0
    MAX_RETRY_COUNT = 3
    RETRY_BASE_DELAY = 1.0
    REQUEST_TIMEOUT = 10.0
    CONNECT_TIMEOUT = 5.0

    def __init__(self):
        self.master_url: Optional[str] = None
        self.api_key: Optional[str] = None
        self.access_token: Optional[str] = None
        self.secret_key: Optional[str] = None
        self.node_id: Optional[str] = None
        self.machine_code: Optional[str] = None

        self._connected = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._heartbeat_interval = self.DEFAULT_HEARTBEAT_INTERVAL
        self._log_flush_task: Optional[asyncio.Task] = None

        self._http_client: Optional[httpx.AsyncClient] = None
        self._log_buffer: Deque[LogEntry] = deque(maxlen=1000)
        self._log_lock = asyncio.Lock()
        self._metrics = RequestMetrics()

        self._on_connect: Optional[Callable] = None
        self._on_disconnect: Optional[Callable] = None
        self._on_task_received: Optional[Callable] = None
        self._os_info_cache: Optional[Dict[str, str]] = None

    @property
    def is_connected(self) -> bool:
        return self._connected and self.master_url is not None

    @property
    def metrics(self) -> RequestMetrics:
        return self._metrics

    def set_callbacks(self, on_connect: Callable = None, on_disconnect: Callable = None,
                      on_task_received: Callable = None):
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_task_received = on_task_received

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            http2_enabled = False
            try:
                import h2  # noqa: F401
                http2_enabled = True
            except Exception:
                logger.warning("未安装h2，HTTP连接将使用HTTP/1.1；如需HTTP/2，请安装 `pip install httpx[h2]`")
            self._http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=self.CONNECT_TIMEOUT, read=self.REQUEST_TIMEOUT,
                                      write=self.REQUEST_TIMEOUT, pool=self.REQUEST_TIMEOUT),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5, keepalive_expiry=30.0),
                trust_env=False,
                http2=http2_enabled,
            )
        return self._http_client

    async def _close_http_client(self):
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    def _generate_signature(self, payload: Dict[str, Any], timestamp: int, nonce: str) -> str:
        if not self.secret_key:
            return ""
        # 使用 ujson 进行高性能序列化
        # 注意：ujson 不支持 sort_keys，需要手动排序
        sorted_payload = Serializer.to_json(dict(sorted(payload.items())))
        sign_string = f"{timestamp}.{nonce}.{sorted_payload}"
        return hmac.new(self.secret_key.encode(), sign_string.encode(), hashlib.sha256).hexdigest()

    def _build_headers(self, payload: Dict[str, Any] = None) -> Dict[str, str]:
        timestamp = int(time.time())
        nonce = uuid.uuid4().hex[:16]

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "X-Timestamp": str(timestamp),
            "X-Nonce": nonce,
            "X-Node-ID": self.node_id or "",
            "X-Machine-Code": self.machine_code or "",
            "Accept-Encoding": "gzip",
        }

        if payload and self.secret_key:
            headers["X-Signature"] = self._generate_signature(payload, timestamp, nonce)

        return headers

    async def _request_with_retry(self, method: str, url: str, json_data: Dict = None,
                                  max_retries: int = None) -> Optional[httpx.Response]:
        max_retries = max_retries or self.MAX_RETRY_COUNT
        client = await self._get_http_client()

        for attempt in range(max_retries):
            try:
                start_time = time.time()
                headers = self._build_headers(json_data)

                response = await (client.get(url, headers=headers) if method.upper() == "GET"
                                  else client.post(url, json=json_data, headers=headers))

                latency = (time.time() - start_time) * 1000
                self._metrics.total_requests += 1
                self._metrics.last_request_time = time.time()

                if response.status_code < 400:
                    self._metrics.success_requests += 1
                    self._metrics.total_latency_ms += latency
                else:
                    self._metrics.failed_requests += 1

                return response
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                self._metrics.total_requests += 1
                self._metrics.failed_requests += 1

                if attempt < max_retries - 1:
                    delay = self.RETRY_BASE_DELAY * (2 ** attempt)
                    logger.debug(f"重试 {attempt + 1}/{max_retries} ({delay:.1f}s): {e}")
                    await asyncio.sleep(delay)
                else:
                    logger.warning(f"请求失败 (已重试 {max_retries} 次): {e}")
                    return None
            except Exception as e:
                self._metrics.total_requests += 1
                self._metrics.failed_requests += 1
                logger.error(f"请求异常: {e}")
                return None

        return None

    async def connect(self, master_url: str, machine_code: str, api_key: str,
                      access_token: str, secret_key: str = None, node_id: str = None):
        self.master_url = master_url.rstrip("/")
        self.machine_code = machine_code
        self.api_key = api_key
        self.access_token = access_token
        self.secret_key = secret_key
        self.node_id = node_id

        if not self.access_token:
            raise ConnectionError("缺少访问令牌")

        try:
            client = await self._get_http_client()
            response = await client.get(
                f"{self.master_url}/api/v1/health",
                headers={"Authorization": f"Bearer {self.access_token}"},
            )
            if response.status_code != 200:
                raise ConnectionError(f"连接失败: HTTP {response.status_code}")
        except httpx.ConnectError as e:
            raise ConnectionError(f"无法连接: {e}")
        except httpx.TimeoutException:
            raise ConnectionError("连接超时")

        self._connected = True
        has_secret = "有" if self.secret_key else "无"
        logger.info(f"已连接到主节点: {master_url} (node_id={node_id}, secret_key={has_secret})")

        await self.start_heartbeat()
        await self._start_log_flush_task()

        if self._on_connect:
            try:
                await self._on_connect()
            except Exception as e:
                logger.warning(f"连接回调异常: {e}")

    async def disconnect(self):
        await self._flush_logs()
        await self.stop_heartbeat()
        await self._stop_log_flush_task()
        await self._close_http_client()

        self._connected = False
        self.master_url = None
        self.api_key = None
        self.access_token = None
        self.secret_key = None

        logger.info("已断开连接")

        if self._on_disconnect:
            try:
                await self._on_disconnect()
            except Exception as e:
                logger.warning(f"断开回调异常: {e}")

    async def start_heartbeat(self):
        if self._heartbeat_task and not self._heartbeat_task.done():
            return
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.debug(f"心跳已启动: {self._heartbeat_interval}s")

    async def stop_heartbeat(self):
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

    def _adjust_heartbeat_interval(self, success: bool):
        if success:
            self._heartbeat_interval = min(self._heartbeat_interval + 2, self.MAX_HEARTBEAT_INTERVAL)
        else:
            self._heartbeat_interval = self.MIN_HEARTBEAT_INTERVAL

    async def _heartbeat_loop(self):
        fail_count = 0
        max_fails = 5

        while True:
            try:
                await asyncio.sleep(self._heartbeat_interval)
                if not self._connected or not self.master_url:
                    continue

                success = await self.send_heartbeat()

                if success:
                    fail_count = 0
                    self._adjust_heartbeat_interval(True)
                else:
                    fail_count += 1
                    self._adjust_heartbeat_interval(False)
                    if fail_count >= max_fails:
                        logger.warning(f"心跳连续失败 {fail_count} 次，标记断开")
                        self._connected = False
                        if self._on_disconnect:
                            try:
                                await self._on_disconnect()
                            except Exception:
                                pass
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"心跳循环异常: {e}")
                await asyncio.sleep(5)

    async def send_heartbeat(self) -> bool:
        if not self.master_url or not self.node_id or not self.api_key or not self.access_token:
            return False

        try:
            from ..config import get_node_config
            from .capability_service import capability_service

            config = get_node_config()

            payload = {
                "node_id": self.node_id,
                "api_key": self.api_key,
                "status": "online",
                "metrics": self.get_system_metrics(),
                "version": config.version if config else "2.0.0",
                **self.get_os_info(),
                # 上报节点能力
                "capabilities": capability_service.detect_all(),
            }

            response = await self._request_with_retry(
                "POST", f"{self.master_url}/api/v1/nodes/heartbeat", payload, max_retries=1
            )

            if response and response.status_code == 200:
                logger.debug(f"心跳成功 (间隔: {self._heartbeat_interval}s)")
                return True
            else:
                logger.warning(f"心跳失败: {response.status_code if response else '无响应'}")
                return False
        except Exception as e:
            logger.warning(f"心跳异常: {e}")
            return False

    async def _start_log_flush_task(self):
        if self._log_flush_task and not self._log_flush_task.done():
            return
        self._log_flush_task = asyncio.create_task(self._log_flush_loop())

    async def _stop_log_flush_task(self):
        if self._log_flush_task:
            self._log_flush_task.cancel()
            try:
                await self._log_flush_task
            except asyncio.CancelledError:
                pass
            self._log_flush_task = None

    async def _log_flush_loop(self):
        while True:
            try:
                await asyncio.sleep(self.LOG_FLUSH_INTERVAL)
                await self._flush_logs()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"日志刷新异常: {e}")

    async def _flush_logs(self):
        if not self._log_buffer:
            return

        async with self._log_lock:
            if not self._log_buffer:
                return
            batch: List[LogEntry] = []
            while self._log_buffer and len(batch) < self.LOG_BATCH_SIZE:
                batch.append(self._log_buffer.popleft())

        if not batch:
            return

        try:
            payload = {
                "logs": [{"execution_id": l.execution_id, "log_type": l.log_type,
                          "content": l.content, "timestamp": l.timestamp} for l in batch],
                "machine_code": self.machine_code,
            }

            response = await self._request_with_retry(
                "POST", f"{self.master_url}/api/v1/nodes/report-logs-batch", payload, max_retries=2
            )

            if not response:
                logger.warning(f"批量日志发送失败: 无响应")
                for log in batch:
                    await self._send_single_log(log)
            elif response.status_code != 200:
                logger.warning(f"批量日志发送失败: HTTP {response.status_code}, {response.text[:200]}")
                for log in batch:
                    await self._send_single_log(log)
        except Exception as e:
            logger.warning(f"批量日志发送异常: {e}")

    async def _send_single_log(self, log: LogEntry):
        try:
            payload = {"execution_id": log.execution_id, "log_type": log.log_type,
                       "content": log.content, "machine_code": self.machine_code}
            response = await self._request_with_retry("POST", f"{self.master_url}/api/v1/nodes/report-log", payload, max_retries=1)
            if response and response.status_code != 200:
                logger.debug(f"单条日志发送失败: HTTP {response.status_code}")
        except Exception as e:
            logger.debug(f"单条日志发送异常: {e}")

    async def report_log_line(self, execution_id: str, log_type: str, content: str) -> bool:
        if not self.master_url or not self.api_key or not self.access_token:
            return False

        async with self._log_lock:
            self._log_buffer.append(LogEntry(execution_id, log_type, content))
            if len(self._log_buffer) >= self.LOG_BATCH_SIZE:
                asyncio.create_task(self._flush_logs())

        return True

    async def report_task_status(self, execution_id: str, status: str,
                                 exit_code: int = None, error_message: str = None) -> bool:
        if not self.master_url or not self.api_key or not self.access_token:
            return False

        await self._flush_logs()

        response = await self._request_with_retry(
            "POST", f"{self.master_url}/api/v1/nodes/report-task",
            {"execution_id": execution_id, "status": status, "exit_code": exit_code,
             "error_message": error_message, "machine_code": self.machine_code}
        )

        return response is not None and response.status_code == 200

    async def report_execution_heartbeat(self, execution_id: str) -> bool:
        """上报任务执行心跳"""
        if not self.master_url or not self.api_key or not self.access_token:
            return False

        response = await self._request_with_retry(
            "POST", f"{self.master_url}/api/v1/nodes/report-heartbeat",
            {"execution_id": execution_id, "machine_code": self.machine_code},
            max_retries=1
        )

        return response is not None and response.status_code == 200

    def get_os_info(self) -> Dict[str, str]:
        if self._os_info_cache is None:
            self._os_info_cache = {
                "os_type": platform.system(),
                "os_version": platform.release(),
                "python_version": platform.python_version(),
                "machine_arch": platform.machine(),
            }
        return self._os_info_cache

    def get_system_metrics(self) -> Dict[str, Any]:
        try:
            from .project_service import local_project_service
            from .env_service import local_env_service
            from ..config import get_node_config
            from ..api.deps import get_engine

            config = get_node_config()

            # 从引擎获取任务统计
            task_count = 0
            running_tasks = 0
            try:
                engine = get_engine()
                stats = engine.get_stats()
                task_count = stats.get("tasks_received", 0)
                running_tasks = stats.get("executor", {}).get("running", 0)
            except Exception:
                pass

            memory_info = psutil.virtual_memory()
            disk_info = psutil.disk_usage("/")

            return {
                "cpu": round(psutil.cpu_percent(interval=0.1), 1),
                "memory": round(memory_info.percent, 1),
                "disk": round(disk_info.percent, 1),
                "taskCount": task_count,
                "runningTasks": running_tasks,
                "maxConcurrentTasks": config.max_concurrent_tasks if config else 5,
                "projectCount": len(local_project_service._projects),
                "envCount": local_env_service.get_env_count(),
                "uptime": self._get_uptime(),
                "cpuCores": psutil.cpu_count(),
                "memoryTotal": memory_info.total,
                "memoryUsed": memory_info.used,
                "memoryAvailable": memory_info.available,
                "diskTotal": disk_info.total,
                "diskUsed": disk_info.used,
                "diskFree": disk_info.free,
            }
        except Exception as e:
            logger.warning(f"获取指标异常: {e}")
            return {"cpu": 0, "memory": 0, "disk": 0, "taskCount": 0, "runningTasks": 0,
                    "maxConcurrentTasks": 5, "projectCount": 0, "envCount": 0, "uptime": 0}

    def _get_uptime(self) -> int:
        try:
            from ..config import get_node_config
            config = get_node_config()
            if config.start_time:
                return int((datetime.now() - config.start_time).total_seconds())
        except Exception:
            pass
        return 0

    async def fetch_pending_tasks(self) -> list:
        if not self.master_url or not self.api_key or not self.access_token:
            return []

        try:
            response = await self._request_with_retry(
                "GET", f"{self.master_url}/api/v1/nodes/pending-tasks?machine_code={self.machine_code}"
            )
            if response and response.status_code == 200:
                return response.json().get("data", {}).get("tasks", [])
            return []
        except Exception as e:
            logger.warning(f"获取待执行任务异常: {e}")
            return []

    async def sync_project_from_master(self, master_project_id: str) -> Optional[Dict[str, Any]]:
        if not self.master_url or not self.api_key or not self.access_token:
            return None

        try:
            response = await self._request_with_retry("GET", f"{self.master_url}/api/v1/projects/{master_project_id}")
            if not response or response.status_code != 200:
                logger.warning("获取项目信息失败")
                return None

            project_data = response.json().get("data", {})
            project_type = project_data.get("type")

            from .project_service import local_project_service

            if project_type == "code":
                return await local_project_service.create_code_project(
                    name=project_data.get("name"), code_content=project_data.get("code_content", ""),
                    language=project_data.get("language", "python"), description=project_data.get("description", ""),
                    entry_point=project_data.get("entry_point"), master_project_id=master_project_id
                )
            elif project_type == "file":
                file_url = project_data.get("file_url")
                if not file_url:
                    return None

                client = await self._get_http_client()
                file_response = await client.get(f"{self.master_url}{file_url}", headers=self._build_headers())
                if file_response.status_code != 200:
                    return None

                return await local_project_service.create_file_project(
                    name=project_data.get("name"), file_content=file_response.content,
                    original_name=project_data.get("original_name", "project.zip"),
                    description=project_data.get("description", ""), entry_point=project_data.get("entry_point"),
                    master_project_id=master_project_id
                )
            else:
                logger.warning(f"不支持的项目类型: {project_type}")
                return None
        except Exception as e:
            logger.error(f"同步项目异常: {e}")
            return None

    def get_node_info(self) -> Dict[str, Any]:
        from ..config import get_node_config

        config = get_node_config()
        metrics = self.get_system_metrics()
        os_info = self.get_os_info()

        return {
            "name": config.name,
            "host": config.host,
            "port": config.port,
            "region": config.region,
            "machine_code": config.machine_code,
            "version": config.version,
            "is_connected": self._connected,
            "start_time": config.start_time.isoformat(),
            "metrics": metrics,
            "system": {**os_info, "cpu_count": psutil.cpu_count(), "memory_total": psutil.virtual_memory().total},
            "communication": {
                "heartbeat_interval": self._heartbeat_interval,
                "request_metrics": {
                    "total": self._metrics.total_requests,
                    "success": self._metrics.success_requests,
                    "failed": self._metrics.failed_requests,
                    "success_rate": round(self._metrics.success_rate, 2),
                    "avg_latency_ms": round(self._metrics.avg_latency_ms, 2),
                },
                "log_buffer_size": len(self._log_buffer),
            }
        }


master_client = MasterClient()
