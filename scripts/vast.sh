#!/usr/bin/env bash
# Rent, reach and destroy the ThaiDub GPU box on Vast.ai. Needs: pip install vastai; vastai set api-key <key>.
set -euo pipefail

IMAGE="${IMAGE:-ghcr.io/monchavin01/thaidub-gpu-server:dev}"
ENABLE_HUNYUAN="${ENABLE_HUNYUAN:-0}"
DEFAULT_VOICE="${DEFAULT_VOICE:-m_young_01}"
STATE=".vast-instance"
IMAGE_TAG="$IMAGE"; CUDA_MIN=12.8
if [ "$ENABLE_HUNYUAN" = "1" ]; then IMAGE_TAG="$IMAGE-hunyuan"; CUDA_MIN=12.9; fi # vLLM -cu129 needs driver >= 575
QUERY="verified=True rentable=True reliability>=0.99 cuda_vers>=$CUDA_MIN cpu_cores_effective>=8 cpu_ram>=32 disk_space>=100 inet_down>=500 inet_up>=200 gpu_name=RTX_3090 num_gpus=1"
ENV_ARGS="-e ENABLE_HUNYUAN=$ENABLE_HUNYUAN -e DEFAULT_VOICE=$DEFAULT_VOICE"
ONSTART="/app/scripts/vast-onstart.sh"

die() { echo "error: $*" >&2; exit 1; }
need_cli() { command -v vastai >/dev/null || die "vastai not installed: pip install vastai (then: vastai set api-key <key>)"; }
instance_id() { [ -s "$STATE" ] || die "no $STATE: run 'vast.sh up' first"; cat "$STATE"; }
json() { python3 -c "import sys,json;d=json.load(sys.stdin);$1"; } # stdin JSON -> python expr using d

# Prints "host port" of the saved instance (empty until Vast assigns ssh).
ssh_target() {
  vastai show instance "$(instance_id)" --raw | json 'print(d.get("ssh_host") or "", d.get("ssh_port") or "")'
}
ssh_run() { # host port args...
  local host=$1 port=$2; shift 2
  ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -p "$port" "$@" "root@$host"
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
  until ssh_run "$1" "$2" 'grep -q ^READY /data/start.log' 2>/dev/null; do
    [ $SECONDS -lt $end ] || die "not READY after 40 min (see: vast.sh logs)"
    line=$(ssh_run "$1" "$2" 'tail -n1 /data/start.log' 2>/dev/null || echo "(ssh not up yet)")
    echo "  $line"
    sleep 15
  done
}

cmd_up() {
  local id=${1:-}
  [ -n "$id" ] || id=$(vastai search offers -n "$QUERY" -o dph --raw | json 'print(d[0]["id"] if d else "")')
  [ -n "$id" ] || die "no offer matches the minimum spec (see: vast.sh offers)"
  echo "Reminder: secrets come from Vast account env vars, not this script. Set once: vastai create env-var LITELLM_MASTER_KEY <key>"
  local t0=$SECONDS out iid
  out=$(vastai create instance "$id" --image "$IMAGE_TAG" --disk 100 --ssh --direct --label thaidub-gpu \
    --env "$ENV_ARGS" --onstart-cmd "$ONSTART" --raw)
  iid=$(printf '%s' "$out" | json 'print(d.get("new_contract") or "")') || true
  [ -n "$iid" ] || die "create failed: $out"
  echo "$iid" >"$STATE"
  echo "created instance $iid from offer $id ($IMAGE_TAG)"
  wait_running "$iid"
  local t1=$SECONDS
  echo "running after $((t1 - t0))s"
  with_target wait_ready
  echo "READY: create->running $((t1 - t0))s, running->READY $((SECONDS - t1))s"
}

cmd_ssh() { with_target _ssh_shell; }
_ssh_shell() { ssh_run "$1" "$2"; }

cmd_tunnel() {
  echo "ThaiDub base URL: http://localhost:4000/v1 (Ctrl-C closes the tunnel)"
  with_target _tunnel
}
_tunnel() { ssh_run "$1" "$2" -N -L 4000:127.0.0.1:4000 -L 8001:127.0.0.1:8001; }

cmd_logs() { with_target _logs; }
_logs() { ssh_run "$1" "$2" 'tail -f /data/start.log'; }

cmd_down() {
  local id answer; id=$(instance_id)
  echo "instance $id, label: $(vastai show instance "$id" --raw | json 'print(d.get("label"))')"
  read -r -p "type the instance id to destroy: " answer
  [ "$answer" = "$id" ] || die "id did not match; nothing destroyed"
  vastai destroy instance "$id" -y
  rm -f "$STATE"
}

cmd_template() {
  vastai create template --name thaidub-gpu --image "$IMAGE_TAG" --env "$ENV_ARGS" --ssh --direct \
    --onstart-cmd "$ONSTART" --disk_space 100 --search_params "$QUERY"
  cat <<'MSG'
Private template created. Find its hash in the output above (hash_id) or in the console under Templates.
One-click rent in the web console: https://cloud.vast.ai/?template_id=<hash>
MSG
}

usage() {
  cat <<'MSG'
usage: scripts/vast.sh <command>
  offers          list the 10 cheapest offers that meet the minimum spec
  up [offer_id]   rent (cheapest offer if no id), wait for running, then READY
  ssh             shell into the saved instance
  tunnel          forward :4000 (LiteLLM) and :8001 (backend) to localhost
  logs            tail -f /data/start.log
  down            destroy the saved instance (asks you to type its id)
  template        create a private Vast template for one-click renting
MSG
}

cmd=${1:-}; [ $# -gt 0 ] && shift
case "$cmd" in
  offers|up|ssh|tunnel|logs|down|template) need_cli; "cmd_$cmd" "$@" ;;
  *) usage; [ -z "$cmd" ] || exit 1 ;;
esac
