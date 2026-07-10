from typing import Any

import pytest

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.secrets import validate_secrets


def _doc(secrets: Any = None, svc_secrets: Any = None) -> dict[str, Any]:  # noqa: ANN401
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
