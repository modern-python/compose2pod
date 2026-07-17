"""Long-form volume nested options: a bind mount with a propagation option round-trips a host file."""

from collections.abc import Callable
from pathlib import Path

from tests.integration.conftest import PodRun


def test_bind_mount_with_propagation_is_read(run_pod: Callable[..., PodRun], tmp_path: Path) -> None:
    (tmp_path / "data.txt").write_text("nested-ok-91\n")
    compose = {
        "services": {
            "app": {
                "image": "busybox:1.36",
                "volumes": [
                    {
                        "type": "bind",
                        "source": "./data.txt",
                        "target": "/data.txt",
                        "bind": {"propagation": "rprivate"},
                    },
                ],
                "command": ["cat", "/data.txt"],
            },
        },
    }
    run = run_pod(compose, target="app", project_dir=tmp_path)
    assert run.returncode == 0, run.stderr
    assert "nested-ok-91" in run.stdout
