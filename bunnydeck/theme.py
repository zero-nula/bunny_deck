"""The shared colours of Bunny Deck, so the main window, the file manager,
the network scan and the dialogs speak one design language (see style.qss)."""

from PyQt6.QtGui import QColor

ACCENT = "#5b8def"       # focus, selection, the active terminal
PRIMARY = "#2f5fbf"      # the main button of a view or dialog
SURFACE = "#121418"
BORDER = "#1f232a"
TEXT = "#c9ced6"
DIM = "#6b7280"
OK = "#6cc070"
WARN = "#e5c07b"
ERROR = "#e0605d"
BROADCAST = "#e8913a"


def accent_for(appearance):
    """A bright colour that stands for a connection (its dot, the line over
    its active terminal), derived from its dark background colour. Grey or
    default backgrounds get the app's accent."""
    bg = QColor(getattr(appearance, "background", "") or "")
    if not bg.isValid():
        return ACCENT
    h, s, v, _ = bg.getHsv()
    chroma = max(bg.red(), bg.green(), bg.blue()) - min(bg.red(), bg.green(), bg.blue())
    if h < 0 or s < 50 or chroma < 12:
        return ACCENT
    return QColor.fromHsv(h, 150, 235).name()
