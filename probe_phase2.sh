#!/bin/sh
# THROWAWAY -- measurement for issue #109 phase 2, not for merge.
# Round 2: semantics, not just exit codes. Round 1 showed three sites where the
# exit code alone would have led to the wrong ruling.
set -u

IMAGE=docker.io/library/busybox:1.36
podman pull -q "$IMAGE" || exit 1
echo "=== podman version ==="; podman --version; echo

echo "=== A. is subpath supported anywhere, or only on image mounts? ==="
for spec in \
  "type=image,source=$IMAGE,target=/mnt,subpath=/bin" \
  "type=volume,source=spvol,target=/mnt,subpath=/sub" \
  "type=bind,source=/tmp,target=/mnt,subpath=/sub" ; do
  out=$(podman run --rm --mount "$spec" "$IMAGE" true 2>&1); code=$?
  printf '  %-58s exit=%s %s\n' "$spec" "$code" "$(printf '%s' "$out" | tr '\n' ' ' | cut -c1-90)"
done

echo
echo "=== B. does -v :nocopy actually suppress the image copy-up? ==="
# Without nocopy, a fresh named volume is seeded from the image's /etc.
podman volume rm -f nc_plain nc_nocopy >/dev/null 2>&1
podman run --rm -v nc_plain:/etc "$IMAGE" true
printf '  plain  entries in volume: %s\n' "$(podman run --rm -v nc_plain:/mnt "$IMAGE" sh -c 'ls -1 /mnt | wc -l')"
podman run --rm -v nc_nocopy:/etc:nocopy "$IMAGE" true
printf '  nocopy entries in volume: %s\n' "$(podman run --rm -v nc_nocopy:/mnt "$IMAGE" sh -c 'ls -1 /mnt | wc -l')"
printf '  (equal counts => podman accepted the option and ignored it)\n'
podman volume rm -f nc_plain nc_nocopy >/dev/null 2>&1

echo
echo "=== C. does --network survive joining a pod? ==="
podman pod rm -f netpod >/dev/null 2>&1
podman pod create --name netpod >/dev/null 2>&1
printf '  pod default   : %s\n' "$(podman run --rm --pod netpod "$IMAGE" sh -c 'ip -o -4 addr | tr -s " " | cut -d" " -f2,4 | tr "\n" " "')"
printf '  pod + host    : %s\n' "$(podman run --rm --pod netpod --network host "$IMAGE" sh -c 'ip -o -4 addr | tr -s " " | cut -d" " -f2,4 | tr "\n" " "')"
printf '  pod + none    : %s\n' "$(podman run --rm --pod netpod --network none "$IMAGE" sh -c 'ip -o -4 addr | tr -s " " | cut -d" " -f2,4 | tr "\n" " "')"
podman pod rm -f netpod >/dev/null 2>&1
printf '  no pod, host  : %s\n' "$(podman run --rm --network host "$IMAGE" sh -c 'ip -o -4 addr | tr -s " " | cut -d" " -f2,4 | tr "\n" " "' | cut -c1-120)"
printf '  (identical to pod default => --network is ignored inside a pod)\n'

echo
echo "=== D. create_host_path: does any podman spelling create a missing source? ==="
rm -rf /tmp/chp && mkdir -p /tmp/chp
for spec in "-v /tmp/chp/a:/var" "--mount type=bind,src=/tmp/chp/b,dst=/var"; do
  out=$(podman run --rm $spec "$IMAGE" true 2>&1); code=$?
  printf '  %-46s exit=%s %s\n' "$spec" "$code" "$(printf '%s' "$out" | tr '\n' ' ' | cut -c1-80)"
done
printf '  created: [%s]\n' "$(ls /tmp/chp | tr '\n' ' ')"
