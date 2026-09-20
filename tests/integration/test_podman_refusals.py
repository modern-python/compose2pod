"""Rule two, measured: every documented refusal names a mount real podman will not make.

ADR-0006 lets compose2pod refuse a document `docker compose config` accepts when
podman cannot express it. That licence is only as good as the measurement behind
it, and until now there was none -- issue #86 asserted expressibility, #104
shipped it, and `podman run` exited 125.
"""

from collections.abc import Callable

import pytest

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.parsing import validate
from tests.integration.refusals import REFUSALS, Refusal


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


def test_every_row_has_a_distinct_id() -> None:
    """Ids label the summary lines and the parametrize cases; a duplicate hides one row behind another."""
    ids = [refusal.id for refusal in REFUSALS]

    assert sorted(ids) == sorted(set(ids))
