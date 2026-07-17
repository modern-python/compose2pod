"""Validate a compose document against the supported subset."""

from collections.abc import Callable
from typing import Any

from compose2pod import stores, values
from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.graph import depends_on, hostnames
from compose2pod.healthcheck import has_healthcheck, health_cmd, interval_seconds
from compose2pod.keys import (
    SERVICE_KEYS,
    STRUCTURAL_KEYS,
    is_number,
    require_string_keys,
    validate_map,
    validate_ulimits,
)
from compose2pod.pod import uses_pod_options, validate_pod_options
from compose2pod.resources import validate_deploy


SUPPORTED_SERVICE_KEYS = set(SERVICE_KEYS) | STRUCTURAL_KEYS


def _validate_bool(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - untyped YAML/JSON
    if not values.is_bool_like(value):
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


def _classify_volume(volume: str) -> tuple[str, str | None]:
    r"""Classify one short-syntax volume entry: its kind, and its source when the kind is 'named'.

    Returns `("anonymous", None)` for a colon-less entry (a bare container
    path), `("named", source)` for a colon-form entry whose source matches
    Docker's own volume-name grammar (`stores.NAME_PATTERN`, the identical
    pattern a secret/config name is checked against:
    `^[a-zA-Z0-9][a-zA-Z0-9_.-]*$`), or `("bind", None)` for every other
    colon-form entry -- a relative (`./rel`) or absolute (`/abs`) host path, a
    `~`-prefixed home-relative path (`~/data`, measured ACCEPT against
    `docker compose config` v5.1.2 with no top-level declaration -- Docker
    expands it against the invoking user's home directory), or a `${VAR}`
    reference (`$`, `{`, `}` are none of them pattern characters either).

    Shared by `_validate_service_volumes` (only cares whether an entry is
    anonymous, to check its own shape) and `_named_volume_source` (only cares
    whether an entry is named, to extract its source for the cross-document
    declaration check) -- one function defines the colon-form split exactly
    once, rather than each re-deriving it. An earlier version of this
    file split named-vs-bind by "does not start with '.' or '/'" instead of
    the name grammar, which wrongly swept up every other bind-mount spelling
    (tilde in particular) into "named" -- an over-rejection once paired with
    the reference check below, since neither needs a top-level declaration.

    A genuine Windows drive-letter source (`C:\\data:/var`) is NOT fixed by
    this pattern swap: `source, _, _ = volume.partition(":")` splits on the
    FIRST colon regardless, so for that entry `source` is just `"C"` -- a
    single letter, which is itself a syntactically valid NAME_PATTERN match --
    not the full `"C:\\data"` a naive reading of "doesn't match the pattern"
    might suggest. Docker's own parser special-cases a leading `<letter>:\\`
    to keep the drive letter attached to the source before ever comparing it
    to a name grammar; this module (and `emit.py`'s `_volume_flags`, which
    shares the same first-colon split for the same reason) does not. Measured,
    still REJECTs post-fix -- a pre-existing, uncatalogued residual from when
    this check was introduced (`_named_volume_source`'s Task 14 predecessor),
    not something this change introduces or was scoped to close.
    """
    if ":" not in volume:
        return "anonymous", None
    source, _, _ = volume.partition(":")
    if stores.NAME_PATTERN.fullmatch(source):
        return "named", source
    return "bind", None


_VOLUME_LONG_TYPES = ("bind", "volume", "tmpfs")
_VOLUME_LONG_KEYS = {"type", "source", "target", "read_only", "consistency"}


def _validate_service_volumes(name: str, svc: dict[str, Any]) -> None:
    """Check volumes is a list of short-syntax strings or long-syntax mappings."""
    volumes = svc.get("volumes")
    if volumes is None:
        return
    if not isinstance(volumes, list):
        # A string would be iterated character-wise by the loop below.
        msg = f"service {name!r}: 'volumes' must be a list"
        raise UnsupportedComposeError(msg)
    for volume in volumes:
        if isinstance(volume, dict):
            _validate_volume_long_form(name, volume)
            continue
        if not isinstance(volume, str):
            msg = f"service {name!r}: volume entry must be a string or mapping"
            raise UnsupportedComposeError(msg)
        kind, _ = _classify_volume(volume)
        if kind == "anonymous" and not volume.startswith("/"):
            msg = f"service {name!r}: anonymous volume '{volume}' must be an absolute path"
            raise UnsupportedComposeError(msg)
        # A named or bind entry needs no further shape check here -- both are
        # accepted; podman creates a named volume implicitly on first
        # reference, and _validate_volume_references (below) is the one place
        # that cross-checks a named entry's source against a declaration.


def _validate_volume_long_form(name: str, entry: dict[str, Any]) -> None:
    """Check one long-syntax volume mapping against Docker's strict schema (measured, v5.1.2).

    Scope A: type (bind/volume/tmpfs), source, target, read_only, consistency.
    The nested bind/volume/tmpfs option maps fall out as unknown keys (refused,
    tracked in planning/deferred.md); cluster/npipe/image types are refused
    (podman cannot express them).
    """
    require_string_keys(f"service {name!r}: volume", entry)
    unknown = set(entry) - _VOLUME_LONG_KEYS
    if unknown:
        msg = f"service {name!r}: volume: unsupported keys {sorted(unknown)}"
        raise UnsupportedComposeError(msg)
    vtype = entry.get("type")
    if vtype not in _VOLUME_LONG_TYPES:
        msg = f"service {name!r}: volume 'type' must be one of {list(_VOLUME_LONG_TYPES)}"
        raise UnsupportedComposeError(msg)
    target = entry.get("target")
    if not isinstance(target, str):
        msg = f"service {name!r}: volume 'target' must be a string"
        raise UnsupportedComposeError(msg)
    if not target.startswith("/") and not values.has_variable(target):
        # podman rejects a relative --mount target for every type ("must be
        # an absolute path"); docker accepts it. A ${VAR} target is
        # host-dependent, so it is carved out like every other
        # values.has_variable case in this file.
        msg = f"service {name!r}: volume 'target' must be an absolute path"
        raise UnsupportedComposeError(msg)
    _validate_volume_long_form_source(name, vtype, entry.get("source"))
    if "read_only" in entry and not values.is_bool_like(entry["read_only"]):
        msg = f"service {name!r}: volume 'read_only' must be a boolean"
        raise UnsupportedComposeError(msg)
    if "consistency" in entry and not isinstance(entry["consistency"], str):
        msg = f"service {name!r}: volume 'consistency' must be a string"
        raise UnsupportedComposeError(msg)


def _validate_volume_long_form_source(name: str, vtype: str, source: Any) -> None:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    """Check a long-form volume entry's 'source': required for bind, refused for tmpfs, optional string for volume."""
    if vtype == "bind":
        if not isinstance(source, str):
            msg = f"service {name!r}: bind volume 'source' must be a string"
            raise UnsupportedComposeError(msg)
    elif vtype == "tmpfs":
        if source is not None:
            msg = f"service {name!r}: tmpfs volume takes no 'source'"
            raise UnsupportedComposeError(msg)
    elif source is not None and not isinstance(source, str):  # volume
        msg = f"service {name!r}: volume 'source' must be a string"
        raise UnsupportedComposeError(msg)


def _named_volume_source(volume: object) -> str | None:
    """Return a volume entry's bare-identifier named source, or None if it needs no declaration.

    A colon-form short-syntax string is classified via `_classify_volume`. A
    long-syntax mapping needs its own check: only a `volume`-type entry names
    a volume at all, and only when its `source` is a bare identifier (an
    absent source is an anonymous volume; a `bind`/`tmpfs` entry's `source`,
    if any, is a host path, never a name to cross-check).
    """
    if isinstance(volume, dict):
        source = volume.get("source")
        if volume.get("type") == "volume" and isinstance(source, str) and stores.NAME_PATTERN.fullmatch(source):
            return source
        return None
    # _validate_service_volumes has already confirmed every non-dict entry is
    # a str -- the signature is `object`, not `str | dict`, purely so a
    # caller need not narrow first; ty cannot see that upstream guarantee.
    kind, source = _classify_volume(volume)  # ty: ignore[invalid-argument-type]
    return source if kind == "named" else None


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
    """Strict bool for build's own no_cache/pull/privileged, plus the YAML-1.1 quoted forms.

    A quoted `"true"`/`"yes"`/`"on"` is accepted (via `values.is_bool_like`),
    matching `docker compose config` v5.1.2. `${VAR}` is a separate,
    host-state-dependent case (`values.has_variable`): `no_cache: ${MYVAR}` is
    accepted when `MYVAR=true` and refused when unset or non-boolean, so it is
    carved out ahead of the shape check, not judged here.
    """
    if values.has_variable(value):
        return
    if not values.is_bool_like(value):
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


def _validate_build_ssh(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - untyped YAML/JSON
    """List-or-map value, but the list form is stricter than args/labels -- a Task 9 regression fix.

    Measured against `docker compose config` v5.1.2: `_validate_build_map`'s
    shared grammar (a list of 'KEY[=value]' strings, or a mapping with
    scalar-or-null values) is right for the map form and for a list entry
    that carries '=' -- but a *bare* list entry (no '='), which args/labels
    accept as a null-valued key, is refused for `ssh` unless it is literally
    'default' ('build.ssh: [mykey]' raises 'invalid ssh key "mykey"'); an
    entry with '=' ('mykey=/path') accepts any id, same as args/labels.
    """
    _validate_build_map(name, key, value)
    if isinstance(value, list):
        for item in value:
            item_key, sep, _path = item.partition("=")
            if not sep and item_key != "default":
                msg = f"service {name!r}: build {key!r} entry {item!r} must be 'default' or 'id=path'"
                raise UnsupportedComposeError(msg)


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
    this value-*type* check cannot reproduce that here, since it never sees
    the whole document, only one key's value. See
    `_validate_build_secret_references`, run separately from `validate()`
    once every service's build has been shape-checked.
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
    "ssh": _validate_build_ssh,
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


_ENV_FILE_ENTRY_KEYS = {"path", "required", "format"}


def _validate_env_file(name: str, svc: dict[str, Any]) -> None:
    """Check env_file: a string, or a list of (string | strict {path, required, format} mapping)."""
    value = svc.get("env_file")
    if value is None:
        return
    if isinstance(value, str):
        return
    if not isinstance(value, list):
        msg = f"service {name!r}: 'env_file' must be a string or list"
        raise UnsupportedComposeError(msg)
    for entry in value:
        if isinstance(entry, str):
            continue
        if not isinstance(entry, dict):
            msg = f"service {name!r}: 'env_file' entry must be a string or mapping"
            raise UnsupportedComposeError(msg)
        _validate_env_file_mapping(name, entry)


def _validate_env_file_mapping(name: str, entry: dict[str, Any]) -> None:
    """Check one long-form env_file mapping against Docker's strict schema (measured, v5.1.2)."""
    require_string_keys(f"service {name!r}: 'env_file'", entry)
    unknown = set(entry) - _ENV_FILE_ENTRY_KEYS
    if unknown:
        msg = f"service {name!r}: 'env_file' entry: unsupported keys {sorted(unknown)}"
        raise UnsupportedComposeError(msg)
    if not isinstance(entry.get("path"), str):
        msg = f"service {name!r}: 'env_file' entry 'path' must be a string"
        raise UnsupportedComposeError(msg)
    if "required" in entry and not values.is_bool_like(entry["required"]):
        msg = f"service {name!r}: 'env_file' entry 'required' must be a boolean"
        raise UnsupportedComposeError(msg)
    if "format" in entry and entry["format"] != "raw":
        msg = f"service {name!r}: 'env_file' entry 'format' must be 'raw'"
        raise UnsupportedComposeError(msg)


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
    CLI's YAML loader (`_build_yaml_loader`, `compose2pod/read.py`) closes
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
    - Each top-level `secrets`/`configs`/`networks`/`volumes` definition's
      body: read by `stores.py` and, since Task 12, by
      `_validate_network_definitions`/`_validate_volume_definitions` below --
      compose2pod ignores what a network/volume definition *means* (every
      service shares the pod namespace; podman creates named volumes on
      first reference), but Docker still validates what it *says*, the same
      "ignored but still validated" stance `build` has always had, so its
      contents are read now, not skipped. All four are swept by name (like
      `services`, not by the generic `x-`-skipping walk): a store name
      matches `[a-zA-Z0-9][a-zA-Z0-9_.-]*` and a network/volume name is
      unconstrained, neither excludes one starting with `x-`, so none of
      the four kinds' *names* may be conflated with a content key.

    Skipped, because compose2pod never emits from them, so a non-string key
    there can never reach the generated script: `x-` blocks (top-level and
    per-service) and `build`'s contents.
    """
    require_string_keys("compose document", compose)
    services = compose.get("services")
    if isinstance(services, dict):
        require_string_keys("compose document.services", services)
        for name, svc in services.items():
            if isinstance(svc, dict):
                _sweep_service(name, svc)
    for top_key in ("secrets", "configs", "networks", "volumes"):
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

    `default` is the one exception: it is Docker's implicit network, always
    available whether or not a top-level `networks:` block exists at all --
    measured against `docker compose config` v5.1.2, `networks: [default]` with
    no top-level `networks:` block ACCEPTS, while every other undeclared name
    (`host`, `none`, or any custom name) still REJECTS. So `declared` always
    carries `default`, whether or not the document declares it explicitly (an
    explicit `networks: {default: {...}}` is legal too, and a no-op here since
    `default` is already in the set).

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
    declared = set(top_networks or {}) | {"default"}
    for name, svc in services.items():
        for network in svc.get("networks") or {}:
            if not isinstance(network, str):
                msg = f"service {name!r}: 'networks' entries must be strings"
                raise UnsupportedComposeError(msg)
            if network not in declared:
                msg = f"service {name!r}: refers to undefined network {network!r}"
                raise UnsupportedComposeError(msg)


def _validate_volume_references(compose: dict[str, Any], services: dict[str, Any]) -> None:
    """Every per-service NAMED volume must be declared top-level, as Docker requires.

    Mirrors `_validate_network_references` above, one level down: compose2pod
    ignores the top-level `volumes:` block's *contents* (podman creates a named
    volume implicitly on first reference, regardless of what the block says), but
    a service naming an undeclared NAMED volume is a document `docker compose
    config` refuses -- so the reference is still checked. Unlike networks, not
    every reference needs a declaration: a bind mount or an anonymous volume
    names no volume at all, so only a bare-identifier source is checked
    (`_named_volume_source` returns None for the other two forms). Measured
    against `docker compose config` v5.1.2: `volumes: [data:/var]` with no
    top-level `volumes:` REJECTS ("refers to undefined volume data"); the same
    entry with `volumes: {data:}` declared, or a bind/anonymous entry with no
    top-level block at all, ACCEPTS either way; `external: true` on the
    declaration still counts as declared (Docker treats it as "must already
    exist", but the *reference* check only cares that the name is known).

    Runs after the per-service loop in `validate()` (`_validate_service` ->
    `_validate_service_volumes` has already confirmed every service's `volumes`
    is a list of strings or long-form mappings, and that a colon-less string
    entry is an absolute path), so `svc.get("volumes") or []` here is safe to
    iterate -- `_named_volume_source` handles both shapes.

    A `${VAR}`-carrying source needs no separate carve-out the way other
    host-state-dependent grammars in this file need `values.has_variable`:
    measured, Docker resolves the variable first and classifies the *result*
    -- the same `${NAME}:/var` document ACCEPTS with no declaration when the
    shell resolves `NAME` to a bind-mount path (`/abs` or `./rel`) and REJECTS
    undeclared when it resolves to a bare identifier, a fact about the shell
    that will later run the generated script, not about this document, so it
    does not bind here either way. `_named_volume_source` already returns None
    for any `${...}`-carrying source unconditionally -- `$`, `{`, and `}` are
    none of them `stores.NAME_PATTERN` characters -- so this function never
    needs to ask `values.has_variable` itself.
    """
    top_volumes = compose.get("volumes")
    if top_volumes is not None and not isinstance(top_volumes, dict):
        msg = "top-level 'volumes' must be a mapping"
        raise UnsupportedComposeError(msg)
    declared = set(top_volumes or {})
    for name, svc in services.items():
        for volume in svc.get("volumes") or []:
            source = _named_volume_source(volume)
            if source is None:
                continue
            if source not in declared:
                msg = f"service {name!r}: refers to undefined volume {source!r}"
                raise UnsupportedComposeError(msg)


def _validate_network_driver_opts(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - untyped YAML/JSON
    """Check a mapping value against Docker's own `driver_opts` schema: string -> (number or string).

    Measured against `docker compose config` v5.1.2: a bool/null/list entry
    value is refused ('must be a number or string') even though the key
    itself may be any string. compose2pod never reads a network entry's
    `driver_opts` contents (see architecture/supported-subset.md), but a
    malformed value here is still a document Docker refuses.
    """
    if not isinstance(value, dict):
        msg = f"service {name!r}: networks {key!r} must be a mapping"
        raise UnsupportedComposeError(msg)
    require_string_keys(f"service {name!r}: networks {key!r}", value)
    for val in value.values():
        if isinstance(val, bool) or not isinstance(val, int | float | str):
            msg = f"service {name!r}: networks {key!r} values must be a number or string"
            raise UnsupportedComposeError(msg)


# Docker's own schema for a service's long-form (mapping) `networks` entry --
# STRICT, unlike short-form (list) `networks` or the top-level `networks:`
# block (whose contents compose2pod never reads). Measured against `docker
# compose config` v5.1.2, cross-checked against the upstream compose-spec
# JSON schema (`$defs/service.networks.oneOf[1].patternProperties`): exactly
# these 9 keys, `additionalProperties: false`, plus a `^x-` extension pattern.
# `aliases` is also independently checked by `graph._host_names` (which reads
# it for `--add-host`) -- this is the same grammar enforced twice, defense in
# depth, the same pattern Task 10 established for `extra_hosts`.
_DOCKER_NETWORK_ENTRY_KEYS: dict[str, Callable[[str, str, Any], None]] = {
    "aliases": _validate_string_list,
    "driver_opts": _validate_network_driver_opts,
    "gw_priority": values.validate_native_number,
    "interface_name": values.validate_string,
    "ipv4_address": values.validate_string,
    "ipv6_address": values.validate_string,
    "link_local_ips": _validate_string_list,
    "mac_address": values.validate_string,
    "priority": values.validate_native_number,
}


def _validate_network_entry_value(name: str, network: str, value: Any) -> None:  # noqa: ANN401 - untyped YAML/JSON
    """Check one service's long-form network entry against Docker's strict sub-schema.

    `value is None` is accepted (measured: means "use default settings", same
    as an explicit `{}`). No `${VAR}` carve-out is applied at this level,
    unlike a scalar grammar: Compose interpolation only ever turns a scalar
    into another scalar string, never a mapping, so a variable reference here
    can never satisfy "must be a mapping" regardless of its runtime value --
    the rejection is a fact about the document, not the host.
    """
    if value is None:
        return
    if not isinstance(value, dict):
        msg = f"service {name!r}: networks {network!r} must be a mapping"
        raise UnsupportedComposeError(msg)
    require_string_keys(f"service {name!r}: networks {network!r}", value)
    unknown = {key for key in value if key not in _DOCKER_NETWORK_ENTRY_KEYS and not key.startswith("x-")}
    if unknown:
        msg = f"service {name!r}: networks {network!r}: unsupported keys {sorted(unknown)}"
        raise UnsupportedComposeError(msg)
    for key, validator in _DOCKER_NETWORK_ENTRY_KEYS.items():
        if key in value:
            validator(name, key, value[key])


def _validate_network_entries(services: dict[str, Any]) -> None:
    """Every service's long-form (mapping) `networks` entry, checked document-wide.

    Runs after `hostnames()` (already confirmed `networks` is a list, mapping,
    or absent for every service) -- mirrors `_validate_network_references`:
    shape validation is document-wide, over every service, not scoped to the
    startup-order closure `emit.py` later computes for a single target.
    """
    for name, svc in services.items():
        networks = svc.get("networks")
        if isinstance(networks, dict):
            for network, value in networks.items():
                _validate_network_entry_value(name, network, value)


def _validate_definition_string(label: str) -> Callable[[str, str, Any], None]:
    """Build a validator for a plain-string field on a top-level `networks`/`volumes` definition (`driver`, `name`)."""

    def validate(ident: str, key: str, value: Any) -> None:  # noqa: ANN401 - untyped YAML/JSON
        if not isinstance(value, str):
            msg = f"{label} {ident!r}: {key!r} must be a string"
            raise UnsupportedComposeError(msg)

    return validate


def _validate_definition_bool(label: str) -> Callable[[str, str, Any], None]:
    """Build a validator for a strict-boolean field (`internal`/`attachable`/`enable_ipv6`), with a `${VAR}` carve-out.

    Measured against `docker compose config` v5.1.2: each casts a *string*
    value through the same YAML-1.1-style boolean interpolation every other
    boolean key in this project accepts (`values.is_bool_like`) --
    `internal: "true"` is accepted, `internal: "notabool"` is refused, and a
    genuine `${VAR}` reference is resolved and cast at read time
    ("error while interpolating ... failed to cast to expected type"), so its
    verdict is a fact about the reading shell, not the document --
    `has_variable` carves that case out, matching `_validate_build_bool`.
    """

    def validate(ident: str, key: str, value: Any) -> None:  # noqa: ANN401 - untyped YAML/JSON
        if values.has_variable(value):
            return
        if not values.is_bool_like(value):
            msg = f"{label} {ident!r}: {key!r} must be a boolean"
            raise UnsupportedComposeError(msg)

    return validate


def _validate_definition_driver_opts(label: str) -> Callable[[str, str, Any], None]:
    """Build a `driver_opts` validator: a mapping of string -> (number or string), shared by networks and volumes.

    Measured against `docker compose config` v5.1.2: identical grammar to a
    service's long-form `networks` entry `driver_opts`
    (`_validate_network_driver_opts`) -- a bool/null/list value is refused
    even though the key itself may be any string.
    """

    def validate(ident: str, key: str, value: Any) -> None:  # noqa: ANN401 - untyped YAML/JSON
        if not isinstance(value, dict):
            msg = f"{label} {ident!r}: {key!r} must be a mapping"
            raise UnsupportedComposeError(msg)
        require_string_keys(f"{label} {ident!r}: {key!r}", value)
        for val in value.values():
            if isinstance(val, bool) or not isinstance(val, int | float | str):
                msg = f"{label} {ident!r}: {key!r} values must be a number or string"
                raise UnsupportedComposeError(msg)

    return validate


def _validate_definition_labels(label: str) -> Callable[[str, str, Any], None]:
    """Build a `labels` validator: a list of 'KEY[=value]' strings, or a mapping with scalar-or-null values.

    Same grammar `keys.validate_map` already enforces for a service's own
    `labels`/`environment`, reimplemented here rather than reused: that
    function's own message hardcodes a "service" prefix, which would misname
    a network or volume definition.
    """

    def validate(ident: str, key: str, value: Any) -> None:  # noqa: ANN401 - untyped YAML/JSON
        if isinstance(value, list):
            for item in value:
                if not isinstance(item, str):
                    msg = f"{label} {ident!r}: {key!r} entries must be strings"
                    raise UnsupportedComposeError(msg)
            return
        if isinstance(value, dict):
            for val in value.values():
                if val is not None and not is_number(val) and not isinstance(val, bool):
                    msg = f"{label} {ident!r}: {key!r} values must be a string, number, boolean, or null"
                    raise UnsupportedComposeError(msg)
            return
        msg = f"{label} {ident!r}: {key!r} must be a list or mapping"
        raise UnsupportedComposeError(msg)

    return validate


_EXTERNAL_MAP_KEYS = {"name"}


def _validate_definition_external(label: str) -> Callable[[str, str, Any], None]:
    """Build an `external` validator: a boolean, or a mapping with an optional string `name` (deprecated but accepted).

    Measured against `docker compose config` v5.1.2: `external: {name: x}`
    still works (with a deprecation warning on stderr, not a rejection) --
    `external: true` plus a separate `name:` key is the modern spelling, but
    both are accepted. A quoted YAML-1.1 boolean spelling (`external: "yes"`)
    is accepted too, via `values.is_bool_like`, same as
    `internal`/`attachable`/`enable_ipv6`. A bare non-boolean string
    (`external: realname`) is refused outright: unlike those three keys, this
    field's own type union has no plain-string branch at all -- Docker casts
    it as a *boolean* interpolation target instead ("invalid boolean:
    realname"), so the same `has_variable` carve-out applies (a `${VAR}`
    reference resolves and casts at read time) but a literal non-boolean
    string never can, document-only like `values.validate_native_number`'s
    `priority` (Task 11). An explicit null is refused too (measured) --
    unlike the *entry* itself, which treats null as "use defaults".
    """

    def validate(ident: str, key: str, value: Any) -> None:  # noqa: ANN401 - untyped YAML/JSON
        if values.has_variable(value):
            return
        if values.is_bool_like(value):
            return
        if isinstance(value, dict):
            require_string_keys(f"{label} {ident!r}: {key!r}", value)
            unknown = set(value) - _EXTERNAL_MAP_KEYS
            if unknown:
                msg = f"{label} {ident!r}: {key!r}: unsupported keys {sorted(unknown)}"
                raise UnsupportedComposeError(msg)
            if "name" in value and not isinstance(value["name"], str):
                msg = f"{label} {ident!r}: {key!r} 'name' must be a string"
                raise UnsupportedComposeError(msg)
            return
        msg = f"{label} {ident!r}: {key!r} must be a boolean or mapping"
        raise UnsupportedComposeError(msg)

    return validate


# ipam is network-only (measured: refused as an unknown key on a volume
# definition) and, unlike every other definition key, validated to a second
# level of nesting -- `ipam.config` is a list of per-subnet mappings, each
# with its own strict schema. Measured against `docker compose config` v5.1.2.
_IPAM_KEYS = {"driver", "config", "options"}
_IPAM_CONFIG_ENTRY_KEYS = {"subnet", "ip_range", "gateway", "aux_addresses"}


def _validate_ipam_aux_addresses(label: str, ident: str, value: Any) -> None:  # noqa: ANN401 - untyped YAML/JSON
    if not isinstance(value, dict):
        msg = f"{label} {ident!r}: ipam config 'aux_addresses' must be a mapping"
        raise UnsupportedComposeError(msg)
    require_string_keys(f"{label} {ident!r}: ipam config 'aux_addresses'", value)
    for val in value.values():
        if not isinstance(val, str):
            msg = f"{label} {ident!r}: ipam config 'aux_addresses' values must be a string"
            raise UnsupportedComposeError(msg)


def _validate_ipam_config_entry(label: str, ident: str, entry: Any) -> None:  # noqa: ANN401 - untyped YAML/JSON
    if not isinstance(entry, dict):
        msg = f"{label} {ident!r}: ipam 'config' entries must be a mapping"
        raise UnsupportedComposeError(msg)
    require_string_keys(f"{label} {ident!r}: ipam config entry", entry)
    unknown = set(entry) - _IPAM_CONFIG_ENTRY_KEYS
    if unknown:
        msg = f"{label} {ident!r}: ipam config entry: unsupported keys {sorted(unknown)}"
        raise UnsupportedComposeError(msg)
    for key in ("subnet", "ip_range", "gateway"):
        if key in entry and not isinstance(entry[key], str):
            msg = f"{label} {ident!r}: ipam config entry {key!r} must be a string"
            raise UnsupportedComposeError(msg)
    if "aux_addresses" in entry:
        _validate_ipam_aux_addresses(label, ident, entry["aux_addresses"])


def _validate_ipam_options(label: str, ident: str, value: Any) -> None:  # noqa: ANN401 - untyped YAML/JSON
    # Measured divergence from driver_opts: ipam.options values must be
    # strings -- a number is refused, unlike driver_opts' number-or-string.
    if not isinstance(value, dict):
        msg = f"{label} {ident!r}: ipam 'options' must be a mapping"
        raise UnsupportedComposeError(msg)
    require_string_keys(f"{label} {ident!r}: ipam options", value)
    for val in value.values():
        if not isinstance(val, str):
            msg = f"{label} {ident!r}: ipam 'options' values must be a string"
            raise UnsupportedComposeError(msg)


def _validate_definition_ipam(label: str) -> Callable[[str, str, Any], None]:
    def validate(ident: str, key: str, value: Any) -> None:  # noqa: ANN401 - untyped YAML/JSON
        if not isinstance(value, dict):
            msg = f"{label} {ident!r}: {key!r} must be a mapping"
            raise UnsupportedComposeError(msg)
        require_string_keys(f"{label} {ident!r}: {key!r}", value)
        unknown = set(value) - _IPAM_KEYS
        if unknown:
            msg = f"{label} {ident!r}: {key!r}: unsupported keys {sorted(unknown)}"
            raise UnsupportedComposeError(msg)
        if "driver" in value and not isinstance(value["driver"], str):
            msg = f"{label} {ident!r}: ipam 'driver' must be a string"
            raise UnsupportedComposeError(msg)
        if "config" in value:
            if not isinstance(value["config"], list):
                msg = f"{label} {ident!r}: ipam 'config' must be a list"
                raise UnsupportedComposeError(msg)
            for entry in value["config"]:
                _validate_ipam_config_entry(label, ident, entry)
        if "options" in value:
            _validate_ipam_options(label, ident, value["options"])

    return validate


# Docker's own schema for a top-level `networks`/`volumes` DEFINITION -- not a
# service's *reference* to one (that's `_DOCKER_NETWORK_ENTRY_KEYS`, above, a
# different, per-service sub-schema entirely). compose2pod never reads either
# block's contents (every service shares the pod namespace; podman creates
# named volumes on first reference), but Docker still validates a document's
# own declarations, the same "ignored but still validated" stance `build` has
# always had. The two definitions share five keys; networks adds four more
# (`ipam`, `internal`, `attachable`, `enable_ipv6`) that measurably do not
# exist on a volume definition (refused there as unknown keys). Measured
# against `docker compose config` v5.1.2 by probing every candidate key
# individually against both block types.
_NETWORK_DEFINITION_KEYS: dict[str, Callable[[str, str, Any], None]] = {
    "attachable": _validate_definition_bool("network"),
    "driver": _validate_definition_string("network"),
    "driver_opts": _validate_definition_driver_opts("network"),
    "enable_ipv6": _validate_definition_bool("network"),
    "external": _validate_definition_external("network"),
    "internal": _validate_definition_bool("network"),
    "ipam": _validate_definition_ipam("network"),
    "labels": _validate_definition_labels("network"),
    "name": _validate_definition_string("network"),
}
_VOLUME_DEFINITION_KEYS: dict[str, Callable[[str, str, Any], None]] = {
    "driver": _validate_definition_string("volume"),
    "driver_opts": _validate_definition_driver_opts("volume"),
    "external": _validate_definition_external("volume"),
    "labels": _validate_definition_labels("volume"),
    "name": _validate_definition_string("volume"),
}


def _validate_top_level_definition(
    label: str,
    ident: str,
    definition: Any,  # noqa: ANN401 - untyped YAML/JSON
    keys: dict[str, Callable[[str, str, Any], None]],
) -> None:
    if definition is None:
        # Measured: means "use default settings" -- same as an explicit {}.
        return
    if not isinstance(definition, dict):
        msg = f"{label} {ident!r} must be a mapping or null"
        raise UnsupportedComposeError(msg)
    require_string_keys(f"{label} {ident!r}", definition)
    unknown = {key for key in definition if key not in keys and not key.startswith("x-")}
    if unknown:
        msg = f"{label} {ident!r}: unsupported keys {sorted(unknown)}"
        raise UnsupportedComposeError(msg)
    for key, validator in keys.items():
        if key in definition:
            validator(ident, key, definition[key])


def _validate_network_definitions(compose: dict[str, Any]) -> None:
    """Every top-level `networks:` definition's own shape -- not a service's reference to one.

    Unlike `_validate_volume_definitions`, below, this does not re-check the
    top-level mapping shape itself: `_validate_network_references` already
    does (and runs first, from `validate()`), raising "top-level 'networks'
    must be a mapping" before this function is ever reached -- duplicating
    that check here would be dead code no test could cover honestly.
    """
    defs = compose.get("networks")
    if not isinstance(defs, dict):
        return
    for ident, definition in defs.items():
        _validate_top_level_definition("network", ident, definition, _NETWORK_DEFINITION_KEYS)


def _validate_volume_definitions(compose: dict[str, Any]) -> None:
    """Every top-level `volumes:` definition's own shape -- not a service's reference to one.

    Unlike before Task 14, this no longer re-checks the top-level mapping shape
    itself: `_validate_volume_references` already does (and runs first, from
    `validate()`), raising "top-level 'volumes' must be a mapping" before this
    function is ever reached -- duplicating that check here would be dead code
    no test could cover honestly (the same reasoning `_validate_network_definitions`
    already documents for its own, symmetric reliance on `_validate_network_references`).
    """
    defs = compose.get("volumes")
    if not isinstance(defs, dict):
        return
    for ident, definition in defs.items():
        _validate_top_level_definition("volume", ident, definition, _VOLUME_DEFINITION_KEYS)


def _validate_build_secret_references(compose: dict[str, Any], services: dict[str, Any]) -> None:
    """Every service's `build.secrets` entry must reference a declared top-level secret.

    compose2pod never reads `build`'s contents at emit time (see
    `_validate_build_secrets`'s own docstring), but Docker cross-checks each
    entry's source -- the bare string itself in short form, or the mapping's
    `source` field in long form -- against the top-level `secrets:` block,
    refusing an undeclared name ('refers to undefined build secret'). Measured
    against `docker compose config` v5.1.2 for both forms.

    Runs after `_validate_service` (each entry's own shape -- a string or a
    mapping, with any *present* `source`/`target` confirmed to be a string --
    is already confirmed by `_validate_build_secret_entry`) and after
    `stores.validate` (the top-level `secrets:` block, if present, is already
    confirmed to be a mapping), so `compose.get("secrets") or {}` is safe
    here. `_validate_build_secret_entry` never *requires* `source` to be
    present, though: a long-form entry with no `source` key (`{target: ...}`,
    or `{}`) reaches here unguarded, so `entry["source"]` would raw-crash with
    KeyError. Docker itself rejects a missing source (measured: "refers to
    undefined build secret ''" -- treated as an empty, always-undeclared
    name), so this falls through to the same undefined-secret rejection
    below rather than requiring source as a separate schema check.
    """
    declared = set(compose.get("secrets") or {})
    for name, svc in services.items():
        build = svc.get("build")
        if not isinstance(build, dict):
            continue
        for entry in build.get("secrets") or []:
            source = entry if isinstance(entry, str) else entry.get("source")
            if source not in declared:
                msg = f"service {name!r}: build secrets refers to undefined secret {source!r}"
                raise UnsupportedComposeError(msg)


def _reject_null_top_level_blocks(compose: dict[str, Any]) -> None:
    """Refuse a bare top-level block -- `docker compose config` refuses each.

    `services` has its own message ("no services defined"); `version`/`name` are
    scalars, not blocks -- see `_validate_top_level_scalar_strings`.
    """
    for key in ("networks", "volumes", "secrets", "configs"):
        if key in compose and compose[key] is None:
            msg = f"top-level {key!r} must not be null"
            raise UnsupportedComposeError(msg)


def _validate_top_level_scalar_strings(compose: dict[str, Any]) -> None:
    """Refuse a non-string top-level `name` or `version` -- both must be plain strings.

    Measured against `docker compose config` v5.1.2: `name: 123` and
    `version: 123` both raise ("name must be a string" / "version must be a
    string"), and so does a bare `name:`/`version:` (null is not a string
    either) -- one `isinstance` check reproduces all three verdicts with no
    separate null case needed, unlike `_reject_null_top_level_blocks`'s blocks
    (where null is a distinct, block-shaped refusal). A `${VAR}` reference is
    still a plain YAML string scalar regardless of what the variable resolves
    to later, so it always passes this check -- there is no host-state-
    dependent case here the way there is for a boolean or numeric grammar.
    """
    for key in ("name", "version"):
        if key in compose and not isinstance(compose[key], str):
            msg = f"top-level {key!r} must be a string"
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
    _validate_top_level_scalar_strings(compose)
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
    _validate_network_entries(services)
    _validate_network_definitions(compose)
    _validate_volume_references(compose, services)
    _validate_volume_definitions(compose)
    _validate_depends_on(services)
    stores.validate(compose)
    _validate_build_secret_references(compose, services)
    if uses_pod_options(services):
        warnings.append(
            "dns/sysctls/extra_hosts apply pod-wide -- all containers in the pod share one "
            "/etc/resolv.conf, sysctl set, and /etc/hosts"
        )
    return warnings
