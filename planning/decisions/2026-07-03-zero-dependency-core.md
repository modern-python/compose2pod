---
status: accepted
summary: Core package stays stdlib-only with PyYAML as an optional [yaml] extra; no compose-parser dependency.
supersedes: null
superseded_by: null
---

# Zero-dependency core, no compose-parser dependency

**Decision:** The `compose2pod` core package has zero runtime dependencies
(stdlib only). PyYAML is shipped only behind the optional `[yaml]` extra.
The core does not depend on any compose-spec parser library.

## Context

`compose2pod` reads a Docker Compose document and must decide how it is
loaded (JSON/YAML) and validated against the supported subset. Two
dependency questions came up while designing the package:

1. Should YAML parsing be a hard dependency, so the CLI always accepts
   `docker-compose.yml` directly?
2. Should the core adopt an existing compose-spec parser library
   (`compose-spec`, `compose-pydantic`) instead of hand-rolled subset
   validation, to get broader spec coverage for free?

The primary differentiator for `compose2pod` is that it installs with no
compiled wheels and runs in minimal Python images (the same CI containers
that motivate the tool in the first place — see the design's "Why this
exists"). Any hard dependency, especially one with a compiled wheel,
undermines that.

## Decision & rationale

- **YAML stays optional.** JSON parsing via stdlib `json` is always
  available. YAML parsing via PyYAML is the optional `[yaml]` extra; without
  it, `--format yaml` errors with an actionable message pointing at `pip
  install compose2pod[yaml]` or piping through `yq -o=json`. This keeps the
  dependency-constrained-CI path (where even PyYAML may not be installable)
  fully functional.
- **No compose-spec parser dependency.** Researched candidates
  (`compose-spec`, `compose-pydantic`) both require pydantic v2 (a compiled
  `pydantic-core` wheel) and are early-stage single-maintainer 0.x projects.
  Adopting one would:
  (a) break the zero-dependency differentiator for the core,
  (b) not actually remove the subset validation — full-spec parsers
  permissively accept constructs (`configs`, `secrets`, `profiles`,
  `deploy`, long-form volumes, ...) that `compose2pod` cannot turn into a
  single pod, so a subset gate is still required regardless of what parses
  the document, and
  (c) add supply-chain risk (compiled wheel, single maintainer, early 0.x)
  to a package whose whole pitch is minimal-footprint installability.
- A future optional `[strict]` extra could cross-validate against
  `compose-spec` for users who want full-spec checking, but that is
  deliberately out of scope for v1 (YAGNI) — no user need has been
  identified yet.

## Revisit trigger

- A mature, pure-Python (no compiled wheel) compose-spec parser reaches a
  stable 1.x release with more than one maintainer, and a concrete user
  need for full-spec validation (beyond the subset `compose2pod` supports)
  emerges.
- PyYAML itself becomes uninstallable in a target CI environment that
  `compose2pod` needs to support, forcing a rethink of the YAML story.
