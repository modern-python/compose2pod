# Supported compose subset

compose2pod converts an honest subset of Docker Compose and refuses the rest
loudly rather than silently dropping behavior. `validate()`
(`compose2pod/parsing.py`) is the gate: anything it does not recognize either
warns (ignored, behavior-neutral inside a single pod) or raises
`UnsupportedComposeError`. The document itself must be a mapping; any other
top-level shape raises immediately, before any other check runs.

`emit_script()` and `referenced_variables()` (both exported from
`compose2pod`) project the same internal `_plan` traversal, which calls
`validate()` itself before reading anything else out of `compose`, and
discards the returned warnings (the CLI surfaces its own copy from its own
`validate()` call). A library caller therefore cannot reach either public
entry point with a document `validate()` would reject — by construction of
the shared `_plan` call site, not by convention.

## Docker-rejection parity

The gate's governing rule
(`planning/decisions/2026-07-14-docker-rejection-parity.md`) is two-way, one
direction hard: a document `docker compose config` rejects, compose2pod
rejects too, no exceptions — accepting it would emit a script for a file
already broken upstream, a false-green CI run. A document Docker accepts,
compose2pod accepts whenever podman can express it inside a shared-namespace
pod; where it cannot yet, that is a tracked limitation, not a design
position or a licence to refuse on taste — recorded in `planning/deferred.md`
with a revisit trigger, not silently. Either way the rule binds only on what
the *document* says: a rejection that is really a fact about the reading
host (`env_file: app.env` when the file is missing locally, `${VAR:?msg}`
when the shell hasn't exported it) does not bind, because the generated
script runs somewhere else, later, where that precondition may hold.

`tests/conformance/` enforces the hard direction mechanically rather than by
a hand-maintained table: it runs both `docker compose config` and the real
`read → resolve_extends → validate → emit_script` pipeline over a
matrix generated from every key in `SERVICE_KEYS | STRUCTURAL_KEYS |
IGNORED_SERVICE_KEYS` crossed with a set of hostile shapes, plus a
hand-authored corpus (`tests/conformance/corpus/`) for what a single-key
matrix can't reach — cross-key and cross-service invalidity, nested
positions, top-level keys. A key added to the registry is probed the moment
it exists; the rule cannot decay silently as the subset grows the way five
consecutive hand-found divergences once did.

Every regex-anchored value grammar in `values.py` (size, duration,
strict-integer-string, port) rejects surrounding whitespace and a trailing
newline, matching Docker — so a block-scalar value like `mem_limit: |` (which
YAML resolves to `"512m\n"`) is refused, not silently accepted.

## Top-level keys

- **Supported:** `services` (required — a non-mapping value raises, an empty
  mapping raises "no services defined"), `version`, `name` (both must be
  strings when present — a number, a bool, or a bare `name:`/`version:`
  (null) all raise "must be a string", matching Docker's own verdict exactly;
  `_validate_top_level_scalar_strings`, `parsing.py`, Task 14), `networks`,
  `volumes`, `secrets`, `configs` (see Stores, below).
- **Ignored (warns):** `networks` — every service shares the pod's single
  network namespace, so top-level network definitions have no *effect*.
  Their *shape* is still fully validated, though (`_validate_network_definitions`,
  `parsing.py`): the block itself must be a mapping when present, as Docker
  requires ("networks must be a mapping") — a list crashed raw (`TypeError:
  unhashable type`) before that outer check existed, when a list entry was
  itself a dict (see Per-service `networks`, below, for the matching
  per-service hazard) — and, since Task 12 (2026-07-15), each individual
  network *definition* is checked against Docker's own schema too: exactly
  nine keys (`driver`, `driver_opts`, `external`, `name`, `labels`, `ipam`,
  `internal`, `attachable`, `enable_ipv6`), an unknown key or a wrong-typed
  value raises, and `ipam` is validated two levels deep (`driver`, a `config`
  list of per-subnet `{subnet, ip_range, gateway, aux_addresses}` mappings,
  and an `options` mapping of strings). None of this changes what
  compose2pod *does* with the block — it is still never read for its effect
  — only what it refuses. `volumes` — podman creates named volumes
  implicitly on first reference, so the top-level block's drivers/options
  are never read for effect either (see Volumes, below), but since Task 12
  its shape is validated the same way: the block itself must be a mapping
  (previously unchecked entirely), and each definition against a narrower
  five-key schema (`driver`, `driver_opts`, `external`, `name`, `labels` —
  no `ipam`/`internal`/`attachable`/`enable_ipv6`, measured absent from a
  volume definition and refused there as unknown keys).
- **Extension fields:** any key prefixed `x-` is accepted and ignored
  silently, per the Compose spec — including a top-level `x-*` block used to
  hold shared config reused via YAML anchors.
- Everything else raises.

**Every mapping key must be a string**, in every region `validate()` actually
reads from or emits into. PyYAML routinely produces a non-string key — a bare
`3:` parses as an int, and under YAML 1.1 a bare `on:`/`off:`/`yes:`/`no:`
parses as a bool. `validate()` sweeps these regions once, recursively, before
any other check (`_sweep_document`/`_sweep_service`/`_require_string_keys_deep`,
built on `require_string_keys`, `compose2pod/keys.py`) and rejects a
non-string key wherever found, naming the offending key and its location.

**Swept:** the document's own keys; the `services` mapping's own keys
(service *names*) — always, regardless of what a name looks like, since
`validate()` treats every entry in `services` as a real service with no `x-`
filter (a service literally named `x-web` is swept like any other service,
not skipped as an extension field); each service's body — every structural
key, `healthcheck`, `deploy` and its nested resources, per-service
`secrets`/`configs` references, and so on — except `build`'s own contents
and the service's own `x-`-prefixed keys; each top-level `secrets`/`configs`/
`networks`/`volumes` definition, swept by name for the same reason (the last
two joined this list in Task 12, 2026-07-15, once their contents gained a
validated schema — see Top-level keys, above). Three service keys key their
mapping form by another entity's *identifier* rather than by content —
`depends_on` (dependency name), `networks` (network name), `ulimits` (limit
category name) — and get the same name-not-content treatment
(`_sweep_identifier_map`): the identifier is checked regardless of what it
looks like (`x-dep` is a real identifier, not an extension field), and only
its *value* is swept with the ordinary `x-`-skipping walk.

**Skipped**, because compose2pod never emits from these regions, so a
non-string key inside one can never reach the generated script: `x-` blocks
(top-level and per-service — user payload by design, though the `x-` key
itself is still checked); `build`'s own contents (never emitted — see
`build`, below, for the narrower value-*type* check that covers a
mapping-shaped build key's own keys through a different path than this
sweep).

Rejecting a non-string key **matches Docker**, which refuses one too
(`non-string key in services.app.environment: 3306`). So `environment:
{3306: db}` and `{true: x}` are refused by both.

What Docker does *not* see is a bare `on:` / `off:` / `yes:` / `no:` as a
non-string key, because it parses **YAML 1.2**, where each of those is an
ordinary string. PyYAML implements YAML **1.1**, where each is a *boolean* —
so `read()` loads YAML with a 1.2-style boolean resolver (`_build_yaml_loader`,
`compose2pod/read.py`), and only `true`/`false` resolve as booleans. Without it,
`environment: {on: 1}` would arrive as the key `True` and be refused — a file
Docker runs — and the *value* `SSL: on` would reach the container as `SSL=true`
rather than `SSL=on`. The spelling cannot be recovered downstream: once the
loader has resolved `on` to `True`, `"on"` is gone.

A genuine boolean *value* still renders lowercase (`DEBUG: true` → `DEBUG=true`,
see `_render_scalar`), which is what Docker renders too.

