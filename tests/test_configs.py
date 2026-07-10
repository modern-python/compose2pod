from typing import Any

import pytest

from compose2pod.configs import CONFIG
from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.store import create_lines, flags, referenced_variables, validate_all


_KINDS = (CONFIG,)


def _doc(configs: Any = None, svc_configs: Any = None) -> dict[str, Any]:  # noqa: ANN401 - Compose values are untyped
    svc: dict[str, Any] = {"image": "x"}
    if svc_configs is not None:
        svc["configs"] = svc_configs
    doc: dict[str, Any] = {"services": {"app": svc}}
    if configs is not None:
        doc["configs"] = configs
    return doc


class TestValidateConfigKind:
    def test_content_source_accepted(self) -> None:
        validate_all(_doc({"c": {"content": "hello"}}, ["c"]), _KINDS)

    def test_absolute_long_form_target_accepted(self) -> None:
        validate_all(_doc({"c": {"file": "./c"}}, [{"source": "c", "target": "/etc/c.conf"}]), _KINDS)

    def test_relative_long_form_target_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"config target 'c.conf' must be an absolute path"):
            validate_all(_doc({"c": {"file": "./c"}}, [{"source": "c", "target": "c.conf"}]), _KINDS)

    def test_external_rejected_with_config_wording(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="external configs are not supported"):
            validate_all(_doc({"c": {"external": True}}, ["c"]), _KINDS)

    def test_unknown_referenced_config_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="unknown config"):
            validate_all(_doc({"c": {"file": "./c"}}, ["ghost"]), _KINDS)


class TestConfigEmission:
    def test_short_form_flag_defaults_to_root_target(self) -> None:
        assert flags({"configs": ["nginx"]}, "p", CONFIG) == ["--secret", "source=p-config-nginx,target=/nginx"]

    def test_long_form_absolute_target(self) -> None:
        svc = {"configs": [{"source": "nginx", "target": "/etc/nginx/nginx.conf", "mode": 0o444}]}
        assert flags(svc, "p", CONFIG) == [
            "--secret",
            "source=p-config-nginx,target=/etc/nginx/nginx.conf,mode=0444",
        ]

    def test_content_source_create_line_expands_at_run_time(self) -> None:
        doc = {"configs": {"c": {"content": "token=${API_TOKEN}"}}}
        assert create_lines(doc, "p", "/proj", ["c"], CONFIG) == [
            "printf '%s' \"token=${API_TOKEN-}\" | podman secret create p-config-c -"
        ]

    def test_file_source_create_line_is_config_prefixed(self) -> None:
        doc = {"configs": {"c": {"file": "./c.conf"}}}
        assert create_lines(doc, "p", "/proj", ["c"], CONFIG) == ['podman secret create p-config-c "/proj/c.conf"']

    def test_content_referenced_variables(self) -> None:
        doc = {"configs": {"c": {"content": "a=${A} b=${B}"}}}
        assert referenced_variables(doc, "/proj", ["c"], CONFIG) == {"A", "B"}
