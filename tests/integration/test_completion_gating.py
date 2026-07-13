"""service_completed_successfully: the target observably requires the one-shot's output.

The one-shot sleeps, then writes a flag to a shared bind mount; the target reads it.
If completion-gating regressed (one-shot backgrounded instead of run blocking), the
target would run during the sleep, cat a missing file, exit non-zero, and fail the
script under `set -e` -- so exit 0 + the flag content genuinely proves ordering.
"""

from collections.abc import Callable
from pathlib import Path

from tests.integration.conftest import PodRun


def test_completion_gating_orders_one_shot(run_pod: Callable[..., PodRun], tmp_path: Path) -> None:
    (tmp_path / "shared").mkdir()
    compose = {
        "services": {
            "init": {
                "image": "busybox:1.36",
                "volumes": ["./shared:/shared"],
                "command": ["sh", "-c", "sleep 2; echo done > /shared/flag"],
            },
            "app": {
                "image": "busybox:1.36",
                "volumes": ["./shared:/shared"],
                "depends_on": {"init": {"condition": "service_completed_successfully"}},
                "command": ["sh", "-c", "cat /shared/flag"],
            },
        },
    }
    run = run_pod(compose, target="app", project_dir=tmp_path)
    assert run.returncode == 0, run.stderr
    assert "done" in run.stdout
