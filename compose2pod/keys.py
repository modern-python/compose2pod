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
class _Expand:
    """A token whose Compose variable references expand at script-run time."""

    value: str


Token = str | _Expand


def _key_value_pairs(value: list[Any] | dict[str, Any]) -> list[Any]:
    """Compose list/map key-value section as 'KEY=value' / 'KEY' entries.

    A null map value yields a bare 'KEY'. Meaning is caller-defined: '-e KEY'
    passes the host value through; '--label KEY' sets an empty label.
    """
    if isinstance(value, list):
        return value
    return [key if val is None else f"{key}={val}" for key, val in value.items()]


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class KeySpec:
    """A service-key spec: how one Compose service key is validated and emitted."""

    validate: Callable[[str, str, Any], None]
    emit: Callable[[Any], list[Token]]


def _validate_scalar(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    if not isinstance(value, str):
        msg = f"service {name!r}: '{key}' must be a string"
        raise UnsupportedComposeError(msg)


def _validate_bool(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    if not isinstance(value, bool):
        msg = f"service {name!r}: '{key}' must be a boolean"
        raise UnsupportedComposeError(msg)


def _is_number(value: Any) -> bool:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    return not isinstance(value, bool) and isinstance(value, int | float | str)


def _validate_number(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    if not _is_number(value):
        msg = f"service {name!r}: '{key}' must be a number or string"
        raise UnsupportedComposeError(msg)


def _validate_list(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    if not isinstance(value, list):
        msg = f"service {name!r}: '{key}' must be a list"
        raise UnsupportedComposeError(msg)


def _validate_map(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    if not isinstance(value, list | dict):
        msg = f"service {name!r}: '{key}' must be a list or mapping"
        raise UnsupportedComposeError(msg)


def _scalar(flag: str) -> KeySpec:
    def emit(value: Any) -> list[Token]:  # noqa: ANN401 - Compose values are untyped YAML/JSON
        return [flag, _Expand(value=str(value))]

    return KeySpec(validate=_validate_scalar, emit=emit)


def _bool(flag: str) -> KeySpec:
    def emit(value: Any) -> list[Token]:  # noqa: ANN401 - Compose values are untyped YAML/JSON
        return [flag] if value else []

    return KeySpec(validate=_validate_bool, emit=emit)


def _number_scalar(flag: str) -> KeySpec:
    def emit(value: Any) -> list[Token]:  # noqa: ANN401 - Compose values are untyped YAML/JSON
        return [flag, _Expand(value=str(value))]

    return KeySpec(validate=_validate_number, emit=emit)


def _list(flag: str) -> KeySpec:
    def emit(value: Any) -> list[Token]:  # noqa: ANN401 - Compose values are untyped YAML/JSON
        tokens: list[Token] = []
        for item in value:
            tokens += [flag, _Expand(value=str(item))]
        return tokens

    return KeySpec(validate=_validate_list, emit=emit)


def _map(flag: str) -> KeySpec:
    def emit(value: Any) -> list[Token]:  # noqa: ANN401 - Compose values are untyped YAML/JSON
        tokens: list[Token] = []
        for pair in _key_value_pairs(value):
            tokens += [flag, _Expand(value=str(pair))]
        return tokens

    return KeySpec(validate=_validate_map, emit=emit)


def _extra_host_pairs(value: list[Any] | dict[str, Any]) -> list[Any]:
    """Compose extra_hosts as 'host:ip' entries; map values keep their colons (IPv6-safe)."""
    if isinstance(value, list):
        return value
    return [f"{host}:{ip}" for host, ip in value.items()]


def _emit_extra_hosts(value: Any) -> list[Token]:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    tokens: list[Token] = []
    for entry in _extra_host_pairs(value):
        tokens += ["--add-host", _Expand(value=str(entry))]
    return tokens


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
            if not isinstance(spec["soft"], int | str) or not isinstance(spec["hard"], int | str):
                msg = f"service {name!r}: ulimit {limit!r} 'soft' and 'hard' must be int or str"
                raise UnsupportedComposeError(msg)
        elif not isinstance(spec, int | str):
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
        tokens += ["--ulimit", _Expand(value=arg)]
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
    "extra_hosts": KeySpec(validate=_validate_map, emit=_emit_extra_hosts),
    "pull_policy": KeySpec(validate=_validate_pull_policy, emit=_emit_pull_policy),
    "ulimits": KeySpec(validate=_validate_ulimits, emit=_emit_ulimits),
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
}
