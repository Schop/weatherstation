#!/bin/bash
# Launched from the desktop session's autostart (see install.sh).
# Waits for the Flask server, then opens Chromium fullscreen.

CHROMIUM="$(command -v chromium || command -v chromium-browser)"

until curl -sf http://localhost:5000/api/ping >/dev/null; do sleep 1; done

# X11 sessions: disable blanking and hide the pointer (unclutter is X11-only)
if [ "$XDG_SESSION_TYPE" = "x11" ]; then
    xset s off; xset -dpms; xset s noblank
    pgrep -x unclutter >/dev/null || unclutter -idle 0.1 -root &
fi

exec "$CHROMIUM" --kiosk --noerrdialogs --disable-infobars \
    --disable-session-crashed-bubble --disk-cache-size=1 \
    --ozone-platform-hint=auto --cursor=none \
    --password-store=basic \
    http://localhost:5000
