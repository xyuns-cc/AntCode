"""
Worker 通信安全验证

特性:
- HMAC-SHA256 签名验证
- 时间戳防重放（300s容差）
- Nonce 防重复请求（Redis SETNX,跨进程共享）
- 频率限制（1000次/60s）

Requirements: 8.4, 8.5
"""

import os
import time

from fastapi import HTTPException, Request, status
from loguru import logger

from antcode_core.common.security import constant_time_compare, generate_hmac_signature


class WorkerAuthVerifier:
    """Worker 认证验证器"""

    TIMESTAMP_TOLERANCE = 300  # 时间戳容差（秒）
    NONCE_EXPIRY = 600  # Nonce 过期（秒）
    MAX_NONCES = 10000  # 最大 Nonce 数量

    def __init__(self):
        self._used_nonces: dict[str, float] = {}
        self._secret_keys: dict[str, str] = {}
        self._rate_limits: dict[str, tuple] = {}

        self.rate_limit_requests = 1000  # 最大请求数
        self.rate_limit_window = 60  # 窗口时长（秒）

    def register_worker_secret(self, worker_id: str, secret_key: str):
        """注册 Worker 密钥"""
        self._secret_keys[worker_id] = secret_key
        logger.debug(f"已注册 Worker 密钥: {worker_id[:8]}...")

    def remove_worker_secret(self, worker_id: str):
        """移除 Worker 密钥"""
        self._secret_keys.pop(worker_id, None)

    def get_worker_secret(self, worker_id: str) -> str | None:
        """获取 Worker 密钥"""
        return self._secret_keys.get(worker_id)

    def _cleanup_expired_nonces(self):
        """清理过期的 Nonce"""
        if len(self._used_nonces) < self.MAX_NONCES:
            return

        current_time = time.time()
        expired = [nonce for nonce, ts in self._used_nonces.items() if current_time - ts > self.NONCE_EXPIRY]

        for nonce in expired:
            del self._used_nonces[nonce]

        if expired:
            logger.debug(f"清理了 {len(expired)} 个过期的 Nonce")

    def _verify_timestamp(self, timestamp: int) -> bool:
        """验证时间戳（防重放攻击）"""
        current_time = int(time.time())
        diff = abs(current_time - timestamp)

        if diff > self.TIMESTAMP_TOLERANCE:
            logger.warning(f"时间戳偏差过大: {diff}s")
            return False

        return True

    async def _claim_nonce_redis(self, nonce: str) -> bool | None:
        """通过 Redis ``SET NX EX`` 占位 nonce。

        返回值:
        - ``True``:成功占位(本次 nonce 第一次出现)
        - ``False``:已存在(重放)
        - ``None``:Redis 不可用,调用方应走 fallback
        """
        try:
            from antcode_core.infrastructure.redis.client import get_redis_client

            redis_client = await get_redis_client()
            ok = await redis_client.set(
                f"antcode:nonce:{nonce}",
                "1",
                nx=True,
                ex=self.NONCE_EXPIRY,
            )
            return bool(ok)
        except Exception:
            logger.exception("Redis nonce 校验失败,降级到进程内 dict")
            return None

    def _verify_nonce(self, nonce: str, timestamp: int) -> bool:
        """验证 Nonce(防重复请求)。

        优先 Redis ``SET NX EX``,跨进程共享 nonce 占用状态,杜绝多
        worker / 重启场景下 dict 失忆导致的重放(P1-#L13)。Redis 不
        可用或显式禁用 (``ANTCODE_NO_REDIS=1``) 时退回到进程内 dict。

        ``verify_signature`` 历史上是同步接口,为不破坏 web_api 端
        ``project_download.py`` 的现有调用方式,这里在同步上下文里
        调度 Redis 协程:有 event loop 时 ``run_until_complete``,
        没有 loop 时直接走 dict fallback。
        """
        redis_result: bool | None = None
        if not os.getenv("ANTCODE_NO_REDIS"):
            redis_result = self._run_async(self._claim_nonce_redis(nonce))

        if redis_result is True:
            return True
        if redis_result is False:
            logger.warning(f"重复 Nonce: {nonce}")
            return False

        # Fallback: Redis 不可用 / 显式关闭
        if nonce in self._used_nonces:
            logger.warning(f"重复 Nonce: {nonce}")
            return False
        self._cleanup_expired_nonces()
        self._used_nonces[nonce] = timestamp
        return True

    @staticmethod
    def _run_async(coro):
        """在同步上下文中安全地运行协程。

        - 当前线程已有 running loop:用 ``asyncio.ensure_future`` 入队是不行的
          (无法同步等结果),只能跳过 Redis 路径。这种情况通常意味着调用方
          本身就在 async 路由里,直接降级到 dict fallback,等待后续把
          ``verify_signature`` 全链路改 async 后再启用 Redis。
        - 没有 running loop:用 ``asyncio.run`` 同步等待。
        """
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            # 已在 async 上下文,但调用方是 sync 接口 —— 不能阻塞 loop
            return None

        try:
            return asyncio.run(coro)
        except Exception:
            logger.exception("nonce 协程执行失败")
            return None

    def _generate_signature(self, secret_key: str, payload: dict, timestamp: int, nonce: str) -> str:
        """生成签名"""
        headers = generate_hmac_signature(payload, secret_key, timestamp, nonce)
        return headers["X-Signature"]

    def verify_signature(self, worker_id: str, payload: dict, timestamp: int, nonce: str, signature: str) -> bool:
        """验证 HMAC-SHA256 签名"""
        secret_key = self.get_worker_secret(worker_id)
        if not secret_key:
            logger.warning(f"未注册 Worker: {worker_id}")
            return False

        if not self._verify_timestamp(timestamp):
            return False

        if not self._verify_nonce(nonce, timestamp):
            return False

        expected_signature = self._generate_signature(secret_key, payload, timestamp, nonce)

        if not constant_time_compare(signature, expected_signature):
            logger.warning(f"签名无效: {worker_id}")
            return False

        return True

    async def verify_signature_async(
        self, worker_id: str, payload: dict, timestamp: int, nonce: str, signature: str
    ) -> bool:
        """异步版本的签名验证,允许 nonce 直接走 Redis 而无需嵌套 loop。

        新接入的路由建议直接 ``await verify_signature_async(...)``
        以获取真正的跨进程 nonce 防重放。
        """
        secret_key = self.get_worker_secret(worker_id)
        if not secret_key:
            logger.warning(f"未注册 Worker: {worker_id}")
            return False

        if not self._verify_timestamp(timestamp):
            return False

        redis_result: bool | None = None
        if not os.getenv("ANTCODE_NO_REDIS"):
            redis_result = await self._claim_nonce_redis(nonce)

        if redis_result is False:
            logger.warning(f"重复 Nonce: {nonce}")
            return False
        if redis_result is None:
            # Redis 不可用 → dict fallback
            if nonce in self._used_nonces:
                logger.warning(f"重复 Nonce: {nonce}")
                return False
            self._cleanup_expired_nonces()
            self._used_nonces[nonce] = timestamp

        expected_signature = self._generate_signature(secret_key, payload, timestamp, nonce)

        if not constant_time_compare(signature, expected_signature):
            logger.warning(f"签名无效: {worker_id}")
            return False

        return True

    def check_rate_limit(self, worker_id: str) -> bool:
        """检查请求频率限制"""
        current_time = time.time()

        if worker_id in self._rate_limits:
            count, window_start = self._rate_limits[worker_id]

            if current_time - window_start < self.rate_limit_window:
                if count >= self.rate_limit_requests:
                    logger.warning(f"请求频率过高: {worker_id}")
                    return False
                self._rate_limits[worker_id] = (count + 1, window_start)
            else:
                self._rate_limits[worker_id] = (1, current_time)
        else:
            self._rate_limits[worker_id] = (1, current_time)

        return True

    async def verify_request(self, request: Request, require_signature: bool = False) -> dict:
        """验证 Worker 请求"""
        worker_id = request.headers.get("X-Worker-ID", "")
        timestamp_str = request.headers.get("X-Timestamp", "")
        nonce = request.headers.get("X-Nonce", "")
        signature = request.headers.get("X-Signature", "")

        result = {
            "worker_id": worker_id,
            "verified": False,
            "signature_verified": False,
        }

        if worker_id and not self.check_rate_limit(worker_id):
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="请求频率过高")

        if require_signature:
            if not all([worker_id, timestamp_str, nonce, signature]):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少签名信息")

            try:
                timestamp = int(timestamp_str)
            except ValueError:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="时间戳格式错误")

            try:
                body = await request.json()
            except Exception:
                body = {}

            if not await self.verify_signature_async(worker_id, body, timestamp, nonce, signature):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="签名验证失败")

            result["signature_verified"] = True

        result["verified"] = True
        return result


# 全局验证器实例
worker_auth_verifier = WorkerAuthVerifier()


# FastAPI 依赖
async def verify_worker_request(request: Request) -> dict:
    """Worker 请求验证（无签名）"""
    return await worker_auth_verifier.verify_request(request, require_signature=False)


async def verify_worker_request_with_signature(request: Request) -> dict:
    """Worker 请求验证（HMAC签名）"""
    return await worker_auth_verifier.verify_request(request, require_signature=True)
