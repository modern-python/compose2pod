"""Runtime ${VAR} interpolation: a variable left live in the script, expanded when it runs.

`run_pod`'s subprocess call inherits the current process environment (no explicit
`env=` override), so setting the variable via `monkeypatch.setenv` just before calling
it proves the full chain for real: the `${VAR}` reference survives compose2pod's
generation step as a live shell expansion (never baked into a literal), the outer
script's shell expands it into a valid `podman run -e TOKEN=...` invocation, and podman
sets that as the container's actual environment variable.
"""

from collections.abc import Callable

import pytest

from tests.integration.conftest import PodRun


def test_variable_interpolation_resolves_at_run_time(
    run_pod: Callable[..., PodRun], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("C2P_IT_TOKEN", "runtime-value-73")
    compose = {
        "services": {
            "app": {
                "image": "busybox:1.36",
                "environment": ["TOKEN=${C2P_IT_TOKEN}"],
                "command": ["sh", "-c", 'echo "$TOKEN"'],
            },
        },
    }
    run = run_pod(compose, target="app")
    assert run.returncode == 0, run.stderr
    assert "runtime-value-73" in run.stdout
