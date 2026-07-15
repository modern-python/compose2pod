# Docker-rejection parity audit

A sweep of the gate against the rule in
`decisions/2026-07-14-docker-rejection-parity.md`: **a document
`docker compose config` rejects must be rejected here too.**

Unlike the previous audits, this one was **measured, not read**. A generated
matrix crossed every key compose2pod knows about
(`SERVICE_KEYS | STRUCTURAL_KEYS | IGNORED_SERVICE_KEYS`, 56 keys) with 12
hostile shapes (`null`, `""`, `[]`, `{}`, `true`, `3`, `1.5`, `"str"`,
`["a"]`, `[{a: 1}]`, `{a: b}`, `{a: {b: 1}}`), and ran each of the 672
resulting documents through **both** oracles:

- `docker compose config` (v5.1.2) — exit code is Docker's verdict. No daemon
  is required; the check is pure parse-and-validate.
- The real CLI pipeline — `_read_compose` (the YAML-1.2 loader) →
  `resolve_extends` → `validate` → `emit_script`. Not `validate()` alone: the
  CLI runs both, and `emit_script` catches things `validate` does not (an
  unknown `depends_on` target, for one). An earlier pass that probed only
  `validate()` overstated the count by 2.

**Result: 116 probes where Docker rejects and compose2pod accepts. Three are the
host-dependent carve-out below and are correct as they stand, leaving **113 real
violations**. Plus 0 raw crashes, and 2 over-rejections — both already declared,
so both legitimate.**

Findings spawn follow-up changes; they are not themselves changes. The fix is
designed in `changes/2026-07-14.10-docker-rejection-parity.md`.

## The carve-out, found by measuring

Three `env_file` probes are **not** violations and must not be fixed. Docker
rejects `env_file: app.env` with `env file not found: stat: no such file`, a
fact about the *reading host's filesystem*, not about the document. compose2pod
emits a script that runs elsewhere — in CI, where the file is checked out. The
same applies to `${VAR:?msg}`, which `docker compose config` fails on when the
variable is unset in the local shell, and which compose2pod deliberately leaves
live for script-run time.

Docker's verdict binds only on the document's own content. The harness must
either satisfy these preconditions or exclude them, or it will demand checks
that are actively wrong. This is written into the decision.

## Class 1 — ignored keys are never shape-checked (68 violations, 7 keys)

The largest class, and the clearest design error: `IGNORED_SERVICE_KEYS` are
warned about and then **never looked at**. `_validate_service` appends
`ignoring '<key>'` and moves on, so any shape at all is accepted —
`restart: {a: {b: 1}}`, `tty: [{a: 1}]`, `ports: 3`. "Ignored" was conflated
with "unvalidated". A key compose2pod does not *use* is still a key Docker
*validates*, and a document carrying a malformed one is a document Docker will
not run.

| Key | Violating shapes | Parity reachable by |
|---|---|---|
| `ports` | 10 | type check + **port grammar** |
| `stop_grace_period` | 11 | type check + **duration grammar** |
| `stdin_open` | 10 | type check (bool) |
| `tty` | 10 | type check (bool) |
| `restart` | 9 | type check (string) |
| `stop_signal` | 9 | type check (string) |
| `profiles` | 9 | type check (list of strings) |

Five of the seven reach **full** parity on a type check alone, because Docker
does not validate their content either — measured: `restart: somevalue` and
`stop_signal: somevalue` are both *accepted* by `docker compose config`, enum
notwithstanding. Only `ports` and `stop_grace_period` need a value grammar.

## Class 2 — structural keys (20 probes, 17 real violations)

| Key | Shapes | What Docker says |
|---|---|---|
| `build` | 8 | contents never read here, so any shape passes; Docker requires a string or mapping |
| `networks` | 4 | `service "app" refers to undefined network a` — per-service networks are never checked against the top-level block (which compose2pod ignores wholesale) |
| `dns_opt` | 2 | requires a **list**; compose2pod accepts a bare string. A Docker quirk — `dns` and `dns_search` *do* accept a string, and neither violates |
| `image` | 1 | `image: ""` — `has neither an image nor a build context` |
| `container_name` | 1 | `container_name: ""` refused |
| `depends_on` | 1 | `depends_on: ""` — a bare string is neither list nor mapping |
| `env_file` | 3 | **carve-out, not a violation** (see above) |

