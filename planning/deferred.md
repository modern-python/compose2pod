# Deferred

Real-but-unscheduled items, each with a revisit trigger.

## Forms Docker accepts that compose2pod does not yet parse

`decisions/2026-07-14-docker-rejection-parity.md` rule two: where Docker accepts
a construct and **podman could express it**, compose2pod should accept it too.
Where it does not yet, that is a *current limitation* — a deferred piece of the
subset, not a bug and not a design position. Each item below is a **form** of a
capability compose2pod already supports, refused only because the parser was
never written. Every one was measured against `docker compose config` v5.1.2.

- **Quoted booleans on a boolean key — 30 measured cells, now more than 30.**
  Docker applies a YAML-1.1-style bool cast to a *string* on a boolean field:
  `tty: "true"`, `read_only: "yes"`, `privileged: "on"`, and bare `yes` are
  all accepted (`"1"` and `"banana"` are not). compose2pod requires a real
  YAML boolean on all six boolean keys (`init`, `read_only`, `privileged`,
  `oom_kill_disable`, `tty`, `stdin_open`). Podman never sees the spelling —
  it sees the flag or nothing — so there is no podman reason to refuse.
  **Carries a trap:** accepting a string means coercing it *before* emit.
  `keys._bool` emits `[flag] if value else []`, and the non-empty string
  `"false"` is truthy in Python, so a naive fix would emit `--read-only` for
  a value the user wrote as false. Coerce first, then test.
  `2026-07-15.08` deliberately kept `build`'s own three boolean keys (`no_cache`,
  `pull`, `privileged` — under `build:`, not the top-level `privileged` service
  key) consistent with this same limitation rather than accepting a quoted
  string there and not here: `build.no_cache: "true"` is refused for the same
  reason, and a `${VAR}` reference on any of the three is a separate, already-
  accepted case (host-state-dependent, `values.has_variable`), not this one.
  Task 12 (2026-07-15) added six more affected keys to this same family, all
  measured the identical shape (a genuine `${VAR}` reference is carved out —
  host-dependent — but a literal quoted string is refused): the top-level
  `networks:`/`volumes:` definition schema's `internal`, `attachable`,
  `enable_ipv6` (network-only) and `external`'s boolean form, plus
  `depends_on`'s long-form `restart` and `required`.
- **Compound and hour healthcheck durations.** `interval: 1h30m` and `1h` raise;
  Docker accepts both. `1h30m` is 5400 seconds and the value only paces the
  script's polling loop, so podman can honor it. `architecture/supported-subset.md`
  presents this refusal as a safety choice ("rather than being silently
  truncated"); under rule two it is simply an unfinished parser.
- **Long-form `volumes`.** The mapping form raises; podman expresses it with
  `--mount`.
- **`volumes: ["a"]`** — a colon-less relative entry. Docker accepts it;
  compose2pod requires an anonymous volume to be an absolute path. Worth
  re-measuring what podman does with it before deciding whether this is a
  legitimate refusal (like `sysctls: ["a"]`, which genuinely cannot form a flag)
  or another unfinished form.
- **Long-form `env_file`.** Docker accepts the mapping form
  (`{path: ..., required: ..., format: ...}`) in addition to a plain string
  or list of strings; compose2pod's `_validate_string_or_string_list` requires
  every entry to be a string. The mapping resolves to the same `--env-file
  <path>` flag `emit._env_flags` already emits for the string form, so
  `required` (skip the flag instead of refusing, when the file is missing)
  and `format` (`raw` vs the default interpolated parsing) are the only real
  work.

**Revisit trigger:** a user reports a compose file that `docker compose` runs and
compose2pod refuses — most likely the quoted-boolean case, since anything that
templates or round-trips YAML (`yq`, Helm-style generators, quote-everything
house styles) produces it. The conformance harness reports these as
`over-reject`, so they stay visible rather than forgotten.

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
