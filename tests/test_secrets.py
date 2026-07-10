from typing import Any

import pytest

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.secrets import (
    referenced_secret_names,
    secret_create_lines,
    secret_flags,
    secret_referenced_variables,
    validate_secrets,
)


def _doc(secrets: Any = None, svc_secrets: Any = None) -> dict[str, Any]:  # noqa: ANN401 - Compose values are untyped
    svc = {"image": "x"}
    if svc_secrets is not None:
        svc["secrets"] = svc_secrets
    doc = {"services": {"app": svc}}
    if secrets is not None:
        doc["secrets"] = secrets
    return doc


class TestValidateSecrets:
    def test_file_and_environment_sources_accepted(self) -> None:
        doc = _doc({"a": {"file": "./a.txt"}, "b": {"environment": "B"}}, ["a", "b"])
        validate_secrets(doc)  # no raise

    def test_long_form_reference_accepted(self) -> None:
        doc = _doc({"a": {"file": "./a.txt"}}, [{"source": "a", "target": "t", "uid": "1", "gid": "2", "mode": 0o400}])
        validate_secrets(doc)

    def test_external_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="external secrets are not supported"):
            validate_secrets(_doc({"a": {"external": True}}, ["a"]))

    def test_two_sources_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="exactly one of 'file' or 'environment'"):
            validate_secrets(_doc({"a": {"file": "./a", "environment": "A"}}, ["a"]))

    def test_unknown_definition_key_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="unsupported keys"):
            validate_secrets(_doc({"a": {"file": "./a", "driver": "x"}}, ["a"]))

    def test_non_mapping_definition_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="secret 'a' must be a mapping"):
            validate_secrets(_doc({"a": ["./a"]}, ["a"]))

    def test_unknown_referenced_secret_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="unknown secret 'ghost'"):
            validate_secrets(_doc({"a": {"file": "./a"}}, ["ghost"]))

    def test_service_secrets_not_a_list_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="secrets must be a list"):
            validate_secrets(_doc({"a": {"file": "./a"}}, {"a": {}}))

    def test_long_form_unknown_key_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="unsupported secret keys"):
            validate_secrets(_doc({"a": {"file": "./a"}}, [{"source": "a", "bogus": 1}]))

    def test_top_level_secrets_not_a_mapping_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="top-level 'secrets' must be a mapping"):
            validate_secrets({"services": {"app": {"image": "x"}}, "secrets": ["a"]})

    def test_non_string_or_mapping_reference_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="secret entry must be a string or mapping"):
            validate_secrets(_doc({"a": {"file": "./a"}}, [123]))

    def test_long_form_non_string_source_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="secret entry 'source' must be a string"):
            validate_secrets(_doc({"a": {"file": "./a"}}, [{"source": 123}]))

    def test_injecting_secret_name_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="must match"):
            validate_secrets(_doc({"n'; touch /tmp/x; '": {"file": "./a"}}, None))

    def test_dotted_dashed_secret_name_accepted(self) -> None:
        validate_secrets(_doc({"db.pw-1": {"file": "./a"}}, ["db.pw-1"]))

    def test_bad_env_var_name_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="not a valid identifier"):
            validate_secrets(_doc({"s": {"environment": 'X"; touch /tmp/x; "'}}, None))

    def test_non_scalar_long_form_value_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="secret 'mode' must be an int or string"):
            validate_secrets(_doc({"s": {"file": "./a"}}, [{"source": "s", "mode": True}]))
        with pytest.raises(UnsupportedComposeError, match="secret 'uid' must be an int or string"):
            validate_secrets(_doc({"s": {"file": "./a"}}, [{"source": "s", "uid": [1]}]))


class TestSecretEmission:
    def test_short_form_flag(self) -> None:
        assert secret_flags({"secrets": ["db"]}, "p") == ["--secret", "source=p-db,target=db"]

    def test_long_form_flag_with_opts(self) -> None:
        svc = {"secrets": [{"source": "db", "target": "pw", "uid": "1000", "gid": "1000", "mode": 0o400}]}
        assert secret_flags(svc, "p") == ["--secret", "source=p-db,target=pw,uid=1000,gid=1000,mode=0400"]

    def test_string_mode_passes_through(self) -> None:
        svc = {"secrets": [{"source": "db", "mode": "0440"}]}
        assert secret_flags(svc, "p") == ["--secret", "source=p-db,target=db,mode=0440"]

    def test_referenced_names_deduped_in_order(self) -> None:
        services = {"a": {"secrets": ["s1", "s2"]}, "b": {"secrets": [{"source": "s1"}]}}
        assert referenced_secret_names(services, ["a", "b"]) == ["s1", "s2"]

    def test_file_source_create_line(self) -> None:
        doc = {"secrets": {"db": {"file": "./db.txt"}}}
        assert secret_create_lines(doc, "p", "/proj", ["db"]) == ['podman secret create p-db "/proj/db.txt"']

    def test_environment_source_create_line(self) -> None:
        doc = {"secrets": {"k": {"environment": "API_KEY"}}}
        assert secret_create_lines(doc, "p", "/proj", ["k"]) == [
            "printf '%s' \"${API_KEY-}\" | podman secret create p-k -"
        ]

    def test_referenced_variables_from_env_and_path(self) -> None:
        doc = {"secrets": {"k": {"environment": "API_KEY"}, "f": {"file": "${DIR}/s.txt"}}}
        assert secret_referenced_variables(doc, "/proj", ["k", "f"]) == {"API_KEY", "DIR"}
