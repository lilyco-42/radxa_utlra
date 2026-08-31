#!/usr/bin/env bash
#
# Install the video watch pipeline as a systemd service.
# Run as root after install-tools.sh.
#
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    echo "Run as root: sudo $0" >&2
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOLS_DIR="${TOOLS_DIR:-/opt/radxa-tools}"
VENV="$TOOLS_DIR/venv"
CONFIG="${RADXA_VIDEO_CONFIG:-/etc/radxa-video/config.yaml}"

if [[ ! -f "$CONFIG" ]]; then
    mkdir -p "$(dirname "$CONFIG")"
    cp "$REPO_ROOT/config.example.yaml" "$CONFIG"
fi

cat > /etc/systemd/system/radxa-video.service <<'UNIT'
[Unit]
Description=Radxa Auto Video Pipeline
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/opt/radxa-tools/venv/bin/python -m video_tool watch --config /etc/radxa-video/config.yaml
Restart=always
RestartSec=10
Environment=RADXA_VIDEO_CONFIG=/etc/radxa-video/config.yaml

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now radxa-video.service
echo "radxa-video.service enabled and started.  Logs: journalctl -fu radxa-video"
