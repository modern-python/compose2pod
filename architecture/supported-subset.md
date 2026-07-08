# Supported compose subset

compose2pod converts an honest subset of Docker Compose and refuses the rest
loudly rather than silently dropping behavior. `validate()`
(`compose2pod/parsing.py`) is the gate: anything it does not recognize either
warns (ignored, behavior-neutral inside a single pod) or raises
`UnsupportedComposeError`.

## Top-level keys

- **Supported:** `services` (required, non-empty), `version`, `name`,
  `networks`.
- **Ignored (warns):** `networks` — all services share the pod's single
  network namespace, so top-level network definitions have no effect.
- **Extension fields:** any key prefixed `x-` is accepted and ignored
  silently, per the Compose spec. This is what lets a document hold shared
  config in a top-level `x-*` block for reuse via YAML anchors.
- Everything else raises.

## Service keys

- **Supported:** `image`, `build`, `command`, `environment`, `env_file`,
  `volumes`, `healthcheck`, `depends_on`, `networks`, `hostname`,
  `container_name`, `tmpfs`. compose2pod never builds: a `build` section is
  accepted but its contents (context, dockerfile, args) are not read —
  `image_for` (`compose2pod/emit.py`) runs the CI image supplied via `--image`
  for any service that has one.
- **`tmpfs`:** a string or list of strings, each `<path>` or
  `<path>:<options>` (e.g. `/tmp:mode=1777`), passed through verbatim as
  `podman run --tmpfs <value>` — Compose's short syntax maps directly onto
  podman's own `--tmpfs CONTAINER-DIR[:OPTIONS]` flag, so no translation is
  needed. No format validation; a malformed option string surfaces as a
  podman error at run time.
- **`hostname` and `container_name`:** both are made resolvable to
  `127.0.0.1` like a network alias (added to the shared `--add-host` set), so
  other services can reach the service by either name. The pod shares the UTS
  namespace, so a service's own hostname is the pod's, and the actual podman
  container is always named `{pod}-{service}` regardless of `container_name`
  (used internally for `podman cp`, healthcheck polling, and target-container
  diagnostics) — only name *resolution* is meaningful to other services, and
  no per-container `--hostname` or renamed `--name` is emitted.
- **Ignored (warns):** `ports`, `restart`, `stdin_open`, `tty` — meaningless
  or irrelevant inside a single shared-namespace pod.
- **Extension fields:** any `x-`-prefixed service key is accepted and ignored
  silently.
- Everything else raises.

## Healthcheck keys

- **Supported:** `test`, `interval`, `timeout`, `retries`, `start_period`.
- **Extension fields:** any `x-`-prefixed healthcheck key is accepted and
  ignored silently.
- Everything else raises.

## Volumes

Short syntax only; the long mapping form raises. A `source:target` entry is
one of two kinds, told apart by whether `source` starts with `.` or `/`:

- **Bind mount** (`source` starts with `.` or `/`): the host path, resolved
  against `--project-dir` when relative.
- **Named volume** (`source` is a bare identifier, e.g. `pgdata:/var/lib/...`):
  passed through verbatim as `-v <name>:<target>` — no format validation, no
  path translation. Podman creates the named volume implicitly on first
  reference (same as plain `podman run -v`), so no explicit `podman volume
  create` step is needed. The volume persists on the host after the pod is
  removed, identical to `docker compose down` without `-v`. The top-level
  `volumes:` block (declaring drivers/options) is accepted but ignored — its
  contents are never read. This assumes a default-driver, non-`external`
  volume; a non-default `driver`/`driver_opts` or `external: true` (which
  Compose treats as "must already exist") has no effect, since podman always
  creates the volume implicitly with default options on first reference.

A single absolute container path with no `source:target` (e.g.
`- /var/cache/models`) is accepted as an **anonymous volume** and emitted
verbatim as `-v <path>` — podman creates an anonymous volume at that path (the
common way to shadow a subdirectory of a bind mount). No host-path translation
is applied, since the entry names a container path, not a host source. A
colon-less entry that is not absolute (e.g. `./cache`) is malformed and raises.

## depends_on

All three conditions are honored: `service_started`, `service_healthy`,
`service_completed_successfully`. A `service_healthy` dependency on a service
with no usable healthcheck raises.

## YAML anchors and merge keys

Anchors (`&name` / `*name`) and the merge key (`<<:`) need no handling in
compose2pod: PyYAML's `safe_load` resolves them at load time, so `validate()`
and `emit` see already-merged service mappings. JSON input has no anchors but
can still carry literal `x-` extension keys, handled identically.
