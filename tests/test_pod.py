import pytest

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.keys import _Expand
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
            _Expand(value="1.1.1.1"),
            "--dns",
            _Expand(value="8.8.8.8"),
        ]

    def test_dns_search_and_opt_flags(self) -> None:
        services = {"a": {"dns_search": "corp.internal", "dns_opt": ["ndots:2"]}}
        assert pod_create_flags(services, ["a"]) == [
            "--dns-search",
            _Expand(value="corp.internal"),
            "--dns-option",
            _Expand(value="ndots:2"),
        ]

    def test_sysctls_mapping_and_list_merge(self) -> None:
        services = {"a": {"sysctls": {"net.core.somaxconn": 1024}}, "b": {"sysctls": ["net.ipv4.tcp_syncookies=1"]}}
        assert pod_create_flags(services, ["a", "b"]) == [
            "--sysctl",
            _Expand(value="net.core.somaxconn=1024"),
            "--sysctl",
            _Expand(value="net.ipv4.tcp_syncookies=1"),
        ]

    def test_sysctls_same_key_same_value_merges(self) -> None:
        services = {"a": {"sysctls": {"net.x": "1"}}, "b": {"sysctls": {"net.x": 1}}}
        assert pod_create_flags(services, ["a", "b"]) == ["--sysctl", _Expand(value="net.x=1")]

    def test_sysctls_same_key_conflict_refused(self) -> None:
        services = {"a": {"sysctls": {"net.x": "1"}}, "b": {"sysctls": {"net.x": "2"}}}
        with pytest.raises(UnsupportedComposeError, match=r"conflicting sysctl 'net.x'"):
            pod_create_flags(services, ["a", "b"])

    def test_non_closure_service_options_ignored(self) -> None:
        services = {"a": {"image": "x"}, "extra": {"dns": ["9.9.9.9"]}}
        assert pod_create_flags(services, ["a"]) == []
