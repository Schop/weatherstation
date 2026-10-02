#!/bin/bash
# Install the weather station as a boot-time kiosk (systemd).
# Run from the repo on the target machine:  sudo ./deploy/install.sh [username]
# The username is the desktop user that auto-logs-in (defaults to the user who ran sudo).
set -e

if [ "$EUID" -ne 0 ]; then
    echo "Run with sudo: sudo $0 [username]" >&2
    exit 1
fi

APP_USER="${1:-${SUDO_USER:-}}"
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"

if [ -z "$APP_USER" ] || ! id "$APP_USER" >/dev/null 2>&1; then
    echo "Could not determine a valid user; pass it as an argument." >&2
    exit 1
fi

CHROMIUM="$(command -v chromium-browser || command -v chromium || true)"
if [ -z "$CHROMIUM" ]; then
    echo "Chromium not found. Install it (e.g. sudo apt install chromium-browser or chromium)." >&2
    exit 1
fi

for tool in unclutter xset curl; do
    command -v "$tool" >/dev/null || echo "Warning: '$tool' not found (sudo apt install unclutter x11-xserver-utils curl)" >&2
done
python3 -c "import flask" 2>/dev/null || echo "Warning: Flask missing (sudo apt install python3-flask)" >&2

for unit in weatherstation weatherstation-kiosk; do
    sed -e "s|__USER__|$APP_USER|g" \
        -e "s|__DIR__|$APP_DIR|g" \
        -e "s|__CHROMIUM__|$CHROMIUM|g" \
        "$APP_DIR/deploy/$unit.service" > "/etc/systemd/system/$unit.service"
done

systemctl daemon-reload
systemctl enable weatherstation.service weatherstation-kiosk.service
systemctl restart weatherstation.service weatherstation-kiosk.service

echo "Installed for user '$APP_USER' from $APP_DIR."
echo "Logs: journalctl -u weatherstation -u weatherstation-kiosk -f"
