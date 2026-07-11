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
        assert set(compose2pod.__all__) == {
            "EmitOptions",
            "UnsupportedComposeError",
            "emit_script",
            "resolve_extends",
            "to_shell",
            "validate",
        }
        assert compose2pod.validate({"services": {"a": {"image": "x"}}}) == []
        assert compose2pod.to_shell("$FOO") == '"${FOO-}"'


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
            json.dumps({"services": {"app": {"image": "x", "network_mode": "host"}}}),
            ["--target", "app", "--image", "i"],
            monkeypatch,
        )
        assert rc == EXIT_USAGE_ERROR
        assert "network_mode" in capsys.readouterr().err

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

    def test_environment_reference_is_emitted_live_not_resolved(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_MODEL", "gpt-4")  # set at generation time -> must be ignored
        yaml_text = "services:\n  app:\n    image: x\n    environment:\n      MODEL: ${LLM_MODEL}\n"
        rc = run_main(yaml_text, ["--target", "app", "--image", "i", "--format", "yaml"], monkeypatch)
        out = capsys.readouterr()
        assert rc == 0
        assert "MODEL=gpt-4" not in out.out  # NOT baked in at generation
        assert "${LLM_MODEL-}" in out.out  # left live for the runtime shell
        assert "note: script references variables at run time: LLM_MODEL" in out.err

    def test_malformed_variable_reference_returns_2(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yaml_text = "services:\n  app:\n    image: x\n    environment:\n      K: ${FOO!bad}\n"
        rc = run_main(yaml_text, ["--target", "app", "--image", "i", "--format", "yaml"], monkeypatch)
        assert rc == EXIT_USAGE_ERROR
        assert "malformed variable reference" in capsys.readouterr().err

    def test_required_reference_survives_generation(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("REQUIRED_VAR", raising=False)
        yaml_text = "services:\n  app:\n    image: x\n    environment:\n      MODEL: ${REQUIRED_VAR:?must be set}\n"
        rc = run_main(yaml_text, ["--target", "app", "--image", "i", "--format", "yaml"], monkeypatch)
        out = capsys.readouterr()
        assert rc == 0  # no longer fails at generation
        assert "${REQUIRED_VAR:?must be set}" in out.out  # shell enforces it at run time

    def test_note_lists_multiple_referenced_variables_sorted(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yaml_text = "services:\n  app:\n    image: x\n    environment:\n      A: ${ZED}\n      B: ${ALPHA}\n"
        rc = run_main(yaml_text, ["--target", "app", "--image", "i", "--format", "yaml"], monkeypatch)
        out = capsys.readouterr()
        assert rc == 0
        assert "note: script references variables at run time: ALPHA, ZED" in out.err

    def test_env_file_path_variable_is_live_and_noted(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yaml_text = "services:\n  app:\n    image: x\n    env_file: ${ENV_DIR}/app.env\n"
        rc = run_main(
            yaml_text, ["--target", "app", "--image", "i", "--project-dir", "/p", "--format", "yaml"], monkeypatch
        )
        out = capsys.readouterr()
        assert rc == 0
        assert "--env-file" in out.out
        assert "${ENV_DIR-}" in out.out
        assert "ENV_DIR" in out.err

    def test_extends_is_resolved_before_emit(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        doc = {
            "services": {
                "base": {"image": "app", "environment": {"LOG": "info"}},
                "web": {"extends": {"service": "base"}, "environment": {"PORT": "8080"}},
            }
        }
        rc = run_main(json.dumps(doc), ["--target", "web", "--image", "i"], monkeypatch)
        out = capsys.readouterr().out
        assert rc == 0
        # merged web runs the base image with both env vars
        assert "app" in out
        assert "LOG=info" in out
        assert "PORT=8080" in out

    def test_cross_file_extends_is_rejected_by_cli(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        doc = {"services": {"web": {"extends": {"service": "base", "file": "other.yml"}}}}
        rc = run_main(json.dumps(doc), ["--target", "web", "--image", "i"], monkeypatch)
        assert rc == EXIT_USAGE_ERROR
        assert "cross-file" in capsys.readouterr().err

    def test_extends_non_dict_base_is_clean_error(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        doc = {"services": {"base": None, "web": {"extends": {"service": "base"}, "image": "x"}}}
        rc = run_main(json.dumps(doc), ["--target", "web", "--image", "i"], monkeypatch)
        assert rc == EXIT_USAGE_ERROR
        assert "must be a mapping" in capsys.readouterr().err

    def test_yaml_anchor_extension_fields_convert(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        yaml_text = (
            "x-defaults: &defaults\n  image: base:latest\nservices:\n  app:\n    <<: *defaults\n    x-meta: keep\n"
        )
        rc = run_main(yaml_text, ["--target", "app", "--image", "i", "--format", "yaml"], monkeypatch)
        out = capsys.readouterr()
        assert rc == 0
        assert "podman pod create" in out.out


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
