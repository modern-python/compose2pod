# compose2pod Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the chats compose-to-podman-pod converter into the standalone public package `compose2pod`, split into focused modules, with the four review fixes, an optional YAML input path, and CI.

**Architecture:** A dependency-free, stdlib-only Python package (root layout, `uv_build`) that reads a Docker Compose document (dict) and emits a POSIX `sh` script running the services as a single Podman pod. `exceptions` → `healthcheck`/`graph` → `parsing`/`emit` → `cli` → `__main__`, acyclic. YAML input is an optional extra; the core reads JSON.

**Tech Stack:** Python 3.10+, stdlib only in the core, PyYAML behind the `[yaml]` extra; uv + `uv_build`, `just`, ruff (`select=ALL`), ty, pytest.

## Global Constraints

- Core package has **zero runtime dependencies** (stdlib only). PyYAML is only the optional `[yaml]` extra.
- **All imports at module level** (Artur's rule) — the optional PyYAML import uses the module-level `try/except ImportError` pattern, never an in-function import.
- **Annotate every function argument.** For type-ignores use `ty: ignore`, never `type: ignore`.
- Quality gates: ruff `select=ALL` (config already in `pyproject.toml`; it **autofixes destructively** — always use `ruff check --no-fix`), ty clean, `eof-fixer` clean, `just test-ci` at **100% line coverage** (`--cov-fail-under=100`).
- The emitted script is POSIX `sh` (`set -eu`).
- Warnings/errors printed by the CLI are prefixed `compose2pod: ` (renamed from the chats prototype's `compose-to-pod: `).
- Package location: `/Users/kevinsmith/src/pypi/compose2pod` (this repo). Source of truth for behavior is the chats prototype `bin/compose_to_pod.py`; the four fixes below are the only behavior changes.
- Environment note: this repo's deps are all on public PyPI (pytest, ruff, ty, eof-fixer, PyYAML), so `just install` and `just test` run locally without the internal artifactory.
- Commit messages: conventional-commit subjects, **no `Co-authored-by` trailer**.

## The four review fixes (folded into the ports below)

1. **Non-dict guard** — `validate` raises `UnsupportedComposeError` when the parsed document is not a mapping (Task 3).
2. **Honor `start_period`/`retries`** — `run_flags` passes them to `podman run` as `--health-start-period`/`--health-retries`; the wait budget is unchanged (Task 4).
3. **Merge duplicate constant** — one `_CMD_MIN_LENGTH = 2` in `healthcheck.py` (Task 1).
4. **Split `emit_script`** — the target-run branch is extracted into `_emit_target` (Task 4).

## File structure

- `compose2pod/exceptions.py` — `UnsupportedComposeError`.
- `compose2pod/healthcheck.py` — `has_healthcheck`, `health_cmd`, `interval_seconds`.
- `compose2pod/graph.py` — `depends_on`, `hostnames`, `startup_order`.
- `compose2pod/parsing.py` — subset constants + `validate` (+ `_validate_service`, `_validate_depends_on`).
- `compose2pod/emit.py` — `EmitOptions`, `image_for`, `command_tokens`, `run_flags`, `emit_script` (+ `_emit_target`, `_run_tokens`, `_render`), `HEALTHY_WAIT_BUDGET_SECONDS`.
- `compose2pod/cli.py` — `main`, format detection, JSON/YAML loading, `POD_NAME_PATTERN`.
- `compose2pod/__main__.py` — `python -m compose2pod`.
- `compose2pod/__init__.py` — public exports.
- `tests/test_healthcheck.py`, `tests/test_graph.py`, `tests/test_parsing.py`, `tests/test_emit.py`, `tests/test_cli.py`.
- `.github/workflows/{ci,_checks,release}.yml`.

Note: `exceptions.py` is a leaf module added beyond the spec's listed modules to keep imports acyclic (`graph`, `healthcheck`, `parsing`, `emit`, `cli` all import the error from it). It matches `modern-di`'s `exceptions.py` convention.

A shared fixture `CHATS_COMPOSE` is duplicated only where needed; to keep tests DRY, define it once in `tests/conftest.py` as a fixture.

---

### Task 0: Toolchain baseline

**Files:**
- Create: `tests/conftest.py`
- Modify: none (scaffold `pyproject.toml`, `justfile` already exist)

- [ ] **Step 1: Install deps**

Run: `cd /Users/kevinsmith/src/pypi/compose2pod && just install`
Expected: uv resolves from public PyPI, creates `.venv`, installs the `yaml` extra + `lint` group. No error.

- [ ] **Step 2: Add the shared fixture**

Create `tests/conftest.py`:

```python
import pytest


@pytest.fixture
def chats_compose() -> dict:
    return {
        "services": {
            "application": {
                "build": {
                    "context": ".",
                    "dockerfile": "./Dockerfile",
                    "args": ["ARTIFACTORY_USER=$ARTIFACTORY_USER", "ARTIFACTORY_PASSWORD=$ARTIFACTORY_PASSWORD"],
                },
                "restart": "always",
                "volumes": [".:/srv/www/"],
                "depends_on": {
                    "migrations": {"condition": "service_completed_successfully"},
                    "keydb": {"condition": "service_healthy"},
                },
                "env_file": "tests.env",
                "stdin_open": True,
                "tty": True,
                "ports": ["9991:9991"],
                "command": ["python", "-m", "chats.api"],
            },
            "migrations": {
                "build": {"context": ".", "dockerfile": "./Dockerfile", "args": []},
                "command": ["alembic", "upgrade", "head"],
                "volumes": [".:/srv/www/"],
                "depends_on": {"db": {"condition": "service_healthy"}},
                "env_file": "tests.env",
            },
            "db": {
                "image": "postgres:13.5-alpine",
                "restart": "always",
                "environment": ["POSTGRES_PASSWORD=password"],
                "healthcheck": {
                    "test": ["CMD-SHELL", "pg_isready -U database -d database"],
                    "interval": "1s",
                    "timeout": "5s",
                    "retries": 15,
                },
                "ports": ["5432:5432"],
            },
            "keydb": {
                "image": "keydb-for-ci:v6.3.0",
                "networks": {"default": {"aliases": ["keydb-test-server-0"]}},
                "healthcheck": {
                    "test": ["CMD", "keydb-cli", "-p", "26379", "sentinel", "get-master-addr-by-name", "mymaster"],
                    "interval": "1s",
                    "timeout": "15s",
                    "retries": 5,
                },
            },
        },
    }
```

- [ ] **Step 3: Commit**

```bash
git add tests/conftest.py
git commit -m "test: add shared chats_compose fixture"
```

---

### Task 1: exceptions + healthcheck modules

**Files:**
- Create: `compose2pod/exceptions.py`, `compose2pod/healthcheck.py`
- Test: `tests/test_healthcheck.py`

**Interfaces:**
- Produces: `UnsupportedComposeError(Exception)`; `has_healthcheck(svc: dict[str, Any]) -> bool`; `health_cmd(test: object) -> str | None`; `interval_seconds(duration: object) -> int`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_healthcheck.py`:

```python
import pytest
from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.healthcheck import has_healthcheck, health_cmd, interval_seconds


class TestHealthCmd:
    def test_cmd_shell_form(self) -> None:
        assert health_cmd(["CMD-SHELL", "pg_isready -U db"]) == "pg_isready -U db"

    def test_cmd_list_form_becomes_json(self) -> None:
        assert health_cmd(["CMD", "keydb-cli", "-p", "26379"]) == '["keydb-cli", "-p", "26379"]'

    def test_plain_string_passes_through(self) -> None:
        assert health_cmd("true") == "true"

    def test_none_and_disable_forms(self) -> None:
        assert health_cmd(None) is None
        assert health_cmd(["NONE"]) is None

    def test_unknown_kind_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="WHATEVER"):
            health_cmd(["WHATEVER", "x"])

    def test_empty_list_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="unsupported healthcheck test"):
            health_cmd([])

    def test_non_list_non_string_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="unsupported healthcheck test"):
            health_cmd(42)

    def test_cmd_shell_without_argument_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="unsupported healthcheck test"):
            health_cmd(["CMD-SHELL"])

    def test_cmd_without_command_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="unsupported healthcheck test"):
            health_cmd(["CMD"])