The same YAML-1.1-vs-1.2 gap exists for **floats**, and `_build_yaml_loader`
fixes it the same way. Docker's YAML 1.2 parser makes a value a float from an
exponent alone — no dot required — so a bare `1e3` is `1000.0` to Docker.
PyYAML's YAML 1.1 float grammar requires a dot unconditionally, so PyYAML
leaves a bare `1e3` the plain string `"1e3"`. That is not cosmetic: `cpuset`
must be a string, so `cpuset: 1e3` is *accepted* here (compose2pod sees a
string) while Docker *rejects* it (Docker sees the float 1000.0) — a false
green. The same gap over-rejects the reverse case on `oom_score_adj`,
`cpu_shares`, `cpu_quota`, `cpu_period` and `pids_limit`, whose validators
accept a whole-valued float natively but reject the same value as a strict
integer *string* — Docker accepts `oom_score_adj: 1e3` (float 1000.0, cast
leniently) while PyYAML's loader was handing compose2pod the string `"1e3"`,
which the strict-integer-string grammar refuses. The loader's float resolver
matches only the YAML 1.2 core-schema grammar (a dotted mantissa with an
optional exponent, or an undotted mantissa with a mandatory exponent, or
`.inf`/`.nan`); a bare integer like `123` has neither a dot nor an exponent
and still resolves as `int`, and a *quoted* `"1e3"` still resolves as `str`
for the same reason a quoted `"on"` does — quoting bypasses implicit
resolution entirely.

