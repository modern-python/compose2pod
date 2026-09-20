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
from tests.integration.refusals import (
    ABSENT_FLAGS,
    LIMITATIONS,
    REFUSALS,
    STUB_FLAGS,
    AbsentFlag,
    Limitation,
    Refusal,
    StubFlag,
)


def _resolve(argv: list[str], host: Path) -> list[str]:
    return [part.format(host=host) for part in argv]


@pytest.mark.parametrize("refusal", REFUSALS, ids=lambda refusal: refusal.id)
def test_a_documented_refusal_names_a_mount_podman_will_not_make(
    refusal: Refusal, probe_podman: Callable[[str, list[str]], int], tmp_path: Path
) -> None:
    with pytest.raises(UnsupportedComposeError, match=refusal.refusal_match):
        validate(refusal.compose)

    control = _resolve(refusal.control_argv, tmp_path)
    assert probe_podman(f"{refusal.id} [control]", control) == 0, (
        "the control mount failed, so this row proves nothing about podman's verdict"
    )
    argv = _resolve(refusal.podman_argv, tmp_path)
    assert probe_podman(refusal.id, argv) != 0, (
        "podman made the mount this document asks for, so refusing it is a limitation, not rule two"
    )


@pytest.mark.parametrize("limitation", LIMITATIONS, ids=lambda limitation: limitation.id)
def test_a_tracked_limitation_names_a_mount_podman_would_have_made(
    limitation: Limitation, probe_podman: Callable[[str, list[str]], int], tmp_path: Path
) -> None:
    with pytest.raises(UnsupportedComposeError, match=limitation.refusal_match):
        validate(limitation.compose)

    source = tmp_path / limitation.host_dir
    source.mkdir(parents=True, exist_ok=True)
    argv = _resolve(limitation.podman_argv, source)

    assert probe_podman(f"{limitation.id} [limitation]", argv) == 0, (
        "podman refuses this mount too, so it is rule two and belongs in REFUSALS"
    )


@pytest.mark.parametrize("absent", ABSENT_FLAGS, ids=lambda absent: absent.id)
def test_a_refusal_citing_no_flag_names_flags_podman_does_not_have(
    absent: AbsentFlag, probe_podman: Callable[[str, list[str]], int]
) -> None:
    with pytest.raises(UnsupportedComposeError, match=absent.refusal_match):
        validate(absent.compose)

    assert probe_podman(f"{absent.id} [control]", []) == 0, (
        "the bare control run failed, so every argv below would look absent whatever podman has"
    )
    for argv in absent.unknown_argv:
        assert probe_podman(f"{absent.id} {argv[0]}", argv) != 0, (
            "podman took this flag, so it has an equivalent and the refusal needs re-examining"
        )


@pytest.mark.parametrize("stub", STUB_FLAGS, ids=lambda stub: stub.id)
def test_a_refusal_citing_a_stub_flag_names_one_podman_does_not_validate(
    stub: StubFlag, probe_podman: Callable[[str, list[str]], int]
) -> None:
    with pytest.raises(UnsupportedComposeError, match=stub.refusal_match):
        validate(stub.compose)

    assert probe_podman(stub.id, stub.nonsense_argv) == 0, (
        "podman rejected deliberate nonsense, so the flag validates something after all"
    )
