#!/bin/bash
# Install the weather station as a boot-time kiosk.
#   - server:  systemd system service
#   - kiosk:   desktop autostart entry (runs inside the user's graphical session)
#   - login:   SDDM autologin so the desktop (and kiosk) start at boot
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
APP_HOME="$(getent passwd "$APP_USER" | cut -d: -f6)"

command -v chromium >/dev/null || command -v chromium-browser >/dev/null || {
    echo "Chromium not found (sudo apt install chromium)." >&2; exit 1; }
for tool in unclutter xset curl; do
    command -v "$tool" >/dev/null || echo "Warning: '$tool' not found (sudo apt install unclutter x11-xserver-utils curl)" >&2
done
python3 -c "import flask" 2>/dev/null || echo "Warning: Flask missing (sudo apt install python3-flask)" >&2

# --- Server service ---------------------------------------------------------
sed -e "s|__USER__|$APP_USER|g" -e "s|__DIR__|$APP_DIR|g" \
    "$APP_DIR/deploy/weatherstation.service" > /etc/systemd/system/weatherstation.service

# Remove the old kiosk system service from earlier versions of this installer
if [ -f /etc/systemd/system/weatherstation-kiosk.service ]; then
    systemctl disable --now weatherstation-kiosk.service 2>/dev/null || true
    rm -f /etc/systemd/system/weatherstation-kiosk.service
fi

systemctl daemon-reload
systemctl enable weatherstation.service
systemctl restart weatherstation.service

# --- Kiosk autostart --------------------------------------------------------
chmod +x "$APP_DIR/deploy/kiosk.sh"
AUTOSTART="$APP_HOME/.config/autostart"
sudo -u "$APP_USER" mkdir -p "$AUTOSTART"
cat > "$AUTOSTART/weatherstation-kiosk.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Weather Station Kiosk
Exec=$APP_DIR/deploy/kiosk.sh
X-GNOME-Autostart-enabled=true
EOF
chown "$APP_USER": "$AUTOSTART/weatherstation-kiosk.desktop"

# --- Autologin (SDDM, used by KDE Plasma) -----------------------------------
if command -v sddm >/dev/null; then
    mkdir -p /etc/sddm.conf.d
    printf '[Autologin]\nUser=%s\n' "$APP_USER" > /etc/sddm.conf.d/weatherstation-autologin.conf
    echo "SDDM autologin enabled for $APP_USER."
else
    echo "SDDM not found: configure automatic login for $APP_USER in your display manager." >&2
fi

# --- Disable the screen locker (KDE) ----------------------------------------
if command -v kwriteconfig6 >/dev/null; then
    sudo -u "$APP_USER" kwriteconfig6 --file kscreenlockerrc --group Daemon --key Autolock false
    sudo -u "$APP_USER" kwriteconfig6 --file kscreenlockerrc --group Daemon --key LockOnResume false
fi

echo "Installed for user '$APP_USER' from $APP_DIR."
echo "Also set System Settings > Energy Saving > 'Screen Energy Saving' off, then reboot to test."
echo "Server log: journalctl -u weatherstation -f"
