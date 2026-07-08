---
summary: Extracted the chats compose-to-podman-pod converter into the standalone public package compose2pod (stdlib-only core, optional YAML extra, CLI + library API, CI, PyPI trusted publishing).
---

# Design: compose2pod extraction

## Summary

Publish the chats compose-to-podman-pod converter as `compose2pod`, a
standalone public PyPI utility under the modern-python org. It is a
dependency-free, stdlib-only Python package that reads a Docker Compose
document and emits a POSIX `sh` script which runs the compose services as a
single Podman pod. It is both a CLI (`compose2pod`) and an importable
library. YAML input is an optional extra; the core reads JSON.

**Tech stack:** Python 3.10+ (stdlib only in the core), uv with the
`uv_build` backend, `just` task runner, ruff (`select = ALL`), ty, pytest,
GitHub Actions, PyPI trusted publishing. Optional `[yaml]` extra pulls
PyYAML. Mirrors the `modern-di` package conventions (see "Local location and
references" below).

## Motivation

CI runners often give unprivileged containers a read-only `/proc/sys`, so
netavark cannot create bridge networks (its `route_localnet` sysctl write
fails with EROFS). This is upstream-declined: podman issue #20713 is closed
"not planned" and netavark PR #910 is unmerged. Containers in a single podman
pod share one network namespace with no bridge and no netavark: services
reach each other over `127.0.0.1`, and names resolve via per-container
`--add-host` entries. Healthchecks are driven manually with `podman
healthcheck run` because there is no systemd inside the CI job container to
schedule them.

Research (2026-07) confirmed no existing tool covers this combination:
`docker`/`podman compose` hang on `depends_on: service_healthy` without
systemd; `podman kube play` has no `depends_on` ordering and its probes also
need systemd; `kompose`/`podlet` target multi-pod or systemd/quadlet; every
mature converter is interpreted (no interpreter in minimal runtime images) or
emits bridge-networked `podman run`. `compose2pod` fills that gap.

## Non-goals

- Redesigning the runtime behavior of the CI-proven chats converter
  (`bin/compose_to_pod.py`) — this is a faithful extraction plus four
  targeted review fixes, not a rewrite.
- A `run` subcommand that executes the emitted script — output is the script;
  execution is left to the caller.
- Depending on a compose-spec parser library for the core (see "Parsing
  approach").

## Design

### 1. Global constraints

- The **core package has zero runtime dependencies** (stdlib only). This is
  the primary differentiator: it must install with no compiled wheels and
  run in minimal Python images. No pydantic or compose-parser dependency in
  the core (see "Parsing approach").
- Python **3.10+**. `X | Y` annotation syntax is allowed at runtime; no
  `from __future__ import annotations` required.
- Quality gates (match modern-python house style, verified against
  `modern-di`): ruff `select = ALL` with justified per-line `# noqa`, ty
  clean, `eof-fixer` clean, pytest at **100% line coverage** enforced
  (`--cov-fail-under=100`); branch coverage is a diagnostic run (`just
  test-branch`), not an enforced gate.
- The emitted script is POSIX `sh` (`set -eu`), not bash.

### 2. Supported compose subset

The tool supports an honest subset and grows on demand. Anything outside it
raises `UnsupportedComposeError` with a clear message rather than emitting a
wrong script. The README documents this matrix.

**Top-level keys:** `services` (required), `version`, `name`, `networks`
(ignored with a warning — all services share the pod namespace).

**Service keys — supported:**
- `image` — used verbatim.
- `build` — the service runs the CI image passed via `--image` instead of
  building.
- `command` — list form is argv; string form becomes `/bin/sh -c
  "<string>"`.
- `environment` — list (`KEY=VALUE`) or mapping form.
- `env_file` — string or list; paths resolved against `--project-dir` and
  passed to `podman run --env-file` (podman parses the file; compose2pod
  does not).
- `volumes` — short-form bind mounts only (`src:dst[:opts]`); `src` must be
  a path (starts with `.` or `/`), resolved against `--project-dir`. Named
  volumes and long-form syntax are unsupported.
- `healthcheck` — `test` as `CMD` (list → argv) or `CMD-SHELL` (string);
  `NONE`/`["NONE"]` disables; `interval`, `timeout`, `retries`,
  `start_period` (see "Deviations from the current chats converter").
- `depends_on` — list form (all `service_started`) or mapping form with
  conditions `service_started`, `service_healthy`,
  `service_completed_successfully`. `service_healthy` on a dependency
  without a healthcheck is a hard error.
- `networks` — only the `aliases` are read, and only to build `--add-host
  NAME:127.0.0.1` entries so intra-pod name resolution works.

**Service keys — ignored with a warning:** `ports`, `restart`,
`stdin_open`, `tty`.

**Anything else** (e.g. `configs`, `secrets`, `profiles`, `deploy`,
long-form volumes, unknown healthcheck keys) raises
`UnsupportedComposeError`.

### 3. Emitted script behavior

Given a `--target` service, the tool computes the dependency closure in
start order (topological sort, cycle detection) and emits a script that:

1. Sets an `EXIT` trap that removes the pod (`podman pod rm -f <pod>`).
2. Creates the pod (`podman pod create --name <pod>`).
3. For each service before the target, in dependency order:
   - `service_completed_successfully` dependencies run in the foreground
     with `--rm` (run-to-completion, e.g. migrations).
   - all other dependencies start detached (`podman run -d`).
4. Before the first dependent of a `service_healthy` dependency, waits for
   health by polling `podman healthcheck run <ctr>` in a loop
   (`wait_healthy`), with attempts derived from a fixed budget
   (`HEALTHY_WAIT_BUDGET_SECONDS // interval`, minimum 1). Each dependency
   is waited on at most once.
5. Runs the target in the foreground, capturing its exit code (`rc=$?`),
   then `podman cp`s each `--artifact SRC:DST` out of the target container
   (best-effort, before the exit-code gate).
6. Treats `0` and each `--allow-exit-code` value as success. On any other
   code it prints the target's `OOMKilled`/`ExitCode` via `podman inspect`
   and `podman ps -a`, then exits with `rc`.

Each `podman run` carries: `--pod`, `--name <pod>-<service>`, one
`--add-host NAME:127.0.0.1` per known hostname/alias, `-e`/`--env-file` for
env, `-v` for bind mounts, and `--health-cmd`/`--health-timeout` when a
healthcheck is present. All tokens are shell-quoted with `shlex.quote`.

### 4. CLI interface

```
compose2pod --target NAME --image IMG [FILE]
            [--project-dir DIR] [--command CMD] [--pod-name NAME]
            [--artifact SRC:DST]... [--allow-exit-code N]...
            [--format auto|json|yaml]
```

- Reads the compose document from `FILE` or stdin.
- `--target` (required): service to run in the foreground.
- `--image` (required): image substituted for services that have a `build`
  section.
- `--project-dir` (default `.`): base for resolving relative volume and
  env_file paths.
- `--command`: shell command overriding the target service's command.
- `--pod-name` (default `test-pod`): validated against
  `^[A-Za-z0-9][A-Za-z0-9_.-]*$`; invalid → exit 2.
- `--artifact SRC:DST` (repeatable): file to `podman cp` out of the target
  after it exits.
- `--allow-exit-code N` (repeatable): target exit code treated as success
  in addition to 0.
- `--format` (default `auto`): `json` (stdlib), `yaml` (requires the
  `[yaml]` extra), or `auto` (try JSON, fall back to YAML if PyYAML is
  importable).
- Warnings for ignored constructs go to stderr prefixed `compose2pod: `.
  Invalid JSON/YAML or an unsupported construct exits 2 with a message on
  stderr.

### 5. Public API

Importable, for programmatic use and testing:

```python
from compose2pod import validate, emit_script, EmitOptions, UnsupportedComposeError
```

- `validate(compose: dict) -> list[str]` — raises `UnsupportedComposeError`;
  returns warnings.
- `emit_script(compose: dict, options: EmitOptions) -> str` — returns the
  POSIX sh script.
- `EmitOptions` — frozen dataclass: `target`, `ci_image`, `command`, `pod`,
  `project_dir`, `artifacts`, `allow_exit_codes`.
- `UnsupportedComposeError` — raised for anything outside the subset.

The core API operates on already-parsed `dict` data; YAML/JSON loading and
format detection live in the CLI layer.

### 6. Parsing approach

The core reads a compose **dict**. Loading is layered:

- **JSON** via stdlib `json` — always available.
- **YAML** via PyYAML, shipped as the optional `[yaml]` extra. Without it,
  `--format yaml` errors with an actionable message ("install
  compose2pod[yaml] or pipe the file through yq"). In dependency-constrained
  CI, users pipe `yq -o=json` and stay dependency-free.

We do **not** depend on a compose-spec parser library. Researched candidates
(`compose-spec`, `compose-pydantic`) both require pydantic v2 (a compiled
`pydantic-core` wheel) and are early-stage single-maintainer 0.x projects.
Adopting one would (a) break the zero-dependency differentiator, (b) not
remove our subset validation — full-spec parsers permissively accept
constructs we cannot turn into a single pod, so a subset gate is still
required — and (c) add supply-chain risk to the core. A future optional
`[strict]` extra could cross-validate against `compose-spec` for users who
want full-spec checking, but it is out of scope for v1 (YAGNI). Formalized
as a decision record:
[`2026-07-03-zero-dependency-core.md`](../../decisions/2026-07-03-zero-dependency-core.md).

### 7. Package layout

**Root layout** (matches `modern-di`: module at the repo root, `uv_build`
backend with `module-name = "compose2pod"`, `module-root = ""`), splitting
the original single 397-line module into focused units:

- `compose2pod/__init__.py` — public exports (`validate`, `emit_script`,
  `EmitOptions`, `UnsupportedComposeError`).
- `compose2pod/py.typed` — marker (PEP 561; the package is typed).
- `compose2pod/parsing.py` — subset definitions and `validate` (+
  `_validate_service`, `_validate_depends_on`, `_has_healthcheck`).
- `compose2pod/graph.py` — `depends_on`, `hostnames`, `startup_order` (topo
  sort, cycle detection).
- `compose2pod/healthcheck.py` — `health_cmd`, `interval_seconds`.
- `compose2pod/emit.py` — `EmitOptions`, `run_flags`, `image_for`,
  `command_tokens`, script header, `emit_script` (with the target-run
  branch extracted into a helper for readability/complexity).
- `compose2pod/cli.py` — argparse, format detection, JSON/YAML loading,
  `main(argv)`.
- `compose2pod/__main__.py` — `python -m compose2pod`.
- `tests/` at the repo root (mirrors `modern-di`).
- Console-script entry point in `[project.scripts]`:
  `compose2pod = "compose2pod.cli:main"`.

### 8. Deviations from the current chats converter

These are the improvement items identified in review; they were fixed as
part of the move, each with a failing test first:

1. **Guard non-dict input** — `yq` on an empty file yields `null`;
   `validate` must raise `UnsupportedComposeError`, not a raw
   `AttributeError`.
2. **`start_period` and `retries`** — previously validated but ignored by
   `run_flags`. Decision (refined during planning): pass them through to
   `podman run` as `--health-start-period` and `--health-retries`
   (alongside the existing `--health-timeout`), recording the author's
   intent on the container. The manual `wait_healthy` poll keeps its fixed
   `HEALTHY_WAIT_BUDGET_SECONDS` budget as a generous readiness timeout —
   it is deliberately **not** shortened to `retries × interval`, which
   would cause premature failure for a service with a long `start_period`
   (the poll loops until first success, so `start_period` never gates it).
   This removes the silently-ignored keys without regressing wait behavior.
   Formalized as a decision record:
   [`2026-07-03-healthcheck-start-period-retries-passthrough.md`](../../decisions/2026-07-03-healthcheck-start-period-retries-passthrough.md).
3. **Merge the duplicate `_CMD_SHELL_MIN_LENGTH` / `_CMD_MIN_LENGTH`
   constants** (both `2`) into one.
4. **Extract the target-run branch of `emit_script`** into a helper.

### 9. Tooling and distribution

Mirror `modern-di` exactly so the package is consistent with the rest of the
org:

- **`pyproject.toml`:** `[project]` with `name = "compose2pod"`, `authors =
  [{ name = "Artur Shiriev", email = "me@shiriev.ru" }]`, `license = "MIT"`,
  `requires-python = ">=3.10,<4"`, `version = "0"` (real version comes from
  the release tag), keywords (podman, docker-compose, compose, pod, ci,
  testing, containers), classifiers (Development Status :: 4 - Beta, Python
  3.10–3.14, `Typing :: Typed`, Topic :: Software Development).
  `[project.optional-dependencies]` `yaml = ["PyYAML>=6"]`.
  `[project.scripts]` `compose2pod = "compose2pod.cli:main"`.
  `[build-system]` `uv_build>=0.11,<1.0`; `[tool.uv.build-backend]`
  `module-name = "compose2pod"`, `module-root = ""`.
- **`[dependency-groups]`:** `dev` (pytest, pytest-cov), `lint` (ruff, ty,
  eof-fixer, typing-extensions).
- **`[tool.ruff]`:** `fix = true`, `unsafe-fixes = true`, `line-length =
  120`, `target-version = "py310"`; `lint.select = ["ALL"]` with the
  modern-di ignore set (`D1`, `S101`, `TCH`, `FBT`, `D203`, `D213`,
  `COM812`, `ISC001`) plus any justified additions; isort config as in
  modern-di. Note: this config **autofixes destructively** — CI and
  reviewers must use `ruff check --no-fix`.
- **`justfile`** with modern-di's recipes: `install`, `lint` (eof-fixer,
  ruff format, ruff check --fix, ty), `lint-ci` (same, `--no-fix`/`--check`),
  `test`, `test-ci` (`--cov=. --cov-fail-under=100`), `test-branch`,
  `publish` (`uv version $GITHUB_REF_NAME && uv build && uv publish`).
- **`.gitignore`** mirrors modern-di (notably `uv.lock` is git-ignored).
- **GitHub Actions:** `ci.yml` → `_checks.yml` (lint on 3.10; pytest matrix
  3.10–3.14; via `setup-uv` + `setup-just`), and `release.yml` (tag-driven,
  `contents: write` + `id-token: write`, PyPI Trusted Publishing / OIDC,
  `environment: pypi`). semver tags.
- **`LICENSE`** (MIT, Artur Shiriev), **`README.md`** with the support
  matrix, both CI usage forms (`[yaml]` extra and the yq-pipe), and the
  "why this exists" niche.
- GitHub repo `modern-python/compose2pod`; `[project.urls]`
  Repository/Issues/Changelog like modern-di.

## Operations

GitHub repo `modern-python/compose2pod` and the PyPI Trusted Publisher
(project `compose2pod`, workflow `release.yml`, environment `pypi`) must be
created/registered manually before the first tag — outside this repo, done
by Artur.

## Out of scope

This spec covers extracting and publishing the package only. The following
are downstream, each its own spec/plan later:

- **chats migration:** consume `compose2pod` via pip, delete the in-repo
  `bin/compose_to_pod.py` copy, install it where the CI job runs the
  converter (the app image).
- **pypelines wiring:** a template job that installs and invokes
  `compose2pod`, with a deprecation path for the old docker-compose/dind
  test jobs.
- A `[strict]` extra cross-validating against a full compose-spec parser
  (YAGNI for v1; see "Parsing approach").
- The heavier modern-di machinery (`planning/`, `docs/` site,
  `architecture/`, benchmarks) was optional for v1 and omitted from the
  initial scaffold — subsequently adopted in a later change that migrated
  this repo onto the `lesnik512/planning-convention` planning convention
  (this bundle itself lives under that convention).

## Testing

Ported the existing 65-test suite (originally 519 lines, 100% line+branch)
into the package. The enforced gate is 100% line coverage (`just test-ci`);
branch coverage is kept clean too (`just test-branch`) since it already is.

Additions over the original suite:
- CLI/format tests: JSON input, YAML input (with the extra), `auto`
  fallback, and the actionable error when YAML is requested without the
  extra.
- Tests for the four deviations above (non-dict guard, `start_period`/
  `retries`, and that the merged constant and extracted helper preserve
  behavior).

The chats `docker-compose.yml` shape (build args, healthchecks in both CMD
forms, list and mapping env, completion- and health-gated depends_on,
network alias) is kept as the primary realistic fixture
(`tests/conftest.py::chats_compose`).

## Risk

- **Silent behavior drift from the chats prototype.** Mitigated by treating
  the prototype as the source of truth for anything not explicitly listed as
  one of the four deviations, and porting its test suite wholesale.
- **Zero-dependency constraint eroding over time** (e.g. a future
  contributor adding a compose-parser dependency for convenience).
  Mitigated by the recorded decision
  (`2026-07-03-zero-dependency-core.md`) and the CI lint gate that would
  need a `pyproject.toml` change to introduce a new runtime dependency,
  making it visible in review.
- **`start_period`/`retries` passthrough interacting badly with a long
  `start_period`** if the wait budget were ever coupled to `retries ×
  interval` in a future change. Mitigated by the recorded decision
  (`2026-07-03-healthcheck-start-period-retries-passthrough.md`) explaining
  why the budget is intentionally decoupled.

## Local location and references

- The package is developed at `/Users/kevinsmith/src/pypi/compose2pod` — a
  sibling of the other modern-python packages (`modern-di`, `semvertag`,
  `that-depends`, `db-retry`, …) that already live under
  `/Users/kevinsmith/src/pypi/`.
- `modern-di` is the reference for all conventions above (pyproject,
  ruff/ty config, justfile recipes, CI workflows, root layout, `py.typed`,
  `.gitignore`, tag-driven trusted-publishing release). Where this spec and
  `modern-di` disagree, `modern-di` wins.

## Versioning and stability

Start at `0.1.0`. The public surface is the CLI flags and the
emitted-script contract; document that these are stable within a minor
series and that the supported subset grows via minor releases.
`UnsupportedComposeError` messages are not part of the stable contract.

## Decisions log

- Audience/distribution: public, modern-python org, public PyPI.
  (user-approved)
- Compose scope: honest subset, grow on demand. (approved)
- Name: `compose2pod` (verified free on PyPI). (approved)
- Core is zero-dependency stdlib; YAML is an optional `[yaml]` extra; no
  compose-parser dependency. (approved, research-backed — formalized as
  [`2026-07-03-zero-dependency-core.md`](../../decisions/2026-07-03-zero-dependency-core.md))
- `start_period`/`retries` passed through to `podman run` flags; wait budget
  not shortened to `retries × interval`. (approved — formalized as
  [`2026-07-03-healthcheck-start-period-retries-passthrough.md`](../../decisions/2026-07-03-healthcheck-start-period-retries-passthrough.md))
- Output is an emitted POSIX sh script; execution is left to the caller (no
  `run` subcommand in v1). (approved)
- Python floor 3.10. (approved)
- Package lives at `/Users/kevinsmith/src/pypi/compose2pod`, mirroring
  `modern-di` conventions (root layout, `uv_build`, `just`, ty, tag-driven
  trusted publishing). (user-directed)
- Enforced coverage gate is 100% line (branch is diagnostic), matching
  modern-di. (research-backed)
