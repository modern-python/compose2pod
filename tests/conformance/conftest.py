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

from compose2pod.emit import EmitOptions, emit_script
from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.extends import resolve_extends
from compose2pod.parsing import validate
from compose2pod.read import read


_DOCKER = shutil.which("docker")
_CONFORMANCE_DIR = Path(__file__).parent

# Every `over-reject` verdict this run, as `<test-id>` labels -- the test id already
# carries the probed key and shape (matrix) or the corpus filename (corpus), so no
# extra bookkeeping is needed to make an entry diffable against planning/deferred.md.
# Stashed on `Config` (pytest's documented cross-hook slot) rather than a plain module
# global so it is unambiguously one collector per pytest run, not one per import.
_OVER_REJECTIONS: pytest.StashKey[list[str]] = pytest.StashKey()


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(items: "list[pytest.Item]") -> None:
    """Auto-mark every test under tests/conformance/ as `conformance` by location."""
    for item in items:
        if _CONFORMANCE_DIR in item.path.parents:
            item.add_marker(pytest.mark.conformance)


def pytest_configure(config: pytest.Config) -> None:
    """Create this run's over-rejection collector before any conformance test executes."""
    config.stash[_OVER_REJECTIONS] = []


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """Print every over-reject verdict collected this run.

    Over-rejections never fail the build (see `assert_rule`); this is the harness's
    only way of keeping them visible, per the promise in `planning/deferred.md` --
    "The conformance harness reports these as `over-reject`, so they stay visible
    rather than forgotten." Silent when nothing was collected, which is the normal
    case for `just test-ci` (the conformance suite is deselected there and this hook
    never runs a probe, so the list stays empty).
    """
    over_rejections = terminalreporter.config.stash.get(_OVER_REJECTIONS, [])
    if not over_rejections:
        return
    terminalreporter.section("conformance: over-rejections (docker accepts, compose2pod refuses)")
    for label in over_rejections:
        terminalreporter.write_line(label)
    terminalreporter.write_line(
        f"{len(over_rejections)} over-rejection(s) -- catalogued limitations, not failures; "
        "cross-check against planning/deferred.md"
    )


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
        compose = resolve_extends(read(text, "yaml"))
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
def assert_rule(tmp_path: Path, request: pytest.FixtureRequest) -> Callable[[dict[str, Any]], str]:
    """Assert the one-way rule for one document; return its verdict as a label.

    Returns 'both-accept', 'both-reject', or 'over-reject' (Docker accepts, we
    refuse -- allowed, but only when catalogued as a known limitation in
    planning/deferred.md; every 'over-reject' verdict is also recorded under the
    calling test's id for `pytest_terminal_summary` to print at the end of the run).
    Raises AssertionError on the one forbidden combination: Docker refuses and we
    accept.
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
        request.config.stash[_OVER_REJECTIONS].append(request.node.nodeid)
        return "over-reject"

    return _assert
