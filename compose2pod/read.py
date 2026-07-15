"""Read a compose document from text (JSON or YAML) the way Docker reads it."""

import contextlib
import json
import re
from types import ModuleType
from typing import Any

from compose2pod.exceptions import UnsupportedComposeError


_yaml: ModuleType | None = None
with contextlib.suppress(ImportError):  # the optional [yaml] extra is not installed
    import yaml as _yaml


# YAML 1.2's boolean set: `true`/`false` only. PyYAML implements YAML *1.1*,
# where a bare `on`/`off`/`yes`/`no` is also a boolean -- but Docker's parser is
# YAML 1.2, so to `docker compose` each of those is an ordinary string. The
# difference is not cosmetic: `SSL: on` reaches the container as `SSL=true`
# instead of `SSL=on`, and `on:` used as a *key* resolves to the bool `True` and
# is then refused by the string-key rule -- rejecting a file Docker runs.
#
# It can only be fixed here. Once PyYAML has resolved `on` to `True`, the
# spelling is gone and no downstream pass can recover it.
_YAML_12_BOOL = r"^(?:true|True|TRUE|false|False|FALSE)$"

# YAML 1.2's core-schema float: a dotted mantissa with an optional exponent, OR
# an undotted mantissa with a *mandatory* exponent, OR one of the two named
# constants. PyYAML implements YAML *1.1*, whose float grammar requires a dot
# unconditionally -- a bare `1e3` has none, so PyYAML leaves it the string
# "1e3". Docker's parser is YAML 1.2, where the exponent alone is enough, so
# to Docker `1e3` is the float 1000.0. That is not cosmetic: `cpuset: 1e3` is
# a *string* to compose2pod today, so it slides past the "must be a string"
# rule Docker enforces on the float it sees -- accepting a document Docker
# refuses. The bare-mantissa branch is deliberately narrower than PyYAML's own
# (no digit-grouping underscore, no sexagesimal `:`) because YAML 1.2's core
# schema has neither; it must also never swallow a plain integer (`123`), so
# an exponent or a dot is required in every branch, never both optional at once.
_YAML_12_FLOAT = (
    r"^[-+]?(?:[0-9]+\.[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?$"
    r"|^[-+]?[0-9]+[eE][-+]?[0-9]+$"
    r"|^[-+]?\.(?:inf|Inf|INF)$"
    r"|^\.(?:nan|NaN|NAN)$"
)


def _build_yaml_loader(yaml_module: ModuleType) -> type:
    """Build a SafeLoader that resolves booleans and floats the way YAML 1.2 (and so Docker) does."""

    class Loader(yaml_module.SafeLoader):
        pass

    # Drop PyYAML's YAML 1.1 bool and float resolvers, then install the 1.2 ones.
    # Rebuilding the table is what removes `on`/`off`/`yes`/`no` from the boolean
    # set and a bare `1e3` from the string set; they fall through to (respectively)
    # the plain-string and float resolvers.
    Loader.yaml_implicit_resolvers = {
        first_char: [
            (tag, regexp)
            for tag, regexp in resolvers
            if tag not in {"tag:yaml.org,2002:bool", "tag:yaml.org,2002:float"}
        ]
        for first_char, resolvers in yaml_module.SafeLoader.yaml_implicit_resolvers.items()
    }
    Loader.add_implicit_resolver("tag:yaml.org,2002:bool", re.compile(_YAML_12_BOOL), list("tTfF"))
    Loader.add_implicit_resolver("tag:yaml.org,2002:float", re.compile(_YAML_12_FLOAT), list("-+0123456789."))
    return Loader


def _load_yaml(text: str) -> Any:  # noqa: ANN401 - returns arbitrary parsed compose data
    if _yaml is None:
        msg = "YAML input requires the 'yaml' extra: pip install compose2pod[yaml] (or pipe JSON via yq)"
        raise UnsupportedComposeError(msg)
    try:
        return _yaml.load(text, Loader=_build_yaml_loader(_yaml))  # noqa: S506 - SafeLoader subclass, not full load
    except _yaml.YAMLError as error:
        msg = f"invalid YAML: {error}"
        raise UnsupportedComposeError(msg) from error


def _load_json(text: str) -> Any:  # noqa: ANN401 - returns arbitrary parsed compose data
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        msg = f"invalid JSON: {error}"
        raise UnsupportedComposeError(msg) from error


def read(text: str, fmt: str) -> Any:  # noqa: ANN401 - returns arbitrary parsed compose data
    """Read a compose document from `text`. `fmt` is 'json', 'yaml', or 'auto' (JSON, then YAML)."""
    if fmt == "json":
        return _load_json(text)
    if fmt == "yaml":
        return _load_yaml(text)
    try:
        return _load_json(text)
    except UnsupportedComposeError:
        return _load_yaml(text)
