"""The ufw firewall of an SSH server, without a GUI: what the window may read
without root, the commands it runs, checking every input, and reading ufw's
answers.

Without root, a user can see whether ufw is installed (/usr/sbin/ufw), whether
it is enabled (ENABLED= in /etc/ufw/ufw.conf, mode 0644) and the server's
network (`ip`). INFO_SCRIPT reads these over the shared connection
(remote.py). The rules themselves (`ufw status`, /etc/ufw/user*.rules, mode
0640) need root. So every sudo command, the status included, is typed into
the server's "Firewall" terminal, where the user enters the sudo password.
Bunny Deck never sees or stores it. The status is read back from that
terminal's output, between two markers (STATUS_BEGIN, STATUS_END).

Every value that goes into a command is checked here first, and quoted with
shlex.quote as well.
"""

import ipaddress
import re
import shlex
from dataclasses import dataclass, field

UFW_CONF = "/etc/ufw/ufw.conf"
# The Firewall terminal: an interactive login shell over the shared connection.
SHELL_COMMAND = 'exec "${SHELL:-/bin/sh}" -l'

PROTOCOLS = ("tcp", "udp", "both")
COMMENT_MAX = 64
_COMMENT = re.compile(r"^[A-Za-z0-9 ._:/@()+,-]*$")
_PORT = re.compile(r"^(\d{1,5})(?::(\d{1,5}))?$")

# Printed around the status. The command line typed into the terminal holds
# "@@%s" and the word apart, so only the output contains the whole marker.
STATUS_BEGIN = "@@ufw-status"
STATUS_END = "@@ufw-done"
STATUS_COMMAND = ("printf '@@%s\\n' ufw-status; sudo ufw status numbered; "
                  "printf '@@%s\\n' ufw-done")


def info_script(conf=None):
    return f"""
LC_ALL=C; export LC_ALL
echo @@ufw
if [ -x /usr/sbin/ufw ] || command -v ufw >/dev/null 2>&1; then echo installed; else echo missing; fi
echo @@enabled
grep -E "^ENABLED=" {shlex.quote(conf or UFW_CONF)} 2>/dev/null
echo @@route
ip -o -4 route show default 2>/dev/null
echo @@addr
ip -o -4 addr show 2>/dev/null
echo @@end
"""


# ----- checking input -------------------------------------------------------------


def parse_port(text):
    """"8080" or "8000:8010" (also "8000-8010") → (ufw port spec, is a range).
    Raises ValueError with a message for the user."""
    text = (text or "").strip().replace("-", ":")
    m = _PORT.match(text)
    if m is None:
        raise ValueError("Enter a port (8080) or a range (8000:8010).")
    first = int(m.group(1))
    last = int(m.group(2)) if m.group(2) else None
    for n in (first, last):
        if n is not None and not 1 <= n <= 65535:
            raise ValueError("Ports go from 1 to 65535.")
    if last is None:
        return str(first), False
    if last <= first:
        raise ValueError("A range goes from the lower port to the higher one.")
    return f"{first}:{last}", True


