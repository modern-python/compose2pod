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

    def test_stop_lifecycle_keys_are_ignored_with_warning(self) -> None:
        # Inert under the pod force teardown -> accepted with a warning, not rejected.
        svc = {"image": "x", "stop_signal": "SIGINT", "stop_grace_period": "30s"}
        warnings = validate({"services": {"app": svc}})
        assert any("stop_signal" in w for w in warnings)
        assert any("stop_grace_period" in w for w in warnings)

    def test_profiles_is_ignored_with_warning(self) -> None:
        # profiles cannot change the --target + depends_on run set, so it is
        # accepted and warned, not rejected.
        svc = {"image": "x", "profiles": ["debug"]}
        warnings = validate({"services": {"app": svc}})
        assert any("profiles" in w for w in warnings)

    def test_non_dict_document_raises(self) -> None:
        for bad in (None, [], "compose", 42):
            with pytest.raises(UnsupportedComposeError, match="must be a mapping"):
                validate(bad)  # ty: ignore[invalid-argument-type]

    def test_non_dict_service_value_is_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="must be a mapping"):
            validate({"services": {"web": None}})

    def test_resource_limits_accepted(self) -> None:
        svc = {"image": "x", "mem_limit": "512m", "cpus": 0.5, "pids_limit": 100, "oom_kill_disable": True}
        assert validate({"services": {"app": svc}}) == []

    def test_dns_and_sysctls_accepted_with_pod_wide_warning(self) -> None:
        svc = {"image": "x", "dns": ["1.1.1.1"], "sysctls": {"net.core.somaxconn": 1024}}
        warnings = validate({"services": {"app": svc}})
        assert any("pod-wide" in w for w in warnings)

    def test_dns_bad_shape_rejected_at_gate(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="must be a string or list of strings"):
            validate({"services": {"app": {"image": "x", "dns": {"bad": "shape"}}}})

    def test_no_pod_options_no_pod_wide_warning(self) -> None:
        assert validate({"services": {"app": {"image": "x"}}}) == []

    def test_mem_limit_bool_value_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"'mem_limit' must be a number or string"):
            validate({"services": {"app": {"image": "x", "mem_limit": True}}})

    def test_cpus_list_value_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"'cpus' must be a number or string"):
            validate({"services": {"app": {"image": "x", "cpus": [1]}}})

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

    def test_string_volumes_rejected_at_gate(self) -> None:
        # Used to be iterated character-wise: "/data:/data" reported the nonsense
        # "anonymous volume 'd'", and "/" was silently accepted as -v "/".
        with pytest.raises(UnsupportedComposeError, match=r"'volumes' must be a list"):
            validate({"services": {"app": {"image": "x", "volumes": "/data:/data"}}})

    def test_single_slash_string_volumes_rejected_at_gate(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"'volumes' must be a list"):
            validate({"services": {"app": {"image": "x", "volumes": "/"}}})

    def test_null_volumes_is_accepted(self) -> None:
        assert validate({"services": {"app": {"image": "x", "volumes": None}}}) == []

    def test_no_services_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="no services"):
            validate({"services": {}})

    def test_non_mapping_services_rejected_at_gate(self) -> None:
        # Used to reach services.items() inside validate() itself and crash raw
        # with AttributeError: 'str'/'list' object has no attribute 'items'.
        with pytest.raises(UnsupportedComposeError, match="'services' must be a mapping"):
            validate({"services": "app"})
        with pytest.raises(UnsupportedComposeError, match="'services' must be a mapping"):
            validate({"services": ["app"]})

    def test_unknown_top_level_key_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="foo"):
            validate({"services": {"app": {"image": "x"}}, "foo": {}})

    def test_non_string_top_level_key_raises_instead_of_crashing_raw(self) -> None:
        # PyYAML routinely produces non-string keys (int, or a YAML-1.1 bool
        # from a bare `on:`). Used to crash raw: AttributeError: 'int' object
        # has no attribute 'startswith', from k.startswith("x-").
        with pytest.raises(UnsupportedComposeError, match="key 3 must be a string"):
            validate({3: "x", "services": {"app": {"image": "x"}}})  # ty: ignore[invalid-argument-type]

    def test_non_string_service_key_raises_instead_of_crashing_raw(self) -> None:
        # A non-string key *inside* a service mapping (an indent slip) used
        # to crash raw: TypeError: '<' not supported between instances of
        # 'int' and 'str', from sorted(svc).
        with pytest.raises(UnsupportedComposeError, match="key 8080 must be a string"):
            validate({"services": {"app": {"image": "x", 8080: 8080}}})

    def test_non_string_healthcheck_key_raises_instead_of_crashing_raw(self) -> None:
        # Same class as the service-key case, one level deeper: used to crash
        # raw via sorted(healthcheck).
        compose = {"services": {"app": {"image": "x", "healthcheck": {1: "x", "test": "true"}}}}
        with pytest.raises(UnsupportedComposeError, match="key 1 must be a string"):
            validate(compose)

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

    def test_capability_list_keys_accepted(self) -> None:
        svc = {"image": "x", "cap_add": ["NET_ADMIN"], "cap_drop": ["ALL"], "security_opt": ["label=disable"]}
        assert validate({"services": {"app": svc}}) == []

    def test_security_opt_non_list_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"'security_opt' must be a list"):
            validate({"services": {"app": {"image": "x", "security_opt": "label=disable"}}})

    def test_labels_accepted(self) -> None:
        assert validate({"services": {"app": {"image": "x", "labels": {"team": "api"}}}}) == []

    def test_labels_non_list_or_map_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"'labels' must be a list or mapping"):
            validate({"services": {"app": {"image": "x", "labels": "team=api"}}})

    def test_platform_devices_annotations_accepted(self) -> None:
        svc = {"image": "x", "platform": "linux/arm64", "devices": ["/dev/fuse"], "annotations": {"k": "v"}}
        assert validate({"services": {"app": svc}}) == []

    def test_platform_non_string_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"'platform' must be a string"):
            validate({"services": {"app": {"image": "x", "platform": ["linux/amd64"]}}})

    def test_devices_non_list_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"'devices' must be a list"):
            validate({"services": {"app": {"image": "x", "devices": "/dev/fuse"}}})

    def test_annotations_non_list_or_map_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"'annotations' must be a list or mapping"):
            validate({"services": {"app": {"image": "x", "annotations": "k=v"}}})

    def test_extra_hosts_accepted(self) -> None:
        # extra_hosts is pod-level like dns/sysctls, so it carries the pod-wide warning.
        warnings = validate({"services": {"app": {"image": "x", "extra_hosts": {"db.local": "10.0.0.5"}}}})
        assert any("pod-wide" in w for w in warnings)

    def test_extra_hosts_non_list_or_map_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"'extra_hosts' must be a list or mapping"):
            validate({"services": {"app": {"image": "x", "extra_hosts": "db.local:10.0.0.5"}}})

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

    def test_pull_policy_accepted(self) -> None:
        assert validate({"services": {"app": {"image": "x", "pull_policy": "always"}}}) == []

    def test_pull_policy_build_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="pull_policy 'build'"):
            validate({"services": {"app": {"image": "x", "pull_policy": "build"}}})

    def test_pull_policy_unknown_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="pull_policy 'sometimes'"):
            validate({"services": {"app": {"image": "x", "pull_policy": "sometimes"}}})

    def test_ulimits_accepted(self) -> None:
        svc = {"image": "x", "ulimits": {"nproc": 65535, "nofile": {"soft": 1024, "hard": 2048}}}
        assert validate({"services": {"app": svc}}) == []

    def test_ulimits_non_mapping_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"'ulimits' must be a mapping"):
            validate({"services": {"app": {"image": "x", "ulimits": ["nofile=1024"]}}})

    def test_ulimits_mapping_missing_hard_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="must have exactly 'soft' and 'hard'"):
            validate({"services": {"app": {"image": "x", "ulimits": {"nofile": {"soft": 1024}}}}})

    def test_ulimits_list_valued_limit_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="must be an int or a soft/hard mapping"):
            validate({"services": {"app": {"image": "x", "ulimits": {"nofile": [1, 2]}}}})

    def test_ulimits_non_scalar_soft_hard_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="'soft' and 'hard' must be int or str"):
            validate({"services": {"app": {"image": "x", "ulimits": {"nofile": {"soft": [1, 2], "hard": 3}}}}})

    def test_ulimits_boolean_scalar_raises(self) -> None:
        # bool IS an int in Python, so isinstance(spec, int | str) let it
        # through and emit rendered the literal --ulimit "nofile=True". A
        # boolean ulimit is meaningless -- unlike environment's bool (which
        # Docker normalizes), there is no sensible ulimit normalization.
        with pytest.raises(UnsupportedComposeError, match="must be an int or a soft/hard mapping"):
            validate({"services": {"app": {"image": "x", "ulimits": {"nofile": True}}}})

    def test_ulimits_boolean_soft_hard_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="'soft' and 'hard' must be int or str"):
            validate({"services": {"app": {"image": "x", "ulimits": {"nofile": {"soft": True, "hard": 100}}}}})

    def test_pull_policy_null_is_accepted(self) -> None:
        assert validate({"services": {"app": {"image": "x", "pull_policy": None}}}) == []

    def test_ulimits_null_is_accepted(self) -> None:
        assert validate({"services": {"app": {"image": "x", "ulimits": None}}}) == []

    def test_non_mapping_healthcheck_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="healthcheck must be a mapping"):
            validate({"services": {"app": {"image": "x", "healthcheck": ["CMD", "true"]}}})

    def test_unparseable_healthcheck_interval_raises(self) -> None:
        compose = {"services": {"app": {"image": "x", "healthcheck": {"test": "true", "interval": "1h30m"}}}}
        with pytest.raises(UnsupportedComposeError, match="unsupported healthcheck interval"):
            validate(compose)

    def test_healthcheck_scalars_accept_ints_and_strings(self) -> None:
        compose = {
            "services": {
                "app": {
                    "image": "x",
                    "healthcheck": {"test": "true", "retries": 15, "timeout": "5s", "start_period": "10s"},
                }
            }
        }
        assert validate(compose) == []

    def test_healthcheck_retries_mapping_rejected_at_gate(self) -> None:
        # Used to be silently accepted and mis-emitted as the literal
        # --health-retries "{'a': 1}".
        compose = {"services": {"app": {"image": "x", "healthcheck": {"test": "true", "retries": {"a": 1}}}}}
        with pytest.raises(UnsupportedComposeError, match=r"healthcheck 'retries' must be a number or string"):
            validate(compose)

    def test_healthcheck_timeout_list_rejected_at_gate(self) -> None:
        compose = {"services": {"app": {"image": "x", "healthcheck": {"test": "true", "timeout": [5]}}}}
        with pytest.raises(UnsupportedComposeError, match=r"healthcheck 'timeout' must be a number or string"):
            validate(compose)

    def test_healthcheck_start_period_mapping_rejected_at_gate(self) -> None:
        compose = {"services": {"app": {"image": "x", "healthcheck": {"test": "true", "start_period": {"a": 1}}}}}
        with pytest.raises(UnsupportedComposeError, match=r"healthcheck 'start_period' must be a number or string"):
            validate(compose)

    def test_healthcheck_test_cmd_shell_nested_list_rejected_at_gate(self) -> None:
        # Used to reach emit and crash raw: health_cmd() returned test[1]
        # (the nested list) unchecked, and it hit shell.py's re.finditer.
        compose = {
            "services": {"app": {"image": "x", "healthcheck": {"test": ["CMD-SHELL", ["curl", "-f"]]}}},
        }
        with pytest.raises(UnsupportedComposeError, match="unsupported healthcheck test"):
            validate(compose)

    def test_healthcheck_test_cmd_non_string_argument_rejected_at_gate(self) -> None:
        compose = {"services": {"app": {"image": "x", "healthcheck": {"test": ["CMD", 123]}}}}
        with pytest.raises(UnsupportedComposeError, match="unsupported healthcheck test"):
            validate(compose)

    def test_healthcheck_test_forms_still_accepted(self) -> None:
        for test in ("true", "NONE", ["NONE"], ["CMD", "a", "b"], ["CMD-SHELL", "some string"]):
            assert validate({"services": {"app": {"image": "x", "healthcheck": {"test": test}}}}) == []

    def test_tmpfs_non_string_or_list_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="'tmpfs' must be a string or list"):
            validate({"services": {"app": {"image": "x", "tmpfs": {"a": "b"}}}})

    def test_tmpfs_list_with_non_string_entry_rejected_at_gate(self) -> None:
        # Used to reach emit and crash with TypeError (shell.py to_shell/variable_names
        # expects a str) -- the same element-level gap already fixed for env_file.
        with pytest.raises(UnsupportedComposeError, match="'tmpfs' entry must be a string"):
            validate({"services": {"app": {"image": "x", "tmpfs": [5]}}})

    def test_tmpfs_string_and_list_of_strings_still_accepted(self) -> None:
        assert validate({"services": {"app": {"image": "x", "tmpfs": "/tmp"}}}) == []  # noqa: S108
        assert validate({"services": {"app": {"image": "x", "tmpfs": ["/tmp", "/run"]}}}) == []  # noqa: S108
        assert validate({"services": {"app": {"image": "x", "tmpfs": None}}}) == []

    def test_non_string_hostname_raises_at_gate(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="hostname must be a string"):
            validate({"services": {"app": {"image": "x", "hostname": 5}}})

    def test_networks_not_list_or_mapping_raises_at_gate(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="networks must be a list or mapping"):
            validate({"services": {"app": {"image": "x", "networks": "default"}}})

    def test_malformed_depends_on_raises_at_gate(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="'depends_on' must be a list or mapping"):
            validate({"services": {"app": {"image": "x", "depends_on": "db"}}})

    def test_depends_on_list_with_mapping_entry_raises_at_gate(self) -> None:
        # Same list/map YAML slip as `environment`/`command`. Used to crash
        # raw (TypeError: unhashable type: 'dict') from inside validate()
        # itself, via graph.depends_on's dict.fromkeys.
        compose = {
            "services": {
                "app": {"image": "x", "depends_on": [{"db": {"condition": "service_healthy"}}]},
                "db": {"image": "x"},
            },
        }
        with pytest.raises(UnsupportedComposeError, match=r"depends_on entry .* must be a string"):
            validate(compose)

    def test_valid_networks_forms_accepted(self) -> None:
        assert validate({"services": {"app": {"image": "x", "networks": ["n1"]}}}) == []
        assert validate({"services": {"app": {"image": "x", "networks": {"default": None}}}}) == []

    def test_secrets_top_level_and_service_ref_accepted(self) -> None:
        doc = {"services": {"app": {"image": "x", "secrets": ["db"]}}, "secrets": {"db": {"file": "./db.txt"}}}
        assert validate(doc) == []

    def test_unknown_secret_reference_raises(self) -> None:
        doc = {"services": {"app": {"image": "x", "secrets": ["ghost"]}}}
        with pytest.raises(UnsupportedComposeError, match="unknown secret 'ghost'"):
            validate(doc)

    def test_config_short_form_accepted(self) -> None:
        doc = {"services": {"app": {"image": "x", "configs": ["c"]}}, "configs": {"c": {"content": "hi"}}}
        assert validate(doc) == []

    def test_unknown_config_reference_rejected(self) -> None:
        doc = {"services": {"app": {"image": "x", "configs": ["ghost"]}}, "configs": {"c": {"file": "./c"}}}
        with pytest.raises(UnsupportedComposeError, match="unknown config"):
            validate(doc)

    def test_relative_config_target_rejected_at_gate(self) -> None:
        doc = {
            "services": {"app": {"image": "x", "configs": [{"source": "c", "target": "c.conf"}]}},
            "configs": {"c": {"file": "./c"}},
        }
        with pytest.raises(UnsupportedComposeError, match="must be an absolute path"):
            validate(doc)

    def test_deploy_resources_accepted(self) -> None:
        svc = {"image": "x", "deploy": {"resources": {"limits": {"cpus": "0.5", "memory": "256m"}}}}
        assert validate({"services": {"app": svc}}) == []

    def test_deploy_legacy_conflict_rejected_at_gate(self) -> None:
        svc = {"image": "x", "mem_limit": "512m", "deploy": {"resources": {"limits": {"memory": "256m"}}}}
        with pytest.raises(UnsupportedComposeError, match=r"conflicts with deploy.resources.limits.memory"):
            validate({"services": {"app": svc}})

    def test_deploy_reservations_cpus_rejected_at_gate(self) -> None:
        svc = {"image": "x", "deploy": {"resources": {"reservations": {"cpus": "0.5"}}}}
        with pytest.raises(UnsupportedComposeError, match=r"reservations.cpus is not supported"):
            validate({"services": {"app": svc}})

    def test_string_environment_rejected_at_gate(self) -> None:
        # Structural key with no KeySpec: used to reach emit and crash with
        # AttributeError: 'str' object has no attribute 'items'.
        with pytest.raises(UnsupportedComposeError, match=r"'environment' must be a list or mapping"):
            validate({"services": {"app": {"image": "x", "environment": "FOO=bar"}}})

    def test_null_environment_is_accepted(self) -> None:
        assert validate({"services": {"app": {"image": "x", "environment": None}}}) == []

    def test_environment_list_of_mapping_rejected_at_gate(self) -> None:
        # The commonest Compose YAML slip: `- KEY: value` (list + mapping)
        # instead of `- KEY=value`. Used to be silently accepted and emit the
        # literal `-e "{'POSTGRES_PASSWORD': 'password'}"` -- exit 0, garbage.
        compose = {"services": {"app": {"image": "x", "environment": [{"POSTGRES_PASSWORD": "password"}]}}}
        with pytest.raises(UnsupportedComposeError, match="'environment' entries must be strings"):
            validate(compose)

    def test_environment_list_and_map_forms_still_accepted(self) -> None:
        assert validate({"services": {"app": {"image": "x", "environment": ["KEY=value", "BARE"]}}}) == []
        assert validate({"services": {"app": {"image": "x", "environment": {"KEY": "value", "BARE": None}}}}) == []

    def test_environment_boolean_map_value_accepted(self) -> None:
        # docker compose config normalizes `DEBUG: true` to the string "true";
        # rejecting a bool value here would be an over-rejection, not a fix.
        assert validate({"services": {"app": {"image": "x", "environment": {"DEBUG": True}}}}) == []

    def test_labels_annotations_list_of_mapping_rejected_at_gate(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="'labels' entries must be strings"):
            validate({"services": {"app": {"image": "x", "labels": [{"team": "core"}]}}})

    def test_labels_map_non_scalar_value_rejected_at_gate(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="'labels' values must be"):
            validate({"services": {"app": {"image": "x", "labels": {"team": {"nested": "dict"}}}}})

    def test_labels_null_and_numeric_map_values_still_accepted(self) -> None:
        assert validate({"services": {"app": {"image": "x", "labels": {"empty": None, "version": 2}}}}) == []

    def test_cap_add_list_of_mapping_rejected_at_gate(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="'cap_add' entries must be strings"):
            validate({"services": {"app": {"image": "x", "cap_add": [{"NET_ADMIN": True}]}}})

    def test_non_string_non_list_env_file_rejected_at_gate(self) -> None:
        # Used to reach emit and crash with TypeError: 'int' object is not iterable.
        with pytest.raises(UnsupportedComposeError, match=r"'env_file' must be a string or list"):
            validate({"services": {"app": {"image": "x", "env_file": 5}}})

    def test_string_and_list_env_file_are_accepted(self) -> None:
        assert validate({"services": {"app": {"image": "x", "env_file": "tests.env"}}}) == []
        assert validate({"services": {"app": {"image": "x", "env_file": ["a.env", "b.env"]}}}) == []
        assert validate({"services": {"app": {"image": "x", "env_file": None}}}) == []

    def test_env_file_list_with_non_string_entry_rejected_at_gate(self) -> None:
        # Used to reach emit and crash with TypeError: argument should be a str or an os.PathLike object.
        with pytest.raises(UnsupportedComposeError, match=r"'env_file' entry must be a string"):
            validate({"services": {"app": {"image": "x", "env_file": [5]}}})

    def test_service_with_neither_image_nor_build_rejected_at_gate(self) -> None:
        # Used to reach emit and crash with KeyError: 'image' (image_for).
        with pytest.raises(UnsupportedComposeError, match=r"must set 'image' or 'build'"):
            validate({"services": {"app": {}}})

    def test_service_with_build_and_no_image_is_accepted(self) -> None:
        # The normal CI case: --image replaces a build section's own image.
        assert validate({"services": {"app": {"build": {"context": "."}}}}) == []

    def test_non_string_image_rejected_at_gate(self) -> None:
        # Used to reach emit and crash with TypeError: expected string or bytes-like object, got 'int'.
        with pytest.raises(UnsupportedComposeError, match=r"'image' must be a string"):
            validate({"services": {"app": {"image": 5}}})

    def test_non_string_image_with_build_present_is_accepted(self) -> None:
        # image_for never reads svc['image'] when 'build' is present, so a
        # malformed image alongside 'build' cannot crash emit.
        assert validate({"services": {"app": {"image": 5, "build": {"context": "."}}}}) == []

    def test_command_string_or_list_accepted(self) -> None:
        assert validate({"services": {"app": {"image": "x", "command": "run me"}}}) == []
        assert validate({"services": {"app": {"image": "x", "command": ["run", "me"]}}}) == []

    def test_missing_command_is_accepted(self) -> None:
        assert validate({"services": {"app": {"image": "x"}}}) == []

    def test_non_string_or_list_command_rejected_at_gate(self) -> None:
        # Used to reach emit and crash with TypeError: 'int' object is not iterable.
        with pytest.raises(UnsupportedComposeError, match=r"'command' must be a string or list"):
            validate({"services": {"app": {"image": "x", "command": 5}}})

    def test_mapping_command_rejected_at_gate(self) -> None:
        # Used to be silently accepted and mis-emitted: only the mapping's key
        # ('run') reached podman run, the value ('tests') was dropped.
        with pytest.raises(UnsupportedComposeError, match=r"'command' must be a string or list"):
            validate({"services": {"app": {"image": "x", "command": {"run": "tests"}}}})

    def test_null_command_is_accepted(self) -> None:
        assert validate({"services": {"app": {"image": "x", "command": None}}}) == []

    def test_command_list_with_mapping_entry_rejected_at_gate(self) -> None:
        # Used to be silently accepted: str({'run': 'tests'}) reached podman
        # run as a single mangled argv token, e.g. "{'run': 'tests'}".
        with pytest.raises(UnsupportedComposeError, match="'command' entries must be strings"):
            validate({"services": {"app": {"image": "x", "command": [{"run": "tests"}]}}})

    def test_entrypoint_list_with_non_string_entry_rejected_at_gate(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="'entrypoint' entries must be strings"):
            validate({"services": {"app": {"image": "x", "entrypoint": [{"run": "tests"}]}}})

    def test_command_and_entrypoint_list_of_strings_still_accepted(self) -> None:
        assert validate({"services": {"app": {"image": "x", "command": ["run", "me"]}}}) == []


