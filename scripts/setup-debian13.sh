#!/usr/bin/env bash
#
# Upgrade a Debian-based Radxa to Debian 13 "trixie".
# Run as root on the board. Backups go to /var/backups/radxa-debian13.
#
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    echo "Run as root: sudo $0" >&2
    exit 1
fi

. /etc/os-release

echo "== Current system =="
cat /etc/os-release

if [[ "${VERSION_CODENAME:-}" == "trixie" ]]; then
    echo "Already on Debian 13 (trixie)."
else
    if [[ "${ID:-}" != "debian" ]] && [[ "${FORCE_DEBIAN13:-0}" != "1" ]]; then
        echo "This script targets Debian. Set FORCE_DEBIAN13=1 to force it." >&2
        exit 1
    fi

    TS="$(date +%Y%m%d%H%M%S)"
    BACKUP="/var/backups/radxa-debian13"
    mkdir -p "$BACKUP"

    for f in /etc/apt/sources.list /etc/apt/sources.list.d/debian.sources; do
        if [[ -e "$f" ]]; then
            cp -a "$f" "${BACKUP}/$(basename "$f").${TS}"
            mv "$f" "${BACKUP}/$(basename "$f").${TS}.disabled"
            echo "Backed up and disabled $f"
        fi
    done

    cat > /etc/apt/sources.list.d/debian-trixie.sources <<'EOF'
Types: deb
URIs: http://deb.debian.org/debian
Suites: trixie trixie-updates
Components: main contrib non-free non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: http://security.debian.org/debian-security
Suites: trixie-security
Components: main contrib non-free non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF
    echo "Wrote /etc/apt/sources.list.d/debian-trixie.sources"
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get -y --fix-broken install
apt-get -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" full-upgrade
apt-get -y autoremove --purge
apt-get -y clean

echo
echo "Debian 13 upgrade finished. Reboot when convenient: sudo reboot"
echo "Custom Radxa kernel/firmware packages may need updating from the vendor repo after reboot."
