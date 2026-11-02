"""Unit tests for the network scan (netscan.py): nmap XML parsing, device
guesses, the system tables, and a whole scan against a fake nmap."""

import ipaddress
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from bunnydeck import netscan  # noqa: E402
from bunnydeck.netscan import Host, Port  # noqa: E402


def fake_nmap_dir():
    """A temp dir with an "nmap" that runs tests/fake_nmap.py."""
    d = tempfile.mkdtemp(prefix="bunnydeck-fake-nmap-")
    path = os.path.join(d, "nmap")
    with open(path, "w") as f:
        f.write(f'#!/bin/sh\nexec "{sys.executable}" "{os.path.join(HERE, "fake_nmap.py")}" "$@"\n')
    os.chmod(path, 0o755)
    return d


class TargetTest(unittest.TestCase):
    def test_networks(self):
        self.assertEqual(str(netscan.parse_target(" 192.168.1.7/24 ")), "192.168.1.0/24")
        self.assertEqual(str(netscan.parse_target("10.0.0.5")), "10.0.0.5/32")

    def test_rejects(self):
        for bad in ("", "-oX /tmp/x", "example.com", "fe80::/64", "10.0.0.0/8",
                    "192.168.1.0/24 -sV"):
            with self.assertRaises(ValueError, msg=bad):
                netscan.parse_target(bad)

    def test_argv_keeps_target_after_double_dash(self):
        net = netscan.parse_target("192.168.1.0/24")
        argv = netscan.discovery_argv("nmap", net)
        self.assertEqual(argv[-2:], ["--", "192.168.1.0/24"])
        self.assertIn("-sn", argv)
        argv = netscan.port_scan_argv("nmap", ["10.0.0.1", "10.0.0.2"], versions=True)
        self.assertEqual(argv[-3:], ["--", "10.0.0.1", "10.0.0.2"])
        self.assertIn("-sV", argv)
        self.assertIn("-sT", argv)  # connect scan: works without root


class XMLTest(unittest.TestCase):
    XML = ('<?xml version="1.0"?><nmaprun>'
           '<taskprogress task="Ping Scan" percent="12.50" remaining="3"/>'
           '<hosthint><status state="up"/><address addr="10.0.0.9" addrtype="ipv4"/></hosthint>'
           '<host><status state="up"/><address addr="10.0.0.1" addrtype="ipv4"/>'
           '<address addr="AA:BB:CC:00:11:22" addrtype="mac" vendor="Acme"/>'
           '<hostnames><hostname name="router.home.lan" type="PTR"/></hostnames>'
           '<ports><port protocol="tcp" portid="443"><state state="open"/>'
           '<service name="http" tunnel="ssl" product="nginx" version="1.25"/></port>'
           '<port protocol="tcp" portid="22"><state state="open"/><service name="ssh"/></port>'
           '<port protocol="tcp" portid="23"><state state="closed"/></port></ports></host>'
           '<host><status state="down"/><address addr="10.0.0.2" addrtype="ipv4"/></host>'
           '</nmaprun>')

    def test_parse_whole(self):
        events = netscan.XMLStream().feed(self.XML)
        self.assertEqual(events[0], ("progress", 12.5))
        hosts = [v for k, v in events if k == "host"]
        self.assertEqual(len(hosts), 1)  # not the hint, not the down host
        h = hosts[0]
        self.assertEqual((h.ip, h.hostname, h.name), ("10.0.0.1", "router.home.lan", "router"))
        self.assertEqual((h.mac, h.vendor), ("aa:bb:cc:00:11:22", "Acme"))
        self.assertEqual(h.port_numbers, [22, 443])
        self.assertEqual(h.ports[1].service, "https")
        self.assertEqual(h.ports[1].label, "https nginx 1.25")
        self.assertTrue(h.ports_scanned)
        self.assertEqual(h.ssh_port, 22)
        self.assertEqual(h.web_urls, ["https://10.0.0.1/"])

    def test_parse_in_pieces(self):
        for size in (1, 7, 50):
            stream = netscan.XMLStream()
            events = []
            for i in range(0, len(self.XML), size):
                events += stream.feed(self.XML[i:i + size])
            self.assertEqual([k for k, _ in events], ["progress", "host"], size)

    def test_discovery_host_has_no_port_info(self):
        (_, h), = netscan.XMLStream().feed(
            '<host><status state="up"/><address addr="10.0.0.3" addrtype="ipv4"/>'
            '<hostnames/></host>')
        self.assertFalse(h.ports_scanned)
        self.assertEqual(h.name, "10.0.0.3")


