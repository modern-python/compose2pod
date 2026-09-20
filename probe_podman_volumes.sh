#!/bin/sh
# THROWAWAY -- measurement for issue #105, not for merge.
#
# Records what real podman does with the -v specs compose2pod would emit for
# the drive-adjacent spellings `docker compose config` accepts. The ruling in
# #105 (legitimate rule-two refusal vs. accept-and-defer) turns on these exit
# codes, and no machine in the loop has podman except this CI job.
set -u

IMAGE=docker.io/library/alpine:3
podman pull -q "$IMAGE" || exit 1

mkdir -p /tmp/probe-src '/tmp/C:\data'

probe() {
  label=$1
  spec=$2
  output=$(podman run --rm -v "$spec" "$IMAGE" true 2>&1)
  code=$?
  printf '%-22s spec=[%s]\n' "$label" "$spec"
  printf '%-22s exit=%s %s\n\n' "" "$code" "$(printf '%s' "$output" | tr '\n' ' ' | cut -c1-200)"
}

echo "=== podman version ==="
podman --version
echo
echo "=== volume spec probes ==="

# The form 0.4.4 now accepts (#104): does podman express it at all?
probe accepted_backslash 'C:\data:/var'
probe accepted_forward 'C:/data:/var'

# The three residuals catalogued in #105.
probe no_target 'C:\data'
probe drive_relative 'C:data:/var'
probe drive_relative_abs "$PWD/C:data:/var"
probe single_letter 'a:/var'

# Does a colon inside an absolute source survive podman's own split?
probe abs_source_with_colon '/tmp/C:\data:/var'

# Controls: the two shapes compose2pod emits every day.
probe control_bind '/tmp/probe-src:/var'
probe control_named 'probevol:/var'

echo "=== volumes podman created along the way ==="
podman volume ls
