"""service_completed_successfully ordering: a one-shot must finish before the target."""

from collections.abc import Callable

from tests.integration.conftest import PodRun


def test_completion_gating_orders_one_shot(run_pod: Callable[..., PodRun]) -> None:
    compose = {
        "services": {
            "init": {"image": "busybox:1.36", "command": ["sh", "-c", "exit 0"]},
            "app": {
                "image": "busybox:1.36",
                "depends_on": {"init": {"condition": "service_completed_successfully"}},
                "command": ["echo", "ready"],
            },
        },
    }
    run = run_pod(compose, target="app")
    # The one-shot runs blocking (`podman run --rm`) before the target; under
    # `set -e` a non-zero init would abort the script. Exit 0 + "ready" proves order.
    assert run.returncode == 0, run.stderr
    assert "ready" in run.stdout
