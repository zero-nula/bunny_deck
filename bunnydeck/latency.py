"""Latency to an SSH host, shown in the terminal header.

Measured with an ordinary ICMP ping from this computer, never over the SSH
connection: a command over the shared connection opens a session on the
server each time (an auth-log line every few seconds), and probing the SSH
port would be logged as an aborted login (fail2ban may ban that). Ping does
not touch sshd at all.

The real address comes from `ssh -G`, so aliases and HostName lines in
~/.ssh/config work. Hosts reached through ProxyJump/ProxyCommand are not
pinged: the ping would not take the same way as the connection. If the host
does not answer ping (blocked ICMP), the header simply shows no latency.

One Monitor per address is shared by all terminals to it.
"""

import re
from collections import deque

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSignal

SSH_BINARY = "/usr/bin/ssh"
PING_BINARY = "ping"
INTERVAL_MS = 5000
TIMEOUT_S = 2
HISTORY = 36  # pings kept for the graph in the stats popup (3 minutes)

_TIME = re.compile(r"time[=<]\s*([\d.]+)\s*ms")

_monitors = {}  # address -> Monitor


def parse_ssh_g(text):
    """(hostname, direct) from `ssh -G` output; direct is False when the
    connection goes through a proxy."""
    values = {}
    for line in text.splitlines():
        key, _, value = line.partition(" ")
        values[key.lower()] = value.strip()
    direct = values.get("proxyjump", "none") == "none" and \
        values.get("proxycommand", "none") == "none"
    return values.get("hostname", ""), direct


def parse_ping(text):
    """Milliseconds from ping's output, or None."""
    m = _TIME.search(text)
    return float(m.group(1)) if m else None


class Monitor(QObject):
    """Pings one address every INTERVAL_MS while someone listens.
    updated(ms): a float, or -1.0 when the host did not answer."""

    updated = pyqtSignal(float)

    def __init__(self, address):
        super().__init__()
        self.address = address
        self.users = 0
        self.last = None
        self.history = deque(maxlen=HISTORY)
        self._proc = None
        self._timer = QTimer(self)
        self._timer.setInterval(INTERVAL_MS)
        self._timer.timeout.connect(self._ping)

    def start(self):
        self.users += 1
        if self.users == 1:
            self._timer.start()
            self._ping()
        elif self.last is not None:
            QTimer.singleShot(0, lambda: self.updated.emit(self.last))

    def stop(self):
        self.users -= 1
        if self.users <= 0:
            self._timer.stop()
            if self._proc is not None:
                self._proc.kill()
                self._proc.waitForFinished(1000)
            _monitors.pop(self.address, None)
            self.deleteLater()

    def _ping(self):
        if self._proc is not None:  # the previous one has not finished
            return
        p = QProcess(self)
        p.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        p.finished.connect(lambda *_: self._done(p))
        p.errorOccurred.connect(lambda err: self._failed(p, err))
        self._proc = p
        # -n: no reverse lookups; "--" so the address is never an option.
        p.start(PING_BINARY, ["-n", "-c", "1", "-W", str(TIMEOUT_S), "--", self.address])

    def _failed(self, p, err):
        if err == QProcess.ProcessError.FailedToStart:  # no ping installed
            self._proc = None
            self._timer.stop()
            p.deleteLater()

    def _done(self, p):
        out = bytes(p.readAll()).decode(errors="replace")
        self._proc = None
        p.deleteLater()
        ms = parse_ping(out)
        self.last = ms if ms is not None else -1.0
        self.history.append(self.last)
        if self.users > 0:
            self.updated.emit(self.last)


def monitor(address):
    m = _monitors.get(address)
    if m is None:
        m = _monitors[address] = Monitor(address)
    return m


class HostWatch(QObject):
    """Latency for one terminal: resolves the profile's address with
    `ssh -G`, then follows the shared Monitor. updated(ms) as in Monitor."""

    updated = pyqtSignal(float)

    def __init__(self, ssh_args, parent=None):
        super().__init__(parent)
        self._monitor = None
        self._stopped = False
        self._proc = QProcess(self)
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self._proc.finished.connect(self._resolved)
        self._proc.start(SSH_BINARY, ["-G"] + list(ssh_args))

    def _resolved(self, *_):
        if self._stopped:
            return
        out = bytes(self._proc.readAllStandardOutput()).decode(errors="replace")
        host, direct = parse_ssh_g(out)
        if not host or not direct:
            return
        self._monitor = monitor(host)
        self._monitor.updated.connect(self.updated)
        self._monitor.start()

    def history(self):
        """Recent pings, oldest first (-1.0: no answer)."""
        return list(self._monitor.history) if self._monitor is not None else []

    def stop(self):
        if self._stopped:
            return
        self._stopped = True
        if self._proc.state() != QProcess.ProcessState.NotRunning:
            self._proc.kill()
            self._proc.waitForFinished(1000)
        if self._monitor is not None:
            self._monitor.updated.disconnect(self.updated)
            self._monitor.stop()
            self._monitor = None
