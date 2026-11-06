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
