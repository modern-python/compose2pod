from compose2pod.keys import SERVICE_KEYS, STRUCTURAL_KEYS
from compose2pod.parsing import SUPPORTED_SERVICE_KEYS


def test_registry_and_structural_keys_are_disjoint() -> None:
    assert not (set(SERVICE_KEYS) & STRUCTURAL_KEYS)


def test_supported_service_keys_snapshot() -> None:
    assert {
        "image",
        "build",
        "command",
        "entrypoint",
        "environment",
        "env_file",
        "volumes",
        "tmpfs",
        "healthcheck",
        "depends_on",
        "networks",
        "hostname",
        "container_name",
        "secrets",
        "configs",
        "user",
        "working_dir",
        "platform",
        "init",
        "read_only",
        "privileged",
        "group_add",
        "cap_add",
        "cap_drop",
        "security_opt",
        "devices",
        "labels",
        "annotations",
        "extra_hosts",
        "pull_policy",
        "ulimits",
        "mem_limit",
        "memswap_limit",
        "mem_reservation",
        "mem_swappiness",
        "cpus",
        "cpu_shares",
        "cpu_quota",
        "cpu_period",
        "cpuset",
        "pids_limit",
        "shm_size",
        "oom_score_adj",
        "oom_kill_disable",
    } == SUPPORTED_SERVICE_KEYS
