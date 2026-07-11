"""模板渲染独立执行入口。

由 ``RenderPlugin._build_template_plan`` 生成 ExecPlan 时通过绝对路径调用：

    python /path/to/_runner.py \
        --engine jinja2 \
        --template-path <relative to template-dir> \
        --output-file <relative to cwd> \
        --template-dir <relative to cwd> \
        [--context-file <path>]

用户可控数据（模板路径、输出路径、模板目录、渲染上下文）全部通过
argv / env / 独立 JSON 文件传入，不再拼进 Python 源码，杜绝 ``python -c``
形式的模板注入 RCE。

设计要点：
- 脚本本身**不依赖** ``antcode_worker`` 包，可以用项目 venv 里的 python 直接运行。
- 上下文数据默认从环境变量 ``ANTCODE_RENDER_CONTEXT_JSON`` 读（JSON 字符串），
  也支持 ``--context-file`` 指向的 JSON 文件；env 变量方式不涉及 argv 长度限制之外，
  也避免临时文件清理。
- 只支持 ``jinja2`` / ``mako``；其他 engine 在插件层已提前拒绝。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path


def _load_context(context_file: str | None) -> dict:
    if context_file:
        with open(context_file, encoding="utf-8") as f:
            return json.load(f)
    raw = os.environ.get("ANTCODE_RENDER_CONTEXT_JSON", "{}")
    return json.loads(raw)


def _render_jinja2(template_path: str, template_dir: str, context_data: dict) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape  # noqa: PLC0415

    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(("html", "htm", "xml")),
    )
    template = env.get_template(template_path)
    return template.render(**context_data)


def _render_mako(template_path: str, template_dir: str, context_data: dict) -> str:
    from mako.lookup import TemplateLookup  # noqa: PLC0415
    from mako.template import Template  # noqa: PLC0415

    lookup = TemplateLookup(directories=[template_dir])
    template = Template(filename=template_path, lookup=lookup)
    return template.render(**context_data)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AntCode render task runner")
    parser.add_argument("--engine", required=True, choices=["jinja2", "mako"])
    parser.add_argument("--template-path", required=True, dest="template_path")
    parser.add_argument("--output-file", required=True, dest="output_file")
    parser.add_argument("--template-dir", default=".", dest="template_dir")
    parser.add_argument(
        "--context-file",
        default=None,
        dest="context_file",
        help="JSON 文件路径；缺省时从 env ANTCODE_RENDER_CONTEXT_JSON 读取",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger = logging.getLogger("antcode.render")

    context_data = _load_context(args.context_file)

    if args.engine == "jinja2":
        result = _render_jinja2(args.template_path, args.template_dir, context_data)
    elif args.engine == "mako":
        result = _render_mako(args.template_path, args.template_dir, context_data)
    else:
        # argparse 的 choices 已经拦了，这里兜底
        parser.error(f"不支持的模板引擎: {args.engine}")

    Path(args.output_file).write_text(result, encoding="utf-8")
    logger.info("Rendered to %s", args.output_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
