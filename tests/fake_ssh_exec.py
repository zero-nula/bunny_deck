#!/usr/bin/env python3
"""A stand-in for ssh in the Docker tests: never connects anywhere. `-O
check` answers like a running master (FAKE_SSH_NO_MASTER=1: none); anything
else runs the remote command here with `sh -c`, with tests/fake_docker.py,
fake_ufw.py and fake_sudo.py as "docker", "ufw" and "sudo" first on PATH. FAKE_SSH_LOG gets one JSON line per call."""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
log = os.environ.get("FAKE_SSH_LOG")
if log:
    with open(log, "a") as f:
        f.write(json.dumps(args) + "\n")
if "-O" in args:
    sys.exit(255 if os.environ.get("FAKE_SSH_NO_MASTER") else 0)

# The remote command is what follows the host, which follows "--".
rest = args[args.index("--") + 2:] if "--" in args else []
bindir = tempfile.mkdtemp(prefix="bunnydeck-fake-docker-")
for name, script in (("docker", "fake_docker.py"), ("ufw", "fake_ufw.py"),
                     ("sudo", "fake_sudo.py")):
    if name in os.environ.get("FAKE_SSH_HIDE", "").split(","):
        continue  # this server does not have it
    path = os.path.join(bindir, name)
    with open(path, "w") as f:
        f.write(f'#!/bin/sh\nexec "{sys.executable}" "{os.path.join(HERE, script)}" "$@"\n')
    os.chmod(path, 0o755)
os.environ["PATH"] = bindir + ":" + os.environ.get("PATH", "/usr/bin:/bin")
if not rest:
    os.execv("/bin/sh", ["sh", "-i"])
os.execv("/bin/sh", ["sh", "-c", " ".join(rest)])
