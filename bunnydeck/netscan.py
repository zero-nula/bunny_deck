"""Network scan with nmap: finds the devices on a local network and their
open ports, for the network map (netmap.py).

Runs as the normal user (never root, never sudo), so nmap uses TCP connect
scans. Two phases:

1. host discovery (nmap -sn), hosts appear one by one;
2. a port scan of the found hosts (nmap -sT on PORTS), filling in services.

Hosts that ignore the discovery probes but answered ARP while it ran are
taken from the kernel's neighbour table (/proc/net/arp), so firewalled
devices on the same network still show up.
"""

import ipaddress
import re
import shutil
import socket
import struct
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from PyQt6.QtCore import QObject, QProcess, pyqtSignal

# Ports scanned in phase 2: the usual suspects in homes, offices and homelabs.
PORTS = (21, 22, 23, 25, 53, 80, 81, 110, 111, 135, 139, 143, 389, 443, 445, 548, 554,
         631, 873, 1883, 2049, 2222, 3000, 3306, 3389, 5000, 5001, 5432, 5900, 6379,
         8000, 8006, 8080, 8081, 8443, 8888, 9000, 9090, 9100, 9443, 10000, 27017, 32400)

SSH_PORTS = (22, 2222)
WEB_PORTS = {80: "http", 81: "http", 443: "https", 3000: "http", 5000: "http",
             5001: "https", 8000: "http", 8006: "https", 8080: "http", 8081: "http",
             8443: "https", 8888: "http", 9000: "http", 9090: "https", 9443: "https",
             10000: "https", 32400: "http"}

# Larger networks take too long with connect scans; the user can scan a part.
MAX_ADDRESSES = 4096

# Device kinds, guessed from the open ports (see guess_kind).
KIND_SELF = "self"
KIND_ROUTER = "router"
KIND_SERVER = "server"
KIND_HYPERVISOR = "hypervisor"
KIND_NAS = "nas"
KIND_PRINTER = "printer"
KIND_WINDOWS = "windows"
KIND_CAMERA = "camera"
KIND_MEDIA = "media"
KIND_IOT = "iot"
KIND_WEB = "web"
KIND_PHONE = "phone"
KIND_UNKNOWN = "unknown"

KIND_LABELS = {
    KIND_SELF: "This computer", KIND_ROUTER: "Router / gateway", KIND_SERVER: "SSH server",
    KIND_HYPERVISOR: "Hypervisor (Proxmox)", KIND_NAS: "NAS / file server",
    KIND_PRINTER: "Printer", KIND_WINDOWS: "Windows computer", KIND_CAMERA: "Camera",
    KIND_MEDIA: "Media server", KIND_IOT: "Smart-home device", KIND_WEB: "Web device",
    KIND_PHONE: "Phone or laptop (private MAC)", KIND_UNKNOWN: "Device",
}

MAC_PREFIX_FILES = ("/usr/share/nmap/nmap-mac-prefixes",
                    "/usr/local/share/nmap/nmap-mac-prefixes")


@dataclass
class Port:
    number: int
    service: str = ""
    product: str = ""

    @property
    def label(self):
        return " ".join(p for p in (self.service, self.product) if p)


@dataclass
class Host:
    ip: str
    hostname: str = ""
    mac: str = ""
    vendor: str = ""
    ports: list = field(default_factory=list)  # [Port], sorted
    ports_scanned: bool = False
    is_self: bool = False
    is_gateway: bool = False
    seen_by: str = "nmap"  # or "arp": answered only on the local network

    @property
    def sort_key(self):
        return int(ipaddress.ip_address(self.ip))

    @property
    def name(self):
        """Short display name: the hostname without the local domain, or the IP."""
        if self.hostname:
            short = self.hostname.split(".")[0]
            return short or self.hostname
        return self.ip

    @property
    def port_numbers(self):
        return [p.number for p in self.ports]

    @property
    def ssh_port(self):
        """The SSH port to connect to, or None."""
        for p in self.ports:
            if p.number in SSH_PORTS or p.service == "ssh":
                return p.number
        return None

    @property
    def web_urls(self):
        urls = []
        for p in self.ports:
            scheme = WEB_PORTS.get(p.number)
            if p.service in ("http", "https"):
                scheme = p.service
            elif "https" in p.service or p.service.startswith("ssl/http"):
                scheme = "https"
            if scheme:
                default = 80 if scheme == "http" else 443
                port = "" if p.number == default else f":{p.number}"
                urls.append(f"{scheme}://{self.ip}{port}/")
        return urls

    @property
    def kind(self):
        return guess_kind(self)

    def merge(self, other):
        """Takes over what other knows that this host doesn't yet."""
        self.hostname = self.hostname or other.hostname
        self.mac = self.mac or other.mac
        self.vendor = self.vendor or other.vendor
        if other.ports_scanned:
            self.ports = other.ports
            self.ports_scanned = True


