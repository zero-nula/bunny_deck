"""GUI test: the firewall window. A fake ssh runs everything here, with a
fake sudo (no password) and a fake ufw that keeps its rules in a temp file
(tests/fake_ssh_exec.py, fake_sudo.py, fake_ufw.py). No real server, sudo or
firewall is used."""

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from gui_harness import check, make_profile, run, term_text  # noqa: E402

from PyQt6.QtCore import QPoint  # noqa: E402

from bunnydeck import docker, firewall, remote, terminal  # noqa: E402

FAKE_SSH = os.path.join(HERE, "fake_ssh_exec.py")
terminal.SSH_BINARY = FAKE_SSH
docker.SSH_BINARY = FAKE_SSH
remote.SSH_BINARY = FAKE_SSH
docker.PROBE_DELAYS = (0.2, 0.4)
remote.DELAYS = (0, 0.3, 0.6)
firewall.SHELL_COMMAND = "exec sh -i"
TMP = tempfile.mkdtemp()
STATE = os.path.join(TMP, "ufw.json")
CONF = os.path.join(TMP, "ufw.conf")
SUDO_FAIL = os.path.join(TMP, "sudo-fails")
LOG = os.path.join(TMP, "ssh.log")
with open(CONF, "w") as f:
    f.write("# ufw.conf\nENABLED=yes\nLOGLEVEL=low\n")
firewall.UFW_CONF = CONF
os.environ.update(FAKE_UFW_STATE=STATE, FAKE_SUDO_FAIL_FILE=SUDO_FAIL, FAKE_SSH_LOG=LOG)


def set_state(**kw):
    try:
        with open(STATE) as f:
            state = json.load(f)
    except OSError:
        state = {"active": True, "rules": [{"to": "22/tcp", "from": "Anywhere",
                                            "comment": "ssh"}]}
    state.update(kw)
    with open(STATE, "w") as f:
        json.dump(state, f)


def geo(win, p):
    pos = p.mapTo(win.layout_, QPoint(0, 0))
    return pos.x(), pos.y(), p.width(), p.height()


def test(app):
    win = app.window
    win.resize(1400, 900)
    set_state()

    srv = app.config.add(make_profile("srv", host="srv.example", user="demo"))
    win.refresh_connections()
    dlg = win.open_firewall(srv, port=3000)
    check(dlg.isVisible() and dlg.port.text() == "3000", "window opens, port filled in")
    main = [p for p in win.terminals() if p.remote_command is None]
    fw = win._fw_terminals.get(srv.id)
    check(len(main) == 1 and fw is not None and fw.name_label.text() == "Firewall: srv",
          "the server's terminal and its Firewall terminal")
    yield lambda: dlg.info is not None
    check(dlg.info.installed and dlg.info.enabled is True, "ufw installed and enabled (no sudo)")
    calls = [json.loads(line) for line in open(LOG)]
    info_calls = [c for c in calls if any("@@enabled" in a for a in c)]
    check(info_calls and all("BatchMode=yes" in c and "ControlMaster=no" in c
                             for c in info_calls), "info over the shared connection only")
    yield lambda: dlg.status is not None
    yield 0.2
    mx, my, mw, mh = geo(win, main[0])
    fx, fy, _, _ = geo(win, fw)
    check(fx == mx and fy >= my + mh - 2, "the Firewall terminal is below the server's")
    check([r.to for r in dlg.status.rules] == ["22/tcp"], "the rules are read from the terminal")
    check(dlg.rules.topLevelItemCount() == 1 and dlg.rules.topLevelItem(0).text(4) == "ssh",
          "and listed in the window")
    check(dlg.banner.property("level") == "ok", "banner: active")
    check("sudo ufw status numbered" in term_text(fw), "the user sees the command typed there")

    # The form: preview, checks, the Anywhere warning
    dlg.ip.setText("10.0.0.0/8")
    check(dlg.src_ip.isChecked(), "typing an address picks IP or network")
    dlg.comment.setText("Open WebUI")
    expected = "sudo ufw allow from 10.0.0.0/8 to any port 3000 proto tcp comment 'Open WebUI'"
    check(dlg.preview.text() == expected, f"the exact command is shown ({dlg.preview.text()})")
    check(dlg.allow_btn.isEnabled(), "Allow is enabled")
    dlg.port.setText("3000; reboot")
    check(not dlg.allow_btn.isEnabled() and dlg.error.text(), "a bad port is refused")
    dlg.port.setText("3000")
    dlg.comment.setText("it's mine")
    check(not dlg.allow_btn.isEnabled(), "a comment with a quote is refused")
    dlg.comment.setText("Open WebUI")
    dlg.src_any.setChecked(True)
    check(dlg.any_warning.isVisibleTo(dlg), "Anywhere shows a warning")
    check("from any" in dlg.preview.text(), "anywhere is 'from any'")
    dlg.protocol.setCurrentIndex(2)
    dlg.port.setText("8000:8010")
    check(dlg.preview.text().count("\n") == 1, "a range with TCP and UDP: two commands")
    dlg.port.setText("3000")
    dlg.protocol.setCurrentIndex(0)
    dlg.src_ip.setChecked(True)

    # Allow: typed into the terminal, the rules come back
    dlg.allow_btn.click()
    yield lambda: dlg.status and len(dlg.status.rules) == 2
    r = dlg.status.rules[1]
    check((r.to, r.source, r.comment) == ("3000/tcp", "10.0.0.0/8", "Open WebUI"),
          "the rule was added and listed")

    # Delete: ufw asks (y|n) in the terminal
    dlg.rules.setCurrentItem(dlg.rules.topLevelItem(1))
    check(dlg.delete_btn.isEnabled(), "Delete enabled for a selected rule")
    dlg.delete_selected()
    yield lambda: "Proceed with operation" in term_text(fw)
    check(len(dlg.status.rules) == 2, "nothing deleted before the user confirms")
    fw.terminal.sendText("y\n")
    yield lambda: len(dlg.status.rules) == 1
    check(True, "confirmed with y in the terminal: deleted")

    # sudo fails: said in the window
    open(SUDO_FAIL, "w").close()
    dlg.refresh()
    yield lambda: "incorrect password" in dlg.rules_note.text()
    check(True, "a failed sudo is shown")
    os.unlink(SUDO_FAIL)

    # ufw inactive
    set_state(active=False)
    dlg.refresh()
    yield lambda: dlg.status.active is False
    check(dlg.banner.property("level") == "warn" and "not enabled" in dlg.banner.text(),
          "inactive: a clear warning")
    set_state(active=True)

    # Reopening uses the same window and terminal
    n = len(win.layout_.panels)
    check(win.open_firewall(srv, port=8080) is dlg and dlg.port.text() == "8080",
          "the same window, with the new port")
    check(len(win.layout_.panels) == n, "no new terminal")

    # ufw missing on another server
    os.environ["FAKE_SSH_HIDE"] = "ufw"
    bare = app.config.add(make_profile("bare", host="bare.example"))
    d2 = win.open_firewall(bare)
    yield lambda: d2.info is not None
    check(not d2.info.installed and d2.banner.property("level") == "bad"
          and "not installed" in d2.banner.text(), "ufw not installed: said clearly")
    d2.port.setText("80")
    check(not d2.allow_btn.isEnabled(), "and nothing can be allowed")
    del os.environ["FAKE_SSH_HIDE"]
    d2.close()
    check(bare.id not in win._firewalls, "closing the window forgets it")


if __name__ == "__main__":
    run(test)
