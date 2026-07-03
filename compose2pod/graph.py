"""Dependency graph: normalize depends_on, collect hostnames, compute startup order."""

from typing import Any, cast

from compose2pod.exceptions import UnsupportedComposeError


def depends_on(svc: dict[str, Any]) -> dict[str, str]:
    """Normalize dependencies of a service to a name -> condition mapping."""
    deps = svc.get("depends_on") or {}
    if isinstance(deps, list):
        return cast(dict[str, str], dict.fromkeys(deps, "service_started"))
    return {name: spec.get("condition", "service_started") for name, spec in deps.items()}


def hostnames(services: dict[str, Any]) -> list[str]:
    """All names other services may use to reach a service: names, then aliases."""
    names = list(services)
    for svc in services.values():
        networks = svc.get("networks")
        if isinstance(networks, dict):
            for network in networks.values():
                if isinstance(network, dict):
                    names.extend(network.get("aliases") or [])
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
