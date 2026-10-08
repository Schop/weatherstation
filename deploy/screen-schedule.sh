#!/bin/bash
# Night-time screen schedule for the kiosk (KDE Plasma on Wayland).
# Launched from the desktop session's autostart (see install.sh).
#
# Between OFF_FROM and ON_AT the display is switched off (the computer and the
# weather app keep running). A touch wakes it; it switches off again WAKE_SECONDS
# later. Outside those hours the screen is kept on.
#
# Override the defaults in ~/.config/weatherstation-screen.conf

CONF="$HOME/.config/weatherstation-screen.conf"
OFF_FROM="22:00"
ON_AT="07:00"
WAKE_SECONDS=60
CHECK_SECONDS=5

[ -f "$CONF" ] && . "$CONF"

to_min() { local h=${1%%:*} m=${1##*:}; echo $((10#$h * 60 + 10#$m)); }

# in_night <minutes since midnight>: true while the screen should be off
in_night() {
    local now=$1 off on
    off=$(to_min "$OFF_FROM"); on=$(to_min "$ON_AT")
    if [ "$off" -le "$on" ]; then
        [ "$now" -ge "$off" ] && [ "$now" -lt "$on" ]
    else                                    # window crosses midnight
        [ "$now" -ge "$off" ] || [ "$now" -lt "$on" ]
    fi
}

# on | off | unknown, from the kernel's per-connector DPMS state
screen_state() {
    local f seen=0 any_on=0
    for f in /sys/class/drm/card*-*/dpms; do
        [ -r "$f" ] || continue
        [ "$(cat "${f%dpms}status" 2>/dev/null)" = "connected" ] || continue
        seen=1
        [ "$(cat "$f" 2>/dev/null)" = "On" ] && any_on=1
    done
    if [ "$seen" -eq 0 ]; then echo unknown
    elif [ "$any_on" -eq 1 ]; then echo on
    else echo off; fi
}

dpms() { kscreen-doctor --dpms "$1" >/dev/null 2>&1; }

# Allow sourcing the helpers for testing without starting the loop
[ "${1:-}" = "--source-only" ] && return 0 2>/dev/null

prev_night=""; woke_at=0; last_off=0
while true; do
    now_min=$(( 10#$(date +%H) * 60 + 10#$(date +%M) ))
    ts=$(date +%s)
    if in_night "$now_min"; then night=1; else night=0; fi
    state=$(screen_state)

    if [ "$night" -eq 1 ]; then
        if [ "$prev_night" != 1 ]; then          # night just started (or we just started)
            dpms off; woke_at=0; last_off=$ts
        elif [ "$state" = on ]; then             # woken by a touch: allow a short look
            [ "$woke_at" -eq 0 ] && woke_at=$ts
            if [ $((ts - woke_at)) -ge "$WAKE_SECONDS" ]; then
                dpms off; woke_at=0
            fi
        elif [ "$state" = off ]; then
            woke_at=0
        elif [ $((ts - last_off)) -ge "$WAKE_SECONDS" ]; then
            dpms off; last_off=$ts               # state unreadable: just re-assert periodically
        fi
    else
        if [ "$prev_night" != 0 ] || [ "$state" = off ]; then
            dpms on                              # morning, or something else switched it off
        fi
    fi

    prev_night=$night
    sleep "$CHECK_SECONDS"
done
