---
status: accepted
summary: sysctls:["a"] and volumes:["a"] stay refused — legitimate rule-two refusals (podman 6.0.1 cannot form the flag), not unfinished parsers.
supersedes: null
superseded_by: null
---

# list-of-str sysctls/volumes entries are legitimate refusals

**Decision:** compose2pod refuses `sysctls: ["a"]` (a list entry with no `=`) and
`volumes: ["a"]` (a colon-less relative entry) even though `docker compose config`
accepts both, because podman cannot form the corresponding flag. These are
legitimate refusals under `2026-07-14-docker-rejection-parity.md` rule two, not
unfinished parsers to be closed later.

## Context

The conformance harness reports both as `over-reject` (Docker accepts, compose2pod
refuses). `deferred.md` had catalogued `volumes: ["a"]` as "worth re-measuring …
legitimate refusal or unfinished form?". Re-measured against `docker compose config`
v5.1.2 and podman 6.0.1:

- **`sysctls: ["a"]`** — Docker normalizes it to `{a: ""}` (the sysctl `a` set to
  the empty string). The equivalent podman flag is refused: `--sysctl a=` →
  `sysctl 'a' is not allowed`; `--sysctl a` → `sysctl values must be in the form
  of KEY=VALUE`. compose2pod accepts the useful `sysctls: ["key=value"]` list form
  (`pod._sysctl_pairs`); only the valueless entry is refused.
- **`volumes: ["a"]`** — Docker normalizes it to an anonymous volume with the
  *relative* target `a`. podman refuses a relative mount target: both `-v a` and
  `--mount type=volume,target=a` → `invalid container path "a", must be an
  absolute path` (an absolute target such as `/a` is accepted). compose2pod's own
  error — "anonymous volume 'a' must be an absolute path" — mirrors podman's
  constraint exactly.

## Decision & rationale

Refuse both, permanently. Rule two accepts a form only when podman can express it
and it means something in a pod; here podman rejects the flag outright, so
accepting the form would emit a script that cannot run. The rejected alternative —
accept at the gate and let podman fail at run time — trades a clean generate-time
refusal for an opaque runtime crash, which the docker-rejection-parity design
explicitly avoids. Neither is an unfinished parser: the useful forms
(`sysctls: ["k=v"]`, absolute anonymous volumes) are already supported; only the
inexpressible edge is refused.

## Revisit trigger

A future podman accepts a relative mount target, or a valueless / bare-`KEY`
sysctl. Then the corresponding form becomes expressible and this decision reopens.
