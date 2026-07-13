import pytest

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.keys import (
    SERVICE_KEYS,
    STRUCTURAL_KEYS,
    Expand,
    _concat_list,
    _merge_map,
    _validate_list,
    _validate_ulimits,
    validate_map,
)
from compose2pod.parsing import SUPPORTED_SERVICE_KEYS


def test_registry_and_structural_keys_are_disjoint() -> None:
    assert not (set(SERVICE_KEYS) & STRUCTURAL_KEYS)


class TestExpand:
    """Expand is the chokepoint every emitted token flows through on its way to to_shell/variable_names."""

    def test_string_value_is_accepted(self) -> None:
        assert Expand(value="ok").value == "ok"

    def test_non_string_value_raises_instead_of_reaching_shell_py_raw(self) -> None:
        # Any per-key validator gap that lets a non-str leak into emit used to
        # crash raw inside shell.py's re.finditer (a TypeError, not a clean
        # UnsupportedComposeError). Guarding construction closes that whole
        # class in one place, regardless of which key leaked it.
        with pytest.raises(UnsupportedComposeError, match="must be a string"):
            Expand(value=123)  # ty: ignore[invalid-argument-type]


def test_supported_service_keys_snapshot() -> None:
    assert {
        "image",
        "build",
        "command",
        "entrypoint",
        "environment",
        "env_file",
        "volumes",
        "tmpfs",
        "healthcheck",
        "depends_on",
        "networks",
        "hostname",
        "container_name",
        "secrets",
        "configs",
        "deploy",
        "user",
        "working_dir",
        "platform",
        "init",
        "read_only",
        "privileged",
        "group_add",
        "cap_add",
        "cap_drop",
        "security_opt",
        "devices",
        "labels",
        "annotations",
        "extra_hosts",
        "pull_policy",
        "ulimits",
        "mem_limit",
        "memswap_limit",
        "mem_reservation",
        "mem_swappiness",
        "cpus",
        "cpu_shares",
        "cpu_quota",
        "cpu_period",
        "cpuset",
        "pids_limit",
        "shm_size",
        "oom_score_adj",
        "oom_kill_disable",
        "dns",
        "dns_search",
        "dns_opt",
        "sysctls",
    } == SUPPORTED_SERVICE_KEYS


@pytest.mark.parametrize("key", sorted(SERVICE_KEYS))
def test_merge_present_iff_list_or_map_shaped(key: str) -> None:
    """A key's merge callable must be set exactly when its shape supports one.

    Ties merge-presence to the validator each entry actually uses, rather than
    a hand-copied key-name list, so a future _list()/_map() key can't silently
    end up with no merge callable.
    """
    spec = SERVICE_KEYS[key]
    is_list_or_map_shaped = spec.validate in (_validate_list, validate_map, _validate_ulimits)
    assert (spec.merge is not None) == is_list_or_map_shaped


class TestMergeCallables:
    """Direct tests of the merge policy functions, independent of any caller.

    extends.py (Task 2) will call these through SERVICE_KEYS[key].merge, but
    that wiring doesn't exist yet — these tests exercise every branch of
    _concat_list/_as_list/_merge_map/pairs_to_mapping on their own so Task 1
    is fully covered without depending on Task 2.
    """

    def test_concat_list_merges_list_forms(self) -> None:
        assert _concat_list("web", "cap_add", ["NET_ADMIN"], ["SYS_TIME"]) == ["NET_ADMIN", "SYS_TIME"]

    def test_concat_list_normalizes_scalar_string_form(self) -> None:
        assert _concat_list("web", "cap_add", "NET_ADMIN", ["SYS_TIME"]) == ["NET_ADMIN", "SYS_TIME"]

    def test_concat_list_refuses_incompatible_form(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="cannot merge 'cap_add' across incompatible forms"):
            _concat_list("web", "cap_add", ["NET_ADMIN"], {"bad": "shape"})

    def test_merge_map_merges_dict_forms(self) -> None:
        assert _merge_map("web", "labels", {"team": "core"}, {"tier": "web"}) == {"team": "core", "tier": "web"}

    def test_merge_map_normalizes_list_form(self) -> None:
        assert _merge_map("web", "labels", ["team=core", "BARE"], ["team=web"]) == {"team": "web", "BARE": None}

    def test_merge_map_refuses_incompatible_form(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="cannot merge 'labels' across incompatible forms"):
            _merge_map("web", "labels", {"team": "core"}, 5)
