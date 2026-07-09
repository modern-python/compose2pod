"""Validate a compose document against the supported subset."""

from typing import Any

# PULL_POLICY_MAP is shared vocabulary: validation checks membership, emission maps values.
# Deliberate one-way import (emit.py never imports parsing.py), so no cycle.
from compose2pod.emit import PULL_POLICY_MAP
from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.graph import depends_on
from compose2pod.healthcheck import has_healthcheck


SUPPORTED_SERVICE_KEYS = {
    "image",
    "build",
    "command",
    "entrypoint",
    "environment",
    "env_file",
    "volumes",
    "healthcheck",
    "depends_on",
    "networks",
    "hostname",
    "container_name",
    "tmpfs",
    "user",
    "working_dir",
    "group_add",
    "labels",
    "read_only",
    "init",
    "privileged",
    "cap_add",
    "cap_drop",
    "security_opt",
    "platform",
    "devices",
    "annotations",
    "extra_hosts",
    "pull_policy",
    "ulimits",
}
IGNORED_SERVICE_KEYS = {"ports", "restart", "stdin_open", "tty", "stop_signal", "stop_grace_period"}
SUPPORTED_HEALTHCHECK_KEYS = {"test", "interval", "timeout", "retries", "start_period"}
SUPPORTED_TOP_LEVEL_KEYS = {"services", "version", "name", "networks", "volumes"}
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
        # Colon-containing volume: bind mount (host path source) or named volume
        # (bare identifier source) — both are accepted; podman creates a named
        # volume implicitly on first reference.


def _validate_service_forms(name: str, svc: dict[str, Any]) -> None:
    """Check the process/identity keys use their allowed Compose forms."""
    for key in ("user", "working_dir", "platform"):
        if key in svc and not isinstance(svc[key], str):
            msg = f"service {name!r}: '{key}' must be a string"
            raise UnsupportedComposeError(msg)
    for key in ("group_add", "cap_add", "cap_drop", "security_opt", "devices"):
        if key in svc and not isinstance(svc[key], list):
            msg = f"service {name!r}: '{key}' must be a list"
            raise UnsupportedComposeError(msg)
    for key in ("labels", "annotations", "extra_hosts"):
        if key in svc and not isinstance(svc[key], list | dict):
            msg = f"service {name!r}: '{key}' must be a list or mapping"
            raise UnsupportedComposeError(msg)
    if "entrypoint" in svc and not isinstance(svc["entrypoint"], str | list):
        msg = f"service {name!r}: 'entrypoint' must be a string or list"
        raise UnsupportedComposeError(msg)
    for key in ("read_only", "init", "privileged"):
        if key in svc and not isinstance(svc[key], bool):
            msg = f"service {name!r}: '{key}' must be a boolean"
            raise UnsupportedComposeError(msg)


def _validate_pull_policy(name: str, svc: dict[str, Any]) -> None:
    """Check pull_policy is a supported enum value (mapped to podman's --pull)."""
    policy = svc.get("pull_policy")
    if policy is not None and (not isinstance(policy, str) or policy not in PULL_POLICY_MAP):
        allowed = "/".join(PULL_POLICY_MAP)
        msg = f"service {name!r}: unsupported pull_policy {policy!r} (use {allowed})"
        raise UnsupportedComposeError(msg)


def _validate_ulimits(name: str, svc: dict[str, Any]) -> None:
    """Check ulimits maps each name to an int/str scalar or a {soft, hard} mapping."""
    ulimits = svc.get("ulimits")
    if ulimits is None:
        return
    if not isinstance(ulimits, dict):
        msg = f"service {name!r}: 'ulimits' must be a mapping"
        raise UnsupportedComposeError(msg)
    for limit, spec in ulimits.items():
        if isinstance(spec, dict):
            if set(spec) != {"soft", "hard"}:
                msg = f"service {name!r}: ulimit {limit!r} mapping must have exactly 'soft' and 'hard'"
                raise UnsupportedComposeError(msg)
        elif not isinstance(spec, int | str):
            msg = f"service {name!r}: ulimit {limit!r} must be an int or a soft/hard mapping"
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
    if isinstance(svc.get("entrypoint"), str) and svc.get("command") is not None:
        warnings.append(f"service {name!r}: string entrypoint runs via shell; 'command' is ignored")
    _validate_service_healthcheck(name, svc)
    _validate_service_volumes(name, svc)
    _validate_service_forms(name, svc)
    _validate_pull_policy(name, svc)
    _validate_ulimits(name, svc)
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
    if not services:
        msg = "no services defined"
        raise UnsupportedComposeError(msg)
    for name, svc in services.items():
        warnings.extend(_validate_service(name, svc))
    _validate_depends_on(services)
    return warnings
