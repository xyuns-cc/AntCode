"""
渲染任务插件

生成渲染任务的 ExecPlan。

Requirements: 8.5
"""

import os
from typing import Any

from antcode_worker.domain.enums import TaskType
from antcode_worker.domain.models import ExecPlan, RunContext, TaskPayload
from antcode_worker.plugins.base import PluginBase


class RenderPlugin(PluginBase):
    """
    渲染任务插件

    生成渲染任务执行的 ExecPlan。
    支持多种渲染引擎：Jinja2、Mako、Playwright 等。

    Requirements: 8.5
    """

    @property
    def name(self) -> str:
        return "render"

    @property
    def priority(self) -> int:
        return 15  # 介于 spider 和 code 之间

    def match(self, payload: TaskPayload) -> bool:
        return payload.task_type == TaskType.RENDER

    def validate(self, payload: TaskPayload) -> list[str]:
        errors = []
        render_config = self._extract_render_config(payload)
        engine = render_config.get("engine", "jinja2")

        if engine in ("jinja2", "mako"):
            if not payload.entry_point:
                errors.append("entry_point 不能为空（模板文件路径）")
        elif engine == "playwright":
            if not render_config.get("url") and not payload.entry_point:
                errors.append("playwright 模式需要 url 或 entry_point")
        elif engine == "script" and not payload.entry_point:
            errors.append("script 模式需要 entry_point")

        return errors

    async def build_plan(
        self,
        context: RunContext,
        payload: TaskPayload,
    ) -> ExecPlan:
        """
        构建渲染执行计划

        支持多种渲染引擎：
        1. jinja2: Jinja2 模板渲染
        2. mako: Mako 模板渲染
        3. playwright: 浏览器渲染（截图/PDF）
        4. script: 自定义渲染脚本
        """
        python_exe = self._get_python_executable(context)
        render_config = self._extract_render_config(payload)
        engine = render_config.get("engine", "jinja2")

        if engine == "playwright":
            return self._build_playwright_plan(
                python_exe, context, payload, render_config
            )
        elif engine in ("jinja2", "mako"):
            return self._build_template_plan(
                python_exe, context, payload, render_config
            )
        else:
            return self._build_script_plan(
                python_exe, context, payload, render_config
            )

    def _extract_render_config(self, payload: TaskPayload) -> dict[str, Any]:
        """从 payload 提取渲染配置"""
        config = {
            "engine": payload.kwargs.get("engine", "jinja2"),
            "output_file": payload.kwargs.get("output_file", "output.html"),
            "output_format": payload.kwargs.get("output_format", "html"),
            "template_dir": payload.kwargs.get("template_dir"),
            "context_data": payload.kwargs.get("context_data", {}),
            # Playwright 特定配置
            "url": payload.kwargs.get("url"),
            "viewport": payload.kwargs.get("viewport", {"width": 1920, "height": 1080}),
            "wait_for": payload.kwargs.get("wait_for"),
            "screenshot": payload.kwargs.get("screenshot", False),
            "pdf": payload.kwargs.get("pdf", False),
            "full_page": payload.kwargs.get("full_page", True),
        }
        return config

    def _build_template_plan(
        self,
        python_exe: str,
        context: RunContext,
        payload: TaskPayload,
        config: dict[str, Any],
    ) -> ExecPlan:
        """构建模板渲染执行计划"""
        engine = config.get("engine", "jinja2")

        # 使用内联脚本执行模板渲染
        render_script = self._generate_template_render_script(
            engine=engine,
            template_path=payload.entry_point,
            output_file=config.get("output_file", "output.html"),
            template_dir=config.get("template_dir"),
            context_data=config.get("context_data", {}),
        )

        args = ["-c", render_script]

        # 环境变量
        env = dict(payload.env_vars)
        if payload.project_path:
            pythonpath = env.get("PYTHONPATH", "")
            if pythonpath:
                env["PYTHONPATH"] = os.pathsep.join([payload.project_path, pythonpath])
            else:
                env["PYTHONPATH"] = payload.project_path

        # 工作目录
        cwd = payload.project_path or os.getcwd()

        # 产物模式
        artifact_patterns = list(payload.artifact_patterns)
        artifact_patterns.append(config.get("output_file", "output.html"))

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

    def _build_playwright_plan(
        self,
        python_exe: str,
        context: RunContext,
        payload: TaskPayload,
        config: dict[str, Any],
    ) -> ExecPlan:
        """构建 Playwright 渲染执行计划"""
        # 如果有入口脚本，直接执行
        if payload.entry_point and payload.entry_point.endswith(".py"):
            args = [payload.entry_point]
            args.extend(payload.args)
        else:
            # 使用内联脚本
            render_script = self._generate_playwright_script(
                url=config.get("url") or payload.entry_point,
                output_file=config.get("output_file", "output.png"),
                viewport=config.get("viewport", {"width": 1920, "height": 1080}),
                wait_for=config.get("wait_for"),
                screenshot=config.get("screenshot", True),
                pdf=config.get("pdf", False),
                full_page=config.get("full_page", True),
            )
            args = ["-c", render_script]

        # 环境变量
        env = dict(payload.env_vars)
        if payload.project_path:
            pythonpath = env.get("PYTHONPATH", "")
            if pythonpath:
                env["PYTHONPATH"] = os.pathsep.join([payload.project_path, pythonpath])
            else:
                env["PYTHONPATH"] = payload.project_path

        # Playwright 需要的环境变量
        env.setdefault("PLAYWRIGHT_BROWSERS_PATH", "0")

        # 工作目录
        cwd = payload.project_path or os.getcwd()

        # 产物模式
        artifact_patterns = list(payload.artifact_patterns)
        artifact_patterns.append(config.get("output_file", "output.png"))

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
        if payload.project_path:
            pythonpath = env.get("PYTHONPATH", "")
            if pythonpath:
                env["PYTHONPATH"] = os.pathsep.join([payload.project_path, pythonpath])
            else:
                env["PYTHONPATH"] = payload.project_path

        # 渲染特定环境变量
        if config.get("output_file"):
            env["RENDER_OUTPUT_FILE"] = config["output_file"]
        if config.get("output_format"):
            env["RENDER_OUTPUT_FORMAT"] = config["output_format"]

        # 工作目录
        cwd = payload.project_path or os.getcwd()

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

    def _generate_template_render_script(
        self,
        engine: str,
        template_path: str,
        output_file: str,
        template_dir: str | None,
        context_data: dict[str, Any],
    ) -> str:
        """生成模板渲染脚本"""
        import json
        context_json = json.dumps(context_data)

        if engine == "jinja2":
            return f'''
import json
import logging
from pathlib import Path
from jinja2 import Environment, FileSystemLoader

template_path = "{template_path}"
output_file = "{output_file}"
template_dir = "{template_dir or '.'}"
context_data = json.loads({repr(context_json)})

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

env = Environment(loader=FileSystemLoader(template_dir))
template = env.get_template(template_path)
result = template.render(**context_data)

Path(output_file).write_text(result, encoding="utf-8")
logger.info("Rendered to %s", output_file)
'''
        elif engine == "mako":
            return f'''
import json
import logging
from pathlib import Path
from mako.template import Template
from mako.lookup import TemplateLookup

template_path = "{template_path}"
output_file = "{output_file}"
template_dir = "{template_dir or '.'}"
context_data = json.loads({repr(context_json)})

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

lookup = TemplateLookup(directories=[template_dir])
template = Template(filename=template_path, lookup=lookup)
result = template.render(**context_data)

Path(output_file).write_text(result, encoding="utf-8")
logger.info("Rendered to %s", output_file)
'''
        else:
            raise ValueError(f"不支持的模板引擎: {engine}")

    def _generate_playwright_script(
        self,
        url: str,
        output_file: str,
        viewport: dict[str, int],
        wait_for: str | None,
        screenshot: bool,
        pdf: bool,
        full_page: bool,
    ) -> str:
        """生成 Playwright 渲染脚本"""
        return f'''
import asyncio
import logging
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)
output_file = "{output_file}"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(
            viewport={{"width": {viewport.get("width", 1920)}, "height": {viewport.get("height", 1080)}}}
        )
        await page.goto("{url}")
        {f'await page.wait_for_selector("{wait_for}")' if wait_for else ''}
        {f'await page.screenshot(path=output_file, full_page={full_page})' if screenshot else ''}
        {f'await page.pdf(path=output_file)' if pdf else ''}
        await browser.close()
        logger.info("Rendered to %s", output_file)

asyncio.run(main())
'''

    def _get_python_executable(self, context: RunContext) -> str:
        """获取 Python 可执行文件路径"""
        if context.runtime_spec and context.runtime_spec.python_path:
            return context.runtime_spec.python_path
        import sys
        return sys.executable
