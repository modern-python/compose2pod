# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`compose2pod` — a dependency-free, stdlib-only Python package and CLI that
converts a Docker Compose document into a POSIX `sh` script running the
services as a single Podman pod. See `README.md` for usage and the supported
compose subset.

## Workflow

This repo follows Artur Shiriev's canonical planning convention
(`lesnik512/planning-convention`, applied version in
`planning/.convention-version`). Before starting any non-trivial change, read
[`planning/README.md`](planning/README.md)'s **Quick path** section — it is
the authoritative process for choosing a lane (Full / Lightweight / Tiny) and
shaping the change bundle. Run `just check-planning` before pushing.

## Architecture

`architecture/` (repo root) holds the living truth about what the system does
now — one file per capability, plus `architecture/glossary.md` for shared
terminology. When a change alters a capability's behavior, update the
matching `architecture/<capability>.md` in the same PR.

## Quality gates

- Core package has **zero runtime dependencies** (stdlib only); PyYAML is
  only the optional `[yaml]` extra.
- All imports at module level — the optional PyYAML import uses the
  module-level `try/except ImportError` pattern, never an in-function import.
- Annotate every function argument. Use `ty: ignore`, never `type: ignore`.
- `just lint-ci` (ruff `select=ALL` with `--no-fix`, ruff format, ty,
  eof-fixer, planning check) must pass clean. Never run bare `ruff check` —
  the repo config autofixes destructively.
- `just test-ci` must pass at **100% line coverage** (`--cov-fail-under=100`).
- Commit messages: conventional-commit subjects, no `Co-authored-by` trailer.
