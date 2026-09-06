# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`compose2pod` is a stdlib-only Python package and CLI that converts a Docker Compose document into a
POSIX `sh` script running one service and its dependencies as a single Podman pod;
[`CONTEXT.md`](CONTEXT.md) opens with what it does and owns the vocabulary — read it before naming a
concept in code, a test name, or an issue title.

## Commands

`just` (task runner) and `uv` (package manager). The [`justfile`](justfile) is the source of truth —
`just --list`, or read it; every recipe carries its intent as a comment. The one thing it does not
say: **never run bare `ruff check`** — `[tool.ruff]` sets `fix = true` and `unsafe-fixes = true`, so
an unqualified invocation rewrites the tree.

## Architecture

The pipeline is `read → resolve_extends → validate → emit_script`, one module per stage, each named
for what it does; read them. What reading a single module will **not** tell you:

- `validate()` (`parsing.py`) is the gate, but it is not the only door: `emit._plan` — the single
  traversal both public entry points (`emit_script`, `referenced_variables`) project from — calls
  `validate()` itself, so a library caller cannot reach either with a document the gate would
  reject. That call site is load-bearing, not defensive
  ([ADR-0006](docs/adr/0006-reject-parse-dont-validate.md)).
- The `SERVICE_KEYS` / `STRUCTURAL_KEYS` split in `keys.py` is a design ruling about which keys can
  share one `emit(value)` interface, not a leftover
  ([ADR-0008](docs/adr/0008-reject-structural-key-registry.md),
  [ADR-0013](docs/adr/0013-volumes-stays-hand-rolled.md)). A new key belongs in the registry only if
  it fits that signature without widening it.
- `tests/conformance/` generates its probe matrix from
  `SERVICE_KEYS | STRUCTURAL_KEYS | IGNORED_SERVICE_KEYS`, so adding a key probes it against
  `docker compose config` automatically. It is the executable form of
  [ADR-0009](docs/adr/0009-docker-rejection-parity.md), and it is CI-only (`just test-conformance`,
  needs the docker CLI; no daemon). Integration tests (`just test-integration`) need real podman.

## Workflow

**The spec for a change is its PR body**, not a committed file: why, design, non-goals, verification,
reviewed with the diff. There is no change file and no lane to choose. A trivial PR (typo, dep bump,
formatter, CI tweak) ships a conventional-commit title with no body ceremony.

Two things outlive the PR, and there are exactly two places to put them: an alternative **rejected**
with reasoning becomes an ADR in [`docs/adr/`](docs/adr/) (`NNNN-slug.md`, sequential, with a revisit
trigger), and real work **not scheduled** becomes a GitHub issue. There is no third state, and no
separate truth-home directory — a behaviour change is reviewed with the diff, not promoted to a page.

### Where a fact goes

Four homes, one owner each:

| Home | Holds |
|---|---|
| `compose2pod/` | anything readable from the module — the default |
| a named test | an **invariant**: must stay true, and a change could silently break it |
| `docs/adr/` | a rejected alternative, with the reasoning that would otherwise be re-litigated |
| `README.md` | anything a user needs |

Before writing a line anywhere:

> Can an agent get this by reading `compose2pod/`? → **don't write it.**
> Would a wrong change here fail a test? → it belongs **in the test**, not in prose.
> Does a user need it? → **`README.md`**.
> Otherwise it does not get written.

**Prose about mechanism has no home. There is no file to add a paragraph to.** This file included:
it is always loaded, so a line that restates a docstring, a justfile comment, or `pyproject.toml`
costs every turn and rots in two places at once. This package's modules carry unusually complete
docstrings; restating one here is the failure mode to watch for.

An invariant is a test whose name is the claim, with a docstring opening `INVARIANT:` and a second
paragraph naming **what breaks it** — design rationale, not a report of what this one test catches.
Nothing enforces that docstring shape; it is read at review time. A relative link to an ADR *is*
checked — CI runs lychee `--offline` over every `.md` — but a path named in a docstring or a comment
is not. Both ADRs and `INVARIANT:` docstrings ratchet: nothing prunes a record once its call is
settled. Keeping them lean is a standing habit.

## Code Style

- The core package stays **zero-dependency**; PyYAML is the optional `[yaml]` extra and nothing else
  is added without overturning [ADR-0002](docs/adr/0002-zero-dependency-core.md).
- Suppress type errors with `ty: ignore`, never `type: ignore`.
- Commit messages: conventional-commit subjects, no `Co-authored-by` trailer.
