import pytest

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.keys import (
    SERVICE_KEYS,
    STRUCTURAL_KEYS,
    Expand,
    GuardedEnvFile,
    _merge_map,
    _validate_list,
    _validate_string_or_string_list,
    concat_list,
    pairs_to_mapping,
    require_string_keys,
    validate_map,
    validate_ulimits,
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


class TestGuardedEnvFile:
    def test_guarded_env_file_rejects_non_string_fields(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="internal: GuardedEnvFile"):
            GuardedEnvFile(var="v", value=5)  # ty: ignore[invalid-argument-type]


class TestRequireStringKeys:
    """Shared guard against PyYAML's non-string mapping keys (int, or a YAML-1.1 bool)."""

    def test_all_string_keys_pass(self) -> None:
        require_string_keys("compose document", {"a": 1, "b": 2})

    def test_empty_mapping_passes(self) -> None:
        require_string_keys("compose document", {})

    def test_non_string_key_raises_naming_the_key_and_location(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"compose document: key 3 must be a string"):
            require_string_keys("compose document", {3: "x"})


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
    is_list_or_map_shaped = spec.validate in (
        _validate_list,
        validate_map,
        validate_ulimits,
        _validate_string_or_string_list,
    )
    assert (spec.merge is not None) == is_list_or_map_shaped


def test_tmpfs_is_a_registry_key_with_concat_merge() -> None:
    assert "tmpfs" not in STRUCTURAL_KEYS
    spec = SERVICE_KEYS["tmpfs"]
    assert spec.merge is concat_list


class TestElementLevelShapeChecks:
    """_validate_list/validate_map used to only check the outer list/dict shape.

    A non-string list element or non-scalar map value slipped through and got
    str()'d/repr()'d straight into the emitted script -- exit 0, garbage
    output, no error anywhere. The commonest trigger is the classic YAML slip
    of mixing list and map form (`- KEY: value` instead of `- KEY=value`).
    """

    def test_validate_list_rejects_non_string_element(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="'cap_add' entries must be strings"):
            _validate_list("web", "cap_add", [{"NET_ADMIN": True}])

    def test_validate_list_accepts_string_elements(self) -> None:
        _validate_list("web", "cap_add", ["NET_ADMIN", "SYS_TIME"])

    def test_validate_map_rejects_non_string_list_element(self) -> None:
        # The realistic trigger: `environment: [{KEY: value}]` instead of `- KEY=value`.
        with pytest.raises(UnsupportedComposeError, match="'environment' entries must be strings"):
            validate_map("web", "environment", [{"POSTGRES_PASSWORD": "password"}])

    def test_validate_map_rejects_non_scalar_map_value(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="'labels' values must be"):
            validate_map("web", "labels", {"team": {"nested": "dict"}})

    def test_validate_map_rejects_list_valued_map_entry(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="'labels' values must be"):
            validate_map("web", "labels", {"team": ["a", "b"]})

    def test_validate_map_accepts_null_value(self) -> None:
        # environment: {KEY: null} -> host-passthrough; labels: {KEY: null} -> empty label.
        validate_map("web", "environment", {"KEY": None})
        validate_map("web", "labels", {"KEY": None})

    def test_validate_map_accepts_numeric_value(self) -> None:
        validate_map("web", "labels", {"version": 2})

    def test_validate_map_accepts_string_list_elements(self) -> None:
        validate_map("web", "labels", ["team=core", "BARE"])

    def test_validate_map_accepts_boolean_value(self) -> None:
        # docker compose config normalizes `DEBUG: true` to the string "true";
        # a bool map value is valid Compose, not a shape to reject.
        validate_map("web", "environment", {"DEBUG": True})


class TestMergeCallables:
    """Direct tests of the merge policy functions, independent of any caller.

    extends.py (Task 2) will call these through SERVICE_KEYS[key].merge, but
    that wiring doesn't exist yet — these tests exercise every branch of
    concat_list/as_list/_merge_map/pairs_to_mapping on their own so Task 1
    is fully covered without depending on Task 2.
    """

    def test_concat_list_merges_list_forms(self) -> None:
        assert concat_list("web", "cap_add", ["NET_ADMIN"], ["SYS_TIME"]) == ["NET_ADMIN", "SYS_TIME"]

    def test_concat_list_normalizes_scalar_string_form(self) -> None:
        assert concat_list("web", "cap_add", "NET_ADMIN", ["SYS_TIME"]) == ["NET_ADMIN", "SYS_TIME"]

    def test_concat_list_refuses_incompatible_form(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="cannot merge 'cap_add' across incompatible forms"):
            concat_list("web", "cap_add", ["NET_ADMIN"], {"bad": "shape"})

    def test_merge_map_merges_dict_forms(self) -> None:
        assert _merge_map("web", "labels", {"team": "core"}, {"tier": "web"}) == {"team": "core", "tier": "web"}

    def test_merge_map_normalizes_list_form(self) -> None:
        assert _merge_map("web", "labels", ["team=core", "BARE"], ["team=web"]) == {"team": "web", "BARE": None}

    def test_merge_map_refuses_incompatible_form(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="cannot merge 'labels' across incompatible forms"):
            _merge_map("web", "labels", {"team": "core"}, 5)

    def test_pairs_to_mapping_rejects_non_string_list_element(self) -> None:
        # Before this fix, a non-string element was str()'d into a mapping
        # *key* instead of rejected -- laundering a malformed list-of-mapping
        # value into a well-formed-looking mapping that validate() then
        # accepted and emit rendered as a literal Python repr.
        with pytest.raises(UnsupportedComposeError, match="'labels' entries must be strings"):
            pairs_to_mapping("web", "labels", [{"BAD": "x"}])

    def test_pairs_to_mapping_accepts_string_list_elements(self) -> None:
        assert pairs_to_mapping("web", "labels", ["team=core", "BARE"]) == {"team": "core", "BARE": None}


@pytest.mark.parametrize(
    "key",
    [
        "mem_limit",
        "memswap_limit",
        "mem_reservation",
        "shm_size",
        "cpus",
        "cpu_shares",
        "cpu_quota",
        "cpu_period",
        "pids_limit",
    ],
)
@pytest.mark.parametrize("value", ["", "somevalue"])
def test_resource_keys_reject_non_numeric_strings(key: str, value: str) -> None:
    with pytest.raises(UnsupportedComposeError, match=key):
        SERVICE_KEYS[key].validate("app", key, value)


@pytest.mark.parametrize("key", ["mem_limit", "shm_size"])
@pytest.mark.parametrize("value", [512, "512m", "1gb", "${MEM}"])
def test_size_keys_accept(key: str, value: object) -> None:
    SERVICE_KEYS[key].validate("app", key, value)


def test_cpuset_requires_a_string() -> None:
    SERVICE_KEYS["cpuset"].validate("app", "cpuset", "0-3")
    for bad in (0, 2, 1.5):
        with pytest.raises(UnsupportedComposeError, match="cpuset"):
            SERVICE_KEYS["cpuset"].validate("app", "cpuset", bad)


# Measured against `docker compose config` v5.1.2: mem_reservation is grouped with
# mem_swappiness/oom_score_adj in the design as "reject a float", but a native float is
# rejected while a size *string* (even "1.5" or "512m") is still accepted -- the
# int-only rule binds the native-numeric branch only, which is exactly what
# validate_size's allow_fractional=False already does (its string branch is ungated).
@pytest.mark.parametrize("key", ["oom_score_adj", "mem_swappiness", "mem_reservation"])
def test_int_only_keys_reject_float(key: str) -> None:
    with pytest.raises(UnsupportedComposeError, match=key):
        SERVICE_KEYS[key].validate("app", key, 1.5)
    SERVICE_KEYS[key].validate("app", key, 60)


# Measured against `docker compose config` v5.1.2: `oom_score_adj: 1000.0`,
# `mem_swappiness: 60.0`, and `mem_reservation: 60.0` are all ACCEPTED -- a
# whole-valued float casts cleanly to the underlying int64 field. Only a
# fractional float is refused (covered above); the constraint is an integral
# value, not the absence of a float type.
@pytest.mark.parametrize("key", ["oom_score_adj", "mem_swappiness", "mem_reservation"])
def test_int_only_keys_accept_whole_float(key: str) -> None:
    SERVICE_KEYS[key].validate("app", key, 1000.0)


# Measured against `docker compose config` v5.1.2: cpu_shares/cpu_quota/cpu_period/
# pids_limit accept any native number (float included) but, as a *string*, go
# through Go's strconv.ParseInt -- strictly digits, no decimal point, no
# exponent, no digit-grouping underscore. "0.5"/"1e3"/"1_000" are all rejected
# as strings even though the identical native value is fine.
@pytest.mark.parametrize("key", ["cpu_shares", "cpu_quota", "cpu_period", "pids_limit"])
@pytest.mark.parametrize("value", [2, 0.5, "2", "-3"])
def test_count_keys_accept(key: str, value: object) -> None:
    SERVICE_KEYS[key].validate("app", key, value)


@pytest.mark.parametrize("key", ["cpu_shares", "cpu_quota", "cpu_period", "pids_limit"])
@pytest.mark.parametrize("value", ["0.5", "1e3", "1_000"])
def test_count_keys_reject_non_strict_integer_strings(key: str, value: str) -> None:
    with pytest.raises(UnsupportedComposeError, match=key):
        SERVICE_KEYS[key].validate("app", key, value)


def test_ulimits_scalar_bound_rejects_non_integer() -> None:
    with pytest.raises(UnsupportedComposeError, match="nofile"):
        SERVICE_KEYS["ulimits"].validate("app", "ulimits", {"nofile": "abc"})
    with pytest.raises(UnsupportedComposeError, match="nofile"):
        SERVICE_KEYS["ulimits"].validate("app", "ulimits", {"nofile": 1.5})
    SERVICE_KEYS["ulimits"].validate("app", "ulimits", {"nofile": 65535})
    SERVICE_KEYS["ulimits"].validate("app", "ulimits", {"nofile": "65535"})
