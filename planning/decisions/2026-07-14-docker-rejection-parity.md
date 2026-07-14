---
status: accepted
summary: A document `docker compose config` rejects must be rejected here too; one it accepts is accepted whenever podman can express it, and where it cannot yet, that is a tracked limitation rather than a defect. Binds only on the document's own content; enforced by a differential conformance harness.
supersedes: null
superseded_by: null
---

# Docker's refusals bind; podman decides what we can accept

**Decision:** two rules, in one direction each.

- **Docker rejects ⇒ compose2pod rejects.** Hard, no exceptions.
- **Docker accepts ⇒ compose2pod accepts, when podman can express it and it
  means something inside a pod.** Where it cannot yet, that is a **current
  limitation** — a deferred piece of the subset, tracked in
  `planning/deferred.md` — not a bug, and not a licence to refuse on taste.

The rule binds only on what the *document* says, never on the host it is read
from.

## Context

The rule was already the project's working belief, but it had never been
written down, and the two places that stated it disagreed:

- `architecture/supported-subset.md` — "Parity on *refusal* is what the
  drop-in role demands; it is not parity for its own sake, and the package
  keeps its documented divergences elsewhere."
- `changes/2026-07-14.09` — "Refusing a file Docker runs is the one direction
  that must never happen."

Read literally the second forbids the honest subset, which refuses
`network_mode`, long-form volumes and `1h30m` healthcheck intervals — all
files Docker runs. Neither statement was wrong; neither was precise.

Nothing checked either of them. Five consecutive changes
(`2026-07-14.05`…`.09`) each hand-found a single divergence and fixed it, which
is what a missing invariant looks like from the outside. A measured sweep (see
`audits/2026-07-14-docker-rejection-parity.md`) then found **113 more** across
672 probes.

## Decision & rationale

**The hard rule (soundness).** `accepted(compose2pod) ⊆ accepted(docker)`. No
exceptions. compose2pod is a drop-in replacement for `docker compose` on
rootless runners: the file it converts is the file the developer runs locally.
Accepting a document Docker refuses emits a script for a file that is already
broken upstream, turning a hard error into a green CI run. That false green is
the single failure the gate exists to prevent, so the rule carries no residue —
`2026-07-14.10` writes the four value grammars (size, number, duration, port)
needed to make it literally true rather than approximately true.

**The second rule: podman decides, not documentation.** An earlier draft of this
decision said an over-rejection was fine "as long as it is declared". That test
was worthless — it is a standard met by typing a sentence, and it let taste
masquerade as design. The test is **podman**:

- **Legitimate refusal — the capability cannot work.** `network_mode` (every
  service shares the pod's namespace), per-service `dns` (one `/etc/resolv.conf`
  per pod), `stop_signal` (the script force-removes the pod and never stops a
  container gracefully), `sysctls: ["a"]` (no `=`, so there is no value to put in
  a `--sysctl` flag). These stay refused, permanently, and the reason is podman's,
  not ours.
- **Not a licence to refuse a *form*.** Where compose2pod supports the
  capability, an unusual spelling of it that podman can honor must be accepted.
  A quoted boolean (`tty: "true"` — podman only ever sees `--tty` or nothing) and
  a compound duration (`interval: 1h30m` — 5400s, and the value only paces a
  polling loop) are forms, not capabilities.

**An over-rejection is a limitation, not a bug.** Where we refuse a form podman
could express, we are behind, not wrong: it is a deferred part of the subset,
recorded in `planning/deferred.md` with a revisit trigger, and workable later.
This distinction is what keeps the rule bounded — it forbids refusing a spelling
on taste, without obliging compose2pod to implement every Compose feature podman
happens to support. A key outside the subset entirely is still refused loudly;
that is the honest subset, and it is unaffected.

**The carve-out: the document, not the host.** Docker's verdict binds only when
it is a property of the document alone. `docker compose config` also rejects on
host state — `env_file: app.env` fails with `env file not found` when the file
is absent, and `${VAR:?msg}` fails when `VAR` is unset in the *reading* shell.
compose2pod generates a script that runs somewhere else, where that file is
checked out and that variable is set; deferring both to script-run time is
deliberate (see `architecture/supported-subset.md`, Variable interpolation).
Docker's rejection there is a fact about the developer's laptop, not about the
document, so it cannot bind — and a harness that enforced it would demand
checks that are actively wrong.

**Enforcement is executable, not asserted.** A hand-measured table is a claim
typed into a file: it cannot catch a construct nobody thought to enumerate,
which is precisely how the last five divergences survived. `tests/conformance/`
instead runs both oracles for real, and generates its probe matrix from
`SERVICE_KEYS | STRUCTURAL_KEYS | IGNORED_SERVICE_KEYS` — so **a new key in the
registry is probed the moment it is added**, and the rule cannot decay as the
subset grows. `docker compose config` needs no daemon, so the harness is a
plain CI job.

## Revisit trigger

- **Docker's own validation changes** such that a construct compose2pod
  correctly accepts starts being refused by `docker compose config` — the
  harness will fail, and the question becomes which version of Compose the
  invariant tracks (it currently tracks whatever the CI runner ships).
- **The carve-out grows.** If a third class of host-dependent rejection appears
  beyond `env_file` existence and `${VAR:?}` interpolation, the "document, not
  the host" line is doing more work than one sentence can carry and needs its
  own rule.
- **A grammar validator drifts from Docker's** — the port or size grammar
  starts refusing a value Docker accepts — turning the soundness fix into an
  over-rejection. The harness catches this too, in the other direction.
- **A refusal is justified by "we don't parse that yet" rather than by podman.**
  That is the failure mode the second rule exists to catch: the refusal is a
  limitation to be tracked and worked off, not a design position to be defended.
