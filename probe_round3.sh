#!/bin/sh
# Throwaway probe, round 2. Deleted before merge.
set -u
IMG=busybox:1.36
podman pull -q "$IMG" >/dev/null 2>&1

echo "=== A2. nocopy: WHICH entries land in the volume ==="
for opt in "" ":nocopy"; do
  vol="v2$(echo "$opt" | tr -d ':')"
  podman volume rm -f "$vol" >/dev/null 2>&1; podman volume create "$vol" >/dev/null
  podman run --rm -v "${vol}:/etc${opt}" "$IMG" true >/dev/null 2>&1
  printf 'mounted at /etc%-8s -> volume contains: %s\n' "$opt" \
    "$(podman run --rm -v "${vol}:/mnt" "$IMG" sh -c 'ls -1A /mnt | tr "\n" " "')"
done

echo
echo "=== A2. candidate target dirs with content (not needed by the shell) ==="
podman run --rm "$IMG" sh -c 'for d in /usr/sbin /var/spool/mail /home /root /var/www; do printf "%-18s %s\n" "$d" "$(ls -1A $d 2>/dev/null | wc -l)"; done'

echo
echo "=== A2. copy-up at /usr/sbin, away from podman's own /etc files ==="
for opt in "" ":nocopy"; do
  vol="v3$(echo "$opt" | tr -d ':')"
  podman volume rm -f "$vol" >/dev/null 2>&1; podman volume create "$vol" >/dev/null
  seen=$(podman run --rm -v "${vol}:/usr/sbin${opt}" "$IMG" sh -c 'ls -1A /usr/sbin | wc -l' 2>&1)
  after=$(podman run --rm -v "${vol}:/mnt" "$IMG" sh -c 'ls -1A /mnt | wc -l' 2>&1)
  printf '  -v vol:/usr/sbin%-8s container sees=%-5s volume afterwards=%s\n' "$opt" "$seen" "$after"
done

echo
echo "=== B2. --gpus on 4.9.3: hidden, and does it do anything? ==="
echo "grep -i gpu over the whole help text:"; podman run --help | grep -i gpu || echo "  (absent from --help)"
for v in all 1 nonsense ""; do
  out=$(podman run --rm --gpus "$v" "$IMG" true 2>&1); rc=$?
  printf '  --gpus %-10s exit=%s %s\n' "'$v'" "$rc" "$(echo "$out" | tr '\n' ' ' | cut -c1-90)"
done
echo "does --gpus all add any device? (nvidia entries in /dev):"
podman run --rm --gpus all "$IMG" sh -c 'ls /dev | grep -ci nvidia'
echo "man page mention:"; (podman run --help 2>&1; man podman-run 2>/dev/null) | grep -ci gpus
echo "done."
