from typing import Any

import pytest

from compose2pod import stores
from compose2pod.exceptions import UnsupportedComposeError


def _doc(top_key: str, defs: Any = None, refs: Any = None) -> dict[str, Any]:  # noqa: ANN401 - Compose values are untyped
    svc: dict[str, Any] = {"image": "x"}
    if refs is not None:
        svc[top_key] = refs
    doc: dict[str, Any] = {"services": {"app": svc}}
    if defs is not None:
        doc[top_key] = defs
    return doc


class TestValidateSecretKind:
    def test_file_and_environment_sources_accepted(self) -> None:
        stores.validate(_doc("secrets", {"a": {"file": "./a.txt"}, "b": {"environment": "B"}}, ["a", "b"]))

    def test_long_form_reference_accepted(self) -> None:
        doc = _doc(
            "secrets",
            {"a": {"file": "./a.txt"}},
            [{"source": "a", "target": "t", "uid": "1", "gid": "2", "mode": 0o400}],
        )
        stores.validate(doc)

    def test_external_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="external secrets are not supported"):
            stores.validate(_doc("secrets", {"a": {"external": True}}, ["a"]))

    def test_two_sources_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="must have exactly one of"):
            stores.validate(_doc("secrets", {"a": {"file": "./a", "environment": "A"}}, ["a"]))

    def test_unknown_definition_key_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="unsupported keys"):
            stores.validate(_doc("secrets", {"a": {"file": "./a", "driver": "x"}}, ["a"]))

    def test_non_mapping_definition_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="must be a mapping"):
            stores.validate(_doc("secrets", {"a": ["./a"]}, ["a"]))

    def test_unknown_referenced_secret_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="unknown secret"):
            stores.validate(_doc("secrets", {"a": {"file": "./a"}}, ["ghost"]))

    def test_service_secrets_not_a_list_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="secrets must be a list"):
            stores.validate(_doc("secrets", {"a": {"file": "./a"}}, {"a": {}}))

    def test_long_form_unknown_key_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="unsupported secret keys"):
            stores.validate(_doc("secrets", {"a": {"file": "./a"}}, [{"source": "a", "bogus": 1}]))

    def test_top_level_secrets_not_a_mapping_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="top-level 'secrets' must be a mapping"):
            stores.validate({"services": {"app": {"image": "x"}}, "secrets": ["a"]})

    def test_non_string_secret_name_raises_instead_of_crashing_raw(self) -> None:
        # PyYAML can produce a non-string mapping key (e.g. an unquoted `1:`).
        # Used to crash raw: TypeError: expected string or bytes-like object,
        # got 'int', from _NAME.fullmatch(name).
        with pytest.raises(UnsupportedComposeError, match="key 1 must be a string"):
            stores.validate(_doc("secrets", {1: {"file": "./s"}}))

    def test_definition_mixed_type_unknown_keys_do_not_crash_raw(self) -> None:
        # sorted(unknown) used to crash raw (TypeError: '<' not supported
        # between instances of 'str' and 'int') once a secret/config
        # definition's unrecognized keys mixed a non-string key with a
        # string one. require_string_keys now catches the non-string key
        # before the unknown-keys check is even reached.
        with pytest.raises(UnsupportedComposeError, match=r"secret 'a': key 1 must be a string"):
            stores.validate(_doc("secrets", {"a": {"file": "./a", 1: "x", "y": "z"}}, ["a"]))

    def test_long_form_reference_mixed_type_unknown_keys_do_not_crash_raw(self) -> None:
        # Same class as above, for a long-form service reference's own
        # unrecognized keys.
        with pytest.raises(UnsupportedComposeError, match=r"secret entry: key 1 must be a string"):
            stores.validate(_doc("secrets", {"a": {"file": "./a"}}, [{"source": "a", 1: "x", "y": "z"}]))

    def test_non_string_or_mapping_reference_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="entry must be a string or mapping"):
            stores.validate(_doc("secrets", {"a": {"file": "./a"}}, [123]))

    def test_long_form_non_string_source_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="entry 'source' must be a string"):
            stores.validate(_doc("secrets", {"a": {"file": "./a"}}, [{"source": 123}]))

    def test_injecting_secret_name_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="must match"):
            stores.validate(_doc("secrets", {"n'; touch /tmp/x; '": {"file": "./a"}}))

    def test_dotted_dashed_secret_name_accepted(self) -> None:
        stores.validate(_doc("secrets", {"db.pw-1": {"file": "./a"}}, ["db.pw-1"]))

    def test_bad_env_var_name_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="is not a valid identifier"):
            stores.validate(_doc("secrets", {"s": {"environment": 'X"; touch /tmp/x; "'}}))

    def test_newline_in_secret_name_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="must match"):
            stores.validate(_doc("secrets", {"db\n": {"file": "./a"}}))

    def test_newline_in_env_var_name_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="is not a valid identifier"):
            stores.validate(_doc("secrets", {"s": {"environment": "VAR\n"}}))

    def test_non_scalar_long_form_value_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="must be an integer"):
            stores.validate(_doc("secrets", {"s": {"file": "./a"}}, [{"source": "s", "mode": True}]))
        with pytest.raises(UnsupportedComposeError, match="must be a string"):
            stores.validate(_doc("secrets", {"s": {"file": "./a"}}, [{"source": "s", "uid": [1]}]))

    # Task 10: uid/gid and mode are NOT the same grammar, despite reading like
    # siblings -- measured against `docker compose config` v5.1.2.

    def test_uid_gid_must_be_a_string_native_int_rejected(self) -> None:
        # uid/gid are typed as a plain string field with no further parsing at
        # config-validate time -- Docker refuses a *native* int outright, even
        # though the numerically identical string is fine.
        with pytest.raises(UnsupportedComposeError, match=r"secret uid.*must be a string"):
            stores.validate(_doc("secrets", {"s": {"file": "./a"}}, [{"source": "s", "uid": 1000}]))
        with pytest.raises(UnsupportedComposeError, match=r"secret gid.*must be a string"):
            stores.validate(_doc("secrets", {"s": {"file": "./a"}}, [{"source": "s", "gid": 1000}]))

    def test_uid_gid_string_content_is_unchecked(self) -> None:
        # Measured: the string's *content* is never parsed at config-validate
        # time -- even a non-numeric string passes `docker compose config`.
        stores.validate(_doc("secrets", {"s": {"file": "./a"}}, [{"source": "s", "uid": "somevalue"}]))
        stores.validate(_doc("secrets", {"s": {"file": "./a"}}, [{"source": "s", "gid": "-1"}]))

    def test_mode_accepts_int_or_integer_string_rejects_non_numeric_string(self) -> None:
        stores.validate(_doc("secrets", {"s": {"file": "./a"}}, [{"source": "s", "mode": 0o400}]))
        stores.validate(_doc("secrets", {"s": {"file": "./a"}}, [{"source": "s", "mode": "0400"}]))
        with pytest.raises(UnsupportedComposeError, match="must be an integer"):
            stores.validate(_doc("secrets", {"s": {"file": "./a"}}, [{"source": "s", "mode": "somevalue"}]))

    def test_mode_rejects_any_float_whole_or_fractional(self) -> None:
        # Unlike uid/gid, mode goes through Go's strconv.ParseInt at decode
        # time -- a native float is refused outright, whole or fractional.
        with pytest.raises(UnsupportedComposeError, match="must be an integer"):
            stores.validate(_doc("secrets", {"s": {"file": "./a"}}, [{"source": "s", "mode": 400.0}]))
        with pytest.raises(UnsupportedComposeError, match="must be an integer"):
            stores.validate(_doc("secrets", {"s": {"file": "./a"}}, [{"source": "s", "mode": 0.5}]))

    def test_mode_string_form_is_a_strict_parseint_not_pythons_lenient_int(self) -> None:
        # Measured: surrounding whitespace and a digit-grouping underscore are
        # both refused as strings, even though Python's own int() would accept
        # either.
        with pytest.raises(UnsupportedComposeError, match="must be an integer"):
            stores.validate(_doc("secrets", {"s": {"file": "./a"}}, [{"source": "s", "mode": "  400  "}]))
        with pytest.raises(UnsupportedComposeError, match="must be an integer"):
            stores.validate(_doc("secrets", {"s": {"file": "./a"}}, [{"source": "s", "mode": "1_000"}]))

    def test_uid_gid_mode_variable_reference_bypasses_validation(self) -> None:
        stores.validate(_doc("secrets", {"s": {"file": "./a"}}, [{"source": "s", "uid": "${U}", "mode": "${M}"}]))

    def test_config_uid_gid_mode_share_secrets_grammar(self) -> None:
        # Task 10: "This applies to BOTH secrets and configs references" -- measured identical.
        with pytest.raises(UnsupportedComposeError, match=r"config uid.*must be a string"):
            stores.validate(_doc("configs", {"c": {"file": "./c"}}, [{"source": "c", "uid": 1000}]))
        stores.validate(_doc("configs", {"c": {"file": "./c"}}, [{"source": "c", "uid": "somevalue"}]))
        with pytest.raises(UnsupportedComposeError, match="must be an integer"):
            stores.validate(_doc("configs", {"c": {"file": "./c"}}, [{"source": "c", "mode": "somevalue"}]))

    def test_non_string_target_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="secret target"):
            stores.validate(_doc("secrets", {"s": {"file": "./a"}}, [{"source": "s", "target": 5}]))
        with pytest.raises(UnsupportedComposeError, match="secret target"):
            stores.validate(_doc("secrets", {"s": {"file": "./a"}}, [{"source": "s", "target": ["t"]}]))

    def test_content_in_secret_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="unsupported keys"):
            stores.validate(_doc("secrets", {"a": {"content": "x"}}))


