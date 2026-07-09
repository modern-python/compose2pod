---
status: accepted
summary: stop_signal and stop_grace_period are reclassified out of Bucket A as accepted-but-inert, because the generated script force-removes the pod (podman pod rm -f) and never gracefully stops a container.
supersedes: null
superseded_by: null
---

# stop_signal / stop_grace_period are inert under the force teardown

**Decision:** `stop_signal` and `stop_grace_period` are not supported and are
reclassified out of Bucket A (clean per-container flags) into
accepted-but-inert. They map to `podman run --stop-signal` / `--stop-timeout`,
which only take effect during a graceful `podman stop` -- something the
generated script never performs.

## Context

The spec-coverage audit (`audits/2026-07-09-compose-spec-coverage.md`) listed
`stop_signal`/`stop_grace_period` in Bucket A as "clean per-container flag
mappings." Revisiting during the container-confinement bundle
(`changes/2026-07-09.04-container-confinement-service-keys.md`) showed the
teardown model makes them inert.

The generated script (`emit.py` `emit_script`) starts services
(`podman run -d`, `--rm` for completion-gated deps, foreground for the target)
and cleans up with a single `trap 'podman pod rm -f <pod>' EXIT`. There is no
`podman stop` anywhere: the pod is force-removed (SIGKILL), which bypasses the
per-container stop signal and grace period entirely. So emitting
`--stop-signal`/`--stop-timeout` would set container metadata that nothing in
the script's lifecycle ever consults.

## Decision & rationale

- Supporting them would emit flags with **no observable effect** -- against the
  tool's honest-subset principle, which prefers to refuse (or leave
  behavior-neutral constructs ignored) rather than imply behavior it does not
  deliver.
- They join the "accepted-but-inert" category alongside `ports`, `restart`,
  `stdin_open`, `tty` -- valid Compose that is meaningless in this pod's run +
  force-teardown model. (They are not yet wired into the `IGNORED_SERVICE_KEYS`
  warn list; that is a follow-up if a user supplies them.)
- This is the same reasoning shape as the `dns` reclassification
  (`deferred.md`): a key looked like a clean flag until the pod/runtime model
  was examined.

## Revisit trigger

- The generated script gains a graceful-stop phase (an explicit `podman stop`
  with a timeout before `pod rm`, or a per-service stop sequence), at which
  point `--stop-signal`/`--stop-timeout` would become effective and worth
  emitting.
