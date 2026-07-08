"""Command-line interface: read a compose document and emit the pod script."""

import argparse
import contextlib
import json
import os
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from compose2pod.emit import EmitOptions, emit_script
from compose2pod.exceptions import UnsupportedComposeError
from compose2pod.interpolate import interpolate
from compose2pod.parsing import validate


_yaml: ModuleType | None = None
with contextlib.suppress(ImportError):  # the optional [yaml] extra is not installed
    import yaml as _yaml


POD_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _load_yaml(text: str) -> Any:  # noqa: ANN401 - returns arbitrary parsed compose data
    if _yaml is None:
        msg = "YAML input requires the 'yaml' extra: pip install compose2pod[yaml] (or pipe JSON via yq)"
        raise UnsupportedComposeError(msg)
    try:
        return _yaml.safe_load(text)
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
    if not POD_NAME_PATTERN.match(args.pod_name):
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
    try:
        compose, interpolation_warnings = interpolate(compose, os.environ)
        warnings = [*interpolation_warnings, *validate(compose)]
        script = emit_script(
            compose=compose,
            options=EmitOptions(
                target=args.target,
                ci_image=args.image,
                command=args.command,
                pod=args.pod_name,
                project_dir=args.project_dir,
                artifacts=args.artifact,
                allow_exit_codes=args.allow_exit_code,
            ),
        )
    except UnsupportedComposeError as error:
        sys.stderr.write(f"compose2pod: error: {error}\n")
        return 2
    for warning in warnings:
        sys.stderr.write(f"compose2pod: {warning}\n")
    sys.stdout.write(script)
    return 0
