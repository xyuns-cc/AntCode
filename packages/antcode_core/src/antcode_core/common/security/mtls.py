"""mTLS 认证模块

提供 mTLS（双向 TLS）认证相关功能。
"""

import ssl
from pathlib import Path

from loguru import logger

from antcode_core.common.exceptions import AuthenticationError, ConfigurationError


def create_ssl_context(
    cert_path: str | Path,
    key_path: str | Path,
    ca_path: str | Path | None = None,
    verify_mode: int = ssl.CERT_REQUIRED,
) -> ssl.SSLContext:
    """创建 SSL 上下文

    Args:
        cert_path: 证书文件路径
        key_path: 私钥文件路径
        ca_path: CA 证书路径（用于验证客户端证书）
        verify_mode: 验证模式

    Returns:
        SSL 上下文

    Raises:
        ConfigurationError: 配置错误
    """
    cert_path = Path(cert_path)
    key_path = Path(key_path)

    if not cert_path.exists():
        raise ConfigurationError(f"证书文件不存在: {cert_path}")
    if not key_path.exists():
        raise ConfigurationError(f"私钥文件不存在: {key_path}")

    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cert_path), str(key_path))

        if ca_path:
            ca_path = Path(ca_path)
            if not ca_path.exists():
                raise ConfigurationError(f"CA 证书文件不存在: {ca_path}")
            context.load_verify_locations(str(ca_path))
            context.verify_mode = verify_mode

        return context
    except ssl.SSLError as e:
        raise ConfigurationError(f"SSL 配置错误: {e}")


def create_client_ssl_context(
    cert_path: str | Path | None = None,
    key_path: str | Path | None = None,
    ca_path: str | Path | None = None,
    verify_server: bool = True,
) -> ssl.SSLContext:
    """创建客户端 SSL 上下文

    Args:
        cert_path: 客户端证书路径（可选）
        key_path: 客户端私钥路径（可选）
        ca_path: CA 证书路径（用于验证服务器证书）
        verify_server: 是否验证服务器证书

    Returns:
        SSL 上下文
    """
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

        if cert_path and key_path:
            cert_path = Path(cert_path)
            key_path = Path(key_path)
            if not cert_path.exists():
                raise ConfigurationError(f"客户端证书文件不存在: {cert_path}")
            if not key_path.exists():
                raise ConfigurationError(f"客户端私钥文件不存在: {key_path}")
            context.load_cert_chain(str(cert_path), str(key_path))

        if ca_path:
            ca_path = Path(ca_path)
            if not ca_path.exists():
                raise ConfigurationError(f"CA 证书文件不存在: {ca_path}")
            context.load_verify_locations(str(ca_path))

        if not verify_server:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

        return context
    except ssl.SSLError as e:
        raise ConfigurationError(f"SSL 配置错误: {e}")


def extract_client_cert_info(ssl_object: ssl.SSLObject | None) -> dict | None:
    """从 SSL 连接中提取客户端证书信息

    Args:
        ssl_object: SSL 对象

    Returns:
        证书信息字典，包含 subject、issuer 等
    """
    if not ssl_object:
        return None

    try:
        cert = ssl_object.getpeercert()
        if not cert:
            return None

        return {
            "subject": dict(x[0] for x in cert.get("subject", [])),
            "issuer": dict(x[0] for x in cert.get("issuer", [])),
            "serial_number": cert.get("serialNumber"),
            "not_before": cert.get("notBefore"),
            "not_after": cert.get("notAfter"),
        }
    except Exception as e:
        logger.warning(f"提取客户端证书信息失败: {e}")
        return None


def verify_client_cert_cn(
    ssl_object: ssl.SSLObject | None,
    expected_cn: str,
) -> bool:
    """验证客户端证书的 Common Name

    Args:
        ssl_object: SSL 对象
        expected_cn: 期望的 CN 值

    Returns:
        是否匹配

    Raises:
        AuthenticationError: 证书验证失败
    """
    cert_info = extract_client_cert_info(ssl_object)
    if not cert_info:
        raise AuthenticationError("无法获取客户端证书")

    subject = cert_info.get("subject", {})
    actual_cn = subject.get("commonName")

    if actual_cn != expected_cn:
        raise AuthenticationError(f"证书 CN 不匹配: 期望 {expected_cn}, 实际 {actual_cn}")

    return True
