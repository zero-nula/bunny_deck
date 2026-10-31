"""SFTP client tests against the local OpenSSH sftp-server binary over pipes.

No network or SSH server involved: the client speaks the real SFTP protocol
to the same server program sshd would run for the "sftp" subsystem.
"""

import io
import os
import shutil
import stat
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bunnydeck import sftp  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openssh_paths import SFTP_SERVER  # noqa: E402


@unittest.skipUnless(SFTP_SERVER, "sftp-server binary not found")
class SFTPClientTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="bunnydeck-sftp-")
        os.makedirs(os.path.join(self.root, "docs", "sub"))
        with open(os.path.join(self.root, "docs", "a.txt"), "w") as f:
            f.write("hello")
        os.symlink("docs", os.path.join(self.root, "link-to-docs"))
        os.symlink("missing", os.path.join(self.root, "broken-link"))
        self.c = sftp.SFTPClient([SFTP_SERVER, "-d", self.root]).connect()

    def tearDown(self):
        self.c.close()
        for dirpath, dirnames, _ in os.walk(self.root):
            for d in dirnames:
                try:
                    os.chmod(os.path.join(dirpath, d), 0o755)
                except OSError:
                    pass
        shutil.rmtree(self.root)

    def p(self, *parts):
        return os.path.join(self.root, *parts)

    def test_home_is_absolute_start_dir(self):
        self.assertEqual(self.c.home, os.path.realpath(self.root))

    def test_listdir_real_contents(self):
        entries = {e.name: e for e in self.c.listdir(self.root)}
        self.assertEqual(set(entries), {"docs", "link-to-docs", "broken-link"})
        self.assertTrue(entries["docs"].is_dir)
        self.assertTrue(entries["link-to-docs"].attrs.is_link)
        self.assertTrue(entries["link-to-docs"].is_dir, "symlink to dir is navigable")
        self.assertFalse(entries["broken-link"].is_dir)
        files = {e.name: e for e in self.c.listdir(self.p("docs"))}
        self.assertEqual(files["a.txt"].attrs.size, 5)

    def test_listdir_errors_are_raised_not_empty(self):
        with self.assertRaises(sftp.SFTPError) as cm:
            self.c.listdir(self.p("nope"))
        self.assertEqual(cm.exception.code, sftp.FX_NO_SUCH_FILE)
        if os.getuid() != 0:
            os.chmod(self.p("docs", "sub"), 0)
            with self.assertRaises(sftp.SFTPError) as cm:
                self.c.listdir(self.p("docs", "sub"))
            self.assertEqual(cm.exception.code, sftp.FX_PERMISSION_DENIED)

    def test_realpath_normalises(self):
        self.assertEqual(self.c.realpath(self.p("docs", "sub", "..")), self.p("docs"))

    def test_mkdir(self):
        self.c.mkdir(self.p("newdir"))
        self.assertTrue(os.path.isdir(self.p("newdir")))
        with self.assertRaises(sftp.SFTPError):
            self.c.mkdir(self.p("newdir"))

    def test_upload_download_roundtrip_large(self):
        data = os.urandom(3 * 1024 * 1024 + 12345)
        seen = []
        n = self.c.upload(io.BytesIO(data), self.p("big.bin"), len(data), progress=seen.append)
        self.assertEqual(n, len(data))
        self.assertEqual(seen[-1], len(data))
        with open(self.p("big.bin"), "rb") as f:
            self.assertEqual(f.read(), data)
        out = io.BytesIO()
        self.assertEqual(self.c.download(self.p("big.bin"), out), len(data))
        self.assertEqual(out.getvalue(), data)
        self.assertFalse([f for f in os.listdir(self.root) if f.endswith(".bunnydeck-part")])

    def test_empty_file(self):
        self.c.upload(io.BytesIO(b""), self.p("empty"), 0)
        self.assertEqual(os.path.getsize(self.p("empty")), 0)
        out = io.BytesIO()
        self.assertEqual(self.c.download(self.p("empty"), out), 0)

    def test_no_silent_overwrite(self):
        with self.assertRaises(sftp.SFTPError):
            self.c.upload(io.BytesIO(b"new"), self.p("docs", "a.txt"), 3)
        with open(self.p("docs", "a.txt")) as f:
            self.assertEqual(f.read(), "hello")

    def test_confirmed_overwrite_keeps_permissions(self):
        os.chmod(self.p("docs", "a.txt"), 0o600)
        self.c.upload(io.BytesIO(b"replaced"), self.p("docs", "a.txt"), 8, overwrite=True)
        with open(self.p("docs", "a.txt")) as f:
            self.assertEqual(f.read(), "replaced")
        self.assertEqual(stat.S_IMODE(os.stat(self.p("docs", "a.txt")).st_mode), 0o600)

    def test_cancelled_upload_leaves_no_partial_and_keeps_target(self):
        cancel = threading.Event()
        data = os.urandom(4 * 1024 * 1024)

        def progress(n):
            if n > 256 * 1024:
                cancel.set()

        with self.assertRaises(sftp.Cancelled):
            self.c.upload(io.BytesIO(data), self.p("docs", "a.txt"), len(data),
                          overwrite=True, progress=progress, cancel=cancel)
        with open(self.p("docs", "a.txt")) as f:
            self.assertEqual(f.read(), "hello")
        self.assertFalse([f for f in os.listdir(self.p("docs")) if "bunnydeck-part" in f])

    def test_cancelled_download(self):
        with open(self.p("big"), "wb") as f:
            f.write(os.urandom(4 * 1024 * 1024))
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(sftp.Cancelled):
            self.c.download(self.p("big"), io.BytesIO(), cancel=cancel)
        # connection still usable
        self.assertTrue(self.c.listdir(self.root))

    def test_download_errors(self):
        with self.assertRaises(sftp.SFTPError) as cm:
            self.c.download(self.p("missing.txt"), io.BytesIO())
        self.assertEqual(cm.exception.code, sftp.FX_NO_SUCH_FILE)

    def test_non_utf8_names_roundtrip(self):
        raw = os.path.join(os.fsencode(self.root), b"caf\xe9.txt")
        with open(raw, "wb") as f:
            f.write(b"x")
        names = [e.name for e in self.c.listdir(self.root)]
        odd = [n for n in names if n.startswith("caf")][0]
        out = io.BytesIO()
        self.c.download(os.path.join(self.root, odd), out)
        self.assertEqual(out.getvalue(), b"x")

    def test_no_remove_api(self):
        public = [n for n in dir(self.c) if not n.startswith("_")]
        for name in ("remove", "unlink", "rmdir", "delete"):
            self.assertNotIn(name, public)


class ConnectFailureTest(unittest.TestCase):
    def test_process_exits_immediately(self):
        c = sftp.SFTPClient(["/bin/sh", "-c", "echo 'Permission denied (publickey).' >&2; exit 255"])
        with self.assertRaises(sftp.ConnectionFailed) as cm:
            c.connect()
        self.assertIn("Authentication failed", str(cm.exception))

    def test_missing_binary(self):
        with self.assertRaises(sftp.ConnectionFailed):
            sftp.SFTPClient(["/nonexistent/ssh"]).connect()


class ArgvTest(unittest.TestCase):
    def test_ssh_argv(self):
        from bunnydeck.config import Profile

        argv = sftp.ssh_sftp_argv(Profile(name="x", host="pi.local", user="pi", port=2222))
        self.assertEqual(argv[0], "/usr/bin/ssh")
        self.assertEqual(argv[-5:], ["-l", "pi", "--", "pi.local", "sftp"])
        self.assertIn("-s", argv)
        joined = " ".join(argv)
        self.assertNotIn("StrictHostKeyChecking", joined)
        self.assertNotIn("UserKnownHostsFile", joined)
        self.assertNotIn("sshpass", joined)


if __name__ == "__main__":
    unittest.main()
