"""The store registry: how compose secrets and configs are validated and emitted.

Both compose `secrets` and `configs` render as podman secrets -- podman has no
config primitive -- so the two `StoreKind`s differ only in namespacing and
default mount, never in the podman noun. This module owns that noun end to end;
callers never name `secret`.
"""

import dataclasses
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from compose2pod import values
from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.keys import Token, require_string_keys
from compose2pod.shell import to_shell, variable_names


_LONG_FORM_KEYS = {"source", "target", "uid", "gid", "mode"}
# Docker's shared identifier grammar for a secret/config *name* -- and, per
# `parsing._named_volume_source`, for telling a named-volume reference apart
# from a bind-mount source (a relative or absolute path, or a `~`-prefixed
# home-relative path all fail this pattern, since none of `.`, `/`, `~` is a
# pattern character). Public (no leading underscore) because parsing.py, in a
# different module, reuses it rather than duplicating it.
NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
_ENV_NAME = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class StoreKind:
    """One store flavor (secret or config): namespacing, sources, default mount."""

    label: str
    top_key: str
    prefix: str
    sources: frozenset[str]
    default_target: Callable[[str], str]
    require_absolute_target: bool


# The secret StoreKind: a podman secret mounted at /run/secrets/<name>.
SECRET = StoreKind(
    label="secret",
    top_key="secrets",
    prefix="",
    sources=frozenset({"file", "environment"}),
    default_target=lambda name: name,
    require_absolute_target=False,
)

# The config StoreKind: a podman secret mounted at the container-root path /<name>.
CONFIG = StoreKind(
    label="config",
    top_key="configs",
    prefix="config-",
    sources=frozenset({"file", "environment", "content"}),
    default_target=lambda name: f"/{name}",
    require_absolute_target=True,
)

# Order is significant: it fixes the flag/create/teardown emission order
# (secrets before configs) across the whole script.
_STORE_KINDS = (SECRET, CONFIG)


def _validate_def(name: str, definition: Any, kind: StoreKind) -> None:  # noqa: ANN401 - Compose values are untyped
    if not NAME_PATTERN.fullmatch(name):
        msg = f"{kind.label} name {name!r} must match [a-zA-Z0-9][a-zA-Z0-9_.-]*"
        raise UnsupportedComposeError(msg)
    if not isinstance(definition, dict):
        msg = f"{kind.label} {name!r} must be a mapping"
        raise UnsupportedComposeError(msg)
    keys = require_string_keys(f"{kind.label} {name!r}", definition)
    unknown = keys - kind.sources
    if unknown:
        if "external" in unknown:
            msg = (
                f"{kind.label} {name!r}: external {kind.label}s are not supported (use a file: or environment: source)"
            )
            raise UnsupportedComposeError(msg)
        msg = f"{kind.label} {name!r}: unsupported keys {sorted(unknown)}"
        raise UnsupportedComposeError(msg)
    sources = [key for key in sorted(kind.sources) if isinstance(definition.get(key), str)]
    if len(sources) != 1:
        allowed = " or ".join(f"'{source}'" for source in sorted(kind.sources))
        msg = f"{kind.label} {name!r} must have exactly one of {allowed} (a string)"
        raise UnsupportedComposeError(msg)
    if isinstance(definition.get("environment"), str) and not _ENV_NAME.fullmatch(definition["environment"]):
        msg = (
            f"{kind.label} {name!r}: environment variable name {definition['environment']!r} is not a valid identifier"
        )
        raise UnsupportedComposeError(msg)


