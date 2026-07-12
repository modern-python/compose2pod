---
status: accepted
summary: Keep graph depends_on/hostnames/startup_order as validate-on-read queries recomputed independently by the gate and emit; reject a shared normalized graph (blocked by the target-agnostic gate vs target-scoped emit split and by parse-don't-validate) and reject splitting the queries' normalization from their shape-raise.
supersedes: null
superseded_by: null
---

# Keep graph queries as validate-on-read; do not thread a shared graph

**Decision:** Keep `depends_on`, `hostnames`, and `startup_order`
(`compose2pod/graph.py`) as query functions that normalize *and* raise on bad
shape, recomputed independently wherever they are called. Do not compute a
normalized graph once and thread it from `validate` into emit, and do not split
each query's normalization from its shape-validation.

## Context

Architecture-review candidate 4 (Speculative) flagged a "query-as-validator"
smell: `hostnames(services)` is called at the gate (`parsing.py:135`) purely to
trigger its shape-raise and its result discarded, then recomputed in `_plan`
for real use; `depends_on` normalizes-and-raises and is re-run at several sites
(inside `startup_order`, at the gate, and twice in `_plan`). Two fixes were
weighed: thread a normalized graph computed once and shared between `validate`
and emit; or split each query into a pure normalizer plus a separate validator.

## Decision & rationale

- **The gate and emit operate at different scopes, so a shared graph cannot
  span them.** `validate(compose)` is target-agnostic; emit is target-scoped
  (`_plan` calls `startup_order(services, target)`). The gate cannot compute the
  dependency closure at all without a target, and cycle/unknown-dep detection is
  inherently target-scoped. The gate does shape-validation; emit does
  target-scoped assembly. That divide is inherent, not incidental.
- **The independent recompute is the accepted price of parse-don't-validate**
  (`decisions/2026-07-10-reject-parse-dont-validate.md`): `validate` and emit are
  independent readers of the raw dict with no shared computed model. Threading a
  normalized graph reintroduces exactly the coupling that decision declined.
- **Query-as-validator is the "validate owns emit shapes" pattern**
  (`changes/2026-07-10.01-validate-owns-emit-shapes.md`): the gate calls
  `hostnames`/`depends_on` to validate the shapes emit later reads. Splitting the
  raise out of the query would leave emit's reader non-validating — a direct
  `emit_script(malformed_dict)` would crash instead of raising
  `UnsupportedComposeError`, regressing that robustness.
- **The recompute is cheap.** Normalizing `depends_on` and walking `hostnames`
  are small in-memory passes over the service dict; the only non-test caller
  (cli) runs once per process.
- **The one ADR-neutral change — deduping `depends_on` within `_plan` — is
  marginal and partial** (`startup_order` normalizes independently regardless),
  not worth the added state.

## Revisit trigger

- the gate becomes **target-aware** (e.g. `validate` gains a target-scoped mode),
  dissolving the scope mismatch that blocks a shared graph; or
- the graph traversal shows up as a **real hotspot in a profiled run** on large
  compose documents — at which point the fix is memoizing or threading the
  normalized graph *within emit*, still never a shared `validate`↔emit model.
