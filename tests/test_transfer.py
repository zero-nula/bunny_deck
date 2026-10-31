"""Transfer engine and path-semantics tests.

Two independent sftp-server processes with different roots stand in for two
remote hosts (e.g. "Ubuntu" and "Raspberry Pi"); no network is involved.
"""

import glob
import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bunnydeck import sftp, transfer  # noqa: E402
from bunnydeck.transfer import LocalBackend, RemoteBackend, copy_file, resolve_remote  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from openssh_paths import SFTP_SERVER  # noqa: E402


class PathTest(unittest.TestCase):
    def test_absolute_input_is_not_duplicated(self):
        self.assertEqual(resolve_remote("/home/flynn", "/home/flynn/Documents", "/home/flynn"),
                         "/home/flynn/Documents")

    def test_relative_input(self):
        self.assertEqual(resolve_remote("/home/flynn", "Documents", "/home/flynn"),
                         "/home/flynn/Documents")
        self.assertEqual(resolve_remote("/home/flynn/a", "../b/./c/", "/home/flynn"),
                         "/home/flynn/b/c")

    def test_tilde_and_root(self):
        self.assertEqual(resolve_remote("/etc", "~/Documents", "/home/flynn"),
                         "/home/flynn/Documents")
        self.assertEqual(resolve_remote("/etc", "~", "/home/flynn"), "/home/flynn")
        self.assertEqual(resolve_remote("/etc", "//", "/home/flynn"), "/")
        self.assertEqual(resolve_remote("/", "..", "/home/flynn"), "/")
        self.assertEqual(resolve_remote("/x", "  ", "/home/flynn"), "/x")

    def test_join_and_parent(self):
        class C:
            home = "/home/flynn"

        rb = RemoteBackend(C(), "r")
        self.assertEqual(rb.join("/home/flynn", "Documents"), "/home/flynn/Documents")
        self.assertEqual(rb.join("/", "etc"), "/etc")
        self.assertEqual(rb.parent("/home/flynn/Documents"), "/home/flynn")
        self.assertEqual(rb.parent("/home"), "/")
        self.assertEqual(rb.parent("/"), "/")