class TestRequireStringKeysDeep:
    """validate()'s document-wide recursive non-string-mapping-key sweep.

    F1: a non-string key in a mapping that gets f-string-interpolated into a
    flag value (environment/labels/annotations/sysctls/extra_hosts/ulimits)
    used to leak Python's bool/int repr into the emitted script rather than
    crash -- the hand-placed require_string_keys call sites never covered
    these because none of them sorted() or startswith()'d that key. F2: a
    non-string *service name* (a key of the `services` mapping itself) was
    never checked at all. Both are closed by one recursive sweep instead of
    hand-placing more calls -- see _require_string_keys_deep.
    """

    def test_environment_non_string_key_rejected(self) -> None:
        # YAML 1.1: a bare `on:` parses as the Python bool True.
        compose = {"services": {"app": {"image": "x", "environment": {True: "1"}}}}
        with pytest.raises(UnsupportedComposeError, match=r"environment: key True must be a string"):
            validate(compose)

    def test_labels_non_string_key_rejected(self) -> None:
        compose = {"services": {"app": {"image": "x", "labels": {True: "v"}}}}
        with pytest.raises(UnsupportedComposeError, match=r"labels: key True must be a string"):
            validate(compose)

    def test_annotations_non_string_key_rejected(self) -> None:
        compose = {"services": {"app": {"image": "x", "annotations": {False: "v"}}}}
        with pytest.raises(UnsupportedComposeError, match=r"annotations: key False must be a string"):
            validate(compose)

    def test_sysctls_non_string_key_rejected(self) -> None:
        compose = {"services": {"app": {"image": "x", "sysctls": {True: 1}}}}
        with pytest.raises(UnsupportedComposeError, match=r"sysctls: key True must be a string"):
            validate(compose)

    def test_extra_hosts_non_string_key_rejected(self) -> None:
        compose = {"services": {"app": {"image": "x", "extra_hosts": {True: "1.2.3.4"}}}}
        with pytest.raises(UnsupportedComposeError, match=r"extra_hosts: key True must be a string"):
            validate(compose)

    def test_ulimits_non_string_key_rejected(self) -> None:
        compose = {"services": {"app": {"image": "x", "ulimits": {True: 100}}}}
        with pytest.raises(UnsupportedComposeError, match=r"ulimits: key True must be a string"):
            validate(compose)

    def test_ulimits_nested_soft_hard_non_string_key_rejected(self) -> None:
        compose = {"services": {"app": {"image": "x", "ulimits": {"nofile": {True: 1024, "hard": 4096}}}}}
        with pytest.raises(UnsupportedComposeError, match=r"ulimits\.nofile: key True must be a string"):
            validate(compose)

    def test_non_string_service_name_rejected(self) -> None:
        # F2: the `services` mapping's own keys (service names) were never
        # checked -- an int/bool name reaches --add-host and --name verbatim.
        compose = {"services": {"app": {"image": "i"}, True: {"image": "j"}}}
        with pytest.raises(UnsupportedComposeError, match=r"compose document\.services: key True must be a string"):
            validate(compose)

    def test_deploy_resources_limits_non_string_key_via_full_pipeline(self) -> None:
        # A deeper structural key, reached only by walking the whole
        # document -- confirms the sweep isn't limited to the sites the old
        # hand-placed calls were wired into.
        compose = {"services": {"app": {"image": "x", "deploy": {"resources": {"limits": {True: "1"}}}}}}
        with pytest.raises(UnsupportedComposeError, match=r"deploy\.resources\.limits: key True must be a string"):
            validate(compose)

    def test_secret_definition_non_string_name_via_full_pipeline(self) -> None:
        compose = {"services": {"app": {"image": "x"}}, "secrets": {1: {"file": "./a"}}}
        with pytest.raises(UnsupportedComposeError, match=r"compose document\.secrets: key 1 must be a string"):
            validate(compose)

    def test_top_level_extension_subtree_with_non_string_keys_is_accepted(self) -> None:
        # x- extension fields legitimately hold arbitrary payloads (e.g.
        # YAML-anchor sources); their contents are never walked.
        compose = {"x-anchors": {1: {"a": "b"}}, "services": {"app": {"image": "x"}}}
        assert validate(compose) == []

    def test_service_level_extension_subtree_with_non_string_keys_is_accepted(self) -> None:
        compose = {"services": {"app": {"image": "x", "x-meta": {True: [1, {2: "z"}]}}}}
        assert validate(compose) == []

    def test_ulimits_soft_hard_mapping_still_accepted(self) -> None:
        compose = {"services": {"app": {"image": "x", "ulimits": {"nofile": {"soft": 1024, "hard": 4096}}}}}
        assert validate(compose) == []

    def test_sysctls_mapping_still_accepted(self) -> None:
        compose = {"services": {"app": {"image": "x", "sysctls": {"net.core.somaxconn": 1024}}}}
        assert any("pod-wide" in w for w in validate(compose))

    def test_extra_hosts_map_and_list_forms_still_accepted(self) -> None:
        assert validate({"services": {"app": {"image": "x", "extra_hosts": {"db": "10.0.0.1"}}}})
        assert validate({"services": {"app": {"image": "x", "extra_hosts": ["db:10.0.0.1"]}}})

    def test_environment_null_int_float_bool_values_still_accepted(self) -> None:
        compose = {
            "services": {
                "app": {
                    "image": "x",
                    "environment": {"A": None, "B": 1, "C": 1.5, "D": True},
                }
            }
        }
        assert validate(compose) == []

    def test_normal_service_names_with_dots_dashes_underscores_accepted(self) -> None:
        compose = {"services": {"app.v1": {"image": "x"}, "app-two": {"image": "y"}, "app_3": {"image": "z"}}}
        assert validate(compose) == []
        assert validate({"services": {"app": {"image": "x", "entrypoint": ["run", "me"]}}}) == []


