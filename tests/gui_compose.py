"""GUI test: the compose files of a Docker project. A fake ssh runs
everything here and a fake docker lists containers of a project in a temp
folder whose name has a space (tests/fake_ssh_exec.py, fake_docker.py):
View compose (tabs, line numbers, Refresh, a missing file, not connected),
Edit compose, Apply (asks first), greyed actions for a container that is
not from compose, and the view as a panel like the others."""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from gui_harness import check, make_profile, run, term_text  # noqa: E402

from PyQt6.QtCore import QPoint  # noqa: E402
from PyQt6.QtWidgets import QLabel, QMenu  # noqa: E402

from bunnydeck import docker, remote, terminal  # noqa: E402
from bunnydeck.composeview import CodeView, ComposeViewPanel  # noqa: E402
from docker_sample import row  # noqa: E402

FAKE_SSH = os.path.join(HERE, "fake_ssh_exec.py")
terminal.SSH_BINARY = FAKE_SSH
docker.SSH_BINARY = FAKE_SSH
remote.SSH_BINARY = FAKE_SSH
docker.PROBE_DELAYS = (0.2, 0.4)
docker.WATCH_INTERVAL_S = 1

TMP = tempfile.mkdtemp()
PROJECT = os.path.join(TMP, "docker", "open webui")
os.makedirs(PROJECT)
MAIN = os.path.join(PROJECT, "compose.yml")
OVERRIDE = os.path.join(PROJECT, "compose.override.yml")
with open(MAIN, "w") as f:
    f.write("services:\n  open-webui:\n    image: ghcr.io/open-webui/open-webui:main  # UI\n"
            "    ports:\n      - \"3000:8080\"\n    restart: unless-stopped\n")
with open(OVERRIDE, "w") as f:
    f.write("services:\n  open-webui:\n    environment:\n      - DEBUG=true\n")


def compose_row(name, service):
    d = json.loads(row(name, "ghcr.io/open-webui/open-webui:main", "running", "Up 1 hour",
                       "0.0.0.0:3000->8080/tcp"))
    d["Labels"] = ("com.docker.compose.project=open-webui,"
                   f"com.docker.compose.project.working_dir={PROJECT},"
                   f"com.docker.compose.project.config_files={MAIN},{OVERRIDE},"
                   f"com.docker.compose.service={service}")
    return json.dumps(d)


PS = os.path.join(TMP, "ps.txt")
with open(PS, "w") as f:
    f.write("\n".join([compose_row("open-webui", "open-webui"),
                       row("solo", "alpine", "running", "Up 2 minutes")]) + "\n")
os.environ["FAKE_DOCKER_PS_FILE"] = PS


def geo(win, p):
    pos = p.mapTo(win.layout_, QPoint(0, 0))
    return pos.x(), pos.y(), p.width(), p.height()


MENUS = []  # kept alive: the actions die with their menu


def menu_for(dp, item):
    """The compose actions the Docker panel's menu offers for a row."""
    menu = QMenu()
    MENUS.append(menu)
    dp._add_compose_actions(menu, item)
    return {a.text(): a for a in menu.actions() if a.text()}