@unittest.skipUnless(os.path.exists(SFTP_SERVER), "sftp-server not found")
class TransferTest(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="bunnydeck-xfer-")
        self.local = os.path.join(self.base, "local")
        self.ubuntu = os.path.join(self.base, "ubuntu")
        self.rpi = os.path.join(self.base, "rpi")
        for d in (self.local, self.ubuntu, self.rpi):
            os.makedirs(d)
        self.cu = sftp.SFTPClient([SFTP_SERVER, "-d", self.ubuntu]).connect()
        self.cr = sftp.SFTPClient([SFTP_SERVER, "-d", self.rpi]).connect()
        self.L = LocalBackend()
        self.U = RemoteBackend(self.cu, "Ubuntu")
        self.R = RemoteBackend(self.cr, "Raspberry Pi")

    def tearDown(self):
        self.cu.close()
        self.cr.close()
        shutil.rmtree(self.base)

    def write(self, path, data):
        with open(path, "wb") as f:
            f.write(data)

    def read(self, path):
        with open(path, "rb") as f:
            return f.read()

    def no_parts(self):
        return not glob.glob(os.path.join(self.base, "**", "*bunnydeck-part*"), recursive=True)

    def test_local_to_remote_and_back(self):
        data = os.urandom(700_000)
        self.write(os.path.join(self.local, "a.bin"), data)
        os.chmod(os.path.join(self.local, "a.bin"), 0o640)
        copy_file(self.L, os.path.join(self.local, "a.bin"), self.U,
                  self.U.join(self.U.home, "a.bin"))
        self.assertEqual(self.read(os.path.join(self.ubuntu, "a.bin")), data)
        self.assertEqual(os.stat(os.path.join(self.ubuntu, "a.bin")).st_mode & 0o777, 0o640)
        os.makedirs(os.path.join(self.local, "dl"))
        copy_file(self.U, self.U.join(self.U.home, "a.bin"), self.L,
                  os.path.join(self.local, "dl", "a.bin"))
        self.assertEqual(self.read(os.path.join(self.local, "dl", "a.bin")), data)
        self.assertTrue(self.no_parts())

    def test_remote_to_remote_via_staging_both_directions(self):
        data = os.urandom(1_500_000)
        self.write(os.path.join(self.rpi, "photo.jpg"), data)
        before = set(glob.glob(os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
                                            "bunny-deck-stage-*")))
        phases, prog = [], []
        copy_file(self.R, self.R.join(self.R.home, "photo.jpg"), self.U,
                  self.U.join(self.U.home, "photo.jpg"),
                  progress=lambda d, t: prog.append((d, t)), phase=phases.append)
        self.assertEqual(self.read(os.path.join(self.ubuntu, "photo.jpg")), data)
        self.assertEqual(prog[-1], (2 * len(data), 2 * len(data)))
        self.assertTrue(any("Downloading from Raspberry Pi" in p for p in phases))
        self.assertTrue(any("Uploading to Ubuntu" in p for p in phases))
        # source untouched, staging cleaned
        self.assertEqual(self.read(os.path.join(self.rpi, "photo.jpg")), data)
        after = set(glob.glob(os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"),
                                           "bunny-deck-stage-*")))
        self.assertEqual(before, after)
        # and back again into a subfolder
        os.makedirs(os.path.join(self.rpi, "back"))
        copy_file(self.U, self.U.join(self.U.home, "photo.jpg"), self.R,
                  self.R.join(self.R.join(self.R.home, "back"), "photo.jpg"))
        self.assertEqual(self.read(os.path.join(self.rpi, "back", "photo.jpg")), data)
        self.assertTrue(self.no_parts())

    def test_no_silent_overwrite_any_direction(self):
        self.write(os.path.join(self.local, "f"), b"local")
        self.write(os.path.join(self.ubuntu, "f"), b"ubuntu")
        self.write(os.path.join(self.rpi, "f"), b"rpi")
        cases = [
            (self.L, os.path.join(self.local, "f"), self.U, self.U.join(self.U.home, "f")),
            (self.U, self.U.join(self.U.home, "f"), self.L, os.path.join(self.local, "f")),
            (self.R, self.R.join(self.R.home, "f"), self.U, self.U.join(self.U.home, "f")),
        ]
        for sb, sp, db, dp in cases:
            with self.assertRaises((sftp.SFTPError, transfer.TransferError)):
                copy_file(sb, sp, db, dp, overwrite=False)
        self.assertEqual(self.read(os.path.join(self.local, "f")), b"local")
        self.assertEqual(self.read(os.path.join(self.ubuntu, "f")), b"ubuntu")
        self.assertTrue(self.no_parts())
        # confirmed overwrite
        copy_file(self.R, self.R.join(self.R.home, "f"), self.U, self.U.join(self.U.home, "f"),
                  overwrite=True)
        self.assertEqual(self.read(os.path.join(self.ubuntu, "f")), b"rpi")
        copy_file(self.U, self.U.join(self.U.home, "f"), self.L, os.path.join(self.local, "f"),
                  overwrite=True)
        self.assertEqual(self.read(os.path.join(self.local, "f")), b"rpi")

    def test_cancel_remote_to_remote_cleans_up(self):
        self.write(os.path.join(self.rpi, "big"), os.urandom(4_000_000))
        cancel = threading.Event()

        def progress(d, t):
            if d > t // 2 + 100_000:
                cancel.set()

        with self.assertRaises(sftp.Cancelled):
            copy_file(self.R, self.R.join(self.R.home, "big"), self.U,
                      self.U.join(self.U.home, "big"), progress=progress, cancel=cancel)
        self.assertFalse(os.path.exists(os.path.join(self.ubuntu, "big")))
        self.assertTrue(os.path.exists(os.path.join(self.rpi, "big")))
        self.assertTrue(self.no_parts())

    def make_tree(self, root):
        """project/{a.txt, sub/{b.txt, deeper/c.txt}, empty/, link -> sub, fifo}"""
        os.makedirs(os.path.join(root, "project", "sub", "deeper"))
        os.makedirs(os.path.join(root, "project", "empty"))
        self.write(os.path.join(root, "project", "a.txt"), b"aaa")
        self.write(os.path.join(root, "project", "sub", "b.txt"), b"bb")
        self.write(os.path.join(root, "project", "sub", "deeper", "c.txt"), b"c")
        os.symlink("sub", os.path.join(root, "project", "link"))
        os.mkfifo(os.path.join(root, "project", "fifo"))
        return os.path.join(root, "project")

    def check_plan(self, plan, dst):
        self.assertEqual([rel for _, rel in plan.dirs],
                         ["project", "project/empty", "project/sub", "project/sub/deeper"])
        self.assertEqual(plan.dirs[0][0], dst)
        self.assertEqual(sorted(f[3] for f in plan.files),
                         ["project/a.txt", "project/sub/b.txt", "project/sub/deeper/c.txt"])
        self.assertEqual(plan.total, 6)
        self.assertEqual(sorted(plan.skipped), ["project/fifo", "project/link"],
                         "symlinks are not followed, special files not copied")
        self.assertEqual(plan.errors, [])

    def test_plan_folder_local(self):
        src = self.make_tree(self.local)
        dst = self.U.join(self.U.home, "project")
        self.check_plan(transfer.plan_folder(self.L, src, self.U, dst), dst)

    def test_plan_folder_remote(self):
        self.make_tree(self.ubuntu)
        src = self.U.join(self.U.home, "project")
        dst = os.path.join(self.local, "project")
        self.check_plan(transfer.plan_folder(self.U, src, self.L, dst), dst)

    def test_plan_folder_unreadable_subfolder(self):
        src = self.make_tree(self.local)
        locked = os.path.join(src, "sub", "deeper")
        os.chmod(locked, 0)
        try:  # (restored here: tearDown runs before addCleanup)
            if os.access(locked, os.R_OK):
                self.skipTest("running as root")
            plan = transfer.plan_folder(self.L, src, self.L, os.path.join(self.base, "x"))
        finally:
            os.chmod(locked, 0o755)
        self.assertEqual([rel for rel, _ in plan.errors], ["project/sub/deeper"])
        self.assertIn("project/sub/b.txt", [f[3] for f in plan.files])

    def test_plan_folder_cancel(self):
        src = self.make_tree(self.local)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(sftp.Cancelled):
            transfer.plan_folder(self.L, src, self.L, os.path.join(self.base, "x"), cancel)

    def test_same_place_and_inside(self):
        self.assertTrue(transfer.same_place(self.L, LocalBackend()))
        self.assertTrue(transfer.same_place(self.U, RemoteBackend(self.cu, "again")))
        self.assertFalse(transfer.same_place(self.U, self.R))
        self.assertFalse(transfer.same_place(self.L, self.U))
        self.assertTrue(transfer.inside("/a/b", "/a/b"))
        self.assertTrue(transfer.inside("/a/b/c", "/a/b/"))
        self.assertFalse(transfer.inside("/a/bc", "/a/b"))

    def test_folder_rejected(self):
        os.makedirs(os.path.join(self.ubuntu, "dir"))
        with self.assertRaises(transfer.TransferError):
            copy_file(self.U, self.U.join(self.U.home, "dir"), self.L,
                      os.path.join(self.local, "dir"))

    def test_listing_and_errors(self):
        os.makedirs(os.path.join(self.ubuntu, "Documents"))
        names = {i.name: i for i in self.U.list(self.U.home)}
        self.assertTrue(names["Documents"].is_dir)
        with self.assertRaises(sftp.SFTPError):
            self.U.list(self.U.join(self.U.home, "missing"))
        with self.assertRaises(OSError):
            self.L.list(os.path.join(self.local, "missing"))

    def test_mkdir(self):
        self.U.mkdir(self.U.join(self.U.home, "new"))
        self.L.mkdir(os.path.join(self.local, "new"))
        self.assertTrue(os.path.isdir(os.path.join(self.ubuntu, "new")))
        self.assertTrue(os.path.isdir(os.path.join(self.local, "new")))


if __name__ == "__main__":
    unittest.main()
