#!/usr/bin/python3 -I
"""SSH_ASKPASS helper executed by OpenSSH for Bunny Deck's SFTP connections.

Forwards the prompt to the running Bunny Deck instance over its private
Unix socket and prints the answer on stdout. Exit status 1 means cancelled.
Standalone on purpose (stdlib only, isolated mode), so it is quick to start.
"""

import json
import os
import socket
import struct
import sys


def main():
    path = os.environ.get("BUNNYDECK_ASKPASS_SOCK")
    token = os.environ.get("BUNNYDECK_ASKPASS_TOKEN")
    conn_id = os.environ.get("BUNNYDECK_ASKPASS_CONN", "")
    if not path or not token:
        return 1
    prompt = sys.argv[1] if len(sys.argv) > 1 else ""
    # OpenSSH >= 8.4 says what kind of answer it expects.
    hint = os.environ.get("SSH_ASKPASS_PROMPT", "")
    if hint == "none":
        # Informational only (e.g. "touch your security key"); nothing to answer.
        return 0
    if hint == "confirm" or "(yes/no" in prompt:
        kind = "confirm"
    else:
        kind = "secret"

    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(path)
        data = json.dumps(
            {"token": token, "conn": conn_id, "prompt": prompt, "kind": kind}
        ).encode("utf-8")
        s.sendall(struct.pack(">I", len(data)) + data)
        hdr = b""
        while len(hdr) < 4:
            c = s.recv(4 - len(hdr))
            if not c:
                return 1
            hdr += c
        (n,) = struct.unpack(">I", hdr)
        body = b""
        while len(body) < n:
            c = s.recv(n - len(body))
            if not c:
                return 1
            body += c
        reply = json.loads(body.decode("utf-8"))
    except (OSError, ValueError):
        return 1
    finally:
        s.close()

    if not reply.get("ok"):
        return 1
    if kind == "confirm" and hint == "confirm":
        # ssh-agent style confirmation: exit status is the answer.
        return 0
    sys.stdout.write(reply.get("answer", "") + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
