import pytest

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.values import (
    has_variable,
    validate_duration,
    validate_integer,
    validate_number,
    validate_ports,
    validate_size,
    validate_string,
)


@pytest.mark.parametrize("value", ["${MEM}", "$MEM", "a${X}b", "${X:-1}"])
def test_has_variable_true(value: str) -> None:
    assert has_variable(value) is True


@pytest.mark.parametrize("value", ["512m", "", "$$literal", 512, None])
def test_has_variable_false(value: object) -> None:
    assert has_variable(value) is False


# A `${VAR}` value is never grammar-checked: its runtime value is unknowable here.
@pytest.mark.parametrize(
    "validate",
    [validate_size, validate_number, validate_integer, validate_duration],
)
def test_variable_value_skips_grammar(validate) -> None:  # noqa: ANN001
    validate("app", "k", "${VAR}")


@pytest.mark.parametrize("value", [512, 1.5, "512", "512m", "512M", "512mb", "512k", "1g", "1gb", "0.5g", "1e3"])
def test_validate_size_accepts(value: object) -> None:
    validate_size("app", "mem_limit", value)


@pytest.mark.parametrize("value", ["", "abc", [], {}, True])
def test_validate_size_rejects(value: object) -> None:
    with pytest.raises(UnsupportedComposeError, match="mem_limit"):
        validate_size("app", "mem_limit", value)


def test_validate_size_int_only_rejects_float() -> None:
    with pytest.raises(UnsupportedComposeError, match="mem_swappiness"):
        validate_size("app", "mem_swappiness", 1.5, allow_float=False)


def test_validate_size_int_only_accepts_int() -> None:
    validate_size("app", "mem_swappiness", 60, allow_float=False)


def test_validate_size_int_only_accepts_int_string() -> None:
    validate_size("app", "mem_swappiness", "60", allow_float=False)


# C1: every Docker size unit accepts, but Docker has no exabyte unit -- 'e'/'eb' is not
# a suffix, it collides with scientific notation ('1e3') and must not be swallowed as one.
@pytest.mark.parametrize("value", ["512b", "512k", "512m", "512g", "512t", "512p"])
def test_validate_size_accepts_every_real_unit(value: str) -> None:
    validate_size("app", "mem_limit", value)


@pytest.mark.parametrize("value", ["1e", "1eb", "512e", "512eb"])
def test_validate_size_rejects_invented_exabyte_unit(value: str) -> None:
    with pytest.raises(UnsupportedComposeError, match="mem_limit"):
        validate_size("app", "mem_limit", value)


# C2: Docker's JSON number decoder refuses NaN/Infinity outright ("unsupported value"),
# and the size grammar refuses the string spellings too ("invalid size").
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), "nan", "inf", "-inf"])
def test_validate_size_rejects_non_finite(value: object) -> None:
    with pytest.raises(UnsupportedComposeError, match="mem_limit"):
        validate_size("app", "mem_limit", value)


# I4: Go's float parser (used for size/number strings) permits digit-grouping
# underscores between digits, so Docker accepts these -- unlike the integer parser.
@pytest.mark.parametrize("value", ["1_000", "1_0"])
def test_validate_size_accepts_digit_grouping(value: str) -> None:
    validate_size("app", "mem_limit", value)


def test_validate_size_int_only_accepts_digit_grouping() -> None:
    validate_size("app", "mem_swappiness", "1_0", allow_float=False)


@pytest.mark.parametrize("value", [0.5, 2, -1, "0.5", "2", 1.5])
def test_validate_number_accepts(value: object) -> None:
    validate_number("app", "cpus", value)


@pytest.mark.parametrize("value", ["", "abc", [], {}, True])
def test_validate_number_rejects(value: object) -> None:
    with pytest.raises(UnsupportedComposeError, match="cpus"):
        validate_number("app", "cpus", value)


# C2: Docker's JSON decoder refuses NaN/Infinity outright ("unsupported value: NaN" /
# "unsupported value: +Inf"), both as native floats and as the string spellings.
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf"), "nan", "inf", "-inf"])
def test_validate_number_rejects_non_finite(value: object) -> None:
    with pytest.raises(UnsupportedComposeError, match="cpus"):
        validate_number("app", "cpus", value)


# I4: Go's float parser permits digit-grouping underscores between digits.
def test_validate_number_accepts_digit_grouping() -> None:
    validate_number("app", "cpus", "1_000")


@pytest.mark.parametrize("value", [100, -500, "100"])
def test_validate_integer_accepts(value: object) -> None:
    validate_integer("app", "oom_score_adj", value)


