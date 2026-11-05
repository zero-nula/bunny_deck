#!/usr/bin/env python3
"""A stand-in for ping in the tests: never sends a packet. Answers with
FAKE_PING_MS milliseconds, or like an unreachable host if that is "none".
FAKE_PING_LOG, if set, gets one line per call with the arguments."""

import os
import sys

log = os.environ.get("FAKE_PING_LOG")
if log:
    with open(log, "a") as f:
        f.write(" ".join(sys.argv[1:]) + "\n")
ms = os.environ.get("FAKE_PING_MS", "12.4")
host = sys.argv[-1]
print(f"PING {host} ({host}) 56(84) bytes of data.")
if ms == "none":
    print("\n--- ping statistics ---\n1 packets transmitted, 0 received, 100% packet loss")
    sys.exit(1)
print(f"64 bytes from {host}: icmp_seq=1 ttl=64 time={ms} ms")
