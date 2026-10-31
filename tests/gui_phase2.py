"""Phase 2 GUI test: three independent terminals, splits, saved profiles,
clipboard, and the critical resize sequence on the SAME window (no
widget/process recreation) in BOTH split layouts.

Note: the test X server has no window manager, so "maximize" is simulated
with plain window resizes. Real KWin maximize/restore must be checked by hand.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gui_harness import (  # noqa: E402
    check, local_shell_argv, make_profile, process_events, run, screen_lines, screenshot,
    scrollbar, term_text,
)

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from bunnydeck.config import Appearance, Config  # noqa: E402
from bunnydeck.layout import HORIZONTAL_SPLIT, VERTICAL_SPLIT  # noqa: E402

SHOTS = os.environ.get("BUNNYDECK_SHOTS", "")
BIG, SMALL = (1800, 1000), (1080, 600)  # SMALL is 60 % of BIG


def prompt_line(panel):
    """Last non-empty visible line (where the prompt/cursor is)."""
    lines = [ln.rstrip() for ln in screen_lines(panel)]
    while lines and not lines[-1]:
        lines.pop()
    return lines[-1] if lines else ""


def at_bottom(panel):
    sb = scrollbar(panel)
    return sb.value() == sb.maximum()


def sizes(panels, orientation):
    return [p.width() if orientation == VERTICAL_SPLIT else p.height() for p in panels]


def test(app):
    win = app.window
    ubuntu = app.config.add(make_profile("Ubuntu", host="ubuntu.invalid"))
    rpi = app.config.add(make_profile("Raspberry Pi", host="rpi.invalid", user="pi"))
    win.rebuild_saved_menu()
    reloaded = Config(app.config.path)
    check([p.id for p in reloaded.profiles] == [ubuntu.id, rpi.id],
          "saved profiles persisted with stable ids")
    items = [a.text() for a in win.saved_menu.actions() if not a.isSeparator() and not a.menu()]
    check(len(items) == 2 and any("Raspberry Pi" in i for i in items),
          f"Saved Connections menu lists profiles {items}")

    win.resize(*BIG)
    yield 0.3
    panels = [
        win.open_profile(ubuntu, argv=local_shell_argv()),
        win.open_profile(rpi, argv=local_shell_argv()),
        win.open_profile(ubuntu, argv=local_shell_argv()),  # 2nd session, same profile
    ]
    # One translucent terminal so the resize test also covers that path.
    panels[1].apply_appearance(Appearance("#0f2318", 0.8))
    yield lambda: all(p.child_pid for p in panels)
    pids = [p.child_pid for p in panels]
    check(len(set(pids)) == 3, f"three independent processes {pids}")
    yield 0.5
    for i, p in enumerate(panels):
        p.terminal.sendText(
            f"PS1='s{i}$ '; for n in $(seq 1 300); do echo line-$n-of-s{i}; done\n")
    yield lambda: all(f"line-300-of-s{i}" in term_text(p) for i, p in enumerate(panels))
    check(all(f"line-300-of-s{i}" in term_text(p) for i, p in enumerate(panels)),
          "each terminal received only its own input")
    check(all(f"of-s{(i + 1) % 3}" not in term_text(p) for i, p in enumerate(panels)),
          "no output leaked between sessions")

    # --- clipboard round trip (real copy and paste) -----------------------
    p0 = panels[0]
    top = p0.terminal.historyLinesCount()
    p0.terminal.setSelectionStart(top - 1, 0)
    p0.terminal.setSelectionEnd(top - 1, 13)
    sel = p0.terminal.selectedText(True).strip()
    QTest.keyClick(p0.display, Qt.Key.Key_C,
                   Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    yield 0.2
    check(sel.startswith("line-") and QApplication.clipboard().text().strip() == sel,
          f"Ctrl+Shift+C copies the selection ({sel!r})")
    QApplication.clipboard().setText("echo pasted-ok")
    QTest.keyClick(p0.display, Qt.Key.Key_V,
                   Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    yield lambda: prompt_line(p0).endswith("echo pasted-ok")
    check(prompt_line(p0).endswith("echo pasted-ok"), "Ctrl+Shift+V pastes into the terminal")
    p0.terminal.sendText("\x15")  # Ctrl+U: clear the pasted line
    yield lambda: prompt_line(p0) == "s0$"

    term_ids = [id(p.terminal) for p in panels]
    for orientation, name in ((HORIZONTAL_SPLIT, "horizontal"), (VERTICAL_SPLIT, "vertical")):
        win.set_split(orientation)
        win.resize(*BIG)
        yield 0.5
        s = sizes(panels, orientation)
        check(max(s) - min(s) <= 4, f"{name} split: equal panel sizes {s}")
        if SHOTS:
            screenshot(win, os.path.join(SHOTS, f"qt-{name}-split.png"))

        # Leave unsent input on every prompt.
        for i, p in enumerate(panels):
            p.terminal.sendText(f"echo pending-{name}-{i}")
        yield lambda: all(prompt_line(p).endswith(f"pending-{name}-{i}")
                          for i, p in enumerate(panels))
        rows_before = [p.terminal.screenLinesCount() for p in panels]
        sizes_before = sizes(panels, orientation)
        hist_before = [p.terminal.historyLinesCount() + p.terminal.screenLinesCount()
                       for p in panels]

        ok_prompt = ok_bottom = True
        bad = []
        for cycle in range(3):
            for (w, h) in (SMALL, BIG):
                win.resize(w, h)
                yield lambda: abs(win.width() - w) < 2
                yield 0.35
                process_events()
                for i, p in enumerate(panels):
                    pl = prompt_line(p)
                    if not pl.endswith(f"pending-{name}-{i}"):
                        ok_prompt = False
                        bad.append((cycle, (w, h), i, pl))
                    ok_bottom &= at_bottom(p)
                if SHOTS and cycle == 0 and (w, h) == SMALL:
                    screenshot(win, os.path.join(SHOTS, f"qt-{name}-small.png"))
        check(ok_prompt, f"{name}: prompt with typed input is the last visible line after "
                         f"every resize {bad[:3]}")
        check(ok_bottom, f"{name}: view stayed at the bottom (no jump into scrollback)")
        check([id(p.terminal) for p in panels] == term_ids,
              f"{name}: terminal widgets were never recreated")
        check([p.child_pid for p in panels] == pids and all(p.running for p in panels),
              f"{name}: processes survived all resizes")
        rows_after = [p.terminal.screenLinesCount() for p in panels]
        check(rows_after == rows_before, f"{name}: rows restored at full size {rows_after}")
        s = sizes(panels, orientation)
        check(all(abs(a - b) <= 3 for a, b in zip(s, sizes_before)),
              f"{name}: split proportions kept {sizes_before} -> {s}")
        hist_after = [p.terminal.historyLinesCount() + p.terminal.screenLinesCount()
                      for p in panels]
        check(all(a >= 300 for a in hist_after),
              f"{name}: scrollback retained ({hist_before} -> {hist_after} lines)")
        for p in panels:
            p.terminal.sendText("\n")
        yield lambda: all(f"\npending-{name}-{i}" in term_text(p) for i, p in enumerate(panels))
        check(all(f"\npending-{name}-{i}" in term_text(p) for i, p in enumerate(panels)),
              f"{name}: typed input survived resizes and executed")

    # --- closing a middle terminal re-lays out the rest -------------------
    win.close_panel(panels[1])
    yield 0.4
    rest = [panels[0], panels[2]]
    s = sizes(rest, VERTICAL_SPLIT)
    check(len(win.layout_.panels) == 2 and max(s) - min(s) <= 4,
          f"layout adapts after closing a terminal {s}")
    check(all(p.running for p in rest) and [id(p.terminal) for p in rest] ==
          [term_ids[0], term_ids[2]], "remaining sessions untouched by layout change")
    win.resize(700, 400)
    yield 0.5
    check(win.width() <= 720, f"window can shrink small ({win.width()})")


if __name__ == "__main__":
    sys.exit(run(test, timeout=120))
