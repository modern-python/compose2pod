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
([ADR-0003](0003-the-shared-namespace-decides-key-classification.md)), which podman expresses
perfectly well even inside a pod (measured, 4.9.3). It carries a refusal site of its own that says
so, rather than the generic unsupported-key message, so the distinction reaches the user who hits
it and not only the reader of this file.
Docker's verdict binds only on the document, not the host: `env_file` existence, `${VAR:?}`, and a
negative on a top-level numeric key are facts about the machine that runs the script and are
deferred to it. `tests/conformance/` runs both oracles for real over a probe matrix generated
from `SERVICE_KEYS | STRUCTURAL_KEYS | IGNORED_SERVICE_KEYS`, so a new key is probed the moment
it is added, and `tests/integration/refusals.py` measures the other side, pairing each volume-family
refusal above with the `--mount` that expresses what Docker says the document means, so a claim that
podman cannot express something cannot go stale unnoticed either. It carries four verdicts, because
four kinds of claim need four experiments: `REFUSALS` for the mounts podman will not make,
`LIMITATIONS` for the ones it would (which is what keeps a limitation from quietly reading as rule
two), and, where the claim is about podman's flag surface rather than a mount, `ABSENT_FLAGS` for a
flag podman does not have and `STUB_FLAGS` for one it has that validates nothing -- a flag accepting
`nonsense` is a worse reason to emit it than a flag that fails, since the script would report
success for something it never did. A gate that every rule-two site has a row is
[#109](https://github.com/modern-python/compose2pod/issues/109) phase 3, and it has to carry the
exemptions first: not every refusal this document names turns out to be one podman makes.
Verdicts are per version, and the supported range is stated rather than implied: the rulings here
are measured against `docker compose config` v5.1.2 and podman 4.9.3, and compose2pod supports
podman 4.9 and up. Rule two reads across that whole range. A form is accepted only where podman
expresses it at the floor as well as at the newest release measured (6.1), so a mount option a
later podman adds is refused until the floor reaches it, and one that a later podman breaks is
refused too. That is one rule where the repo previously had two and stated neither: a nested
`subpath` is refused because podman adds it above the floor (5.1 for an image mount, 5.4 for a
named volume) ([#114](https://github.com/modern-python/compose2pod/issues/114)), and a float tmpfs
`mode` is refused at the other end of the range, where podman 6.0.1's `crun` will not mount it.
The `integration` job pins `ubuntu-24.04` for the same reason: it is the runner that ships the
floor, and on a newer one the job would measure a podman no user of the floor has.
Two residuals are open by design: `depends_on` errors among services outside the
target's closure are accepted here and rejected by Docker
([#87](https://github.com/modern-python/compose2pod/issues/87)), and the drive-qualified *bind*
(`C:\data:/var`) is a limitation rather than rule two, since podman mounts that source through
`--mount` and only the short `-v` spec cannot spell it -- the long form already emits `--mount`, so
the capability is reachable today and only the short spelling is missing
([#111](https://github.com/modern-python/compose2pod/issues/111)).
