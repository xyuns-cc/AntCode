"""规则爬虫任务插件。

用户在前端"规则项目"里配置 ``target_url`` + ``extraction_rules`` +
``pagination_config`` 后，master 派发时把 ``ProjectRule.to_dispatch_dict()``
放进 ``params.kwargs``；本插件把它转成命令行调用
``python -m antcode_scrapy.crawl`` 让子进程消费（S2 后走 Scrapy 引擎）。

设计要点：
- 走 python module 命令，无需 workspace（rule 项目通常不带用户代码）；这时
  ``project_cwd`` 可以为空，用 worker 内部 runtime 目录当 cwd 兜底。
- rule JSON 通过临时文件传，避免 --rule-json 过长（extraction_rules 可能很大）
  被 shell arg 长度限制截掉；子进程结束由 worker cleanup 目录清理。
- 幂等：CLI runner 严格 exit != 0 报错，不静默降级。
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from antcode_worker.domain.enums import TaskType
from antcode_worker.domain.models import ExecPlan, RunContext, TaskPayload
from antcode_worker.executor.rule_policy import RULE_SANDBOX_ALLOW_NETWORK
from antcode_worker.plugins.base import PluginBase


class RulePlugin(PluginBase):
    """规则爬虫任务插件（``TaskType.RULE`` / project_type="rule"）。"""

    @property
    def name(self) -> str:
        return "rule"

    @property
    def priority(self) -> int:
        return 30  # 高于 spider（20）—— 精确 match rule 类型

    def match(self, payload: TaskPayload) -> bool:
        return payload.task_type == TaskType.RULE

    def validate(self, payload: TaskPayload) -> list[str]:
        errors: list[str] = []
        rule = self._extract_rule(payload)
        if not rule.get("target_url"):
            errors.append("规则任务缺少 target_url")
        extraction_rules = rule.get("extraction_rules") or []
        if not isinstance(extraction_rules, list) or not extraction_rules:
            errors.append("规则任务缺少 extraction_rules")
        errors.extend(self._validate_secure_spool_features(rule))
        return errors

    async def build_plan(self, context: RunContext, payload: TaskPayload) -> ExecPlan:
        python_exe = self._get_python_executable(context)
        rule = self._extract_rule(payload)

        # 把 rule JSON 写到 run 目录下的临时文件（extraction_rules 可能很大）
        rule_dir = self._resolve_rule_dir(context, payload)
        os.makedirs(rule_dir, exist_ok=True)
        fd, rule_path = tempfile.mkstemp(prefix=f"rule-{context.run_id[:12]}-", suffix=".json", dir=rule_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(rule, f, ensure_ascii=False)
        except Exception:
            # 打开失败时清理临时文件
            try:
                os.unlink(rule_path)
            except OSError:
                pass
            raise

        spool_path = self._create_spool(rule_dir, context.run_id)
        args = [
            "-m",
            "antcode_scrapy.crawl",
            "--rule-file",
            rule_path,
        ]

        env = {
            "ANTCODE_SPIDER_RUN_ID": context.run_id,
            "ANTCODE_SPIDER_PROJECT_ID": payload.project_id or "",
            "ANTCODE_SPIDER_SINK_MODE": "spool",
            "ANTCODE_SPIDER_SPOOL_PATH": spool_path,
        }

        cwd = payload.project_cwd or payload.workspace_path or rule_dir

        # P0-03: Rule 插件默认不放行网络。历史上恒 True 因为爬虫要访问
        # target_url,但那把整个宿主网络暴露给用户提供的规则(headers/cookies
        # 可含内部凭据、target_url 可指向内部地址),对于不可信租户是横向移动向量。
        # 现在改为需显式 ANTCODE_RULE_ALLOW_NETWORK=1 才放行;生产环境应结合
        # K8s NetworkPolicy / egress proxy 而非依赖 Worker 全放行。
        allow_network = os.getenv("ANTCODE_RULE_ALLOW_NETWORK", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

        return ExecPlan(
            command=python_exe,
            args=args,
            env=env,
            cwd=cwd,
            timeout_seconds=context.timeout_seconds,
            memory_limit_mb=context.memory_limit_mb,
            cpu_limit_seconds=context.cpu_limit_seconds,
            artifact_patterns=list(payload.artifact_patterns),
            sandbox_config={RULE_SANDBOX_ALLOW_NETWORK: allow_network},
        )

    # ---------- helpers ----------

    def _extract_rule(self, payload: TaskPayload) -> dict[str, Any]:
        """规则字典优先从 ``payload.kwargs`` 取（master 通过 params.kwargs 塞）。"""
        kwargs = payload.kwargs if isinstance(payload.kwargs, dict) else {}
        # 常见键名兜底：直接 kwargs 顶层含规则字段，或者 kwargs["rule"] 是完整包
        if "target_url" in kwargs or "extraction_rules" in kwargs:
            return dict(kwargs)
        for key in ("rule", "rule_detail", "dispatch"):
            candidate = kwargs.get(key)
            if isinstance(candidate, dict) and ("target_url" in candidate or "extraction_rules" in candidate):
                return dict(candidate)
        # 什么都没有：交给 validate 报错
        return dict(kwargs)

    @staticmethod
    def _validate_secure_spool_features(rule: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if rule.get("resume_enabled"):
            errors.append("安全 spool 模式暂不支持 resume_enabled：需要 Worker 父进程 checkpoint")
        proxy_config = rule.get("proxy_config")
        if proxy_config is not None and not isinstance(proxy_config, dict):
            errors.append("proxy_config 必须是对象")
        elif proxy_config and proxy_config.get("enabled"):
            errors.append("安全 spool 模式暂不支持 proxy_config：拒绝把代理凭据交给子进程")
        return errors

    def _get_python_executable(self, context: RunContext) -> str:
        """P8: rule 任务恒用 worker 自己的 python。

        规则爬虫是 worker 内建能力（S2 之后走 ``antcode_scrapy.crawl``），
        它依赖 worker 自己安装的 Scrapy / scrapy-playwright / scrapy-redis
        依赖 —— **不是**用户 code 项目的运行时。此前从
        ``context.runtime_spec.python_path`` 取用户 venv 的 python，
        子进程立刻 ``ModuleNotFoundError``，rule 每次派发都 exit 1。
        用户 venv 只服务 code/file 项目。
        """
        import sys

        return sys.executable

    def _resolve_rule_dir(self, context: RunContext, payload: TaskPayload) -> str:
        """把 rule JSON 放到 run 目录里，交给 project_fetcher.cleanup 一起清。"""
        base = payload.project_cwd or payload.workspace_path
        if base:
            return os.path.join(base, ".antcode-rule")
        # 没有 workspace 时（rule 项目通常没有代码）用 /tmp 分片目录
        return os.path.join(tempfile.gettempdir(), "antcode-rule", context.run_id or "unknown")

    @staticmethod
    def _create_spool(rule_dir: str, run_id: str) -> str:
        fd, path = tempfile.mkstemp(
            prefix=f"spool-{run_id[:12]}-",
            suffix=".jsonl",
            dir=rule_dir,
        )
        os.close(fd)
        return path
