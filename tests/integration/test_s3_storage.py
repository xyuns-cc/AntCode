#!/usr/bin/env python3
"""S3 存储功能全面测试脚本

测试内容：
1. S3 客户端连接和健康检查
2. 文件上传/下载/删除
3. 目录上传/下载
4. 预签名 URL 生成
5. 项目文件服务（模拟）
"""

import asyncio
import os
import sys
import tempfile
import zipfile
from pathlib import Path

import pytest
from loguru import logger

pytestmark = pytest.mark.asyncio

# 设置环境变量（优先尊重已有配置）
endpoint = (
    os.getenv("TEST_MINIO_ENDPOINT")
    or os.getenv("S3_ENDPOINT_URL")
    or os.getenv("MINIO_ENDPOINT")
    or "127.0.0.1:9000"
)
if not endpoint.startswith(("http://", "https://")):
    endpoint = f"http://{endpoint}"

access_key = (
    os.getenv("TEST_MINIO_ACCESS_KEY")
    or os.getenv("S3_ACCESS_KEY")
    or os.getenv("MINIO_ACCESS_KEY")
    or "minioadmin"
)
secret_key = (
    os.getenv("TEST_MINIO_SECRET_KEY")
    or os.getenv("S3_SECRET_KEY")
    or os.getenv("MINIO_SECRET_KEY")
    or "minioadmin"
)
bucket = (
    os.getenv("TEST_MINIO_BUCKET")
    or os.getenv("S3_BUCKET")
    or os.getenv("MINIO_BUCKET")
    or "antcode"
)
region = os.getenv("TEST_MINIO_REGION") or os.getenv("S3_REGION") or "us-east-1"

os.environ.setdefault("FILE_STORAGE_BACKEND", "s3")
os.environ.setdefault("S3_ENDPOINT_URL", endpoint)
os.environ.setdefault("S3_ACCESS_KEY", access_key)
os.environ.setdefault("S3_SECRET_KEY", secret_key)
os.environ.setdefault("S3_BUCKET", bucket)
os.environ.setdefault("S3_REGION", region)


async def test_s3_client_manager():
    """测试 S3 客户端管理器"""
    logger.info("=" * 60)
    logger.info("测试 1: S3 客户端管理器")
    logger.info("=" * 60)
    
    from antcode_core.infrastructure.storage.s3_client import (
        S3ClientManager,
        get_s3_client_manager,
        is_s3_configured,
    )
    
    # 重置单例以使用新的环境变量
    S3ClientManager.reset()
    
    manager = get_s3_client_manager()
    
    logger.info("端点: {}", manager.endpoint_url)
    logger.info("桶名: antcode")
    logger.info("已配置: {}", is_s3_configured())
    
    # 健康检查
    logger.info("执行健康检查...")
    healthy = await manager.health_check()
    if healthy:
        logger.info("健康状态: OK 正常")
    else:
        logger.error("健康状态: FAIL 异常")
    
    if not healthy:
        logger.error("S3 连接失败，请检查配置")
        return False
    
    # 确保桶存在
    logger.info("确保桶存在...")
    bucket_ok = await manager.ensure_bucket("antcode")
    if bucket_ok:
        logger.info("桶状态: OK 就绪")
    else:
        logger.error("桶状态: FAIL 失败")
    
    return healthy and bucket_ok


async def test_file_operations():
    """测试文件操作"""
    logger.info("=" * 60)
    logger.info("测试 2: 文件基本操作")
    logger.info("=" * 60)
    
    from antcode_core.infrastructure.storage.s3_client import get_s3_client_manager
    
    manager = get_s3_client_manager()
    client = await manager.get_client()
    bucket = "antcode"
    test_key = "test/s3_test_file.txt"
    test_content = b"Hello from AntCode S3 Test!"
    
    try:
        # 上传文件
        logger.info("上传文件: {}", test_key)
        await client.put_object(Bucket=bucket, Key=test_key, Body=test_content)
        logger.info("OK 上传成功")
        
        # 检查文件存在
        logger.info("检查文件存在...")
        response = await client.head_object(Bucket=bucket, Key=test_key)
        logger.info("OK 文件存在，大小: {} 字节", response["ContentLength"])
        
        # 读取文件
        logger.info("读取文件内容...")
        response = await client.get_object(Bucket=bucket, Key=test_key)
        async with response["Body"] as stream:
            content = await stream.read()
        logger.info("OK 读取成功: {}", content.decode("utf-8"))
        
        # 删除文件
        logger.info("删除文件...")
        await client.delete_object(Bucket=bucket, Key=test_key)
        logger.info("OK 删除成功")
        
        return True
        
    except Exception as e:
        logger.exception("操作失败: {}", e)
        return False


