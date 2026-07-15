"""Direct tests for the compose reader: JSON/YAML/auto dispatch and the YAML-1.2 dialect."""

import pytest

from compose2pod import read as read_module
from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.read import read


class TestReadJson:
    def test_json_document_parses(self) -> None:
        assert read('{"services": {"app": {"image": "x"}}}', "json") == {"services": {"app": {"image": "x"}}}

    def test_invalid_json_raises_unsupported(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="invalid JSON"):
            read("not json", "json")


class TestReadYamlDialect:
    def test_bare_on_is_string_not_bool(self) -> None:
        # YAML 1.2 / Docker: `on` is a plain string, not the boolean True.
        assert read("k: on", "yaml") == {"k": "on"}

    def test_bare_true_is_bool(self) -> None:
        assert read("k: true", "yaml")["k"] is True

    def test_exponent_without_dot_is_float(self) -> None:
        # YAML 1.2 core-schema float: a bare exponent is enough (PyYAML 1.1 leaves it a string).
        assert read("k: 1e3", "yaml") == {"k": 1000.0}

    def test_plain_integer_stays_int(self) -> None:
        # The float-resolver swap must not swallow a plain integer.
        result = read("k: 123", "yaml")
        assert result == {"k": 123}
        assert isinstance(result["k"], int)

    def test_bare_on_key_is_string(self) -> None:
        # A bare `on:` key resolves to the string "on", not the bool True.
        assert read("on: x", "yaml") == {"on": "x"}

    def test_invalid_yaml_raises_unsupported(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="invalid YAML"):
            read("a: [1, 2", "yaml")


class TestReadAuto:
    def test_auto_parses_json(self) -> None:
        assert read('{"a": 1}', "auto") == {"a": 1}

    def test_auto_falls_back_to_yaml(self) -> None:
        assert read("a: 1", "auto") == {"a": 1}

    def test_auto_both_invalid_raises(self) -> None:
        with pytest.raises(UnsupportedComposeError):
            read("a: [1, 2", "auto")


class TestReadMissingExtra:
    def test_yaml_without_pyyaml_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(read_module, "_yaml", None)
        with pytest.raises(UnsupportedComposeError, match="requires the 'yaml' extra"):
            read("services: {}", "yaml")
