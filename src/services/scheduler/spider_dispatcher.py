from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from loguru import logger

from src.core.config import settings
from src.core.exceptions import TaskExecutionException
from src.services.scheduler.redis_task_service import redis_task_service


class RedisSpiderExecutor:
    """通过 Redis 提交任务的执行器。"""

    async def submit_rule_task(
        self,
        project,
        rule_detail,
        execution_id,
        params=None,
    ):
        try:
            await redis_task_service.connect()
            result = await redis_task_service.submit_rule_task(
                project=project,
                rule_detail=rule_detail,
                execution_id=execution_id,
                params=params,
            )
            result["executor"] = "redis"
            return result
        finally:
            try:
                await redis_task_service.disconnect()
            except Exception as exc:  # noqa: BLE001
                logger.debug(f"断开 Redis 连接时出现异常: {exc}")


class LocalScrapyExecutor:
    """直接执行本地 Scrapy 项目的执行器。"""

    QUEUE_NAME = "local:scrapy"

    def __init__(self):
        self._project_root = Path(settings.BASE_DIR) / "src" / "tasks" / "antcode_spider"
        self._scrapy_cfg = self._project_root / "scrapy.cfg"
        self._work_root = Path(settings.TASK_EXECUTION_WORK_DIR)

    async def submit_rule_task(
        self,
        project,
        rule_detail,
        execution_id,
        params=None,
    ):
        if not self._scrapy_cfg.exists():
            raise TaskExecutionException(
                f"未找到 Scrapy 配置文件: {self._scrapy_cfg}. "
                "请确认已初始化本地爬虫项目。"
            )

        task_json = await redis_task_service._build_task_json(  # noqa: SLF001
            project=project,
            rule_detail=rule_detail,
            execution_id=execution_id,
            params=params,
        )
        task_id = task_json["meta"]["task_id"]

        run_dir = self._work_root / execution_id
        run_dir.mkdir(parents=True, exist_ok=True)

        payload_path = run_dir / f"{task_id}.json"
        payload_path.write_text(
            json.dumps(task_json, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        log_path = run_dir / f"{task_id}.log"
        spider_name = "spider"
        if params and isinstance(params, dict):
            spider_name = params.get("spider_name") or spider_name

        command = [
            sys.executable,
            "-m",
            "scrapy",
            "crawl",
            spider_name,
            "-a",
            f"rule_file={payload_path.as_posix()}",
            "-s",
            f"LOG_FILE={log_path.as_posix()}",
            "-s",
            "LOG_STDOUT=True",
        ]

        env = os.environ.copy()
        python_paths = [env.get("PYTHONPATH", "")]
        base_dir = settings.BASE_DIR
        if base_dir not in python_paths[0]:
            python_paths.insert(0, base_dir)
        env["PYTHONPATH"] = ":".join(filter(None, python_paths))

        logger.info(
            "未配置 Redis，切换为本地 Scrapy 执行。任务ID: {}，执行目录: {}",
            task_id,
            run_dir,
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(self._project_root),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as exc:
            raise TaskExecutionException(
                "执行 Scrapy 失败，未找到命令。请确保依赖已安装。"
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise TaskExecutionException(f"启动本地 Scrapy 失败: {exc}") from exc

        stdout_bytes, stderr_bytes = await process.communicate()
        stdout_text = stdout_bytes.decode("utf-8", errors="ignore")
        stderr_text = stderr_bytes.decode("utf-8", errors="ignore")

        success = process.returncode == 0
        if success:
            logger.info("本地 Scrapy 执行完成，任务ID: {}", task_id)
        else:
            logger.error(
                "本地 Scrapy 执行失败，任务ID: {}，返回码: {}，stderr: {}",
                task_id,
                process.returncode,
                stderr_text[-4000:],
            )

        return {
            "success": success,
            "executor": "local",
            "queue": self.QUEUE_NAME,
            "task_id": task_id,
            "task": task_json,
            "payload_file": str(payload_path),
            "log_file": str(log_path),
            "stdout": stdout_text[-4000:] if stdout_text else "",
            "stderr": stderr_text[-4000:] if stderr_text else "",
            "returncode": process.returncode,
            "message": "本地 Scrapy 执行完成" if success else "本地 Scrapy 执行失败",
        }


class SpiderTaskDispatcher:
    """统一爬虫任务调度网关"""

    def __init__(self):
        self._redis_executor = RedisSpiderExecutor()
        self._local_executor = LocalScrapyExecutor()

    def _use_redis(self):
        return bool(settings.REDIS_URL and settings.REDIS_URL.strip())

    async def submit_rule_task(
        self,
        project,
        rule_detail,
        execution_id,
        params=None,
    ):
        if self._use_redis():
            return await self._redis_executor.submit_rule_task(
                project=project,
                rule_detail=rule_detail,
                execution_id=execution_id,
                params=params,
            )

        return await self._local_executor.submit_rule_task(
            project=project,
            rule_detail=rule_detail,
            execution_id=execution_id,
            params=params,
        )


spider_task_dispatcher = SpiderTaskDispatcher()

