import pytest

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.keys import Expand
from compose2pod.pod import pod_create_flags, uses_pod_options, validate_pod_options


class TestValidatePodOptions:
    def test_no_pod_options_is_noop(self) -> None:
        validate_pod_options("app", {"image": "x"})

    def test_dns_string_and_list_accepted(self) -> None:
        validate_pod_options("app", {"image": "x", "dns": "1.1.1.1"})
        validate_pod_options("app", {"image": "x", "dns": ["1.1.1.1", "8.8.8.8"]})

    def test_dns_search_and_opt_accepted(self) -> None:
        validate_pod_options("app", {"image": "x", "dns_search": ["corp.internal"], "dns_opt": ["ndots:2"]})

    def test_dns_non_string_list_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"'dns' must be a string or list of strings"):
            validate_pod_options("app", {"image": "x", "dns": [1]})

    def test_dns_non_string_scalar_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"'dns' must be a string or list of strings"):
            validate_pod_options("app", {"image": "x", "dns": 5})

    def test_sysctls_mapping_accepted(self) -> None:
        validate_pod_options("app", {"image": "x", "sysctls": {"net.core.somaxconn": 1024}})

    def test_sysctls_list_accepted(self) -> None:
        validate_pod_options("app", {"image": "x", "sysctls": ["net.core.somaxconn=1024"]})

    def test_sysctls_bool_value_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="value must be a string or number"):
            validate_pod_options("app", {"image": "x", "sysctls": {"net.x": True}})

    def test_sysctls_list_entry_without_equals_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"must be 'key=value' strings"):
            validate_pod_options("app", {"image": "x", "sysctls": ["net.x"]})

    def test_sysctls_wrong_type_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="'sysctls' must be a mapping or a list"):
            validate_pod_options("app", {"image": "x", "sysctls": "net.x=1"})

    def test_extra_hosts_list_of_mapping_rejected(self) -> None:
        # Used to be silently accepted and str()'d straight into --add-host.
        with pytest.raises(UnsupportedComposeError, match="'extra_hosts' entries must be strings"):
            validate_pod_options("app", {"image": "x", "extra_hosts": [{"db": "10.0.0.5"}]})

    def test_extra_hosts_map_non_scalar_value_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="'extra_hosts' values must be"):
            validate_pod_options("app", {"image": "x", "extra_hosts": {"db": ["10.0.0.5"]}})


class TestUsesPodOptions:
    def test_true_when_declared(self) -> None:
        assert uses_pod_options({"a": {"image": "x"}, "b": {"image": "y", "dns": ["1.1.1.1"]}}) is True

    def test_false_when_absent(self) -> None:
        assert uses_pod_options({"a": {"image": "x"}}) is False


class TestPodCreateFlags:
    def test_no_options_no_flags(self) -> None:
        assert pod_create_flags({"a": {"image": "x"}}, ["a"], []) == []

    def test_dns_union_across_closure_dedup_first_seen(self) -> None:
        services = {"a": {"dns": ["1.1.1.1", "8.8.8.8"]}, "b": {"dns": "8.8.8.8"}}
        assert pod_create_flags(services, ["a", "b"], []) == [
            "--dns",
            Expand(value="1.1.1.1"),
            "--dns",
            Expand(value="8.8.8.8"),
        ]

    def test_dns_search_and_opt_flags(self) -> None:
        services = {"a": {"dns_search": "corp.internal", "dns_opt": ["ndots:2"]}}
        assert pod_create_flags(services, ["a"], []) == [
            "--dns-search",
            Expand(value="corp.internal"),
            "--dns-option",
            Expand(value="ndots:2"),
        ]

    def test_sysctls_mapping_and_list_merge(self) -> None:
        services = {"a": {"sysctls": {"net.core.somaxconn": 1024}}, "b": {"sysctls": ["net.ipv4.tcp_syncookies=1"]}}
        assert pod_create_flags(services, ["a", "b"], []) == [
            "--sysctl",
            Expand(value="net.core.somaxconn=1024"),
            "--sysctl",
            Expand(value="net.ipv4.tcp_syncookies=1"),
        ]

    def test_sysctls_same_key_same_value_merges(self) -> None:
        services = {"a": {"sysctls": {"net.x": "1"}}, "b": {"sysctls": {"net.x": 1}}}
        assert pod_create_flags(services, ["a", "b"], []) == ["--sysctl", Expand(value="net.x=1")]

    def test_sysctls_same_key_conflict_refused(self) -> None:
        services = {"a": {"sysctls": {"net.x": "1"}}, "b": {"sysctls": {"net.x": "2"}}}
        with pytest.raises(UnsupportedComposeError, match=r"conflicting sysctl 'net.x'"):
            pod_create_flags(services, ["a", "b"], [])

    def test_non_closure_service_options_ignored(self) -> None:
        services = {"a": {"image": "x"}, "extra": {"dns": ["9.9.9.9"]}}
        assert pod_create_flags(services, ["a"], []) == []


