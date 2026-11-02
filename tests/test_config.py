import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bunnydeck.config import (  # noqa: E402
    AITool, Appearance, Config, Profile, backgrounds_dir, store_background,
    validate_host, validate_user,
)


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="bunnydeck-cfg-")
        self.path = os.path.join(self.dir, "bunny-deck", "config.json")
        self.addCleanup(shutil.rmtree, self.dir)

    def test_roundtrip_and_stable_ids(self):
        c = Config(self.path)
        p = c.add(Profile(name="Pi", host="pi.local", user="pi", port=2222))
        c.update_appearance(p.id, Appearance("#0f2318", 0.85))
        c2 = Config(self.path)
        q = c2.get(p.id)
        self.assertEqual((q.name, q.host, q.user, q.port), ("Pi", "pi.local", "pi", 2222))
        self.assertEqual((q.appearance.background, q.appearance.opacity), ("#0f2318", 0.85))
        self.assertEqual(os.stat(os.path.dirname(self.path)).st_mode & 0o777, 0o700)

    def test_ai_tools_roundtrip(self):
        c = Config(self.path)
        t = c.add_ai_tool(AITool(name="Claude", command="claude --resume"))
        c.add_ai_tool(AITool(name="OpenCode", command="opencode"))
        c.add(Profile(name="Pi", host="pi.local"))
        c2 = Config(self.path)
        self.assertEqual([(x.name, x.command) for x in c2.ai_tools],
                         [("Claude", "claude --resume"), ("OpenCode", "opencode")])
        self.assertEqual(len(c2.profiles), 1)
        self.assertEqual(c2.remove_ai_tool(t.id).name, "Claude")
        self.assertEqual([x.name for x in Config(self.path).ai_tools], ["OpenCode"])

    def test_invalid_ai_tools_skipped(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            json.dump({"profiles": [], "ai_tools": [{"name": "x"}, {"command": "  "},
                                                    {"command": "codex"}]}, f)
        self.assertEqual([(t.name, t.command) for t in Config(self.path).ai_tools],
                         [("codex", "codex")])

    def test_background_image_and_local_appearance(self):
        c = Config(self.path)
        c.update_local_appearance(Appearance("#0f2318", 0.9, image="/x/y.png",
                                             image_mode="center"))
        t = c.add_ai_tool(AITool(name="Claude", command="claude"))
        c.update_ai_tool_appearance(t.id, Appearance(image="/a.png", image_mode="stretch"))
        c2 = Config(self.path)
        self.assertEqual(c2.local_appearance, Appearance("#0f2318", 0.9, "/x/y.png", "center"))
        self.assertEqual(c2.ai_tools[0].appearance.image_mode, "stretch")
        a = Appearance.from_dict({"image": 5, "image_mode": "evil"})
        self.assertEqual((a.image, a.image_mode), ("", "fill"))
        self.assertFalse(Appearance(image="/a.png").is_default())

    def test_local_default_has_no_image(self):
        c = Config(self.path)
        self.assertEqual(c.local_appearance, Appearance())
        # An explicit choice (also "no image") is kept.
        c.update_local_appearance(Appearance("#000000", 0.9))
        self.assertEqual(Config(self.path).local_appearance, Appearance("#000000", 0.9))

    def test_store_background_copies_once(self):
        os.environ["XDG_CONFIG_HOME"] = self.dir
        self.addCleanup(os.environ.pop, "XDG_CONFIG_HOME")
        src = os.path.join(self.dir, "pic.PNG")
        with open(src, "wb") as f:
            f.write(b"image bytes")
        stored = store_background(src)
        self.assertEqual(os.path.dirname(stored), backgrounds_dir())
        self.assertTrue(stored.endswith(".png"))
        self.assertEqual(store_background(stored), stored)
        self.assertEqual(store_background(src), stored)
        self.assertEqual(len(os.listdir(backgrounds_dir())), 1)
        self.assertEqual(store_background(""), "")

    def test_no_secrets_in_file(self):
        c = Config(self.path)
        c.add(Profile(name="x", host="h"))
        with open(self.path) as f:
            text = f.read().lower()
        self.assertNotIn("password", text)
        self.assertNotIn("passphrase", text)

    def test_invalid_appearance_sanitised(self):
        a = Appearance.from_dict({"background": "red; evil", "opacity": "7"})
        self.assertEqual((a.background, a.opacity), ("#0e1013", 1.0))

    def test_broken_file_is_preserved(self):
        os.makedirs(os.path.dirname(self.path))
        with open(self.path, "w") as f:
            f.write("{not json")
        c = Config(self.path)
        self.assertEqual(c.profiles, [])
        c.add(Profile(name="a", host="b"))
        self.assertTrue(os.path.exists(self.path + ".broken"))
        with open(self.path) as f:
            self.assertEqual(len(json.load(f)["profiles"]), 1)

    def test_ssh_args(self):
        self.assertEqual(Profile(name="a", host="h").ssh_args(), ["--", "h"])
        self.assertEqual(Profile(name="a", host="h", user="u", port=2200).ssh_args(),
                         ["-p", "2200", "-l", "u", "--", "h"])

    def test_validation(self):
        self.assertIsNotNone(validate_host(""))
        self.assertIsNotNone(validate_host("-oProxyCommand=x"))
        self.assertIsNotNone(validate_host("a b"))
        self.assertIsNone(validate_host("pi.local"))
        self.assertIsNotNone(validate_user("-l"))
        self.assertIsNone(validate_user(""))


if __name__ == "__main__":
    unittest.main()
