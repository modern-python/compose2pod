"""Dependency graph: normalize depends_on, collect hostnames, compute startup order."""

import re
from typing import Any, cast

from compose2pod.exceptions import UnsupportedComposeError


def depends_on(svc: dict[str, Any]) -> dict[str, str]:
    """Normalize dependencies of a service to a name -> condition mapping."""
    deps = svc.get("depends_on")
    if deps is None:
        # Explicitly absent, not merely falsy. `or {}` treated `depends_on: ""`
        # as "no dependencies" and skipped every shape check below -- a document
        # `docker compose config` refuses. An empty list/mapping still yields {},
        # via the branches below, exactly as before.
        return {}
    if isinstance(deps, list):
        for dep in deps:
            if not isinstance(dep, str):
                msg = f"depends_on entry {dep!r} must be a string"
                raise UnsupportedComposeError(msg)
        return cast(dict[str, str], dict.fromkeys(deps, "service_started"))
    if not isinstance(deps, dict):
        msg = "'depends_on' must be a list or mapping"
        raise UnsupportedComposeError(msg)
    result: dict[str, str] = {}
    for dep, spec in deps.items():
        if not isinstance(spec, dict):
            msg = f"depends_on entry {dep!r} must be a mapping"
            raise UnsupportedComposeError(msg)
        condition = spec.get("condition", "service_started")
        if not isinstance(condition, str):
            # Callers (parsing._validate_depends_on) test membership in a
            # `set` of known condition strings -- `x in a_set` hashes `x`,
            # so an unhashable condition (a dict or list) would otherwise
            # crash raw with `TypeError: unhashable type` instead of failing
            # clean. Checked here, not there: this function already owns
            # every other depends_on shape check (list vs mapping, spec must
            # be a mapping), so a bad condition type belongs with them, and
            # every caller of `depends_on` -- not just validate() -- gets the
            # same protection.
            msg = f"depends_on entry {dep!r}: condition must be a string"
            raise UnsupportedComposeError(msg)
        result[dep] = condition
    return result


# Docker validates container_name against this exact pattern (measured:
# `container_name '' does not match pattern '[a-zA-Z0-9][a-zA-Z0-9_.-]+'`).
# It is a *search*, not a fullmatch -- JSON-schema `pattern` semantics, which is
# what Compose uses. `hostname` carries no such rule: Docker accepts an empty one.
_CONTAINER_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]+")


def _validated_name(name: str, key: str, svc: dict[str, Any]) -> str | None:
    """Check one of svc's hostname/container_name keys and return its value, if set."""
    value = svc.get(key)
    if value is not None and not isinstance(value, str):
        msg = f"service {name!r}: {key} must be a string"
        raise UnsupportedComposeError(msg)
    # `key in svc` guards presence: an *absent* container_name is fine (value
    # is None the same as an absent key), but an *explicit* one -- including
    # an explicit "" -- must match Docker's pattern. A null is already refused
    # upstream (parsing._reject_null_values), so a present key's value is
    # always a string here.
    if key == "container_name" and key in svc and not _CONTAINER_NAME.search(value or ""):
        msg = f"service {name!r}: container_name {value!r} is not a valid container name"
        raise UnsupportedComposeError(msg)
    return value


def _host_names(name: str, svc: dict[str, Any]) -> list[str]:
    """Names one service is reachable by: hostname, container_name, and network aliases."""
    result: list[str] = [value for key in ("hostname", "container_name") if (value := _validated_name(name, key, svc))]
    networks = svc.get("networks")
    if networks is not None and not isinstance(networks, list | dict):
        msg = f"service {name!r}: networks must be a list or mapping"
        raise UnsupportedComposeError(msg)
    if isinstance(networks, dict):
        for network in networks.values():
            if isinstance(network, dict):
                aliases = network.get("aliases")
                if aliases is None:
                    continue
                # A string would be iterated character-wise by extend() below.
                if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
                    msg = f"service {name!r}: aliases must be a list of strings"
                    raise UnsupportedComposeError(msg)
                result.extend(aliases)
    return result


def hostnames(services: dict[str, Any]) -> list[str]:
    """All names other services may use to reach a service: names, hostnames/container names, then aliases."""
    names = list(services)
    for name, svc in services.items():
        names.extend(_host_names(name, svc))
    return names


def startup_order(services: dict[str, Any], target: str) -> list[str]:
    """Dependency closure of target in start order (dependencies first, target last)."""
    if target not in services:
        msg = f"target service '{target}' not found"
        raise UnsupportedComposeError(msg)
    order: list[str] = []
    state: dict[str, str] = {}

    def visit(name: str) -> None:
        if state.get(name) == "visiting":
            msg = f"dependency cycle involving '{name}'"
            raise UnsupportedComposeError(msg)
        if state.get(name) == "done":
            return
        if name not in services:
            msg = f"unknown dependency '{name}'"
            raise UnsupportedComposeError(msg)
        state[name] = "visiting"
        for dep in depends_on(services[name]):
            visit(dep)
        state[name] = "done"
        order.append(name)

    visit(target)
    return order
