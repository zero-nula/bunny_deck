#!/usr/bin/env python3
"""A stand-in for ufw in the tests: never touches a firewall. The rules live
in FAKE_UFW_STATE (JSON). Like the real one it needs root (FAKE_UFW_ROOT,
set by tests/fake_sudo.py) and `delete` asks (y|n) on stdin."""

import json
import os
import sys

STATE = os.environ.get("FAKE_UFW_STATE", "/nonexistent")
args = sys.argv[1:]


def load():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"active": True, "rules": [{"to": "22/tcp", "from": "Anywhere", "comment": "ssh"}]}


def save(state):
    with open(STATE, "w") as f:
        json.dump(state, f)


if not os.environ.get("FAKE_UFW_ROOT"):
    print("ERROR: You need to be root to run this script")
    sys.exit(1)
state = load()
if args[:2] == ["status", "numbered"]:
    if not state["active"]:
        print("Status: inactive")
        sys.exit(0)
    print("Status: active\n")
    print("     To                         Action      From")
    print("     --                         ------      ----")
    for i, r in enumerate(state["rules"], 1):
        line = f"[{i:2}] {r['to']:<26} ALLOW IN    {r['from']:<26}"
        print((line + (f" # {r['comment']}" if r["comment"] else "")).rstrip())
    print()
elif args[:1] == ["allow"]:
    # allow from SRC to any port P [proto X] [comment C]
    a = dict(zip(args[1::2], args[2::2]))
    to = a["port"] + ("/" + a["proto"] if "proto" in a else "")
    src = "Anywhere" if a["from"] == "any" else a["from"]
    state["rules"].append({"to": to, "from": src, "comment": a.get("comment", "")})
    save(state)
    print("Rule added")
elif args[:1] == ["delete"]:
    n = int(args[1])
    r = state["rules"][n - 1]
    print(f"Deleting:\n allow {r['to']}\nProceed with operation (y|n)? ", end="", flush=True)
    if sys.stdin.readline().strip().lower() == "y":
        del state["rules"][n - 1]
        save(state)
        print("Rule deleted")
    else:
        print("Aborted")
else:
    print("ERROR: Invalid syntax")
    sys.exit(1)