def _check_long_form_scalars(name: str, ref: dict[str, Any], kind: StoreKind) -> None:
    """Check uid/gid/mode against Docker's own per-field grammar for a store reference.

    Measured against `docker compose config` v5.1.2: uid/gid and mode are NOT
    the same grammar, despite reading like siblings. uid/gid are typed as a
    plain string field with no further parsing at config-validate time -- an
    int/bool/float/null is refused ('must be a string'), but the string's
    *content* is entirely unchecked (even 'uid: somevalue' passes; whatever
    numeric parse exists happens later, outside Compose's own validation).
    `mode` goes through Go's `strconv.ParseInt` at decode time instead: it
    accepts a native int (any float is refused, whole or not, unlike uid/gid)
    or a strict, sign-optional ParseInt-grammar string (no digit-grouping
    underscore, no surrounding whitespace -- 'mode: "  400  "' is refused).
    """
    for key in ("uid", "gid"):
        if key in ref:
            values.validate_string(name, f"{kind.label} {key}", ref[key])
    if "mode" in ref:
        values.validate_integer(name, f"{kind.label} mode", ref["mode"], strict_string=True)


def _check_target(name: str, ref: dict[str, Any], kind: StoreKind) -> None:
    if "target" in ref and not isinstance(ref["target"], str):
        msg = f"service {name!r}: {kind.label} target must be a string"
        raise UnsupportedComposeError(msg)
    if kind.require_absolute_target and isinstance(ref.get("target"), str) and not ref["target"].startswith("/"):
        msg = f"service {name!r}: {kind.label} target {ref['target']!r} must be an absolute path"
        raise UnsupportedComposeError(msg)


def _ref_source(name: str, ref: Any, kind: StoreKind) -> str:  # noqa: ANN401 - Compose values are untyped
    if isinstance(ref, str):
        return ref
    if not isinstance(ref, dict):
        msg = f"service {name!r}: {kind.label} entry must be a string or mapping"
        raise UnsupportedComposeError(msg)
    keys = require_string_keys(f"service {name!r}: {kind.label} entry", ref)
    unknown = keys - _LONG_FORM_KEYS
    if unknown:
        msg = f"service {name!r}: unsupported {kind.label} keys {sorted(unknown)}"
        raise UnsupportedComposeError(msg)
    source = ref.get("source")
    if not isinstance(source, str):
        msg = f"service {name!r}: {kind.label} entry 'source' must be a string"
        raise UnsupportedComposeError(msg)
    _check_long_form_scalars(name, ref, kind)
    _check_target(name, ref, kind)
    return source


def _validate_kind(compose: dict[str, Any], kind: StoreKind) -> None:
    defs = compose.get(kind.top_key)
    if defs is not None and not isinstance(defs, dict):
        msg = f"top-level {kind.top_key!r} must be a mapping"
        raise UnsupportedComposeError(msg)
    defs = defs or {}
    require_string_keys(f"top-level {kind.top_key!r}", defs)
    for name, definition in defs.items():
        _validate_def(name, definition, kind)
    for name, svc in (compose.get("services") or {}).items():
        refs = svc.get(kind.top_key)
        if refs is None:
            continue
        if not isinstance(refs, list):
            msg = f"service {name!r}: {kind.top_key} must be a list"
            raise UnsupportedComposeError(msg)
        for ref in refs:
            source = _ref_source(name, ref, kind)
            if source not in defs:
                msg = f"service {name!r}: unknown {kind.label} {source!r}"
                raise UnsupportedComposeError(msg)


def _mode_str(mode: Any) -> str:  # noqa: ANN401 - Compose values are untyped
    return format(mode, "04o") if isinstance(mode, int) else str(mode)


def _referenced_names(services: dict[str, Any], order: list[str], kind: StoreKind) -> list[str]:
    """Store names referenced by services in `order`, unique in first-seen order."""
    seen: dict[str, None] = {}
    for name in order:
        for ref in services[name].get(kind.top_key) or []:
            seen[ref if isinstance(ref, str) else ref["source"]] = None
    return list(seen)


