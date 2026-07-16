# Deferred

Real-but-unscheduled items, each with a revisit trigger.

## Forms Docker accepts that compose2pod does not yet parse

`decisions/2026-07-14-docker-rejection-parity.md` rule two: where Docker accepts
a construct and **podman could express it**, compose2pod should accept it too.
Where it does not yet, that is a *current limitation* — a deferred piece of the
subset, not a bug and not a design position. Each item below is a **form** of a
capability compose2pod already supports, refused only because the parser was
never written. Every one was measured against `docker compose config` v5.1.2.

- **Long-form `volumes`.** The mapping form raises; podman expresses it with
  `--mount`.
- **Windows drive-letter volume source.** `volumes: ["C:\data:/var"]` with no
  top-level declaration: Docker ACCEPTS (measured, `docker compose config`
  v5.1.2 -- it special-cases a leading `<letter>:\` so the drive letter stays
  part of the source, resolving to `{source: C:\data, target: /var}`).
  compose2pod REJECTS ("refers to undefined volume 'C'"). The colon-based
  source extraction shared by `parsing._classify_volume` and `emit.py`'s
  `_volume_flags` (`source, _, _ = volume.partition(":")`) splits on the
  *first* colon regardless, so for this entry `source` is just the single
  letter `"C"` -- itself a syntactically valid volume-name grammar match, not
  the unparseable string a naive read of "doesn't match the name pattern"
  would suggest. Fixing it needs genuine Windows-drive detection ahead of the
  split, in both call sites, not a grammar-check swap. Pre-existing since the
  named-volume reference check was introduced; not touched by the 2026-07-15
  tilde-bind-mount fix that discovered it.

**Revisit trigger:** a user reports a compose file that `docker compose` runs and
compose2pod refuses — most likely long-form `volumes`, a common form in
hand-written and generated compose files alike. The conformance harness reports
these as `over-reject`, so they stay visible rather than forgotten.

Two other `over-reject` cells the harness reports — `sysctls: ["a"]` and
`volumes: ["a"]` — are *not* deferred parsers: they are measured legitimate
refusals (podman cannot form the flag), recorded in
`decisions/2026-07-16-list-of-str-refusals.md`, not here.

## Non-target `depends_on` graph is not validated outside the target's closure

`planning/decisions/2026-07-14-docker-rejection-parity.md`'s hard rule is
`accepted(compose2pod) ⊆ accepted(docker)`, no exceptions — but this one item
is a deliberate, maintainer-ruled exception to it, not an oversight, because
closing it fights compose2pod's own validation design rather than completing
an unfinished parser (contrast the section above, where every item is a
genuine gap). Two false greens survive against the hard rule, both measured
against `docker compose config` v5.1.2, same YAML both oracles:

- **`depends_on: [ghost]` on a service OUTSIDE the target's dependency
  closure**, naming a service nothing in the document defines — Docker
  REJECTS the whole document ("undefined service"); compose2pod ACCEPTS it
  (and runs successfully), because the target never reaches `ghost`'s
  declaring service in its own closure walk.
- **A dependency cycle among services OUTSIDE the target's closure** —
  Docker REJECTS the whole document; compose2pod ACCEPTS it, same reason.

**Why this is deferred, not fixed:** `validate()` (`compose2pod/parsing.py`)
checks every `depends_on` entry's *condition* document-wide
(`_validate_depends_on`), but never cross-checks a dependency *name* against
the full service set — that check happens only inside `startup_order`
(`compose2pod/graph.py`), which walks exclusively the `--target` service's
own `depends_on` closure (see `architecture/supported-subset.md`'s `##
depends_on` section: "`startup_order` ... walks the target's `depends_on`
closure and raises if a dependency names a service absent from the
document"). A service outside that closure never runs, by design — `profiles`
inertness and the "closure authoritative" stance both already lean on the
same fact (`architecture/supported-subset.md`'s Service keys section) — so
its own `depends_on` graph, however broken, can never desync a running
script from the document. Validating it anyway would mean a document-wide
pre-pass wholly independent of `--target`, cutting against the one design
choice (closure-scoped validation, not whole-document validation) that makes
compose2pod's gate cheap to reason about and keeps an unrelated service's
typo from blocking every other target in a shared compose file. The
maintainer ruled: catalogue, don't fix, pending a concrete need.

**Revisit trigger:** a document-wide pre-validation pass over `depends_on`
existence and cycles, run independently of the target closure (so it costs
nothing extra for a document where every service is well-formed, and only
ever narrows acceptance for one that is not), would close both. Worth
building if a user hits either case in practice — most likely the cycle,
since an unrelated-service typo is comparatively easy to notice by eye.

## Unify the store render/vars seam

`stores.create_lines` (rendered lines) and `stores.referenced_variables` (the
vars those lines expand) are two functions that must agree — the same
"two readers, one source" pattern that `2026-07-12.03` fixes at the emit level,
one level down inside `stores.py`. Folding them into one per-line
`(text, vars)` producer would make store-side drift unrepresentable too.

**Revisit trigger:** a third reader of the store create-lines appears, or a
drift bug surfaces between the two store functions (a `$VAR` a create line
expands that `referenced_variables` fails to report). Left out of
`2026-07-12.03` to keep that change emit-internal and avoid re-touching the
just-shipped store interface.
