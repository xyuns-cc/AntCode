"""起发布 E2E 的 Git HTTP 源，并证明该端口上应答的就是本次起的那个进程。

run-gateway-e2e.sh 的 [3/7] 原先是 `nohup … &` 加一句 `curl` 探活，两个判据都不成立：
`nohup` 的退出码无人读，而 `curl` 只证明"这个端口上有人应答"。测试机上已复现完整
链路——上一轮遗留的 git_http_server 占着端口时，新进程以 EADDRINUSE 立刻死掉，探活
却对着孤儿一次就成功（连"连接被拒"都没等到），于是后续步骤对着别人的仓库树跑还报
"全过"。ANTCODE_GATEWAY_E2E_KEEP=1 是按设计把 Git 源留活的，孤儿因此是常态而非意外。

所以判据换成三条彼此独立、都能证伪的：
  * 起之前用一次**不带 SO_REUSEADDR** 的独占绑定证明端口是空的，占着就报出占用者；
  * 子进程由本进程 Popen 持有，用 poll() 读它的真实退出码，而不是猜它还活着；
  * 探活比对本轮随机 identity——根路径回的是启动方给的串，冒名者对不上。
"""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
# Git 源必须同时被宿主上的 pytest 和 Worker 容器连到，只有通配地址两侧都成立；
# 端口不在这里另定一份，从 base_url 里取——那是 tests/e2e 真正会去连的地址。
BIND_HOST = "0.0.0.0"
IDENTITY_BYTES = 16
READY_TIMEOUT_SECONDS = 30.0
PROBE_INTERVAL_SECONDS = 0.5
PROBE_TIMEOUT_SECONDS = 5.0
LOG_TAIL_LINES = 40


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--pid-file", type=Path, required=True)
    return parser.parse_args()


def service_port(base_url: str) -> int:
    """从 tests/e2e 实际使用的地址里取端口，避免"检查的端口"和"被测的端口"分叉。"""
    port = urlsplit(base_url).port
    if port is None:
        raise ValueError(f"E2E Git 源地址必须显式带端口: {base_url}")
    return port


def _read_root_path(base_url: str) -> str:
    """读根路径的自述。连不上/超时返回空串——这是轮询中的正常中间态，
    真正的失败由子进程退出码和总超时负责报，不在这里吞。"""
    try:
        with urlopen(f"{base_url.rstrip('/')}/", timeout=PROBE_TIMEOUT_SECONDS) as response:
            return str(response.read().decode("utf-8", errors="replace")).strip()
    except (URLError, OSError, TimeoutError):
        return ""


def _responder_identity(base_url: str) -> str:
    body = _read_root_path(base_url)
    if not body:
        return ""
    try:
        return str(json.loads(body).get("identity", ""))
    except json.JSONDecodeError:
        # 应答方不是本仓库的 Git 源；对调用方而言等同于"还没等到自己"。
        return ""


def describe_port_occupant(port: int) -> str:
    """尽力而为地说出占用者是谁：非 root 的 `ss` 看不到别人进程的 PID，所以再补
    一次根路径探活——占用者若是本仓库的 Git 源，它会自报 identity / root / pid。"""
    details: list[str] = []
    listing = shutil.which("ss")
    if listing is not None:
        result = subprocess.run([listing, "-lntp"], capture_output=True, text=True, check=False)
        details.extend(line.strip() for line in result.stdout.splitlines() if f":{port}" in line)
    answer = _read_root_path(f"http://127.0.0.1:{port}")
    if answer:
        details.append(f"根路径应答={answer}")
    return "; ".join(details) or "无法识别（ss 无匹配且根路径无应答）"


def require_free_port(port: int) -> None:
    """独占绑一次证明端口是空的，占着就当场失败并报出占用者。

    刻意不设 SO_REUSEADDR：ThreadingHTTPServer 自己会设，而带 SO_REUSEADDR 的通配
    绑定可以骑在别人已有的 127.0.0.1 绑定之上并"成功"，那样子进程活着、探活却打给
    另一个进程。这里比子进程自己的绑定更严，才能把这种重叠也挡在启动之前。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((BIND_HOST, port))
        except OSError as exc:
            raise RuntimeError(f"端口 {port} 已被占用，拒绝起 E2E Git 源: {describe_port_occupant(port)}") from exc


def spawn(root: Path, port: int, *, identity: str, log: Path) -> subprocess.Popen[bytes]:
    """起 Git 源子进程。start_new_session：本进程探活完就退出，Git 源要活到整轮 E2E 结束。"""
    log.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        "-m",
        "tests.e2e.git_http_server",
        "--root",
        str(root),
        "--host",
        BIND_HOST,
        "--port",
        str(port),
        "--identity",
        identity,
    ]
    with log.open("wb") as handle:
        return subprocess.Popen(command, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True)


def await_own_service(process: subprocess.Popen[bytes], *, base_url: str, identity: str, log: Path) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            raise RuntimeError(f"E2E Git 源进程已退出（exit={exit_code}）；{_log_tail(log)}")
        if _responder_identity(base_url) == identity:
            return
        time.sleep(PROBE_INTERVAL_SECONDS)
    raise RuntimeError(
        f"{READY_TIMEOUT_SECONDS}s 内没等到本轮 E2E Git 源在 {base_url} 应答；"
        f"当前应答方={_read_root_path(base_url) or '无'}；{_log_tail(log)}"
    )


def _log_tail(log: Path) -> str:
    if not log.exists():
        return "无日志"
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    return "日志尾部: " + " | ".join(lines[-LOG_TAIL_LINES:])


def main() -> None:
    args = _arguments()
    identity = secrets.token_hex(IDENTITY_BYTES)
    port = service_port(args.base_url)
    require_free_port(port)
    process = spawn(args.root, port, identity=identity, log=args.log)
    try:
        await_own_service(process, base_url=args.base_url, identity=identity, log=args.log)
    except BaseException:
        # 自证失败就把自己起的进程收干净，否则它正是下一轮的"占着端口的孤儿"。
        process.kill()
        process.wait()
        raise
    # 记真正在 listen 的那个 PID（不是 uv 包装进程），teardown 的 kill 才不依赖 uv 转发信号。
    args.pid_file.write_text(f"{process.pid}\n", encoding="utf-8")


if __name__ == "__main__":
    main()