async def test_directory_operations():
    """测试目录操作"""
    logger.info("=" * 60)
    logger.info("测试 3: 目录上传/下载")
    logger.info("=" * 60)
    
    from antcode_core.infrastructure.storage.s3_client import get_s3_client_manager
    
    manager = get_s3_client_manager()
    bucket = "antcode"
    s3_prefix = "test/project_dir"
    
    try:
        # 创建临时目录和文件
        with tempfile.TemporaryDirectory() as temp_dir:
            # 创建测试文件
            src_dir = Path(temp_dir) / "src"
            src_dir.mkdir()
            (src_dir / "main.py").write_text(
                "import logging\n"
                "logging.basicConfig(level=logging.INFO, format='%(message)s')\n"
                "logging.getLogger(__name__).info('Hello World')\n"
            )
            (src_dir / "utils.py").write_text("def helper(): pass")
            (Path(temp_dir) / "README.md").write_text("# Test Project")
            (Path(temp_dir) / "requirements.txt").write_text("requests==2.28.0")
            
            logger.info("创建测试目录: {}", temp_dir)
            logger.info("文件列表:")
            for f in Path(temp_dir).rglob("*"):
                if f.is_file():
                    logger.info("  - {}", f.relative_to(temp_dir))
            
            # 上传目录
            logger.info("上传目录到 S3: {}", s3_prefix)
            uploaded = await manager.upload_directory(
                bucket=bucket,
                local_dir=temp_dir,
                s3_prefix=s3_prefix,
            )
            logger.info("OK 上传成功，共 {} 个文件", len(uploaded))
            for rel_path, s3_key in uploaded.items():
                logger.info("  - {} -> {}", rel_path, s3_key)
            
            # 列出对象
            logger.info("列出 S3 对象...")
            objects = await manager.list_objects(bucket=bucket, prefix=s3_prefix)
            logger.info("OK 找到 {} 个对象", len(objects))
            for obj in objects:
                logger.info("  - {} ({} 字节)", obj["key"], obj["size"])
            
            # 下载目录
            download_dir = Path(temp_dir) / "downloaded"
            download_dir.mkdir()
            logger.info("下载目录到: {}", download_dir)
            downloaded = await manager.download_to_directory(
                bucket=bucket,
                prefix=s3_prefix + "/",
                local_dir=str(download_dir),
            )
            logger.info("OK 下载成功，共 {} 个文件", downloaded)
            for f in download_dir.rglob("*"):
                if f.is_file():
                    logger.info("  - {}", f.relative_to(download_dir))
            
            # 删除前缀
            logger.info("清理 S3 前缀: {}", s3_prefix)
            deleted = await manager.delete_prefix(bucket=bucket, prefix=s3_prefix)
            logger.info("OK 删除成功，共 {} 个对象", deleted)
        
        return True
        
    except Exception as e:
        logger.exception("操作失败: {}", e)
        return False


