"""Long-form volumes type: image -- an image's rootfs mounts into the container."""

from collections.abc import Callable
from pathlib import Path

from tests.integration.conftest import PodRun


def test_image_mount_exposes_the_image_rootfs(run_pod: Callable[..., PodRun], tmp_path: Path) -> None:
    compose = {
        "services": {
            "app": {
                "image": "busybox:1.36",
                "volumes": [{"type": "image", "source": "busybox:1.36", "target": "/img"}],
                "command": ["test", "-e", "/img/bin/busybox"],  # exit 0 iff the image rootfs mounted
            },
        },
    }
    run = run_pod(compose, target="app", project_dir=tmp_path)
    assert run.returncode == 0, run.stderr
