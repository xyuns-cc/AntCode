"""
Worker 通信安全验证

特性:
- HMAC-SHA256 签名验证
- 时间戳防重放（300s容差）
- Nonce 防重复请求
- 频率限制（1000次/60s）

Requirements: 8.4, 8.5
"""

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
        expired = [
            nonce
            for nonce, ts in self._used_nonces.items()
            if current_time - ts > self.NONCE_EXPIRY
        ]

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

    def _verify_nonce(self, nonce: str, timestamp: int) -> bool:
        """验证 Nonce（防重复请求）"""
        if nonce in self._used_nonces:
            logger.warning(f"重复 Nonce: {nonce}")
            return False

        self._cleanup_expired_nonces()
        self._used_nonces[nonce] = timestamp
        return True

    def _generate_signature(
        self, secret_key: str, payload: dict, timestamp: int, nonce: str
    ) -> str:
        """生成签名"""
        headers = generate_hmac_signature(payload, secret_key, timestamp, nonce)
        return headers["X-Signature"]

    def verify_signature(
        self, worker_id: str, payload: dict, timestamp: int, nonce: str, signature: str
    ) -> bool:
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
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="请求频率过高"
            )

        if require_signature:
            if not all([worker_id, timestamp_str, nonce, signature]):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少签名信息")

            try:
                timestamp = int(timestamp_str)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, detail="时间戳格式错误"
                )

            try:
                body = await request.json()
            except Exception:
                body = {}

            if not self.verify_signature(worker_id, body, timestamp, nonce, signature):
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

