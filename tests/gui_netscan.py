"""GUI test: the Network Scan window. Scans the test network with a fake
nmap (no packets are sent), checks the map, the device list, the details
and connecting to a found SSH server."""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gui_harness import check, run  # noqa: E402
from test_netscan import fake_nmap_dir  # noqa: E402

from bunnydeck import netscan  # noqa: E402

BIN = fake_nmap_dir()
os.environ["PATH"] = BIN + os.pathsep + os.environ.get("PATH", "")
os.environ["FAKE_NMAP_DELAY"] = "0.05"
netscan.own_addresses = lambda: {"10.99.0.23"}
netscan.default_gateway = lambda: ("10.99.0.1", "eth0")
netscan.arp_neighbours = lambda: {"10.99.0.88": "da:a1:19:00:00:88"}
SHOT = os.environ.get("NETSCAN_SCREENSHOT")


def test(app):
    win = app.window
    check(win.scan_btn.text() == "Scan Network", "toolbar has a Scan Network button")
    win.run_shortcut("network-scan")
    w = win.network_scan
    yield lambda: w is not None and w.isVisible()
    check(w.isVisible(), "Ctrl+Shift+M opens the Network Scan window")
    check(w.scan_btn.isEnabled() and not w.missing.isVisible(), "nmap is found")

    # A bad target is refused without running nmap.
    check(not w.start_scan("10.0.0.0/8"), "a /8 is refused")
    check("scan a /20" in w.status.text(), f"…with a reason ({w.status.text()})")
    check(not w.start_scan("--script=x"), "an option is refused as target")

    w.target.setEditText("10.99.0.0/24")
    w.scan_btn.click()
    check(w.scan_btn.text() == "Stop", "Scan turns into Stop while scanning")
    check(w.map.sweeping, "the radar sweeps")
    check(set(w.map.nodes) == {"10.99.0.1", "10.99.0.23"},
          "this computer and the router are shown at once")
    yield lambda: not w.scanner.running
    check(w.scan_btn.text() == "Scan", "back to Scan when done")
    check(not w.map.sweeping, "the sweep stops")
    check(w.progress.value() == 1000, "progress is full")
    check("Done: 6 devices" in w.status.text(), f"status says done ({w.status.text()})")
    check(len(w.map.nodes) == 6 and w.list.count() == 6, "six devices on map and in the list")
    check(w.count.text() == "Devices (6)", "count heading")
    order = [w.list.item(i).data(0x0100) for i in range(w.list.count())]
    check(order == ["10.99.0.1", "10.99.0.10", "10.99.0.23", "10.99.0.42", "10.99.0.77",
                    "10.99.0.88"], f"list in IP order ({order})")

    yield 0.8  # let the move animations finish
    hub = w.map.hub
    check(hub is not None and hub.host.ip == "10.99.0.1", "the router is the hub")
    check(hub.pos().manhattanLength() < 1, "…in the middle")
    dists = [math.hypot(n.pos().x(), n.pos().y()) for n in w.map.nodes.values() if n is not hub]
    check(all(abs(d - 200) < 2 for d in dists), f"the others on the first ring ({dists})")
    check(w.map.nodes["10.99.0.23"].host.is_self, "this computer is marked")

    # Selecting in the list selects on the map and shows details.
    w.list.setCurrentItem(w.items["10.99.0.42"])
    check(w.map.selected_host().ip == "10.99.0.42", "list selection selects the map node")
    text = w.details.text()
    check("OpenSSH 9.2" in text and "1883" in text and "SSH server" in text,
          "details show the ports and the kind")
    check(w.ssh_btn.isEnabled() and "port 22" in w.ssh_btn.text(), "SSH button names the port")
    check(not w.web_btn.isEnabled(), "no browser button without a web port")

    # Map selection selects in the list.
    w.map.scene_.clearSelection()
    w.map.nodes["10.99.0.77"].setSelected(True)
    check(w.current_host().ip == "10.99.0.77", "map selection selects the list row")
    check("Printer" in w.details.text(), "the printer is recognised")
    w.list.setCurrentItem(w.items["10.99.0.10"])
    check(w.web_btn.isEnabled(), "the NAS has a web interface")
    check(w.current_host().web_urls == ["https://10.99.0.10:5001/"], "…on port 5001")
    w.list.setCurrentItem(w.items["10.99.0.88"])
    check("answered on the" in w.details.text(), "an ARP-only device is explained")
    w.list.setCurrentItem(w.items["10.99.0.23"])
    check(not w.ssh_btn.isEnabled(), "no SSH to yourself")

    if SHOT:
        w.list.setCurrentItem(w.items["10.99.0.42"])
        yield 0.3
        w.grab().save(SHOT)
        print("  screenshot:", SHOT)

    # Connecting goes through the Add SSH dialog, filled in.
    calls = []
    win.add_ssh = lambda *a: calls.append(a)
    w.list.setCurrentItem(w.items["10.99.0.42"])
    w.ssh_btn.click()
    check(calls == [("10.99.0.42", 22, "pi")], f"Connect asks to add pi ({calls})")
    w.map.nodes["10.99.0.10"].activated.emit(w.map.nodes["10.99.0.10"].host)
    check(calls[-1] == ("10.99.0.10", 22, "nas"), "double-clicking a node connects")
    del win.add_ssh

    from bunnydeck.dialogs import AddSSHDialog
    dlg = AddSSHDialog(win)
    dlg.prefill("10.99.0.42", 2222, "pi")
    check((dlg.host.text(), dlg.port.value(), dlg.name.text()) == ("10.99.0.42", 2222, "pi"),
          "the Add SSH dialog is filled in")
    dlg.deleteLater()

    # Stop in the middle.
    os.environ["FAKE_NMAP_DELAY"] = "0.4"
    w.start_scan("10.99.0.0/24")
    yield 0.6
    w.scan_btn.click()
    check(not w.scanner.running and w.scan_btn.text() == "Scan", "Stop stops the scan")
    check(w.status.text() == "Stopped.", f"status says stopped ({w.status.text()})")

    # Without nmap.
    path = os.environ["PATH"]
    os.environ["PATH"] = "/nonexistent"
    w.check_nmap()
    check(w.missing.isVisible() and not w.scan_btn.isEnabled(),
          "without nmap: install hint, Scan disabled")
    check("install" in w.missing_text.text(), "the hint names the install command")
    os.environ["PATH"] = path
    w.recheck.click()
    check(not w.missing.isVisible() and w.scan_btn.isEnabled(), "Check again finds it")

    # Closing the window while scanning stops nmap.
    w.start_scan("10.99.0.0/24")
    w.close()
    check(not w.scanner.running, "closing the window stops the scan")
    win.run_shortcut("network-scan")
    check(win.network_scan is w and w.isVisible(), "the window opens again")
    w.close()


sys.exit(run(test))
