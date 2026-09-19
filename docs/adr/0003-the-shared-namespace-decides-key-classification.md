# The shared namespace decides which keys are refused, inert, or pod-level

Every service runs in one pod sharing `net`, `uts`, `ipc` and `cgroup`, and the shared network
namespace, with localhost discovery through per-container `--add-host`, is the reason the tool
exists. Keys that pull a container out of it (`network_mode`, `links`, `external_links`,
`expose`) are refused permanently; per-container namespace overrides (`ipc`, `uts`, `domainname`,
`cgroup`, `userns_mode`) are refused while the pod keeps its default `--share`. `dns*` and
`sysctls` are pod-level, unioned and conflict-checked across the closure onto
`podman pod create`, because a container that joined the pod owns neither namespace and podman
rejects the per-container flag. `stop_signal` and `stop_grace_period` are accepted but inert, in
`IGNORED_SERVICE_KEYS` with a warning: the script tears down with `pod rm -f` and never runs
`podman stop`, so the flags would set metadata nothing consults.
