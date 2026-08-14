"""Deterministic Git ref selection for source bundles."""

from __future__ import annotations

LS_REMOTE_FIELD_COUNT = 2


def parse_ls_remote_output(output: str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    for line in output.strip().splitlines():
        parts = line.split(maxsplit=1)
        if len(parts) == LS_REMOTE_FIELD_COUNT:
            entries.append((parts[0].strip(), parts[1].strip()))
    return entries


def select_revision(entries: list[tuple[str, str]], ref: str) -> str:
    """Select the commit matching git clone's branch-before-tag behavior."""
    by_ref = {refname: sha for sha, refname in entries}
    branch_sha = by_ref.get(f"refs/heads/{ref}")
    if branch_sha:
        return branch_sha
    tag_ref = f"refs/tags/{ref}"
    tag_sha = by_ref.get(tag_ref)
    if tag_sha:
        return by_ref.get(f"{tag_ref}^{{}}") or tag_sha
    dereferenced = [sha for sha, refname in entries if refname.endswith("^{}")]
    if dereferenced:
        return dereferenced[-1]
    return entries[-1][0]


__all__ = ["parse_ls_remote_output", "select_revision"]
