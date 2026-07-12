---
status: accepted
summary: Keep the SERVICE_KEYS registry + STRUCTURAL_KEYS name-set split; reject a uniform structural-key registry as a false seam, and keep each structural key's behavior in the module that owns its concern.
supersedes: null
superseded_by: null
---

# Reject a structural-key registry; keep behavior in the owning modules

**Decision:** Do not introduce a uniform structural-key registry (a table of
`emit(value, ctx)` callbacks covering `image`/`command`/`depends_on`/`dns`/
`secrets`/`deploy`/...). Keep the split as it is: the `SERVICE_KEYS` registry
holds the keys that share one `emit(value) -> list[Token]` interface, and each
*structural* key's behavior stays in the module that owns its concern
(`emit.py`, `graph.py`, `pod.py`, `stores.py`, `resources.py`,
`healthcheck.py`), with `keys.STRUCTURAL_KEYS` as the gate's accept-list.

## Context

Architecture-review candidate 3 flagged a "split-brain": `SERVICE_KEYS`
single-sources validate+emit per key, but the 20 `STRUCTURAL_KEYS` are a bare
name set whose behavior lives across six modules, and the supported-key name
list is echoed again in `parsing.SUPPORTED_SERVICE_KEYS` and the 49-name
`test_keys.py` snapshot. Two fixes were on the table: (1) a uniform structural
registry with an `emit(value, ctx)` interface; (2) a narrow single-sourcing of
the key-*name* list from each owning module.

## Decision & rationale

- **Structural keys are heterogeneous — at least six distinct emit shapes**, so
  they share no single interface:
  - *slot-occupiers* — `image`/`build` (image token), `command`/`entrypoint`
    (argv tokens): they don't produce `--flags` at all;
  - *project_dir flag-producers* — `environment`/`env_file`, `volumes`/`tmpfs`;
  - *healthcheck* — a sub-mapping → `--health-*`, also driving `wait_healthy`;
  - *graph keys* — `depends_on`/`networks`/`hostname`/`container_name`: drive
    ordering and pod-wide `--add-host`, not per-service flags;
  - *pod-level aggregated* — `dns`/`dns_search`/`dns_opt`/`sysctls`: unioned
    across all services onto `podman pod create`;
  - *document-scoped* — `secrets`/`configs`/`deploy`: need compose defs +
    closure order + `project_dir`, and emit create/teardown lines too.
- `SERVICE_KEYS` is a real registry precisely because its ~29 keys all fit one
  shape ("two adapters = a real seam"). A structural registry spanning the six
  shapes above would need `Any`-typed / variadic callbacks — **a false seam**.
- It would also **scatter cohesive logic** away from its concern: `depends_on`
  belongs with the graph, `dns` with the pod, `secrets` with stores, `deploy`
  with resources. Centralizing them behind a dispatcher trades locality for a
  lookup table — the opposite of a deep module.
- `STRUCTURAL_KEYS` holds **no behavior** (deletion test): delete it and only
  the gate's accept-list and the snapshot test break, never an emit path. The
  sole duplicated knowledge is the key-*name* list.
- **The narrow single-sourcing (option 2) was also weighed and declined now.**
  Deriving the accept-list from per-module declared key sets would distribute
  six small sets plus import edges to remove a minor duplication that the
  snapshot + disjoint tests already guard loudly (no silent-bug risk). Not worth
  it at this size and stage.

## Revisit trigger

Reopen — and when reopening, reach for the **narrow name single-sourcing**, not
a registry — if either holds:

- a **third reader** of the supported-key name set appears beyond the parsing
  gate and the owning-module handlers (e.g. a `--list-supported-keys` feature,
  or docs generated from the key set), so single-sourcing the list pays back
  across more than the gate; or
- **several new structural keys arrive that share one new uniform shape** (e.g.
  a cluster of new pod-level aggregated keys), at which point a **narrow
  sub-registry for that one shape** — a `SERVICE_KEYS`-analog for the new emit
  signature — is warranted, never a universal structural registry spanning all
  shapes.
