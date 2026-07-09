---
status: accepted
summary: Reject network_mode/links/expose permanently (invariant-violating) and ipc/uts/domainname/cgroup/userns_mode while the pod keeps default --share; dns and pid are feasible-but-deferred, not permanent rejects.
supersedes: null
superseded_by: null
---

# Reject network- and namespace-mode service keys, but only some permanently

**Decision:** The Compose keys that touch a container's network or namespace
mode are rejected, but for two distinct reasons that must not be conflated:
one permanent, one contingent on the pod model. `dns` and `pid`, previously
grouped with them, are feasible and are *deferred*, not rejected.

## Context

While auditing Compose spec coverage
(`audits/2026-07-09-compose-spec-coverage.md`) the reject bucket was justified
with a single reason — "namespaces shared at pod level — conflict." That reason
is imprecise. `compose2pod` runs every service in one Podman pod that shares
`net`, `uts`, `ipc` (and `cgroup`) by default; `pid` is **not** shared by
default. So "shared namespace" does not uniformly explain the rejects, and the
tool risks treating a feasible key as impossible.

The tool's one load-bearing invariant is the **shared network namespace**:
services talk over `127.0.0.1` and resolve names via per-container `--add-host`.
That invariant is the whole reason the tool exists (bridge-less CI). It is the
correct axis for deciding acceptance.

## Decision & rationale

- **Permanent reject (invariant-violating):** `network_mode`, `links`,
  `external_links`, `expose`. Honoring any of these pulls a container out of the
  shared netns or implies bridge/link semantics — silently breaking localhost
  service discovery. Refusing is the contract, not a limitation.
- **Reject while the pod keeps default `--share` (fights a shared namespace):**
  `ipc`, `uts`, `domainname`, `cgroup`, `userns_mode`. A per-container override
  conflicts with the pod's shared namespace; supporting it means reshaping how
  the pod is created for a need no CI user has raised.
- **Deferred, not rejected (feasible):**
  - `dns` / `dns_search` / `dns_opt` are **pod-wide, not per-container**. Podman
    rejects `--dns` on a container that has joined a pod's netns (invalid when
    the netns is `container:<infra>`), while `--add-host` edits the per-container
    `/etc/hosts` and is allowed — hence the tool already emits add-host per
    container. `dns` is expressible on `podman pod create` and reconciled across
    services. Tracked in `deferred.md`.
  - `pid` is not pod-shared, so `pid: host` / `pid: service:x` map to clean
    per-container `--pid` flags with no pod conflict. Feasible; low demand.

The per-container-`--dns`-invalid-in-pod behavior is documented, not yet
observed on a live podman; validate it before building `dns` support.

## Revisit trigger

- A concrete CI need appears for a per-service network/namespace mode
  (`ipc: host`, a private `pid` namespace, a service-specific resolver), with a
  design that preserves the shared-network invariant — e.g. hoisting `dns` to
  `podman pod create`, or selectively narrowing the pod's `--share`.
- A live podman run contradicts the documented `--dns`-in-pod behavior this
  decision relies on.