async def test_presigned_url():
    """测试预签名 URL"""
    logger.info("=" * 60)
    logger.info("测试 4: 预签名 URL")
    logger.info("=" * 60)
    
    from antcode_core.infrastructure.storage.s3 import S3FileStorageBackend
    from antcode_core.infrastructure.storage.presign import (
        is_s3_storage_enabled,
        generate_download_url,
        try_generate_download_url,
    )
    from antcode_core.infrastructure.storage.base import reset_file_storage_backend
    
    # 重置后端以使用新配置
    reset_file_storage_backend()
    
    logger.info("S3 存储已启用: {}", is_s3_storage_enabled())
    
    backend = S3FileStorageBackend()
    test_key = "test/presign_test.txt"
    test_content = b"Presigned URL Test Content"
    
    try:
        # 上传测试文件
        logger.info("上传测试文件: {}", test_key)
        client = await backend._get_client()
        await client.put_object(Bucket=backend.bucket, Key=test_key, Body=test_content)
        logger.info("OK 上传成功")
        
        # 生成下载预签名 URL
        logger.info("生成下载预签名 URL...")
        download_url = await backend.generate_presigned_url(test_key, expires_in=3600)
        logger.info("OK URL 生成成功")
        logger.info("URL: {}...", download_url[:80])
        
        # 使用 httpx 测试下载
        logger.info("测试预签名 URL 下载...")
        import httpx
        async with httpx.AsyncClient() as http_client:
            response = await http_client.get(download_url)
            if response.status_code == 200:
                logger.info("OK 下载成功: {}", response.content.decode("utf-8"))
            else:
                logger.error("下载失败: HTTP {}", response.status_code)
                return False
        
        # 清理
        logger.info("清理测试文件...")
        await client.delete_object(Bucket=backend.bucket, Key=test_key)
        logger.info("OK 清理成功")
        
        return True
        
    except Exception as e:
        logger.exception("操作失败: {}", e)
        return False


async def test_file_storage_backend():
    """测试文件存储后端"""
    logger.info("=" * 60)
    logger.info("测试 5: 文件存储后端")
    logger.info("=" * 60)
    
    from antcode_core.infrastructure.storage.base import get_file_storage_backend, reset_file_storage_backend
    from io import BytesIO
    
    # 重置后端
    reset_file_storage_backend()
    
    backend = get_file_storage_backend()
    logger.info("后端类型: {}", type(backend).__name__)
    logger.info("是 S3 后端: {}", backend.is_s3_backend())
    
    try:
        # 创建模拟文件流
        test_content = b"Test file content for storage backend"
        
        class MockFile:
            def __init__(self, content, filename):
                self._content = content
                self._pos = 0
                self.filename = filename
            
            async def read(self, size=-1):
                if size == -1:
                    data = self._content[self._pos:]
                    self._pos = len(self._content)
                else:
                    data = self._content[self._pos:self._pos + size]
                    self._pos += len(data)
                return data
            
            async def seek(self, pos):
                self._pos = pos
        
        mock_file = MockFile(test_content, "test_upload.txt")
        
        # 保存文件
        logger.info("保存文件...")
        metadata = await backend.save(mock_file, "test_upload.txt")
        logger.info("OK 保存成功")
        logger.info("路径: {}", metadata.path)
        logger.info("大小: {}", metadata.size)
        logger.info("哈希: {}", metadata.hash)
        
        # 检查文件存在
        logger.info("检查文件存在...")
        exists = await backend.exists(metadata.path)
        logger.info("OK 文件存在: {}", exists)
        
        # 获取文件大小
        logger.info("获取文件大小...")
        size = await backend.get_file_size(metadata.path)
        logger.info("OK 文件大小: {} 字节", size)
        
        # 读取文件内容
        logger.info("读取文件内容...")
        content = await backend.get_file_bytes(metadata.path)
        logger.info("OK 读取成功: {}", content.decode("utf-8"))
        
        # 删除文件
        logger.info("删除文件...")
        deleted = await backend.delete(metadata.path)
        logger.info("OK 删除成功: {}", deleted)
        
        return True
        
    except Exception as e:
        logger.exception("操作失败: {}", e)
        return False


