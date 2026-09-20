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


def test_healthcheck_hour_duration_is_no_longer_an_over_rejection(
    assert_rule: Callable[[dict[str, Any]], str],
) -> None:
    """`interval_seconds` now parses the full compose-go duration grammar, including `h`.

    Same reasoning as the over-rejection tests above: the generic corpus run alone
    would stay green even pre-fix, filing `healthcheck_hour_duration` under the
    allowed 'over-reject' verdict instead of catching a regression. The stronger
    claim -- both oracles ACCEPT `interval: 1h` -- needs this dedicated assertion
    on the verdict itself.
    """
    path = Path(__file__).parent / "corpus" / "healthcheck_hour_duration.yaml"
    assert assert_rule(yaml.safe_load(path.read_text())) == "both-accept"


def test_healthcheck_compound_duration_is_no_longer_an_over_rejection(
    assert_rule: Callable[[dict[str, Any]], str],
) -> None:
    """`interval_seconds` now parses compound compose-go durations such as `1h30m`.

    Same reasoning as the over-rejection tests above: the generic corpus run alone
    would stay green even pre-fix, filing `healthcheck_compound_duration` under the
    allowed 'over-reject' verdict instead of catching a regression. The stronger
    claim -- both oracles ACCEPT `interval: 1h30m` -- needs this dedicated assertion
    on the verdict itself.
    """
    path = Path(__file__).parent / "corpus" / "healthcheck_compound_duration.yaml"
    assert assert_rule(yaml.safe_load(path.read_text())) == "both-accept"


def test_volumes_long_form_is_no_longer_an_over_rejection(
    assert_rule: Callable[[dict[str, Any]], str],
) -> None:
    """The long-syntax (mapping) volume entry now parses instead of raising.

    Same reasoning as the over-rejection tests above: the generic corpus run alone
    would stay green even pre-fix, filing `volumes_long_form` under the allowed
    'over-reject' verdict instead of catching a regression. The stronger claim --
    both oracles ACCEPT `{type: bind, source: ./data, target: /data}` -- needs
    this dedicated assertion on the verdict itself.
    """
    path = Path(__file__).parent / "corpus" / "volumes_long_form.yaml"
    assert assert_rule(yaml.safe_load(path.read_text())) == "both-accept"


def test_volumes_long_form_bind_options_is_no_longer_an_over_rejection(
    assert_rule: Callable[[dict[str, Any]], str],
) -> None:
    """The long-form entry's nested `bind:` option map now parses instead of raising.

    Same reasoning as the over-rejection tests above: the generic corpus run alone
    would stay green even pre-fix, filing `volumes_long_form_bind_options` under
    the allowed 'over-reject' verdict instead of catching a regression. The
    stronger claim -- both oracles ACCEPT a `bind: {propagation: rshared}` sub-map
    on a `type: bind` entry -- needs this dedicated assertion on the verdict
    itself.
    """
    path = Path(__file__).parent / "corpus" / "volumes_long_form_bind_options.yaml"
    assert assert_rule(yaml.safe_load(path.read_text())) == "both-accept"


def test_volumes_long_form_image_type_is_no_longer_an_over_rejection(
    assert_rule: Callable[[dict[str, Any]], str],
) -> None:
    """The long-form entry's `type: image` now parses instead of raising.

    Same reasoning as the over-rejection tests above: the generic corpus run alone
    would stay green even pre-fix, filing `volumes_long_form_image_type` under
    the allowed 'over-reject' verdict instead of catching a regression. The
    stronger claim -- both oracles ACCEPT `{type: image, source: nginx, target:
    /img}` -- needs this dedicated assertion on the verdict itself.
    """
    path = Path(__file__).parent / "corpus" / "volumes_long_form_image_type.yaml"
    assert assert_rule(yaml.safe_load(path.read_text())) == "both-accept"


def test_service_links_is_no_longer_an_over_rejection(
    assert_rule: Callable[[dict[str, Any]], str],
) -> None:
    """Docker accepts `links: [db:database]`, and since issue 132 so does compose2pod.

    This assertion is the one the issue said would report when the limitation closed: it
    asserted `over-reject` while the key was refused, and the verdict it asserts now is the
    measurement that replaced it. Left to the generic corpus run it would stay green either
    way, `over-reject` being an allowed verdict -- which is exactly why the flip needs saying.
    """
    path = Path(__file__).parent / "corpus" / "service_links_alias.yaml"
    assert assert_rule(yaml.safe_load(path.read_text())) == "both-accept"


