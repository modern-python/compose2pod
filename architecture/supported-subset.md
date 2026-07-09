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

- **Supported:** `image`, `build`, `command`, `entrypoint`, `environment`,
  `env_file`, `volumes`, `healthcheck`, `depends_on`, `networks`, `hostname`,
  `container_name`, `tmpfs`, `user`, `working_dir`, `group_add`, `labels`,
  `read_only`, `init`, `privileged`, `cap_add`, `cap_drop`, `security_opt`,
  `platform`, `devices`, `annotations`, `extra_hosts`, `pull_policy`, `ulimits`.
  compose2pod never builds: a `build` section is accepted but its contents
  (context, dockerfile, args) are not read — `image_for` (`compose2pod/emit.py`)
  runs the CI image supplied via `--image` for any service that has one.
- **`environment`:** list form (`- KEY=value`, `- KEY`) or mapping form
  (`KEY: value`, `KEY:`). A null mapping value (`KEY:`) means "pass `KEY`
  through from the host", emitted as a bare `-e KEY` exactly like the list
  form `- KEY`.
- **`entrypoint`:** string or list. List form is exec form; string form is
  shell form (`/bin/sh -c <string>`), mirroring `command`. Emitted as
  `--entrypoint <first-token>` with the remaining tokens placed ahead of the
  command after the image, so `podman run --entrypoint a IMAGE b <command>`
  runs `a b <command>` -- no JSON needed. A string (shell-form) entrypoint
  ignores the service `command`, matching Docker; `validate()` warns when both
  are set. The target's `--command` override still applies as explicit intent,
  but when the target has a string entrypoint, the override tokens land after
  `-c <entrypoint-string>` and are passed positionally to `sh` as `$0`/`$1`...
  rather than executed -- the same Docker shell-form `ENTRYPOINT` semantic, not
  a compose2pod-specific limitation. Use a list (exec-form) entrypoint, or none,
  if the override needs to actually run.
- **`user` / `working_dir`:** strings, emitted verbatim as `--user` / `--workdir`.
- **`group_add`:** a list, emitted as repeated `--group-add`.
- **`read_only` / `init` / `privileged`:** booleans, emitted as the bare
  `--read-only` / `--init` / `--privileged` flag only when true (nothing when
  false or absent).
- **`cap_add` / `cap_drop` / `security_opt`:** lists, emitted as repeated
  `--cap-add` / `--cap-drop` / `--security-opt`. Item contents pass through
  verbatim (no content validation), like `tmpfs` and named volumes.
- **`platform`:** a string, emitted verbatim as `--platform`.
- **`devices`:** a list, emitted as repeated `--device` (contents verbatim).
- **`annotations`:** list or mapping, emitted as repeated `--annotation`
  (`KEY=value`, or bare `KEY` for a null value), sharing the `_MAP_FLAGS`
  machinery with `labels`.
- **`extra_hosts`:** list (`- host:ip`) or mapping (`host: ip`), emitted as
  per-service `--add-host host:ip`. Distinct from the alias/hostname entries
  (which resolve to `127.0.0.1`); IPv6 values keep their colons.
- **`pull_policy`:** a validated enum mapped to podman's `--pull`
  (`if_not_present` → `missing`; `always`/`never`/`missing` pass through),
  emitted literally. `build` and unknown values are rejected — compose2pod
  never builds, so a `build` pull policy cannot be honored.
- **`ulimits`:** a mapping of limit name to either a scalar (`nproc: 65535` →
  `--ulimit nproc=65535`, podman sets soft = hard) or a `{soft, hard}` mapping
  (`nofile: {soft, hard}` → `--ulimit nofile=soft:hard`). A mapping value must
  have exactly `soft` and `hard`; other shapes are rejected. (`sysctls`, by
  contrast, is refused — it is pod-level, not per-container; see
  `planning/decisions/2026-07-09-sysctls-pod-level.md`.)
- **`labels`:** list (`- KEY=value` / `- KEY`) or mapping (`KEY: value` / `KEY:`),
  emitted as repeated `--label`. A null value means an empty label
  (`--label KEY`) -- the same emitted shape as `environment`'s null but a
  distinct meaning (labels have no host-passthrough).
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
- **Ignored (warns):** `ports`, `restart`, `stdin_open`, `tty`, `stop_signal`,
  `stop_grace_period` — meaningless or irrelevant inside a single
  shared-namespace pod. `stop_signal`/`stop_grace_period` are inert because the
  script force-removes the pod (`podman pod rm -f`) and never gracefully stops a
  container.
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

## Variable interpolation

compose2pod does not resolve Compose Spec `${VAR}` references at generation
time. `to_shell()` (`compose2pod/shell.py`) instead re-encodes every
compose-derived string leaf (`environment`, `image`, `command`, `volumes`,
`tmpfs`, `env_file`, healthcheck `test`) into a double-quoted POSIX-shell
fragment with the variable references left live, so the generated script's
own shell expands them against its runtime environment when the script
runs. Only those field values pass through `to_shell()`; other document
fields (service and network names, ports, and the like) are never
interpolated. Supported forms: `$VAR`, `${VAR}`,
`${VAR:-default}`, `${VAR-default}`, `${VAR:?msg}`, `${VAR?msg}`,
`${VAR:+alt}`, `${VAR+alt}`, and `$$` for a literal `$`. The operator forms
map onto identical POSIX `sh` parameter expansion; bare `$VAR`/`${VAR}` is
emitted as `${VAR-}` so an unset variable expands to empty under the
script's `set -eu` (matching Compose semantics) instead of aborting on
`nounset`. `${VAR:?msg}`/`${VAR?msg}` fails the script at run time — with
`msg` — if the variable is unset or empty; there is no generation-time
check. A braced reference whose text after the name is not one of these
operators (e.g. `${FOO!bar}`) is malformed and raises
`UnsupportedComposeError` rather than silently dropping the trailing text.
Tool/CLI-supplied values (`--project-dir`, `--image`, the pod name,
the `--command` override) are literal and never interpolated. The CLI
prints one informational stderr note listing the variable names the
generated script actually expands at run time — `referenced_variables()`
(`compose2pod/emit.py`) collects these from the same tokens `to_shell()`
renders, so a `${VAR}` sitting in a literally-emitted field (or in the
`--command` override) never appears in the note. There is no `.env` file
support — only the environment present when the generated script runs is
consulted; export the values first (`set -a; . .env; set +a`) if a project
relies on a `.env` file.

## depends_on

All three conditions are honored: `service_started`, `service_healthy`,
`service_completed_successfully`. A `service_healthy` dependency on a service
with no usable healthcheck raises.

## YAML anchors and merge keys

Anchors (`&name` / `*name`) and the merge key (`<<:`) need no handling in
compose2pod: PyYAML's `safe_load` resolves them at load time, so `validate()`
and `emit` see already-merged service mappings. JSON input has no anchors but
can still carry literal `x-` extension keys, handled identically.
