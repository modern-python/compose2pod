"""Validate a compose document against the supported subset."""

from typing import Any

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.graph import depends_on
from compose2pod.healthcheck import has_healthcheck


SUPPORTED_SERVICE_KEYS = {
    "image",
    "build",
    "command",
    "environment",
    "env_file",
    "volumes",
    "healthcheck",
    "depends_on",
    "networks",
    "hostname",
}
IGNORED_SERVICE_KEYS = {"ports", "restart", "stdin_open", "tty"}
SUPPORTED_HEALTHCHECK_KEYS = {"test", "interval", "timeout", "retries", "start_period"}
SUPPORTED_TOP_LEVEL_KEYS = {"services", "version", "name", "networks"}
DEPENDS_ON_CONDITIONS = {"service_started", "service_healthy", "service_completed_successfully"}


def _validate_service_healthcheck(name: str, svc: dict[str, Any]) -> None:
    """Check healthcheck keys against the supported subset, skipping 'x-' extension keys."""
    for key in sorted(svc.get("healthcheck") or {}):
        if key.startswith("x-"):
            continue
        if key not in SUPPORTED_HEALTHCHECK_KEYS:
            msg = f"service {name!r}: unsupported healthcheck key '{key}'"
            raise UnsupportedComposeError(msg)


def _validate_service_volumes(name: str, svc: dict[str, Any]) -> None:
    """Check volumes use short bind-mount syntax only."""
    for volume in svc.get("volumes") or []:
        if not isinstance(volume, str):
            msg = f"service {name!r}: only short volume syntax is supported"
            raise UnsupportedComposeError(msg)
        if ":" not in volume:
            # Anonymous volume: must be an absolute container path.
            if not volume.startswith("/"):
                msg = f"service {name!r}: anonymous volume '{volume}' must be an absolute path"
                raise UnsupportedComposeError(msg)
            continue
        source = volume.split(":", 1)[0]
        if not source.startswith((".", "/")):
            msg = f"service {name!r}: named volume '{source}' is not supported (bind mounts only)"
            raise UnsupportedComposeError(msg)


def _validate_service(name: str, svc: dict[str, Any]) -> list[str]:
    """Validate one service; returns warnings, raises UnsupportedComposeError."""
    warnings: list[str] = []
    for key in sorted(svc):
        if key.startswith("x-"):
            continue
        if key in IGNORED_SERVICE_KEYS:
            warnings.append(f"service {name!r}: ignoring '{key}'")
        elif key not in SUPPORTED_SERVICE_KEYS:
            msg = f"service {name!r}: unsupported key '{key}'"
            raise UnsupportedComposeError(msg)
    _validate_service_healthcheck(name, svc)
    _validate_service_volumes(name, svc)
    return warnings


def _validate_depends_on(services: dict[str, Any]) -> None:
    """Cross-service depends_on checks: known conditions, service_healthy needs a healthcheck."""
    for name, svc in services.items():
        for dep, condition in depends_on(svc).items():
            if condition not in DEPENDS_ON_CONDITIONS:
                msg = f"service {name!r}: depends_on {dep!r} has unsupported condition {condition!r}"
                raise UnsupportedComposeError(msg)
            if condition == "service_healthy" and dep in services and not has_healthcheck(services[dep]):
                msg = f"service {name!r}: depends on {dep!r} (service_healthy) but {dep!r} has no healthcheck"
                raise UnsupportedComposeError(msg)


def validate(compose: dict[str, Any]) -> list[str]:
    """Check the compose document against the supported subset.

    Returns human-readable warnings for ignored constructs.
    Raises UnsupportedComposeError for anything that would change behavior silently.
    """
    if not isinstance(compose, dict):
        msg = f"compose document must be a mapping, got {type(compose).__name__}"
        raise UnsupportedComposeError(msg)
    warnings: list[str] = []
    unknown_top = {k for k in compose if k not in SUPPORTED_TOP_LEVEL_KEYS and not k.startswith("x-")}
    if unknown_top:
        msg = f"unsupported top-level keys: {sorted(unknown_top)}"
        raise UnsupportedComposeError(msg)
    if "networks" in compose:
        warnings.append("ignoring top-level 'networks' (all services share the pod namespace)")
    services = compose.get("services") or {}
    if not services:
        msg = "no services defined"
        raise UnsupportedComposeError(msg)
    for name, svc in services.items():
        warnings.extend(_validate_service(name, svc))
    _validate_depends_on(services)
    return warnings
