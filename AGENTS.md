# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

`compose2pod` is a stdlib-only Python package and CLI that converts a Docker Compose document into a
POSIX `sh` script running one service and its dependencies as a single Podman pod;
[`CONTEXT.md`](CONTEXT.md) opens with what it does and owns the vocabulary — read it before naming a
concept in code, a test name, or an issue title.

## Commands

`just` (task runner) and `uv` (package manager). The [`justfile`](justfile) is the source of truth —
`just --list`, or read it. Every recipe carries its intent as a comment. The one thing it does not
say: **never run bare `ruff check`** — `[tool.ruff]` sets `fix = true` and `unsafe-fixes = true`, so
an unqualified invocation rewrites the tree.

## Architecture

The pipeline is `read → resolve_extends → validate → emit_script`, one module per stage, each named
for what it does; read them. What reading a single module will **not** tell you:

- `validate()` (`parsing.py`) is the gate, but it is not the only door: `emit._plan` — the single
  traversal both public entry points (`emit_script`, `referenced_variables`) project from — calls
  `validate()` itself, so a library caller cannot reach either with a document the gate would
  reject. That call site is load-bearing, not defensive.
- The `SERVICE_KEYS` / `STRUCTURAL_KEYS` split in `keys.py` is a design ruling about which keys can
  share one `emit(value)` interface, not a leftover. A new key belongs in the registry only if
  it fits that signature without widening it.
- `compose2pod/podman.py` is not a pipeline stage: it holds every claim the tool makes about
  podman's behaviour, so a refusal citing podman can be enumerated rather than grepped for.
  `tests/test_podman_claim_coverage.py` requires each claim to be measured against real podman.
  A new refusal whose reason is podman's belongs there, or the gate goes red.
- `tests/conformance/` generates its probe matrix from
  `SERVICE_KEYS | STRUCTURAL_KEYS | IGNORED_SERVICE_KEYS`, so adding a key probes it against
  `docker compose config` automatically. It is CI-only (`just test-conformance`,
  needs the docker CLI; no daemon). Integration tests (`just test-integration`) need real podman.

## Workflow

Every link in `README.md` must be absolute: `https://github.com/modern-python/<repo>/blob/main/<path>`,
or `.../tree/main/<path>` for a directory. Never a relative path: `README.md` is also the PyPI long
description, and PyPI does not rewrite relative links, so a relative one 404s on the package page.

## Code Style

- The core package stays **zero-dependency**; PyYAML is the optional `[yaml]` extra and nothing else
  is added.
- Commit messages: conventional-commit subjects, no `Co-authored-by` trailer.

## Agent skills

### Issue tracker

GitHub issues on `modern-python/compose2pod`, via `gh`. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical roles, each label string equal to its name. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.
