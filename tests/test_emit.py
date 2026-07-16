import os
import shutil
import subprocess
from pathlib import Path

import pytest

from compose2pod.emit import (
    _SCRIPT_HEADER,
    EmitOptions,
    Expand,
    GuardedEnvFile,
    command_tokens,
    emit_script,
    entrypoint_tokens,
    image_for,
    referenced_variables,
    run_flags,
    run_tokens,
)
from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.parsing import validate


_SH = shutil.which("sh")


class TestRunFlags:
    def test_db_flags(self, chats_compose: dict) -> None:
        flags = run_flags("db", chats_compose["services"]["db"], "test-pod", "/builds/chats")
        assert flags[:4] == ["--pod", "test-pod", "--name", "test-pod-db"]
        # environment is a registry key now, emitted after the healthcheck flags.
        assert flags[4:6] == ["--health-cmd", Expand(value="pg_isready -U database -d database")]
        assert flags[6:8] == ["--health-timeout", "5s"]
        assert flags[8:10] == ["--health-retries", "15"]  # fix #2
        assert flags[10:12] == ["-e", Expand(value="POSTGRES_PASSWORD=password")]

    def test_start_period_is_passed_through(self) -> None:
        svc = {"image": "x", "healthcheck": {"test": "true", "start_period": "30s"}}
        flags = run_flags("app", svc, "p", "/b")
        assert "--health-start-period" in flags
        assert flags[flags.index("--health-start-period") + 1] == "30s"

    def test_env_map_form(self) -> None:
        svc = {"image": "x", "environment": {"A": "1", "B": "two words"}}
        flags = run_flags("app", svc, "p", "/builds/x")
        assert flags[4:8] == ["-e", Expand(value="A=1"), "-e", Expand(value="B=two words")]

    def test_env_map_null_value_is_host_passthrough(self) -> None:
        # A null mapping value means "pass KEY through from the host", like `- KEY`.
        svc = {"image": "x", "environment": {"PASSTHRU": None, "SET": "v"}}
        flags = run_flags("app", svc, "p", "/builds/x")
        assert flags[4:8] == ["-e", Expand(value="PASSTHRU"), "-e", Expand(value="SET=v")]

    def test_env_map_boolean_value_normalizes_like_docker(self) -> None:
        # `environment: {DEBUG: true}` is valid Compose; `docker compose config`
        # normalizes it to the string "true". Before this fix, this either
        # emitted the raw Python repr "-e DEBUG=True" (silent corruption) or,
        # after list/map hardening, was rejected outright (an over-rejection
        # regression) -- the maintainer's ruling is to normalize, like Docker.
        svc = {"image": "x", "environment": {"DEBUG": True, "VERBOSE": False}}
        flags = run_flags("app", svc, "p", "/builds/x")
        assert flags[4:8] == ["-e", Expand(value="DEBUG=true"), "-e", Expand(value="VERBOSE=false")]

    def test_env_file_and_volume_resolved_against_project_dir(self) -> None:
        svc = {"image": "x", "env_file": "tests.env", "volumes": [".:/srv/www/"]}
        flags = run_flags("app", svc, "p", "/builds/chats")
        assert flags[4:6] == ["--env-file", Expand(value="/builds/chats/tests.env")]
        assert flags[6:8] == ["-v", Expand(value="/builds/chats:/srv/www/")]

    def test_env_file_list_form(self) -> None:
        svc = {"image": "x", "env_file": ["a.env", "b.env"]}
        flags = run_flags("app", svc, "p", "/builds/x")
        assert flags[4:8] == [
            "--env-file",
            Expand(value="/builds/x/a.env"),
            "--env-file",
            Expand(value="/builds/x/b.env"),
        ]

    def test_env_file_long_form_mapping_path_resolved(self) -> None:
        svc = {"image": "x", "env_file": [{"path": "a.env"}, "b.env"]}
        flags = run_flags("app", svc, "p", "/proj")
        assert flags[4:8] == [
            "--env-file",
            Expand(value="/proj/a.env"),
            "--env-file",
            Expand(value="/proj/b.env"),
        ]

    def test_env_file_mixed_entries_preserve_order(self) -> None:
        svc = {
            "image": "x",
            "env_file": [
                {"path": "base.env"},
                {"path": "opt.env", "required": False},
            ],
        }
        flags = run_flags("app", svc, "p", "/proj")
        # base.env unconditional, then the guarded opt.env, in list order.
        assert flags[4:6] == ["--env-file", Expand(value="/proj/base.env")]
        assert flags[6] == GuardedEnvFile(var="c2p_envfile_1", value="/proj/opt.env")

    def test_tmpfs_string_form(self) -> None:
        # S108 flags "/tmp" as an insecure hardcoded temp path; this is a
        # pass-through string being tested, not a file write.
        flags = run_flags("app", {"image": "x", "tmpfs": "/tmp:mode=1777"}, "p", "/builds/x")  # noqa: S108
        assert flags[4:6] == ["--tmpfs", Expand(value="/tmp:mode=1777")]  # noqa: S108

    def test_tmpfs_list_form(self) -> None:
        svc = {"image": "x", "tmpfs": ["/tmp:mode=1777", "/run"]}  # noqa: S108
        flags = run_flags("app", svc, "p", "/builds/x")
        assert flags[4:8] == ["--tmpfs", Expand(value="/tmp:mode=1777"), "--tmpfs", Expand(value="/run")]  # noqa: S108

    def test_absolute_volume_source_is_kept_as_is(self) -> None:
        flags = run_flags("app", {"image": "x", "volumes": ["/data/app:/srv/www/"]}, "p", "/builds/x")
        assert flags[4:6] == ["-v", Expand(value="/data/app:/srv/www/")]

    def test_anonymous_volume_emitted_as_single_path(self) -> None:
        flags = run_flags("app", {"image": "x", "volumes": ["/var/cache/models"]}, "p", "/builds/x")
        assert flags[4:6] == ["-v", Expand(value="/var/cache/models")]

    def test_named_volume_emitted_without_project_dir_translation(self) -> None:
        svc = {"image": "x", "volumes": ["pgdata:/var/lib/postgresql/data"]}
        flags = run_flags("db", svc, "p", "/builds/x")
        assert flags[4:6] == ["-v", Expand(value="pgdata:/var/lib/postgresql/data")]

    def test_secret_flag_emitted(self) -> None:
        flags = run_flags("app", {"image": "x", "secrets": ["db"]}, "test-pod", "/b")
        assert flags[-2:] == ["--secret", "source=test-pod-db,target=db"]

    def test_healthcheck_without_timeout_omits_health_timeout_flag(self) -> None:
        flags = run_flags("app", {"image": "x", "healthcheck": {"test": "true"}}, "p", "/builds/x")
        assert flags[4:6] == ["--health-cmd", Expand(value="true")]
        assert "--health-timeout" not in flags

    def test_healthcheck_explicit_null_scalars_omit_flags(self) -> None:
        # An explicit `null` means unset, matching `docker compose config`
        # and how this package treats null everywhere else (environment/
        # volumes/command). Before the fix: keyed off key *presence*
        # (`"timeout" in healthcheck`), not the value, so an explicit null
        # emitted the literal string 'None' as the flag value.
        svc = {
            "image": "x",
            "healthcheck": {"test": "true", "timeout": None, "retries": None, "start_period": None},
        }
        flags = run_flags("app", svc, "p", "/b")
        assert "--health-timeout" not in flags
        assert "--health-retries" not in flags
        assert "--health-start-period" not in flags
        assert "None" not in [str(f) for f in flags]

    def test_user_flag(self) -> None:
        flags = run_flags("app", {"image": "x", "user": "1000:1000"}, "p", "/b")
        assert flags[4:6] == ["--user", Expand(value="1000:1000")]

    def test_working_dir_flag(self) -> None:
        flags = run_flags("app", {"image": "x", "working_dir": "/srv/app"}, "p", "/b")
        assert flags[4:6] == ["--workdir", Expand(value="/srv/app")]

    def test_user_and_working_dir_order(self) -> None:
        svc = {"image": "x", "user": "root", "working_dir": "/app"}
        flags = run_flags("app", svc, "p", "/b")
        assert flags[4:8] == ["--user", Expand(value="root"), "--workdir", Expand(value="/app")]

    def test_mem_limit_flag(self) -> None:
        flags = run_flags("app", {"image": "x", "mem_limit": "512m"}, "p", "/b")
        assert flags[4:6] == ["--memory", Expand(value="512m")]

    def test_cpus_numeric_value_flag(self) -> None:
        flags = run_flags("app", {"image": "x", "cpus": 0.5}, "p", "/b")
        assert flags[4:6] == ["--cpus", Expand(value="0.5")]

    def test_pids_limit_int_flag(self) -> None:
        flags = run_flags("app", {"image": "x", "pids_limit": 100}, "p", "/b")
        assert flags[4:6] == ["--pids-limit", Expand(value="100")]

    def test_cpuset_and_shm_size_flags(self) -> None:
        svc = {"image": "x", "cpuset": "0-3", "shm_size": "64m"}
        flags = run_flags("app", svc, "p", "/b")
        assert flags[4:8] == ["--cpuset-cpus", Expand(value="0-3"), "--shm-size", Expand(value="64m")]

    def test_oom_kill_disable_bool_flag(self) -> None:
        assert run_flags("app", {"image": "x", "oom_kill_disable": True}, "p", "/b")[4:5] == ["--oom-kill-disable"]
        assert "--oom-kill-disable" not in run_flags("app", {"image": "x", "oom_kill_disable": False}, "p", "/b")

    def test_group_add_flag(self) -> None:
        flags = run_flags("app", {"image": "x", "group_add": ["docker", 1000]}, "p", "/b")
        assert flags[4:8] == ["--group-add", Expand(value="docker"), "--group-add", Expand(value="1000")]

    def test_cap_add_flag(self) -> None:
        flags = run_flags("app", {"image": "x", "cap_add": ["NET_ADMIN", "SYS_TIME"]}, "p", "/b")
        assert flags[4:8] == ["--cap-add", Expand(value="NET_ADMIN"), "--cap-add", Expand(value="SYS_TIME")]

    def test_cap_drop_and_security_opt_flags(self) -> None:
        svc = {"image": "x", "cap_drop": ["ALL"], "security_opt": ["label=disable"]}
        flags = run_flags("app", svc, "p", "/b")
        assert flags[4:8] == ["--cap-drop", Expand(value="ALL"), "--security-opt", Expand(value="label=disable")]

    def test_labels_map_form(self) -> None:
        svc = {"image": "x", "labels": {"team": "api", "tier": "backend"}}
        flags = run_flags("app", svc, "p", "/b")
        assert flags[4:8] == ["--label", Expand(value="team=api"), "--label", Expand(value="tier=backend")]

    def test_labels_list_form(self) -> None:
        svc = {"image": "x", "labels": ["team=api", "standalone"]}
        flags = run_flags("app", svc, "p", "/b")
        assert flags[4:8] == ["--label", Expand(value="team=api"), "--label", Expand(value="standalone")]

    def test_labels_null_value_is_empty_label(self) -> None:
        # A null map value is an empty label here, NOT the host-passthrough that
        # `environment`'s null means -- same emitted shape, distinct meaning.
        flags = run_flags("app", {"image": "x", "labels": {"empty": None}}, "p", "/b")
        assert flags[4:6] == ["--label", Expand(value="empty")]

    def test_labels_boolean_value_normalizes_like_docker(self) -> None:
        flags = run_flags("app", {"image": "x", "labels": {"enabled": True}}, "p", "/b")
        assert flags[4:6] == ["--label", Expand(value="enabled=true")]

    def test_platform_flag(self) -> None:
        flags = run_flags("app", {"image": "x", "platform": "linux/amd64"}, "p", "/b")
        assert flags[4:6] == ["--platform", Expand(value="linux/amd64")]

    def test_devices_flag(self) -> None:
        flags = run_flags("app", {"image": "x", "devices": ["/dev/fuse", "/dev/net/tun"]}, "p", "/b")
        assert flags[4:8] == ["--device", Expand(value="/dev/fuse"), "--device", Expand(value="/dev/net/tun")]

    def test_annotations_map_form(self) -> None:
        flags = run_flags("app", {"image": "x", "annotations": {"com.example/team": "api"}}, "p", "/b")
        assert flags[4:6] == ["--annotation", Expand(value="com.example/team=api")]

    def test_annotations_null_value_is_bare_key(self) -> None:
        flags = run_flags("app", {"image": "x", "annotations": {"marker": None}}, "p", "/b")
        assert flags[4:6] == ["--annotation", Expand(value="marker")]

    def test_annotations_boolean_value_normalizes_like_docker(self) -> None:
        flags = run_flags("app", {"image": "x", "annotations": {"enabled": False}}, "p", "/b")
        assert flags[4:6] == ["--annotation", Expand(value="enabled=false")]

    def test_labels_still_emit_after_map_flags_refactor(self) -> None:
        flags = run_flags("app", {"image": "x", "labels": {"team": "api"}}, "p", "/b")
        assert flags[4:6] == ["--label", Expand(value="team=api")]

    def test_read_only_flag(self) -> None:
        flags = run_flags("app", {"image": "x", "read_only": True}, "p", "/b")
        assert flags[4:5] == ["--read-only"]

    def test_all_boolean_flags_emit_when_true(self) -> None:
        svc = {"image": "x", "init": True, "read_only": True, "privileged": True}
        flags = run_flags("app", svc, "p", "/b")
        assert flags[4:7] == ["--init", "--read-only", "--privileged"]

    def test_quoted_true_boolean_emits_flag(self) -> None:
        # A quoted YAML-1.1 true-spelling must set the flag, like a real bool.
        assert run_flags("app", {"image": "x", "read_only": "yes"}, "p", "/b")[4:5] == ["--read-only"]

    def test_quoted_false_boolean_emits_no_flag(self) -> None:
        # The truthiness trap: a non-empty string "false"/"no"/"off" is truthy in
        # Python, so a naive `[flag] if value else []` would wrongly set the flag.
        # `as_bool` coerces first.
        for spelling in ("false", "no", "off"):
            assert "--read-only" not in run_flags("app", {"image": "x", "read_only": spelling}, "p", "/b")

    def test_boolean_flag_false_or_absent_is_omitted(self) -> None:
        flags = run_flags("app", {"image": "x", "read_only": False}, "p", "/b")
        assert "--read-only" not in flags
        assert flags == ["--pod", "p", "--name", "p-app"]

    def test_pull_policy_maps_if_not_present_to_missing(self) -> None:
        flags = run_flags("app", {"image": "x", "pull_policy": "if_not_present"}, "p", "/b")
        assert flags[4:6] == ["--pull", "missing"]

    def test_pull_policy_passthrough_values(self) -> None:
        for value in ("always", "never", "missing"):
            flags = run_flags("app", {"image": "x", "pull_policy": value}, "p", "/b")
            assert flags[4:6] == ["--pull", value]

    def test_ulimits_mapping_form(self) -> None:
        svc = {"image": "x", "ulimits": {"nofile": {"soft": 20000, "hard": 40000}}}
        flags = run_flags("app", svc, "p", "/b")
        assert flags[4:6] == ["--ulimit", Expand(value="nofile=20000:40000")]

    def test_ulimits_scalar_form(self) -> None:
        flags = run_flags("app", {"image": "x", "ulimits": {"nproc": 65535}}, "p", "/b")
        assert flags[4:6] == ["--ulimit", Expand(value="nproc=65535")]

    def test_ulimits_mixed_forms(self) -> None:
        svc = {"image": "x", "ulimits": {"nproc": 65535, "nofile": {"soft": 1024, "hard": 2048}}}
        flags = run_flags("app", svc, "p", "/b")
        assert flags[4:8] == ["--ulimit", Expand(value="nproc=65535"), "--ulimit", Expand(value="nofile=1024:2048")]

    def test_null_pull_policy_and_ulimits_emit_nothing(self) -> None:
        flags = run_flags("app", {"image": "x", "pull_policy": None, "ulimits": None}, "p", "/b")
        assert flags == ["--pod", "p", "--name", "p-app"]

    def test_registry_emission_order_across_shape_groups(self) -> None:
        # Locks the cross-key flag order (scalar, bool, list, map, pull_policy, ulimits)
        # against a future reordering of the SERVICE_KEYS registry.
        svc = {
            "image": "x",
            "user": "root",
            "init": True,
            "cap_add": ["NET_ADMIN"],
            "labels": {"team": "api"},
            "pull_policy": "always",
            "ulimits": {"nofile": 1024},
        }
        flags = run_flags("app", svc, "p", "/b")
        assert flags[4:] == [
            "--user",
            Expand(value="root"),
            "--init",
            "--cap-add",
            Expand(value="NET_ADMIN"),
            "--label",
            Expand(value="team=api"),
            "--pull",
            "always",
            "--ulimit",
            Expand(value="nofile=1024"),
        ]

    def test_deploy_resource_flags_emitted(self) -> None:
        svc = {
            "image": "x",
            "deploy": {"resources": {"limits": {"memory": "256m"}, "reservations": {"memory": "128m"}}},
        }
        flags = run_flags("app", svc, "p", "/b")
        assert flags[4:8] == ["--memory", Expand(value="256m"), "--memory-reservation", Expand(value="128m")]


