"""Aggregate compose dns/sysctls onto pod-level `podman pod create` flags."""

from typing import Any

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.keys import Expand, Token, extra_host_entries, validate_map


_DNS_KEYS = {"dns": "--dns", "dns_search": "--dns-search", "dns_opt": "--dns-option"}
# Docker accepts a bare string for `dns` and `dns_search` but requires a list for
# `dns_opt`. Measured, not inferred -- the asymmetry is Docker's, not ours.
_DNS_LIST_ONLY = {"dns_opt"}
_POD_OPTION_KEYS = (*_DNS_KEYS, "sysctls", "extra_hosts")


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


def _check_extra_host_separators(name: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped
    """Check each list-form entry actually divides into a host and an address.

    An entry with neither separator has no address to emit, and would render as
    a malformed `--add-host "no-separator:"`. The mapping form cannot have this
    problem: its key and value are already separate.
    """
    if not isinstance(value, list):
        return
    for entry in value:
        if "=" not in entry and ":" not in entry:
            msg = f"service {name!r}: extra_hosts entries must be 'host=ip' or 'host:ip' (got {entry!r})"
            raise UnsupportedComposeError(msg)


def _check_extra_host_value_types(name: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped
    """Check each map-form value is a string; Docker refuses a non-string extra_hosts value.

    `validate_map`'s shared shape check accepts a map value that is a string,
    number, boolean, or null -- correct for `labels`/`annotations`' bare-key-
    means-null semantics, but too loose for `extra_hosts`: an int/bool/null
    address would otherwise reach `keys.extra_host_entries` and get coerced
    into a bogus `--add-host` value. Measured against `docker compose config`
    v5.1.2: `extra_hosts: {h: 3}` and `extra_hosts: {h: true}` are both
    refused ("services.<svc>.extra_hosts.h must be a string"). The list form
    cannot have this problem: `validate_map` already requires every list
    element to be a string.
    """
    if not isinstance(value, dict):
        return
    for host, address in value.items():
        if not isinstance(address, str):
            msg = f"service {name!r}: extra_hosts {host!r} must be a string"
            raise UnsupportedComposeError(msg)


def validate_pod_options(name: str, svc: dict[str, Any]) -> None:
    """Shape-check a service's pod-level dns/sysctls declarations."""
    for key in _DNS_KEYS:
        if key in svc:
            if key in _DNS_LIST_ONLY and not isinstance(svc[key], list):
                msg = f"service {name!r}: {key!r} must be a list of strings"
                raise UnsupportedComposeError(msg)
            _as_str_list(name, key, svc[key])
    if "sysctls" in svc:
        _sysctl_pairs(name, svc["sysctls"])
    if "extra_hosts" in svc:
        validate_map(name, "extra_hosts", svc["extra_hosts"])
        _check_extra_host_separators(name, svc["extra_hosts"])
        _check_extra_host_value_types(name, svc["extra_hosts"])


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
            tokens += [flag, Expand(value=value)]
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
        tokens += ["--sysctl", Expand(value=f"{key}={val}")]
    return tokens


_LOCALHOST_LINES: tuple[str, str] = ("127.0.0.1 localhost", "::1 localhost")


def hosts_file_tokens(services: dict[str, Any], order: list[str], hosts: list[str]) -> list[Token]:
    """Render the pod's /etc/hosts as `IP NAME` line tokens (localhost lines first).

    compose2pod owns /etc/hosts: the generated script bind-mounts these lines
    into every container under `--no-hosts`, replacing the former pod-level
    `--add-host` set (which conflicts with `--no-hosts`). Merge/conflict rules
    are unchanged from the add-host era: alias/hostname names are fixed at
    127.0.0.1, `extra_hosts` is order-scoped, and a name landing on two
    addresses is refused. Alias lines are literal tokens; `extra_hosts` lines
    render via `Expand` so a `${VAR}` address stays live at run time.
    """
    merged: dict[str, str] = {}
    from_extra_hosts: set[str] = set()
    for host in hosts:
        merged[host] = "127.0.0.1"
    for name in order:
        svc = services[name]
        if "extra_hosts" not in svc:
            continue
        for host, addr in extra_host_entries(svc["extra_hosts"]):
            if merged.get(host, addr) != addr:
                msg = f"service {name!r}: conflicting host {host!r} ({merged[host]!r} vs {addr!r})"
                raise UnsupportedComposeError(msg)
            merged[host] = addr
            from_extra_hosts.add(host)
    tokens: list[Token] = [*_LOCALHOST_LINES]
    for host, addr in merged.items():
        line = f"{addr} {host}"
        tokens.append(Expand(value=line) if host in from_extra_hosts else line)
    return tokens


def pod_create_flags(services: dict[str, Any], order: list[str]) -> list[Token]:
    """Pod-create flag tokens aggregated across the closure `order`.

    dns/dns_search/dns_opt are unioned (dedup, first-seen order); sysctls are
    unioned by key. Name resolution no longer rides on `--add-host` (it conflicts
    with the `--no-hosts` the script now passes); it lives in the bind-mounted
    hosts file -- see `hosts_file_tokens`.
    """
    return _dns_flags(services, order) + _sysctl_flags(services, order)
