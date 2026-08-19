"""dockerignore 语义门禁：凭据模式必须在**任意深度**生效。

历史缺陷：本仓两个契约用例用 `pathspec.GitIgnoreSpec` 建模 `.dockerignore`。两者语义不同
——gitignore 的无斜杠模式在任意深度生效，dockerignore 锚定 context 根且 `*` 不跨越 `/`。
于是 `assert spec.match_file("nested/private.key")` 恒真、用例常绿，而真实 `docker build`
照样把 `nested/private.key` 收进 context。实测：开发机上的
`web/antcode-frontend/.claude/settings.local.json`（未被 git 跟踪）就这样进了 context，
并被 `Dockerfile.test` 的 `COPY . /app` 拷进最终镜像层。

因此本文件一律用 `dockerignore_support.DockerIgnoreSpec`（moby/patternmatcher 的移植），
并先用一张**真实 `docker build` 跑出来的判定表**把移植保真度钉死——否则"换了个匹配器"
本身就可能是下一个假绿。
"""

from pathlib import Path

from pathspec import GitIgnoreSpec

from tests.unit.core.dockerignore_support import CREDENTIAL_PROBE_PATHS, DockerIgnoreSpec

ROOT = Path(__file__).resolve().parents[3]
DOCKERIGNORE_PATHS = (
    ROOT / ".dockerignore",
    ROOT / "infra/docker/Dockerfile.test.dockerignore",
    ROOT / "web/antcode-frontend/.dockerignore",
)

#: 覆盖 patternmatcher 四种匹配类型 + 反选 + `?` + 字符组 + 根锚定的合成规则集。
FIDELITY_DOCKERIGNORE = """# comment line must be dropped
*.key
**/*.pem
secret*.json
**/.claude/
**/.ssh/
build/**
**/.env*
!keep/.env.example
docs/*/tmp*
a?c.txt
*.py[cod]
/anchored.txt
trailing/
"""

#: 判定表来自测试机上真实的 `docker buildx build --output type=local`（BuildKit 实物结果），
#: 不是人工推演。True = 该路径被排除出 context。
REAL_DOCKER_DECISIONS = (
    ("plain.txt", False),
    ("root.key", True),
    ("n/deep.key", False),  # 裸 `*.key` 只锚定根目录——这正是被修掉的那个洞
    ("root.pem", True),
    ("n/deep.pem", True),  # `**/*.pem` 才能覆盖任意深度
    ("secret-a.json", True),
    ("n/secret-a.json", False),
    (".claude/x.json", True),
    ("n/.claude/x.json", True),
    (".ssh/id", True),
    ("n/.ssh/id", True),
    ("build/out/x.js", True),
    ("n/build/out/x.js", False),  # `build/**` 是根锚定的前缀匹配
    (".env", True),
    ("n/.env", True),
    ("keep/.env.example", False),
    ("n/keep/.env.example", True),  # 反选同样锚定根，不在深层生效
    ("docs/v1/tmpfile", True),
    ("docs/v1/v2/tmpfile", False),  # 单个 `*` 不跨越 `/`
    ("abc.txt", True),
    ("axc.txt", True),
    ("aXXc.txt", False),  # `?` 恰好一个字符
    ("mod.pyc", True),
    ("mod.pyo", True),
    ("n/mod.pyc", False),
    ("anchored.txt", True),
    ("n/anchored.txt", False),  # 前导 `/` 被剥掉后仍只等于根路径
    ("trailing/x.txt", True),  # 目录被排除时其下内容一并排除
    ("n/trailing/x.txt", False),
)

#: 本地 agent / IDE 配置目录：里面常有 API key 与 MCP 凭据，且基本都不被 git 跟踪。
AGENT_DIRECTORIES = (".agents", ".claude", ".codex", ".kiro", ".serena", ".cursor", ".continue", ".windsurf")
#: 实测在真实构建 context 里抓到过的那一份，钉成具名判据防回归。
OBSERVED_LEAKED_PATH = "web/antcode-frontend/.claude/settings.local.json"


def _spec(path: Path) -> DockerIgnoreSpec:
    return DockerIgnoreSpec.from_lines(path.read_text(encoding="utf-8").splitlines())


def test_port_reproduces_real_docker_decisions() -> None:
    """移植保真度：29 条判定与测试机上真实 BuildKit 的实物结果逐条一致。"""
    spec = DockerIgnoreSpec.from_lines(FIDELITY_DOCKERIGNORE.splitlines())

    actual = tuple((path, spec.match_file(path)) for path, _ in REAL_DOCKER_DECISIONS)
    assert actual == REAL_DOCKER_DECISIONS


def test_gitignore_spec_is_not_a_valid_model_for_dockerignore() -> None:
    """钉死"为什么不能用 GitIgnoreSpec"：它在嵌套路径上与真实 Docker 判定相反。

    注意：这条**不是证伪判据**——摘掉本次任何一项修复它都不会变红。它只钉住前提本身
    （pathspec 升级后若语义变了，这里会红，提示上面的结论需要重新论证）。真正的证伪
    判据是 test_port_reproduces_real_docker_decisions 与两条 *_at_every_depth。
    """
    git_spec = GitIgnoreSpec.from_lines(FIDELITY_DOCKERIGNORE.splitlines())

    disagreements = [
        path for path, docker_excluded in REAL_DOCKER_DECISIONS if git_spec.match_file(path) != docker_excluded
    ]
    assert "n/deep.key" in disagreements, "GitIgnoreSpec 若已与 Docker 一致，本文件的前提需重新论证"


def test_credential_patterns_apply_at_every_depth() -> None:
    """三份 .dockerignore 都必须在任意深度挡住凭据，而不只是 context 根那一层。"""
    for dockerignore_path in DOCKERIGNORE_PATHS:
        spec = _spec(dockerignore_path)
        leaked = [path for path in CREDENTIAL_PROBE_PATHS if not spec.match_file(path)]
        assert not leaked, f"{dockerignore_path} 未挡住嵌套凭据：{leaked}"


def test_local_agent_configuration_excluded_at_every_depth() -> None:
    """`web/antcode-frontend/.claude/` 曾实际进入 context 与 antcode-test 镜像层。"""
    agent_paths = tuple(f"nested/{directory}/private.json" for directory in AGENT_DIRECTORIES)
    agent_paths += tuple(f"{directory}/settings.json" for directory in AGENT_DIRECTORIES)
    agent_paths += (OBSERVED_LEAKED_PATH,)

    for dockerignore_path in DOCKERIGNORE_PATHS:
        spec = _spec(dockerignore_path)
        leaked = [path for path in agent_paths if not spec.match_file(path)]
        assert not leaked, f"{dockerignore_path} 未挡住本地 agent 配置：{leaked}"
