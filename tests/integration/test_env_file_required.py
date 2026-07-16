"""env_file required:false: an absent optional file is skipped, a present one is loaded."""

from collections.abc import Callable
from pathlib import Path

from tests.integration.conftest import PodRun


def test_required_false_absent_file_is_skipped(run_pod: Callable[..., PodRun], tmp_path: Path) -> None:
    # No opt.env on disk. The guard must drop --env-file, and `set -eu` must not abort.
    compose = {
        "services": {
            "app": {
                "image": "busybox:1.36",
                "env_file": [{"path": "opt.env", "required": False}],
                "command": ["sh", "-c", "echo ok"],
            },
        },
    }
    run = run_pod(compose, target="app", project_dir=tmp_path)
    assert run.returncode == 0, run.stderr
    assert "ok" in run.stdout


def test_required_false_present_file_is_loaded(run_pod: Callable[..., PodRun], tmp_path: Path) -> None:
    (tmp_path / "opt.env").write_text("COLOR=teal-9\n")
    compose = {
        "services": {
            "app": {
                "image": "busybox:1.36",
                "env_file": [{"path": "opt.env", "required": False}],
                # `$$` escapes to a literal `$` (Compose syntax): a bare `$COLOR` here
                # would instead be compose2pod's OWN interpolation, resolved by the
                # OUTER script against ITS environment -- not what we want, since COLOR
                # only exists inside the container via --env-file.
                "command": ["sh", "-c", 'echo "$$COLOR"'],
            },
        },
    }
    run = run_pod(compose, target="app", project_dir=tmp_path)
    assert run.returncode == 0, run.stderr
    assert "teal-9" in run.stdout
