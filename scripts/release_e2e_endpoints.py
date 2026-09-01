"""本轮发布 E2E 真正发布在宿主上的入口地址——门禁各步唯一的事实来源。

`production.env` 是 `release_e2e_environment.write_environment` 写出的 Compose 变量面，
里面的 `HTTPS_PORT` / `HTTP_REDIRECT_PORT` / `ANTCODE_GATEWAY_PUBLIC_PORT` 就是 Compose
`ports:` 实际发布的那三个端口。门禁脚本从这里读回来，而不是各自带一份 `443` / `80` /
`15051` 默认值：端口一被重映射，带默认值的判据就会打在别人的栈上，"能连通 + 头对"完全
无法区分是不是本轮起的栈（[3/7] 的 875e6dd 已经为 18081 立过同一条规矩）。

Docker 的端口发布是独占的：`[4/7]` 的 `up -d --wait` 一旦成功，这三个端口上应答的就
必然是本轮的容器——这才是 HTTP 侧（无 TLS 可 pin）判据的身份依据。HTTPS 侧另有本轮
一次性 CA 的 pin。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from scripts.release_e2e_environment import runner_origin

HTTPS_PORT_VARIABLE = "HTTPS_PORT"
HTTP_REDIRECT_PORT_VARIABLE = "HTTP_REDIRECT_PORT"
GATEWAY_PORT_VARIABLE = "ANTCODE_GATEWAY_PUBLIC_PORT"


@dataclass(frozen=True)
class ReleaseEndpoints:
    """本轮 Compose 发布出去的宿主侧入口。"""

    https_origin: str
    http_origin: str
    gateway_port: int


def compose_variables(environment: Path) -> dict[str, str]:
    """读回 `production.env`；缺行或格式不对一律抛错，不做任何补默认值。"""
    lines = environment.read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", maxsplit=1) for line in lines if line)


def release_endpoints(environment: Path) -> ReleaseEndpoints:
    variables = compose_variables(environment)
    return ReleaseEndpoints(
        https_origin=runner_origin("https", int(variables[HTTPS_PORT_VARIABLE])),
        http_origin=runner_origin("http", int(variables[HTTP_REDIRECT_PORT_VARIABLE])),
        gateway_port=int(variables[GATEWAY_PORT_VARIABLE]),
    )
