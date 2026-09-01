""" "材料变没变"这个判据本身是否成立。

轮换能不能生效钉在 ``test_tls_material_rotation``；这里钉的是它上游的那一步——
fetcher 凭什么断定"没变、不用换"。判错的两个方向后果完全不对称：

- 误判成"变了"：多读一次盘、多一条 WARNING，纯浪费；
- 误判成"没变"：被吊销的 CA 继续被信任，**而且不会有任何日志**。

所以这个文件里每条用例都要说清自己钉的是哪个方向。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from antcode_gateway.tls_material import (
    TLS_MATERIAL_INVALID_PEM,
    TLS_MATERIAL_RELOADED,
    TlsMaterialLoader,
    TlsMaterialPaths,
)
from loguru import logger

from tests.unit.gateway.tls_material_support import (
    Authorities,
    build_authorities,
    rewrite_in_place_keeping_stat,
    write_material,
)

#: 材料不变时的重复回调次数；每多读一轮盘就是每次握手多三次 IO。
REPEATED_HANDSHAKES = 5

#: 显式推进 mtime 的步长。取 1 秒是为了远大于任何文件系统的时间戳粒度
#: （ext4 实测约 4ms），确保"mtime 确实变了"这个前置条件不依赖运行时序。
ONE_SECOND_NS = 1_000_000_000


@pytest.fixture(scope="module")
def authorities() -> Authorities:
    return build_authorities()


def _half_written(blob: bytes) -> bytes:
    """砍掉后半段：BEGIN 头留着、END 没了——运维中断的写留下的就是这个形态。"""
    return blob[: len(blob) // 2]


def _loaded(tmp_path: Path, authorities: Authorities) -> tuple[TlsMaterialLoader, TlsMaterialPaths]:
    paths = write_material(tmp_path, authorities.current, authorities.current.ca)
    loader = TlsMaterialLoader(paths)
    loader.load()
    return loader, paths


def test_unchanged_material_is_reported_as_no_change(
    tmp_path: Path,
    authorities: Authorities,
) -> None:
    """内容没变时必须返回 None，否则每次握手都白换一次材料。"""
    loader, _ = _loaded(tmp_path, authorities)

    assert loader.certificate_configuration() is None


def test_stat_identical_rewrite_is_still_detected_as_a_change(
    tmp_path: Path,
    authorities: Authorities,
) -> None:
    """``(st_ino, st_mtime_ns, st_size)`` 完全相同、内容不同 —— 必须判成"变了"。

    这是漏检方向的核心用例。实机上有两条路径会落进这个形态（ext4 与 overlayfs
    实测一致）：写方保留时间戳（``cp -p`` / ``rsync -t`` / ``tar -x``），以及同一个
    粗粒度时间戳 tick（实测 4ms）内改写两次。后者连 ctime 都不变，所以"把
    ``st_ctime_ns`` 也加进三元组"并不能替代内容哈希。

    判据落在 stat 三元组确实没变上：否则这条用例可能是靠 mtime 变化过的，
    换回 stat 实现也不会红，就不再是证伪项。
    """
    loader, paths = _loaded(tmp_path, authorities)
    before = paths.client_ca.stat()

    rewrite_in_place_keeping_stat(paths.client_ca, authorities.replacement.ca)

    after = paths.client_ca.stat()
    assert (before.st_ino, before.st_mtime_ns, before.st_size) == (
        after.st_ino,
        after.st_mtime_ns,
        after.st_size,
    )
    assert loader.certificate_configuration() is not None


def test_touching_material_without_changing_content_is_not_a_reload(
    tmp_path: Path,
    authorities: Authorities,
) -> None:
    """内容一个字节没动、只有 mtime 被推进 —— 不许当成一次轮换。

    误判方向。stat 三元组把 ``touch`` 一律读成"变了"，于是每次 ``touch`` 都换一次
    材料并落一条 WARNING；而 ``release_e2e_tls_probe.reload_count`` 正是靠数这条
    日志判断指纹短路有没有生效，假阳性会直接污染容器级门禁的读数。
    """
    loader, paths = _loaded(tmp_path, authorities)
    unchanged = paths.client_ca.read_bytes()
    stat = paths.client_ca.stat()

    # 用 os.utime 显式推进 mtime，不指望 write_bytes 自己推进：ext4 的时间戳粒度实测
    # 约 4ms，紧跟着 stat() 的写入大概率落在同一个 tick 里、st_mtime_ns 原样不变，
    # 于是下面那条前置断言会随机失败（实测 12 跑 10 挂）。要造的场景是"内容没动、
    # 只有 mtime 变了"，那就把 mtime 直接设成一个确定不同的值。
    paths.client_ca.write_bytes(unchanged)
    os.utime(paths.client_ca, ns=(stat.st_atime_ns, stat.st_mtime_ns + ONE_SECOND_NS))

    assert paths.client_ca.stat().st_mtime_ns != stat.st_mtime_ns
    assert loader.certificate_configuration() is None


def test_truncated_replacement_is_not_applied_and_reports_a_structured_code(
    tmp_path: Path,
    authorities: Authorities,
) -> None:
    """运维半写入的 CA 文件不得顶掉当前材料，且必须留下结构化失败码。

    断言的是错误码常量而不是中文描述——描述会漂移，码不会。写进去的是**真实**的
    半写入形态（BEGIN 头完整、body 截断、没有 END），不是截在 BEGIN 标记内部那种
    连标记判据都能拦住的构造；校验强度本身钉在 ``test_tls_material_validation``。
    """
    loader, paths = _loaded(tmp_path, authorities)

    records: list[str] = []
    sink = logger.add(records.append, level="ERROR")
    try:
        paths.client_ca.write_bytes(_half_written(authorities.replacement.ca))
        assert loader.certificate_configuration() is None
    finally:
        logger.remove(sink)

    assert any(TLS_MATERIAL_INVALID_PEM in record for record in records)


def test_persistently_broken_material_keeps_reporting_on_every_callback(
    tmp_path: Path,
    authorities: Authorities,
) -> None:
    """坏材料一直坏着，就必须每次回调都继续报——不许喊过一次就沉默。

    指纹只在校验通过后才更新。若在读盘之后立刻记下，坏材料的哈希就进了指纹，
    下一次回调会判成"没变"直接返回 None：Gateway 从此带着**旧**材料静默运行，
    而运维只在日志里见过一条 ERROR，误以为是瞬时抖动。
    """
    loader, paths = _loaded(tmp_path, authorities)

    records: list[str] = []
    sink = logger.add(records.append, level="ERROR")
    try:
        paths.client_ca.write_bytes(_half_written(authorities.replacement.ca))
        for _ in range(REPEATED_HANDSHAKES):
            assert loader.certificate_configuration() is None
    finally:
        logger.remove(sink)

    reported = [record for record in records if TLS_MATERIAL_INVALID_PEM in record]
    assert len(reported) == REPEATED_HANDSHAKES


def test_repeated_callbacks_do_not_reinstall_unchanged_material(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    authorities: Authorities,
) -> None:
    """材料没变时反复回调：每份材料每次**至多读一遍**，且一次都不重新装。

    变更检测按内容哈希判，读盘是判据的一部分，省不掉（省掉就是上面那条漏检用例
    的代价）。所以这里守的不再是"零读盘"，而是两件仍然守得住的事：读盘次数与回调
    次数成正比而不是成倍数（挡住"读一遍算哈希、再读一遍建材料"这种实现），以及
    没变就绝不重新构造凭证。
    """
    paths = write_material(tmp_path, authorities.current, authorities.current.ca)
    reads: list[str] = []
    read_bytes = Path.read_bytes

    def _counting(self: Path) -> bytes:
        reads.append(self.name)
        return read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", _counting)
    loader = TlsMaterialLoader(paths)
    loader.load()
    per_load = len(reads)
    assert per_load == len(paths.all_paths)

    for _ in range(REPEATED_HANDSHAKES):
        assert loader.certificate_configuration() is None
    assert len(reads) == per_load * (1 + REPEATED_HANDSHAKES)


def test_successful_reload_reports_a_structured_code(
    tmp_path: Path,
    authorities: Authorities,
) -> None:
    """换料成功也要留码：容器级校验靠数这条日志判断指纹短路有没有失效。"""
    loader, paths = _loaded(tmp_path, authorities)

    records: list[str] = []
    sink = logger.add(records.append, level="WARNING")
    try:
        paths.client_ca.write_bytes(authorities.replacement.ca)
        assert loader.certificate_configuration() is not None
    finally:
        logger.remove(sink)

    assert any(TLS_MATERIAL_RELOADED in record for record in records)
