#!/bin/sh
# Firefox test harnesses (mochitest, reftest, wpt, marionette) need an X display;
# CI runs them under one. Without this only xpcshell and gtest could run locally.
set -e

Xvfb "$DISPLAY" -screen 0 1920x1080x24 -nolisten tcp &

# mach may start a test seconds after boot, so wait for the display to accept
# connections rather than racing it.
i=0
while [ "$i" -lt 50 ]; do
    xdpyinfo -display "$DISPLAY" >/dev/null 2>&1 && break
    i=$((i + 1))
    sleep 0.2
done

exec "$@"
