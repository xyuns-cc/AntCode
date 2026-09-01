"""换料之前的校验强度：坏材料到底拦不拦得住。

"变没变"钉在 ``test_tls_material_change_detection``，"换得动不动"钉在
``test_tls_material_rotation``；这里钉的是夹在两者中间的那一步——判定"这份材料
gRPC 用得了"。

**这里的材料形态一律照着真实事故长**。曾经的用例写的是 ``b"-----BEGIN CERT"``：
截断点落在 BEGIN 标记内部，标记本身缺失，于是连"标记在不在"这种判据都能拦住它。
真实的半写入不长这样——运维中断的写留下的是 **BEGIN 头完整、body 截断、没有 END**，
标记查得到。用比现实更极端的构造去证明防护有效，通过的理由推广不到现实。

五种能骗过标记判据、gRPC 却装不上的形态各占一条用例；每条都单独回退验证过会变红。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from antcode_gateway.tls_material import (
    TLS_MATERIAL_INVALID_PEM,
    TLS_MATERIAL_READ_FAILED,
    TLS_MATERIAL_RELOADED,
    TlsMaterialLoader,
    TlsMaterialPaths,
)
from cryptography.hazmat.primitives import serialization
from loguru import logger

from tests.unit.gateway.tls_material_support import (
    Authorities,
    build_authorities,
    write_material,
)

#: 重复回调次数：坏材料没提交指纹，就该每一次回调都重新报，而不是喊一声就沉默。
REPEATED_HANDSHAKES = 3

Mutation = Callable[[TlsMaterialPaths, Authorities], None]


@pytest.fixture(scope="module")
def authorities() -> Authorities:
    return build_authorities()


def _half(blob: bytes) -> bytes:
    """砍掉后半段：BEGIN 头留在里面，END 没了——运维中断的写就是这个形态。"""
    return blob[: len(blob) // 2]


def _truncated_certificate(paths: TlsMaterialPaths, authorities: Authorities) -> None:
    paths.certificate.write_bytes(_half(authorities.replacement.server_cert))


def _private_key_with_only_the_end_line(paths: TlsMaterialPaths, _authorities: Authorities) -> None:
    # 标记常量是 ``PRIVATE KEY-----``，END 行原样命中它；这份文件里一个字节的密钥都没有。
    paths.private_key.write_bytes(b"-----END PRIVATE KEY-----\n")


def _encrypted_private_key(paths: TlsMaterialPaths, authorities: Authorities) -> None:
    """带口令的私钥。标记是 ``BEGIN ENCRYPTED PRIVATE KEY``，同样命中标记常量。

    gRPC 没有输入口令的入口，这份材料它解不开，装上去等于没装。
    """
    key = serialization.load_pem_private_key(authorities.replacement.server_key, password=None)
    paths.private_key.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.BestAvailableEncryption(b"rotation-passphrase"),
        )
    )


def _new_key_against_the_old_certificate(paths: TlsMaterialPaths, authorities: Authorities) -> None:
    """三份材料顺序读，轮换途中必然存在这个窗口：私钥已经是新的，证书还是旧的。

    两份都是完好的 PEM，各自单独看毫无问题，只有配对检查看得出它们不是一对。
    """
    paths.private_key.write_bytes(authorities.replacement.server_key)


def _ca_bundle_with_a_half_written_trailing_certificate(paths: TlsMaterialPaths, authorities: Authorities) -> None:
    """bundle 的第一张完好、追加到一半断掉。

    ``x509.load_pem_x509_certificates`` 会静默丢掉尾部那个残块并返回 1 条，所以
    "解析出至少一条"判不出这个形态——少掉的那张 CA 名下的 Worker 会集体被拒。
    """
    paths.client_ca.write_bytes(authorities.current.ca + _half(authorities.replacement.ca))


BROKEN_MATERIALS: tuple[tuple[str, Mutation], ...] = (
    ("truncated_certificate", _truncated_certificate),
    ("private_key_with_only_the_end_line", _private_key_with_only_the_end_line),
    ("encrypted_private_key", _encrypted_private_key),
    ("new_key_against_the_old_certificate", _new_key_against_the_old_certificate),
    ("ca_bundle_with_a_half_written_trailing_certificate", _ca_bundle_with_a_half_written_trailing_certificate),
)


def _loaded(tmp_path: Path, authorities: Authorities) -> tuple[TlsMaterialLoader, TlsMaterialPaths]:
    paths = write_material(tmp_path, authorities.current, authorities.current.ca)
    loader = TlsMaterialLoader(paths)
    loader.load()
    return loader, paths


def _callback_records(loader: TlsMaterialLoader, times: int) -> list[str]:
    records: list[str] = []
    sink = logger.add(records.append, level="WARNING")
    try:
        for _ in range(times):
            assert loader.certificate_configuration() is None
    finally:
        logger.remove(sink)
    return records


@pytest.mark.parametrize(
    "mutate",
    [mutate for _, mutate in BROKEN_MATERIALS],
    ids=[name for name, _ in BROKEN_MATERIALS],
)
def test_material_that_grpc_cannot_load_is_refused_and_never_reported_as_reloaded(
    tmp_path: Path,
    authorities: Authorities,
    mutate: Mutation,
) -> None:
    """坏材料必须被拒、必须留 ``INVALID_PEM``，而且**绝不能**打成一次成功换料。

    最后一条是本用例真正的要害。按标记判时这些材料全部放行，于是指纹被提交、
    ``TLS_MATERIAL_RELOADED`` 落进日志——而 gRPC 那边拒绝建 handshaker factory、
    继续沿用旧材料。日志说的和实际发生的正好相反，并且指纹一提交，下一次回调就判
    "没变"直接返回 None，从此一条日志都不会再有。``release_e2e_tls_probe.reload_count``
    数的就是这条 WARNING，容器级门禁会把它读成一次成功轮换。
    """
    loader, paths = _loaded(tmp_path, authorities)

    mutate(paths, authorities)
    records = _callback_records(loader, REPEATED_HANDSHAKES)

    assert not any(TLS_MATERIAL_RELOADED in record for record in records)
    # 每次回调都报 = 指纹没被坏材料污染；只报一次说明它被当成"已处理"了。
    assert len([record for record in records if TLS_MATERIAL_INVALID_PEM in record]) == REPEATED_HANDSHAKES


def test_the_failure_code_reflects_what_actually_went_wrong(
    tmp_path: Path,
    authorities: Authorities,
) -> None:
    """ "文件读不出来"与"内容不合法"必须落成两个不同的码，两个方向都钉。

    回调的 except 分支原本固定拼 ``TLS_MATERIAL_READ_FAILED`` 当前缀，而校验抛的是
    ``TLS_MATERIAL_INVALID_PEM``：覆写之后两个码同时出现在同一行里，按码计数的告警
    会把"签发流程给了张坏证书"一并计进"挂载掉了"，排查方向从一开始就是错的。

    所以不合法那一侧必须断言 ``READ_FAILED`` **不出现**——只断言 ``INVALID_PEM``
    出现拦不住覆写，两个码共存时它照样成立。
    """
    loader, paths = _loaded(tmp_path, authorities)

    paths.client_ca.write_bytes(_half(authorities.replacement.ca))
    invalid = _callback_records(loader, 1)
    assert any(TLS_MATERIAL_INVALID_PEM in record for record in invalid)
    assert not any(TLS_MATERIAL_READ_FAILED in record for record in invalid)

    paths.client_ca.unlink()
    unreadable = _callback_records(loader, 1)
    assert any(TLS_MATERIAL_READ_FAILED in record for record in unreadable)
    assert not any(TLS_MATERIAL_INVALID_PEM in record for record in unreadable)


def test_a_complete_rotation_of_all_three_files_is_still_accepted(
    tmp_path: Path,
    authorities: Authorities,
) -> None:
    """反向判据：配对校验不许把正常的整套轮换也一起拒了。

    没有这条，上面那些用例可以靠"什么都拒"拿满分。
    """
    loader, paths = _loaded(tmp_path, authorities)

    paths.certificate.write_bytes(authorities.replacement.server_cert)
    paths.private_key.write_bytes(authorities.replacement.server_key)
    paths.client_ca.write_bytes(authorities.replacement.ca)

    assert loader.certificate_configuration() is not None
