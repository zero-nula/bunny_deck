"""Text editors on this computer: which ones are installed, which one the
desktop uses for text files, and the command line that opens a file in one.
No UI code in here.

Editors are found the way desktops find applications: the .desktop files in
$XDG_DATA_HOME/applications and $XDG_DATA_DIRS/*/applications (which also
covers Flatpak exports). An entry counts as a text editor when it opens
text/plain and is in the TextEditor or IDE category; LibreOffice or a web
browser also open text/plain but are not meant for editing a config file.

Bunny Deck's own choice is a string stored in config.json:
  ""                 the desktop's default for text/plain (xdg-mime)
  "desktop:<id>"     a desktop entry, e.g. "desktop:dev.zed.Zed.desktop"
  "command:<cmd>"    a command line, e.g. "konsole -e nvim"
"""

import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass

SYSTEM = ""
DESKTOP_PREFIX = "desktop:"
COMMAND_PREFIX = "command:"
TEXT_MIME = "text/plain"
EDITOR_CATEGORIES = {"TextEditor", "IDE"}


@dataclass
class Editor:
    id: str        # desktop file id ("org.kde.kate.desktop"); "" for a command
    name: str
    exec: str      # the Exec line (desktop entry) or the user's command
    icon: str = ""
    path: str = ""  # the .desktop file

    @property
    def choice(self):
        return DESKTOP_PREFIX + self.id if self.id else COMMAND_PREFIX + self.exec


def data_dirs():
    """Data directories in precedence order (the user's own first)."""
    home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    dirs = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    return [home] + [d for d in dirs.split(":") if d]


def read_desktop_entry(path):
    """The keys of the [Desktop Entry] group (other groups, such as desktop
    actions with their own Exec lines, are ignored). Localized keys are
    skipped; Name is the untranslated name."""
    entry = {}
    group = None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("["):
                    group = line
                    continue
                if group != "[Desktop Entry]" or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if "[" not in key:
                    entry.setdefault(key, value.strip())
    except OSError:
        return {}
    return entry


def _list(value):
    return [v for v in (value or "").split(";") if v]


def _program_exists(program):
    if not program:
        return False
    if os.path.isabs(program):
        return os.access(program, os.X_OK)
    return shutil.which(program) is not None


def _exec_program(exec_line):
    try:
        args = shlex.split(exec_line)
    except ValueError:
        return None
    return args[0] if args else None


def _desktop_files():
    """(desktop id, path) of every .desktop file, the first one of each id
    winning (a user's own entry hides the system one)."""
    seen = set()
    for base in data_dirs():
        apps = os.path.join(base, "applications")
        for root, _dirs, files in os.walk(apps):
            for name in sorted(files):
                if not name.endswith(".desktop"):
                    continue
                path = os.path.join(root, name)
                # A file in a subfolder has the folder in its id: kde/x.desktop -> kde-x.desktop
                did = os.path.relpath(path, apps).replace(os.sep, "-")
                if did not in seen:
                    seen.add(did)
                    yield did, path


def _usable(entry):
    """Whether a desktop entry can be launched as a graphical program."""
    if entry.get("Type") != "Application":
        return False
    if entry.get("Hidden") == "true" or not entry.get("Exec"):
        return False
    try_exec = entry.get("TryExec")
    if try_exec and not _program_exists(try_exec):
        return False
    return _program_exists(_exec_program(entry["Exec"]))


def _editor_from(did, path, entry):
    return Editor(did, entry.get("Name") or did[:-len(".desktop")], entry["Exec"],
                  entry.get("Icon", ""), path)


def find_editors():
    """The installed graphical text editors, by name. Editors that run in a
    terminal (Terminal=true, such as vim) are left out; they can be used
    with a command such as "konsole -e nvim"."""
    editors = []
    for did, path in _desktop_files():
        entry = read_desktop_entry(path)
        if not _usable(entry) or entry.get("NoDisplay") == "true" \
                or entry.get("Terminal") == "true":
            continue
        if TEXT_MIME not in _list(entry.get("MimeType")):
            continue
        if not EDITOR_CATEGORIES & set(_list(entry.get("Categories"))):
            continue
        editors.append(_editor_from(did, path, entry))
    return sorted(editors, key=lambda e: e.name.lower())


def find_desktop(did):
    """The launchable Editor for a desktop file id, or None."""
    for d, path in _desktop_files():
        if d == did:
            entry = read_desktop_entry(path)
            return _editor_from(did, path, entry) if _usable(entry) else None
    return None


def system_default_id():
    """The desktop's default application for text files ("" if unknown)."""
    if shutil.which("xdg-mime") is None:
        return ""
    try:
        out = subprocess.run(["xdg-mime", "query", "default", TEXT_MIME],
                             capture_output=True, text=True, timeout=5,
                             stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return ""
    lines = out.stdout.strip().splitlines()
    return lines[0].strip() if out.returncode == 0 and lines else ""


def system_default(editors=None):
    """The desktop's default for text files, or else the first editor found
    (None when there is no editor at all)."""
    did = system_default_id()
    editor = find_desktop(did) if did else None
    if editor is not None and editor.exec:
        return editor
    editors = find_editors() if editors is None else editors
    return editors[0] if editors else None


def resolve(choice, editors=None):
    """The Editor for a stored choice. A desktop entry that is no longer
    installed falls back to the system default."""
    choice = choice or SYSTEM
    if choice.startswith(COMMAND_PREFIX):
        cmd = choice[len(COMMAND_PREFIX):].strip()
        if cmd:
            return Editor("", cmd, cmd)
    elif choice.startswith(DESKTOP_PREFIX):
        editor = find_desktop(choice[len(DESKTOP_PREFIX):])
        if editor is not None:
            return editor
    return system_default(editors)


def check_command(cmd):
    """None if a custom command can be run, else the reason."""
    try:
        args = shlex.split(cmd)
    except ValueError as e:
        return f"The command cannot be read: {e}"
    if not args:
        return "Enter a command."
    if not _program_exists(args[0]):
        return f"“{args[0]}” was not found."
    return None


def command_for(editor, path):
    """The argv that opens path in editor. Field codes of the Desktop Entry
    specification are expanded (%f, %F, %u, %U: the file; %i: the icon;
    %c: the name; %k: the .desktop file); without a file field code the
    file is added at the end, so a custom command can be just "kate"."""
    args = []
    placed = False
    for arg in shlex.split(editor.exec):
        if arg in ("%f", "%F", "%u", "%U"):
            args.append(path)
            placed = True
            continue
        if arg == "%i":
            if editor.icon:
                args += ["--icon", editor.icon]
            continue
        out, i = [], 0
        while i < len(arg):
            c = arg[i]
            if c == "%" and i + 1 < len(arg):
                code = arg[i + 1]
                i += 2
                if code == "%":
                    out.append("%")
                elif code in "fFuU":
                    out.append(path)
                    placed = True
                elif code == "c":
                    out.append(editor.name)
                elif code == "k":
                    out.append(editor.path)
                # Deprecated and unknown codes (%d, %n, %v, …) are dropped.
                continue
            out.append(c)
            i += 1
        if out:
            args.append("".join(out))
    if not placed:
        args.append(path)
    return args
