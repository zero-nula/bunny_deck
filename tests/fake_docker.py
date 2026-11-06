#!/usr/bin/env python3
"""A stand-in for docker in the tests (never talks to a daemon).
FAKE_DOCKER_MODE: "ok" (default), "denied" or "daemon" (how `info` and `ps`
fail). exec, logs and compose logs print a marker line and the arguments."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from docker_sample import PS_LINES, RUNNING  # noqa: E402

args = sys.argv[1:]
mode = os.environ.get("FAKE_DOCKER_MODE", "ok")
ERRORS = {
    "denied": ("permission denied while trying to connect to the Docker daemon socket at "
               "unix:///var/run/docker.sock: Get \"http://%2Fvar%2Frun%2Fdocker.sock/v1.47/"
               "info\": dial unix /var/run/docker.sock: connect: permission denied"),
    "daemon": ("Cannot connect to the Docker daemon at unix:///var/run/docker.sock. "
               "Is the docker daemon running?"),
}
cmd = args[0] if args else ""
if cmd in ("info", "ps") and mode in ERRORS:
    print(ERRORS[mode], file=sys.stderr)
    sys.exit(1)
if cmd == "info":
    print(os.environ.get("FAKE_DOCKER_RUNNING", str(RUNNING)))
elif cmd == "ps":
    print("\n".join(PS_LINES))
elif cmd == "exec":
    print("FAKE-DOCKER-EXEC " + " ".join(args[1:]))
elif cmd == "logs":
    print("FAKE-DOCKER-LOGS " + " ".join(args[1:]))
elif cmd == "compose":
    print("FAKE-DOCKER-COMPOSE " + " ".join(args[1:]))
else:
    print("fake docker: unknown " + " ".join(args), file=sys.stderr)
    sys.exit(2)
