"""Long-form volumes: a {type: bind} mapping round-trips a host file into the container."""

from collections.abc import Callable
from pathlib import Path

from tests.integration.conftest import PodRun


def test_long_form_bind_mount_is_read(run_pod: Callable[..., PodRun], tmp_path: Path) -> None:
    (tmp_path / "data.txt").write_text("mount-ok-73\n")
    compose = {
        "services": {
            "app": {
                "image": "busybox:1.36",
                "volumes": [{"type": "bind", "source": "./data.txt", "target": "/data.txt", "read_only": True}],
                "command": ["cat", "/data.txt"],
            },
        },
    }
    run = run_pod(compose, target="app", project_dir=tmp_path)
    assert run.returncode == 0, run.stderr
    assert "mount-ok-73" in run.stdout
