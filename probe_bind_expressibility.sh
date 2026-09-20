#!/bin/sh
# THROWAWAY -- measurement for issue #109 phase 2, not for merge.
#
# The four rows in tests/integration/refusals.py are anonymous volumes whose
# target is not absolute: nothing in podman can mount those. The drive-qualified
# *bind* spellings are the open question. `docker compose config` reads
# `C:\data:/var` as a bind with source `C:\data`, and on Linux that is an
# ordinary relative filename -- `--mount` does not re-split it on colons the way
# `-v` does. If podman mounts it, ADR-0006's "podman cannot mount it either way"
# is too strong and #108's bind half is a tracked limitation, not rule two.
set -u

IMAGE=docker.io/library/busybox:1.36
podman pull -q "$IMAGE" || exit 1

mkdir -p './C:\data' "$PWD/C:data"

probe() {
  label=$1
  shift
  output=$(podman run --rm "$@" "$IMAGE" true 2>&1)
  code=$?
  printf '%-28s argv=[%s]\n' "$label" "$*"
  printf '%-28s exit=%s %s\n\n' "" "$code" "$(printf '%s' "$output" | tr '\n' ' ' | cut -c1-200)"
}

echo "=== podman version ==="
podman --version
echo
echo "=== bind expressibility (the #108 bind half) ==="

# Docker's reading of `C:\data:/var` -- a bind whose source is a relative filename.
probe bind_relative_backslash --mount 'type=bind,src=./C:\data,dst=/var'
probe bind_relative_bare      --mount 'type=bind,src=C:\data,dst=/var'
# Docker's reading of `C:data:/var` -- it resolves the source against project_dir.
probe bind_absolute_colon     --mount "type=bind,src=$PWD/C:data,dst=/var"
# Same absolute source through the short form, for contrast with the #105 probe.
probe bind_absolute_short     -v "$PWD/C:data:/var"

echo "=== anonymous-volume control spellings ==="
probe anon_mount_no_source    --mount 'type=volume,dst=/data'
probe anon_short_form         -v '/data'
