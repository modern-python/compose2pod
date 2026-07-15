from typing import Any

import pytest

from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.keys import Expand
from compose2pod.resources import deploy_resource_flags, validate_deploy


def _svc(deploy: Any, **extra: Any) -> dict[str, Any]:  # noqa: ANN401 - Compose values are untyped
    return {"image": "x", "deploy": deploy, **extra}


class TestValidateDeploy:
    def test_no_deploy_is_noop(self) -> None:
        validate_deploy("app", {"image": "x"})

    def test_full_resources_accepted(self) -> None:
        deploy = {
            "resources": {"limits": {"cpus": "0.5", "memory": "256m", "pids": 100}, "reservations": {"memory": "128m"}}
        }
        validate_deploy("app", _svc(deploy))

    def test_deploy_not_mapping_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="'deploy' must be a mapping"):
            validate_deploy("app", _svc(["resources"]))

    def test_non_resources_deploy_key_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="deploy: only 'resources' is supported"):
            validate_deploy("app", _svc({"resources": {}, "replicas": 3}))

    def test_unsupported_resources_section_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"deploy.resources: unsupported keys"):
            validate_deploy("app", _svc({"resources": {"foo": {}}}))

    def test_resources_absent_is_noop(self) -> None:
        # Genuinely absent -- a document not asking for resources at all.
        validate_deploy("app", _svc({}))

    def test_null_resources_is_refused(self) -> None:
        # `resources:` with its contents deleted. docker compose config refuses it.
        with pytest.raises(UnsupportedComposeError, match=r"'deploy.resources' must not be null"):
            validate_deploy("app", _svc({"resources": None}))

    def test_resources_not_mapping_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"deploy.resources must be a mapping"):
            validate_deploy("app", _svc({"resources": ["limits"]}))

    def test_limits_not_mapping_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"deploy.resources.limits must be a mapping"):
            validate_deploy("app", _svc({"resources": {"limits": ["cpus"]}}))

    def test_reservations_not_mapping_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"deploy.resources.reservations must be a mapping"):
            validate_deploy("app", _svc({"resources": {"reservations": ["memory"]}}))

    def test_unsupported_reservation_key_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"deploy.resources.reservations: unsupported keys"):
            validate_deploy("app", _svc({"resources": {"reservations": {"gpus": 1}}}))

    def test_unsupported_limit_key_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"deploy.resources.limits: unsupported keys"):
            validate_deploy("app", _svc({"resources": {"limits": {"gpus": 1}}}))

    def test_reservations_cpus_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"reservations.cpus is not supported"):
            validate_deploy("app", _svc({"resources": {"reservations": {"cpus": "0.5"}}}))

    def test_reservations_devices_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"reservations.devices is not supported"):
            validate_deploy("app", _svc({"resources": {"reservations": {"devices": []}}}))

    def test_limit_value_bool_rejected(self) -> None:
        with pytest.raises(UnsupportedComposeError, match="must be a size string"):
            validate_deploy("app", _svc({"resources": {"limits": {"memory": True}}}))

    # Per-field grammars, measured against `docker compose config` v5.1.2 (both
    # oracles fed the same YAML text): limits.memory/reservations.memory are a
    # Go *string* field -- a native number is refused, only a size string is
    # accepted; limits.cpus is validate_number; limits.pids is validate_integer
    # with the whole-float allowance (matches oom_score_adj).

    @pytest.mark.parametrize("value", ["", "somevalue", 512, 1073741824, 1.5])
    def test_limits_memory_rejects(self, value: object) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"deploy\.resources\.limits\.memory"):
            validate_deploy("app", _svc({"resources": {"limits": {"memory": value}}}))

    @pytest.mark.parametrize("value", ["512", "512m", "1.5", "1e3"])
    def test_limits_memory_accepts(self, value: object) -> None:
        validate_deploy("app", _svc({"resources": {"limits": {"memory": value}}}))

    @pytest.mark.parametrize("value", ["", "abc"])
    def test_limits_cpus_rejects(self, value: object) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"deploy\.resources\.limits\.cpus"):
            validate_deploy("app", _svc({"resources": {"limits": {"cpus": value}}}))

    @pytest.mark.parametrize("value", [2, 0.5, "0.5", "2"])
    def test_limits_cpus_accepts(self, value: object) -> None:
        validate_deploy("app", _svc({"resources": {"limits": {"cpus": value}}}))

    @pytest.mark.parametrize("value", [0.5, "0.5", "1e3"])
    def test_limits_pids_rejects(self, value: object) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"deploy\.resources\.limits\.pids"):
            validate_deploy("app", _svc({"resources": {"limits": {"pids": value}}}))

    @pytest.mark.parametrize("value", [100, "100", 100.0, -1])
    def test_limits_pids_accepts(self, value: object) -> None:
        validate_deploy("app", _svc({"resources": {"limits": {"pids": value}}}))

    @pytest.mark.parametrize("value", ["", "somevalue", 512, 1.5])
    def test_reservations_memory_rejects(self, value: object) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"deploy\.resources\.reservations\.memory"):
            validate_deploy("app", _svc({"resources": {"reservations": {"memory": value}}}))

    @pytest.mark.parametrize("value", ["512", "512m", "1.5", "1e3"])
    def test_reservations_memory_accepts(self, value: object) -> None:
        validate_deploy("app", _svc({"resources": {"reservations": {"memory": value}}}))

    def test_memory_conflict_rejected(self) -> None:
        deploy = {"resources": {"limits": {"memory": "256m"}}}
        with pytest.raises(UnsupportedComposeError, match=r"'mem_limit' conflicts with deploy.resources.limits.memory"):
            validate_deploy("app", _svc(deploy, mem_limit="512m"))

    def test_cpus_conflict_rejected(self) -> None:
        deploy = {"resources": {"limits": {"cpus": "1"}}}
        with pytest.raises(UnsupportedComposeError, match=r"'cpus' conflicts with deploy.resources.limits.cpus"):
            validate_deploy("app", _svc(deploy, cpus="2"))

    def test_pids_conflict_rejected(self) -> None:
        deploy = {"resources": {"limits": {"pids": 50}}}
        with pytest.raises(UnsupportedComposeError, match=r"'pids_limit' conflicts with deploy.resources.limits.pids"):
            validate_deploy("app", _svc(deploy, pids_limit=100))

    def test_reservation_memory_conflict_rejected(self) -> None:
        deploy = {"resources": {"reservations": {"memory": "128m"}}}
        match = r"'mem_reservation' conflicts with deploy.resources.reservations.memory"
        with pytest.raises(UnsupportedComposeError, match=match):
            validate_deploy("app", _svc(deploy, mem_reservation="256m"))

    def test_null_limits_is_refused(self) -> None:
        # Used to emit no --memory/--cpus at all: the limits silently did not exist.
        with pytest.raises(UnsupportedComposeError, match=r"'deploy.resources.limits' must not be null"):
            validate_deploy("app", _svc({"resources": {"limits": None}}))

    def test_null_reservations_is_refused(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"'deploy.resources.reservations' must not be null"):
            validate_deploy("app", _svc({"resources": {"reservations": None}}))

    def test_deploy_mixed_type_unknown_keys_do_not_crash_raw(self) -> None:
        # sorted(unknown) used to crash raw (TypeError: '<' not supported
        # between instances of 'str' and 'int') once 'deploy's unrecognized
        # keys mixed a non-string key with a string one.
        with pytest.raises(UnsupportedComposeError, match=r"service 'app': deploy: key 1 must be a string"):
            validate_deploy("app", _svc({"resources": {}, 1: "x", "y": "z"}))

    def test_resources_mixed_type_unknown_keys_do_not_crash_raw(self) -> None:
        with pytest.raises(UnsupportedComposeError, match=r"service 'app': deploy\.resources: key 1 must be a string"):
            validate_deploy("app", _svc({"resources": {1: "x", "y": "z"}}))

    def test_limits_mixed_type_unknown_keys_do_not_crash_raw(self) -> None:
        with pytest.raises(
            UnsupportedComposeError, match=r"service 'app': deploy\.resources\.limits: key 1 must be a string"
        ):
            validate_deploy("app", _svc({"resources": {"limits": {1: "x", "y": "z"}}}))

    def test_reservations_mixed_type_unknown_keys_do_not_crash_raw(self) -> None:
        with pytest.raises(
            UnsupportedComposeError, match=r"service 'app': deploy\.resources\.reservations: key 1 must be a string"
        ):
            validate_deploy("app", _svc({"resources": {"reservations": {1: "x", "y": "z"}}}))


class TestDeployResourceFlags:
    def test_no_deploy_no_flags(self) -> None:
        assert deploy_resource_flags({"image": "x"}) == []

    def test_limits_emitted(self) -> None:
        deploy = {"resources": {"limits": {"cpus": "0.5", "memory": "256m", "pids": 100}}}
        assert deploy_resource_flags(_svc(deploy)) == [
            "--cpus",
            Expand(value="0.5"),
            "--memory",
            Expand(value="256m"),
            "--pids-limit",
            Expand(value="100"),
        ]

    def test_reservation_memory_emitted(self) -> None:
        deploy = {"resources": {"reservations": {"memory": "128m"}}}
        assert deploy_resource_flags(_svc(deploy)) == ["--memory-reservation", Expand(value="128m")]
