---
status: accepted
summary: volumes stays hand-rolled (not a SERVICE_KEYS registry key) — its emit needs project_dir and its reference check is document-level, a poor registry fit; only tmpfs was moved.
supersedes: null
superseded_by: null
---

# volumes stays hand-rolled, outside the service-key registry

**Decision:** `tmpfs` moved into `SERVICE_KEYS` (`2026-07-17.01`); `volumes` did not, and stays hand-rolled.

## Context

The registry-unification refactor considered moving both `volumes` and `tmpfs`.
`tmpfs` is a uniform scalar-or-list flag key and fits cleanly. `volumes` does not:
its emit (`emit._volume_flags`/`_mount_flag`) needs `project_dir` (relative bind
resolution), which `KeySpec.emit(value)` cannot supply without widening the
signature for all ~30 keys; and its validation is partly document-level
(`_validate_volume_references` cross-checks named-volume sources against the
top-level `volumes:` block), which cannot live in a per-service
`KeySpec.validate(name, key, value)`.

## Decision & rationale

Leave `volumes` hand-rolled. A registry `volumes` would be a thin `KeySpec`
wrapper around three still-custom, still-split pieces (custom validate, custom
project_dir emit, separate document-level reference pass), bought by degrading
the clean `emit(value) -> tokens` interface for the 28 keys that do not need
`project_dir`. The common case would pay for the uncommon one. The long-form
`--mount` work (`2026-07-16.05`) made `volumes` an even worse fit.

## Revisit trigger

`KeySpec.emit` is widened to carry a context (`project_dir`) for another,
independent reason — then moving `volumes`/`env_file` in costs nothing extra and
should be reconsidered.
