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
    validate_size("app", "mem_swappiness", 60, allow_float=False)
    validate_size("app", "mem_swappiness", "60", allow_float=False)


@pytest.mark.parametrize("value", [0.5, 2, -1, "0.5", "2", 1.5])
def test_validate_number_accepts(value: object) -> None:
    validate_number("app", "cpus", value)


@pytest.mark.parametrize("value", ["", "abc", [], {}, True])
def test_validate_number_rejects(value: object) -> None:
    with pytest.raises(UnsupportedComposeError, match="cpus"):
        validate_number("app", "cpus", value)


@pytest.mark.parametrize("value", [100, -500, "100"])
def test_validate_integer_accepts(value: object) -> None:
    validate_integer("app", "oom_score_adj", value)


@pytest.mark.parametrize("value", [1.5, "abc", "", True, []])
def test_validate_integer_rejects(value: object) -> None:
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
