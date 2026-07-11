"""Extract compose deploy.resources limits/reservations into podman run flags."""

from typing import Any

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.keys import Token, _Expand, _is_number


# deploy.resources.limits.<field> -> (podman flag, conflicting legacy key)
_LIMITS = {"cpus": ("--cpus", "cpus"), "memory": ("--memory", "mem_limit"), "pids": ("--pids-limit", "pids_limit")}


def _check_number(name: str, field: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped
    if not _is_number(value):
        msg = f"service {name!r}: {field} must be a number or string"
        raise UnsupportedComposeError(msg)


def _validate_limits(name: str, svc: dict[str, Any], limits: Any) -> None:  # noqa: ANN401 - Compose values are untyped
    if limits is None:
        return
    if not isinstance(limits, dict):
        msg = f"service {name!r}: deploy.resources.limits must be a mapping"
        raise UnsupportedComposeError(msg)
    unknown = set(limits) - set(_LIMITS)
    if unknown:
        msg = f"service {name!r}: deploy.resources.limits: unsupported keys {sorted(unknown)}"
        raise UnsupportedComposeError(msg)
    for field, (_flag, legacy) in _LIMITS.items():
        if field in limits:
            _check_number(name, f"deploy.resources.limits.{field}", limits[field])
            if legacy in svc:
                msg = f"service {name!r}: {legacy!r} conflicts with deploy.resources.limits.{field}"
                raise UnsupportedComposeError(msg)


def _validate_reservations(name: str, svc: dict[str, Any], reservations: Any) -> None:  # noqa: ANN401 - Compose values are untyped
    if reservations is None:
        return
    if not isinstance(reservations, dict):
        msg = f"service {name!r}: deploy.resources.reservations must be a mapping"
        raise UnsupportedComposeError(msg)
    unknown = set(reservations) - {"cpus", "memory", "devices"}
    if unknown:
        msg = f"service {name!r}: deploy.resources.reservations: unsupported keys {sorted(unknown)}"
        raise UnsupportedComposeError(msg)
    for field in ("cpus", "devices"):
        if field in reservations:
            msg = f"service {name!r}: deploy.resources.reservations.{field} is not supported (no podman equivalent)"
            raise UnsupportedComposeError(msg)
    if "memory" in reservations:
        _check_number(name, "deploy.resources.reservations.memory", reservations["memory"])
        if "mem_reservation" in svc:
            msg = f"service {name!r}: 'mem_reservation' conflicts with deploy.resources.reservations.memory"
            raise UnsupportedComposeError(msg)


def validate_deploy(name: str, svc: dict[str, Any]) -> None:
    """Validate a service's deploy block: only deploy.resources, only mappable fields, no legacy conflicts."""
    deploy = svc.get("deploy")
    if deploy is None:
        return
    if not isinstance(deploy, dict):
        msg = f"service {name!r}: 'deploy' must be a mapping"
        raise UnsupportedComposeError(msg)
    unknown = set(deploy) - {"resources"}
    if unknown:
        msg = f"service {name!r}: deploy: only 'resources' is supported (got {sorted(unknown)})"
        raise UnsupportedComposeError(msg)
    resources = deploy.get("resources")
    if resources is None:
        return
    if not isinstance(resources, dict):
        msg = f"service {name!r}: deploy.resources must be a mapping"
        raise UnsupportedComposeError(msg)
    unknown = set(resources) - {"limits", "reservations"}
    if unknown:
        msg = f"service {name!r}: deploy.resources: unsupported keys {sorted(unknown)}"
        raise UnsupportedComposeError(msg)
    if "limits" in resources:
        _validate_limits(name, svc, resources["limits"])
    if "reservations" in resources:
        _validate_reservations(name, svc, resources["reservations"])


def deploy_resource_flags(svc: dict[str, Any]) -> list[Token]:
    """Emit podman resource flags from a service's deploy.resources block."""
    resources = (svc.get("deploy") or {}).get("resources") or {}
    limits = resources.get("limits") or {}
    reservations = resources.get("reservations") or {}
    tokens: list[Token] = []
    for field, (flag, _legacy) in _LIMITS.items():
        if field in limits:
            tokens += [flag, _Expand(value=str(limits[field]))]
    if "memory" in reservations:
        tokens += ["--memory-reservation", _Expand(value=str(reservations["memory"]))]
    return tokens