class KindTest(unittest.TestCase):
    def kind(self, ports, **kw):
        return Host("10.0.0.5", ports=[Port(n) for n in ports], **kw).kind

    def test_guesses(self):
        K = netscan
        self.assertEqual(self.kind([22], is_self=True), K.KIND_SELF)
        self.assertEqual(self.kind([80, 443], is_gateway=True), K.KIND_ROUTER)
        self.assertEqual(self.kind([22, 8006]), K.KIND_HYPERVISOR)
        self.assertEqual(self.kind([631, 9100]), K.KIND_PRINTER)
        self.assertEqual(self.kind([22, 445, 5001]), K.KIND_NAS)
        self.assertEqual(self.kind([22]), K.KIND_SERVER)
        self.assertEqual(self.kind([135, 445, 3389]), K.KIND_WINDOWS)
        self.assertEqual(self.kind([554, 80]), K.KIND_CAMERA)
        self.assertEqual(self.kind([1883]), K.KIND_IOT)
        self.assertEqual(self.kind([8080]), K.KIND_WEB)
        self.assertEqual(self.kind([], mac="da:a1:19:00:00:01"), K.KIND_PHONE)
        self.assertEqual(self.kind([]), K.KIND_UNKNOWN)

    def test_web_urls_and_ssh(self):
        h = Host("10.0.0.5", ports=[Port(80, "http"), Port(2222, "ssh"), Port(8443)])
        self.assertEqual(h.web_urls, ["http://10.0.0.5/", "https://10.0.0.5:8443/"])
        self.assertEqual(h.ssh_port, 2222)

    def test_merge(self):
        a = Host("10.0.0.5", hostname="pi")
        b = Host("10.0.0.5", mac="b8:27:eb:00:00:01", ports=[Port(22)], ports_scanned=True)
        a.merge(b)
        self.assertEqual((a.hostname, a.mac, a.port_numbers, a.ports_scanned),
                         ("pi", "b8:27:eb:00:00:01", [22], True))


class SystemTablesTest(unittest.TestCase):
    def write(self, text):
        fd, path = tempfile.mkstemp()
        with os.fdopen(fd, "w") as f:
            f.write(text)
        self.addCleanup(os.unlink, path)
        return path

    def test_default_gateway(self):
        path = self.write(
            "Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\n"
            "wlan0\t0001A8C0\t00000000\t0001\t0\t0\t600\t00FFFFFF\n"
            "wlan0\t00000000\t0101A8C0\t0003\t0\t0\t600\t00000000\n")
        self.assertEqual(netscan.default_gateway(path), ("192.168.1.1", "wlan0"))
        self.assertEqual(netscan.default_gateway("/nonexistent"), (None, None))

    def test_arp(self):
        path = self.write(
            "IP address       HW type     Flags       HW address            Mask     Device\n"
            "192.168.1.1      0x1         0x2         AA:BB:CC:DD:EE:FF     *        wlan0\n"
            "192.168.1.9      0x1         0x0         00:00:00:00:00:00     *        wlan0\n")
        self.assertEqual(netscan.arp_neighbours(path), {"192.168.1.1": "aa:bb:cc:dd:ee:ff"})

    def test_mac_vendor(self):
        path = self.write("# comment\nB827EB Raspberry Pi Foundation\n0050C2ABC Tiny Maker\n")
        netscan._vendors = None
        try:
            self.assertEqual(netscan.mac_vendor("b8:27:eb:12:34:56", files=(path,)),
                             "Raspberry Pi Foundation")
            self.assertEqual(netscan.mac_vendor("00:50:c2:ab:cd:ef", files=(path,)),
                             "Tiny Maker")
            self.assertEqual(netscan.mac_vendor("da:a1:19:00:00:01", files=(path,)),
                             "Private (randomized MAC)")
            self.assertEqual(netscan.mac_vendor("00:11:22:33:44:55", files=(path,)), "")
        finally:
            netscan._vendors = None


