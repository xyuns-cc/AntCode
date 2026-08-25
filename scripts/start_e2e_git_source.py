"""起发布 E2E 的 Git HTTP 源，并证明该端口上应答的就是本次起的那个进程。

run-gateway-e2e.sh 的 [3/7] 原先是 `nohup … &` 加一句 `curl` 探活，两个判据都不成立：
`nohup` 的退出码无人读，而 `curl` 只证明"这个端口上有人应答"。测试机上已复现完整
链路——上一轮遗留的 git_http_server 占着端口时，新进程以 EADDRINUSE 立刻死掉，探活
却对着孤儿一次就成功（连"连接被拒"都没等到），于是后续步骤对着别人的仓库树跑还报
"全过"。ANTCODE_GATEWAY_E2E_KEEP=1 是按设计把 Git 源留活的，孤儿因此是常态而非意外。

所以判据换成三条彼此独立、都能证伪的：
  * 起之前先证明端口上没有别人的服务，且本轮 Git 源自己绑得上，占着就报出占用者；
  * 子进程由本进程 Popen 持有，用 poll() 读它的真实退出码，而不是猜它还活着；
  * 探活比对本轮随机 identity——根路径回的是启动方给的串，冒名者对不上。
"""

from __future__ import annotations

import argparse
import json
import secrets
import shutil
import socket
import struct
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
CONNECT_TIMEOUT_SECONDS = 2.0
LOG_TAIL_LINES = 40
# struct linger{onoff=1, linger=0}：close() 直接发 RST。占用探测借此确定性地不在被测
# 端口上留下 TIME_WAIT，而不是依赖"谁先关"这个竞态。
RESET_ON_CLOSE = struct.pack("ii", 1, 0)
# `ss -tan` 的列：State Recv-Q Send-Q Local:Port Peer:Port。只认本地地址那一列，
# 否则把"连往该端口的客户端"也算成占用者。
SS_LOCAL_ADDRESS_COLUMN = 3


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


def _socket_states(port: int) -> list[str]:
    """列出这个端口上所有状态的 TCP 套接字。

    用 `ss -tanp` 而不是 `-lntp`：只问 LISTEN 的话，占着端口却没在 listen 的套接字
    会被归到"无法识别"里；带上状态列，运维还能一眼分清"真有人在服务"和"只剩上一轮
    的 TIME-WAIT 尾巴"。
    """
    listing = shutil.which("ss")
    if listing is None:
        return []
    result = subprocess.run([listing, "-tanp"], capture_output=True, text=True, check=False)
    columns = (line.split() for line in result.stdout.splitlines()[1:])
    return [
        " ".join(fields)
        for fields in columns
        if len(fields) > SS_LOCAL_ADDRESS_COLUMN and fields[SS_LOCAL_ADDRESS_COLUMN].endswith(f":{port}")
    ]


def describe_port_occupant(port: int) -> str:
    """尽力而为地说出占用者是谁：非 root 的 `ss` 看不到别人进程的 PID，所以再补
    一次根路径探活——占用者若是本仓库的 Git 源，它会自报 identity / root / pid。

    这次探活自己会在 port 上留下一条 TIME_WAIT（HTTP 请求带 Connection: close，
    服务端先关）。以前那是条死路：守卫用无 SO_REUSEADDR 的独占绑定，于是"报占用 →
    运维杀掉占用者 → 立刻重试仍失败"要卡满一个 TIME_WAIT 周期，起点正是这句探活。
    现在两条判据都不把 TIME_WAIT 当占用，这条残留才不再有害。
    """
    details = _socket_states(port)
    answer = _read_root_path(f"http://127.0.0.1:{port}")
    if answer:
        details.append(f"根路径应答={answer}")
    if details:
        return "; ".join(details)
    return f"ss 与根路径都没认出占用者；用 `sudo lsof -nP -iTCP:{port}` 查出持有者并结束它"


def _port_has_listener(host: str, port: int) -> bool:
    """有人 accept 就是有人在这个端口上服务——875e6dd 要挡的正是这一种。

    判据取自内核的 connect 而不是 `ss` 的状态字段：开发机（macOS）上没有 `ss`，把
    决策建在它上面等于在那里悄悄把守卫关掉；`ss` 只留给给人看的占用者描述。
    探测地址取自 base_url，即 tests/e2e 真正会去连的那个地址，避免"检查的"和
    "被测的"分叉。
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(CONNECT_TIMEOUT_SECONDS)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, RESET_ON_CLOSE)
        try:
            probe.connect((host, port))
        except ConnectionRefusedError:
            return False
        except OSError as exc:
            raise RuntimeError(
                f"判定不了 {host}:{port} 有没有人在服务（connect 既没被接受也没被拒绝：{exc}）；"
                f"tests/e2e 待会儿连的就是这个地址，先查这台机器到该地址的路由与防火墙"
            ) from exc
        return True


def _require_bindable(port: int) -> None:
    """用 Git 源自己的绑定语义试一次：SO_REUSEADDR 也绑不上，说明有活着的套接字占着。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((BIND_HOST, port))
        except OSError as exc:
            raise RuntimeError(
                f"端口 {port} 已被占用：Git 源自己的绑定语义（SO_REUSEADDR）也绑不上（{exc.strerror}），"
                f"拒绝起 E2E Git 源: {describe_port_occupant(port)}"
            ) from exc


def require_free_port(base_url: str) -> None:
    """证明端口上没有别人的服务、且本轮 Git 源绑得上，占着就当场失败并报出占用者。

    两条判据都直接问内核，缺一不可：
      * 没人 accept——排掉"别人的进程正占着这个端口在服务"，那才是 875e6dd 的洞；
      * 用与 ThreadingHTTPServer 完全相同的 SO_REUSEADDR 语义绑得上——"守卫绑得上"
        才精确等价于"Git 源绑得上"。

    不再用无 SO_REUSEADDR 的独占绑定当判据：Git 源自己 allow_reuse_address=1，那样
    守卫比它守卫的东西还严。上一轮 E2E（乃至本文件的探活）在端口上留下的 TIME_WAIT
    会让独占绑定接着拒绝一个完整的 TIME_WAIT 周期——Linux 的 TCP_TIMEWAIT_LEN 写死
    60s，等不快也调不动——于是"杀掉占用者、立刻重试仍然失败"成了一条没人解释得了的
    死路。反过来只用 SO_REUSEADDR 也不行：macOS 上带 SO_REUSEADDR 的通配绑定能骑在
    别人已有的 127.0.0.1 LISTEN 之上并"成功"，守卫会就此漏掉真占用（已实测）。
    """
    port = service_port(base_url)
    host = urlsplit(base_url).hostname or BIND_HOST
    if _port_has_listener(host, port):
        raise RuntimeError(
            f"端口 {port} 已被占用：{host}:{port} 上有进程在应答，拒绝起 E2E Git 源: {describe_port_occupant(port)}"
        )
    _require_bindable(port)


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
    require_free_port(args.base_url)
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
