r"""Every documented refusal, paired with the podman invocation that would express it.

ADR-0006 draws a line: compose2pod accepts whatever Docker accepts *whenever podman
can express it*, and refuses where podman cannot. Nothing ran podman to check which
side of that line a refusal sat on, which is how issue #86's unmeasured "podman can
express it" became #104's shipped acceptance of a script that dies at `podman run`.
These tables turn each claim back into a measurement, in both directions:

- `REFUSALS` -- podman will not make this mount, so refusing is rule two.
- `LIMITATIONS` -- podman *will* make it and we refuse anyway, so the refusal is the
  short form's own limit. ADR-0006 calls that "a tracked limitation, never a design
  position", and a row here is what keeps it tracked.

`podman_argv` is the faithful spelling of what `docker compose config` says the
document means -- a `type`/`source`/`target` triple, rendered as the `--mount` that
maps onto it one-to-one. It is deliberately not whatever `emit._volume_flags` would
produce: a short `-v` spec re-splits on colons and so can fail for its own grammar
rather than for podman's inability to mount, which would make a row pass for the
wrong reason and hide an emit bug behind a parity claim.

Only the volume family is covered so far; the rest of the refusals ADR-0006 names
are issue #109 phase 2, and the gate that every refusal site has a row is phase 3.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Refusal:
    """A refusal podman agrees with: it will not make the mount the document asks for."""

    id: str
    compose: dict[str, Any]
    refusal_match: str
    podman_argv: list[str]
    control_argv: list[str]


@dataclass(frozen=True)
class Limitation:
    """A refusal podman does not agree with: it makes the mount, and compose2pod cannot spell it.

    `host_dir` is created under the test's `tmp_path` first, because a bind whose
    source does not exist fails for that reason instead of the one being measured.
    `{host}` in `podman_argv` is substituted with its absolute path.
    """

    id: str
    compose: dict[str, Any]
    refusal_match: str
    host_dir: str
    podman_argv: list[str]


def _one_volume(entry: str) -> dict[str, Any]:
    return {"services": {"app": {"image": "busybox:1.36", "volumes": [entry]}}}


# An anonymous volume podman *can* mount, run through the same `--mount` grammar
# every row below uses. A row going red because busybox is unpullable or because
# `type=volume` with no source is spelled wrong would take this control with it,
# so "podman refused the mount" is never confused with "nothing ran".
_ANONYMOUS_CONTROL = ["--mount", "type=volume,dst=/data"]

_NOT_ABSOLUTE = "podman refuses a container path that is not absolute"
_SHORT_FORM_CANNOT_EMIT = "which the short form cannot emit"


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
        refusal_match=_NOT_ABSOLUTE,
        podman_argv=["--mount", "type=volume,dst=C:\\data"],
        control_argv=_ANONYMOUS_CONTROL,
    ),
    Refusal(
        id="single-letter-source-with-path",
        compose=_one_volume("v:/data"),
        refusal_match=_NOT_ABSOLUTE,
        podman_argv=["--mount", "type=volume,dst=v:/data"],
        control_argv=_ANONYMOUS_CONTROL,
    ),
    Refusal(
        id="single-letter-source-empty-target",
        compose=_one_volume("v:"),
        refusal_match=_NOT_ABSOLUTE,
        podman_argv=["--mount", "type=volume,dst=v:"],
        control_argv=_ANONYMOUS_CONTROL,
    ),
]


LIMITATIONS: list[Limitation] = [
    Limitation(
        id="drive-qualified-bind",
        compose=_one_volume("C:\\data:/var"),
        refusal_match=_SHORT_FORM_CANNOT_EMIT,
        host_dir="C:\\data",
        podman_argv=["--mount", "type=bind,src={host},dst=/var"],
    ),
    Limitation(
        id="drive-relative-bind",
        compose=_one_volume("C:data:/var"),
        refusal_match=_SHORT_FORM_CANNOT_EMIT,
        host_dir="C:data",
        podman_argv=["--mount", "type=bind,src={host},dst=/var"],
    ),
]
