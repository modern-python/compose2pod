# The shared namespace decides which keys are refused, inert, or pod-level

Every service runs in one pod sharing `net`, `uts`, `ipc` and `cgroup`, and the shared network
namespace, with localhost discovery through a bind-mounted hosts file, is the reason the tool
exists. Keys that pull a container out of it (`network_mode`, `external_links`) are refused
permanently: `network_mode` moves the container to another namespace, and `external_links` names
a container the generated script never creates, which the pod's hosts file -- where every name
compose2pod writes resolves to `127.0.0.1` -- has no address for, `extra_hosts` being the
supported way to name one. This clause once also swept up `links` and `expose`, which arrived in
the same sentence and belong in neither category
([#120](https://github.com/modern-python/compose2pod/issues/120), measured against
`docker compose config` v5.1.2). `links` normalises to a `depends_on` edge plus a hostname alias
-- docker refuses `links: [ghost]` exactly as it refuses a ghost `depends_on` -- so it neither
escapes the namespace nor is satisfied by it, and ignoring it would drop a dependency the
`--target` closure is built from. It is refused as a tracked limitation under
[ADR-0006](0006-docker-rejection-parity.md), and both halves are mechanisms compose2pod already
has, so this one is expected to shrink. `expose` carries no edge, is never published, and is
validated by docker no further than its list shape (it keeps `expose: [banana]`), which makes it
inert exactly as `ports` is: it sits in `IGNORED_SERVICE_KEYS` with a warning, not refused.
Per-container namespace overrides (`ipc`, `uts`, `domainname`, `cgroup`, `userns_mode`) are
refused while the pod keeps its default `--share`. `dns*` and `sysctls` are pod-level, unioned
and conflict-checked across the closure onto `podman pod create`, because a container that joined
the pod owns neither namespace and podman rejects the per-container flag. `stop_signal` and
`stop_grace_period` are accepted but inert for a different reason, in that same table with a
warning: the script tears down with `pod rm -f` and never runs `podman stop`, so the flags would
set metadata nothing consults. Inert is not unchecked -- every key in the table keeps docker's own
shape rule at the gate, since a document carrying a malformed one is a document docker will not
run.