class TestIntervalSeconds:
    def test_seconds_suffix(self) -> None:
        assert interval_seconds("5s") == 5

    def test_minutes_suffix(self) -> None:
        assert interval_seconds("2m") == 120

    def test_none_defaults_to_one(self) -> None:
        assert interval_seconds(None) == 1

    def test_milliseconds_floor_to_one(self) -> None:
        assert interval_seconds("500ms") == 1

    def test_milliseconds_above_one_second(self) -> None:
        assert interval_seconds("5000ms") == 5

    def test_int_value_passes_through(self) -> None:
        assert interval_seconds(5) == 5

    def test_float_value_below_one_floors_to_one(self) -> None:
        assert interval_seconds(0.4) == 1


class TestHasHealthcheck:
    def test_true_when_test_present(self) -> None:
        assert has_healthcheck({"healthcheck": {"test": ["CMD-SHELL", "true"]}}) is True

    def test_false_when_missing(self) -> None:
        assert has_healthcheck({"image": "x"}) is False

    def test_false_when_none_test(self) -> None:
        assert has_healthcheck({"healthcheck": {"test": "NONE"}}) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test tests/test_healthcheck.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'compose2pod.healthcheck'`.

- [ ] **Step 3: Implement `exceptions.py`**

```python
"""Exceptions for compose2pod."""


class UnsupportedComposeError(Exception):
    """Raised when the compose file uses a construct outside the supported subset."""
```

- [ ] **Step 4: Implement `healthcheck.py`** (fix #3: single `_CMD_MIN_LENGTH`)

```python
"""Healthcheck translation: compose healthcheck -> podman --health-* values."""

import json
from typing import Any

from compose2pod.exceptions import UnsupportedComposeError


_CMD_MIN_LENGTH = 2


def has_healthcheck(svc: dict[str, Any]) -> bool:
    """Report whether the service defines a healthcheck with a non-disabled test."""
    test = (svc.get("healthcheck") or {}).get("test")
    return test is not None and test not in ("NONE", ["NONE"])


def health_cmd(test: object) -> str | None:
    """Compose healthcheck `test` value to a podman --health-cmd value."""
    if test is None or test in ("NONE", ["NONE"]):
        return None
    if isinstance(test, str):
        return test
    if not isinstance(test, list) or not test:
        raise UnsupportedComposeError(f"unsupported healthcheck test: {test!r}")
    kind = test[0]
    if kind == "CMD-SHELL":
        if len(test) < _CMD_MIN_LENGTH:
            raise UnsupportedComposeError(f"unsupported healthcheck test: {test!r}")
        return test[1]
    if kind == "CMD":
        if len(test) < _CMD_MIN_LENGTH:
            raise UnsupportedComposeError(f"unsupported healthcheck test: {test!r}")
        return json.dumps(test[1:])
    raise UnsupportedComposeError(f"unsupported healthcheck test kind: {kind!r}")


def interval_seconds(duration: object) -> int:
    """Compose duration ('1s', '2m', '500ms', int) to whole seconds, minimum 1."""
    if duration is None:
        return 1
    if isinstance(duration, (int, float)):
        return max(int(duration), 1)
    text = str(duration).strip()
    if text.endswith("ms"):
        return max(int(float(text[:-2]) / 1000), 1)
    if text.endswith("m"):
        return max(int(float(text[:-1])) * 60, 1)
    text = text.removesuffix("s")
    return max(int(float(text)), 1)
```

- [ ] **Step 5: Run tests + lint**

Run: `just test tests/test_healthcheck.py` → Expected: PASS (20 tests).
Run: `just lint-ci` → Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add compose2pod/exceptions.py compose2pod/healthcheck.py tests/test_healthcheck.py
git commit -m "feat: healthcheck translation and UnsupportedComposeError"
```

---

### Task 2: graph module

**Files:**
- Create: `compose2pod/graph.py`
- Test: `tests/test_graph.py`

**Interfaces:**
- Consumes: `UnsupportedComposeError` from `compose2pod.exceptions`.
- Produces: `depends_on(svc: dict[str, Any]) -> dict[str, str]`; `hostnames(services: dict[str, Any]) -> list[str]`; `startup_order(services: dict[str, Any], target: str) -> list[str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_graph.py`:

```python
import pytest
from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.graph import depends_on, hostnames, startup_order


class TestDependsOn:
    def test_list_form_normalizes_to_service_started(self) -> None:
        assert depends_on({"depends_on": ["db"]}) == {"db": "service_started"}

    def test_map_form_keeps_conditions(self) -> None:
        assert depends_on({"depends_on": {"db": {"condition": "service_healthy"}}}) == {"db": "service_healthy"}

    def test_missing_depends_on_is_empty(self) -> None:
        assert depends_on({"image": "x"}) == {}


class TestHostnames:
    def test_collects_service_names_and_aliases(self, chats_compose: dict) -> None:
        assert hostnames(chats_compose["services"]) == [
            "application", "migrations", "db", "keydb", "keydb-test-server-0",
        ]

    def test_non_dict_network_entry_is_skipped(self) -> None:
        services = {"app": {"image": "x", "networks": {"default": None, "other": {"aliases": ["app-alias"]}}}}
        assert hostnames(services) == ["app", "app-alias"]


class TestStartupOrder:
    def test_chats_order(self, chats_compose: dict) -> None:
        order = startup_order(chats_compose["services"], "application")
        assert order[-1] == "application"
        assert order.index("db") < order.index("migrations") < order.index("application")
        assert order.index("keydb") < order.index("application")
        assert set(order) == {"db", "migrations", "keydb", "application"}

    def test_services_outside_target_closure_are_excluded(self) -> None:
        assert startup_order({"app": {"image": "x"}, "unrelated": {"image": "y"}}, "app") == ["app"]

    def test_unknown_target_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="nope"):
            startup_order({"app": {"image": "x"}}, "nope")

    def test_unknown_dependency_raises(self) -> None:
        services = {"app": {"image": "x", "depends_on": {"ghost": {"condition": "service_started"}}}}
        with pytest.raises(UnsupportedComposeError, match="ghost"):
            startup_order(services, "app")

    def test_dependency_cycle_raises(self) -> None:
        services = {
            "a": {"image": "x", "depends_on": {"b": {"condition": "service_started"}}},
            "b": {"image": "x", "depends_on": {"a": {"condition": "service_started"}}},
        }
        with pytest.raises(UnsupportedComposeError, match="cycle"):
            startup_order(services, "a")

    def test_diamond_dependency_visits_shared_service_once(self) -> None:
        services = {
            "c": {"image": "x"},
            "a": {"image": "x", "depends_on": {"c": {"condition": "service_started"}}},
            "b": {"image": "x", "depends_on": {"c": {"condition": "service_started"}}},
            "target": {"image": "x", "depends_on": {
                "a": {"condition": "service_started"}, "b": {"condition": "service_started"}}},
        }
        order = startup_order(services, "target")
        assert order.count("c") == 1
        assert order.index("c") < order.index("a")
        assert order.index("c") < order.index("b")
        assert order[-1] == "target"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test tests/test_graph.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'compose2pod.graph'`.