def guess_kind(host):
    if host.is_self:
        return KIND_SELF
    if host.is_gateway:
        return KIND_ROUTER
    ports = set(host.port_numbers)
    vendor = host.vendor.lower()
    if 8006 in ports:
        return KIND_HYPERVISOR
    if ports & {9100, 631} and not ports & set(SSH_PORTS):
        return KIND_PRINTER
    if 554 in ports:
        return KIND_CAMERA
    if 32400 in ports:
        return KIND_MEDIA
    if ports & {5000, 5001, 548, 2049} or any(v in vendor for v in ("synology", "qnap")):
        return KIND_NAS
    if ports & set(SSH_PORTS):
        return KIND_SERVER
    if ports & {3389, 135}:
        return KIND_WINDOWS
    if 445 in ports or 139 in ports:
        return KIND_NAS
    if 1883 in ports or any(v in vendor for v in ("espressif", "shelly", "tuya", "sonoff")):
        return KIND_IOT
    if ports & set(WEB_PORTS):
        return KIND_WEB
    if host.mac and is_random_mac(host.mac):
        return KIND_PHONE
    return KIND_UNKNOWN


def is_random_mac(mac):
    """Locally administered MAC: phones and laptops use these for privacy."""
    try:
        return bool(int(mac.split(":")[0], 16) & 0x02)
    except (ValueError, IndexError):
        return False


# ----- nmap ---------------------------------------------------------------


def nmap_path():
    return shutil.which("nmap")


def install_hint():
    """The command that installs nmap on this distribution."""
    ids = ""
    try:
        with open("/etc/os-release") as f:
            for line in f:
                key, _, value = line.strip().partition("=")
                if key in ("ID", "ID_LIKE"):
                    ids += " " + value.strip('"')
    except OSError:
        pass
    words = ids.split()
    if any(w.startswith("opensuse") or w == "suse" for w in words):
        return "sudo zypper install nmap"
    if "fedora" in words or "rhel" in words:
        return "sudo dnf install nmap"
    if "arch" in words:
        return "sudo pacman -S nmap"
    if "debian" in words or "ubuntu" in words:
        return "sudo apt install nmap"
    return "install the “nmap” package"


def parse_target(text):
    """The network (or single address) to scan, as an ip_network, from user
    input like 192.168.1.0/24 or 10.0.0.7. Raises ValueError with a message
    for the user."""
    text = text.strip()
    if not text:
        raise ValueError("Enter a network, e.g. 192.168.1.0/24.")
    try:
        net = ipaddress.ip_network(text, strict=False)
    except ValueError:
        raise ValueError(f"“{text}” is not a network address like 192.168.1.0/24.") from None
    if net.version != 4:
        raise ValueError("Only IPv4 networks can be scanned.")
    if net.num_addresses > MAX_ADDRESSES:
        raise ValueError(f"{net} has {net.num_addresses:,} addresses; scan a /20 or "
                         "smaller part of it.")
    return net


def discovery_argv(nmap, network):
    return [nmap, "-sn", "-T4", "--stats-every", "1s", "-oX", "-", "--", str(network)]


