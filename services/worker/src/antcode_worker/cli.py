"""命令行入口

使用 HealthServer (aiohttp) 替代 FastAPI 提供健康检查端点。
支持 Direct 模式（内网直连 Redis）和 Gateway 模式（公网走 gRPC）。
支持 run, doctor, print-config 命令。

Requirements: 2.1, 7.1, 7.2
"""

import argparse
import contextlib
import os
import secrets
import sys
import time

import yaml
from loguru import logger

from antcode_worker.config import (
    DATA_ROOT,
    WORKER_CONFIG_FILE,
    PROJECT_ROOT,
    SERVICE_ROOT,
    WorkerConfig,
    init_worker_config,
)


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def _generate_default_worker_name() -> str:
    return f"worker-{secrets.token_hex(4)}"


def _get_default_redis_url() -> str:
    from antcode_worker.config import _get_env_value, _load_env_file

    _load_env_file()
    return (
        _get_env_value("WORKER_REDIS_URL", "REDIS_URL", "ANTCODE_REDIS_URL")
        or "redis://localhost:6379/0"
    )


def _get_default_gateway_host_port() -> tuple[str, int]:
    from antcode_worker.config import _get_env_int, _get_env_value, _load_env_file

    _load_env_file()
    host = _get_env_value("WORKER_GATEWAY_HOST", "GATEWAY_HOST", "ANTCODE_GATEWAY_HOST") or "localhost"
    port = _get_env_int("WORKER_GATEWAY_PORT", "GATEWAY_PORT", "ANTCODE_GATEWAY_PORT") or 50051
    endpoint = _get_env_value("WORKER_GATEWAY_ENDPOINT", "GATEWAY_ENDPOINT", "ANTCODE_GATEWAY_ENDPOINT")
    if endpoint:
        if ":" in endpoint:
            endpoint_host, endpoint_port = endpoint.rsplit(":", 1)
            host = endpoint_host or host
            with contextlib.suppress(ValueError):
                port = int(endpoint_port)
        else:
            host = endpoint
    return host, port


def _get_default_gateway_endpoint() -> str:
    host, port = _get_default_gateway_host_port()
    return f"{host}:{port}"


def _parse_gateway_endpoint(endpoint: str, default_port: int = 50051) -> tuple[str, int]:
    raw = (endpoint or "").strip()
    if not raw:
        return "localhost", default_port
    if "://" in raw:
        from urllib.parse import urlparse

        parsed = urlparse(raw)
        host = parsed.hostname or raw
        port = parsed.port or default_port
        return host, port
    if ":" in raw:
        host, port_str = raw.rsplit(":", 1)
        with contextlib.suppress(ValueError):
            return host, int(port_str)
    return raw, default_port


def _log_block(message: str) -> None:
    logger.info("{}", message.rstrip())


def print_banner():
    """打印启动横幅"""
    _log_block(
        "  AntCode Worker v0.1.0 (Dual Transport Mode)\n"
        "  =================================================\n"
        "  支持模式: Direct (Redis) / Gateway (gRPC)"
    )


def print_main_menu():
    """打印主菜单"""
    _log_block(
        "  [1] 启动 Worker (Direct 模式)\n"
        "  [2] 启动 Worker (Gateway 模式)\n"
        "  [3] 启动 Worker (配置文件模式)\n"
        "  [0] 退出"
    )


def show_help():
    """显示帮助信息"""
    _log_block(
        "  AntCode Worker v0.1.0 - 使用帮助\n"
        "  ======================================\n"
        "\n"
        "  传输模式说明:\n"
        "    - Direct 模式: 内网 Worker 直连 Redis Streams，低延迟\n"
        "    - Gateway 模式: 公网 Worker 通过 Gateway gRPC/TLS 连接，安全\n"
        "\n"
        "  功能:\n"
        "    - 虚拟环境管理 (创建/删除环境，安装/卸载包)\n"
        "    - 项目管理 (上传文件/代码项目，编辑代码)\n"
        "    - 任务执行 (运行项目，查看日志，取消任务)\n"
        "    - 平台通信 (心跳上报，状态同步，任务分发)\n"
        "\n"
        "  命令行参数:\n"
        "    --name          Worker 名称 (默认: Worker-001)\n"
        "    --port          健康检查端口 (默认: 8001)\n"
        "    --host          绑定地址 (默认: 0.0.0.0)\n"
        "    --transport     传输模式 (direct/gateway, 不传则使用配置文件)\n"
        "    --redis-url     Redis URL (Direct 模式)\n"
        "    --gateway-endpoint  Gateway 地址 (Gateway 模式, 例: host:port)\n"
        "    --worker-id     手动指定 Worker ID (Direct 模式)\n"
        "    --worker-key    安装 Key (Gateway 首次注册)\n"
        "\n"
        "  健康检查端点:\n"
        "    GET /health       - 基本状态\n"
        "    GET /health/live  - 存活探针\n"
        "    GET /health/ready - 就绪探针"
    )
    input("按 Enter 键返回...")


