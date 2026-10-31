"""End-to-end test of SFTP over real OpenSSH with prompts routed via askpass.

Uses a throwaway, unprivileged sshd on 127.0.0.1 (tests/throwaway_sshd.py);
~/.ssh is never read or written. The passphrase-protected client key makes
OpenSSH prompt through SSH_ASKPASS exactly as it does for passwords.
"""

import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import throwaway_sshd  # noqa: E402
from bunnydeck import sftp  # noqa: E402
from bunnydeck.askpass import AskpassServer  # noqa: E402
from bunnydeck.config import Profile  # noqa: E402


@unittest.skipUnless(throwaway_sshd.available(), "sshd not available")
class AskpassE2ETest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sshd = throwaway_sshd.ThrowawaySSHD()
        cls.tmp = cls.sshd.tmp
        cls.server = AskpassServer()

    @classmethod
    def tearDownClass(cls):
        cls.sshd.stop()
        cls.server.close()

    def client(self, handler, conn_id):
        self.server.register(conn_id, handler)
        self.addCleanup(self.server.unregister, conn_id)
        argv = sftp.ssh_sftp_argv(Profile(name="t", host=throwaway_sshd.HOST_ALIAS),
                                  extra_opts=["-F", self.sshd.config])
        c = sftp.SFTPClient(argv, env=self.server.env_for(conn_id))
        self.addCleanup(c.close)
        return c

    def test_1_hostkey_confirm_then_wrong_then_right_passphrase(self):
        self.sshd.reset_known_hosts()
        calls = []
        answers = iter(["yes", "wrong passphrase", throwaway_sshd.PASSPHRASE])

        def handler(prompt, kind):
            calls.append((kind, prompt))
            a = next(answers)
            return a if kind == "secret" else ("yes" if a == "yes" else None)

        c = self.client(handler, "c1").connect()
        self.assertEqual([k for k, _ in calls], ["confirm", "secret", "secret"], calls)
        self.assertIn("authenticity of host", calls[0][1])
        self.assertIn("passphrase", calls[1][1].lower())
        self.assertEqual(c.home, os.path.expanduser("~"))
        self.assertIsInstance(c.listdir(c.home), list)
        with open(f"{self.tmp}/known_hosts") as f:
            self.assertIn("127.0.0.1", f.read())

    def test_2_cancel_passphrase(self):
        c = self.client(lambda p, k: "yes" if k == "confirm" else None, "c2")
        with self.assertRaises(sftp.ConnectionFailed) as cm:
            c.connect()
        self.assertIn("Authentication failed", str(cm.exception))

    def test_3_reject_host_key(self):
        self.sshd.reset_known_hosts()
        c = self.client(lambda p, k: None, "c3")
        with self.assertRaises(sftp.ConnectionFailed) as cm:
            c.connect()
        self.assertIn("Host key verification failed", str(cm.exception))
        self.assertFalse(os.path.exists(f"{self.tmp}/known_hosts"))

    def test_4_changed_host_key_is_refused(self):
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f",
                        f"{self.tmp}/other"], check=True)
        with open(f"{self.tmp}/other.pub") as f:
            other = f.read().split()
        with open(f"{self.tmp}/known_hosts", "w") as f:
            f.write(f"[127.0.0.1]:{self.sshd.port} {other[0]} {other[1]}\n")
        prompts = []
        c = self.client(lambda p, k: prompts.append(k) or None, "c4")
        with self.assertRaises(sftp.ConnectionFailed) as cm:
            c.connect()
        self.assertIn("HOST IDENTIFICATION HAS CHANGED", str(cm.exception))
        self.assertEqual(prompts, [], "no prompt may be offered for a changed key")
        self.sshd.reset_known_hosts()

    def test_5_wrong_token_rejected(self):
        helper = self.server.env_for("c5")["SSH_ASKPASS"]
        env = dict(os.environ, **self.server.env_for("c5"))
        env["BUNNYDECK_ASKPASS_TOKEN"] = "0" * 64
        self.server.register("c5", lambda p, k: "secret")
        r = subprocess.run([helper, "Password:"], env=env, capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")
        env["BUNNYDECK_ASKPASS_TOKEN"] = self.server.token
        r = subprocess.run([helper, "Password:"], env=env, capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout), (0, "secret\n"))
        self.assertEqual(os.stat(self.server.dir).st_mode & 0o777, 0o700)


if __name__ == "__main__":
    unittest.main()
