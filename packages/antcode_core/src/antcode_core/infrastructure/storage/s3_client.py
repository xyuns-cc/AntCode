"""S3/MinIO 客户端管理器

提供统一的 S3 客户端实例管理，避免重复创建连接。
文件存储和日志存储共用此客户端。
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from types_aiobotocore_s3 import S3Client


class S3ClientManager:
    """S3 客户端管理器（单例）
    
    统一管理 S3 客户端连接，支持：
    - 连接复用
    - 自动重连
    - 优雅关闭
    
    配置优先级：
    1. 构造函数参数
    2. S3_* 环境变量
    3. MINIO_* 环境变量
    """

    _instance: S3ClientManager | None = None
    _initialized: bool = False

    def __new__(cls) -> S3ClientManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str | None = None,
    ):
        # 避免重复初始化
        if S3ClientManager._initialized:
            return
        
        self.endpoint_url = endpoint_url or self._get_endpoint_url()
        self.access_key = access_key or os.getenv("S3_ACCESS_KEY") or os.getenv("MINIO_ACCESS_KEY")
        self.secret_key = secret_key or os.getenv("S3_SECRET_KEY") or os.getenv("MINIO_SECRET_KEY")
        self.region = region or os.getenv("S3_REGION", "us-east-1")
        
        self._session = None
        self._client_cm = None
        self._client: S3Client | None = None
        
        S3ClientManager._initialized = True

    def _get_endpoint_url(self) -> str | None:
        """获取 S3 端点 URL"""
        # 优先使用 S3_ENDPOINT_URL
        url = os.getenv("S3_ENDPOINT_URL")
        if url:
            return url
        
        # 从 MINIO_ENDPOINT 构建
        endpoint = os.getenv("MINIO_ENDPOINT")
        if endpoint:
            if not endpoint.startswith(("http://", "https://")):
                return f"http://{endpoint}"
            return endpoint
        
        return None

    @property
    def is_configured(self) -> bool:
        """检查是否已配置 S3"""
        return bool(self.access_key and self.secret_key)

    async def get_client(self) -> S3Client:
        """获取 S3 客户端（长期复用）
        
        Returns:
            S3 客户端实例
            
        Raises:
            ImportError: 未安装 aioboto3
            RuntimeError: S3 未配置
        """
        if self._client is not None:
            return self._client
        
        if not self.is_configured:
            raise RuntimeError("S3 未配置，请设置 S3_ACCESS_KEY 和 S3_SECRET_KEY 环境变量")
        
        try:
            import aioboto3
        except ImportError:
            raise ImportError("请安装 aioboto3: pip install aioboto3")
        
        self._session = aioboto3.Session()
        self._client_cm = self._session.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region,
        )
        self._client = await self._client_cm.__aenter__()
        
        logger.debug(f"S3 客户端已创建: endpoint={self.endpoint_url}")
        return self._client

    @asynccontextmanager
    async def client_context(self):
        """获取 S3 客户端上下文（短期操作推荐）
        
        Yields:
            S3 客户端实例
        """
        if not self.is_configured:
            raise RuntimeError("S3 未配置")
        
        try:
            import aioboto3
        except ImportError:
            raise ImportError("请安装 aioboto3: pip install aioboto3")
        
        session = aioboto3.Session()
        async with session.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key,
            aws_secret_access_key=self.secret_key,
            region_name=self.region,
        ) as client:
            yield client

    async def close(self) -> None:
        """关闭 S3 客户端连接"""
        if self._client_cm is not None:
            try:
                await self._client_cm.__aexit__(None, None, None)
                logger.debug("S3 客户端已关闭")
            except Exception as e:
                logger.warning(f"关闭 S3 客户端失败: {e}")
            finally:
                self._client = None
                self._client_cm = None
                self._session = None

    async def ensure_bucket(self, bucket: str) -> bool:
        """确保 bucket 存在
        
        Args:
            bucket: 桶名
            
        Returns:
            是否成功
        """
        try:
            client = await self.get_client()
            try:
                await client.head_bucket(Bucket=bucket)
            except Exception:
                await client.create_bucket(Bucket=bucket)
                logger.info(f"创建 S3 桶: {bucket}")
            return True
        except Exception as e:
            if "BucketAlreadyOwnedByYou" in str(e):
                return True
            logger.error(f"确保桶存在失败: {e}")
            return False

    async def health_check(self) -> bool:
        """健康检查"""
        try:
            client = await self.get_client()
            await client.list_buckets()
            return True
        except Exception as e:
            logger.error(f"S3 健康检查失败: {e}")
            return False

    async def upload_directory(
        self,
        bucket: str,
        local_dir: str,
        s3_prefix: str,
        max_files: int = 2000,
    ) -> dict[str, str]:
        """上传本地目录到 S3
        
        Args:
            bucket: S3 桶名
            local_dir: 本地目录路径
            s3_prefix: S3 前缀（目录路径）
            max_files: 最大文件数限制
            
        Returns:
            上传的文件映射 {relative_path: s3_key}
        """
        import os
        from pathlib import Path
        
        client = await self.get_client()
        uploaded = {}
        file_count = 0
        
        local_path = Path(local_dir)
        if not local_path.exists():
            raise FileNotFoundError(f"目录不存在: {local_dir}")
        
        for root, _, files in os.walk(local_dir):
            for filename in files:
                if file_count >= max_files:
                    logger.warning(f"达到最大文件数限制: {max_files}")
                    break
                
                local_file = Path(root) / filename
                relative_path = local_file.relative_to(local_path)
                s3_key = f"{s3_prefix.rstrip('/')}/{relative_path}".replace("\\", "/")
                
                try:
                    with open(local_file, "rb") as f:
                        await client.put_object(
                            Bucket=bucket,
                            Key=s3_key,
                            Body=f.read(),
                        )
                    uploaded[str(relative_path)] = s3_key
                    file_count += 1
                except Exception as e:
                    logger.error(f"上传文件失败: {local_file} -> {s3_key}, 错误: {e}")
                    raise
        
        logger.info(f"目录上传完成: {local_dir} -> s3://{bucket}/{s3_prefix}, 共 {file_count} 个文件")
        return uploaded

    async def list_objects(
        self,
        bucket: str,
        prefix: str,
        max_keys: int = 1000,
    ) -> list[dict]:
        """列出 S3 前缀下的对象
        
        Args:
            bucket: S3 桶名
            prefix: S3 前缀
            max_keys: 最大返回数量
            
        Returns:
            对象列表 [{key, size, last_modified}]
        """
        client = await self.get_client()
        objects = []
        
        paginator = client.get_paginator("list_objects_v2")
        async for page in paginator.paginate(
            Bucket=bucket,
            Prefix=prefix,
            PaginationConfig={"MaxItems": max_keys},
        ):
            for obj in page.get("Contents", []):
                objects.append({
                    "key": obj["Key"],
                    "size": obj["Size"],
                    "last_modified": obj["LastModified"],
                })
        
        return objects

    async def delete_prefix(self, bucket: str, prefix: str) -> int:
        """删除 S3 前缀下的所有对象
        
        Args:
            bucket: S3 桶名
            prefix: S3 前缀
            
        Returns:
            删除的对象数量
        """
        client = await self.get_client()
        deleted_count = 0
        
        # 列出所有对象
        objects_to_delete = []
        paginator = client.get_paginator("list_objects_v2")
        async for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                objects_to_delete.append({"Key": obj["Key"]})
        
        if not objects_to_delete:
            return 0
        
        # 批量删除（每次最多 1000 个）
        for i in range(0, len(objects_to_delete), 1000):
            batch = objects_to_delete[i:i + 1000]
            await client.delete_objects(
                Bucket=bucket,
                Delete={"Objects": batch},
            )
            deleted_count += len(batch)
        
        logger.info(f"删除 S3 前缀: s3://{bucket}/{prefix}, 共 {deleted_count} 个对象")
        return deleted_count

    async def download_to_directory(
        self,
        bucket: str,
        prefix: str,
        local_dir: str,
    ) -> int:
        """下载 S3 前缀下的文件到本地目录
        
        Args:
            bucket: S3 桶名
            prefix: S3 前缀
            local_dir: 本地目录路径
            
        Returns:
            下载的文件数量
        """
        import os
        from pathlib import Path
        
        client = await self.get_client()
        downloaded_count = 0
        
        # 确保本地目录存在
        Path(local_dir).mkdir(parents=True, exist_ok=True)
        
        # 列出所有对象
        paginator = client.get_paginator("list_objects_v2")
        async for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                s3_key = obj["Key"]
                # 计算相对路径
                relative_path = s3_key[len(prefix):].lstrip("/")
                if not relative_path:
                    continue
                
                local_file = Path(local_dir) / relative_path
                local_file.parent.mkdir(parents=True, exist_ok=True)
                
                try:
                    response = await client.get_object(Bucket=bucket, Key=s3_key)
                    async with response["Body"] as stream:
                        content = await stream.read()
                    local_file.write_bytes(content)
                    downloaded_count += 1
                except Exception as e:
                    logger.error(f"下载文件失败: {s3_key} -> {local_file}, 错误: {e}")
                    raise
        
        logger.info(f"目录下载完成: s3://{bucket}/{prefix} -> {local_dir}, 共 {downloaded_count} 个文件")
        return downloaded_count

    @classmethod
    def reset(cls) -> None:
        """重置单例实例（用于测试）"""
        if cls._instance is not None:
            cls._instance._client = None
            cls._instance._client_cm = None
            cls._instance._session = None
        cls._instance = None
        cls._initialized = False


# 便捷函数
def get_s3_client_manager() -> S3ClientManager:
    """获取 S3 客户端管理器单例"""
    return S3ClientManager()


async def get_s3_client() -> S3Client:
    """获取 S3 客户端"""
    return await get_s3_client_manager().get_client()


def is_s3_configured() -> bool:
    """检查 S3 是否已配置"""
    return get_s3_client_manager().is_configured