@pytest.mark.parametrize("value", [1.5, "abc", "", True, []])
def test_validate_integer_rejects(value: object) -> None:
    with pytest.raises(UnsupportedComposeError, match="oom_score_adj"):
        validate_integer("app", "oom_score_adj", value)


# I4: unlike the float grammars above, Go's integer parser (ParseInt, used for
# oom_score_adj) does not permit digit-grouping underscores -- "invalid syntax".
@pytest.mark.parametrize("value", ["1_00", "1_0", "1_000"])
def test_validate_integer_rejects_digit_grouping(value: str) -> None:
    with pytest.raises(UnsupportedComposeError, match="oom_score_adj"):
        validate_integer("app", "oom_score_adj", value)


@pytest.mark.parametrize("value", ["0-3", "0,1", "", "abc"])
def test_validate_string_accepts(value: object) -> None:
    validate_string("app", "cpuset", value)


@pytest.mark.parametrize("value", [0, 2, 1.5, True, [], {}])
def test_validate_string_rejects(value: object) -> None:
    with pytest.raises(UnsupportedComposeError, match="cpuset"):
        validate_string("app", "cpuset", value)


@pytest.mark.parametrize("value", ["1m30s", "90s", "1h", "500ms", "1h30m", "0s"])
def test_validate_duration_accepts(value: str) -> None:
    validate_duration("app", "stop_grace_period", value)


# A bare number and a unitless string are both refused -- Docker: "missing unit in duration".
@pytest.mark.parametrize("value", [90, "90", "", "abc", 1.5, True, []])
def test_validate_duration_rejects(value: object) -> None:
    with pytest.raises(UnsupportedComposeError, match="stop_grace_period"):
        validate_duration("app", "stop_grace_period", value)


@pytest.mark.parametrize(
    "value",
    [
        [8080],
        ["8080"],
        ["8080:80"],
        ["8080:80/tcp"],
        ["3000-3005:3000-3005"],
        ["127.0.0.1:8080:80"],
        [{"target": 80, "published": "8080"}],
        ["${PORT}:80"],
        [],
    ],
)
def test_validate_ports_accepts(value: object) -> None:
    validate_ports("app", "ports", value)


@pytest.mark.parametrize("value", ["8080:80", ["abc"], ["80:80:80"], 8080, {}, [None], [1.5]])
def test_validate_ports_rejects(value: object) -> None:
    with pytest.raises(UnsupportedComposeError, match="ports"):
        validate_ports("app", "ports", value)


# C3: a host range is fine on its own, or matched 1:1 against a container range, but
# a bare/differently-sized container range is "invalid ranges specified for container
# and host Ports", and a descending range (start > end) is invalid regardless of side.
@pytest.mark.parametrize("value", [["3000-3005:3000-3005"], ["3000-3005:3000"]])
def test_validate_ports_accepts_compatible_ranges(value: object) -> None:
    validate_ports("app", "ports", value)


@pytest.mark.parametrize(
    "value",
    [
        ["3000:3000-3005"],  # container is a range, host is a single port
        ["3000-3005:3000-3002"],  # both ranges, but different lengths (6 vs 3)
        ["3005-3000:3005-3000"],  # descending range on both sides
        ["3005-3000:3000"],  # descending range on the host side only
    ],
)
def test_validate_ports_rejects_incompatible_ranges(value: object) -> None:
    with pytest.raises(UnsupportedComposeError, match="ports"):
        validate_ports("app", "ports", value)


# Measured against `docker compose config` v5.1.2: the container (target) port
# must be 1-65535 -- 0 is rejected as "missing a target port", not accepted as
# a wildcard. The host (published) port allows 0 (meaning "pick a free port"),
# so its floor is 0, not 1 -- the two sides are not symmetric.
@pytest.mark.parametrize(
    "value",
    [
        ["1:1"],
        ["65535:65535"],
        ["0:80"],
        [1],
        [65535],
    ],
)
def test_validate_ports_accepts_bounds(value: object) -> None:
    validate_ports("app", "ports", value)


@pytest.mark.parametrize(
    "value",
    [
        ["0:0"],  # missing target port
        ["8080:0"],  # missing target port
        ["65536:80"],  # invalid hostPort: 65536
        ["80:65536"],  # invalid containerPort: 65536
        ["1-65536:80"],  # invalid hostPort: 1-65536 (range upper bound out of range)
        [0],  # bare container port: missing target port
        [65536],  # bare container port: invalid containerPort: 65536
    ],
)
def test_validate_ports_rejects_out_of_bounds(value: object) -> None:
    with pytest.raises(UnsupportedComposeError, match="ports"):
        validate_ports("app", "ports", value)
