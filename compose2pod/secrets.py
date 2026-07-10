"""The secret StoreKind: a podman secret mounted at /run/secrets/<name>."""

from compose2pod.store import StoreKind


SECRET = StoreKind(
    label="secret",
    top_key="secrets",
    prefix="",
    sources=frozenset({"file", "environment"}),
    default_target=lambda name: name,
    require_absolute_target=False,
)