class TestImageAndCommand:
    def test_build_service_uses_ci_image(self, chats_compose: dict) -> None:
        assert image_for(chats_compose["services"]["application"], "reg/ci:abc") == "reg/ci:abc"

    def test_plain_service_keeps_image(self, chats_compose: dict) -> None:
        assert image_for(chats_compose["services"]["db"], "reg/ci:abc") == Expand(value="postgres:13.5-alpine")

    def test_command_list_passes_through(self, chats_compose: dict) -> None:
        assert command_tokens(chats_compose["services"]["migrations"]) == [
            Expand(value="alembic"),
            Expand(value="upgrade"),
            Expand(value="head"),
        ]

    def test_command_string_becomes_shell(self) -> None:
        assert command_tokens({"command": "echo hi"}) == ["/bin/sh", "-c", Expand(value="echo hi")]

    def test_missing_command_is_empty(self) -> None:
        assert command_tokens({"image": "x"}) == []


class TestEntrypoint:
    def test_list_form_passes_through(self) -> None:
        assert entrypoint_tokens({"entrypoint": ["sleep", "600"]}) == [Expand(value="sleep"), Expand(value="600")]

    def test_string_form_becomes_shell(self) -> None:
        assert entrypoint_tokens({"entrypoint": "serve now"}) == ["/bin/sh", "-c", Expand(value="serve now")]

    def test_missing_entrypoint_is_empty(self) -> None:
        assert entrypoint_tokens({"image": "x"}) == []

    def test_empty_list_is_empty(self) -> None:
        # An empty list means "no override", not "an entrypoint of zero tokens" --
        # the same convention `command: []` follows.
        assert entrypoint_tokens({"entrypoint": []}) == []


