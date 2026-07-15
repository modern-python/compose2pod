"""The hand-authored corpus: documents whose invalidity is cross-key or nested.

The generated matrix probes one key at a time on a single service, so it can never
reach an extends cycle, a depends_on condition, a nested healthcheck/deploy/store
position, or a top-level key. These files cover that.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml


_CORPUS = sorted((Path(__file__).parent / "corpus").glob("*.yaml"))


@pytest.mark.parametrize("path", _CORPUS, ids=lambda p: p.stem)
def test_corpus_document_obeys_the_rule(path: Path, assert_rule: Callable[[dict[str, Any]], str]) -> None:
    assert_rule(yaml.safe_load(path.read_text()))


def test_networks_default_implicit_is_no_longer_an_over_rejection(
    assert_rule: Callable[[dict[str, Any]], str],
) -> None:
    """Fix 1 lifts an over-rejection, not just a forbidden combination.

    `assert_rule` alone only enforces the one-way rule (Docker rejects implies
    we reject) -- it never fails on the opposite direction (Docker accepts, we
    refuse), which is the allowed 'over-reject' verdict. That means the generic
    `test_corpus_document_obeys_the_rule` run above would stay green for this
    file even before Fix 1: it would just quietly file `networks_default_implicit`
    under 'over-reject' instead. The stronger claim this task makes -- that both
    oracles ACCEPT `networks: [default]` with no top-level declaration -- needs
    this dedicated assertion on the verdict itself.
    """
    path = Path(__file__).parent / "corpus" / "networks_default_implicit.yaml"
    assert assert_rule(yaml.safe_load(path.read_text())) == "both-accept"


def test_volume_tilde_bind_is_no_longer_an_over_rejection(
    assert_rule: Callable[[dict[str, Any]], str],
) -> None:
    """Task 14's `_validate_volume_references` over-classified `~`-prefixed sources as named volumes.

    Same reasoning as `test_networks_default_implicit_is_no_longer_an_over_rejection`
    above: the generic corpus run alone would stay green even pre-fix, filing this
    under the allowed 'over-reject' verdict instead of catching the regression. The
    stronger claim -- both oracles ACCEPT `volumes: [~/data:/var]` with no top-level
    declaration -- needs this dedicated assertion on the verdict itself.
    """
    path = Path(__file__).parent / "corpus" / "volume_tilde_bind_no_declaration.yaml"
    assert assert_rule(yaml.safe_load(path.read_text())) == "both-accept"
