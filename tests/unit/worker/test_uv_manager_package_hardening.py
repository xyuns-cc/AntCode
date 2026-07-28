"""
P0-01 回归测试：UVManager._validate_packages 必须拒绝任何会触发
sdist / PEP 517 build backend / VCS / URL / 本地路径 / shell 元字符
的依赖来源，只允许严格 PEP 508 name[extras] operator version [; markers]。

背景（GPT 2026-07-13 审查报告 P0-01）：
之前的 PACKAGE_PATTERN 允许 `pkg@https://…`、`git+https://…`、`https://…tar.gz`
等 direct-reference，导致 `uv pip install <恶意来源>` 在 Worker 主 UID / 主网络 /
主文件系统视图下运行任意构建脚本，等价 RCE。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

worker_src = Path(__file__).parent.parent.parent.parent / "services" / "worker" / "src"
if str(worker_src) not in sys.path:
    sys.path.insert(0, str(worker_src))

from antcode_worker.runtime.uv_manager import UVManager  # noqa: E402


class _Validator(UVManager):
    """只用 _validate_packages，跳过 __init__（不需要 venvs_dir）。"""

    def __init__(self) -> None:  # noqa: D401
        pass


@pytest.fixture
def validator() -> _Validator:
    return _Validator()


ALLOWED = [
    "requests",
    "requests==2.31.0",
    "urllib3>=1.26,<2",
    "pkg[extras]==1.0",
    "lxml==5.1.0",
    'pkg==1.0; python_version>="3.10"',
    "numpy>=1.24,!=1.25.0",
]

# 每条都是历史 CVE / 常见 supply-chain 攻击面
REJECTED = [
    # direct URL reference
    "pkg@https://evil/x.tar.gz",
    "pkg @ https://evil/x.tar.gz",
    "https://evil/pkg.tar.gz",
    "http://evil/pkg-1.0.tar.gz",
    "file:///tmp/backdoor",
    # VCS reference
    "git+https://evil/repo.git",
    "git+ssh://git@evil/repo",
    "hg+https://evil",
    "svn+http://evil",
    "bzr+lp:evil",
    # editable / local path
    "-e .",
    "-e ./localpkg",
    "./localpkg",
    "../etc/passwd",
    "/absolute/backdoor",
    # shell / arg injection
    "pkg`whoami`",
    "pkg$(id)",
    "pkg\n--index-url=http://evil",
    "pkg\r\n--extra-index-url=http://evil",
    "pkg\t--upgrade",
    "pkg\\evil",
    # operator prefix (uv 参数注入)
    "-runtime",
    "=runtime",
    "!runtime",
    # empty / oversize
    "",
    "a" * 300,
]


@pytest.mark.parametrize("pkg", ALLOWED)
def test_valid_pep508_allowed(validator: _Validator, pkg: str) -> None:
    validator._validate_packages([pkg])


@pytest.mark.parametrize("pkg", REJECTED)
def test_malicious_source_rejected(validator: _Validator, pkg: str) -> None:
    """任何 URL / VCS / direct reference / 本地路径 / shell 元字符必须被拒。"""
    with pytest.raises(RuntimeError, match="非法包名"):
        validator._validate_packages([pkg])


def test_mixed_batch_rejects_if_any_bad(validator: _Validator) -> None:
    """一批包名里只要有一个恶意，整批必须拒（不能部分放行）。"""
    with pytest.raises(RuntimeError, match="非法包名"):
        validator._validate_packages(["requests==2.31.0", "git+https://evil/repo.git"])


def test_pattern_docstring_examples_still_pass(validator: _Validator) -> None:
    """确保常见 PEP 508 语法（extras/markers/多约束）都能通过。"""
    validator._validate_packages(
        [
            "requests[socks]>=2.28",
            "pandas>=2,<3",
            'httpx==0.27.0; python_version>="3.11"',
        ]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "package",
    [
        "git+https://evil/repo.git",
        "pkg@https://evil/x.tar.gz",
        "file:///tmp/backdoor",
    ],
)
async def test_create_env_validates_initial_packages_before_creating_venv(
    validator: _Validator,
    package: str,
) -> None:
    with pytest.raises(RuntimeError, match="非法包名"):
        await validator.create_env(
            env_name="private-security-test",
            python_version="3.11",
            packages=[package],
        )