class TestSweepServiceNamedExtensionPrefix:
    """A service literally named `x-web` is a real service, not an extension field.

    `validate()` iterates `services.items()` with no `x-` filter, so a
    service named `x-web` is planned and emitted like any other service.
    The sweep's `x-` skip is syntactic (`key.startswith("x-")`) but its
    rationale is semantic ("a subtree we ignore"); on the `services`
    mapping's own keys those two diverge, since a service name is an
    identifier, not an extension-field marker. This regression let such a
    service's whole body escape the sweep -- see _sweep_service/_sweep_document.
    """

    def test_top_level_body_non_string_key_rejected_cleanly(self) -> None:
        # Before the fix: _validate_service's own sorted(svc) crashed raw
        # (`TypeError: '<' not supported between instances of 'int' and
        # 'str'`) because the sweep skipped this service's body entirely.
        compose = {"services": {"x-web": {"image": "alpine", 3306: "db"}}}
        with pytest.raises(UnsupportedComposeError, match=r"service 'x-web': key 3306 must be a string"):
            validate(compose)

    def test_healthcheck_non_string_key_rejected_cleanly(self) -> None:
        # Same escape, reached via _validate_service_healthcheck's own
        # sorted(healthcheck) instead.
        compose = {"services": {"x-web": {"image": "alpine", "healthcheck": {3: "x"}}}}
        with pytest.raises(UnsupportedComposeError, match=r"service 'x-web'\.healthcheck: key 3 must be a string"):
            validate(compose)

    def test_nested_map_non_string_key_rejected_cleanly(self) -> None:
        # Before the fix: validate() returned [] (no warnings, no raise) and
        # emit_script rendered `-e "True=1" --label "True=v"` -- the Python
        # repr of the YAML-1.1 bareword keys `on`/`yes`, leaked verbatim.
        compose = {
            "services": {
                "x-web": {"image": "alpine", "environment": {True: 1}, "labels": {True: "v"}},
            }
        }
        with pytest.raises(UnsupportedComposeError, match=r"service 'x-web'\.environment: key True must be a string"):
            validate(compose)

    def test_well_formed_is_accepted_as_a_real_service(self) -> None:
        compose = {"services": {"x-web": {"image": "alpine"}}}
        assert validate(compose) == []


