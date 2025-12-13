"""节点请求构建工具"""

import hashlib
import hmac
import time
import uuid

from src.utils.serialization import json_dumps_compact


def build_node_signed_headers(node, payload=None):
    """构建带签名的节点请求头，匹配节点侧的 HMAC 校验逻辑。"""
    if not node or not getattr(node, "public_id", None):
        raise ValueError("节点标识缺失")
    if not getattr(node, "api_key", None):
        raise ValueError("节点 API Key 缺失")

    payload = payload or {}
    timestamp = int(time.time())
    nonce = uuid.uuid4().hex[:16]

    headers = {
        "Authorization": f"Bearer {node.api_key}",
        "X-Node-ID": node.public_id,
        "X-Timestamp": str(timestamp),
        "X-Nonce": nonce,
        "Accept-Encoding": "gzip",
    }

    if getattr(node, "secret_key", None):
        sorted_payload = json_dumps_compact(payload, sort_keys=True)
        sign_string = f"{timestamp}.{nonce}.{sorted_payload}"
        headers["X-Signature"] = hmac.new(
            node.secret_key.encode("utf-8"),
            sign_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    # 去掉空值，避免发送多余头
    return {k: v for k, v in headers.items() if v}
