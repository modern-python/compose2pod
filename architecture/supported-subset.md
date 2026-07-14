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

## Top-level keys

- **Supported:** `services` (required — a non-mapping value raises, an empty
  mapping raises "no services defined"), `version`, `name`, `networks`,
  `volumes`, `secrets`, `configs` (see Stores, below).
- **Ignored (warns):** `networks` — every service shares the pod's single
  network namespace, so top-level network definitions have no effect.
  `volumes` — podman creates named volumes implicitly on first reference, so
  the top-level block's drivers/options are never read (see Volumes, below).
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
and the service's own `x-`-prefixed keys; each top-level `secrets`/`configs`
definition, swept by name for the same reason. Three service keys key their
mapping form by another entity's *identifier* rather than by content —
`depends_on` (dependency name), `networks` (network name), `ulimits` (limit
category name) — and get the same name-not-content treatment
(`_sweep_identifier_map`): the identifier is checked regardless of what it
looks like (`x-dep` is a real identifier, not an extension field), and only
its *value* is swept with the ordinary `x-`-skipping walk.

**Skipped**, because compose2pod never reads or emits from these regions, so
a non-string key inside one can never reach the generated script: `x-`
blocks (top-level and per-service — user payload by design, though the `x-`
key itself is still checked); `build`'s own contents (never read — see
`build`, below); and the ignored top-level `networks`/`volumes` blocks.

Rejecting a non-string key **matches Docker**, which refuses one too
(`non-string key in services.app.environment: 3306`). So `environment:
{3306: db}` and `{true: x}` are refused by both.

What Docker does *not* see is a bare `on:` / `off:` / `yes:` / `no:` as a
non-string key, because it parses **YAML 1.2**, where each of those is an
ordinary string. PyYAML implements YAML **1.1**, where each is a *boolean* —
so the CLI loads YAML with a 1.2-style boolean resolver (`_build_yaml_loader`,
`compose2pod/cli.py`), and only `true`/`false` resolve as booleans. Without it,
`environment: {on: 1}` would arrive as the key `True` and be refused — a file
Docker runs — and the *value* `SSL: on` would reach the container as `SSL=true`
rather than `SSL=on`. The spelling cannot be recovered downstream: once the
loader has resolved `on` to `True`, `"on"` is gone.

A genuine boolean *value* still renders lowercase (`DEBUG: true` → `DEBUG=true`,
see `_render_scalar`), which is what Docker renders too.

The rejection runs up front, not key by key, because some downstream
readers crash raw on a non-string key (`sorted()`, `str.startswith`, the
secret/config name regex) while others silently f-string it into a flag
value, leaking a Python repr into the script (`key_value_pairs`,
`extra_host_pairs`, `_ulimit_args`, `_sysctl_pairs`) — corruption, not a
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
  `build` section is accepted but its contents (context, dockerfile, args)
  are never read — `image_for` (`compose2pod/emit.py`) runs the CI image
  supplied via `--image` for any service that has one.
- **Ignored (warns):** `ports`, `restart`, `stdin_open`, `tty`,
  `stop_signal`, `stop_grace_period`, `profiles` — meaningless or irrelevant
  inside a single shared-namespace pod. `stop_signal`/`stop_grace_period`
  are inert because the script force-removes the pod (`podman pod rm -f`)
  rather than gracefully stopping a container. `profiles` is inert because
  the run set is fixed by `--target` plus its `depends_on` closure, not by
  profile activation: targeting a service runs it regardless of profile (as
  Compose does), and a service outside the closure never runs — so if the
  target `depends_on` a member Compose would leave in a disabled profile,
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
| `pull_policy` | `--pull` (`if_not_present` → `missing`, rest verbatim) | enum: `always`/`never`/`missing`/`if_not_present` |
| `ulimits` | `--ulimit` (`name=value` or `name=soft:hard`) | mapping, see below |

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
- **`build`:** a string or mapping — its contents are never read (see
  `image`, above), only the shape, since a document `docker compose config`
  refuses (e.g. `build: 3`) must still be refused here.
