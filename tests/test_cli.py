import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

import compose2pod
from compose2pod import cli
from compose2pod.cli import main


EXIT_USAGE_ERROR = 2


def run_main(compose_text: str, argv: list[str], monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(sys, "stdin", io.StringIO(compose_text))
    return main(argv)


class TestPublicApi:
    def test_exports(self) -> None:
        assert set(compose2pod.__all__) == {"EmitOptions", "UnsupportedComposeError", "emit_script", "validate"}
        assert compose2pod.validate({"services": {"a": {"image": "x"}}}) == []


class TestMain:
    def test_json_stdin_success_with_warnings(
        self, chats_compose: dict, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rc = run_main(
            json.dumps(chats_compose),
            [
                "--target",
                "application",
                "--image",
                "reg/ci:abc",
                "--project-dir",
                "/builds/chats",
                "--command",
                "pytest .",
                "--artifact",
                "/srv/out/junit.xml:junit.xml",
                "--allow-exit-code",
                "5",
            ],
            monkeypatch,
        )
        out = capsys.readouterr()
        assert rc == 0
        assert out.out.startswith("#!/bin/sh")
        assert "podman pod create" in out.out
        assert "compose2pod:" in out.err

    def test_yaml_stdin_success(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        yaml_text = "services:\n  app:\n    image: x\n"
        rc = run_main(yaml_text, ["--target", "app", "--image", "i", "--format", "yaml"], monkeypatch)
        out = capsys.readouterr()
        assert rc == 0
        assert "podman pod create" in out.out

    def test_auto_falls_back_to_yaml(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        rc = run_main("services:\n  app:\n    image: x\n", ["--target", "app", "--image", "i"], monkeypatch)
        assert rc == 0
        assert "podman pod create" in capsys.readouterr().out

    def test_yaml_without_pyyaml_errors(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(cli, "_yaml", None)
        rc = run_main("services: {}", ["--target", "app", "--image", "i", "--format", "yaml"], monkeypatch)
        assert rc == EXIT_USAGE_ERROR
        assert "requires the 'yaml' extra" in capsys.readouterr().err

    def test_invalid_yaml_returns_2(self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        rc = run_main("a: [1, 2", ["--target", "app", "--image", "i", "--format", "yaml"], monkeypatch)
        assert rc == EXIT_USAGE_ERROR
        assert "invalid YAML" in capsys.readouterr().err

    def test_malformed_json_returns_2(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rc = run_main("not json", ["--target", "app", "--image", "i", "--format", "json"], monkeypatch)
        assert rc == EXIT_USAGE_ERROR
        assert "could not parse" in capsys.readouterr().err

    def test_non_mapping_document_returns_2(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rc = run_main("42", ["--target", "app", "--image", "i", "--format", "json"], monkeypatch)
        assert rc == EXIT_USAGE_ERROR
        assert "must be a mapping" in capsys.readouterr().err

    def test_unsupported_compose_returns_2(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rc = run_main(
            json.dumps({"services": {"app": {"image": "x", "privileged": True}}}),
            ["--target", "app", "--image", "i"],
            monkeypatch,
        )
        assert rc == EXIT_USAGE_ERROR
        assert "privileged" in capsys.readouterr().err

    def test_invalid_pod_name_returns_2(
        self, chats_compose: dict, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rc = run_main(
            json.dumps(chats_compose),
            ["--target", "application", "--image", "i", "--pod-name", "bad name"],
            monkeypatch,
        )
        assert rc == EXIT_USAGE_ERROR
        assert "invalid pod name" in capsys.readouterr().err

    def test_file_argument_is_read(
        self, tmp_path: Path, chats_compose: dict, capsys: pytest.CaptureFixture[str]
    ) -> None:
        compose_file = tmp_path / "docker-compose.json"
        compose_file.write_text(json.dumps(chats_compose))
        rc = main([str(compose_file), "--target", "application", "--image", "i"])
        assert rc == 0
        assert "podman pod create" in capsys.readouterr().out

    def test_missing_file_returns_2(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = main(["/no/such/compose.json", "--target", "app", "--image", "i"])
        assert rc == EXIT_USAGE_ERROR
        assert "compose2pod: error:" in capsys.readouterr().err


class TestModuleEntrypoint:
    def test_python_m_runs(self, chats_compose: dict) -> None:
        # fixed interpreter + module + test args, not external input.
        result = subprocess.run(
            [sys.executable, "-m", "compose2pod", "--target", "application", "--image", "i"],
            input=json.dumps(chats_compose),
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.startswith("#!/bin/sh")
