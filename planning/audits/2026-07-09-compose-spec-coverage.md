# Compose spec coverage audit

A sweep of every Compose service-level key against what `compose2pod` supports
now, bucketed by fit with the tool's mission: **one Podman pod, one shared
network namespace, CI/test, no bridge, no systemd**. Findings here spawn
follow-up change files; they are not themselves changes.

The gate is `validate()` (`compose2pod/parsing.py`); the accept/ignore/reject
truth is `architecture/supported-subset.md`. This audit is forward-looking (what
we *could* add and why), not current truth.

## Guiding principle

The tool's single load-bearing invariant is the **shared network namespace**:
services reach each other over `127.0.0.1`, names resolve via per-container
`--add-host`. A key is honest to support only if honoring it preserves that
invariant. This gives a clean line:

- **Invariant-preserving + per-container** → a direct `podman run` flag. Cheap.
- **Invariant-preserving + pod-wide** → hoist to `podman pod create`, reconcile
  across services. Real design.
- **Invariant-violating** → reject permanently. Refusing is the contract, not a gap.

## Supported today (11 + healthcheck)

`image`, `build` (accepted, contents unread), `command`, `environment`,
`env_file` (string/list form only), `volumes` (short), `healthcheck`,
`depends_on`, `networks` (aliases), `hostname`, `container_name`, `tmpfs`.
Ignored with a warning: `ports`, `restart`, `stdin_open`, `tty`, and top-level
`networks` / `volumes`.

## Bucket A — clean per-container flags (high value, low risk)

Each appends directly in `run_flags()` as a `podman run` flag; none touches the
network namespace, so none conflicts with the pod.

| Key | podman flag | Note |
|-----|-------------|------|
| `entrypoint` | `--entrypoint` | The glaring omission; companion to `command` |
| `user` | `--user` | Direct |
| `working_dir` | `--workdir` | Direct |
| `labels` | `--label` | list or map form |
| `extra_hosts` | `--add-host` | Merges into the existing add-host set |
| `init` | `--init` | boolean |
| `read_only` | `--read-only` | boolean |
| `stop_signal` | `--stop-signal` | Direct |
| `stop_grace_period` | `--stop-timeout` | duration parse |
| `cap_add` / `cap_drop` | `--cap-add` / `--cap-drop` | lists |
| `privileged` | `--privileged` | boolean |
| `security_opt` | `--security-opt` | list |
| `group_add` | `--group-add` | list |
| `devices` | `--device` | list |
| `pull_policy` | `--pull` | Direct |
| `platform` | `--platform` | Direct |
| `ulimits` | `--ulimit` | soft/hard map form |
| `annotations` | `--annotation` | Direct |
| `sysctls` | `--sysctl` | net-namespace sysctls are pod-level; non-net ones are per-container |

## Bucket B — meaningful, needs translation or pod-wide reconciliation

- **Resource limits.** Modern `deploy.resources.limits/reservations` plus legacy
  `mem_limit` / `cpus` / `pids_limit` / `shm_size` / `oom_*` map onto
  `--memory` / `--cpus` / `--pids-limit` / `--shm-size`. Design work is the
  legacy-vs-`deploy` precedence and which of the ~20 cpu/mem knobs to honor.
- **`secrets` (+ top-level `secrets`).** `file:` source → read-only bind at
  `/run/secrets/<name>`; `environment:` source → env var. High CI value.
- **`configs` (+ top-level `configs`).** Same shape as secrets, mounted at a
  declared target path.
- **`profiles`.** Gate services on a new `--profile` CLI flag; filter the
  startup graph. Good CI fit (enable a seed/test profile).
- **`extends`.** Merge a service from the same or another file. Powerful but
  changes the input model to multi-file — the largest design here.
- **`dns` / `dns_search` / `dns_opt`.** Pod-wide, not per-container (see the
  namespace split below). Hoist to `podman pod create` and reconcile across
  services. Low demand; deferred (see `deferred.md`).
- **`pid`.** Not pod-shared by default, so `pid: host` → `--pid host` and
  `pid: service:x` → `--pid container:<pod>-<x>` are clean per-container flags.
  Near-zero CI demand; deferred rather than built.
- **Lifecycle hooks `post_start` / `pre_stop`.** No direct podman-run
  equivalent; would emit as extra script steps. Design-heavy, low demand.

## Bucket C — reject, split three ways

The reject rationale is not one thing. It splits into a permanent line and a
model-dependent line; see the decision
`decisions/2026-07-09-reject-namespace-network-keys.md`.

**C1 — invariant-violating (permanent reject).** Honoring these breaks the
shared-network invariant the tool exists to provide.
`network_mode`, `links`, `external_links`, `expose`.

**C2 — fights a pod-shared namespace (reject unless the pod model changes).**
A Podman pod shares `net`, `uts`, `ipc` (and `cgroup`) by default. A
per-container override of these conflicts with the pod's shared namespace and
would require reshaping `--share`.
`ipc`, `uts`, `domainname`, `cgroup`, `userns_mode`.

**C3 — niche / platform / legacy (reject; no CI demand).**
`volumes_from`, `scale`, `deploy` swarm keys (replicas/placement/update_config/
restart_policy), `develop`/`watch`, `attach`, `logging`, `blkio_config`,
`device_cgroup_rules`, `isolation`, `credential_spec`, `storage_opt`,
`cpu_count`/`cpu_percent` (Windows), `mac_address` (deprecated), `gpus` (niche),
`runtime`, `cgroup_parent`.

## Correction folded in

An earlier read lumped `dns`, `pid`, and `ipc` together under "namespaces shared
at pod level." That was imprecise on two counts, both now reflected above:

1. **`pid` is not pod-shared by default** — it is a clean per-container flag, not
   a conflict. Moved to Bucket B (feasible, deferred), not C.
2. **`dns` is pod-wide, not per-container, but not hostile.** Podman rejects
   `--dns` on a container that has joined a pod's netns (the netns is
   `container:<infra>`, and `--dns` is invalid there), whereas `--add-host`
   edits the per-container `/etc/hosts` file and is allowed — which is why the
   tool already emits add-host per container. `dns` is therefore expressible on
   `podman pod create`, pod-wide. Moved to Bucket B, deferred.

   Caveat: the per-container-`--dns`-invalid-in-pod behavior came from podman
   docs, not a live run. Validate against a real podman before building `dns`.

## Suggested next steps

1. Ship Bucket A as the near-term pipeline, led by `entrypoint` (its own change).
2. Keep C1/C2 as honest rejects; sharpen `architecture/supported-subset.md`'s
   reject rationale to distinguish "invariant-violating" from "feasible-but-deferred."
3. Revisit `dns`, `secrets`, `configs`, `profiles` per their `deferred.md` triggers.
