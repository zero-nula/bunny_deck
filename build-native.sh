#!/bin/bash
# Builds the PyQt6 binding for the system QTermWidget into bunnydeck/.
# Needed once, and again after a system update that changes libqtermwidget6's
# soname or PyQt6's ABI. See README.md for the build packages per distribution.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

die() { echo "build-native.sh: $*" >&2; exit 1; }

# PyQt6's .sip files (in a -devel/-dev package on most distributions).
PYQT6_SIP_DIR=""
pyqt6_dir=$(python3 -c 'import os, PyQt6; print(os.path.dirname(PyQt6.__file__))' 2>/dev/null) \
  || die "PyQt6 for python3 is not installed."
for d in "$pyqt6_dir/bindings" /usr/share/sip/PyQt6 /usr/share/python3-sip/PyQt6; do
  if [ -f "$d/QtWidgets/QtWidgetsmod.sip" ]; then PYQT6_SIP_DIR="$d"; break; fi
done
[ -n "$PYQT6_SIP_DIR" ] || die "PyQt6's .sip files were not found (install PyQt6's development package)."

# QTermWidget's headers.
QTERMWIDGET_INCLUDE_DIR=""
for d in "$(pkg-config --variable=includedir qtermwidget6 2>/dev/null)/qtermwidget6" \
         /usr/include/qtermwidget6 /usr/local/include/qtermwidget6; do
  if [ -f "$d/qtermwidget.h" ]; then QTERMWIDGET_INCLUDE_DIR="$d"; break; fi
done
[ -n "$QTERMWIDGET_INCLUDE_DIR" ] || die "qtermwidget.h (Qt6) was not found (install QTermWidget's development package)."

# Qt 6's qmake.
QMAKE=""
for q in "$(command -v qmake6 || true)" /usr/lib/qt6/bin/qmake /usr/lib64/qt6/bin/qmake \
         "/usr/lib/$(gcc -dumpmachine 2>/dev/null)/qt6/bin/qmake"; do
  if [ -n "$q" ] && [ -x "$q" ]; then QMAKE="$q"; break; fi
done
[ -n "$QMAKE" ] || die "Qt 6 qmake was not found (install Qt 6's base development package)."

command -v sip-build >/dev/null || die "sip-build was not found (install SIP 6 and PyQt-builder)."

# The ABI (major.minor) of the installed PyQt6.sip module; newer SIP needs the minor too.
SIP_ABI=$(python3 -c 'from PyQt6 import sip; v = sip.SIP_ABI_VERSION; print(f"{v >> 16}.{(v >> 8) & 255}")') \
  || die "could not read the ABI version of PyQt6.sip."

BUILD=$(mktemp -d)
trap 'rm -rf "$BUILD"' EXIT
cp -r "$HERE/native/sip" "$BUILD/"
sed -e "s|@PYQT6_SIP_DIR@|$PYQT6_SIP_DIR|" \
    -e "s|@QTERMWIDGET_INCLUDE_DIR@|$QTERMWIDGET_INCLUDE_DIR|" \
    -e "s|@SIP_ABI@|$SIP_ABI|" \
    "$HERE/native/pyproject.toml.in" > "$BUILD/pyproject.toml"
cd "$BUILD"
sip-build --no-make --build-dir build --qmake "$QMAKE" >/dev/null
make -C build -j"$(nproc)" >/dev/null
so=$(find build -name 'qtermwidget*.so' | head -1)
[ -n "$so" ] || die "the build produced no module."
install -m 755 "$so" "$HERE/bunnydeck/"
echo "Built $(basename "$so") -> bunnydeck/"
