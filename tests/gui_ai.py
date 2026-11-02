"""GUI test: the AI toolbar menu. Lists only the AI tools the user added (no
auto-detection), adds and removes them, runs the chosen one in a local
terminal (with ~/.bashrc's PATH), and leaves the user's shell when it exits."""

import atexit
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gui_harness import check, run, term_text  # noqa: E402

from bunnydeck.config import Config  # noqa: E402

# A throwaway $HOME with a fake "claude" in PATH and a fake "opencode" whose
# directory is added to PATH only in ~/.bashrc, as its installer does.
HOME = tempfile.mkdtemp(prefix="bunnydeck-test-home-")
atexit.register(shutil.rmtree, HOME, True)
BIN = os.path.join(HOME, "bin")
RC_BIN = os.path.join(HOME, ".opencode", "bin")
for d, name in ((BIN, "claude"), (RC_BIN, "opencode")):
    os.makedirs(d)
    with open(os.path.join(d, name), "w") as f:
        f.write(f"#!/bin/sh\necho FAKE-{name.upper()} \"$@\"\n")
    os.chmod(os.path.join(d, name), 0o755)
with open(os.path.join(HOME, ".bashrc"), "w") as f:
    f.write(f'export PATH="{RC_BIN}:$PATH"\n')
os.environ["HOME"] = HOME
os.environ["SHELL"] = "/bin/bash"
os.environ["PATH"] = BIN + ":/usr/bin:/bin"


def menu_labels(menu):
    return [a.text() for a in menu.actions() if a.text()]


def test(app):
    win = app.window
    check(win.ai_button.accessibleName() == "AI assistants" and win.ai_button.menu(),
          "toolbar has an AI menu")
    check(menu_labels(win.ai_menu) == ["No AI tools added", "Add…", "Remove"],
          f"installed tools are not listed until added ({menu_labels(win.ai_menu)})")

    # Add through the dialog: the name defaults from the command.
    from bunnydeck.dialogs import AddAIToolDialog
    dlg = AddAIToolDialog(win)
    dlg.accept()
    check(dlg.tool is None and dlg.error.text(), "empty command is rejected")
    dlg.command.setText("claude")
    dlg.accept()
    check(dlg.tool and dlg.tool.name == "Claude", "name defaults from the command")
    win.config.add_ai_tool(dlg.tool)
    dlg = AddAIToolDialog(win)
    dlg.command.setText("opencode")
    dlg.name.setText("OpenCode")
    dlg.accept()
    win.config.add_ai_tool(dlg.tool)
    win.rebuild_ai_menu()
    check(menu_labels(win.ai_menu) == ["Claude", "OpenCode", "Add…", "Remove"],
          f"added tools are listed ({menu_labels(win.ai_menu)})")
    check([t.name for t in Config().ai_tools] == ["Claude", "OpenCode"], "tools are saved")

    win.ai_menu.actions()[0].trigger()
    yield lambda: win.layout_.panels and win.layout_.panels[0].child_pid
    p = win.layout_.panels[0]
    check(p.profile.is_local and p.profile.name == "Claude", "panel is a local 'Claude' terminal")
    yield lambda: "FAKE-CLAUDE" in term_text(p)
    check("FAKE-CLAUDE" in term_text(p), "claude ran in the terminal")

    # After the tool exits the shell takes over in the same terminal.
    p.terminal.sendText("echo AFTER-$((40+2))\n")
    yield lambda: "AFTER-42" in term_text(p)
    check("AFTER-42" in term_text(p) and p.running, "shell continues after the tool exits")

    win.ai_menu.actions()[1].trigger()
    p2 = win.layout_.panels[-1]
    yield lambda: "FAKE-OPENCODE" in term_text(p2)
    check("FAKE-OPENCODE" in term_text(p2), "command found via ~/.bashrc's PATH")

    claude = win.config.ai_tools[0]
    check(win.open_ai(claude).profile is p.profile, "terminals of one tool share a profile")

    # Remove (confirmation accepted).
    win._confirm = lambda *a: True
    remove_menu = [a for a in win.ai_menu.actions() if a.text() == "Remove"][0].menu()
    remove_menu.actions()[0].trigger()
    check(menu_labels(win.ai_menu) == ["OpenCode", "Add…", "Remove"],
          f"removed tool is gone from the menu ({menu_labels(win.ai_menu)})")
    check([t.name for t in Config().ai_tools] == ["OpenCode"], "removal is saved")
    check(p.running, "open terminal of a removed tool keeps running")


if __name__ == "__main__":
    run(test)
