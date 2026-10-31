"""Loads the locally built PyQt6 binding for the system QTermWidget.

Distributions ship libqtermwidget6 without Python bindings, so Bunny Deck
builds a tiny one itself (native/, ../build-native.sh). If it is missing or no
longer matches the installed libraries (e.g. after a system update bumped
libqtermwidget6's soname), this raises an error that says how to fix it.
"""

import os

HERE = os.path.dirname(os.path.abspath(__file__))
BUILD_SCRIPT = os.path.join(os.path.dirname(HERE), "build-native.sh")


class NativeModuleError(ImportError):
    pass


try:
    from .qtermwidget import QTermWidget  # noqa: F401
except ImportError as e:  # pragma: no cover - depends on the local build
    raise NativeModuleError(
        "The QTermWidget binding for Bunny Deck is missing or out of date:\n"
        f"  {e}\n\n"
        f"Rebuild it with:\n  {BUILD_SCRIPT}\n\n"
        "The packages it needs are listed per distribution in README.md "
        "(\"Installation\")."
    ) from e
