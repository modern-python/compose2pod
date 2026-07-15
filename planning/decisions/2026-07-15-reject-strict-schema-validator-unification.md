---
status: accepted
summary: Reject a unified `validate_schema(where, mapping, fields)` for the repeated strict-schema validators; keep each block's shape check in place, since the "common core" is 2-4 lines and the surrounding shape diverges per site (x- policy above all). `_validate_top_level_definition` already shares the only two cases that genuinely match.
supersedes: null
superseded_by: null
---

# Reject a unified strict-schema validator

**Decision:** Do not introduce a shared `validate_schema(where, mapping, fields)`
helper to unify the ~14 hand-written "known-key set, reject unknown, run each
present key's grammar" sites across `parsing.py`, `values.py`, `stores.py`,
`resources.py`, and `graph.py`. Keep each block's validator as it is. The only
shared shape that earns its keep — the top-level `networks`/`volumes` definition
pair — is already factored into `parsing._validate_top_level_definition`.

## Context

Architecture-review candidate 4 flagged that the strict-schema pattern is
re-implemented five-plus times: `_validate_network_entry_value`
(`_DOCKER_NETWORK_ENTRY_KEYS`), `_validate_top_level_definition`
(`_NETWORK_DEFINITION_KEYS`/`_VOLUME_DEFINITION_KEYS`), `_validate_port_long_form`
(`_PORT_LONG_FORM_KEYS`), the two ipam sub-schemas, `stores._ref_source`
(`_LONG_FORM_KEYS`), `_validate_build` (`_DOCKER_BUILD_KEYS`), the deploy blocks,
and `graph._depends_on_entry_condition`. The proposed deepening was one
`validate_schema(where, mapping, fields)` deep module every caller feeds its own
field table.

## Decision & rationale

The candidate was walked down its design tree (grilling) against the actual code.
The unifiable core is genuinely small — `unknown = <diff>; if unknown: raise
f"{where}: unsupported keys {sorted(...)}"`, sometimes preceded by
`require_string_keys` — 2 to 4 lines. Everything wrapped around it diverges per
site, so a single signature is a **false seam** that would need a flag for every
divergence:

- **`x-` extension policy is not uniform.** Only 4 sites skip `x-` keys (build,
  the per-service network entry, `depends_on`, and the top-level definition); the
  other ~8 (both ipam sub-schemas, both store schemas, port long-form, the
  `external` map, build-secrets, the four deploy blocks) deliberately do **not** —
  `x-` is not legal there. A shared helper cannot hold a fixed `x-` policy; it
  would have to take `allow_extensions` per call. This divergence alone forecloses
  one signature.
- **Two non-string-key idioms with different messages.** The comprehension form
  runs `require_string_keys` first (precise `"key ... must be a string"`); the
  bare `set(m) - KEYS` form lets a non-string key fall into `"unsupported keys"`.
  Unifying would change one site's error text or the other's.
- **Pre-check shape differs:** null-or-dict (definitions, network entry) vs
  str-or-dict (build, store ref) vs dict-only (ipam, port).
- **Required keys are bespoke and site-specific:** port needs `target`, store ref
  needs `source`, `depends_on` needs `condition` — each checked around the unknown
  check, not by it.
- **Field-dispatch context differs:** definition validators receive `ident` with
  the label closured in (`_validate_definition_string("network")`); network-entry
  validators receive the service `name` and drop the network name from their
  messages. Same loop, different error text.
- **Nesting:** ipam's `config` is a list of per-subnet sub-schemas; the rest are
  flat.

To span all of that, `validate_schema` would carry `allow_null`, `allow_str`,
`allow_extensions`, `required_keys`, a message prefix *and* a separate field
context — a wide, flag-laden interface whose body is still a 3-line loop. That is
the same over-abstraction-across-divergent-shapes error already rejected for the
structural keys (`decisions/2026-07-12-reject-structural-key-registry.md`), at
smaller scale. Depth here is illusory: the interface would be nearly as complex as
the implementation.

`_validate_top_level_definition` is the right amount of sharing — it unifies the
two cases (network and volume definitions) that match shape exactly (null-or-dict,
`x-` skip, `(ident, key, value)` field dispatch, identical message frame). Pushing
past those two trades locality for a lookup table.

A narrower `reject_unknown_keys(where, mapping, allowed, *, allow_extensions)`
helper — mirroring the existing `require_string_keys` — was weighed and also
declined: it would concentrate only the message format and the `x-` flag across
~12 one-line call sites, a modest churn for a check the per-site tests already
guard, and the two non-string-key idioms would still have to be reconciled first.
Not worth it at this size and stage — the same call
`decisions/2026-07-12-reject-structural-key-registry.md` made for its own narrow
single-sourcing.

## Revisit trigger

Reopen if **three or more new strict-schema blocks arrive that share one exact
shape** — same pre-check, same `x-` policy, same field-dispatch convention, same
message frame — at which point a narrow helper for *that one shape* (a
`_validate_top_level_definition` analog), not a universal `validate_schema`, is
warranted. A new block that merely resembles an existing one at the 3-line-loop
level is not a trigger.
