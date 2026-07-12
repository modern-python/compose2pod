# Deferred

Real-but-unscheduled items, each with a revisit trigger.

## Add the compose2pod brand lockup to the README header

Sibling org repos (`db-retry`, `eof-fixer`, `semvertag`, …) open their README
with a centered brand lockup (`<picture>` → `modern-python/.github`
`brand/projects/<name>/lockup-{dark,light}.svg` + `lockup.png`) above the badge
row. compose2pod's README carries the badges but no lockup: the `.github`
repo's `brand/build/projects.py` `MANIFEST` has no `compose2pod` entry, so no
glyph or lockup asset is generated for it. Adding one requires designing a
distinctive gold inner symbol (a `sym.compose2pod(...)` in the brand build),
regenerating assets, and shipping that in a `.github` PR — brand design that
needs the org owner's sign-off.

**Revisit trigger:** the org owner approves a compose2pod brand glyph, or a
`.github` PR adds `compose2pod` to the brand `MANIFEST` and regenerates the
lockup assets. Then wire the `<picture>` block above the badges (dropping the
`# compose2pod` H1) to match siblings.

## Harden the pod-cleanup trap's nested `shlex.quote` for the `emit_script` library path

`emit.py`'s `EXIT` trap quotes the pod name with a nested `shlex.quote` inside
an already-quoted trap command. The CLI never reaches this edge because
`cli.py` validates pod names against `POD_NAME_PATTERN` before calling
`emit_script`, but a library caller invoking `emit_script`/`EmitOptions`
directly can pass an unvalidated `pod` value that produces a malformed or
unsafe trap command.

**Revisit trigger:** a library-API test is added that exercises `emit_script`
with adversarial `pod` values (quotes, spaces, shell metacharacters), or a bug
report surfaces from a caller using the library path without CLI validation.
Needs its own change file.
