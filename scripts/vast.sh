#!/usr/bin/env bash
# Rent, reach and destroy the ThaiDub GPU box on Vast.ai. Needs: uv tool install vastai; vastai set api-key <key>.
# The box is created from the owner's private template "thaidub-gpu" (image, On-start script, env, disk live there).
set -euo pipefail

VAST_TEMPLATE_HASH="${VAST_TEMPLATE_HASH:-}"
VAST_GEO="${VAST_GEO:-TW}"
STATE=".vast-instance"
REMOTE_W=/workspace
# Owner's machine filter (template "thaidub-gpu"). CUDA >= 12.8: the backend's torch cu128 wheels need driver >= 570.
CUDA_MIN=12.8; [ "${ENABLE_HUNYUAN:-0}" = "1" ] && CUDA_MIN=13.0  # vLLM 0.31.0 PyPI wheels are CUDA 13 (driver >= 580)
QUERY="verified=True rentable=True reliability>=0.99 cuda_vers>=$CUDA_MIN disk_space>=100 gpu_name=RTX_3090 num_gpus=1 geolocation in [$VAST_GEO]"

die() { echo "error: $*" >&2; exit 1; }
need_cli() { command -v vastai >/dev/null || die "vastai not installed: pip install vastai (then: vastai set api-key <key>)"; }
instance_id() { [ -s "$STATE" ] || die "no $STATE: run 'vast.sh up' first"; cat "$STATE"; }
json() { python3 -c "import sys,json;d=json.load(sys.stdin);$1"; } # stdin JSON -> python expr using d

# Prints "host port" of the saved instance (empty until Vast assigns ssh).
ssh_target() {
  vastai show instance "$(instance_id)" --raw | json 'print(d.get("ssh_host") or "", d.get("ssh_port") or "")'
}
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ConnectTimeout=10)
ssh_run() { # host port [remote command...]  (ssh options must precede the destination)
  local host=$1 port=$2; shift 2
  ssh "${SSH_OPTS[@]}" -p "$port" "root@$host" "$@"
}
with_target() { # fn: runs "$fn host port"
  read -r host port < <(ssh_target)
  [ -n "${host:-}" ] && [ -n "${port:-}" ] || die "instance has no ssh host/port yet (still starting?)"
  "$1" "$host" "$port"
}

cmd_offers() {
  vastai search offers -n "$QUERY" -o dph --raw | json '
rows=d[:10]
h="id $/h gpu cuda drv cores ram_gb disk_gb down/up rel geo".split()
print("%-9s %-6s %-9s %-5s %-10s %-5s %-6s %-7s %-9s %-6s %s"%tuple(h))
for o in rows:
    print("%-9s %-6.3f %-9s %-5s %-10s %-5.1f %-6.0f %-7.0f %-4.0f/%-4.0f %-6.3f %s"%(o["id"],o["dph_total"],o["gpu_name"],o.get("cuda_max_good"),o.get("driver_version"),o["cpu_cores_effective"],o["cpu_ram"]/1024,o["disk_space"],o["inet_down"],o["inet_up"],o["reliability2"] if "reliability2" in o else o.get("reliability",0),o.get("geolocation")))
if not rows: print("no offer matches the minimum spec")'
}

wait_running() { # id
  local end=$((SECONDS + 900))
  until [ "$(vastai show instance "$1" --raw | json 'print(d.get("actual_status"))')" = running ]; do
    [ $SECONDS -lt $end ] || die "instance $1 not running after 15 min (see: vastai show instance $1)"
    sleep 10
  done
}

wait_ready() { # host port
  local end=$((SECONDS + 2400)) line
  until ssh_run "$1" "$2" 'grep -q ^READY /workspace/bootstrap.log' 2>/dev/null; do
    [ $SECONDS -lt $end ] || die "not READY after 40 min (see: vast.sh logs)"
    line=$(ssh_run "$1" "$2" 'tail -n1 /workspace/bootstrap.log' 2>/dev/null || echo "(ssh not up yet)")
    echo "  $line"
    sleep 15
  done
}

