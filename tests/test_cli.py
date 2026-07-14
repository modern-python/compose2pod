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
        # main() calls validate() once for its own warnings, and emit_script /
        # referenced_variables each call validate() again internally (closing
        # the gate for direct library callers) -- each warning must still
        # appear exactly once, not once per internal validate() call.
        err_lines = out.err.splitlines()
        expected_warnings = [
            "compose2pod: service 'application': ignoring 'ports'",
            "compose2pod: service 'application': ignoring 'restart'",
            "compose2pod: service 'application': ignoring 'stdin_open'",
            "compose2pod: service 'application': ignoring 'tty'",
            "compose2pod: service 'db': ignoring 'ports'",
            "compose2pod: service 'db': ignoring 'restart'",
        ]
        for warning in expected_warnings:
            assert err_lines.count(warning) == 1, err_lines

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

    def test_non_string_key_survives_extends_and_is_rejected_cleanly(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # resolve_extends() runs before validate(); confirms the merged
        # non-string key reaches validate()'s sweep and fails clean (exit 2)
        # instead of crashing raw somewhere in extends.py.
        yaml_text = (
            "services:\n"
            "  base:\n    image: j\n    environment:\n      A: '1'\n"
            "  app:\n    extends: {service: base}\n    environment:\n      3306: '2'\n"
        )
        rc = run_main(yaml_text, ["--target", "app", "--image", "i", "--format", "yaml"], monkeypatch)
        assert rc == EXIT_USAGE_ERROR
        assert "key 3306 must be a string" in capsys.readouterr().err

    def test_non_string_service_name_rejected_cleanly(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A non-string service name reached --add-host/--name verbatim
        # (e.g. `podman run --name test-pod-3306 ...`) instead of being
        # rejected at the gate. Docker refuses a non-string key too.
        yaml_text = "services:\n  app:\n    image: i\n  3306:\n    image: j\n"
        rc = run_main(yaml_text, ["--target", "app", "--image", "i", "--format", "yaml"], monkeypatch)
        assert rc == EXIT_USAGE_ERROR
        assert "compose document.services: key 3306 must be a string" in capsys.readouterr().err

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

    def test_non_string_key_from_x_block_anchor_merged_into_service_is_rejected(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # PyYAML resolves anchors/merge keys at load time, so content whose
        # *source* lived in a skipped x- block lands in the service body as
        # ordinary content once merged -- it must still be swept there.
        yaml_text = (
            "x-common: &common\n  environment:\n    3306: 1\nservices:\n  app:\n    image: alpine\n    <<: *common\n"
        )
        rc = run_main(yaml_text, ["--target", "app", "--image", "i", "--format", "yaml"], monkeypatch)
        assert rc == EXIT_USAGE_ERROR
        assert "key 3306 must be a string" in capsys.readouterr().err


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


class TestYaml12Booleans:
    """`on`/`off`/`yes`/`no` are strings, as they are for `docker compose`.

    PyYAML implements YAML 1.1, where each is a boolean. Docker parses YAML 1.2,
    where each is an ordinary string -- so a bare `on` must survive as the string
    it was written as, both as a key and as a value. `true`/`false` are booleans
    in both schemas and stay so.
    """

    def test_yaml_11_booleans_load_as_strings(self) -> None:
        loaded = cli._load_yaml(  # noqa: SLF001 - the loader is the unit under test
            "k:\n  a: on\n  b: off\n  c: yes\n  d: no\n"
        )
        assert loaded["k"] == {"a": "on", "b": "off", "c": "yes", "d": "no"}

    def test_real_booleans_still_load_as_booleans(self) -> None:
        loaded = cli._load_yaml(  # noqa: SLF001 - the loader is the unit under test
            "k:\n  t: true\n  f: false\n  T: True\n  F: FALSE\n"
        )
        assert loaded["k"] == {"t": True, "f": False, "T": True, "F": False}

    def test_on_as_a_key_stays_a_string(self) -> None:
        # PyYAML 1.1 resolves this key to the bool True, and the gate's
        # string-key rule then refuses a document `docker compose` runs fine.
        loaded = cli._load_yaml(  # noqa: SLF001 - the loader is the unit under test
            "environment:\n  on: 1\n  off: 2\n"
        )
        assert list(loaded["environment"]) == ["on", "off"]

    def test_value_on_reaches_the_script_as_on(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Used to emit -e "SSL=true": the app read "true" where its author wrote `on`.
        compose = "services:\n  app:\n    image: alpine\n    environment:\n      SSL: on\n      DEBUG: yes\n"
        assert run_main(compose, ["--target", "app", "--image", "ci:1", "--format", "yaml"], monkeypatch) == 0
        script = capsys.readouterr().out
        assert '-e "SSL=on"' in script
        assert '-e "DEBUG=yes"' in script

    def test_on_as_a_key_is_accepted_end_to_end(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        compose = "services:\n  app:\n    image: alpine\n    environment:\n      on: 1\n"
        assert run_main(compose, ["--target", "app", "--image", "ci:1", "--format", "yaml"], monkeypatch) == 0
        assert '-e "on=1"' in capsys.readouterr().out
