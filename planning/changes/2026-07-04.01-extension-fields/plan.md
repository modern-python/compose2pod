# extension-fields — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps
> use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Accept Compose `x-` extension fields at every level `validate()`
inspects, silently, so anchor-based compose files convert instead of erroring.

**Spec:** [`design.md`](./design.md)

**Branch:** `feat/extension-fields` (already created; the spec commit lives here).

**Commit strategy:** Per-task commits, conventional-commit subjects, **no**
`Co-authored-by` trailer (project rule).

## Global Constraints

Copied verbatim from `CLAUDE.md` — every task must respect these:

- Core package has **zero runtime dependencies** (stdlib only); PyYAML is only
  the optional `[yaml]` extra. This change adds no imports.
- All imports at module level. Annotate every function argument.
- Use `ty: ignore`, never `type: ignore`.
- `just lint-ci` must pass clean. **Never run bare `ruff check`** — the repo
  config autofixes destructively. Use `just lint` (safe) or `just lint-ci`.
- `just test-ci` must pass at **100% line coverage** (`--cov-fail-under=100`).
- `just check-planning` must pass before pushing.
- The `x-` prefix match is lowercase exactly (`key.startswith("x-")`), as the
  Compose spec mandates.

---

### Task 1: Accept `x-` extension fields in `validate()`

**Files:**
- Modify: `compose2pod/parsing.py` (three key-validation loops)
- Test: `tests/test_parsing.py` (add to `TestValidate`)
- Test: `tests/test_cli.py` (add end-to-end anchor test to `TestMain`)

Teach the three key-inspection loops in `validate()` to skip any `x-`-prefixed
key silently, and prove it end to end through the YAML/anchor pipeline.

- [ ] **Step 1: Write the failing unit tests**

  Add these three methods to the `TestValidate` class in
  `tests/test_parsing.py`:

  ```python
      def test_top_level_extension_key_is_accepted(self) -> None:
          compose = {"x-application-defaults": {"build": {}}, "services": {"app": {"image": "x"}}}
          assert validate(compose) == []

      def test_service_extension_key_is_accepted_silently(self) -> None:
          warnings = validate({"services": {"app": {"image": "x", "x-labels": {"team": "a"}}}})
          assert warnings == []

      def test_healthcheck_extension_key_is_accepted(self) -> None:
          compose = {"services": {"app": {"image": "x", "healthcheck": {"test": "true", "x-note": "n"}}}}
          assert validate(compose) == []
  ```

- [ ] **Step 2: Run the unit tests to verify they fail**

  Run: `uv run --no-sync pytest tests/test_parsing.py -k extension -v`
  Expected: 3 FAILs — top-level raises `unsupported top-level keys:
  ['x-application-defaults']`, service raises `unsupported key 'x-labels'`,
  healthcheck raises `unsupported healthcheck key 'x-note'`.

- [ ] **Step 3: Implement the three skips in `compose2pod/parsing.py`**

  In `_validate_service`, add an `x-` skip at the top of the service-key loop
  (before the `IGNORED_SERVICE_KEYS` branch):

  ```python
      for key in sorted(svc):
          if key.startswith("x-"):
              continue
          if key in IGNORED_SERVICE_KEYS:
              warnings.append(f"service {name!r}: ignoring '{key}'")
          elif key not in SUPPORTED_SERVICE_KEYS:
              msg = f"service {name!r}: unsupported key '{key}'"
              raise UnsupportedComposeError(msg)
  ```

  In the same function, add an `x-` skip to the healthcheck-key loop:

  ```python
      for key in sorted(svc.get("healthcheck") or {}):
          if key.startswith("x-"):
              continue
          if key not in SUPPORTED_HEALTHCHECK_KEYS:
              msg = f"service {name!r}: unsupported healthcheck key '{key}'"
              raise UnsupportedComposeError(msg)
  ```

  In `validate`, replace the top-level unknown-key computation (the
  `unknown_top = set(compose) - SUPPORTED_TOP_LEVEL_KEYS` line) with a
  comprehension that also drops `x-` keys:

  ```python
      unknown_top = {k for k in compose if k not in SUPPORTED_TOP_LEVEL_KEYS and not k.startswith("x-")}
  ```

