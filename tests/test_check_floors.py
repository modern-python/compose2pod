import pathlib
import runpy
import sys

import pytest

from scripts import check_floors


_PYYAML_SPLIT: list[str] = [
    "pyyaml>=6; python_version < '3.12' and extra == 'yaml'",
    "pyyaml>=6.0.1; python_version == '3.12' and extra == 'yaml'",
    "pyyaml>=6.0.3; python_version >= '3.14' and extra == 'yaml'",
]


def test_a_floor_resolved_as_declared_passes() -> None:
    assert check_floors.floor_mismatches(["pyyaml>=6"], [], {"pyyaml": "6.0"}) == []


def test_a_floor_resolved_above_its_declaration_is_reported() -> None:
    assert check_floors.floor_mismatches(["PyYAML>=6"], [], {"pyyaml": "6.0.1"}) == [
        "PyYAML: declared floor 6, resolved 6.0.1",
    ]


def test_the_marker_for_the_running_interpreter_picks_the_floor() -> None:
    environment = {"python_version": "3.12"}

    assert check_floors.floor_mismatches(_PYYAML_SPLIT, ["yaml"], {"pyyaml": "6.0.1"}, environment) == []
    assert check_floors.floor_mismatches(_PYYAML_SPLIT, ["yaml"], {"pyyaml": "6.0.3"}, environment) == [
        "pyyaml: declared floor 6.0.1, resolved 6.0.3",
    ]


def test_a_requirement_behind_an_unrequested_extra_is_skipped() -> None:
    assert check_floors.floor_mismatches(_PYYAML_SPLIT, [], {}, {"python_version": "3.12"}) == []


def test_an_unbounded_requirement_is_reported() -> None:
    assert check_floors.floor_mismatches(["pyyaml<7"], [], {"pyyaml": "6.0"}) == ["pyyaml: declares no floor"]


def test_an_exact_pin_is_its_own_floor() -> None:
    assert check_floors.floor_mismatches(["pyyaml==6.0.1"], [], {"pyyaml": "6.0.1"}) == []


def test_a_required_distribution_that_is_not_installed_is_reported() -> None:
    assert check_floors.floor_mismatches(["pyyaml>=6"], [], {}) == ["pyyaml: not installed"]


def test_main_reports_mismatches_and_fails(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(check_floors.importlib.metadata, "requires", lambda _: ["pyyaml>=6; extra == 'yaml'"])
    monkeypatch.setattr(check_floors, "installed_versions", lambda: {"pyyaml": "6.0.3"})

    assert check_floors.main(["compose2pod", "yaml"]) == 1
    assert capsys.readouterr().out == "pyyaml: declared floor 6, resolved 6.0.3\n"


def test_main_passes_when_every_floor_resolved(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(check_floors.importlib.metadata, "requires", lambda _: ["pyyaml>=6; extra == 'yaml'"])
    monkeypatch.setattr(check_floors, "installed_versions", lambda: {"pyyaml": "6.0"})

    assert check_floors.main(["compose2pod", "yaml"]) == 0
    assert capsys.readouterr().out == "every declared floor of compose2pod resolved as declared\n"


def test_installed_versions_reads_the_running_environment() -> None:
    assert check_floors.installed_versions()["pytest"]


def test_running_the_script_exits_with_the_verdict(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check_floors.importlib.metadata, "requires", lambda _: [])
    monkeypatch.setattr(sys, "argv", ["check_floors.py", "compose2pod"])

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(pathlib.Path(check_floors.__file__)), run_name="__main__")

    assert exit_info.value.code == 0
