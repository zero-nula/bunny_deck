"""In-app endpoint for OpenSSH's SSH_ASKPASS mechanism.

For SFTP connections the app runs OpenSSH without a terminal and sets
SSH_ASKPASS to askpass_helper.py with SSH_ASKPASS_REQUIRE=force. Whenever ssh
needs a password, passphrase, keyboard-interactive answer or host-key
confirmation, it executes the helper, which forwards the prompt over a
private Unix socket to this server. The server asks a registered handler
(normally a Qt dialog) and sends the answer back; the helper prints it to
ssh on stdout.

Security properties:
  * The socket lives in a fresh 0700 directory under $XDG_RUNTIME_DIR.
  * Peers must have our UID (SO_PEERCRED) and present a random token that
    only exists in the environment of ssh processes we started.
  * Answers are never logged, written to disk or put on a command line.
"""

import json
import os
import secrets
import shutil
import socket
import struct
import tempfile
import threading

HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "askpass_helper.py")

ENV_SOCK = "BUNNYDECK_ASKPASS_SOCK"
ENV_TOKEN = "BUNNYDECK_ASKPASS_TOKEN"
ENV_CONN = "BUNNYDECK_ASKPASS_CONN"

MAX_MSG = 64 * 1024


def send_msg(sock, obj):
    data = json.dumps(obj).encode("utf-8")
    sock.sendall(struct.pack(">I", len(data)) + data)


def recv_msg(sock):
    hdr = _recv_exact(sock, 4)
    (n,) = struct.unpack(">I", hdr)
    if n > MAX_MSG:
        raise ValueError("askpass message too large")
    return json.loads(_recv_exact(sock, n).decode("utf-8"))


def _recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise EOFError("askpass peer closed")
        buf += chunk
    return buf


class AskpassServer:
    def __init__(self):
        base = os.environ.get("XDG_RUNTIME_DIR")
        if not base or not os.path.isdir(base):
            base = None
        self.dir = tempfile.mkdtemp(prefix="bunny-deck-askpass-", dir=base)
        os.chmod(self.dir, 0o700)
        self.path = os.path.join(self.dir, "sock")
        self.token = secrets.token_hex(32)
        self._handlers = {}
        self._lock = threading.Lock()
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(self.path)
        os.chmod(self.path, 0o600)
        self._sock.listen(8)
        self._closed = False
        threading.Thread(target=self._accept_loop, name="askpass", daemon=True).start()

    # handler(prompt: str, kind: str) -> str | None   (kind: "secret" | "confirm" | "none")
    def register(self, conn_id, handler):
        with self._lock:
            self._handlers[conn_id] = handler

    def unregister(self, conn_id):
        with self._lock:
            self._handlers.pop(conn_id, None)

    def env_for(self, conn_id):
        return {
            "SSH_ASKPASS": HELPER,
            "SSH_ASKPASS_REQUIRE": "force",
            ENV_SOCK: self.path,
            ENV_TOKEN: self.token,
            ENV_CONN: conn_id,
        }

    def close(self):
        self._closed = True
        try:
            self._sock.close()
        except OSError:
            pass
        shutil.rmtree(self.dir, ignore_errors=True)

    def _accept_loop(self):
        while not self._closed:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        with conn:
            try:
                creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                                        struct.calcsize("3i"))
                _pid, uid, _gid = struct.unpack("3i", creds)
                if uid != os.getuid():
                    return
                conn.settimeout(10)
                req = recv_msg(conn)
                if not secrets.compare_digest(str(req.get("token", "")), self.token):
                    return
                conn.settimeout(None)
                with self._lock:
                    handler = self._handlers.get(str(req.get("conn", "")))
                prompt = str(req.get("prompt", ""))[:4096]
                kind = str(req.get("kind", "secret"))
                answer = handler(prompt, kind) if handler is not None else None
                send_msg(conn, {"ok": answer is not None, "answer": answer or ""})
            except (OSError, ValueError, EOFError):
                return