- [ ] **Step 4: Run the unit tests to verify they pass**

  Run: `uv run --no-sync pytest tests/test_parsing.py -k extension -v`
  Expected: 3 PASS.

- [ ] **Step 5: Write the failing end-to-end anchor test**

  Add this method to the `TestMain` class in `tests/test_cli.py` (exercises a
  top-level `x-` anchor block, a `<<:` merge, and a service-level `x-` key
  through the real `_read_compose` → `validate` → `emit_script` pipeline):

  ```python
      def test_yaml_anchor_extension_fields_convert(
          self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
      ) -> None:
          yaml_text = (
              "x-defaults: &defaults\n"
              "  image: base:latest\n"
              "services:\n"
              "  app:\n"
              "    <<: *defaults\n"
              "    x-meta: keep\n"
          )
          rc = run_main(yaml_text, ["--target", "app", "--image", "i", "--format", "yaml"], monkeypatch)
          out = capsys.readouterr()
          assert rc == 0
          assert "podman pod create" in out.out
  ```

- [ ] **Step 6: Run the end-to-end test to verify it passes**

  Run: `uv run --no-sync pytest tests/test_cli.py -k anchor_extension -v`
  Expected: PASS (implementation from Step 3 already makes it green; this test
  guards the full pipeline, not just `validate()`).

- [ ] **Step 7: Run the full suite at 100% coverage**

  Run: `just test-ci`
  Expected: all pass, coverage 100%. If any new branch is uncovered, the run
  fails — the three tests above each hit one of the three new `x-` skips.

- [ ] **Step 8: Lint**

  Run: `just lint-ci`
  Expected: clean. (If it flags formatting, run `just lint` to autofix safely,
  then re-run `just lint-ci`.)

- [ ] **Step 9: Commit**

  ```bash
  git add compose2pod/parsing.py tests/test_parsing.py tests/test_cli.py
  git commit -m "feat: accept compose x- extension fields"
  ```

---

### Task 2: Document the supported subset

**Files:**
- Create: `architecture/supported-subset.md`
- Modify: `README.md` (the "Supported compose subset" section)

Pin the accept/ignore/reject rules — including the new `x-` rule — into the
living architecture doc, and point the README at it.

- [ ] **Step 1: Create `architecture/supported-subset.md`**

  Write this file exactly (no frontmatter — living prose, per the
  `architecture/` convention):

  ```markdown
  # Supported compose subset

  compose2pod converts an honest subset of Docker Compose and refuses the rest
  loudly rather than silently dropping behavior. `validate()`
  (`compose2pod/parsing.py`) is the gate: anything it does not recognize either
  warns (ignored, behavior-neutral inside a single pod) or raises
  `UnsupportedComposeError`.

  ## Top-level keys

  - **Supported:** `services` (required, non-empty), `version`, `name`,
    `networks`.
  - **Ignored (warns):** `networks` — all services share the pod's single
    network namespace, so top-level network definitions have no effect.
  - **Extension fields:** any key prefixed `x-` is accepted and ignored
    silently, per the Compose spec. This is what lets a document hold shared
    config in a top-level `x-*` block for reuse via YAML anchors.
  - Everything else raises.

  ## Service keys

  - **Supported:** `image`, `build`, `command`, `environment`, `env_file`,
    `volumes`, `healthcheck`, `depends_on`, `networks`.
  - **Ignored (warns):** `ports`, `restart`, `stdin_open`, `tty` — meaningless
    or irrelevant inside a single shared-namespace pod.
  - **Extension fields:** any `x-`-prefixed service key is accepted and ignored
    silently.
  - Everything else raises.

  ## Healthcheck keys

  - **Supported:** `test`, `interval`, `timeout`, `retries`, `start_period`.
  - **Extension fields:** any `x-`-prefixed healthcheck key is accepted and
    ignored silently.
  - Everything else raises.

  ## Volumes

  Short bind-mount syntax only (`source:target`). The source must be a host path
  (starts with `.` or `/`); named volumes and the long mapping form raise.

  ## depends_on

  All three conditions are honored: `service_started`, `service_healthy`,
  `service_completed_successfully`. A `service_healthy` dependency on a service
  with no usable healthcheck raises.

  ## YAML anchors and merge keys

  Anchors (`&name` / `*name`) and the merge key (`<<:`) need no handling in
  compose2pod: PyYAML's `safe_load` resolves them at load time, so `validate()`
  and `emit` see already-merged service mappings. JSON input has no anchors but
  can still carry literal `x-` extension keys, handled identically.
  ```

