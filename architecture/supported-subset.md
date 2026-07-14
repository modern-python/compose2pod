# Supported compose subset

compose2pod converts an honest subset of Docker Compose and refuses the rest
loudly rather than silently dropping behavior. `validate()`
(`compose2pod/parsing.py`) is the gate: anything it does not recognize either
warns (ignored, behavior-neutral inside a single pod) or raises
`UnsupportedComposeError`.

`emit_script()` (exported from `compose2pod`) and `referenced_variables()`
(public as `compose2pod.emit.referenced_variables`) both project the same
internal `_plan` traversal; `_plan` calls `validate()` itself, before reading
anything else out of `compose`, and discards the returned warnings (the CLI
surfaces its own copy from its own `validate()` call). A library caller
therefore cannot
reach either public entry point with a document `validate()` would reject —
calling `emit_script()`/`referenced_variables()` directly, without calling
`validate()` first, is exactly as safe as the CLI path, by construction of
the shared `_plan` call site, not by convention.

## Top-level keys

- **Supported:** `services` (required, non-empty mapping of service name to
  service definition — a non-mapping `services` value, e.g. a bare string or
  list, raises inside `validate()` itself), `version`, `name`, `networks`,
  `volumes`, `secrets`, `configs`.
- **Ignored (warns):** `networks` — all services share the pod's single
  network namespace, so top-level network definitions have no effect.
- **Extension fields:** any key prefixed `x-` is accepted and ignored
  silently, per the Compose spec. This is what lets a document hold shared
  config in a top-level `x-*` block for reuse via YAML anchors.
