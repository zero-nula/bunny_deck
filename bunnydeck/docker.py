"""Docker on SSH servers, without a GUI: finding out whether a server has
Docker, listing its containers, and the commands for a shell or logs.

Everything runs over the terminal's shared SSH connection (sshmux.py), like
the stats popup (stats.py): ControlMaster=no so it never becomes a master,
BatchMode=yes so it never asks for a password. A check first asks the master
whether it is up (`ssh -O check`), so it never logs in on its own either.

The parsing functions are pure, so they are tested without Docker or SSH
(tests/test_docker.py). Container and project names are always passed
through shlex.quote before they become part of a command.
"""

import json
import re
import shlex
import time

from PyQt6.QtCore import QObject, QProcess, pyqtSignal

from . import remote
from .config import DockerInfo

SSH_BINARY = "/usr/bin/ssh"
WATCH_INTERVAL_S = 3
# Seconds after a terminal starts at which the check is tried while the SSH
# connection is not up yet (a password is still being typed, for example).
PROBE_DELAYS = (2, 5, 10, 30, 60)
LOG_TAIL = 200

COMPOSE_PROJECT = "com.docker.compose.project"
COMPOSE_WORKDIR = "com.docker.compose.project.working_dir"
NO_PROJECT = "(no project)"

# Is there a docker command, may we use the daemon, how many containers run.
PROBE_SCRIPT = """
LC_ALL=C; export LC_ALL
command -v docker >/dev/null 2>&1 || { echo @@nodocker; exit 0; }
out=$(docker info --format "{{.ContainersRunning}}" 2>&1)
if [ $? -eq 0 ]; then echo "@@ok $out"; else echo @@error; printf "%s\\n" "$out"; fi
"""

# The containers, every WATCH_INTERVAL_S seconds, while a Docker panel shows.
WATCH_SCRIPT = f"""
LC_ALL=C; export LC_ALL
command -v docker >/dev/null 2>&1 || {{ echo @@nodocker; echo @@end; exit 0; }}
while :; do
  out=$(docker ps -a --format "{{{{json .}}}}" 2>&1)
  if [ $? -eq 0 ]; then echo @@ps; else echo @@error; fi
  [ -n "$out" ] && printf "%s\\n" "$out"
  echo @@end
  sleep {WATCH_INTERVAL_S}
done
"""

# ----- commands ---------------------------------------------------------------

_SHELL = "command -v bash >/dev/null && exec bash || exec sh"


def exec_command(container):
    """A shell in the container: bash if it has one, else sh."""
    return f"docker exec -it {shlex.quote(container)} sh -c {shlex.quote(_SHELL)}"


def logs_command(container, tail=LOG_TAIL):
    return f"docker logs -f --tail {int(tail)} {shlex.quote(container)}"


def compose_logs_command(project, tail=LOG_TAIL):
    # -p finds the project's containers by their labels, so this works from
    # any folder, without the compose file.
    return f"docker compose -p {shlex.quote(project)} logs -f --tail {int(tail)}"


def ssh_argv(ssh_args, script):
    """Runs script on the server over the existing shared connection."""
    return remote.ssh_argv(ssh_args, script, SSH_BINARY)


def master_check_argv(ssh_args):
    """Exit status 0 when the shared connection to this server is up."""
    return remote.master_check_argv(ssh_args, SSH_BINARY)


# ----- parsing ----------------------------------------------------------------

_DENIED = re.compile(r"permission denied", re.I)
_NO_DAEMON = re.compile(r"cannot connect to the docker daemon|is the docker daemon running", re.I)


def classify_error(text):
    """(kind, message) for what `docker` printed when it failed."""
    if _DENIED.search(text):
        return "denied", ("Docker is installed, but this user may not use it. Add the user "
                          "to the docker group (sudo usermod -aG docker $USER), then log in "
                          "again.")
    if _NO_DAEMON.search(text):
        return "daemon", "Docker is installed, but its daemon is not running."
    last = text.strip().splitlines()[-1] if text.strip() else "unknown error"
    return "other", f"Docker did not answer: {last}"


def parse_probe(text, now=None):
    """A DockerInfo from PROBE_SCRIPT's output, or None if it is not one
    (e.g. the connection failed before the script ran)."""
    now = time.time() if now is None else now
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("@@nodocker"):
            return DockerInfo(has_docker=False, checked=now)
        if line.startswith("@@ok"):
            value = line[4:].strip()
            running = int(value) if value.isdigit() else 0
            return DockerInfo(has_docker=True, running=running, checked=now)
        if line.startswith("@@error"):
            kind, message = classify_error("\n".join(lines[i + 1:]))
            return DockerInfo(has_docker=True, checked=now, error=kind, message=message)
    return None


def parse_labels(text):
    """{key: value} from docker's "k=v,k2=v2" label string. A value may
    itself contain commas: a part without "=" belongs to the previous one."""
    out = {}
    key = None
    for part in (text or "").split(","):
        if "=" in part:
            key, _, value = part.partition("=")
            key = key.strip()
            out[key] = value
        elif key is not None:
            out[key] += "," + part
    return out


_PORT = re.compile(r"^(?:(?P<ip>[\d.]+|\[?[0-9a-fA-F:]*\]?):(?P<host>\d+(?:-\d+)?)->)?"
                   r"(?P<port>\d+(?:-\d+)?)/(?P<proto>\w+)$")


