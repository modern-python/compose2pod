# Deferred

Real-but-unscheduled items, each with a revisit trigger.

## Support `dns` / `dns_search` / `dns_opt` as pod-wide options

Unlike `--add-host` (a per-container `/etc/hosts` edit the tool already emits),
`--dns` is coupled to the network namespace, and podman rejects it on a
container that has joined a pod's netns. So `dns` cannot be a per-container
`podman run` flag — it must be hoisted to `podman pod create --dns` and applied
pod-wide, which means reconciling the values across services (union, or error on
disagreement). This is the tool's first pod-level aggregated option; it is
feasible and invariant-preserving but has no demonstrated CI demand yet. See
`decisions/2026-07-09-reject-namespace-network-keys.md` and
`audits/2026-07-09-compose-spec-coverage.md`.

**Revisit trigger:** a user needs a service to resolve names through a specific
resolver or search domain inside the pod, or a live podman run contradicts the
documented "`--dns` invalid on a pod-joined container" behavior this deferral
assumes. Needs its own change file; validate the podman behavior first.

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
