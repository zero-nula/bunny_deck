"""Delivering work from worker threads to the Qt UI thread."""

from PyQt6.QtCore import QObject, pyqtSignal


class _Invoker(QObject):
    call = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        # Emitting from another thread queues the call into this object's
        # (the UI) thread automatically.
        self.call.connect(lambda fn: fn())


_invoker = None


def call_soon(fn):
    """Runs fn() on the UI thread (later, never synchronously)."""
    global _invoker
    if _invoker is None:
        raise RuntimeError("mainthread.init() must be called on the UI thread first")
    _invoker.call.emit(fn)


def init():
    """Create the invoker on the UI thread, after QApplication exists."""
    global _invoker
    if _invoker is None:
        _invoker = _Invoker()
