# compose2pod

Convert a Docker Compose file into a POSIX `sh` script that runs its services as a **single Podman pod**.

Built for CI and test environments where you can't use `docker compose` or `podman kube play`:

- **No bridge networking / netavark.** Unprivileged CI containers often have a read-only `/proc/sys`, so netavark fails to create bridge networks. A single pod shares one network namespace with no bridge: services talk over `127.0.0.1`, and names resolve via `--add-host`.
- **No systemd.** Podman healthchecks are normally scheduled by systemd timers. compose2pod gates startup by polling `podman healthcheck run` directly, so `depends_on: service_healthy` works without systemd.
- **No heavy runtime.** The core is stdlib-only — no dependencies, no compiled wheels — so it installs and runs in minimal Python images.

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

compose2pod supports an honest subset and errors clearly on anything outside
it: `image`/`build`, `command`, `environment`/`env_file`, short-form bind
`volumes`, `healthcheck` (CMD/CMD-SHELL), `depends_on` (all conditions), and
network `aliases`. Compose extension fields (any `x-`-prefixed key) and YAML
anchors are accepted as-is, so a top-level `x-*` anchor block for shared
config just works. See `architecture/supported-subset.md` for the full
accept/ignore/reject matrix.

## Status

Beta. Part of the [modern-python](https://github.com/modern-python) family. MIT licensed.
