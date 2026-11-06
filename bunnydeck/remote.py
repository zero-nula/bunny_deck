"""Running a small script on an SSH server over its shared connection, once
that connection is up (sshmux.py).

It first asks the master whether it is up (`ssh -O check`) and only then
runs the script over it, with ControlMaster=no (never a master) and
BatchMode=yes (never asks). So it never logs in on its own: while a password
is still being typed in the terminal, it waits and tries again.

Used by the Docker check (docker.py) and the firewall window (firewall.py).
"""

import shlex

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSignal

from . import sshmux

SSH_BINARY = "/usr/bin/ssh"
# Seconds to wait before each try while the connection is not up yet.
DELAYS = (0, 2, 5, 10, 30, 60)


def ssh_argv(ssh_args, script, ssh_binary=None):
    """Runs script on the server over the existing shared connection."""
    return ([ssh_binary or SSH_BINARY] + sshmux.client_opts()
            + ["-o", "ControlMaster=no", "-o", "BatchMode=yes", "-T"]
            + list(ssh_args) + ["sh -c " + shlex.quote(script)])


def master_check_argv(ssh_args, ssh_binary=None):
    """Exit status 0 when the shared connection to this server is up."""
    return [ssh_binary or SSH_BINARY] + sshmux.client_opts() + ["-O", "check"] + list(ssh_args)


class RemoteScript(QObject):
    """Runs script once the connection is up. output(text) with what it
    printed; parse(text) may return None to mean "not a result, try again"
    (the connection went away in between). gave_up() when it never came up.
    ssh_binary: a callable giving the ssh to use (tests swap it)."""

    output = pyqtSignal(object)
    gave_up = pyqtSignal()

    def __init__(self, ssh_args, script, parse=None, delays=None, ssh_binary=None,
                 parent=None):
        super().__init__(parent)
        self.ssh_args = list(ssh_args)
        self.script = script
        self.parse = parse or (lambda text: text)
        self.delays = list(DELAYS if delays is None else delays)
        self.ssh_binary = ssh_binary or (lambda: SSH_BINARY)
        self.finished = False
        self._proc = None
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._check_master)

    def start(self):
        self._next()

    def _next(self):
        if self.finished:
            return
        if not self.delays:
            self.finished = True
            self.gave_up.emit()
            return
        self._timer.start(int(self.delays.pop(0) * 1000))

    def _run(self, argv, on_done):
        p = QProcess(self)
        p.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        p.finished.connect(lambda code, _status: on_done(p, code))
        p.errorOccurred.connect(
            lambda err: err == QProcess.ProcessError.FailedToStart and on_done(p, -1))
        self._proc = p
        p.start(argv[0], argv[1:])

    def _check_master(self):
        self._run(master_check_argv(self.ssh_args, self.ssh_binary()), self._master_checked)

    def _master_checked(self, p, code):
        self._proc = None
        p.deleteLater()
        if self.finished:
            return
        if code != 0:
            self._next()  # not connected yet
            return
        self._run(ssh_argv(self.ssh_args, self.script, self.ssh_binary()), self._ran)

    def _ran(self, p, code):
        text = bytes(p.readAll()).decode(errors="replace")
        self._proc = None
        p.deleteLater()
        if self.finished:
            return
        result = self.parse(text)
        if result is None:
            self._next()
            return
        self.finished = True
        self.output.emit(result)

    def stop(self):
        self.finished = True
        self._timer.stop()
        if self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning:
            self._proc.kill()
            self._proc.waitForFinished(1000)
