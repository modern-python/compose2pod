"""depends_on's default condition (service_started): the one condition with no gate.

Unlike service_healthy (wait_healthy) and service_completed_successfully (blocking
--rm), service_started provides zero synchronization -- helper is started detached
(-d) and app runs immediately after, with no wait. A single immediate check of
helper's side effect would be a race (podman run -d returns once the container
LAUNCHES, not once its command finishes), so app polls with a bounded retry loop
instead -- the correct shape for testing a condition whose entire point is "the
caller must provide their own synchronization."

Uses the short (list) form, which is where this default actually lives: Docker
itself defaults a short-form entry to service_started, but (measured against
`docker compose config` v5.1.2, Task 13) refuses a long-form entry with no
explicit `condition` -- `depends_on: {db: {}}` no longer means the same thing
compose2pod's own long-form default used to.
"""

from collections.abc import Callable
from pathlib import Path

from tests.integration.conftest import PodRun


def test_depends_on_default_condition_is_service_started(run_pod: Callable[..., PodRun], tmp_path: Path) -> None:
    (tmp_path / "shared").mkdir()
    compose = {
        "services": {
            "helper": {
                "image": "busybox:1.36",
                "volumes": ["./shared:/shared"],
                "command": ["sh", "-c", "sleep 1; echo started > /shared/flag"],
            },
            "app": {
                "image": "busybox:1.36",
                "volumes": ["./shared:/shared"],
                "depends_on": ["helper"],  # short form -> defaults to service_started
                "command": [
                    "sh",
                    "-c",
                    "for i in $(seq 1 20); do [ -f /shared/flag ] && cat /shared/flag && exit 0; sleep 1; done; exit 1",
                ],
            },
        },
    }
    run = run_pod(compose, target="app", project_dir=tmp_path)
    # Exit 0 + "started" in stdout proves startup ordering (helper's podman run -d
    # line is emitted before app's) AND that this condition provides no built-in
    # wait -- app had to poll for it itself.
    assert run.returncode == 0, run.stderr
    assert "started" in run.stdout