async def test_project_extract_to_s3():
    """测试项目解压到 S3"""
    logger.info("=" * 60)
    logger.info("测试 6: 项目解压到 S3")
    logger.info("=" * 60)
    
    from antcode_core.infrastructure.storage.s3_client import get_s3_client_manager
    from antcode_core.infrastructure.storage.base import get_file_storage_backend, reset_file_storage_backend
    
    reset_file_storage_backend()
    backend = get_file_storage_backend()
    manager = get_s3_client_manager()
    
    try:
        # 创建测试 ZIP 文件
        with tempfile.TemporaryDirectory() as temp_dir:
            # 创建项目文件
            project_dir = Path(temp_dir) / "project"
            project_dir.mkdir()
            (project_dir / "main.py").write_text(
                "import logging\n"
                "logging.basicConfig(level=logging.INFO, format='%(message)s')\n"
                "logging.getLogger(__name__).info('Hello from project')\n"
            )
            (project_dir / "config.json").write_text('{"name": "test"}')
            
            # 创建 ZIP
            zip_path = Path(temp_dir) / "project.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                for f in project_dir.rglob("*"):
                    if f.is_file():
                        zf.write(f, f.relative_to(project_dir))
            
            logger.info("创建测试 ZIP: {}", zip_path)
            logger.info("ZIP 大小: {} 字节", zip_path.stat().st_size)
            
            # 上传 ZIP 到 S3
            zip_s3_key = "files/test/project.zip"
            logger.info("上传 ZIP 到 S3: {}", zip_s3_key)
            client = await manager.get_client()
            with open(zip_path, "rb") as f:
                await client.put_object(Bucket=backend.bucket, Key=zip_s3_key, Body=f.read())
            logger.info("OK 上传成功")
            
            # 模拟解压到 S3
            logger.info("模拟解压到 S3...")
            s3_project_prefix = "projects/test_project_123"
            
            # 下载 ZIP
            response = await client.get_object(Bucket=backend.bucket, Key=zip_s3_key)
            async with response["Body"] as stream:
                zip_content = await stream.read()
            
            # 解压到临时目录
            extract_dir = Path(temp_dir) / "extracted"
            extract_dir.mkdir()
            
            import io
            with zipfile.ZipFile(io.BytesIO(zip_content), "r") as zf:
                zf.extractall(extract_dir)
            
            logger.info("解压到临时目录: {}", extract_dir)
            for f in extract_dir.rglob("*"):
                if f.is_file():
                    logger.info("  - {}", f.relative_to(extract_dir))
            
            # 上传到 S3 项目目录
            uploaded = await manager.upload_directory(
                bucket=backend.bucket,
                local_dir=str(extract_dir),
                s3_prefix=s3_project_prefix,
            )
            logger.info("OK 上传到 S3 项目目录成功，共 {} 个文件", len(uploaded))
            
            # 列出项目文件
            objects = await manager.list_objects(bucket=backend.bucket, prefix=s3_project_prefix)
            logger.info("S3 项目文件:")
            for obj in objects:
                logger.info("  - {} ({} 字节)", obj["key"], obj["size"])
            
            # 清理
            logger.info("清理测试数据...")
            await client.delete_object(Bucket=backend.bucket, Key=zip_s3_key)
            await manager.delete_prefix(bucket=backend.bucket, prefix=s3_project_prefix)
            logger.info("OK 清理成功")
        
        return True
        
    except Exception as e:
        logger.exception("操作失败: {}", e)
        return False


async def main():
    """运行所有测试"""
    logger.info("=" * 60)
    logger.info("AntCode S3 存储功能全面测试")
    logger.info("=" * 60)
    logger.info("端点: {}", os.environ["S3_ENDPOINT_URL"])
    logger.info("桶名: {}", os.environ["S3_BUCKET"])
    
    results = {}
    
    # 测试 1: S3 客户端管理器
    results["S3 客户端管理器"] = await test_s3_client_manager()
    
    if not results["S3 客户端管理器"]:
        logger.error("S3 连接失败，跳过后续测试")
        return
    
    # 测试 2: 文件基本操作
    results["文件基本操作"] = await test_file_operations()
    
    # 测试 3: 目录操作
    results["目录上传/下载"] = await test_directory_operations()
    
    # 测试 4: 预签名 URL
    results["预签名 URL"] = await test_presigned_url()
    
    # 测试 5: 文件存储后端
    results["文件存储后端"] = await test_file_storage_backend()
    
    # 测试 6: 项目解压到 S3
    results["项目解压到 S3"] = await test_project_extract_to_s3()
    
    # 清理：关闭 S3 客户端
    from antcode_core.infrastructure.storage.s3_client import get_s3_client_manager
    await get_s3_client_manager().close()
    
    # 打印结果汇总
    logger.info("=" * 60)
    logger.info("测试结果汇总")
    logger.info("=" * 60)
    
    all_passed = True
    for name, passed in results.items():
        status = "OK 通过" if passed else "FAIL 失败"
        logger.info("{}: {}", name, status)
        if not passed:
            all_passed = False
    
    logger.info("=" * 60)
    if all_passed:
        logger.info("OK 所有测试通过，S3 存储功能正常")
    else:
        logger.error("FAIL 部分测试失败，请检查日志")
    logger.info("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