- **Every mapping key must be a string, in every region `validate()`
  actually reads from or emits into.** PyYAML routinely produces a
  non-string key — a bare `3:` parses as an int, and under YAML 1.1 a bare
  `on:`/`off:`/`yes:`/`no:` parses as a bool. `validate()` sweeps these
  regions once, recursively, before running any other check
  (`_sweep_document`/`_sweep_service`/`_require_string_keys_deep`,
  `compose2pod/parsing.py`, built on `require_string_keys`,
  `compose2pod/keys.py`) and rejects a non-string key found in any of
  them — `UnsupportedComposeError` names the offending key and its
  location. Swept: the top-level document's own keys; the `services`
  mapping's own keys (service *names*) — always, regardless of what a name
  looks like, since `validate()` treats every entry in `services` as a real
  service with no `x-` filter (a service literally named `x-web` is swept
  like any other service, not skipped as an extension field); each
  service's body — every structural key, `healthcheck`, `deploy` and its
  nested `resources`/`limits`/`reservations`, per-service `secrets`/
  `configs` references, and so on — except `build`'s own contents and the
  service's own `x-`-prefixed keys; and each top-level `secrets`/`configs`
  definition (read by `stores.py`), by name, for the same reason a service
  name is swept regardless of what it looks like.

  Three service keys key their mapping form by another entity's
  *identifier* rather than by content — `depends_on` (a dependency's
  service name), `networks` (a network's name), and `ulimits` (a
  resource-limit category's name) — and get the identical name-not-content
  treatment (`_sweep_identifier_map`, `compose2pod/parsing.py`): each
  identifier is checked regardless of what it looks like (a dependency
  literally named `x-dep` is a real identifier, not an extension field), and
  only *its* value — ordinary content from that point on, e.g. `ulimits`'
  nested `{soft, hard}` mapping — is swept with the ordinary `x-`-skipping
  walk.

  **Skipped**, because compose2pod never reads or emits from these
  regions, so a non-string key inside one can never reach the generated
  script: `x-` blocks (top-level and per-service) — Compose extension
  fields legitimately hold arbitrary payloads (e.g. YAML anchor sources
  reused via `<<:`) that compose2pod accepts and ignores by design, so
  their contents are never walked (the `x-` key itself is still checked,
  trivially — its own name has to look like `x-...`); `build`'s own
  contents (`context`/`dockerfile`/`args`, never read — see `build` below);
  and the ignored top-level `networks`/`volumes` blocks (accepted, but
  their contents are never read — see Volumes below and the Pod-level
  options section for why top-level `networks` has no effect).

  Two distinct downstream consumer classes motivate rejecting a non-string
  key up front rather than one key at a time within a swept region:
  mapping-key readers that crash raw otherwise (`sorted()`,
  `str.startswith`, the secret/config name regex), and mapping-key
  consumers that don't crash but f-string-interpolate the key straight into
  a flag value, silently leaking a bool's or int's Python repr into the
  emitted script instead (`keys.key_value_pairs` — `environment`/
  `labels`/`annotations`, `keys.extra_host_pairs` — `extra_hosts`,
  `keys._ulimit_args` — `ulimits`, `pod._sysctl_pairs` — `sysctls`). The
  second class is silent corruption, not a crash.

  This is a deliberate divergence from Docker for keys that *are* swept:
  `environment: {3306: db}` is valid Compose and Docker accepts it, but
  compose2pod refuses it. Docker parses Compose as YAML 1.2, where a bare
  `3306`/`on`/`off` stays the string it looks like, so Docker never
  observes a non-string key at all. Normalizing Python's `int`/`bool` back
  into a key string here would not reproduce that: unlike a boolean *value*
  (`DEBUG: true`, normalized to the string `"true"` like Docker does — see
  `_render_scalar` below), a boolean or int *key* has no single correct
  string form to normalize to (`True` → `"on"`? `"true"`? `"True"`?). A
  non-string key is a YAML-1.1 accident, not intentional Compose; anyone
  who means the literal string `on` writes `"on"`. Docker also accepts a
  non-string key in `build`'s `args` or a top-level `volumes` block's
  `driver_opts` — since compose2pod never reads either, it accepts them
  too, matching Docker rather than diverging from it there.

  A handful of module entry points that are also called directly (not only
  reached through `validate()`) keep their own `require_string_keys` call
  in addition to the sweep, as defense at their own boundary and as
  belt-and-braces for two service-level checks the sweep also covers:
  `_validate_service` and `_validate_service_healthcheck`
  (`compose2pod/parsing.py`), `resources.validate_deploy`'s four checks
  (`deploy` and its nested `resources`/`limits`/`reservations`), and
  `stores.py`'s three checks (a secret/config definition's keys, a
  long-form reference's keys, and the top-level `secrets`/`configs`
  block's own keys). These are redundant only when reached through
  `validate()`; each is still load-bearing for a caller that invokes that
  function directly.
- Everything else raises.

## Service keys

- **Supported:** `image`, `build`, `command`, `entrypoint`, `environment`,
  `env_file`, `volumes`, `healthcheck`, `depends_on`, `networks`, `hostname`,
  `container_name`, `tmpfs`, `secrets`, `user`, `working_dir`, `group_add`,
  `labels`, `read_only`, `init`, `privileged`, `cap_add`, `cap_drop`,
  `security_opt`, `platform`, `devices`, `annotations`, `extra_hosts`,
  `pull_policy`, `ulimits`.
  compose2pod never builds: a `build` section is accepted but its contents
  (context, dockerfile, args) are not read — `image_for` (`compose2pod/emit.py`)
  runs the CI image supplied via `--image` for any service that has one.
- **Service-key registry:** the declarative, uniformly-shaped flag keys —
  `user`, `working_dir`, `platform`, `init`, `read_only`, `privileged`,
  `group_add`, `cap_add`, `cap_drop`, `security_opt`, `devices`, `labels`,
  `annotations`, `pull_policy`, `ulimits` — are defined once, each as a
  `(validate, emit)` pair, in the **service-key registry** (`SERVICE_KEYS` in
  `compose2pod/keys.py`); see `architecture/glossary.md` for the service-key
  spec / service-key registry / structural key terms. `extra_hosts` is a
  supported key but not a registry entry — it is pod-level, see Pod-level
  options below.
  The remaining keys documented below are **structural keys**, handled
  outside the registry because their `emit` needs `project_dir`, spans
  multiple keys, or occupies the image/command slot.
  The list-shaped registry keys (`group_add`, `cap_add`, `cap_drop`,
  `security_opt`, `devices`) share one validator, `keys._validate_list`; the
  map-shaped ones (`labels`, `annotations`) and the pod-level `extra_hosts`
  share `keys.validate_map`. Both require every list element to be a string
  and every mapping value to be a string, number, boolean, or null — a
  non-string element or a non-scalar value (e.g. `cap_add: [{NET_ADMIN:
  true}]`, a list entry that is itself a mapping) is rejected rather than
  `str()`'d/`repr()`'d straight into the flag value. A boolean mapping value
  (`labels: {enabled: true}`) is deliberately accepted, not rejected: it is
  normalized the way `docker compose config` normalizes it, rendered as the
  lowercase string `true`/`false` (`keys._render_scalar`, shared by
  `key_value_pairs` and `extra_host_pairs`) rather than Python's
  `str(True) == "True"`.
- **`image`:** a string; `image_for` (`compose2pod/emit.py`) reads it verbatim
  when the service has no `build`. A service must set at least one of `image`
  or `build`; a service with neither, or a non-string `image` while `build` is
  absent, raises at the gate (`_validate_image`, `compose2pod/parsing.py`). A
  non-string `image` alongside `build` is accepted, since `image_for` never
  reads it in that case — the CI image always wins.
- **`command`:** string or list. List form is argv tokens; string form runs
  via `/bin/sh -c`. Any other shape (e.g. a mapping) raises at the gate
  (`_validate_command`, `compose2pod/parsing.py`) — a mapping is rejected
  outright rather than reaching `podman run` with only its keys emitted as
  bare tokens and its values silently discarded. Each list element must
  itself be a string; a non-string element (e.g. `command: [{run: tests}]`,
  the same list/map YAML slip that trips up `environment`) raises the same
  way, rather than being `str()`'d into a single mangled argv token.
  `command: null` is accepted, treated the same as an absent `command` —
  unlike `entrypoint: null` (see below), a narrower pre-existing divergence
  between the two keys.
- **`environment`:** list form (`- KEY=value`, `- KEY`) or mapping form
  (`KEY: value`, `KEY:`). A null mapping value (`KEY:`) means "pass `KEY`
  through from the host", emitted as a bare `-e KEY` exactly like the list
  form `- KEY`.
  The key itself must be a list or mapping; any other shape (e.g. a bare
  string) raises at the gate. List elements must themselves be strings, and
  mapping values must be a string, number, boolean, or null; the commonest
  violation is the classic YAML slip of mixing list and map form
  (`environment: [{KEY: value}]` instead of `- KEY=value`), rejected rather
  than emitting the literal `-e "{'KEY': 'value'}"` — both rules are
  enforced by the shared `keys.validate_map` (`compose2pod/keys.py`). A
  boolean mapping value (`DEBUG: true`) is valid Compose and is normalized
  like Docker: emitted as `-e DEBUG=true` (lowercase), not the Python repr
  `-e DEBUG=True`.
- **`env_file`:** a string or a list. Any other shape raises
  (`_validate_env_file`, `compose2pod/parsing.py`). Each list element must
  itself be a string; a non-string element (e.g. `env_file: [5]`) raises the
  same way. Each resolved path is passed through `--project-dir` when
  relative, then emitted as a `--env-file` flag (`compose2pod/emit.py`).
- **`entrypoint`:** string or list. List form is exec form; string form is
  shell form (`/bin/sh -c <string>`), mirroring `command`. Emitted as
  `--entrypoint <first-token>` with the remaining tokens placed ahead of the
  command after the image, so `podman run --entrypoint a IMAGE b <command>`
  runs `a b <command>` -- no JSON needed. Each list element must itself be a
  string, the same rule and rejection as `command`'s list form. Unlike
  `command`, `entrypoint: null` raises rather than being treated as absent —
  a pre-existing, narrower divergence in how the two keys' validators handle
  an explicit `null` (`_validate_command` checks for `None` first and treats
  it as "absent"; `_validate_entrypoint` does not). A string (shell-form) entrypoint
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
- **`extra_hosts`:** list (`- host:ip`) or mapping (`host: ip`), pod-level like
  `dns`/`sysctls` — see the Pod-level options section below. Distinct from
  the alias/hostname set in one respect: alias/hostname resolution is
  always fixed at `127.0.0.1`, while `extra_hosts` carries a user-specified
  address; IPv6 values keep their colons.
- **`pull_policy`:** a validated enum mapped to podman's `--pull`
  (`if_not_present` → `missing`; `always`/`never`/`missing` pass through),
  emitted literally. `build` and unknown values are rejected — compose2pod
  never builds, so a `build` pull policy cannot be honored.
- **`ulimits`:** a mapping of limit name to either a scalar (`nproc: 65535` →
  `--ulimit nproc=65535`, podman sets soft = hard) or a `{soft, hard}` mapping
  (`nofile: {soft, hard}` → `--ulimit nofile=soft:hard`). A mapping value must
  have exactly `soft` and `hard`, each an int or string; other shapes are
  rejected. A boolean scalar or a boolean `soft`/`hard` bound is rejected too
  — `bool` is technically an `int` in Python, so a naive `isinstance(spec,
  int | str)` would otherwise let one through and emit the literal
  `--ulimit nofile=True`. Unlike `environment`'s boolean (normalized to a
  string, see above), a boolean ulimit has no sensible Docker-equivalent
  normalization, so it is rejected rather than coerced. (`sysctls`, by
  contrast, is pod-level rather than per-container — see the
  Pod-level options section below.)
