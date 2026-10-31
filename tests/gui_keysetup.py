"""GUI test: importing hosts from ~/.ssh/config, and setting up key login
through the real dialogs against a throwaway local sshd.

Runs with a throwaway $HOME, so the new key and ~/.ssh/config entry are
created there; the key is authorized in the sshd's temp directory, never in
the real ~/.ssh/authorized_keys."""

import atexit
import os
import shutil
import sys
import tempfile

HOME = tempfile.mkdtemp(prefix="bunnydeck-test-home-")
atexit.register(shutil.rmtree, HOME, True)
os.environ["HOME"] = HOME
os.environ["SHELL"] = "/bin/sh"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gui_harness import check, find_top, make_profile, run  # noqa: E402
import throwaway_sshd  # noqa: E402

from PyQt6.QtCore import Qt, QTimer  # noqa: E402

from bunnydeck import keysetup, sshconfig  # noqa: E402
from bunnydeck.dialogs import CredentialDialog, ImportSSHConfigDialog, KeySetupDialog  # noqa: E402

SSHD = throwaway_sshd.ThrowawaySSHD()
atexit.register(SSHD.stop)
REMOTE = os.path.join(SSHD.tmp, "remote_ssh")
VERIFY_CONFIG = os.path.join(SSHD.tmp, "verify_config")
with open(SSHD.config) as f, open(VERIFY_CONFIG, "w") as g:
    g.write("".join(ln for ln in f if "IdentityFile" not in ln))
NEW_PASSPHRASE = "bunny passphrase"


class TestKeySetup(keysetup.KeySetup):
    def __init__(self, profile, ask, call_soon):
        super().__init__(profile, ask, call_soon, extra_opts=["-F", SSHD.config],
                         verify_opts=["-F", VERIFY_CONFIG], remote_dir=REMOTE)


keysetup.KeySetup = TestKeySetup
# ssh reads the config of the passwd home directory, not $HOME.
_resolve = sshconfig.resolve
sshconfig.resolve = lambda alias: _resolve(alias, extra_opts=["-F", sshconfig.config_path()])


def test(app):
    win = app.window

    # --- import from ~/.ssh/config ----------------------------------------
    os.makedirs(os.path.join(HOME, ".ssh"), mode=0o700)
    with open(os.path.join(HOME, ".ssh", "config"), "w") as f:
        f.write("Host pi\n  HostName 192.0.2.10\n  User admin\n  Port 2222\n"
                "Host *\n  ServerAliveInterval 30\nHost nas\n  HostName 192.0.2.11\n")
    win.config.add(make_profile("NAS", host="nas", user=""))
    win.rebuild_saved_menu()
    labels = [a.text() for a in win.saved_menu.actions()]
    check("Import from ~/.ssh/config…" in labels and "Set up key login" in labels,
          f"Saved Connections menu offers import and key setup ({labels})")

    QTimer.singleShot(0, win.import_ssh_config)
    yield lambda: find_top(ImportSSHConfigDialog)
    d = find_top(ImportSSHConfigDialog)
    items = [d.list.item(i) for i in range(d.list.count())]
    texts = [it.text() for it in items]
    check(len(items) == 2 and "admin@192.0.2.10:2222" in texts[0],
          f"hosts listed with what ssh resolves, patterns skipped ({texts})")
    check(not items[1].flags() & Qt.ItemFlag.ItemIsEnabled and "already saved" in texts[1],
          "an already saved host cannot be imported twice")
    d.accept()
    yield lambda: find_top(ImportSSHConfigDialog) is None
    pi = [p for p in win.config.profiles if p.host == "pi"]
    check(len(pi) == 1 and pi[0].name == "pi" and pi[0].user == "" and pi[0].port == 22,
          "imported as the alias, so ~/.ssh/config keeps applying")

    # --- key login setup ---------------------------------------------------
    prof = win.config.add(make_profile("Throwaway Box", host=throwaway_sshd.HOST_ALIAS, user=""))
    QTimer.singleShot(0, lambda: win.setup_key_login(prof))
    yield lambda: find_top(KeySetupDialog)
    d = find_top(KeySetupDialog)
    check(d.passphrase is not None, "no key yet: the dialog offers to create one with a passphrase")
    check(d.config_box.isChecked() and d.alias.text() == "throwaway-box",
          f"suggests a ~/.ssh/config name ({d.alias.text()})")
    d.passphrase.setText(NEW_PASSPHRASE)
    d.passphrase2.setText("typo")
    d.start_btn.click()
    check(d.error.isVisible() and not d.running, "mismatched passphrases are caught")
    d.passphrase2.setText(NEW_PASSPHRASE)
    d.start_btn.click()

    asked = []
    for _ in range(200):
        if d.succeeded or (d.error.isVisible() and not d.running):
            break
        c = find_top(CredentialDialog)
        if c is not None:
            text = c.text()
            asked.append("confirm" if c.confirm else ("client" if "client_key" in text else "new"))
            if c.confirm:
                c.accept()
            else:
                c.entry.setText(throwaway_sshd.PASSPHRASE if "client_key" in text
                                else NEW_PASSPHRASE)
                c.accept()
        yield 0.1
    check(d.succeeded, f"all steps succeeded (error: {d.error.text()!r})")
    check(asked == ["confirm", "client", "new"],
          f"asked: host key, server login once, new key's passphrase to verify ({asked})")
    check(all(d.marks[k].text() == "✓" for k in ("key", "install", "verify", "config")),
          "every step marked done")
    check(keysetup.key_exists() and keysetup.key_path().startswith(HOME),
          "key created in ~/.ssh")
    with open(keysetup.key_path() + ".pub") as f:
        pub = f.read().strip()
    with open(os.path.join(REMOTE, "authorized_keys")) as f:
        check(f.read().splitlines() == [pub], "public key authorized on the server")
    with open(sshconfig.config_path()) as f:
        text = f.read()
    check("Host throwaway-box\n    HostName deck-test\n    IdentityFile ~/.ssh/id_ed25519" in text
          and text.startswith("Host pi\n"), "Host entry appended to ~/.ssh/config")
    backups = [n for n in os.listdir(os.path.join(HOME, ".ssh")) if n.endswith(".bak")]
    check(len(backups) == 1, f"~/.ssh/config backed up first ({backups})")
    d.close_btn.click()
    yield lambda: find_top(KeySetupDialog) is None

    # Running it again: the existing key is used and nothing is duplicated.
    QTimer.singleShot(0, lambda: win.setup_key_login(prof))
    yield lambda: find_top(KeySetupDialog)
    d = find_top(KeySetupDialog)
    check(d.passphrase is None, "an existing key is reused, not replaced")
    d.config_box.setChecked(False)
    d.start_btn.click()
    for _ in range(200):
        if d.succeeded or (d.error.isVisible() and not d.running):
            break
        c = find_top(CredentialDialog)
        if c is not None and not c.confirm:
            c.entry.setText(throwaway_sshd.PASSPHRASE if "client_key" in c.text()
                            else NEW_PASSPHRASE)
            c.accept()
        yield 0.1
    check(d.succeeded and "already" in d.texts["install"].text(),
          f"second run: key already authorized ({d.texts['install'].text()})")
    with open(os.path.join(REMOTE, "authorized_keys")) as f:
        check(len(f.read().splitlines()) == 1, "authorized_keys has no duplicate")
    d.close_btn.click()
    yield lambda: find_top(KeySetupDialog) is None


if __name__ == "__main__":
    sys.exit(run(test, timeout=90))
