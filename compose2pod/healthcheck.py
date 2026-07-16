"""Healthcheck translation: compose healthcheck -> podman --health-* values."""

import json
import math
import re
from typing import Any

from compose2pod.exceptions import UnsupportedComposeError


_CMD_MIN_LENGTH = 2


def has_healthcheck(svc: dict[str, Any]) -> bool:
    """Report whether the service defines a healthcheck with a non-disabled test."""
    test = (svc.get("healthcheck") or {}).get("test")
    return test is not None and test not in ("NONE", ["NONE"])


def health_cmd(test: object) -> str | None:
    """Compose healthcheck `test` value to a podman --health-cmd value."""
    if test is None or test in ("NONE", ["NONE"]):
        return None
    if isinstance(test, str):
        return test
    if not isinstance(test, list) or not test:
        msg = f"unsupported healthcheck test: {test!r}"
        raise UnsupportedComposeError(msg)
    kind = test[0]
    if kind == "CMD-SHELL":
        if len(test) < _CMD_MIN_LENGTH or not isinstance(test[1], str):
            msg = f"unsupported healthcheck test: {test!r}"
            raise UnsupportedComposeError(msg)
        return test[1]
    if kind == "CMD":
        if len(test) < _CMD_MIN_LENGTH or not all(isinstance(item, str) for item in test[1:]):
            msg = f"unsupported healthcheck test: {test!r}"
            raise UnsupportedComposeError(msg)
        return json.dumps(test[1:])
    msg = f"unsupported healthcheck test kind: {kind!r}"
    raise UnsupportedComposeError(msg)


# compose-go's duration grammar (measured vs `docker compose config` v5.1.2): a
# signed sequence of <number><unit> components. Broader than Go's
# time.ParseDuration -- compose-go adds `d` (days) and `w` (weeks). `interval`
# is converted to seconds to pace the wait_healthy loop and never reaches podman,
# so compose2pod can honor the full set -- unlike timeout/start_period, which
# flow to podman's Go-parser --health-* flags (see values._DURATION).
_UNITS = "ns|us|µs|ms|s|m|h|d|w"
_INTERVAL_DURATION = re.compile(rf"^[+-]?(?:[0-9]+(?:\.[0-9]+)?(?:{_UNITS}))+$")
_DURATION_COMPONENT = re.compile(rf"([0-9]+(?:\.[0-9]+)?)({_UNITS})")
_UNIT_SECONDS: dict[str, float] = {
    "ns": 1e-9,
    "us": 1e-6,
    "µs": 1e-6,
    "ms": 1e-3,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
    "w": 604800.0,
}


def interval_seconds(duration: object) -> int:
    """Compose healthcheck `interval` to whole seconds, minimum 1.

    The interval paces compose2pod's `wait_healthy` polling loop and never
    reaches podman, so it accepts the full compose-go duration grammar (all
    units incl. `d`/`w`, compound like `1h30m`, fractional, sign) -- measured
    against `docker compose config` v5.1.2. Whitespace and uppercase units are
    refused, as Docker refuses them. `None` and the literal `"0"` default to 1;
    a native number, a unitless string, or a value overflowing to infinity raises.
    """
    if duration is None:
        return 1
    msg = f"unsupported healthcheck interval {duration!r} (use forms like '30s', '2m', '1h30m', '500ms')"
    if not isinstance(duration, str):
        raise UnsupportedComposeError(msg)
    if duration == "0":
        return 1
    if not _INTERVAL_DURATION.match(duration):
        raise UnsupportedComposeError(msg)
    total = sum(float(num) * _UNIT_SECONDS[unit] for num, unit in _DURATION_COMPONENT.findall(duration))
    if not math.isfinite(total):
        raise UnsupportedComposeError(msg)
    if duration.startswith("-"):
        total = -total
    return max(int(total), 1)
