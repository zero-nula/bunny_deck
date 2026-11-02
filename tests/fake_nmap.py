#!/usr/bin/env python3
"""A stand-in for nmap in the tests: prints canned XML for the test network
10.99.0.0/24 (a bit at a time, like the real one), never sends a packet.
FAKE_NMAP_LOG, if set, gets one line per call with the arguments."""

import os
import sys
import time

args = sys.argv[1:]
log = os.environ.get("FAKE_NMAP_LOG")
if log:
    with open(log, "a") as f:
        f.write(" ".join(args) + "\n")
if os.environ.get("FAKE_NMAP_FAIL"):
    print("QUITTING! " + os.environ["FAKE_NMAP_FAIL"], file=sys.stderr)
    sys.exit(1)

DELAY = float(os.environ.get("FAKE_NMAP_DELAY", "0.15"))

NAMES = {"10.99.0.1": "router.lan", "10.99.0.10": "nas.lan", "10.99.0.23": "deck.lan",
         "10.99.0.42": "pi.lan"}
PORTS = {
    "10.99.0.1": [(53, "domain", ""), (80, "http", ""), (443, "https", "")],
    "10.99.0.10": [(22, "ssh", "OpenSSH 9.6"), (445, "microsoft-ds", ""), (5001, "https", "")],
    "10.99.0.23": [(22, "ssh", "OpenSSH 10.0")],
    "10.99.0.42": [(22, "ssh", "OpenSSH 9.2"), (1883, "mqtt", "")],
    "10.99.0.77": [(631, "ipp", "CUPS 2.4"), (9100, "jetdirect", "")],
}
UP = ["10.99.0.1", "10.99.0.10", "10.99.0.23", "10.99.0.42", "10.99.0.77"]


def out(text):
    sys.stdout.write(text)
    sys.stdout.flush()


def host_xml(ip, ports=None):
    name = NAMES.get(ip)
    names = f'<hostnames><hostname name="{name}" type="PTR"/></hostnames>' if name else \
        "<hostnames/>"
    x = (f'<host><status state="up" reason="conn-refused" reason_ttl="0"/>\n'
         f'<address addr="{ip}" addrtype="ipv4"/>\n{names}\n')
    if ports is not None:
        x += '<ports><extraports state="closed" count="40"/>\n'
        for num, svc, product in ports:
            prod, _, ver = product.partition(" ")
            extra = f' product="{prod}" version="{ver}"' if product else ""
            x += (f'<port protocol="tcp" portid="{num}"><state state="open" reason="syn-ack"/>'
                  f'<service name="{svc}"{extra} method="table" conf="3"/></port>\n')
        x += "</ports>\n"
    return x + "</host>\n"


out('<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE nmaprun>\n'
    f'<nmaprun scanner="nmap" args="nmap {" ".join(args)}" version="7.95">\n')
targets = args[args.index("--") + 1:] if "--" in args else args[-1:]
if "-sn" in args:
    for i, ip in enumerate(UP):
        time.sleep(DELAY)
        out(f'<taskprogress task="Ping Scan" percent="{(i + 1) * 20:.2f}" remaining="1"/>\n')
        # A <hosthint> must not be taken for a host.
        out(f'<hosthint><status state="up"/><address addr="{ip}" addrtype="ipv4"/></hosthint>\n')
        out(host_xml(ip))
else:
    for i, ip in enumerate(targets):
        time.sleep(DELAY)
        out(f'<taskprogress task="Connect Scan" percent="{(i + 1) * 100 / len(targets):.2f}"/>\n')
        out(host_xml(ip, PORTS.get(ip, [])))
out('<runstats><finished exit="success"/></runstats>\n</nmaprun>\n')
