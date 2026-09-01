"""发布门禁的每一步都必须打在**本轮真正发布出去**的端口上。

`[5/7]`（scripts/verify_release_transport.py）原先自带 `https://localhost` /
`http://localhost` / `15051` 三个默认值，而 run-gateway-e2e.sh 从来没传过 origin。
端口一被 `ANTCODE_GATEWAY_E2E_HTTPS_PORT` 重映射，判据就打在 :443 / :80 上；共享测试机
上那可能是别人的 AntCode 栈，而"四个安全头恰好各一份"正是本仓三层中间件都设的那组，
于是"能连通 + 头对"完全区分不出被验对象是谁——875e6dd 在 `[3/7]` 清掉的就是这个形状。

下面每条都配一正一反：默认端口下取值必须与改动前逐字一致（正常路径没被挡住），
重映射端口下必须跟着走，且不给本轮环境时脚本根本起不来（没有默认值可以退回）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest

from scripts import release_e2e_orchestrator, verify_gateway_tls_hot_reload, verify_release_transport
from scripts.release_e2e_endpoints import release_endpoints
from scripts.release_e2e_environment import (
    DEFAULT_GATEWAY_PUBLIC_PORT,
    DEFAULT_HTTP_REDIRECT_PORT,
    DEFAULT_HTTPS_PORT,
)
from scripts.release_e2e_workers import Fleet
from tests.unit.scripts.release_e2e_support import environment_file, environment_values, run_prepare

RUN_SCRIPT = Path("infra/docker/run-gateway-e2e.sh")
#: 三个都与默认端口不同、彼此也不同——取值串了立刻看得出来。
REMAPPED = {"https-port": 18443, "http-redirect-port": 18080, "gateway-port": 25051}
GATE_MODULES = (
    "scripts.release_e2e_orchestrator",
    "scripts.verify_release_transport",
    "scripts.verify_gateway_tls_hot_reload",
)
PORT_OVERRIDE_WIRING = (
    ("HTTPS_PORT", "--https-port"),
    ("HTTP_PORT", "--http-redirect-port"),
    ("GATEWAY_PORT", "--gateway-port"),
)
REDIRECT = verify_release_transport.HTTP_PERMANENT_REDIRECT_STATUS


def _statements() -> str:
    """只看可执行语句：注释里写着这段历史，不能让它自己把断言喂饱。"""
    lines = RUN_SCRIPT.read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def _transport_argv(state: Path) -> list[str]:
    return [
        "verify_release_transport",
        "--environment",
        str(environment_file(state)),
        "--ca",
        str(state / "public-ca.crt"),
        "--client-cert",
        str(state / "worker-tls/client.crt"),
        "--client-key",
        str(state / "worker-tls/client.key"),
        "--worker-id",
        "worker-0",
    ]


def test_default_ports_keep_the_portless_origins_the_runner_already_used(monkeypatch, tmp_path: Path) -> None:
    """必须成功臂：默认端口下取值与改动前逐字一致，正常路径没有被挡住。"""
    state, runner_env = run_prepare(monkeypatch, tmp_path)

    endpoints = release_endpoints(environment_file(state))

    assert endpoints.https_origin == "https://localhost"
    assert endpoints.http_origin == "http://localhost"
    assert endpoints.gateway_port == DEFAULT_GATEWAY_PUBLIC_PORT
    values = environment_values(state)
    assert values["HTTPS_PORT"] == str(DEFAULT_HTTPS_PORT)
    assert values["HTTP_REDIRECT_PORT"] == str(DEFAULT_HTTP_REDIRECT_PORT)
    # pytest 侧用的就是同一个属性，门禁与 tests/e2e 不会分叉到两个 origin 上。
    assert f"ANTCODE_E2E_WEB_API_URL={endpoints.https_origin}\n" in runner_env.read_text(encoding="utf-8")


def test_transport_gate_reads_origins_from_the_round_and_keeps_no_fallback(monkeypatch, tmp_path: Path) -> None:
    """必须失败臂：端口重映射后，判据的目标整体跟着搬走，且没有默认值可以退回。

    这三个值原样来自 ``release_endpoints``（``_arguments`` 只是把它 ``asdict`` 摊进
    Namespace），所以重映射后的取值不必再单独测一遍 ``release_endpoints``——那会是同一条
    代码路径上的同一组断言。
    """
    state, _ = run_prepare(monkeypatch, tmp_path, ports=REMAPPED)
    argv = _transport_argv(state)
    monkeypatch.setattr(sys, "argv", argv)

    arguments = verify_release_transport._arguments()

    assert arguments.https_origin == f"https://localhost:{REMAPPED['https-port']}"
    assert arguments.http_origin == f"http://localhost:{REMAPPED['http-redirect-port']}"
    assert arguments.gateway_port == REMAPPED["gateway-port"]
    # 必须失败臂：不给本轮环境就起不来，没有任何默认值能把判据退回 :443 / :80 / 15051。
    monkeypatch.setattr(sys, "argv", [item for item in argv if item != "--environment" and item != argv[2]])
    with pytest.raises(SystemExit):
        verify_release_transport._arguments()


def test_hot_reload_gate_reads_the_gateway_port_from_the_round(monkeypatch, tmp_path: Path) -> None:
    state, _ = run_prepare(monkeypatch, tmp_path, ports=REMAPPED)
    argv = [
        "verify_gateway_tls_hot_reload",
        "--environment",
        str(environment_file(state)),
        "--gateway-tls-dir",
        str(state / "gateway-tls"),
        "--ca-cert",
        str(state / "public-ca.crt"),
        "--ca-key",
        str(state / "ca.key"),
        "--client-cert",
        str(state / "worker-tls/client.crt"),
        "--client-key",
        str(state / "worker-tls/client.key"),
        "--gateway-container",
        "container",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    assert verify_gateway_tls_hot_reload._arguments().gateway_port == REMAPPED["gateway-port"]


def test_admin_client_logs_in_to_the_origin_this_round_published(monkeypatch, tmp_path: Path) -> None:
    """编排器原先写死 `PUBLIC_API_ORIGIN = "https://localhost"`（与 runner_api_origin 同名双实现）。

    端口一重映射它就会去登录别人的 :443，失败信息会是"管理员密码不对"这类完全指错方向的东西。
    """
    state, _ = run_prepare(monkeypatch, tmp_path, ports=REMAPPED)
    fleet = Fleet(environment=environment_file(state), state_dir=state, slug="local-gateway-e2e", count=1)
    captured: dict[str, str] = {}

    class _Recorder:
        def __init__(self, *, base_url: str, verify: str, timeout: float) -> None:
            captured.update(base_url=base_url, verify=verify)

    monkeypatch.setattr(release_e2e_orchestrator.httpx, "AsyncClient", _Recorder)
    release_e2e_orchestrator._admin_client(fleet)

    assert captured["base_url"] == f"https://localhost:{REMAPPED['https-port']}"
    # 服务端仍由本轮一次性 CA 校验：这才是"连上的确实是本轮的栈"的密码学依据。
    assert captured["verify"] == str(state / "public-ca.crt")


def test_redirect_criterion_accepts_the_portless_nginx_target_and_rejects_a_foreign_one() -> None:
    """nginx 的 308 目标由 `$host` 拼出，`$host` 不含端口——端口重映射不该把它判失败。

    端口的身份证明落在紧随其后那次 ready 请求上：它连的是本轮发布的端口，而且用本轮
    一次性 CA 校验服务端，别人的栈过不去。
    """
    origin = f"https://localhost:{REMAPPED['https-port']}"

    verify_release_transport._verify_redirect(
        httpx.Response(REDIRECT, headers=[("location", "https://localhost/")]), origin
    )

    with pytest.raises(RuntimeError, match="exact HTTPS origin"):
        verify_release_transport._verify_redirect(
            httpx.Response(REDIRECT, headers=[("location", "https://elsewhere.invalid/")]), origin
        )
    with pytest.raises(RuntimeError, match="exact HTTPS origin"):
        verify_release_transport._verify_redirect(httpx.Response(httpx.codes.OK), origin)


def test_run_script_threads_the_round_environment_into_every_gate_step() -> None:
    """脚本层没有可执行门禁，这里静态钉住：端口只从 production.env 来，脚本不再抄一份。"""
    statements = _statements()

    assert statements.count('--environment "$ENVIRONMENT_FILE"') == len(GATE_MODULES)
    for module in GATE_MODULES:
        assert f"python -m {module}" in statements
    assert "15051" not in statements
    assert "--https-origin" not in statements
    assert "--http-origin" not in statements
    # 端口只在调用方显式覆盖时才透传，脚本自己不带 443 / 80 / 15051 的默认值。
    for variable, option in PORT_OVERRIDE_WIRING:
        assert f'PORT_OVERRIDES+=({option} "$ANTCODE_GATEWAY_E2E_{variable}")' in statements
    assert 'PORT_OVERRIDES[@]+"${PORT_OVERRIDES[@]}"' in statements
