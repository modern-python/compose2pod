"""Resolve same-file compose `extends`: flatten each service's inheritance."""

from typing import Any

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.keys import SERVICE_KEYS, _pairs_to_mapping


# Merge policy for keys with a SERVICE_KEYS KeySpec comes from spec.merge (see
# _merge below); these two sets cover only the remaining structural keys, which
# have no KeySpec. Unifying structural-key merge policy is deferred — see
# decisions/2026-07-12-reject-structural-key-registry.md's revisit trigger.
_STRUCTURAL_MERGE_KEYS = {"environment", "extra_hosts", "healthcheck", "depends_on"}
_STRUCTURAL_CONCAT_KEYS = {"secrets", "configs", "volumes", "tmpfs", "env_file"}


def _extends_target(name: str, ext: Any) -> str:  # noqa: ANN401 - Compose values are untyped
    """Return the referenced service name, after refusing cross-file and malformed forms."""
    if not isinstance(ext, dict):
        msg = f"service {name!r}: 'extends' must be a mapping with a 'service' key"
        raise UnsupportedComposeError(msg)
    if "file" in ext:
        msg = f"service {name!r}: extends with 'file:' (cross-file) is not supported"
        raise UnsupportedComposeError(msg)
    unknown = set(ext) - {"service"}
    if unknown:
        msg = f"service {name!r}: unsupported 'extends' keys {sorted(unknown)}"
        raise UnsupportedComposeError(msg)
    service = ext.get("service")
    if not isinstance(service, str):
        msg = f"service {name!r}: extends 'service' must be a string"
        raise UnsupportedComposeError(msg)
    return service


def _as_mapping(key: str, name: str, value: Any) -> dict[str, Any]:  # noqa: ANN401 - Compose values are untyped
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        if key == "environment":
            return _pairs_to_mapping(name, key, value)
        if key == "depends_on":
            return {dep: {} for dep in value}
    msg = f"service {name!r}: cannot merge {key!r} across incompatible forms"
    raise UnsupportedComposeError(msg)


def _as_list(key: str, name: str, value: Any) -> list[Any]:  # noqa: ANN401 - Compose values are untyped
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        return [value]
    msg = f"service {name!r}: cannot merge {key!r} across incompatible forms"
    raise UnsupportedComposeError(msg)


def _merge(base: dict[str, Any], local: dict[str, Any], name: str) -> dict[str, Any]:
    """Merge `local` onto `base` per key category: mapping-merge, sequence-concat, else override."""
    merged: dict[str, Any] = dict(base)
    for key, local_val in local.items():
        spec = SERVICE_KEYS.get(key)
        if key in base and spec is not None and spec.merge is not None:
            merged[key] = spec.merge(name, key, base[key], local_val)
        elif key in base and key in _STRUCTURAL_MERGE_KEYS:
            merged[key] = {**_as_mapping(key, name, base[key]), **_as_mapping(key, name, local_val)}
        elif key in base and key in _STRUCTURAL_CONCAT_KEYS:
            merged[key] = _as_list(key, name, base[key]) + _as_list(key, name, local_val)
        else:
            merged[key] = local_val
    return merged


def resolve_extends(compose: Any) -> Any:  # noqa: ANN401 - Compose values are untyped
    """Return a new document with every service's same-file `extends` flattened.

    Transitive and cycle-checked; cross-file (`file:`) extends is refused. A
    non-dict document or non-dict `services` is returned unchanged for
    `validate()` to reject.
    """
    if not isinstance(compose, dict):
        return compose
    services = compose.get("services")
    if not isinstance(services, dict):
        return compose
    resolved: dict[str, Any] = {}
    resolving: set[str] = set()

    def resolve(name: str) -> Any:  # noqa: ANN401 - Compose values are untyped
        if name in resolved:
            return resolved[name]
        svc = services[name]
        ext = svc.get("extends") if isinstance(svc, dict) else None
        if ext is None:
            resolved[name] = svc
            return svc
        if name in resolving:
            msg = f"extends cycle involving {name!r}"
            raise UnsupportedComposeError(msg)
        resolving.add(name)
        base_name = _extends_target(name, ext)
        if base_name not in services:
            msg = f"service {name!r}: extends unknown service {base_name!r}"
            raise UnsupportedComposeError(msg)
        base = resolve(base_name)
        local = {key: value for key, value in svc.items() if key != "extends"}
        resolving.discard(name)
        if not isinstance(base, dict):
            resolved[name] = local
            return local
        merged = _merge(base, local, name)
        resolved[name] = merged
        return merged

    new_services = {name: resolve(name) for name in services}
    return {**compose, "services": new_services}
