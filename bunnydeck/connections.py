"""Pool of SFTP connections (one per profile).

Toolkit-neutral: the UI passes in
  call_soon(fn)                      -> runs fn() later on the UI thread
  ask(profile, prompt, kind, retry)  -> str | None, called on the UI thread
Connecting happens on worker threads; callbacks are delivered via call_soon.
"""

import threading

from . import sftp
from .askpass import AskpassServer


def run_on_main_sync(call_soon, fn, *args):
    """Runs fn(*args) on the UI thread and waits for its result."""
    done = threading.Event()
    box = {}

    def call():
        try:
            box["result"] = fn(*args)
        except BaseException as e:  # pragma: no cover - surfaced to caller
            box["error"] = e
        done.set()

    call_soon(call)
    done.wait()
    if "error" in box:
        raise box["error"]
    return box.get("result")


class ConnectionPool:
    def __init__(self, call_soon, ask, argv_factory=None):
        self.call_soon = call_soon
        self.ask = ask
        self.argv_factory = argv_factory or (lambda profile: sftp.ssh_sftp_argv(profile))
        self._askpass = None
        self._clients = {}  # profile id -> SFTPClient
        self._waiters = {}  # profile id -> [callbacks] while connecting
        self._seq = 0

    def _server(self):
        if self._askpass is None:
            self._askpass = AskpassServer()
        return self._askpass

    def cached(self, profile_id):
        c = self._clients.get(profile_id)
        return c if c is not None and c.alive else None

    def get(self, profile, callback):
        """callback(client, error_message_or_None) on the UI thread.
        error is "cancelled" when the user cancelled authentication."""
        client = self.cached(profile.id)
        if client is not None:
            self.call_soon(lambda: callback(client, None))
            return
        if profile.id in self._waiters:
            self._waiters[profile.id].append(callback)
            return
        self._waiters[profile.id] = [callback]
        self._seq += 1
        conn_id = f"{profile.id}-{self._seq}"
        server = self._server()
        client = sftp.SFTPClient(self.argv_factory(profile), env=server.env_for(conn_id))
        server.register(conn_id, self._make_prompt_handler(profile, client))
        threading.Thread(
            target=self._connect, args=(profile, client, conn_id), daemon=True
        ).start()

    def _connect(self, profile, client, conn_id):
        error = None
        try:
            client.connect()
        except sftp.Cancelled:
            error = "cancelled"
        except sftp.SFTPError as e:
            error = str(e)
        except Exception as e:  # unexpected; report rather than crash the UI
            error = f"Unexpected error: {e}"
        finally:
            self._server().unregister(conn_id)

        def finish():
            callbacks = self._waiters.pop(profile.id, [])
            if error is None:
                self._clients[profile.id] = client
            else:
                client.close()
            for cb in callbacks:
                cb(client if error is None else None, error)

        self.call_soon(finish)

    def _make_prompt_handler(self, profile, client):
        seen = {}

        def handler(prompt, kind):
            # Called on the askpass thread for every OpenSSH prompt.
            key = prompt.strip()
            retry = kind == "secret" and seen.get(key, 0) > 0
            seen[key] = seen.get(key, 0) + 1
            answer = run_on_main_sync(self.call_soon, self.ask, profile, prompt, kind, retry)
            if answer is None:
                # Cancelled: stop this connection attempt completely instead
                # of letting ssh fall through to other auth methods.
                client.close("cancelled")
            return answer

        return handler

    def close_all(self):
        for c in self._clients.values():
            c.close()
        self._clients.clear()
        if self._askpass is not None:
            self._askpass.close()
            self._askpass = None
