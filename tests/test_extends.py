from typing import Any, ClassVar

import pytest

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.extends import _STRUCTURAL_CONCAT_KEYS, _STRUCTURAL_MERGE_KEYS, resolve_extends
from compose2pod.keys import SERVICE_KEYS
from compose2pod.parsing import validate


def _services(doc: dict[str, Any]) -> dict[str, Any]:
    return resolve_extends(doc)["services"]


class TestResolveExtends:
    def test_no_extends_document_is_unchanged(self) -> None:
        doc = {"services": {"app": {"image": "x", "environment": {"A": "1"}}}}
        assert resolve_extends(doc) == doc

    def test_non_dict_document_passes_through(self) -> None:
        assert resolve_extends([1, 2]) == [1, 2]

    def test_non_dict_services_passes_through(self) -> None:
        doc = {"services": "nope"}
        assert resolve_extends(doc) == doc

    def test_single_level_scalar_override_and_env_merge(self) -> None:
        doc = {
            "services": {
                "base": {"image": "app", "environment": {"LOG": "info", "PORT": "80"}},
                "web": {"extends": {"service": "base"}, "environment": {"PORT": "8080"}},
            }
        }
        web = _services(doc)["web"]
        assert "extends" not in web
        assert web["image"] == "app"
        assert web["environment"] == {"LOG": "info", "PORT": "8080"}

    def test_transitive_resolution(self) -> None:
        doc = {
            "services": {
                "a": {"image": "base", "environment": {"A": "1"}},
                "b": {"extends": {"service": "a"}, "environment": {"B": "2"}},
                "c": {"extends": {"service": "b"}, "environment": {"C": "3"}},
            }
        }
        c = _services(doc)["c"]
        assert c["image"] == "base"
        assert c["environment"] == {"A": "1", "B": "2", "C": "3"}

    def test_command_overrides_not_concatenates(self) -> None:
        doc = {
            "services": {
                "base": {"image": "x", "command": ["a", "b"]},
                "web": {"extends": {"service": "base"}, "command": ["c"]},
            }
        }
        assert _services(doc)["web"]["command"] == ["c"]

    def test_environment_list_forms_normalized_and_merged(self) -> None:
        doc = {
            "services": {
                "base": {"image": "x", "environment": ["A=1", "SHARED=base", "BARE"]},
                "web": {"extends": {"service": "base"}, "environment": ["SHARED=web"]},
            }
        }
        assert _services(doc)["web"]["environment"] == {"A": "1", "SHARED": "web", "BARE": None}

    def test_labels_mapping_merge(self) -> None:
        doc = {
            "services": {
                "base": {"image": "x", "labels": {"team": "core", "tier": "base"}},
                "web": {"extends": {"service": "base"}, "labels": {"tier": "web"}},
            }
        }
        assert _services(doc)["web"]["labels"] == {"team": "core", "tier": "web"}

    def test_labels_list_form_normalized_and_merged(self) -> None:
        doc = {
            "services": {
                "base": {"image": "x", "labels": ["team=core", "BARE"]},
                "web": {"extends": {"service": "base"}, "labels": ["team=web"]},
            }
        }
        assert _services(doc)["web"]["labels"] == {"team": "web", "BARE": None}

    def test_incompatible_labels_form_is_refused(self) -> None:
        doc = {
            "services": {
                "base": {"image": "x", "labels": {"team": "core"}},
                "web": {"extends": {"service": "base"}, "labels": 5},
            }
        }
        with pytest.raises(UnsupportedComposeError, match="'labels' must be a list or mapping"):
            resolve_extends(doc)

    def test_cap_add_scalar_form_is_refused_not_normalized(self) -> None:
        # Compose has no scalar form for cap_add and the gate refuses one, so a
        # merge must not quietly normalize it into a list -- that would let
        # `extends` accept a document that is invalid on its own.
        doc = {
            "services": {
                "base": {"image": "x", "cap_add": "NET_ADMIN"},
                "web": {"extends": {"service": "base"}, "cap_add": ["SYS_TIME"]},
            }
        }
        with pytest.raises(UnsupportedComposeError, match="'cap_add' must be a list"):
            resolve_extends(doc)

    def test_depends_on_list_and_map_forms_merge(self) -> None:
        doc = {
            "services": {
                "base": {"image": "x", "depends_on": {"db": {"condition": "service_started"}}},
                "web": {"extends": {"service": "base"}, "depends_on": ["cache"]},
            }
        }
        merged = _services(doc)["web"]["depends_on"]
        assert merged == {"db": {"condition": "service_started"}, "cache": {}}

    def test_cap_add_sequence_concatenates(self) -> None:
        doc = {
            "services": {
                "base": {"image": "x", "cap_add": ["NET_ADMIN"]},
                "web": {"extends": {"service": "base"}, "cap_add": ["SYS_TIME"]},
            }
        }
        assert _services(doc)["web"]["cap_add"] == ["NET_ADMIN", "SYS_TIME"]

    def test_tmpfs_scalar_normalized_before_concat(self) -> None:
        # S108 flags "/tmp" as an insecure hardcoded temp path; this is a
        # compose value under test, not a real temp-file usage.
        doc = {
            "services": {
                "base": {"image": "x", "tmpfs": "/run"},
                "web": {"extends": {"service": "base"}, "tmpfs": ["/tmp"]},  # noqa: S108
            }
        }
        assert _services(doc)["web"]["tmpfs"] == ["/run", "/tmp"]  # noqa: S108

    def test_local_only_key_is_added(self) -> None:
        doc = {
            "services": {
                "base": {"image": "x"},
                "web": {"extends": {"service": "base"}, "user": "1000"},
            }
        }
        assert _services(doc)["web"] == {"image": "x", "user": "1000"}

    def test_cycle_is_refused(self) -> None:
        doc = {
            "services": {
                "a": {"extends": {"service": "b"}, "image": "x"},
                "b": {"extends": {"service": "a"}, "image": "y"},
            }
        }
        with pytest.raises(UnsupportedComposeError, match="extends cycle"):
            resolve_extends(doc)

    def test_cross_file_extends_is_refused(self) -> None:
        doc = {"services": {"web": {"extends": {"service": "base", "file": "other.yml"}}}}
        with pytest.raises(UnsupportedComposeError, match=r"'file:' \(cross-file\) is not supported"):
            resolve_extends(doc)

    def test_bare_string_extends_is_refused(self) -> None:
        doc = {"services": {"web": {"extends": "base", "image": "x"}}}
        with pytest.raises(UnsupportedComposeError, match="must be a mapping"):
            resolve_extends(doc)

    def test_unknown_extends_key_is_refused(self) -> None:
        doc = {"services": {"web": {"extends": {"service": "base", "bogus": 1}}, "base": {"image": "x"}}}
        with pytest.raises(UnsupportedComposeError, match="unsupported 'extends' keys"):
            resolve_extends(doc)

    def test_unknown_extends_keys_with_mixed_types_do_not_crash_raw(self) -> None:
        # `sorted(unknown)` used to crash raw -- TypeError: '<' not supported
        # between instances of 'str' and 'int' -- once `unknown` held keys of
        # more than one incomparable type (a non-string key mixed with a
        # string one is exactly what a hostile/malformed 'extends' mapping
        # can produce, since 'extends' is read ahead of validate()'s gate).
        doc = {"services": {"web": {"extends": {"service": "base", 1: "x", "y": "z"}}, "base": {"image": "x"}}}
        with pytest.raises(UnsupportedComposeError, match="unsupported 'extends' keys"):
            resolve_extends(doc)

    def test_non_string_service_is_refused(self) -> None:
        doc = {"services": {"web": {"extends": {"service": 5}}}}
        with pytest.raises(UnsupportedComposeError, match="extends 'service' must be a string"):
            resolve_extends(doc)

    def test_unknown_referenced_service_is_refused(self) -> None:
        doc = {"services": {"web": {"extends": {"service": "ghost"}, "image": "x"}}}
        with pytest.raises(UnsupportedComposeError, match="extends unknown service 'ghost'"):
            resolve_extends(doc)

    def test_incompatible_mapping_form_is_refused(self) -> None:
        doc = {
            "services": {
                "base": {"image": "x", "environment": {"A": "1"}},
                "web": {"extends": {"service": "base"}, "environment": "NOT_A_MAP"},
            }
        }
        with pytest.raises(UnsupportedComposeError, match="cannot merge 'environment' across incompatible forms"):
            resolve_extends(doc)

    def test_diamond_inheritance_resolves_base_once(self) -> None:
        doc = {
            "services": {
                "base": {"image": "app", "environment": {"COMMON": "1"}},
                "web": {"extends": {"service": "base"}, "environment": {"ROLE": "web"}},
                "worker": {"extends": {"service": "base"}, "environment": {"ROLE": "worker"}},
            }
        }
        services = resolve_extends(doc)["services"]
        assert services["web"]["environment"] == {"COMMON": "1", "ROLE": "web"}
        assert services["worker"]["environment"] == {"COMMON": "1", "ROLE": "worker"}

    def test_incompatible_sequence_form_is_refused(self) -> None:
        doc = {
            "services": {
                "base": {"image": "x", "cap_add": ["NET_ADMIN"]},
                "web": {"extends": {"service": "base"}, "cap_add": {"bad": "shape"}},
            }
        }
        with pytest.raises(UnsupportedComposeError, match="'cap_add' must be a list"):
            resolve_extends(doc)

    def test_extends_non_dict_base_defers_to_validate(self) -> None:
        # A non-dict base cannot be merged; resolve must not crash — it leaves
        # the invalid base for validate() to reject.
        doc = {"services": {"base": None, "web": {"extends": {"service": "base"}, "image": "x"}}}
        result = resolve_extends(doc)
        assert "extends" not in result["services"]["web"]
        assert result["services"]["base"] is None

    def test_depends_on_list_with_mapping_entry_raises_instead_of_crashing_raw(self) -> None:
        # Without extends, graph.depends_on's own element check catches this
        # cleanly. extends.py has its own merge-time normalization
        # (_as_mapping) that runs ahead of that check and, before this fix,
        # crashed raw building `{dep: {} for dep in value}` -- a dict list
        # element isn't hashable.
        doc = {
            "services": {
                "db": {"image": "pg"},
                "base": {"image": "a", "depends_on": [{"db": {"condition": "service_healthy"}}]},
                "web": {"extends": {"service": "base"}, "depends_on": ["db"]},
            }
        }
        with pytest.raises(UnsupportedComposeError, match=r"depends_on entry .* must be a string"):
            resolve_extends(doc)

    def test_labels_list_with_mapping_entry_raises_instead_of_laundering(self) -> None:
        # Before this fix, a non-string labels list element on the extending
        # (child) side survived the merge: pairs_to_mapping str()'d the dict
        # element into a mapping *key* instead of rejecting it, so the merged
        # service came out well-formed enough for validate() to accept and
        # emit to render the literal --label "{'BAD': 'x'}".
        doc = {
            "services": {
                "base": {"image": "a", "labels": {"OK": "1"}},
                "web": {"extends": {"service": "base"}, "labels": [{"BAD": "x"}]},
            }
        }
        with pytest.raises(UnsupportedComposeError, match="'labels' entries must be strings"):
            resolve_extends(doc)

    def test_incompatible_structural_concat_form_is_refused(self) -> None:
        doc = {
            "services": {
                "base": {"image": "x", "env_file": ["base.env"]},
                "web": {"extends": {"service": "base"}, "env_file": {"bad": "shape"}},
            }
        }
        with pytest.raises(UnsupportedComposeError, match="cannot merge 'env_file' across incompatible forms"):
            resolve_extends(doc)


