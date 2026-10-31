"""Local terminal profiles: config compatibility and the command that runs."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bunnydeck.config import KIND_LOCAL, KIND_SSH, Config, Profile  # noqa: E402
from bunnydeck import sshmux, terminal  # noqa: E402


class LocalProfileConfigTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="bunnydeck-cfg-")
        self.path = os.path.join(self.dir, "bunny-deck", "config.json")
        self.addCleanup(shutil.rmtree, self.dir)

    def write(self, profiles):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            json.dump({"version": 1, "profiles": profiles}, f)

    def test_old_config_without_kind_loads_as_ssh(self):
        # Exactly the shape written before "kind" existed.
        self.write([{"name": "Pi", "host": "pi.local", "user": "pi", "port": 2222,
                     "id": "abc", "appearance": {"background": "#0e1013", "opacity": 1.0}}])
        p = Config(self.path).get("abc")
        self.assertEqual(p.kind, KIND_SSH)
        self.assertFalse(p.is_local)
        self.assertEqual(p.ssh_args(), ["-p", "2222", "-l", "pi", "--", "pi.local"])
        self.assertEqual(p.target, "pi@pi.local:2222")

    def test_local_roundtrip_without_host(self):
        c = Config(self.path)
        c.add(Profile(name="Here", host="", kind=KIND_LOCAL, id="loc"))
        with open(self.path) as f:
            data = json.load(f)
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["profiles"][0]["kind"], "local")
        p = Config(self.path).get("loc")
        self.assertTrue(p.is_local)
        self.assertEqual((p.name, p.target), ("Here", "local"))

    def test_local_entry_without_host_key(self):
        self.write([{"name": "Here", "kind": "local", "id": "loc"}])
        self.assertTrue(Config(self.path).get("loc").is_local)

    def test_ssh_entry_without_host_still_skipped(self):
        self.write([{"name": "Broken", "id": "x"}, {"name": "Ok", "host": "h", "id": "y"}])
        c = Config(self.path)
        self.assertIsNone(c.get("x"))
        self.assertIsNotNone(c.get("y"))

    def test_unknown_kind_falls_back_to_ssh(self):
        self.write([{"name": "Odd", "host": "h", "kind": "telnet", "id": "z"}])
        self.assertEqual(Config(self.path).get("z").kind, KIND_SSH)


class LocalArgvTest(unittest.TestCase):
    def test_ssh_argv_shares_the_connection(self):
        p = Profile(name="a", host="h", user="u", port=2200)
        self.assertEqual(terminal.default_argv(p), ["/usr/bin/ssh", *sshmux.master_opts(),
                                                    "-p", "2200", "-l", "u", "--", "h"])
        self.assertIn("ControlMaster=auto", sshmux.master_opts())

    def test_local_uses_shell_without_arguments(self):
        with mock.patch.dict(os.environ, {"SHELL": "/bin/sh"}):
            self.assertEqual(terminal.default_argv(Profile(name="l", host="", kind=KIND_LOCAL)),
                             ["/bin/sh"])

    def test_shell_fallback(self):
        for value in (None, "", "zsh", "/nonexistent/shell", "/etc/passwd", "/tmp"):
            env = dict(os.environ)
            env.pop("SHELL", None)
            if value is not None:
                env["SHELL"] = value
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(terminal.local_shell(), "/bin/bash", value)


if __name__ == "__main__":
    unittest.main()
