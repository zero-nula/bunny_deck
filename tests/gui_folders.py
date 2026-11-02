"""GUI test: copying folders in the file manager.

Like gui_phase3.py, local sftp-server processes rooted in temp directories
stand in for two hosts. Covers Local -> remote, remote -> Local and
remote -> remote folder copies, symlinks and special files left out, copying
into an existing folder (merge, per-file overwrite question), skipping it,
and refusing to copy a folder into itself.
"""

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gui_harness import check, local_shell_argv, make_profile, run  # noqa: E402

from bunnydeck import sftp  # noqa: E402
from bunnydeck.filemanager import LOCAL_ID  # noqa: E402

from openssh_paths import SFTP_SERVER  # noqa: E402
BASE = tempfile.mkdtemp(prefix="bunnydeck-folders-")
ROOTS = {"ubuntu.invalid": os.path.join(BASE, "ubuntu"),
         "rpi.invalid": os.path.join(BASE, "rpi")}
LOCAL = os.path.join(BASE, "local")
sftp.ssh_sftp_argv = lambda profile: [SFTP_SERVER, "-d", ROOTS[profile.host]]


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


def read(path):
    with open(path) as f:
        return f.read()


def tree(root):
    """{relative path: content or "<dir>"} of everything below root."""
    out = {}
    for d, dirs, files in os.walk(root):
        for name in dirs:
            out[os.path.relpath(os.path.join(d, name), root)] = "<dir>"
        for name in files:
            p = os.path.join(d, name)
            out[os.path.relpath(p, root)] = read(p) if os.path.isfile(p) else "<special>"
    return out


def setup_tree():
    for d in list(ROOTS.values()):
        os.makedirs(d)
    project = os.path.join(LOCAL, "project")
    write(os.path.join(project, "README.md"), "readme")
    write(os.path.join(project, "src", "main.py"), "print('hi')")
    write(os.path.join(project, "src", "lib", "util.py"), "x = 1")
    write(os.path.join(project, "big.bin"), "B" * 300000)
    os.makedirs(os.path.join(project, "empty"))
    os.symlink("src", os.path.join(project, "link-to-src"))
    os.mkfifo(os.path.join(project, "pipe"))


EXPECTED = {"README.md": "readme", "src": "<dir>", "src/main.py": "print('hi')",
            "src/lib": "<dir>", "src/lib/util.py": "x = 1", "big.bin": "B" * 300000,
            "empty": "<dir>"}


def test(app):
    setup_tree()
    win = app.window
    ubuntu = app.config.add(make_profile("Ubuntu", host="ubuntu.invalid"))
    app.config.add(make_profile("Raspberry Pi", host="rpi.invalid", user="pi"))
    win.resize(1600, 900)
    t = win.open_profile(ubuntu, argv=local_shell_argv())
    yield lambda: t.child_pid
    t.scpRequested.emit(t)
    fm = win.file_manager
    L, R = fm.left, fm.right
    yield lambda: R.cwd is not None and L.cwd is not None
    L.load(LOCAL)
    yield lambda: L.cwd == LOCAL and "project" in L.names()

    # --- Local -> Ubuntu ---------------------------------------------------
    L.select_names("project")
    fm.copy_selection(L, R)
    yield lambda: not fm.busy and "project" in R.names()
    remote_project = os.path.join(ROOTS["ubuntu.invalid"], "project")
    check(tree(remote_project) == EXPECTED, f"whole folder uploaded ({sorted(tree(remote_project))})")
    status = fm.status.text()
    check("Copied 4 file(s) and 4 folder(s)" in status and "2 link(s)/special file(s)" in status,
          f"summary counts files, folders and what was left out ({status!r})")

    # --- again: the folder exists -> skip, then merge ----------------------
    fm._ask_merge = lambda path, where: "skip"
    fm.copy_selection(L, R)
    yield lambda: not fm.busy
    check("1 skipped" in fm.status.text(), f"Skip Folder leaves it alone ({fm.status.text()!r})")

    write(os.path.join(LOCAL, "project", "src", "main.py"), "print('v2')")
    write(os.path.join(LOCAL, "project", "NEW.txt"), "new")
    merged, asked = [], []
    fm._ask_merge = lambda path, where: merged.append(path) or "merge"
    fm._ask_overwrite = lambda path, where, multi: asked.append(path) or (
        "replace" if path.endswith("main.py") else "skip")
    fm.copy_selection(L, R)
    yield lambda: not fm.busy and merged
    yield 0.3
    check(merged == [remote_project], "asked once before copying into the existing folder")
    check(len(asked) == 4, f"each existing file asked about ({len(asked)})")
    check(read(os.path.join(remote_project, "src", "main.py")) == "print('v2')"
          and read(os.path.join(remote_project, "README.md")) == "readme"
          and read(os.path.join(remote_project, "NEW.txt")) == "new",
          "replaced what was confirmed, kept the rest, added the new file")

    # --- Ubuntu -> Raspberry Pi (remote to remote) -------------------------
    L.select_source(ubuntu.id)
    yield lambda: L.cwd == ROOTS["ubuntu.invalid"] and "project" in L.names()
    R.select_source([p.id for p in app.config.profiles if p.host == "rpi.invalid"][0])
    yield lambda: R.cwd == ROOTS["rpi.invalid"]
    L.select_names("project")
    fm.copy_selection(L, R)
    yield lambda: not fm.busy and "project" in R.names()
    expected2 = dict(EXPECTED, **{"src/main.py": "print('v2')", "NEW.txt": "new"})
    check(tree(os.path.join(ROOTS["rpi.invalid"], "project")) == expected2,
          "remote -> remote folder copy")

    # --- Raspberry Pi -> Local, into a new place ---------------------------
    dl = os.path.join(BASE, "downloads")
    os.makedirs(dl)
    L.select_source(LOCAL_ID)
    yield lambda: L.backend is not None and not L.backend.is_remote
    L.load(dl)
    yield lambda: L.cwd == dl
    R.select_names("project")
    fm.copy_selection(R, L)
    yield lambda: not fm.busy and "project" in L.names()
    check(tree(os.path.join(dl, "project")) == expected2, "remote -> Local folder download")
    leftovers = [n for n in tree(dl) if "bunnydeck-part" in n]
    check(not leftovers, "no temporary part files left")

    # --- a folder cannot be copied into itself -----------------------------
    errors = []
    fm.message = lambda title, text, error=False: errors.append(text)
    L.load(os.path.join(dl, "project", "src"))
    yield lambda: L.cwd and L.cwd.endswith("src")
    fm.start_transfer(L.backend, [os.path.join(dl, "project")], L)
    yield lambda: not fm.busy and errors
    check(errors and "cannot be copied into itself" in errors[0], f"refused ({errors})")
    check(not os.path.exists(os.path.join(dl, "project", "src", "project")), "nothing created")


if __name__ == "__main__":
    try:
        sys.exit(run(test, timeout=90))
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
