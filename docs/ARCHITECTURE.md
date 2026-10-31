# Bunny Deck – architecture

Bunny Deck is a single Python application with a Qt user interface. It has
no web front end or back end, and no SSH implementation of its own: all SSH
traffic goes through the system OpenSSH.

## Technology

| Layer | Technology |
|---|---|
| Language | Python 3 (developed with 3.13) |
| GUI | Qt 6 through PyQt6 (QtWidgets, QtDBus), styled with `bunnydeck/style.qss` |
| Terminal | QTermWidget 2 for Qt 6 (`libqtermwidget6`, the Konsole terminal emulator as used by QTerminal) |
| QTermWidget binding | Bunny Deck's own, built with SIP 6 and PyQt-builder (`native/`), because distributions ship none |
| SSH | the system `/usr/bin/ssh`; no paramiko or libssh |
| Configuration | JSON in `~/.config/bunny-deck/config.json` (schema `version: 1`) |

## Source layout

```
bunny-deck                launcher script (runs from the project directory)
install-deps.sh           installs the distribution packages (also used by CI)
build-native.sh           builds the QTermWidget binding into bunnydeck/
install.sh, uninstall.sh  add / remove the menu entry and icon in ~/.local/share
native/                   pyproject.toml.in + SIP declaration of the QTermWidget API subset
data/                     icon, .desktop template, default background (and its generator)
bunnydeck/app.py           QApplication setup, main window, toolbar, AI menu, single instance (D-Bus)
bunnydeck/layout.py        nested QSplitters, Move and split presets
bunnydeck/terminal.py      terminal panel: header + QTermWidget, starts ssh or a shell
bunnydeck/colorschemes.py  per-profile colours as generated QTermWidget colour schemes
bunnydeck/background.py    background images composed to the terminal's size
bunnydeck/dialogs.py       Add SSH, Add AI tool, Background and credential dialogs
bunnydeck/filemanager.py   dual-pane file manager UI
bunnydeck/mainthread.py    hands results from worker threads to the UI thread
bunnydeck/native.py        loads the binding, with a clear error if it is missing
--- no GUI code ---
bunnydeck/config.py        profiles, AI tools and appearance (XDG config, atomic writes)
bunnydeck/sftp.py          SFTP v3 protocol client over `ssh -s host sftp`
bunnydeck/askpass.py       in-app SSH_ASKPASS endpoint (private Unix socket)
bunnydeck/askpass_helper.py   helper executed by ssh; forwards prompts to the app
bunnydeck/connections.py   SFTP connection pool (UI hooks passed in)
bunnydeck/transfer.py      Local/Remote backends and the copy engine
tests/                     unit tests and GUI tests on Xvfb (run_all.sh)
```

## Application identity

| Identity | Value |
|---|---|
| Application ID / Wayland app_id / D-Bus name | `org.bunnydeck.BunnyDeck` |
| X11 `WM_CLASS` class, `_KDE_NET_WM_DESKTOP_FILE` | `org.bunnydeck.BunnyDeck` |
| Desktop file | `org.bunnydeck.BunnyDeck.desktop` (`StartupWMClass=org.bunnydeck.BunnyDeck`) |
| Icon | `bunny-deck` |

The app is single-instance: launching it again raises the existing window.

## The QTermWidget binding

Distributions ship QTermWidget without Python bindings, so `native/`
declares the subset of its API that Bunny Deck uses
(`native/sip/qtermwidget/qtermwidgetmod.sip`). `build-native.sh` finds
PyQt6's `.sip` files, QTermWidget's headers and Qt 6's `qmake` on the
system, fills them into `pyproject.toml.in`, runs `sip-build` and `make` in a
temporary directory and installs the resulting module into `bunnydeck/`.

The module is tied to the installed libqtermwidget6 and PyQt6 ABI. After an
update changes either, importing it fails and `bunnydeck/native.py` shows a
message asking to run `./build-native.sh` again.

## Terminals

There is no web rendering. Each terminal is a `TerminalPanel`
(`bunnydeck/terminal.py`): a header (QLabel/QToolButton) and a
`QTermWidget`, which emulates a VT terminal and draws with Qt
(`Konsole::TerminalDisplay`). Font: Monospace 10.5 pt, 10 000 lines of
scrollback. Ctrl+Shift+C/V and Shift+Insert are caught by an event filter on
the `TerminalDisplay`.

- **SSH terminal:** runs `/usr/bin/ssh [-p port] [-l user] -- host`
  (`Profile.ssh_args()`; port 22 is not passed, so a `Port` in
  `~/.ssh/config` still applies). `QTermWidget(0)` → `setShellProgram` /
  `setArgs` → `startShellProgram()`. **QTermWidget opens the PTY** and runs
  `ssh` in it; the data flows keys → TerminalDisplay → emulator → PTY → `ssh`
  → server and back, and Python code never reads the PTY. OpenSSH asks for
  passwords and passphrases inside the terminal.
- **Local terminal:** runs `$SHELL` without arguments (or `/bin/bash` if it
  is unset or not executable) in `$HOME`. All local terminals share one
  profile. One opens automatically when the app starts.
- **AI tools:** run `$SHELL -ic "<command>; exec $SHELL"` in a local
  terminal. The shell is interactive so that `~/.bashrc` (PATH additions,
  aliases) applies even when the app was started from the desktop; when the
  tool exits the shell takes over, so its last output stays readable.
