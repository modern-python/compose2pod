"""The service-key registry: how each Compose service key is validated and emitted."""

import dataclasses
from collections.abc import Callable
from typing import Any

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


Token = str | Expand


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
    if not isinstance(value, bool):
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


def _validate_number(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    if not is_number(value):
        msg = f"service {name!r}: '{key}' must be a number or string"
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


def validate_map(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    if isinstance(value, list):
        _validate_list_elements(name, key, value)
        return
    if isinstance(value, dict):
        _validate_map_values(name, key, value)
        return
    msg = f"service {name!r}: '{key}' must be a list or mapping"
    raise UnsupportedComposeError(msg)


def _as_list(name: str, key: str, value: Any) -> list[Any]:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    """Normalize list-or-scalar-string form to a list, for merging across extends."""
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        return [value]
    msg = f"service {name!r}: cannot merge {key!r} across incompatible forms"
    raise UnsupportedComposeError(msg)


def _concat_list(name: str, key: str, base: Any, local: Any) -> list[Any]:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    """Merge policy for list-shaped keys: concatenate base then local."""
    return _as_list(name, key, base) + _as_list(name, key, local)


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
        return [flag] if value else []

    return KeySpec(validate=_validate_bool, emit=emit)


def _number_scalar(flag: str) -> KeySpec:
    def emit(value: Any) -> list[Token]:  # noqa: ANN401 - Compose values are untyped YAML/JSON
        return [flag, Expand(value=str(value))]

    return KeySpec(validate=_validate_number, emit=emit)


def _list(flag: str) -> KeySpec:
    def emit(value: Any) -> list[Token]:  # noqa: ANN401 - Compose values are untyped YAML/JSON
        tokens: list[Token] = []
        for item in value:
            tokens += [flag, Expand(value=str(item))]
        return tokens

    return KeySpec(validate=_validate_list, emit=emit, merge=_concat_list)


def _map(flag: str) -> KeySpec:
    def emit(value: Any) -> list[Token]:  # noqa: ANN401 - Compose values are untyped YAML/JSON
        tokens: list[Token] = []
        for pair in key_value_pairs(value):
            tokens += [flag, Expand(value=str(pair))]
        return tokens

    return KeySpec(validate=validate_map, emit=emit, merge=_merge_map)


def extra_host_pairs(value: list[Any] | dict[str, Any]) -> list[Any]:
    """Compose extra_hosts as 'host:ip' entries; map values keep their colons (IPv6-safe)."""
    if isinstance(value, list):
        return value
    return [f"{host}:{_render_scalar(ip)}" for host, ip in value.items()]


def _validate_pull_policy(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped
    if value is not None and (not isinstance(value, str) or value not in PULL_POLICY_MAP):
        allowed = "/".join(PULL_POLICY_MAP)
        msg = f"service {name!r}: unsupported {key} {value!r} (use {allowed})"
        raise UnsupportedComposeError(msg)


def _emit_pull_policy(value: Any) -> list[Token]:  # noqa: ANN401 - Compose values are untyped
    return ["--pull", PULL_POLICY_MAP[value]] if value is not None else []


def _validate_ulimits(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    if value is None:
        return
    if not isinstance(value, dict):
        msg = f"service {name!r}: '{key}' must be a mapping"
        raise UnsupportedComposeError(msg)
    for limit, spec in value.items():
        if isinstance(spec, dict):
            if set(spec) != {"soft", "hard"}:
                msg = f"service {name!r}: ulimit {limit!r} mapping must have exactly 'soft' and 'hard'"
                raise UnsupportedComposeError(msg)
            # bool IS an int in Python, so a plain `isinstance(..., int | str)`
            # would silently let a boolean soft/hard value through.
            if any(
                isinstance(spec[bound], bool) or not isinstance(spec[bound], int | str) for bound in ("soft", "hard")
            ):
                msg = f"service {name!r}: ulimit {limit!r} 'soft' and 'hard' must be int or str"
                raise UnsupportedComposeError(msg)
        elif isinstance(spec, bool) or not isinstance(spec, int | str):
            # A boolean ulimit is meaningless -- unlike environment's bool
            # (which Docker normalizes to a string), there is no sensible
            # normalization here, so it is rejected rather than coerced.
            msg = f"service {name!r}: ulimit {limit!r} must be an int or a soft/hard mapping"
            raise UnsupportedComposeError(msg)


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
    "ulimits": KeySpec(validate=_validate_ulimits, emit=_emit_ulimits, merge=_merge_map),
    "mem_limit": _number_scalar("--memory"),
    "memswap_limit": _number_scalar("--memory-swap"),
    "mem_reservation": _number_scalar("--memory-reservation"),
    "mem_swappiness": _number_scalar("--memory-swappiness"),
    "cpus": _number_scalar("--cpus"),
    "cpu_shares": _number_scalar("--cpu-shares"),
    "cpu_quota": _number_scalar("--cpu-quota"),
    "cpu_period": _number_scalar("--cpu-period"),
    "cpuset": _number_scalar("--cpuset-cpus"),
    "pids_limit": _number_scalar("--pids-limit"),
    "shm_size": _number_scalar("--shm-size"),
    "oom_score_adj": _number_scalar("--oom-score-adj"),
    "oom_kill_disable": _bool("--oom-kill-disable"),
}

STRUCTURAL_KEYS: set[str] = {
    "image",
    "build",
    "command",
    "entrypoint",
    "environment",
    "env_file",
    "volumes",
    "tmpfs",
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
