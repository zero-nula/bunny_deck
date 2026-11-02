#!/bin/bash
# Runs the GUI tests on a private Xvfb display (never on the user's desktop)
# with a private D-Bus session and a throwaway config directory.
set -u
cd "$(dirname "$0")/.."
DISP=:$(( 90 + RANDOM % 9 ))
Xvfb "$DISP" -screen 0 1920x1080x24 -nolisten tcp >/dev/null 2>&1 &
XPID=$!
trap 'kill $XPID 2>/dev/null' EXIT
sleep 1
rc=0
for t in "${@:-tests/gui_phase*.py}"; do
  for f in $t; do
    echo "== $f"
    # fresh, throwaway config directory per suite
    export XDG_CONFIG_HOME
    XDG_CONFIG_HOME=$(mktemp -d /tmp/bunnydeck-test-cfg-XXXXXX)
    # -X faulthandler: a crash prints where it happened.
    env -u WAYLAND_DISPLAY QT_QPA_PLATFORM=xcb DISPLAY="$DISP" NO_AT_BRIDGE=1 \
      python3 -X faulthandler "$f" 2>&1
    code=$?
    if [ "$code" != 0 ]; then
      echo "  FAIL $f exited with code $code"
      rc=1
    fi
    rm -rf "$XDG_CONFIG_HOME"
  done
done
exit $rc
