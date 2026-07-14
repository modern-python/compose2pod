"""The generated probe matrix: every known key x every hostile shape.

The key list is read from the registry itself, so a key added to SERVICE_KEYS or
IGNORED_SERVICE_KEYS is probed the moment it is added and the rule cannot decay
as the subset grows. That is the whole point of generating it rather than listing it.
"""

from collections.abc import Callable
from typing import Any

import pytest

from compose2pod.keys import SERVICE_KEYS, STRUCTURAL_KEYS
from compose2pod.parsing import IGNORED_SERVICE_KEYS


KEYS = sorted(set(SERVICE_KEYS) | STRUCTURAL_KEYS | set(IGNORED_SERVICE_KEYS))

SHAPES: dict[str, Any] = {
    "null": None,
    "empty-str": "",
    "empty-list": [],
    "empty-map": {},
    "bool": True,
    "int": 3,
    "float": 1.5,
    "str": "somevalue",
    "list-of-str": ["a"],
    "list-of-map": [{"a": 1}],
    "map-str": {"a": "b"},
    "nested-map": {"a": {"b": 1}},
}

# `env_file` is the decision's carve-out: `docker compose config` rejects a
# missing env file, which is a fact about the *reading host's filesystem*, not
# about the document. compose2pod emits a script that runs elsewhere, where the
# file is checked out. Docker's verdict there cannot bind, so the key is not
# probed by the matrix.
CARVE_OUT_KEYS = {"env_file"}


@pytest.mark.parametrize("key", [k for k in KEYS if k not in CARVE_OUT_KEYS])
@pytest.mark.parametrize("shape", list(SHAPES))
def test_docker_rejection_implies_our_rejection(
    key: str,
    shape: str,
    assert_rule: Callable[[dict[str, Any]], str],
) -> None:
    compose = {"services": {"app": {"image": "nginx:alpine", key: SHAPES[shape]}}}
    assert_rule(compose)
