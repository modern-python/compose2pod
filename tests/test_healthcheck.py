import pytest

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.healthcheck import has_healthcheck, health_cmd, interval_seconds


_FIVE_SECONDS = 5
_ONE_HUNDRED_TWENTY_SECONDS = 120
_ONE_HOUR_SECONDS = 3600
_ONE_HOUR_THIRTY_MINUTES_SECONDS = 5400
_ONE_DAY_SECONDS = 86400
_ONE_WEEK_SECONDS = 604800
_ONE_AND_A_HALF_DAYS_SECONDS = 129600
_COMPOUND_DURATION_SECONDS = 2 * _ONE_HOUR_SECONDS + 45 * 60 + 30


class TestHealthCmd:
    def test_cmd_shell_form(self) -> None:
        assert health_cmd(["CMD-SHELL", "pg_isready -U db"]) == "pg_isready -U db"

    def test_cmd_list_form_becomes_json(self) -> None:
        assert health_cmd(["CMD", "keydb-cli", "-p", "26379"]) == '["keydb-cli", "-p", "26379"]'

    def test_plain_string_passes_through(self) -> None:
        assert health_cmd("true") == "true"

    def test_none_and_disable_forms(self) -> None:
        assert health_cmd(None) is None
        assert health_cmd(["NONE"]) is None

    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="WHATEVER"):
            health_cmd(["WHATEVER", "x"])

    def test_empty_list_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="unsupported healthcheck test"):
            health_cmd([])

    def test_non_list_non_string_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="unsupported healthcheck test"):
            health_cmd(42)

    def test_cmd_shell_without_argument_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="unsupported healthcheck test"):
            health_cmd(["CMD-SHELL"])

    def test_cmd_without_command_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="unsupported healthcheck test"):
            health_cmd(["CMD"])

    def test_cmd_shell_non_string_argument_raises(self) -> None:
        # A nested list where a string is expected: used to reach emit and
        # crash raw when the non-string was wrapped in Expand.
        with pytest.raises(UnsupportedComposeError, match="unsupported healthcheck test"):
            health_cmd(["CMD-SHELL", ["curl", "-f"]])

    def test_cmd_non_string_argument_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="unsupported healthcheck test"):
            health_cmd(["CMD", "keydb-cli", 123])


class TestIntervalSeconds:
    def test_seconds_suffix(self) -> None:
        assert interval_seconds("5s") == _FIVE_SECONDS

    def test_minutes_suffix(self) -> None:
        assert interval_seconds("2m") == _ONE_HUNDRED_TWENTY_SECONDS

    def test_none_defaults_to_one(self) -> None:
        assert interval_seconds(None) == 1

    def test_milliseconds_floor_to_one(self) -> None:
        assert interval_seconds("500ms") == 1

    def test_milliseconds_above_one_second(self) -> None:
        assert interval_seconds("5000ms") == _FIVE_SECONDS

    def test_bare_zero_string_floors_to_one(self) -> None:
        # Go's time.ParseDuration special-cases the literal '0' as a valid zero
        # duration; the result still floors at 1 for the polling loop.
        assert interval_seconds("0") == 1

    def test_unparseable_interval_raises(self) -> None:
        for bad in ("abc", "5x"):
            with pytest.raises(UnsupportedComposeError, match="unsupported healthcheck interval"):
                interval_seconds(bad)

    def test_hour_and_compound_and_larger_units(self) -> None:
        assert interval_seconds("1h") == _ONE_HOUR_SECONDS
        assert interval_seconds("1h30m") == _ONE_HOUR_THIRTY_MINUTES_SECONDS
        assert interval_seconds("1d") == _ONE_DAY_SECONDS
        assert interval_seconds("1w") == _ONE_WEEK_SECONDS
        assert interval_seconds("1.5d") == _ONE_AND_A_HALF_DAYS_SECONDS
        assert interval_seconds("2h45m30s500ms") == _COMPOUND_DURATION_SECONDS

    def test_negative_interval_floors_to_one(self) -> None:
        # Docker accepts `-1h`; a negative poll interval is nonsensical, floored to 1.
        assert interval_seconds("-1h") == 1

    def test_whitespace_and_uppercase_rejected(self) -> None:
        # Measured: Docker rejects any whitespace or uppercase in a duration.
        # " 5s "/"5s "/" 2m " are the real strip-defect demonstrators: the old
        # `.strip()`-based parser accepted them (strips to "5s"/"2m", both valid
        # units), silently diverging from Docker, which refuses any whitespace.
        for bad in (" 1h ", "1h ", " 1h", "1 h", "1h 30m", "1H", " 5s ", "5s ", " 2m "):
            with pytest.raises(UnsupportedComposeError, match="unsupported healthcheck interval"):
                interval_seconds(bad)

    def test_trailing_newline_rejected(self) -> None:
        # A YAML block scalar (`interval: |\n  5s\n`) parses to "5s\n". Python's
        # `$` (non-MULTILINE) matches before a trailing newline, so a naive regex
        # wrongly accepts it; Docker refuses it ('unknown unit "s\n"').
        for bad in ("5s\n", "1h\n", "1h30m\n"):
            with pytest.raises(UnsupportedComposeError, match="unsupported healthcheck interval"):
                interval_seconds(bad)

    def test_overflowing_duration_raises_not_crashes(self) -> None:
        # A giant integer floats to inf; must raise cleanly, not OverflowError.
        with pytest.raises(UnsupportedComposeError, match="unsupported healthcheck interval"):
            interval_seconds("1" + "0" * 400 + "s")

    def test_non_finite_interval_raises(self) -> None:
        # inf/nan are valid YAML floats; they must be refused, not crash raw.
        for bad in (float("inf"), float("nan"), "1e400"):
            with pytest.raises(UnsupportedComposeError, match="unsupported healthcheck interval"):
                interval_seconds(bad)

    # Measured against `docker compose config` v5.1.2: the interval field is a
    # Go duration *string* -- a native number is refused outright ("missing unit
    # in duration"), even a value that used to be silently accepted as bare seconds.
    def test_native_number_raises(self) -> None:
        for bad in (5, 0.4, 30, 0, 3, 0.5, True):
            with pytest.raises(UnsupportedComposeError, match="unsupported healthcheck interval"):
                interval_seconds(bad)

    # A unitless string is refused the same way -- Docker: "missing unit in
    # duration" -- except the bare '0' special case covered above.
    def test_unitless_string_raises(self) -> None:
        for bad in ("30", "3", "1.5", "0.5", "somevalue"):
            with pytest.raises(UnsupportedComposeError, match="unsupported healthcheck interval"):
                interval_seconds(bad)


class TestHasHealthcheck:
    def test_true_when_test_present(self) -> None:
        assert has_healthcheck({"healthcheck": {"test": ["CMD-SHELL", "true"]}}) is True

    def test_false_when_missing(self) -> None:
        assert has_healthcheck({"image": "x"}) is False

    def test_false_when_none_test(self) -> None:
        assert has_healthcheck({"healthcheck": {"test": "NONE"}}) is False
