# Deferred

Real-but-unscheduled items, each with a revisit trigger.

## Forms Docker accepts that compose2pod does not yet parse

`decisions/2026-07-14-docker-rejection-parity.md` rule two: where Docker accepts
a construct and **podman could express it**, compose2pod should accept it too.
Where it does not yet, that is a *current limitation* — a deferred piece of the
subset, not a bug and not a design position. Each item below is a **form** of a
capability compose2pod already supports, refused only because the parser was
never written. Every one was measured against `docker compose config` v5.1.2.

- **Quoted booleans on a boolean key — 30 measured cells.** Docker applies a
  YAML-1.1-style bool cast to a *string* on a boolean field: `tty: "true"`,
  `read_only: "yes"`, `privileged: "on"`, and bare `yes` are all accepted (`"1"`
  and `"banana"` are not). compose2pod requires a real YAML boolean on all six
  boolean keys (`init`, `read_only`, `privileged`, `oom_kill_disable`, `tty`,
  `stdin_open`). Podman never sees the spelling — it sees the flag or nothing —
  so there is no podman reason to refuse. **Carries a trap:** accepting a string
  means coercing it *before* emit. `keys._bool` emits `[flag] if value else []`,
  and the non-empty string `"false"` is truthy in Python, so a naive fix would
  emit `--read-only` for a value the user wrote as false. Coerce first, then test.
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

**Revisit trigger:** a user reports a compose file that `docker compose` runs and
compose2pod refuses — most likely the quoted-boolean case, since anything that
templates or round-trips YAML (`yq`, Helm-style generators, quote-everything
house styles) produces it. The conformance harness reports these as
`over-reject`, so they stay visible rather than forgotten.

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
