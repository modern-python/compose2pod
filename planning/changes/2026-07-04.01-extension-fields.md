---
summary: Accept Compose `x-` extension fields at every validated level, ignoring them silently.
---

# Design: Support Compose extension fields (`x-`)

## Summary

compose2pod rejects any top-level key it does not explicitly support, so a
document that carries a Compose extension field — e.g. `x-application-defaults`
holding a YAML anchor for reuse — fails with
`unsupported top-level keys: ['x-application-defaults']`. The Compose spec
reserves the `x-` prefix for arbitrary user data that tools must ignore. This
change teaches `validate()` to skip any `x-`-prefixed key at every mapping level
it inspects (top level, service, healthcheck), silently and without warning.
The change is confined to `compose2pod/parsing.py`; a new
`architecture/supported-subset.md` capability file pins down the supported
subset, including this rule.

## Motivation

A real-world CI compose file uses the idiomatic anchor pattern:

```yaml
x-application-defaults: &application-defaults
  build:
    context: .
    dockerfile: ./Dockerfile
services:
  application:
    <<: *application-defaults
    ...
```

PyYAML's `safe_load` already resolves the `&anchor` and `<<:` merge key at load
time — after loading, each service correctly contains the merged `build:` block.
The *only* thing that fails is the leftover top-level `x-application-defaults`
key, which `validate()` (`parsing.py:73`) rejects because it is not in
`SUPPORTED_TOP_LEVEL_KEYS`. Verified: once `x-` keys are accepted, the entire
file validates and emits with only the normal `ports`/`restart`/`stdin_open`/
`tty` "ignoring" warnings.

Extension fields are a first-class Compose construct, not an edge case: the spec
states any key prefixed with `x-` at any level is a user extension that
consuming tools ignore. Holding YAML anchors in a top-level `x-` block is the
canonical way to share config across services. Rejecting them makes compose2pod
refuse valid, common compose documents.

## Non-goals

- No handling of YAML anchors / `<<:` merge keys in code — PyYAML resolves them
  at load time, and JSON input has none.
- No interpretation of extension-field *contents*. `x-` values are ignored
  wholesale, never read for behavior.
- No change to which real service keys are supported, ignored, or rejected.

## Design

### 1. Ignore `x-` keys at every validated level (`parsing.py`)

The rule is a single predicate — `key.startswith("x-")`, lowercase as the spec
mandates — applied at each of the three places `validate()` inspects keys:

- **Top level** (`validate`, line 73): exclude `x-` keys from the `unknown_top`
  set so `x-application-defaults` and peers no longer raise. The `unknown_top`
  computation becomes a comprehension that drops supported keys *and* `x-` keys.
- **Service level** (`_validate_service`, line 30 loop): an `x-`-prefixed service
  key is skipped before the ignored/unsupported branches — no warning, no raise.
- **Healthcheck level** (`_validate_service`, line 36 loop): an `x-`-prefixed
  healthcheck key is skipped rather than raising `unsupported healthcheck key`.

Ignoring is **silent**: unlike `ports`/`restart` (which warn because compose2pod
drops real runtime behavior), an `x-` field carries nothing actionable, so a
warning would be noise. This is a deliberate decision (see Testing — a test
guards the no-warning behavior).

Nothing downstream needs to change: `emit.py` and `graph.py` only ever access
*known* service keys and iterate `compose["services"]`; they never walk service
keys generically nor read top-level keys other than `services`. Skipping `x-`
in `validate()` is therefore sufficient.

### 2. Seed `architecture/supported-subset.md`

The parsing subset has no capability file yet. This change creates
`architecture/supported-subset.md` as the living truth for what compose2pod
accepts, ignores, and rejects: the supported top-level keys, the supported /
ignored / rejected service keys, the healthcheck subset, volume constraints,
`depends_on` conditions, and — added here — the `x-` extension-field rule. The
file carries no frontmatter (living prose, dated by git), per the
`architecture/` convention.

## Testing

TDD, and `just test-ci` must stay at 100% line coverage.

- Unit: a top-level `x-foo` key is accepted (`validate` does not raise).
- Unit: a service-level `x-foo` key is accepted **and produces no warning** —
  asserts the returned warnings list contains nothing mentioning `x-foo`,
  guarding the "silent" decision.
- Unit: a healthcheck-level `x-foo` key is accepted (does not raise).
- Integration: the anchor-based compose document from Motivation round-trips
  through `_read_compose` → `validate` → `emit_script` and produces a script.

## Risk

Low. The `x-` prefix is spec-reserved and unambiguous, so skipping such keys
cannot mask a real supported/unsupported key. The blast radius is three edits in
one function. The main risk is *over*-narrow matching (e.g. only top-level),
which the spec-faithful "everywhere" scope and the three-level test set avoid.
