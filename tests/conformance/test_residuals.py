"""The documents where rule one is knowingly broken, probed instead of described.

ADR-0006 calls `accepted(compose2pod) ⊆ accepted(docker)` hard, and issue 87 records two
exceptions as a deliberate ruling: a `depends_on` naming an undefined service, and a
dependency cycle, both on services outside the `--target`'s closure, which `startup_order`
never walks. `assert_rule` raises on exactly that combination, so neither could live in
`corpus/` -- and so neither was measured anywhere, which left the one hard rule's known
breach resting on prose. These files are that breach, executed.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml


_RESIDUAL_CORPUS = sorted((Path(__file__).parent / "corpus_residual").glob("*.yaml"))


@pytest.mark.parametrize("path", _RESIDUAL_CORPUS, ids=lambda p: p.stem)
def test_a_catalogued_residual_still_breaks_rule_one(
    path: Path, assert_residual: Callable[[dict[str, Any]], None]
) -> None:
    """A file here fails when the residual *closes*, which is when it should be deleted.

    Tolerating a catalogued exception is not the point -- an entry nobody re-runs goes
    stale in the direction that looks green, which is how issue 86's unmeasured claim
    survived long enough to ship. Cataloguing it is only worth anything if the catalogue
    is wrong when the world changes.
    """
    assert_residual(yaml.safe_load(path.read_text()))
