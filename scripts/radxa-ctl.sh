#!/usr/bin/env bash
#
# Connect to a Radxa over public DNS or a fixed LAN host.
#   ./radxa-ctl.sh                      # interactive shell via DDNS
#   ./radxa-ctl.sh 'hostname'           # run a command
#
set -euo pipefail

DOMAIN="${RADXA_DOMAIN:-lain42.top}"
HOST="${RADXA_HOST:-}"
SSH_USER="${RADXA_USER:-root}"
SSH_PORT="${RADXA_PORT:-22}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/lain42.pem}"

if [[ -z "$HOST" ]]; then
  if command -v dig >/dev/null 2>&1; then
    HOST="$(dig +short "$DOMAIN" A 2>/dev/null | head -n1)"
  else
    HOST="$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk 'NR==1 {print $1; exit}')"
  fi
fi

if [[ -z "$HOST" ]]; then
  echo "Unable to resolve $DOMAIN (or RADXA_HOST is empty)" >&2
  exit 1
fi

echo "Connecting ${SSH_USER}@${HOST} (${DOMAIN}) with $SSH_KEY" >&2
exec ssh -i "$SSH_KEY" \
  -p "$SSH_PORT" \
  -o ConnectTimeout=12 \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=4 \
  "${SSH_USER}@${HOST}" "$@"
