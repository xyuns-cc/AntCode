"""CA bundle policy for Worker -> control-plane HTTP calls.

httpx 的 ``trust_env`` 同时管两件互不相干的事：代理环境变量，以及
``SSL_CERT_FILE`` 提供的 CA bundle。Worker 对回环/单标签主机关掉 ``trust_env``
是为了不让 ``HTTPS_PROXY`` 劫持控制面调用，副作用却是**私有 CA 被一并静默忽略**：
``SSL_CERT_FILE`` 明明设了却完全不生效，握手以
``CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`` 结束，
错误信息里没有任何线索指向代理策略。

这不是假想场景——本仓自己的生产画像就是这个组合：
``docker-compose.prod.ci-worker.yml`` 正是用 ``SSL_CERT_FILE`` 把发布 PKI 的 CA
注入 Worker。Docker 画像碰巧躲过了，因为它的 API 地址是带点的主机名；物理机
Worker 只要把控制面写成 ``https://localhost`` 或单标签内网名（``https://antcode-api``）
就会 100% 注册失败（真机实测）。

httpx 也不读操作系统信任库（默认用 certifi），所以 ``update-ca-certificates``
同样救不了——``SSL_CERT_FILE`` 是唯一入口，必须与代理策略解耦。
"""

from __future__ import annotations

import os
import ssl

CA_BUNDLE_ENV = "SSL_CERT_FILE"


def certificate_authority() -> ssl.SSLContext | bool:
    """返回 httpx ``verify`` 参数：显式 CA bundle，或默认信任库。

    ``SSL_CERT_FILE`` 指向不存在/不可解析的文件时直接抛错，不退回默认信任库——
    静默降级会把"私有 CA 没生效"伪装成"证书链有问题"。
    """
    ca_file = os.environ.get(CA_BUNDLE_ENV, "").strip()
    if not ca_file:
        return True
    return ssl.create_default_context(cafile=ca_file)
