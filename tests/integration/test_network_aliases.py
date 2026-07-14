"""Long-form networks: aliases -- a distinct code path from hostname/container_name."""

from collections.abc import Callable

from tests.integration.conftest import PodRun


def test_network_alias_lands_on_the_pod(run_pod: Callable[..., PodRun]) -> None:
    compose = {
        "services": {
            "app": {
                "image": "busybox:1.36",
                "networks": {"default": {"aliases": ["cache-alias"]}},
                "command": ["sh", "-c", "grep cache-alias /etc/hosts && grep 127.0.0.1 /etc/hosts"],
            },
        },
        "networks": {"default": None},
    }
    run = run_pod(compose, target="app")
    # Exit 0 proves the long-form networks.aliases entry (a distinct code path
    # from hostname/container_name in graph.py's _host_names) reaches the
    # pod-level --add-host set, resolving to 127.0.0.1 like every other alias.
    assert run.returncode == 0, run.stderr
