"""The service-key registry: how each Compose service key is validated and emitted."""

import dataclasses
from collections.abc import Callable
from typing import Any

from compose2pod import values
from compose2pod.exceptions import UnsupportedComposeError  # module-level; keys must not import emit/parsing


PULL_POLICY_MAP: dict[str, str] = {
    "always": "always",
    "never": "never",
    "missing": "missing",
    "if_not_present": "missing",
}


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class Expand:
    """A token whose Compose variable references expand at script-run time.

    Every emitted `Expand` eventually reaches `shell.to_shell`/`variable_names`,
    both of which assume `value` is a `str` (they run a compiled regex over
    it) and crash raw (a `TypeError`, not `UnsupportedComposeError`) otherwise.
    Rather than trust every call site across keys.py/emit.py/pod.py/
    resources.py to have cast or validated first, this is the one chokepoint:
    a non-str value is rejected right here, so any per-key validator gap
    becomes a clean error instead of a raw crash downstream, no matter which
    key leaked it. This is defense-in-depth, not a substitute for validating
    shape at the gate -- it only turns an otherwise-unhandled crash into a
    clean one; it can't catch a non-str value that a caller has already
    coerced to str() (see the silent str()/repr() corruption class instead).
    """

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            msg = f"internal: Expand.value must be a string, got {type(self.value).__name__}: {self.value!r}"
            raise UnsupportedComposeError(msg)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class GuardedEnvFile:
    """A `required: false` env_file entry, emitted only if the file exists at run time.

    Renders inline as `${var:+"$var"}` (present -> the `--env-file=PATH` flag as one
    shell word, absent -> nothing); its assignment lives in a prelude line emit.py
    places before the `podman run` line. `value` is the resolved path and may carry
    `${VAR}` references (expanded identically in the guard test and the flag).
    """

    var: str
    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.var, str) or not isinstance(self.value, str):
            msg = f"internal: GuardedEnvFile fields must be strings, got {self.var!r}, {self.value!r}"
            raise UnsupportedComposeError(msg)


Token = str | Expand | GuardedEnvFile


def _render_scalar(value: Any) -> str:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    """Render a map scalar value the way `docker compose config` does.

    A bool renders lowercase ('true'/'false'), matching Docker's own
    normalization -- Python's str(True) == 'True' would otherwise leak into
    the emitted flag value verbatim.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def key_value_pairs(value: list[Any] | dict[str, Any]) -> list[Any]:
    """Compose list/map key-value section as 'KEY=value' / 'KEY' entries.

    A null map value yields a bare 'KEY'. Meaning is caller-defined: '-e KEY'
    passes the host value through; '--label KEY' sets an empty label.
    """
    if isinstance(value, list):
        return value
    return [key if val is None else f"{key}={_render_scalar(val)}" for key, val in value.items()]


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class KeySpec:
    """A service-key spec: how one Compose service key is validated, emitted, and merged across extends."""

    validate: Callable[[str, str, Any], None]
    emit: Callable[[Any], list[Token]]
    merge: Callable[[str, str, Any, Any], Any] | None = None


def _validate_scalar(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    if not isinstance(value, str):
        msg = f"service {name!r}: '{key}' must be a string"
        raise UnsupportedComposeError(msg)


def _validate_bool(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    if not values.is_bool_like(value):
        msg = f"service {name!r}: '{key}' must be a boolean"
        raise UnsupportedComposeError(msg)


def is_number(value: Any) -> bool:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    return not isinstance(value, bool) and isinstance(value, int | float | str)


def require_string_keys(where: str, mapping: dict[Any, Any]) -> None:
    """Check every key of a raw YAML/JSON mapping is a string.

    PyYAML routinely produces non-string keys (a bare `3:` is an int; under
    YAML 1.1, a bare `on:`/`off:` is a bool). Every mapping-key consumer
    downstream (`sorted()`, `str.startswith`, a compiled regex) assumes
    `str` and crashes raw otherwise. This is the one shared check the gate
    runs before it reads any of `mapping`'s keys, so a non-string key fails
    clean regardless of which mapping it turned up in.
    """
    for key in mapping:
        if not isinstance(key, str):
            msg = f"{where}: key {key!r} must be a string"
            raise UnsupportedComposeError(msg)


def _validate_list_elements(name: str, key: str, value: list[Any]) -> None:
    """Check every list element is a string, so emit can't str() a non-string into the script."""
    for item in value:
        if not isinstance(item, str):
            msg = f"service {name!r}: '{key}' entries must be strings"
            raise UnsupportedComposeError(msg)


