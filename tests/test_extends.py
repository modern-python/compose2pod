from typing import Any

import pytest

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.extends import resolve_extends


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
        with pytest.raises(UnsupportedComposeError, match="cannot merge 'labels' across incompatible forms"):
            resolve_extends(doc)

    def test_cap_add_scalar_normalized_before_concat(self) -> None:
        doc = {
            "services": {
                "base": {"image": "x", "cap_add": "NET_ADMIN"},
                "web": {"extends": {"service": "base"}, "cap_add": ["SYS_TIME"]},
            }
        }
        assert _services(doc)["web"]["cap_add"] == ["NET_ADMIN", "SYS_TIME"]

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
        with pytest.raises(UnsupportedComposeError, match="cannot merge 'cap_add' across incompatible forms"):
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
