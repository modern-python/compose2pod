---
status: accepted
summary: A negative native number on a top-level size/number/integer key (mem_limit, cpus, cpu_shares, oom_score_adj, ulimits, ...) is accepted — docker compose config accepts it and defers the negative to runtime, so compose2pod matches; only the mount sub-schema fields (tmpfs.size/mode) are config-unsigned and refuse a negative.
---

# Negative numeric values are config-accepted and deferred to runtime

**Decision:** compose2pod does **not** refuse a negative native number on the
top-level numeric keys. `docker compose config` v5.1.2 accepts one there and
defers the negative to run time; compose2pod matches, per the config-level parity
rule. Only the volume mount sub-schema (`tmpfs.size`/`tmpfs.mode`) is
config-validated as unsigned and refuses a negative — closed in `2026-07-17.02`.

## Context

The final review of `2026-07-17.02` (nested volume options) found that
`tmpfs.size: -5` / `tmpfs.mode: -1` were a false green (docker rejects the
unsigned mount fields; compose2pod accepted) and suggested the same might hold
for every `values.validate_size` caller. Measured against `docker compose config`
v5.1.2:

- **Top-level keys ACCEPT a negative native** — `mem_limit: -5`, `cpus: -1.5`,
  `cpu_shares: -5`, `cpu_quota`/`cpu_period`/`pids_limit: -5`, `oom_score_adj:
  -5`, `ulimits.nofile: -5`, `mem_reservation`/`memswap_limit`/`mem_swappiness`/
  `shm_size: -5` — all accepted. compose2pod accepts them too → both-accept, no
  violation. (`memswap_limit: -1` and `oom_score_adj` are even legitimately
  signed.)
- **The tmpfs mount sub-schema REJECTS a negative** — `{type: tmpfs, tmpfs:
  {size: -5}}` / `{mode: -1}` raise (`overflows uint`). This is a *config-level*
  refusal, so accepting it was a genuine hard-rule false green — fixed in
  `2026-07-17.02`.

## Decision & rationale

Leave the top-level keys as-is. Refusing a negative there would **introduce**
over-rejections: compose2pod would reject a document `docker compose config`
accepts, diverging from Docker for a purely run-time concern the project already
defers (like env-file existence or a `${VAR}`'s host value). The hard rule
`accepted(compose2pod) ⊆ accepted(docker)`
(`decisions/2026-07-14-docker-rejection-parity.md`) is already satisfied — a
negative native is inside Docker's config-accept set. The rejected alternative — a
rule-two "refuse anything podman can't run" stance for negatives — is inconsistent
with the config-parity + defer-runtime line the whole subset draws, and would
special-case negatives among the many run-time-invalid-but-config-valid values.

## Revisit trigger

`docker compose config` starts rejecting a negative on a top-level numeric key
(i.e. the negative becomes a config-level, not run-time, error) — then matching it
would be a parity fix, not an over-rejection.
