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

Real work **not scheduled** becomes a GitHub issue.

An invariant is a test whose name is the claim, with a docstring opening `INVARIANT:` and a second
paragraph naming **what breaks it** — design rationale, not a report of what this one test catches.
Nothing enforces that docstring shape; it is read at review time.

## Code Style

- The core package stays **zero-dependency**; PyYAML is the optional `[yaml]` extra and nothing else
  is added without overturning [ADR-0002](docs/adr/0002-zero-dependency-core.md).
- Suppress type errors with `ty: ignore`, never `type: ignore`.
- Commit messages: conventional-commit subjects, no `Co-authored-by` trailer.