- [ ] **Step 3: Implement `graph.py`**

```python
"""Dependency graph: normalize depends_on, collect hostnames, compute startup order."""

from typing import Any

from compose2pod.exceptions import UnsupportedComposeError


def depends_on(svc: dict[str, Any]) -> dict[str, str]:
    """Normalize dependencies of a service to a name -> condition mapping."""
    deps = svc.get("depends_on") or {}
    if isinstance(deps, list):
        return dict.fromkeys(deps, "service_started")
    return {name: spec.get("condition", "service_started") for name, spec in deps.items()}


def hostnames(services: dict[str, Any]) -> list[str]:
    """All names other services may use to reach a service: names, then aliases."""
    names = list(services)
    for svc in services.values():
        networks = svc.get("networks")
        if isinstance(networks, dict):
            for network in networks.values():
                if isinstance(network, dict):
                    names.extend(network.get("aliases") or [])
    return names


def startup_order(services: dict[str, Any], target: str) -> list[str]:
    """Dependency closure of target in start order (dependencies first, target last)."""
    if target not in services:
        raise UnsupportedComposeError(f"target service '{target}' not found")
    order: list[str] = []
    state: dict[str, str] = {}

    def visit(name: str) -> None:
        if state.get(name) == "visiting":
            raise UnsupportedComposeError(f"dependency cycle involving '{name}'")
        if state.get(name) == "done":
            return
        if name not in services:
            raise UnsupportedComposeError(f"unknown dependency '{name}'")
        state[name] = "visiting"
        for dep in depends_on(services[name]):
            visit(dep)
        state[name] = "done"
        order.append(name)

    visit(target)
    return order
```

- [ ] **Step 4: Run tests + lint**

Run: `just test tests/test_graph.py` → Expected: PASS.
Run: `just lint-ci` → Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add compose2pod/graph.py tests/test_graph.py
git commit -m "feat: dependency graph and startup order"
```

---

### Task 3: parsing module (fix #1: non-dict guard)

**Files:**
- Create: `compose2pod/parsing.py`
- Test: `tests/test_parsing.py`

**Interfaces:**
- Consumes: `UnsupportedComposeError` (exceptions), `depends_on` (graph), `has_healthcheck` (healthcheck).
- Produces: `validate(compose: dict[str, Any]) -> list[str]`; module constants `SUPPORTED_SERVICE_KEYS`, `IGNORED_SERVICE_KEYS`, `SUPPORTED_HEALTHCHECK_KEYS`, `SUPPORTED_TOP_LEVEL_KEYS`, `DEPENDS_ON_CONDITIONS`.

- [ ] **Step 1: Write the failing tests** (ports the chats `TestValidate` + a new non-dict test)

Create `tests/test_parsing.py`:

```python
import pytest
from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.parsing import validate


class TestValidate:
    def test_chats_compose_is_accepted_with_warnings_for_ignored_keys(self, chats_compose: dict) -> None:
        joined = "\n".join(validate(chats_compose))
        assert "'ports'" in joined
        assert "'restart'" in joined
        assert "'stdin_open'" in joined
        assert "'tty'" in joined

    def test_non_dict_document_raises(self) -> None:
        for bad in (None, [], "compose", 42):
            with pytest.raises(UnsupportedComposeError, match="must be a mapping"):
                validate(bad)  # ty: ignore[invalid-argument-type]

    def test_unsupported_service_key_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="privileged"):
            validate({"services": {"app": {"image": "x", "privileged": True}}})

    def test_unsupported_healthcheck_key_raises(self) -> None:
        compose = {"services": {"app": {"image": "x", "healthcheck": {"test": "true", "start_interval": "1s"}}}}
        with pytest.raises(UnsupportedComposeError, match="start_interval"):
            validate(compose)

    def test_named_volume_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="pgdata"):
            validate({"services": {"db": {"image": "x", "volumes": ["pgdata:/var/lib/postgresql/data"]}}})

    def test_long_volume_syntax_raises(self) -> None:
        compose = {"services": {"app": {"image": "x", "volumes": [{"type": "bind", "source": ".", "target": "/s"}]}}}
        with pytest.raises(UnsupportedComposeError, match="short volume syntax"):
            validate(compose)

    def test_no_services_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="no services"):
            validate({"services": {}})

    def test_unknown_top_level_key_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="secrets"):
            validate({"services": {"app": {"image": "x"}}, "secrets": {}})

    def test_top_level_networks_is_ignored_with_warning(self) -> None:
        warnings = validate({"services": {"app": {"image": "x"}}, "networks": {"default": None}})
        assert any("networks" in w for w in warnings)

    def test_service_healthy_dependency_without_healthcheck_raises(self) -> None:
        compose = {"services": {
            "migrations": {"image": "x", "depends_on": {"db": {"condition": "service_healthy"}}},
            "db": {"image": "y"}}}
        with pytest.raises(UnsupportedComposeError,
                           match=r"depends on 'db' \(service_healthy\) but 'db' has no healthcheck"):
            validate(compose)

    def test_service_healthy_dependency_with_none_test_raises(self) -> None:
        compose = {"services": {
            "migrations": {"image": "x", "depends_on": {"db": {"condition": "service_healthy"}}},
            "db": {"image": "y", "healthcheck": {"test": "NONE"}}}}
        with pytest.raises(UnsupportedComposeError, match=r"depends on 'db' \(service_healthy\)"):
            validate(compose)

    def test_service_healthy_dependency_with_healthcheck_is_accepted(self) -> None:
        compose = {"services": {
            "migrations": {"image": "x", "depends_on": {"db": {"condition": "service_healthy"}}},
            "db": {"image": "y", "healthcheck": {"test": ["CMD-SHELL", "true"]}}}}
        assert validate(compose) == []

    def test_service_healthy_dependency_on_unknown_service_is_out_of_scope(self) -> None:
        assert validate(
            {"services": {"app": {"image": "x", "depends_on": {"ghost": {"condition": "service_healthy"}}}}}
        ) == []

    def test_unknown_depends_on_condition_raises(self) -> None:
        compose = {"services": {
            "app": {"image": "x", "depends_on": {"db": {"condition": "service_ready"}}},
            "db": {"image": "y"}}}
        with pytest.raises(UnsupportedComposeError,
                           match=r"service 'app': depends_on 'db' has unsupported condition 'service_ready'"):
            validate(compose)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test tests/test_parsing.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'compose2pod.parsing'`.

- [ ] **Step 3: Implement `parsing.py`** (fix #1 is the `isinstance` guard at the top of `validate`)

```python
"""Validate a compose document against the supported subset."""

