# compose2pod

A stdlib-only Python package and CLI that converts a Docker Compose document into a
POSIX `sh` script running one service and its dependencies as a single Podman pod.
Built for CI and test environments where neither `docker compose` nor
`podman kube play` can run: no bridge networking, no systemd, no heavy runtime.

## Language

A term is listed only when there is a synonym to reject, or a meaning subtle enough
that code, tests and docs must agree on it. General programming vocabulary does not
belong here, however heavily this package uses it — nor does anything a module
docstring already states in its first lines.

**Service-key registry**:
`SERVICE_KEYS` in `keys.py`: the table mapping each *declarative* Compose service key
to its `KeySpec` — the `(validate, emit, merge)` triple saying how that key is
checked, how it renders to `podman run` flags, and (for list/map-shaped keys) how it
merges across `extends`. It is the single source both the gate (`validate`) and the
emitter (`run_flags`) derive from, so the two cannot drift apart; that single-sourcing
is the whole point of the table, not an incidental property of it.

**Structural key**:
A supported service key handled *outside* the service-key registry, listed in
`STRUCTURAL_KEYS`, because the `emit(value)` interface cannot express it — it needs
`project_dir` (`env_file`, `volumes`), spans keys, or occupies the image/command slot
(`entrypoint`). Structural keys keep their own validate/emit machinery, in the module
that owns the concern. Which side of this line a key falls on is a design ruling, not
a convenience: see [ADR-0008](docs/adr/0008-reject-structural-key-registry.md) and
[ADR-0013](docs/adr/0013-volumes-stays-hand-rolled.md).

**Token**:
The result of rendering one Compose value into a `podman run`/`pod create` argument:
`Token = str | Expand | GuardedEnvFile` in `keys.py`. A `str` is already shell-safe
and final; the other two members are deferred — they carry something the *generated
script*, not compose2pod, resolves. Anything walking a flag list has to handle all
three.

**Expand**:
A token whose Compose `${VAR}` references expand when the generated script runs, not
when compose2pod generates it. This deferral is the project's central semantic: a
variable's value is a fact about the machine the script runs on, which is not the
machine that read the compose file.

**Closure**:
The target service and everything reachable from it through `depends_on`
(`graph.startup_order`). It is the unit of scope for almost everything: only the
closure joins the pod, so only the closure is emitted, only its hostnames are
resolvable, and pod-level options (`dns`, `sysctls`, `extra_hosts`) are unioned and
conflict-checked across it and nothing else. A service outside the closure never runs,
which is why `profiles` is inert and why validation is closure-scoped rather than
document-wide.

**Rule one / rule two**:
The two directions of the Docker-rejection parity rule
([ADR-0009](docs/adr/0009-docker-rejection-parity.md)). "Rule two" is named bare
in `parsing.py` comments and in tests, with no restatement at the call site.
**Rule one**: a document `docker compose config` rejects, compose2pod
rejects too — hard, no exceptions. **Rule two**: a document Docker accepts,
compose2pod accepts whenever podman can express it; where it cannot, that is a
*rule-two refusal* (measured, legitimate) or a *rule-two narrowing*. A refusal that
cannot cite podman is a tracked limitation, never a design position.

**Store**:
The umbrella noun for a Compose `secret` or `config` — the two `StoreKind`s in
`stores.py`. Use *store* for anything true of both, which is nearly everything.
*Secret* is reserved for the podman primitive both kinds render as, and is spoken
only inside `stores.py`; elsewhere it would read as the Compose `secrets:` key and
lose the distinction.