def run_doctor() -> int:
    """
    运行环境诊断

    检查项目:
    1. Python 版本
    2. uv 是否安装
    3. Redis 连接（Direct 模式）
    4. 目录结构
    5. 域模型导入

    Returns:
        0 表示全部通过，非 0 表示有问题

    Requirements: 2.1, 15.4
    """
    import shutil
    import subprocess

    _log_block(
        "  AntCode Worker - 环境诊断\n"
        "  " + "=" * 40
    )

    issues = []

    # 1. Python 版本检查
    logger.info("检查 Python 版本")
    py_version = sys.version_info
    if py_version >= (3, 11):
        logger.info("OK  Python {}.{}.{}", py_version.major, py_version.minor, py_version.micro)
    else:
        msg = f"Python 版本过低: {py_version.major}.{py_version.minor} (需要 >= 3.11)"
        logger.error("FAIL {}", msg)
        issues.append(msg)

    # 2. uv 检查
    logger.info("检查 uv 包管理器")
    uv_path = shutil.which("uv")
    if uv_path:
        try:
            result = subprocess.run(
                ["uv", "--version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            uv_version = result.stdout.strip()
            logger.info("OK  uv 已安装: {}", uv_version)
        except Exception:
            logger.info("OK  uv 已安装: {}", uv_path)
    else:
        msg = "uv 未安装 (运行 'pip install uv' 或 'brew install uv')"
        logger.error("FAIL {}", msg)
        issues.append(msg)

    # 3. 目录结构检查
    logger.info("检查 目录结构")
    required_dirs = [
        ("config/", SERVICE_ROOT / "config"),
        ("runtime_data/cache/", SERVICE_ROOT / "runtime_data" / "cache"),
        ("runtime_data/logs/", SERVICE_ROOT / "runtime_data" / "logs"),
        ("runtime_data/secrets/", SERVICE_ROOT / "runtime_data" / "secrets"),
        ("scripts/", SERVICE_ROOT / "scripts"),
        ("src/antcode_worker/", SERVICE_ROOT / "src" / "antcode_worker"),
        ("tests/unit/", SERVICE_ROOT / "tests" / "unit"),
        ("tests/integration/", SERVICE_ROOT / "tests" / "integration"),
    ]

    for name, path in required_dirs:
        if path.exists():
            logger.info("OK  {}", name)
        else:
            msg = f"目录缺失: {name}"
            logger.error("FAIL {}", msg)
            issues.append(msg)

    # 4. 配置文件检查
    logger.info("检查 配置文件")
    config_files = [
        ("worker.example.yaml", SERVICE_ROOT / "config" / "worker.example.yaml"),
        ("logging.example.yaml", SERVICE_ROOT / "config" / "logging.example.yaml"),
    ]

    for name, path in config_files:
        if path.exists():
            logger.info("OK  {}", name)
        else:
            msg = f"配置文件缺失: {name}"
            logger.error("FAIL {}", msg)
            issues.append(msg)

    # 5. 域模型导入检查
    logger.info("检查 域模型导入")
    try:
        import importlib
        models_module = importlib.import_module("antcode_worker.domain.models")
        # 验证关键类存在
        required_models = ["RunContext", "TaskPayload", "ExecPlan", "ExecResult", "LogEntry", "ArtifactRef", "RuntimeHandle"]
        for model_name in required_models:
            if not hasattr(models_module, model_name):
                raise ImportError(f"缺少 {model_name}")
        logger.info("OK  domain/models.py")
    except ImportError as e:
        msg = f"域模型导入失败: {e}"
        logger.error("FAIL {}", msg)
        issues.append(msg)

    try:
        enums_module = importlib.import_module("antcode_worker.domain.enums")
        required_enums = ["RunStatus", "LogStream", "TaskType", "ExitReason", "ArtifactType"]
        for enum_name in required_enums:
            if not hasattr(enums_module, enum_name):
                raise ImportError(f"缺少 {enum_name}")
        logger.info("OK  domain/enums.py")
    except ImportError as e:
        msg = f"枚举导入失败: {e}"
        logger.error("FAIL {}", msg)
        issues.append(msg)

    try:
        errors_module = importlib.import_module("antcode_worker.domain.errors")
        required_errors = ["WorkerError", "ExecutionError", "TransportError"]
        for error_name in required_errors:
            if not hasattr(errors_module, error_name):
                raise ImportError(f"缺少 {error_name}")
        logger.info("OK  domain/errors.py")
    except ImportError as e:
        msg = f"错误类导入失败: {e}"
        logger.error("FAIL {}", msg)
        issues.append(msg)

    # 6. 核心模块导入检查
    logger.info("检查 核心模块导入")
    core_modules = [
        ("config", "antcode_worker.config"),
        ("app/main", "antcode_worker.app.main"),
        ("app/wiring", "antcode_worker.app.wiring"),
        ("app/lifecycle", "antcode_worker.app.lifecycle"),
    ]

    for name, module in core_modules:
        try:
            __import__(module)
            logger.info("OK  {}", name)
        except ImportError as e:
            msg = f"模块导入失败 {name}: {e}"
            logger.error("FAIL {}", msg)
            issues.append(msg)

    # 7. 数据目录检查
    logger.info("检查 数据目录")
    data_dirs = [
        ("var/worker/", DATA_ROOT),
        ("var/worker/projects/", DATA_ROOT / "projects"),
        ("var/worker/venvs/", DATA_ROOT / "venvs"),
        ("var/worker/logs/", DATA_ROOT / "logs"),
        ("var/worker/executions/", DATA_ROOT / "executions"),
    ]

    for name, path in data_dirs:
        if path.exists():
            logger.info("OK  {}", name)
        else:
            # 数据目录可以自动创建，不算严重问题
            logger.warning("目录缺失: {} (将自动创建)", name)

    # 总结
    if issues:
        logger.error("诊断完成: 发现 {} 个问题", len(issues))
        for i, issue in enumerate(issues, 1):
            logger.error("{}. {}", i, issue)
        return 1
    else:
        logger.info("诊断完成: 所有检查通过")
        return 0


def print_config(config_format: str = "yaml") -> None:
    """
    打印当前有效配置

    Args:
        config_format: 输出格式 (yaml/json)

    Requirements: 2.1, 15.3
    """
    import json

    _log_block(
        "  AntCode Worker - 当前配置\n"
        "  " + "=" * 40
    )

    # 加载配置
    config = WorkerConfig.load_from_file()
    config_dict = config.to_dict()

    # 添加路径信息
    config_dict["_paths"] = {
        "service_root": str(SERVICE_ROOT),
        "data_root": str(DATA_ROOT),
        "config_file": str(WORKER_CONFIG_FILE),
    }

    if config_format == "json":
        logger.info("{}", json.dumps(config_dict, indent=2, ensure_ascii=False, default=str))
    else:
        logger.info("{}", yaml.dump(config_dict, allow_unicode=True, default_flow_style=False, sort_keys=False))


def start_worker(
    name: str = "Worker-001",
    port: int = 8001,
    host: str = "0.0.0.0",
    log_level: str = "INFO",
    transport_mode: str | None = None,
    redis_url: str | None = None,
    gateway_host: str = "localhost",
    gateway_port: int = 50051,
    worker_id: str | None = None,
    worker_key: str | None = None,
):
    """启动 Worker 服务

    支持两种传输模式：
    - Direct 模式：内网直连 Redis Streams
    - Gateway 模式：公网通过 Gateway gRPC 连接

    Args:
        name: Worker 名称
        port: 健康检查端口
        host: 绑定地址
        log_level: 日志级别
        transport_mode: 传输模式 (direct/gateway, None 使用配置文件)
        redis_url: Redis URL (Direct 模式，留空使用环境变量或配置默认值)
        gateway_host: Gateway 主机 (Gateway 模式)
        gateway_port: Gateway 端口 (Gateway 模式)
        worker_id: 手动指定 Worker ID（Direct 模式）
        worker_key: 安装 Key（Gateway 首次注册）

    Requirements: 7.1, 7.2
    """
    import asyncio
    import signal
    import sys

    from loguru import logger

    from antcode_worker.app.main import run_worker

    if transport_mode:
        os.environ["WORKER_TRANSPORT_MODE"] = transport_mode
    if worker_id:
        os.environ["WORKER_ID"] = worker_id
    if worker_key:
        os.environ["WORKER_KEY"] = worker_key
    # 配置日志
    logger.remove()
    logger.add(
        sys.stderr,
        level=log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    )

    # 初始化 Worker 配置
    grace_period = 30.0
    config_kwargs: dict[str, object] = {
        "host": host,
        "gateway_host": gateway_host,
        "gateway_port": gateway_port,
    }
    if redis_url and transport_mode != "gateway":
        config_kwargs["redis_url"] = redis_url
    if transport_mode is not None:
        config_kwargs["transport_mode"] = transport_mode

    worker_config = init_worker_config(
        name=name,
        port=port,
        **config_kwargs,
    )
    worker_config.ensure_directories()

    # 使用自定义事件循环运行，避免 asyncio.run() 覆盖信号处理
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    cancel_timeout = min(5.0, grace_period)

    try:
        loop.run_until_complete(run_worker(worker_config))
    except KeyboardInterrupt:
        logger.info("收到 KeyboardInterrupt，开始清理")
    finally:
        # 清理
        try:
            # 取消所有待处理任务
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                try:
                    loop.run_until_complete(
                        asyncio.wait_for(
                            asyncio.gather(*pending, return_exceptions=True),
                            timeout=cancel_timeout,
                        )
                    )
                except TimeoutError:
                    logger.warning("取消任务超时，仍有 {} 个任务未完成", len(pending))
            loop.run_until_complete(loop.shutdown_asyncgens())
            if hasattr(loop, "shutdown_default_executor"):
                try:
                    loop.run_until_complete(
                        asyncio.wait_for(
                            loop.shutdown_default_executor(),
                            timeout=cancel_timeout,
                        )
                    )
                except TimeoutError:
                    logger.warning("关闭默认执行器超时，可能仍有后台线程")
        finally:
            loop.close()


def prompt_start_worker(transport_mode: str) -> None:
    """交互式配置并启动 Worker（Direct/Gateway）"""
    if transport_mode not in ("direct", "gateway"):
        raise ValueError("transport_mode 必须是 direct 或 gateway")

    title = "Direct" if transport_mode == "direct" else "Gateway"
    _log_block(f"  {title} Worker 配置\n" "  --------------")

    default_name = _generate_default_worker_name()
    name = input(f"  Worker 名称 [{default_name}]: ").strip() or default_name

    if transport_mode == "direct":
        redis_url = _get_default_redis_url()
        redis_url = input(f"  Redis URL [{redis_url}]: ").strip() or redis_url
        gateway_host, gateway_port = "localhost", 50051
        worker_key = None
    else:
        default_endpoint = _get_default_gateway_endpoint()
        endpoint = input(f"  Gateway 地址 [{default_endpoint}]: ").strip() or default_endpoint
        gateway_host, gateway_port = _parse_gateway_endpoint(endpoint)
        from antcode_worker.services.credential import (
            get_credential_store,
            init_credential_service,
        )
        store = os.getenv("WORKER_CREDENTIAL_STORE") or os.getenv("ANTCODE_WORKER_CREDENTIAL_STORE") or "file"
        if WORKER_CONFIG_FILE.exists():
            try:
                with open(WORKER_CONFIG_FILE, encoding="utf-8") as f:
                    file_config = yaml.safe_load(f) or {}
                store = file_config.get("credential_store", store)
            except Exception:
                pass
        credential_service = init_credential_service(get_credential_store(store))
        credentials = credential_service.load()
        if credentials and credentials.is_valid():
            logger.info(
                "检测到已保存凭证: worker_id={}, gateway={}:{}",
                credentials.worker_id,
                credentials.gateway_host,
                credentials.gateway_port,
            )
        worker_key = input("  安装 Key (首次注册必填，留空使用已保存凭证): ").strip() or None
        redis_url = None

    while True:
        port_str = input("  健康检查端口 [8001]: ").strip()
        if not port_str:
            port = 8001
            break
        try:
            port = int(port_str)
            if 1 <= port <= 65535:
                break
            logger.warning("端口范围应为 1-65535")
        except ValueError:
            logger.warning("请输入有效的端口号")

    logger.info("配置: name={} port={} mode={}", name, port, transport_mode)

    confirm = input("  确认启动? (Y/n): ").strip().lower()
    if confirm != "n":
        start_worker(
            name=name,
            port=port,
            transport_mode=transport_mode,
            redis_url=redis_url,
            gateway_host=gateway_host,
            gateway_port=gateway_port,
            worker_key=worker_key,
        )
    else:
        logger.info("已取消")
        input("\n按 Enter 键返回...")


def interactive_menu():
    """交互式菜单"""
    while True:
        clear_screen()
        print_banner()
        print_main_menu()

        choice = input("请选择 [0-3]: ").strip()

        if choice == "1":
            logger.info("正在使用 Direct 模式启动 Worker...")
            prompt_start_worker("direct")
            break
        elif choice == "2":
            logger.info("正在使用 Gateway 模式启动 Worker...")
            prompt_start_worker("gateway")
            break
        elif choice == "3":
            logger.info("正在使用配置文件启动 Worker...")
            start_worker()
            break
        elif choice == "0":
            logger.info("再见")
            break
        else:
            logger.warning("无效选择")
            time.sleep(1)


def main():
    """主入口"""
    parser = argparse.ArgumentParser(
        description="AntCode Worker v0.1.0 (Dual Transport Mode)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用方式:
  交互式菜单: python -m antcode_worker
  Gateway 模式: python -m antcode_worker run --name "Node-001" --transport gateway
  Direct 模式:  python -m antcode_worker run --name "Node-001" --transport direct
  环境诊断:     python -m antcode_worker doctor
  查看配置:     python -m antcode_worker print-config

传输模式说明:
  - Direct 模式: 内网 Worker 直连 Redis Streams，低延迟
  - Gateway 模式: 公网 Worker 通过 Gateway gRPC/TLS 连接，安全

优雅关闭:
  收到 SIGTERM 或 SIGINT 信号时，Worker 会:
  1. 停止接收新任务
  2. 等待运行中任务完成（最长等待 30 秒，内部默认）
  3. 上报未执行任务
  4. 发送离线心跳后退出

健康检查端点:
  GET /health       - 基本状态
  GET /health/live  - 存活探针 (K8s liveness)
  GET /health/ready - 就绪探针 (K8s readiness)
        """,
    )

    # 子命令
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # run 命令
    run_parser = subparsers.add_parser("run", help="启动 Worker")
    run_parser.add_argument("--name", default="Worker-001", help="Worker 名称")
    run_parser.add_argument("--port", type=int, default=8001, help="健康检查端口")
    run_parser.add_argument("--host", default="0.0.0.0", help="绑定地址")
    run_parser.add_argument(
        "--transport",
        default=None,
        choices=["direct", "gateway"],
        help="传输模式: direct (Redis) 或 gateway (gRPC)，不传则使用配置文件",
    )
    run_parser.add_argument(
        "--redis-url",
        default=None,
        help="Redis URL (Direct 模式)",
    )
    run_parser.add_argument(
        "--gateway-endpoint",
        default=None,
        help="Gateway 地址 (Gateway 模式, 例: host:port)",
    )
    run_parser.add_argument(
        "--worker-id",
        default=None,
        help="手动指定 Worker ID (Direct 模式)",
    )
    run_parser.add_argument(
        "--worker-key",
        default=None,
        help="安装 Key (Gateway 首次注册)",
    )
    run_parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="日志级别，默认 INFO",
    )

    # doctor 命令
    subparsers.add_parser("doctor", help="运行环境诊断")

    # print-config 命令
    config_parser = subparsers.add_parser("print-config", help="打印当前配置")
    config_parser.add_argument(
        "--format",
        default="yaml",
        choices=["yaml", "json"],
        help="输出格式 (yaml/json)",
    )

    args = parser.parse_args()

    # 处理子命令
    if args.command == "doctor":
        exit_code = run_doctor()
        sys.exit(exit_code)

    if args.command == "print-config":
        print_config(config_format=args.format)
        return

    if args.command == "run":
        gateway_host = None
        gateway_port = None
        if args.gateway_endpoint:
            gateway_host, gateway_port = _parse_gateway_endpoint(args.gateway_endpoint)
        start_worker(
            name=args.name,
            port=args.port,
            host=args.host,
            log_level=args.log_level,
            transport_mode=args.transport,
            redis_url=args.redis_url,
            gateway_host=gateway_host or "localhost",
            gateway_port=gateway_port or 50051,
            worker_id=args.worker_id,
            worker_key=args.worker_key,
        )
        return

    interactive_menu()


if __name__ == "__main__":
    main()
