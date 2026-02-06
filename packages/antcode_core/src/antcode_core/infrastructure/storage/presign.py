"""预签名 URL 工具

提供生成预签名 URL 的便捷函数。
"""

from loguru import logger

from antcode_core.infrastructure.storage.base import get_file_storage_backend


def is_s3_storage_enabled() -> bool:
    """检查是否启用了 S3 存储后端"""
    backend = get_file_storage_backend()
    return hasattr(backend, "is_s3_backend") and backend.is_s3_backend()


async def generate_upload_url(
    filename: str,
    expires_in: int = 3600,
) -> dict[str, str]:
    """生成上传预签名 URL
    
    Args:
        filename: 文件名
        expires_in: 过期时间（秒）
        
    Returns:
        包含 url 和 path 的字典
        
    Raises:
        NotImplementedError: 当前存储后端不支持预签名 URL
    """
    backend = get_file_storage_backend()
    
    # 构建存储路径
    path = backend.build_path(filename)
    
    # 检查是否支持预签名
    if hasattr(backend, "generate_presigned_url"):
        try:
            url = await backend.generate_presigned_url(
                path,
                expires_in=expires_in,
                method="put_object",
            )
            return {"url": url, "path": path}
        except Exception as e:
            logger.error(f"生成上传预签名 URL 失败: {e}")
            raise IOError(f"生成上传预签名 URL 失败: {e}") from e
    
    # 本地存储不支持预签名
    raise NotImplementedError("当前存储后端不支持预签名 URL")


async def generate_download_url(
    path: str,
    expires_in: int = 3600,
) -> str:
    """生成下载预签名 URL
    
    Args:
        path: 文件路径
        expires_in: 过期时间（秒）
        
    Returns:
        预签名 URL
        
    Raises:
        NotImplementedError: 当前存储后端不支持预签名 URL
    """
    backend = get_file_storage_backend()
    
    # 检查是否支持预签名
    if hasattr(backend, "generate_presigned_url"):
        try:
            return await backend.generate_presigned_url(
                path,
                expires_in=expires_in,
                method="get_object",
            )
        except Exception as e:
            logger.error(f"生成下载预签名 URL 失败: {e}")
            raise IOError(f"生成下载预签名 URL 失败: {e}") from e
    
    # 本地存储不支持预签名
    raise NotImplementedError("当前存储后端不支持预签名 URL")


async def try_generate_download_url(
    path: str,
    expires_in: int = 3600,
    fallback_url: str | None = None,
) -> str | None:
    """尝试生成下载预签名 URL，失败时返回回退 URL
    
    Args:
        path: 文件路径
        expires_in: 过期时间（秒）
        fallback_url: 回退 URL（当 S3 不可用时使用）
        
    Returns:
        预签名 URL 或回退 URL，如果都不可用则返回 None
    """
    try:
        return await generate_download_url(path, expires_in)
    except (NotImplementedError, IOError) as e:
        logger.debug(f"无法生成 S3 预签名 URL: {e}，使用回退 URL")
        return fallback_url
