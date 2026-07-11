---
status: accepted
summary: sysctls is reclassified out of Bucket A as pod-level (net/ipc-namespaced sysctls belong on podman pod create, not a per-container run); it stays refused and is deferred alongside dns.
supersedes: null
superseded_by: null
---

# sysctls is pod-level, not per-container

**Decision:** `sysctls` is not supported as a per-container flag and is
reclassified out of Bucket A. The only sysctls podman lets a container set are
namespaced to the network or IPC namespace, and a Podman pod owns both, so
sysctls belong on `podman pod create --sysctl` (pod-wide) -- the same shape as
`dns`. It stays refused (raises), and pod-level support is deferred alongside
`dns`.

## Context

The spec-coverage audit (`audits/2026-07-09-compose-spec-coverage.md`) listed
`sysctls` in Bucket A as a per-container `--sysctl` flag, with a note that
`net.*` are pod-level. Revisiting during the `ulimits` bundle
(`changes/2026-07-09.07-ulimits-service-key.md`), the podman docs show the note
understated it:

- For the **network** namespace, only `net.*` sysctls are allowed, and only if
  that namespace is owned by the container.
- For the **IPC** namespace, only a fixed set (`kernel.msgmax`, `kernel.sem`,
  `kernel.shm*`, `fs.mqueue.*`, ...) is allowed, and only if owned.
- Non-namespaced sysctls cannot be set in a container at all.

A Podman pod shares net + ipc by default, so a container joining the pod owns
neither namespace. Every settable sysctl is therefore pod-level in this model;
a per-container `podman run --sysctl` would be rejected or wrong.

## Decision & rationale

- **Not per-container.** Emitting `--sysctl` on `podman run` cannot honor the
  request in a shared-namespace pod. The honest home is
  `podman pod create --sysctl`, unioned/conflict-checked across services -- the
  same pod-level-aggregation design `dns` needs (`deferred.md`).
- **Refuse, do not ignore.** Unlike `stop_signal`/`stop_grace_period` (inert, so
  warn-and-ignore), a `sysctls` request is *not* behavior-neutral -- silently
  dropping it would lose behavior the user asked for. So `sysctls` keeps raising
  as unsupported, matching `dns`.
- The behavior is documented, not observed on a live podman in a pod; validate
  before building pod-level support.

## Revisit trigger

- The pod-level-aggregation pattern is built (for `dns` or otherwise), giving
  `sysctls` a `podman pod create --sysctl` home; or a live podman run
  contradicts the documented per-container-`--sysctl`-in-pod behavior.

## Resolved

The revisit trigger was met: `changes/2026-07-11.03-pod-dns-sysctls.md` built
the pod-level-aggregation pattern, so `sysctls` (and `dns`/`dns_search`/
`dns_opt`) are now supported via `podman pod create --sysctl`/`--dns`, unioned
and conflict-checked across the target's closure. This decision's reasoning
(sysctls is pod-level, not per-container) was realized, not reversed, so its
status stays `accepted` as the record of why the pod-level home was chosen.