class ScanTest(unittest.TestCase):
    """A whole scan through QProcess, with the fake nmap."""

    def setUp(self):
        from PyQt6.QtCore import QCoreApplication

        self.app = QCoreApplication.instance() or QCoreApplication([])
        self.bin = fake_nmap_dir()
        self.log = os.path.join(self.bin, "calls.log")
        self.env = {k: os.environ.get(k) for k in ("PATH", "FAKE_NMAP_LOG", "FAKE_NMAP_DELAY")}
        os.environ["PATH"] = self.bin + os.pathsep + os.environ.get("PATH", "")
        os.environ["FAKE_NMAP_LOG"] = self.log
        os.environ["FAKE_NMAP_DELAY"] = "0.01"
        self.saved = (netscan.own_addresses, netscan.default_gateway, netscan.arp_neighbours)
        netscan.own_addresses = lambda: {"10.99.0.23", "127.0.0.1"}
        netscan.default_gateway = lambda: ("10.99.0.1", "eth0")
        netscan.arp_neighbours = lambda: {"10.99.0.42": "b8:27:eb:00:00:42",
                                          "10.99.0.88": "da:a1:19:00:00:88",
                                          "172.16.0.1": "00:11:22:33:44:55"}

    def tearDown(self):
        netscan.own_addresses, netscan.default_gateway, netscan.arp_neighbours = self.saved
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        subprocess.run(["rm", "-rf", self.bin])

    def run_scan(self, versions=False):
        from PyQt6.QtCore import QEventLoop, QTimer

        scanner = netscan.NetworkScanner()
        found, updated, progress, done = [], [], [], []
        scanner.hostFound.connect(found.append)
        scanner.hostUpdated.connect(updated.append)
        scanner.progress.connect(lambda t, p: progress.append(p))
        loop = QEventLoop()
        scanner.finished.connect(lambda e: (done.append(e), loop.quit()))
        QTimer.singleShot(15000, loop.quit)
        scanner.start(ipaddress.ip_network("10.99.0.0/24"), versions)
        if not done:
            loop.exec()
        return scanner, found, updated, progress, done

    def test_scan(self):
        scanner, found, updated, progress, done = self.run_scan(versions=True)
        self.assertEqual(done, [""])
        ips = [h.ip for h in found]
        self.assertEqual(ips, ["10.99.0.1", "10.99.0.10", "10.99.0.23", "10.99.0.42",
                               "10.99.0.77", "10.99.0.88"])  # .88 only answered ARP
        hosts = scanner.hosts
        self.assertTrue(hosts["10.99.0.23"].is_self)
        self.assertTrue(hosts["10.99.0.1"].is_gateway)
        self.assertEqual(hosts["10.99.0.1"].kind, netscan.KIND_ROUTER)
        self.assertEqual(hosts["10.99.0.10"].kind, netscan.KIND_NAS)
        self.assertEqual(hosts["10.99.0.42"].kind, netscan.KIND_SERVER)
        self.assertEqual(hosts["10.99.0.42"].mac, "b8:27:eb:00:00:42")
        self.assertEqual(hosts["10.99.0.77"].kind, netscan.KIND_PRINTER)
        self.assertEqual(hosts["10.99.0.88"].seen_by, "arp")
        self.assertEqual(hosts["10.99.0.88"].kind, netscan.KIND_PHONE)
        self.assertNotIn("172.16.0.1", hosts)  # not on the scanned network
        self.assertTrue(all(h.ports_scanned for h in hosts.values()))
        self.assertEqual(hosts["10.99.0.42"].ports[0].label, "ssh OpenSSH 9.2")
        self.assertEqual(progress[-1], 100.0)
        nums = [p for p in progress if p >= 0]
        self.assertEqual(nums, sorted(nums), "progress only goes forward")
        with open(self.log) as f:
            calls = f.read().splitlines()
        self.assertEqual(len(calls), 2)
        self.assertIn("-sn", calls[0])
        self.assertTrue(calls[1].endswith("-- 10.99.0.1 10.99.0.10 10.99.0.23 10.99.0.42 "
                                          "10.99.0.77 10.99.0.88"), calls[1])
        self.assertIn("-sV", calls[1])

    def test_failure_is_reported(self):
        os.environ["FAKE_NMAP_FAIL"] = "bad things"
        try:
            scanner, found, _, _, done = self.run_scan()
        finally:
            del os.environ["FAKE_NMAP_FAIL"]
        self.assertEqual(found, [])
        self.assertEqual(done, ["nmap failed: QUITTING! bad things"])
        self.assertFalse(scanner.running)

    def test_missing_nmap(self):
        os.environ["PATH"] = "/nonexistent"
        _, found, _, _, done = self.run_scan()
        self.assertEqual(done, ["nmap is not installed."])


if __name__ == "__main__":
    unittest.main()
