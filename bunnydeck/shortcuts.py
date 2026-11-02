"""Keyboard shortcuts: one table used by the terminals' key filter, the
window (when no terminal has the focus) and the shortcut overview.

All use Ctrl+Shift or Alt+arrows, so the plain Ctrl+letter keys stay with
the programs running in the terminal (Ctrl+C, Ctrl+R, Ctrl+W, ...). Only
font zoom uses Ctrl with + - 0, as most terminals do.
"""

from PyQt6.QtCore import QKeyCombination, Qt
from PyQt6.QtGui import QKeySequence

K = Qt.Key
CTRL = Qt.KeyboardModifier.ControlModifier
SHIFT = Qt.KeyboardModifier.ShiftModifier
ALT = Qt.KeyboardModifier.AltModifier
META = Qt.KeyboardModifier.MetaModifier
CS = CTRL | SHIFT

# (modifiers, key) -> action. Several entries may share an action where
# keyboard layouts differ (e.g. "+" needs Shift on some, not on others).
KEYS = {
    (CS, K.Key_C): "copy",
    (CS, K.Key_V): "paste",
    (SHIFT, K.Key_Insert): "paste",
    (CS, K.Key_B): "broadcast",
    (CS, K.Key_T): "new-local",
    (CS, K.Key_N): "new-ssh",
    (CS, K.Key_O): "saved-menu",
    (CS, K.Key_W): "close",
    (CS, K.Key_F): "files",
    (CS, K.Key_M): "network-scan",
    (CS, K.Key_Question): "help",
    (CTRL, K.Key_Question): "help",
    (CS, K.Key_Slash): "help",
    (ALT, K.Key_Left): "focus-left",
    (ALT, K.Key_Right): "focus-right",
    (ALT, K.Key_Up): "focus-up",
    (ALT, K.Key_Down): "focus-down",
    (CTRL, K.Key_Tab): "next",
    (CS, K.Key_Backtab): "previous",
    (CS, K.Key_Tab): "previous",
    (CTRL, K.Key_Plus): "zoom-in",
    (CS, K.Key_Plus): "zoom-in",
    (CTRL, K.Key_Equal): "zoom-in",
    (CTRL, K.Key_Minus): "zoom-out",
    (CTRL, K.Key_0): "zoom-reset",
}

# For the overview: (keys, description), grouped.
OVERVIEW = [
    ("Terminals", [
        ("Ctrl+Shift+T", "New local terminal"),
        ("Ctrl+Shift+N", "New SSH connection"),
        ("Ctrl+Shift+O", "Open a saved connection"),
        ("Ctrl+Shift+W", "Close the current terminal"),
        ("Ctrl+Shift+F", "File manager for the current terminal / back"),
        ("Ctrl+Shift+B", "Broadcast on / off"),
        ("Ctrl+Shift+M", "Network scan (map of your network)"),
    ]),
    ("Moving", [
        ("Alt+← → ↑ ↓", "Terminal to the left, right, above, below"),
        ("Ctrl+Tab / Ctrl+Shift+Tab", "Next / previous terminal"),
    ]),
    ("Font", [
        ("Ctrl++", "Larger (current terminal)"),
        ("Ctrl+-", "Smaller"),
        ("Ctrl+0", "Default size"),
    ]),
    ("Copy and paste", [
        ("Ctrl+Shift+C", "Copy"),
        ("Ctrl+Shift+V, Shift+Insert", "Paste"),
    ]),
    ("Help", [
        ("Ctrl+Shift+?", "This overview"),
    ]),
]


def action_for(event):
    """The action of a key event, or None."""
    mods = event.modifiers() & (CTRL | SHIFT | ALT | META)
    return KEYS.get((mods, Qt.Key(event.key())))


def sequences():
    """(QKeySequence, action) for window-wide QShortcuts."""
    return [(QKeySequence(QKeyCombination(mods, key)), action)
            for (mods, key), action in KEYS.items()]
