"""GUI test: compose files edited in a local editor. Everything runs here:
a local sftp-server stands in for the server's SFTP (as in gui_edit.py), a
fake ssh and docker for the rest (tests/fake_ssh_exec.py, fake_docker.py),
tests/fake_editor.py for the editor (the test "saves" by writing the copy).

Covered: both files open, each on its own; a save is uploaded; the compose
view refreshes; a toast offers Apply, which asks and never applies on its
own; Edit With (an editor, Custom Command…); a file that cannot be written
offers Edit in Terminal; the view's Edit button; not connected."""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
BASE = tempfile.mkdtemp(prefix="bunnydeck-compose-edit-")
os.environ["XDG_CACHE_HOME"] = os.path.join(BASE, "cache")  # never ~/.cache

from gui_harness import check, make_profile, run, term_text  # noqa: E402

from PyQt6.QtWidgets import QMenu  # noqa: E402

from bunnydeck import docker, editors, remote, sftp, terminal  # noqa: E402
from bunnydeck.composeview import ComposeViewPanel  # noqa: E402
from docker_sample import row  # noqa: E402
from openssh_paths import SFTP_SERVER  # noqa: E402

FAKE_SSH = os.path.join(HERE, "fake_ssh_exec.py")
terminal.SSH_BINARY = docker.SSH_BINARY = remote.SSH_BINARY = FAKE_SSH
docker.PROBE_DELAYS = (0.2, 0.4)
docker.WATCH_INTERVAL_S = 1
sftp.ssh_sftp_argv = lambda profile: [SFTP_SERVER, "-d", BASE]
FAKE_EDITOR = os.path.join(HERE, "fake_editor.py")
LOGS = {n: os.path.join(BASE, f"editor{n}.log") for n in (1, 2, 3)}

PROJECT = os.path.join(BASE, "docker", "open webui")
os.makedirs(PROJECT)
MAIN = os.path.join(PROJECT, "compose.yml")
OVERRIDE = os.path.join(PROJECT, "compose.override.yml")
with open(MAIN, "w") as f:
    f.write("services:\n  open-webui:\n    image: ghcr.io/open-webui/open-webui:main\n")
with open(OVERRIDE, "w") as f:
    f.write("services:\n  open-webui:\n    environment:\n      - DEBUG=false\n")

d = json.loads(row("open-webui", "ghcr.io/open-webui/open-webui:main", "running", "Up 1 hour"))
d["Labels"] = ("com.docker.compose.project=open-webui,"
               f"com.docker.compose.project.working_dir={PROJECT},"
               f"com.docker.compose.project.config_files={MAIN},{OVERRIDE}")
PS = os.path.join(BASE, "ps.txt")
with open(PS, "w") as f:
    f.write(json.dumps(d) + "\n")
os.environ["FAKE_DOCKER_PS_FILE"] = PS
MENUS = []


def editor_cmd(n):
    return f"{sys.executable} {FAKE_EDITOR} {LOGS[n]}"


def opened(n):
    try:
        with open(LOGS[n]) as f:
            return f.read().splitlines()
    except OSError:
        return []


def read(path):
    with open(path) as f:
        return f.read()


def save(local, text):
    with open(local, "w") as f:
        f.write(text)


