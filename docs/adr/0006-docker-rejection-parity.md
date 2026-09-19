# Docker's refusals bind; podman decides what we can accept

Two rules, one direction each. A document `docker compose config` rejects, compose2pod rejects:
`accepted(compose2pod) ⊆ accepted(docker)`, because the tool is a drop-in for `docker compose` on
rootless runners and accepting a file Docker refuses turns a hard error into a green CI run. A
document Docker accepts, compose2pod accepts whenever podman can express it inside a pod. Where
podman cannot, that is a legitimate refusal (`network_mode`; `sysctls: ["a"]` with no value;
`volumes: ["a"]`, which podman rejects as a relative mount target), and where compose2pod merely
does not parse a form yet, that is a tracked limitation, never a design position. Docker's
verdict binds only on the document, not the host: `env_file` existence, `${VAR:?}`, and a
negative on a top-level numeric key are facts about the machine that runs the script and are
deferred to it. `tests/conformance/` runs both oracles for real over a probe matrix generated
from `SERVICE_KEYS | STRUCTURAL_KEYS | IGNORED_SERVICE_KEYS`, so a new key is probed the moment
it is added. One residual is open by design: `depends_on` errors among services outside the
target's closure are accepted here and rejected by Docker
([#87](https://github.com/modern-python/compose2pod/issues/87)).
