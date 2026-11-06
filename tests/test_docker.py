"""Unit tests for Docker support (docker.py): parsing `docker ps` JSON,
labels and ports, grouping by compose project, status levels, the check's
output and errors, and that names are quoted in every command. No Docker
and no SSH are needed."""

import os
import shlex
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from bunnydeck import docker  # noqa: E402
from bunnydeck.config import Config, DockerInfo, Profile  # noqa: E402
from docker_sample import PS_LINES, RUNNING  # noqa: E402


class PortsTest(unittest.TestCase):
    def test_ipv4_and_ipv6_listed_once(self):
        self.assertEqual(docker.parse_ports("0.0.0.0:8080->80/tcp, :::8080->80/tcp"),
                         ["8080→80"])
        self.assertEqual(docker.parse_ports("0.0.0.0:3000->8080/tcp, [::]:3000->8080/tcp"),
                         ["3000→8080"])

    def test_unpublished_udp_and_ranges(self):
        self.assertEqual(docker.parse_ports("5432/tcp"), ["5432/tcp"])
        self.assertEqual(docker.parse_ports("0.0.0.0:53->53/udp"), ["53→53/udp"])
        self.assertEqual(docker.parse_ports("127.0.0.1:8000-8002->8000-8002/tcp"),
                         ["8000-8002→8000-8002"])

    def test_empty_and_unknown(self):
        self.assertEqual(docker.parse_ports(""), [])
        self.assertEqual(docker.parse_ports(None), [])
        self.assertEqual(docker.parse_ports("something odd"), ["something odd"])


class LabelsTest(unittest.TestCase):
    def test_plain(self):
        self.assertEqual(docker.parse_labels("a=1,b=two"), {"a": "1", "b": "two"})

    def test_value_with_comma_and_equals(self):
        labels = docker.parse_labels(
            "com.docker.compose.project=web,"
            "com.docker.compose.project.working_dir=/srv/a,b,x=y=z")
        self.assertEqual(labels["com.docker.compose.project.working_dir"], "/srv/a,b")
        self.assertEqual(labels["x"], "y=z")

    def test_empty(self):
        self.assertEqual(docker.parse_labels(""), {})
        self.assertEqual(docker.parse_labels(None), {})


class PsTest(unittest.TestCase):
    def setUp(self):
        self.containers = docker.parse_ps(PS_LINES)
        self.by_name = {c.name: c for c in self.containers}

    def test_all_parsed(self):
        self.assertEqual(len(self.containers), len(PS_LINES))
        self.assertEqual(sum(c.running for c in self.containers), RUNNING)
        c = self.by_name["open-webui"]
        self.assertEqual(c.image, "ghcr.io/open-webui/open-webui:main")
        self.assertEqual(c.ports, ["3000→8080"])
        self.assertEqual(c.project, "ai")
        self.assertEqual(c.working_dir, "/home/demo/docker/ai")

    def test_skips_noise(self):
        lines = ["", "WARNING: something", "{not json", "[1, 2]"] + PS_LINES[:1]
        self.assertEqual([c.name for c in docker.parse_ps(lines)], ["ollama"])

    def test_first_of_several_names(self):
        c = docker.Container({"Names": "app,other/alias", "State": "running"})
        self.assertEqual(c.name, "app")

    def test_levels(self):
        levels = {name: c.level for name, c in self.by_name.items()}
        self.assertEqual(levels["ollama"], "running")
        self.assertEqual(levels["open-webui"], "running")  # (healthy)
        self.assertEqual(levels["db"], "bad")  # (unhealthy)
        self.assertEqual(levels["ai-worker-1"], "bad")  # restarting
        self.assertEqual(levels["web-nginx-1"], "stopped")
        self.assertEqual(levels["weird'name;x"], "starting")  # (health: starting)
        self.assertEqual(docker.Container({"State": "dead"}).level, "bad")
        self.assertEqual(docker.Container({"State": "created"}).level, "stopped")

    def test_groups(self):
        groups = docker.group_by_project(self.containers)
        self.assertEqual([g[0] for g in groups], ["ai", "web", docker.NO_PROJECT])
        ai = groups[0]
        self.assertEqual(ai[1], "/home/demo/docker/ai")
        self.assertEqual([c.name for c in ai[2]], ["ai-worker-1", "ollama", "open-webui"])
        self.assertEqual([c.name for c in groups[2][2]], ["busy_turing", "weird'name;x"])

    def test_no_containers(self):
        self.assertEqual(docker.group_by_project([]), [])


