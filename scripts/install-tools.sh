#!/usr/bin/env bash
#
# Install high-star media processing tools on the Radxa and create a Python venv.
# Run as root: sudo ./install-tools.sh
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
    TOOLS_DIR="${TOOLS_DIR:-/opt/radxa-tools}"
else
    TOOLS_DIR="${TOOLS_DIR:-$HOME/.local/share/radxa-tools}"
fi

APT_PACKAGES=(
    ffmpeg
    imagemagick
    yt-dlp
    rclone
    curl
    wget
    git
    build-essential
    cmake
    python3
    python3-venv
    python3-pip
    mediainfo
    libass-dev
    jq
)

echo "== apt packages =="
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y "${APT_PACKAGES[@]}"

echo "== Python toolchain =="
if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

mkdir -p "$TOOLS_DIR"
python3 -m venv "$TOOLS_DIR/venv"
"$TOOLS_DIR/venv/bin/python" -m pip install --upgrade pip wheel
"$TOOLS_DIR/venv/bin/python" -m pip install -r "$REPO_ROOT/requirements.txt"
"$TOOLS_DIR/venv/bin/python" -m pip install --no-deps -e "$REPO_ROOT"

CONFIG_DIR="${RADXA_VIDEO_CONFIG_DIR:-$HOME/.config/radxa-video}"
mkdir -p "$CONFIG_DIR"
if [[ ! -f "$CONFIG_DIR/config.yaml" ]]; then
    cp "$REPO_ROOT/config.example.yaml" "$CONFIG_DIR/config.yaml"
fi

cat > "$TOOLS_DIR/env.sh" <<EOF
export PATH="$TOOLS_DIR/venv/bin:\$PATH"
export RADXA_VIDEO_CONFIG="$CONFIG_DIR/config.yaml"
export RADXA_HOST="lain42.top"
export RADXA_USER="root"
export SSH_KEY="\$HOME/.ssh/lain42.pem"
EOF

echo
echo "Installed: ffmpeg, imagemagick, yt-dlp, rclone, faster-whisper"
echo "Venv: $TOOLS_DIR/venv"
echo "Config: $CONFIG_DIR/config.yaml"
echo "Env:   source $TOOLS_DIR/env.sh"