from typing import Any

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.graph import depends_on
from compose2pod.healthcheck import has_healthcheck


SUPPORTED_SERVICE_KEYS = {
    "image", "build", "command", "environment", "env_file",
    "volumes", "healthcheck", "depends_on", "networks",
}
IGNORED_SERVICE_KEYS = {"ports", "restart", "stdin_open", "tty"}
SUPPORTED_HEALTHCHECK_KEYS = {"test", "interval", "timeout", "retries", "start_period"}
SUPPORTED_TOP_LEVEL_KEYS = {"services", "version", "name", "networks"}
DEPENDS_ON_CONDITIONS = {"service_started", "service_healthy", "service_completed_successfully"}


def _validate_service(name: str, svc: dict[str, Any]) -> list[str]:
    """Validate one service; returns warnings, raises UnsupportedComposeError."""
    warnings: list[str] = []
    for key in sorted(svc):
        if key in IGNORED_SERVICE_KEYS:
            warnings.append(f"service {name!r}: ignoring '{key}'")
        elif key not in SUPPORTED_SERVICE_KEYS:
            raise UnsupportedComposeError(f"service {name!r}: unsupported key '{key}'")
    for key in sorted(svc.get("healthcheck") or {}):
        if key not in SUPPORTED_HEALTHCHECK_KEYS:
            raise UnsupportedComposeError(f"service {name!r}: unsupported healthcheck key '{key}'")
    for volume in svc.get("volumes") or []:
        if not isinstance(volume, str):
            raise UnsupportedComposeError(f"service {name!r}: only short volume syntax is supported")
        source = volume.split(":", 1)[0]
        if not source.startswith((".", "/")):
            raise UnsupportedComposeError(
                f"service {name!r}: named volume '{source}' is not supported (bind mounts only)"
            )
    return warnings


def _validate_depends_on(services: dict[str, Any]) -> None:
    """Cross-service depends_on checks: known conditions, service_healthy needs a healthcheck."""
    for name, svc in services.items():
        for dep, condition in depends_on(svc).items():
            if condition not in DEPENDS_ON_CONDITIONS:
                raise UnsupportedComposeError(
                    f"service {name!r}: depends_on {dep!r} has unsupported condition {condition!r}"
                )
            if condition == "service_healthy" and dep in services and not has_healthcheck(services[dep]):
                raise UnsupportedComposeError(
                    f"service {name!r}: depends on {dep!r} (service_healthy) but {dep!r} has no healthcheck"
                )


def validate(compose: dict[str, Any]) -> list[str]:
    """Check the compose document against the supported subset.

    Returns human-readable warnings for ignored constructs.
    Raises UnsupportedComposeError for anything that would change behavior silently.
    """
    if not isinstance(compose, dict):
        raise UnsupportedComposeError(f"compose document must be a mapping, got {type(compose).__name__}")
    warnings: list[str] = []
    unknown_top = set(compose) - SUPPORTED_TOP_LEVEL_KEYS
    if unknown_top:
        raise UnsupportedComposeError(f"unsupported top-level keys: {sorted(unknown_top)}")
    if "networks" in compose:
        warnings.append("ignoring top-level 'networks' (all services share the pod namespace)")
    services = compose.get("services") or {}
    if not services:
        raise UnsupportedComposeError("no services defined")
    for name, svc in services.items():
        warnings.extend(_validate_service(name, svc))
    _validate_depends_on(services)
    return warnings
```

- [ ] **Step 4: Run tests + lint**

Run: `just test tests/test_parsing.py` → Expected: PASS.
Run: `just lint-ci` → Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add compose2pod/parsing.py tests/test_parsing.py
git commit -m "feat: compose subset validation with non-dict guard"
```

---

### Task 4: emit module (fix #2: start_period/retries; fix #4: split emit_script)

**Files:**
- Create: `compose2pod/emit.py`
- Test: `tests/test_emit.py`

**Interfaces:**
- Consumes: `depends_on`, `hostnames`, `startup_order` (graph); `health_cmd`, `interval_seconds` (healthcheck).
- Produces: `EmitOptions` (frozen dataclass: `target, ci_image, command, pod, project_dir, artifacts: list[str], allow_exit_codes: list[int]`); `image_for(svc, ci_image) -> str`; `command_tokens(svc) -> list[str]`; `run_flags(name, svc, pod, hosts, project_dir) -> list[str]`; `emit_script(compose, options: EmitOptions) -> str`; `HEALTHY_WAIT_BUDGET_SECONDS = 120`.

**Design note (for reviewer/Artur):** fix #2 implements the spec's refined decision — `start_period`/`retries` are passed to `podman run` as `--health-start-period`/`--health-retries`; the `wait_healthy` budget is unchanged (still `HEALTHY_WAIT_BUDGET_SECONDS // interval` attempts). This deliberately does not shorten the wait to `retries × interval`, which would risk premature failure for a long `start_period`.