def port_scan_argv(nmap, ips, versions=False):
    argv = [nmap, "-sT", "-Pn", "-n", "-T4", "--open",
            "-p", ",".join(str(p) for p in PORTS)]
    if versions:
        argv += ["-sV", "--version-light"]
    return argv + ["--stats-every", "1s", "-oX", "-", "--"] + list(ips)


class XMLStream:
    """Picks complete <host> and <taskprogress> elements out of nmap's XML
    output as it arrives, so hosts can be shown before nmap finishes."""

    _ELEMENT = re.compile(r"<host[\s>].*?</host>|<taskprogress\b[^>]*/>", re.S)

    def __init__(self):
        self.buffer = ""

    def feed(self, text):
        """Returns [("host", Host) | ("progress", percent)] for what is complete."""
        self.buffer += text
        out = []
        end = 0
        for m in self._ELEMENT.finditer(self.buffer):
            end = m.end()
            chunk = m.group(0)
            try:
                el = ET.fromstring(chunk)
            except ET.ParseError:
                continue
            if el.tag == "taskprogress":
                try:
                    out.append(("progress", float(el.get("percent", "0"))))
                except ValueError:
                    pass
            else:
                host = parse_host(el)
                if host is not None:
                    out.append(("host", host))
        self.buffer = self.buffer[end:]
        return out


def parse_host(el):
    """A Host from an nmap <host> element, or None if it is not up."""
    status = el.find("status")
    if status is not None and status.get("state") != "up":
        return None
    host = None
    mac = vendor = ""
    for a in el.findall("address"):
        if a.get("addrtype") == "ipv4":
            host = a.get("addr")
        elif a.get("addrtype") == "mac":
            mac = (a.get("addr") or "").lower()
            vendor = a.get("vendor") or ""
    if not host:
        return None
    names = [h.get("name") for h in el.findall("hostnames/hostname") if h.get("name")]
    ports = []
    ports_el = el.find("ports")
    for p in el.findall("ports/port"):
        state = p.find("state")
        if state is None or state.get("state") != "open":
            continue
        svc = p.find("service")
        product = ""
        name = ""
        if svc is not None:
            name = svc.get("name") or ""
            if svc.get("tunnel") == "ssl" and name.startswith("http"):
                name = "https"
            product = " ".join(x for x in (svc.get("product"), svc.get("version")) if x)
        try:
            ports.append(Port(int(p.get("portid")), name, product))
        except (TypeError, ValueError):
            pass
    ports.sort(key=lambda p: p.number)
    return Host(ip=host, hostname=names[0] if names else "", mac=mac, vendor=vendor,
                ports=ports, ports_scanned=ports_el is not None)


# ----- this computer's networks -------------------------------------------


@dataclass
class LocalNetwork:
    network: ipaddress.IPv4Network
    address: str  # this computer's address in it
    interface: str

    @property
    def label(self):
        return f"{self.network}  ({self.interface}, this computer {self.address})"


def default_gateway(route_file="/proc/net/route"):
    """(gateway IP, interface) of the default IPv4 route, or (None, None)."""
    try:
        with open(route_file) as f:
            next(f, None)
            for line in f:
                cols = line.split()
                if len(cols) < 4 or cols[1] != "00000000":
                    continue
                if not int(cols[3], 16) & 0x2:  # RTF_GATEWAY
                    continue
                gw = socket.inet_ntoa(struct.pack("<L", int(cols[2], 16)))
                return gw, cols[0]
    except (OSError, ValueError):
        pass
    return None, None


def arp_neighbours(arp_file="/proc/net/arp"):
    """{ip: mac} of the complete entries in the kernel's IPv4 neighbour table."""
    out = {}
    try:
        with open(arp_file) as f:
            next(f, None)
            for line in f:
                cols = line.split()
                if len(cols) < 4:
                    continue
                ip, flags, mac = cols[0], cols[2], cols[3].lower()
                try:
                    complete = int(flags, 16) & 0x2
                except ValueError:
                    continue
                if complete and mac != "00:00:00:00:00:00":
                    out[ip] = mac
    except OSError:
        pass
    return out