class TestRunTokens:
    def _options(self, command: str = "") -> EmitOptions:
        return EmitOptions(
            target="app",
            ci_image="ci",
            command=command,
            pod="p",
            project_dir="/proj",
            artifacts=[],
            allow_exit_codes=[],
        )

    def test_list_entrypoint_prepends_to_command(self) -> None:
        svc = {"image": "x", "entrypoint": ["prog", "--flag"], "command": ["run"]}
        tokens = run_tokens("app", {"app": svc}, self._options())
        assert tokens[-5:] == [
            "--entrypoint",
            Expand(value="prog"),
            Expand(value="x"),
            Expand(value="--flag"),
            Expand(value="run"),
        ]

    def test_string_entrypoint_ignores_service_command(self) -> None:
        svc = {"image": "x", "entrypoint": "serve now", "command": ["dropped"]}
        tokens = run_tokens("app", {"app": svc}, self._options())
        assert tokens[-5:] == [
            "--entrypoint",
            "/bin/sh",
            Expand(value="x"),
            "-c",
            Expand(value="serve now"),
        ]
        assert Expand(value="dropped") not in tokens

    def test_command_override_lands_after_entrypoint(self) -> None:
        svc = {"image": "x", "entrypoint": "serve"}
        tokens = run_tokens("app", {"app": svc}, self._options(command="pytest tests"))
        assert tokens[-7:] == [
            "--entrypoint",
            "/bin/sh",
            Expand(value="x"),
            "-c",
            Expand(value="serve"),
            "pytest",
            "tests",
        ]

    def test_list_entrypoint_composes_with_command_override(self) -> None:
        svc = {"image": "x", "entrypoint": ["prog", "--flag"]}
        tokens = run_tokens("app", {"app": svc}, self._options(command="run me"))
        assert tokens[-6:] == [
            "--entrypoint",
            Expand(value="prog"),
            Expand(value="x"),
            Expand(value="--flag"),
            "run",
            "me",
        ]

    def test_empty_list_entrypoint_is_a_noop(self) -> None:
        svc = {"image": "x", "entrypoint": [], "command": ["run"]}
        tokens = run_tokens("app", {"app": svc}, self._options())
        assert "--entrypoint" not in tokens
        assert tokens[-2:] == [Expand(value="x"), Expand(value="run")]


