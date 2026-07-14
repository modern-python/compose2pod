# Docs and gate audit

A sweep of `architecture/`, `README.md`, and the in-code comments against what
`compose2pod` actually does, hunting inconsistencies, redundancies, bugs, and
compaction candidates. Every finding below was reproduced or verified against
the code, not read off the prose. Findings spawn follow-up change files; they
are not themselves changes.

Two accepted decisions govern the fixes and both survive intact:
`decisions/2026-07-10-reject-parse-dont-validate.md` (the gate stays
`validate(dict)`, no typed model) and
`decisions/2026-07-12-reject-structural-key-registry.md` (no uniform structural
registry; each structural key's behavior stays in its owning module).

## Guiding contract

The tool's stated promise is a **complete gate**: `validate()`
(`compose2pod/parsing.py`) either accepts, warns, or raises
`UnsupportedComposeError` — it never lets a malformed document reach `emit` and
crash raw. `architecture/supported-subset.md` states it outright for
`depends_on` ("raises `UnsupportedComposeError` at the gate instead of failing
later with a raw `AttributeError`/`TypeError` when the shape is walked"), and
`decisions/2026-07-10-reject-parse-dont-validate.md` rests its entire rationale
on that promise already holding document-wide:

> **validate() owning every shape emit reads** (`changes/2026-07-10.01`) made
> the shape-reading functions robust: a direct `emit_script(dict)` call on a
> *malformed* document now fails with `UnsupportedComposeError`, not a raw
> crash, and `validate()` exercises every shape.

**That premise is false today.** Findings A1-A3 are three structural keys where
it does not hold. This is the audit's headline: an accepted decision is standing
on an invariant the code does not have. Restoring the invariant (A1-A3) makes
the decision true again — it does not reopen it.

## Bucket A — the gate is incomplete (bugs)

`environment`, `env_file`, and `volumes` are **structural keys**: they carry no
`KeySpec`, so the `SERVICE_KEYS` validate loop never sees them, and unlike
`tmpfs` / `entrypoint` / `healthcheck` they have no hand-written validator in
`parsing.py` either. `emit` walks their raw shape and crashes. All three are
reachable from the CLI with an ordinary compose file.

| # | Input | Today | Should be |
|---|-------|-------|-----------|
| A1 | `environment: "FOO=bar"` (string) | `AttributeError: 'str' object has no attribute 'items'` | `UnsupportedComposeError` |
| A2 | `env_file: 5` | `TypeError: 'int' object is not iterable` | `UnsupportedComposeError` |
| A3 | `volumes: "/data:/data"` (string) | iterates *characters*; reports `anonymous volume 'd' must be an absolute path` | `UnsupportedComposeError` |
| A4 | `--artifact nocolon` | `ValueError: not enough values to unpack` out of `emit.py:209` | clean CLI error, exit 2 |

A3 has a silent-corruption face as well as a crash face: `volumes: "/"` is
**accepted** and emits `-v "/"`, because the single character `/` happens to
pass the anonymous-volume check. A string `volumes` is never rejected as the
wrong shape; it is destructured one character at a time and whatever survives is
emitted.

A1-A3 share one root cause (a structural key with no shape validator) and one
fix shape: a `_validate_*` function in `parsing.py` mirroring the existing
`_validate_tmpfs`. No registry needed — `decisions/2026-07-12` stands.

A4 is a different code path (CLI argument, not compose input) but the same
user-visible contract: a raw traceback where a clean refusal was promised.
`emit._emit_target` splits `--artifact` on `:` without checking the value has
one, and `cli.main` only catches `UnsupportedComposeError`.

## Bucket B — inconsistent scoping (behavior)

**B1. `--add-host` mixes document-wide and closure-scoped sources, then
conflict-checks across the seam.** `emit._plan` seeds hosts from
`graph.hostnames(services)` — every service in the *document* — while
`extra_hosts` is layered per service in `order`, the target's dependency
closure. `pod._add_host_flags` then refuses any host landing on two addresses.
So a service that **never runs** can veto a valid configuration:

```yaml
services:
  app:   {image: i, extra_hosts: ["db:1.2.3.4"]}
  other: {image: i, hostname: db}          # not in app's closure; never started
```
→ `UnsupportedComposeError: service 'app': conflicting host 'db'
('127.0.0.1' vs '1.2.3.4')`

Every other aggregate in the emit path — `dns`, `dns_search`, `dns_opt`,
`sysctls`, secrets, configs — is closure-scoped. `--add-host` is the lone
exception, and `supported-subset.md:200-202` documents the split honestly as
"pre-existing, orthogonal behavior" rather than defending it.

**Resolution (ruled):** scope the emit-side host set to the closure. `hostnames`
has exactly two callers — `parsing.py:135` (shape validation, stays
document-wide, loses nothing) and `emit.py:233` (the add-host source). Only the
latter changes. An `--add-host` entry for a service that never starts is a lie
anyway: it points a name at `127.0.0.1`, where nothing is listening, turning an
honest NXDOMAIN into a connection-refused.

## Bucket C — doc drift (all verified against code)

| # | Doc says | Code says |
|---|----------|-----------|
| C1 | `supported-subset.md:70`: `annotations` shares "the `_MAP_FLAGS` machinery" | `_MAP_FLAGS` does not exist anywhere in the repo. It is `_map()` (`keys.py`). |
| C2 | `glossary.md:8`: a service-key spec is the `(validate, emit, merge)` **triple** | `supported-subset.md:35`: "each as a `(validate, emit)` **pair**". Two architecture files contradict each other. `KeySpec` has three fields. |
| C3 | `supported-subset.md:31-39` lists the service-key registry as 15 keys | `SERVICE_KEYS` has **28**. All 13 resource keys (`mem_limit`, `cpus`, `pids_limit`, `oom_kill_disable`, …) are absent from the list, though the Resource limits section documents them correctly further down. |
| C4 | `supported-subset.md:22-27` "Supported" service keys | Omits **19** supported keys: `configs`, `deploy`, `dns`, `dns_search`, `dns_opt`, `sysctls`, and all 13 resource keys. |
| C5 | `supported-subset.md:13-14` top-level "Ignored (warns): `networks`" | `parsing.py:127` also warns for top-level `volumes`. |
| C6 | `README.md:60-68` "supports an honest subset … `image`/`build`, `command`, `environment`/`env_file`, short-form bind `volumes`, `healthcheck`, `depends_on`, and network `aliases`" | Predates `extends`, secrets, configs, resource limits, and pod-level dns/sysctls — all shipped. Also says "bind `volumes`" when named and anonymous volumes are supported too. |

C1 and C3 are the drift signature of the doc's ~40 inline code citations: the
prose names private identifiers that rename out from under it. Worth keeping the
citations (they are good navigation) but at one-per-section, not one-per-bullet.

## Bucket D — redundancy and token economy

**D1. Secrets and Configs are near-verbatim twins.** 123 lines
(`supported-subset.md:331-454`) for two things the doc itself says are the same:
"mirroring secrets", "the same closure-scoped-creation rule secrets follow",
"`uid`/`gid`/`mode` behave exactly as for secrets", "byte-for-byte the same
teardown parity as secrets". They differ in exactly four ways — store name
prefix, allowed sources, default target, absolute-target requirement — which is
a four-row table. Collapsing to one **Stores** section costs no information.

**D2. Per-key prose restates the code.** ~30 bullets of the form "`X` is a list,
emitted as repeated `--x`" (`cap_add`, `cap_drop`, `security_opt`, `devices`,
`group_add`, `platform`, `user`, `working_dir`, …). This is a table.

**D3. Changelog voice in present-tense docs.** `architecture/README.md` defines
these files as "the living truth about what the system does **now**". Five sites
narrate history instead:

- `supported-subset.md:201` "pre-existing, orthogonal behavior"
- `supported-subset.md:214` "pre-existing behavior, unchanged by this move"
- `supported-subset.md:216` "same as before it was per-service"
- `supported-subset.md:292` "previously a non-mapping healthcheck reached
  `.get()` calls downstream and crashed raw"
- `pod.py:91-98` — an 8-line docstring that is mostly changelog ("as before this
  move", "relocating the flags changes nothing else observable about either
  source")

The *why* belongs in `changes/`; the diff already records the move.

Together D1-D3 take `supported-subset.md` from **537 to roughly 300 lines**
with nothing lost.

**D4. `extends` duplicates the `keys` merge primitives.**
`extends._as_list(key, name, value)` and `keys._as_list(name, key, value)` have
identical bodies and identical error messages, with the **first two parameters
swapped** — a live footgun, correct today only because each call site matches
its own local signature. `extends._as_mapping` likewise re-implements
`keys.pairs_to_mapping` plus a `depends_on` case. `extends.py:9-12` already
flags the duplication and defers it to `decisions/2026-07-12`'s revisit trigger;
the trigger is about a structural *registry*, which this is not — collapsing two
copies of one helper onto the `keys.py` primitive needs no registry.

## Spawned changes

| Lane | Scope | Findings |
|------|-------|----------|
| Full | Close the structural-key gate: shape validators for `environment`, `env_file`, `volumes`; validate `--artifact SRC:DST` in the CLI. Failing test first for each. | A1, A2, A3, A4 |
| Lightweight | Closure-scope the emit-side host set in `emit._plan`. | B1 |
| Full | Rewrite `supported-subset.md` (537 → ~300): merge Stores, tabulate keys, fix drift, strip changelog voice. Fix `glossary.md`, `README.md`, `pod.py` docstring. | C1-C6, D1-D3 |
| Lightweight | Collapse `extends._as_list` / `_as_mapping` onto the `keys.py` primitives. | D4 |

## Non-findings (checked, no action)

- `interval_seconds` correctly refuses `inf`/`nan` (via the guarded parse) and
  compound durations (`"1h30m"`); the `ms`-before-`m` suffix order is right.
- `to_shell` / `variable_names` share one regex, so the emitted script and the
  CLI's variable note cannot disagree.
- A healthcheck `test: []` is refused, but by `health_cmd` at emit rather than at
  the gate. User-visible behavior is still `UnsupportedComposeError`, so this is
  a purity nit, not a bug — left alone.
- `deferred.md`'s "Unify the store render/vars seam" remains correctly deferred:
  no third reader has appeared and no drift bug has surfaced.