_VIRTUAL = ("docker", "br-", "veth", "virbr", "podman", "cni", "flannel", "tun", "tap", "wg",
            "tailscale", "zt")


def local_networks():
    """This computer's IPv4 networks, the one with the default route first and
    virtual (container/VM/VPN) networks last. A network larger than
    MAX_ADDRESSES is narrowed to the /24 around this computer's address."""
    from PyQt6.QtNetwork import QAbstractSocket, QNetworkInterface

    _, gw_iface = default_gateway()
    nets = []
    flags = QNetworkInterface.InterfaceFlag
    for iface in QNetworkInterface.allInterfaces():
        f = iface.flags()
        if not (f & flags.IsUp and f & flags.IsRunning) or f & flags.IsLoopBack:
            continue
        for entry in iface.addressEntries():
            ip = entry.ip()
            if ip.protocol() != QAbstractSocket.NetworkLayerProtocol.IPv4Protocol:
                continue
            prefix = entry.prefixLength()
            if prefix < 0 or prefix > 30:
                continue
            addr = ip.toString()
            net = ipaddress.ip_network(f"{addr}/{prefix}", strict=False)
            if net.num_addresses > MAX_ADDRESSES:
                net = ipaddress.ip_network(f"{addr}/24", strict=False)
            nets.append(LocalNetwork(net, addr, iface.name()))

    def rank(n):
        name = n.interface
        return (name != gw_iface, name.startswith(_VIRTUAL), name)

    return sorted(nets, key=rank)


def own_addresses():
    from PyQt6.QtNetwork import QAbstractSocket, QNetworkInterface

    return {a.toString() for a in QNetworkInterface.allAddresses()
            if a.protocol() == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol}


_vendors = None


def mac_vendor(mac, files=MAC_PREFIX_FILES):
    """The manufacturer for a MAC address, from nmap's prefix list."""
    global _vendors
    if not mac:
        return ""
    if is_random_mac(mac):
        return "Private (randomized MAC)"
    if _vendors is None:
        _vendors = {}
        for path in files:
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        if line.startswith("#"):
                            continue
                        prefix, _, name = line.strip().partition(" ")
                        if prefix:
                            _vendors[prefix.upper()] = name
                break
            except OSError:
                continue
    hexmac = mac.replace(":", "").replace("-", "").upper()
    # The list has 24-bit prefixes and some longer ones (28, 36 bit).
    for n in (9, 7, 6):
        name = _vendors.get(hexmac[:n])
        if name:
            return name
    return ""


# ----- the scan -----------------------------------------------------------


