"""Every flag compose2pod emits for a long-form mount, run once against the floor podman.

`refusals.py` measures one direction: that podman will not make a mount we refuse.
Nothing measured the other, and that is the direction #104 and #114 both failed in --
a document passes the gate, emits a flag, and the script dies at `podman run`. Unit
coverage cannot catch it, because `test_emit.py` asserts the string compose2pod
produces, which is a fact about compose2pod, not about podman.

A row is the inverse of a `Refusal`, and its `expected_argv` is checked against what
`emit` really produces before it is run. That check is deliberately the opposite of
`refusals.py`'s rule, where the argv is hand-written so an emit bug cannot hide behind
a parity claim: here the emitted flag is the thing under test, so the row has to be
pinned to it or it measures a flag nobody ships.

A row goes red when a podman in the supported range drops or renames an option --
the same maintenance trigger the `subpath` refusals carry, pointing the other way.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Acceptance:
    """A form the gate accepts, the flag it compiles to, and podman's verdict on that flag.

    `{host}` in `expected_argv` is substituted with the test's project directory, which is
    also what `emit` resolves a relative bind source against. `host_dir` is created under it
    first, because a bind whose source does not exist fails for that reason instead of
    proving anything about the option being measured.
    """

    id: str
    service: dict[str, Any]
    expected_argv: list[str]
    host_dir: str = ""


def _bind(nested: dict[str, Any]) -> dict[str, Any]:
    return {
        "image": "busybox:1.36",
        "volumes": [{"type": "bind", "source": "./d", "target": "/data", "bind": nested}],
    }


def _tmpfs(nested: dict[str, Any]) -> dict[str, Any]:
    return {"image": "busybox:1.36", "volumes": [{"type": "tmpfs", "target": "/data", "tmpfs": nested}]}


def _mount(value: str) -> list[str]:
    return ["--mount", value]


ACCEPTANCES: list[Acceptance] = [
    Acceptance(
        id=f"bind-propagation-{propagation}",
        service=_bind({"propagation": propagation}),
        expected_argv=_mount(f"type=bind,source={{host}}/d,target=/data,bind-propagation={propagation}"),
        host_dir="d",
    )
    # Every value `_PROPAGATION_VALUES` admits. Only `rprivate` had ever run.
    for propagation in ("private", "rprivate", "shared", "rshared", "slave", "rslave")
] + [
    Acceptance(
        id="bind-relabel-shared",
        service=_bind({"selinux": "z"}),
        expected_argv=_mount("type=bind,source={host}/d,target=/data,relabel=shared"),
        host_dir="d",
    ),
    Acceptance(
        id="bind-relabel-private",
        service=_bind({"selinux": "Z"}),
        expected_argv=_mount("type=bind,source={host}/d,target=/data,relabel=private"),
        host_dir="d",
    ),
    Acceptance(
        id="tmpfs-size",
        service=_tmpfs({"size": "1m"}),
        expected_argv=_mount("type=tmpfs,target=/data,tmpfs-size=1m"),
    ),
    Acceptance(
        id="tmpfs-mode",
        service=_tmpfs({"mode": 1777}),
        expected_argv=_mount("type=tmpfs,target=/data,tmpfs-mode=1777"),
    ),
    Acceptance(
        id="tmpfs-size-and-mode",
        service=_tmpfs({"size": "1m", "mode": 1777}),
        expected_argv=_mount("type=tmpfs,target=/data,tmpfs-size=1m,tmpfs-mode=1777"),
    ),
    Acceptance(
        id="bind-read-only",
        service={
            "image": "busybox:1.36",
            "volumes": [{"type": "bind", "source": "./d", "target": "/data", "read_only": True}],
        },
        expected_argv=_mount("type=bind,source={host}/d,target=/data,ro"),
        host_dir="d",
    ),
    Acceptance(
        id="volume-read-only",
        service={"image": "busybox:1.36", "volumes": [{"type": "volume", "target": "/data", "read_only": True}]},
        expected_argv=_mount("type=volume,target=/data,ro"),
    ),
    Acceptance(
        id="service-tmpfs",
        service={"image": "busybox:1.36", "tmpfs": "/data"},
        expected_argv=["--tmpfs", "/data"],
    ),
]
