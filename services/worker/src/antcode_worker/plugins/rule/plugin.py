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
        return errors

    async def build_plan(
        self, context: RunContext, payload: TaskPayload
    ) -> ExecPlan:
        python_exe = self._get_python_executable(context)
        rule = self._extract_rule(payload)

        # 把 rule JSON 写到 run 目录下的临时文件（extraction_rules 可能很大）
        rule_dir = self._resolve_rule_dir(context, payload)
        os.makedirs(rule_dir, exist_ok=True)
        fd, rule_path = tempfile.mkstemp(
            prefix=f"rule-{context.run_id[:12]}-", suffix=".json", dir=rule_dir
        )
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

        # 规则爬虫执行引擎：``antcode_scrapy.crawl`` (Scrapy)。
        # env 变量名与 --rule-file 参数与旧 spiderkit CLI 完全兼容。
        args = [
            "-m",
            "antcode_scrapy.crawl",
            "--rule-file",
            rule_path,
        ]

        env = dict(payload.env_vars)
        env.setdefault("ANTCODE_SPIDER_RUN_ID", context.run_id)
        env.setdefault("ANTCODE_SPIDER_PROJECT_ID", payload.project_id or "")
        # R1-P0-7 (审查报告): Redis URL 必须从**实际 transport/config** 拿，
        # 不能只看 os.environ["WORKER_REDIS_URL"]——用户通过配置文件/CLI
        # 传的 redis_url 从来不会 write back 到 environ（config.py:109 只
        # 从 env 读，从不回写）。
        #
        # T6-T3b: gateway 传输模式下通过新增的 SpiderData sink 走 gRPC 上报，
        # 不再直连 Redis；老 fail-fast 分支删除。子进程根据
        # ANTCODE_SPIDER_SINK_MODE 决定走哪条 sink。
        from antcode_worker.config import get_worker_config

        wcfg = get_worker_config()
        transport_mode = str(getattr(wcfg, "transport_mode", "") or "").lower()

        if transport_mode == "gateway":
            # P1-24 (审查报告): WorkerConfig 上并不存在 ``gateway_endpoint`` /
            # ``gateway_use_tls`` / ``gateway_auth_token`` 三个字段（见
            # ``worker/config.py::WorkerConfig``），旧实现全部走 ``getattr(..., "")``
            # 静默拿到空串，然后 fallback 到 env 变量；如果 env 也没配就直接
            # ``raise RuntimeError`` —— 用户从配置文件里根本没办法为规则爬虫
            # 打开 gateway sink，导致 rule 项目在 gateway 模式必然 exit 1。
            #
            # 修复：
            # 1. 优先从 WorkerConfig 真正存在的字段 ``gateway_host`` / ``gateway_port``
            #    组装 endpoint（这是 gateway 模式下 worker 自身连的 gateway 地址,
            #    子进程 SpiderData sink 也应该走同一个 endpoint）。
            # 2. TLS / auth token 目前 WorkerConfig 未建模，走 env fallback；
            #    若两处都没配置就明确 error message 提示缺什么。
            endpoint = os.environ.get("WORKER_GATEWAY_ENDPOINT", "").strip()
            if not endpoint:
                host = str(getattr(wcfg, "gateway_host", "") or "").strip()
                port = getattr(wcfg, "gateway_port", 0) or 0
                if host and int(port) > 0:
                    endpoint = f"{host}:{int(port)}"
            if not endpoint:
                raise RuntimeError(
                    "规则爬虫 gateway 模式需要 gateway endpoint，但 worker 配置的 "
                    "gateway_host/gateway_port 为空且 WORKER_GATEWAY_ENDPOINT 未设置。"
                )
            env["ANTCODE_SPIDER_SINK_MODE"] = "gateway"
            env["ANTCODE_SPIDER_GATEWAY_ENDPOINT"] = endpoint
            # TLS 开关只从 env 读（WorkerConfig 未建模）
            tls_flag = os.environ.get("WORKER_GATEWAY_USE_TLS", "").strip().lower()
            if tls_flag in ("1", "true", "yes", "on"):
                env["ANTCODE_SPIDER_GATEWAY_SECURE"] = "1"
            # auth token 同上，仅走 env fallback
            token = os.environ.get("WORKER_GATEWAY_AUTH_TOKEN", "").strip()
            if token:
                env["ANTCODE_SPIDER_GATEWAY_AUTH_TOKEN"] = token
        else:
            # direct 模式：Redis URL 走原路径
            redis_url = (
                getattr(wcfg, "redis_url", "")
                or os.environ.get("WORKER_REDIS_URL", "")
            )
            if not redis_url:
                raise RuntimeError(
                    "规则爬虫 direct 模式需要 Redis 上报通道，但 worker 未配置 "
                    "redis_url（config.redis_url / WORKER_REDIS_URL 均为空）。"
                )
            env["ANTCODE_SPIDER_SINK_MODE"] = "redis"
            env["ANTCODE_SPIDER_REDIS_URL"] = redis_url

        namespace = (
            getattr(wcfg, "redis_namespace", "")
            or os.environ.get("WORKER_REDIS_NAMESPACE", "")
        )
        if namespace:
            env["ANTCODE_SPIDER_REDIS_NAMESPACE"] = namespace
        # 传 worker_id 让子进程 gateway sink 上报时能带上
        worker_id = str(getattr(wcfg, "worker_id", "") or "").strip()
        if worker_id:
            env["ANTCODE_WORKER_ID"] = worker_id

        cwd = payload.project_cwd or payload.workspace_path or rule_dir

        return ExecPlan(
            command=python_exe,
            args=args,
            env=env,
            cwd=cwd,
            timeout_seconds=context.timeout_seconds,
            memory_limit_mb=context.memory_limit_mb,
            cpu_limit_seconds=context.cpu_limit_seconds,
            artifact_patterns=list(payload.artifact_patterns),
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
            if isinstance(candidate, dict) and (
                "target_url" in candidate or "extraction_rules" in candidate
            ):
                return dict(candidate)
        # 什么都没有：交给 validate 报错
        return dict(kwargs)

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
        base = payload.workspace_path or payload.project_cwd
        if base:
            return os.path.join(base, ".antcode-rule")
        # 没有 workspace 时（rule 项目通常没有代码）用 /tmp 分片目录
        return os.path.join(
            tempfile.gettempdir(), "antcode-rule", context.run_id or "unknown"
        )