class ProbeOutputTest(unittest.TestCase):
    def test_ok(self):
        info = docker.parse_probe("@@ok 3\n", now=100.0)
        self.assertEqual((info.has_docker, info.running, info.error, info.checked),
                         (True, 3, "", 100.0))
        self.assertTrue(info.usable)

    def test_no_docker(self):
        info = docker.parse_probe("@@nodocker\n")
        self.assertIs(info.has_docker, False)
        self.assertFalse(info.usable)

    def test_permission_denied_is_explained(self):
        info = docker.parse_probe(
            "@@error\npermission denied while trying to connect to the Docker daemon "
            "socket at unix:///var/run/docker.sock: connect: permission denied\n")
        self.assertEqual((info.has_docker, info.error), (True, "denied"))
        self.assertIn("docker group", info.message)
        self.assertFalse(info.usable)

    def test_daemon_down(self):
        info = docker.parse_probe("@@error\nCannot connect to the Docker daemon at "
                                  "unix:///var/run/docker.sock. Is the docker daemon running?")
        self.assertEqual(info.error, "daemon")

    def test_not_a_result(self):
        self.assertIsNone(docker.parse_probe(""))
        self.assertIsNone(docker.parse_probe("ssh: connect to host x: No route to host\n"))

    def test_watch_blocks(self):
        self.assertEqual(docker.split_blocks("@@ps\n{}\n{}\n"), ("ps", ["{}", "{}"]))
        self.assertEqual(docker.split_blocks("@@error\nboom\n"), ("error", ["boom"]))


class CommandTest(unittest.TestCase):
    NASTY = "x'; touch /tmp/pwned; echo '"

    def run_sh(self, command):
        """What the remote shell would see: the words it splits command into."""
        return shlex.split(command)

    def test_exec_quotes_name(self):
        words = self.run_sh(docker.exec_command(self.NASTY))
        self.assertEqual(words[:4], ["docker", "exec", "-it", self.NASTY])
        self.assertEqual(words[4:6], ["sh", "-c"])
        self.assertIn("exec bash || exec sh", words[6])

    def test_logs_quote(self):
        self.assertEqual(self.run_sh(docker.logs_command(self.NASTY)),
                         ["docker", "logs", "-f", "--tail", "200", self.NASTY])
        self.assertEqual(self.run_sh(docker.compose_logs_command("my project")),
                         ["docker", "compose", "-p", "my project", "logs", "-f", "--tail", "200"])

    def test_quoting_really_holds_in_sh(self):
        out = subprocess.run(["sh", "-c", docker.logs_command(self.NASTY).replace(
            "docker logs -f --tail 200", "printf %s")], capture_output=True, text=True)
        self.assertEqual(out.stdout, self.NASTY)

    def test_ssh_argv_uses_shared_connection_and_never_asks(self):
        argv = docker.ssh_argv(["-l", "demo", "--", "srv"], docker.PROBE_SCRIPT)
        self.assertIn("BatchMode=yes", argv)
        self.assertIn("ControlMaster=no", argv)
        self.assertTrue(any(a.startswith("ControlPath=") for a in argv))
        self.assertEqual(argv[-2], "srv")
        self.assertEqual(shlex.split(argv[-1])[:2], ["sh", "-c"])
        self.assertEqual(shlex.split(argv[-1])[2], docker.PROBE_SCRIPT)

    def test_master_check(self):
        argv = docker.master_check_argv(["--", "srv"])
        self.assertEqual(argv[-3:], ["check", "--", "srv"])

    def test_scripts_run_in_plain_sh(self):
        for script in (docker.PROBE_SCRIPT, docker.WATCH_SCRIPT):
            r = subprocess.run(["sh", "-n", "-c", script], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)


class ScriptTest(unittest.TestCase):
    """The probe script against tests/fake_docker.py, run here with sh."""

    def probe(self, mode):
        env = dict(os.environ, FAKE_SSH_LOG="", FAKE_DOCKER_MODE=mode)
        argv = docker.ssh_argv(["--", "srv"], docker.PROBE_SCRIPT)
        argv[0] = os.path.join(HERE, "fake_ssh_exec.py")
        r = subprocess.run(argv, capture_output=True, text=True, env=env, timeout=30)
        return docker.parse_probe(r.stdout)

    def test_modes(self):
        self.assertEqual((self.probe("ok").running, self.probe("ok").usable), (RUNNING, True))
        self.assertEqual(self.probe("denied").error, "denied")
        self.assertEqual(self.probe("daemon").error, "daemon")

    def test_no_docker_command(self):
        env = dict(os.environ, PATH="/usr/bin:/bin")
        r = subprocess.run(["sh", "-c", docker.PROBE_SCRIPT.replace("command -v docker",
                                                                   "command -v no-such-docker")],
                           capture_output=True, text=True, env=env)
        self.assertIs(docker.parse_probe(r.stdout).has_docker, False)


