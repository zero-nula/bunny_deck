"""Finding text editors and building the command that opens a file
(editors.py), with desktop files in a throwaway XDG data directory."""

import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bunnydeck import editors  # noqa: E402

ENTRY = """[Desktop Entry]
Type=Application
Name={name}
Name[sl]=Prevod
Exec={exec}
Icon={icon}
MimeType={mime}
Categories={cat}
{extra}
[Desktop Action new]
Name=New Window
Exec=other --new %U
"""


class EditorsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bunnydeck-editors-")
        self.home = os.path.join(self.tmp, "home")
        self.sys = os.path.join(self.tmp, "sys")
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        for prog in ("kate", "zed", "writer", "nvim", "code"):
            path = os.path.join(self.bin, prog)
            with open(path, "w") as f:
                f.write("#!/bin/sh\n")
            os.chmod(path, 0o755)
        env = {"XDG_DATA_HOME": self.home, "XDG_DATA_DIRS": self.sys,
               "PATH": self.bin}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(shutil.rmtree, self.tmp)

    def entry(self, base, did, name, exec_, mime="text/plain;", cat="Utility;TextEditor;",
              icon="", extra=""):
        path = os.path.join(base, "applications", did)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(ENTRY.format(name=name, exec=exec_, mime=mime, cat=cat, icon=icon,
                                 extra=extra))
        return path

    def test_finds_only_graphical_text_editors(self):
        self.entry(self.sys, "org.kde.kate.desktop", "Kate", "kate -b %U", icon="kate")
        self.entry(self.home, "dev.zed.Zed.desktop", "Zed",
                   f"{self.bin}/zed %U", cat="Development;IDE;")
        self.entry(self.sys, "writer.desktop", "Writer", "writer %U", cat="Office;")
        self.entry(self.sys, "nvim.desktop", "Neovim", "nvim %F", extra="Terminal=true")
        self.entry(self.sys, "hidden.desktop", "Hidden", "kate %U", extra="NoDisplay=true")
        self.entry(self.sys, "images.desktop", "Images", "kate %U", mime="image/png;")
        self.entry(self.sys, "gone.desktop", "Gone", "not-installed %U")
        self.entry(self.sys, "tryexec.desktop", "TryExec", "kate %U",
                   extra="TryExec=not-installed")
        found = editors.find_editors()
        self.assertEqual([e.name for e in found], ["Kate", "Zed"])
        kate = found[0]
        self.assertEqual(kate.id, "org.kde.kate.desktop")
        self.assertEqual(kate.exec, "kate -b %U")  # not the desktop action's Exec
        self.assertEqual(kate.choice, "desktop:org.kde.kate.desktop")

    def test_user_entry_hides_system_entry_and_subfolder_ids(self):
        self.entry(self.sys, "kate.desktop", "System Kate", "kate %U")
        self.entry(self.home, "kate.desktop", "My Kate", "kate --new %U")
        self.entry(self.sys, "kde/zed.desktop", "Zed", "zed %U")
        found = {e.id: e.name for e in editors.find_editors()}
        self.assertEqual(found, {"kate.desktop": "My Kate", "kde-zed.desktop": "Zed"})

    def test_command_field_codes(self):
        e = editors.Editor("x.desktop", "Kate", 'kate -b %U --title "%c" %i %k %%x', "kate",
                           "/apps/x.desktop")
        self.assertEqual(editors.command_for(e, "/tmp/a b.conf"),
                         ["kate", "-b", "/tmp/a b.conf", "--title", "Kate", "--icon", "kate",
                          "/apps/x.desktop", "%x"])
        # Without a file code the file goes at the end; unknown codes are dropped.
        e = editors.Editor("", "konsole -e nvim", "konsole -e nvim %d")
        self.assertEqual(editors.command_for(e, "/f"), ["konsole", "-e", "nvim", "/f"])
        e = editors.Editor("", "x", "sh -c 'edit --file=%f'")
        self.assertEqual(editors.command_for(e, "/f"), ["sh", "-c", "edit --file=/f"])

    def test_resolve_and_system_default(self):
        self.entry(self.sys, "kate.desktop", "Kate", "kate %U")
        self.entry(self.sys, "zed.desktop", "Zed", "zed %U")
        with mock.patch.object(editors, "system_default_id", return_value="zed.desktop"):
            self.assertEqual(editors.resolve("").name, "Zed")
            self.assertEqual(editors.resolve("desktop:kate.desktop").name, "Kate")
            # An editor that was uninstalled falls back to the system default.
            self.assertEqual(editors.resolve("desktop:gone.desktop").name, "Zed")
            e = editors.resolve("command:konsole -e nvim")
            self.assertEqual((e.id, e.exec), ("", "konsole -e nvim"))
        with mock.patch.object(editors, "system_default_id", return_value=""):
            self.assertEqual(editors.resolve("").name, "Kate")  # the first one found
        with mock.patch.object(editors, "system_default_id", return_value="gone.desktop"):
            self.assertEqual(editors.resolve("").name, "Kate")

    def test_no_editor(self):
        with mock.patch.object(editors, "system_default_id", return_value=""):
            self.assertIsNone(editors.resolve(""))

    def test_check_command(self):
        self.assertIsNone(editors.check_command("kate -n"))
        self.assertIn("not found", editors.check_command("not-installed"))
        self.assertIsNotNone(editors.check_command(""))
        self.assertIsNotNone(editors.check_command('kate "unclosed'))


if __name__ == "__main__":
    unittest.main()