- **`labels`:** list (`- KEY=value` / `- KEY`) or mapping (`KEY: value` / `KEY:`),
  emitted as repeated `--label`. A null value means an empty label
  (`--label KEY`) -- the same emitted shape as `environment`'s null but a
  distinct meaning (labels have no host-passthrough).
- **`tmpfs`:** a string or list of strings, each `<path>` or
  `<path>:<options>` (e.g. `/tmp:mode=1777`), passed through verbatim as
  `podman run --tmpfs <value>` — Compose's short syntax maps directly onto
  podman's own `--tmpfs CONTAINER-DIR[:OPTIONS]` flag, so no translation is
  needed. The key itself must be a string or list — a non-string/non-list
  value (e.g. a mapping) raises; each list element must itself be a string, a
  non-string element (e.g. `tmpfs: [5]`) raises the same way
  (`_validate_tmpfs`, `compose2pod/parsing.py`). No format validation beyond
  shape, so a malformed option string inside an accepted string/list surfaces
  as a podman error at run time.
- **`hostname` and `container_name`:** both are made resolvable to
  `127.0.0.1` like a network alias (added to the shared `--add-host` set), so
  other services can reach the service by either name. The pod shares the UTS
  namespace, so a service's own hostname is the pod's, and the actual podman
  container is always named `{pod}-{service}` regardless of `container_name`
  (used internally for `podman cp`, healthcheck polling, and target-container
  diagnostics) — only name *resolution* is meaningful to other services, and
  no per-container `--hostname` or renamed `--name` is emitted. Each must be a
  string when present; a non-string raises (`_host_names`,
  `compose2pod/graph.py`).
