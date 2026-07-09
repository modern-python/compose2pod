"""Encode Compose string values as POSIX-shell fragments the runtime shell expands."""

import re

from compose2pod.exceptions import UnsupportedComposeError


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
    arg = match.group("arg")
    if op is None:
        if arg:
            # `${NAME<garbage>}` -- text after the name is not a valid operator.
            msg = f"malformed variable reference: ${{{name}{arg}}}"
            raise UnsupportedComposeError(msg)
        return "${" + name + "-}"
    return "${" + name + op + _escape_literal(arg) + "}"


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


def variable_names(value: str) -> set[str]:
    """Variable names `value` references (excluding `$$`)."""
    names: set[str] = set()
    for match in _PATTERN.finditer(value):
        if match.group("escaped"):
            continue
        names.add(match.group("named") or match.group("braced"))
    return names
