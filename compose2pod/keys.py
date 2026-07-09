"""The service-key registry: how each Compose service key is validated and emitted."""

import dataclasses
from typing import Any


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
