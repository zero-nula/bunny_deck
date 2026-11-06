"""GUI test: Docker support. A fake ssh runs everything here and a fake
docker answers (tests/fake_ssh_exec.py, tests/fake_docker.py): the check
after login, the sidebar whale, the Docker panel (tree, placement, moving,
hiding), shells and logs as terminals, the session, and the errors.
No real server, ssh or Docker is used."""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from gui_harness import check, make_profile, run, term_text  # noqa: E402

from PyQt6.QtCore import QPoint  # noqa: E402

from bunnydeck import docker, session, terminal  # noqa: E402
from bunnydeck.config import Config  # noqa: E402
from bunnydeck.dockerpanel import DockerPanel  # noqa: E402
from bunnydeck.sidebar import ROLE_DOCKER, ROLE_OPEN  # noqa: E402
from docker_sample import PS_LINES, RUNNING  # noqa: E402

FAKE_SSH = os.path.join(HERE, "fake_ssh_exec.py")
terminal.SSH_BINARY = FAKE_SSH
docker.SSH_BINARY = FAKE_SSH
docker.PROBE_DELAYS = (0.2, 0.4, 0.6)
docker.WATCH_INTERVAL_S = 1
LOG = os.path.join(tempfile.mkdtemp(), "ssh.log")
os.environ["FAKE_SSH_LOG"] = LOG


def ssh_calls():
    try:
        with open(LOG) as f:
            return [json.loads(line) for line in f]
    except OSError:
        return []


def geo(win, p):
    pos = p.mapTo(win.layout_, QPoint(0, 0))
    return pos.x(), pos.y(), p.width(), p.height()


