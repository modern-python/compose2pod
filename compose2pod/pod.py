"""Aggregate compose dns/sysctls onto pod-level `podman pod create` flags."""

from typing import Any

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.keys import Token, _Expand


_DNS_KEYS = {"dns": "--dns", "dns_search": "--dns-search", "dns_opt": "--dns-option"}
_POD_OPTION_KEYS = (*_DNS_KEYS, "sysctls")


def _as_str_list(name: str, key: str, value: Any) -> list[str]:  # noqa: ANN401 - Compose values are untyped
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
        msg = f"service {name!r}: {key!r} must be a string or list of strings"
        raise UnsupportedComposeError(msg)
    return items


def _sysctl_pairs(name: str, value: Any) -> list[tuple[str, str]]:  # noqa: ANN401 - Compose values are untyped
    if isinstance(value, dict):
        pairs: list[tuple[str, str]] = []
        for key, val in value.items():
            if isinstance(val, bool) or not isinstance(val, str | int | float):
                msg = f"service {name!r}: sysctl {key!r} value must be a string or number"
                raise UnsupportedComposeError(msg)
            pairs.append((str(key), str(val)))
        return pairs
    if isinstance(value, list):
        pairs = []
        for item in value:
            if not isinstance(item, str) or "=" not in item:
                msg = f"service {name!r}: sysctls list entries must be 'key=value' strings"
                raise UnsupportedComposeError(msg)
            key, _sep, val = item.partition("=")
            pairs.append((key, val))
        return pairs
    msg = f"service {name!r}: 'sysctls' must be a mapping or a list of 'key=value' strings"
    raise UnsupportedComposeError(msg)


def validate_pod_options(name: str, svc: dict[str, Any]) -> None:
    """Shape-check a service's pod-level dns/sysctls declarations."""
    for key in _DNS_KEYS:
        if key in svc:
            _as_str_list(name, key, svc[key])
    if "sysctls" in svc:
        _sysctl_pairs(name, svc["sysctls"])


def uses_pod_options(services: dict[str, Any]) -> bool:
    """Whether any service declares a pod-level dns/sysctls option."""
    return any(key in svc for svc in services.values() for key in _POD_OPTION_KEYS)


def _dns_flags(services: dict[str, Any], order: list[str]) -> list[Token]:
    tokens: list[Token] = []
    for key, flag in _DNS_KEYS.items():
        seen: dict[str, None] = {}
        for name in order:
            svc = services[name]
            if key in svc:
                for value in _as_str_list(name, key, svc[key]):
                    seen[value] = None
        for value in seen:
            tokens += [flag, _Expand(value=value)]
    return tokens


def _sysctl_flags(services: dict[str, Any], order: list[str]) -> list[Token]:
    merged: dict[str, str] = {}
    for name in order:
        svc = services[name]
        if "sysctls" not in svc:
            continue
        for key, val in _sysctl_pairs(name, svc["sysctls"]):
            if merged.get(key, val) != val:
                msg = f"service {name!r}: conflicting sysctl {key!r} ({merged[key]!r} vs {val!r})"
                raise UnsupportedComposeError(msg)
            merged[key] = val
    tokens: list[Token] = []
    for key, val in merged.items():
        tokens += ["--sysctl", _Expand(value=f"{key}={val}")]
    return tokens


def pod_create_flags(services: dict[str, Any], order: list[str]) -> list[Token]:
    """Pod-create flag tokens aggregated across the closure `order`.

    dns/dns_search/dns_opt are unioned (dedup, first-seen order); sysctls are
    unioned by key and a same-key value conflict is refused.
    """
    return _dns_flags(services, order) + _sysctl_flags(services, order)
