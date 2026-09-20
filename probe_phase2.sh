#!/bin/sh
# THROWAWAY -- measurement for issue #109 phase 2, not for merge.
#
# One counterfactual per documented rule-two refusal site, so the Refusal rows
# are authored from podman's real verdicts instead of from what its docs imply.
set -u

IMAGE=docker.io/library/busybox:1.36
podman pull -q "$IMAGE" || exit 1

probe() {
  label=$1
  shift
  output=$(podman run --rm "$@" "$IMAGE" true 2>&1)
  code=$?
  printf '%-32s argv=[%s]\n' "$label" "$*"
  printf '%-32s exit=%s %s\n\n' "" "$code" "$(printf '%s' "$output" | tr '\n' ' ' | cut -c1-180)"
}

echo "=== podman version ==="
podman --version
echo

echo "=== long-form target must be absolute (parsing.py:279) ==="
probe target_relative      --mount 'type=volume,dst=rel'
probe target_control       --mount 'type=volume,dst=/data'

echo "=== image subpath must be absolute (parsing.py:361) ==="
probe subpath_relative     --mount "type=image,source=$IMAGE,target=/mnt,subpath=bin"
probe subpath_control      --mount "type=image,source=$IMAGE,target=/mnt,subpath=/bin"

echo "=== cluster / npipe long-form types (parsing.py:266) ==="
probe type_cluster         --mount 'type=cluster,dst=/data'
probe type_npipe           --mount 'type=npipe,dst=/data'

echo "=== volume nocopy (parsing.py:318) ==="
probe nocopy_bare          --mount 'type=volume,source=ncvol1,dst=/data,nocopy'
probe nocopy_true          --mount 'type=volume,source=ncvol2,dst=/data,nocopy=true'
probe nocopy_short         -v 'ncvol3:/data:nocopy'

echo "=== bind create_host_path (parsing.py:300) ==="
mkdir -p "$PWD/chp"
probe chp_false_missing     --mount "type=bind,src=$PWD/chp/absent-a,dst=/var"
probe chp_true_missing      -v "$PWD/chp/absent-b:/var"
ls -la "$PWD/chp" | tail -n +2

echo
echo "=== network_mode inside a pod (parsing.py:782, generic path) ==="
podman pod rm -f probepod >/dev/null 2>&1
podman pod create --name probepod >/dev/null 2>&1 && echo "pod created"
for netmode in host bridge none; do
  output=$(podman run --rm --pod probepod --network "$netmode" "$IMAGE" true 2>&1)
  printf '%-32s exit=%s %s\n' "netmode_$netmode" "$?" "$(printf '%s' "$output" | tr '\n' ' ' | cut -c1-180)"
done
podman pod rm -f probepod >/dev/null 2>&1

echo
echo "=== deploy.resources.reservations (resources.py:64) ==="
echo "-- any run flag mentioning reservation/reserve:"
podman run --help | grep -i "reserv" || echo "   (none)"
echo "-- any run flag mentioning device:"
podman run --help | grep -i -- "--device" | head -5
probe reservation_flag     --cpu-reservation 1
