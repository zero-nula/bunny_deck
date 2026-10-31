# Contributing to Bunny Deck

Thanks for your interest! Bunny Deck is free software: you can use it,
change it and build on it without asking anyone.

## Show what you built

If you make something good with Bunny Deck — a fix, a new feature, a nicer
background, support for another distribution — it would be great to see it.
Please:

- open a **merge request** if you would like the change to be part of Bunny Deck, or
- open an **issue** with a link to your fork if you just want to share what you made.

Both are welcome, also for work in progress. Nothing needs to be approved
for you to use or share your own version.

## Reporting a problem

Open an issue and include:

- your distribution and version, and your desktop (for example KDE Plasma 6 on Wayland),
- what you did, what you expected and what happened,
- any error message, for example from running `./bunny-deck` in a terminal.

Please do not post passwords, private keys or host names you want to keep private.

## Working on the code

```bash
./install-deps.sh --tests   # build, run and test dependencies
./build-native.sh           # the QTermWidget binding
./bunny-deck                # run from the project directory
tests/run_all.sh            # all tests; GUI tests run on a hidden Xvfb display
```

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) explains how the parts fit together.

Guidelines that keep the project easy to work on:

- Keep the style of the surrounding code: small functions, short comments that explain *why*.
- Add or update tests for behaviour you change. GUI behaviour is tested in `tests/gui_*.py`.
- Run `tests/run_all.sh` before opening a merge request. CI runs it on openSUSE, Fedora, Arch, Debian and Ubuntu.
- Bunny Deck relies on the system OpenSSH and must never store passwords. Changes that touch authentication or file transfers need particular care.
- Avoid new dependencies where the standard library, Qt or OpenSSH already do the job.
- Update `README.md` when you change what users see.

## License

By contributing, you agree that your contribution is licensed under the
[GNU General Public License v3.0 or later](LICENSE), like the rest of
Bunny Deck.
