"""Translate compose secrets into podman secret store create / mount / remove."""

import re
from pathlib import Path
from typing import Any

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.keys import Token
from compose2pod.shell import to_shell, variable_names


_LONG_FORM_KEYS = {"source", "target", "uid", "gid", "mode"}
_SECRET_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
_ENV_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _validate_secret_def(name: str, definition: Any) -> None:  # noqa: ANN401 - Compose values are untyped
    if not _SECRET_NAME.fullmatch(name):
        msg = f"secret name {name!r} must match [a-zA-Z0-9][a-zA-Z0-9_.-]*"
        raise UnsupportedComposeError(msg)
    if not isinstance(definition, dict):
        msg = f"secret {name!r} must be a mapping"
        raise UnsupportedComposeError(msg)
    unknown = set(definition) - {"file", "environment"}
    if unknown:
        if "external" in unknown:
            msg = f"secret {name!r}: external secrets are not supported (use a file: or environment: source)"
            raise UnsupportedComposeError(msg)
        msg = f"secret {name!r}: unsupported keys {sorted(unknown)}"
        raise UnsupportedComposeError(msg)
    sources = [key for key in ("file", "environment") if isinstance(definition.get(key), str)]
    if len(sources) != 1:
        msg = f"secret {name!r} must have exactly one of 'file' or 'environment' (a string)"
        raise UnsupportedComposeError(msg)
    if sources[0] == "environment" and not _ENV_NAME.fullmatch(definition["environment"]):
        msg = f"secret {name!r}: environment variable name {definition['environment']!r} is not a valid identifier"
        raise UnsupportedComposeError(msg)


def _check_long_form_scalars(name: str, ref: dict[str, Any]) -> None:
    for key in ("uid", "gid", "mode"):
        if key in ref and (isinstance(ref[key], bool) or not isinstance(ref[key], int | str)):
            msg = f"service {name!r}: secret {key!r} must be an int or string"
            raise UnsupportedComposeError(msg)


def _ref_source(name: str, ref: Any) -> str:  # noqa: ANN401 - Compose values are untyped
    if isinstance(ref, str):
        return ref
    if not isinstance(ref, dict):
        msg = f"service {name!r}: secret entry must be a string or mapping"
        raise UnsupportedComposeError(msg)
    unknown = set(ref) - _LONG_FORM_KEYS
    if unknown:
        msg = f"service {name!r}: unsupported secret keys {sorted(unknown)}"
        raise UnsupportedComposeError(msg)
    source = ref.get("source")
    if not isinstance(source, str):
        msg = f"service {name!r}: secret entry 'source' must be a string"
        raise UnsupportedComposeError(msg)
    _check_long_form_scalars(name, ref)
    return source


def validate_secrets(compose: dict[str, Any]) -> None:
    """Validate top-level secret definitions and every service's references to them."""
    defs = compose.get("secrets")
    if defs is not None and not isinstance(defs, dict):
        msg = "top-level 'secrets' must be a mapping"
        raise UnsupportedComposeError(msg)
    defs = defs or {}
    for name, definition in defs.items():
        _validate_secret_def(name, definition)
    for name, svc in (compose.get("services") or {}).items():
        refs = svc.get("secrets")
        if refs is None:
            continue
        if not isinstance(refs, list):
            msg = f"service {name!r}: secrets must be a list"
            raise UnsupportedComposeError(msg)
        for ref in refs:
            source = _ref_source(name, ref)
            if source not in defs:
                msg = f"service {name!r}: unknown secret {source!r}"
                raise UnsupportedComposeError(msg)


def _mode_str(mode: Any) -> str:  # noqa: ANN401 - Compose values are untyped
    return format(mode, "04o") if isinstance(mode, int) else str(mode)


def referenced_secret_names(services: dict[str, Any], order: list[str]) -> list[str]:
    """Secret names referenced by services in `order`, unique in first-seen order."""
    seen: dict[str, None] = {}
    for name in order:
        for ref in services[name].get("secrets") or []:
            seen[ref if isinstance(ref, str) else ref["source"]] = None
    return list(seen)


def secret_flags(svc: dict[str, Any], pod: str) -> list[Token]:
    """Per-service `--secret source=<pod>-<name>,target=...` flag tokens."""
    tokens: list[Token] = []
    for ref in svc.get("secrets") or []:
        opts = {} if isinstance(ref, str) else ref
        source = ref if isinstance(ref, str) else ref["source"]
        parts = [f"source={pod}-{source}", f"target={opts.get('target', source)}"]
        parts += [f"{key}={opts[key]}" for key in ("uid", "gid") if key in opts]
        if "mode" in opts:
            parts.append(f"mode={_mode_str(opts['mode'])}")
        tokens += ["--secret", ",".join(parts)]
    return tokens


def secret_create_lines(compose: dict[str, Any], pod: str, project_dir: str, names: list[str]) -> list[str]:
    """`podman secret create` lines for the referenced secrets (file or environment source)."""
    defs = compose.get("secrets") or {}
    lines: list[str] = []
    for name in names:
        definition = defs[name]
        store = f"{pod}-{name}"
        if "file" in definition:
            path = to_shell(str(Path(project_dir, definition["file"])))
            lines.append(f"podman secret create {store} {path}")
        else:
            var = definition["environment"]
            lines.append(f"printf '%s' \"${{{var}-}}\" | podman secret create {store} -")
    return lines


def secret_referenced_variables(compose: dict[str, Any], project_dir: str, names: list[str]) -> set[str]:
    """Run-time variable names the secret create lines expand (env-source vars + file-path vars)."""
    defs = compose.get("secrets") or {}
    result: set[str] = set()
    for name in names:
        definition = defs[name]
        if "file" in definition:
            result |= variable_names(str(Path(project_dir, definition["file"])))
        else:
            result.add(definition["environment"])
    return result
