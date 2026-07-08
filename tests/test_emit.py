from compose2pod.emit import (
    EmitOptions,
    command_tokens,
    emit_script,
    image_for,
    run_flags,
)
from compose2pod.parsing import validate


_EXPECTED_RUN_LINES: int = 4


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

    def test_anonymous_volume_emitted_as_single_path(self) -> None:
        flags = run_flags("app", {"image": "x", "volumes": ["/var/cache/models"]}, "p", [], "/builds/x")
        assert flags[4:6] == ["-v", "/var/cache/models"]

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
        assert len(run_lines) == _EXPECTED_RUN_LINES
        for line in run_lines:
            assert "--add-host keydb-test-server-0:127.0.0.1" in line

    def test_wait_healthy_function_uses_healthcheck_run(self, chats_compose: dict) -> None:
        script = self.make_script(chats_compose)
        assert "podman healthcheck run" in script
        assert "wait_healthy()" in script

    def test_hostname_becomes_add_host_entry(self) -> None:
        compose = {
            "services": {
                "application": {"image": "app", "depends_on": ["keydb"]},
                "keydb": {"image": "keydb", "hostname": "keydb-test-server-0"},
            }
        }
        options = EmitOptions(
            target="application",
            ci_image="ci",
            command="",
            pod="testpod",
            project_dir="/proj",
            artifacts=[],
            allow_exit_codes=[],
        )
        assert validate(compose) == []
        script = emit_script(compose=compose, options=options)
        assert "--add-host keydb-test-server-0:127.0.0.1" in script

    def test_target_without_command_uses_service_command(self, chats_compose: dict) -> None:
        options = EmitOptions(
            target="application",
            ci_image="reg/ci:abc",
            command="",
            pod="test-pod",
            project_dir="/b",
            artifacts=[],
            allow_exit_codes=[],
        )
        script = emit_script(compose=chats_compose, options=options)
        target_line = next(line for line in script.splitlines() if "--name test-pod-application" in line)
        assert target_line.rstrip().endswith("python -m chats.api || rc=$?")
