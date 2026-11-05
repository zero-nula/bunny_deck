"""Server stats: the popup behind the chart icon in a terminal header.

While the popup is open, one small POSIX sh script runs on the server (over
the terminal's shared SSH connection, see sshmux.py) or, for a local
terminal, on this computer. It prints a block of /proc readings every
INTERVAL_S seconds; the popup parses them as they arrive. Closing the popup
ends the script, so nothing keeps running on the server. Being one command
per opening, it shows up in the server's auth log once, not every refresh.

Works on Linux servers (it reads /proc). The script only reads; it changes
nothing on the server.
"""

import shlex

from PyQt6.QtCore import QPointF, QProcess, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import (
    QFrame, QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget,
)

from . import sshmux, theme

SSH_BINARY = "/usr/bin/ssh"
INTERVAL_S = 2
MAX_DISKS = 5
MAX_PROCESSES = 5
NAME_WIDTH = 110  # the label column, the same in every section so bars line up

# No single quotes in here: it is passed to the remote shell in '...'.
SCRIPT = f"""
LC_ALL=C; export LC_ALL
echo @@info
hostname 2>/dev/null || cat /proc/sys/kernel/hostname
(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME") || echo
uname -sr
nproc 2>/dev/null || grep -c ^processor /proc/cpuinfo
echo @@end
while :; do
  echo @@cpu; head -n 1 /proc/stat
  echo @@mem; grep -E "^(MemTotal|MemAvailable|SwapTotal|SwapFree):" /proc/meminfo
  echo @@load; cat /proc/loadavg
  echo @@uptime; cat /proc/uptime
  echo @@df; df -P -k 2>/dev/null
  echo @@ps; ps -eo rss=,comm= --sort=-rss 2>/dev/null | head -n {MAX_PROCESSES}
  echo @@end
  sleep {INTERVAL_S}
done
"""

_SKIP_FS = ("tmpfs", "devtmpfs", "overlay", "squashfs", "efivarfs", "udev", "none")


def local_argv():
    return ["sh", "-c", SCRIPT]


def ssh_argv(ssh_args):
    """Runs the script over the existing shared connection. BatchMode: if
    there is none (and a password would be needed), fail instead of asking."""
    return ([SSH_BINARY] + sshmux.client_opts()
            + ["-o", "ControlMaster=no", "-o", "BatchMode=yes", "-T"]
            + list(ssh_args) + ["sh -c " + shlex.quote(SCRIPT)])


# ----- parsing ------------------------------------------------------------------


def split_blocks(text):
    """{section: [lines]} for one block of the script's output."""
    out, current = {}, None
    for line in text.splitlines():
        if line.startswith("@@"):
            current = line[2:].strip()
            out[current] = []
        elif current is not None:
            out[current].append(line)
    return out


def parse_cpu(lines):
    """(busy, total) jiffies from the "cpu" line of /proc/stat."""
    for line in lines:
        parts = line.split()
        if parts and parts[0] == "cpu":
            nums = [int(x) for x in parts[1:] if x.isdigit()]
            if len(nums) >= 4:
                idle = nums[3] + (nums[4] if len(nums) > 4 else 0)  # idle + iowait
                total = sum(nums[:8])
                return total - idle, total
    return None


def parse_meminfo(lines):
    """Bytes: {"MemTotal": .., "MemAvailable": .., "SwapTotal": .., "SwapFree": ..}"""
    out = {}
    for line in lines:
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            out[key.strip()] = int(parts[0]) * 1024
    return out


def parse_df(lines):
    """[(mount, used bytes, total bytes)] of the real filesystems: devices,
    and "/" whatever it is (ZFS on Proxmox, for example)."""
    seen, out = set(), []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 6 or not parts[1].isdigit():
            continue
        dev, total, used, mount = parts[0], int(parts[1]), int(parts[2]), parts[5]
        if dev in _SKIP_FS or dev.startswith("/dev/loop") or total == 0:
            continue
        if not (dev.startswith("/dev/") or mount == "/"):
            continue
        if dev in seen:
            continue
        seen.add(dev)
        out.append((mount, used * 1024, total * 1024))
    out.sort(key=lambda d: (d[0] != "/", d[0]))
    return out[:MAX_DISKS]


def parse_ps(lines):
    """[(name, rss bytes)]"""
    out = []
    for line in lines:
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            out.append((parts[1].strip(), int(parts[0]) * 1024))
    return out


