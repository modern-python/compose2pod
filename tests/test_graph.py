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
