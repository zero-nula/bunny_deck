"""GUI test: the root badge and the ping latency in the terminal header.
Uses fake ping and `ssh -G` (no packets, never reads ~/.ssh) and a local
bash whose prompt is switched between a user and a root prompt."""

import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from gui_harness import check, local_shell_argv, make_profile, run  # noqa: E402

from bunnydeck import latency, stats  # noqa: E402
from bunnydeck.terminal import prompt_is_root, title_is_root  # noqa: E402

latency.PING_BINARY = os.path.join(HERE, "fake_ping.py")
latency.SSH_BINARY = os.path.join(HERE, "fake_ssh_g.py")
latency.INTERVAL_MS = 300
LOG = os.path.join(tempfile.mkdtemp(), "ping.log")
os.environ["FAKE_PING_LOG"] = LOG


def pings():
    try:
        with open(LOG) as f:
            return f.read().splitlines()
    except OSError:
        return []


def test(app):
    win = app.window

    # Prompt and title recognition
    check(prompt_is_root("root@pve:~# ") is True, "root@host# is root")
    check(prompt_is_root("[root@web ~]# ") is True, "[root@host]# is root")
    check(prompt_is_root("\x1b[01;31mroot@x\x1b[00m:~# ") is True, "coloured root prompt")
    check(prompt_is_root("bash-5.3# ") is True, "bare bash root prompt")
    check(prompt_is_root("bob@pve:~$ ") is False, "user prompt is not root")
    check(prompt_is_root("output line\n# comment") is None, "a comment is not a prompt")
    check(prompt_is_root("total 12\n") is None, "plain output is not judged")
    check(title_is_root("root@pve: ~") is True and title_is_root("bob@pve: ~") is False,
          "window title user@host")
    check(latency.parse_ssh_g("hostname 10.0.0.5\nproxyjump none\n") == ("10.0.0.5", True),
          "ssh -G: address, direct")
    check(latency.parse_ssh_g("hostname x\nproxycommand nc %h %p\n")[1] is False,
          "ssh -G: a proxy is not direct")
    check(latency.parse_ping("64 bytes from x: icmp_seq=1 ttl=64 time=0.043 ms") == 0.043,
          "ping time parsed")

    # Stats parsing
    blk = ("@@cpu\ncpu  100 0 100 800 0 0 0 0 0 0\n@@mem\nMemTotal: 1000 kB\n"
           "MemAvailable: 250 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n@@load\n0.50 0.40 0.30 1/99 7\n"
           "@@uptime\n93784.5 1000.0\n@@df\nFilesystem 1024-blocks Used Available Capacity Mounted on\n"
           "rpool/ROOT/pve-1 1000 400 600 40% /\ntmpfs 100 1 99 1% /run\n"
           "/dev/sda1 2000 1000 1000 50% /data\n/dev/loop0 10 10 0 100% /snap/x\n"
           "rpool/data 900 1 899 1% /rpool/data\n@@ps\n 2048 qemu-system-x86\n 1024 pveproxy\n")
    rd = stats.Reader()
    rd.feed("@@info\npve\nDebian GNU/Linux 12\nLinux 6.8.12\n8\n")
    s1 = rd.feed(blk)
    check(s1["host"] == "pve" and s1["cores"] == 8, "info: host and cores")
    check("cpu" not in s1, "no CPU usage from the first sample")
    s2 = rd.feed(blk.replace("cpu  100 0 100 800", "cpu  150 0 150 900"))
    check(abs(s2["cpu"] - 0.5) < 1e-6, f"CPU usage from two samples ({s2.get('cpu')})")
    check(s2["mem"] == (750 * 1024, 1000 * 1024), "memory used = total - available")
    check("swap" not in s2, "no swap row without swap")
    check([d[0] for d in s2["disks"]] == ["/", "/data"],
          f"disks: / (ZFS too) and devices, no tmpfs/loop/datasets ({s2['disks']})")
    check(s2["processes"][0] == ("qemu-system-x86", 2048 * 1024), "biggest process first")
    check(stats.duration(93784) == "1 d 2 h" and stats.size(1536 * 1024) == "1.5 MB", "formatting")
    argv = stats.ssh_argv(["-l", "root", "--", "pve"])
    check("BatchMode=yes" in argv and "ControlMaster=no" in argv and argv[-2] == "pve"
          and argv[-1].startswith("sh -c '"), "over the shared connection, never asks")

    # Root: a local bash whose prompt turns into a root prompt and back
    p = win.open_profile(make_profile("box"), argv=local_shell_argv())
    yield lambda: p.child_pid
    yield 0.3
    check(not p.root_badge.isVisibleTo(p), "no root badge for a user prompt")
    p.terminal.sendText("PS1='root@box:~# '\n")
    yield lambda: p.root_badge.isVisibleTo(p)
    check(p.is_root, "root prompt shows the root badge")
    p.terminal.sendText("PS1='tester@box:~$ '\n")
    yield lambda: not p.root_badge.isVisibleTo(p)
    check(not p.is_root, "back to a user prompt hides the badge")

    rp = win.open_profile(make_profile("asroot", user="root"), argv=local_shell_argv())
    check(rp.root_badge.isVisibleTo(rp), "a profile with user root shows the badge at once")
    yield lambda: rp.child_pid
    yield lambda: not rp.root_badge.isVisibleTo(rp)
    check(True, "then the prompt decides (this test shell is not root)")
    check(rp.latency_text == "", "no latency when argv is given (tests)")

    # Latency: two terminals to the same host share one ping
    a = win.open_profile(make_profile("a", host="pve"), argv=local_shell_argv())
    b = win.open_profile(make_profile("b", host="pve"), argv=local_shell_argv())
    yield lambda: a.child_pid and b.child_pid
    a.start_latency()
    b.start_latency()
    yield lambda: a.latency_text and b.latency_text
    check(a.latency_text == "12 ms", f"latency shown ({a.latency_text})")
    check(a.stats_btn.property("level") == "good", "fast is green")
    check(all(line.endswith("-- pve.resolved") for line in pings()),
          f"pings the address from ssh -G ({pings()[:1]})")
    yield 1.0
    n = len(pings())
    check(n <= 6, f"one ping per interval for both terminals ({n})")

    os.environ["FAKE_PING_MS"] = "240"
    yield lambda: a.stats_btn.property("level") == "slow"
    check(a.latency_text == "240 ms", "slow is shown in red")
    os.environ["FAKE_PING_MS"] = "none"
    yield lambda: a.latency_text == ""
    check(True, "no answer hides the latency")
    os.environ["FAKE_PING_MS"] = "12.4"

    j = win.open_profile(make_profile("j", host="viajump"), argv=local_shell_argv())
    yield lambda: j.child_pid
    j.start_latency()
    yield 0.8
    check(j.latency_text == "", "no ping through a ProxyJump")

    # The stats popup of a local terminal (the real script, run here)
    check(p.stats_btn.isVisibleTo(p), "the stats button shows while connected")
    p.stats_btn.click()
    pop = p.stats_popup
    check(pop is not None and pop.isVisible(), "clicking it opens the stats")
    yield lambda: pop.last is not None and "cpu" in pop.last
    check(pop.rows["mem"][1].value > 0, "memory bar filled")
    check(pop.disk_rows and pop.disk_rows[0][0].text() == "/", "the root filesystem listed")
    check(pop.proc_rows and pop.proc_rows[0][0].text(), "processes listed")
    check(not pop.ping_box.isVisible(), "no ping graph without ping")
    src = pop.source
    pop.close()
    yield lambda: src.state() == src.ProcessState.NotRunning
    check(p.stats_popup is None, "closing the popup ends the script")

    a.show_stats()
    yield lambda: a.stats_popup.last is not None
    check(a.stats_popup.ping_box.isVisible(), "SSH-like terminal with ping: graph shown")
    check(len(a.stats_popup.sparkline.values) >= 1, "ping history in the graph")
    a.stats_popup.close()

    a.terminal.sendText("exit\n")
    b.terminal.sendText("exit\n")
    yield lambda: not a.running and not b.running
    check(a.latency_text == "" and not a.stats_btn.isVisibleTo(a), "latency and stats button hidden when the session ends")
    check(not latency._monitors, "the shared monitor stops when nobody listens")
    n = len(pings())
    yield 0.8
    check(len(pings()) == n, "no more pings after the sessions end")


if __name__ == "__main__":
    sys.exit(run(test))