class TestValidateConfigKind:
    def test_content_source_accepted(self) -> None:
        stores.validate(_doc("configs", {"c": {"content": "hello"}}, ["c"]))

    def test_absolute_long_form_target_accepted(self) -> None:
        stores.validate(_doc("configs", {"c": {"file": "./c"}}, [{"source": "c", "target": "/etc/c.conf"}]))

    def test_relative_long_form_target_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"config target 'c.conf' must be an absolute path"):
            stores.validate(_doc("configs", {"c": {"file": "./c"}}, [{"source": "c", "target": "c.conf"}]))

    def test_non_string_target_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="config target"):
            stores.validate(_doc("configs", {"c": {"file": "./c"}}, [{"source": "c", "target": 5}]))
        with pytest.raises(UnsupportedComposeError, match="config target"):
            stores.validate(_doc("configs", {"c": {"file": "./c"}}, [{"source": "c", "target": ["/etc"]}]))

    def test_external_rejected_with_config_wording(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="external configs are not supported"):
            stores.validate(_doc("configs", {"c": {"external": True}}, ["c"]))

    def test_unknown_referenced_config_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="unknown config"):
            stores.validate(_doc("configs", {"c": {"file": "./c"}}, ["ghost"]))


class TestSecretEmission:
    def test_short_form_flag(self) -> None:
        assert stores.flags({"secrets": ["db"]}, "p") == ["--secret", "source=p-db,target=db"]

    def test_long_form_flag_with_opts(self) -> None:
        svc = {"secrets": [{"source": "db", "target": "pw", "uid": "1000", "gid": "1000", "mode": 0o400}]}
        assert stores.flags(svc, "p") == ["--secret", "source=p-db,target=pw,uid=1000,gid=1000,mode=0400"]

    def test_string_mode_passes_through(self) -> None:
        svc = {"secrets": [{"source": "db", "mode": "0440"}]}
        assert stores.flags(svc, "p") == ["--secret", "source=p-db,target=db,mode=0440"]

    def test_referenced_names_deduped_in_order_via_teardown(self) -> None:
        compose = {"services": {"a": {"secrets": ["s1", "s2"]}, "b": {"secrets": [{"source": "s1"}]}}}
        assert stores.teardown_line(compose, ["a", "b"], "p") == "podman secret rm p-s1 p-s2 >/dev/null 2>&1 || true"

    def test_file_source_create_line(self) -> None:
        doc = {"services": {"app": {"secrets": ["db"]}}, "secrets": {"db": {"file": "./db.txt"}}}
        assert stores.create_lines(doc, ["app"], "p", "/proj") == ['podman secret create p-db "/proj/db.txt"']

    def test_environment_source_create_line(self) -> None:
        doc = {"services": {"app": {"secrets": ["k"]}}, "secrets": {"k": {"environment": "API_KEY"}}}
        assert stores.create_lines(doc, ["app"], "p", "/proj") == [
            "printf '%s' \"${API_KEY-}\" | podman secret create p-k -"
        ]

    def test_referenced_variables_from_env_and_path(self) -> None:
        doc = {
            "services": {"app": {"secrets": ["k", "f"]}},
            "secrets": {"k": {"environment": "API_KEY"}, "f": {"file": "${DIR}/s.txt"}},
        }
        assert stores.referenced_variables(doc, ["app"], "/proj") == {"API_KEY", "DIR"}

    def test_non_string_file_takes_environment_branch(self) -> None:
        doc = {"services": {"app": {"secrets": ["k"]}}, "secrets": {"k": {"file": ["x"], "environment": "API_KEY"}}}
        assert stores.create_lines(doc, ["app"], "p", "/proj") == [
            "printf '%s' \"${API_KEY-}\" | podman secret create p-k -"
        ]
        assert stores.referenced_variables(doc, ["app"], "/proj") == {"API_KEY"}