- **Per-service `networks`:** long (mapping) form contributes each entry's
  `aliases` to the same resolvable-name set as `hostname`/`container_name`;
  short (list) form carries no aliases (a bare network name has none to
  contribute). The key must be a list or mapping — anything else raises. A
  long-form *value* that isn't itself a mapping (e.g. `networks: {default:
  true}`) is lenient, not rejected: it simply contributes no aliases, since
  only a mapping value can carry an `aliases` list. When present, `aliases`
  itself must be a list of strings — a non-list value (e.g. a bare string)
  would otherwise be destructured character-wise into the resolvable-name
  set, and a non-string element crashes downstream the same way a malformed
  `volumes`/`tmpfs` element does; both raise at the gate instead
  (`_host_names`, `compose2pod/graph.py`).
- **Ignored (warns):** `ports`, `restart`, `stdin_open`, `tty`, `stop_signal`,
  `stop_grace_period`, `profiles` — meaningless or irrelevant inside a single
  shared-namespace pod. `stop_signal`/`stop_grace_period` are inert because the
  script force-removes the pod (`podman pod rm -f`) and never gracefully stops a
  container. `profiles` is inert because compose2pod's run set is fixed by
  `--target` plus its `depends_on` closure, not by profile activation: targeting
  a service by name runs it regardless of its profile (as Compose does), and a
  service outside the closure never runs. One divergence follows: if the target
  `depends_on` a member Compose would leave in a disabled profile, compose2pod
  runs it anyway (the closure is authoritative) — more permissive than Compose,
  never a silent drop.
- **Extension fields:** any `x-`-prefixed service key is accepted and ignored
  silently.
- Everything else raises.

## Extends

- **Resolution timing:** `extends` is resolved by `resolve_extends`
  (`compose2pod/extends.py`) as a pre-validation flattening step, called from
  `cli.py` immediately before `validate()` — the rest of the pipeline
  (`validate()`, `emit_script()`) never sees an `extends` key.
- **Supported form:** only the mapping form `extends: {service: <name>}`;
  `service` must name another service in the same document. Resolution is
  transitive (a chain of `extends` is fully flattened) and cycle-checked — an
  `extends` cycle raises `UnsupportedComposeError`.
- **Merge rules**, applied per key when both the base service and the local
  (extending) service define it:
  - **Mapping-merge, local wins:** `environment`, `labels`, `annotations`,
    `extra_hosts`, `ulimits`, `healthcheck`, `depends_on` — base's and local's
    keys are combined into one mapping, with local's value winning on
    collision.
  - **Sequence-concatenate, base then local:** `cap_add`, `cap_drop`,
    `security_opt`, `devices`, `group_add`, `secrets`, `configs`, `volumes`,
    `tmpfs`, `env_file` — base's list is followed by local's list, unchanged.
  - **Override, local replaces:** every other key, including `command` and
    `entrypoint` (argv replaced wholesale, never concatenated) — this also
    covers unknown keys, which `validate()` then rejects downstream exactly
    as it would without `extends`.
  - **Normalization before merge:** list-form `environment`
    (`- KEY=value` / `- KEY`) and list-form `depends_on` (a bare
    service-name list) are normalized to mappings before the mapping-merge;
    scalar-form `tmpfs`/`env_file` (a single string) are normalized to a
    one-element list before the concatenation.
- **Refused loudly** (`UnsupportedComposeError`), rather than guessed at:
  cross-file `extends: {file: ..., service: ...}`; a bare-string `extends`
  (or any other non-mapping value); an unrecognized key under `extends`
  other than `service`; a non-string `service`; an `extends` `service`
  naming a service that doesn't exist in the document; and a merge across
  incompatible forms — a mapping-merge key that is neither a mapping nor a
  list-form `environment`/`depends_on`, or a sequence-concatenate key that is
  neither a list nor a scalar string.
- **Divergences from Compose:**
  - `environment` and `depends_on` accept list form directly in extends.py's
    own merge (`_as_mapping`, `compose2pod/extends.py`); `extra_hosts` and
    `healthcheck` do not — list form on a merged side is refused as an
    incompatible form for those two. `labels`, `annotations`, and `ulimits`
    instead route through their own `SERVICE_KEYS` merge policy
    (`_merge_map`, `compose2pod/keys.py`), which uses the same
    list-accepting `pairs_to_mapping` normalizer that `environment` uses —
    so list form on a merged side *is* coerced to a mapping for these three,
    not refused. Every one of these normalizers rejects a non-string list
    element (e.g. `labels: [{BAD: "x"}]`) rather than laundering it into a
    mapping key via `str()`: `pairs_to_mapping` (`compose2pod/keys.py`) is
    the single function both paths share, so this rule holds identically
    whichever merge route a key takes.
  - Short-form `volumes` are concatenated rather than merged by target path;
    podman resolves duplicate mounts at run time rather than compose2pod
    deduplicating them at generation time.
  - Referenced resources (top-level `volumes`, `networks`, `secrets`,
    `configs`) are not auto-imported by `extends` — as in Compose, the
    extending service must declare what it needs.

## Pod-level options

A Podman pod shares one network namespace across every container joined to
it, so a handful of Compose keys cannot be per-container `podman run` flags.
compose2pod hoists them onto `podman pod create` instead
(`compose2pod/pod.py`) — the tool's only pod-create flags.

- **Supported:** `dns`, `dns_search`, `dns_opt`, `sysctls`, `extra_hosts` —
  mapped to `--dns`, `--dns-search`, `--dns-option`, `--sysctl`, and (merged
  with the alias/hostname set) `--add-host` respectively (`_DNS_KEYS`,
  `pod.py`).
- **Aggregation is closure-scoped:** `pod_create_flags(services, order,
  hosts)` is called with `order` — the target's dependency closure
  (`startup_order`) — exactly like other closure-scoped constructs (secrets,
  configs). `dns` / `dns_search` / `dns_opt` are unioned across the closure
  (deduplicated, first-seen order); `sysctls` are unioned by key, and two
  services in the closure setting the same key to different values is
  refused (`UnsupportedComposeError: conflicting sysctl ...`) rather than
  resolved by last-writer-wins. `--add-host` is seeded from the
  alias/hostname set (`hosts`, computed document-wide by `graph.hostnames`,
  not closure-scoped — pre-existing, orthogonal behavior), then layered with
  each closure service's `extra_hosts` (closure-scoped like `dns`/`sysctls`);
  a host name landing on two different addresses — across two services'
  `extra_hosts`, or between an `extra_hosts` entry and an alias's fixed
  `127.0.0.1` — is refused (`UnsupportedComposeError: conflicting host ...`),
  the same refuse-rather-than-guess rule as the `sysctls` conflict.
- **Value shapes:** `dns` / `dns_search` / `dns_opt` accept a string or a list
  of strings; `sysctls` accepts a mapping (`key: value`) or a list of
  `"key=value"` strings, each value a string or number. A `${VAR}` inside a
  value is wrapped in `Expand` like other interpolated fields, so it stays
  live at run time and counts toward `referenced_variables` — the generated
  script's own shell expands it when it runs, not compose2pod at generation
  time. `--add-host` entries render differently by source: an alias/hostname
  entry stays a plain unquoted token (pre-existing behavior, unchanged by
  this move), while an `extra_hosts` entry goes through `Expand` (quoted,
  `${VAR}`-live) — same as before it was per-service.
- **Pod-wide divergence:** unlike every other service key, these apply to
  every container in the pod once emitted — including services that never
  declared them — because the pod shares one `/etc/resolv.conf`, one sysctl
  set, and one `/etc/hosts`. `validate()` (`compose2pod/parsing.py`) is
  target-agnostic shape validation over the whole document: whenever any
  service anywhere declares `dns` / `dns_search` / `dns_opt` / `sysctls` /
  `extra_hosts` (`uses_pod_options`), it emits the warning "dns/sysctls/
  extra_hosts apply pod-wide -- all containers in the pod share one
  /etc/resolv.conf, sysctl set, and /etc/hosts", regardless of whether that
  service turns out to be inside the target's closure. Conversely, at emit
  time a `dns` / `sysctls` / `extra_hosts` declaration on a service outside
  the target's closure is silently ignored by `pod_create_flags` — no flag
  is emitted for it, since that service is never run.
- **Requires Podman >= 6.0.0.** Before 6.0.0, Podman had a bug where a
  container stopping inside a multi-container pod wiped `/etc/hosts` for
  every container in the pod, not just the one that stopped. Because
  `--add-host` here is the pod's *only* source of `/etc/hosts` entries (moved
  off per-service `podman run` for exactly this pod-wide reason), a
  `service_completed_successfully` dependency — a container that runs to
  completion and exits, e.g. a migration step run via `podman run --rm` —
  triggers the bug and erases name resolution for every service started
  after it. Confirmed present on 5.8.1, fixed on 6.0.0/6.0.1 (verified by
  reproducing the exact failure end-to-end against real Podman). See
  `README.md`'s Requirements section. The generated script itself checks
  `podman version` at startup and warns on stderr (without blocking) when
  it detects a major version below 6, so the requirement is visible at the
  point of failure, not only in the docs.
- **Non-goals:** per-service DNS/sysctls — impossible inside a
  shared-namespace pod, not a compose2pod limitation; last-writer-wins on a
  sysctl key conflict — refused instead, matching the refuse-on-conflict
  policy used elsewhere (see Resource limits, below).

## Resource limits

Compose exposes container resource limits two ways — the legacy scalar
service keys and the Compose-spec `deploy.resources` block — and compose2pod
honors both, refusing loudly on overlap rather than picking a precedence.

- **Legacy keys** (`SERVICE_KEYS` in `compose2pod/keys.py`) map straight onto
  podman run flags: `mem_limit` → `--memory`, `memswap_limit` →
  `--memory-swap`, `mem_reservation` → `--memory-reservation`,
  `mem_swappiness` → `--memory-swappiness`, `cpus` → `--cpus`, `cpu_shares` →
  `--cpu-shares`, `cpu_quota` → `--cpu-quota`, `cpu_period` → `--cpu-period`,
  `cpuset` → `--cpuset-cpus`, `pids_limit` → `--pids-limit`, `shm_size` →
  `--shm-size`, `oom_score_adj` → `--oom-score-adj`. Each of these twelve is a
  number-scalar key (`_number_scalar`/`_validate_number`): the value is
  passed through unchanged (a `${VAR}` inside stays live at run time, per
  Variable interpolation, below); a non-number, non-string value — including
  a bool — is refused. `oom_kill_disable` → `--oom-kill-disable` is the one
  boolean-typed exception in this group: like `read_only`/`init`/`privileged`,
  it validates as an actual bool (`_bool`/`_validate_bool`) and emits the bare
  flag only when true (nothing when false or absent).
- **`deploy.resources`** (`compose2pod/resources.py`, wired in from
  `validate_deploy`/`deploy_resource_flags` called by `parsing.py`/`emit.py`):
  under `deploy`, only `resources` is honored — any other `deploy` subkey
  (`replicas`, `placement`, `restart_policy`, ...) raises. Within `resources`,
  only `limits` and `reservations` are read. `limits.cpus`/`limits.memory`/
  `limits.pids` map to `--cpus`/`--memory`/`--pids-limit`;
  `reservations.memory` maps to `--memory-reservation`. `reservations.cpus`
  and `reservations.devices` have no podman equivalent and are refused
  outright, regardless of value.
- **Refuse on conflict:** when a legacy key and its `deploy.resources`
  counterpart both set the same flag — `mem_limit`/`limits.memory`,
  `cpus`/`limits.cpus`, `pids_limit`/`limits.pids`, and
  `mem_reservation`/`reservations.memory` — conversion refuses loudly rather
  than picking a precedence the Compose spec itself leaves undefined.
- **Non-goals:** `blkio_config` and the Windows-only `cpu_count`/
  `cpu_percent` remain rejected — neither key is in the service-key registry
  or the structural-key set, so each hits the generic "everything else
  raises" gate.

## Healthcheck keys

- **Supported:** `test`, `interval`, `timeout`, `retries`, `start_period`.
- A `healthcheck` value that isn't a mapping raises
  (`_validate_service_healthcheck`, `compose2pod/parsing.py`).
- **`test`:** a bare string (shell form), `"NONE"` / `["NONE"]` (disabled),
  `["CMD", ...]` (exec form), or `["CMD-SHELL", <string>]`. `health_cmd`
  (`compose2pod/healthcheck.py`) is the sole reader and the sole validator —
  `_validate_service_healthcheck` (`compose2pod/parsing.py`) calls it at
  `validate()` time purely for its shape check (the returned `--health-cmd`
  value is discarded there; `emit.py` calls it again to get the value for
  real). Any other shape raises, including a `CMD-SHELL` whose argument isn't
  a string and a `CMD` whose trailing elements aren't all strings (e.g.
  `["CMD-SHELL", ["curl", "-f"]]`).
- **`interval`:** parsed to whole seconds by `interval_seconds`
  (`compose2pod/healthcheck.py`). Supported forms: a bare number of seconds
  (`30`, `"30"`, `"30s"`), minutes (`"2m"`), and milliseconds (`"500ms"`).
  Compound durations (`"1h30m"`) and hour suffixes (`"1h"`) are not parsed —
  each is rejected with an `UnsupportedComposeError` rather than silently
  truncated or misinterpreted. An explicit `null` (or an absent `interval`)
  defaults to 1 second.
- **`timeout`, `retries`, `start_period`:** each must be a number (int or
  float), a string, or `null` when present — the same shape `keys.is_number`
  enforces for the legacy resource-limit keys, plus `null`. A mapping or list
  (e.g. `retries: {a: 1}`) raises at the gate (`_validate_service_healthcheck`,
  `compose2pod/parsing.py`) rather than reaching its `--health-*` flag as a
  literal Python `repr()`. An explicit `null` is treated the same as an
  absent key -- unset, so its `--health-*` flag is omitted entirely
  (`_health_flags`, `compose2pod/emit.py`, keyed off the *value*, not key
  presence). This matches `docker compose config`, which treats an
  explicitly-null key as unset, and is the same treatment this package
  already gives a null `environment`/`volumes`/`command` value elsewhere.
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

## Secrets

- **Top-level `secrets:` definitions:** each entry must be a mapping with
  exactly one of `file:` (a host path, resolved against `--project-dir` when
  relative) or `environment:` (a host environment variable name), both as a
  plain string. `external: true` gets its own rejection message (compose's
  "must already exist" secrets have no analogue here); any other unrecognized
  key raises generically (`_validate_def`, `compose2pod/stores.py`, via the
  `SECRET` `StoreKind` in `compose2pod/stores.py`).
- **Per-service `secrets:` references:** short form (`- name`) or long form
  (a mapping with `source` and optionally `target`, `uid`, `gid`, `mode`).
  `source` must name a top-level secret; an unknown `source` raises at
  `validate()` time (`_ref_source`/`stores.validate`, `compose2pod/stores.py` --
  `stores.validate` dispatches per store kind through the internal
  `_validate_kind`).
- **Closure-scoped creation:** only secrets referenced (by `source`) from
  somewhere in the target service's dependency closure are ever created, so a
  top-level secret nothing in the closure references never becomes a
  `podman secret create` call (`_referenced_names`, `compose2pod/stores.py`,
  driven by the same `startup_order` closure used to decide which services
  run at all).
- **Creation:** each referenced secret becomes one pod-namespaced
  `podman secret create <pod>-<name> ...` line, emitted right after
  `podman pod create` and before any `podman run` by `stores.create_lines`
  (`compose2pod/stores.py`, called from `emit_script`, `compose2pod/emit.py`).
  A `file:` source resolves `Path(project_dir, file)` through `to_shell()`,
  so a `${VAR}` in the path expands live when the script runs, exactly like
  other interpolated fields. An `environment:` source instead pipes
  `printf '%s' "${VAR-}"` into `podman secret create ... -`, where the
  `${VAR-}` means an *unset* host variable yields an empty secret rather than
  failing the script (`_create_lines_for`, `compose2pod/stores.py`).
- **Mounting:** each service reference becomes a
  `--secret source=<pod>-<name>,target=<target>` flag on that service's
  `podman run`, assembled per service by `stores.flags` (`compose2pod/stores.py`,
  called from `emit_script`, `compose2pod/emit.py`), where `target` defaults
  to the secret's own name when the reference doesn't give one (short form,
  or long form without `target`). When present, `target` must be a string.
  `uid`/`gid`/`mode` are only added when the long form gives them explicitly; `mode` renders as a 4-digit octal string
  when given as a Python int (`0o400` becomes `"0400"`) and passes through
  verbatim when given as a string (`_flags_for`, `compose2pod/stores.py`). When
  `uid`/`gid`/`mode` are omitted, podman itself applies its own defaults: the
  secret is mounted at `/run/secrets/<target>`, owned `0:0`, mode `0444`.
- **Teardown:** the EXIT trap that force-removes the pod also runs
  `podman secret rm <pod>-<name> ...` for every referenced secret, so the
  store never outlives the pod even when the script exits abnormally. The
  store module returns the complete best-effort trap fragment and
  `emit_script` splices it after the pod-removal fragment
  (`stores.teardown_line`, `compose2pod/stores.py`; `compose2pod/emit.py`).
- **Variable interpolation:** an `environment:` source's variable name, and
  any `${VAR}` inside a `file:` path, both count toward the CLI's
  informational stderr note of variables the generated script expands at run
  time (`_referenced_variables_for`, `compose2pod/stores.py`, assembled across
  store kinds by `stores.referenced_variables` and folded into
  `referenced_variables()` in `compose2pod/emit.py`); see Variable
  interpolation, below, for the note itself.
- Everything else raises `UnsupportedComposeError` rather than silently doing
  nothing: `external: true`, an unknown `source`, a definition with neither
  or both of `file`/`environment`, and an unrecognized long-form key.

## Configs

- **Top-level `configs:` definitions:** each entry must be a mapping with
  exactly one of `file:` (a host path, resolved against `--project-dir` when
  relative), `environment:` (a host environment variable name), or `content:`
  (inline literal text), all as a plain string. `external: true` gets its own
  rejection message, mirroring secrets; any other unrecognized key raises
  generically (`_validate_def`, `compose2pod/stores.py`, via the `CONFIG`
  `StoreKind` in `compose2pod/stores.py`).
- **Per-service `configs:` references:** short form (`- name`) or long form
  (a mapping with `source` and optionally `target`, `uid`, `gid`, `mode`).
  `source` must name a top-level config; an unknown `source` raises at
  `validate()` time (`_ref_source`/`stores.validate`, `compose2pod/stores.py`).
- **Closure-scoped creation:** only configs referenced (by `source`) from
  somewhere in the target service's dependency closure are ever created, so a
  top-level config nothing in the closure references never becomes a
  `podman secret create` call -- the same closure-scoped-creation rule
  secrets follow (`_referenced_names`, `compose2pod/stores.py`).
- **Creation and delivery:** configs are delivered through podman's secret
  store, exactly like secrets, but pod- *and kind*-namespaced with a
  `config-` prefix (store name `<pod>-config-<name>`, vs. a secret's bare
  `<pod>-<name>`), so a config never collides with a same-named secret. Each
  referenced config becomes one `podman secret create <pod>-config-<name>
  ...` line, emitted right after `podman pod create` and before any
  `podman run` by `stores.create_lines` (`compose2pod/stores.py`, called from
  `emit_script`, `compose2pod/emit.py`). A `file:` source resolves
  `Path(project_dir, file)` through `to_shell()`; an `environment:` source
  pipes `printf '%s' "${VAR-}"` into `podman secret create ... -`; and a
  `content:` source pipes the literal text through `to_shell()` into that
  same `podman secret create ... -` form, so a `${VAR}` written inside
  `content:` stays live and expands against the generated script's own
  runtime environment when the script runs -- the same deferred-interpolation
  model as `file:`/`environment:` (`_create_lines_for`, `compose2pod/stores.py`).
- **Mounting:** each service reference becomes a
  `--secret source=<pod>-config-<name>,target=<target>` flag on that
  service's `podman run`, assembled per service by `stores.flags`
  (`compose2pod/stores.py`, called from `emit_script`, `compose2pod/emit.py`).
  Unlike a secret, whose default `target` is its own name (mounted by podman
  under `/run/secrets/<target>`), a config's default `target` is the
  container-root absolute path `/<name>` (`CONFIG.default_target`,
  `compose2pod/stores.py`). When present, `target` must be a string. A long-form `target` must be an absolute path
  (start with `/`); a relative target raises
  (`CONFIG.require_absolute_target`, checked by `_check_target`,
  `compose2pod/stores.py`). `uid`/`gid`/`mode` behave exactly as for secrets:
  only added when the long form gives them explicitly, `mode` renders as a
  4-digit octal string when given as a Python int and passes through
  verbatim when given as a string (`_flags_for`, `compose2pod/stores.py`).
- **Teardown:** the EXIT trap that force-removes the pod also runs
  `podman secret rm <pod>-config-<name> ...` for every referenced config, so
  the store never outlives the pod even when the script exits abnormally --
  byte-for-byte the same teardown parity as secrets (`stores.teardown_line`,
  `compose2pod/stores.py`, spliced into the trap by `emit_script`,
  `compose2pod/emit.py`).
- **Variable interpolation:** an `environment:` source's variable name, any
  `${VAR}` inside a `file:` path, and any `${VAR}` inside `content:` all
  count toward the CLI's informational stderr note of variables the
  generated script expands at run time (`_referenced_variables_for`,
  `compose2pod/stores.py`, assembled across store kinds by
  `stores.referenced_variables` and folded into `referenced_variables()` in
  `compose2pod/emit.py`); see Variable interpolation, below, for the note
  itself.
- Everything else raises `UnsupportedComposeError` rather than silently
  doing nothing: `external: true`, an unknown `source`, a definition with
  not exactly one of `file`/`environment`/`content`, an unrecognized
  long-form key, and a relative long-form `target`.

## Variable interpolation

compose2pod does not resolve Compose Spec `${VAR}` references at generation
time. `to_shell()` (`compose2pod/shell.py`) instead re-encodes each
interpolated string leaf into a double-quoted POSIX-shell fragment with the
variable references left live, so the generated script's own shell expands
them against its runtime environment when the script runs.

`Expand` (`compose2pod/keys.py`) is a frozen dataclass wrapping the `str`
value every interpolated field carries; it rejects a non-`str` value at
construction (`__post_init__`) with `UnsupportedComposeError`, since
`to_shell()`/`variable_names()` both assume a `str` and crash raw otherwise.
This is a chokepoint, not a substitute for validating each key's shape at
`validate()` time: it only converts an already-malformed value into a clean
error one step later, at `emit_script()`, instead of leaving it to crash
inside `shell.py`'s regex matching.

The interpolated set is exactly what `Expand(...)` wraps in
`compose2pod/emit.py` and `compose2pod/keys.py` — there is no separate list
to maintain by hand, so treat the **service-key registry**
(`SERVICE_KEYS` in `compose2pod/keys.py`; see `architecture/glossary.md`)
and the `Expand(...)` call sites in `emit.py` as the source of truth if this
enumeration ever appears to drift:

- **Structural fields:** `image` (only when the service has no `build`
  override — otherwise the CI image is used, not the compose value),
  `command`, `entrypoint`, `environment`, `env_file`, `volumes`, `tmpfs`, and
  the healthcheck `test` command.
- **Service-key registry fields** whose spec wraps its value in `Expand` —
  e.g. `user`, `working_dir`, `platform`, `group_add`, `cap_add`, `cap_drop`,
  `security_opt`, `devices`, `labels`, `annotations`, `ulimits`, and every
  numeric resource-limit key (`mem_limit`, `cpus`, `pids_limit`, ...). The
  rule, not the list, is authoritative: this is every `SERVICE_KEYS` entry
  whose `emit` wraps its value (the `_scalar`/`_number_scalar`/`_list`/`_map`
  factories, plus the custom `ulimits` emitter) except `pull_policy` (a
  validated enum emitted verbatim from `PULL_POLICY_MAP`) and the four
  boolean flags
  `init`/`read_only`/`privileged`/`oom_kill_disable` (each emits a bare flag
  with no value to interpolate).

Everything else is never interpolated: `build`'s own contents (context,
dockerfile, args — never read), `depends_on`, `networks`, `hostname`, and
`container_name` (the last two are emitted as literal `--add-host
host:127.0.0.1` entries, not expanded), and the healthcheck
`timeout`/`start_period`/`retries` numbers. Supported forms: `$VAR`, `${VAR}`,
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
the `--command` override) are literal and never interpolated.
`--artifact` must be a string in `SRC:DST` form; a non-string value or one
with no `:` raises `UnsupportedComposeError` (`_validate_options`,
`compose2pod/emit.py`), guarding library callers of both `emit_script` and
`referenced_variables` as well as the CLI (the CLI itself always supplies a
string — this guards a direct `EmitOptions` construction). `allow_exit_codes`
entries must each be an `int` (not `bool`, which is an `int` subclass in
Python but not a meaningful exit code) — `_validate_options` rejects
anything else, since each entry is interpolated unquoted into the generated
`case "$rc" in ...)` pattern; the CLI's `argparse` `type=int` already
guarantees this, so the check exists for a library caller passing
`EmitOptions` directly, where an unvalidated string would be shell injection,
not just a crash. The pod
name is embedded into the pod-create line, the single-quoted `EXIT`
trap, and the `<pod>-<name>` store names (some of them unquoted), so it
must be a shell-inert identifier — `emit_script` validates it against
`POD_NAME_PATTERN` (`^[A-Za-z0-9][A-Za-z0-9_.-]*$`) and raises
`UnsupportedComposeError` on any other value, guarding library callers
as well as the CLI's `--pod-name` (`compose2pod/emit.py`). The CLI
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

`depends_on` (`compose2pod/graph.py`) itself must be either a list of service
names (short form, each defaulting to `service_started`) or a mapping of
service name to a per-dependency mapping (long form, read for `condition`).
Anything else — a bare string, a number, a mapping whose value isn't itself a
mapping — raises `UnsupportedComposeError` at the gate instead of failing
later with a raw `AttributeError`/`TypeError` when the shape is walked. Each
short-form list element must itself be a string; a non-string element (e.g.
`depends_on: [{db: {condition: service_healthy}}]`, the same list/map YAML
slip that trips up `environment`/`command`) raises the same way, instead of
crashing raw (`TypeError: unhashable type`) from inside `validate()` itself
when the list was passed to `dict.fromkeys`. `extends.py`'s own
list-to-mapping normalization (`_as_mapping`, used when merging a
list-form `depends_on` across `extends`) enforces the identical
element-must-be-a-string check before `validate()` ever runs — `extends`
resolution happens ahead of the gate (see Extends, above), so without its
own check this same malformed input crashed raw there instead.

The long form's `condition` value gets the same treatment: it must be a
string (`graph.depends_on`), so a mapping or list condition (e.g. `depends_on:
{db: {condition: {a: 1}}}`) raises `UnsupportedComposeError` there instead of
crashing raw (`TypeError: cannot use 'dict' as a set element -- unhashable
type: 'dict'`) at the `condition not in DEPENDS_ON_CONDITIONS` set-membership
check that follows it in `parsing._validate_depends_on` — `in` against a
`set` hashes its operand, and only a `str` condition ever reaches that check.
This is checked in `graph.py`, not `parsing.py`, so every caller of
`depends_on` — not only `validate()` — gets the same protection, matching
every other depends_on shape check, which already lives there.

## YAML anchors and merge keys

Anchors (`&name` / `*name`) and the merge key (`<<:`) need no handling in
compose2pod: PyYAML's `safe_load` resolves them at load time, so `validate()`
and `emit` see already-merged service mappings. JSON input has no anchors but
can still carry literal `x-` extension keys, handled identically.
