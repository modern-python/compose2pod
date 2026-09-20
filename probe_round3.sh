#!/bin/sh
# Throwaway probe, deleted before merge. Rounds for #116 and #118.
set -u
IMG=busybox:1.36
podman pull -q "$IMG" >/dev/null 2>&1

run() {  # run <label> <argv...>
  label=$1; shift
  out=$(podman run --rm "$@" 2>&1); rc=$?
  printf '%-52s exit=%s %s\n' "$label" "$rc" "$(echo "$out" | tr '\n' ' ' | cut -c1-120)"
}

echo "=================== podman version ==================="
podman --version

echo
echo "=== A. nocopy: is it parsed at all? (-v short form) ==="
podman volume rm -f ncA >/dev/null 2>&1; podman volume create ncA >/dev/null
run "-v ncA:/etc:nocopy" -v ncA:/etc:nocopy "$IMG" true
podman volume rm -f ncB >/dev/null 2>&1; podman volume create ncB >/dev/null
run "-v ncB:/etc:nosuchoption" -v ncB:/etc:nosuchoption "$IMG" true

echo
echo "=== A. nocopy: copy-up, counted exactly ==="
echo "image /etc entry count (no volume):"
podman run --rm "$IMG" sh -c 'ls -1A /etc | wc -l'
for opt in "" ":nocopy"; do
  vol="ncv$(echo "$opt" | tr -d ':')x"
  podman volume rm -f "$vol" >/dev/null 2>&1; podman volume create "$vol" >/dev/null
  seen=$(podman run --rm -v "${vol}:/etc${opt}" "$IMG" sh -c 'ls -1A /etc | wc -l' 2>&1)
  # read the volume back through a SECOND container, at a different path, with no options:
  ondisk=$(podman run --rm -v "${vol}:/mnt" "$IMG" sh -c 'ls -1A /mnt | wc -l' 2>&1)
  printf '  -v vol:/etc%-10s  container sees=%-6s volume on disk afterwards=%s\n' "$opt" "$seen" "$ondisk"
done

echo
echo "=== B. reservations: which flags exist on podman run? ==="
echo "flags matching reserv:"; podman run --help | grep -iE '^\s+--\S*reserv' || echo "  (none)"
echo "flags matching gpu:";    podman run --help | grep -iE '^\s+--\S*gpu'    || echo "  (none)"
echo "flags matching device:"; podman run --help | grep -iE '^\s+--\S*device' || echo "  (none)"
for f in --cpu-reservation --cpus-reservation --device-reservation --gpus; do
  run "$f 1" "$f" 1 "$IMG" true
done

echo
echo "=== C. acceptance side (#118): every nested option we emit ==="
D=$(mktemp -d); echo hi > "$D/f"
run "bind relabel=shared"        --mount "type=bind,src=$D,dst=/data,relabel=shared"    "$IMG" true
run "bind relabel=private"       --mount "type=bind,src=$D,dst=/data,relabel=private"   "$IMG" true
for p in private rprivate shared rshared slave rslave; do
  run "bind bind-propagation=$p" --mount "type=bind,src=$D,dst=/data,bind-propagation=$p" "$IMG" true
done
run "tmpfs tmpfs-size=1m"        --mount "type=tmpfs,dst=/data,tmpfs-size=1m"           "$IMG" true
run "tmpfs tmpfs-size=1000"      --mount "type=tmpfs,dst=/data,tmpfs-size=1000"         "$IMG" true
run "tmpfs tmpfs-mode=1777"      --mount "type=tmpfs,dst=/data,tmpfs-mode=1777"         "$IMG" true
run "tmpfs size+mode"            --mount "type=tmpfs,dst=/data,tmpfs-size=1m,tmpfs-mode=1777" "$IMG" true
run "--tmpfs /data"              --tmpfs /data                                          "$IMG" true
run "bind ro"                    --mount "type=bind,src=$D,dst=/data,ro"                "$IMG" true
run "volume ro"                  --mount "type=volume,dst=/data,ro"                     "$IMG" true
echo "done."
