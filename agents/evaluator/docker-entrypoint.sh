#!/bin/sh
# Give Firefox what it expects from a desktop session: an X display for anything
# launched from the agent's shell (`./mach run`, mochitests), and a session bus
# plus PipeWire so Web Audio and media tests have a working audio device.
set -e

# Local runs only: borrow objects from a mounted Firefox clone instead of
# cloning over the network; the runtime then fetches the commit from it.
if [ -n "$FIREFOX_MIRROR" ] && [ ! -d "$SOURCE_REPO/.git" ]; then
    git init -q "$SOURCE_REPO"
    git -C "$SOURCE_REPO" remote add origin "file://$FIREFOX_MIRROR"
    echo "$FIREFOX_MIRROR/.git/objects" > "$SOURCE_REPO/.git/objects/info/alternates"
fi

mkdir -p -m 700 "$XDG_RUNTIME_DIR"

Xvfb "$DISPLAY" -screen 0 "${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH}" -nolisten tcp &

dbus-daemon --session --address="$DBUS_SESSION_BUS_ADDRESS" --fork --nopidfile

# Their logs are noisy (no system bus, bluetooth or cameras here) and not useful
# to the agent.
pipewire >/dev/null 2>&1 &
wireplumber >/dev/null 2>&1 &
pipewire-pulse >/dev/null 2>&1 &

# The agent may start Firefox seconds after boot, so wait for the display and the
# audio server to accept connections rather than racing them.
i=0
while [ "$i" -lt 50 ]; do
    xset -display "$DISPLAY" q >/dev/null 2>&1 && [ -S "$XDG_RUNTIME_DIR/pulse/native" ] && break
    i=$((i + 1))
    sleep 0.2
done

exec "$@"
