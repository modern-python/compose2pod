"""Secrets and configs: both compile to podman secret create, mounted at different paths."""

from collections.abc import Callable
from pathlib import Path

from tests.integration.conftest import PodRun


def test_secret_and_config_are_mounted(run_pod: Callable[..., PodRun], tmp_path: Path) -> None:
    (tmp_path / "secret.txt").write_text("shh-secret-99\n")
    compose = {
        "services": {
            "app": {
                "image": "busybox:1.36",
                "secrets": ["mysecret"],
                "configs": ["myconfig"],
                "command": ["sh", "-c", "cat /run/secrets/mysecret && cat /myconfig"],
            },
        },
        "secrets": {"mysecret": {"file": "secret.txt"}},
        "configs": {"myconfig": {"content": "config-value-7\n"}},
    }
    run = run_pod(compose, target="app", project_dir=tmp_path)
    # Exit 0 + both contents in stdout proves: podman secret create (file source
    # for the secret, content source for the config), the --secret mount at both
    # default target shapes (/run/secrets/<name> and /<name>), and that secret rm
    # teardown doesn't interfere with the run.
    assert run.returncode == 0, run.stderr
    assert "shh-secret-99" in run.stdout
    assert "config-value-7" in run.stdout
