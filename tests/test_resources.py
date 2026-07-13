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
        with pytest.raises(UnsupportedComposeError, match="must be a number or string"):
            validate_deploy("app", _svc({"resources": {"limits": {"memory": True}}}))

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

    def test_null_limits_is_noop(self) -> None:
        validate_deploy("app", _svc({"resources": {"limits": None}}))

    def test_null_reservations_is_noop(self) -> None:
        validate_deploy("app", _svc({"resources": {"reservations": None}}))


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
