"""Pod-level dns/sysctls/extra_hosts merge onto podman pod create -- the area that broke first."""

from collections.abc import Callable

from tests.integration.conftest import PodRun


def test_pod_level_options_land_on_the_pod(run_pod: Callable[..., PodRun]) -> None:
    compose = {
        "services": {
            "app": {
                "image": "busybox:1.36",
                "dns": ["9.9.9.9"],
                "sysctls": {"net.core.somaxconn": "1024"},
                "extra_hosts": {"external-svc": "10.0.0.9"},
                "command": [
                    "sh",
                    "-c",
                    "grep 9.9.9.9 /etc/resolv.conf "
                    "&& grep 1024 /proc/sys/net/core/somaxconn "
                    "&& grep external-svc:10.0.0.9 /etc/hosts",
                ],
            },
        },
    }
    run = run_pod(compose, target="app")
    # Exit 0 proves all three landed on the pod (podman pod create), not the
    # container -- exactly the merge that regressed in the add-host bug.
    assert run.returncode == 0, run.stderr
