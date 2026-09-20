# Docker's refusals bind; podman decides what we can accept

Two rules, one direction each. A document `docker compose config` rejects, compose2pod rejects:
`accepted(compose2pod) ⊆ accepted(docker)`, because the tool is a drop-in for `docker compose` on
rootless runners and accepting a file Docker refuses turns a hard error into a green CI run. A
document Docker accepts, compose2pod accepts whenever podman can express it inside a pod. Where
podman cannot, that is a legitimate refusal (`sysctls: ["a"]` with no value;
`volumes: ["a"]`, which podman rejects as a relative mount target; a short-form volume entry Docker
reads as an anonymous volume whose target is not absolute -- a leading single letter is a drive
marker to Docker, never a volume name, so `C:\data`, `v:/data` and `v:` all ask for a container path
podman refuses outright, leaving the long form as the way to name a one-character volume), and where
compose2pod merely does not parse a form yet, that is a tracked limitation, never a design position.
The two are held apart in the refusal messages as well as here: a drive-shaped entry Docker reads as
a *bind* is refused for the short form's own limit, not for podman's, and says so. A refusal this
rule does not reach is one this rule must not claim: `network_mode` is refused because honouring it
pulls a container out of the pod's shared namespace
([0003-the-shared-namespace-decides-key-classification.md](0003-the-shared-namespace-decides-key-classification.md)),
which podman expresses perfectly well even inside a pod (measured, 4.9.3)
([#115](https://github.com/modern-python/compose2pod/issues/115)).
Docker's verdict binds only on the document, not the host: `env_file` existence, `${VAR:?}`, and a
negative on a top-level numeric key are facts about the machine that runs the script and are
deferred to it. `tests/conformance/` runs both oracles for real over a probe matrix generated
from `SERVICE_KEYS | STRUCTURAL_KEYS | IGNORED_SERVICE_KEYS`, so a new key is probed the moment
it is added, and `tests/integration/refusals.py` measures the other side, pairing each volume-family
refusal above with the `--mount` that expresses what Docker says the document means, so a claim that
podman cannot express something cannot go stale unnoticed either. It carries both verdicts:
`REFUSALS` for the mounts podman will not make, `LIMITATIONS` for the ones it would, which is what
keeps a limitation from quietly reading as rule two. The rest of the refusals get rows, and a gate
that every site has one, under [#109](https://github.com/modern-python/compose2pod/issues/109).
Verdicts are per version: the rulings here are measured against `docker compose config` v5.1.2 and
podman 4.9.3. No minimum podman is stated anywhere, and one acceptance already outruns the version
CI tests -- an absolute image `subpath` passes the gate and podman 4.9.3 rejects the flag outright
([#114](https://github.com/modern-python/compose2pod/issues/114)). Two residuals are open by design: `depends_on` errors among services outside the
target's closure are accepted here and rejected by Docker
([#87](https://github.com/modern-python/compose2pod/issues/87)), and the drive-qualified *bind*
(`C:\data:/var`) is a limitation rather than rule two, since podman mounts that source through
`--mount` and only the short `-v` spec cannot spell it -- the long form already emits `--mount`, so
the capability is reachable today and only the short spelling is missing
([#111](https://github.com/modern-python/compose2pod/issues/111)).
