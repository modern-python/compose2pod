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
        with pytest.raises(UnsupportedComposeError, match="network_mode"):
            validate({"services": {"app": {"image": "x", "network_mode": "host"}}})

    def test_unsupported_healthcheck_key_raises(self) -> None:
        compose = {"services": {"app": {"image": "x", "healthcheck": {"test": "true", "start_interval": "1s"}}}}
        with pytest.raises(UnsupportedComposeError, match="start_interval"):
            validate(compose)

    def test_named_volume_is_accepted(self) -> None:
        compose = {"services": {"db": {"image": "x", "volumes": ["pgdata:/var/lib/postgresql/data"]}}}
        assert validate(compose) == []

    def test_long_volume_syntax_raises(self) -> None:
        compose = {"services": {"app": {"image": "x", "volumes": [{"type": "bind", "source": ".", "target": "/s"}]}}}
        with pytest.raises(UnsupportedComposeError, match="short volume syntax"):
            validate(compose)

    def test_anonymous_volume_is_accepted(self) -> None:
        assert validate({"services": {"app": {"image": "x", "volumes": ["/var/cache/models"]}}}) == []

    def test_relative_anonymous_volume_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="absolute"):
            validate({"services": {"app": {"image": "x", "volumes": ["./cache"]}}})

    def test_no_services_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="no services"):
            validate({"services": {}})

    def test_unknown_top_level_key_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="secrets"):
            validate({"services": {"app": {"image": "x"}}, "secrets": {}})

    def test_top_level_networks_is_ignored_with_warning(self) -> None:
        warnings = validate({"services": {"app": {"image": "x"}}, "networks": {"default": None}})
        assert any("networks" in w for w in warnings)

    def test_top_level_volumes_is_ignored_with_warning(self) -> None:
        compose = {"services": {"app": {"image": "x"}}, "volumes": {"pgdata": {"driver": "local"}}}
        warnings = validate(compose)
        assert any("volumes" in w for w in warnings)

    def test_service_healthy_dependency_without_healthcheck_raises(self) -> None:
        compose = {
            "services": {
                "migrations": {"image": "x", "depends_on": {"db": {"condition": "service_healthy"}}},
                "db": {"image": "y"},
            }
        }
        with pytest.raises(
            UnsupportedComposeError, match=r"depends on 'db' \(service_healthy\) but 'db' has no healthcheck"
        ):
            validate(compose)

    def test_service_healthy_dependency_with_none_test_raises(self) -> None:
        compose = {
            "services": {
                "migrations": {"image": "x", "depends_on": {"db": {"condition": "service_healthy"}}},
                "db": {"image": "y", "healthcheck": {"test": "NONE"}},
            }
        }
        with pytest.raises(UnsupportedComposeError, match=r"depends on 'db' \(service_healthy\)"):
            validate(compose)

    def test_service_healthy_dependency_with_healthcheck_is_accepted(self) -> None:
        compose = {
            "services": {
                "migrations": {"image": "x", "depends_on": {"db": {"condition": "service_healthy"}}},
                "db": {"image": "y", "healthcheck": {"test": ["CMD-SHELL", "true"]}},
            }
        }
        assert validate(compose) == []

    def test_service_healthy_dependency_on_unknown_service_is_out_of_scope(self) -> None:
        assert (
            validate({"services": {"app": {"image": "x", "depends_on": {"ghost": {"condition": "service_healthy"}}}}})
            == []
        )

    def test_unknown_depends_on_condition_raises(self) -> None:
        compose = {
            "services": {
                "app": {"image": "x", "depends_on": {"db": {"condition": "service_ready"}}},
                "db": {"image": "y"},
            }
        }
        with pytest.raises(
            UnsupportedComposeError, match=r"service 'app': depends_on 'db' has unsupported condition 'service_ready'"
        ):
            validate(compose)

    def test_top_level_extension_key_is_accepted(self) -> None:
        compose = {"x-application-defaults": {"build": {}}, "services": {"app": {"image": "x"}}}
        assert validate(compose) == []

    def test_service_extension_key_is_accepted_silently(self) -> None:
        warnings = validate({"services": {"app": {"image": "x", "x-labels": {"team": "a"}}}})
        assert warnings == []

    def test_healthcheck_extension_key_is_accepted(self) -> None:
        compose = {"services": {"app": {"image": "x", "healthcheck": {"test": "true", "x-note": "n"}}}}
        assert validate(compose) == []

    def test_service_hostname_is_accepted(self) -> None:
        compose = {"services": {"keydb": {"image": "x", "hostname": "keydb-test-server-0"}}}
        assert validate(compose) == []

    def test_service_container_name_is_accepted(self) -> None:
        compose = {"services": {"application": {"image": "x", "container_name": "calutron-ronline"}}}
        assert validate(compose) == []

    def test_service_tmpfs_is_accepted(self) -> None:
        compose = {"services": {"app": {"image": "x", "tmpfs": ["/tmp:mode=1777"]}}}  # noqa: S108
        assert validate(compose) == []

    def test_user_and_working_dir_accepted(self) -> None:
        assert validate({"services": {"app": {"image": "x", "user": "root", "working_dir": "/app"}}}) == []

    def test_user_non_string_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"'user' must be a string"):
            validate({"services": {"app": {"image": "x", "user": 1000}}})

    def test_working_dir_non_string_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"'working_dir' must be a string"):
            validate({"services": {"app": {"image": "x", "working_dir": ["/app"]}}})

    def test_group_add_accepted(self) -> None:
        assert validate({"services": {"app": {"image": "x", "group_add": ["docker"]}}}) == []

    def test_group_add_non_list_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"'group_add' must be a list"):
            validate({"services": {"app": {"image": "x", "group_add": "docker"}}})

    def test_labels_accepted(self) -> None:
        assert validate({"services": {"app": {"image": "x", "labels": {"team": "api"}}}}) == []

    def test_labels_non_list_or_map_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"'labels' must be a list or mapping"):
            validate({"services": {"app": {"image": "x", "labels": "team=api"}}})

    def test_entrypoint_list_accepted(self) -> None:
        assert validate({"services": {"app": {"image": "x", "entrypoint": ["run"]}}}) == []

    def test_entrypoint_non_string_or_list_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"'entrypoint' must be a string or list"):
            validate({"services": {"app": {"image": "x", "entrypoint": {"a": "b"}}}})

    def test_string_entrypoint_with_command_warns(self) -> None:
        warnings = validate({"services": {"app": {"image": "x", "entrypoint": "serve", "command": ["x"]}}})
        assert any("command' is ignored" in w for w in warnings)

    def test_boolean_confinement_keys_accepted(self) -> None:
        svc = {"image": "x", "read_only": True, "init": True, "privileged": False}
        assert validate({"services": {"app": svc}}) == []

    def test_read_only_non_bool_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"'read_only' must be a boolean"):
            validate({"services": {"app": {"image": "x", "read_only": "yes"}}})
