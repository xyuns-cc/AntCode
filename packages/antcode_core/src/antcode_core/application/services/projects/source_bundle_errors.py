"""source bundle 生产侧的结构化失败码。

两条线严格分开：``error_code`` 是程序唯一可判定的契约，``detail`` 装给人看的原文
（具体仓库内路径）。**禁止**对 ``detail`` 做匹配分支——中文文案一改契约就漂。

继承 ``ValueError`` 是有意的：``resolve_bundle_paths`` 家族此前一律抛 ValueError，
上层（web_api / master 派发链路）按 ValueError 兜住并翻成 4xx，换基类会让这些
调用点静默漏接。
"""

from typing import Final

# 仓库里含符号链接。生产侧与消费侧（Worker fetcher）都不接受符号链接成员。
SOURCE_BUNDLE_SYMLINK_REJECTED: Final = "SOURCE_BUNDLE_SYMLINK_REJECTED"
# 四条容量线各自独立，报错必须点名先触到的那一条，否则用户只知道"太大了"、不知道减什么。
SOURCE_BUNDLE_FILE_COUNT_EXCEEDED: Final = "SOURCE_BUNDLE_FILE_COUNT_EXCEEDED"
SOURCE_BUNDLE_FILE_BYTES_EXCEEDED: Final = "SOURCE_BUNDLE_FILE_BYTES_EXCEEDED"
SOURCE_BUNDLE_TOTAL_BYTES_EXCEEDED: Final = "SOURCE_BUNDLE_TOTAL_BYTES_EXCEEDED"
SOURCE_BUNDLE_ARCHIVE_BYTES_EXCEEDED: Final = "SOURCE_BUNDLE_ARCHIVE_BYTES_EXCEEDED"

# 实测：把 npm 离线缓存整棵提交进仓库，约 2000 个包就会顶穿压缩包上限。
# 用户拿到裸数字无从下手，必须直接给出"减什么"。
CAPACITY_HINT: Final = (
    "超限的通常是随仓库提交的依赖产物："
    "Node 项目只提交 package-lock.json，依赖走离线缓存 .antcode-deps/npm-cache/ 并裁到本项目实际用到的包；"
    "Python 项目只提交 requirements.txt，不要提交 .venv/ 或 wheel 缓存。"
)


class SourceBundleRejected(ValueError):
    """带结构化码的 source bundle 生产失败。``str()`` 渲染成 ``码: 原文``。"""

    def __init__(self, error_code: str, detail: str) -> None:
        self.error_code = error_code
        self.detail = detail
        super().__init__(f"{error_code}: {detail}")


def _bytes_text(value: int) -> str:
    return f"{value} 字节({value / 1024 / 1024:.1f} MiB)"


def reject_file_count(actual: int, limit: int) -> SourceBundleRejected:
    return SourceBundleRejected(
        SOURCE_BUNDLE_FILE_COUNT_EXCEEDED,
        f"source bundle 文件数超过上限：实际 {actual} 个，上限 {limit} 个，超出 {actual - limit} 个。{CAPACITY_HINT}",
    )


def reject_file_bytes(name: str, actual: int, limit: int) -> SourceBundleRejected:
    return SourceBundleRejected(
        SOURCE_BUNDLE_FILE_BYTES_EXCEEDED,
        f"source bundle 单文件超过上限：{name} 实际 {_bytes_text(actual)}，"
        f"上限 {_bytes_text(limit)}，超出 {_bytes_text(actual - limit)}。{CAPACITY_HINT}",
    )


def reject_total_bytes(actual: int, limit: int) -> SourceBundleRejected:
    return SourceBundleRejected(
        SOURCE_BUNDLE_TOTAL_BYTES_EXCEEDED,
        f"source bundle 解压后总大小超过上限：实际 {_bytes_text(actual)}，"
        f"上限 {_bytes_text(limit)}，超出 {_bytes_text(actual - limit)}。{CAPACITY_HINT}",
    )


def reject_archive_bytes(actual: int, limit: int) -> SourceBundleRejected:
    return SourceBundleRejected(
        SOURCE_BUNDLE_ARCHIVE_BYTES_EXCEEDED,
        f"source bundle 压缩包超过上限：实际 {_bytes_text(actual)}，"
        f"上限 {_bytes_text(limit)}，超出 {_bytes_text(actual - limit)}。"
        f"压缩包上限按 tar.gz 后的字节数判定，已压缩过的依赖产物（npm 缓存里的 .tgz、预编译二进制）"
        f"几乎不会再被压小，解压后总大小没超也可能先顶穿这一条。{CAPACITY_HINT}",
    )


__all__ = [
    "CAPACITY_HINT",
    "SOURCE_BUNDLE_ARCHIVE_BYTES_EXCEEDED",
    "SOURCE_BUNDLE_FILE_BYTES_EXCEEDED",
    "SOURCE_BUNDLE_FILE_COUNT_EXCEEDED",
    "SOURCE_BUNDLE_SYMLINK_REJECTED",
    "SOURCE_BUNDLE_TOTAL_BYTES_EXCEEDED",
    "SourceBundleRejected",
    "reject_archive_bytes",
    "reject_file_bytes",
    "reject_file_count",
    "reject_total_bytes",
]
