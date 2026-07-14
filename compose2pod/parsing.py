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
# The only service keys Docker tolerates an explicit null on, where it means
# "not specified" (measured against `docker compose config`). Every other key
# with a bare `key:` is refused -- see `_reject_null_values`.
NULL_ALLOWED_KEYS = {"command", "entrypoint", "deploy"}
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
    # Redundant with validate()'s _sweep_document when reached through
    # validate() (the only caller today), but this function has its own
    # contract as a module entry point -- belt-and-braces, not load-bearing
    # only by luck of the current call graph.
    require_string_keys(f"service {name!r}: healthcheck", healthcheck)
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
    entrypoint = svc.get("entrypoint")
    if entrypoint is None:
        # An explicit null means "not specified", as it does for its sibling
        # `command` and as Docker accepts for both. `entrypoint_tokens` emits
        # nothing for it.
        return
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


def _reject_null_values(name: str, svc: dict[str, Any]) -> None:
    """Refuse an explicitly-null service value, everywhere Docker refuses one.

    Measured against `docker compose config`: it tolerates an explicit null on
    exactly `command`, `entrypoint` and `deploy` -- where a null means "not
    specified" -- and refuses one on every other service key, because a bare
    `environment:` is almost always a deleted-contents mistake rather than an
    intent. Emitting nothing for it silently would be exactly the dropped
    behavior this gate exists to catch.

    One rule rather than a per-key decision, so the policy cannot drift back
    into an enumeration. `x-` keys are arbitrary user payload and are exempt.
    """
    for key, value in svc.items():
        if value is None and key not in NULL_ALLOWED_KEYS and not key.startswith("x-"):
            msg = f"service {name!r}: '{key}' must not be null (Compose refuses a bare '{key}:')"
            raise UnsupportedComposeError(msg)


def _validate_service(name: str, svc: Any) -> list[str]:  # noqa: ANN401 - Compose values are untyped
    """Validate one service; returns warnings, raises UnsupportedComposeError."""
    if not isinstance(svc, dict):
        msg = f"service {name!r} must be a mapping"
        raise UnsupportedComposeError(msg)
    # Redundant with validate()'s _sweep_document when reached through
    # validate() (the only caller today), but this function has its own
    # contract as a module entry point -- belt-and-braces, not load-bearing
    # only by luck of the current call graph.
    require_string_keys(f"service {name!r}", svc)
    _reject_null_values(name, svc)
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
      labels/annotations, `keys.extra_host_entries`, `keys._ulimit_args`,
      `pod._sysctl_pairs`). This class is corruption, not a crash, and is the
      one the old hand-placed `require_string_keys` call sites never covered
      -- they were only wired into mappings whose keys get sorted or
      startswith'd, never the ones whose keys get f-string'd into a flag.

    `x-`-prefixed keys' *values* are not recursed into: Compose extension
    fields legitimately hold arbitrary user payloads (e.g. anchor sources
    reused via YAML merge keys), and compose2pod accepts and ignores their
    contents by design. The `x-` key itself is still checked, trivially: it
    is a string by construction (its own name has to look like `x-...`). This
    skip is only ever correct for a key that plays the role of "extension
    field name" in the node being walked -- callers (`_sweep_document`/
    `_sweep_service`/`_sweep_identifier_map`) are responsible for never
    handing this function a mapping whose keys are *identifiers* (a service
    name, a store name, a dependency name, a network name, a ulimit name)
    rather than content keys, since an identifier starting with `x-` is not
    an extension field. `_sweep_document` sweeps `services`/`secrets`/
    `configs` names this way already; `_sweep_identifier_map` does the same
    for the identifier-keyed *service* keys (`depends_on`, `networks`,
    `ulimits`) before handing each identifier's own content to this
    function.

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


_IDENTIFIER_KEYED_SERVICE_KEYS = {"depends_on", "networks", "ulimits"}


