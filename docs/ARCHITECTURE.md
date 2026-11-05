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
bunnydeck/snap.py          snap layout templates, zone assignment and the popup
bunnydeck/sidebar.py       connections sidebar (search, colour dots, open terminal counts)
bunnydeck/aimenu.py        the AI menu's rows (QWidgetActions: initial tile, command, open count)
bunnydeck/welcome.py       start screen: main actions and recent connections
bunnydeck/theme.py         shared colours; a connection's accent colour from its background
bunnydeck/icons.py         toolbar and header icons drawn with QPainter
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
bunnydeck/session.py       the open terminals and their arrangement, saved on quit
bunnydeck/shortcuts.py     the keyboard shortcut table (terminal key filter, window, overview)
bunnydeck/sshmux.py        one shared SSH connection per host (OpenSSH ControlMaster)
bunnydeck/latency.py       ping to SSH hosts for the terminal header (one per address)
bunnydeck/stats.py         the header's stats popup: CPU, memory, disks, processes, ping graph
bunnydeck/sshconfig.py     reads hosts from ~/.ssh/config (ssh -G), appends Host entries
bunnydeck/keysetup.py      key login setup: create key, authorize it, verify
bunnydeck/transfer.py      Local/Remote backends and the copy engine
bunnydeck/editors.py       installed text editors (.desktop files), the default one, their command lines
bunnydeck/remoteedit.py    editing a server's file in a local editor: download, watch, upload on save
bunnydeck/netscan.py       network scan: runs nmap, parses its XML, guesses device kinds
bunnydeck/netmap.py        Network Scan window: the animated map, device list and details
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

`build-native.sh` also takes the SIP ABI version from the installed
`PyQt6.sip`, and disables optional parts of the API (SIP `%Feature`) that
an older QTermWidget lacks: Debian 13's 2.1 has no `autoHideMouseAfter`,
so there the mouse pointer simply stays visible while typing.

The module is tied to the installed libqtermwidget6 and PyQt6 ABI. After an
update changes either, importing it fails and `bunnydeck/native.py` shows a
message asking to run `./build-native.sh` again.

## Terminals

There is no web rendering. Each terminal is a `TerminalPanel`
(`bunnydeck/terminal.py`): a header (QLabel/QToolButton) and a
`QTermWidget`, which emulates a VT terminal and draws with Qt
(`Konsole::TerminalDisplay`). Font: the desktop's fixed-width font at 10.5 pt, 10 000 lines of
scrollback. Ctrl+Shift+C/V and Shift+Insert are caught by an event filter on
the `TerminalDisplay`, together with all other shortcuts from
`shortcuts.py`: Konsole claims every key (also as ShortcutOverride), so a
QShortcut never sees keys typed in a terminal. The same table also feeds
window-wide QShortcuts for when the focus is elsewhere (file manager).

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

### Reconnect

A QTermWidget whose program has ended cannot start a new one, so a reconnect
(the header's Reconnect/Restart button, Enter in an ended terminal, or the
right-click menu) builds a new `TerminalPanel` for the same profile and argv
with the old one's appearance, font size and broadcast tick, and
`SplitLayout.replace_panel` puts it at the old one's index in the same
splitter with the same sizes; the old panel is then discarded. Enter only
reconnects in a terminal whose session ran and ended (`panel.ended`).
The sidebar gets the number of ended terminals per connection
(`sessionEnded` refreshes it) and paints the badge red; its Reconnect entry
reconnects the ended terminals of that connection, or, if none has ended,
asks and then replaces the running ones (`reconnect(force=True)`).

### Root badge and latency

The header shows a **root** badge while the session is logged in as root.
SSH terminals cannot see the remote process, so it is read from what the
shell shows: `receivedData` keeps the last few hundred characters of output,
and when its last (unfinished) line looks like a prompt, `prompt_is_root`
decides (`root@host…#`, `[root@host ~]#`, `bash-5.2#` → root;
`user@host…$`, `bash-5.2$` → not). The window title most distributions set
(`user@host: dir`) is used the same way. Other output and unusual prompts
change nothing; before the first prompt, a profile with user `root` counts
as root. A local terminal reads the user id of the foreground process from
`/proc/<pid>/status` once a second instead.

