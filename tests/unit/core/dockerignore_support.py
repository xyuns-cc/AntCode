"""按真实 Docker 语义判定 `.dockerignore` 的匹配器。

`.dockerignore` 与 `.gitignore` **语义不同**：gitignore 的无斜杠模式在任意深度生效，
dockerignore 的模式一律锚定 context 根，且 `*` 不跨越 `/`。用 `pathspec.GitIgnoreSpec`
建模 dockerignore，会把 `*.key` 误判成能命中 `nested/private.key`——于是"凭据已被排除"
的断言恒真，测试假绿，而真实构建照样把嵌套凭据收进 context。

本模块移植 moby/patternmatcher（BuildKit 与 docker CLI 实际使用的实现）：

- 预处理对齐 `ignorefile.ReadAll`：`#` 开头整行丢弃、trim、拆 `!`、`filepath.Clean`、
  去前导 `/`；
- 编译/匹配对齐 `Pattern.compile` 与 `Pattern.match` 的四种匹配类型；
- `match_file` 对齐 `MatchesOrParentMatches`：路径本身或任一祖先目录命中即算排除，
  反选按"最后命中者胜"覆盖。

移植保真度由 tests/unit/core/test_dockerignore_semantics.py 钉住，其判定表是在测试机
上用真实 `docker build` 跑出来的实物结果。
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum

_SEPARATOR = "/"
_STAR = "*"
_DOUBLE_STAR = "**"
_DOUBLE_STAR_WIDTH = len(_DOUBLE_STAR)
_SINGLE_CHAR_WILDCARD = "?"
_BACKSLASH = "\\"
_COMMENT_PREFIX = "#"
_NEGATION_PREFIX = "!"
#: Go 侧 shouldEscape 覆盖的字符：正则里有特殊含义、但 filepath.Match 里没有。
_ESCAPED_CHARS = frozenset(".+()|{}$")
_BRACKET_CHARS = frozenset("[]")
#: `**` 位于模式中段：允许任意层目录（含 0 层）。
_ANY_DEPTH = "(.*" + _SEPARATOR + ")?"
_ANY_SUFFIX = ".*"
_SEGMENT_WILDCARD = "[^" + _SEPARATOR + "]*"
_SEGMENT_SINGLE = "[^" + _SEPARATOR + "]"

#: 凭据类探针路径，每条代表一个凭据类别，供 .gitignore 与三份 .dockerignore 共用判据。
#: 一律放在**子目录**：根目录那一层旧模式本来就挡得住，真正会漏的是嵌套——而凭据恰恰
#: 多半是未被 git 跟踪的本地文件（`git ls-files` 干净 ≠ 构建 context 干净）。
CREDENTIAL_PROBE_PATHS: tuple[str, ...] = (
    "nested/config.env",
    "nested/.env",
    "nested/.env.staging",
    "nested/private.key",
    "nested/cert.pem",
    "nested/ca.der",
    "nested/store.pkcs12",
    "nested/vpn.ovpn",
    "nested/prod.kubeconfig",
    "nested/credentials.json",
    "nested/cloud-credentials-prod.json",
    "nested/service-account-prod.json",
    "nested/secrets.yaml",
    "nested/secrets.yml",
    "nested/secret-prod.json",
    "nested/auth.json",
    "nested/.pgpass",
    "nested/.netrc",
    "nested/.npmrc",
    "nested/.pypirc",
    "nested/.authinfo",
    "nested/.my.cnf",
    "nested/.git-credentials",
    "nested/.vault-token",
    "nested/pip.conf",
    "nested/pip.ini",
    "nested/tf.tfstate",
    "nested/tf.tfvars",
    "nested/id_ed25519",
    "nested/.ssh/id_ed25519",
    "nested/.aws/credentials",
    "nested/.azure/accessTokens.json",
    "nested/.config/gcloud/application_default_credentials.json",
    "nested/.kube/config",
    "nested/.docker/config.json",
    "services/worker/deep/nested/private.key",
    "web/antcode-frontend/src/.env.local",
)


class _MatchType(Enum):
    """patternmatcher 的四种匹配类型；只有 REGEXP 才真正走正则。"""

    EXACT = "exact"
    PREFIX = "prefix"
    SUFFIX = "suffix"
    REGEXP = "regexp"


@dataclass(frozen=True)
class _Pattern:
    cleaned: str
    exclusion: bool
    match_type: _MatchType
    regexp: re.Pattern[str] | None


def _normalize_line(line: str) -> str | None:
    """对齐 ignorefile.ReadAll。注释判定发生在 trim 之前，故缩进的 `#` 不算注释。"""
    if line.startswith(_COMMENT_PREFIX):
        return None
    pattern = line.strip()
    if not pattern:
        return None
    invert = pattern.startswith(_NEGATION_PREFIX)
    if invert:
        pattern = pattern[1:].strip()
    if pattern:
        pattern = posixpath.normpath(pattern)
        if len(pattern) > 1 and pattern.startswith(_SEPARATOR):
            pattern = pattern[1:]
    return _NEGATION_PREFIX + pattern if invert else pattern


def _translate_backslash(cleaned: str, cursor: int) -> tuple[int, str, bool]:
    """`\\` 转义其后一个字符；位于串尾时按 Go 的做法原样保留。"""
    if cursor >= len(cleaned):
        return cursor, _BACKSLASH, False
    return cursor + 1, _BACKSLASH + cleaned[cursor], True


def _translate_char(cleaned: str, cursor: int) -> tuple[int, str, bool]:
    """翻译单个非 `**` 字符，返回（新游标、正则片段、是否强制降级为正则匹配）。"""
    char = cleaned[cursor]
    cursor += 1
    if char == _STAR:
        return cursor, _SEGMENT_WILDCARD, True
    if char == _SINGLE_CHAR_WILDCARD:
        return cursor, _SEGMENT_SINGLE, True
    if char in _ESCAPED_CHARS:
        return cursor, _BACKSLASH + char, False
    if char in _BRACKET_CHARS:
        return cursor, char, True
    if char == _BACKSLASH:
        return _translate_backslash(cleaned, cursor)
    return cursor, char, False


def _consume_double_star(cleaned: str, cursor: int, match_type: _MatchType) -> tuple[int, str, _MatchType]:
    """`**` 已识别；先吃掉紧随的 `/`（Go 把 `**/` 当作 `**`），再按是否到串尾分流。"""
    if cleaned.startswith(_SEPARATOR, cursor):
        cursor += 1
    if cursor < len(cleaned):
        return cursor, _ANY_DEPTH, _MatchType.REGEXP
    if match_type is _MatchType.EXACT:
        return cursor, "", _MatchType.PREFIX
    return cursor, _ANY_SUFFIX, _MatchType.REGEXP


def _compile(cleaned: str) -> tuple[_MatchType, re.Pattern[str] | None]:
    """对齐 Pattern.compile：逐字符翻译成正则，同时判定可短路的匹配类型。"""
    fragments = ["^"]
    match_type = _MatchType.EXACT
    cursor = 0
    iteration = 0
    while cursor < len(cleaned):
        if cleaned.startswith(_DOUBLE_STAR, cursor):
            cursor, fragment, match_type = _consume_double_star(cleaned, cursor + _DOUBLE_STAR_WIDTH, match_type)
            # Go 的 `if i == 0`：`**` 位于首位时整体退化成后缀匹配；若后续字符再引入
            # 通配符，下面的 forces_regexp 会把类型改回 REGEXP。
            match_type = _MatchType.SUFFIX if iteration == 0 else match_type
        else:
            cursor, fragment, forces_regexp = _translate_char(cleaned, cursor)
            match_type = _MatchType.REGEXP if forces_regexp else match_type
        fragments.append(fragment)
        iteration += 1
    if match_type is not _MatchType.REGEXP:
        return match_type, None
    return match_type, re.compile("".join(fragments) + "$")


def _build_pattern(normalized: str) -> _Pattern:
    exclusion = normalized.startswith(_NEGATION_PREFIX)
    cleaned = normalized[1:] if exclusion else normalized
    if not cleaned:
        raise ValueError("illegal exclusion pattern: '!'")
    match_type, regexp = _compile(cleaned)
    return _Pattern(cleaned=cleaned, exclusion=exclusion, match_type=match_type, regexp=regexp)


def _match_one(pattern: _Pattern, path: str) -> bool:
    if pattern.match_type is _MatchType.EXACT:
        return path == pattern.cleaned
    if pattern.match_type is _MatchType.PREFIX:
        return path.startswith(pattern.cleaned[:-_DOUBLE_STAR_WIDTH])
    if pattern.match_type is _MatchType.SUFFIX:
        suffix = pattern.cleaned[_DOUBLE_STAR_WIDTH:]
        # Go 的特例：`**/foo` 也要命中裸 `foo`。
        return path.endswith(suffix) or (suffix.startswith(_SEPARATOR) and path == suffix[1:])
    return bool(pattern.regexp.search(path))  # type: ignore[union-attr]


def _self_and_ancestors(path: str) -> tuple[str, ...]:
    """路径本身 + 逐层祖先目录，顺序对齐 MatchesOrParentMatches 的检查次序。"""
    parent = posixpath.dirname(path)
    if parent in ("", posixpath.curdir):
        return (path,)
    segments = parent.split(_SEPARATOR)
    ancestors = (_SEPARATOR.join(segments[: index + 1]) for index in range(len(segments)))
    return (path, *ancestors)


@dataclass(frozen=True)
class DockerIgnoreSpec:
    """一份 `.dockerignore` 的规则集，按 moby/patternmatcher 语义求值。"""

    patterns: tuple[_Pattern, ...]

    @classmethod
    def from_lines(cls, lines: Iterable[str]) -> DockerIgnoreSpec:
        normalized = (_normalize_line(line) for line in lines)
        return cls(tuple(_build_pattern(line) for line in normalized if line is not None))

    def match_file(self, path: str) -> bool:
        """该路径是否被排除出构建 context（祖先目录被排除时，其下全部内容一并排除）。"""
        candidates = _self_and_ancestors(path)
        matched = False
        for pattern in self.patterns:
            # Go 的短路：已命中时跳过后续排除模式，未命中时跳过反选模式。
            if pattern.exclusion != matched:
                continue
            if any(_match_one(pattern, candidate) for candidate in candidates):
                matched = not pattern.exclusion
        return matched
