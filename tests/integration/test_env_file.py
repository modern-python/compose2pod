"""env_file: a real host file, resolved against project_dir, loaded into the container's env."""

from collections.abc import Callable
from pathlib import Path

from tests.integration.conftest import PodRun


def test_env_file_is_loaded(run_pod: Callable[..., PodRun], tmp_path: Path) -> None:
    (tmp_path / "app.env").write_text("COLOR=teal-9\n")
    compose = {
        "services": {
            "app": {
                "image": "busybox:1.36",
                "env_file": "app.env",
                "command": ["sh", "-c", 'echo "$COLOR"'],
            },
        },
    }
    run = run_pod(compose, target="app", project_dir=tmp_path)
    # Exit 0 + the value in stdout proves --env-file resolved against
    # project_dir and podman actually loaded the file into the container's
    # real environment.
    assert run.returncode == 0, run.stderr
    assert "teal-9" in run.stdout