def parse_source(text):
    """An IP address or network (IPv4 or IPv6) in ufw's form; a network is
    reduced to its start (192.168.1.7/24 → 192.168.1.0/24). Raises ValueError."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Enter an IP address or a network such as 192.168.1.0/24.")
    try:
        if "/" in text:
            net = ipaddress.ip_network(text, strict=False)
            return str(net.network_address) if net.prefixlen == net.max_prefixlen else str(net)
        return str(ipaddress.ip_address(text))
    except ValueError:
        raise ValueError(f"“{text}” is not an IP address or network.") from None


def parse_comment(text):
    text = (text or "").strip()
    if len(text) > COMMENT_MAX:
        raise ValueError(f"A comment can be at most {COMMENT_MAX} characters.")
    if not _COMMENT.match(text):
        raise ValueError("A comment may use letters, digits, spaces and . _ : / @ ( ) + , -")
    return text


# ----- commands ---------------------------------------------------------------


def allow_commands(port, protocol="tcp", source=None, comment=""):
    """The sudo ufw commands that allow port from source (None: anywhere).
    Checks every value (ValueError). A port range needs a protocol in ufw,
    so "both" with a range gives one command per protocol."""
    spec, is_range = parse_port(port)
    if protocol not in PROTOCOLS:
        raise ValueError("Choose TCP, UDP or both.")
    src = "any" if source is None else parse_source(source)
    comment = parse_comment(comment)
    protos = ["tcp", "udp"] if protocol == "both" and is_range else \
        [None] if protocol == "both" else [protocol]
    out = []
    for proto in protos:
        words = ["sudo", "ufw", "allow", "from", shlex.quote(src), "to", "any", "port",
                 shlex.quote(spec)]
        if proto:
            words += ["proto", proto]
        if comment:
            words += ["comment", shlex.quote(comment)]
        out.append(" ".join(words))
    return out


def delete_command(number):
    """ufw shows the rule and asks (y|n) in the terminal: nothing is deleted
    by a number that has moved meanwhile without the user seeing it."""
    n = int(number)
    if n < 1:
        raise ValueError("No such rule.")
    return f"sudo ufw delete {n}"


def with_status(command):
    """A command followed by the status, as typed into the terminal."""
    return f"{command}; {STATUS_COMMAND}"


# ----- reading answers ----------------------------------------------------------


@dataclass
class Info:
    installed: bool = False
    enabled: object = None  # True / False / None (cannot tell)
    lan: str = ""  # the server's network, e.g. 192.168.1.0/24


def lan_from(route_lines, addr_lines):
    """The network of the interface with the default route ("" if unknown);
    else the first private IPv4 network."""
    dev = ""
    for line in route_lines:
        m = re.search(r"\bdev\s+(\S+)", line)
        if m:
            dev = m.group(1)
            break
    nets = []
    for line in addr_lines:
        m = re.search(r"^\d+:\s+(\S+)\s+inet\s+([\d.]+/\d+)", line.strip())
        if not m:
            continue
        try:
            iface = ipaddress.ip_interface(m.group(2))
        except ValueError:
            continue
        if iface.ip.is_loopback:
            continue
        nets.append((m.group(1), iface.network))
    for name, net in nets:
        if name == dev:
            return str(net)
    for _name, net in nets:
        if net.is_private:
            return str(net)
    return ""


def parse_info(text):
    """An Info from INFO_SCRIPT's output, or None if it did not run to the end."""
    sections, current = {}, None
    for line in text.splitlines():
        if line.startswith("@@"):
            current = line[2:].strip()
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
    if "end" not in sections:
        return None
    installed = "installed" in [s.strip() for s in sections.get("ufw", [])]
    enabled = None
    for line in sections.get("enabled", []):
        value = line.partition("=")[2].strip().strip("\"'").lower()
        enabled = value == "yes"
    return Info(installed=installed, enabled=enabled,
                lan=lan_from(sections.get("route", []), sections.get("addr", [])))


@dataclass
class Rule:
    number: int
    to: str
    action: str  # ALLOW IN, DENY IN, LIMIT IN, ALLOW OUT, …
    source: str
    comment: str = ""
    v6: bool = False


@dataclass
class Status:
    active: object = None  # True / False / None (no status: an error)
    rules: list = field(default_factory=list)
    error: str = ""


_ESCAPES = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b.")
_RULE = re.compile(r"^\[\s*(\d+)\]\s+(.+?)\s{2,}((?:ALLOW|DENY|REJECT|LIMIT)(?:\s+(?:IN|OUT|FWD))?)"
                   r"\s+(.+?)(?:\s+#\s?(.*))?$")


def clean(text):
    """Terminal output as plain lines: no colour codes, no carriage returns."""
    return _ESCAPES.sub("", text).replace("\r\n", "\n").replace("\r", "\n")


def parse_status(text):
    """A Status from `ufw status numbered` (and whatever sudo printed)."""
    st = Status()
    errors = []
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("Status:"):
            st.active = line.split(":", 1)[1].strip().lower() == "active"
            continue
        m = _RULE.match(line.strip())
        if m:
            to = m.group(2).strip()
            source = m.group(4).strip()
            v6 = "(v6)" in to or "(v6)" in source
            st.rules.append(Rule(int(m.group(1)), to, " ".join(m.group(3).split()), source,
                                 (m.group(5) or "").strip(), v6))
            continue
        low = line.lower()
        if ("sudo:" in low or low.startswith("error") or "sorry, try again" in low
                or "command not found" in low or "not allowed" in low):
            errors.append(line.strip())
    if st.active is None and errors:
        st.error = errors[-1]
    elif st.active is None and not st.rules:
        st.error = "No answer from ufw."
    return st


class StatusReader:
    """Collects a terminal's output and yields the status between the
    markers each time a complete one has arrived."""

    def __init__(self):
        self._buf = ""

    def feed(self, text):
        """Returns a Status when one is complete, else None."""
        self._buf = (self._buf + text)[-200000:]
        plain = clean(self._buf)
        end = plain.rfind("\n" + STATUS_END)
        if end < 0:
            return None
        begin = plain.rfind(STATUS_BEGIN + "\n", 0, end)
        if begin < 0:
            return None
        body = plain[begin + len(STATUS_BEGIN) + 1:end]
        self._buf = ""
        return parse_status(body)