cmd_up() {
  local id=${1:-}
  [ -n "$id" ] || id=$(vastai search offers -n "$QUERY" -o dph --raw | json 'print(d[0]["id"] if d else "")')
  [ -n "$id" ] || die "no offer matches the minimum spec (see: vast.sh offers)"
  local t0=$SECONDS out iid
  [ -n "$VAST_TEMPLATE_HASH" ] || die "set VAST_TEMPLATE_HASH in .env (the hash of your thaidub-gpu template)"
  out=$(vastai create instance "$id" --template_hash "$VAST_TEMPLATE_HASH" --disk 100 --label thaidub-gpu --raw)
  iid=$(printf '%s' "$out" | json 'print(d.get("new_contract") or "")') || true
  [ -n "$iid" ] || die "create failed: $out"
  echo "$iid" >"$STATE"
  echo "created instance $iid from offer $id (template thaidub-gpu)"
  wait_running "$iid"
  local t1=$SECONDS
  echo "running after $((t1 - t0))s"
  with_target upload_voices
  with_target wait_ready
  echo "READY: create->running $((t1 - t0))s, running->READY $((SECONDS - t1))s"
}

cmd_ssh() { with_target _ssh_shell; }
_ssh_shell() { ssh_run "$1" "$2"; }

cmd_tunnel() {
  echo "ThaiDub base URL: http://localhost:4000/v1 (Ctrl-C closes the tunnel)"
  with_target _tunnel
}
_tunnel() { ssh "${SSH_OPTS[@]}" -N -L 4000:127.0.0.1:4000 -L 8001:127.0.0.1:8001 -p "$2" "root@$1"; }

# The voice bank never goes into git or the image (real dubbed voices): copy the local voices/ dir to the box.
cmd_voices() { with_target upload_voices; }
upload_voices() { # host port; retries while sshd is still coming up
  [ -s voices/manifest.sha256 ] || die "voices/manifest.sha256 missing (run: make voices)"
  local tries=0
  until rsync -az -e "ssh ${SSH_OPTS[*]} -p $2" voices/ "root@$1:$REMOTE_W/voices/"; do
    tries=$((tries + 1)); [ $tries -lt 20 ] || die "voice upload failed (is ssh up? try: vast.sh voices)"
    sleep 15
  done
  echo "voice bank uploaded ($(grep -c . voices/manifest.sha256) files in manifest)"
}

cmd_logs() { with_target _logs; }
_logs() { ssh_run "$1" "$2" 'tail -f /workspace/bootstrap.log'; }

cmd_down() {
  local id answer; id=$(instance_id)
  echo "instance $id, label: $(vastai show instance "$id" --raw | json 'print(d.get("label"))')"
  read -r -p "type the instance id to destroy: " answer
  [ "$answer" = "$id" ] || die "id did not match; nothing destroyed"
  vastai destroy instance "$id" -y
  rm -f "$STATE"
}


usage() {
  cat <<'MSG'
usage: scripts/vast.sh <command>
  offers          list the 10 cheapest offers that meet the minimum spec
  up [offer_id]   rent (cheapest offer if no id), wait for running, then READY
  ssh             shell into the saved instance
  tunnel          fallback if Tailscale is down: forward :4000 and :8001 over SSH
  voices          upload the local voices/ dir to /workspace/voices (vast-up does this too)
  logs            tail -f /workspace/bootstrap.log
  down            destroy the saved instance (asks you to type its id)
MSG
}

cmd=${1:-}; [ $# -gt 0 ] && shift
case "$cmd" in
  offers|up|voices|ssh|tunnel|logs|down) need_cli; "cmd_$cmd" "$@" ;;
  *) usage; [ -z "$cmd" ] || exit 1 ;;
esac
