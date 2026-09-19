# Zero-dependency core

The core has no runtime dependencies: JSON via the stdlib always works and PyYAML sits behind the
`[yaml]` extra, because the tool exists for minimal CI images where even a compiled wheel may not
install. No compose-spec parser library is used either. The candidates require `pydantic-core`,
are early single-maintainer projects, and would not remove the subset gate anyway, since a
full-spec parser accepts constructs a single pod cannot express.
