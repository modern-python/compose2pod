# Glossary

The project's ubiquitous language — the domain terms that code, specs, and
capability pages share. One term, what it *is* (not what it does), and the
synonyms to reject.

**Service-key spec**:
The `(validate, emit, merge)` triple for one Compose service key — how that key
is checked, how it renders to `podman run` flags, and (for list/map-shaped keys)
how it merges across `extends`. In code, `KeySpec` in `keys.py`.
_Avoid_: handler, plugin, rule.

**Service-key registry**:
The table mapping each declarative service-key name to its service-key spec
(`SERVICE_KEYS` in `keys.py`); the single source both the gate (`validate`) and
the emitter (`run_flags`) derive from, so they cannot drift apart.
_Avoid_: map, dispatch table, lookup.

**Structural key**:
A supported service key handled *outside* the service-key registry because the
`emit(value)` interface cannot express it — it needs `project_dir`
(`env_file`, `volumes`), spans keys, or occupies the image/command slot
(`entrypoint`). Structural keys keep their own validate/emit machinery.
_Avoid_: special key, bespoke key (bespoke describes the spec body, not the key).

**Store kind**:
One flavor of podman-secret-backed store — a Compose `secret` or `config` — with
its own namespacing prefix, allowed sources, and default mount target
(`StoreKind` in `stores.py`). Both kinds render as podman secrets (podman has no
config primitive), so they differ in namespacing and mount, never in the podman
noun; the noun lives in `stores.py` alone.
_Avoid_: secret type, store type, backend.

**Store registry**:
The tuple of every store kind (`_STORE_KINDS = (SECRET, CONFIG)` in `stores.py`),
module-private so the store interface (`validate`, `flags`, `create_lines`,
`teardown_line`, `referenced_variables`) hides the kinds from callers — the same
single-source shape as the service-key registry.
_Avoid_: store list, kinds table.

**Token**:
The result of rendering one Compose value into a `podman run`/`pod create`
argument — either a literal `str` (already shell-safe) or an `Expand` (a
value carrying `${VAR}` references that expand at script-run time, not at
generation time). `Token = str | Expand` in `keys.py`.
_Avoid_: arg, flag value.

**Expand**:
A token whose Compose variable references must expand when the generated
script runs, not when compose2pod generates it. In code, `Expand` in
`keys.py`.
_Avoid_: variable, placeholder.
