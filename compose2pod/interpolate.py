"""Resolve Compose-spec `${VAR}` interpolation against a given environment."""

import re
from collections.abc import Mapping
from typing import Any

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


def _resolve(name: str, op: str | None, arg: str, environ: Mapping[str, str], warnings: list[str]) -> str:
    is_set = name in environ
    is_set_and_nonempty = is_set and environ[name] != ""
    if op is None:
        if is_set:
            return environ[name]
        warnings.append(f"variable '{name}' is not set, defaulting to blank string")
        return ""
    if op in {"-", ":-"}:
        condition = is_set_and_nonempty if op == ":-" else is_set
        return environ[name] if condition else arg
    if op in {"?", ":?"}:
        condition = is_set_and_nonempty if op == ":?" else is_set
        if condition:
            return environ[name]
        raise UnsupportedComposeError(arg)
    condition = is_set_and_nonempty if op == ":+" else is_set
    return arg if condition else ""


def _interpolate_string(text: str, environ: Mapping[str, str], warnings: list[str]) -> str:
    def substitute(match: re.Match[str]) -> str:
        if match.group("escaped"):
            return "$"
        if match.group("named") is not None:
            return _resolve(match.group("named"), None, "", environ, warnings)
        return _resolve(match.group("braced"), match.group("op"), match.group("arg"), environ, warnings)

    return _PATTERN.sub(substitute, text)


def _walk(node: Any, environ: Mapping[str, str], warnings: list[str]) -> Any:  # noqa: ANN401 - arbitrary compose value
    if isinstance(node, str):
        return _interpolate_string(node, environ, warnings)
    if isinstance(node, dict):
        return {key: _walk(value, environ, warnings) for key, value in node.items()}
    if isinstance(node, list):
        return [_walk(item, environ, warnings) for item in node]
    return node


def interpolate(document: Any, environ: Mapping[str, str]) -> tuple[Any, list[str]]:  # noqa: ANN401 - arbitrary compose value
    """Resolve `${VAR}`-style references in every string leaf of `document`.

    Dict keys are left untouched. Returns the resolved document and any
    "variable not set" warnings; raises `UnsupportedComposeError` for a
    `${VAR:?msg}`/`${VAR?msg}` reference whose condition is not met.
    """
    warnings: list[str] = []
    resolved = _walk(document, environ, warnings)
    return resolved, warnings
