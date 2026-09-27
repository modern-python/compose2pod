"""The generated probe matrix: every known key x every hostile shape.

The key list is read from the registry itself, so a key added to SERVICE_KEYS or
IGNORED_SERVICE_KEYS is probed the moment it is added and the rule cannot decay
as the subset grows. That is the whole point of generating it rather than listing it.
"""

from collections.abc import Callable
from typing import Any

import pytest

from compose2pod.keys import SERVICE_KEYS, STRUCTURAL_KEYS
from compose2pod.parsing import IGNORED_SERVICE_KEYS, NAME_CHECKED_TOP_LEVEL_BLOCKS


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
    # Docker casts a YAML-1.1-style *string* on a boolean field ("true", "yes", "on",
    # bare `yes`) rather than requiring a real YAML boolean. compose2pod now matches:
    # every boolean field routes through `values.is_bool_like` (measured against
    # `docker compose config` v5.1.2), so this shape verifies conformance rather than
    # surfacing an over-reject. One spelling is enough to guard every boolean key --
    # the other accepted spellings ("yes", "on") would only repeat the identical
    # compose2pod-side verdict (accepted, same as Docker), so adding them would inflate
    # the report without adding signal. The generic "str" shape above ("somevalue")
    # does *not* reach this: Docker refuses an arbitrary string on a boolean key just as
    # compose2pod does, so it never exercises the coercion path.
    "quoted-bool": "true",
    # Every prior shape is structural (null/empty/bool/int/list/map/bare-string) -- none
    # carry padded or internal whitespace, which is exactly how the size/number/integer
    # false green survived the matrix undetected: Docker's
    # Go parsers refuse a leading/trailing/doubled space Python's float()/int() silently
    # strip. "512m" is a plain value on a size/number/integer key -- padded with a leading
    # and trailing space, it reaches every key's own grammar, not just the three this shape
    # was added to guard.
    "padded-scalar": " 512m ",
}

# `env_file`'s shapes that carry a bare string -- "" (empty-str), "somevalue"
# (str), "true" (quoted-bool), and ["a"] (list-of-str) -- are the decision's
# carve-out: `docker compose config` treats every one of those strings as a
# path and rejects it when the file is missing from the *reading host's*
# filesystem -- a fact about the host, not the document. Verified by hand
# against `docker compose config` v5.1.2 that all four raise that
# host-dependent error ("env file ... not found") regardless of the string's
# content, including the empty string. compose2pod emits a script that runs
# elsewhere, where the file is checked out at run time, so Docker's verdict
# there cannot bind. Every other env_file shape -- including the long-form
# mapping (`{path: ..., required: ..., format: ...}`) -- is a question about
# the document alone, not the host, and is probed like any other key.
CARVE_OUT: set[tuple[str, str]] = {
    ("env_file", "empty-str"),
    ("env_file", "str"),
    ("env_file", "quoted-bool"),
    ("env_file", "list-of-str"),
    # Same file-existence host-state carve-out: docker reads " 512m " as a (missing)
    # path ("env file ... not found: stat ..."), exactly as the four string shapes above.
    ("env_file", "padded-scalar"),
}


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("shape", list(SHAPES))
def test_docker_rejection_implies_our_rejection(
    key: str,
    shape: str,
    assert_rule: Callable[[dict[str, Any]], str],
) -> None:
    if (key, shape) in CARVE_OUT:
        pytest.skip("host-dependent shape -- see CARVE_OUT's comment")
    compose = {"services": {"app": {"image": "nginx:alpine", key: SHAPES[shape]}}}
    assert_rule(compose)


REFUSED_NAMES = ["a b", "a/b", "a:b", "a#b", "ab!", "a+b", "a~b", "a@b", "a$b", "", "\xe4"]
# The four Docker takes that the old store-only pattern refused -- the over-rejection
# issue 143 lifted. They carry `both-accept` below, so lifting it cannot silently undo.
ACCEPTED_NAMES = [".a", "-a", "_a", "a.b"]
# The full set on the two positions the grammar is enforced from, a hostile pair on the
# remaining blocks: Docker applies one grammar to all of them (measured), and the pair is
# what would catch a block that stopped sharing it.
CROSS_BLOCK_NAMES = ("a b", "")
_OTHER_BLOCKS = [block for block in NAME_CHECKED_TOP_LEVEL_BLOCKS if block != "services"]
NAME_POSITIONS = (
    [("services", name, "both-reject") for name in REFUSED_NAMES]
    + [("services", name, "both-accept") for name in ACCEPTED_NAMES]
    + [("service-networks", name, "both-reject") for name in REFUSED_NAMES]
    + [("service-networks", name, "both-accept") for name in ACCEPTED_NAMES]
    + [(block, name, "both-reject") for block in _OTHER_BLOCKS for name in CROSS_BLOCK_NAMES]
)


def _document_named(position: str, name: str) -> dict[str, Any]:
    """Build a document whose only variable is one identifier, in the position under probe."""
    app: dict[str, Any] = {"image": "nginx:alpine"}
    compose: dict[str, Any] = {"services": {"app": app}}
    if position == "services":
        compose["services"][name] = {"image": "nginx:alpine"}
    elif position == "service-networks":
        app["networks"] = {name: {}}
        compose["networks"] = {name: {}}
    elif position in ("secrets", "configs"):
        compose[position] = {name: {"file": "./f"}}
    else:
        compose[position] = {name: {}}
    return compose


@pytest.mark.parametrize(("position", "name", "verdict"), NAME_POSITIONS, ids=lambda value: repr(value).strip("'"))
def test_identifier_grammar_matches_docker(
    position: str,
    name: str,
    verdict: str,
    assert_rule: Callable[[dict[str, Any]], str],
) -> None:
    """Both directions, per position: what Docker refuses we refuse, what it takes we take.

    The verdict is asserted rather than left to `assert_rule` alone, which tolerates an
    over-rejection -- and an over-rejection on `ACCEPTED_NAMES` is precisely the defect
    issue 143 removed, so the generic contract cannot see the thing under test.

    Generated from `NAME_CHECKED_TOP_LEVEL_BLOCKS` so a block added to that tuple is
    probed the moment it is added, the way a new `SERVICE_KEYS` entry is above.
    """
    assert assert_rule(_document_named(position, name)) == verdict
