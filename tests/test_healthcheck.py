import pytest

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.healthcheck import has_healthcheck, health_cmd, interval_seconds


_FIVE_SECONDS = 5
_ONE_HUNDRED_TWENTY_SECONDS = 120


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

    def test_int_value_passes_through(self) -> None:
        assert interval_seconds(5) == _FIVE_SECONDS

    def test_float_value_below_one_floors_to_one(self) -> None:
        assert interval_seconds(0.4) == 1

    def test_unparseable_interval_raises(self) -> None:
        for bad in ("1h30m", "abc", "5x"):
            with pytest.raises(UnsupportedComposeError, match="unsupported healthcheck interval"):
                interval_seconds(bad)

    def test_non_finite_interval_raises(self) -> None:
        # inf/nan are valid YAML floats; they must be refused, not crash raw.
        for bad in (float("inf"), float("nan"), "1e400"):
            with pytest.raises(UnsupportedComposeError, match="unsupported healthcheck interval"):
                interval_seconds(bad)


class TestHasHealthcheck:
    def test_true_when_test_present(self) -> None:
        assert has_healthcheck({"healthcheck": {"test": ["CMD-SHELL", "true"]}}) is True

    def test_false_when_missing(self) -> None:
        assert has_healthcheck({"image": "x"}) is False

    def test_false_when_none_test(self) -> None:
        assert has_healthcheck({"healthcheck": {"test": "NONE"}}) is False
