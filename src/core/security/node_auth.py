"""
节点通信安全验证

特性:
- HMAC-SHA256 签名验证
- 时间戳防重放（300s容差）
- Nonce 防重复请求
- 频率限制（1000次/60s）
"""
import hashlib
import hmac
import time
from typing import Optional, Dict

from fastapi import Request, HTTPException, status
from loguru import logger

from src.utils.serialization import json_dumps_compact


class NodeAuthVerifier:
    """节点认证验证器"""

    TIMESTAMP_TOLERANCE = 300  # 时间戳容差（秒）
    NONCE_EXPIRY = 600         # Nonce 过期（秒）
    MAX_NONCES = 10000         # 最大 Nonce 数量

    def __init__(self):
        self._used_nonces: Dict[str, float] = {}
        self._secret_keys: Dict[str, str] = {}
        self._rate_limits: Dict[str, tuple] = {}

        self.rate_limit_requests = 1000  # 最大请求数
        self.rate_limit_window = 60      # 窗口时长（秒）

    def register_node_secret(self, node_id: str, secret_key: str):
        """注册节点密钥"""
        self._secret_keys[node_id] = secret_key
        logger.debug(f"已注册节点密钥: {node_id[:8]}...")

    def remove_node_secret(self, node_id: str):
        """移除节点密钥"""
        self._secret_keys.pop(node_id, None)

    def get_node_secret(self, node_id: str) -> Optional[str]:
        """获取节点密钥"""
        return self._secret_keys.get(node_id)

    def _cleanup_expired_nonces(self):
        """清理过期的 Nonce"""
        if len(self._used_nonces) < self.MAX_NONCES:
            return

        current_time = time.time()
        expired = [
            nonce for nonce, ts in self._used_nonces.items()
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

    def _generate_signature(self, secret_key: str, payload: Dict, timestamp: int, nonce: str) -> str:
        """生成签名"""
        sorted_payload = json_dumps_compact(payload, sort_keys=True)
        sign_string = f"{timestamp}.{nonce}.{sorted_payload}"

        signature = hmac.new(
            secret_key.encode('utf-8'),
            sign_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        return signature

    def verify_signature(
        self,
        node_id: str,
        payload: Dict,
        timestamp: int,
        nonce: str,
        signature: str
    ) -> bool:
        """验证 HMAC-SHA256 签名"""
        secret_key = self.get_node_secret(node_id)
        if not secret_key:
            logger.warning(f"未注册节点: {node_id}")
            return False

        if not self._verify_timestamp(timestamp):
            return False

        if not self._verify_nonce(nonce, timestamp):
            return False

        expected_signature = self._generate_signature(secret_key, payload, timestamp, nonce)

        if not hmac.compare_digest(signature, expected_signature):
            logger.warning(f"签名无效: {node_id}")
            return False

        return True

    def check_rate_limit(self, node_id: str) -> bool:
        """检查请求频率限制"""
        current_time = time.time()

        if node_id in self._rate_limits:
            count, window_start = self._rate_limits[node_id]

            if current_time - window_start < self.rate_limit_window:
                if count >= self.rate_limit_requests:
                    logger.warning(f"请求频率过高: {node_id}")
                    return False
                self._rate_limits[node_id] = (count + 1, window_start)
            else:
                self._rate_limits[node_id] = (1, current_time)
        else:
            self._rate_limits[node_id] = (1, current_time)

        return True

    async def verify_request(self, request: Request, require_signature: bool = False) -> Dict:
        """验证节点请求"""
        node_id = request.headers.get("X-Node-ID", "")
        machine_code = request.headers.get("X-Machine-Code", "")
        timestamp_str = request.headers.get("X-Timestamp", "")
        nonce = request.headers.get("X-Nonce", "")
        signature = request.headers.get("X-Signature", "")

        result = {"node_id": node_id, "machine_code": machine_code, 
                  "verified": False, "signature_verified": False}

        if node_id and not self.check_rate_limit(node_id):
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                              detail="请求频率过高")

        if require_signature:
            if not all([node_id, timestamp_str, nonce, signature]):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                  detail="缺少签名信息")

            try:
                timestamp = int(timestamp_str)
            except ValueError:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                                  detail="时间戳格式错误")

            try:
                body = await request.json()
            except Exception:
                body = {}

            if not self.verify_signature(node_id, body, timestamp, nonce, signature):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                                  detail="签名验证失败")

            result["signature_verified"] = True

        result["verified"] = True
        return result


# 全局验证器实例
node_auth_verifier = NodeAuthVerifier()


# FastAPI 依赖
async def verify_node_request(request: Request) -> Dict:
    """节点请求验证（无签名）"""
    return await node_auth_verifier.verify_request(request, require_signature=False)


async def verify_node_request_with_signature(request: Request) -> Dict:
    """节点请求验证（HMAC签名）"""
    return await node_auth_verifier.verify_request(request, require_signature=True)
