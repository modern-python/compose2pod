"""Resource limits: deploy.resources.limits is acceptance-only, ulimits is value-checked.

Verifying the exact --memory/--pids-limit value would require reading cgroup files
inside the container, which assumes a cgroup v2 unified hierarchy on the CI runner --
an assumption that could make this scenario flake for reasons unrelated to compose2pod's
correctness. Acceptance-only (the script simply runs to completion) targets the actual
risk class this harness exists to catch: podman refusing a flag combination outright,
the same failure mode as the add-host bug. ulimits has no such fragility -- `ulimit -n`
is a shell builtin, independent of cgroups -- so its value is checked directly.
"""

from collections.abc import Callable

from tests.integration.conftest import PodRun


def test_resource_limits_are_accepted_and_ulimit_is_applied(run_pod: Callable[..., PodRun]) -> None:
    compose = {
        "services": {
            "app": {
                "image": "busybox:1.36",
                "deploy": {"resources": {"limits": {"memory": "128m", "cpus": "0.5", "pids": 100}}},
                "ulimits": {"nofile": 1024},
                "command": ["sh", "-c", '[ "$(ulimit -n)" = 1024 ] && echo ulimit-ok'],
            },
        },
    }
    run = run_pod(compose, target="app")
    # Exit 0 proves podman accepted --memory/--cpus/--pids-limit (or the run would
    # fail to start) AND that --ulimit nofile=1024 took effect inside the container.
    assert run.returncode == 0, run.stderr
    assert "ulimit-ok" in run.stdout
