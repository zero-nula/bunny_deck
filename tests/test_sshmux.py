"""Connection sharing over real OpenSSH (throwaway local sshd): once a
terminal's ssh is connected, the file manager's SFTP channel runs over the
same connection without asking for credentials again, and quitting closes
the shared connection."""

import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import throwaway_sshd  # noqa: E402
from bunnydeck import sftp, sshmux  # noqa: E402
from bunnydeck.askpass import AskpassServer  # noqa: E402
from bunnydeck.config import Profile  # noqa: E402

PROFILE = Profile(name="t", host=throwaway_sshd.HOST_ALIAS)


@unittest.skipUnless(throwaway_sshd.available(), "sshd not available")
class SharedConnectionTest(unittest.TestCase):
    def setUp(self):
        self.sshd = throwaway_sshd.ThrowawaySSHD()
        self.addCleanup(self.sshd.stop)
        self.server = AskpassServer()
        self.addCleanup(self.server.close)
        self.addCleanup(sshmux.shutdown)
        self.prompts = []

    def ssh_env(self, conn_id, answer):
        def handler(prompt, kind):
            self.prompts.append((conn_id, kind))
            return "yes" if kind == "confirm" else answer
        self.server.register(conn_id, handler)
        self.addCleanup(self.server.unregister, conn_id)
        return {**os.environ, **self.server.env_for(conn_id)}

    def check_master(self):
        return subprocess.run(
            [sshmux.SSH_BINARY, "-F", self.sshd.config, *sshmux.master_opts(), "-O", "check",
             throwaway_sshd.HOST_ALIAS],
            stdin=subprocess.DEVNULL, capture_output=True).returncode == 0

    def test_sftp_shares_the_terminal_connection(self):
        # What a terminal runs (sshmux.master_opts() + host), here with a
        # command instead of a shell; askpass stands in for typing the passphrase.
        term = subprocess.run(
            [sshmux.SSH_BINARY, "-F", self.sshd.config, *sshmux.master_opts(),
             *PROFILE.ssh_args(), "echo", "TERMINAL-OK"],
            env=self.ssh_env("terminal", throwaway_sshd.PASSPHRASE),
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
            start_new_session=True)
        self.assertEqual(term.stdout.strip(), "TERMINAL-OK", term.stderr)
        self.assertIn(("terminal", "secret"), self.prompts)
        self.assertTrue(self.check_master(), "master keeps running after the session ends")

        # SFTP: must not prompt (a prompt would get a wrong answer and fail).
        c = sftp.SFTPClient(sftp.ssh_sftp_argv(PROFILE, extra_opts=["-F", self.sshd.config]),
                            env=self.server.env_for("sftp"))
        self.ssh_env("sftp", "wrong passphrase")
        c.connect()
        self.addCleanup(c.close)
        self.assertEqual([p for p in self.prompts if p[0] == "sftp"], [],
                         "SFTP asked for credentials although a connection was shared")
        self.assertIsInstance(c.listdir(c.home), list)

        # A second terminal also runs without a prompt.
        term2 = subprocess.run(
            [sshmux.SSH_BINARY, "-F", self.sshd.config, *sshmux.master_opts(),
             *PROFILE.ssh_args(), "echo", "SECOND-OK"],
            env=self.ssh_env("terminal2", "wrong passphrase"),
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30,
            start_new_session=True)
        self.assertEqual(term2.stdout.strip(), "SECOND-OK", term2.stderr)
        self.assertEqual([p for p in self.prompts if p[0] == "terminal2"], [])

        # Quitting the app closes the shared connection and removes the sockets.
        c.close()
        directory = sshmux.control_dir()
        sshmux.shutdown()
        self.assertFalse(os.path.exists(directory))
        self.assertFalse(self.check_master(), "master closed on shutdown")

    def test_sftp_without_a_terminal_connects_by_itself(self):
        c = sftp.SFTPClient(sftp.ssh_sftp_argv(PROFILE, extra_opts=["-F", self.sshd.config]),
                            env=self.server.env_for("alone"))
        self.ssh_env("alone", throwaway_sshd.PASSPHRASE)
        c.connect()
        self.addCleanup(c.close)
        self.assertIn(("alone", "secret"), self.prompts)
        self.assertEqual(os.listdir(sshmux.control_dir()), [], "SFTP never becomes a master")


if __name__ == "__main__":
    unittest.main()