- [ ] **Step 1: Write the failing tests** (ports `TestRunFlags`, `TestImageAndCommand`, `TestEmitScript`, plus fix-#2 cases)

Create `tests/test_emit.py`:

```python
from compose2pod.emit import (
    EmitOptions, command_tokens, emit_script, image_for, run_flags,
)


class TestRunFlags:
    def test_db_flags(self, chats_compose: dict) -> None:
        flags = run_flags("db", chats_compose["services"]["db"], "test-pod", ["db", "keydb"], "/builds/chats")
        assert flags[:4] == ["--pod", "test-pod", "--name", "test-pod-db"]
        assert flags[4:6] == ["--add-host", "db:127.0.0.1"]
        assert flags[6:8] == ["--add-host", "keydb:127.0.0.1"]
        assert flags[8:10] == ["-e", "POSTGRES_PASSWORD=password"]
        assert flags[10:12] == ["--health-cmd", "pg_isready -U database -d database"]
        assert flags[12:14] == ["--health-timeout", "5s"]
        assert flags[14:16] == ["--health-retries", "15"]  # fix #2

    def test_start_period_is_passed_through(self) -> None:
        svc = {"image": "x", "healthcheck": {"test": "true", "start_period": "30s"}}
        flags = run_flags("app", svc, "p", [], "/b")
        assert "--health-start-period" in flags
        assert flags[flags.index("--health-start-period") + 1] == "30s"

    def test_env_map_form(self) -> None:
        svc = {"image": "x", "environment": {"A": "1", "B": "two words"}}
        flags = run_flags("app", svc, "p", [], "/builds/x")
        assert flags[4:6] == ["-e", "A=1"]
        assert flags[6:8] == ["-e", "B=two words"]

    def test_env_file_and_volume_resolved_against_project_dir(self) -> None:
        svc = {"image": "x", "env_file": "tests.env", "volumes": [".:/srv/www/"]}
        flags = run_flags("app", svc, "p", [], "/builds/chats")
        assert flags[4:6] == ["--env-file", "/builds/chats/tests.env"]
        assert flags[6:8] == ["-v", "/builds/chats:/srv/www/"]

    def test_env_file_list_form(self) -> None:
        svc = {"image": "x", "env_file": ["a.env", "b.env"]}
        flags = run_flags("app", svc, "p", [], "/builds/x")
        assert flags[4:8] == ["--env-file", "/builds/x/a.env", "--env-file", "/builds/x/b.env"]

    def test_absolute_volume_source_is_kept_as_is(self) -> None:
        flags = run_flags("app", {"image": "x", "volumes": ["/data/app:/srv/www/"]}, "p", [], "/builds/x")
        assert flags[4:6] == ["-v", "/data/app:/srv/www/"]

    def test_healthcheck_without_timeout_omits_health_timeout_flag(self) -> None:
        flags = run_flags("app", {"image": "x", "healthcheck": {"test": "true"}}, "p", [], "/builds/x")
        assert flags[4:6] == ["--health-cmd", "true"]
        assert "--health-timeout" not in flags


class TestImageAndCommand:
    def test_build_service_uses_ci_image(self, chats_compose: dict) -> None:
        assert image_for(chats_compose["services"]["application"], "reg/ci:abc") == "reg/ci:abc"

    def test_plain_service_keeps_image(self, chats_compose: dict) -> None:
        assert image_for(chats_compose["services"]["db"], "reg/ci:abc") == "postgres:13.5-alpine"

    def test_command_list_passes_through(self, chats_compose: dict) -> None:
        assert command_tokens(chats_compose["services"]["migrations"]) == ["alembic", "upgrade", "head"]

    def test_command_string_becomes_shell(self) -> None:
        assert command_tokens({"command": "echo hi"}) == ["/bin/sh", "-c", "echo hi"]

    def test_missing_command_is_empty(self) -> None:
        assert command_tokens({"image": "x"}) == []


class TestEmitScript:
    def make_script(self, chats_compose: dict) -> str:
        options = EmitOptions(
            target="application",
            ci_image="reg/app/ci:abc1234",
            command="pytest . -n 4 --junitxml=/srv/out/junit.xml",
            pod="test-pod",
            project_dir="/builds/chats",
            artifacts=["/srv/out/junit.xml:junit.xml", "/srv/out/coverage.xml:coverage.xml"],
            allow_exit_codes=[5],
        )
        return emit_script(compose=chats_compose, options=options)

    def test_pod_lifecycle(self, chats_compose: dict) -> None:
        script = self.make_script(chats_compose)
        assert "podman pod create --name test-pod" in script
        assert "trap 'podman pod rm -f test-pod" in script

    def test_dependencies_start_before_target_and_waits_are_placed(self, chats_compose: dict) -> None:
        script = self.make_script(chats_compose)
        run_db = script.index("--name test-pod-db")
        wait_db = script.index("wait_healthy test-pod-db")
        run_migrations = script.index("--name test-pod-migrations")
        wait_keydb = script.index("wait_healthy test-pod-keydb")
        run_application = script.index("--name test-pod-application")
        assert run_db < wait_db < run_migrations < run_application
        assert wait_keydb < run_application

    def test_db_and_keydb_detached_migrations_foreground(self, chats_compose: dict) -> None:
        script = self.make_script(chats_compose)
        for line in script.splitlines():
            if "--name test-pod-db" in line:
                assert line.startswith("podman run -d ")
            if "--name test-pod-keydb" in line:
                assert line.startswith("podman run -d ")
            if "--name test-pod-migrations" in line:
                assert line.startswith("podman run --rm ")
                assert line.rstrip().endswith("alembic upgrade head")

    def test_target_command_is_overridden_and_rc_gated(self, chats_compose: dict) -> None:
        script = self.make_script(chats_compose)
        assert "pytest . -n 4 --junitxml=/srv/out/junit.xml" in script.replace("'", "")
        assert "|| rc=$?" in script
        assert "0|5)" in script

    def test_artifacts_copied_before_rc_gate(self, chats_compose: dict) -> None:
        script = self.make_script(chats_compose)
        cp_junit = script.index("podman cp test-pod-application:/srv/out/junit.xml junit.xml")
        gate = script.index('case "$rc" in')
        assert cp_junit < gate

    def test_failure_branch_prints_oom_diagnostics(self, chats_compose: dict) -> None:
        script = self.make_script(chats_compose)
        gate = script.index('case "$rc" in')
        assert "OOMKilled={{.State.OOMKilled}}" in script[gate:]
        assert "podman ps -a" in script[gate:]

    def test_add_host_on_every_run(self, chats_compose: dict) -> None:
        script = self.make_script(chats_compose)
        run_lines = [line for line in script.splitlines() if line.startswith("podman run")]
        assert len(run_lines) == 4
        for line in run_lines:
            assert "--add-host keydb-test-server-0:127.0.0.1" in line

    def test_wait_healthy_function_uses_healthcheck_run(self, chats_compose: dict) -> None:
        script = self.make_script(chats_compose)
        assert "podman healthcheck run" in script
        assert "wait_healthy()" in script

    def test_target_without_command_uses_service_command(self, chats_compose: dict) -> None:
        options = EmitOptions(
            target="application", ci_image="reg/ci:abc", command="",
            pod="test-pod", project_dir="/b", artifacts=[], allow_exit_codes=[],
        )
        script = emit_script(compose=chats_compose, options=options)
        target_line = next(line for line in script.splitlines() if "--name test-pod-application" in line)
        assert target_line.rstrip().endswith("python -m chats.api")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test tests/test_emit.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'compose2pod.emit'`.

- [ ] **Step 3: Implement `emit.py`** (fix #2 in `run_flags`; fix #4 splits `_emit_target`/`_run_tokens`/`_render`)

```python
"""Render the podman-pod test script for a target service and its dependencies."""

import dataclasses
import shlex
from pathlib import Path
from typing import Any

from compose2pod.graph import depends_on, hostnames, startup_order
from compose2pod.healthcheck import health_cmd, interval_seconds


HEALTHY_WAIT_BUDGET_SECONDS = 120


def image_for(svc: dict[str, Any], ci_image: str) -> str:
    """Services with a build section run the freshly built CI image."""
    if "build" in svc:
        return ci_image
    return svc["image"]


def command_tokens(svc: dict[str, Any]) -> list[str]:
    """Service command as argv tokens; compose string form means shell form."""
    command = svc.get("command")
    if command is None:
        return []
    if isinstance(command, str):
        return ["/bin/sh", "-c", command]
    return list(command)


def run_flags(name: str, svc: dict[str, Any], pod: str, hosts: list[str], project_dir: str) -> list[str]:
    """Flag tokens (unquoted) for `podman run` of one service."""
    flags = ["--pod", pod, "--name", f"{pod}-{name}"]
    for host in hosts:
        flags += ["--add-host", f"{host}:127.0.0.1"]
    environment = svc.get("environment") or {}
    pairs = environment if isinstance(environment, list) else [f"{k}={v}" for k, v in environment.items()]
    for pair in pairs:
        flags += ["-e", pair]
    env_files = svc.get("env_file") or []
    if isinstance(env_files, str):
        env_files = [env_files]
    for env_file in env_files:
        flags += ["--env-file", str(Path(project_dir, env_file))]
    for volume in svc.get("volumes") or []:
        source, destination = volume.split(":", 1)
        if not source.startswith("/"):
            source = str(Path(project_dir, source))
        flags += ["-v", f"{source}:{destination}"]
    healthcheck = svc.get("healthcheck") or {}
    cmd = health_cmd(healthcheck.get("test"))
    if cmd is not None:
        flags += ["--health-cmd", cmd]
        if "timeout" in healthcheck:
            flags += ["--health-timeout", str(healthcheck["timeout"])]
        if "start_period" in healthcheck:
            flags += ["--health-start-period", str(healthcheck["start_period"])]
        if "retries" in healthcheck:
            flags += ["--health-retries", str(healthcheck["retries"])]
    return flags


_SCRIPT_HEADER = """\
#!/bin/sh
# Generated by compose2pod -- do not edit, regenerate instead.
set -eu

wait_healthy() {
  ctr=$1
  attempts=$2
  interval=$3
  i=0
  while [ "$i" -lt "$attempts" ]; do
    if podman healthcheck run "$ctr"; then
      return 0
    fi
    i=$((i + 1))
    sleep "$interval"
  done
  echo "wait_healthy: $ctr did not become healthy after $attempts attempts" >&2
  podman logs "$ctr" >&2 || true
  return 1
}
"""


@dataclasses.dataclass(frozen=True)
class EmitOptions:
    """Options for emit_script rendering."""

    target: str
    ci_image: str
    command: str
    pod: str
    project_dir: str
    artifacts: list[str]
    allow_exit_codes: list[int]


def _render(tokens: list[str]) -> str:
    return " ".join(shlex.quote(token) for token in tokens)


def _run_tokens(name: str, services: dict[str, Any], options: EmitOptions, hosts: list[str]) -> list[str]:
    svc = services[name]
    tokens = run_flags(name, svc, options.pod, hosts, options.project_dir)
    tokens.append(image_for(svc, options.ci_image))
    if name == options.target and options.command:
        tokens.extend(shlex.split(options.command))
    else:
        tokens.extend(command_tokens(svc))
    return tokens


def _emit_target(lines: list[str], run_tokens: list[str], options: EmitOptions) -> None:
    target_ctr = shlex.quote(f"{options.pod}-{options.target}")
    lines.append("rc=0")
    lines.append(f"podman run {_render(run_tokens)} || rc=$?")
    for artifact in options.artifacts:
        source, destination = artifact.split(":", 1)
        lines.append(f"podman cp {target_ctr}:{shlex.quote(source)} {shlex.quote(destination)} || true")
    allowed = "|".join(str(code) for code in [0, *options.allow_exit_codes])
    lines.append('case "$rc" in')
    lines.append(f"  {allowed}) ;;")
    lines.append('  *) echo "target service failed with exit code $rc" >&2')
    lines.append(
        "     podman inspect --format "
        "'OOMKilled={{.State.OOMKilled}} ExitCode={{.State.ExitCode}}' "
        + target_ctr
        + " >&2 || true"
    )
    lines.append("     podman ps -a --format '{{.Names}} {{.Status}}' >&2 || true")
    lines.append('     exit "$rc" ;;')
    lines.append("esac")


def emit_script(compose: dict[str, Any], options: EmitOptions) -> str:
    """Render the full pod test script for `target` and its dependency closure."""
    services = compose["services"]
    hosts = hostnames(services)
    order = startup_order(services, options.target)
    completion_gated = {
        dep
        for svc in services.values()
        for dep, condition in depends_on(svc).items()
        if condition == "service_completed_successfully"
    }

    lines = [_SCRIPT_HEADER]
    lines.append(f"trap 'podman pod rm -f {shlex.quote(options.pod)} >/dev/null 2>&1 || true' EXIT")
    lines.append(f"podman pod create --name {shlex.quote(options.pod)}")
    waited: set[str] = set()
    for name in order:
        for dep, condition in depends_on(services[name]).items():
            if condition == "service_healthy" and dep not in waited:
                interval = interval_seconds((services[dep].get("healthcheck") or {}).get("interval"))
                attempts = max(HEALTHY_WAIT_BUDGET_SECONDS // interval, 1)
                lines.append(f"wait_healthy {shlex.quote(f'{options.pod}-{dep}')} {attempts} {interval}")
                waited.add(dep)
        run_tokens = _run_tokens(name, services, options, hosts)
        if name == options.target:
            _emit_target(lines, run_tokens, options)
        elif name in completion_gated:
            lines.append(f"podman run --rm {_render(run_tokens)}")
        else:
            lines.append(f"podman run -d {_render(run_tokens)}")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run tests + lint**

Run: `just test tests/test_emit.py` → Expected: PASS.
Run: `just lint-ci` → Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add compose2pod/emit.py tests/test_emit.py
git commit -m "feat: pod script emission with start_period/retries pass-through"
```

---

### Task 5: cli + __main__ + public API (YAML extra, --format)

**Files:**
- Create: `compose2pod/cli.py`, `compose2pod/__main__.py`
- Modify: `compose2pod/__init__.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `validate` (parsing), `EmitOptions`/`emit_script` (emit), `UnsupportedComposeError` (exceptions).
- Produces: `main(argv: list[str] | None = None) -> int`; `POD_NAME_PATTERN`; package exports `validate`, `emit_script`, `EmitOptions`, `UnsupportedComposeError`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli.py`:

```python
import io
import json
import subprocess
import sys

import pytest
import compose2pod.cli as cli
from compose2pod.cli import main


def run_main(compose_text: str, argv: list[str], monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(sys, "stdin", io.StringIO(compose_text))
    return main(argv)


class TestPublicApi:
    def test_exports(self) -> None:
        import compose2pod
        assert set(compose2pod.__all__) == {"EmitOptions", "UnsupportedComposeError", "emit_script", "validate"}
        assert compose2pod.validate({"services": {"a": {"image": "x"}}}) == []


class TestMain:
    def test_json_stdin_success_with_warnings(self, chats_compose: dict,
                                              capsys: pytest.CaptureFixture[str],
                                              monkeypatch: pytest.MonkeyPatch) -> None:
        rc = run_main(json.dumps(chats_compose),
                      ["--target", "application", "--image", "reg/ci:abc",
                       "--project-dir", "/builds/chats", "--command", "pytest .",
                       "--artifact", "/srv/out/junit.xml:junit.xml", "--allow-exit-code", "5"],
                      monkeypatch)
        out = capsys.readouterr()
        assert rc == 0
        assert out.out.startswith("#!/bin/sh")
        assert "podman pod create" in out.out
        assert "compose2pod:" in out.err

    def test_yaml_stdin_success(self, capsys: pytest.CaptureFixture[str],
                               monkeypatch: pytest.MonkeyPatch) -> None:
        yaml_text = "services:\n  app:\n    image: x\n"
        rc = run_main(yaml_text, ["--target", "app", "--image", "i", "--format", "yaml"], monkeypatch)
        out = capsys.readouterr()
        assert rc == 0
        assert "podman pod create" in out.out

    def test_auto_falls_back_to_yaml(self, capsys: pytest.CaptureFixture[str],
                                     monkeypatch: pytest.MonkeyPatch) -> None:
        rc = run_main("services:\n  app:\n    image: x\n", ["--target", "app", "--image", "i"], monkeypatch)
        assert rc == 0
        assert "podman pod create" in capsys.readouterr().out

    def test_yaml_without_pyyaml_errors(self, capsys: pytest.CaptureFixture[str],
                                        monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cli, "_yaml", None)
        rc = run_main("services: {}", ["--target", "app", "--image", "i", "--format", "yaml"], monkeypatch)
        assert rc == 2
        assert "requires the 'yaml' extra" in capsys.readouterr().err

    def test_invalid_yaml_returns_2(self, capsys: pytest.CaptureFixture[str],
                                    monkeypatch: pytest.MonkeyPatch) -> None:
        rc = run_main("a: [1, 2", ["--target", "app", "--image", "i", "--format", "yaml"], monkeypatch)
        assert rc == 2
        assert "invalid YAML" in capsys.readouterr().err

    def test_malformed_json_returns_2(self, capsys: pytest.CaptureFixture[str],
                                      monkeypatch: pytest.MonkeyPatch) -> None:
        rc = run_main("not json", ["--target", "app", "--image", "i", "--format", "json"], monkeypatch)
        assert rc == 2
        assert "could not parse" in capsys.readouterr().err

    def test_non_mapping_document_returns_2(self, capsys: pytest.CaptureFixture[str],
                                            monkeypatch: pytest.MonkeyPatch) -> None:
        rc = run_main("42", ["--target", "app", "--image", "i", "--format", "json"], monkeypatch)
        assert rc == 2
        assert "must be a mapping" in capsys.readouterr().err

    def test_unsupported_compose_returns_2(self, capsys: pytest.CaptureFixture[str],
                                           monkeypatch: pytest.MonkeyPatch) -> None:
        rc = run_main(json.dumps({"services": {"app": {"image": "x", "privileged": True}}}),
                      ["--target", "app", "--image", "i"], monkeypatch)
        assert rc == 2
        assert "privileged" in capsys.readouterr().err

    def test_invalid_pod_name_returns_2(self, chats_compose: dict, capsys: pytest.CaptureFixture[str],
                                        monkeypatch: pytest.MonkeyPatch) -> None:
        rc = run_main(json.dumps(chats_compose),
                      ["--target", "application", "--image", "i", "--pod-name", "bad name"], monkeypatch)
        assert rc == 2
        assert "invalid pod name" in capsys.readouterr().err

    def test_file_argument_is_read(self, tmp_path, chats_compose: dict,
                                   capsys: pytest.CaptureFixture[str]) -> None:
        compose_file = tmp_path / "docker-compose.json"
        compose_file.write_text(json.dumps(chats_compose))
        rc = main([str(compose_file), "--target", "application", "--image", "i"])
        assert rc == 0
        assert "podman pod create" in capsys.readouterr().out


class TestModuleEntrypoint:
    def test_python_m_runs(self, chats_compose: dict) -> None:
        # S603: fixed interpreter + module + test args, not external input.
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-m", "compose2pod", "--target", "application", "--image", "i"],
            input=json.dumps(chats_compose), capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("#!/bin/sh")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `just test tests/test_cli.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'compose2pod.cli'`.

- [ ] **Step 3: Implement `cli.py`** (module-level optional yaml import; `# noqa: ANN401` where returning parsed data)

```python
"""Command-line interface: read a compose document and emit the pod script."""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from compose2pod.emit import EmitOptions, emit_script
from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.parsing import validate


try:
    import yaml as _yaml
except ImportError:  # pragma: no cover - the optional [yaml] extra is not installed
    _yaml = None


POD_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _load_yaml(text: str) -> Any:  # noqa: ANN401 - returns arbitrary parsed compose data
    if _yaml is None:
        raise UnsupportedComposeError(
            "YAML input requires the 'yaml' extra: pip install compose2pod[yaml] (or pipe JSON via yq)"
        )
    try:
        return _yaml.safe_load(text)
    except _yaml.YAMLError as error:
        raise UnsupportedComposeError(f"invalid YAML: {error}") from error


def _read_compose(text: str, fmt: str) -> Any:  # noqa: ANN401 - returns arbitrary parsed compose data
    if fmt == "json":
        return json.loads(text)
    if fmt == "yaml":
        return _load_yaml(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return _load_yaml(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="compose2pod",
        description="Convert a Docker Compose document to a podman-pod run script (stdout).",
    )
    parser.add_argument("file", nargs="?", help="compose file to read (default: stdin)")
    parser.add_argument("--target", required=True, help="service to run in the foreground with --command")
    parser.add_argument("--image", required=True, help="CI image replacing services that have a build section")
    parser.add_argument("--project-dir", default=".", help="host path relative volume/env_file sources resolve to")
    parser.add_argument("--command", default="", help="shell command overriding the target service command")
    parser.add_argument("--pod-name", default="test-pod")
    parser.add_argument("--format", choices=("auto", "json", "yaml"), default="auto")
    parser.add_argument("--artifact", action="append", default=[], metavar="SRC:DST",
                        help="file to podman-cp out of the target container after it exits")
    parser.add_argument("--allow-exit-code", type=int, action="append", default=[],
                        help="target exit code treated as success in addition to 0")
    args = parser.parse_args(argv)
    if not POD_NAME_PATTERN.match(args.pod_name):
        sys.stderr.write(f"compose2pod: error: invalid pod name {args.pod_name!r}\n")
        return 2
    text = Path(args.file).read_text() if args.file else sys.stdin.read()
    try:
        compose = _read_compose(text, args.format)
    except (json.JSONDecodeError, UnsupportedComposeError) as error:
        sys.stderr.write(f"compose2pod: error: could not parse compose input: {error}\n")
        return 2
    try:
        warnings = validate(compose)
        script = emit_script(
            compose=compose,
            options=EmitOptions(
                target=args.target,
                ci_image=args.image,
                command=args.command,
                pod=args.pod_name,
                project_dir=args.project_dir,
                artifacts=args.artifact,
                allow_exit_codes=args.allow_exit_code,
            ),
        )
    except UnsupportedComposeError as error:
        sys.stderr.write(f"compose2pod: error: {error}\n")
        return 2
    for warning in warnings:
        sys.stderr.write(f"compose2pod: {warning}\n")
    sys.stdout.write(script)
    return 0
```

Note: the non-mapping case (`42`, `"not json"` under `auto`) reaches `validate`, which raises `UnsupportedComposeError` (fix #1) → caught in the second `try` → exit 2. The `test_non_mapping_document_returns_2` test asserts that path.

- [ ] **Step 4: Implement `__main__.py`**

```python
"""python -m compose2pod entry point."""

import sys

from compose2pod.cli import main


if __name__ == "__main__":  # pragma: no cover - exercised via subprocess in tests
    sys.exit(main())
```

- [ ] **Step 5: Implement `__init__.py` exports**

```python
"""compose2pod: convert a Docker Compose file into a single-Podman-pod run script."""

from compose2pod.emit import EmitOptions, emit_script
from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.parsing import validate


__all__ = [
    "EmitOptions",
    "UnsupportedComposeError",
    "emit_script",
    "validate",
]
```

- [ ] **Step 6: Run tests + lint + full coverage**

Run: `just test tests/test_cli.py` → Expected: PASS.
Run: `just test-ci` → Expected: PASS at 100% line coverage.
Run: `just lint-ci` → Expected: clean.

- [ ] **Step 7: Commit**

```bash
git add compose2pod/cli.py compose2pod/__main__.py compose2pod/__init__.py tests/test_cli.py
git commit -m "feat: CLI with JSON/YAML input and public API exports"
```

---

### Task 6: CI workflows

**Files:**
- Create: `.github/workflows/ci.yml`, `.github/workflows/_checks.yml`, `.github/workflows/release.yml`

**Interfaces:** none (CI only). Mirrors `modern-di`, minus the docs and planning-bundle jobs.

- [ ] **Step 1: Create `.github/workflows/ci.yml`**

```yaml
name: main
on:
  push:
    branches:
      - main
  pull_request: {}

concurrency:
  group: ${{ github.head_ref || github.run_id }}
  cancel-in-progress: true

jobs:
  checks:
    uses: ./.github/workflows/_checks.yml
```

- [ ] **Step 2: Create `.github/workflows/_checks.yml`**

```yaml
name: checks
on:
  workflow_call: {}

jobs:
  lint:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: extractions/setup-just@v4
      - uses: astral-sh/setup-uv@v8.2.0
        with:
          enable-cache: true
          cache-dependency-glob: "**/pyproject.toml"
      - run: uv python install 3.10
      - run: uv python pin 3.10
      - run: just install lint-ci

  pytest:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        python-version: ["3.10", "3.11", "3.12", "3.13", "3.14"]
    steps:
      - uses: actions/checkout@v6
      - uses: extractions/setup-just@v4
      - uses: astral-sh/setup-uv@v8.2.0
        with:
          enable-cache: true
          cache-dependency-glob: "**/pyproject.toml"
      - run: uv python install ${{ matrix.python-version }}
      - run: uv python pin ${{ matrix.python-version }}
      - run: just install
      - run: just test-ci
```

- [ ] **Step 3: Create `.github/workflows/release.yml`**

```yaml
name: Release

# Tag-driven: pushing a semver tag publishes to PyPI (Trusted Publishing) and
# creates the matching GitHub Release with auto-generated notes.
on:
  push:
    tags:
      - '[0-9]+.[0-9]+.[0-9]+'
      - '[0-9]+.[0-9]+.[0-9]+[a-z]+[0-9]+'

permissions:
  contents: write
  id-token: write

jobs:
  release:
    runs-on: ubuntu-latest
    environment: pypi
    steps:
      - uses: actions/checkout@v6
      - uses: extractions/setup-just@v4
      - uses: astral-sh/setup-uv@v7
      - run: just publish
      - name: Resolve prerelease flag
        id: meta
        run: |
          set -euo pipefail
          if [[ "$GITHUB_REF_NAME" =~ [a-z] ]]; then
            echo "prerelease=true" >> "$GITHUB_OUTPUT"
          else
            echo "prerelease=false" >> "$GITHUB_OUTPUT"
          fi
      - name: Publish GitHub Release
        uses: softprops/action-gh-release@v3
        with:
          generate_release_notes: true
          prerelease: ${{ steps.meta.outputs.prerelease }}
          draft: false
```

- [ ] **Step 4: Validate workflow YAML**

Run: `python3 -c "import yaml,glob; [yaml.safe_load(open(f)) for f in glob.glob('.github/workflows/*.yml')]; print('ok')"`
Expected: `ok`.

- [ ] **Step 5: Final full-suite gate**

Run: `just test-ci` → Expected: PASS at 100% line coverage.
Run: `just lint-ci` → Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/
git commit -m "ci: lint + pytest matrix and tag-driven PyPI trusted publishing"
```

**Manual follow-up (outside this plan, Artur):** create the GitHub repo `modern-python/compose2pod`, push, and register the PyPI Trusted Publisher (project `compose2pod`, workflow `release.yml`, environment `pypi`) before the first tag.

---

## Self-Review

**Spec coverage:** niche/why (README + docstrings ✓); supported subset (Task 3 constants + tests ✓); emitted-script behavior (Task 4 ✓); CLI incl. `--format`, file arg, pod-name validation (Task 5 ✓); public API (Task 5 `__init__` ✓); parsing approach — stdlib core + optional yaml (Task 5 module-level try/except ✓); root layout / uv_build (scaffold ✓, unchanged); four fixes — #1 Task 3, #2 Task 4, #3 Task 1, #4 Task 4 ✓; tooling/CI (Task 6 ✓); testing 100% line (Task 5 Step 6, Task 6 Step 5 ✓). Out-of-scope items (chats migration, pypelines wiring) correctly absent.

**Placeholder scan:** no TBD/TODO; every code step contains complete code; every test step contains real assertions.

**Type consistency:** `EmitOptions` fields identical across Task 4 definition and Task 5 usage; `validate`/`emit_script`/`health_cmd`/`interval_seconds`/`run_flags`/`depends_on`/`hostnames`/`startup_order` signatures identical between producing task, tests, and importers; module import graph is acyclic (`exceptions` ← `healthcheck`/`graph` ← `parsing`/`emit` ← `cli` ← `__main__`).

## Notes for the executor

- Coverage is line-based (`--cov-fail-under=100`), matching modern-di. `# pragma: no cover` is used on the optional-yaml `except ImportError` (PyYAML is installed via `--all-extras`, so that line never runs in CI) and on the `__main__` guard.
- Never run bare `ruff check` in this repo — the config autofixes destructively. Use `just lint-ci` (`ruff check --no-fix`).
- Commit messages: conventional-commit subjects, no `Co-authored-by` trailer.
