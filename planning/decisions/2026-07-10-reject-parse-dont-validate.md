---
status: accepted
summary: Do not restructure the validate->emit seam as parse-don't-validate (a typed CheckedDocument that emit consumes); emit._plan now calls validate() itself, so both public emit entry points are safe by construction, and a typed-model rewrite plus a breaking API would buy nothing further.
supersedes: null
superseded_by: null
---

# Reject parse-don't-validate for the validate -> emit seam

**Decision:** Keep `validate(compose) -> warnings` and `emit_script(compose,
options) -> str` both taking the raw compose dict. Do not introduce a typed
`CheckedDocument` that `validate`/`parse` produces and `emit` consumes (the
"parse, don't validate" restructuring — Candidate 3 of the architecture review).

## Context

The architecture review proposed type-enforcing the seam: `validate` would
return a normalized, typed model that `emit` consumes, so the type system
guarantees `emit` only ever sees checked input, rather than relying on
`cli.py`'s call-order convention (`validate` then `emit_script`). It was flagged
Speculative at the time.

Two of the review's candidates then shipped and changed the calculus:

- The **service-key registry** (`changes/2026-07-09.08`) single-sourced each
  declarative key's validate + emit, so `emit` no longer re-derives shape
  knowledge.
- **validate() owning every shape emit reads** (`changes/2026-07-10.01`) was
  believed, at the time this decision was first written, to make the
  shape-reading functions robust enough that a direct `emit_script(dict)`
  call on a malformed document would fail with `UnsupportedComposeError`,
  not a raw crash. That belief was wrong — see the note below.

**Correction, added when the gap this decision names was actually closed**
(`changes/2026-07-13.10`, "Round 8"): the claim above was false when
written, and stayed false through seven further review rounds that each
hardened another shape-reading function and re-asserted it — because every
round hardened callers reached *through* `validate()`, and none checked
whether `validate()` itself was reachable from `emit_script`/
`referenced_variables`'s own call graph. It was not: `emit_script` is
exported from `compose2pod`, `referenced_variables` is public as
`compose2pod.emit.referenced_variables`, and a library caller can call
either directly, and
doing so on a malformed document reached a raw `KeyError`/`TypeError`, or
worse, silently emitted a corrupted flag value (e.g. `--user "{'a': 1}"`
for `user: {a: 1}`) — identical to what this decision assumed was already
fixed. The gap was closed not by further hardening individual readers, but
by giving `emit._plan` — the single traversal both public entry points
project from — its own call to `validate(compose)`, discarding the returned
warnings (the CLI already prints its own copy from its own `validate()`
call). This is a *mechanism* difference from what this decision originally
described, not a reopening of it: see below.

## Decision & rationale

After those two changes — and now, after the Round 8 correction above —
parse-don't-validate's *unique* remaining benefit is narrow: preventing a
library caller from calling `emit_script` on a **valid-but-unvalidated**
dict (skipping warnings/normalization). That gap is:

- **CLI-unreachable** — the only product entry point always calls `validate()`
  before `emit_script()`.
- **Already de-risked for malformed input** — not because the shape-reading
  functions are individually robust against every malformed input (that
  claim was false, per the correction above), but because `emit._plan`
  (`compose2pod/emit.py`) calls `validate(compose)` itself, before reading
  anything else out of `compose`. Both public entry points that project
  from `_plan` — `emit_script` and `referenced_variables` — are safe by
  construction of that one call site, not by relying on `cli.py`'s
  call-order convention or on every reader being individually hardened.

Against that marginal gain, the cost is real: a typed `CheckedDocument` for the
whole ~30-key subset, rewriting the just-built, 100%-covered registry so its
specs produce/consume typed fields, and a **breaking** public-API change
(`validate` -> `parse`, `emit_script` signature). A thin "branded" wrapper
(`frozen CheckedDocument` holding the dict, constructible only via `parse`)
avoids the model rewrite but still carries the breaking API for a near-zero real
gain, since `emit` would still read the wrapped dict.

Parse-don't-validate is the architecturally pure pattern, but it does not earn
its keep at this codebase's size and stage — it is the over-engineering the
review itself flagged, more so now that Candidates 1-2 shrank its payoff. This
also fits the zero-dependency, minimal-footprint ethos
(`decisions/2026-07-03-zero-dependency-core.md`).

## Revisit trigger

- A **second `emit` consumer** appears — a distinct output format alongside the
  pod script, or another module that renders from the compose model — so the
  typed model would pay back across more than one consumer; or
- `emit._plan`'s own `validate()` call (added to close this decision's gap —
  see the Round 8 correction above) is bypassed or removed, and a real caller
  emits unvalidated input and ships wrong output as a result — not a
  hypothetical convention violation, an actual regression in the enforced
  invariant.
