"""Conformance harness: compose2pod must refuse every document `docker compose config` refuses.

The rule is one-way (planning/decisions/2026-07-14-docker-rejection-parity.md):
Docker rejecting a document binds; Docker accepting one does not oblige us to,
because compose2pod converts an honest subset.

`docker compose config` parses and validates without contacting a daemon, so this
needs only the CLI on PATH.
"""

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from compose2pod.cli import _read_compose
from compose2pod.emit import EmitOptions, emit_script
from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.extends import resolve_extends
from compose2pod.parsing import validate


_DOCKER = shutil.which("docker")
_CONFORMANCE_DIR = Path(__file__).parent


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: "list[pytest.Item]") -> None:
    """Auto-mark every test under tests/conformance/ as `conformance` by location."""
    for item in items:
        if _CONFORMANCE_DIR in item.path.parents:
            item.add_marker(pytest.mark.conformance)


@pytest.fixture(autouse=True)
def _require_docker() -> None:
    """Skip every conformance test when the docker CLI is not installed."""
    if _DOCKER is None:
        pytest.skip("docker not installed")


def _docker_accepts(text: str, workdir: Path) -> bool:
    path = workdir / "compose.yaml"
    path.write_text(text)
    assert _DOCKER is not None  # narrows for the type checker; _require_docker already skipped otherwise
    proc = subprocess.run(  # noqa: S603 - _DOCKER is an absolute path from shutil.which
        [_DOCKER, "compose", "-f", str(path), "config"],
        capture_output=True,
        text=True,
        check=False,
        cwd=workdir,
        timeout=60,
    )
    return proc.returncode == 0


def _compose2pod_accepts(text: str, workdir: Path) -> bool:
    """Run the real CLI pipeline -- not `validate()` alone.

    The CLI runs validate() *and* emit_script(), and emit catches what validate
    does not (an unknown depends_on target, for one). Probing only validate()
    would understate compose2pod's strictness and report false violations.
    """
    try:
        compose = resolve_extends(_read_compose(text, "yaml"))
        validate(compose)
        emit_script(
            compose=compose,
            options=EmitOptions(
                target=next(iter(compose["services"])),
                ci_image="ci:latest",
                command="",
                pod="conformance-pod",
                project_dir=str(workdir),
                artifacts=[],
                allow_exit_codes=[],
            ),
        )
    except UnsupportedComposeError:
        return False
    return True


@pytest.fixture
def assert_rule(tmp_path: Path) -> Callable[[dict[str, Any]], str]:
    """Assert the one-way rule for one document; return its verdict as a label.

    Returns 'both-accept', 'both-reject', or 'over-reject' (Docker accepts, we
    refuse -- allowed, but only when catalogued as a known limitation in
    planning/deferred.md). Raises AssertionError on the one forbidden
    combination: Docker refuses and we accept.
    """

    def _assert(compose: dict[str, Any]) -> str:
        text = yaml.safe_dump(compose, sort_keys=False)
        docker_ok = _docker_accepts(text, tmp_path)
        c2p_ok = _compose2pod_accepts(text, tmp_path)
        if not docker_ok and c2p_ok:
            msg = f"docker compose config REJECTS this document but compose2pod ACCEPTS it:\n{text}"
            raise AssertionError(msg)
        if docker_ok and c2p_ok:
            return "both-accept"
        if not docker_ok:
            return "both-reject"
        return "over-reject"

    return _assert