The rejection runs up front, not key by key, because some downstream
readers crash raw on a non-string key (`sorted()`, `str.startswith`, the
secret/config name regex) while others silently f-string it into a flag
value, leaking a Python repr into the script (`key_value_pairs`,
`extra_host_entries`, `_ulimit_args`, `_sysctl_pairs`) — corruption, not a
crash. A handful of entry points reached directly, not only through
`validate()`, keep their own `require_string_keys` call as boundary defense
(`_validate_service`/`_validate_service_healthcheck` in `parsing.py`,
`validate_deploy` in `resources.py`, `stores.py`'s own three checks) —
redundant only when reached through `validate()`.

## Service keys

Each entry in `services` must itself be a mapping; any other shape (a bare
string, a list, ...) raises.

**An explicitly-null value raises**, for every service key except `command`,
`entrypoint` and `deploy` — the three where Compose gives a null a meaning
("not specified") and `docker compose config` accepts one. A bare
`environment:` with its contents deleted is a mistake, not an instruction to
emit nothing, so it is refused rather than silently dropped
(`_reject_null_values`). One rule, taken from Docker's own verdict key by key,
rather than a per-key decision to keep in sync. `x-` extension keys are exempt:
their contents are arbitrary user payload compose2pod never reads.

The same rule holds **wherever a null can appear** — the healthcheck sub-keys,
`deploy.resources` and its `limits`/`reservations`, and the top-level
`networks:`/`volumes:`/`secrets:`/`configs:` blocks. compose2pod is a drop-in
replacement for `docker compose` on rootless runners: the file it converts is
the file the developer runs locally. So a document `docker compose` will not run
must not pass green here — accepting a null it refuses would emit a script for a
file that is already broken upstream, turning a hard error into a false green.
Parity on *refusal* is what the drop-in role demands; it is not parity for its
own sake, and the package keeps its documented divergences elsewhere. A null
*inside* a value is a different thing and stays accepted, because Docker accepts
it too: `environment: {KEY: null}` is host-passthrough, `labels: {KEY: null}` an
empty label.

- **Supported:** `image`, `build`, `command`, `entrypoint`, `environment`,
  `env_file`, `volumes`, `healthcheck`, `depends_on`, `networks`, `hostname`,
  `container_name`, `tmpfs`, `secrets`, `configs`, plus the declarative
  registry and resource-limit keys below. compose2pod never builds: a
  `build` section is accepted, and each known key's shape and value is
  checked against Docker's own grammar for it (see `build`, below), but none
  of it is ever *used* — `image_for` (`compose2pod/emit.py`) runs the CI
  image supplied via `--image` for any service that has one.
- **Ignored (warns):** `ports`, `restart`, `stdin_open`, `tty`,
  `stop_signal`, `stop_grace_period`, `profiles` — meaningless or irrelevant
  inside a single shared-namespace pod. Ignoring one at *emit* does not mean
  leaving it unchecked at the *gate*: each is still shape-validated against
  the same grammar Docker itself validates it against
  (`IGNORED_SERVICE_KEYS`, `compose2pod/parsing.py`) — `restart`/
  `stop_signal` a string, `stdin_open`/`tty` a boolean, `stop_grace_period`
  a duration with a unit (`values.validate_duration`), `profiles` a list of
  strings, and `ports` the fuller port-mapping grammar
  (`values.validate_ports`): a long-form (mapping) entry must carry a
  `target` key — Docker refuses to omit one ("is missing a target port",
  measured against `docker compose config` v5.1.2) — and, since Task 10, every
  other known field's *value* is checked too, even though compose2pod never
  reads `ports` at all: `target` a non-negative integer (native, a
  whole-valued float, or a strict integer string — no digit-grouping
  underscore, no surrounding whitespace, matching Go's `strconv.Atoi` exactly
  rather than Python's lenient `int()`; no upper bound, unlike the short-form
  container port's 1-65535); `published` an integer or a string with content
  entirely unchecked (Docker's own field is a bare string, so `published:
  abc` and a range string both pass; only `true`/a float fail the type
  union); `host_ip` a bare IP address (v4 or v6, unbracketed — checked with
  stdlib `ipaddress.ip_address`, matching Docker's own "invalid ip address"
  refusal of a hostname or a bracketed literal, plus an explicit `%` guard
  rejecting an RFC 4007 zone-id suffix like `fe80::1%eth0` that stdlib
  `ipaddress` parses but Docker's Go `net.ParseIP` refuses);
  `protocol`/`mode`/`name`/`app_protocol` each a plain string, content
  unchecked (`mode: bogus` passes). An unrecognized long-form key raises —
  Docker's schema is strict here — but `app_protocol` is a real field, not a
  typo, and is in the known set alongside the other six.
  `restart`/`stop_signal` stop at the type check because that's all
  Docker itself validates for either — measured, `restart: banana` and any
  `stop_signal` string are both accepted — so enumerating an enum here would
  refuse a file Docker runs. `stop_signal`/`stop_grace_period` are inert
  because the script force-removes the pod (`podman pod rm -f`) rather than
  gracefully stopping a container. `profiles` is inert because the run set
  is fixed by `--target` plus its `depends_on` closure, not by profile
  activation: targeting a service runs it regardless of profile (as Compose
  does), and a service outside the closure never runs — so if the target
  `depends_on` a member Compose would leave in a disabled profile,
  compose2pod runs it anyway (closure authoritative, more permissive than
  Compose, never a silent drop).
- **Extension fields:** any `x-`-prefixed service key is accepted and
  ignored silently.
- Everything else raises.

### Declarative registry keys

The uniformly-shaped flag keys are defined once, each as a `(validate, emit,
merge)` triple, in the **service-key registry** (`SERVICE_KEYS` in
`compose2pod/keys.py`; see `architecture/glossary.md` for the service-key
spec / registry / structural-key terms). List-shaped keys share one
validator; map-shaped keys share another. Both require every list element
to be a string and every mapping value to be a string, number, boolean, or
null — a non-string element or non-scalar value (e.g. `cap_add: [{NET_ADMIN:
true}]`) is rejected rather than `str()`/`repr()`'d into the flag value. A
boolean mapping value (`labels: {enabled: true}`) is normalized like
`docker compose config`: the lowercase string `true`/`false`, not Python's
`str(True) == "True"`.

| Key | Podman flag | Accepted shape |
|---|---|---|
| `user` | `--user` | string |
| `working_dir` | `--workdir` | string |
| `platform` | `--platform` | string |
| `init` | `--init` (bare, only if true) | boolean |
| `read_only` | `--read-only` (bare, only if true) | boolean |
| `privileged` | `--privileged` (bare, only if true) | boolean |
| `group_add` | `--group-add` (repeated) | list of strings |
| `cap_add` | `--cap-add` (repeated) | list of strings |
| `cap_drop` | `--cap-drop` (repeated) | list of strings |
| `security_opt` | `--security-opt` (repeated) | list of strings |
| `devices` | `--device` (repeated) | list of strings |
| `labels` | `--label` (repeated; null value → bare `KEY`) | list or mapping |
| `annotations` | `--annotation` (repeated; null value → bare `KEY`) | list or mapping |
| `environment` | `-e` (repeated; null value → bare `KEY`, i.e. host passthrough) | list or mapping |
| `pull_policy` | `--pull` (`if_not_present` → `missing`, rest verbatim) | enum: `always`/`never`/`missing`/`if_not_present` |
| `ulimits` | `--ulimit` (`name=value` or `name=soft:hard`) | mapping, see below |

Every boolean field in the subset — `init`/`read_only`/`privileged`/
`oom_kill_disable` above, `tty`/`stdin_open`, the `build` and network/volume
definition bools, and `depends_on`'s `restart`/`required` — accepts a quoted
YAML-1.1 boolean spelling (`"yes"`, `"on"`, bare `yes`, ...) as a string, cast
via `values.is_bool_like`/`as_bool`, matching `docker compose config`'s own
cast. A `${VAR}` reference on an *emitting* bool (`init: ${X}`) still refuses:
a static script cannot conditionally emit a flag from a run-time variable, so
that stays a can't-express limit, not an over-reject.

`ulimits` is per limit: a scalar (`nproc: 65535` → `--ulimit nproc=65535`,
podman sets soft = hard) or a `{soft, hard}` mapping exactly (`nofile:
{soft, hard}` → `--ulimit nofile=soft:hard`), each bound an int or string. A
boolean scalar or bound is rejected rather than coerced (`bool` is
technically an `int` in Python, so a naive `isinstance` check would emit the
literal `--ulimit nofile=True`) — unlike `environment`'s boolean, a boolean
ulimit has no sensible Docker-equivalent normalization to fall back on.

`extra_hosts` is a supported key but not a registry entry — it is
pod-level; see Pod-level options, below. The resource-limit keys
(`mem_limit`, `cpus`, ...) are registry entries too, tabulated separately
under Resource limits, below.

### Structural keys

Handled outside the registry because `emit(value)` alone can't express them
— they need `project_dir`, span multiple keys, or occupy the image/command
slot (`architecture/glossary.md`).

- **`image`:** a string; read verbatim when the service has no `build`. A
  service must set at least one of `image` or `build`; neither, an empty
  string, or a non-string `image` with no `build`, raises. A non-string
  `image` alongside `build` is accepted — the CI image always wins, so
  `image_for` never reads it.
- **`build`:** a string or mapping — its contents are never *used*: `image_for`
  always substitutes the CI image, so nothing under `build:` ever reaches the
  generated script, whatever it validates as. A document `docker compose
  config` refuses (e.g. `build: 3`) must still be refused here, so the shape,
  each known key's *name*, and — since `2026-07-15.08` — each known key's
  *value* are all checked, even though none of it is emitted. A mapping's own
  top-level key names are checked against Docker's schema for it
  (`_DOCKER_BUILD_KEYS`, `compose2pod/parsing.py` — 22 keys plus any
  `x-`-prefixed extension key, measured against `docker compose config`
  v5.1.2 by probing each key individually): `additional_contexts`, `args`,
  `cache_from`, `cache_to`, `context`, `dockerfile`, `dockerfile_inline`,
  `entitlements`, `extra_hosts`, `isolation`, `labels`, `network`,
  `no_cache`, `platforms`, `privileged`, `pull`, `secrets`, `shm_size`,
  `ssh`, `tags`, `target`, `ulimits`. `context` is not required —
  `build: {dockerfile: Dockerfile}` alone is accepted.

  Each key's value is checked against Docker's own grammar for it, grouped
  into six measured clusters: a **string** (`context`, `dockerfile`,
  `dockerfile_inline`, `target`, `network`, `isolation`); a **size**
  (`shm_size` — a number or size string, but unlike the top-level `shm_size`
  service key, a *fractional* native float is refused, whole or not); a
  strict **bool** (`no_cache`, `pull`, `privileged` — a quoted YAML-1.1
  boolean spelling (`"true"`, `"yes"`, ...) is accepted via
  `values.is_bool_like`, matching Docker's own cast, same as the top-level
  six boolean keys; a `${VAR}` reference passes through, since Docker's own
  cast of it is host-state-dependent); a **list of strings** (`cache_from`,
  `cache_to`, `tags`, `platforms`, `entitlements`); a **list-or-map**, itself
  three different grammars (`args`/`labels` share one — a list of
  `'KEY[=value]'` strings or a mapping with scalar-or-null values; `ssh`
  reads like a plain list at a glance and shares that grammar for its map
  form, but its list form is stricter — a bare entry (no `'='`) must equal
  `'default'`, measured (`'invalid ssh key "mykey"'`); an entry with `'='`
  accepts any id, same as `args`/`labels`; `additional_contexts` requires
  `'='` in every list entry and a plain-string map value; `extra_hosts`
  accepts either host/IP separator in a list entry and a map value that is a
  string or list of strings); and **nested**
  structures (`ulimits` — identical grammar to the top-level `ulimits` service
  key, via the same `keys.validate_ulimits`; `secrets` — a list of a bare
  secret-name string or a `{source, target}` mapping, each `source`
  cross-checked document-wide against the top-level `secrets:` block
  (`_validate_build_secret_references`, `compose2pod/parsing.py`) — an
  undeclared name raises ("refers to undefined secret"), the same cross-check
  the service-level `secrets`/`configs` registry already runs via
  `stores.py`, though build's own reference has no `uid`/`gid`/`mode` fields
  to also check). Every mapping-shaped value's own keys
  are checked too: `build`'s contents are the one region `require_string_keys`
  does not cover through the general per-service sweep (see Top-level keys,
  below), so each of these validators checks its own keys directly — matching
  Docker, which refuses a non-string key inside `build.args`/`labels`/etc. the
  same as everywhere else.
- **`command`:** string or list, each list element a string. String form
  runs via `/bin/sh -c`; list form is argv tokens. Any other shape (e.g. a
  mapping, or a list containing one — the classic YAML list/map slip)
  raises rather than reaching `podman run` mangled. `command: null` is
  accepted, treated as absent, matching Docker (`NULL_ALLOWED_KEYS`,
  `parsing.py`).
- **`entrypoint`:** string or list, each list element a string, mirroring
  `command`; `entrypoint: null` is likewise accepted and treated as absent.
  List form is exec form; string form is shell form (`/bin/sh -c
  <string>`). Emitted as `--entrypoint <first-token>` with the remaining
  tokens placed ahead of the command after the image, so `podman run
  --entrypoint a IMAGE b <command>` runs `a b <command>` — no JSON needed. A
  string (shell-form) entrypoint ignores the service `command`, matching
  Docker; `validate()` warns when both are set. The target's `--command`
  override still applies as explicit intent, but when the target has a
  string entrypoint, the override tokens land after `-c <entrypoint-string>`
  and are passed positionally to `sh` as `$0`/`$1`... rather than executed —
  the same Docker shell-form `ENTRYPOINT` semantic, not a
  compose2pod-specific limitation. Use a list (exec-form) entrypoint, or
  none, if the override needs to actually run.
- **`env_file`:** a string, or a list whose entries are each a string or a
  mapping `{path, required, format}`. `path` resolves through `--project-dir`
  when relative and emits `--env-file`, same as the string form. `format:
  raw` is accepted then ignored: compose2pod hands the file to podman's own
  `--env-file` parser, and Compose's `format` governs only Compose's own
  parser, which compose2pod never runs. `required: true` (the default) and
  every string entry emit the flag unconditionally; `required: false` is
  honored with an in-position run-time `[ -f path ]` guard (`c2p_envfile_<i>`
  prelude lines plus a `${c2p_envfile_<i>:+"$c2p_envfile_<i>"}` token) that
  drops the flag when the optional file is absent at run time, so an
  optional env file missing at container-start time never trips `set -eu`.
- **`tmpfs`:** a string or list of strings, each `<path>` or
  `<path>:<options>` (e.g. `/tmp:mode=1777`), passed through verbatim as
  `--tmpfs <value>` — Compose's short syntax maps directly onto podman's
  own flag. No format validation beyond shape; a malformed option string
  surfaces as a podman error at run time.
- **`hostname` and `container_name`:** each made resolvable to `127.0.0.1`
  like a network alias (added to the shared, script-owned `/etc/hosts`, see
  Pod-level options), so other services can reach the service by either
  name. The pod
  shares the UTS namespace, so a service's own hostname is the pod's; the
  actual podman container is always named `{pod}-{service}` regardless of
  `container_name` (used internally for `podman cp`, healthcheck polling,
  diagnostics) — only name *resolution* is meaningful to other services, no
  per-container `--hostname` or renamed `--name` is emitted. Each must be a
  string when present. `container_name`, when present, must additionally
  match Docker's own pattern `[a-zA-Z0-9][a-zA-Z0-9_.-]+` (a search, not a
  fullmatch) — an empty `container_name: ""` fails it and raises, unlike
  `hostname`, which carries no such rule and accepts an empty string
  (measured against `docker compose config`; refusing an empty `hostname`
  would be an over-rejection).
- **Per-service `networks`:** long (mapping) form contributes each entry's
  `aliases` to the same resolvable-name set as `hostname`/`container_name`;
  short (list) form carries none. Must be a list or mapping. Every network a
  service names must be declared in the top-level `networks` block, or
  `validate()` raises ("refers to undefined network") — the top-level
  block's *contents* are still ignored (see Top-level keys, above), only the
  reference is checked, because a document naming an undeclared network is
  one `docker compose config` refuses. **`default` is the one exception:**
  it is Docker's implicit network, always available whether or not a
  top-level `networks:` block exists at all — `networks: [default]` with no
  top-level declaration is accepted (measured against `docker compose
  config` v5.1.2; `_validate_network_references` seeds `declared` with
  `{"default"}` unconditionally, Task 14). Every other undeclared name
  still raises, including Docker's other two reserved names (`host`,
  `none` — neither is implicitly available). Declaring `default` explicitly
  (`networks: {default: {...}}`) is legal too, and a no-op alongside the
  implicit declaration. A short (list) form's entry must itself be a string
  before the membership check runs — a still-unvalidated entry (e.g. a
  dict, the same list/map YAML slip `depends_on`/`command`/`environment`
  all suffer) used to hash straight into a `set` and crash raw
  (`TypeError: unhashable type`) instead of failing clean.

  A long-form entry's *value* is a STRICT typed sub-schema, unlike the
  ignored top-level `networks:` block — Docker refuses both a non-mapping
  value (`networks: {n: somevalue}`) and an unknown sub-key (`additional
  properties 'x' not allowed`), checked document-wide over every service
  (`_validate_network_entries`, `compose2pod/parsing.py`), mirroring
  `_validate_network_references`'s own document-wide scope rather than the
  startup-order closure `emit.py` computes at emit time. `null` is accepted
  (means "use default settings", same as an explicit `{}`). The full valid
  key set — measured against `docker compose config` v5.1.2, cross-checked
  against the upstream compose-spec JSON schema — is exactly nine, plus any
  `x-`-prefixed extension key: `aliases` (list of strings — the same grammar
  `graph._host_names` already enforces for its own alias extraction, checked
  twice by design, defense in depth, same pattern as `extra_hosts`);
  `ipv4_address`, `ipv6_address`, `mac_address`, `interface_name` (a plain
  string, content unchecked); `priority`, `gw_priority` (a *native* number
  only — unlike most numeric grammars in `values.py`, a numeric string is
  refused, and unlike them, a `${VAR}` reference is never carved out:
  interpolation only ever produces a string, which this grammar never
  accepts, so the rejection holds for every possible value of the variable,
  not just the reading host's — a fact about the document, not the host);
  `link_local_ips` (list of strings); `driver_opts` (a mapping, each value a
  number or string — keys otherwise unchecked, matching Docker; compose2pod
  never reads a network entry's contents at emit time regardless of the
  checked shape).

## Extends

- **Resolution timing:** `extends` is resolved by `resolve_extends`
  (`compose2pod/extends.py`) as a pre-validation flattening step, called
  from `cli.py` immediately before `validate()` — the rest of the pipeline
  never sees an `extends` key.
- **Supported form:** only `extends: {service: <name>}`; `service` must
  name another service in the same document. Resolution is transitive (a
  chain of `extends` is fully flattened); a cycle raises.
- **Merge rules**, applied per key when both the base and local service
  define it:
  - **Mapping-merge, local wins:** `environment`, `labels`, `annotations`,
    `extra_hosts`, `ulimits`, `healthcheck`, `depends_on` — keys combined
    into one mapping, local's value winning on collision.
  - **Sequence-concatenate, base then local:** `cap_add`, `cap_drop`,
    `security_opt`, `devices`, `group_add`, `secrets`, `configs`, `volumes`,
    `tmpfs`, `env_file`.
  - **Override, local replaces:** every other key, including `command` and
    `entrypoint` (argv replaced wholesale, never concatenated) — also
    covers unknown keys, which `validate()` then rejects downstream exactly
    as it would without `extends`.
  - **Normalization before merge — a merge never widens the gate.** A merged
    side is normalized only through a form that key *actually has*: list-form
    `environment`, `labels`, `annotations`, `extra_hosts` and `depends_on`
    become mappings before the mapping-merge, and scalar-form `tmpfs`/`env_file`
    become a one-element list before the concatenation. Nothing else is
    coerced. This matters because `resolve_extends` runs **ahead of**
    `validate()`: normalizing a form a key does not have would hand the gate a
    document it would have
    refused standalone. So `ulimits` and `healthcheck` (no list form) refuse a
    list, and every list-only key (`cap_add`, `cap_drop`, `security_opt`,
    `devices`, `group_add`, `volumes`, `secrets`, `configs`) refuses a bare
    scalar — exactly as each does outside `extends`. For a **registry** key the
    accepted forms are read from the key's own validator (`spec.validate` runs
    on both sides before `spec.merge`), so those cannot drift from the gate by
    construction. The **structural** keys have no `KeySpec`, so their accepted
    forms are declared in `extends.py` (`_STRUCTURAL_*`, `_SCALAR_FORM_KEYS`)
    and kept honest by a test asserting, for every mergeable key, that a form
    the gate refuses standalone is refused through `extends` too.
    `extra_hosts`' list form divides on `=` or `:` (`keys.split_extra_host`,
    the one reader the gate, the emitter and the merge all share), so an IPv6
    address survives either way.
- **Refused loudly:** cross-file `extends: {file: ..., service: ...}`; a
  bare-string (or any other non-mapping) `extends`; an unrecognized key
  under `extends` other than `service`; a non-string `service`; a `service`
  naming one that doesn't exist; and a merge across incompatible forms — a
  side whose form that key does not have. A registry key raises with its own
  validator's message (`'cap_add' must be a list`), the same message the value
  produces standalone; a structural key raises `cannot merge '<key>' across
  incompatible forms`. A malformed *form* is reported against the service the
  value belongs to — the base, when it is the base's value at fault, not the
  service extending it.
- **A null in the extending service means "not specified"** — the base's value
  is inherited, as Docker does. **Except `command` and `entrypoint`, where a
  null is a *reset*:** Docker erases the inherited value so the image's own
  default runs, and so does compose2pod. (`deploy` tolerates a null and
  inherits, so the reset set is not simply "the keys that allow a null".)
  A null in the *base* means "not specified" the same way, so the extending
  service's value wins. This is reachable for exactly the three keys the gate
  allows a null on: a base with `command:` and a child that sets one gives the
  child's, as Docker does. For every other key the base's null is refused by
  the gate first (the base is a service too), so the merge never sees it.
- **Divergences from Compose:** short-form `volumes` are concatenated rather
  than merged by target path; podman resolves duplicate mounts at run time.
  Referenced resources
  (top-level `volumes`, `networks`, `secrets`, `configs`) are not
  auto-imported — as in Compose, the extending service must declare what it
  needs.

## Pod-level options

A Podman pod shares one network namespace across every container joined to
it, so a handful of Compose keys cannot be per-container `podman run`
flags. compose2pod hoists them onto `podman pod create` instead
(`compose2pod/pod.py`) — the tool's only pod-create flags.

- **Supported:** `dns`, `dns_search`, `dns_opt`, `sysctls`, `extra_hosts` —
  mapped to `--dns`, `--dns-search`, `--dns-option`, `--sysctl`, and (merged
  with the alias/hostname set) lines in the pod's owned `/etc/hosts`
  respectively.
- **Value shapes:** `dns`/`dns_search` accept a string or list of strings;
  `dns_opt` accepts a list of strings only — Docker refuses a bare string
  there (`dns_opt: "ndots:2"`) even though it accepts one for `dns` and
  `dns_search`; the asymmetry is Docker's, measured, not compose2pod's.
  `sysctls` accepts a mapping (`key: value`) or a list of
  `"key=value"` strings, each value a string or number; `extra_hosts`
  accepts a list or a mapping (`host: ip`) — every map value must be a
  string (measured: Docker refuses `extra_hosts: {h: 3}` and
  `extra_hosts: {h: true}` alike, "must be a string"; the list form cannot
  carry this problem, since its elements are already required to be
  strings). A list entry divides on `=`
  (Compose's documented separator, `- somehost=162.242.195.82`) or on the
  legacy `:` (`- somehost:162.242.195.82`) — `=` wins when both appear,
  because an IPv6 address is itself full of colons and splitting on the first
  one would tear it apart (`myhostv6=::1`); the colon form splits on the
  *first* colon only, so `myhost:::1` works too. Every reader — the gate, the
  emitter, and the `extends` merge — goes through the one shared
  `keys.split_extra_host`, so they cannot disagree about where an entry
  divides. An entry with neither separator has no address and raises, rather
  than emitting a malformed hosts-file line. A `${VAR}` inside a
  value stays live at run time (see Variable interpolation, below) and
  counts toward `referenced_variables`.
- **Aggregation is closure-scoped:** computed over the target's dependency
  closure (`startup_order`), exactly like stores. `dns`/`dns_search`/
  `dns_opt` are unioned (deduplicated, first-seen order); `sysctls` are
  unioned by key, and two closure services setting the same key to
  different values is refused (`conflicting sysctl ...`) rather than
  resolved last-writer-wins. The hosts file is seeded from the alias/hostname
  set of the closure's services (each mapped to `127.0.0.1`), then layered
  with each closure service's `extra_hosts` (each mapped to its address); a
  host name landing on two different addresses is refused the same way
  (`conflicting host ...`). An alias/hostname line stays a plain unquoted
  token; an `extra_hosts` line is `${VAR}`-live.

  Only the closure joins the pod, so only the closure is resolvable: a
  service outside it contributes no name and cannot conflict with an
  `extra_hosts` entry. Resolving a never-run service's name to `127.0.0.1`
  would point it at a port where nothing listens, turning an honest
  resolution failure into a connection-refused. Shape validation of
  `hostname`/`container_name`/`networks` stays document-wide at the gate
  (`hostnames` in `parsing.py`), so a malformed value is still rejected on a
  service the target never reaches.
- **Pod-wide divergence:** unlike every other service key, these apply to
  every container in the pod once emitted — including services that never
  declared them — because the pod shares one `/etc/resolv.conf`, sysctl
  set, and `/etc/hosts`. `validate()` warns whenever *any* service anywhere
  declares one of these keys (`uses_pod_options`), regardless of whether
  that service is in the target's closure; at emit time a declaration on a
  service outside the closure is silently ignored — no flag for it, since
  that service never runs.
- **The script owns `/etc/hosts`, not Podman.** `pod_create_flags` no longer
  emits `--add-host`; instead `hosts_file_tokens` (`compose2pod/pod.py`)
  renders the merged alias/hostname/`extra_hosts` set as `IP NAME` lines,
  prefixed with `127.0.0.1 localhost` and `::1 localhost`. The script writes
  those lines to a `mktemp` path (`hostsfile=$(mktemp)`) once, before the
  first container starts, and removes it in the `trap ... EXIT` teardown
  (`rm -f "$hostsfile"`). `mktemp` creates the file `0600`; the script then
  `chmod 644`s it so a service image running as a non-root user can read the
  bind-mounted `/etc/hosts` — without it, glibc cannot read the file, falls
  through to DNS, and resolution fails with "Temporary failure in name
  resolution". `podman pod create` and every `podman run` —
  target, `--rm` completion dependency, and `-d` long-running alike — pass
  `--no-hosts` and bind-mount the file read-only:
  `-v "$hostsfile":/etc/hosts:ro,z`. `--no-hosts` and `--add-host` conflict,
  so the two moves are one change. `z` relabels the mount for SELinux (a
  no-op where SELinux is not enforcing) — without it, a bind-mounted
  `/etc/hosts` is unreadable on an SELinux-enforcing host. Because
  `--no-hosts` stops Podman from managing `/etc/hosts` at all, the pre-6.0.0
  bug where a container stopping inside a pod wiped the shared `/etc/hosts`
  for every other container cannot fire — compose2pod works identically on
  every Podman version, with no version floor. `--no-hosts` also stops Podman
  from adding each container's own hostname line, so `podman pod create` gets
  `--uts private --hostname <pod-name>` and the pod name is added to the hosts
  file (`127.0.0.1 <pod-name>`): a container resolving its own hostname
  (`hostname -f`) — which Podman handled before `--no-hosts` and which images
  with a self-referential startup do — resolves again. A pod shares one UTS
  namespace, so this hostname is pod-wide; per-service `hostname:` values stay
  resolvable by peers via the hosts file but do not set a container's own name.
  `host.containers.internal` /
  `host.docker.internal` are not provided: `--no-hosts` means Podman's
  computed gateway IP is unavailable, so a service needing it must add an
  explicit `extra_hosts` entry (see `README.md`'s Requirements section).
- **Non-goals:** per-service DNS/sysctls — impossible inside a
  shared-namespace pod, not a compose2pod limitation; last-writer-wins on a
  sysctl or host conflict — refused instead.

## Resource limits

Compose exposes container resource limits two ways — legacy scalar service
keys and the Compose-spec `deploy.resources` block — and compose2pod honors
both, refusing loudly on overlap rather than picking a precedence.

| Legacy key | Podman flag | Grammar |
|---|---|---|
| `mem_limit` | `--memory` | size |
| `memswap_limit` | `--memory-swap` | size |
| `mem_reservation` | `--memory-reservation` | size, whole-valued if native float |
| `mem_swappiness` | `--memory-swappiness` | size, whole-valued if native float |
| `cpus` | `--cpus` | number |
| `cpu_shares` | `--cpu-shares` | count |
| `cpu_quota` | `--cpu-quota` | count |
| `cpu_period` | `--cpu-period` | count |
| `cpuset` | `--cpuset-cpus` | string |
| `pids_limit` | `--pids-limit` | count |
| `shm_size` | `--shm-size` | size |
| `oom_score_adj` | `--oom-score-adj` | integer, whole-valued float allowed |
| `oom_kill_disable` | `--oom-kill-disable` (bare, only if true) | boolean |

Each grammar is a value shape measured against `docker compose config`
v5.1.2, not a blanket "number or string" — `values.py` implements the five
non-boolean ones:

- **size** (`validate_size`): a number, or a string like `512m`/`1gb`/`1e3`
  (Docker's byte-size grammar, an optional unit suffix on a float). Whitespace
  is exactly one optional space, and only between the number and the unit:
  `"512 m"` and the unit-less `"512 "` are both accepted, but a leading,
  trailing-after-a-unit, doubled, or tab space is refused (`" 512m"`,
  `"512m "`, `"512  m"`, `"512\tm"` — Go's `ParseFloat` grammar, not Python's
  whitespace-stripping `float()`). Refused:
  `''`, a non-finite float, a string with no unit Docker recognizes.
  `mem_reservation`/`mem_swappiness` additionally refuse a *native* float
  with a fractional part (`0.5` — Docker: "must be a integer"; `60.0` is
  fine); the string branch (`"1.5"`) is unrestricted either way, since
  Docker's size-string grammar has no such rule. `string_only=True`
  (`deploy.resources.limits.memory`/`reservations.memory`, below) refuses
  *any* native number, whole or fractional — those two fields are typed as a
  plain Go string, not the size-or-string union `mem_limit`/
  `mem_reservation` are, so only a size string is ever accepted.
- **number** (`validate_number`, `cpus` only): a number, or a string that
  parses as one — Docker's `ParseFloat`, unrestricted except that (unlike a
  size) it permits no surrounding whitespace at all (`" 1.5 "` is refused),
  matching Go rather than Python's whitespace-stripping `float()`.
- **count** (`validate_count`; `cpu_shares`/`cpu_quota`/`cpu_period`/
  `pids_limit`): a native number is cast leniently (`0.5` accepted), but a
  *string* must be a strict integer — no decimal point, exponent, or
  digit-grouping underscore — because Docker casts the string form through
  Go's `ParseInt`. The identical native value the string form refuses is
  accepted natively; this native/string asymmetry is Docker's, not
  compose2pod's.
- **string** (`validate_string`, `cpuset` only): any string, refusing only a
  number — Docker validates no further content (`cpuset: abc` and `''` are
  both accepted).
- **integer** (`validate_integer`, `oom_score_adj` only, `allow_whole_float`):
  an integer, or a string that parses as one; a native whole-valued float
  (`1000.0`) is accepted the same way `mem_reservation`'s size grammar
  accepts one, but a fractional float is refused. The string form goes
  through Go's `ParseInt` — no digit-grouping underscore and no surrounding
  whitespace (`" 5 "` is refused), unlike Python's lenient `int()`.

Every grammar short-circuits on a value carrying a `${VAR}` reference and
passes it through unvalidated, live at run time — not merely because
interpolation is deferred, but because Docker's own verdict on such a value
is itself a fact about the *reading host's* environment: `mem_limit: ${MEM}`
fails `docker compose config` with `invalid size: ''` only because the
variable is unset in the checking shell, and passes once it's exported. A
bool is refused by every grammar above; `oom_kill_disable` is the one
boolean-typed exception in this group, validated as an actual bool like
`read_only`/`init`/`privileged`.

- **`deploy.resources`** (`compose2pod/resources.py`): under `deploy`, only
  `resources` is honored — any other subkey (`replicas`, `placement`,
  `restart_policy`, ...) raises, and unrecognized keys within `resources`,
  `limits`, or `reservations` raise the same way. `limits.cpus`/
  `limits.memory`/`limits.pids` map to `--cpus`/`--memory`/`--pids-limit`;
  `reservations.memory` maps to `--memory-reservation`. These four fields are
  not registry entries, but each is measured against and carries its own
  grammar from `values.py` above — nested position is not a reason to fall
  back to a loose check:
  - `limits.cpus` — **number** (`validate_number`), identical to the
    top-level `cpus` key.
  - `limits.pids` — **integer**, `allow_whole_float=True`
    (`validate_integer`), identical to `oom_score_adj`.
  - `limits.memory` / `reservations.memory` — **size**, `string_only=True`.
    This is the field where nesting actually changes the grammar: Docker
    types these two as a plain string, so a native number that the legacy
    `mem_limit`/`mem_reservation` keys accept (`512`, `1.5`) is refused here
    — only a size *string* (`"512m"`, `"1.5"`) passes. Measured against
    `docker compose config` v5.1.2.

  `reservations.cpus` and `reservations.devices` have no podman equivalent
  and are refused outright, regardless of value.
- **Refuse on conflict:** when a legacy key and its `deploy.resources`
  counterpart both set the same flag — `mem_limit`/`limits.memory`,
  `cpus`/`limits.cpus`, `pids_limit`/`limits.pids`, and
  `mem_reservation`/`reservations.memory` — conversion refuses rather than
  picking a precedence the Compose spec itself leaves undefined.
- **Non-goals:** `blkio_config` and the Windows-only
  `cpu_count`/`cpu_percent` remain rejected — neither is in the
  service-key registry or the structural-key set, so each hits the generic
  "everything else raises" gate.

## Healthcheck keys

- **Supported:** `test`, `interval`, `timeout`, `retries`, `start_period`.
  A `healthcheck` value that isn't a mapping raises.
- **`test`:** a bare string (shell form), `"NONE"` / `["NONE"]` (disabled),
  `["CMD", ...]` (exec form), or `["CMD-SHELL", <string>]`. `health_cmd`
  (`compose2pod/healthcheck.py`) is the sole reader and validator. Any
  other shape raises, including a `CMD-SHELL` whose argument isn't a
  string and a `CMD` whose trailing elements aren't all strings.
- **`interval`, `timeout`, `start_period`:** a Go duration *string* with a
  mandatory unit — `"30s"`, `"2m"`, `"500ms"`, `"1m30s"` — or the bare literal
  `"0"` (Go's `time.ParseDuration` special-cases the zero duration; Docker
  accepts it even though a unitless `"30"` is refused as "missing unit in
  duration"). A native number (`30`, `1.5`, `0`) and any other unitless
  string raise, matching `docker compose config` v5.1.2 — these used to be
  silently accepted (`is_number`/`interval_seconds`'s bare-number fallback),
  producing a script for a file Docker itself refuses.
  `interval` is parsed to whole seconds by `interval_seconds`, which never
  reaches podman (it only paces compose2pod's own `wait_healthy` polling
  loop), so it honors the full compose-go duration grammar rather than the
  narrower one `timeout`/`start_period` forward to podman's `--health-*`
  flags: a signed sequence of `<number><unit>` components, units
  `ns`/`us`/`µs`/`ms`/`s`/`m`/`h`/`d`/`w` (`d` = 86400s, `w` = 604800s —
  compose-go additions over Go's `time.ParseDuration`), compound
  (`"1h30m"`), fractional (`"1.5d"`), and signed (`"-1h"`) forms — measured
  against `docker compose config` v5.1.2. Whitespace and uppercase units are
  refused, matching Docker (`" 1h "`, `"1H"` both raise). It floors at 1
  second: the polling loop has no sub-second resolution, so `"500ms"` and
  `"0"` both poll once a second, and a negative result (`"-1h"`) also floors
  to 1 rather than producing a nonsensical negative interval. An explicit
  `null` (or an absent `interval`) defaults to 1 second.
- **`retries`:** an int64 count (`values.validate_count`): a native number
  casts leniently (`0.5` is accepted), but a string form must be a strict
  integer — no decimal point, no exponent (`"1.5"`, `"30s"` raise). A mapping
  or list raises rather than reaching its `--health-retries` flag as a
  literal Python `repr()`.
- **A null raises in every healthcheck position** — `test`, `interval`,
  `timeout`, `retries`, `start_period` — because `docker compose config`
  refuses each. A bare `test:` would silently drop the healthcheck entirely;
  a bare `timeout:` drops nothing on its own, but the document carrying it is
  one `docker compose` will not run, and compose2pod is a drop-in replacement
  for it — so passing CI green on such a file would be a false green. An
  *omitted* key is a different thing and stays fine: podman's default applies.
- **Extension fields:** any `x-`-prefixed healthcheck key is accepted and
  ignored silently.
- Everything else raises.

## Volumes

A `volumes` entry may be the short string syntax or the long-form mapping
`{type, source, target, read_only, consistency}`. `type` must be `bind`,
`volume`, `tmpfs`, or `image`. `cluster` and `npipe` are refused as permanent
rule-two limitations — podman's `--mount` rejects them (`invalid filesystem
type`) and can never express them. A `type: image` entry mounts an image's
rootfs at `target`: `source` (an image reference) is required — podman
refuses a sourceless image mount (`must set source and destination for image
volume`), a rule-two narrowing the same way `bind`'s required `source` is.
`read_only` is accepted and validated on an image entry but never emitted: an
image mount is inherently read-only and podman's `--mount` refuses a `ro`
option on one, so a `true` is a redundant no-op and a `false` a harmless one,
matching Docker's own accept-and-ignore stance. A long-form entry may also
carry the nested option sub-map matching its own `type` — `bind:
{propagation, selinux}`, `volume: {subpath}`, `tmpfs: {size, mode}`, `image:
{subpath}` — each accepted and appended to the emitted `--mount` value:
`propagation` (narrowed to podman's enum `private`/`rprivate`/`shared`/
`rshared`/`slave`/`rslave`) becomes `bind-propagation=<value>`; `selinux`
(`z`/`Z`) becomes `relabel=shared`/`relabel=private`; `subpath` (on either
`volume` or `image`) becomes `subpath=<value>`; `tmpfs.size`/`tmpfs.mode`
become `tmpfs-size=<value>`/`tmpfs-mode=<value>`. An `image` subpath must be
an absolute path — podman requires one (`must be an absolute path`) while
docker accepts a relative one, a rule-two narrowing (a `${VAR}` subpath is
exempt, as host-dependent). A `volume` subpath has no such requirement and
may stay relative — podman resolves it against the volume root. Two options
stay refused as
permanent rule-two limitations because podman's `--mount` cannot express them
at all: `bind.create_host_path` and `volume.nocopy`. A sub-map that does not
match the entry's own `type` (a `bind:` map on a `type: volume` entry, for
example) is refused too — docker accepts and silently ignores a mismatched
sub-map, but compose2pod treats it as a likely mistake and refuses it, a
deliberate stricter-than-docker check.

A `target` must be an absolute path (a `${VAR}` reference is accepted, being
host-dependent) — podman's `--mount` rejects a relative target for every
type (`invalid container path "rel", must be an absolute path`) even though
docker accepts one, matching the short-form anonymous-volume refusal (below).
A `tmpfs`-type entry's `source` is refused even though docker accepts one:
podman's `--mount` has no way to express a `source` on a `tmpfs` mount
(`"source" option not supported for "tmpfs" mount types`), so this is a
rule-two refusal, not a docker-schema rule.

Each accepted entry is emitted as a single `--mount` flag
(`compose2pod/emit.py`'s `_mount_flag`) rather than `-v`: `type=<type>`,
`source=<source>` (a relative bind `source` is resolved against
`--project-dir`, the same as the short form), `target=<target>`, and a
trailing `ro` when `read_only` is truthy — `read_only` accepts the quoted
`"true"`/`"false"` form via the same `is_bool_like` check every other
boolean field uses. An `image`-type entry is the one exception: `ro` is never
appended, regardless of `read_only`, since the mount is read-only by
construction and podman refuses the option outright. `consistency` is
accepted and validated as a string but
otherwise ignored — podman's `--mount` has no consistency knob. A long-form
`volume`-type entry whose `source` is a bare identifier is cross-checked
against the top-level `volumes:` block exactly like a short-form named
volume (below) — a `bind`/`tmpfs`/`image` entry's `source`, or an absent one,
needs no declaration.

The `volumes` key itself
must be a list — a bare string raises, rather than being destructured one
character at a time. A `source:target` entry is one of two kinds, told apart
by whether `source` matches Docker's own volume-name grammar
(`stores.NAME_PATTERN`, `^[a-zA-Z0-9][a-zA-Z0-9_.-]*$` — the identical
pattern a secret/config name is checked against):

- **Bind mount** (`source` does not match the name grammar — `./rel`,
  `/abs`, a `~`-prefixed home-relative path, or a `${VAR}` reference): the
  host path, resolved against `--project-dir` when relative and passed
  through verbatim otherwise (a `~`-prefixed source is not expanded by
  compose2pod itself — it reaches the emitted script as written, the same as
  every other unresolved token). **Known residual:** a Windows drive-letter
  source (`C:\data`) is still misclassified as a named volume and rejected —
  the shared colon-split (`source, _, _ = volume.partition(":")`) takes the
  drive letter alone as `source`, which is itself a valid name-grammar match;
  Docker's own parser special-cases the leading `<letter>:\` before ever
  comparing it to a name grammar. Catalogued in `planning/deferred.md`, not
  fixed (needs a bigger change to the shared split, in both `parsing.py` and
  `emit.py`, not a grammar swap).
- **Named volume** (`source` matches the name grammar, e.g.
  `pgdata:/var/lib/...`): passed through verbatim as `-v <name>:<target>` —
  no format validation, no path translation. Podman creates it implicitly
  with default options on first reference (same as plain `podman run -v`,
  no explicit `podman volume create` step needed), and it persists on the
  host after the pod is removed, identical to `docker compose down` without
  `-v`. The top-level `volumes:` block (declaring drivers/options) is
  accepted but ignored *for effect* — a non-default `driver`/`driver_opts` or
  `external: true` (which Compose treats as "must already exist") has no
  effect, since podman's implicit creation is the only path taken either way.
  **The name itself must still be declared, though:** a NAMED volume's source
  must appear as a key in the top-level `volumes:` block, or `validate()`
  raises ("refers to undefined volume") — the same "ignored for effect, still
  validated for existence" stance the top-level `networks:` block already has
  (`_validate_volume_references`, `parsing.py`, Task 14, mirroring
  `_validate_network_references`). A declaration with `external: true` still
  counts. A bind mount or an anonymous volume needs no declaration at all —
  neither names a volume, so neither needs the `${VAR}`-carrying-source
  carve-out other host-state-dependent grammars in `parsing.py` need either:
  `$`, `{`, and `}` are none of them name-grammar characters, so a
  `${VAR}`-carrying source is already excluded by the grammar match itself.

A single absolute container path with no `source:target` (e.g.
`- /var/cache/models`) is accepted as an **anonymous volume** and emitted
verbatim as `-v <path>` — podman creates an anonymous volume at that path
(the common way to shadow a subdirectory of a bind mount). No host-path
translation is applied, since the entry names a container path, not a host
source. A colon-less entry that is not absolute (e.g. `./cache`) is
malformed and raises.

## Stores (secrets and configs)

Compose `secrets` and `configs` both render as podman secrets — podman has
no config primitive — so the two differ only in namespacing and default
mount, never in the podman noun (`compose2pod/stores.py`, `StoreKind`):

| | Secrets | Configs |
|---|---|---|
| Store-name prefix | none — `<pod>-<name>` | `config-` — `<pod>-config-<name>` |
| Allowed sources | `file`, `environment` | `file`, `environment`, `content` |
| Default mount target | the store's own name | `/<name>` (container-root absolute path) |
| Absolute target required | no | yes — a relative long-form `target` raises |

The prefix keeps a config from ever colliding with a same-named secret.

- **Top-level definitions:** each entry's name must match
  `[a-zA-Z0-9][a-zA-Z0-9_.-]*`; its value must be a mapping with exactly one
  of the allowed sources, each a plain string — `file` a host path resolved
  against `--project-dir` when relative, `environment` a host
  environment-variable name (checked against `[a-zA-Z_][a-zA-Z0-9_]*`),
  `content` (configs only) inline literal text. `external: true` gets its
  own rejection message (this package has no analogue to Compose's "must
  already exist" store); any other unrecognized key raises generically.
- **Per-service references:** the key's value must be a list — a bare string
  raises. Each entry is short form (`- name`) or long form (a mapping with
  `source` and optionally `target`, `uid`, `gid`, `mode`), and a long-form
  `source` must be a string. `source`
  must name a top-level definition of the same kind; an unknown `source`
  raises. An unrecognized long-form key raises. When present, `target` must
  be a string. `uid`/`gid` and `mode` are NOT the same grammar, despite
  reading like siblings (measured against `docker compose config` v5.1.2):
  `uid`/`gid` are a plain string field with no further parsing at
  config-validate time — a native int/bool/float/null is refused ("must be a
  string"), but the string's own content is entirely unchecked, even
  `uid: somevalue`. `mode` goes through Go's `strconv.ParseInt` at decode
  time instead — a native int is accepted, any float is refused (whole or
  fractional, unlike uid/gid, which have no numeric type at all), and a
  string must match ParseInt's strict grammar (an optional sign then digits
  only — no digit-grouping underscore, no surrounding whitespace;
  `values.validate_integer`'s `strict_string=True`).
- **Closure-scoped creation:** only a definition referenced (by `source`)
  from somewhere in the target service's dependency closure is ever
  created — a top-level definition nothing in the closure references never
  becomes a `podman secret create` call, driven by the same `startup_order`
  closure that decides which services run at all.
- **Creation:** each referenced store becomes one pod- (and, for configs,
  kind-) namespaced `podman secret create` line, emitted right after `podman
  pod create` and before any `podman run`. `file:` resolves `Path(project_dir,
  file)` through `to_shell()`, so a `${VAR}` in the path expands live at run
  time. `environment:` pipes `printf '%s' "${VAR-}"` in — `${VAR-}` means an
  *unset* host variable yields an empty store rather than failing the
  script. `content:` (configs only) pipes the literal text through
  `to_shell()` the same way, so a `${VAR}` inside it also stays live.
- **Mounting:** each reference becomes a `--secret
  source=<pod>-<prefix><name>,target=<target>` flag on that service's
  `podman run`, `target` defaulting per the table above when the reference
  doesn't give one. `uid`/`gid`/`mode` are added only when given explicitly;
  `mode` renders as 4-digit octal when given as a Python int (`0o400` →
  `"0400"`), verbatim otherwise. Omitted, podman applies its own defaults
  (a secret: mounted at `/run/secrets/<target>`, owned `0:0`, mode `0444`).
- **Teardown:** the EXIT trap that force-removes the pod also runs `podman
  secret rm` for every referenced store, so no store outlives the pod even
  when the script exits abnormally — best-effort (`|| true`), so a failed
  removal can never abort the trap and leak the pod.
- **Variable interpolation:** an `environment:` source's variable name, and
  any `${VAR}` inside a `file:` or `content:` value, count toward the CLI's
  informational stderr note of variables the script expands at run time
  (see Variable interpolation, below).
- Everything else raises: `external: true`; an unknown `source`; a
  definition with not exactly one of the allowed sources; an unrecognized
  long-form key; and (configs only) a relative long-form `target`.

## Variable interpolation

compose2pod does not resolve Compose Spec `${VAR}` references at generation
time. `to_shell()` (`compose2pod/shell.py`) instead re-encodes each
interpolated string leaf into a double-quoted POSIX-shell fragment with the
variable references left live, so the generated script's own shell expands
them against its runtime environment when the script runs.

`Expand` (`compose2pod/keys.py`) is a frozen dataclass wrapping the `str`
value every interpolated field carries; it rejects a non-`str` value at
construction with `UnsupportedComposeError`, since `to_shell()`/
`variable_names()` both assume a `str` and crash raw otherwise. This is a
chokepoint, not a substitute for validating each key's shape at
`validate()` time — it only converts an already-malformed value into a
clean error one step later, at `emit_script()`, instead of leaving it to
crash inside `shell.py`'s regex matching.

The interpolated set is exactly what `Expand(...)` wraps in `emit.py` and
`keys.py` — there is no separate list to maintain by hand, so treat the
service-key registry and the `Expand(...)` call sites as the source of
truth if this enumeration ever appears to drift:

- **Structural fields:** `image` (only when the service has no `build`
  override — otherwise the CI image is used, not the compose value),
  `command`, `entrypoint`, `env_file`, `volumes`, `tmpfs`,
  the healthcheck `test` command, and the pod-level
  `dns`/`dns_search`/`dns_opt`/`sysctls`/`extra_hosts` values.
- **Registry fields** whose spec wraps its value in `Expand` — every
  `SERVICE_KEYS` entry emitted by the `_scalar`/`_number_scalar`/
  `_list`/`_map` factories, plus the custom `ulimits` emitter, except
  `pull_policy` (a validated enum emitted verbatim from `PULL_POLICY_MAP`)
  and the four boolean flags
  `init`/`read_only`/`privileged`/`oom_kill_disable` (each emits a bare
  flag with no value to interpolate).

Everything else is never interpolated: `build`'s own contents (never
read), `depends_on`, `networks`, `hostname`, and `container_name` (the
last two are emitted as literal `127.0.0.1 host` hosts-file lines, not
expanded), and the healthcheck `timeout`/`start_period`/`retries` numbers.

Supported forms: `$VAR`, `${VAR}`, `${VAR:-default}`, `${VAR-default}`,
`${VAR:?msg}`, `${VAR?msg}`, `${VAR:+alt}`, `${VAR+alt}`, and `$$` for a
literal `$`. The operator forms map onto identical POSIX `sh` parameter
expansion; bare `$VAR`/`${VAR}` is emitted as `${VAR-}` so an unset
variable expands to empty under the script's `set -eu` (matching Compose
semantics) instead of aborting on `nounset`. `${VAR:?msg}`/`${VAR?msg}`
fails the script at run time — with `msg` — if the variable is unset or
empty; there is no generation-time check. A braced reference whose text
after the name is not one of these operators (e.g. `${FOO!bar}`) is
malformed and raises rather than silently dropping the trailing text.

Tool/CLI-supplied values (`--project-dir`, `--image`, the pod name, the
`--command` override) are literal and never interpolated. `_validate_options`
(`compose2pod/emit.py`) guards two of them for a library caller building
`EmitOptions` directly (the CLI's `argparse` already enforces both):
`--artifact` must be a string in `SRC:DST` form, a non-string or `:`-less
value raising; each `allow_exit_codes` entry must be an `int`, not `bool`
(an `int` subclass but not a meaningful exit code), since it is
interpolated unquoted into the generated `case "$rc" in ...)` pattern —
otherwise shell injection, not just a crash. The pod name is embedded into
the pod-create line, the single-quoted `EXIT` trap, and the `<pod>-<name>`
store names (some unquoted), so it must be a shell-inert identifier —
`emit_script` validates it against `POD_NAME_PATTERN`
(`^[A-Za-z0-9][A-Za-z0-9_.-]*$`) and raises otherwise, guarding library
callers as well as the CLI's `--pod-name`.

The CLI prints one informational stderr note listing the variable names the
generated script actually expands at run time — `referenced_variables()`
collects these from the same tokens `to_shell()` renders, so a `${VAR}`
sitting in a literally-emitted field (or in the `--command` override) never
appears in the note. There is no `.env` file support — only the environment
present when the generated script runs is consulted; export the values
first (`set -a; . .env; set +a`) if a project relies on a `.env` file.

## depends_on

All three conditions are honored: `service_started`, `service_healthy`,
`service_completed_successfully` — any other condition string raises. A
`service_healthy` dependency on a service with no usable healthcheck raises.

`depends_on` (`compose2pod/graph.py`) itself must be either a list of
service names (short form) or a mapping of service name to a per-dependency
mapping (long form, read for `condition`). The two forms default
differently, matching `docker compose config` v5.1.2 exactly: a short-form
entry defaults to `service_started` — Compose's own default for that form —
but a long-form entry with no `condition` key at all raises ("missing
required key 'condition'"); Docker refuses this too ("missing property
'condition'"), even though it silently defaults the short form. compose2pod
used to default the long form the same way the short form does
(`spec.get("condition", "service_started")`), which was a false green
against `planning/decisions/2026-07-14-docker-rejection-parity.md`'s hard
rule, closed in Task 13. Anything else — a bare string (including the empty
string; `or {}`'s falsy default used to swallow it silently as "no
dependencies"), a number, a mapping whose value isn't itself a mapping —
raises at the gate instead of failing later with a raw
`AttributeError`/`TypeError`. Only an *absent* `depends_on` (not merely a
falsy one) yields no dependencies. Each short-form list element must itself
be a string (the same list/map YAML slip that trips up
`environment`/`command`), checked before the list would otherwise crash raw
(`TypeError: unhashable type`) when passed to `dict.fromkeys`. `extends.py`'s
own list-to-mapping normalization enforces the identical string check ahead
of `validate()`, since `extends` resolution runs first (see Extends, above),
and injects the same `service_started` default explicitly (`{dep:
{"condition": "service_started"}}`, not `{dep: {}}`) so a short-form entry
that merges through `extends` stays the valid, Docker-default-matching
document it would be standalone rather than becoming a condition-less
long-form entry the gate now refuses.

The long form's `condition` value gets the same treatment: it must be a
string, so a mapping or list condition raises there instead of crashing raw
at the later `condition not in DEPENDS_ON_CONDITIONS` set-membership check
in `parsing.py`. This check lives in `graph.py`, not `parsing.py`, so every
caller of `depends_on` — not only `validate()` — gets the same protection.

A long-form entry is a strict schema (Task 12, 2026-07-15): exactly three
keys — `condition` (read above), `restart` and `required` (both a plain
boolean; neither is read for effect, since podman has no equivalent of
either, but a malformed value is still a document Docker refuses) — plus the
usual `^x-` extension pattern; an unrecognized key raises. `restart`/
`required` share the six top-level boolean keys' quoted-boolean acceptance: a
literal `"true"` (or any other YAML-1.1 boolean spelling) is accepted, since
Docker itself casts it, and a genuine `${VAR}` reference is carved out too,
since its verdict is a fact about the reading shell, not the document.

Separately, at emit time, `startup_order` (`compose2pod/graph.py`) walks the
target's `depends_on` closure and raises if a dependency names a service
absent from the document ("unknown dependency"), if that walk finds a cycle
("dependency cycle"), or if `--target` itself names a service absent from
the document ("target service not found").

## YAML anchors and merge keys

Anchors (`&name` / `*name`) and the merge key (`<<:`) need no handling in
compose2pod: PyYAML's `safe_load` resolves them at load time, so
`validate()` and `emit` see already-merged service mappings. JSON input has
no anchors but can still carry literal `x-` extension keys, handled
identically.
