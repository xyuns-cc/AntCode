"""Redacted rendering for subprocess argv in Worker logs and errors."""

from __future__ import annotations

import shlex

_SENSITIVE_OPTIONS = frozenset(
    {
        "--api-key",
        "--authorization",
        "--extra-index-url",
        "--index-url",
        "--password",
        "--registry",
        "--token",
        "-i",
    }
)
_REDACTED = "<redacted>"


def format_command_for_log(args: list[str]) -> str:
    """Render argv while hiding values of credential-bearing options."""
    rendered: list[str] = []
    redact_next = False
    for arg in args:
        if redact_next:
            rendered.append(_REDACTED)
            redact_next = False
            continue
        option = arg.split("=", 1)[0]
        if option in _SENSITIVE_OPTIONS:
            rendered.append(f"{option}={_REDACTED}" if "=" in arg else option)
            redact_next = "=" not in arg
            continue
        rendered.append(arg)
    return shlex.join(rendered)


__all__ = ["format_command_for_log"]