class NetworkScanner(QObject):
    """Runs the two nmap phases for one network.

    hostFound(Host) for each new host; hostUpdated(Host) when more is known
    (ports, MAC); progress(text, percent 0-100, or -1 to keep the last);
    finished(error message, or "" on success)."""

    hostFound = pyqtSignal(object)
    hostUpdated = pyqtSignal(object)
    progress = pyqtSignal(str, float)
    finished = pyqtSignal(str)

    DISCOVERY_SHARE = 30.0  # % of the progress bar for phase 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hosts = {}  # ip -> Host
        self.network = None
        self.versions = False
        self.phase = None  # "discover", "ports" or None
        self.proc = None
        self._stream = None
        self._stderr = ""
        self._stopped = False
        self.self_ips = set()
        self.gateway = None

    @property
    def running(self):
        return self.phase is not None

    def start(self, network, versions=False):
        nmap = nmap_path()
        if nmap is None:
            self.finished.emit("nmap is not installed.")
            return False
        self.stop()
        self.hosts = {}
        self.network = network
        self.versions = versions
        self._stopped = False
        self.self_ips = {ip for ip in own_addresses()
                         if ipaddress.ip_address(ip) in network}
        gw, _ = default_gateway()
        self.gateway = gw if gw and ipaddress.ip_address(gw) in network else None
        self.phase = "discover"
        self.progress.emit(f"Looking for devices on {network}…", 0.0)
        self._run(discovery_argv(nmap, network))
        return True

    def stop(self):
        if self.proc is not None:
            self._stopped = True
            proc, self.proc = self.proc, None
            proc.finished.disconnect()
            proc.kill()
            proc.waitForFinished(2000)
            proc.deleteLater()
            self.phase = None
            self.finished.emit("Stopped.")

    def _run(self, argv):
        self._stream = XMLStream()
        self._stderr = ""
        proc = self.proc = QProcess(self)
        proc.readyReadStandardOutput.connect(self._on_stdout)
        proc.readyReadStandardError.connect(self._on_stderr)
        proc.finished.connect(self._on_finished)
        proc.errorOccurred.connect(self._on_error)
        proc.start(argv[0], argv[1:])

    def _on_stdout(self):
        if self.proc is None:
            return
        text = bytes(self.proc.readAllStandardOutput()).decode("utf-8", "replace")
        for kind, value in self._stream.feed(text):
            if kind == "progress":
                self.progress.emit(self._status_text(), self._overall(value))
            else:
                self._on_host(value)

    def _on_stderr(self):
        if self.proc is not None:
            self._stderr += bytes(self.proc.readAllStandardError()).decode("utf-8", "replace")

    def _status_text(self):
        if self.phase == "discover":
            return f"Looking for devices on {self.network}… {len(self.hosts)} found"
        return f"Checking open ports on {len(self.hosts)} devices…"

    def _overall(self, percent):
        share = self.DISCOVERY_SHARE
        if self.phase == "discover":
            return percent * share / 100
        return share + percent * (100 - share) / 100

    def _on_host(self, host):
        host.is_self = host.ip in self.self_ips
        host.is_gateway = host.ip == self.gateway
        if host.mac and not host.vendor:
            host.vendor = mac_vendor(host.mac)
        known = self.hosts.get(host.ip)
        if known is None:
            self.hosts[host.ip] = host
            self.hostFound.emit(host)
            self.progress.emit(self._status_text(), -1.0)
        else:
            known.merge(host)
            self.hostUpdated.emit(known)

    def _on_error(self, error):
        if error == QProcess.ProcessError.FailedToStart and self.proc is not None:
            self.proc.deleteLater()
            self.proc = None
            self.phase = None
            self.finished.emit("nmap could not be started.")

    def _on_finished(self, code, status):
        if self.proc is None:
            return
        self._on_stdout()
        self.proc.deleteLater()
        self.proc = None
        if status != QProcess.ExitStatus.NormalExit or code != 0:
            self.phase = None
            msg = self._stderr.strip().splitlines()
            self.finished.emit("nmap failed: " + (msg[-1] if msg else f"exit code {code}"))
            return
        if self.phase == "discover":
            self._add_arp_neighbours()
            ips = sorted(self.hosts, key=lambda ip: self.hosts[ip].sort_key)
            if not ips:
                self.phase = None
                self.progress.emit("No devices found.", 100.0)
                self.finished.emit("")
                return
            self.phase = "ports"
            self.progress.emit(self._status_text(), self.DISCOVERY_SHARE)
            self._run(port_scan_argv(nmap_path() or "nmap", ips, self.versions))
            return
        self.phase = None
        for host in self.hosts.values():
            if not host.ports_scanned:
                host.ports_scanned = True
                self.hostUpdated.emit(host)
        n = len(self.hosts)
        self.progress.emit(f"Done: {n} device{'s' if n != 1 else ''} on {self.network}.", 100.0)
        self.finished.emit("")

    def _add_arp_neighbours(self):
        """MACs for the hosts nmap found, and the hosts it missed that
        answered ARP on this network meanwhile."""
        for ip, mac in arp_neighbours().items():
            try:
                if ipaddress.ip_address(ip) not in self.network:
                    continue
            except ValueError:
                continue
            host = self.hosts.get(ip)
            if host is not None:
                if not host.mac:
                    host.mac = mac
                    host.vendor = mac_vendor(mac)
                    self.hostUpdated.emit(host)
            else:
                self._on_host(Host(ip=ip, mac=mac, seen_by="arp"))

