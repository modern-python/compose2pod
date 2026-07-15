"""Healthcheck translation: compose healthcheck -> podman --health-* values."""

import json
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


def interval_seconds(duration: object) -> int:
    """Compose duration ('1s', '2m', '500ms', '0') to whole seconds, minimum 1.

    Docker's own field is a Go duration *string*: a native number and a
    unitless string ('30') are both refused ("missing unit in duration"),
    except the literal '0' zero-duration special case -- measured against
    `docker compose config` v5.1.2. Compound durations ('1h30m') and the hour
    unit ('1h') are refused here even though Docker accepts them; that is a
    deliberate, deferred limitation (planning/deferred.md), not part of the
    grammar this function otherwise enforces.
    """
    if duration is None:
        return 1
    msg = f"unsupported healthcheck interval {duration!r} (use forms like '30s', '2m', '500ms')"
    if not isinstance(duration, str):
        raise UnsupportedComposeError(msg)
    text = duration.strip()
    if text == "0":
        return 1
    try:
        if text.endswith("ms"):
            return max(int(float(text[:-2]) / 1000), 1)
        if text.endswith("m"):
            return max(int(float(text[:-1])) * 60, 1)
        if text.endswith("s"):
            return max(int(float(text.removesuffix("s"))), 1)
    except (ValueError, OverflowError):
        raise UnsupportedComposeError(msg) from None
    raise UnsupportedComposeError(msg)