def test(app):
    win = app.window
    win.resize(1500, 900)

    # Logging in checks for Docker, over the shared connection
    srv = app.config.add(make_profile("srv", host="srv.example", user="demo"))
    win.refresh_connections()
    term = win.open_profile(srv)
    yield lambda: srv.docker.has_docker is True
    check(srv.docker.running == RUNNING and srv.docker.usable, "Docker found, containers counted")
    check(Config().get(srv.id).docker.running == RUNNING, "the result is kept in the config")
    calls = ssh_calls()
    check(any("-O" in c and "check" in c for c in calls), "first asks whether the master is up")
    probe = [c for c in calls if "BatchMode=yes" in c]
    check(probe and all("ControlMaster=no" in c for c in probe), "never a master, never asks")
    info = win.sidebar.item_for(srv.id).data(ROLE_DOCKER)
    check(info is not None and info.running == RUNNING, "the sidebar shows the whale and count")
    check(term.has_docker(), "the terminal menu offers Docker…")

    # The Docker panel: right of the terminal, the tree by project
    dp = win.open_docker(srv)
    check(isinstance(dp, DockerPanel) and dp in win.layout_.panels, "Docker panel opened")
    yield lambda: dp.containers
    yield 0.3
    tx, ty, tw, th = geo(win, term)
    dx, dy, dw, dh = geo(win, dp)
    check(dx > tx and dy == ty and dh == th, f"right of its terminal ({geo(win, term)} {geo(win, dp)})")
    tops = [dp.tree.topLevelItem(i).text(0) for i in range(dp.tree.topLevelItemCount())]
    check(tops == ["ai", "web", docker.NO_PROJECT], f"grouped by compose project ({tops})")
    check(dp.tree.topLevelItem(0).toolTip(0) == "/home/demo/docker/ai", "project folder shown")
    db = dp.item_for("db")
    check(db is not None and db.text(1) == "postgres:16" and db.text(3) == "5432/tcp",
          "name, image, ports")
    check(dp.count_label.text() == f"{RUNNING}/{len(PS_LINES)} running", dp.count_label.text())
    check(win.open_docker(srv) is dp, "opening again shows the same panel")
    check(win.sidebar.item_for(srv.id).data(ROLE_OPEN) == 1, "the panel is not counted as a terminal")

    # Only while visible
    check(dp.watching, "watching while visible")
    dp.hide()
    check(not dp.watching, "hidden: the loop on the server stops")
    dp.show()
    yield lambda: dp.watching
    check(True, "shown again: watching again")

    # Shell and logs open as terminals below the panel
    n = len(win.layout_.panels)
    dp._double_clicked(dp.item_for("ollama"), 0)
    sh = win.layout_.panels[-1]
    check(len(win.layout_.panels) == n + 1 and sh.is_terminal, "double-click opens a terminal")
    check(sh.name_label.text() == "🐳 ollama", f"named after the container ({sh.name_label.text()})")
    yield lambda: "FAKE-DOCKER-EXEC" in term_text(sh)
    check("FAKE-DOCKER-EXEC -it ollama sh -c command -v bash" in term_text(sh),
          "docker exec with bash, else sh")
    yield 0.3
    sx, sy, _, _ = geo(win, sh)
    _, dy2, _, dh2 = geo(win, dp)
    check(sy >= dy2 + dh2 - 2 and abs(sx - geo(win, dp)[0]) < 3, "below the Docker panel")
    check("-t" in sh.argv, "with a terminal (-t)")

    nasty = "weird'name;x"
    dp.logsRequested.emit(dp, nasty)
    logs = win.layout_.panels[-1]
    yield lambda: "FAKE-DOCKER-LOGS" in term_text(logs)
    check(f"FAKE-DOCKER-LOGS -f --tail 200 {nasty}" in term_text(logs),
          "logs of a container with a nasty name, quoted")
    dp.projectLogsRequested.emit(dp, "ai")
    plogs = win.layout_.panels[-1]
    yield lambda: "FAKE-DOCKER-COMPOSE" in term_text(plogs)
    check("FAKE-DOCKER-COMPOSE -p ai logs -f --tail 200" in term_text(plogs), "project logs")

    # It moves like a terminal: drop it on the middle of the terminal to swap
    before = geo(win, term)
    centre = term.mapToGlobal(term.rect().center())
    win._on_header_drag(dp, centre, "start")
    win._on_header_drag(dp, centre, "drop")
    yield 0.3
    check(geo(win, dp)[:2] == before[:2], "dragged by its header onto the terminal: swapped")

    # Session: the panel is kept, container shells and logs are not
    data = session.encode(win.layout_.describe(), win._session_entry, win.active_panel)
    text = json.dumps(data)
    check('"kind": "docker"' in text, "the Docker panel is saved in the session")
    check(text.count('"kind": "ssh"') == 1, "shells and logs are not saved")

    # Permission denied: a clear message, not an error
    os.environ["FAKE_DOCKER_MODE"] = "denied"
    locked = app.config.add(make_profile("locked", host="locked.example"))
    win.refresh_connections()
    win.open_profile(locked)
    yield lambda: locked.docker.has_docker is True
    check(locked.docker.error == "denied" and "docker group" in locked.docker.message,
          "permission denied explained")
    item = win.sidebar.item_for(locked.id)
    check(item.data(ROLE_DOCKER) is not None and not item.data(ROLE_DOCKER).usable,
          "sidebar: a grey whale without a count")
    lp = win.open_docker(locked)
    yield lambda: lp.problem is not None
    check(lp.problem[0] == "denied" and "docker group" in lp.message.text(),
          "the panel shows the explanation")
    os.environ["FAKE_DOCKER_MODE"] = "ok"

    # No connection yet: only `-O check`, never a login of its own
    os.environ["FAKE_SSH_NO_MASTER"] = "1"
    off = app.config.add(make_profile("off", host="off.example"))
    probe = win.probe_docker(off)
    yield lambda: probe.finished
    mine = [c for c in ssh_calls() if "off.example" in c]
    check(mine and all("-O" in c for c in mine), "waits for the master, never logs in itself")
    check(off.docker.has_docker is None, "nothing is known then")
    del os.environ["FAKE_SSH_NO_MASTER"]

    # Closing the panel stops it
    win.close_panel(dp)
    check(not dp.watching and dp not in win.layout_.panels, "closed: stopped and gone")


if __name__ == "__main__":
    run(test)