def test(app):
    win = app.window
    win.resize(1500, 950)
    srv = app.config.add(make_profile("srv", host="srv.example", user="demo"))
    win.refresh_connections()
    win.open_profile(srv)
    yield lambda: srv.docker.has_docker is True
    dp = win.open_docker(srv)
    yield lambda: dp.containers
    check(dp.projects == {"open-webui": (PROJECT, [MAIN, OVERRIDE])},
          f"project folder and both files from the labels ({dp.projects})")

    # Greyed for a container that is not from compose
    acts = menu_for(dp, dp.item_for("solo"))
    check(set(acts) == {"View compose", "Edit compose", "Edit With",
                        "Edit in terminal ($EDITOR)", "Apply (up -d)"}, str(set(acts)))
    check(not any(a.isEnabled() for a in acts.values()), "greyed for a non-compose container")
    check("not started by docker compose" in acts["View compose"].toolTip(), "with a reason")
    acts = menu_for(dp, dp.item_for("open-webui"))
    check(all(a.isEnabled() for a in acts.values()), "enabled for a compose container")

    # Double-click the project: View compose below the Docker panel
    top = dp.tree.topLevelItem(0)
    check(top.text(0) == "open-webui", "the project row")
    dp._double_clicked(top, 0)
    view = next(p for p in win.all_panels() if isinstance(p, ComposeViewPanel))
    yield lambda: view.results is not None
    yield 0.3
    check([view.tabs.tabText(i) for i in range(view.tabs.count())]
          == ["compose.yml", "compose.override.yml"], "one tab per file")
    page = view.tabs.widget(0)
    check(isinstance(page, CodeView) and page.isReadOnly(), "read-only text")
    check(page.toPlainText().startswith("services:\n  open-webui:"), "the file's content")
    check(page.gutter.width() > 10, "with line numbers")
    check(view.path_label.text() == MAIN, "the path in the header")
    view.tabs.setCurrentIndex(1)
    check(view.path_label.text() == OVERRIDE, "and of the tab shown")
    vx, vy, _, _ = geo(win, view)
    dx, dy, _, dh = geo(win, dp)
    check(abs(vx - dx) < 3 and vy >= dy + dh - 2, "below the Docker panel")
    check(win._session_entry(view) is None, "not saved in the session")

    # Refresh: a file that is gone is said so
    os.rename(OVERRIDE, OVERRIDE + ".bak")
    view.refresh_btn.click()
    yield lambda: view.results and view.results[1][1] == "missing"
    page = view.tabs.widget(1)
    check(isinstance(page, QLabel) and "no longer on the server" in page.text(),
          "a missing file is said clearly")
    check(view.status_dot.property("state") == "error", "and the dot turns red")
    os.rename(OVERRIDE + ".bak", OVERRIDE)

    # Opening it again shows the same panel
    n = len(win.all_panels())
    dp.composeViewRequested.emit(dp, "open-webui")
    check(len(win.all_panels()) == n, "no second view of the same project")
    yield lambda: view.results and view.results[1][1] == "ok"
    check(True, "refreshed instead")

    # A panel like the others
    win.detach_panel(view)
    check(view in win.detached, "it detaches")
    win.attach_panel(view)
    check(view in win.layout_.panels, "and attaches back")

    # Not connected
    os.environ["FAKE_SSH_NO_MASTER"] = "1"
    view.refresh()
    yield lambda: "not connected" in view.message.text()
    check(True, "not connected is said clearly")
    del os.environ["FAKE_SSH_NO_MASTER"]

    # Edit compose: the server's $EDITOR in the project folder, in a terminal
    os.environ["EDITOR"] = "printf EDITOR:%s\\n"
    ed = win._compose_edit(dp, "open-webui")
    yield lambda: "EDITOR:" in term_text(ed)
    text = term_text(ed)
    check(f"EDITOR:{MAIN}" in text and f"EDITOR:{OVERRIDE}" in text,
          "the editor gets both files (paths with a space intact)")
    check(ed.name_label.text() == "Edit: open-webui", "titled Edit")

    # Apply: asks first, then runs up -d in a terminal
    asked = []
    win._confirm = lambda *a: asked.append(a) or False
    check(win._compose_apply(dp, "open-webui") is None and asked, "Apply asks; Cancel stops")
    check("up -d" in asked[0][1], "showing the command")
    win._confirm = lambda *a: True
    ap = win._compose_apply(dp, "open-webui")
    yield lambda: "FAKE-DOCKER-COMPOSE" in term_text(ap)
    check(f"FAKE-DOCKER-COMPOSE -p open-webui -f {MAIN} -f {OVERRIDE} up -d"
          in term_text(ap).replace("\n", ""), "docker compose up -d with both files")

    win.close_panel(view)
    check(view not in win.all_panels(), "closes like the others")


if __name__ == "__main__":
    sys.exit(run(test))
