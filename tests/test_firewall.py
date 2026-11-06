"""Unit tests for the firewall (firewall.py): checking ports, addresses and
comments, attempts to sneak a command in, the ufw commands, the server info
and reading `ufw status numbered` from terminal output. No ufw is needed."""

import os
import shlex
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from bunnydeck import firewall  # noqa: E402

STATUS = """Status: active

     To                         Action      From
     --                         ------      ----
[ 1] 22/tcp                     ALLOW IN    Anywhere                   # ssh
[ 2] 8080/tcp                   ALLOW IN    192.168.1.0/24
[ 3] 8000:8010/udp              ALLOW IN    10.0.0.5                   # game servers
[ 4] 443                        DENY IN     203.0.113.9
[ 5] OpenSSH                    LIMIT IN    Anywhere
[ 6] 22/tcp (v6)                ALLOW IN    Anywhere (v6)              # ssh
"""

# Things a user might paste that must never become part of a command.
INJECTIONS = [
    "8080; rm -rf /", "8080 && reboot", "$(reboot)", "`reboot`", "8080|nc x 1",
    "8080\nreboot", "'; reboot; '", "-1", "80 80", "0x50",
]


class PortTest(unittest.TestCase):
    def test_single_and_range(self):
        self.assertEqual(firewall.parse_port("8080"), ("8080", False))
        self.assertEqual(firewall.parse_port(" 8000:8010 "), ("8000:8010", True))
        self.assertEqual(firewall.parse_port("8000-8010"), ("8000:8010", True))

    def test_limits(self):
        for bad in ("0", "65536", "99999", "8010:8000", "8000:8000", "", "abc", ":80", "80:"):
            with self.assertRaises(ValueError, msg=bad):
                firewall.parse_port(bad)
        self.assertEqual(firewall.parse_port("65535"), ("65535", False))

    def test_injections(self):
        for bad in INJECTIONS:
            with self.assertRaises(ValueError, msg=bad):
                firewall.parse_port(bad)


class SourceTest(unittest.TestCase):
    def test_addresses_and_networks(self):
        self.assertEqual(firewall.parse_source("192.168.1.20"), "192.168.1.20")
        self.assertEqual(firewall.parse_source("192.168.1.7/24"), "192.168.1.0/24")
        self.assertEqual(firewall.parse_source("10.0.0.5/32"), "10.0.0.5")
        self.assertEqual(firewall.parse_source("2001:db8::/32"), "2001:db8::/32")

    def test_rejects(self):
        for bad in ["", "192.168.1.300", "example.com", "192.168.1.0/33", "any",
                    "192.168.1.0/24; reboot", "$(id)", "10.0.0.1 && x"]:
            with self.assertRaises(ValueError, msg=bad):
                firewall.parse_source(bad)


class CommentTest(unittest.TestCase):
    def test_allowed(self):
        self.assertEqual(firewall.parse_comment("  Open WebUI (port 3000)  "),
                         "Open WebUI (port 3000)")
        self.assertEqual(firewall.parse_comment(""), "")

    def test_rejects(self):
        for bad in ["it's", 'say "hi"', "a; reboot", "$(id)", "`id`", "a\nb", "a|b", "a&b",
                    "x" * (firewall.COMMENT_MAX + 1), "back\\slash", "#hash"]:
            with self.assertRaises(ValueError, msg=bad):
                firewall.parse_comment(bad)


class CommandTest(unittest.TestCase):
    def words(self, command):
        return shlex.split(command)

    def test_lan_tcp_with_comment(self):
        cmds = firewall.allow_commands("8080", "tcp", "192.168.1.0/24", "web ui")
        self.assertEqual(cmds, ["sudo ufw allow from 192.168.1.0/24 to any port 8080 proto tcp "
                                "comment 'web ui'"])
        self.assertEqual(self.words(cmds[0])[-2:], ["comment", "web ui"])

    def test_both_protocols(self):
        self.assertEqual(firewall.allow_commands("53", "both", None),
                         ["sudo ufw allow from any to any port 53"])
        # ufw needs a protocol with a range: one command for each.
        cmds = firewall.allow_commands("8000:8010", "both", "10.0.0.0/8")
        self.assertEqual([self.words(c)[-1] for c in cmds], ["tcp", "udp"])

    def test_every_input_is_checked(self):
        for bad in INJECTIONS:
            with self.assertRaises(ValueError):
                firewall.allow_commands(bad, "tcp", None)
            with self.assertRaises(ValueError):
                firewall.allow_commands("80", "tcp", bad)
            if any(c in bad for c in ";&|$`'\n"):  # ("-1", "80 80" are fine comments)
                with self.assertRaises(ValueError, msg=bad):
                    firewall.allow_commands("80", "tcp", None, bad)
        with self.assertRaises(ValueError):
            firewall.allow_commands("80", "tcp; reboot", None)

    def test_command_is_what_sh_runs(self):
        cmd = firewall.allow_commands("8080", "udp", "10.1.2.3", "a (b), c:d/e@f+g-h")[0]
        out = subprocess.run(["sh", "-c", cmd.replace("sudo ufw", "printf '%s\\n'")],
                             capture_output=True, text=True).stdout.splitlines()
        self.assertEqual(out, ["allow", "from", "10.1.2.3", "to", "any", "port", "8080",
                               "proto", "udp", "comment", "a (b), c:d/e@f+g-h"])

    def test_delete(self):
        self.assertEqual(firewall.delete_command(3), "sudo ufw delete 3")
        self.assertNotIn("--force", firewall.delete_command(3))
        for bad in (0, -1, "3; reboot"):
            with self.assertRaises(ValueError):
                firewall.delete_command(bad)

    def test_never_reads_a_password(self):
        cmd = firewall.with_status(firewall.allow_commands("80", "tcp", None)[0])
        self.assertNotIn("-S", self.words(cmd.replace(";", " ")))
        self.assertNotIn("NOPASSWD", cmd)

    def test_markers_only_in_output(self):
        # The typed command line must not contain the markers themselves.
        self.assertNotIn(firewall.STATUS_BEGIN, firewall.STATUS_COMMAND)
        self.assertNotIn(firewall.STATUS_END, firewall.STATUS_COMMAND)
        out = subprocess.run(["sh", "-c", firewall.STATUS_COMMAND.replace(
            "sudo ufw status numbered", "true")], capture_output=True, text=True).stdout
        self.assertEqual(out.split(), [firewall.STATUS_BEGIN, firewall.STATUS_END])


