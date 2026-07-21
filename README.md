<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)"  srcset="https://raw.githubusercontent.com/modern-python/.github/main/brand/projects/compose2pod/lockup-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/modern-python/.github/main/brand/projects/compose2pod/lockup-light.svg">
    <img alt="compose2pod" src="https://raw.githubusercontent.com/modern-python/.github/main/brand/projects/compose2pod/lockup.png" width="420">
  </picture>
</p>

[![PyPI version](https://img.shields.io/pypi/v/compose2pod.svg)](https://pypi.org/project/compose2pod/)
[![Supported Python versions](https://img.shields.io/pypi/pyversions/compose2pod.svg)](https://pypi.org/project/compose2pod/)
[![Downloads](https://static.pepy.tech/badge/compose2pod/month)](https://pepy.tech/projects/compose2pod)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen.svg)](https://github.com/modern-python/compose2pod/actions/workflows/ci.yml)
[![CI](https://github.com/modern-python/compose2pod/actions/workflows/ci.yml/badge.svg)](https://github.com/modern-python/compose2pod/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/modern-python/compose2pod.svg)](https://github.com/modern-python/compose2pod/blob/main/LICENSE)
[![GitHub stars](https://img.shields.io/github/stars/modern-python/compose2pod)](https://github.com/modern-python/compose2pod/stargazers)
[![Context7](https://img.shields.io/badge/Context7-docs-blue)](https://context7.com/modern-python/compose2pod)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![ty](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ty/main/assets/badge/v0.json)](https://github.com/astral-sh/ty)

Convert a Docker Compose file into a POSIX `sh` script that runs its services as a **single Podman pod**.

Built for CI and test environments where you can't use `docker compose` or `podman kube play`:

- **No bridge networking / netavark.** Unprivileged CI containers often have a read-only `/proc/sys`, so netavark fails to create bridge networks. A single pod shares one network namespace with no bridge: services talk over `127.0.0.1`, and names resolve via a generated `/etc/hosts` the script owns.
- **No systemd.** Podman healthchecks are normally scheduled by systemd timers. compose2pod gates startup by polling `podman healthcheck run` directly, so `depends_on: service_healthy` works without systemd.
- **No heavy runtime.** The core is stdlib-only — no dependencies, no compiled wheels — so it installs and runs in minimal Python images.

## Requirements

compose2pod's generated scripts own `/etc/hosts`: they write it to a temp
file and bind-mount it read-only into every container under `--no-hosts`
(see `architecture/supported-subset.md`), so pod-internal name resolution
works on any Podman version. `host.containers.internal` /
`host.docker.internal` are not provided — add an explicit `extra_hosts`
entry if you need them.

## Install

```bash
pip install compose2pod            # core: reads compose as JSON
pip install compose2pod[yaml]      # optional: read YAML directly (adds PyYAML)
```

## Usage

```bash
# YAML directly (needs the [yaml] extra)
compose2pod docker-compose.yml --target app --image myimage:ci > run.sh

# Or stay dependency-free by piping JSON (e.g. via yq)
yq -o=json '.' docker-compose.yml | compose2pod --target app --image myimage:ci > run.sh

sh ./run.sh
```

## Supported compose subset

compose2pod refuses **every document `docker compose config` refuses** — a
measured property, checked continuously by a differential conformance harness
that runs the real Docker CLI and the real compose2pod pipeline over the same
YAML. So a file that compiles is a file Docker would run; where compose2pod
still refuses a form Docker accepts, it is because Podman genuinely cannot
express it (each such case is documented, not guessed).

Within that boundary it covers most of what real compose files use:

- **Services** — `image`/`build`, `command`/`entrypoint`, `environment` and
  `env_file` (string and long-form `{path, required, format}`), `volumes`
  (short-form and long-form `--mount`, including the `bind`/`volume`/`tmpfs`
  option maps), `tmpfs`, `healthcheck`, `depends_on` (all conditions), network
  `aliases`, `hostname`/`container_name`.
- **Confinement & metadata** — `user`, `working_dir`, `read_only`, `init`,
  `privileged`, `cap_add`/`cap_drop`, `security_opt`, `devices`, `group_add`,
  `platform`, `labels`, `annotations`, `pull_policy` (the quoted-boolean and
  YAML-1.1 spellings Docker accepts, too).
- **Resources** — the legacy keys (`mem_limit`, `cpus`, `pids_limit`,
  `ulimits`, …) and the modern `deploy.resources` block.
- **Pod-wide** — `dns`/`dns_search`/`dns_opt`, `sysctls`, `extra_hosts`.
- **Composition** — same-file `extends`, `secrets`/`configs`, `profiles`.

Compose extension fields (any `x-`-prefixed key) and YAML anchors are accepted
as-is, so a top-level `x-*` anchor block for shared config just works.
`${VAR}`-style variable interpolation is left live in the generated script,
resolved by its shell against the environment present when the script runs (no
`.env` file support). See `architecture/supported-subset.md` for the full
accept/ignore/reject matrix and `planning/decisions/` for the boundary rulings.

## Status

Beta. Part of the [modern-python](https://github.com/modern-python) family. MIT licensed.