class TestNonStringKeysAheadOfTheGate:
    """resolve_extends() runs before validate()'s document-wide string-key sweep.

    validate() closes non-string mapping keys as a class (see
    parsing._require_string_keys_deep), but it never gets a turn if
    resolve_extends() crashes raw on the same input first. None of these
    hostile shapes involve a value resolve_extends interprets as anything
    other than an opaque dict key or dict value, so each should pass through
    unchanged for validate() to reject afterward -- not crash here.
    """

    def test_non_string_service_name_passes_through(self) -> None:
        doc = {"services": {True: {"image": "j"}, "app": {"image": "i"}}}
        assert resolve_extends(doc) == doc

    def test_non_string_key_in_non_extending_service_body_passes_through(self) -> None:
        doc = {"services": {"app": {"image": "i", 3: "x"}}}
        assert resolve_extends(doc) == doc

    def test_non_string_key_in_extends_base_body_passes_through(self) -> None:
        doc = {
            "services": {
                "base": {"image": "j", True: "y"},
                "app": {"extends": {"service": "base"}, "image": "i"},
            }
        }
        merged = resolve_extends(doc)
        assert merged["services"]["app"][True] == "y"

    def test_non_string_key_merged_into_environment_across_extends_passes_through(self) -> None:
        doc = {
            "services": {
                "base": {"image": "j", "environment": {"A": "1"}},
                "app": {"extends": {"service": "base"}, "environment": {3: "x"}},
            }
        }
        merged = resolve_extends(doc)
        assert merged["services"]["app"]["environment"] == {"A": "1", 3: "x"}


