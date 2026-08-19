"""任务语言运行时可执行文件的解析（fail-closed）。

Node/Go/Java 二进制只能由镜像构建期预装（``infra/docker/Dockerfile.worker`` 用 mise
装进镜像内的安装根并写进 PATH），运行期一律不补装：任务沙箱 ``--unshare-net``，
目标部署又在内网，没有任何出网通道能让"按需下载"成立。

因此"找不到就退回裸命令名"必须去掉。裸名字会一路走到 ``sandbox_mounts``，最终以
``任务可执行文件不存在: node`` 或 exit 127 的形式出现在用户的任务日志里——那是一个
交付事故（镜像少了一层），却被伪装成任务自身的错误，既无法归因也无法自愈。
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass


class LanguageRuntimeMissingError(RuntimeError):
    """任务需要的语言运行时不在 Worker PATH 上。"""


@dataclass(frozen=True)
class LanguageRuntime:
    """一种语言运行时的对外名称与其可执行文件名。"""

    label: str
    binary: str


NODE_RUNTIME = LanguageRuntime(label="Node.js", binary="node")
GO_RUNTIME = LanguageRuntime(label="Go", binary="go")
JAVA_RUNTIME = LanguageRuntime(label="Java", binary="java")


def require_language_executable(runtime: LanguageRuntime) -> str:
    """返回运行时可执行文件的绝对路径；缺失即抛错，不返回裸命令名。"""
    resolved = shutil.which(runtime.binary)
    if resolved is None:
        raise LanguageRuntimeMissingError(
            f"{runtime.label} 运行时缺失：Worker PATH 上找不到 {runtime.binary}。"
            f"该运行时必须由镜像构建期预装（infra/docker/Dockerfile.worker），"
            f"任务运行期不下载。"
        )
    return resolved


__all__ = [
    "GO_RUNTIME",
    "JAVA_RUNTIME",
    "NODE_RUNTIME",
    "LanguageRuntime",
    "LanguageRuntimeMissingError",
    "require_language_executable",
]
