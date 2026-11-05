# Bunny Deck

A lightweight, native SSH and SFTP manager for the Linux desktop, built with
Python 3, Qt 6 (PyQt6), QTermWidget and the system OpenSSH. It is made with
KDE Plasma in mind and works on other desktops too.

![Bunny Deck with a local terminal on the left and two SSH terminals with their own background colours on the right](docs/screenshot.png)

## Features

- **Real terminals** (QTermWidget, the terminal widget of QTerminal) in one window, side by side or stacked, with draggable dividers and free arrangement (for example one large terminal with two stacked beside it).
- **Saved connections**: name, host or `~/.ssh/config` alias, user and port. Any number of sessions per connection.
- **Broadcast**: type a command once and run it in several terminals at the same time, for example an update on five servers.
- **Integrated SFTP file manager** for each session, with two panes: Local ↔ Remote and Remote ↔ Remote, with drag and drop.
- **Edit server files in your own editor**: double-click a file on a server and it opens in Kate, Zed, VS Code or whichever editor you use; every save goes straight back to the server.
- **Your SSH setup just works**: keys, ssh-agent, `~/.ssh/config` and `known_hosts` are used exactly as on the command line, because Bunny Deck runs the system `ssh`. Passwords are asked when needed and never stored.
- **Key login in a few clicks**: Bunny Deck can create an SSH key, add it to a server, check that it works and add the server to `~/.ssh/config` — and import the hosts you already have there.
- **One connection per server**: terminals and the file manager share it, so you log in once, and further terminals and the file manager open instantly.
- **Network scan**: a live map of the devices on your network (router, servers, NAS, printers, phones…) with their open ports, found with `nmap` (install it separately on existing installs, see [Updating](#updating)). Double-click an SSH server to connect to it.
- **AI menu**: your own list of command line AI assistants (for example `claude`, `codex` or `opencode`), each opened in its own terminal with one click.
- **Backgrounds**: per connection colour, transparency and an optional background image. A Bunny Deck badge to use as one is in `data/backgrounds/`.

## Installation

Bunny Deck runs from a cloned copy of this repository. It contains a small
Python binding for QTermWidget, because distributions ship QTermWidget
without one; `install.sh` compiles it against the installed library.

### Supported distributions

| Distribution | Status |
|---|---|
| openSUSE Tumbleweed (KDE Plasma) | Tested — developed on it and used every day |
| Ubuntu 26.04 LTS (GNOME) | Tested — used every day |
| Fedora 43 and newer | Supported; covered by the automated tests in CI |
| Arch Linux (and derivatives) | Supported; covered by the automated tests in CI |
| Debian 13 (trixie) and newer | Supported; covered by the automated tests in CI |

If you use Bunny Deck on Fedora, Arch or Debian, a short note in an issue
saying it works (or what did not) is very welcome.

Older releases (for example Ubuntu 24.04 or Debian 12) ship QTermWidget only
for Qt 5 and are not supported.

### Steps

```bash
git clone https://gitlab.com/zeronulla-group/bunny_deck.git
cd bunny_deck
./install-deps.sh     # installs the packages below (asks for sudo)
./install.sh          # builds the binding, adds the app to your application menu
```

Then start **Bunny Deck** from the application menu, or run `./bunny-deck`
from the project directory.

`install.sh` writes only two files outside the project, and `./uninstall.sh`
removes them again. Your settings in `~/.config/bunny-deck/` are kept.

- `~/.local/share/applications/org.bunnydeck.BunnyDeck.desktop`
- `~/.local/share/icons/hicolor/scalable/apps/bunny-deck.svg`

Because the menu entry points to the cloned directory, keep that directory
where it is (or run `./install.sh` again after moving it).

### Packages

`./install-deps.sh --print` shows the exact command for your system without
running it. These are the packages it installs:

| Distribution | Packages |
|---|---|
| openSUSE Tumbleweed | `python313-PyQt6` `libqtermwidget6-2` `qtermwidget-data` `openssh-clients` `nmap` `python313-devel` `python313-PyQt6-devel` `python313-pyqt-builder` `python313-sip6-devel` `qtermwidget-devel` `qt6-base-common-devel` `gcc-c++` `make` `pkg-config` |
| Fedora | `python3-pyqt6` `qtermwidget` `openssh-clients` `nmap` `python3-devel` `python3-pyqt6-devel` `PyQt-builder` `sip6` `qtermwidget-devel` `qt6-qtbase-devel` `gcc-c++` `make` `pkgconf-pkg-config` |
| Arch Linux | `python-pyqt6` `qtermwidget` `openssh` `nmap` `sip` `pyqt-builder` `qt6-base` `base-devel` `pkgconf` |
| Debian | `python3-pyqt6` `libqtermwidget6-2` `openssh-client` `nmap` `python3-dev` `pyqt6-dev` `sip-tools` `python3-pyqtbuild` `libqtermwidget-dev` `qt6-base-dev` `qmake6` `build-essential` `pkg-config` |
| Ubuntu | as Debian, with `libqtermwidget6-2-dev` instead of `libqtermwidget-dev` |

On openSUSE, the `python313-` prefix follows your Python version; the script
picks the right one.

On another distribution, install the equivalents by hand: PyQt6 including its
`.sip` files, QTermWidget 2 for Qt 6 (library and headers), SIP 6
(`sip-build`), PyQt-builder, Qt 6 `qmake`, a C++ compiler, `make` and the
OpenSSH client; `nmap` for the network scan (optional). Then run `./install.sh`.

### Updating

```bash
git pull
./build-native.sh     # only needed if the app says the binding is out of date
```

After a system update that changes QTermWidget's or PyQt6's version, Bunny
Deck stops with a message asking you to run `./build-native.sh` again.

**Updating to the version with the network scan:** it needs `nmap`, which
older installs do not have. Run `./install-deps.sh` again, or install it
directly:

| Distribution | Command |
|---|---|
| openSUSE | `sudo zypper install nmap` |
| Fedora | `sudo dnf install nmap` |
| Arch Linux | `sudo pacman -S nmap` |
| Debian / Ubuntu | `sudo apt install nmap` |

Everything else works without it; the Network Scan window shows the command
for your system until `nmap` is installed.

## Using it

When no terminal is open, the **start screen** offers the main actions as big buttons (new SSH connection, local terminal, network scan, import from `~/.ssh/config`) and your most recently opened connections.

The **Connections sidebar** on the left lists the local terminal and every saved connection with its colour (taken from its background). A green number shows how many terminals of a connection are open. Type in **Search…** to filter by name, user or host; Enter opens the first match. Double-click a row (or select it and press Enter) to open a terminal; right-click for key login setup and Remove. The first toolbar button or Ctrl+Shift+S hides and shows the sidebar; Bunny Deck remembers your choice.

- **New SSH** (the blue button): enter a name, host (an IP, a hostname or a `~/.ssh/config` alias), an optional username, and a port (default 22).
  The terminal opens immediately. If you tick *Save*, the profile goes into **Saved**.
The other toolbar buttons are icons; hover over one to see its name and shortcut.

- **Terminal icon**: a local shell on this computer.
- **Saved ▾**: opens a new terminal for the chosen profile. The same profile can have any number of sessions. The menu also has **Import from ~/.ssh/config…**, **Set up key login** and **Remove** (pick a saved profile and confirm; open sessions keep running).
  - **Import from ~/.ssh/config…** lists the hosts defined there (with the user, address and port OpenSSH resolves) and adds the ones you tick. They are saved under their `~/.ssh/config` name, so all settings there keep applying.
  - **Set up key login ▸** (also in a terminal's right-click menu) switches a connection from passwords to an SSH key:
    1. creates `~/.ssh/id_ed25519` if you have no key yet (with an optional passphrase), or uses the existing one;
    2. adds the public key to the server's `~/.ssh/authorized_keys` (only once). With a terminal to that server open, no password is needed; otherwise it is asked once;
    3. logs in with only the key to prove it works;
    4. optionally adds a `Host` entry to `~/.ssh/config` (after a backup copy), so `ssh <name>` works in any terminal.

    It never changes the server's SSH settings: password logins stay allowed until you turn them off yourself.
- **Sparkles icon (AI)**: your own list of AI assistants. **Add…** asks for the command (for example `claude` or `opencode --continue`) and a name; **Remove** takes one off the list.
  Choosing one runs its command in a new local terminal, in your shell with `~/.bashrc` loaded; when it exits, the shell stays open.
- **Broadcast icon** (or Ctrl+Shift+B): each terminal gets a tick box; tick the ones that should take part. What you type in a ticked terminal — keys, Enter, Ctrl+C, pastes — goes to every ticked terminal.
  Nothing is ticked when you turn it on. Ticked terminals have an orange frame, and the button turns orange and shows how many there are.
  Broadcast is always off when Bunny Deck starts.
- **Radar icon (Scan network)** (or Ctrl+Shift+M): opens a map of your network. Your network is filled in; press **Scan**.
  Devices pop up around the router as `nmap` finds them, then their open ports are checked. The icon and colour show what a device seems to be (SSH server, NAS, printer, router, Windows computer, camera, phone…); a green `>_` badge means you can SSH into it.
  Click a device for its address, manufacturer and open ports; **Connect with SSH…** (or a double-click) opens the Add SSH dialog filled in, **Open in browser** opens its web interface.
  `nmap` runs as your user, never as root, and only on the network you choose. Only scan networks you own or are allowed to scan.
- **Layout buttons** (middle): the left one puts terminals side by side, the right one stacks them top to bottom.
  You can drag the dividers. Adding or closing a terminal re-balances the layout.
- **Keyboard icon** (top right): the list of keyboard shortcuts.
- **Terminal header**: shows the name and user@host. The dot is green while connected; other states (connecting, disconnected) are also written out. The terminal you are typing in has a thin line over its header in the connection's colour.
  A red **root** badge appears next to the name while you are logged in as root, also after `sudo -i` or `su -` (recognised from the prompt, e.g. `root@host:~#`). On the right, SSH terminals show the **ping** to the server (green, orange or red) next to a small chart icon; it is an ordinary ping from your computer, so it stays hidden for servers that do not answer ping or are reached through a ProxyJump.
  **Click the chart icon** for the computer's live stats: CPU, memory, swap, load, disks, the processes using the most memory and a graph of the last few minutes of ping. They are read over the terminal's existing connection (no extra login) every 2 seconds while the popup is open, and only on Linux servers.
  When a session ends (the server went away, you typed `exit`, the network dropped), a blue **Reconnect** button appears in its header (**Restart** for a local terminal); click it or press **Enter** in the terminal, and a new session opens in exactly the same place, with the same background and font size. In the sidebar the connection's count badge turns red, and right-clicking it offers **Reconnect** too (for a session that still runs but hangs, it asks first and then replaces it).
  Move the mouse over a header to show its buttons: **Move** (arrows) and **Background** (picture). **✕** closes the terminal. Right-click in a terminal for Copy, Paste and **Files for this connection**.
- **Snap layouts**, as in Windows 11: click **Move** (the arrows in a terminal's header), the layout button in the toolbar (next to the two split buttons) or press Ctrl+Shift+L. A popup shows layouts (two or three columns, one big terminal with two or three beside it, a grid, …); click the part of a layout where the terminal should go, and the other terminals fill the rest.
- **Drag a terminal by its header** (the empty part of it; it lights up blue while you hold it) onto another one: a blue preview shows where it will land. Near an edge it goes beside (left, right, above, below); in the middle the two swap places.
- **Move → Next to a terminal…** places the terminal left of, right of, above or below another terminal, so you can build any mix of rows and columns.
  Sessions keep running; nothing reconnects. The layout buttons re-arrange all terminals uniformly again.
- **Copy and paste**: Ctrl+Shift+C / Ctrl+Shift+V, Shift+Insert, middle-click, or the right-click menu.
- **Files** (top right in the toolbar, or Ctrl+Shift+F) opens the file manager inside the main window, with the current terminal's computer on the right. The terminals keep running; switch back with **Files** again or **Back to terminals**.
  - Each side has its own source (This computer, or any open or saved connection, with its colour), Up, Home, the folder as clickable parts (click the free space after them to type a path; Esc goes back), Reload, and icons for hidden files, New Folder and Delete.
  - Files show the icon of their type from your desktop's icon theme; dates read like *Today 14:32*.
  - The round **→** and **←** buttons in the middle copy the selected files into the other pane's current folder. You can also drag files between panes, or from your file manager into a pane.
  - An existing target is never silently replaced: you choose Replace, Skip, Replace All or Cancel.
  - **Folders** are copied with everything in them, in every direction (also Remote ↔ Remote). If the folder already exists in the target, you are asked once whether to copy into it; files that exist in both are then asked about as above.
  - Symbolic links and special files (pipes, sockets, devices) inside folders are not copied; the summary says how many were left out.
  - **Edit a file**: double-click a text file (or right-click → **Edit**) to open it in your editor. A file on a server is downloaded into a private folder, and **every time you save it in the editor it is uploaded again** — the status bar says *Saved nginx.conf to Ubuntu*. If the file was changed (or deleted) on the server since you opened it, Bunny Deck asks before replacing it. A symbolic link edits the file it points to, and the server file keeps its permissions. Opening the same file again shows the copy that is already open. Files on this computer open where they are.
    - Double-clicking other files: on this computer they open in their usual application; on a server, right-click → **Edit** opens them anyway (after a warning if they do not look like text).
    - **Which editor**: by default the one your desktop uses for text files (KDE: *System Settings → Default Applications / File Associations*). Right-click → **Default Editor ▸** chooses another one for Bunny Deck only: it lists the text editors installed on this computer (Kate, Zed, VSCodium, gedit…, also Flatpaks) and **Custom Command…** for anything else, for example `konsole -e nvim` (the file is added at the end, or where you put `%f`). **Edit With ▸** uses another editor just once.
    - The local copies are in `~/.cache/bunny-deck/edit/` and are removed when Bunny Deck quits. A copy whose last changes did not reach the server is kept, and quitting asks first.
  - **Delete** (the bin icon or the Delete key) removes the selected files and folders, folders with everything in them. It always asks first, showing how many files and folders will go; **Cancel** is the default. Deleting is permanent (there is no trash). A symbolic link is removed itself, never what it points to, and the root or your home folder (or a folder containing it) is never deleted.
- **Background**: pick a background colour, opacity and an optional image (Fill, Fit, Stretch or Center) for the profile.
  The change applies live to every session of that profile. **Save** stores it; **Reset to Default** restores the default.
  The chosen image is copied to `~/.config/bunny-deck/backgrounds/`, so it stays even if the original is moved.

### Keyboard shortcuts

New terminals, closing, the file manager, moving between terminals
(`Alt+arrows`, `Ctrl+Tab`) and the font size (`Ctrl++`, `Ctrl+-`, `Ctrl+0`)
all have shortcuts. See [docs/SHORTCUTS.md](docs/SHORTCUTS.md), or press
`Ctrl+Shift+?` in the app.

### Session

When you quit, Bunny Deck remembers which terminals were open and how they
were arranged (including the divider positions and the active terminal),
and opens them again on the next start; SSH terminals reconnect. Without a
saved session it starts with one local terminal. Broadcast, font zoom and
the file manager are not restored. Connections or AI tools you removed in
the meantime are left out.

Settings are stored in `~/.config/bunny-deck/config.json`, the session in
`session.json` next to it. Neither ever contains passwords.

## Security

- Bunny Deck uses the system OpenSSH for everything; it has no SSH implementation of its own.
- Host-key checking stays on. Unknown keys are shown for confirmation; a *changed* key is refused.
- Passwords and passphrases are typed into the terminal, or into a dialog for the file manager. They never appear in command lines, environment variables, logs or files, and are not cached.

Details are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Known limitations

- Editing a file uploads it with your own login: files you could only change with `sudo` (for example in `/etc`) cannot be saved this way.
- Synchronisation and transfer queues are not implemented. Symbolic links inside copied folders are left out.
- The shared connection is started by a terminal. If you open the file manager for a server without a terminal to it, it connects (and asks for a password) on its own.
- The terminal header cannot tell a failed connection from a closed one (QTermWidget does not report the exit code).
- The network scan runs without root, so it uses TCP connect scans and checks a list of about 40 common ports, not all 65535. Devices that ignore pings are still found on your own network segment (through the kernel's ARP table), but not behind a router.
- Window transparency needs a compositing desktop (KDE Plasma, GNOME and most Wayland desktops have one).

## Development

```bash
./install-deps.sh --tests     # also installs Xvfb, sshd and D-Bus for the tests
tests/run_all.sh              # unit tests, then GUI tests on a hidden Xvfb display
```

GUI tests run the real application on a private Xvfb display with a
throwaway configuration. They never open windows on your desktop or touch
`~/.ssh`. How the app is put together is described in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Contributing

Improvements are welcome. If you build something good on top of Bunny Deck,
please open a merge request or an issue so it can be seen — see
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

Bunny Deck is free software under the [GNU General Public License v3.0 or
later](LICENSE). You may use, study, change and share it. If you share a
modified version, share its source code under the same license.
