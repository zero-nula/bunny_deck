"""GUI test: snap layouts (templates, zones) and dragging a terminal by its
header onto another one."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gui_harness import check, local_shell_argv, make_profile, run  # noqa: E402

from PyQt6 import sip  # noqa: E402
from PyQt6.QtCore import QPoint  # noqa: E402

from bunnydeck import snap  # noqa: E402
from bunnydeck.layout import ABOVE, LEFT, RIGHT, SWAP  # noqa: E402


def geo(win, p):
    pos = p.mapTo(win.layout_, QPoint(0, 0))
    return pos.x(), pos.y(), p.width(), p.height()


def test(app):
    win = app.window
    win.resize(1500, 900)

    # Pure template logic
    big_left = dict(snap.TEMPLATES)["Big left"]
    a, b, c, d = "a", "b", "c", "d"
    check(snap.assign(big_left, [a, b, c], chosen=c, zone=0) == {0: [c], 1: [a], 2: [b]},
          "the chosen terminal takes its zone, the others fill the rest in order")
    check(snap.assign(big_left, [a, b, c, d], chosen=a, zone=2) == {0: [b], 1: [c, d], 2: [a]},
          "extra terminals share the last free zone, the chosen one stays alone")
    tree = snap.build(big_left, {0: [a], 1: [b], 2: []})
    check(tree[2] == [a, b], f"empty zones are left out ({tree})")
    check(snap.zone_count(snap.resolve(snap.ALL_STACKED, 5)) == 5, "'all' adapts to the count")

    panels = [win.open_profile(make_profile(n), argv=local_shell_argv())
              for n in ("one", "two", "three")]
    yield lambda: all(p.child_pid for p in panels)
    one, two, three = panels

    # The popup from a header's Move button
    one.move_btn.click()
    popup = win.snap_popup
    check(popup is not None and popup.isVisible(), "Move opens the snap layouts popup")
    names = [t if isinstance(t, str) else [n for n, x in snap.TEMPLATES if x is t][0]
             for t, _ in popup.thumbs]
    check("Big left" in names and "Grid" not in names,
          f"templates fitting three terminals are offered ({names})")
    thumb = dict((n, th) for n, (_, th) in zip(names, popup.thumbs))["Big left"]
    thumb.picked.emit(0)
    yield 0.5
    check(sip.isdeleted(popup) or not popup.isVisible(), "the popup closes after a choice")
    W, H = win.layout_.width(), win.layout_.height()
    x, y, w, h = geo(win, one)
    check(x == 0 and abs(w - W * 0.6) < 30 and h > H - 10,
          f"'one' fills the big left zone ({x, y, w, h} of {W}x{H})")
    x2, y2, w2, h2 = geo(win, two)
    x3, y3, w3, h3 = geo(win, three)
    check(x2 > w and x3 > w and y3 > y2 and abs(h2 - h3) < 10,
          "the other two are stacked on the right")
    check(win.layout_.custom and not win.vsplit_btn.isChecked(), "presets are cleared")

    # Clicking a zone in the toolbar popup for the current terminal
    win._focus_panel(three)
    win.layouts_btn.click()
    popup = win.snap_popup
    thumb = [th for t, th in popup.thumbs if t == snap.ALL_STACKED][0]
    thumb.picked.emit(0)
    yield 0.5
    check(win.hsplit_btn.isChecked() and not win.layout_.custom,
          "'All stacked' is the stacked preset")
    check(win.layout_.ordered_panels()[0] is three, "the current terminal went to the top")

    # Dragging: drop targets
    win.set_split(win.layout_.orientation.__class__.Horizontal)  # side by side
    yield 0.4
    order = win.layout_.ordered_panels()
    left, mid, right = order
    r = mid.rect()

    def at(panel, fx, fy):
        return panel.mapToGlobal(QPoint(int(r.width() * fx), int(r.height() * fy)))

    t = win.layout_.drop_target(at(mid, 0.5, 0.5), left)
    check(t and t[0] is mid and t[1] == SWAP, "the middle of a terminal means swap")
    t = win.layout_.drop_target(at(mid, 0.1, 0.5), left)
    check(t and t[1] == LEFT and t[2].width() <= mid.width() // 2 + 1, "near the left edge: left half")
    t = win.layout_.drop_target(at(mid, 0.5, 0.05), left)
    check(t and t[1] == ABOVE, "near the top: above")
    check(win.layout_.drop_target(at(left, 0.5, 0.5), left) is None,
          "over itself there is no target")

    # The drag signals: preview, then swap on drop
    left.headerDragged.emit(left, at(right, 0.5, 0.5), "start")
    left.headerDragged.emit(left, at(right, 0.5, 0.5), "move")
    ov = win.layout_.overlay
    check(ov.isVisible() and ov.label == f"Swap with {right.profile.name}",
          f"the preview names the target ({ov.label!r})")
    left.headerDragged.emit(left, at(right, 0.5, 0.5), "drop")
    yield 0.4
    check(not ov.isVisible(), "the preview disappears on drop")
    check(win.layout_.ordered_panels() == [right, mid, left], "dropped in the middle: swapped")
    check(all(p.child_pid for p in panels), "the terminals keep running")

    # Drop on the right half of the first one
    mid.headerDragged.emit(mid, at(right, 0.95, 0.5), "move")
    mid.headerDragged.emit(mid, at(right, 0.95, 0.5), "drop")
    yield 0.4
    check(win.layout_.ordered_panels() == [right, mid, left], "dropped right of 'right'")
    left.headerDragged.emit(left, at(right, 0.5, 0.97), "drop")
    yield 0.4
    xr, yr, wr, hr = geo(win, right)
    xl, yl, wl, hl = geo(win, left)
    check(xl == xr and yl > yr, "dropped below: stacked under it")
    check(win.layout_.custom and not win.vsplit_btn.isChecked(), "a custom arrangement now")

    # Dropping outside any terminal does nothing.
    before = win.layout_.describe()
    mid.headerDragged.emit(mid, win.mapToGlobal(QPoint(5, 5)), "drop")
    check(str(win.layout_.describe()) == str(before), "dropping outside changes nothing")


sys.exit(run(test))
