# Glossary

The project's ubiquitous language — the domain terms that code, specs, and
capability pages share. One term, what it *is* (not what it does), and the
synonyms to reject.

**Service-key spec**:
The `(validate, emit)` pair for one Compose service key — how that key is checked
and how it renders to `podman run` flags. In code, `KeySpec` in `keys.py`.
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
