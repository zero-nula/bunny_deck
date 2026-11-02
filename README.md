# Bunny Deck

A lightweight, native SSH and SFTP manager for the Linux desktop, built with
Python 3, Qt 6 (PyQt6), QTermWidget and the system OpenSSH. It is made with
KDE Plasma in mind and works on other desktops too.

![Bunny Deck with a local terminal and an AI assistant side by side](docs/screenshot.png)

## Features

- **Real terminals** (QTermWidget, the terminal widget of QTerminal) in one window, side by side or stacked, with draggable dividers and free arrangement (for example one large terminal with two stacked beside it).
- **Saved connections**: name, host or `~/.ssh/config` alias, user and port. Any number of sessions per connection.
- **Broadcast**: type a command once and run it in several terminals at the same time, for example an update on five servers.
- **Integrated SFTP file manager** for each session, with two panes: Local ↔ Remote and Remote ↔ Remote, with drag and drop.
- **Your SSH setup just works**: keys, ssh-agent, `~/.ssh/config` and `known_hosts` are used exactly as on the command line, because Bunny Deck runs the system `ssh`. Passwords are asked when needed and never stored.
- **Key login in a few clicks**: Bunny Deck can create an SSH key, add it to a server, check that it works and add the server to `~/.ssh/config` — and import the hosts you already have there.
- **One connection per server**: terminals and the file manager share it, so you log in once, and further terminals and the file manager open instantly.
- **AI menu**: your own list of command line AI assistants (for example `claude`, `codex` or `opencode`), each opened in its own terminal with one click.
- **Backgrounds**: per connection colour, transparency and an optional background image. A Bunny Deck badge to use as one is in `data/backgrounds/`.

## Installation

Bunny Deck runs from a cloned copy of this repository. It contains a small
Python binding for QTermWidget, because distributions ship QTermWidget
without one; `install.sh` compiles it against the installed library.

### Supported distributions

| Distribution | Status |
|---|---|
| openSUSE Tumbleweed | Developed and used daily |
| Fedora 43 and newer | Built and tested in CI |
| Arch Linux (and derivatives) | Built and tested in CI |
| Debian 13 (trixie) and newer | Built and tested in CI |
| Ubuntu 26.04 and newer | Built and tested in CI |

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
| openSUSE Tumbleweed | `python313-PyQt6` `libqtermwidget6-2` `qtermwidget-data` `openssh-clients` `python313-PyQt6-devel` `python313-pyqt-builder` `python313-sip6-devel` `qtermwidget-devel` `qt6-base-common-devel` `gcc-c++` `make` `pkg-config` |
| Fedora | `python3-pyqt6` `qtermwidget` `openssh-clients` `python3-pyqt6-devel` `PyQt-builder` `sip6` `qtermwidget-devel` `qt6-qtbase-devel` `gcc-c++` `make` `pkgconf-pkg-config` |
| Arch Linux | `python-pyqt6` `qtermwidget` `openssh` `sip` `python-pyqt-builder` `qt6-base` `base-devel` `pkgconf` |
| Debian | `python3-pyqt6` `libqtermwidget6-2` `openssh-client` `pyqt6-dev` `sip-tools` `python3-pyqtbuild` `libqtermwidget-dev` `qt6-base-dev` `qmake6` `build-essential` `pkg-config` |
| Ubuntu | as Debian, with `libqtermwidget6-2-dev` instead of `libqtermwidget-dev` |

On openSUSE, the `python313-` prefix follows your Python version; the script
picks the right one.

On another distribution, install the equivalents by hand: PyQt6 including its
`.sip` files, QTermWidget 2 for Qt 6 (library and headers), SIP 6
(`sip-build`), PyQt-builder, Qt 6 `qmake`, a C++ compiler, `make` and the
OpenSSH client. Then run `./install.sh`.

### Updating

```bash
git pull
./build-native.sh     # only needed if the app says the binding is out of date
```

After a system update that changes QTermWidget's or PyQt6's version, Bunny
Deck stops with a message asking you to run `./build-native.sh` again.

## Using it

- **+ Add SSH**: enter a name, host (an IP, a hostname or a `~/.ssh/config` alias), an optional username, and a port (default 22).
  The terminal opens immediately. If you tick *Save*, the profile goes into **Saved Connections**.
