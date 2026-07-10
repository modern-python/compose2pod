---
status: accepted
summary: Do not restructure the validate->emit seam as parse-don't-validate (a typed CheckedDocument that emit consumes); the registry + complete-gate changes already delivered the safety, and the residual type-enforcement gap is CLI-unreachable and not worth a typed-model rewrite plus a breaking API.
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
- **validate() owning every shape emit reads** (`changes/2026-07-10.01`) made
  the shape-reading functions robust: a direct `emit_script(dict)` call on a
  *malformed* document now fails with `UnsupportedComposeError`, not a raw
  crash, and `validate()` exercises every shape.

## Decision & rationale

After those two changes, parse-don't-validate's *unique* remaining benefit is
narrow: preventing a library caller from calling `emit_script` on a
**valid-but-unvalidated** dict (skipping warnings/normalization). That gap is:

- **CLI-unreachable** — the only product entry point always calls `validate()`
  before `emit_script()`.
- **Already de-risked for malformed input** — the robust readers reject bad
  shapes at emit time regardless of whether `validate` ran.

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
- the "`emit` only sees validated input" convention actually causes a bug (a
  real caller emits unvalidated input and ships wrong output), not a
  hypothetical one.
