"""Integration harness: render a compose doc, run the generated script on real podman.

The generated script installs `trap '<podman pod rm -f>' EXIT` and runs the target
in the foreground, so the pod is gone the instant `sh run.sh` returns. Assertions are
therefore behavioral: under the script's `set -eu`, a clean exit proves pod creation,
dependency startup, `service_healthy` gating, completion-gating, and a passing target
probe. Each scenario encodes its check as the target service's own command.
"""

import shutil
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest

from compose2pod import EmitOptions, emit_script


_PODMAN = shutil.which("podman")
_SH = shutil.which("sh")
_INTEGRATION_DIR = Path(__file__).parent


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: "list[pytest.Item]") -> None:
    """Auto-mark every test under tests/integration/ as `integration` by location."""
    for item in items:
        if _INTEGRATION_DIR in item.path.parents:
            item.add_marker(pytest.mark.integration)


@dataclass(frozen=True)
class PodRun:
    """Result of running one generated script to completion."""

    pod: str
    returncode: int
    stdout: str
    stderr: str


@pytest.fixture(autouse=True)
def _require_podman() -> None:
    """Skip every integration test when podman is not installed."""
    if _PODMAN is None:
        pytest.skip("podman not installed")


@pytest.fixture
def run_pod(tmp_path: Path) -> Iterator[Callable[..., PodRun]]:
    """Render `compose` for `target`, run the script, return a `PodRun`.

    Force-removes every pod it created on teardown as a defensive backstop, in
    case the script's own EXIT trap did not fire (timeout kill or early crash).
    """
    created: list[str] = []

    def _run(
        compose: dict,
        *,
        target: str,
        command: str = "",
        project_dir: "str | Path | None" = None,
        timeout: int = 180,
    ) -> PodRun:
        pod = f"c2p-it-{uuid4().hex[:8]}"
        created.append(pod)
        options = EmitOptions(
            target=target,
            ci_image="unused:latest",  # only used by services with a `build:` section
            command=command,
            pod=pod,
            project_dir=str(project_dir if project_dir is not None else tmp_path),
            artifacts=[],
            allow_exit_codes=[],
        )
        script = tmp_path / f"{pod}.sh"
        script.write_text(emit_script(compose, options))
        assert _SH is not None  # narrows for the type checker; `sh` presence is a harness precondition
        proc = subprocess.run(  # noqa: S603 - _SH is an absolute path from shutil.which, not untrusted input
            [_SH, str(script)],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return PodRun(pod=pod, returncode=proc.returncode, stdout=proc.stdout, stderr=proc.stderr)

    yield _run

    for pod in created:
        assert _PODMAN is not None  # narrows for the type checker; _require_podman already skipped otherwise
        subprocess.run([_PODMAN, "pod", "rm", "-f", pod], capture_output=True, check=False)  # noqa: S603