def parse_ports(text):
    """Short port descriptions from docker's Ports column:
    "0.0.0.0:8080->80/tcp, :::8080->80/tcp, 443/tcp" -> ["8080→80", "443/tcp"].
    The same mapping on IPv4 and IPv6 is listed once; tcp is not spelled
    out for published ports."""
    out = []
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        m = _PORT.match(part)
        if m is None:
            item = part
        elif m.group("host"):
            proto = "" if m.group("proto") == "tcp" else "/" + m.group("proto")
            item = f"{m.group('host')}→{m.group('port')}{proto}"
        else:
            item = f"{m.group('port')}/{m.group('proto')}"
        if item not in out:
            out.append(item)
    return out


class Container:
    """One line of `docker ps -a --format '{{json .}}'`."""

    def __init__(self, d):
        self.id = str(d.get("ID", ""))
        # Names may list several (old links); the first is the container's.
        self.name = str(d.get("Names", "")).split(",")[0].strip() or self.id
        self.image = str(d.get("Image", ""))
        self.state = str(d.get("State", "")).lower()
        self.status = str(d.get("Status", ""))
        self.ports = parse_ports(d.get("Ports", ""))
        labels = d.get("Labels", "")
        self.labels = labels if isinstance(labels, dict) else parse_labels(labels)
        self.project = self.labels.get(COMPOSE_PROJECT, "")
        self.working_dir = self.labels.get(COMPOSE_WORKDIR, "")

    @property
    def level(self):
        """The status dot: "running" (green), "bad" (red: unhealthy,
        restarting, dead), "starting" (orange: health check not passed yet)
        or "stopped" (grey: exited, created, paused)."""
        status = self.status.lower()
        if self.state in ("restarting", "dead") or "unhealthy" in status:
            return "bad"
        if self.state == "running":
            return "starting" if "health: starting" in status else "running"
        return "stopped"

    @property
    def running(self):
        return self.state == "running"

    def key(self):
        return (self.id, self.name, self.image, self.state, self.status, tuple(self.ports),
                self.project)


def parse_ps(lines):
    """Containers from `docker ps -a --format '{{json .}}'` lines; lines that
    are not JSON objects are skipped."""
    out = []
    for line in lines:
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict):
            out.append(Container(d))
    return out


def group_by_project(containers):
    """[(project, working_dir, [containers])]: compose projects by name,
    containers by name within them, the ones without a project last."""
    groups = {}
    for c in containers:
        groups.setdefault(c.project, []).append(c)
    out = []
    for project in sorted((p for p in groups if p), key=str.lower):
        members = sorted(groups[project], key=lambda c: c.name.lower())
        workdir = next((c.working_dir for c in members if c.working_dir), "")
        out.append((project, workdir, members))
    if "" in groups:
        out.append((NO_PROJECT, "", sorted(groups[""], key=lambda c: c.name.lower())))
    return out


def split_blocks(text):
    """(section, lines) of one WATCH_SCRIPT block ("@@ps", "@@error", …)."""
    section, lines = None, []
    for line in text.splitlines():
        if line.startswith("@@") and section is None:
            section = line[2:].strip()
        elif section is not None:
            lines.append(line)
    return section, lines


# ----- running the scripts ------------------------------------------------------


class Probe(QObject):
    """Checks one server for Docker once its shared SSH connection is up
    (remote.RemoteScript). done(DockerInfo); gives up quietly when the
    connection never comes up."""

    done = pyqtSignal(object)

    def __init__(self, ssh_args, delays=None, parent=None):
        super().__init__(parent)
        self._script = remote.RemoteScript(
            ssh_args, PROBE_SCRIPT, parse=parse_probe,
            delays=PROBE_DELAYS if delays is None else delays,
            ssh_binary=lambda: SSH_BINARY, parent=self)
        self._script.output.connect(self.done)

    @property
    def finished(self):
        return self._script.finished

    def start(self):
        self._script.start()

    def stop(self):
        self._script.stop()


class Watch(QProcess):
    """Runs WATCH_SCRIPT: updated(list of Container), problem(kind, message)
    with kind "nodocker", "denied", "daemon", "other" or "connection"."""

    updated = pyqtSignal(list)
    problem = pyqtSignal(str, str)

    def __init__(self, argv, parent=None):
        super().__init__(parent)
        self._buf = ""
        self._stopping = False
        self.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.readyReadStandardOutput.connect(self._read)
        self.finished.connect(self._finished)
        self.errorOccurred.connect(self._error)
        self.start(argv[0], argv[1:])

    def _read(self):
        self._buf += bytes(self.readAllStandardOutput()).decode(errors="replace")
        while "@@end\n" in self._buf:
            block, self._buf = self._buf.split("@@end\n", 1)
            section, lines = split_blocks(block)
            if section == "ps":
                self.updated.emit(parse_ps(lines))
            elif section == "error":
                kind, message = classify_error("\n".join(lines))
                self.problem.emit(kind, message)
            elif section == "nodocker":
                self._stopping = True  # the script ends here; that is no failure
                self.problem.emit("nodocker", "There is no docker command on this server.")

    def _finished(self, *_):
        if self._stopping:
            return
        err = bytes(self.readAllStandardError()).decode(errors="replace").strip()
        last = err.splitlines()[-1] if err else "the connection ended"
        self.problem.emit("connection", last)

    def _error(self, err):
        if err == QProcess.ProcessError.FailedToStart and not self._stopping:
            self.problem.emit("connection", "Could not start ssh.")

    def stop(self):
        self._stopping = True
        if self.state() != QProcess.ProcessState.NotRunning:
            self.kill()
            self.waitForFinished(1000)