## Class 3 — resource-limit keys (28 violations, 13 keys)

`is_number` (`keys.py:90`) returns true for *any* `str`, so every
`_number_scalar` key accepts every string. Docker validates a real grammar:

- **Size** (`invalid size: ''`): `mem_limit`, `memswap_limit`,
  `mem_reservation`, `mem_swappiness`, `shm_size`.
- **Number** (`failed to cast to expected type`): `cpus`, `cpu_shares`,
  `cpu_quota`, `cpu_period`, `pids_limit`, `oom_score_adj`.
- **String-only**: `cpuset` — an `int`/`float` is refused (2 violations).
- **Int, not float**: `mem_reservation`, `mem_swappiness`, `oom_score_adj`.
- `ulimits: {a: b}` — a bound must cast to an int; compose2pod's
  `_validate_ulimits` accepts `int | str`.

This is the class most likely to reach a real user: `mem_limit: 512` (bare int,
no unit) is an easy mistake, and today it converts silently.

**The nuance that constrains the fix:** a value carrying a `${VAR}` reference
must stay unvalidated and pass through live — deferring interpolation to
script-run time is deliberate design, not an oversight. A size/number grammar
that rejected `mem_limit: ${MEM}` would break it. Docker never sees this case
because it interpolates first.

## Class 4 — documentation contradicting the code (3 findings)

Each is a claim in the tree that the code refutes.

**D1 — `architecture/supported-subset.md` contradicts itself on
`entrypoint: null`.** Line 98 correctly says a null raises "for every service
key except `command`, `entrypoint` and `deploy`", matching
`NULL_ALLOWED_KEYS` (`parsing.py:19`). Lines 198–202 then say the opposite:
"`command: null` is accepted … *unlike* `entrypoint: null`" and
"`entrypoint: null` raises rather than being treated as absent." `2026-07-14.07`
changed the behavior and updated the summary paragraph but missed the
structural-keys section. The code accepts it; the page says both.

**D2 — `parsing.py:274` states a divergence that does not exist.**
`_require_string_keys_deep`'s docstring: "Rejecting a non-string key is a
deliberate divergence from Docker for map-typed *keys* specifically (Docker
accepts `environment: {3306: db}`)." Docker does **not**. Measured:

```
$ docker compose config     # environment: {3306: db}
non-string key in services.app.environment: 3306
```

`2026-07-14.09` established this and corrected `supported-subset.md` (which now
says "Rejecting a non-string key **matches Docker**"), but left the docstring
carrying the refuted claim — so the two now disagree, and the docstring is the
one a reader of the gate meets first.

**D3 — `supported-subset.md:83` names a function that does not exist.**
`extra_host_pairs`; the function is `keys.extra_host_entries`. Renamed in
`2026-07-14.06`.

## Not violations

- **`sysctls: ["a"]`** — Docker accepts a bare list entry; compose2pod requires
  `key=value`. Declared in `supported-subset.md` (Pod-level options).
- **`volumes: ["a"]`** — Docker accepts a colon-less relative entry; compose2pod
  requires an anonymous volume to be an absolute path. Declared
  (`supported-subset.md`, Volumes).

Both are over-rejections, which the soft rule permits *because* they are
declared. Neither needs a change.

## Coverage gaps in the sweep

Stated so the numbers are not read as more than they are. The matrix probes
**one key at a time on a single service**. It does not reach:

- cross-key and cross-service invalidity — `extends` cycles, `depends_on`
  conditions, `service_healthy` against a service with no healthcheck,
  conflicting sysctls/hosts across a closure;
- nested positions — `healthcheck.*`, `deploy.resources.*`, per-service
  `secrets`/`configs` entries, top-level store definitions;
- top-level keys.

The hand-authored `corpus/*.yaml` half of the harness
(`changes/2026-07-14.10`) exists to cover exactly these. **The 116 is a floor,
not a total.**
