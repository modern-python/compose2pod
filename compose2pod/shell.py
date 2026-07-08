"""Encode Compose string values as POSIX-shell fragments the runtime shell expands."""

import re
from collections.abc import Iterator
from typing import Any


_PATTERN = re.compile(
    r"""
    \$(?:
        (?P<escaped>\$)
        |(?P<named>[a-zA-Z_][a-zA-Z0-9_]*)
        |\{(?P<braced>[a-zA-Z_][a-zA-Z0-9_]*)(?P<op>:-|-|:\?|\?|:\+|\+)?(?P<arg>[^}]*)\}
    )
    """,
    re.VERBOSE,
)

_DQUOTE_ESCAPES = {"\\": "\\\\", '"': '\\"', "$": "\\$", "`": "\\`"}


def _escape_literal(text: str) -> str:
    """Escape `text` so a POSIX shell treats it literally inside double quotes."""
    return "".join(_DQUOTE_ESCAPES.get(char, char) for char in text)


def _encode_match(match: re.Match[str]) -> str:
    if match.group("escaped"):
        return "\\$"  # Compose `$$` -> a literal `$`
    if match.group("named") is not None:
        return "${" + match.group("named") + "-}"  # unset -> empty, survives `set -u`
    name = match.group("braced")
    op = match.group("op")
    if op is None:
        return "${" + name + "-}"
    return "${" + name + op + _escape_literal(match.group("arg")) + "}"


def to_shell(value: str) -> str:
    """Return `value` as a double-quoted shell fragment.

    Compose variable references (`$VAR`, `${VAR:-d}`, ...) stay live so the
    shell running the generated script resolves them against its own
    environment; every other character is inert (no command substitution, no
    accidental expansion). `$$` becomes a literal `$`.
    """
    out: list[str] = []
    pos = 0
    for match in _PATTERN.finditer(value):
        out.append(_escape_literal(value[pos : match.start()]))
        out.append(_encode_match(match))
        pos = match.end()
    out.append(_escape_literal(value[pos:]))
    return '"' + "".join(out) + '"'


def _string_leaves(node: Any) -> Iterator[str]:  # noqa: ANN401 - arbitrary compose value
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _string_leaves(value)
    elif isinstance(node, list):
        for item in node:
            yield from _string_leaves(item)


def referenced_variables(document: Any) -> list[str]:  # noqa: ANN401 - arbitrary compose value
    """Sorted unique variable names the document references (excluding `$$`)."""
    names: set[str] = set()
    for text in _string_leaves(document):
        for match in _PATTERN.finditer(text):
            if match.group("escaped"):
                continue
            names.add(match.group("named") or match.group("braced"))
    return sorted(names)