class TestMergeErrorMessageOrder:
    """The service name and the key must not be transposed in a merge error."""

    def test_structural_concat_incompatible_form_names_service_then_key(self) -> None:
        # 'volumes' is a structural concat key: it goes through extends' own
        # list-normalizing path, not a KeySpec.merge. A swapped (name, key) would
        # render "service 'volumes': cannot merge 'app'" and go unnoticed.
        doc = {
            "services": {
                "base": {"image": "x", "volumes": ["./a:/a"]},
                "app": {"extends": {"service": "base"}, "volumes": {"not": "a list"}},
            }
        }
        with pytest.raises(UnsupportedComposeError) as excinfo:
            resolve_extends(doc)
        assert str(excinfo.value) == "service 'app': cannot merge 'volumes' across incompatible forms"

    def test_structural_concat_merges_scalar_and_list_forms(self) -> None:
        # The normalize-then-concat path itself: a scalar string on one side.
        doc = {
            "services": {
                "base": {"image": "x", "env_file": "base.env"},
                "app": {"extends": {"service": "base"}, "env_file": ["local.env"]},
            }
        }
        merged = resolve_extends(doc)
        assert merged["services"]["app"]["env_file"] == ["base.env", "local.env"]


class TestMergeNeverWidensTheGate:
    """`extends` must not turn a form the gate refuses into one it accepts.

    `resolve_extends` runs ahead of `validate()`, so a normalizing merge can
    launder an invalid shape into a valid one. Each case below uses the shape
    that actually laundered -- a bare scalar for the list-only keys, a list for
    the mapping-only keys -- not merely any invalid value.
    """

    # key -> (valid base value, a form that key does NOT have)
    HOSTILE: ClassVar[dict[str, tuple[Any, Any]]] = {
        # Mapping-only: Compose gives these no list form.
        "ulimits": ({"nofile": 1}, ["nofile=2"]),
        "healthcheck": ({"test": ["CMD", "x"]}, ["x"]),
        # List-only: Compose gives these no scalar form. `as_list` used to
        # normalize a bare string into [string], laundering it past the gate.
        "cap_add": (["NET_ADMIN"], "SYS_TIME"),
        "cap_drop": (["ALL"], "NET_RAW"),
        "security_opt": (["label=disable"], "seccomp=unconfined"),
        "devices": (["/dev/fuse"], "/dev/null"),
        "group_add": (["1000"], "2000"),
        "volumes": (["./a:/a"], "./b:/b"),
        "secrets": (["s"], "s"),
        "configs": (["c"], "c"),
        # Map-or-list keys: a scalar is neither.
        "labels": ({"a": "1"}, 5),
        "annotations": ({"a": "1"}, 5),
        "environment": ({"A": "1"}, 5),
        "extra_hosts": ({"h": "1.1.1.1"}, 5),
        "depends_on": ({"db": {}}, 5),
        # Scalar IS a valid form for these two, so a non-scalar non-list is used.
        "tmpfs": (["/scratch/a"], 5),
        "env_file": (["a.env"], 5),
    }

    def test_table_covers_every_mergeable_key(self) -> None:
        # Guards the guard: a new mergeable key cannot be added without a case
        # here, which is exactly how the merge policy drifted from the gate.
        mergeable = {key for key, spec in SERVICE_KEYS.items() if spec.merge is not None}
        mergeable |= _STRUCTURAL_MERGE_KEYS | _STRUCTURAL_CONCAT_KEYS
        assert set(self.HOSTILE) == mergeable

    # Top-level definitions the store keys need, so the only thing that can
    # refuse them is the shape -- not an "unknown secret/config" lookup failure.
    TOP_LEVEL: ClassVar[dict[str, dict[str, Any]]] = {
        "secrets": {"secrets": {"s": {"environment": "E"}}},
        "configs": {"configs": {"c": {"content": "x"}}},
    }

    @pytest.mark.parametrize("key", sorted(HOSTILE))
    def test_form_refused_standalone_is_refused_through_extends(self, key: str) -> None:
        base_val, bad_val = self.HOSTILE[key]
        top = self.TOP_LEVEL.get(key, {})

        # The gate refuses this form on a plain service...
        with pytest.raises(UnsupportedComposeError):
            validate({"services": {"app": {"image": "x", key: bad_val}}, **top})

        # ...so it must not become acceptable by arriving through `extends`.
        doc = {
            "services": {
                "base": {"image": "x", key: base_val},
                "app": {"extends": {"service": "base"}, key: bad_val},
            },
            **top,
        }
        with pytest.raises(UnsupportedComposeError):
            validate(resolve_extends(doc))


