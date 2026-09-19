# The healthcheck wait budget is not coupled to `retries`

`run_flags` passes a healthcheck's `start_period` and `retries` through as `--health-start-period`
and `--health-retries`, but the emitted script's `wait_healthy` loop keeps its fixed
`HEALTHY_WAIT_BUDGET_SECONDS` rather than deriving `retries × interval`. `retries` counts the
consecutive failures podman tolerates, not the time until the first check is meaningful, so a
budget derived from it can expire before a long `start_period` has elapsed. The fixed 120 s errs
toward slow starters at the cost of a slower failure signal; a service that needs more makes the
budget configurable, not coupled.
