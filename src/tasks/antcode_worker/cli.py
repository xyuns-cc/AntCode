"""命令行入口"""

import os
import time
import argparse

from .config import (
    get_or_create_machine_code,
    reset_machine_code,
    init_node_config,
    MACHINE_CODE_FILE,
)


def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')


def print_banner():
    """打印启动横幅"""
    code = get_or_create_machine_code()
    code_status = "已持久化" if MACHINE_CODE_FILE.exists() else "新生成"

    print(f"""
  AntCode Worker Node v2.1
  ========================
  机器码: {code} ({code_status})
    """)


def print_main_menu():
    """打印主菜单"""
    print("""
  [1] 快速启动节点 (默认配置)
  [2] 自定义启动节点
  [3] 查看机器码
  [4] 重置机器码
  [5] 使用帮助
  [0] 退出
    """)


def show_machine_code_info():
    """显示机器码信息"""
    code = get_or_create_machine_code()
    code_status = "已持久化" if MACHINE_CODE_FILE.exists() else "新生成"

    print(f"""
  机器码信息
  ----------
  机器码:   {code}
  状态:     {code_status}
  存储路径: {MACHINE_CODE_FILE}

  提示: 在主节点添加此节点时需要输入此机器码
    """)
    input("按 Enter 键返回...")


def do_reset_machine_code():
    """重置机器码"""
    print("""
  重置机器码
  ----------
  警告: 重置机器码后，需要在主节点重新绑定此节点!
    """)

    confirm = input("确定要重置机器码吗? (y/N): ").strip().lower()
    if confirm == 'y':
        old_code = get_or_create_machine_code()
        new_code = reset_machine_code()
        print(f"\n  [完成] 机器码已重置")
        print(f"         旧: {old_code}")
        print(f"         新: {new_code}")
    else:
        print("\n  已取消")

    input("\n按 Enter 键返回...")


def show_help():
    """显示帮助信息"""
    print("""
  AntCode Worker Node v2.1 - 使用帮助
  ====================================

  功能:
    - 虚拟环境管理 (创建/删除环境，安装/卸载包)
    - 项目管理 (上传文件/代码项目，编辑代码)
    - 任务执行 (运行项目，查看日志，取消任务)
    - 主节点通信 (心跳上报，状态同步，任务分发)

  架构组件:
    - Engine: 中央调度引擎
    - Scheduler: 优先级任务调度
    - Executor: 任务执行与资源监控
    - Pipeline: 结果处理管道

  命令行参数:
    --name    节点名称 (默认: Worker-Node)
    --port    监听端口 (默认: 8001)
    --region  区域标签 (默认: 默认)
    --host    绑定地址 (默认: 0.0.0.0)

  API 文档: http://localhost:8001/docs
    """)
    input("按 Enter 键返回...")


def start_node(name: str = "Worker-Node", port: int = 8001, region: str = "默认", host: str = "0.0.0.0"):
    """启动节点服务"""
    init_node_config(name=name, port=port, region=region)

    from .api import app
    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="warning")


def custom_start_node():
    """自定义启动节点"""
    print("""
  自定义节点配置
  --------------
    """)

    name = input("  节点名称 [Worker-Node]: ").strip() or "Worker-Node"

    while True:
        port_str = input("  监听端口 [8001]: ").strip()
        if not port_str:
            port = 8001
            break
        try:
            port = int(port_str)
            if 1 <= port <= 65535:
                break
            print("  [错误] 端口范围应为 1-65535")
        except ValueError:
            print("  [错误] 请输入有效的端口号")

    region = input("  区域标签 [默认]: ").strip() or "默认"

    print(f"\n  配置: {name} | 端口: {port} | 区域: {region}")

    confirm = input("  确认启动? (Y/n): ").strip().lower()
    if confirm != 'n':
        start_node(name=name, port=port, region=region)
    else:
        print("\n  已取消")
        input("\n按 Enter 键返回...")


def interactive_menu():
    """交互式菜单"""
    while True:
        clear_screen()
        print_banner()
        print_main_menu()

        choice = input("请选择 [0-5]: ").strip()

        if choice == '1':
            print("\n  正在使用默认配置启动节点...")
            start_node()
            break
        elif choice == '2':
            clear_screen()
            custom_start_node()
        elif choice == '3':
            clear_screen()
            show_machine_code_info()
        elif choice == '4':
            clear_screen()
            do_reset_machine_code()
        elif choice == '5':
            clear_screen()
            show_help()
        elif choice == '0':
            print("\n  再见\n")
            break
        else:
            print("\n  无效选择")
            time.sleep(1)


def main():
    """主入口"""
    parser = argparse.ArgumentParser(
        description="AntCode Worker Node v2.1",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用方式:
  交互式菜单: python -m antcode_worker
  命令行启动: python -m antcode_worker --name "Node-001" --port 8001
  查看机器码: python -m antcode_worker --show-machine-code
        """
    )
    parser.add_argument("--name", default=None, help="节点名称")
    parser.add_argument("--port", type=int, default=None, help="节点端口")
    parser.add_argument("--region", default=None, help="区域标签")
    parser.add_argument("--host", default="0.0.0.0", help="绑定地址")
    parser.add_argument("--reset-machine-code", action="store_true", help="重置机器码")
    parser.add_argument("--show-machine-code", action="store_true", help="显示机器码")
    parser.add_argument("--menu", "-m", action="store_true", help="显示交互式菜单")

    args = parser.parse_args()

    if args.show_machine_code:
        code = get_or_create_machine_code()
        print(f"机器码: {code}")
        return

    if args.reset_machine_code:
        old_code = get_or_create_machine_code()
        new_code = reset_machine_code()
        print(f"[完成] 机器码已重置: {old_code} -> {new_code}")
        return

    if args.menu or (args.name is None and args.port is None):
        interactive_menu()
        return

    start_node(
        name=args.name or "Worker-Node",
        port=args.port or 8001,
        region=args.region or "默认",
        host=args.host
    )


if __name__ == "__main__":
    main()
