"""Validate a compose document against the supported subset."""

from typing import Any

from compose2pod import stores
from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.graph import depends_on, hostnames
from compose2pod.healthcheck import has_healthcheck, interval_seconds
from compose2pod.keys import SERVICE_KEYS, STRUCTURAL_KEYS, is_number, validate_map
from compose2pod.pod import uses_pod_options, validate_pod_options
from compose2pod.resources import validate_deploy


SUPPORTED_SERVICE_KEYS = set(SERVICE_KEYS) | STRUCTURAL_KEYS
IGNORED_SERVICE_KEYS = {"ports", "restart", "stdin_open", "tty", "stop_signal", "stop_grace_period", "profiles"}
SUPPORTED_HEALTHCHECK_KEYS = {"test", "interval", "timeout", "retries", "start_period"}
_HEALTHCHECK_SCALAR_KEYS = ("timeout", "retries", "start_period")
SUPPORTED_TOP_LEVEL_KEYS = {"services", "version", "name", "networks", "volumes", "secrets", "configs"}
DEPENDS_ON_CONDITIONS = {"service_started", "service_healthy", "service_completed_successfully"}


def _validate_service_healthcheck(name: str, svc: dict[str, Any]) -> None:
    """Check healthcheck is a mapping with supported keys and a parseable interval."""
    healthcheck = svc.get("healthcheck")
    if healthcheck is None:
        return
    if not isinstance(healthcheck, dict):
        msg = f"service {name!r}: healthcheck must be a mapping"
        raise UnsupportedComposeError(msg)
    for key in sorted(healthcheck):
        if key.startswith("x-"):
            continue
        if key not in SUPPORTED_HEALTHCHECK_KEYS:
            msg = f"service {name!r}: unsupported healthcheck key '{key}'"
            raise UnsupportedComposeError(msg)
    if "interval" in healthcheck:
        interval_seconds(healthcheck["interval"])
    for key in _HEALTHCHECK_SCALAR_KEYS:
        if key in healthcheck and healthcheck[key] is not None and not is_number(healthcheck[key]):
            msg = f"service {name!r}: healthcheck {key!r} must be a number or string"
            raise UnsupportedComposeError(msg)


def _validate_service_volumes(name: str, svc: dict[str, Any]) -> None:
    """Check volumes is a list of short bind-mount entries."""
    volumes = svc.get("volumes")
    if volumes is None:
        return
    if not isinstance(volumes, list):
        # A string would be iterated character-wise by the loop below.
        msg = f"service {name!r}: 'volumes' must be a list"
        raise UnsupportedComposeError(msg)
    for volume in volumes:
        if not isinstance(volume, str):
            msg = f"service {name!r}: only short volume syntax is supported"
            raise UnsupportedComposeError(msg)
        if ":" not in volume:
            # Anonymous volume: must be an absolute container path.
            if not volume.startswith("/"):
                msg = f"service {name!r}: anonymous volume '{volume}' must be an absolute path"
                raise UnsupportedComposeError(msg)
            continue
        # Colon-containing volume: bind mount (host path source) or named volume
        # (bare identifier source) — both are accepted; podman creates a named
        # volume implicitly on first reference.


def _validate_image(name: str, svc: dict[str, Any]) -> None:
    """Check the service has a usable image (image_for reads svc['image'] verbatim when there's no 'build')."""
    if "build" in svc:
        return
    image = svc.get("image")
    if image is None:
        msg = f"service {name!r}: must set 'image' or 'build'"
        raise UnsupportedComposeError(msg)
    if not isinstance(image, str):
        msg = f"service {name!r}: 'image' must be a string"
        raise UnsupportedComposeError(msg)


def _validate_entrypoint(name: str, svc: dict[str, Any]) -> None:
    """Check the structural entrypoint key's form (it is not a registry key)."""
    if "entrypoint" in svc and not isinstance(svc["entrypoint"], str | list):
        msg = f"service {name!r}: 'entrypoint' must be a string or list"
        raise UnsupportedComposeError(msg)