class StatusTest(unittest.TestCase):
    def test_rules(self):
        st = firewall.parse_status(STATUS)
        self.assertIs(st.active, True)
        self.assertEqual([r.number for r in st.rules], [1, 2, 3, 4, 5, 6])
        r1, r2, r3, r4, r5, r6 = st.rules
        self.assertEqual((r1.to, r1.action, r1.source, r1.comment), ("22/tcp", "ALLOW IN",
                                                                     "Anywhere", "ssh"))
        self.assertEqual((r2.source, r2.comment), ("192.168.1.0/24", ""))
        self.assertEqual((r3.to, r3.comment), ("8000:8010/udp", "game servers"))
        self.assertEqual(r4.action, "DENY IN")
        self.assertEqual((r5.to, r5.action), ("OpenSSH", "LIMIT IN"))
        self.assertTrue(r6.v6 and not r1.v6)

    def test_inactive(self):
        st = firewall.parse_status("Status: inactive\n")
        self.assertIs(st.active, False)
        self.assertEqual(st.rules, [])

    def test_errors(self):
        st = firewall.parse_status("sudo: 3 incorrect password attempts\n")
        self.assertIsNone(st.active)
        self.assertIn("incorrect password", st.error)
        st = firewall.parse_status("sudo: ufw: command not found\n")
        self.assertIn("command not found", st.error)

    def test_reader_from_terminal_output(self):
        r = firewall.StatusReader()
        typed = "demo@srv:~$ " + firewall.STATUS_COMMAND + "\r\n[sudo] password for demo: \r\n"
        self.assertIsNone(r.feed(typed))
        self.assertIsNone(r.feed(firewall.STATUS_BEGIN + "\r\n" + STATUS.replace("\n", "\r\n")))
        st = r.feed("\x1b[0m" + firewall.STATUS_END + "\r\ndemo@srv:~$ ")
        self.assertEqual(len(st.rules), 6)
        self.assertIsNone(r.feed("ls\r\n"), "nothing new until the next status")


class InfoTest(unittest.TestCase):
    ROUTE = ["default via 192.168.1.1 dev eth0 proto dhcp src 192.168.1.50 metric 100"]
    ADDR = [
        "1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever",
        "2: docker0    inet 172.17.0.1/16 brd 172.17.255.255 scope global docker0\\ x",
        "3: eth0    inet 192.168.1.50/24 brd 192.168.1.255 scope global dynamic eth0\\ x",
    ]

    def test_lan_is_the_default_routes_network(self):
        self.assertEqual(firewall.lan_from(self.ROUTE, self.ADDR), "192.168.1.0/24")

    def test_lan_without_default_route(self):
        self.assertEqual(firewall.lan_from([], self.ADDR), "172.17.0.0/16")
        self.assertEqual(firewall.lan_from([], self.ADDR[:1]), "")

    def test_parse_info(self):
        text = ("@@ufw\ninstalled\n@@enabled\nENABLED=yes\n@@route\n" + self.ROUTE[0]
                + "\n@@addr\n" + "\n".join(self.ADDR) + "\n@@end\n")
        info = firewall.parse_info(text)
        self.assertEqual((info.installed, info.enabled, info.lan), (True, True, "192.168.1.0/24"))
        info = firewall.parse_info("@@ufw\nmissing\n@@enabled\n@@route\n@@addr\n@@end\n")
        self.assertEqual((info.installed, info.enabled), (False, None))
        self.assertIsNone(firewall.parse_info("ssh: connect to host x: No route to host\n"))

    def test_info_script_runs_in_sh(self):
        r = subprocess.run(["sh", "-n", "-c", firewall.info_script()], capture_output=True)
        self.assertEqual(r.returncode, 0)
        r = subprocess.run(["sh", "-c", firewall.info_script("/nonexistent")],
                           capture_output=True, text=True)
        self.assertIsNotNone(firewall.parse_info(r.stdout))


if __name__ == "__main__":
    unittest.main()
