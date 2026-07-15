import pytest

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.values import (
    has_variable,
    validate_count,
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
    [validate_size, validate_number, validate_integer, validate_duration, validate_count],
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


def test_validate_size_int_only_rejects_fractional_float() -> None:
    with pytest.raises(UnsupportedComposeError, match="mem_swappiness"):
        validate_size("app", "mem_swappiness", 1.5, allow_fractional=False)


def test_validate_size_int_only_accepts_int() -> None:
    validate_size("app", "mem_swappiness", 60, allow_fractional=False)


def test_validate_size_int_only_accepts_int_string() -> None:
    validate_size("app", "mem_swappiness", "60", allow_fractional=False)


# Measured against `docker compose config` v5.1.2: `mem_swappiness: 60.0` and
# `mem_reservation: 60.0` are ACCEPTED (a whole-valued float casts cleanly to
# Docker's int64 field); only a fractional float ("must be a integer") is
# refused. The constraint is an integral *value*, not the absence of a float type.
def test_validate_size_int_only_accepts_whole_float() -> None:
    validate_size("app", "mem_swappiness", 60.0, allow_fractional=False)


def test_validate_size_int_only_rejects_fractional_float_string_unaffected() -> None:
    # The string branch is ungated by allow_fractional -- "1.5" is a valid size
    # string regardless (matches the pre-existing allow_fractional=True case).
    validate_size("app", "mem_swappiness", "1.5", allow_fractional=False)


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
    validate_size("app", "mem_swappiness", "1_0", allow_fractional=False)


# string_only: deploy.resources.limits.memory / reservations.memory are typed
# as a Go string field, unlike mem_limit/mem_reservation -- a native number is
# refused outright, only a size *string* is accepted. Measured against
# `docker compose config` v5.1.2.
@pytest.mark.parametrize("value", [512, 1073741824, 1.5, 60, True])
def test_validate_size_string_only_rejects_native_number(value: object) -> None:
    with pytest.raises(UnsupportedComposeError, match=r"limits\.memory"):
        validate_size("app", "limits.memory", value, string_only=True)


@pytest.mark.parametrize("value", ["512", "512m", "512M", "512mb", "1.5", "1e3"])
def test_validate_size_string_only_accepts_size_string(value: object) -> None:
    validate_size("app", "limits.memory", value, string_only=True)


@pytest.mark.parametrize("value", ["", "somevalue", "abc"])
def test_validate_size_string_only_rejects_non_size_string(value: object) -> None:
    with pytest.raises(UnsupportedComposeError, match=r"limits\.memory"):
        validate_size("app", "limits.memory", value, string_only=True)


def test_validate_size_string_only_skips_variable() -> None:
    validate_size("app", "limits.memory", "${MEM}", string_only=True)


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


# validate_count: cpu_shares/cpu_quota/cpu_period/pids_limit. Measured against
# `docker compose config` v5.1.2 -- the *native* number is cast leniently (a
# float is fine), but the *string* form goes through Go's strconv.ParseInt,
# which is a strict integer: no decimal point, no exponent, no digit-grouping
# underscore. `cpus` does not share this grammar (it stays on validate_number,
# a genuine ParseFloat field) -- measured `cpus: "0.5"` is accepted.
@pytest.mark.parametrize("value", [2, 0.5, -1, "2", "-3", "0"])
def test_validate_count_accepts(value: object) -> None:
    validate_count("app", "cpu_shares", value)


@pytest.mark.parametrize("value", ["0.5", "1e3", "1_000", "", "abc", [], {}, True])
def test_validate_count_rejects(value: object) -> None:
    with pytest.raises(UnsupportedComposeError, match="cpu_shares"):
        validate_count("app", "cpu_shares", value)


# C2: Docker's JSON decoder refuses NaN/Infinity outright, same as validate_number.
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_validate_count_rejects_non_finite(value: object) -> None:
    with pytest.raises(UnsupportedComposeError, match="cpu_shares"):
        validate_count("app", "cpu_shares", value)


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


# Measured against `docker compose config` v5.1.2: ulimits' int64 field rejects
# any float outright ("invalid type float64 for external"), whole or fractional
# -- unlike oom_score_adj/mem_swappiness/mem_reservation. Strict is the default
# so a bare `validate_integer` call (as `validate_ulimits` makes) stays exact.
def test_validate_integer_default_rejects_whole_float() -> None:
    with pytest.raises(UnsupportedComposeError, match="nofile"):
        validate_integer("app", "nofile", 60.0)


# oom_score_adj opts in: `oom_score_adj: 1000.0` is ACCEPTED by Docker, only a
# fractional float ("must be a integer") is refused.
def test_validate_integer_allow_whole_float_accepts_whole() -> None:
    validate_integer("app", "oom_score_adj", 1000.0, allow_whole_float=True)


def test_validate_integer_allow_whole_float_still_rejects_fractional() -> None:
    with pytest.raises(UnsupportedComposeError, match="oom_score_adj"):
        validate_integer("app", "oom_score_adj", 0.5, allow_whole_float=True)


@pytest.mark.parametrize("value", ["0-3", "0,1", "", "abc"])
def test_validate_string_accepts(value: object) -> None:
    validate_string("app", "cpuset", value)


@pytest.mark.parametrize("value", [0, 2, 1.5, True, [], {}])
def test_validate_string_rejects(value: object) -> None:
    with pytest.raises(UnsupportedComposeError, match="cpuset"):
        validate_string("app", "cpuset", value)


@pytest.mark.parametrize("value", ["1m30s", "90s", "1h", "500ms", "1h30m", "0s", "0"])
def test_validate_duration_accepts(value: str) -> None:
    validate_duration("app", "stop_grace_period", value)


# A bare number and a unitless string are both refused -- Docker: "missing unit in duration".
# '0' is the one exception (Go's time.ParseDuration special-cases the zero literal); '30' is not.
@pytest.mark.parametrize("value", [90, "90", "30", "", "abc", 1.5, True, []])
def test_validate_duration_rejects(value: object) -> None:
    with pytest.raises(UnsupportedComposeError, match="stop_grace_period"):
        validate_duration("app", "stop_grace_period", value)


# Measured against `docker compose config` v5.1.2: 'stop_grace_period: "0"' is accepted
# (a pre-existing over-rejection this fix incidentally closes), while a native 0 and the
# unitless '30' stay refused -- only the bare '0' string is special-cased.
def test_validate_duration_accepts_bare_zero_string() -> None:
    validate_duration("app", "stop_grace_period", "0")


@pytest.mark.parametrize("value", [0, 30])
def test_validate_duration_rejects_native_number(value: object) -> None:
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


# Measured against `docker compose config` v5.1.2: a long-form entry is Docker's
# business except for one hard requirement -- a 'target' key -- which "docker
# compose config" refuses to omit ("is missing a target port"). Everything else
# inside the mapping stays unchecked; compose2pod never reads `ports` at all.
@pytest.mark.parametrize(
    "value",
    [
        [{"target": 80}],
        [{"target": 80, "published": "8080"}],
    ],
)
def test_validate_ports_accepts_long_form_with_target(value: object) -> None:
    validate_ports("app", "ports", value)


@pytest.mark.parametrize(
    "value",
    [
        [{"published": "8080"}],
        [{"a": 1}],
    ],
)
def test_validate_ports_rejects_long_form_missing_target(value: object) -> None:
    with pytest.raises(UnsupportedComposeError, match="missing a target port"):
        validate_ports("app", "ports", value)


# --- long-form ports fields (Task 10, Class 4): `_validate_port_entry`'s dict
# branch used to return right after checking 'target' is present -- every other
# field's value was Docker's business, not ours. Each grammar below was
# measured against `docker compose config` v5.1.2 with the same YAML text fed
# to both oracles.


def _port(**fields: object) -> list[dict[str, object]]:
    return [{"target": 80, **fields}]


def test_validate_ports_rejects_long_form_unknown_field() -> None:
    with pytest.raises(UnsupportedComposeError, match="unsupported keys"):
        validate_ports("app", "ports", _port(bogus_field=1))


def test_validate_ports_accepts_every_known_long_form_field_name() -> None:
    # app_protocol is a real Compose Spec field, not a typo -- measured, it is
    # accepted, unlike a genuinely unknown key (see the test above).
    validate_ports(
        "app",
        "ports",
        _port(published="8080", host_ip="1.2.3.4", protocol="tcp", mode="host", name="web", app_protocol="http"),
    )


@pytest.mark.parametrize("value", [80, 80.0, "80", 0])
def test_validate_ports_long_form_target_accepts(value: object) -> None:
    validate_ports("app", "ports", [{"target": value}])


@pytest.mark.parametrize("value", [80.5, -1, "-5", "abc", True, "80-90", "1_000", "  80  "])
def test_validate_ports_long_form_target_rejects(value: object) -> None:
    # Docker casts a string target through Go's uint32 decoder: no fractional
    # float, no negative (native or string), no non-numeric string, no bool,
    # and no range (unlike the string form's short-form grammar). "1_000" and
    # "  80  " are digit-grouping/whitespace forms Python's own int() would
    # accept but Go's strconv.Atoi refuses -- measured against `docker compose
    # config` v5.1.2.
    with pytest.raises(UnsupportedComposeError, match="target"):
        validate_ports("app", "ports", [{"target": value}])


def test_validate_ports_long_form_target_has_no_upper_bound() -> None:
    # Measured: unlike the short-form container port (bound to 1-65535), the
    # long-form target field is accepted well past 65535 at config-validate time.
    validate_ports("app", "ports", [{"target": 99999}])


@pytest.mark.parametrize("value", [8080, -1, "8080", "abc", "8080-8090", ""])
def test_validate_ports_long_form_published_accepts(value: object) -> None:
    # Docker's own Published field is a plain string with no further
    # validation at config time -- content is entirely unchecked.
    validate_ports("app", "ports", _port(published=value))


@pytest.mark.parametrize("value", [True, 80.5])
def test_validate_ports_long_form_published_rejects(value: object) -> None:
    with pytest.raises(UnsupportedComposeError, match="published"):
        validate_ports("app", "ports", _port(published=value))


@pytest.mark.parametrize(
    "value",
    ["1.2.3.4", "255.255.255.255", "0.0.0.0", "::1", "2001:db8::1", "::ffff:1.2.3.4"],  # noqa: S104 - a real Compose value, not a bind address
)
def test_validate_ports_long_form_host_ip_accepts(value: object) -> None:
    # ::ffff:1.2.3.4 is an IPv4-mapped IPv6 literal -- measured, Docker accepts
    # it against `docker compose config` v5.1.2, same as stdlib `ipaddress`.
    validate_ports("app", "ports", _port(host_ip=value))


@pytest.mark.parametrize(
    "value",
    ["localhost", "1.2.3", "1.2.3.4.5", "[::1]", "", 3, "fe80::1%eth0", "  1.2.3.4  "],
)
def test_validate_ports_long_form_host_ip_rejects(value: object) -> None:
    # fe80::1%eth0 is an RFC 4007 zone-id literal: stdlib `ipaddress` parses it
    # (Python 3.9+ scope_id support), but Docker's Go `net.ParseIP` has no
    # zone-id support and refuses it ("invalid ip address") -- measured against
    # `docker compose config` v5.1.2. "  1.2.3.4  " (leading/trailing
    # whitespace) is refused by both oracles already.
    with pytest.raises(UnsupportedComposeError, match="host_ip"):
        validate_ports("app", "ports", _port(host_ip=value))


@pytest.mark.parametrize("field", ["protocol", "mode", "name", "app_protocol"])
def test_validate_ports_long_form_string_fields_accept_any_string(field: str) -> None:
    # Measured: content is entirely unchecked -- 'protocol: bogus' and
    # 'mode: bogus' both pass `docker compose config`; only the type matters.
    validate_ports("app", "ports", _port(**{field: "bogus"}))
    validate_ports("app", "ports", _port(**{field: ""}))


@pytest.mark.parametrize("field", ["protocol", "mode", "name", "app_protocol"])
def test_validate_ports_long_form_string_fields_reject_non_string(field: str) -> None:
    with pytest.raises(UnsupportedComposeError, match=field):
        validate_ports("app", "ports", _port(**{field: 3}))


def test_validate_ports_long_form_fields_accept_variable_reference() -> None:
    validate_ports("app", "ports", _port(published="${P}", host_ip="${H}", protocol="${PR}"))
    validate_ports("app", "ports", [{"target": "${T}"}])
