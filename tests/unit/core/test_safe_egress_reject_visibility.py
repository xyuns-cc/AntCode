"""SSRF 拒绝理由必须可见，但不能回显解析结果。

走查实测：三种拒绝原因全部塌缩成"退出码: 1"，因为 Scrapy 对 ``IgnoreRequest``
刻意不记日志、中间件自己也不打，理由从生成到丢弃从未离开 Worker 进程。
这里用 caplog 抓真实日志记录，同时守住反面：日志里绝不能出现解析到的地址。
"""

from __future__ import annotations

import logging
import socket
from types import SimpleNamespace

import pytest
from antcode_scrapy.safe_egress import SafeEgressProxyMiddleware
from scrapy.exceptions import IgnoreRequest

PROXY_URL = "http://127.0.0.1:32001"
INTERNAL_ANSWER = "10.11.12.13"


def _resolve_to(address: str):
    def fake_getaddrinfo(*_args, **_kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))]

    return fake_getaddrinfo


def _reject(url: str) -> None:
    with pytest.raises(IgnoreRequest):
        SafeEgressProxyMiddleware(PROXY_URL).process_request(SimpleNamespace(url=url, meta={}), None)


@pytest.mark.parametrize(
    "case",
    [
        ("http://10.0.0.5/", "目标主机是回环 / 私网 / 保留地址"),
        ("http://169.254.169.254/", "目标主机是云元数据端点"),
    ],
)
def test_literal_targets_report_distinguishable_reasons(monkeypatch, caplog, case) -> None:
    url, expected_reason = case
    monkeypatch.setattr(socket, "getaddrinfo", _resolve_to(INTERNAL_ANSWER))

    with caplog.at_level(logging.ERROR, logger="antcode_scrapy.safe_egress"):
        _reject(url)

    assert expected_reason in caplog.text
    # 光有理由不够，还要告诉用户怎么办
    assert "只允许访问公网 HTTP(S) 目标" in caplog.text


def test_resolved_restriction_is_reported_without_leaking_the_answer(monkeypatch, caplog) -> None:
    """公网域名解析到内网地址：说清类别，但绝不回显那个地址。

    回显等于把规则项目变成任何登录用户都能用的内网地址预言机。
    """
    monkeypatch.setattr(socket, "getaddrinfo", _resolve_to(INTERNAL_ANSWER))

    with caplog.at_level(logging.ERROR, logger="antcode_scrapy.safe_egress"):
        _reject("https://intranet.example.com/admin")

    assert "目标主机名解析到受限地址" in caplog.text
    assert INTERNAL_ANSWER not in caplog.text


def test_rejection_log_drops_credentials_and_query(monkeypatch, caplog) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", _resolve_to(INTERNAL_ANSWER))

    with caplog.at_level(logging.ERROR, logger="antcode_scrapy.safe_egress"):
        _reject("https://bob:hunter2@10.0.0.5/x?token=s3cr3t")

    assert "hunter2" not in caplog.text
    assert "s3cr3t" not in caplog.text


def test_reason_reaches_stderr_so_it_lands_in_task_logs(monkeypatch, capsys) -> None:
    """日志必须真的写到 stderr —— task_logs 采的就是 stderr。"""
    monkeypatch.setattr(socket, "getaddrinfo", _resolve_to(INTERNAL_ANSWER))
    handler = logging.StreamHandler()
    logger = logging.getLogger("antcode_scrapy.safe_egress")
    logger.addHandler(handler)
    try:
        _reject("http://10.0.0.5/")
    finally:
        logger.removeHandler(handler)

    assert "目标主机是回环 / 私网 / 保留地址" in capsys.readouterr().err
