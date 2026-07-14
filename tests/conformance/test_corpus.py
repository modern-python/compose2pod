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
