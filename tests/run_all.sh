#!/bin/bash
# Runs all automated tests: unit/integration tests, then GUI tests on a
# private Xvfb display. Never touches the user's desktop, ~/.ssh or config.
cd "$(dirname "$0")/.."
echo "### Unit and integration tests"
python3 -m unittest discover -s tests -p 'test_*.py' -v 2>&1 | tail -n +1 | grep -E "^(test_|Ran |OK|FAILED)|Error|FAIL:" 
u=${PIPESTATUS[0]}
echo "### GUI tests (Xvfb)"
tests/run_gui_tests.sh tests/gui_phase1.py tests/gui_phase2.py tests/gui_phase3.py \
  tests/gui_phase3_auth.py tests/gui_phase5.py tests/gui_local.py tests/gui_startup.py tests/gui_ai.py tests/gui_background.py tests/gui_sshmux.py tests/gui_keysetup.py tests/gui_broadcast.py tests/gui_folders.py tests/gui_delete.py tests/gui_shortcuts.py tests/gui_session.py tests/gui_netscan.py | grep -E "^==|FAIL|checks passed|Fatal Python|Current thread|Thread 0x|  File |Segmentation"
g=${PIPESTATUS[0]}
[ "$u" = 0 ] && [ "$g" = 0 ] && echo "ALL PASSED" || { echo "FAILURES"; exit 1; }
