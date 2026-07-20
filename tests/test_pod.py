import pytest

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.keys import Expand
from compose2pod.parsing import validate
from compose2pod.pod import hosts_file_tokens, pod_create_flags, uses_pod_options, validate_pod_options


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

    # Task 10, Class 2: a non-string map value passed `validate_map`'s shared
    # scalar-or-null shape check (correct for labels/annotations) but Docker
    # itself refuses it for extra_hosts -- measured against `docker compose
    # config` v5.1.2: 'extra_hosts: {h: 3}' and 'extra_hosts: {h: true}' both
    # raise 'must be a string'.

    def test_extra_hosts_map_int_value_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="extra_hosts 'db' must be a string"):
            validate_pod_options("app", {"image": "x", "extra_hosts": {"db": 3}})

    def test_extra_hosts_map_bool_value_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="extra_hosts 'db' must be a string"):
            validate_pod_options("app", {"image": "x", "extra_hosts": {"db": True}})

    def test_extra_hosts_map_null_value_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="extra_hosts 'db' must be a string"):
            validate_pod_options("app", {"image": "x", "extra_hosts": {"db": None}})

    def test_extra_hosts_map_string_value_accepted(self) -> None:
        validate_pod_options("app", {"image": "x", "extra_hosts": {"db": "1.2.3.4"}})

    def test_extra_hosts_map_variable_reference_accepted(self) -> None:
        validate_pod_options("app", {"image": "x", "extra_hosts": {"db": "${VAR}"}})


class TestUsesPodOptions:
    def test_true_when_declared(self) -> None:
        assert uses_pod_options({"a": {"image": "x"}, "b": {"image": "y", "dns": ["1.1.1.1"]}}) is True

    def test_false_when_absent(self) -> None:
        assert uses_pod_options({"a": {"image": "x"}}) is False


class TestPodCreateFlags:
    def test_no_options_no_flags(self) -> None:
        assert pod_create_flags({"a": {"image": "x"}}, ["a"]) == []

    def test_dns_union_across_closure_dedup_first_seen(self) -> None:
        services = {"a": {"dns": ["1.1.1.1", "8.8.8.8"]}, "b": {"dns": "8.8.8.8"}}
        assert pod_create_flags(services, ["a", "b"]) == [
            "--dns",
            Expand(value="1.1.1.1"),
            "--dns",
            Expand(value="8.8.8.8"),
        ]

    def test_dns_search_and_opt_flags(self) -> None:
        services = {"a": {"dns_search": "corp.internal", "dns_opt": ["ndots:2"]}}
        assert pod_create_flags(services, ["a"]) == [
            "--dns-search",
            Expand(value="corp.internal"),
            "--dns-option",
            Expand(value="ndots:2"),
        ]

    def test_sysctls_mapping_and_list_merge(self) -> None:
        services = {"a": {"sysctls": {"net.core.somaxconn": 1024}}, "b": {"sysctls": ["net.ipv4.tcp_syncookies=1"]}}
        assert pod_create_flags(services, ["a", "b"]) == [
            "--sysctl",
            Expand(value="net.core.somaxconn=1024"),
            "--sysctl",
            Expand(value="net.ipv4.tcp_syncookies=1"),
        ]

    def test_sysctls_same_key_same_value_merges(self) -> None:
        services = {"a": {"sysctls": {"net.x": "1"}}, "b": {"sysctls": {"net.x": 1}}}
        assert pod_create_flags(services, ["a", "b"]) == ["--sysctl", Expand(value="net.x=1")]

    def test_sysctls_same_key_conflict_refused(self) -> None:
        services = {"a": {"sysctls": {"net.x": "1"}}, "b": {"sysctls": {"net.x": "2"}}}
        with pytest.raises(UnsupportedComposeError, match=r"conflicting sysctl 'net.x'"):
            pod_create_flags(services, ["a", "b"])

    def test_non_closure_service_options_ignored(self) -> None:
        services = {"a": {"image": "x"}, "extra": {"dns": ["9.9.9.9"]}}
        assert pod_create_flags(services, ["a"]) == []