class TestAddHostFlags:
    def test_alias_only_hosts_produce_add_host(self) -> None:
        # Alias entries render as plain tokens (unquoted), matching pre-move `run_flags` behavior.
        services = {"a": {"image": "x"}}
        assert pod_create_flags(services, ["a"], ["web", "db"]) == [
            "--add-host",
            "web:127.0.0.1",
            "--add-host",
            "db:127.0.0.1",
        ]

    def test_extra_hosts_list_form(self) -> None:
        services = {"a": {"extra_hosts": ["db:10.0.0.5"]}}
        assert pod_create_flags(services, ["a"], []) == ["--add-host", Expand(value="db:10.0.0.5")]

    def test_extra_hosts_mapping_form(self) -> None:
        services = {"a": {"extra_hosts": {"db": "10.0.0.5"}}}
        assert pod_create_flags(services, ["a"], []) == ["--add-host", Expand(value="db:10.0.0.5")]

    def test_extra_hosts_boolean_value_normalizes_like_docker(self) -> None:
        # Same boolean-map-value normalization as environment/labels/annotations,
        # applied uniformly since extra_hosts is also a validate_map-shaped key.
        services = {"a": {"extra_hosts": {"db": True}}}
        assert pod_create_flags(services, ["a"], []) == ["--add-host", Expand(value="db:true")]

    def test_extra_hosts_ipv6_value_keeps_colons(self) -> None:
        services = {"a": {"extra_hosts": {"myhost": "2001:db8::1"}}}
        assert pod_create_flags(services, ["a"], []) == [
            "--add-host",
            Expand(value="myhost:2001:db8::1"),
        ]

    def test_alias_and_extra_hosts_on_different_hosts_both_appear(self) -> None:
        services = {"a": {"extra_hosts": {"db": "10.0.0.5"}}}
        assert pod_create_flags(services, ["a"], ["web"]) == [
            "--add-host",
            "web:127.0.0.1",
            "--add-host",
            Expand(value="db:10.0.0.5"),
        ]

    def test_same_host_same_address_across_sources_dedups_silently(self) -> None:
        services = {"a": {"extra_hosts": {"web": "127.0.0.1"}}}
        assert pod_create_flags(services, ["a"], ["web"]) == ["--add-host", Expand(value="web:127.0.0.1")]

    def test_conflicting_extra_hosts_across_services_refused(self) -> None:
        services = {"a": {"extra_hosts": {"db": "10.0.0.5"}}, "b": {"extra_hosts": {"db": "10.0.0.6"}}}
        with pytest.raises(UnsupportedComposeError, match="conflicting host"):
            pod_create_flags(services, ["a", "b"], [])

    def test_conflicting_alias_and_extra_hosts_refused(self) -> None:
        services = {"a": {"extra_hosts": {"web": "10.0.0.5"}}}
        with pytest.raises(UnsupportedComposeError, match="conflicting host"):
            pod_create_flags(services, ["a"], ["web"])

    def test_non_closure_service_extra_hosts_ignored(self) -> None:
        services = {"a": {"image": "x"}, "extra": {"extra_hosts": {"db": "10.0.0.5"}}}
        assert pod_create_flags(services, ["a"], []) == []


class TestExtraHostsSeparators:
    """Compose documents 'host=ip'; the legacy 'host:ip' also works. Both must."""

    def _flags(self, entries: object) -> list[str]:
        services = {"a": {"image": "x", "extra_hosts": entries}}
        return [t.value if isinstance(t, Expand) else t for t in pod_create_flags(services, ["a"], [])]

    def test_equals_separator_is_split_correctly(self) -> None:
        # Used to emit the whole string as the hostname: 'somehost=1.2.3.4:'
        assert self._flags(["somehost=162.242.195.82"]) == ["--add-host", "somehost:162.242.195.82"]

    def test_colon_separator_still_works(self) -> None:
        assert self._flags(["somehost:162.242.195.82"]) == ["--add-host", "somehost:162.242.195.82"]

    def test_equals_separator_keeps_ipv6(self) -> None:
        # '=' wins over ':' precisely because an IPv6 address is full of colons.
        assert self._flags(["myhostv6=::1"]) == ["--add-host", "myhostv6:::1"]

    def test_colon_separator_keeps_ipv6(self) -> None:
        assert self._flags(["myhost:::1"]) == ["--add-host", "myhost:::1"]

    def test_mapping_form_is_unchanged(self) -> None:
        assert self._flags({"somehost": "162.242.195.82"}) == ["--add-host", "somehost:162.242.195.82"]

    def test_entry_with_no_separator_is_refused(self) -> None:
        # Used to be accepted and emit the malformed '--add-host "no-separator:"'
        with pytest.raises(UnsupportedComposeError, match="extra_hosts entries must be 'host=ip' or 'host:ip'"):
            validate_pod_options("a", {"image": "x", "extra_hosts": ["no-separator"]})
