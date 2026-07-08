# Deferred

Real-but-unscheduled items, each with a revisit trigger.

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
