"""爬虫任务插件。"""

import os
from typing import Any

from antcode_worker.domain.enums import TaskType
from antcode_worker.domain.models import ExecPlan, RunContext, TaskPayload
from antcode_worker.plugins.base import PluginBase


class SpiderPlugin(PluginBase):
    """
    爬虫任务插件

    生成爬虫任务执行的 ExecPlan。
    支持 Scrapy 和自定义爬虫框架。

    Requirements: 8.4
    """

    @property
    def name(self) -> str:
        return "spider"

    @property
    def priority(self) -> int:
        return 20  # 高于 code plugin

    def match(self, payload: TaskPayload) -> bool:
        return payload.task_type == TaskType.SPIDER

    def validate(self, payload: TaskPayload) -> list[str]:
        errors = []
        if not payload.entry_point:
            errors.append("entry_point 不能为空（爬虫入口脚本）")
        return errors

    async def build_plan(
        self,
        context: RunContext,
        payload: TaskPayload,
    ) -> ExecPlan:
        """
        构建爬虫执行计划

        支持两种模式：
        1. Scrapy 模式：entry_point 为 spider 名称
        2. 脚本模式：entry_point 为 Python 脚本路径
        """
        python_exe = self._get_python_executable(context)
        spider_config = self._extract_spider_config(payload)

        # 判断执行模式
        if spider_config.get("framework") == "scrapy":
            plan = self._build_scrapy_plan(python_exe, context, payload, spider_config)
        else:
            plan = self._build_script_plan(python_exe, context, payload, spider_config)

        # V7: 注入 spider 子进程联系 RedisDataReporter / GatewayDataReporter
        # 所需的最小上下文。子脚本里读这些 env 即可启动 reporter,无需
        # 业务代码硬编码 redis url。WORKER_REDIS_URL / WORKER_REDIS_NAMESPACE
        # 已在父 worker 进程的 environ 里 (由 config 模块负责设置)。
        self._inject_reporter_env(plan, payload)
        return plan

    def _inject_reporter_env(self, plan: ExecPlan, payload: TaskPayload) -> None:
        """把 reporter 配置写入 plan.env, 子进程通过 env 拉起 SpiderDataReporter."""
        redis_url = os.environ.get("WORKER_REDIS_URL", "")
        if redis_url:
            plan.env.setdefault("ANTCODE_SPIDER_REDIS_URL", redis_url)
            plan.env.setdefault("ANTCODE_SPIDER_REPORTER", "redis")
        # run_id / project_id 来自 payload(engine 已在 _execute_task 写入),
        # 即使没有 redis 也提供,便于 gateway-mode reporter 或日志关联使用。
        if payload.run_id:
            plan.env.setdefault("ANTCODE_SPIDER_RUN_ID", payload.run_id)
        if payload.project_id:
            plan.env.setdefault("ANTCODE_SPIDER_PROJECT_ID", payload.project_id)
        namespace = os.environ.get("WORKER_REDIS_NAMESPACE", "")
        if namespace:
            plan.env.setdefault("ANTCODE_SPIDER_REDIS_NAMESPACE", namespace)

    def _extract_spider_config(self, payload: TaskPayload) -> dict[str, Any]:
        """从 payload 提取爬虫配置"""
        config = {
            "framework": payload.kwargs.get("framework", "script"),
            "spider_name": payload.kwargs.get("spider_name"),
            "settings": payload.kwargs.get("settings", {}),
            "output_format": payload.kwargs.get("output_format", "json"),
            "output_file": payload.kwargs.get("output_file"),
            "log_level": payload.kwargs.get("log_level", "INFO"),
            "concurrent_requests": payload.kwargs.get("concurrent_requests", 16),
            "download_delay": payload.kwargs.get("download_delay", 0),
        }
        return config

    def _build_scrapy_plan(
        self,
        python_exe: str,
        context: RunContext,
        payload: TaskPayload,
        config: dict[str, Any],
    ) -> ExecPlan:
        """构建 Scrapy 执行计划"""
        args = ["-m", "scrapy", "crawl"]

        # Spider 名称
        spider_name = config.get("spider_name") or payload.entry_point
        args.append(spider_name)

        # 输出配置
        if config.get("output_file"):
            args.extend(["-o", config["output_file"]])
            args.extend(["-t", config.get("output_format", "json")])

        # Scrapy 设置
        settings = config.get("settings", {})
        settings.setdefault("LOG_LEVEL", config.get("log_level", "INFO"))
        settings.setdefault("CONCURRENT_REQUESTS", config.get("concurrent_requests", 16))
        if config.get("download_delay"):
            settings["DOWNLOAD_DELAY"] = config["download_delay"]

        for key, value in settings.items():
            args.extend(["-s", f"{key}={value}"])

        # 额外参数
        args.extend(payload.args)

        # 环境变量
        env = dict(payload.env_vars)
        if payload.workspace_path:
            pythonpath = env.get("PYTHONPATH", "")
            if pythonpath:
                env["PYTHONPATH"] = os.pathsep.join([payload.workspace_path, pythonpath])
            else:
                env["PYTHONPATH"] = payload.workspace_path

        cwd = self._get_project_cwd(payload)

        # 产物模式
        artifact_patterns = list(payload.artifact_patterns)
        if config.get("output_file"):
            artifact_patterns.append(config["output_file"])

        return ExecPlan(
            command=python_exe,
            args=args,
            env=env,
            cwd=cwd,
            timeout_seconds=context.timeout_seconds,
            memory_limit_mb=context.memory_limit_mb,
            cpu_limit_seconds=context.cpu_limit_seconds,
            artifact_patterns=artifact_patterns,
        )

    def _build_script_plan(
        self,
        python_exe: str,
        context: RunContext,
        payload: TaskPayload,
        config: dict[str, Any],
    ) -> ExecPlan:
        """构建脚本模式执行计划"""
        args = [payload.entry_point]
        args.extend(payload.args)

        # 环境变量
        env = dict(payload.env_vars)
        if payload.workspace_path:
            pythonpath = env.get("PYTHONPATH", "")
            if pythonpath:
                env["PYTHONPATH"] = os.pathsep.join([payload.workspace_path, pythonpath])
            else:
                env["PYTHONPATH"] = payload.workspace_path

        # 爬虫特定环境变量
        env["SPIDER_LOG_LEVEL"] = config.get("log_level", "INFO")
        if config.get("output_file"):
            env["SPIDER_OUTPUT_FILE"] = config["output_file"]
        if config.get("output_format"):
            env["SPIDER_OUTPUT_FORMAT"] = config["output_format"]

        cwd = self._get_project_cwd(payload)

        # 产物模式
        artifact_patterns = list(payload.artifact_patterns)
        if config.get("output_file"):
            artifact_patterns.append(config["output_file"])

        return ExecPlan(
            command=python_exe,
            args=args,
            env=env,
            cwd=cwd,
            timeout_seconds=context.timeout_seconds,
            memory_limit_mb=context.memory_limit_mb,
            cpu_limit_seconds=context.cpu_limit_seconds,
            artifact_patterns=artifact_patterns,
        )

    def _get_python_executable(self, context: RunContext) -> str:
        """获取 Python 可执行文件路径"""
        if context.runtime_spec and context.runtime_spec.python_path:
            return context.runtime_spec.python_path
        raise RuntimeError("runtime_spec.python_path 不能为空")

    def _get_project_cwd(self, payload: TaskPayload) -> str:
        cwd = payload.project_cwd or payload.workspace_path
        if not cwd:
            raise RuntimeError("project_cwd 不能为空")
        return cwd