The **latency** (`latency.py`) is an ordinary ICMP `ping` every 5 s from this
computer, never a command over the SSH connection (that would open a server
session, and an auth-log line, every time) and never a probe of the SSH port
(logged as an aborted login, which fail2ban may punish). The address comes
from `ssh -G` with the profile's arguments, so aliases and `HostName` work;
hosts behind `ProxyJump`/`ProxyCommand` are not pinged, because the ping
would not take the connection's way. One `Monitor` per address is shared by
all terminals to it and stops when the last one ends. No answer hides the
value. It keeps the last 36 values for the graph in the stats popup.

The **stats popup** (`stats.py`, the chart icon) runs one read-only POSIX
`sh` script while it is open: once the host name, `PRETTY_NAME`, kernel and
core count, then every 2 s `/proc/stat`, `/proc/meminfo`, `/proc/loadavg`,
`/proc/uptime`, `df -P` and `ps --sort=-rss`, each block ending in `@@end`.
For SSH it goes over the shared connection (`ssh` with `ControlPath`,
`ControlMaster=no`, `BatchMode=yes`, `-T`), so it never asks for a password
and costs one session (one auth-log line) per opening, not per refresh; for
a local terminal it runs here. CPU usage is the difference between two
`/proc/stat` samples. Disks are real devices plus `/` (ZFS on Proxmox),
without tmpfs, loop devices and datasets. Closing the popup kills the
command; the remote script ends at its next write.

### Session

`MainWindow.shutdown()` (window closed, SIGTERM/SIGINT) first saves the
session: `SplitLayout.describe()` gives the splitter tree with orientation,
sizes and panels, and `session.encode()` turns each panel into what it runs
— `local`, `ai` (tool id), `ssh` (saved profile id, or name/host/user/port
for an unsaved connection). It is written atomically to
`~/.config/bunny-deck/session.json` (`0600`). On start, `main()` calls
`restore_session()`: every entry is opened as usual (entries whose profile
or AI tool no longer exists are skipped, and give up their share of the
space), then `SplitLayout.arrange()` moves the panels into a rebuilt
splitter tree and applies the saved proportions, scaled to the current size.
Nothing typed or shown in a terminal is stored.

### Broadcast

With Broadcast on, the key filter on each `TerminalDisplay` replays every
key press of a ticked terminal as a new `QKeyEvent` into the displays of the
other ticked terminals (`TerminalPanel.forward_key`), so letters, Enter,
arrows and Ctrl combinations behave exactly as typed there. A module flag
keeps replayed keys from being broadcast again. Pastes (keyboard, menu,
middle click) are repeated with `pasteClipboard` / `pasteSelection`. Copy
and Ctrl+Shift+B are never broadcast. The state is never saved.

### Layout and resizing

Snap layouts (`snap.py`) are templates in the same form as `describe()`
(orientation, weights, children) with zone numbers as leaves. Choosing a
zone puts that terminal there and the others in the remaining zones in
reading order (more terminals than zones share the last free zone; unused
zones are dropped); the result goes through `arrange()`, so terminals are
only moved, never restarted. Dragging a header emits `headerDragged`; the
layout finds the terminal under the cursor (`drop_target`: the middle third
swaps, otherwise the nearest edge picks the side) and paints the preview in
a mouse-transparent overlay above the splitters.

The layout (`layout.py`) is a tree of nested `QSplitter`s. Moving a terminal
or switching between vertical and horizontal split only re-arranges the
panels; they are never recreated, and no session reconnects. On window
resize the splitters keep their proportions and QTermWidget rewraps its
content itself. There are no resize overrides, forced scrolling, terminal
clearing or padding tricks.

## Look and feel

One design language across the main window, the file manager, the network
scan and the dialogs: `style.qss` styles every button, field and menu the
same way (6 px radius, one border colour, the accent `#5b8def` for focus and
selection, a primary blue for the main button of a view or dialog), and
`theme.py` holds the colours for code that paints itself. Icons are drawn by
`icons.py` with QPainter rather than taken from the icon theme, so they look
the same on every desktop.

Each connection has an accent colour derived from its background
(`theme.accent_for`: same hue, bright; grey backgrounds get the app accent).
It is the dot in the sidebar and on the start screen, and the line over the
active terminal's header. The header's Move, Files and Background buttons
sit behind a `QGraphicsOpacityEffect` that fades them in while the mouse is
over the header, so the header never changes size.