- **End of a session** is detected with `finished()` / `titleChanged` and a
  1 s timer that reads `/proc/<pid>/stat`. Closing sends `SIGHUP`.
  QTermWidget does not report the exit code, so the header shows
  "disconnected" for both a closed and a failed connection.

### Layout and resizing

The layout (`layout.py`) is a tree of nested `QSplitter`s. Moving a terminal
or switching between vertical and horizontal split only re-arranges the
panels; they are never recreated, and no session reconnects. On window
resize the splitters keep their proportions and QTermWidget rewraps its
content itself. There are no resize overrides, forced scrolling, terminal
clearing or padding tricks.

## Colours, transparency and background images

QTermWidget only accepts named colour schemes. For each background colour
the app generates a scheme file (same text palette for all) in a private
`$XDG_RUNTIME_DIR/bunny-deck-schemes-*` directory and loads it by path. That
directory is removed when the app quits.

The main window is translucent (`WA_TranslucentBackground`) and
`QTermWidget.setTerminalOpacity()` sets only the background alpha. Text,
the cursor, headers and toolbars stay fully opaque, and on a compositing
desktop the desktop shows through the terminal background.

Background images are not placed by QTermWidget itself: its Fit and Center
modes leave the area around the image unpainted, and it has no Fill mode.
`background.py` composes the image on the background colour at the
terminal's exact size and hands QTermWidget that file in Stretch mode; after
a resize settles (120 ms) it is composed again. A chosen image is copied to
`~/.config/bunny-deck/backgrounds/` (named after its content hash) when the
setting is saved.

The default background of local terminals is
`data/backgrounds/bunny-skate.png`, drawn by `make_bunny_skate.py` in the
same directory: a transparent, dim badge shown in Center mode, so it suits
any background colour.

## File manager and SFTP

- The file manager uses its own SSH connection per profile:
  `ssh <options> -s host sftp`, started with `subprocess.Popen` over pipes
  (no PTY). `sftp.py` is a small SFTP v3 client (32 KiB packets, reader and
  stderr threads). `connections.py` keeps one connection per profile and
  connects in worker threads; results reach the UI thread through
  `mainthread.call_soon()` (a Qt signal).
- Because it runs the system OpenSSH, `~/.ssh/config`, ssh-agent, keys and
  `known_hosts` behave as on the command line.
- Host-key verification stays on: unknown keys are shown for confirmation; a
  *changed* key is refused and reported.
- Password, keyboard-interactive and passphrase prompts go through
  `SSH_ASKPASS` (`SSH_ASKPASS_REQUIRE=force`): `askpass_helper.py` forwards
  the prompt over a private `0700` Unix socket, checked with `SO_PEERCRED`
  and a per-run token, to `askpass.py`, which shows a Qt dialog.
- Passwords never appear in command lines, environment variables, logs or
  files, and are not cached. A wrong password asks again with a warning;
  Cancel stops the attempt. `sshpass` is not used.

### Safe writes

- **Uploads** go to a hidden `.name.XXXX.bunnydeck-part` file and are renamed into place (with `posix-rename` only after you confirm a replace). The only file the app ever removes is its own part file after a failure.
- **Downloads** follow the same pattern locally; without confirmation they use `link()`, which fails if the target exists.
- **Remote ↔ Remote** copies are staged in a private `0700` directory under `$XDG_RUNTIME_DIR`, always removed afterwards. The source is never touched.

## Tests

```bash
tests/run_all.sh                                # everything
python3 -m unittest discover -s tests -p 'test_*.py'
tests/run_gui_tests.sh tests/gui_phase2.py      # a single GUI suite
```

GUI tests run the real application on a private **Xvfb** display with a
throwaway configuration directory. They never open windows on your desktop
or touch `~/.ssh`. SFTP and password tests use the local `sftp-server` and a
throwaway, unprivileged `sshd` on 127.0.0.1 (package `openssh-server`), so
they must not run as root. CI (`.gitlab-ci.yml`) runs the whole
suite on openSUSE Tumbleweed, Fedora, Arch Linux, Debian and Ubuntu.

| Area | Automated (no GUI) | Headless GUI (Xvfb, real app) | Needs a manual check |
|---|---|---|---|
| Window, terminals, real `ssh` spawn | | ✓ local shell + `ssh` to a closed local port | Real hosts |
| Add SSH dialog, saved profiles, menus, AI tools | ✓ | ✓ | |
| Copy / paste keys | | ✓ real clipboard round trip | |
| Splits, Move, resize (same widgets and PIDs, scrollback kept) | | ✓ | Real window manager maximize / restore |
| SFTP protocol | ✓ against local `sftp-server` | ✓ | Real hosts |
| Password / passphrase / host-key dialogs | ✓ real OpenSSH + throwaway local `sshd` | ✓ wrong → retry warning → right, cancel | Password auth on a real server |
| Local↔Remote, Remote↔Remote copies, drag and drop | ✓ | ✓ (drops simulated at the widget level) | Real drag from a file manager |
| Backgrounds: preview, save, persist, reset, images, per-pixel alpha | ✓ | ✓ | Desktop visibly showing through |
| Menu entry, icon, window association | `desktop-file-validate` | ✓ X11 `WM_CLASS`, `_KDE_NET_WM_DESKTOP_FILE` | Pinning to a task bar |