class TestMergeErrorAttribution:
    """A merge error names the service the offending value actually belongs to."""

    def test_bad_value_in_the_base_blames_the_base(self) -> None:
        # `web`'s own cap_add is a valid list; the base's is the malformed one.
        doc = {
            "services": {
                "base": {"image": "x", "cap_add": "NET_ADMIN"},
                "web": {"extends": {"service": "base"}, "cap_add": ["SYS_TIME"]},
            }
        }
        with pytest.raises(UnsupportedComposeError, match="service 'base': 'cap_add' must be a list"):
            resolve_extends(doc)

    def test_bad_value_in_the_extending_service_blames_it(self) -> None:
        doc = {
            "services": {
                "base": {"image": "x", "cap_add": ["NET_ADMIN"]},
                "web": {"extends": {"service": "base"}, "cap_add": "SYS_TIME"},
            }
        }
        with pytest.raises(UnsupportedComposeError, match="service 'web': 'cap_add' must be a list"):
            resolve_extends(doc)

    def test_structural_concat_base_value_blames_the_base(self) -> None:
        # `_as_concat_list` path, not the registry one.
        doc = {
            "services": {
                "base": {"image": "x", "volumes": "./a:/a"},
                "web": {"extends": {"service": "base"}, "volumes": ["./b:/b"]},
            }
        }
        with pytest.raises(UnsupportedComposeError, match="service 'base': 'volumes' must be a list"):
            resolve_extends(doc)

    def test_structural_mapping_base_value_blames_the_base(self) -> None:
        # `_as_mapping` path: healthcheck has no list form.
        doc = {
            "services": {
                "base": {"image": "x", "healthcheck": ["bad"]},
                "web": {"extends": {"service": "base"}, "healthcheck": {"test": ["CMD", "x"]}},
            }
        }
        with pytest.raises(UnsupportedComposeError, match="service 'base': cannot merge 'healthcheck'"):
            resolve_extends(doc)