The sidebar and the recent list are refreshed from the open panels and the
config whenever a terminal opens or closes or a profile changes
(`MainWindow.refresh_connections`). `config.json` keeps `recent` (the last
six saved connections opened) and `sidebar_visible`.

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

`data/backgrounds/bunny-skate.png`, drawn by `make_bunny_skate.py` in the
same directory, is an optional background: a transparent, dim badge meant
for Center mode, so it suits any background colour. No terminal uses it
unless chosen.

## Shared connections

Terminals and the file manager share one SSH connection per host through
OpenSSH connection sharing (`sshmux.py`). A terminal runs ssh with
`ControlMaster=auto`, `ControlPath=<dir>/%C` and `ControlPersist=60`: the
first terminal to a host becomes the master (the user logs in there), later
terminals and the SFTP channel run over it without authenticating again.
ControlPersist moves the master to the background, so a terminal ends as soon
as its own session ends even while the file manager still uses the
connection; an idle master exits after 60 seconds.

The SFTP channel only *uses* a master (`ControlMaster=no` plus the same
`ControlPath`) and never becomes one: a master started with its restrictive
options (no agent, X11 or port forwarding) would impose them on terminals
sharing it. Without a terminal to the host it connects by itself, as before.

The sockets live in a private `0700` directory
(`$XDG_RUNTIME_DIR/bunny-deck-ssh-*`) of the app run. When the app quits, every
master is closed with `ssh -O exit` and the directory is removed. Options
given on the command line take precedence over `ControlMaster` settings in
`~/.ssh/config`.

## ~/.ssh/config import and key login

`sshconfig.py` collects the concrete `Host` aliases of `~/.ssh/config`
(following `Include`, skipping patterns and negations) and asks OpenSSH what
each one means with `ssh -G alias`, so defaults and `Match` rules apply as on
the command line. Imported connections use the alias as host name, which
keeps every setting of `~/.ssh/config` in effect. Writing only appends a
`# Added by Bunny Deck` `Host` block after copying the file to
`config.bunny-deck-<time>.bak`; `~/.ssh` and a new file get `0700`/`0600`.

`keysetup.py` runs the key login steps in a worker thread:

1. **Key:** `~/.ssh/id_ed25519` is used if it exists and never overwritten;
   otherwise `ssh-keygen -t ed25519` creates it. OpenSSH tries this file by
   default, so no configuration is needed to use it.
2. **Authorize:** one `ssh host 'exec sh -c …'` reads the public key from
   stdin and appends it to `~/.ssh/authorized_keys` unless that exact line is
   already there (`umask 077`, `restorecon` where available). It uses the
   terminal's shared connection if there is one, so no password is needed.
3. **Verify:** `ssh -o ControlPath=none -o PreferredAuthentications=publickey
   -o PasswordAuthentication=no -o IdentitiesOnly=yes -i <key> host true`
   proves the key alone logs in.

As everywhere else, secrets never reach a command line: the new key's
passphrase is given to `ssh-keygen` and server passwords to `ssh` through
`SSH_ASKPASS` (`askpass.py`). The server's sshd configuration is never
changed.

## Network scan

`netscan.py` runs `nmap` with `QProcess`, as the user (never root or sudo),
in two phases: host discovery (`-sn`) over the chosen network, then a TCP
connect scan (`-sT -Pn --open`) of a fixed list of common ports on the hosts
found, optionally with `-sV --version-light`. Both write XML to stdout
(`-oX -`, with `--stats-every 1s`); `XMLStream` picks complete `<host>` and
`<taskprogress>` elements out of the stream as it arrives, so devices appear
while nmap is still running. The target must parse as an IPv4 network of at
most 4096 addresses and is passed after `--`, so it can never be read as an
option.

Without root, nmap reports no MAC addresses and misses hosts that ignore its
TCP probes. After discovery the kernel's neighbour table (`/proc/net/arp`)
fills both in: MACs of found hosts, and hosts that answered ARP during the
scan. Manufacturers come from nmap's `nmap-mac-prefixes`; a locally
administered MAC is shown as a private (randomized) address. This computer's
networks come from `QNetworkInterface`, the gateway from `/proc/net/route`.
The device kind (router, SSH server, NAS, printer, …) is a guess from the
open ports, the gateway address and the manufacturer.