def test(app):
    win = app.window
    win.resize(1500, 950)
    app.config.set_editor("command:" + editor_cmd(1))
    srv = app.config.add(make_profile("srv", host="srv.example", user="demo"))
    win.refresh_connections()
    win.open_profile(srv)
    yield lambda: srv.docker.has_docker is True
    dp = win.open_docker(srv)
    yield lambda: dp.projects
    view = win.open_compose_view(dp, "open-webui")
    yield lambda: view.results is not None

    # The menu: Edit (local), Edit With ▸ (editors + Custom Command…), terminal
    menu = QMenu()
    MENUS.append(menu)
    dp._add_compose_actions(menu, dp.item_for("open-webui"))
    texts = [a.text() for a in menu.actions() if a.text()]
    check(texts[:4] == ["View compose", "Edit compose", "Edit With",
                        "Edit in terminal ($EDITOR)"], f"the compose menu ({texts})")
    with_ = next(a.menu() for a in menu.actions() if a.text() == "Edit With")
    check(with_.actions()[-1].text() == "Custom Command…", "Edit With ends in Custom Command…")

    # Edit compose: each file opens on its own in Bunny Deck's editor
    dp.composeEditRequested.emit(dp, "open-webui", None)
    yield lambda: len(opened(1)) == 2
    copies = opened(1)
    check(sorted(os.path.basename(c) for c in copies) == ["compose.override.yml", "compose.yml"],
          f"both files opened, each on its own ({copies})")
    check(all(c.startswith(os.environ["XDG_CACHE_HOME"]) for c in copies),
          "local copies in the edit cache")
    main_copy = next(c for c in copies if c.endswith("/compose.yml"))

    # A save is uploaded; the view refreshes; a toast offers Apply
    save(main_copy, read(main_copy).replace("main", "v0.5"))
    yield lambda: "v0.5" in read(MAIN)
    check(True, "the save reached the server (the same safe upload as Files)")
    yield lambda: view.results and "v0.5" in view.results[0][2]
    check("v0.5" in view.tabs.widget(0).toPlainText(), "the open compose view refreshed")
    yield lambda: win.toast.isVisible() and win.toast.action_btn.isVisibleTo(win.toast)
    check(win.toast.action_btn.text() == "Apply (up -d)" and "compose.yml" in
          win.toast.label.text(), f"a toast offers Apply ({win.toast.label.text()})")
    n = len(win.all_panels())
    check(not any(p.name_label.text().startswith("Apply") for p in win.all_panels()),
          "nothing applied on its own")
    asked = []
    win._confirm = lambda *a: asked.append(a) or False
    win.toast.action_btn.click()
    check(asked and "up -d" in asked[0][1] and len(win.all_panels()) == n,
          "Apply asks first; Cancel runs nothing")
    save(main_copy, read(main_copy) + "# again\n")
    yield lambda: "# again" in read(MAIN) and win.toast.action_btn.isVisibleTo(win.toast)
    win._confirm = lambda *a: True
    win.toast.action_btn.click()
    ap = win.all_panels()[-1]
    yield lambda: "FAKE-DOCKER-COMPOSE" in term_text(ap)
    check(f"-p open-webui -f {MAIN} -f {OVERRIDE} up -d" in term_text(ap).replace("\n", ""),
          "confirmed: up -d with both files, in a terminal")

    # Edit With: a chosen editor, and Custom Command…
    chosen = editors.resolve("command:" + editor_cmd(2))
    dp.composeEditRequested.emit(dp, "open-webui", chosen)
    yield lambda: len(opened(2)) == 2
    check(True, "Edit With an editor opens both files in that one")
    win.editing.ask_command = lambda title: editor_cmd(3)
    menu = QMenu()
    MENUS.append(menu)
    win._fill_edit_with(menu, lambda e: dp.composeEditRequested.emit(dp, "open-webui", e))
    menu.actions()[-1].trigger()
    yield lambda: len(opened(3)) == 2
    check(True, "Edit With ▸ Custom Command… asks for a command and uses it")

    # The view's Edit button: the file shown
    view.tabs.setCurrentIndex(1)
    before = len(opened(1))
    view.edit_btn.click()
    yield lambda: len(opened(1)) == before + 1
    check(opened(1)[-1].endswith("/compose.override.yml"), "the view's Edit opens its tab's file")

    # A file that cannot be written: said clearly, Edit in Terminal offered
    os.chmod(OVERRIDE, 0o444)
    asked.clear()
    win._confirm = lambda *a: asked.append(a) or True
    os.environ["EDITOR"] = "printf EDITOR:%s\\n"
    before = len(opened(1))
    dp.composeEditRequested.emit(dp, "open-webui", None)
    yield lambda: asked
    check("compose.override.yml may not be written" in asked[0][1]
          and asked[0][2] == "Edit in Terminal", f"read-only explained ({asked[0][1]})")
    ed = win.all_panels()[-1]
    yield lambda: "EDITOR:" in term_text(ed)
    check(f"EDITOR:{OVERRIDE}" in term_text(ed) and f"EDITOR:{MAIN}" not in term_text(ed),
          "Edit in Terminal opens only that file there")
    yield lambda: len(opened(1)) == before + 1
    check(opened(1)[-1].endswith("/compose.yml"), "the writable one still opens locally")
    os.chmod(OVERRIDE, 0o644)

    # Not connected: nothing is opened, no login is tried
    errors = []
    win.editing._message_box = lambda title, text: errors.append(text)
    os.environ["FAKE_SSH_NO_MASTER"] = "1"
    before = len(opened(1))
    dp.composeEditRequested.emit(dp, "open-webui", None)
    yield lambda: errors
    check("not connected" in errors[0], f"not connected is said ({errors[0]})")
    check(len(opened(1)) == before, "and nothing is opened")
    del os.environ["FAKE_SSH_NO_MASTER"]

    check(win.editing.unsaved() == [], "every save reached the server")
    check(win.file_manager is None, "all without opening Files")
    win.open_file_manager(dp)
    fm = win.file_manager
    check(fm.edits is win.editing.edits and fm.pool is win.editing.pool,
          "Files uses the same edits and SFTP connection")
    check(len(fm.edits.sessions) == 2, "and sees the compose files being edited")


if __name__ == "__main__":
    sys.exit(run(test))
