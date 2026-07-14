import pytest

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.graph import depends_on, hostnames, startup_order


class TestDependsOn:
    def test_list_form_normalizes_to_service_started(self) -> None:
        assert depends_on({"depends_on": ["db"]}) == {"db": "service_started"}

    def test_map_form_keeps_conditions(self) -> None:
        assert depends_on({"depends_on": {"db": {"condition": "service_healthy"}}}) == {"db": "service_healthy"}

    def test_missing_depends_on_is_empty(self) -> None:
        assert depends_on({"image": "x"}) == {}

    def test_non_list_or_mapping_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="'depends_on' must be a list or mapping"):
            depends_on({"depends_on": "db"})

    def test_mapping_entry_not_a_mapping_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="depends_on entry 'db' must be a mapping"):
            depends_on({"depends_on": {"db": "service_healthy"}})

    def test_list_entry_not_a_string_raises(self) -> None:
        # Same YAML slip as `environment`/`command`: `- db: {condition: ...}`
        # is a hyphen + mapping, not a bare service name. Used to crash raw
        # (TypeError: unhashable type: 'dict') from dict.fromkeys inside
        # validate() itself, instead of a clean UnsupportedComposeError.
        with pytest.raises(UnsupportedComposeError, match=r"depends_on entry .* must be a string"):
            depends_on({"depends_on": [{"db": {"condition": "service_healthy"}}]})

    def test_list_form_string_entries_still_accepted(self) -> None:
        assert depends_on({"depends_on": ["db", "keydb"]}) == {
            "db": "service_started",
            "keydb": "service_started",
        }

    def test_unhashable_condition_raises_cleanly(self) -> None:
        # DEPENDS_ON_CONDITIONS (parsing.py) is a set, and `x in a_set`
        # hashes `x` -- an unhashable condition (dict/list) used to crash
        # raw (TypeError: unhashable type) instead of failing clean.
        with pytest.raises(UnsupportedComposeError, match=r"depends_on entry 'db': condition must be a string"):
            depends_on({"depends_on": {"db": {"condition": {"a": 1}}}})

    def test_list_condition_also_raises_cleanly(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"depends_on entry 'db': condition must be a string"):
            depends_on({"depends_on": {"db": {"condition": ["x"]}}})

    def test_int_condition_raises_cleanly(self) -> None:
        # Hashable but still not a valid condition shape -- must not slip
        # past this check only to fail confusingly deeper in.
        with pytest.raises(UnsupportedComposeError, match=r"depends_on entry 'db': condition must be a string"):
            depends_on({"depends_on": {"db": {"condition": 1}}})


class TestHostnames:
    def test_collects_service_names_and_aliases(self, chats_compose: dict) -> None:
        assert hostnames(chats_compose["services"]) == [
            "application",
            "migrations",
            "db",
            "keydb",
            "keydb-test-server-0",
        ]

    def test_non_dict_network_entry_is_skipped(self) -> None:
        services = {"app": {"image": "x", "networks": {"default": None, "other": {"aliases": ["app-alias"]}}}}
        assert hostnames(services) == ["app", "app-alias"]

    def test_collects_service_hostname(self) -> None:
        services = {"keydb": {"image": "x", "hostname": "keydb-test-server-0"}}
        assert hostnames(services) == ["keydb", "keydb-test-server-0"]

    def test_collects_service_container_name(self) -> None:
        services = {"application": {"image": "x", "container_name": "calutron-ronline"}}
        assert hostnames(services) == ["application", "calutron-ronline"]

    def test_empty_hostname_is_skipped(self) -> None:
        services = {"a": {"image": "x", "hostname": ""}}
        assert hostnames(services) == ["a"]

    def test_non_string_hostname_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="'app': hostname must be a string"):
            hostnames({"app": {"image": "x", "hostname": 5}})

    def test_non_string_container_name_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="'app': container_name must be a string"):
            hostnames({"app": {"image": "x", "container_name": ["c"]}})

    def test_networks_not_list_or_mapping_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="'app': networks must be a list or mapping"):
            hostnames({"app": {"image": "x", "networks": "default"}})

    def test_networks_list_short_form_and_null_value_accepted(self) -> None:
        assert hostnames({"app": {"image": "x", "networks": ["n1"]}}) == ["app"]
        assert hostnames({"app": {"image": "x", "networks": {"default": None}}}) == ["app"]

    def test_string_aliases_rejected_instead_of_iterated_character_wise(self) -> None:
        # Used to be destructured one character at a time, emitting
        # --add-host a:127.0.0.1 --add-host b:127.0.0.1 --add-host c:127.0.0.1
        # for aliases: "abc" -- the same bug already fixed for volumes.
        with pytest.raises(UnsupportedComposeError, match="'app': aliases must be a list of strings"):
            hostnames({"app": {"image": "x", "networks": {"default": {"aliases": "abc"}}}})

    def test_non_string_alias_entry_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="'app': aliases must be a list of strings"):
            hostnames({"app": {"image": "x", "networks": {"default": {"aliases": [5]}}}})

    def test_list_aliases_still_accepted(self) -> None:
        services = {"app": {"image": "x", "networks": {"default": {"aliases": ["app-alias"]}}}}
        assert hostnames(services) == ["app", "app-alias"]

    def test_network_mapping_with_no_aliases_key_contributes_nothing(self) -> None:
        services = {"app": {"image": "x", "networks": {"default": {"driver": "bridge"}}}}
        assert hostnames(services) == ["app"]


class TestStartupOrder:
    def test_chats_order(self, chats_compose: dict) -> None:
        order = startup_order(chats_compose["services"], "application")
        assert order[-1] == "application"
        assert order.index("db") < order.index("migrations") < order.index("application")
        assert order.index("keydb") < order.index("application")
        assert set(order) == {"db", "migrations", "keydb", "application"}

    def test_services_outside_target_closure_are_excluded(self) -> None:
        assert startup_order({"app": {"image": "x"}, "unrelated": {"image": "y"}}, "app") == ["app"]

    def test_unknown_target_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="nope"):
            startup_order({"app": {"image": "x"}}, "nope")

    def test_unknown_dependency_raises(self) -> None:
        services = {"app": {"image": "x", "depends_on": {"ghost": {"condition": "service_started"}}}}
        with pytest.raises(UnsupportedComposeError, match="ghost"):
            startup_order(services, "app")

    def test_dependency_cycle_raises(self) -> None:
        services = {
            "a": {"image": "x", "depends_on": {"b": {"condition": "service_started"}}},
            "b": {"image": "x", "depends_on": {"a": {"condition": "service_started"}}},
        }
        with pytest.raises(UnsupportedComposeError, match="cycle"):
            startup_order(services, "a")

    def test_diamond_dependency_visits_shared_service_once(self) -> None:
        services = {
            "c": {"image": "x"},
            "a": {"image": "x", "depends_on": {"c": {"condition": "service_started"}}},
            "b": {"image": "x", "depends_on": {"c": {"condition": "service_started"}}},
            "target": {
                "image": "x",
                "depends_on": {"a": {"condition": "service_started"}, "b": {"condition": "service_started"}},
            },
        }
        order = startup_order(services, "target")
        assert order.count("c") == 1
        assert order.index("c") < order.index("a")
        assert order.index("c") < order.index("b")
        assert order[-1] == "target"