`netmap.py` draws the map with `QGraphicsView`: the router (or this
computer) as hub at the centre and the other devices by address on rings
around it. Nodes are `QGraphicsObject`s animated with `QPropertyAnimation`
(pop-in, ripple, moving when the rings fill up); the background, links,
radar sweep and the packets travelling to devices being probed are painted
in `drawBackground`, driven by a 30 fps timer only while a scan runs.
Connecting emits `connectRequested(host, port, name)`; the main window opens
its Add SSH dialog with those values.

The tests use `tests/fake_nmap.py`, a stand-in that prints canned XML for a
test network, so no packets are sent.

## File manager and SFTP

- The file manager opens one SFTP channel per profile (over the shared
  connection when a terminal has one): `ssh <options> -s host sftp`, started with `subprocess.Popen` over pipes
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

### Copying folders

The transfer worker first expands the selection into jobs:
`transfer.plan_folder()` lists a folder recursively into the folders to
create (parents first) and the files to copy, with their total size for one
overall progress bar. Symlinks are never followed — the walk cannot loop or
leave the folder — and are skipped like special files (a FIFO would block a
copy forever). A subfolder that cannot be read is reported and the rest is
still copied. Copying a folder into itself is refused. Then the jobs run in
order: folders are created if missing, and every file goes through the same
`copy_file()` path and overwrite question as single files. If a folder
cannot be created, the files below it are not attempted.

### Deleting

Deleting happens only from the file manager's **Delete** button or Delete
key, and only after a confirmation that shows what will go (Cancel is the
default and the Escape button). First a worker thread counts the files and
folders (`transfer.count_tree`); after confirmation `transfer.delete_path`
removes them depth-first with `unlink`/`rmdir` (SFTP `REMOVE`/`RMDIR`).
Symlinks are removed as links and never followed, so nothing outside the
selected folders can be touched. `transfer.protected` refuses the root
and the home folder or any folder containing it, before anything is asked.
Deleting stops at the first error of an item and can be cancelled; what was
removed until then stays removed.

### Editing files

`editors.py` finds the text editors the way desktops find applications:
the `.desktop` files in `$XDG_DATA_HOME/applications` and every
`$XDG_DATA_DIRS/*/applications` (which includes Flatpak exports), the user's
own entry winning. An entry is an editor when it opens `text/plain`, is in the
`TextEditor` or `IDE` category (LibreOffice and browsers also open text), is
not `NoDisplay` or `Terminal=true`, and its program exists. The desktop's
default is `xdg-mime query default text/plain`, deliberately not the default
for the file's own type: a `.sh` must not be run and an `.xml` must not open
in a browser. The command line is the entry's `Exec` with its field codes
expanded (`%f %F %u %U` → the file, `%i`, `%c`, `%k`); it is started with
`QProcess.startDetached`, never through a shell. Bunny Deck's own choice is
`editor` in `config.json`: `""` (the desktop's default),
`desktop:<id>` or `command:<command line>`.

`remoteedit.py` keeps one `EditSession` per remote file (keyed by the
connection and the server's `realpath`, so a symlink edits its target and is
never replaced by a file). Opening downloads the file into its own `0700`
folder `$XDG_CACHE_HOME/bunny-deck/edit/<connection>-<random>/<name>` (file
`0600`) and starts the editor; files over 20 MB and files with a NUL byte
in the first 8 KiB are only opened after a question. A
`QFileSystemWatcher` watches the file *and* its folder: many editors save by
renaming a new file over the old one, which ends the watch on the file. Events
are collected for 300 ms; the content's SHA-256 decides whether anything
changed. Then the server's `(mtime, size)` is compared with what it was after
the last download or upload: if it differs (or the file is gone) the user is
asked, with Cancel as default. The upload is the usual `upload()` (part file
renamed into place, the file's permissions kept) over the pool's connection,
which reconnects if needed. A save during an upload is handled after it.
On quit, copies the server has are removed; a copy with changes the server
lacks is kept, and closing the window asks first.

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
| Root badge, ping latency, stats popup | ✓ prompt recognition, stats parsing | ✓ local shell with a root prompt, fake `ping` and `ssh -G`, the stats script run locally | Real servers, `sudo -i` |
| Menu entry, icon, window association | `desktop-file-validate` | ✓ X11 `WM_CLASS`, `_KDE_NET_WM_DESKTOP_FILE` | Pinning to a task bar |
