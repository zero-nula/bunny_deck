# Bunny Deck – keyboard shortcuts

Press `Ctrl+Shift+?` in Bunny Deck to see this list in the app.

Apart from the font size, every shortcut uses `Ctrl+Shift` or `Alt` with
an arrow key. The usual `Ctrl` keys (`Ctrl+C`, `Ctrl+R`, `Ctrl+W`, …)
therefore still reach the programs running in the terminal.

## Terminals

| Keys | Action |
|---|---|
| `Ctrl+Shift+T` | Open a new local terminal |
| `Ctrl+Shift+N` | Open a new SSH connection (the Add SSH dialog) |
| `Ctrl+Shift+O` | Open the Saved Connections menu; pick one with the arrow keys and Enter |
| `Ctrl+Shift+W` | Close the current terminal (asks first if its session is still running) |
| `Ctrl+Shift+F` | Open the file manager for the current terminal; press again to go back |
| `Ctrl+Shift+B` | Turn Broadcast on or off (type into several terminals at once) |
| `Ctrl+Shift+M` | Open the Network Scan window (a map of the devices on your network) |
| `Ctrl+Shift+S` | Show or hide the connections sidebar |
| `Ctrl+Shift+L` | Snap layouts: choose where the current terminal goes |

## Moving between terminals

| Keys | Action |
|---|---|
| `Alt+←` | Go to the terminal on the left |
| `Alt+→` | Go to the terminal on the right |
| `Alt+↑` | Go to the terminal above |
| `Alt+↓` | Go to the terminal below |
| `Ctrl+Tab` | Go to the next terminal |
| `Ctrl+Shift+Tab` | Go to the previous terminal |

`Ctrl+Tab` and `Ctrl+Shift+Tab` go through the terminals from top left to
bottom right and wrap around at the end.

## Font size

These change only the current terminal, and only until it is closed.

| Keys | Action |
|---|---|
| `Ctrl++` (or `Ctrl+=`) | Larger font |
| `Ctrl+-` | Smaller font |
| `Ctrl+0` | Default font size |

## Copy and paste

| Keys | Action |
|---|---|
| `Ctrl+Shift+C` | Copy the selected text |
| `Ctrl+Shift+V` or `Shift+Insert` | Paste |
| Middle mouse button | Paste the last selected text |

## File manager

| Keys | Action |
|---|---|
| `Delete` | Delete the selected files and folders (always asks first) |
| `Ctrl+Shift+F` | Back to the terminals |

## Help

| Keys | Action |
|---|---|
| `Ctrl+Shift+?` | Show the list of shortcuts |

## Good to know

- Bunny Deck catches these keys before the program in the terminal sees
  them. If a program you use needs one of them (for example `Alt+←` in zsh,
  vim or tmux), it no longer receives it inside Bunny Deck.
- With Broadcast on, these shortcuts are not sent to the other terminals;
  only what you type is.