class TestSecretsLifecycle:
    def _script(self, doc: dict) -> str:
        options = EmitOptions(
            target="app",
            ci_image="ci",
            command="",
            pod="test-pod",
            project_dir="/proj",
            artifacts=[],
            allow_exit_codes=[],
        )
        return emit_script(compose=doc, options=options)

    def test_file_secret_create_and_trap_and_mount(self) -> None:
        doc = {"services": {"app": {"image": "x", "secrets": ["db"]}}, "secrets": {"db": {"file": "./db.txt"}}}
        script = self._script(doc)
        assert 'podman secret create test-pod-db "/proj/db.txt"' in script
        assert "podman secret rm test-pod-db" in script
        assert "--secret source=test-pod-db,target=db" in script

    def test_env_secret_referenced_variable_noted(self) -> None:
        doc = {"services": {"app": {"image": "x", "secrets": ["k"]}}, "secrets": {"k": {"environment": "API_KEY"}}}
        options = EmitOptions(
            target="app",
            ci_image="ci",
            command="",
            pod="p",
            project_dir="/proj",
            artifacts=[],
            allow_exit_codes=[],
        )
        assert "API_KEY" in referenced_variables(doc, options)

    def test_config_create_trap_and_mount(self) -> None:
        doc = {
            "services": {"app": {"image": "x", "configs": [{"source": "nginx", "target": "/etc/nginx.conf"}]}},
            "configs": {"nginx": {"file": "./nginx.conf"}},
        }
        script = self._script(doc)
        assert 'podman secret create test-pod-config-nginx "/proj/nginx.conf"' in script
        assert "podman secret rm test-pod-config-nginx" in script
        assert "--secret source=test-pod-config-nginx,target=/etc/nginx.conf" in script

    def test_secret_and_same_named_config_are_distinct_stores(self) -> None:
        doc = {
            "services": {"app": {"image": "x", "secrets": ["app"], "configs": ["app"]}},
            "secrets": {"app": {"file": "./s"}},
            "configs": {"app": {"file": "./c"}},
        }
        script = self._script(doc)
        assert "podman secret rm test-pod-app test-pod-config-app" in script
        assert "--secret source=test-pod-app,target=app" in script
        assert "--secret source=test-pod-config-app,target=/app" in script


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
                assert line.rstrip().endswith('"alembic" "upgrade" "head"')

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

    def test_add_host_becomes_pod_level(self, chats_compose: dict) -> None:
        script = self.make_script(chats_compose)
        pod_create = next(line for line in script.splitlines() if line.startswith("podman pod create"))
        assert "--add-host keydb-test-server-0:127.0.0.1" in pod_create
        run_lines = [line for line in script.splitlines() if line.startswith("podman run")]
        for line in run_lines:
            assert "--add-host" not in line

    def test_wait_healthy_function_uses_healthcheck_run(self, chats_compose: dict) -> None:
        script = self.make_script(chats_compose)
        assert "podman healthcheck run" in script
        assert "wait_healthy()" in script

    def test_podman_version_guard_present_in_header(self, chats_compose: dict) -> None:
        script = self.make_script(chats_compose)
        assert "podman version --format '{{.Client.Version}}'" in script
        assert "requires podman >= 6.0.0" in script
        version_guard_index = script.index("podman_version=")
        wait_healthy_index = script.index("wait_healthy()")
        set_eu_index = script.index("set -eu")
        assert set_eu_index < version_guard_index < wait_healthy_index

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

    def test_container_name_becomes_add_host_entry(self) -> None:
        compose = {
            "services": {
                "application": {"image": "app", "container_name": "calutron-ronline"},
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
        assert "--add-host calutron-ronline:127.0.0.1" in script

    def test_named_volume_round_trips_through_validate_and_emit(self) -> None:
        compose = {
            "services": {"db": {"image": "postgres:16", "volumes": ["calutrondb:/var/lib/postgresql/data"]}},
            "volumes": {"calutrondb": {"driver": "local"}},
        }
        options = EmitOptions(
            target="db",
            ci_image="ci",
            command="",
            pod="testpod",
            project_dir="/proj",
            artifacts=[],
            allow_exit_codes=[],
        )
        assert validate(compose) == ["ignoring top-level 'volumes' (podman creates named volumes on first reference)"]
        script = emit_script(compose=compose, options=options)
        assert '-v "calutrondb:/var/lib/postgresql/data"' in script

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
        assert target_line.rstrip().endswith('"python" "-m" "chats.api" || rc=$?')

    def _single(self, svc: dict, command: str = "") -> str:
        options = EmitOptions(
            target="app",
            ci_image="ci",
            command=command,
            pod="p",
            project_dir="/proj",
            artifacts=[],
            allow_exit_codes=[],
        )
        return emit_script(compose={"services": {"app": svc}}, options=options)

    def test_command_override_still_applies_with_entrypoint(self) -> None:
        script = self._single({"image": "x", "entrypoint": "serve"}, command="pytest tests")
        assert "--entrypoint /bin/sh" in script
        assert "pytest tests" in script.replace("'", "")
        target_line = next(line for line in script.splitlines() if "--entrypoint /bin/sh" in line)
        # The override tokens must land AFTER `-c "serve"`, proving they are
        # passed positionally to `sh -c` ($0/$1...) rather than executed --
        # a string entrypoint still runs only `serve`, never `pytest tests`.
        assert target_line.index('-c "serve"') < target_line.index("pytest")

    def test_all_process_identity_keys_compose_on_one_service(self) -> None:
        svc = {
            "image": "x",
            "user": "1000:1000",
            "working_dir": "/srv/app",
            "group_add": ["docker"],
            "labels": {"team": "api"},
            "entrypoint": ["serve"],
        }
        script = self._single(svc)
        assert '--user "1000:1000"' in script
        assert '--workdir "/srv/app"' in script
        assert '--group-add "docker"' in script
        assert '--label "team=api"' in script
        assert '--entrypoint "serve"' in script

    def test_confinement_keys_compose_on_one_service(self) -> None:
        svc = {
            "image": "x",
            "read_only": True,
            "init": True,
            "cap_add": ["NET_ADMIN"],
            "cap_drop": ["ALL"],
            "security_opt": ["label=disable"],
        }
        script = self._single(svc)
        for fragment in (
            "--read-only",
            "--init",
            '--cap-add "NET_ADMIN"',
            '--cap-drop "ALL"',
            '--security-opt "label=disable"',
        ):
            assert fragment in script

    def test_image_host_metadata_keys_compose_on_one_service(self) -> None:
        svc = {
            "image": "x",
            "platform": "linux/amd64",
            "devices": ["/dev/fuse"],
            "annotations": {"team": "api"},
            "extra_hosts": {"db.local": "10.0.0.5"},
            "pull_policy": "if_not_present",
        }
        script = self._single(svc)
        for fragment in (
            '--platform "linux/amd64"',
            '--device "/dev/fuse"',
            '--annotation "team=api"',
            '--add-host "db.local:10.0.0.5"',
            "--pull missing",
        ):
            assert fragment in script

    def test_ulimits_compose_through_emit_script(self) -> None:
        svc = {"image": "x", "ulimits": {"nproc": 65535, "nofile": {"soft": 20000, "hard": 40000}}}
        script = self._single(svc)
        assert '--ulimit "nproc=65535"' in script
        assert '--ulimit "nofile=20000:40000"' in script

    def test_pod_create_carries_dns_and_sysctl_flags(self) -> None:
        svc = {"image": "x", "dns": ["1.1.1.1", "8.8.8.8"], "sysctls": {"net.core.somaxconn": 1024}}
        script = self._single(svc)
        # `app` (the service's own name) is always a self-alias, so it precedes dns/sysctls.
        assert 'podman pod create --name p --add-host app:127.0.0.1 --dns "1.1.1.1" --dns "8.8.8.8"' in script
        assert '--sysctl "net.core.somaxconn=1024"' in script

    def test_pod_create_carries_only_self_alias_without_pod_options(self) -> None:
        # A service's own name is always a resolvable alias (`graph.hostnames`), so even
        # with no dns/sysctls/extra_hosts declared, pod create still carries its add-host.
        script = self._single({"image": "x"})
        assert "podman pod create --name p --add-host app:127.0.0.1\n" in script

    def test_env_file_required_false_is_guarded(self) -> None:
        svc = {"image": "x", "env_file": [{"path": "opt.env", "required": False}]}
        script = self._single(svc)
        assert "c2p_envfile_0=" in script
        assert '[ -f "/proj/opt.env" ] && c2p_envfile_0=--env-file="/proj/opt.env"' in script
        run_line = next(line for line in script.splitlines() if "podman run" in line and "--name p-app" in line)
        assert '${c2p_envfile_0:+"$c2p_envfile_0"}' in run_line

    def test_env_file_required_true_stays_unconditional(self) -> None:
        svc = {"image": "x", "env_file": [{"path": "req.env", "required": True}]}
        script = self._single(svc)
        assert "c2p_envfile" not in script
        assert '--env-file "/proj/req.env"' in script


class TestReferencedVariables:
    def _options(self, command: str = "") -> EmitOptions:
        return EmitOptions(
            target="app",
            ci_image="i",
            command=command,
            pod="p",
            project_dir="/p",
            artifacts=[],
            allow_exit_codes=[],
        )

    def test_collects_from_interpolated_fields_sorted(self) -> None:
        compose = {"services": {"app": {"image": "${IMG}", "environment": {"A": "${AVAR}", "B": "${BVAR}"}}}}
        assert referenced_variables(compose, self._options()) == ["AVAR", "BVAR", "IMG"]

    def test_dns_variable_is_referenced(self) -> None:
        doc = {"services": {"app": {"image": "x", "dns": ["${DNS_HOST}"]}}}
        assert "DNS_HOST" in referenced_variables(doc, self._options())

    def test_includes_env_file_excludes_non_interpolated_fields(self) -> None:
        compose = {
            "services": {
                "app": {
                    "image": "x",
                    "environment": {"K": "${LIVE}"},
                    "env_file": "${EDIR}/a.env",
                    "x-note": "${XVAR}",
                }
            }
        }
        assert referenced_variables(compose, self._options()) == ["EDIR", "LIVE"]

    def test_env_file_guarded_path_variable_is_reported(self) -> None:
        doc = {"services": {"app": {"image": "x", "env_file": [{"path": "${EDIR}/opt.env", "required": False}]}}}
        assert referenced_variables(doc, self._options()) == ["EDIR"]

    def test_command_override_vars_are_excluded(self) -> None:
        compose = {"services": {"app": {"image": "x", "command": "run ${CMDVAR}"}}}
        options = self._options(command="override ${SHELLVAR}")
        assert referenced_variables(compose, options) == []

    def test_collects_from_new_process_identity_fields(self) -> None:
        compose = {
            "services": {
                "app": {
                    "image": "x",
                    "user": "${U}",
                    "working_dir": "${WD}",
                    "group_add": ["${G}"],
                    "labels": {"team": "${T}"},
                }
            }
        }
        assert referenced_variables(compose, self._options()) == ["G", "T", "U", "WD"]

    def test_collects_from_entrypoint(self) -> None:
        compose = {"services": {"app": {"image": "x", "entrypoint": ["${EP}", "run"]}}}
        assert referenced_variables(compose, self._options()) == ["EP"]

    def test_collects_from_capability_and_security_lists(self) -> None:
        compose = {"services": {"app": {"image": "x", "cap_add": ["${CAP}"], "security_opt": ["${OPT}"]}}}
        assert referenced_variables(compose, self._options()) == ["CAP", "OPT"]

    def test_collects_from_extra_hosts(self) -> None:
        compose = {"services": {"app": {"image": "x", "extra_hosts": ["h:${HOST_IP}"]}}}
        assert referenced_variables(compose, self._options()) == ["HOST_IP"]

    def test_collects_from_ulimits(self) -> None:
        compose = {"services": {"app": {"image": "x", "ulimits": {"nofile": "${MAX}"}}}}
        assert referenced_variables(compose, self._options()) == ["MAX"]


class TestProfiles:
    def _script(self, doc: dict, target: str = "app") -> str:
        options = EmitOptions(
            target=target,
            ci_image="ci",
            command="",
            pod="test-pod",
            project_dir="/proj",
            artifacts=[],
            allow_exit_codes=[],
        )
        return emit_script(compose=doc, options=options)

    def test_profiles_key_does_not_change_the_emitted_script(self) -> None:
        # Nothing in emit reads 'profiles', so a service in the closure emits
        # identically with and without it.
        base = {"services": {"app": {"image": "x"}}}
        with_profiles = {"services": {"app": {"image": "x", "profiles": ["debug"]}}}
        assert self._script(with_profiles) == self._script(base)

    def test_profiled_service_outside_closure_is_not_run(self) -> None:
        # debug-tools is not in app's depends_on closure, so it never gets a
        # container (its name would appear as `test-pod-debug-tools` if run).
        doc = {
            "services": {
                "app": {"image": "x"},
                "debug-tools": {"image": "y", "profiles": ["debug"]},
            }
        }
        assert "test-pod-debug-tools" not in self._script(doc)

    def test_target_on_a_profiled_service_still_runs_it(self) -> None:
        # Targeting a service by name runs it regardless of its profile
        # (Compose auto-activates a targeted service's profile).
        doc = {
            "services": {
                "app": {"image": "x"},
                "debug-tools": {"image": "y", "profiles": ["debug"]},
            }
        }
        assert "test-pod-debug-tools" in self._script(doc, target="debug-tools")


class TestPodNameValidation:
    def _options(self, pod: str) -> EmitOptions:
        return EmitOptions(
            target="app",
            ci_image="i",
            command="",
            pod=pod,
            project_dir="/b",
            artifacts=[],
            allow_exit_codes=[],
        )

    def test_emit_script_rejects_invalid_pod_names(self) -> None:
        doc = {"services": {"app": {"image": "x"}}}
        for bad in ("p'; rm -rf /; '", "bad name", "$(touch x)", "", "-p", "p\n"):
            with pytest.raises(UnsupportedComposeError, match="invalid pod name"):
                emit_script(compose=doc, options=self._options(bad))

    def test_emit_script_accepts_a_valid_pod_name(self) -> None:
        doc = {"services": {"app": {"image": "x"}}}
        script = emit_script(compose=doc, options=self._options("test-pod.1"))
        assert "podman pod create --name test-pod.1" in script

    def test_referenced_variables_also_rejects_invalid_pod_names(self) -> None:
        # Used to only be checked by emit_script; referenced_variables projects
        # the same _plan traversal and skipped the check entirely.
        doc = {"services": {"app": {"image": "x"}}}
        with pytest.raises(UnsupportedComposeError, match="invalid pod name"):
            referenced_variables(doc, self._options("bad name"))


class TestArtifactValidation:
    def _options(self, artifacts: list[str]) -> EmitOptions:
        return EmitOptions(
            target="application",
            ci_image="ci:latest",
            command="",
            pod="test-pod",
            project_dir=".",
            artifacts=artifacts,
            allow_exit_codes=[],
        )

    def test_artifact_without_colon_raises(self, chats_compose: dict) -> None:
        # Used to escape as a raw ValueError: not enough values to unpack.
        options = self._options(["nocolon"])
        with pytest.raises(UnsupportedComposeError, match=r"artifact 'nocolon' must be in SRC:DST form"):
            emit_script(compose=chats_compose, options=options)

    def test_artifact_without_colon_also_raises_from_referenced_variables(self, chats_compose: dict) -> None:
        # The other public entry point projects the same _plan traversal.
        options = self._options(["nocolon"])
        with pytest.raises(UnsupportedComposeError, match=r"artifact 'nocolon' must be in SRC:DST form"):
            referenced_variables(chats_compose, options)

    def test_non_string_artifact_raises_cleanly(self, chats_compose: dict) -> None:
        # CLI-unreachable (argparse enforces str), but a library caller can
        # pass EmitOptions directly -- a non-string artifact used to crash
        # raw on the ':' membership test (TypeError: argument of type 'int'
        # is not iterable) instead of failing clean.
        options = self._options([3])  # ty: ignore[invalid-argument-type]
        with pytest.raises(UnsupportedComposeError, match=r"artifact 3 must be in SRC:DST form"):
            emit_script(compose=chats_compose, options=options)

    def test_valid_artifact_still_emits_podman_cp(self, chats_compose: dict) -> None:
        options = self._options(["/srv/out/junit.xml:junit.xml"])
        script = emit_script(compose=chats_compose, options=options)
        assert "podman cp test-pod-application:/srv/out/junit.xml junit.xml || true" in script


class TestAllowExitCodesValidation:
    def _options(self, allow_exit_codes: list[int]) -> EmitOptions:
        return EmitOptions(
            target="application",
            ci_image="ci:latest",
            command="",
            pod="test-pod",
            project_dir=".",
            artifacts=[],
            allow_exit_codes=allow_exit_codes,
        )

    def test_non_int_allow_exit_code_raises_cleanly(self, chats_compose: dict) -> None:
        # CLI-unreachable (argparse enforces int), but a library caller can
        # pass EmitOptions directly -- a non-int entry is interpolated
        # unquoted into the generated `case "$rc" in ...)` pattern, so an
        # unvalidated string is shell injection, not just a crash.
        options = self._options(
            ['0) ;; *) rm -rf / ;; esac; case "$rc" in 0']  # ty: ignore[invalid-argument-type]
        )
        with pytest.raises(UnsupportedComposeError, match=r"allow_exit_codes entry .* must be an int"):
            emit_script(compose=chats_compose, options=options)

    def test_bool_allow_exit_code_raises_cleanly(self, chats_compose: dict) -> None:
        # bool is an int subclass in Python; True/False are not meaningful
        # exit codes and must not slip past an isinstance(..., int) check.
        options = self._options([True])
        with pytest.raises(UnsupportedComposeError, match="allow_exit_codes entry True must be an int"):
            emit_script(compose=chats_compose, options=options)

    def test_valid_int_allow_exit_codes_still_accepted(self, chats_compose: dict) -> None:
        options = self._options([1, 2, 5])
        script = emit_script(compose=chats_compose, options=options)
        assert "in\n  0|1|2|5) ;;" in script


class TestPodmanVersionGuard:
    def _run_header(self, tmp_path: Path, podman_stub_body: str) -> "subprocess.CompletedProcess[str]":
        assert _SH is not None  # sh is a POSIX baseline binary, always present
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        podman_stub = bin_dir / "podman"
        podman_stub.write_text(f"#!/bin/sh\n{podman_stub_body}\n")
        podman_stub.chmod(0o755)
        header_path = tmp_path / "header.sh"
        header_path.write_text(_SCRIPT_HEADER)
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
        return subprocess.run(  # noqa: S603 - _SH is an absolute path from shutil.which, not untrusted input
            [_SH, str(header_path)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=10,
        )

    def test_warns_when_podman_major_below_six(self, tmp_path: Path) -> None:
        result = self._run_header(tmp_path, 'echo "5.8.1"')
        assert result.returncode == 0
        assert "podman 5.8.1 detected; compose2pod requires podman >= 6.0.0" in result.stderr
        assert "/etc/hosts" in result.stderr

    def test_silent_when_podman_major_six_or_above(self, tmp_path: Path) -> None:
        result = self._run_header(tmp_path, 'echo "6.0.1"')
        assert result.returncode == 0
        assert result.stderr == ""

    def test_silent_when_podman_version_unparseable(self, tmp_path: Path) -> None:
        result = self._run_header(tmp_path, "exit 1")
        assert result.returncode == 0
        assert result.stderr == ""


class TestPublicEntryPointsValidateWithoutBeingToldTo:
    """Both public entry points must reject malformed input on their own.

    emit_script/referenced_variables are public exports; a library caller may
    call either directly, skipping validate(). Both project the same `_plan`
    traversal, so `_plan` itself must gate malformed input -- these documents
    used to reach a raw crash or (worse) silently corrupt output.
    """

    def _options(self, target: str = "web") -> EmitOptions:
        return EmitOptions(
            target=target,
            ci_image="ci",
            command="",
            pod="p",
            project_dir=".",
            artifacts=[],
            allow_exit_codes=[],
        )

    def test_missing_services_key_raises_cleanly_not_a_keyerror(self) -> None:
        with pytest.raises(UnsupportedComposeError):
            emit_script(compose={}, options=self._options())

    def test_non_list_cap_add_raises_cleanly_not_a_typeerror(self) -> None:
        compose = {"services": {"web": {"image": "a", "cap_add": 3}}}
        with pytest.raises(UnsupportedComposeError):
            emit_script(compose=compose, options=self._options())

    def test_non_string_user_is_rejected_not_silently_stringified(self) -> None:
        # Before the _plan gate, this silently emitted the Python repr of the
        # dict as the --user value: --user "{'a': 1}" -- corruption, not a crash.
        compose = {"services": {"web": {"image": "a", "user": {"a": 1}}}}
        with pytest.raises(UnsupportedComposeError):
            emit_script(compose=compose, options=self._options())

    def test_referenced_variables_is_equally_guarded(self) -> None:
        # referenced_variables projects the same _plan traversal as emit_script
        # and must reject malformed input identically.
        with pytest.raises(UnsupportedComposeError):
            referenced_variables({}, self._options())


class TestAddHostClosureScope:
    """--add-host covers the target's closure, like every other emit-path aggregate."""

    def _options(self, target: str) -> EmitOptions:
        return EmitOptions(
            target=target,
            ci_image="ci:latest",
            command="",
            pod="test-pod",
            project_dir=".",
            artifacts=[],
            allow_exit_codes=[],
        )

    def test_service_outside_the_closure_is_not_resolvable(self) -> None:
        # A never-run service pointed its name at 127.0.0.1, where nothing listens.
        compose = {"services": {"app": {"image": "x"}, "never_run": {"image": "x"}}}
        script = emit_script(compose=compose, options=self._options("app"))
        assert "--add-host app:127.0.0.1" in script
        assert "never_run" not in script

    def test_out_of_closure_hostname_does_not_veto_extra_hosts(self) -> None:
        # 'other' is not in app's closure, so its hostname cannot conflict with app's extra_hosts.
        compose = {
            "services": {
                "app": {"image": "x", "extra_hosts": ["db:1.2.3.4"]},
                "other": {"image": "x", "hostname": "db"},
            }
        }
        script = emit_script(compose=compose, options=self._options("app"))
        assert '--add-host "db:1.2.3.4"' in script

    def test_in_closure_hostname_still_conflicts_with_extra_hosts(self) -> None:
        # The conflict rule still holds for services that actually run.
        compose = {
            "services": {
                "app": {"image": "x", "extra_hosts": ["db:1.2.3.4"], "depends_on": ["db_svc"]},
                "db_svc": {"image": "x", "hostname": "db"},
            }
        }
        with pytest.raises(UnsupportedComposeError, match="conflicting host"):
            emit_script(compose=compose, options=self._options("app"))

    def test_dependency_hostnames_and_aliases_still_resolve(self) -> None:
        # Everything inside the closure keeps its add-host entry.
        compose = {
            "services": {
                "app": {"image": "x", "depends_on": ["db"]},
                "db": {"image": "x", "hostname": "db-host", "networks": {"default": {"aliases": ["db-alias"]}}},
            },
            "networks": {"default": None},
        }
        script = emit_script(compose=compose, options=self._options("app"))
        for host in ("app", "db", "db-host", "db-alias"):
            assert f"--add-host {host}:127.0.0.1" in script


class TestGuardedEnvFileDependencyWiring:
    def test_env_file_guarded_on_dependency_service(self) -> None:
        # Proves the prelude wiring on the `-d` dependency branch (not just the target),
        # and that the prelude precedes the dependency's own `podman run -d` line.
        compose = {
            "services": {
                "app": {"image": "x", "depends_on": ["db"]},
                "db": {"image": "y", "env_file": [{"path": "db.env", "required": False}]},
            }
        }
        options = EmitOptions(
            target="app",
            ci_image="ci",
            command="",
            pod="p",
            project_dir="/proj",
            artifacts=[],
            allow_exit_codes=[],
        )
        script = emit_script(compose=compose, options=options)
        prelude = '[ -f "/proj/db.env" ] && c2p_envfile_0=--env-file="/proj/db.env"'
        db_run = next(line for line in script.splitlines() if "podman run -d" in line and "p-db" in line)
        assert prelude in script
        assert '${c2p_envfile_0:+"$c2p_envfile_0"}' in db_run
        assert script.index(prelude) < script.index(db_run)

    def test_env_file_guarded_on_completion_gated_dependency(self) -> None:
        # Proves the prelude wiring on the `--rm` completion-gated branch (a dependency
        # gated with service_completed_successfully renders via `podman run --rm`).
        compose = {
            "services": {
                "app": {"image": "x", "depends_on": {"job": {"condition": "service_completed_successfully"}}},
                "job": {"image": "y", "env_file": [{"path": "job.env", "required": False}]},
            }
        }
        options = EmitOptions(
            target="app",
            ci_image="ci",
            command="",
            pod="p",
            project_dir="/proj",
            artifacts=[],
            allow_exit_codes=[],
        )
        script = emit_script(compose=compose, options=options)
        prelude = '[ -f "/proj/job.env" ] && c2p_envfile_0=--env-file="/proj/job.env"'
        rm_run = next(line for line in script.splitlines() if "podman run --rm" in line and "p-job" in line)
        assert prelude in script
        assert '${c2p_envfile_0:+"$c2p_envfile_0"}' in rm_run
        assert script.index(prelude) < script.index(rm_run)
