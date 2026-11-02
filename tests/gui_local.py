"""GUI test: "Local terminal" toolbar action. Runs $SHELL (no arguments) in
$HOME, has no SFTP/file manager, and leaves SSH sessions as they were."""

import atexit
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gui_harness import check, make_profile, run, term_text  # noqa: E402

from bunnydeck.filemanager import LOCAL_ID  # noqa: E402
from bunnydeck import sshmux  # noqa: E402

# A throwaway $HOME (no rc files) and a known shell, set before the app starts.
HOME = tempfile.mkdtemp(prefix="bunnydeck-test-home-")
atexit.register(shutil.rmtree, HOME, True)
os.environ["HOME"] = HOME
os.environ["SHELL"] = "/bin/sh"


def cmdline(pid):
    with open(f"/proc/{pid}/cmdline", "rb") as f:
        return [a.decode() for a in f.read().split(b"\0") if a]


def test(app):
    win = app.window
    check(win.local_btn.accessibleName() == "Local terminal", "toolbar has a Local terminal action")

    win.local_btn.click()
    yield lambda: win.layout_.panels and win.layout_.panels[0].child_pid
    p = win.layout_.panels[0]
    check(p.profile.is_local and p.running, "local terminal started")
    check(p.argv == ["/bin/sh"], f"argv is $SHELL without arguments ({p.argv})")
    check(cmdline(p.child_pid) == ["/bin/sh"], f"process cmdline {cmdline(p.child_pid)}")
    check(os.readlink(f"/proc/{p.child_pid}/cwd") == os.path.realpath(HOME),
          "working directory is $HOME")
    check(win.files_btn.isVisible(), "Files button in the toolbar")
    check(p.target_label.text() == "local", "header shows 'local'")

    p.terminal.sendText("echo LOCAL-$((40+2))\n")
    yield lambda: "LOCAL-42" in term_text(p)
    check("LOCAL-42" in term_text(p), "local shell runs commands")

    # SCP on a local terminal: file manager with this computer in both panes,
    # and a local -> local copy works.
    src_dir = os.path.join(HOME, "src")
    os.mkdir(src_dir)
    with open(os.path.join(src_dir, "a.txt"), "w") as f:
        f.write("local copy\n")
    p.scpRequested.emit(p)
    fm = win.file_manager
    check(fm is not None and win.current_view() == "files", "SCP opens the file manager")
    check(fm.left.source_id == LOCAL_ID and fm.right.source_id == LOCAL_ID,
          "both panes show this computer")
    yield lambda: fm.left.cwd and fm.right.cwd
    fm.right.load(src_dir)
    yield lambda: fm.right.cwd == src_dir
    fm.start_transfer(fm.right.backend, [os.path.join(src_dir, "a.txt")], fm.left)
    yield lambda: not fm.busy
    dst = os.path.join(fm.left.cwd, "a.txt")
    check(os.path.isfile(dst) and open(dst).read() == "local copy\n",
          f"local -> local copy works ({dst})")
    win.show_terminals()

    # An SSH session next to it is unaffected and still offers SFTP.
    s = win.open_profile(make_profile("Refused", host="127.0.0.1", port=1))
    check(s.argv == ["/usr/bin/ssh", *sshmux.master_opts(), "-p", "1", "-l", "tester", "--",
                     "127.0.0.1"], f"ssh argv: shared connection + target ({s.argv})")
    check(not hasattr(s, "scp_btn"), "no Files button in the terminal header")
    win.open_file_manager(s)
    check(win.current_view() == "files", "file manager opens for an SSH terminal")
    check(fm.right.source_id == s.profile.id, "SSH terminal's SCP selects its host")
    ids = [fm.left.source.itemData(i) for i in range(fm.left.source.count())]
    check(p.profile.id not in ids and s.profile.id in ids,
          "local terminal not offered as an SFTP source")
    win.show_terminals()

    # A second local terminal shares the profile (appearance applies to both).
    q = win.open_local()
    yield lambda: q.child_pid
    check(q.profile is p.profile and q.child_pid != p.child_pid,
          "second local terminal is an independent process")

    p.terminal.sendText("exit\n")
    yield lambda: not p.running
    check(p.status_label.text() == "disconnected", "local shell exit detected")
    for x in list(win.layout_.panels):
        win.close_panel(x)


if __name__ == "__main__":
    sys.exit(run(test))
