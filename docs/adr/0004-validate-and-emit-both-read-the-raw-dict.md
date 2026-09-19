# `validate` and `emit` both read the raw dict

`validate(compose)` and `emit_script(compose, options)` both take the raw compose dict. There is
no typed `CheckedDocument` produced by one and consumed by the other, and the graph queries in
`graph.py` (`depends_on`, `hostnames`, `startup_order`) normalise and raise on bad shape wherever
they are called rather than being computed once and threaded through. The gate is
target-agnostic and emit is target-scoped, so one shared model cannot span them, and the typed
model would cost a registry rewrite plus a breaking API for a gap that is closed differently:
`emit._plan`, the single traversal both `emit_script` and `referenced_variables` project from,
calls `validate(compose)` itself, so a library caller cannot reach a raw `KeyError` or a corrupted
flag. A second emit consumer is what would make the typed model pay.
