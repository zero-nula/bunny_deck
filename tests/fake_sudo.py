#!/usr/bin/env python3
"""A stand-in for sudo in the tests: runs the command "as root" for
tests/fake_ufw.py (FAKE_UFW_ROOT=1), without a password. While the file
FAKE_SUDO_FAIL_FILE exists it fails like three wrong passwords (a file, so
a test can switch it while a terminal runs)."""

import os
import sys

fail = os.environ.get("FAKE_SUDO_FAIL_FILE")
if fail and os.path.exists(fail):
    print("sudo: 3 incorrect password attempts")
    sys.exit(1)
os.environ["FAKE_UFW_ROOT"] = "1"
os.execvp(sys.argv[1], sys.argv[1:])
