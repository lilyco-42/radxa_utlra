#!/usr/bin/env bash
#
# Find a Radxa on the LAN and via a public DDNS domain.
#   ./scan-radxa.sh [lain42.top]
#
set -uo pipefail

DOMAIN="${RADXA_DOMAIN:-${1:-lain42.top}}"
SSH_USER="${RADXA_USER:-root}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/lain42.pem}"

resolve_domain() {
  local ip=""
  if command -v dig >/dev/null 2>&1; then
    ip="$(dig +short "$DOMAIN" A 2>/dev/null | awk 'NR==1 {print; exit}')"
  fi
  if [[ -z "$ip" ]] && command -v getent >/dev/null 2>&1; then
    ip="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1 {print $1; exit}')"
  fi
  if [[ -z "$ip" ]]; then
    ip="$(nslookup "$DOMAIN" 2>/dev/null | awk '/^Address: / {print $2; exit}')"
  fi
  printf '%s' "$ip"
}

find_subnet() {
  local gw
  gw="$(ip -4 route 2>/dev/null | awk '/default/ {for (i=1; i<=NF; i++) if ($i == "via") {print $(i+1); exit}}')"
  if [[ -z "$gw" ]]; then
    gw="$(hostname -I 2>/dev/null | awk '{print $1}')"
  fi
  printf '%s' "$gw" | awk -F. '{print $1"."$2"."$3}'
}

probe_ssh() {
  local host="$1"
  echo "--- SSH ${SSH_USER}@${host} ---"
  timeout 12 ssh -i "$SSH_KEY" \
    -o BatchMode=yes \
    -o StrictHostKeyChecking=accept-new \
    -o UserKnownHostsFile="$HOME/.ssh/known_hosts" \
    -o ConnectTimeout=6 \
    "${SSH_USER}@${host}" \
    'echo CONNECTED; hostname; uname -m; . /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-}"' 2>&1 || true
}

echo "== DNS =="
PUBLIC_IP="$(resolve_domain)"
echo "$DOMAIN -> ${PUBLIC_IP:-<not resolvable>}"

echo
echo "== Local network =="
SUBNET="$(find_subnet)"
echo "subnet: ${SUBNET:-<not found>}.0/24"
hostname -I 2>/dev/null | tr ' ' '\n' | awk 'NF'

echo
echo "== Online hosts (ping sweep) =="
LAN_HOSTS=()
if [[ -n "$SUBNET" ]]; then
  mapfile -t LAN_HOSTS < <(seq 1 254 | xargs -P 32 -I{} sh -c \
    "ping -c1 -W1 '${SUBNET}.{}' >/dev/null 2>&1 && echo '${SUBNET}.{}'")
  printf '%s\n' "${LAN_HOSTS[@]:-<none>}"
fi

echo
echo "== SSH probes =="
if [[ -n "${PUBLIC_IP:-}" ]]; then
  probe_ssh "$PUBLIC_IP"
fi
for host in "${LAN_HOSTS[@]:-}"; do
  probe_ssh "$host"
done

echo
echo "== Summary =="
echo "Public: ${PUBLIC_IP:-<not resolved>} (${DOMAIN})"
echo "LAN candidates: ${LAN_HOSTS[*]:-<none>}"
echo "SSH key: $SSH_KEY"
echo "Tip: set RADXA_HOST, RADXA_USER, SSH_KEY to override."
