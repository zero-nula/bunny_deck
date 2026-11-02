#!/bin/bash
# Installs the system packages Bunny Deck needs to build and run, using the
# distribution's package manager (asks for sudo unless run as root).
#
#   ./install-deps.sh           packages to build and run Bunny Deck
#   ./install-deps.sh --tests   also what the test suite needs (Xvfb, sshd, D-Bus)
#   ./install-deps.sh --print   only print the command, install nothing
#
# Supported: openSUSE Tumbleweed, Fedora 43+, Arch Linux (and derivatives),
# Debian 13+, Ubuntu 26.04+. The same lists are used by the CI.
set -euo pipefail

TESTS=0
PRINT=0
for arg in "$@"; do
  case "$arg" in
    --tests) TESTS=1 ;;
    --print) PRINT=1 ;;
    -h|--help) sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

[ -r /etc/os-release ] || { echo "Cannot identify the distribution (/etc/os-release missing)." >&2; exit 1; }
# shellcheck disable=SC1091
. /etc/os-release
IDS=" ${ID:-} ${ID_LIKE:-} "

SUDO=""
if [ "$(id -u)" != 0 ]; then SUDO="sudo"; fi

run() {
  echo "+ $*"
  [ "$PRINT" = 1 ] || "$@"
}

version_at_least() {  # version_at_least 26.04 26.04 -> true
  [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -1)" = "$2" ]
}

case "$IDS" in
  *" opensuse-tumbleweed "*|*" opensuse-slowroll "*)
    # Python packages carry the interpreter version (python313-...).
    if ! command -v python3 >/dev/null; then run $SUDO zypper --non-interactive install python3; fi
    py=$(python3 -c 'import sys; print(f"python{sys.version_info[0]}{sys.version_info[1]}")' 2>/dev/null || echo python3)
    pkgs=("$py-PyQt6" libqtermwidget6-2 qtermwidget-data openssh-clients
          "$py-devel" "$py-PyQt6-devel" "$py-pyqt-builder" "$py-sip6-devel" qtermwidget-devel
          qt6-base-common-devel gcc-c++ make pkg-config)
    [ "$TESTS" = 1 ] && pkgs+=(openssh-server xorg-x11-server-Xvfb dbus-1 dejavu-fonts)
    run $SUDO zypper --non-interactive install --no-recommends "${pkgs[@]}"
    ;;
  *" fedora "*)
    version_at_least "${VERSION_ID:-0}" 43 \
      || echo "Note: Fedora 43 or newer is needed for QTermWidget 2 (Qt 6)." >&2
    pkgs=(python3-pyqt6 qtermwidget openssh-clients
          python3-devel python3-pyqt6-devel PyQt-builder sip6 qtermwidget-devel qt6-qtbase-devel
          gcc-c++ make pkgconf-pkg-config)
    [ "$TESTS" = 1 ] && pkgs+=(openssh-server xorg-x11-server-Xvfb dbus-daemon dejavu-sans-mono-fonts)
    run $SUDO dnf install -y "${pkgs[@]}"
    ;;
  *" arch "*)
    pkgs=(python-pyqt6 qtermwidget openssh sip pyqt-builder qt6-base base-devel pkgconf)
    [ "$TESTS" = 1 ] && pkgs+=(xorg-server-xvfb dbus ttf-dejavu)
    run $SUDO pacman -S --needed --noconfirm "${pkgs[@]}"
    ;;
  *" debian "*|*" ubuntu "*)
    if [ "${ID:-}" = ubuntu ]; then
      version_at_least "${VERSION_ID:-0}" 26.04 \
        || { echo "Ubuntu 26.04 or newer is needed (older releases have no QTermWidget for Qt 6)." >&2; exit 1; }
      qtw_dev=libqtermwidget6-2-dev
    else
      if [ -n "${VERSION_ID:-}" ] && ! version_at_least "$VERSION_ID" 13; then
        echo "Debian 13 (trixie) or newer is needed (older releases have no QTermWidget for Qt 6)." >&2
        exit 1
      fi
      qtw_dev=libqtermwidget-dev
    fi
    # (libqtermwidget6-2 pulls in its data package: qtermwidget-data on
    # Debian, qtermwidget6-data on Ubuntu.)
    pkgs=(python3-pyqt6 libqtermwidget6-2 openssh-client
          python3-dev pyqt6-dev sip-tools python3-pyqtbuild "$qtw_dev" qt6-base-dev qmake6
          build-essential pkg-config)
    [ "$TESTS" = 1 ] && pkgs+=(openssh-server xvfb dbus fonts-dejavu-core)
    run $SUDO apt-get update
    run $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${pkgs[@]}"
    ;;
  *)
    echo "Unsupported distribution: ${PRETTY_NAME:-unknown}." >&2
    echo "Install by hand: PyQt6 (with its .sip files), QTermWidget 2 for Qt 6 (library and" >&2
    echo "headers), SIP 6 (sip-build), PyQt-builder, Qt 6 qmake, a C++ compiler, make and the" >&2
    echo "OpenSSH client. Then run ./install.sh." >&2
    exit 1
    ;;
esac
[ "$PRINT" = 1 ] || echo "Done. Next: ./install.sh"
