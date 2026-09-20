"""Rule two, measured: which refusals podman agrees with, and which are only ours.

ADR-0006 lets compose2pod refuse a document `docker compose config` accepts when
podman cannot express it, and calls a refusal podman *would* honour a tracked
limitation instead. That distinction is only as good as the measurement behind it,
and until now there was none -- issue #86 asserted expressibility, #104 shipped it,
and `podman run` exited 125.
"""

from collections.abc import Callable
from pathlib import Path

import pytest

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.parsing import validate
from tests.integration.refusals import LIMITATIONS, REFUSALS, Limitation, Refusal


@pytest.mark.parametrize("refusal", REFUSALS, ids=lambda refusal: refusal.id)
def test_a_documented_refusal_names_a_mount_podman_will_not_make(
    refusal: Refusal, probe_podman: Callable[[str, list[str]], int]
) -> None:
    with pytest.raises(UnsupportedComposeError, match=refusal.refusal_match):
        validate(refusal.compose)

    assert probe_podman(f"{refusal.id} [control]", refusal.control_argv) == 0, (
        "the control mount failed, so this row proves nothing about podman's verdict"
    )
    assert probe_podman(refusal.id, refusal.podman_argv) != 0, (
        "podman made the mount this document asks for, so refusing it is a limitation, not rule two"
    )


@pytest.mark.parametrize("limitation", LIMITATIONS, ids=lambda limitation: limitation.id)
def test_a_tracked_limitation_names_a_mount_podman_would_have_made(
    limitation: Limitation, probe_podman: Callable[[str, list[str]], int], tmp_path: Path
) -> None:
    with pytest.raises(UnsupportedComposeError, match=limitation.refusal_match):
        validate(limitation.compose)

    source = tmp_path / limitation.host_dir
    source.mkdir(parents=True)
    argv = [part.format(host=source) for part in limitation.podman_argv]

    assert probe_podman(f"{limitation.id} [limitation]", argv) == 0, (
        "podman refuses this mount too, so it is rule two and belongs in REFUSALS"
    )
