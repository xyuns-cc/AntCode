"""Worker 请求构建工具

Requirements: 8.4, 8.5
"""

import time
import uuid

from antcode_core.common.security import generate_hmac_signature


def build_worker_signed_headers(worker, payload=None):
    """构建带签名的 Worker 请求头，匹配 Worker 侧的 HMAC 校验逻辑。"""
    if not worker or not getattr(worker, "public_id", None):
        raise ValueError("Worker 标识缺失")
    if not getattr(worker, "api_key", None):
        raise ValueError("Worker API Key 缺失")

    payload = payload or {}
    timestamp = int(time.time())
    nonce = uuid.uuid4().hex[:16]

    headers = {
        "Authorization": f"Bearer {worker.api_key}",
        "X-Worker-ID": worker.public_id,
        "X-Timestamp": str(timestamp),
        "X-Nonce": nonce,
        "Accept-Encoding": "gzip",
    }

    if getattr(worker, "secret_key", None):
        sig_headers = generate_hmac_signature(payload, worker.secret_key, timestamp, nonce)
        headers["X-Signature"] = sig_headers["X-Signature"]

    # 去掉空值，避免发送多余头
    return {k: v for k, v in headers.items() if v}

