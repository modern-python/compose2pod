"""Resolve same-file compose `extends`: flatten each service's inheritance."""

from typing import Any

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.keys import SERVICE_KEYS, as_list, pairs_to_mapping


# Merge policy for keys with a SERVICE_KEYS KeySpec comes from spec.merge (see
# _merge below); these two sets cover only the remaining structural keys, which
# have no KeySpec. Both categories obey one rule: a merge may normalize a value
# only through a form that key actually has, so it can never accept a shape the
# gate would reject standalone.
_STRUCTURAL_MERGE_KEYS = {"environment", "extra_hosts", "healthcheck", "depends_on"}
_STRUCTURAL_CONCAT_KEYS = {"secrets", "configs", "volumes", "tmpfs", "env_file"}

# The only concat keys Compose gives a bare-string form. The gate accepts a
# scalar for exactly these two and requires a list for the rest, so only these
# two may be normalized scalar -> [scalar] on a merged side; normalizing any
# other would accept a shape the gate refuses standalone.
_SCALAR_FORM_KEYS = {"tmpfs", "env_file"}


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
        # sorted(unknown) would crash raw (TypeError: '<' not supported...)
        # if unknown holds keys of more than one incomparable type -- e.g. an
        # int key alongside a str key, which a hostile 'extends' mapping can
        # freely produce since this runs ahead of validate()'s gate. Sorting
        # by repr keeps the message deterministic without assuming key types
        # are mutually comparable.
        msg = f"service {name!r}: unsupported 'extends' keys {sorted(unknown, key=repr)}"
        raise UnsupportedComposeError(msg)
    service = ext.get("service")
    if not isinstance(service, str):
        msg = f"service {name!r}: extends 'service' must be a string"
        raise UnsupportedComposeError(msg)
    return service


def _extra_hosts_to_mapping(name: str, value: list[Any]) -> dict[str, Any]:
    """Normalize list-form `extra_hosts` ('host:ip') to a mapping.

    Separated by a colon, not '=', so `pairs_to_mapping` would mangle the whole
    entry into a single `{'host:ip': None}` key. Split on the *first* colon only:
    an IPv6 address is itself full of them (`myhost:::1` -> `{'myhost': '::1'}`).
    """
    result: dict[str, Any] = {}
    for item in value:
        if not isinstance(item, str) or ":" not in item:
            msg = f"service {name!r}: extra_hosts entries must be 'host:ip' strings"
            raise UnsupportedComposeError(msg)
        host, _sep, address = item.partition(":")
        result[host] = address
    return result


def _as_concat_list(name: str, key: str, value: Any) -> list[Any]:  # noqa: ANN401 - Compose values are untyped
    """Normalize a structural concat key's value to a list, without widening the gate.

    `keys.as_list` turns a bare string into a one-element list, which is right
    for `tmpfs`/`env_file` (Compose gives them a scalar form and the gate accepts
    one) and wrong for `volumes`/`secrets`/`configs`, where the gate requires a
    list -- normalizing there would let `extends` accept a scalar the same
    document would be refused for standalone.
    """
    if isinstance(value, str) and key not in _SCALAR_FORM_KEYS:
        msg = f"service {name!r}: '{key}' must be a list"
        raise UnsupportedComposeError(msg)
    return as_list(name, key, value)


def _as_mapping(name: str, key: str, value: Any) -> dict[str, Any]:  # noqa: ANN401 - Compose values are untyped
    """Normalize a structural mapping-merge key's value to a mapping.

    List form is accepted for exactly the keys Compose defines one for --
    `environment`, `extra_hosts`, `depends_on` -- each through the normalizer
    that key's own list form actually needs. `healthcheck` has no list form, so
    a list is refused rather than coerced: a merge must not accept a shape the
    gate would reject standalone (see `_merge`).
    """
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        if key == "environment":
            return pairs_to_mapping(name, key, value)
        if key == "extra_hosts":
            return _extra_hosts_to_mapping(name, value)
        if key == "depends_on":
            for dep in value:
                if not isinstance(dep, str):
                    msg = f"service {name!r}: depends_on entry {dep!r} must be a string"
                    raise UnsupportedComposeError(msg)
            return {dep: {} for dep in value}
    msg = f"service {name!r}: cannot merge {key!r} across incompatible forms"
    raise UnsupportedComposeError(msg)


def _merge(base: dict[str, Any], local: dict[str, Any], name: str) -> dict[str, Any]:
    """Merge `local` onto `base` per key category: mapping-merge, sequence-concat, else override."""
    merged: dict[str, Any] = dict(base)
    for key, local_val in local.items():
        spec = SERVICE_KEYS.get(key)
        if key in base and spec is not None and spec.merge is not None:
            # A merge must never *widen* what the gate accepts. `resolve_extends`
            # runs ahead of `validate()`, so a normalizing merge (list -> mapping)
            # can launder a form the key does not have into one it does:
            # `ulimits: ["nofile=2"]` is refused standalone, but coercing it here
            # would produce a valid-looking `{"nofile": "2"}` that then sails
            # through the gate. Checking each side with the key's own validator
            # first derives the merge's accepted forms from the gate's, so the
            # two cannot drift apart.
            spec.validate(name, key, base[key])
            spec.validate(name, key, local_val)
            merged[key] = spec.merge(name, key, base[key], local_val)
        elif key in base and key in _STRUCTURAL_MERGE_KEYS:
            merged[key] = {**_as_mapping(name, key, base[key]), **_as_mapping(name, key, local_val)}
        elif key in base and key in _STRUCTURAL_CONCAT_KEYS:
            merged[key] = _as_concat_list(name, key, base[key]) + _as_concat_list(name, key, local_val)
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
