"""The config StoreKind: a podman secret mounted at the container-root path /<name>."""

from compose2pod.store import StoreKind


CONFIG = StoreKind(
    label="config",
    top_key="configs",
    prefix="config-",
    sources=frozenset({"file", "environment", "content"}),
    default_target=lambda name: f"/{name}",
    require_absolute_target=True,
)