def _sweep_identifier_map(where: str, key: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped
    """Sweep a service key whose mapping form is keyed by another entity's *identifier*.

    `depends_on` (dependency = another service's name), `networks` (a
    network's name), and `ulimits` (a resource-limit category's name) all
    key their mapping form by an identifier, not a content key -- unlike
    `environment`/`labels`/`sysctls`/etc., where the map key itself *is*
    content. `_require_string_keys_deep`'s blanket `x-` skip is only correct
    for content keys (see its docstring): fed an identifier map directly, it
    would treat a dependency/network/ulimit literally named `x-foo` as an
    extension field and skip checking its value, the same conflation that
    made a service named `x-web` fall through the old sweep. So the
    identifiers here are checked with `require_string_keys` (no `x-` skip --
    an identifier starting with `x-` is still a real identifier), and only
    each identifier's own *value* -- ordinary content from that point on --
    is handed to the `x-`-skipping deep walk.

    List-form (`depends_on: [...]`/`networks: [...]`) and absent/null values
    are not identifier-keyed at all; they fall through to the plain deep
    walk unchanged.
    """
    full = f"{where}.{key}"
    if not isinstance(value, dict):
        _require_string_keys_deep(full, value)
        return
    require_string_keys(full, value)
    for identifier, content in value.items():
        _require_string_keys_deep(f"{full}.{identifier}", content)


def _sweep_service(name: str, svc: dict[str, Any]) -> None:
    """Require every mapping key in one service body to be a string, at every depth compose2pod reads.

    The service's own top-level keys are always checked (`require_string_keys`
    below), regardless of what the service's *name* looks like -- this
    function is only ever called with the real body of a real service,
    including one literally named `x-web` (see `_sweep_document`).

    Two of the service's own top-level keys are skipped rather than swept,
    because compose2pod never reads their contents: `build` (accepted, but
    `image_for` never reads its contents -- see
    architecture/supported-subset.md) and any `x-`-prefixed key (an
    extension field whose value is arbitrary user payload, by design, same
    as everywhere else `x-` is skipped). `depends_on`/`networks`/`ulimits`
    are identifier-keyed and go through `_sweep_identifier_map` instead of
    the plain deep walk (see there). Everything else is swept recursively,
    since every other service key's value compose2pod either reads
    structurally or emits into the generated script.
    """
    where = f"service {name!r}"
    require_string_keys(where, svc)
    for key, value in svc.items():
        if key == "build" or key.startswith("x-"):
            continue
        if key in _IDENTIFIER_KEYED_SERVICE_KEYS:
            _sweep_identifier_map(where, key, value)
            continue
        _require_string_keys_deep(f"{where}.{key}", value)


def _sweep_document(compose: dict[str, Any]) -> None:
    """Require every mapping key to be a string, but only in regions `validate()` actually reads.

    Every later check that reads a mapping's keys directly (`sorted()`,
    `.startswith()`) or f-string-interpolates one into a flag value can
    assume every key it sees is a string -- provided it only ever reads a
    region swept here.

    Swept:

    - The top-level document's own keys.
    - The `services` mapping's own keys (service *names*): always, and
      regardless of what a name looks like. `validate()` iterates
      `services.items()` with no `x-` filter, so a service literally named
      `x-web` is a real service, not an extension field -- conflating a
      NAME with a content key is exactly the bug this function exists to
      not repeat (see `_sweep_service`).
    - Each service's body (`_sweep_service`), except `build`'s contents and
      the service's own `x-`-prefixed keys -- neither is ever read.
    - Each top-level `secrets`/`configs` definition's body: read by
      `stores.py`. Swept by name (like `services`, not by the generic
      `x-`-skipping walk), for the same reason -- `stores._validate_def`
      accepts a store name matching `[a-zA-Z0-9][a-zA-Z0-9_.-]*`, which
      does not exclude one starting with `x-`, so a store's *name* must not
      be conflated with a content key either.

    Skipped, because compose2pod never reads or emits from them, so a
    non-string key there can never reach the generated script: `x-` blocks
    (top-level and per-service), `build`'s contents, and the ignored
    top-level `networks`/`volumes` blocks (accepted, but never read --
    see architecture/supported-subset.md).
    """
    require_string_keys("compose document", compose)
    services = compose.get("services")
    if isinstance(services, dict):
        require_string_keys("compose document.services", services)
        for name, svc in services.items():
            if isinstance(svc, dict):
                _sweep_service(name, svc)
    for top_key in ("secrets", "configs"):
        defs = compose.get(top_key)
        if isinstance(defs, dict):
            require_string_keys(f"compose document.{top_key}", defs)
            for def_name, definition in defs.items():
                if isinstance(definition, dict):
                    _require_string_keys_deep(f"compose document.{top_key}.{def_name}", definition)


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
    _sweep_document(compose)
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
