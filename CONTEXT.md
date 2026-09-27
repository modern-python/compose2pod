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
a convenience: see
[ADR-0005](docs/adr/0005-structural-keys-and-schema-validators-stay-in-their-owning-modules.md).

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
The target service and everything reachable from it through `depends_on` or `links`
(`graph.startup_order`, over the one graph `graph.depends_on` returns for both keys). It is
the unit of scope for almost everything: only the closure joins the pod, so only the closure
is emitted, only its hostnames are resolvable, and pod-level options (`dns`, `sysctls`,
`extra_hosts`) are unioned and conflict-checked across it and nothing else. A service outside
the closure never runs, which is why `profiles` is inert. Validation is not scoped to it,
though: the dependency graph is checked document-wide (`graph.validate_graph`), because
whether Docker rejects a document cannot depend on which service the caller happened to
target ([#87](https://github.com/modern-python/compose2pod/issues/87)).

**Rule one / rule two**:
The two directions of the Docker-rejection parity rule
([ADR-0006](docs/adr/0006-docker-rejection-parity.md)). "Rule two" is named bare
in `parsing.py` comments and in tests, with no restatement at the call site.
**Rule one**: a document `docker compose config` rejects, compose2pod
rejects too — hard, no exceptions. **Rule two**: a document Docker accepts,
compose2pod accepts whenever podman can express it. That means every *supported*
podman, from the declared floor to the newest version measured, so a mount option
only a later podman has is refused until the floor reaches it. Where podman cannot
express it, that is a *rule-two refusal* (measured, legitimate) or a *rule-two
narrowing*. A refusal citing neither podman nor a ruling of its own is a tracked
limitation, never a design position. Two rulings stand on their own: the pod's
shared namespace ([ADR-0003](docs/adr/0003-the-shared-namespace-decides-key-classification.md))
and the grammar of the `/etc/hosts` compose2pod writes
([ADR-0006](docs/adr/0006-docker-rejection-parity.md)).

**Name grammar / volume discriminator**:
Two different questions Docker answers with two different rules, once conflated
here into one pattern. The *name grammar* (`values.TOP_LEVEL_NAME`,
`[a-zA-Z0-9._-]+`) is what a top-level `services`/`volumes`/`secrets`/`configs`
key and a service's long-form `networks` key must match. The *discriminator*
(`parsing._is_named_volume_source`) asks a different thing of a short-form
volume source — named volume, or bind? — and does not use the grammar at all:
a leading `.`, `/` or `~` is a host path, anything else is a volume name. Say
which one you mean; one pattern doing both jobs is the bug
[ADR-0006](docs/adr/0006-docker-rejection-parity.md) records.

**Store**:
The umbrella noun for a Compose `secret` or `config` — the two `StoreKind`s in
`stores.py`. Use *store* for anything true of both, which is nearly everything.
*Secret* is reserved for the podman primitive both kinds render as, and is spoken
only inside `stores.py`; elsewhere it would read as the Compose `secrets:` key and
lose the distinction.
