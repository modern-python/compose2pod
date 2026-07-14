"""Validate a compose document against the supported subset."""

from typing import Any

from compose2pod import stores
from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.graph import depends_on, hostnames
from compose2pod.healthcheck import has_healthcheck, health_cmd, interval_seconds
from compose2pod.keys import SERVICE_KEYS, STRUCTURAL_KEYS, is_number, require_string_keys, validate_map
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
    # Non-string healthcheck keys are already rejected by validate()'s
    # document-wide _require_string_keys_deep sweep, which runs before this
    # function is ever reached.
    for key in sorted(healthcheck):
        if key.startswith("x-"):
            continue
        if key not in SUPPORTED_HEALTHCHECK_KEYS:
            msg = f"service {name!r}: unsupported healthcheck key '{key}'"
            raise UnsupportedComposeError(msg)
    if "interval" in healthcheck:
        interval_seconds(healthcheck["interval"])
    if "test" in healthcheck:
        # health_cmd's return value is unused here -- it raises on any
        # unsupported shape, which is all this gate needs (emit.py calls it
        # again for the actual --health-cmd value at emit time).
        health_cmd(healthcheck["test"])
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


def _validate_argv_list(name: str, key: str, value: list[Any]) -> None:
    """Check every command/entrypoint list element is a string.

    Without this, a list-of-mapping form (e.g. `command: [{run: tests}]` --
    the same list/map YAML slip as `environment`) was silently accepted and
    str()'d into a single mangled podman-run argv token.
    """
    for token in value:
        if not isinstance(token, str):
            msg = f"service {name!r}: '{key}' entries must be strings"
            raise UnsupportedComposeError(msg)


def _validate_entrypoint(name: str, svc: dict[str, Any]) -> None:
    """Check the structural entrypoint key's form (it is not a registry key)."""
    if "entrypoint" not in svc:
        return
    entrypoint = svc["entrypoint"]
    if not isinstance(entrypoint, str | list):
        msg = f"service {name!r}: 'entrypoint' must be a string or list"
        raise UnsupportedComposeError(msg)
    if isinstance(entrypoint, list):
        _validate_argv_list(name, "entrypoint", entrypoint)


def _validate_command(name: str, svc: dict[str, Any]) -> None:
    """Check the structural command key's form (it is not a registry key)."""
    command = svc.get("command")
    if command is None:
        return
    if not isinstance(command, str | list):
        msg = f"service {name!r}: 'command' must be a string or list"
        raise UnsupportedComposeError(msg)
    if isinstance(command, list):
        _validate_argv_list(name, "command", command)


def _validate_string_or_string_list(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    """Check a key is a string or list of strings (emit iterates it), shared by tmpfs and env_file."""
    if value is None:
        return
    if not isinstance(value, str | list):
        msg = f"service {name!r}: '{key}' must be a string or list"
        raise UnsupportedComposeError(msg)
    if isinstance(value, list):
        for entry in value:
            if not isinstance(entry, str):
                msg = f"service {name!r}: '{key}' entry must be a string"
                raise UnsupportedComposeError(msg)


def _validate_tmpfs(name: str, svc: dict[str, Any]) -> None:
    """Check tmpfs is a string or list of strings (emit iterates it)."""
    _validate_string_or_string_list(name, "tmpfs", svc.get("tmpfs"))


def _validate_environment(name: str, svc: dict[str, Any]) -> None:
    """Check environment is a list or mapping (a bare string would be walked as .items())."""
    if svc.get("environment") is not None:
        validate_map(name, "environment", svc["environment"])


def _validate_env_file(name: str, svc: dict[str, Any]) -> None:
    """Check env_file is a string or list of strings (emit iterates it)."""
    _validate_string_or_string_list(name, "env_file", svc.get("env_file"))


def _validate_service(name: str, svc: Any) -> list[str]:  # noqa: ANN401 - Compose values are untyped
    """Validate one service; returns warnings, raises UnsupportedComposeError."""
    if not isinstance(svc, dict):
        msg = f"service {name!r} must be a mapping"
        raise UnsupportedComposeError(msg)
    # Non-string service-body keys are already rejected by validate()'s
    # document-wide _require_string_keys_deep sweep, which runs before this
    # function is ever reached.
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


def _require_string_keys_deep(where: str, node: Any) -> None:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    """Require every mapping key, at every depth of `node`, to be a string.

    PyYAML routinely produces a non-string key: a bare `3:` parses as an int,
    and under YAML 1.1 a bare `on:`/`off:`/`yes:`/`no:` parses as a bool.
    Two distinct downstream consumer classes assume `str` keys and break
    otherwise:

    - Mapping-key readers that crash raw: `sorted()`, `str.startswith`, the
      secret/config name regex.
    - Mapping-key consumers that don't crash but f-string-interpolate the key
      straight into a flag value, silently leaking the Python repr of a bool
      or int into the emitted script (`keys.key_value_pairs` -- environment/
      labels/annotations, `keys.extra_host_pairs`, `keys._ulimit_args`,
      `pod._sysctl_pairs`). This class is corruption, not a crash, and is the
      one the old hand-placed `require_string_keys` call sites never covered
      -- they were only wired into mappings whose keys get sorted or
      startswith'd, never the ones whose keys get f-string'd into a flag.

    Walking the whole document once, recursively, from `validate()`'s entry
    point closes both classes uniformly regardless of which mapping --
    existing or future -- a non-string key turns up in, rather than trusting
    every new structural key's validator to remember to call
    `require_string_keys` by hand.

    `x-`-prefixed keys' *values* are not recursed into: Compose extension
    fields legitimately hold arbitrary user payloads (e.g. anchor sources
    reused via YAML merge keys), and compose2pod accepts and ignores their
    contents by design. The `x-` key itself is still checked, trivially: it
    is a string by construction (its own name has to look like `x-...`).

    Rejecting a non-string key is a deliberate divergence from Docker for
    map-typed *keys* specifically (Docker accepts `environment: {3306: db}`):
    Compose is parsed as YAML 1.2, where a bare `on`/`off`/`3306` stays a
    string, so Docker never observes a non-string key at all. Normalizing
    Python's `bool`/`int` back to a string here would not reproduce that --
    `True` has no single correct string form (`"on"`? `"true"`? `"True"`?)
    the way a boolean *value* does (see `keys._render_scalar`, which mirrors
    Docker's own `true`/`false` value normalization). A non-string key is a
    YAML-1.1 accident, not intentional Compose; anyone who means the literal
    string `on` writes `"on"`.
    """
    if isinstance(node, dict):
        require_string_keys(where, node)
        for key, value in node.items():
            if key.startswith("x-"):
                continue
            _require_string_keys_deep(f"{where}.{key}", value)
    elif isinstance(node, list):
        for item in node:
            _require_string_keys_deep(where, item)


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
    # Runs first, ahead of every other check: every later check that reads a
    # mapping's keys directly (sorted(), .startswith()) or f-string-
    # interpolates one into a flag value can assume every key in the document
    # is a string from this point on.
    _require_string_keys_deep("compose document", compose)
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