def _validate_command(name: str, svc: dict[str, Any]) -> None:
    """Check the structural command key's form (it is not a registry key)."""
    command = svc.get("command")
    if command is None:
        return
    if not isinstance(command, str | list):
        msg = f"service {name!r}: 'command' must be a string or list"
        raise UnsupportedComposeError(msg)


def _validate_tmpfs(name: str, svc: dict[str, Any]) -> None:
    """Check tmpfs is a string or list of strings (emit iterates it)."""
    tmpfs = svc.get("tmpfs")
    if tmpfs is None:
        return
    if not isinstance(tmpfs, str | list):
        msg = f"service {name!r}: tmpfs must be a string or list"
        raise UnsupportedComposeError(msg)
    if isinstance(tmpfs, list):
        for entry in tmpfs:
            if not isinstance(entry, str):
                msg = f"service {name!r}: tmpfs entry must be a string"
                raise UnsupportedComposeError(msg)


def _validate_environment(name: str, svc: dict[str, Any]) -> None:
    """Check environment is a list or mapping (a bare string would be walked as .items())."""
    if svc.get("environment") is not None:
        validate_map(name, "environment", svc["environment"])


def _validate_env_file(name: str, svc: dict[str, Any]) -> None:
    """Check env_file is a string or list of strings (emit iterates it)."""
    env_file = svc.get("env_file")
    if env_file is None:
        return
    if not isinstance(env_file, str | list):
        msg = f"service {name!r}: 'env_file' must be a string or list"
        raise UnsupportedComposeError(msg)
    if isinstance(env_file, list):
        for entry in env_file:
            if not isinstance(entry, str):
                msg = f"service {name!r}: 'env_file' entry must be a string"
                raise UnsupportedComposeError(msg)


def _validate_service(name: str, svc: Any) -> list[str]:  # noqa: ANN401 - Compose values are untyped
    """Validate one service; returns warnings, raises UnsupportedComposeError."""
    if not isinstance(svc, dict):
        msg = f"service {name!r} must be a mapping"
        raise UnsupportedComposeError(msg)
    warnings: list[str] = []
    for key in sorted(svc):
        if key.startswith("x-"):
            continue
        if key in IGNORED_SERVICE_KEYS:
            warnings.append(f"service {name!r}: ignoring '{key}'")
        elif key not in SUPPORTED_SERVICE_KEYS:
            msg = f"service {name!r}: unsupported key '{key}'"
            raise UnsupportedComposeError(msg)
    if isinstance(svc.get("entrypoint"), str) and svc.get("command") is not None:
        warnings.append(f"service {name!r}: string entrypoint runs via shell; 'command' is ignored")
    _validate_image(name, svc)
    _validate_service_healthcheck(name, svc)
    _validate_service_volumes(name, svc)
    _validate_entrypoint(name, svc)
    _validate_command(name, svc)
    _validate_tmpfs(name, svc)
    _validate_environment(name, svc)
    _validate_env_file(name, svc)
    validate_deploy(name, svc)
    validate_pod_options(name, svc)
    for key, spec in SERVICE_KEYS.items():
        if key in svc:
            spec.validate(name, key, svc[key])
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
    if "volumes" in compose:
        warnings.append("ignoring top-level 'volumes' (podman creates named volumes on first reference)")
    services = compose.get("services") or {}
    if not isinstance(services, dict):
        msg = f"'services' must be a mapping, got {type(services).__name__}"
        raise UnsupportedComposeError(msg)
    if not services:
        msg = "no services defined"
        raise UnsupportedComposeError(msg)
    for name, svc in services.items():
        warnings.extend(_validate_service(name, svc))
    hostnames(services)  # validate hostname/container_name/networks shapes at the gate
    _validate_depends_on(services)
    stores.validate(compose)
    if uses_pod_options(services):
        warnings.append(
            "dns/sysctls/extra_hosts apply pod-wide -- all containers in the pod share one "
            "/etc/resolv.conf, sysctl set, and /etc/hosts"
        )
    return warnings
