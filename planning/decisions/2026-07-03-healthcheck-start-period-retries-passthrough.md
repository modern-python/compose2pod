---
status: accepted
summary: healthcheck start_period/retries pass through to podman run as flags; the wait_healthy budget is not shortened to retries x interval.
supersedes: null
superseded_by: null
---

# Healthcheck start_period/retries pass-through without shortening the wait budget

**Decision:** `run_flags` passes a service's healthcheck `start_period` and
`retries` through to `podman run` as `--health-start-period` and
`--health-retries` respectively (alongside the existing
`--health-timeout`). The `wait_healthy` polling budget in the emitted
script stays `HEALTHY_WAIT_BUDGET_SECONDS // interval` attempts —
it is **not** shortened to `retries × interval`.

## Context

The chats prototype validated `start_period` and `retries` as recognized
healthcheck keys but silently ignored both when building `podman run`
flags and when computing the `wait_healthy` poll budget — a
review-identified gap (deviation #2 in the extraction design). Two things
needed fixing: recording the author's intent on the container itself, and
deciding whether the emitted script's own wait loop should account for
`retries`/`start_period`.

The options considered for the wait loop:
1. Keep the loop's fixed `HEALTHY_WAIT_BUDGET_SECONDS` budget, independent
   of the podman-level `--health-retries`/`--health-start-period` values.
2. Recompute the loop's attempt count from `retries × interval` (plus
   `start_period`), so the script-level wait tracks the container's own
   healthcheck retry policy more precisely.

## Decision & rationale

- **Pass `start_period`/`retries` to `podman run`** so the container's own
  healthcheck scheduling (as podman understands it) matches what the
  compose file declares — this is the straightforward, low-risk half of
  the fix, and was uncontroversial.
- **Do not shorten the wait budget to `retries × interval`.** `wait_healthy`
  polls `podman healthcheck run <ctr>` directly in a loop and returns as
  soon as the first successful check is observed — it does not wait for the
  full budget on a healthy service. Coupling the budget to `retries ×
  interval` would risk **premature failure** for a service with a long
  `start_period`: `retries` counts consecutive *failures* podman tolerates
  before marking a container unhealthy, not the time before the first
  check is meaningful, and a short `retries × interval` product could expire
  the script's wait loop before `start_period` has even elapsed. The fixed,
  generous `HEALTHY_WAIT_BUDGET_SECONDS` (120s) is a safer default that
  errs toward giving slow-starting services enough time, at the cost of a
  slower failure signal for services that are genuinely broken.

## Revisit trigger

- A real service in the fleet needs a `start_period` long enough that the
  fixed 120s `HEALTHY_WAIT_BUDGET_SECONDS` is insufficient (i.e. the
  container needs more than 120s from first `podman run` to first healthy
  check), which would call for making the budget configurable rather than
  coupling it to `retries × interval`.
- Repeated false-positive "did not become healthy" failures in CI point at
  the fixed budget being too tight for a class of services, prompting a
  reconsideration of how the budget is derived.