- [ ] **Step 2: Update the README subset section**

  In `README.md`, replace the paragraph under `## Supported compose subset`
  (the one starting "compose2pod supports an honest subset...") with:

  ```markdown
  compose2pod supports an honest subset and errors clearly on anything outside
  it: `image`/`build`, `command`, `environment`/`env_file`, short-form bind
  `volumes`, `healthcheck` (CMD/CMD-SHELL), `depends_on` (all conditions), and
  network `aliases`. Compose extension fields (any `x-`-prefixed key) and YAML
  anchors are accepted as-is, so a top-level `x-*` anchor block for shared
  config just works. See `architecture/supported-subset.md` for the full
  accept/ignore/reject matrix.
  ```

- [ ] **Step 3: Verify the eof-fixer / formatting on docs**

  Run: `just lint-ci`
  Expected: clean (eof-fixer requires a trailing newline on both files).

- [ ] **Step 4: Commit**

  ```bash
  git add architecture/supported-subset.md README.md
  git commit -m "docs: document supported subset and x- extension fields"
  ```

---

### Task 3: Finalize the change bundle and run all gates

**Files:**
- Modify: `planning/changes/2026-07-04.01-extension-fields/design.md` (finalize
  `summary:` if wording needs it)

Confirm the whole change is green and the planning bundle is ship-ready.

- [ ] **Step 1: Confirm the `design.md` summary reads as the realized result**

  Open `planning/changes/2026-07-04.01-extension-fields/design.md`. The
  `summary:` line should state what shipped:
  `Accept Compose x- extension fields at every validated level, ignoring them
  silently.` Adjust only if the implementation diverged; otherwise leave it.

- [ ] **Step 2: Run the full gate set**

  ```bash
  just lint-ci
  just test-ci
  just check-planning
  ```
  Expected: all three succeed; coverage 100%; `planning: OK`.

- [ ] **Step 3: Commit any bundle edit (skip if nothing changed)**

  ```bash
  git add planning/changes/2026-07-04.01-extension-fields/design.md
  git commit -m "docs: finalize extension-fields change summary"
  ```

- [ ] **Step 4: Hand off to finishing-a-development-branch**

  Push `feat/extension-fields` and open a PR (never local-merge, per project
  workflow). Use the `superpowers:finishing-a-development-branch` skill to do
  this and watch CI.

---

## Self-review notes

- **Spec coverage:** Design §1 (three-level `x-` skip) → Task 1 Steps 1-4;
  silent decision → Task 1 Step 1 `test_service_extension_key_is_accepted_silently`
  (`warnings == []`); end-to-end anchor round-trip → Task 1 Steps 5-6; Design §2
  (seed `architecture/supported-subset.md`) → Task 2 Step 1; README note → Task 2
  Step 2; testing/coverage → Task 1 Step 7.
- **No placeholders:** every code and doc step shows full content.
- **Type/name consistency:** `validate()` signature and `UnsupportedComposeError`
  messages match `parsing.py` exactly; test helper `run_main` and fixtures match
  `tests/test_cli.py`.
