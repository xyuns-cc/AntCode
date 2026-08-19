"""构建 context 传输阶段的目录剪枝门禁。

BuildKit 的 context 发送端 fsutil，只要在 .dockerignore 里发现**任意一条**带通配符的
反选（`!` 开头）模式，就把内部的 onlyPrefixExcludeExceptions 置否；此后它不再对任何
被排除的目录返回 filepath.SkipDir，而是递归进去逐个文件比对——因为带通配符的反选
"可能"在任意深度再包含某个文件。于是 `/data/` 明明被排除，却仍会被打开。

Rule 沙箱的 unix bridge 根目录 data/worker/egress 按设计建成 0300（可写不可读），
opendir 立刻 EACCES，构建在 "load build context" 阶段就失败：

    ERROR: error from sender: open data/worker/egress: permission denied

只在跑过 Rule 任务的机器上复现——没跑过时该目录根本不存在。五个生产镜像虽然用精确
COPY、从不引用 data/，但 context 传输发生在任何 COPY 之前，因此同样会被打断。

fsutil 判定"带通配符"的规则：剥掉结尾的 `/**` 与 `/*` 之后，若仍含 ``*[]?^\\`` 中任一
字符即算通配符反选。本文件按同一规则把这条约束钉成门禁。
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOCKERIGNORE_PATHS = (
    ROOT / ".dockerignore",
    ROOT / "infra/docker/Dockerfile.test.dockerignore",
    ROOT / "web/antcode-frontend/.dockerignore",
)
#: 与 fsutil 的 patternChars 一致（非 Windows 平台额外含反斜杠）。
FSUTIL_PATTERN_CHARS = frozenset("*[]?^\\")
#: fsutil 的 patternWithoutTrailingGlob 依次剥掉这两个结尾。
TRAILING_GLOB_SUFFIXES = ("/**", "/*")
#: 运行期产物目录：必须整棵可剪，任何反选都不许伸进去。
PRUNABLE_RUNTIME_ROOTS = frozenset({"data", "logs", "runtime_data"})


def _negation_patterns(dockerignore: Path) -> list[str]:
    """取出该 .dockerignore 里全部反选模式（已去掉前导 `!`）。"""
    lines = dockerignore.read_text(encoding="utf-8").splitlines()
    return [line.strip()[1:] for line in lines if line.strip().startswith("!")]


def _without_trailing_glob(pattern: str) -> str:
    for suffix in TRAILING_GLOB_SUFFIXES:
        if pattern.endswith(suffix):
            return pattern[: -len(suffix)]
    return pattern


def test_dockerignore_negations_stay_glob_free() -> None:
    """任何带通配符的反选都会关掉全局剪枝，把构建拖进不可读的 data/worker/egress。"""
    for dockerignore in DOCKERIGNORE_PATHS:
        for pattern in _negation_patterns(dockerignore):
            offending = FSUTIL_PATTERN_CHARS & set(_without_trailing_glob(pattern))
            assert not offending, (
                f"{dockerignore} 的反选 `!{pattern}` 含通配符 {sorted(offending)}；"
                "这会关掉 BuildKit 的目录剪枝，构建端将递归进入 /data/ 并在 0300 的 "
                "data/worker/egress 上 EACCES。请改写成不含通配符的精确路径。"
            )


def test_no_negation_reaches_into_runtime_data_directories() -> None:
    """剪枝生效的前提：没有任何反选把再包含项指向运行期产物目录内部。"""
    for dockerignore in DOCKERIGNORE_PATHS:
        for pattern in _negation_patterns(dockerignore):
            root_segment = pattern.lstrip("/").split("/", maxsplit=1)[0]
            assert root_segment not in PRUNABLE_RUNTIME_ROOTS, (
                f"{dockerignore} 的反选 `!{pattern}` 落在运行期产物目录 "
                f"{root_segment}/ 内，BuildKit 因此无法整棵剪掉它。"
            )
