"""Reading and extending the user's OpenSSH client configuration.

Host aliases are read from ~/.ssh/config (following Include), and what each
one really means (host name, user, port) is asked from OpenSSH itself with
`ssh -G`, so defaults, Match blocks and every other rule apply exactly as on
the command line. Writing only ever appends a new Host block, after making a
backup copy of the file.
"""

import glob
import os
import shutil
import subprocess
import time

SSH_BINARY = "/usr/bin/ssh"
_PATTERN_CHARS = set("*?!")


def ssh_dir():
    return os.path.expanduser("~/.ssh")


def config_path():
    return os.path.join(ssh_dir(), "config")


def _lines(path, seen):
    """Yields (keyword, args) of a config file and its Includes, in order."""
    path = os.path.realpath(path)
    if path in seen:
        return
    seen.add(path)
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.replace("=", " ", 1).split()
        keyword, args = parts[0].lower(), parts[1:]
        if keyword == "include":
            for arg in args:
                arg = os.path.expanduser(arg)
                if not os.path.isabs(arg):
                    arg = os.path.join(ssh_dir(), arg)
                for inc in sorted(glob.glob(arg)):
                    yield from _lines(inc, seen)
        else:
            yield keyword, args


def host_aliases(path=None):
    """Concrete host aliases in the order they appear (patterns such as
    `*` or `*.example.com` and negations are skipped)."""
    aliases = []
    for keyword, args in _lines(path or config_path(), set()):
        if keyword != "host":
            continue
        for name in args:
            if not _PATTERN_CHARS & set(name) and name not in aliases:
                aliases.append(name)
    return aliases


def resolve(alias, extra_opts=()):
    """What OpenSSH uses for `ssh alias`: {"hostname", "user", "port"}, or
    None if it cannot evaluate the configuration."""
    try:
        out = subprocess.run([SSH_BINARY, *extra_opts, "-G", "--", alias],
                             stdin=subprocess.DEVNULL, capture_output=True, text=True,
                             timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    values = {}
    for line in out.stdout.splitlines():
        key, _, value = line.partition(" ")
        if key in ("hostname", "user", "port") and key not in values:
            values[key] = value.strip()
    try:
        values["port"] = int(values.get("port", 22))
    except ValueError:
        values["port"] = 22
    return values if "hostname" in values else None


def valid_alias(alias):
    return bool(alias) and not any(c.isspace() for c in alias) \
        and not _PATTERN_CHARS & set(alias) and not alias.startswith("-")


def add_host(alias, hostname, user="", port=22, identity_file=""):
    """Appends a Host block to ~/.ssh/config (creating ~/.ssh and the file
    with private permissions if needed). An existing file is copied to
    config.bunny-deck-<timestamp>.bak first. Returns the backup path or None."""
    if not valid_alias(alias):
        raise ValueError(f"invalid host alias {alias!r}")
    if alias in host_aliases():
        raise ValueError(f"{alias!r} is already in {config_path()}")
    os.makedirs(ssh_dir(), mode=0o700, exist_ok=True)
    path = config_path()
    backup = None
    existing = ""
    if os.path.exists(path):
        backup = f"{path}.bunny-deck-{time.strftime('%Y%m%d-%H%M%S')}.bak"
        shutil.copy2(path, backup)
        with open(path, encoding="utf-8", errors="replace") as f:
            existing = f.read()
    block = [f"Host {alias}", f"    HostName {hostname}"]
    if user:
        block.append(f"    User {user}")
    if port and port != 22:
        block.append(f"    Port {port}")
    if identity_file:
        home = os.path.expanduser("~")
        if identity_file.startswith(home + os.sep):
            identity_file = "~" + identity_file[len(home):]
        block.append(f"    IdentityFile {identity_file}")
    sep = "" if not existing or existing.endswith("\n\n") else \
        ("\n" if existing.endswith("\n") else "\n\n")
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as f:
        f.write(sep + "# Added by Bunny Deck\n" + "\n".join(block) + "\n")
    return backup