- **`command`:** string or list, each list element a string. String form
  runs via `/bin/sh -c`; list form is argv tokens. Any other shape (e.g. a
  mapping, or a list containing one — the classic YAML list/map slip)
  raises rather than reaching `podman run` mangled. `command: null` is
  accepted, treated as absent — unlike `entrypoint: null`, below (a
  narrower, pre-existing divergence between the two keys' validators).
- **`entrypoint`:** string or list, each list element a string, mirroring
  `command`; `entrypoint: null` raises rather than being treated as absent.
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
- **`environment`:** list (`- KEY=value`, `- KEY`) or mapping (`KEY: value`,
  `KEY:`) — the same shape rule as the registry's map-shaped keys. A null
  mapping value means "pass `KEY` through from the host", emitted as a bare
  `-e KEY`, same as the list form `- KEY`. A boolean value (`DEBUG: true`)
  is normalized like Docker: `-e DEBUG=true`, not the Python repr `-e
  DEBUG=True`.
- **`env_file`:** a string or list of strings. Each resolved path passes
  through `--project-dir` when relative, then is emitted as `--env-file`.
- **`tmpfs`:** a string or list of strings, each `<path>` or
  `<path>:<options>` (e.g. `/tmp:mode=1777`), passed through verbatim as
  `--tmpfs <value>` — Compose's short syntax maps directly onto podman's
  own flag. No format validation beyond shape; a malformed option string
  surfaces as a podman error at run time.
- **`hostname` and `container_name`:** each made resolvable to `127.0.0.1`
  like a network alias (added to the shared `--add-host` set, see Pod-level
  options), so other services can reach the service by either name. The pod
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
  short (list) form carries none. Must be a list or mapping. A long-form
  *value* that isn't itself a mapping (e.g. `networks: {default: true}`) is
  lenient, not rejected — it just contributes no aliases. When present,
  `aliases` must be a list of strings. Every network a service names must be
  declared in the top-level `networks` block, or `validate()` raises
  ("refers to undefined network") — the top-level block's *contents* are
  still ignored (see Top-level keys, above), only the reference is checked,
  because a document naming an undeclared network is one `docker compose
  config` refuses.

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
  with the alias/hostname set) `--add-host` respectively.
