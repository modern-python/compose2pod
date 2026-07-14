"""Value grammars: the shapes `docker compose config` accepts for a scalar key.

compose2pod refuses every document Docker refuses
(`planning/decisions/2026-07-14-docker-rejection-parity.md`), which means
matching Docker's *value* grammars, not just its types: `mem_limit: ""` and
`cpus: somevalue` are documents Docker will not run.

Every grammar here short-circuits on a value carrying a `${VAR}` reference.
That is not laziness -- it is the decision's carve-out. Docker rejects
`mem_limit: ${MEM}` with `invalid size: ''` because it interpolates the unset
variable to empty and *then* validates, so its verdict is a fact about the
reading shell's environment, not about the document: export `MEM=512m` and the
same file is accepted. compose2pod defers interpolation to script-run time by
design and cannot know that value, so it must not judge it.
"""

import re
from typing import Any

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.shell import variable_names


# Docker parses a size as a float with an optional unit suffix, so `1e3` and
# `0.5g` are both valid. `b` alone and the `<unit>b` spellings (`mb`, `gb`) are
# accepted alongside the bare unit letters.
_SIZE = re.compile(r"^\s*[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?\s*(?:[bkmgtpe]b?)?\s*$", re.IGNORECASE)

# Go's duration grammar, as used by `stop_grace_period`. A unit is mandatory --
# Docker refuses a bare `90` with "missing unit in duration".
_DURATION = re.compile(r"^[+-]?(?:[0-9]+(?:\.[0-9]+)?(?:ns|us|µs|ms|s|m|h))+$")

# One side of a port mapping: a single port or an inclusive range.
_PORT_RANGE = r"[0-9]+(?:-[0-9]+)?"
# [[IP:]HOST:]CONTAINER[/PROTO] -- the IP may be IPv4 or bracketed IPv6.
_PORT = re.compile(
    rf"""^
    (?:
        (?: (?P<ip> \[[0-9A-Fa-f:]+\] | [0-9]{{1,3}}(?:\.[0-9]{{1,3}}){{3}} ) : )?
        (?P<host> {_PORT_RANGE} ) :
    )?
    (?P<container> {_PORT_RANGE} )
    (?: / (?P<proto> [A-Za-z]+ ) )?
    $""",
    re.VERBOSE,
)


def has_variable(value: Any) -> bool:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    """Whether `value` is a string carrying a Compose variable reference.

    `$$` is an escaped literal `$`, not a reference, and does not count --
    `variable_names` already excludes it.
    """
    return isinstance(value, str) and bool(variable_names(value))


def _is_int(value: Any) -> bool:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    # bool IS an int in Python, so a plain isinstance check would let `true` through.
    return not isinstance(value, bool) and isinstance(value, int)


def validate_size(name: str, key: str, value: Any, *, allow_float: bool = True) -> None:  # noqa: ANN401 - untyped YAML
    """Check `value` is a byte size: a number, or a string like '512m' / '1gb' / '1e3'."""
    if has_variable(value):
        return
    if _is_int(value) or (allow_float and isinstance(value, float)):
        return
    if isinstance(value, str) and _SIZE.match(value):
        return
    msg = f"service {name!r}: {key!r} must be a size (a number, or a string like '512m')"
    raise UnsupportedComposeError(msg)


def validate_number(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    """Check `value` is a number, or a string that parses as one."""
    if has_variable(value):
        return
    if _is_int(value) or isinstance(value, float):
        return
    if isinstance(value, str):
        try:
            float(value)
        except ValueError:
            pass
        else:
            return
    msg = f"service {name!r}: {key!r} must be a number"
    raise UnsupportedComposeError(msg)


def validate_integer(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    """Check `value` is an integer, or a string that parses as one (a float is refused)."""
    if has_variable(value):
        return
    if _is_int(value):
        return
    if isinstance(value, str):
        try:
            int(value)
        except ValueError:
            pass
        else:
            return
    msg = f"service {name!r}: {key!r} must be an integer"
    raise UnsupportedComposeError(msg)


def validate_string(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    """Check `value` is a string -- any string.

    Docker accepts `cpuset: abc` and `cpuset: ''` without complaint, and refuses
    only a number. Validating the content would over-reject a file Docker runs.
    """
    if not isinstance(value, str):
        msg = f"service {name!r}: {key!r} must be a string"
        raise UnsupportedComposeError(msg)


def validate_duration(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    """Check `value` is a Go duration string with a unit ('90s', '1m30s')."""
    if has_variable(value):
        return
    if isinstance(value, str) and _DURATION.match(value):
        return
    msg = f"service {name!r}: {key!r} must be a duration with a unit (e.g. '90s', '1m30s')"
    raise UnsupportedComposeError(msg)


def _validate_port_entry(name: str, key: str, entry: Any) -> None:  # noqa: ANN401 - Compose values are untyped
    if isinstance(entry, dict):
        # Long form ({target, published, ...}); compose2pod ignores `ports`
        # entirely, so its inner keys are Docker's business, not ours.
        return
    if _is_int(entry):
        return
    if has_variable(entry):
        return
    if isinstance(entry, str) and _PORT.match(entry):
        return
    msg = f"service {name!r}: {key!r} entry {entry!r} is not a valid port mapping"
    raise UnsupportedComposeError(msg)


def validate_ports(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    """Check `value` is a list of port mappings. A bare string is refused, as Docker refuses it."""
    if not isinstance(value, list):
        msg = f"service {name!r}: {key!r} must be a list"
        raise UnsupportedComposeError(msg)
    for entry in value:
        _validate_port_entry(name, key, entry)
