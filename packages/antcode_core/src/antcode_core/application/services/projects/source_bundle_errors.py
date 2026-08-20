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


class SourceBundleRejected(ValueError):
    """带结构化码的 source bundle 生产失败。``str()`` 渲染成 ``码: 原文``。"""

    def __init__(self, error_code: str, detail: str) -> None:
        self.error_code = error_code
        self.detail = detail
        super().__init__(f"{error_code}: {detail}")


__all__ = ["SOURCE_BUNDLE_SYMLINK_REJECTED", "SourceBundleRejected"]