def _flags_for(svc: dict[str, Any], pod: str, kind: StoreKind) -> list[Token]:
    """Per-service `--secret source=<pod>-<prefix><name>,target=...` flag tokens."""
    tokens: list[Token] = []
    for ref in svc.get(kind.top_key) or []:
        opts = {} if isinstance(ref, str) else ref
        source = ref if isinstance(ref, str) else ref["source"]
        parts = [f"source={pod}-{kind.prefix}{source}", f"target={opts.get('target', kind.default_target(source))}"]
        parts += [f"{key}={opts[key]}" for key in ("uid", "gid") if key in opts]
        if "mode" in opts:
            parts.append(f"mode={_mode_str(opts['mode'])}")
        tokens += ["--secret", ",".join(parts)]
    return tokens


def _create_lines_for(
    compose: dict[str, Any],
    pod: str,
    project_dir: str,
    names: list[str],
    kind: StoreKind,
) -> list[str]:
    """`podman secret create` lines for the referenced stores (file or environment source)."""
    defs = compose.get(kind.top_key) or {}
    lines: list[str] = []
    for name in names:
        definition = defs[name]
        store = f"{pod}-{kind.prefix}{name}"
        if isinstance(definition.get("file"), str):
            path = to_shell(str(Path(project_dir, definition["file"])))
            lines.append(f"podman secret create {store} {path}")
        elif isinstance(definition.get("content"), str):
            lines.append(f"printf '%s' {to_shell(definition['content'])} | podman secret create {store} -")
        else:
            var = definition["environment"]
            lines.append(f"printf '%s' \"${{{var}-}}\" | podman secret create {store} -")
    return lines


def _referenced_variables_for(
    compose: dict[str, Any],
    project_dir: str,
    names: list[str],
    kind: StoreKind,
) -> set[str]:
    """Run-time variable names the create lines expand (env-source vars + file-path vars)."""
    defs = compose.get(kind.top_key) or {}
    result: set[str] = set()
    for name in names:
        definition = defs[name]
        if isinstance(definition.get("file"), str):
            result |= variable_names(str(Path(project_dir, definition["file"])))
        elif isinstance(definition.get("content"), str):
            result |= variable_names(definition["content"])
        else:
            result.add(definition["environment"])
    return result


def validate(compose: dict[str, Any]) -> None:
    """Validate every kind's top-level definitions and service references."""
    for kind in _STORE_KINDS:
        _validate_kind(compose, kind)


def flags(svc: dict[str, Any], pod: str) -> list[Token]:
    """Per-service `--secret` flag tokens across every store kind (secrets, then configs)."""
    tokens: list[Token] = []
    for kind in _STORE_KINDS:
        tokens += _flags_for(svc, pod, kind)
    return tokens


def teardown_line(compose: dict[str, Any], order: list[str], pod: str) -> str:
    """Best-effort EXIT-trap fragment removing every referenced store, or "" if none.

    Returns the complete fragment -- suppression plus `|| true` -- so a failed
    removal can never abort the trap and leak the pod.
    """
    services = compose.get("services") or {}
    names: list[str] = []
    for kind in _STORE_KINDS:
        names += [f"{pod}-{kind.prefix}{name}" for name in _referenced_names(services, order, kind)]
    if not names:
        return ""
    return f"podman secret rm {' '.join(names)} >/dev/null 2>&1 || true"


def create_lines(compose: dict[str, Any], order: list[str], pod: str, project_dir: str) -> list[str]:
    """`podman secret create` lines for every referenced store (secrets, then configs)."""
    services = compose.get("services") or {}
    lines: list[str] = []
    for kind in _STORE_KINDS:
        lines += _create_lines_for(compose, pod, project_dir, _referenced_names(services, order, kind), kind)
    return lines


def referenced_variables(compose: dict[str, Any], order: list[str], project_dir: str) -> set[str]:
    """Run-time variable names every referenced store's create lines expand."""
    services = compose.get("services") or {}
    result: set[str] = set()
    for kind in _STORE_KINDS:
        result |= _referenced_variables_for(compose, project_dir, _referenced_names(services, order, kind), kind)
    return result