class TestSweepSkipsUnreadRegions:
    """`build`'s contents and the ignored top-level `networks`/`volumes` blocks are never read.

    compose2pod accepts these regions but never inspects their contents (see
    architecture/supported-subset.md), so a non-string key inside them can
    never reach the generated script and must not be rejected -- Docker
    itself accepts them. The `environment`/other emitted-map divergence
    (`{3306: db}` rejected) is unaffected: those keys do reach the script.
    """

    def test_build_contents_non_string_key_accepted(self) -> None:
        compose = {"services": {"app": {"build": {"context": ".", "args": {True: 1}}}}}
        assert validate(compose) == []

    def test_top_level_volumes_contents_non_string_key_accepted(self) -> None:
        compose = {
            "services": {"app": {"image": "alpine"}},
            "volumes": {"data": {"driver_opts": {True: 1}}},
        }
        assert any("ignoring top-level 'volumes'" in w for w in validate(compose))

    def test_top_level_networks_contents_non_string_key_accepted(self) -> None:
        compose = {
            "services": {"app": {"image": "alpine"}},
            "networks": {"net1": {"driver_opts": {True: 1}}},
        }
        assert any("ignoring top-level 'networks'" in w for w in validate(compose))

    def test_environment_non_string_key_still_rejected(self) -> None:
        # The `environment: {3306: db}` divergence from Docker stands: that
        # key does reach the emitted script, unlike build/top-level
        # networks/volumes above.
        compose = {"services": {"app": {"image": "x", "environment": {3306: "db"}}}}
        with pytest.raises(UnsupportedComposeError, match=r"environment: key 3306 must be a string"):
            validate(compose)

    def test_top_level_secrets_and_configs_stay_swept(self) -> None:
        # Unlike build/top-level networks/volumes, secrets and configs ARE
        # read (stores.py) and must stay swept, at both the definition and
        # the service-reference sides.
        compose = {
            "services": {"app": {"image": "x", "secrets": [{"source": "s", True: "bogus"}]}},
            "secrets": {"s": {"file": "./a"}},
        }
        with pytest.raises(UnsupportedComposeError):
            validate(compose)