class TestHostsFileTokens:
    def test_localhost_lines_always_first(self) -> None:
        assert hosts_file_tokens({"a": {"image": "x"}}, ["a"], []) == [
            "127.0.0.1 localhost",
            "::1 localhost",
        ]

    def test_alias_hosts_render_as_literal_lines(self) -> None:
        services = {"a": {"image": "x"}}
        assert hosts_file_tokens(services, ["a"], ["web", "db"]) == [
            "127.0.0.1 localhost",
            "::1 localhost",
            "127.0.0.1 web",
            "127.0.0.1 db",
        ]

    def test_extra_hosts_list_form_renders_as_expand(self) -> None:
        services = {"a": {"extra_hosts": ["db:10.0.0.5"]}}
        assert hosts_file_tokens(services, ["a"], []) == [
            "127.0.0.1 localhost",
            "::1 localhost",
            Expand(value="10.0.0.5 db"),
        ]

    def test_extra_hosts_mapping_form_renders_as_expand(self) -> None:
        services = {"a": {"extra_hosts": {"db": "10.0.0.5"}}}
        assert hosts_file_tokens(services, ["a"], []) == [
            "127.0.0.1 localhost",
            "::1 localhost",
            Expand(value="10.0.0.5 db"),
        ]

    def test_extra_hosts_variable_address_stays_live(self) -> None:
        services = {"a": {"extra_hosts": {"db": "${DB_IP}"}}}
        assert hosts_file_tokens(services, ["a"], []) == [
            "127.0.0.1 localhost",
            "::1 localhost",
            Expand(value="${DB_IP} db"),
        ]

    def test_conflicting_address_for_same_host_refused(self) -> None:
        services = {"a": {"extra_hosts": ["db:10.0.0.5"]}, "b": {"extra_hosts": ["db:10.0.0.9"]}}
        with pytest.raises(UnsupportedComposeError, match=r"conflicting host 'db'"):
            hosts_file_tokens(services, ["a", "b"], [])

    def test_alias_vs_extra_hosts_address_conflict_refused(self) -> None:
        # An alias fixes db at 127.0.0.1; extra_hosts wanting another address is refused.
        services = {"a": {"extra_hosts": ["db:10.0.0.5"]}}
        with pytest.raises(UnsupportedComposeError, match=r"conflicting host 'db'"):
            hosts_file_tokens(services, ["a"], ["db"])


class TestExtraHostsSeparators:
    """Compose documents 'host=ip'; the legacy 'host:ip' also works. Both must."""

    def _lines(self, entries: object) -> list[str]:
        services = {"a": {"image": "x", "extra_hosts": entries}}
        # pod.py never emits a GuardedEnvFile (only emit.py's env_file path does), so this
        # is always str at runtime; the Token union just doesn't narrow that far statically.
        tokens = hosts_file_tokens(services, ["a"], [])[2:]  # drop the two fixed localhost lines
        return [t.value if isinstance(t, Expand) else t for t in tokens]  # ty: ignore[invalid-return-type]

    def test_equals_separator_is_split_correctly(self) -> None:
        # Used to emit the whole string as the hostname: '1.2.3.4: somehost'
        assert self._lines(["somehost=162.242.195.82"]) == ["162.242.195.82 somehost"]

    def test_colon_separator_still_works(self) -> None:
        assert self._lines(["somehost:162.242.195.82"]) == ["162.242.195.82 somehost"]

    def test_equals_separator_keeps_ipv6(self) -> None:
        # '=' wins over ':' precisely because an IPv6 address is full of colons.
        assert self._lines(["myhostv6=::1"]) == ["::1 myhostv6"]

    def test_colon_separator_keeps_ipv6(self) -> None:
        assert self._lines(["myhost:::1"]) == ["::1 myhost"]

    def test_mapping_form_is_unchanged(self) -> None:
        assert self._lines({"somehost": "162.242.195.82"}) == ["162.242.195.82 somehost"]

    def test_entry_with_no_separator_is_refused(self) -> None:
        # Used to be accepted and emit the malformed '--add-host "no-separator:"'
        with pytest.raises(UnsupportedComposeError, match="extra_hosts entries must be 'host=ip' or 'host:ip'"):
            validate_pod_options("a", {"image": "x", "extra_hosts": ["no-separator"]})

    def test_mapping_value_containing_an_equals_is_not_re_split(self) -> None:
        # The mapping form arrives already divided. Joining it into 'address host'
        # and re-splitting would divide at the '=' instead.
        assert self._lines({"somehost": "weird=address"}) == ["weird=address somehost"]


def test_dns_opt_must_be_a_list() -> None:
    # Docker requires a list for dns_opt specifically; dns and dns_search still take a string.
    with pytest.raises(UnsupportedComposeError, match="dns_opt"):
        validate({"services": {"app": {"image": "nginx", "dns_opt": "ndots:2"}}})
    validate({"services": {"app": {"image": "nginx", "dns_opt": ["ndots:2"]}}})
    validate({"services": {"app": {"image": "nginx", "dns": "8.8.8.8"}}})
    validate({"services": {"app": {"image": "nginx", "dns_search": "example.com"}}})
