"""Usage: python scripts/check_floors.py <distribution> [extra ...]."""

import collections.abc
import importlib.metadata
import sys

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version


_FLOOR_OPERATORS: collections.abc.Set[str] = frozenset({">=", "==", "~="})


def _declared_floor(requirement: Requirement) -> Version | None:
    floors = [Version(spec.version) for spec in requirement.specifier if spec.operator in _FLOOR_OPERATORS]
    return max(floors, default=None)


def _applies(requirement: Requirement, extras: collections.abc.Iterable[str], environment: dict[str, str]) -> bool:
    if requirement.marker is None:
        return True
    return any(requirement.marker.evaluate({**environment, "extra": extra}) for extra in ["", *extras])


def floor_mismatches(
    requirements: collections.abc.Iterable[str],
    extras: collections.abc.Iterable[str],
    installed: collections.abc.Mapping[str, str],
    environment: collections.abc.Mapping[str, str] | None = None,
) -> list[str]:
    extras = list(extras)
    mismatches: list[str] = []
    for line in requirements:
        requirement = Requirement(line)
        if not _applies(requirement, extras, dict(environment or {})):
            continue
        floor = _declared_floor(requirement)
        resolved = installed.get(canonicalize_name(requirement.name))
        if floor is None:
            mismatches.append(f"{requirement.name}: declares no floor")
        elif resolved is None:
            mismatches.append(f"{requirement.name}: not installed")
        elif Version(resolved) != floor:
            mismatches.append(f"{requirement.name}: declared floor {floor}, resolved {resolved}")
    return mismatches


def installed_versions() -> dict[str, str]:
    return {
        canonicalize_name(distribution.metadata["Name"]): distribution.version
        for distribution in importlib.metadata.distributions()
    }


def main(argv: collections.abc.Sequence[str]) -> int:
    distribution, *extras = argv
    mismatches = floor_mismatches(importlib.metadata.requires(distribution) or [], extras, installed_versions())
    for mismatch in mismatches:
        print(mismatch)  # noqa: T201
    if mismatches:
        return 1
    print(f"every declared floor of {distribution} resolved as declared")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
