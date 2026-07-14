"""Command-line interface: read a compose document and emit the pod script."""

import argparse
import contextlib
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from compose2pod.emit import POD_NAME_PATTERN, EmitOptions, emit_script, referenced_variables
from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.extends import resolve_extends
from compose2pod.parsing import validate


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


def _read_compose(text: str, fmt: str) -> Any:  # noqa: ANN401 - returns arbitrary parsed compose data
    if fmt == "json":
        return json.loads(text)
    if fmt == "yaml":
        return _load_yaml(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return _load_yaml(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="compose2pod",
        description="Convert a Docker Compose document to a podman-pod run script (stdout).",
    )
    parser.add_argument("file", nargs="?", help="compose file to read (default: stdin)")
    parser.add_argument("--target", required=True, help="service to run in the foreground with --command")
    parser.add_argument("--image", required=True, help="CI image replacing services that have a build section")
    parser.add_argument("--project-dir", default=".", help="host path relative volume/env_file sources resolve to")
    parser.add_argument("--command", default="", help="shell command overriding the target service command")
    parser.add_argument("--pod-name", default="test-pod")
    parser.add_argument("--format", choices=("auto", "json", "yaml"), default="auto")
    parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        metavar="SRC:DST",
        help="file to podman-cp out of the target container after it exits",
    )
    parser.add_argument(
        "--allow-exit-code",
        type=int,
        action="append",
        default=[],
        help="target exit code treated as success in addition to 0",
    )
    args = parser.parse_args(argv)
    if not POD_NAME_PATTERN.fullmatch(args.pod_name):
        sys.stderr.write(f"compose2pod: error: invalid pod name {args.pod_name!r}\n")
        return 2
    if args.file:
        try:
            text = Path(args.file).read_text()
        except OSError as error:
            sys.stderr.write(f"compose2pod: error: could not read file: {error}\n")
            return 2
    else:
        text = sys.stdin.read()
    try:
        compose = _read_compose(text, args.format)
    except (json.JSONDecodeError, UnsupportedComposeError) as error:
        sys.stderr.write(f"compose2pod: error: could not parse compose input: {error}\n")
        return 2
    options = EmitOptions(
        target=args.target,
        ci_image=args.image,
        command=args.command,
        pod=args.pod_name,
        project_dir=args.project_dir,
        artifacts=args.artifact,
        allow_exit_codes=args.allow_exit_code,
    )
    try:
        compose = resolve_extends(compose)
        warnings = validate(compose)
        script = emit_script(compose=compose, options=options)
    except UnsupportedComposeError as error:
        sys.stderr.write(f"compose2pod: error: {error}\n")
        return 2
    referenced = referenced_variables(compose, options)
    if referenced:
        sys.stderr.write("compose2pod: note: script references variables at run time: " + ", ".join(referenced) + "\n")
    for warning in warnings:
        sys.stderr.write(f"compose2pod: {warning}\n")
    sys.stdout.write(script)
    return 0
