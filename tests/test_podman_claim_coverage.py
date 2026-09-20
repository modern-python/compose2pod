import ast
import pathlib
import typing

from compose2pod import podman
from tests.integration.refusals import ABSENT_FLAGS, LIMITATIONS, REFUSALS, STUB_FLAGS


_PACKAGE: typing.Final = pathlib.Path(__file__).resolve().parent.parent / "compose2pod"
_CLAIMS_MODULE: typing.Final = "podman"


def _site_of(statement: ast.stmt) -> str:
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return statement.name
    targets = getattr(statement, "targets", [])
    return next((target.id for target in targets if isinstance(target, ast.Name)), "")


def _claims_in(statement: ast.stmt) -> set[str]:
    return {
        node.attr
        for node in ast.walk(statement)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == _CLAIMS_MODULE
    }


def claim_sites(package: pathlib.Path) -> set[tuple[str, str]]:
    """Every (site, claim) pair in a package: where it draws a clause from the claims module.

    A site is the top-level function, class or assignment that holds the reference, named
    `<module>.<site>` -- the granularity a measurement is written at. The scan reads
    attribute access rather than message text because a message is assembled from an
    f-string, a shared preamble or a lookup table depending on the site, and prose is not
    a registry.
    """
    found: set[tuple[str, str]] = set()
    for file in sorted(package.glob("*.py")):
        if file.stem == _CLAIMS_MODULE:
            continue
        for statement in ast.parse(file.read_text(encoding="utf-8")).body:
            site = _site_of(statement)
            found |= {(f"{file.stem}.{site}", claim) for claim in _claims_in(statement) if site}
    return found


def _measured_pairs() -> set[tuple[str, str]]:
    return {
        (row.site, row.claim)
        for table in (REFUSALS, LIMITATIONS, ABSENT_FLAGS, STUB_FLAGS)
        for row in table
        if row.site
    }


def test_every_claim_compose2pod_makes_about_podman_is_measured_by_a_row() -> None:
    """INVARIANT: a refusal whose reason is podman's behaviour is measured against real podman.

    Broken by adding a refusal that draws a clause from `compose2pod/podman.py` without a row
    in `tests/integration/refusals.py`. That is issue #86's mistake mechanised: an asserted
    "podman cannot express it" became #104's shipped acceptance of a script that dies at
    `podman run`, and #114 repeated it from the other side.

    Out of scope by construction: a refusal that makes no claim here. `network_mode` is
    refused under docs/adr/0003-the-shared-namespace-decides-key-classification.md and podman
    honours it, so it has no claim and needs no row. A refusal whose reason *is* podman's but
    whose message keeps that to itself would be invisible to this gate, which is what the
    invariant below covers from the other side.
    """
    unmeasured = sorted(claim_sites(_PACKAGE) - _measured_pairs())

    assert unmeasured == [], "\n".join(
        f"{site} claims podman.{claim} and no row measures it" for site, claim in unmeasured
    )


def test_every_refusal_podman_agrees_with_tells_the_user_podmans_reason() -> None:
    """INVARIANT: a rule-two refusal states the reason that makes it legitimate, not just the rule.

    A `REFUSALS` row exists because podman will not make the mount, so the refusal's reason is
    podman's. A row with no claim therefore marks a message that states a rule while keeping
    its reason in the source, which is what issue #121 found in four of them, and which the
    gate above cannot see because there is no claim for it to scan.

    `LIMITATIONS` is where a refusal with no podman reason belongs: the drive-qualified bind
    is the short form's own limit, and its message says so.
    """
    silent = sorted(row.id for row in REFUSALS if not row.claim)

    assert silent == [], "\n".join(f"{row} refuses for podman's reason without telling the user" for row in silent)


def test_every_row_naming_a_claim_names_one_the_source_still_makes() -> None:
    """INVARIANT: a row measures a claim that exists, at the site that makes it.

    Broken by renaming a claim, moving a refusal to another function, or dropping the refusal
    and leaving the row behind. A row that measures nothing passes forever.
    """
    stale = sorted(_measured_pairs() - claim_sites(_PACKAGE))

    assert stale == [], "\n".join(f"row claims {site} uses podman.{claim}; it does not" for site, claim in stale)


def test_every_claim_a_row_names_is_an_attribute_of_the_claims_module() -> None:
    """A typo in a row's `claim` would otherwise pair with a typo in the scan and cancel out."""
    missing = sorted({claim for _, claim in _measured_pairs() if not hasattr(podman, claim)})

    assert missing == []


def test_a_claim_made_with_no_row_is_reported_with_its_site(tmp_path: pathlib.Path) -> None:
    """The scanner is exercised against a known result, so an empty scan cannot pass as a green one."""
    (tmp_path / "parsing.py").write_text(
        "from compose2pod import podman\n\n\ndef _refuse():\n    raise ValueError(podman.CANNOT_EXPRESS)\n",
        encoding="utf-8",
    )

    assert claim_sites(tmp_path) == {("parsing._refuse", "CANNOT_EXPRESS")}


def test_a_claim_held_in_a_module_level_table_is_found(tmp_path: pathlib.Path) -> None:
    """`resources.py` keeps its two reservation clauses in a dict, outside any function."""
    (tmp_path / "resources.py").write_text(
        "from compose2pod import podman\n\n_REFUSALS = {'cpus': podman.NO_RESERVATION_FLAG}\n",
        encoding="utf-8",
    )

    assert claim_sites(tmp_path) == {("resources._REFUSALS", "NO_RESERVATION_FLAG")}


def test_the_claims_module_is_not_scanned_against_itself(tmp_path: pathlib.Path) -> None:
    """Its own helper spells a clause from `FLOOR`, which is a definition, not a claim made."""
    (tmp_path / "podman.py").write_text("FLOOR = '4.9'\n\n\ndef clause():\n    return podman.FLOOR\n", encoding="utf-8")

    assert claim_sites(tmp_path) == set()


def test_a_module_naming_nothing_from_the_claims_module_contributes_no_pairs(tmp_path: pathlib.Path) -> None:
    (tmp_path / "graph.py").write_text("def walk():\n    return 1\n", encoding="utf-8")

    assert claim_sites(tmp_path) == set()
