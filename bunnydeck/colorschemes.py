"""Per-profile terminal colours as QTermWidget (Konsole format) colour schemes.

QTermWidget only takes named colour schemes. For every background colour a
scheme file named after that colour is generated in a private runtime
directory and passed to setColorScheme() by absolute path. (Schemes created
after QTermWidget has scanned its directories are only found by path, not by
name.) Opacity is applied separately with setTerminalOpacity(), which only
affects the background, never text.
"""

import os
import re
import tempfile

FOREGROUND = "#d8dce2"
# Readable 16 colour palette tuned for dark backgrounds (same as before).
PALETTE = [
    "#1d1f21", "#e0605d", "#8fc970", "#e5c07b", "#6ea8f0", "#c38ae0", "#5fc8c8", "#c8ccd2",
    "#5c6370", "#ff7b78", "#a8e08a", "#f2d494", "#8fbfff", "#d8a6f2", "#7fe0e0", "#f2f4f7",
]

_HEX = re.compile(r"^#[0-9a-f]{6}$")
_dir = None


def _rgb(hexcol):
    return ",".join(str(int(hexcol[i:i + 2], 16)) for i in (1, 3, 5))


def scheme_dir():
    global _dir
    if _dir is None:
        base = os.environ.get("XDG_RUNTIME_DIR")
        if not base or not os.path.isdir(base):
            base = None
        _dir = tempfile.mkdtemp(prefix="bunny-deck-schemes-", dir=base)
    return _dir


def scheme_path(background):
    """Absolute path of the scheme file for this background colour,
    creating it if needed."""
    background = background.lower()
    if not _HEX.match(background):
        raise ValueError(f"invalid colour {background!r}")
    path = os.path.join(scheme_dir(), f"bunnydeck-{background[1:]}.colorscheme")
    if not os.path.exists(path):
        lines = []

        def section(name, color, bold=False):
            lines.append(f"[{name}]")
            lines.append(f"Color={_rgb(color)}")
            if bold:
                lines.append("Bold=true")

        section("Background", background)
        section("BackgroundIntense", background)
        section("Foreground", FOREGROUND)
        section("ForegroundIntense", "#ffffff", bold=True)
        for i in range(8):
            section(f"Color{i}", PALETTE[i])
            section(f"Color{i}Intense", PALETTE[8 + i])
        lines += ["[General]", f"Description=Bunny Deck {background}", "Opacity=1", ""]
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        os.replace(tmp, path)
    return path