- **Value shapes:** `dns`/`dns_search` accept a string or list of strings;
  `dns_opt` accepts a list of strings only — Docker refuses a bare string
  there (`dns_opt: "ndots:2"`) even though it accepts one for `dns` and
  `dns_search`; the asymmetry is Docker's, measured, not compose2pod's.
  `sysctls` accepts a mapping (`key: value`) or a list of
  `"key=value"` strings, each value a string or number; `extra_hosts`
  accepts a list or a mapping (`host: ip`). A list entry divides on `=`
  (Compose's documented separator, `- somehost=162.242.195.82`) or on the
  legacy `:` (`- somehost:162.242.195.82`) — `=` wins when both appear,
  because an IPv6 address is itself full of colons and splitting on the first
  one would tear it apart (`myhostv6=::1`); the colon form splits on the
  *first* colon only, so `myhost:::1` works too. Every reader — the gate, the
  emitter, and the `extends` merge — goes through the one shared
  `keys.split_extra_host`, so they cannot disagree about where an entry
  divides. An entry with neither separator has no address and raises, rather
  than emitting a malformed `--add-host`. A `${VAR}` inside a
  value stays live at run time (see Variable interpolation, below) and
  counts toward `referenced_variables`.
- **Aggregation is closure-scoped:** computed over the target's dependency
  closure (`startup_order`), exactly like stores. `dns`/`dns_search`/
  `dns_opt` are unioned (deduplicated, first-seen order); `sysctls` are
  unioned by key, and two closure services setting the same key to
  different values is refused (`conflicting sysctl ...`) rather than
  resolved last-writer-wins. `--add-host` is seeded from the alias/hostname
  set of the closure's services, then layered with each closure service's
  `extra_hosts`; a host name landing on two different addresses is refused
  the same way (`conflicting host ...`). An alias/hostname `--add-host`
  entry stays a plain unquoted token; an `extra_hosts` entry is
  `${VAR}`-live.

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
- **Requires Podman >= 6.0.0.** Before 6.0.0, a container stopping inside a
  multi-container pod wiped `/etc/hosts` for every container in the pod.
  Because `--add-host` here is the pod's *only* source of `/etc/hosts`
  entries, a `service_completed_successfully` dependency (a container that
  runs to completion and exits, e.g. a migration step) triggers the bug and
  erases name resolution for every service started after it. Confirmed
  present on 5.8.1, fixed on 6.0.0/6.0.1. The generated script checks
  `podman version` at startup and warns on stderr (without blocking) below
  major version 6, so the requirement is visible at the point of failure,
  not only in the docs. See `README.md`'s Requirements section.
- **Non-goals:** per-service DNS/sysctls — impossible inside a
  shared-namespace pod, not a compose2pod limitation; last-writer-wins on a
  sysctl or host conflict — refused instead.

## Resource limits

Compose exposes container resource limits two ways — legacy scalar service
keys and the Compose-spec `deploy.resources` block — and compose2pod honors
both, refusing loudly on overlap rather than picking a precedence.

| Legacy key | Podman flag | Shape |
|---|---|---|
| `mem_limit` | `--memory` | number or string |
| `memswap_limit` | `--memory-swap` | number or string |
| `mem_reservation` | `--memory-reservation` | number or string |
| `mem_swappiness` | `--memory-swappiness` | number or string |
| `cpus` | `--cpus` | number or string |
| `cpu_shares` | `--cpu-shares` | number or string |
| `cpu_quota` | `--cpu-quota` | number or string |
| `cpu_period` | `--cpu-period` | number or string |
| `cpuset` | `--cpuset-cpus` | number or string |
| `pids_limit` | `--pids-limit` | number or string |
| `shm_size` | `--shm-size` | number or string |
| `oom_score_adj` | `--oom-score-adj` | number or string |
| `oom_kill_disable` | `--oom-kill-disable` (bare, only if true) | boolean |

These twelve number-scalar keys pass their value through unchanged (a
`${VAR}` inside stays live at run time); a non-number, non-string value —
including a bool — is refused. `oom_kill_disable` is the one boolean-typed
exception in this group, validated as an actual bool like
`read_only`/`init`/`privileged`.

- **`deploy.resources`** (`compose2pod/resources.py`): under `deploy`, only
  `resources` is honored — any other subkey (`replicas`, `placement`,
  `restart_policy`, ...) raises, and unrecognized keys within `resources`,
  `limits`, or `reservations` raise the same way. `limits.cpus`/
  `limits.memory`/`limits.pids` map to `--cpus`/`--memory`/`--pids-limit`,
  each the same number-or-string shape as the legacy keys above;
  `reservations.memory` maps to `--memory-reservation`. `reservations.cpus`
  and `reservations.devices` have no podman equivalent and are refused
  outright, regardless of value.
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
- **`interval`:** parsed to whole seconds by `interval_seconds`. Supported
  forms: a bare number of seconds (`30`, `"30"`, `"30s"`), minutes
  (`"2m"`), and milliseconds (`"500ms"`). Compound durations (`"1h30m"`)
  and hour suffixes (`"1h"`) are not parsed — each raises rather than being
  silently truncated or misinterpreted. An explicit `null` (or an absent
  `interval`) defaults to 1 second. The result floors at 1 second: the
  interval only paces the script's `podman healthcheck run` polling loop,
  which has no sub-second resolution, so `"500ms"` and `0` both poll once a
  second.
- **`timeout`, `retries`, `start_period`:** each must be a number (int or
  float) or a string. A mapping or list raises rather than reaching its
  `--health-*` flag as a literal Python `repr()`.
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

Short syntax only; the long mapping form raises. The `volumes` key itself
must be a list — a bare string raises, rather than being destructured one
character at a time. A `source:target` entry is one of two kinds, told
apart by whether `source` starts with `.` or `/`:

- **Bind mount** (`source` starts with `.` or `/`): the host path, resolved
  against `--project-dir` when relative.
- **Named volume** (`source` is a bare identifier, e.g.
  `pgdata:/var/lib/...`): passed through verbatim as `-v <name>:<target>` —
  no format validation, no path translation. Podman creates it implicitly
  with default options on first reference (same as plain `podman run -v`,
  no explicit `podman volume create` step needed), and it persists on the
  host after the pod is removed, identical to `docker compose down` without
  `-v`. The top-level `volumes:` block (declaring drivers/options) is
  accepted but ignored — a non-default `driver`/`driver_opts` or `external:
  true` (which Compose treats as "must already exist") has no effect, since
  podman's implicit creation is the only path taken either way.

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
  be a string; `uid`/`gid`/`mode` must each be an int or string, not a bool
  (`bool` is technically an `int` in Python, so a naive check would let one
  through).
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
  `command`, `entrypoint`, `environment`, `env_file`, `volumes`, `tmpfs`,
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
last two are emitted as literal `--add-host host:127.0.0.1` entries, not
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
service names (short form, each defaulting to `service_started`) or a
mapping of service name to a per-dependency mapping (long form, read for
`condition`). Anything else — a bare string (including the empty string;
`or {}`'s falsy default used to swallow it silently as "no dependencies"),
a number, a mapping whose value isn't itself a mapping — raises at the gate
instead of failing later with a raw `AttributeError`/`TypeError`. Only an
*absent* `depends_on` (not merely a falsy one) yields no dependencies. Each
short-form list element must
itself be a string (the same list/map YAML slip that trips up
`environment`/`command`), checked before the list would otherwise crash raw
(`TypeError: unhashable type`) when passed to `dict.fromkeys`. `extends.py`'s
own list-to-mapping normalization enforces the identical check ahead of
`validate()`, since `extends` resolution runs first (see Extends, above)
and would otherwise crash raw on the same input.

The long form's `condition` value gets the same treatment: it must be a
string, so a mapping or list condition raises there instead of crashing raw
at the later `condition not in DEPENDS_ON_CONDITIONS` set-membership check
in `parsing.py`. This check lives in `graph.py`, not `parsing.py`, so every
caller of `depends_on` — not only `validate()` — gets the same protection.

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
