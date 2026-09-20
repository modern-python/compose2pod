r"""The rule-two refusals, each paired with the podman invocation that would express it.

ADR-0006's second rule says compose2pod accepts whatever Docker accepts *whenever
podman can express it*, and refuses where podman cannot. Nothing ran podman to
check that, which is how issue #86's unmeasured "podman can express it" became
#104's shipped acceptance of a script that dies at `podman run`. Each row here
turns one such claim back into a measurement.

`podman_argv` is the faithful spelling of what `docker compose config` says the
document means -- a `type`/`source`/`target` triple, rendered as the `--mount`
that maps onto it one-to-one. It is deliberately not whatever `emit._volume_flags`
would produce: a short `-v` spec re-splits on colons and so can fail for its own
grammar rather than for podman's inability to mount, which would make a row pass
for the wrong reason and hide an emit bug behind a parity claim.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Refusal:
    """One documented rule-two refusal and its counterfactual podman invocation."""

    id: str
    compose: dict[str, Any]
    refusal_match: str
    podman_argv: list[str]
    control_argv: list[str]


def _one_volume(entry: str) -> dict[str, Any]:
    return {"services": {"app": {"image": "busybox:1.36", "volumes": [entry]}}}


# An anonymous volume podman *can* mount, run through the same `--mount` grammar
# every row below uses. A row going red because busybox is unpullable or because
# `type=volume` with no source is spelled wrong would take this control with it,
# so "podman refused the mount" is never confused with "nothing ran".
_ANONYMOUS_CONTROL = ["--mount", "type=volume,dst=/data"]


REFUSALS: list[Refusal] = [
    Refusal(
        id="anonymous-volume-relative-target",
        compose=_one_volume("a"),
        refusal_match="anonymous volume 'a' must be an absolute path",
        podman_argv=["--mount", "type=volume,dst=a"],
        control_argv=_ANONYMOUS_CONTROL,
    ),
    Refusal(
        id="drive-shaped-source-no-target",
        compose=_one_volume("C:\\data"),
        refusal_match="Windows drive path",
        podman_argv=["--mount", "type=volume,dst=C:\\data"],
        control_argv=_ANONYMOUS_CONTROL,
    ),
    Refusal(
        id="single-letter-source-with-path",
        compose=_one_volume("v:/data"),
        refusal_match="Windows drive path",
        podman_argv=["--mount", "type=volume,dst=v:/data"],
        control_argv=_ANONYMOUS_CONTROL,
    ),
    Refusal(
        id="single-letter-source-empty-target",
        compose=_one_volume("v:"),
        refusal_match="Windows drive path",
        podman_argv=["--mount", "type=volume,dst=v:"],
        control_argv=_ANONYMOUS_CONTROL,
    ),
]
