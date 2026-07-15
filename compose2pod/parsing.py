"""Validate a compose document against the supported subset."""

from collections.abc import Callable
from typing import Any

from compose2pod import stores, values
from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.graph import depends_on, hostnames
from compose2pod.healthcheck import has_healthcheck, health_cmd, interval_seconds
from compose2pod.keys import SERVICE_KEYS, STRUCTURAL_KEYS, require_string_keys, validate_map, validate_ulimits
from compose2pod.pod import uses_pod_options, validate_pod_options
from compose2pod.resources import validate_deploy


SUPPORTED_SERVICE_KEYS = set(SERVICE_KEYS) | STRUCTURAL_KEYS


def _validate_bool(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - untyped YAML/JSON
    if not isinstance(value, bool):
        msg = f"service {name!r}: {key!r} must be a boolean"
        raise UnsupportedComposeError(msg)


def _validate_string_list(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - untyped YAML/JSON
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        msg = f"service {name!r}: {key!r} must be a list of strings"
        raise UnsupportedComposeError(msg)


# Accepted but never emitted -- meaningless inside a single shared-namespace pod
# (see architecture/supported-subset.md). Ignored at *emit* is not unchecked at
# the *gate*: a key compose2pod does not use is still one Docker validates, and a
# document carrying a malformed one is a document Docker will not run. Each value
# is the shape validator; the content rules are Docker's own, measured -- it
# accepts `restart: banana` and any `stop_signal` string, so neither is enumerated
# here, or we would refuse a file it runs.
IGNORED_SERVICE_KEYS: dict[str, Callable[[str, str, Any], None]] = {
    "ports": values.validate_ports,
    "restart": values.validate_string,
    "stdin_open": _validate_bool,
    "tty": _validate_bool,
    "stop_signal": values.validate_string,
    "stop_grace_period": values.validate_duration,
    "profiles": _validate_string_list,
}
# The only service keys Docker tolerates an explicit null on, where it means
# "not specified" (measured against `docker compose config`). Every other key
# with a bare `key:` is refused -- see `_reject_null_values`.
NULL_ALLOWED_KEYS = {"command", "entrypoint", "deploy"}
SUPPORTED_HEALTHCHECK_KEYS = {"test", "interval", "timeout", "retries", "start_period"}
_HEALTHCHECK_SCALAR_KEYS = ("timeout", "retries", "start_period")
# timeout/start_period are a Go duration string (values.validate_duration); retries is
# an int64 count (values.validate_count) -- measured against `docker compose config`
# v5.1.2. Neither is the "any number or string" shape `is_number` used to accept.
_HEALTHCHECK_SCALAR_VALIDATORS: dict[str, Callable[[str, str, Any], None]] = {
    "timeout": values.validate_duration,
    "start_period": values.validate_duration,
    "retries": values.validate_count,
}
SUPPORTED_TOP_LEVEL_KEYS = {"services", "version", "name", "networks", "volumes", "secrets", "configs"}
DEPENDS_ON_CONDITIONS = {"service_started", "service_healthy", "service_completed_successfully"}


def _reject_null_healthcheck_values(name: str, healthcheck: dict[str, Any]) -> None:
    """Refuse a null in any healthcheck position -- `docker compose config` refuses each.

    A bare `test:` would silently drop the healthcheck entirely. A bare `timeout:`
    drops nothing by itself, but the document carrying it is one `docker compose`
    will not run, and compose2pod is a drop-in replacement for it -- so emitting a
    script for such a file would turn a hard error into a false green. An *omitted*
    key is a different thing and stays fine: podman's default applies.
    """
    for key in ("test", "interval", *_HEALTHCHECK_SCALAR_KEYS):
        if key in healthcheck and healthcheck[key] is None:
            msg = f"service {name!r}: healthcheck {key!r} must not be null"
            raise UnsupportedComposeError(msg)


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
    _reject_null_healthcheck_values(name, healthcheck)
    if "interval" in healthcheck:
        interval_seconds(healthcheck["interval"])
    if "test" in healthcheck:
        # health_cmd's return value is unused here -- it raises on any
        # unsupported shape, which is all this gate needs (emit.py calls it
        # again for the actual --health-cmd value at emit time).
        health_cmd(healthcheck["test"])
    for key in _HEALTHCHECK_SCALAR_KEYS:
        if key in healthcheck:
            _HEALTHCHECK_SCALAR_VALIDATORS[key](name, key, healthcheck[key])


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
    if image is None or image == "":
        msg = f"service {name!r}: must set 'image' or 'build'"
        raise UnsupportedComposeError(msg)
    if not isinstance(image, str):
        msg = f"service {name!r}: 'image' must be a string"
        raise UnsupportedComposeError(msg)


def _validate_build_shm_size(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - untyped YAML/JSON
    # allow_fractional=False: measured against `docker compose config` v5.1.2 --
    # build.shm_size refuses ANY fractional float (whole or not), unlike the
    # top-level `shm_size` service key (which keeps validate_size's default,
    # allow_fractional=True). The same divergence mem_reservation already has
    # from mem_limit.
    values.validate_size(name, key, value, allow_fractional=False)


def _validate_build_bool(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - untyped YAML/JSON
    """Strict bool for build's own no_cache/pull/privileged -- `${VAR}` passes through.

    A quoted `"true"` is refused, unlike the top-level six boolean keys' deferred
    limitation note in `planning/deferred.md` (this is the same limitation, just
    on a different set of keys, kept consistent on purpose). `${VAR}` is
    different: measured against `docker compose config` v5.1.2, `no_cache:
    ${MYVAR}` is accepted when `MYVAR=true` and refused when unset or non-boolean
    -- genuinely host-state-dependent, so it is not this refusal's business
    (`values.has_variable`, the same carve-out every other grammar in `values.py`
    applies).
    """
    if values.has_variable(value):
        return
    if not isinstance(value, bool):
        msg = f"service {name!r}: build {key!r} must be a boolean"
        raise UnsupportedComposeError(msg)


def _validate_build_map(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - untyped YAML/JSON
    """List-or-map value shared by args/labels/ssh -- reuses `keys.validate_map`'s shape rules.

    Measured against `docker compose config` v5.1.2: `ssh` is not the plain
    list-of-strings shape a first pass might assume (Docker's own docs show
    `default | key=/path`) -- `build.ssh: {default: /path}` is accepted too,
    identically to `args`/`labels`. All three share one grammar: a list of
    'KEY[=value]' strings (a bare 'KEY' is fine, meaning a null value), or a
    mapping with scalar-or-null values.

    `require_string_keys` is checked here, not left to `validate_map` alone:
    `parsing._sweep_service` skips build's contents (see
    `TestSweepSkipsUnreadRegions` in `tests/test_parsing.py`), so nothing
    upstream has guaranteed this mapping's keys are strings the way it has for
    the top-level `labels`/`environment`. Measured: Docker refuses a
    non-string key here too ('non-string key in services.app.build.args').
    """
    if isinstance(value, dict):
        require_string_keys(f"service {name!r}: build {key!r}", value)
    validate_map(name, key, value)


def _validate_build_additional_contexts(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - untyped YAML/JSON
    """List-or-map value, but stricter than args/labels/ssh in two measured ways.

    A list entry must contain '=' (a bare 'KEY' is refused: "invalid value
    KEY, expected key=value") -- additional_contexts has no null-value
    concept the way a build arg can inherit one. A map value must be a plain
    string -- a number/bool/null passes Docker's own JSON schema (the same
    scalar-or-null union args/labels/ssh accept) but then crashes Docker's
    own context-path resolver downstream (`compose-go` v2.10.2: "interface
    conversion: interface {} is int, not string", exit code 2 -- a real bug
    in the dependency, but still 'docker rejects' by the harness's own
    returncode check, so accepting it here would be a document already
    broken upstream).
    """
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, str) or "=" not in item:
                msg = f"service {name!r}: build {key!r} entries must be 'KEY=value' strings"
                raise UnsupportedComposeError(msg)
        return
    if isinstance(value, dict):
        require_string_keys(f"service {name!r}: build {key!r}", value)
        for val in value.values():
            if not isinstance(val, str):
                msg = f"service {name!r}: build {key!r} values must be strings"
                raise UnsupportedComposeError(msg)
        return
    msg = f"service {name!r}: build {key!r} must be a list or mapping"
    raise UnsupportedComposeError(msg)


def _validate_build_extra_hosts_value(name: str, key: str, val: Any) -> None:  # noqa: ANN401 - untyped YAML/JSON
    if isinstance(val, str):
        return
    if isinstance(val, list) and all(isinstance(item, str) for item in val):
        return
    msg = f"service {name!r}: build {key!r} values must be a string or list of strings"
    raise UnsupportedComposeError(msg)


def _validate_build_extra_hosts(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - untyped YAML/JSON
    """List-or-map value: a list entry needs a 'host=ip' or 'host:ip' separator (both accepted, measured).

    A map value may be a single address string, or a list of address strings
    (one host, multiple IPs) -- but not a number/bool/null, unlike
    args/labels/ssh's scalar-or-null union. All three shapes measured against
    `docker compose config` v5.1.2.
    """
    if isinstance(value, list):
        for item in value:
            if not isinstance(item, str) or ("=" not in item and ":" not in item):
                msg = f"service {name!r}: build {key!r} entries must be 'host=ip' or 'host:ip' strings"
                raise UnsupportedComposeError(msg)
        return
    if isinstance(value, dict):
        require_string_keys(f"service {name!r}: build {key!r}", value)
        for val in value.values():
            _validate_build_extra_hosts_value(name, key, val)
        return
    msg = f"service {name!r}: build {key!r} must be a list or mapping"
    raise UnsupportedComposeError(msg)


_BUILD_SECRET_REF_KEYS = {"source", "target"}


def _validate_build_secret_entry(name: str, entry: Any) -> None:  # noqa: ANN401 - untyped YAML/JSON
    if isinstance(entry, str):
        return
    if not isinstance(entry, dict):
        msg = f"service {name!r}: build 'secrets' entries must be a string or mapping"
        raise UnsupportedComposeError(msg)
    require_string_keys(f"service {name!r}: build 'secrets' entry", entry)
    unknown = set(entry) - _BUILD_SECRET_REF_KEYS
    if unknown:
        msg = f"service {name!r}: build 'secrets' entry: unsupported keys {sorted(unknown)}"
        raise UnsupportedComposeError(msg)
    for field in _BUILD_SECRET_REF_KEYS:
        if field in entry and not isinstance(entry[field], str):
            msg = f"service {name!r}: build 'secrets' entry {field!r} must be a string"
            raise UnsupportedComposeError(msg)


def _validate_build_secrets(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - untyped YAML/JSON
    """List of secret references: a bare name string, or a {source, target} mapping.

    Docker also cross-checks each `source` against the top-level `secrets:`
    store, refusing an undeclared name ("refers to undefined build secret") --
    measured, but not reproduced here: compose2pod never reads `build`'s
    contents at emit time, and closing that gap would need `_validate_build`
    to see the whole document rather than one service, which is out of scope
    for a value-*type* check (see planning/changes -- flagged as a known,
    narrow residual gap, not invented-and-skipped).
    """
    if not isinstance(value, list):
        msg = f"service {name!r}: build {key!r} must be a list"
        raise UnsupportedComposeError(msg)
    for entry in value:
        _validate_build_secret_entry(name, entry)


# Docker's own schema for the long-form `build` mapping -- not compose2pod's:
# compose2pod never uses any of build's contents to construct a command
# (`image_for` always substitutes the CI image), so nothing here is
# enumerated because compose2pod itself needs it. It exists so a document
# `docker compose config` refuses is refused here too -- both a bogus key
# ("additional properties '<key>' not allowed") and, per key, a bogus value
# (Task 9 -- Task 4 only closed the key-*name* gap). Measured against `docker
# compose config` v5.1.2, each key and its value grammar probed individually.
# `context` is deliberately not required: `build: {dockerfile: Dockerfile}`
# alone is accepted. A validated value is never read again after this: it
# still never reaches emit.py, only the gate.
_DOCKER_BUILD_KEYS: dict[str, Callable[[str, str, Any], None]] = {
    "additional_contexts": _validate_build_additional_contexts,
    "args": _validate_build_map,
    "cache_from": _validate_string_list,
    "cache_to": _validate_string_list,
    "context": values.validate_string,
    "dockerfile": values.validate_string,
    "dockerfile_inline": values.validate_string,
    "entitlements": _validate_string_list,
    "extra_hosts": _validate_build_extra_hosts,
    "isolation": values.validate_string,
    "labels": _validate_build_map,
    "network": values.validate_string,
    "no_cache": _validate_build_bool,
    "platforms": _validate_string_list,
    "privileged": _validate_build_bool,
    "pull": _validate_build_bool,
    "secrets": _validate_build_secrets,
    "shm_size": _validate_build_shm_size,
    "ssh": _validate_build_map,
    "tags": _validate_string_list,
    "target": values.validate_string,
    "ulimits": validate_ulimits,
}


def _validate_build(name: str, svc: dict[str, Any]) -> None:
    """Check build is a string or mapping; each known key's value is checked against Docker's own grammar for it.

    None of it is ever read again after this -- `image_for` always substitutes
    the CI image, so a validated value still never reaches the generated
    script -- but a malformed one is still a document `docker compose config`
    refuses, so it must be refused here too.
    """
    if "build" not in svc:
        return
    build = svc["build"]
    if not isinstance(build, str | dict):
        msg = f"service {name!r}: 'build' must be a string or mapping"
        raise UnsupportedComposeError(msg)
    if isinstance(build, dict):
        require_string_keys(f"service {name!r}: build", build)
        unknown = {key for key in build if key not in _DOCKER_BUILD_KEYS and not key.startswith("x-")}
        if unknown:
            msg = f"service {name!r}: build: unsupported keys {sorted(unknown)}"
            raise UnsupportedComposeError(msg)
        for key, validator in _DOCKER_BUILD_KEYS.items():
            if key in build:
                validator(name, key, build[key])


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
            IGNORED_SERVICE_KEYS[key](name, key, svc[key])
            warnings.append(f"service {name!r}: ignoring '{key}'")
        elif key not in SUPPORTED_SERVICE_KEYS:
            msg = f"service {name!r}: unsupported key '{key}'"
            raise UnsupportedComposeError(msg)
    if isinstance(svc.get("entrypoint"), str) and svc.get("command") is not None:
        warnings.append(f"service {name!r}: string entrypoint runs via shell; 'command' is ignored")
    _validate_image(name, svc)
    _validate_build(name, svc)
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

    Rejecting a non-string key matches Docker, not diverges from it --
    measured: `docker compose config` refuses `environment: {3306: db}` with
    `non-string key in services.app.environment: 3306`, the identical verdict
    this function reaches. Normalizing Python's `bool`/`int` back to a string
    instead of rejecting it would not reproduce Docker's behavior either --
    `True` has no single correct string form (`"on"`? `"true"`? `"True"`?)
    the way a boolean *value* does (see `keys._render_scalar`, which mirrors
    Docker's own `true`/`false` value normalization).

    The bare `3:` case needs no further help: Docker rejects it too, so
    PyYAML producing the int `3` and this function refusing it agree with
    Docker by construction. The bare `on:`/`off:`/`yes:`/`no:` case is the one
    that would otherwise disagree: PyYAML's default YAML 1.1 resolver turns
    it into a bool, which Docker's YAML 1.2 parser reads as an ordinary
    string -- refusing that bool here would reject a file Docker runs. The
    CLI's YAML loader (`_build_yaml_loader`, `compose2pod/cli.py`) closes
    that gap ahead of this function, not inside it: it resolves only
    `true`/`false` as booleans, matching YAML 1.2, so a bare `on`/`off` never
    arrives here as a non-string key at all.
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

    Skipped, because compose2pod never emits from them, so a non-string key
    there can never reach the generated script: `x-` blocks (top-level and
    per-service), `build`'s contents, and the ignored top-level `volumes`
    block (accepted, but never read -- see architecture/supported-subset.md).
    The top-level `networks` block is a narrower case: its *contents* are
    still never read, but `_validate_network_references` does read its own
    *keys*, to check a per-service `networks` reference names one that's
    declared. That reader only ever hashes a key into a `set` for a `not in`
    membership test against an already-string-checked per-service value --
    never `sorted()`s, `startswith()`s, or f-string-interpolates it -- so a
    non-string top-level network name cannot crash or leak a repr into the
    script the way this sweep exists to prevent; it would just never match
    any per-service reference, which is a separate, narrower correctness
    question this function does not need to answer.
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


def _validate_network_references(compose: dict[str, Any], services: dict[str, Any]) -> None:
    """Every per-service network must be declared top-level, as Docker requires.

    compose2pod ignores the top-level `networks` block's *contents* (every service
    shares the pod namespace), but a service naming a network nothing declares is a
    document `docker compose config` refuses -- so the reference is still checked.

    Runs after `hostnames()` deliberately: that call already confirmed each
    service's `networks` is a list or mapping (or absent), so `svc.get("networks")
    or {}` here is safe to iterate -- a still-unvalidated string would be walked
    character by character and produce the wrong error message.

    Both membership checks below hash their left-hand side into a `set` (`network
    not in declared`) -- the same hazard as `graph.depends_on`'s `dict.fromkeys`
    (see its docstring): an untrusted, still-unchecked value that happens to be
    unhashable (a dict or list, from the same list/map YAML slip `depends_on`/
    `command`/`environment` all suffer) crashes raw with `TypeError: unhashable
    type` instead of failing clean. So each side is type-checked before it is
    ever hashed: the top-level block must be a mapping (Docker's own verdict,
    measured -- a list is "networks must be a mapping"), and each per-service
    list-form entry must be a string (Docker: "must be a string").
    """
    top_networks = compose.get("networks")
    if top_networks is not None and not isinstance(top_networks, dict):
        msg = "top-level 'networks' must be a mapping"
        raise UnsupportedComposeError(msg)
    declared = set(top_networks or {})
    for name, svc in services.items():
        for network in svc.get("networks") or {}:
            if not isinstance(network, str):
                msg = f"service {name!r}: 'networks' entries must be strings"
                raise UnsupportedComposeError(msg)
            if network not in declared:
                msg = f"service {name!r}: refers to undefined network {network!r}"
                raise UnsupportedComposeError(msg)


def _reject_null_top_level_blocks(compose: dict[str, Any]) -> None:
    """Refuse a bare top-level block -- `docker compose config` refuses each.

    `services` has its own message ("no services defined"); `version`/`name` are
    scalars, not blocks.
    """
    for key in ("networks", "volumes", "secrets", "configs"):
        if key in compose and compose[key] is None:
            msg = f"top-level {key!r} must not be null"
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
    _reject_null_top_level_blocks(compose)
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
    _validate_network_references(compose, services)
    _validate_depends_on(services)
    stores.validate(compose)
    if uses_pod_options(services):
        warnings.append(
            "dns/sysctls/extra_hosts apply pod-wide -- all containers in the pod share one "
            "/etc/resolv.conf, sysctl set, and /etc/hosts"
        )
    return warnings
