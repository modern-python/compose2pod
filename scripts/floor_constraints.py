"""Usage: python scripts/floor_constraints.py [pyproject.toml] > floors.txt."""

import collections.abc
import pathlib
import sys
import typing

import tomli
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version


_FLOOR_OPERATORS: collections.abc.Set[str] = frozenset({">=", "==", "~="})


class UnboundedError(ValueError):
    pass


def _declared_floor(requirement: Requirement) -> Version | None:
    floors = [Version(spec.version) for spec in requirement.specifier if spec.operator in _FLOOR_OPERATORS]
    return max(floors, default=None)


def declared_requirements(project: collections.abc.Mapping[str, typing.Any]) -> list[str]:
    requirements = list(project.get("dependencies", []))
    for extra_requirements in project.get("optional-dependencies", {}).values():
        requirements.extend(extra_requirements)
    return requirements


def floor_constraints(requirements: collections.abc.Iterable[str]) -> list[str]:
    floors: dict[tuple[str, str], tuple[str, Version]] = {}
    unbounded: list[str] = []
    for line in requirements:
        requirement = Requirement(line)
        floor = _declared_floor(requirement)
        if floor is None:
            unbounded.append(requirement.name)
            continue
        key = (canonicalize_name(requirement.name), str(requirement.marker or ""))
        name, known = floors.get(key, (requirement.name, floor))
        floors[key] = (name, max(known, floor))
    if unbounded:
        raise UnboundedError("declares no floor: " + ", ".join(unbounded))
    return [f"{name}=={floor}" + (f"; {marker}" if marker else "") for (_, marker), (name, floor) in floors.items()]


def main(argv: collections.abc.Sequence[str]) -> int:
    path = pathlib.Path(argv[0] if argv else "pyproject.toml")
    project = tomli.loads(path.read_text(encoding="utf-8")).get("project", {})
    try:
        constraints = floor_constraints(declared_requirements(project))
    except UnboundedError as error:
        print(error, file=sys.stderr)  # noqa: T201
        return 1
    print("\n".join(constraints))  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