class TestConfigEmission:
    def test_short_form_flag_defaults_to_root_target(self) -> None:
        assert stores.flags({"configs": ["nginx"]}, "p") == ["--secret", "source=p-config-nginx,target=/nginx"]

    def test_long_form_absolute_target(self) -> None:
        svc = {"configs": [{"source": "nginx", "target": "/etc/nginx/nginx.conf", "mode": 0o444}]}
        assert stores.flags(svc, "p") == [
            "--secret",
            "source=p-config-nginx,target=/etc/nginx/nginx.conf,mode=0444",
        ]

    def test_content_source_create_line_expands_at_run_time(self) -> None:
        doc = {"services": {"app": {"configs": ["c"]}}, "configs": {"c": {"content": "token=${API_TOKEN}"}}}
        assert stores.create_lines(doc, ["app"], "p", "/proj") == [
            "printf '%s' \"token=${API_TOKEN-}\" | podman secret create p-config-c -"
        ]

    def test_file_source_create_line_is_config_prefixed(self) -> None:
        doc = {"services": {"app": {"configs": ["c"]}}, "configs": {"c": {"file": "./c.conf"}}}
        assert stores.create_lines(doc, ["app"], "p", "/proj") == ['podman secret create p-config-c "/proj/c.conf"']

    def test_content_referenced_variables(self) -> None:
        doc = {"services": {"app": {"configs": ["c"]}}, "configs": {"c": {"content": "a=${A} b=${B}"}}}
        assert stores.referenced_variables(doc, ["app"], "/proj") == {"A", "B"}


class TestBothKinds:
    def test_flags_emit_secrets_then_configs(self) -> None:
        svc = {"secrets": ["s"], "configs": ["c"]}
        assert stores.flags(svc, "p") == [
            "--secret",
            "source=p-s,target=s",
            "--secret",
            "source=p-config-c,target=/c",
        ]

    def test_teardown_line_lists_secrets_then_configs(self) -> None:
        compose = {"services": {"app": {"secrets": ["s"], "configs": ["c"]}}}
        assert stores.teardown_line(compose, ["app"], "p") == "podman secret rm p-s p-config-c >/dev/null 2>&1 || true"

    def test_teardown_line_empty_without_stores(self) -> None:
        assert stores.teardown_line({"services": {"app": {"image": "x"}}}, ["app"], "p") == ""
