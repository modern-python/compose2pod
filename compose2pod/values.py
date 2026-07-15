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

import math
import re
from typing import Any

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.shell import variable_names


# Go's float grammar (used for size/number strings) permits a digit-grouping
# underscore between two digits -- "1_000" is valid, "_1000"/"1000_"/"1__0" are not.
_DIGITS = r"[0-9]+(?:_[0-9]+)*"

# Docker parses a size as a float with an optional unit suffix, so `1e3` and
# `0.5g` are both valid. `b` alone and the `<unit>b` spellings (`mb`, `gb`) are
# accepted alongside the bare unit letters. There is no exabyte unit: `e`/`eb`
# is not a suffix Docker recognizes -- it collides with scientific notation.
_SIZE = re.compile(
    rf"^\s*{_DIGITS}(?:\.{_DIGITS})?(?:[eE][+-]?{_DIGITS})?\s*(?:[bkmgtp]b?)?\s*$",
    re.IGNORECASE,
)

# Go's duration grammar, as used by `stop_grace_period`. A unit is mandatory --
# Docker refuses a bare `90` with "missing unit in duration".
_DURATION = re.compile(r"^[+-]?(?:[0-9]+(?:\.[0-9]+)?(?:ns|us|µs|ms|s|m|h))+$")

# Go's strconv.ParseInt grammar, used for the *string* form of an int64 field
# (cpu_shares/cpu_quota/cpu_period/pids_limit): an optional sign then digits
# only -- no decimal point, no exponent, no digit-grouping underscore. The
# *native* number form of these fields is far more permissive (see validate_count).
_STRICT_INT_STRING = re.compile(r"^[+-]?[0-9]+$")

# The highest valid TCP/UDP port number, per Docker's own bound.
_MAX_PORT = 65535

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


def validate_size(
    name: str,
    key: str,
    value: Any,  # noqa: ANN401 - untyped YAML
    *,
    allow_fractional: bool = True,
    string_only: bool = False,
) -> None:
    """Check `value` is a byte size: a number, or a string like '512m' / '1gb' / '1e3'.

    `allow_fractional=False` (mem_reservation, mem_swappiness) mirrors Docker's
    own int64-cast fields: a native float is accepted only if it has no
    fractional part -- `60.0` is fine, `0.5` is "must be a integer" -- measured
    against `docker compose config` v5.1.2. The *string* branch is ungated by
    this flag: a size string like `"1.5"` or `"512m"` is accepted either way,
    because Docker's size-string grammar has no such restriction.

    `string_only=True` (deploy.resources.limits.memory,
    deploy.resources.reservations.memory) mirrors a Go field typed as a plain
    string rather than a size-or-string union: a native number is refused
    outright ("must be a string"), even though the *legacy* `mem_limit`/
    `mem_reservation` keys accept one -- measured against `docker compose
    config` v5.1.2. `allow_fractional` has no effect when `string_only` is set,
    since no native number is ever accepted either way.
    """
    if has_variable(value):
        return
    if not string_only:
        if _is_int(value):
            return
        if isinstance(value, float) and math.isfinite(value) and (allow_fractional or value.is_integer()):
            return
    if isinstance(value, str) and _SIZE.match(value):
        return
    if string_only:
        msg = f"service {name!r}: {key!r} must be a size string (e.g. '512m')"
    else:
        msg = f"service {name!r}: {key!r} must be a size (a number, or a string like '512m')"
    raise UnsupportedComposeError(msg)