NASTY_DIR = "/srv/my app/x$(touch /tmp/bunnydeck-pwned)'; rm -rf ~; '"


def compose_container(project="web", workdir="/srv/my app",
                      files="/srv/my app/compose.yml,/srv/my app/compose.override.yml"):
    labels = (f"com.docker.compose.project={project},"
              f"com.docker.compose.project.working_dir={workdir},"
              f"com.docker.compose.project.config_files={files},"
              "com.docker.compose.service=app")
    return docker.Container({"Names": "app", "State": "running", "Labels": labels})


class ComposeLabelsTest(unittest.TestCase):
    def test_several_files_with_spaces(self):
        c = compose_container()
        self.assertTrue(c.is_compose)
        self.assertEqual(c.config_files, ["/srv/my app/compose.yml",
                                          "/srv/my app/compose.override.yml"])
        self.assertEqual(docker.project_info([c]),
                         {"web": ("/srv/my app", c.config_files)})

    def test_relative_files_and_duplicates(self):
        self.assertEqual(docker.split_config_files("compose.yml, ./x.yml,compose.yml", "/p"),
                         ["/p/compose.yml", "/p/./x.yml"])
        self.assertEqual(docker.split_config_files("", "/p"), [])

    def test_not_compose(self):
        c = docker.Container({"Names": "solo", "Labels": "maintainer=me"})
        self.assertFalse(c.is_compose)
        self.assertEqual(docker.project_info([c]), {})
        with self.assertRaises(ValueError):
            docker.compose_up_command("", "", [])

    def test_sample_projects(self):
        info = docker.project_info(docker.parse_ps(PS_LINES))
        self.assertEqual(info["ai"], ("/home/demo/docker/ai", ["/home/demo/docker/ai/compose.yml"]))


class ComposeCommandTest(unittest.TestCase):
    FILES = [NASTY_DIR + "/compose.yml", "/srv/my app/compose.override.yml"]

    def test_apply_words(self):
        cmd = docker.compose_up_command("my project", NASTY_DIR, self.FILES)
        cd, rest = cmd.split(" && ", 1)
        self.assertEqual(shlex.split(cd), ["cd", NASTY_DIR])
        self.assertEqual(shlex.split(rest), ["docker", "compose", "-p", "my project",
                                             "-f", self.FILES[0], "-f", self.FILES[1],
                                             "up", "-d"])

    def test_edit_words(self):
        cmd = docker.compose_edit_command(NASTY_DIR, self.FILES)
        cd, rest = cmd.split(" && ", 1)
        self.assertEqual(shlex.split(cd), ["cd", NASTY_DIR])
        self.assertEqual(shlex.split(rest), ["exec", "${EDITOR:-nano}"] + self.FILES)

    def test_commands_hold_in_sh(self):
        """Run through sh with harmless stand-ins: the nasty path stays one word."""
        import tempfile
        d = tempfile.mkdtemp()
        nasty = os.path.join(d, "my app", "x$(touch pwned)'; touch pwned2; '")
        os.makedirs(nasty)
        cmd = docker.compose_edit_command(nasty, [nasty + "/compose.yml"])
        r = subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, cwd=d,
                           env=dict(os.environ, EDITOR="printf %s\\n"))
        self.assertEqual(r.stdout.splitlines(), [nasty + "/compose.yml"])
        self.assertFalse(os.path.exists(os.path.join(d, "pwned")))
        self.assertFalse(os.path.exists(os.path.join(d, "pwned2")))
        self.assertFalse(os.path.exists(os.path.join(nasty, "pwned")))


class ComposeReadTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.dir = os.path.join(tempfile.mkdtemp(), "my app", "x$(touch pwned)'q")
        os.makedirs(self.dir)
        self.ok = os.path.join(self.dir, "compose.yml")
        with open(self.ok, "w") as f:
            f.write("services:\n  web:\n    image: nginx  # a comment\n@@bd-0000 end\n")
        self.locked = os.path.join(self.dir, "secret.yml")
        with open(self.locked, "w") as f:
            f.write("x: 1\n")
        os.chmod(self.locked, 0)
        self.missing = os.path.join(self.dir, "gone.yml")

    def read(self, files, nonce):
        r = subprocess.run(["sh", "-c", docker.compose_read_script(files, nonce)],
                           capture_output=True, text=True, cwd=self.dir)
        return docker.parse_compose_read(r.stdout, files, nonce)

    def test_ok_missing_unreadable(self):
        files = [self.ok, self.missing, self.locked]
        result = self.read(files, docker.new_nonce())
        self.assertEqual([s for _p, s, _c in result],
                         ["ok", "missing", "unreadable" if os.geteuid() else "ok"])
        self.assertEqual(result[0][2],
                         "services:\n  web:\n    image: nginx  # a comment\n@@bd-0000 end")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "pwned")))

    def test_marker_in_the_file_is_not_a_marker(self):
        # The file holds "@@bd-0000 end"; only the read's own nonce counts.
        result = self.read([self.ok], docker.new_nonce())
        self.assertIn("@@bd-0000 end", result[0][2])

    def test_unfinished_read(self):
        self.assertIsNone(docker.parse_compose_read("@@n ok\nservices:\n", [self.ok], "n"))
        self.assertIsNone(docker.parse_compose_read("", [self.ok], "n"))

    def test_nonces_differ(self):
        self.assertNotEqual(docker.new_nonce(), docker.new_nonce())


class ComposeAccessTest(unittest.TestCase):
    """Can a compose file be edited in a local editor (read, written, and its
    folder written, for the safe upload)?"""

    def setUp(self):
        import tempfile
        self.base = tempfile.mkdtemp()
        self.dir = os.path.join(self.base, "my app", "x$(touch pwned)'q")
        os.makedirs(self.dir)
        self.ok = self.make("compose.yml")
        self.readonly = self.make("ro.yml", 0o444)
        self.unreadable = self.make("secret.yml", 0)
        self.locked_dir = os.path.join(self.base, "locked")
        os.makedirs(self.locked_dir)
        self.in_locked = os.path.join(self.locked_dir, "compose.yml")
        with open(self.in_locked, "w") as f:
            f.write("x: 1\n")
        os.chmod(self.locked_dir, 0o555)
        self.addCleanup(os.chmod, self.locked_dir, 0o755)

    def make(self, name, mode=0o644):
        path = os.path.join(self.dir, name)
        with open(path, "w") as f:
            f.write("x: 1\n")
        os.chmod(path, mode)
        return path

    def check_access(self, files):
        nonce = docker.new_nonce()
        r = subprocess.run(["sh", "-c", docker.compose_access_script(files, nonce)],
                           capture_output=True, text=True, cwd=self.base)
        return docker.parse_compose_access(r.stdout, files, nonce)

    def test_statuses(self):
        if os.geteuid() == 0:
            self.skipTest("root may read and write everything")
        missing = os.path.join(self.dir, "gone.yml")
        got = self.check_access([self.ok, self.readonly, self.unreadable, self.in_locked,
                                 missing])
        self.assertEqual(got, {self.ok: "ok", self.readonly: "readonly",
                               self.unreadable: "unreadable",
                               self.in_locked: "folderreadonly", missing: "missing"})
        self.assertFalse(os.path.exists(os.path.join(self.base, "pwned")))
        self.assertFalse(os.path.exists(os.path.join(self.dir, "pwned")))

    def test_every_problem_is_explained(self):
        for status in ("missing", "unreadable", "readonly", "folderreadonly"):
            self.assertIn(status, docker.ACCESS_PROBLEMS)

    def test_incomplete_or_forged(self):
        files = [self.ok]
        self.assertIsNone(docker.parse_compose_access("", files, "n"))
        self.assertIsNone(docker.parse_compose_access("@@n ok\n", files, "n"))
        self.assertIsNone(docker.parse_compose_access("@@other ok\n@@other end\n", files, "n"))
        self.assertEqual(docker.parse_compose_access("@@n weird\n@@n end\n", files, "n"),
                         {self.ok: "missing"})


class ConfigTest(unittest.TestCase):
    def test_round_trip_and_old_files(self):
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "config.json")
        cfg = Config(path)
        p = cfg.add(Profile(name="srv", host="srv.example"))
        self.assertIsNone(p.docker.has_docker)  # unknown until checked
        cfg.set_docker(p.id, DockerInfo(has_docker=True, running=5, checked=123.0))
        again = Config(path).get(p.id)
        self.assertEqual((again.docker.has_docker, again.docker.running, again.docker.checked),
                         (True, 5, 123.0))
        old = Profile.from_dict({"name": "x", "host": "y"})  # written before Docker
        self.assertIsNone(old.docker.has_docker)
        bad = Profile.from_dict({"name": "x", "host": "y",
                                 "docker": {"has_docker": "yes", "running": "many",
                                            "error": "weird"}})
        self.assertIsNone(bad.docker.has_docker)
        self.assertEqual((bad.docker.running, bad.docker.error), (0, "other"))


if __name__ == "__main__":
    unittest.main()