- **Local terminal**: a shell on this computer.
- **Saved Connections ▾**: opens a new terminal for the chosen profile. The same profile can have any number of sessions.
  - **Import from ~/.ssh/config…** lists the hosts defined there (with the user, address and port OpenSSH resolves) and adds the ones you tick. They are saved under their `~/.ssh/config` name, so all settings there keep applying.
  - **Set up key login ▸** (also in a terminal's right-click menu) switches a connection from passwords to an SSH key:
    1. creates `~/.ssh/id_ed25519` if you have no key yet (with an optional passphrase), or uses the existing one;
    2. adds the public key to the server's `~/.ssh/authorized_keys` (only once). With a terminal to that server open, no password is needed; otherwise it is asked once;
    3. logs in with only the key to prove it works;
    4. optionally adds a `Host` entry to `~/.ssh/config` (after a backup copy), so `ssh <name>` works in any terminal.

    It never changes the server's SSH settings: password logins stay allowed until you turn them off yourself.
- **AI ▾**: your own list of AI assistants. **Add…** asks for the command (for example `claude` or `opencode --continue`) and a name; **Remove** takes one off the list.
  Choosing one runs its command in a new local terminal, in your shell with `~/.bashrc` loaded; when it exits, the shell stays open.
- **⇉ Broadcast** (or Ctrl+Shift+B): each terminal gets a tick box; tick the ones that should take part. What you type in a ticked terminal — keys, Enter, Ctrl+C, pastes — goes to every ticked terminal.
  Nothing is ticked when you turn it on. Ticked terminals have an orange frame, and the button shows how many there are.
  Broadcast is always off when Bunny Deck starts.
- **Vertical Split** puts terminals side by side. **Horizontal Split** stacks them top to bottom.
  You can drag the dividers. Adding or closing a terminal re-balances the layout.
- **Remove Connection** (top right): pick a saved profile and confirm to remove it. Open sessions keep running.
- **Terminal header**: shows the name, user@host and status, plus the **Move**, **SCP**, **Background** and **✕** buttons.
- **Move** places the terminal left of, right of, above or below another terminal, so you can build any mix of rows and columns.
  Sessions keep running; nothing reconnects. **Vertical Split** / **Horizontal Split** re-arrange all terminals uniformly again.
- **Copy and paste**: Ctrl+Shift+C / Ctrl+Shift+V, Shift+Insert, middle-click, or the right-click menu.
- **SCP** opens the file manager inside the main window. The terminals keep running; switch back with **← Terminals** or the **Files** toggle.
  - Each pane has its own source selector (Local or any open or saved connection), Up, Home, an editable path, Reload, New Folder and a hidden-files toggle.
  - **Copy →** and **← Copy** copy the selected files into the other pane's current folder. You can also drag files between panes, or from your file manager into a pane.
  - An existing target is never silently replaced: you choose Replace, Skip, Replace All or Cancel.
  - **Folders** are copied with everything in them, in every direction (also Remote ↔ Remote). If the folder already exists in the target, you are asked once whether to copy into it; files that exist in both are then asked about as above.
  - Symbolic links and special files (pipes, sockets, devices) inside folders are not copied; the summary says how many were left out.
  - **Delete** (button or the Delete key) removes the selected files and folders, folders with everything in them. It always asks first, showing how many files and folders will go; **Cancel** is the default. Deleting is permanent (there is no trash). A symbolic link is removed itself, never what it points to, and the root or your home folder (or a folder containing it) is never deleted.
- **Background**: pick a background colour, opacity and an optional image (Fill, Fit, Stretch or Center) for the profile.
  The change applies live to every session of that profile. **Save** stores it; **Reset to Default** restores the default.
  The chosen image is copied to `~/.config/bunny-deck/backgrounds/`, so it stays even if the original is moved.

### Keyboard shortcuts

New terminals, closing, the file manager, moving between terminals
(`Alt+arrows`, `Ctrl+Tab`) and the font size (`Ctrl++`, `Ctrl+-`, `Ctrl+0`)
all have shortcuts. See [docs/SHORTCUTS.md](docs/SHORTCUTS.md), or press
`Ctrl+Shift+?` in the app.

Settings are stored in `~/.config/bunny-deck/config.json`. It never contains passwords.

## Security

- Bunny Deck uses the system OpenSSH for everything; it has no SSH implementation of its own.
- Host-key checking stays on. Unknown keys are shown for confirmation; a *changed* key is refused.
- Passwords and passphrases are typed into the terminal, or into a dialog for the file manager. They never appear in command lines, environment variables, logs or files, and are not cached.

Details are in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Known limitations

- Synchronisation and transfer queues are not implemented. Symbolic links inside copied folders are left out.
- The shared connection is started by a terminal. If you open the file manager for a server without a terminal to it, it connects (and asks for a password) on its own.
- The terminal header cannot tell a failed connection from a closed one (QTermWidget does not report the exit code).
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
