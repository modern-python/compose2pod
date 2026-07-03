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
        if len(test) < _CMD_MIN_LENGTH:
            msg = f"unsupported healthcheck test: {test!r}"
            raise UnsupportedComposeError(msg)
        return test[1]  # ty: ignore
    if kind == "CMD":
        if len(test) < _CMD_MIN_LENGTH:
            msg = f"unsupported healthcheck test: {test!r}"
            raise UnsupportedComposeError(msg)
        return json.dumps(test[1:])
    msg = f"unsupported healthcheck test kind: {kind!r}"
    raise UnsupportedComposeError(msg)


def interval_seconds(duration: object) -> int:
    """Compose duration ('1s', '2m', '500ms', int) to whole seconds, minimum 1."""
    if duration is None:
        return 1
    if isinstance(duration, (int, float)):
        return max(int(duration), 1)
    text = str(duration).strip()
    if text.endswith("ms"):
        return max(int(float(text[:-2]) / 1000), 1)
    if text.endswith("m"):
        return max(int(float(text[:-1])) * 60, 1)
    text = text.removesuffix("s")
    return max(int(float(text)), 1)
