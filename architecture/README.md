# Architecture

This directory holds the living truth about what `compose2pod` does **now** —
one file per capability, plus a single `glossary.md` for shared terminology.
Files here carry no frontmatter; they are living prose, dated by git.

## Promotion rule

A change **promotes** its conclusions into the affected
`architecture/<capability>.md` by hand, in the same PR as the code — the edit
rides in the same diff and is reviewed with it, never applied as a separate
post-merge step. The bundle in `planning/changes/` stays as the *why*;
`architecture/` stays as the *what, now*.

Capability files and `architecture/glossary.md` are authored lazily — created
when the first capability or term is worth pinning down, not scaffolded ahead
of need.
