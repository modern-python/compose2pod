# Structural keys and schema validators stay in their owning modules

`SERVICE_KEYS` holds the keys that share one `emit(value) -> list[Token]` interface. The
structural keys in `STRUCTURAL_KEYS` stay in the module that owns their concern (`emit.py`,
`graph.py`, `pod.py`, `stores.py`, `resources.py`, `healthcheck.py`) because they have at least
six emit shapes: `entrypoint` emits on both sides of the image token and its string form cancels
`command`, `volumes` and `env_file` need `project_dir`, `depends_on` drives ordering, `dns` and
`sysctls` aggregate onto the pod, `secrets` and `deploy` need document scope. A registry over
them would be `Any`-typed callbacks, a false seam, and widening `KeySpec.emit` to carry a context
for the two keys that need it would tax the ~29 that do not, which is why `tmpfs` joined the
registry and `volumes` did not. The same holds for the ~14 strict-schema validators: the shareable
core is a three-line unknown-key check and everything around it differs per site (`x-` policy,
pre-check shape, required keys, message text), so only the two identical definition validators
share `parsing._validate_top_level_definition`. Reopened 2026-07-15 with two reviewers blind to
this record arguing opposite sides; both converged on it. A third reader of the key-name list, or
a cluster of new keys sharing one new shape, earns a narrow sub-registry for that shape, never a
universal one.
