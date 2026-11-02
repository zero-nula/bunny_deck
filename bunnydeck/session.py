"""The terminals and their arrangement, saved on quit and reopened on start.

Stored in $XDG_CONFIG_HOME/bunny-deck/session.json, apart from config.json.
A terminal is recorded by what it runs: the local shell, a saved connection
(by id), an AI tool (by id) or an unsaved connection (name, host, user,
port). Never passwords, output or anything typed.
"""

import json
import os
import tempfile

from PyQt6.QtCore import Qt

from .config import config_dir

VERSION = 1
_ORIENTATION = {"row": Qt.Orientation.Horizontal, "column": Qt.Orientation.Vertical}


def session_path():
    return os.path.join(config_dir(), "session.json")


def encode(tree, entry_for, active=None):
    """JSON data for a layout.describe() tree; entry_for(panel) gives the
    record of a terminal (or None to leave it out)."""
    panels = []

    def node(n):
        orientation, sizes, children = n
        out = []
        for c in children:
            if isinstance(c, tuple):
                out.append(node(c))
            else:
                entry = entry_for(c)
                if entry is not None:
                    panels.append(c)
                    out.append({"terminal": entry})
        name = "row" if orientation == Qt.Orientation.Horizontal else "column"
        return {"split": name, "sizes": list(sizes), "children": out}

    data = {"version": VERSION, "layout": node(tree)}
    if active in panels:
        data["active"] = panels.index(active)
    return data


def decode(data, panel_for):
    """The tree back with panels: panel_for(entry) creates a terminal (or
    returns None to skip it). Returns (tree, panels) or (None, [])."""
    panels = []

    def node(n):
        saved = n.get("sizes", [])
        children, sizes = [], []
        for i, c in enumerate(n.get("children", [])):
            if "terminal" in c:
                child = panel_for(c["terminal"])
                if child is not None:
                    panels.append(child)
            else:
                child = node(c)
                if not child[2]:
                    child = None
            if child is not None:
                children.append(child)
                # Left-out terminals give up their share; the rest keep theirs.
                if i < len(saved) and isinstance(saved[i], (int, float)):
                    sizes.append(int(saved[i]))
        if len(sizes) != len(children):
            sizes = []
        return (_ORIENTATION.get(n.get("split"), Qt.Orientation.Horizontal), sizes, children)

    try:
        if data.get("version") != VERSION:
            return None, []
        tree = node(data["layout"])
    except (KeyError, TypeError, AttributeError, ValueError):
        return None, panels
    return (tree if panels else None), panels


def load():
    try:
        with open(session_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def save(data):
    os.makedirs(config_dir(), mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=config_dir(), prefix=".session-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, session_path())
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
