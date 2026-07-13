"""Short-form bind volume: a host file resolved against project_dir is mounted and read."""

from collections.abc import Callable
from pathlib import Path

from tests.integration.conftest import PodRun


def test_bind_volume_is_mounted(run_pod: Callable[..., PodRun], tmp_path: Path) -> None:
    (tmp_path / "data.txt").write_text("sentinel-42\n")
    compose = {
        "services": {
            "app": {
                "image": "busybox:1.36",
                "volumes": ["./data.txt:/mnt/data.txt"],  # relative -> resolved against project_dir
                "command": ["cat", "/mnt/data.txt"],
            },
        },
    }
    run = run_pod(compose, target="app", project_dir=tmp_path)
    assert run.returncode == 0, run.stderr
    assert "sentinel-42" in run.stdout