def validate_number(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    """Check `value` is a number, or a string that parses as one."""
    if has_variable(value):
        return
    if _is_int(value) or (isinstance(value, float) and math.isfinite(value)):
        return
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            pass
        else:
            if math.isfinite(parsed):
                return
    msg = f"service {name!r}: {key!r} must be a number"
    raise UnsupportedComposeError(msg)


def validate_count(name: str, key: str, value: Any) -> None:  # noqa: ANN401 - Compose values are untyped YAML/JSON
    """Check `value` is an int64 count field: cpu_shares, cpu_quota, cpu_period, pids_limit.

    Measured against `docker compose config` v5.1.2, this family has a
    native/string asymmetry `validate_number` does not capture: the *native*
    number is cast leniently (`cpu_shares: 0.5` is accepted), but the
    *string* form goes through Go's strconv.ParseInt -- a strict integer, no
    decimal point, no exponent, no digit-grouping underscore. `"0.5"`,
    `"1e3"`, and `"1_000"` are all refused as strings even though the
    identical native value is fine. `cpus` is not one of these fields -- it
    is a genuine float (ParseFloat) and stays on `validate_number`.
    """
    if has_variable(value):
        return
    if _is_int(value) or (isinstance(value, float) and math.isfinite(value)):
        return
    if isinstance(value, str) and _STRICT_INT_STRING.match(value):
        return
    msg = f"service {name!r}: {key!r} must be an integer"
    raise UnsupportedComposeError(msg)


def validate_integer(
    name: str,
    key: str,
    value: Any,  # noqa: ANN401 - Compose values are untyped YAML/JSON
    *,
    allow_whole_float: bool = False,
) -> None:
    """Check `value` is an integer, or a string that parses as one (a fractional value is refused).

    Default (`allow_whole_float=False`) matches ulimits' int64 field, which
    Docker's own decoder refuses for *any* float -- whole or fractional
    ("invalid type float64 for external"), measured against `docker compose
    config` v5.1.2. `oom_score_adj` opts in with `allow_whole_float=True`: its
    Go field casts a whole-valued JSON number leniently (`1000.0` is
    accepted), and refuses only a fractional one (`0.5`).
    """
    if has_variable(value):
        return
    if _is_int(value):
        return
    if isinstance(value, float) and math.isfinite(value) and allow_whole_float and value.is_integer():
        return
    # Go's ParseInt (used for oom_score_adj etc.) does not permit the digit-grouping
    # underscores its float parser allows; unlike Python's int(), it is "invalid syntax".
    if isinstance(value, str) and "_" not in value:
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


def _parse_port_range(value: str) -> tuple[int, int]:
    if "-" in value:
        start, end = value.split("-", 1)
        return int(start), int(end)
    port = int(value)
    return port, port


def _ranges_compatible(host: str | None, container: str) -> bool:
    """Whether a host/container port pairing is a range Docker accepts.

    A range must ascend (start <= end). If the container side is a range, a
    present host side must be a range of the same length; a host range paired
    with a single container port is fine (each host port maps to it).

    Bounds are asymmetric, as measured against `docker compose config`: the
    container (target) port must be 1-65535 -- 0 is rejected as "missing a
    target port", not treated as a wildcard. The host (published) port may be
    0 (meaning "pick a free port"), so its floor is 0, not 1. Both endpoints
    of a range are held to the same bound as a single port on that side.
    """
    c_start, c_end = _parse_port_range(container)
    if c_start > c_end or not (1 <= c_start <= _MAX_PORT and c_end <= _MAX_PORT):
        return False
    if host is None:
        return True
    h_start, h_end = _parse_port_range(host)
    if h_start > h_end or h_end > _MAX_PORT:
        return False
    c_len = c_end - c_start + 1
    return c_len == 1 or h_end - h_start + 1 == c_len


def _validate_port_entry(name: str, key: str, entry: Any) -> None:  # noqa: ANN401 - Compose values are untyped
    if isinstance(entry, dict):
        # Long form ({target, published, ...}); compose2pod ignores `ports`
        # entirely, so every other inner key is Docker's business, not ours --
        # except 'target', which Docker refuses to omit ("is missing a target
        # port", measured against `docker compose config` v5.1.2). The dict's
        # own keys are already guaranteed strings by validate()'s sweep
        # (`_require_string_keys_deep`, which runs ahead of every other check),
        # so a plain `in` check here is safe.
        if "target" not in entry:
            msg = f"service {name!r}: {key!r} entry {entry!r} is missing a target port"
            raise UnsupportedComposeError(msg)
        return
    if has_variable(entry):
        return
    # A bare int is a container-only mapping (no host prefix): the same
    # 1-65535 bound applies as the container side of a string mapping. Bound
    # it directly rather than routing through `_parse_port_range`, which
    # splits on "-" and would misparse a negative int stringified.
    if _is_int(entry):
        if 1 <= entry <= _MAX_PORT:
            return
    elif isinstance(entry, str):
        match = _PORT.match(entry)
        if match and _ranges_compatible(match.group("host"), match.group("container")):
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
