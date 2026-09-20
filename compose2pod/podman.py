"""Every claim compose2pod makes about podman, in one place.

A refusal whose reason is podman's behaviour draws its clause from here instead of
spelling one inline. The point is not the wording, which changes freely, but the name:
`tests/test_podman_claim_coverage.py` reads this module's attributes to find the sites
that make such a claim, and requires each one to be measured by a row in
`tests/integration/refusals.py`. Grepping for prose cannot do that -- the message is
assembled from an f-string, a shared preamble or a lookup table, depending on the site.

That gate is what issue #86 was missing: an unmeasured "podman can express it" became
#104's shipped acceptance of a script that dies at `podman run`.
"""

# compose2pod supports podman FLOOR and up (README, docs/adr/0006-docker-rejection-parity.md).
# A form is accepted only where podman expresses it across that whole range.
FLOOR = "4.9"

CANNOT_EXPRESS = "podman cannot express it"
REFUSES_RELATIVE_CONTAINER_PATH = "podman refuses a container path that is not absolute"
NOCOPY_NEEDS_THE_SHORT_FORM = "use the short syntax, which emits -v and podman honours"
NO_RESERVATION_FLAG = "podman run has no reservation flag for it"
GPUS_RESERVES_NOTHING = "podman run's --gpus accepts any value and reserves nothing"


def adds_the_mount_option_in(version: str) -> str:
    """Spell the clause for a mount option podman gained above the floor."""
    return f"podman {version} adds the mount option, and compose2pod supports podman {FLOOR} and up"
