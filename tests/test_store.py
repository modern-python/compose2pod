from typing import Any

import pytest

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.secrets import SECRET
from compose2pod.store import (
    create_lines,
    flags,
    referenced_names,
    referenced_variables,
    validate_all,
)


_KINDS = (SECRET,)


def _doc(secrets: Any = None, svc_secrets: Any = None) -> dict[str, Any]:  # noqa: ANN401 - Compose values are untyped
    svc: dict[str, Any] = {"image": "x"}
    if svc_secrets is not None:
        svc["secrets"] = svc_secrets
    doc: dict[str, Any] = {"services": {"app": svc}}
    if secrets is not None:
        doc["secrets"] = secrets
    return doc


class TestValidateSecretKind:
    def test_file_and_environment_sources_accepted(self) -> None:
        validate_all(_doc({"a": {"file": "./a.txt"}, "b": {"environment": "B"}}, ["a", "b"]), _KINDS)

    def test_long_form_reference_accepted(self) -> None:
        doc = _doc({"a": {"file": "./a.txt"}}, [{"source": "a", "target": "t", "uid": "1", "gid": "2", "mode": 0o400}])
        validate_all(doc, _KINDS)

    def test_external_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="external secrets are not supported"):
            validate_all(_doc({"a": {"external": True}}, ["a"]), _KINDS)

    def test_two_sources_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="must have exactly one of"):
            validate_all(_doc({"a": {"file": "./a", "environment": "A"}}, ["a"]), _KINDS)

    def test_unknown_definition_key_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="unsupported keys"):
            validate_all(_doc({"a": {"file": "./a", "driver": "x"}}, ["a"]), _KINDS)

    def test_non_mapping_definition_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="must be a mapping"):
            validate_all(_doc({"a": ["./a"]}, ["a"]), _KINDS)

    def test_unknown_referenced_secret_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="unknown secret"):
            validate_all(_doc({"a": {"file": "./a"}}, ["ghost"]), _KINDS)

    def test_service_secrets_not_a_list_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="secrets must be a list"):
            validate_all(_doc({"a": {"file": "./a"}}, {"a": {}}), _KINDS)

    def test_long_form_unknown_key_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="unsupported secret keys"):
            validate_all(_doc({"a": {"file": "./a"}}, [{"source": "a", "bogus": 1}]), _KINDS)

    def test_top_level_secrets_not_a_mapping_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="top-level 'secrets' must be a mapping"):
            validate_all({"services": {"app": {"image": "x"}}, "secrets": ["a"]}, _KINDS)

    def test_non_string_or_mapping_reference_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="entry must be a string or mapping"):
            validate_all(_doc({"a": {"file": "./a"}}, [123]), _KINDS)

    def test_long_form_non_string_source_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="entry 'source' must be a string"):
            validate_all(_doc({"a": {"file": "./a"}}, [{"source": 123}]), _KINDS)

    def test_injecting_secret_name_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="must match"):
            validate_all(_doc({"n'; touch /tmp/x; '": {"file": "./a"}}, None), _KINDS)

    def test_dotted_dashed_secret_name_accepted(self) -> None:
        validate_all(_doc({"db.pw-1": {"file": "./a"}}, ["db.pw-1"]), _KINDS)

    def test_bad_env_var_name_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="is not a valid identifier"):
            validate_all(_doc({"s": {"environment": 'X"; touch /tmp/x; "'}}, None), _KINDS)

    def test_newline_in_secret_name_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="must match"):
            validate_all(_doc({"db\n": {"file": "./a"}}, None), _KINDS)

    def test_newline_in_env_var_name_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="is not a valid identifier"):
            validate_all(_doc({"s": {"environment": "VAR\n"}}, None), _KINDS)

    def test_non_scalar_long_form_value_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="must be an int or string"):
            validate_all(_doc({"s": {"file": "./a"}}, [{"source": "s", "mode": True}]), _KINDS)
        with pytest.raises(UnsupportedComposeError, match="must be an int or string"):
            validate_all(_doc({"s": {"file": "./a"}}, [{"source": "s", "uid": [1]}]), _KINDS)

    def test_content_in_secret_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="unsupported keys"):
            validate_all(_doc({"a": {"content": "x"}}, None), _KINDS)


class TestSecretEmission:
    def test_short_form_flag(self) -> None:
        assert flags({"secrets": ["db"]}, "p", SECRET) == ["--secret", "source=p-db,target=db"]

    def test_long_form_flag_with_opts(self) -> None:
        svc = {"secrets": [{"source": "db", "target": "pw", "uid": "1000", "gid": "1000", "mode": 0o400}]}
        assert flags(svc, "p", SECRET) == ["--secret", "source=p-db,target=pw,uid=1000,gid=1000,mode=0400"]

    def test_string_mode_passes_through(self) -> None:
        svc = {"secrets": [{"source": "db", "mode": "0440"}]}
        assert flags(svc, "p", SECRET) == ["--secret", "source=p-db,target=db,mode=0440"]

    def test_referenced_names_deduped_in_order(self) -> None:
        services = {"a": {"secrets": ["s1", "s2"]}, "b": {"secrets": [{"source": "s1"}]}}
        assert referenced_names(services, ["a", "b"], SECRET) == ["s1", "s2"]

    def test_file_source_create_line(self) -> None:
        doc = {"secrets": {"db": {"file": "./db.txt"}}}
        assert create_lines(doc, "p", "/proj", ["db"], SECRET) == ['podman secret create p-db "/proj/db.txt"']

    def test_environment_source_create_line(self) -> None:
        doc = {"secrets": {"k": {"environment": "API_KEY"}}}
        assert create_lines(doc, "p", "/proj", ["k"], SECRET) == [
            "printf '%s' \"${API_KEY-}\" | podman secret create p-k -"
        ]

    def test_referenced_variables_from_env_and_path(self) -> None:
        doc = {"secrets": {"k": {"environment": "API_KEY"}, "f": {"file": "${DIR}/s.txt"}}}
        assert referenced_variables(doc, "/proj", ["k", "f"], SECRET) == {"API_KEY", "DIR"}

    def test_non_string_file_takes_environment_branch(self) -> None:
        doc = {"secrets": {"k": {"file": ["x"], "environment": "API_KEY"}}}
        assert create_lines(doc, "p", "/proj", ["k"], SECRET) == [
            "printf '%s' \"${API_KEY-}\" | podman secret create p-k -"
        ]
        assert referenced_variables(doc, "/proj", ["k"], SECRET) == {"API_KEY"}
