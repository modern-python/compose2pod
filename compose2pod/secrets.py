"""Translate compose secrets into podman secret store create / mount / remove."""

from typing import Any

from compose2pod.exceptions import UnsupportedComposeError


_LONG_FORM_KEYS = {"source", "target", "uid", "gid", "mode"}


def _validate_secret_def(name: str, definition: Any) -> None:  # noqa: ANN401
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


def _ref_source(name: str, ref: Any) -> str:  # noqa: ANN401
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