def _validate_map_values(name: str, key: str, value: dict[str, Any]) -> None:
    """Check every map value is a scalar or null, so emit can't repr() a dict/list into the script."""
    for val in value.values():
        if val is not None and not is_number(val) and not isinstance(val, bool):
            msg = f"service {name!r}: '{key}' values must be a string, number, boolean, or null"
            raise UnsupportedComposeError(msg)


def _validate_list(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    if not isinstance(value, list):
        msg = f"service {name!r}: '{key}' must be a list"
        raise UnsupportedComposeError(msg)
    _validate_list_elements(name, key, value)


def _validate_string_or_string_list(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    """Check a key is a string or list of strings (emit iterates it). Used by tmpfs."""
    if not isinstance(value, str | list):
        msg = f"service {name!r}: '{key}' must be a string or list"
        raise UnsupportedComposeError(msg)
    if isinstance(value, list):
        for entry in value:
            if not isinstance(entry, str):
                msg = f"service {name!r}: '{key}' entry must be a string"
                raise UnsupportedComposeError(msg)


def validate_map(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    if isinstance(value, list):
        _validate_list_elements(name, key, value)
        return
    if isinstance(value, dict):
        _validate_map_values(name, key, value)
        return
    msg = f"service {name!r}: '{key}' must be a list or mapping"
    raise UnsupportedComposeError(msg)


def as_list(name: str, key: str, value: Any) -> list[Any]:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    """Normalize list-or-scalar-string form to a list, for merging across extends."""
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        return [value]
    msg = f"service {name!r}: cannot merge {key!r} across incompatible forms"
    raise UnsupportedComposeError(msg)


def concat_list(name: str, key: str, base: Any, local: Any) -> list[Any]:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    """Merge policy for list-shaped keys: concatenate base then local."""
    return as_list(name, key, base) + as_list(name, key, local)


def pairs_to_mapping(name: str, key: str, value: Any) -> dict[str, Any]:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    """Normalize list-or-dict key-value form to a mapping; inverse of key_value_pairs."""
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        result: dict[str, Any] = {}
        for item in value:
            if not isinstance(item, str):
                msg = f"service {name!r}: '{key}' entries must be strings"
                raise UnsupportedComposeError(msg)
            pair_key, sep, pair_value = item.partition("=")
            result[pair_key] = pair_value if sep else None
        return result
    msg = f"service {name!r}: cannot merge {key!r} across incompatible forms"
    raise UnsupportedComposeError(msg)


def _merge_map(name: str, key: str, base: Any, local: Any) -> dict[str, Any]:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    """Merge policy for map-shaped keys: key-by-key merge, local wins."""
    return {**pairs_to_mapping(name, key, base), **pairs_to_mapping(name, key, local)}


def _scalar(flag: str) -> KeySpec:
    def emit(value: Any) -> list[Token]:  # noqa: ANN401 - Compose values are untyped YAML/JSON
        return [flag, Expand(value=str(value))]

    return KeySpec(validate=_validate_scalar, emit=emit)


def _bool(flag: str) -> KeySpec:
    def emit(value: Any) -> list[Token]:  # noqa: ANN401 - Compose values are untyped YAML/JSON
        return [flag] if values.as_bool(value) else []

    return KeySpec(validate=_validate_bool, emit=emit)


def _scalar_of(flag: str, validate: Callable[[str, str, Any], None]) -> KeySpec:
    """Build a single-value flag whose accepted values are defined by `validate`."""

    def emit(value: Any) -> list[Token]:  # noqa: ANN401 - Compose values are untyped YAML/JSON
        return [flag, Expand(value=str(value))]

    return KeySpec(validate=validate, emit=emit)


def _size(flag: str, *, allow_fractional: bool = True) -> KeySpec:
    def validate(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - untyped YAML/JSON
        values.validate_size(name, key, value, allow_fractional=allow_fractional)

    return _scalar_of(flag, validate)


def _integer(flag: str, *, allow_whole_float: bool = False) -> KeySpec:
    def validate(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - untyped YAML/JSON
        values.validate_integer(name, key, value, allow_whole_float=allow_whole_float)

    return _scalar_of(flag, validate)


def _list(flag: str) -> KeySpec:
    def emit(value: Any) -> list[Token]:  # noqa: ANN401 - Compose values are untyped YAML/JSON
        tokens: list[Token] = []
        for item in value:
            tokens += [flag, Expand(value=str(item))]
        return tokens

    return KeySpec(validate=_validate_list, emit=emit, merge=concat_list)


def _scalar_or_list(flag: str) -> KeySpec:
    def emit(value: Any) -> list[Token]:  # noqa: ANN401 - Compose values are untyped YAML/JSON
        # `value or []`: a falsy scalar/list (e.g. "") drops to no-emit, matching
        # the pre-refactor `svc.get("tmpfs") or []` truthiness drop exactly.
        normalized = value or []
        items = [normalized] if isinstance(normalized, str) else normalized
        tokens: list[Token] = []
        for item in items:
            tokens += [flag, Expand(value=str(item))]
        return tokens

    return KeySpec(validate=_validate_string_or_string_list, emit=emit, merge=concat_list)


def _map(flag: str) -> KeySpec:
    def emit(value: Any) -> list[Token]:  # noqa: ANN401 - Compose values are untyped YAML/JSON
        tokens: list[Token] = []
        for pair in key_value_pairs(value):
            tokens += [flag, Expand(value=str(pair))]
        return tokens

    return KeySpec(validate=validate_map, emit=emit, merge=_merge_map)


def split_extra_host(entry: str) -> tuple[str, str]:
    """Split an `extra_hosts` entry into (host, address).

    Compose documents the separator as '=' ('somehost=162.242.195.82') and also
    accepts the legacy 'host:ip'. '=' wins when present, because an IPv6 address
    is itself full of colons and splitting on the first one would tear it apart:
    'myhostv6=::1' -> ('myhostv6', '::1'). The colon form splits on the *first*
    colon only, so 'myhost:::1' -> ('myhost', '::1') still works.

    The one shared reader for every site that parses an entry -- the gate, the
    emitter, and the `extends` merge -- so they cannot disagree about where an
    entry divides.
    """
    separator = "=" if "=" in entry else ":"
    host, _sep, address = entry.partition(separator)
    return host, address


def extra_host_entries(value: list[Any] | dict[str, Any]) -> list[tuple[str, str]]:
    """Compose extra_hosts as (host, address) pairs, from either form.

    The mapping form arrives already divided, so it is read straight through;
    only the list form needs splitting. Joining a mapping into 'host:address'
    and re-splitting it would be lossy -- an address containing '=' would then
    re-divide at the wrong character.
    """
    if isinstance(value, dict):
        # `pod.validate_pod_options` (the gate) already refuses a non-string map
        # value before this ever runs -- unlike labels/annotations, Docker itself
        # refuses a boolean/numeric extra_hosts address rather than normalizing
        # it (measured against `docker compose config` v5.1.2). `_render_scalar`
        # is defense-in-depth for a caller that reaches this function directly,
        # bypassing the gate; it is a no-op on the guaranteed-string values a
        # validated document ever supplies.
        return [(str(host), _render_scalar(address)) for host, address in value.items()]
    return [split_extra_host(str(item)) for item in value]


def _validate_pull_policy(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped
    # No `value is None` escape, matching `validate_ulimits`: the gate refuses a
    # null `pull_policy:` outright (`parsing._reject_null_values`), as Docker
    # does, so a null reaching a *shape* validator is a wrong shape like any
    # other. Null policy lives in one place -- the gate -- not in each validator.
    if not isinstance(value, str) or value not in PULL_POLICY_MAP:
        allowed = "/".join(PULL_POLICY_MAP)
        msg = f"service {name!r}: unsupported {key} {value!r} (use {allowed})"
        raise UnsupportedComposeError(msg)


def _emit_pull_policy(value: Any) -> list[Token]:  # noqa: ANN401 - Compose values are untyped
    # The emitters stay null-tolerant as defense-in-depth: they are reachable
    # from `run_flags` without the gate, and `PULL_POLICY_MAP[None]` would be a
    # raw KeyError rather than a clean refusal.
    return ["--pull", PULL_POLICY_MAP[value]] if value is not None else []


def validate_ulimits(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    """Check `value` is a ulimits mapping: name -> int, or name -> {soft, hard} (both int).

    Public (not `_`-prefixed): reused directly by `parsing._validate_build` for
    `build.ulimits`, which is measured to share this exact grammar with the
    top-level `ulimits` service key. The `require_string_keys` calls are
    defense-in-depth for the top-level key -- `parsing._sweep_service` already
    guarantees string keys there before this ever runs -- but load-bearing for
    `build.ulimits`: `_sweep_service` skips build's contents, so this is the
    one place its keys get checked at all (measured: Docker refuses a
    non-string key here too).

    No `value is None` escape: the gate refuses a null `ulimits:` outright
    (`parsing._reject_null_values`), as Docker does, so a null reaching here
    is a wrong shape like any other.
    """
    if not isinstance(value, dict):
        msg = f"service {name!r}: '{key}' must be a mapping"
        raise UnsupportedComposeError(msg)
    require_string_keys(f"service {name!r}: {key!r}", value)
    for limit, spec in value.items():
        if isinstance(spec, dict):
            require_string_keys(f"service {name!r}: {key!r} ulimit {limit!r}", spec)
            if set(spec) != {"soft", "hard"}:
                msg = f"service {name!r}: ulimit {limit!r} mapping must have exactly 'soft' and 'hard'"
                raise UnsupportedComposeError(msg)
            for bound in ("soft", "hard"):
                values.validate_integer(name, f"ulimit {limit!r} {bound!r}", spec[bound])
        else:
            values.validate_integer(name, f"ulimit {limit!r}", spec)


def _ulimit_args(ulimits: dict[str, Any]) -> list[str]:
    """Compose ulimits as podman `name=soft:hard` / `name=value` args."""
    args: list[str] = []
    for limit, spec in ulimits.items():
        args.append(f"{limit}={spec['soft']}:{spec['hard']}" if isinstance(spec, dict) else f"{limit}={spec}")
    return args


def _emit_ulimits(value: Any) -> list[Token]:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    if value is None:
        return []
    tokens: list[Token] = []
    for arg in _ulimit_args(value):
        tokens += ["--ulimit", Expand(value=arg)]
    return tokens


SERVICE_KEYS: dict[str, KeySpec] = {
    "environment": _map("-e"),
    "user": _scalar("--user"),
    "working_dir": _scalar("--workdir"),
    "platform": _scalar("--platform"),
    "init": _bool("--init"),
    "read_only": _bool("--read-only"),
    "privileged": _bool("--privileged"),
    "group_add": _list("--group-add"),
    "cap_add": _list("--cap-add"),
    "cap_drop": _list("--cap-drop"),
    "security_opt": _list("--security-opt"),
    "devices": _list("--device"),
    "labels": _map("--label"),
    "annotations": _map("--annotation"),
    "pull_policy": KeySpec(validate=_validate_pull_policy, emit=_emit_pull_policy),
    "ulimits": KeySpec(validate=validate_ulimits, emit=_emit_ulimits, merge=_merge_map),
    "mem_limit": _size("--memory"),
    "memswap_limit": _size("--memory-swap"),
    # allow_fractional=False: measured against `docker compose config` v5.1.2 --
    # mem_reservation/mem_swappiness accept a *whole* native float (60.0) but
    # refuse a fractional one (0.5); the string branch is unaffected either way.
    "mem_reservation": _size("--memory-reservation", allow_fractional=False),
    "mem_swappiness": _size("--memory-swappiness", allow_fractional=False),
    "cpus": _scalar_of("--cpus", values.validate_number),
    # validate_count, not validate_number: cpu_shares/cpu_quota/cpu_period/pids_limit
    # cast a native number leniently but their *string* form is Go's strict
    # ParseInt -- "0.5"/"1e3"/"1_000" are refused as strings even though the
    # identical native value is accepted. cpus is a genuine float field and
    # keeps validate_number.
    "cpu_shares": _scalar_of("--cpu-shares", values.validate_count),
    "cpu_quota": _scalar_of("--cpu-quota", values.validate_count),
    "cpu_period": _scalar_of("--cpu-period", values.validate_count),
    "cpuset": _scalar_of("--cpuset-cpus", values.validate_string),
    "pids_limit": _scalar_of("--pids-limit", values.validate_count),
    "shm_size": _size("--shm-size"),
    # allow_whole_float=True: measured -- oom_score_adj accepts a whole native
    # float (1000.0) but refuses a fractional one (0.5), unlike ulimits' strict
    # int64 field (validate_integer's default), which refuses any float.
    "oom_score_adj": _integer("--oom-score-adj", allow_whole_float=True),
    "oom_kill_disable": _bool("--oom-kill-disable"),
    "tmpfs": _scalar_or_list("--tmpfs"),
}

STRUCTURAL_KEYS: set[str] = {
    "image",
    "build",
    "command",
    "entrypoint",
    # "environment" removed — now a SERVICE_KEYS registry key (_map("-e")).
    "env_file",
    "volumes",
    # "tmpfs" removed — now a SERVICE_KEYS registry key (_scalar_or_list("--tmpfs")).
    "healthcheck",
    "depends_on",
    "networks",
    "hostname",
    "container_name",
    "secrets",
    "configs",
    "deploy",
    "dns",
    "dns_search",
    "dns_opt",
    "sysctls",
    "extra_hosts",
}
