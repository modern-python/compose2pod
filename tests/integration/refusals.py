r"""Every documented refusal, paired with the podman invocation that would express it.

ADR-0006 draws a line: compose2pod accepts whatever Docker accepts *whenever podman
can express it*, and refuses where podman cannot. Nothing ran podman to check which
side of that line a refusal sat on, which is how issue #86's unmeasured "podman can
express it" became #104's shipped acceptance of a script that dies at `podman run`.
These tables turn each claim back into a measurement. The mirror image, that a form
the gate *accepts* compiles to a flag podman runs, is `acceptances.py`.

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

Two more tables hold the refusals whose claim is about podman's *flag surface* rather
than about a mount, which `Refusal` cannot express: its argv asserts that a flag fails,
and here the claim is that no flag exists to try, or that one exists and checks nothing.

- `ABSENT_FLAGS` -- no such flag on `podman run`, checked by running it rather than by
  reading `--help`, because `--gpus` proves a flag can exist while staying out of it.
- `STUB_FLAGS` -- the flag exists and accepts deliberate nonsense, so emitting it would
  exit 0 having done nothing, which is worse than refusing.

A row measuring a refusal whose message draws a clause from `compose2pod/podman.py` names
the `site` that makes the claim and the `claim` it makes, which is what
`tests/test_podman_claim_coverage.py` gates on. Every `REFUSALS` row names one, because the
table's own premise is that podman will not make the mount: a row that named none would mark
a message stating a rule while keeping podman's reason to itself, which is what #121 found
in four of them. Only a `LIMITATIONS` row leaves both empty, and only where the limit really
is the short form's rather than podman's.

Four claims, four experiments. The keys refused under ADR-0003 have no rows, because
their reason is the pod model rather than rule two: podman honours `network_mode` for a
container that joined a pod (#115), and `external_links` names a container the generated
script never creates, which is a fact about the script. `links` has none either, for a
third reason -- it is a form compose2pod does not read yet (#120). The gate that every
rule-two site has a row is issue #109 phase 3, and those are the exemptions it has to
know about.

A `subpath` row measures the floor, not podman as such: podman gained the option above
the supported minimum (ADR-0006), so the row goes red on a runner newer than the floor,
which is the signal that the floor can be raised and the refusal dropped.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Refusal:
    """A refusal podman agrees with: it will not make the mount the document asks for.

    `{host}` in either argv is substituted with the test's `tmp_path`, for a row whose
    counterfactual turns on a host path rather than on the spec's grammar.
    """

    id: str
    compose: dict[str, Any]
    refusal_match: str
    podman_argv: list[str]
    control_argv: list[str]
    site: str = ""
    claim: str = ""


@dataclass(frozen=True)
class Limitation:
    """A refusal podman does not agree with: it makes the mount, and compose2pod cannot spell it.

    `host_dir` is created under the test's `tmp_path` first, because a bind whose
    source does not exist fails for that reason instead of the one being measured.
    `{host}` in `podman_argv` is substituted with its absolute path. A row whose
    counterfactual needs no host path leaves it empty.
    """

    id: str
    compose: dict[str, Any]
    refusal_match: str
    podman_argv: list[str]
    host_dir: str = ""
    site: str = ""
    claim: str = ""


@dataclass(frozen=True)
class AbsentFlag:
    """A refusal whose claim is that podman has no flag to emit for the key.

    Each `unknown_argv` must be rejected by `podman run`. The row goes red the day
    podman grows one, which is the only thing keeping "no equivalent" from going stale.
    """

    id: str
    compose: dict[str, Any]
    refusal_match: str
    unknown_argv: list[list[str]]
    site: str = ""
    claim: str = ""


@dataclass(frozen=True)
class StubFlag:
    """A refusal whose claim is that podman's flag exists and validates nothing.

    `nonsense_argv` is deliberate rubbish podman accepts anyway; the row goes red when
    a podman starts rejecting it, which is when the refusal deserves re-examining.
    """

    id: str
    compose: dict[str, Any]
    refusal_match: str
    nonsense_argv: list[str]
    site: str = ""
    claim: str = ""


def _one_volume(entry: "str | dict[str, Any]") -> dict[str, Any]:
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
        site="parsing._validate_service_volumes",
        claim="REFUSES_RELATIVE_CONTAINER_PATH",
        compose=_one_volume("a"),
        refusal_match="anonymous volume 'a' must be an absolute path",
        podman_argv=["--mount", "type=volume,dst=a"],
        control_argv=_ANONYMOUS_CONTROL,
    ),
    Refusal(
        id="drive-shaped-source-no-target",
        site="parsing._reject_drive_shaped_volume",
        claim="REFUSES_RELATIVE_CONTAINER_PATH",
        compose=_one_volume("C:\\data"),
        refusal_match=_NOT_ABSOLUTE,
        podman_argv=["--mount", "type=volume,dst=C:\\data"],
        control_argv=_ANONYMOUS_CONTROL,
    ),
    Refusal(
        id="single-letter-source-with-path",
        site="parsing._reject_drive_shaped_volume",
        claim="REFUSES_RELATIVE_CONTAINER_PATH",
        compose=_one_volume("v:/data"),
        refusal_match=_NOT_ABSOLUTE,
        podman_argv=["--mount", "type=volume,dst=v:/data"],
        control_argv=_ANONYMOUS_CONTROL,
    ),
    Refusal(
        id="single-letter-source-empty-target",
        site="parsing._reject_drive_shaped_volume",
        claim="REFUSES_RELATIVE_CONTAINER_PATH",
        compose=_one_volume("v:"),
        refusal_match=_NOT_ABSOLUTE,
        podman_argv=["--mount", "type=volume,dst=v:"],
        control_argv=_ANONYMOUS_CONTROL,
    ),
    Refusal(
        id="long-form-relative-target",
        site="parsing._validate_volume_long_form",
        claim="REFUSES_RELATIVE_CONTAINER_PATH",
        compose=_one_volume({"type": "volume", "target": "rel"}),
        refusal_match="volume 'target' must be an absolute path",
        podman_argv=["--mount", "type=volume,dst=rel"],
        control_argv=_ANONYMOUS_CONTROL,
    ),
    Refusal(
        id="long-form-type-cluster",
        site="parsing._validate_volume_long_form",
        claim="CANNOT_EXPRESS",
        compose=_one_volume({"type": "cluster", "target": "/data"}),
        refusal_match="volume 'type: cluster' is not supported",
        podman_argv=["--mount", "type=cluster,dst=/data"],
        control_argv=_ANONYMOUS_CONTROL,
    ),
    Refusal(
        id="long-form-type-npipe",
        site="parsing._validate_volume_long_form",
        claim="CANNOT_EXPRESS",
        compose=_one_volume({"type": "npipe", "target": "/data"}),
        refusal_match="volume 'type: npipe' is not supported",
        podman_argv=["--mount", "type=npipe,dst=/data"],
        control_argv=_ANONYMOUS_CONTROL,
    ),
    Refusal(
        # Measured: podman creates a missing bind source in no spelling, `-v` or `--mount`.
        id="bind-create-host-path",
        site="parsing._validate_bind_options",
        claim="CANNOT_EXPRESS",
        compose=_one_volume({"type": "bind", "source": "./src", "target": "/var", "bind": {"create_host_path": True}}),
        refusal_match="bind 'create_host_path' is not supported",
        podman_argv=["--mount", "type=bind,src={host}/absent,dst=/var"],
        control_argv=["--mount", "type=bind,src={host},dst=/var"],
    ),
    Refusal(
        # `source=`/`target=` rather than the `src=`/`dst=` above: the spelling #114 measured.
        id="image-subpath",
        site="parsing._reject_subpath",
        claim="adds_the_mount_option_in",
        compose=_one_volume(
            {"type": "image", "source": "busybox:1.36", "target": "/mnt", "image": {"subpath": "/bin"}}
        ),
        refusal_match="image 'subpath' is not supported",
        podman_argv=["--mount", "type=image,source=busybox:1.36,target=/mnt,subpath=/bin"],
        control_argv=["--mount", "type=image,source=busybox:1.36,target=/mnt"],
    ),
    Refusal(
        id="volume-subpath",
        site="parsing._reject_subpath",
        claim="adds_the_mount_option_in",
        compose=_one_volume({"type": "volume", "target": "/mnt", "volume": {"subpath": "/sub"}}),
        refusal_match="volume 'subpath' is not supported",
        podman_argv=["--mount", "type=volume,target=/mnt,subpath=/sub"],
        control_argv=_ANONYMOUS_CONTROL,
    ),
]


def _reservation(field: str, value: object) -> dict[str, Any]:
    return {"services": {"app": {"image": "busybox:1.36", "deploy": {"resources": {"reservations": {field: value}}}}}}


LIMITATIONS: list[Limitation] = [
    Limitation(
        # Measured: `-v vol:/etc:nocopy` leaves only podman's own hosts/hostname/resolv.conf
        # in the volume, so the image copy-up really is suppressed.
        id="volume-nocopy",
        site="parsing._validate_volume_type_options",
        claim="NOCOPY_NEEDS_THE_SHORT_FORM",
        compose={
            "services": {
                "app": {
                    "image": "busybox:1.36",
                    "volumes": [{"type": "volume", "source": "ncvol", "target": "/data", "volume": {"nocopy": True}}],
                }
            },
            "volumes": {"ncvol": {}},
        },
        refusal_match="use the short syntax, which emits -v",
        podman_argv=["-v", "c2p-nocopy-probe:/data:nocopy"],
    ),
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


ABSENT_FLAGS: list[AbsentFlag] = [
    AbsentFlag(
        id="reservations-cpus",
        site="resources._RESERVATION_REFUSALS",
        claim="NO_RESERVATION_FLAG",
        compose=_reservation("cpus", "0.5"),
        refusal_match="podman run has no reservation flag for it",
        unknown_argv=[["--cpu-reservation", "1"], ["--cpus-reservation", "1"]],
    ),
]


STUB_FLAGS: list[StubFlag] = [
    StubFlag(
        id="reservations-devices",
        site="resources._RESERVATION_REFUSALS",
        claim="GPUS_RESERVES_NOTHING",
        compose=_reservation("devices", [{"capabilities": ["gpu"]}]),
        refusal_match="--gpus accepts any value and reserves nothing",
        nonsense_argv=["--gpus", "nonsense"],
    ),
]
