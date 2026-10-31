"""Key login setup and ~/.ssh/config handling, against real OpenSSH.

Uses a throwaway local sshd (tests/throwaway_sshd.py) and a throwaway $HOME.
Keys are installed into the sshd's temp directory (remote_dir), never into
the real ~/.ssh/authorized_keys.
"""

import os
import subprocess
import sys
import tempfile
import shutil
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import throwaway_sshd  # noqa: E402
from bunnydeck import keysetup, sshconfig, sshmux  # noqa: E402
from bunnydeck.config import Profile  # noqa: E402

PROFILE = Profile(name="t", host=throwaway_sshd.HOST_ALIAS)
NEW_PASSPHRASE = "new key passphrase"


def call_now(fn):
    fn()


class ConfigFileTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="bunnydeck-home-")
        self.addCleanup(shutil.rmtree, self.home)
        patcher = mock.patch.dict(os.environ, {"HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, name, text):
        os.makedirs(os.path.join(self.home, ".ssh"), exist_ok=True)
        with open(os.path.join(self.home, ".ssh", name), "w") as f:
            f.write(text)

    def test_aliases_follow_include_and_skip_patterns(self):
        self.write("config", "Include conf.d/*\nHost pi nas\n  User pi\nHost *.lan !x *\n"
                             "Match host foo\n  User bar\nHost=web\n")
        os.makedirs(os.path.join(self.home, ".ssh", "conf.d"))
        self.write("conf.d/work", "Host work-vm pi\n  HostName 10.0.0.5\n")
        self.assertEqual(sshconfig.host_aliases(), ["work-vm", "pi", "nas", "web"])

    def test_resolve_asks_openssh(self):
        self.write("config", "Host pi\n  HostName 192.168.1.20\n  User admin\n  Port 2222\n")
        r = sshconfig.resolve("pi", extra_opts=["-F", sshconfig.config_path()])
        self.assertEqual(r, {"hostname": "192.168.1.20", "user": "admin", "port": 2222})

    def test_add_host_appends_with_backup(self):
        self.write("config", "Host old\n  HostName 1.2.3.4")
        backup = sshconfig.add_host("pi", "192.168.1.20", "admin", 2222,
                                    os.path.join(self.home, ".ssh", "id_ed25519"))
        with open(backup) as f:
            self.assertEqual(f.read(), "Host old\n  HostName 1.2.3.4")
        with open(sshconfig.config_path()) as f:
            text = f.read()
        self.assertTrue(text.startswith("Host old\n  HostName 1.2.3.4\n\n# Added by Bunny Deck\n"))
        self.assertIn("Host pi\n    HostName 192.168.1.20\n    User admin\n    Port 2222\n"
                      "    IdentityFile ~/.ssh/id_ed25519\n", text)
        r = sshconfig.resolve("pi", extra_opts=["-F", sshconfig.config_path()])
        self.assertEqual((r["hostname"], r["user"], r["port"]), ("192.168.1.20", "admin", 2222))
        with self.assertRaises(ValueError):
            sshconfig.add_host("pi", "x")  # already there
        with self.assertRaises(ValueError):
            sshconfig.add_host("bad alias", "x")

    def test_add_host_creates_private_file(self):
        self.assertIsNone(sshconfig.add_host("nas", "10.0.0.2"))
        self.assertEqual(os.stat(sshconfig.config_path()).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(sshconfig.ssh_dir()).st_mode & 0o777, 0o700)


@unittest.skipUnless(throwaway_sshd.available(), "sshd not available")
class KeySetupTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="bunnydeck-home-")
        self.addCleanup(shutil.rmtree, self.home)
        patcher = mock.patch.dict(os.environ, {"HOME": self.home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sshd = throwaway_sshd.ThrowawaySSHD()
        self.addCleanup(self.sshd.stop)
        self.addCleanup(sshmux.shutdown)
        self.remote = os.path.join(self.sshd.tmp, "remote_ssh")
        # For verification: the same host without the sshd's own client key,
        # so only the new key can log in.
        self.verify_config = os.path.join(self.sshd.tmp, "verify_config")
        with open(self.sshd.config) as f, open(self.verify_config, "w") as g:
            g.write("".join(ln for ln in f if "IdentityFile" not in ln))
        self.prompts = []

    def setup(self, answers):
        def ask(profile, prompt, kind, retry):
            self.prompts.append((kind, prompt))
            return answers(prompt, kind)
        ks = keysetup.KeySetup(PROFILE, ask, call_now, extra_opts=["-F", self.sshd.config],
                               verify_opts=["-F", self.verify_config], remote_dir=self.remote)
        self.addCleanup(ks.close)
        return ks

    def existing_key(self):
        os.makedirs(os.path.dirname(keysetup.key_path()), mode=0o700)
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", keysetup.key_path()],
                       check=True)

    def authorized(self):
        with open(os.path.join(self.remote, "authorized_keys")) as f:
            return f.read().splitlines()

    def test_full_setup_without_a_terminal(self):
        def answers(prompt, kind):
            if kind == "confirm":
                return "yes"
            return throwaway_sshd.PASSPHRASE if "client_key" in prompt else NEW_PASSPHRASE
        ks = self.setup(answers)

        self.assertIn("Created", ks.create_key(NEW_PASSPHRASE))
        self.assertTrue(keysetup.key_exists())
        self.assertEqual(os.stat(keysetup.key_path()).st_mode & 0o777, 0o600)
        self.assertTrue(keysetup.fingerprint().startswith("SHA256:"))
        self.assertEqual(self.prompts, [], "the new passphrase is not asked from the user")
        check = subprocess.run(["ssh-keygen", "-y", "-P", NEW_PASSPHRASE, "-f",
                                keysetup.key_path()], capture_output=True)
        self.assertEqual(check.returncode, 0, "key is protected by the chosen passphrase")

        self.assertIn("added", ks.install_key())
        with open(keysetup.key_path() + ".pub") as f:
            pub = f.read().strip()
        self.assertEqual(self.authorized(), [pub])
        self.assertIn("already", ks.install_key())
        self.assertEqual(self.authorized(), [pub], "not added twice")
        self.assertEqual(os.stat(self.remote).st_mode & 0o777, 0o700)

        self.prompts.clear()
        self.assertIn("without a password", ks.verify())
        self.assertTrue(all("client_key" not in p for _, p in self.prompts),
                        "verification used only the new key")

        self.assertIn("existing", ks.create_key("ignored"), "an existing key is never replaced")

    def test_install_over_the_terminal_connection_needs_no_password(self):
        self.existing_key()
        # A terminal is connected (master), logged in with the sshd's client key.
        ks_master = self.setup(lambda p, k: "yes" if k == "confirm" else throwaway_sshd.PASSPHRASE)
        code, out, err = ks_master._run(
            [sshmux.SSH_BINARY, "-F", self.sshd.config, *sshmux.master_opts(),
             *PROFILE.ssh_args(), "true"])
        self.assertEqual(code, 0, err)

        self.prompts.clear()
        ks = self.setup(lambda p, k: None)  # any prompt would be cancelled
        self.assertIn("added", ks.install_key())
        self.assertEqual(self.prompts, [], "no password asked over the shared connection")
        self.assertIn("without a password", ks.verify())

    def test_cancelled_password_fails_cleanly(self):
        self.existing_key()
        ks = self.setup(lambda p, k: "yes" if k == "confirm" else None)
        with self.assertRaises(keysetup.StepFailed):
            ks.install_key()
        self.assertFalse(os.path.exists(os.path.join(self.remote, "authorized_keys")))


if __name__ == "__main__":
    unittest.main()
