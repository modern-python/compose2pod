# Deferred

Real-but-unscheduled items, each with a revisit trigger.

## Unify the store render/vars seam

`stores.create_lines` (rendered lines) and `stores.referenced_variables` (the
vars those lines expand) are two functions that must agree — the same
"two readers, one source" pattern that `2026-07-12.03` fixes at the emit level,
one level down inside `stores.py`. Folding them into one per-line
`(text, vars)` producer would make store-side drift unrepresentable too.

**Revisit trigger:** a third reader of the store create-lines appears, or a
drift bug surfaces between the two store functions (a `$VAR` a create line
expands that `referenced_variables` fails to report). Left out of
`2026-07-12.03` to keep that change emit-internal and avoid re-touching the
just-shipped store interface.
