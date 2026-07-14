---
status: accepted
summary: A document `docker compose config` rejects must be rejected here too — a hard, one-way rule binding only on the document's own content, enforced by a differential conformance harness rather than by hand-measurement.
supersedes: null
superseded_by: null
---

# Docker's refusals bind; compose2pod's own refusals must be declared

**Decision:** compose2pod must reject every document `docker compose config`
rejects. It may reject more — that is the honest subset — but only
deliberately, and every such refusal is named in
`architecture/supported-subset.md`. The rule binds only on what the *document*
says, never on the host it is read from.

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

**The soft rule (the subset).** Rejecting what Docker accepts stays allowed:
compose2pod cannot support everything Compose does, and refusing loudly beats
dropping behavior silently. But an *undeclared* over-rejection is a bug, not a
subset — the `on:`-as-a-key regression (`2026-07-14.09`) was exactly that. So
every refusal of something Docker accepts must be named in
`architecture/supported-subset.md` with a reason. Two exist today
(`sysctls: [a]`, `volumes: [a]`); both are already declared.

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
