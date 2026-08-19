"""Worker 镜像必须自带产品对外承诺的语言运行时。

前端项目表单提供 Python/JavaScript/TypeScript/Java/Go 五种执行语言
（``web/antcode-frontend/src/components/projects/CodeProjectForm.tsx``、
``FileProjectFields.tsx``），CodePlugin 按 entry_point 后缀路由到 node / java / go。
这些二进制只能在构建期落进镜像：任务沙箱 ``--unshare-net``，目标部署又在内网，
运行期没有任何下载通道，"跑的时候再装"必然失败。

安装根不能落在挂载点下这一条，由 ``test_docker_compose_prod_topology_contract`` 里的
镜像内置工具链契约负责。
"""

import re
from pathlib import Path

DOCKERFILE = Path("infra/docker/Dockerfile.worker")
# 语言标识 → (Dockerfile ARG 名, 版本必须匹配的形状)
REQUIRED_RUNTIMES = {
    "node": ("DEFAULT_RUNTIME_NODE", re.compile(r"^\d+\.\d+\.\d+$")),
    "go": ("DEFAULT_RUNTIME_GO", re.compile(r"^\d+\.\d+\.\d+$")),
    "java": ("DEFAULT_RUNTIME_JAVA", re.compile(r"^temurin(-jre)?-\d+\.\d+\.\d+\+\S+$")),
}
ARG_PATTERN = re.compile(r"^ARG\s+(\w+)=(\S+)\s*$", re.MULTILINE)


def _dockerfile_text() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def _dockerfile_args() -> dict[str, str]:
    return dict(ARG_PATTERN.findall(_dockerfile_text()))


def test_language_runtime_versions_are_pinned_to_exact_patch() -> None:
    args = _dockerfile_args()

    for language, (arg_name, shape) in REQUIRED_RUNTIMES.items():
        version = args.get(arg_name)
        assert version is not None, f"{language} 运行时版本未在 Dockerfile 声明（缺 ARG {arg_name}）"
        assert shape.match(version), f"{language} 运行时版本 {version!r} 不是确定补丁版本，同一份 Dockerfile 会构出不同镜像"


def test_image_installs_every_promised_language_runtime() -> None:
    text = _dockerfile_text()
    install_index = text.find("mise install")

    assert install_index >= 0, "Worker 镜像没有任何 mise install，Node/Go/Java 任务在沙箱层就会因找不到可执行文件而失败"
    install_block = text[install_index : text.find("\nENV", install_index)]
    for language, (arg_name, _) in REQUIRED_RUNTIMES.items():
        assert f"{language}@${{{arg_name}}}" in install_block, f"mise install 未安装 {language} 运行时"


def test_path_points_at_pinned_install_dirs_not_mise_shims() -> None:
    """PATH 必须引用 ARG 而不是硬编码版本，否则升版本会和 PATH 悄悄脱节。"""
    path_lines = [line for line in _dockerfile_text().splitlines() if line.startswith("ENV PATH=")]
    combined = "\n".join(path_lines)

    for language, (arg_name, _) in REQUIRED_RUNTIMES.items():
        expected = f"installs/{language}/${{{arg_name}}}/bin"
        assert expected in combined, f"{language} 运行时的 bin 目录未进 PATH：{expected}"
    assert "shims" not in combined, (
        "PATH 指向 mise shims 会让 executor/sandbox_executables.py 无法反推安装根"
        "（shims 是指向 mise 自身的软链，不满足 <root>/bin/<exe> 布局），任务将被拒绝执行"
    )