class TestExtraHostsListFormMerge:
    """extra_hosts has a list form in Compose, so a merge normalizes it -- correctly."""

    def test_list_form_merges_with_map_form(self) -> None:
        doc = {
            "services": {
                "base": {"image": "x", "extra_hosts": {"db": "1.1.1.1"}},
                "web": {"extends": {"service": "base"}, "extra_hosts": ["cache:2.2.2.2"]},
            }
        }
        assert resolve_extends(doc)["services"]["web"]["extra_hosts"] == {"db": "1.1.1.1", "cache": "2.2.2.2"}

    def test_local_wins_on_collision(self) -> None:
        doc = {
            "services": {
                "base": {"image": "x", "extra_hosts": {"db": "1.1.1.1"}},
                "web": {"extends": {"service": "base"}, "extra_hosts": ["db:9.9.9.9"]},
            }
        }
        assert resolve_extends(doc)["services"]["web"]["extra_hosts"] == {"db": "9.9.9.9"}

    def test_ipv6_address_keeps_its_colons(self) -> None:
        # Splitting on the *first* colon only. pairs_to_mapping would have split on
        # '=' and produced a single {'myhost:::1': None} key.
        doc = {
            "services": {
                "base": {"image": "x", "extra_hosts": {"a": "1.1.1.1"}},
                "web": {"extends": {"service": "base"}, "extra_hosts": ["myhost:::1"]},
            }
        }
        assert resolve_extends(doc)["services"]["web"]["extra_hosts"] == {"a": "1.1.1.1", "myhost": "::1"}

    def test_equals_separator_form_merges(self) -> None:
        # Compose's documented separator, through the merge path.
        doc = {
            "services": {
                "base": {"image": "x", "extra_hosts": {"a": "1.1.1.1"}},
                "web": {"extends": {"service": "base"}, "extra_hosts": ["somehost=162.242.195.82"]},
            }
        }
        merged = resolve_extends(doc)["services"]["web"]["extra_hosts"]
        assert merged == {"a": "1.1.1.1", "somehost": "162.242.195.82"}

    def test_equals_separator_keeps_ipv6(self) -> None:
        doc = {
            "services": {
                "base": {"image": "x", "extra_hosts": {"a": "1.1.1.1"}},
                "web": {"extends": {"service": "base"}, "extra_hosts": ["myhostv6=::1"]},
            }
        }
        merged = resolve_extends(doc)["services"]["web"]["extra_hosts"]
        assert merged == {"a": "1.1.1.1", "myhostv6": "::1"}

    def test_entry_without_a_separator_is_refused(self) -> None:
        doc = {
            "services": {
                "base": {"image": "x", "extra_hosts": {"a": "1.1.1.1"}},
                "web": {"extends": {"service": "base"}, "extra_hosts": ["no-colon-here"]},
            }
        }
        with pytest.raises(UnsupportedComposeError, match="extra_hosts entries must be 'host=ip' or 'host:ip'"):
            resolve_extends(doc)

    def test_non_string_entry_is_refused(self) -> None:
        doc = {
            "services": {
                "base": {"image": "x", "extra_hosts": {"a": "1.1.1.1"}},
                "web": {"extends": {"service": "base"}, "extra_hosts": [5]},
            }
        }
        with pytest.raises(UnsupportedComposeError, match="extra_hosts entries must be 'host=ip' or 'host:ip'"):
            resolve_extends(doc)


class TestStructuralConcatScalarForm:
    """Only tmpfs/env_file have a bare-string form; the rest must stay lists."""

    def test_volumes_scalar_form_is_refused(self) -> None:
        # The gate refuses `volumes: "./a:/a"` standalone, so a merge must not
        # normalize it into a list either.
        doc = {
            "services": {
                "base": {"image": "x", "volumes": ["./a:/a"]},
                "web": {"extends": {"service": "base"}, "volumes": "./b:/b"},
            }
        }
        with pytest.raises(UnsupportedComposeError, match="'volumes' must be a list"):
            resolve_extends(doc)

    def test_tmpfs_scalar_form_still_normalizes(self) -> None:
        # Compose does give tmpfs a scalar form, and the gate accepts one.
        doc = {
            "services": {
                "base": {"image": "x", "tmpfs": "/scratch/base"},
                "web": {"extends": {"service": "base"}, "tmpfs": ["/scratch/local"]},
            }
        }
        assert resolve_extends(doc)["services"]["web"]["tmpfs"] == ["/scratch/base", "/scratch/local"]
