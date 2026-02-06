"""
安全工具模块

提供统一的 HMAC 签名/验证方法。

Requirements: 8.3
"""

import hashlib
import hmac
import time
import uuid
from typing import Any

from antcode_core.common.serialization import json_dumps_compact


def generate_hmac_signature(
    payload: dict[str, Any],
    secret_key: str,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, str]:
    """
    生成 HMAC-SHA256 请求签名

    签名算法:
    1. 将 payload 按 key 排序后序列化为紧凑 JSON
    2. 构建签名字符串: "{timestamp}.{nonce}.{sorted_payload}"
    3. 使用 HMAC-SHA256 计算签名

    Args:
        payload: 请求体数据
        secret_key: 签名密钥
        timestamp: 时间戳（可选，默认当前时间）
        nonce: 随机数（可选，默认生成 UUID）

    Returns:
        包含签名相关的请求头字典:
            - X-Signature: HMAC-SHA256 签名
            - X-Timestamp: 时间戳
            - X-Nonce: 随机数
    """
    if timestamp is None:
        timestamp = int(time.time())

    if nonce is None:
        nonce = uuid.uuid4().hex[:16]

    # 按 key 排序并序列化为紧凑 JSON
    sorted_payload = json_dumps_compact(payload, sort_keys=True)

    # 构建签名字符串
    sign_string = f"{timestamp}.{nonce}.{sorted_payload}"

    # 计算 HMAC-SHA256 签名
    signature = hmac.new(
        secret_key.encode("utf-8"),
        sign_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return {
        "X-Signature": signature,
        "X-Timestamp": str(timestamp),
        "X-Nonce": nonce,
    }


def verify_hmac_signature(
    payload: dict[str, Any],
    secret_key: str,
    signature: str,
    timestamp: int,
    nonce: str,
    max_age_seconds: int = 300,
) -> bool:
    """
    验证 HMAC-SHA256 请求签名

    Args:
        payload: 请求体数据
        secret_key: 签名密钥
        signature: 待验证的签名
        timestamp: 请求时间戳
        nonce: 请求随机数
        max_age_seconds: 签名最大有效期（秒），默认 300 秒

    Returns:
        签名是否有效
    """
    # 检查时间戳是否过期
    current_time = int(time.time())
    if abs(current_time - timestamp) > max_age_seconds:
        return False

    # 重新计算签名
    expected_headers = generate_hmac_signature(payload, secret_key, timestamp, nonce)
    expected_signature = expected_headers["X-Signature"]

    # 使用常量时间比较防止时序攻击
    return hmac.compare_digest(signature, expected_signature)


def compute_hmac(
    data: str | bytes,
    secret_key: str | bytes,
    algorithm: str = "sha256",
) -> str:
    """
    计算 HMAC 值

    Args:
        data: 待签名的数据
        secret_key: 签名密钥
        algorithm: 哈希算法，默认 sha256

    Returns:
        HMAC 十六进制字符串
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    if isinstance(secret_key, str):
        secret_key = secret_key.encode("utf-8")

    return hmac.new(secret_key, data, algorithm).hexdigest()


def constant_time_compare(a: str, b: str) -> bool:
    """
    常量时间字符串比较，防止时序攻击

    Args:
        a: 第一个字符串
        b: 第二个字符串

    Returns:
        两个字符串是否相等
    """
    return hmac.compare_digest(a, b)
