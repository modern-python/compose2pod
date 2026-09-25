import pathlib
import runpy
import sys

import pytest

from scripts import floor_constraints


def test_a_lower_bound_becomes_an_exact_pin() -> None:
    assert floor_constraints.floor_constraints(["PyYAML>=6,<7"]) == ["PyYAML==6"]


def test_a_marker_is_kept_so_the_resolver_evaluates_it() -> None:
    assert floor_constraints.floor_constraints(["PyYAML>=6.0.1; python_version == '3.12'"]) == [
        'PyYAML==6.0.1; python_version == "3.12"',
    ]


def test_compatible_release_and_exact_pins_are_floors() -> None:
    assert floor_constraints.floor_constraints(["a~=1.4", "b==2.0.1"]) == ["a==1.4", "b==2.0.1"]


def test_the_same_requirement_declared_twice_pins_the_higher_floor() -> None:
    assert floor_constraints.floor_constraints(["a>=1", "A>=1.2"]) == ["a==1.2"]


def test_differently_marked_declarations_each_keep_their_floor() -> None:
    assert floor_constraints.floor_constraints(
        ["a>=1; python_version < '3.12'", "a>=2; python_version >= '3.12'"],
    ) == ['a==1; python_version < "3.12"', 'a==2; python_version >= "3.12"']


def test_an_unbounded_requirement_is_refused() -> None:
    with pytest.raises(floor_constraints.UnboundedError, match=r"^declares no floor: a, b$"):
        floor_constraints.floor_constraints(["a<2", "b", "c>=1"])


def test_every_extra_contributes_its_requirements() -> None:
    project = {"dependencies": ["a>=1"], "optional-dependencies": {"x": ["b>=2"], "y": ["c>=3"]}}

    assert floor_constraints.declared_requirements(project) == ["a>=1", "b>=2", "c>=3"]


def test_a_project_without_dependencies_declares_none() -> None:
    assert floor_constraints.declared_requirements({}) == []


def test_main_prints_this_repos_constraints(capsys: pytest.CaptureFixture[str]) -> None:
    assert floor_constraints.main([str(pathlib.Path(__file__).parent.parent / "pyproject.toml")]) == 0
    assert capsys.readouterr().out.splitlines() == [
        'PyYAML==6; python_version < "3.12"',
        'PyYAML==6.0.1; python_version == "3.12"',
        'PyYAML==6.0.2; python_version == "3.13"',
        'PyYAML==6.0.3; python_version >= "3.14"',
    ]


def test_main_fails_on_an_unbounded_requirement(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\ndependencies = ["a"]\n', encoding="utf-8")

    assert floor_constraints.main([str(pyproject)]) == 1
    assert capsys.readouterr().err == "declares no floor: a\n"


def test_running_the_script_exits_with_the_verdict(monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path) -> None:
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\ndependencies = ["a>=1"]\n', encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["floor_constraints.py", str(pyproject)])

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(pathlib.Path(floor_constraints.__file__)), run_name="__main__")

    assert exit_info.value.code == 0
