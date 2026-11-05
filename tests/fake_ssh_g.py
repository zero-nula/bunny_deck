#!/usr/bin/env python3
"""A stand-in for `ssh -G` in the tests (never reads ~/.ssh/config): the
host "viajump" goes through a ProxyJump, every other host resolves to
"<host>.resolved"."""

import sys

host = sys.argv[-1]
print("user tester")
print(f"hostname {host}.resolved")
print("port 22")
print("proxyjump " + ("bastion" if host == "viajump" else "none"))