def test_service_links_empty_alias_is_accepted_by_both(
    assert_rule: Callable[[dict[str, Any]], str],
) -> None:
    """`links: ['db:']` is accepted by docker with a blank alias, so it cannot be refused here.

    Asserted on the verdict for the same reason as the row above: an over-rejection of this
    shape would pass the generic run silently, and refusing a document docker runs over an
    alias that names nothing would be a limitation invented rather than measured.
    """
    path = Path(__file__).parent / "corpus" / "service_links_empty_alias.yaml"
    assert assert_rule(yaml.safe_load(path.read_text())) == "both-accept"


def test_service_external_links_is_a_catalogued_over_rejection(
    assert_rule: Callable[[dict[str, Any]], str],
) -> None:
    """Docker accepts `external_links`; the pod's hosts file has no address to give it.

    Same reason for asserting it as the row above. Unlike `links` this one is not expected
    to flip: a container the script never creates is outside the pod model, and `extra_hosts`
    is the supported way to name one.
    """
    path = Path(__file__).parent / "corpus" / "service_external_links.yaml"
    assert assert_rule(yaml.safe_load(path.read_text())) == "over-reject"


def test_volume_windows_drive_letter_bind_is_a_catalogued_over_rejection(
    assert_rule: Callable[[dict[str, Any]], str],
) -> None:
    r"""Docker accepts `volumes: ['C:\data:/var']`; podman cannot express it, so we refuse it.

    The inverse of the tests above, and the reason it is asserted rather than
    left to the generic corpus run: `over-reject` is an allowed verdict, so the
    run stays green whichever way this file falls. Pinning it here makes the
    catalogued limitation (issue 105, measured against podman 4.9.3) fail loudly
    if it ever turns into an accept without the ruling being revisited.
    """
    path = Path(__file__).parent / "corpus" / "volume_windows_drive_letter_bind.yaml"
    assert assert_rule(yaml.safe_load(path.read_text())) == "over-reject"


def test_volume_long_form_cluster_type_is_a_catalogued_over_rejection(
    assert_rule: Callable[[dict[str, Any]], str],
) -> None:
    """Docker accepts `type: cluster`; podman has no such mount, so we refuse it -- rule two.

    Asserted rather than left to the generic corpus run for the usual reason: `over-reject`
    is an allowed verdict, so the run stays green whichever way this file falls. What it
    pins is the half of issue #121's split that only docker can answer -- the refusal cites
    podman, and citing podman is only legitimate while docker itself takes the document.
    """
    path = Path(__file__).parent / "corpus" / "volume_long_form_cluster_type.yaml"
    assert assert_rule(yaml.safe_load(path.read_text())) == "over-reject"


def test_volume_long_form_misspelled_type_is_rejected_by_both(
    assert_rule: Callable[[dict[str, Any]], str],
) -> None:
    """The other half of the split: docker rejects a typo too, so podman is not the reason.

    `volume 'type' must be one of [...]` fires for both this and `cluster`, and the two are
    refused for opposite reasons. Pinning the verdict keeps the message that claims nothing
    about podman attached to the case where podman has nothing to do with it.
    """
    path = Path(__file__).parent / "corpus" / "volume_long_form_misspelled_type.yaml"
    assert assert_rule(yaml.safe_load(path.read_text())) == "both-reject"


def test_volume_single_letter_source_is_a_catalogued_over_rejection(
    assert_rule: Callable[[dict[str, Any]], str],
) -> None:
    """Docker accepts `volumes: ['v:/data']` -- as an anonymous volume, not as the declared `v`.

    The declaration in the file is deliberate: Docker ignores it, because a
    leading single letter is a drive marker and never a volume name. Both
    oracles once accepted this document while meaning different mounts, which
    is a divergence the harness cannot see -- it compares verdicts, not
    meanings. Refusing it makes the disagreement visible as an over-rejection,
    and asserting the verdict here keeps it that way.
    """
    path = Path(__file__).parent / "corpus" / "volume_single_letter_source.yaml"
    assert assert_rule(yaml.safe_load(path.read_text())) == "over-reject"