def size(n):
    """1536 → "1.5 KB" (powers of 1024, as free and df -h do)."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") or n >= 100 else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def duration(seconds):
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m = s // 60
    if d:
        return f"{d} d {h} h"
    if h:
        return f"{h} h {m} min"
    return f"{m} min"


class Reader:
    """Turns the script's blocks into a stats dict; CPU usage comes from the
    difference between two blocks."""

    def __init__(self):
        self.info = {}
        self._prev_cpu = None

    def feed(self, block):
        sections = split_blocks(block)
        if "info" in sections:
            lines = sections["info"] + ["", "", "", ""]
            self.info = {"host": lines[0].strip(), "os": lines[1].strip(),
                         "kernel": lines[2].strip(),
                         "cores": int(lines[3]) if lines[3].strip().isdigit() else 0}
            return None
        if "cpu" not in sections:
            return None
        stats = dict(self.info)
        cpu = parse_cpu(sections.get("cpu", []))
        stats["linux"] = cpu is not None
        if cpu is not None:
            if self._prev_cpu is not None:
                busy = cpu[0] - self._prev_cpu[0]
                total = cpu[1] - self._prev_cpu[1]
                stats["cpu"] = max(0.0, min(1.0, busy / total)) if total > 0 else 0.0
            self._prev_cpu = cpu
        mem = parse_meminfo(sections.get("mem", []))
        if "MemTotal" in mem:
            stats["mem"] = (mem["MemTotal"] - mem.get("MemAvailable", 0), mem["MemTotal"])
        if mem.get("SwapTotal"):
            stats["swap"] = (mem["SwapTotal"] - mem.get("SwapFree", 0), mem["SwapTotal"])
        load = " ".join(sections.get("load", [])).split()
        if len(load) >= 3:
            stats["load"] = tuple(load[:3])
        up = " ".join(sections.get("uptime", [])).split()
        if up:
            try:
                stats["uptime"] = float(up[0])
            except ValueError:
                pass
        stats["disks"] = parse_df(sections.get("df", []))
        stats["processes"] = parse_ps(sections.get("ps", []))
        return stats


class Source(QProcess):
    """Runs the script; updated(stats dict), failed(message)."""

    updated = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, argv, parent=None):
        super().__init__(parent)
        self.reader = Reader()
        self._buf = ""
        self._got = False
        self._stopping = False
        self.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.readyReadStandardOutput.connect(self._read)
        self.finished.connect(self._finished)
        self.errorOccurred.connect(self._error)
        self.start(argv[0], argv[1:])

    def _read(self):
        self._buf += bytes(self.readAllStandardOutput()).decode(errors="replace")
        while "@@end\n" in self._buf:
            block, self._buf = self._buf.split("@@end\n", 1)
            stats = self.reader.feed(block)
            if stats is not None:
                self._got = True
                self.updated.emit(stats)

    def _finished(self, *_):
        if self._stopping:
            return
        err = bytes(self.readAllStandardError()).decode(errors="replace").strip()
        last = err.splitlines()[-1] if err else "the command ended"
        self.failed.emit(f"Could not read the stats: {last}")

    def _error(self, err):
        if err == QProcess.ProcessError.FailedToStart and not self._stopping:
            self.failed.emit("Could not start the stats command.")

    def stop(self):
        self._stopping = True
        if self.state() != QProcess.ProcessState.NotRunning:
            self.kill()
            self.waitForFinished(1000)


# ----- the popup ----------------------------------------------------------------


def level_colour(fraction):
    if fraction >= 0.9:
        return "#e5534b"
    if fraction >= 0.7:
        return "#e0a43a"
    return theme.ACCENT


class Bar(QWidget):
    """A thin rounded usage bar, coloured by how full it is."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0.0
        self.setFixedHeight(6)
        self.setMinimumWidth(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set_value(self, v):
        self.value = max(0.0, min(1.0, v))
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        r = QRectF(self.rect())
        p.setBrush(QColor("#232832"))
        p.drawRoundedRect(r, 3, 3)
        if self.value > 0:
            w = max(r.height(), r.width() * self.value)
            p.setBrush(QColor(level_colour(self.value)))
            p.drawRoundedRect(QRectF(r.left(), r.top(), w, r.height()), 3, 3)
        p.end()


class Sparkline(QWidget):
    """Recent pings as a small line graph; gaps where there was no answer."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.values = []
        self.setFixedHeight(34)
        self.setMinimumWidth(200)

    def set_values(self, values):
        self.values = list(values)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 3, -1, -2)
        p.setPen(QPen(QColor("#232832"), 1))
        p.drawLine(r.bottomLeft(), r.bottomRight())
        good = [v for v in self.values if v >= 0]
        if len(self.values) < 2 or not good:
            p.end()
            return
        top = max(50.0, max(good) * 1.2)
        step = r.width() / (max(len(self.values), 2) - 1)
        accent = QColor(theme.ACCENT)
        runs, run = [], []
        for i, v in enumerate(self.values):
            if v < 0:
                if run:
                    runs.append(run)
                run = []
                continue
            run.append(QPointF(r.left() + i * step, r.bottom() - r.height() * min(v, top) / top))
        if run:
            runs.append(run)
        for pts in runs:
            if len(pts) == 1:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(accent)
                p.drawEllipse(pts[0], 1.5, 1.5)
                continue
            fill = QPainterPath(QPointF(pts[0].x(), r.bottom()))
            for pt in pts:
                fill.lineTo(pt)
            fill.lineTo(QPointF(pts[-1].x(), r.bottom()))
            grad = QLinearGradient(0, r.top(), 0, r.bottom())
            c = QColor(accent)
            c.setAlpha(70)
            grad.setColorAt(0, c)
            c.setAlpha(0)
            grad.setColorAt(1, c)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(grad)
            p.drawPath(fill)
            line = QPainterPath(pts[0])
            for pt in pts[1:]:
                line.lineTo(pt)
            p.setPen(QPen(accent, 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(line)
        p.end()


def _dim(text=""):
    lbl = QLabel(text)
    lbl.setObjectName("dimLabel")
    return lbl


def _section(text):
    lbl = QLabel(text)
    lbl.setObjectName("statsSection")
    return lbl


class StatsPopup(QFrame):
    """The stats of one terminal's computer, live while open.
    ping_history: a callable giving recent pings, or None (no ping)."""

    closed = pyqtSignal()

    def __init__(self, title, argv, ping_history=None, parent=None):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setObjectName("statsPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumWidth(380)
        self.ping_history = ping_history
        self.last = None

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(6)

        top = QHBoxLayout()
        self.host_label = QLabel(title)
        self.host_label.setObjectName("statsHost")
        self.uptime_label = _dim()
        top.addWidget(self.host_label, 1)
        top.addWidget(self.uptime_label)
        v.addLayout(top)
        self.os_label = _dim()
        v.addWidget(self.os_label)
        v.addSpacing(6)

        self.message = _dim("Collecting…")
        self.message.setWordWrap(True)
        v.addWidget(self.message)

        self.body = QWidget()
        b = QVBoxLayout(self.body)
        b.setContentsMargins(0, 0, 0, 0)
        b.setSpacing(6)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(12)
        self.grid.setVerticalSpacing(8)
        self.grid.setColumnStretch(1, 1)
        self.grid.setColumnMinimumWidth(0, NAME_WIDTH)
        self.rows = {}
        for key, name in (("cpu", "CPU"), ("mem", "Memory"), ("swap", "Swap")):
            self._add_row(key, name)
        b.addLayout(self.grid)
        self.load_label = _dim()
        b.addWidget(self.load_label)

        b.addSpacing(6)
        b.addWidget(_section("DISKS"))
        self.disk_grid = QGridLayout()
        self.disk_grid.setHorizontalSpacing(12)
        self.disk_grid.setVerticalSpacing(8)
        self.disk_grid.setColumnStretch(1, 1)
        self.disk_grid.setColumnMinimumWidth(0, NAME_WIDTH)
        b.addLayout(self.disk_grid)
        self.disk_rows = []

        self.ping_box = QWidget()
        pv = QVBoxLayout(self.ping_box)
        pv.setContentsMargins(0, 6, 0, 0)
        pv.setSpacing(4)
        ph = QHBoxLayout()
        ph.addWidget(_section("PING"))
        ph.addStretch(1)
        self.ping_label = _dim()
        ph.addWidget(self.ping_label)
        pv.addLayout(ph)
        self.sparkline = Sparkline()
        pv.addWidget(self.sparkline)
        b.addWidget(self.ping_box)
        self.ping_box.setVisible(ping_history is not None)

        b.addSpacing(6)
        b.addWidget(_section("MOST MEMORY"))
        self.proc_grid = QGridLayout()
        self.proc_grid.setHorizontalSpacing(12)
        self.proc_grid.setVerticalSpacing(3)
        self.proc_grid.setColumnStretch(0, 1)
        b.addLayout(self.proc_grid)
        self.proc_rows = []
        v.addWidget(self.body)
        self.body.hide()

        v.addSpacing(4)
        v.addWidget(_dim(f"Live, every {INTERVAL_S} s. Closing this stops it."))

        self.source = Source(argv, self)
        self.source.updated.connect(self.show_stats)
        self.source.failed.connect(self._failed)
        self.update_ping()

    def _add_row(self, key, name):
        r = self.grid.rowCount()
        label = QLabel(name)
        label.setObjectName("statsName")
        bar = Bar()
        value = QLabel()
        value.setObjectName("statsValue")
        value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.grid.addWidget(label, r, 0)
        self.grid.addWidget(bar, r, 1)
        self.grid.addWidget(value, r, 2)
        self.rows[key] = (label, bar, value)

    def _set_row(self, key, fraction, text):
        label, bar, value = self.rows[key]
        visible = fraction is not None
        for w in (label, bar, value):
            w.setVisible(visible)
        if visible:
            bar.set_value(fraction)
            value.setText(text)

    def _failed(self, message):
        self.message.setText(message)
        self.message.show()

    def show_stats(self, s):
        self.last = s
        if s.get("host"):
            self.host_label.setText(s["host"])
        self.os_label.setText(" · ".join(x for x in (s.get("os"), s.get("kernel")) if x))
        if "uptime" in s:
            self.uptime_label.setText("up " + duration(s["uptime"]))
        if not s.get("linux"):
            self.message.setText("Stats are available for Linux computers only.")
            self.message.show()
            self.body.hide()
            self.adjustSize()
            return
        self.message.hide()
        self.body.show()

        cpu = s.get("cpu")
        self._set_row("cpu", cpu if cpu is not None else 0.0,
                      f"{cpu * 100:.0f} %" if cpu is not None else "…")
        if "mem" in s:
            used, total = s["mem"]
            self._set_row("mem", used / total, f"{size(used)} / {size(total)}")
        else:
            self._set_row("mem", None, "")
        if "swap" in s:
            used, total = s["swap"]
            self._set_row("swap", used / total, f"{size(used)} / {size(total)}")
        else:
            self._set_row("swap", None, "")
        bits = []
        if s.get("cores"):
            bits.append(f"{s['cores']} cores")
        if "load" in s:
            bits.append("load " + "  ".join(s["load"]))
        self.load_label.setText(" · ".join(bits))

        self._fill_disks(s.get("disks", []))
        self._fill_processes(s.get("processes", []))
        self.adjustSize()

    def _fill_disks(self, disks):
        while len(self.disk_rows) < len(disks):
            r = len(self.disk_rows)
            name, bar, value = QLabel(), Bar(), QLabel()
            name.setObjectName("statsName")
            value.setObjectName("statsValue")
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.disk_grid.addWidget(name, r, 0)
            self.disk_grid.addWidget(bar, r, 1)
            self.disk_grid.addWidget(value, r, 2)
            self.disk_rows.append((name, bar, value))
        for i, (name, bar, value) in enumerate(self.disk_rows):
            if i < len(disks):
                mount, used, total = disks[i]
                name.setText(mount)
                name.setToolTip(mount)
                bar.set_value(used / total)
                value.setText(f"{size(used)} / {size(total)}")
            for w in (name, bar, value):
                w.setVisible(i < len(disks))

    def _fill_processes(self, procs):
        while len(self.proc_rows) < len(procs):
            r = len(self.proc_rows)
            name, value = QLabel(), QLabel()
            name.setObjectName("statsProc")
            value.setObjectName("statsValue")
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.proc_grid.addWidget(name, r, 0)
            self.proc_grid.addWidget(value, r, 1)
            self.proc_rows.append((name, value))
        for i, (name, value) in enumerate(self.proc_rows):
            if i < len(procs):
                name.setText(procs[i][0])
                value.setText(size(procs[i][1]))
            name.setVisible(i < len(procs))
            value.setVisible(i < len(procs))

    def update_ping(self, *_):
        if self.ping_history is None:
            return
        values = self.ping_history()
        self.sparkline.set_values(values)
        good = [x for x in values if x >= 0]
        if good:
            self.ping_label.setText(
                f"now {values[-1]:.0f} ms · min {min(good):.0f} · avg "
                f"{sum(good) / len(good):.0f} · max {max(good):.0f}"
                if values[-1] >= 0 else "no answer")
        else:
            self.ping_label.setText("no answer")

    def show_below(self, widget):
        self.adjustSize()
        anchor = widget.mapToGlobal(widget.rect().bottomRight())
        screen = widget.screen().availableGeometry()
        x = max(screen.left() + 4, min(anchor.x() - self.width(), screen.right() - self.width() - 4))
        y = anchor.y() + 4
        if y + self.height() > screen.bottom():
            y = widget.mapToGlobal(widget.rect().topLeft()).y() - self.height() - 4
        self.move(x, max(screen.top(), y))
        self.show()

    def closeEvent(self, event):
        self.source.stop()
        self.closed.emit()
        super().closeEvent(event)
