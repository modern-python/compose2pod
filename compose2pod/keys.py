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


@dataclasses.dataclass(frozen=True)
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


@dataclasses.dataclass(frozen=True)
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
        return [flag, _Expand(str(value))]

    return KeySpec(_validate_scalar, emit)


def _bool(flag: str) -> KeySpec:
    def emit(value: Any) -> list[Token]:  # noqa: ANN401 - Compose values are untyped YAML/JSON
        return [flag] if value else []

    return KeySpec(_validate_bool, emit)


def _list(flag: str) -> KeySpec:
    def emit(value: Any) -> list[Token]:  # noqa: ANN401 - Compose values are untyped YAML/JSON
        tokens: list[Token] = []
        for item in value:
            tokens += [flag, _Expand(str(item))]
        return tokens

    return KeySpec(_validate_list, emit)


def _map(flag: str) -> KeySpec:
    def emit(value: Any) -> list[Token]:  # noqa: ANN401 - Compose values are untyped YAML/JSON
        tokens: list[Token] = []
        for pair in _key_value_pairs(value):
            tokens += [flag, _Expand(str(pair))]
        return tokens

    return KeySpec(_validate_map, emit)


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
}
