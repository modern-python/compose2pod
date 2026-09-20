"""The other half of rule two: a form the gate accepts compiles to a flag podman runs."""

from collections.abc import Callable
from pathlib import Path

import pytest

from compose2pod.emit import Expand, run_flags
from compose2pod.parsing import validate
from tests.integration.acceptances import ACCEPTANCES, Acceptance


def _flag_values(service: dict, project_dir: Path) -> list[str]:
    tokens = run_flags("app", service, "pod", str(project_dir))
    return [token.value if isinstance(token, Expand) else str(token) for token in tokens]


def _contains(haystack: list[str], needle: list[str]) -> bool:
    return any(haystack[i : i + len(needle)] == needle for i in range(len(haystack) - len(needle) + 1))


@pytest.mark.parametrize("acceptance", ACCEPTANCES, ids=lambda acceptance: acceptance.id)
def test_an_accepted_form_compiles_to_a_flag_podman_runs(
    acceptance: Acceptance, probe_podman: Callable[[str, list[str]], int], tmp_path: Path
) -> None:
    compose = {"services": {"app": acceptance.service}}
    validate(compose)

    (tmp_path / acceptance.host_dir).mkdir(parents=True, exist_ok=True)
    expected = [part.format(host=tmp_path) for part in acceptance.expected_argv]
    assert _contains(_flag_values(acceptance.service, tmp_path), expected), (
        "emit no longer produces this flag, so podman's verdict on it says nothing about the tool"
    )

    assert probe_podman(acceptance.id, expected) == 0, (
        "podman refused a flag compose2pod emits, so an accepted document compiles to a script that dies"
    )
