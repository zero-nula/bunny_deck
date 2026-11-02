"""Profile and appearance persistence for Bunny Deck.

Stored as JSON in $XDG_CONFIG_HOME/bunny-deck/config.json. Passwords are
never part of this model.
"""

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field, asdict

APP_DIR_NAME = "bunny-deck"
DEFAULT_BACKGROUND = "#0e1013"
DEFAULT_OPACITY = 1.0

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

# How a background image is placed in the terminal (QTermWidget modes).
IMAGE_MODES = ("fill", "fit", "stretch", "center")
DEFAULT_IMAGE_MODE = "fill"

# Profile kinds. Profiles written before "kind" existed have none: they are SSH.
KIND_SSH = "ssh"
KIND_LOCAL = "local"
KINDS = (KIND_SSH, KIND_LOCAL)


def config_dir():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, APP_DIR_NAME)


def config_path():
    return os.path.join(config_dir(), "config.json")


def backgrounds_dir():
    return os.path.join(config_dir(), "backgrounds")


def store_background(path):
    """Copies a chosen background image into the config directory, so it
    survives the original being moved or deleted. Returns the stored path
    (unchanged if it is already stored, or if copying fails)."""
    if not path or os.path.dirname(os.path.abspath(path)) == backgrounds_dir():
        return path
    try:
        with open(path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()[:16]
        ext = os.path.splitext(path)[1].lower()
        target = os.path.join(backgrounds_dir(), digest + ext)
        if not os.path.exists(target):
            os.makedirs(backgrounds_dir(), mode=0o700, exist_ok=True)
            shutil.copyfile(path, target)
        return target
    except OSError as e:
        print(f"bunny-deck: could not store background image {path}: {e}")
        return path


@dataclass
class Appearance:
    background: str = DEFAULT_BACKGROUND
    opacity: float = DEFAULT_OPACITY
    image: str = ""
    image_mode: str = DEFAULT_IMAGE_MODE

    @classmethod
    def from_dict(cls, d):
        d = d or {}
        bg = d.get("background", DEFAULT_BACKGROUND)
        if not isinstance(bg, str) or not _HEX_RE.match(bg):
            bg = DEFAULT_BACKGROUND
        try:
            op = float(d.get("opacity", DEFAULT_OPACITY))
        except (TypeError, ValueError):
            op = DEFAULT_OPACITY
        image = d.get("image") or ""
        if not isinstance(image, str):
            image = ""
        mode = d.get("image_mode", DEFAULT_IMAGE_MODE)
        if mode not in IMAGE_MODES:
            mode = DEFAULT_IMAGE_MODE
        return cls(background=bg.lower(), opacity=min(1.0, max(0.0, op)),
                   image=image, image_mode=mode)

    def is_default(self):
        return self.background == DEFAULT_BACKGROUND and self.opacity == DEFAULT_OPACITY \
            and not self.image


@dataclass
class Profile:
    name: str
    host: str
    user: str = ""
    port: int = 22
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    appearance: Appearance = field(default_factory=Appearance)
    kind: str = KIND_SSH

    @property
    def is_local(self):
        return self.kind == KIND_LOCAL

    @property
    def target(self):
        """Human readable user@host[:port] ("local" for a local terminal)."""
        if self.is_local:
            return "local"
        s = f"{self.user}@{self.host}" if self.user else self.host
        return s if self.port == 22 else f"{s}:{self.port}"

    def ssh_args(self):
        """Connection arguments for OpenSSH (no command, no options that
        override the user's ~/.ssh/config unless explicitly set)."""
        args = []
        if self.port and self.port != 22:
            args += ["-p", str(self.port)]
        if self.user:
            args += ["-l", self.user]
        # "--" stops option parsing so a host can never be read as an option.
        args += ["--", self.host]
        return args

    def to_dict(self):
        d = asdict(self)
        d["appearance"] = asdict(self.appearance)
        return d

    @classmethod
    def from_dict(cls, d):
        kind = d.get("kind", KIND_SSH)
        if kind not in KINDS:
            kind = KIND_SSH
        try:
            port = int(d.get("port", 22) or 22)
        except (TypeError, ValueError):
            port = 22
        return cls(
            id=str(d.get("id") or uuid.uuid4().hex),
            name=str(d.get("name") or d.get("host") or "?"),
            # A local profile needs no host; an SSH profile without one is invalid.
            host=str(d.get("host") or "") if kind == KIND_LOCAL else str(d["host"]),
            user=str(d.get("user") or ""),
            port=port,
            appearance=Appearance.from_dict(d.get("appearance")),
            kind=kind,
        )


@dataclass
class AITool:
    """An AI assistant the user added to the AI menu: a shell command run in a
    local terminal (e.g. "claude" or "opencode")."""
    name: str
    command: str
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    appearance: Appearance = field(default_factory=Appearance)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        command = str(d["command"]).strip()
        if not command:
            raise ValueError("empty command")
        return cls(
            id=str(d.get("id") or uuid.uuid4().hex),
            name=str(d.get("name") or command),
            command=command,
            appearance=Appearance.from_dict(d.get("appearance")),
        )


def validate_host(host):
    host = host.strip()
    if not host:
        return "Hostname is required."
    if host.startswith("-") or any(c.isspace() for c in host):
        return "Hostname must not start with '-' or contain spaces."
    return None


def validate_user(user):
    user = user.strip()
    if user.startswith("-") or any(c.isspace() for c in user) or "@" in user:
        return "Username must not start with '-' or contain spaces or '@'."
    return None


class Config:
    def __init__(self, path=None):
        self.path = path or config_path()
        self.profiles = []
        self.ai_tools = []
        # Appearance shared by all local terminals ("Local terminal").
        self.local_appearance = Appearance()
        self.load()

    def load(self):
        self.profiles = []
        self.ai_tools = []
        self.local_appearance = Appearance()
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as e:
            # Keep the broken file untouched for the user; start empty.
            print(f"bunny-deck: could not read {self.path}: {e}")
            self._broken = True
            return
        for p in data.get("profiles", []):
            try:
                self.profiles.append(Profile.from_dict(p))
            except (KeyError, TypeError):
                continue
        if "local_appearance" in data:
            self.local_appearance = Appearance.from_dict(data["local_appearance"])
        for t in data.get("ai_tools", []):
            try:
                self.ai_tools.append(AITool.from_dict(t))
            except (KeyError, TypeError, ValueError):
                continue

    def save(self):
        if getattr(self, "_broken", False):
            # Do not overwrite a file we failed to parse.
            backup = self.path + ".broken"
            if not os.path.exists(backup):
                os.replace(self.path, backup)
            self._broken = False
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        data = {"version": 1, "profiles": [p.to_dict() for p in self.profiles],
                "ai_tools": [t.to_dict() for t in self.ai_tools],
                "local_appearance": asdict(self.local_appearance)}
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), prefix=".config-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def get(self, profile_id):
        for p in self.profiles:
            if p.id == profile_id:
                return p
        return None

    def add(self, profile):
        self.profiles.append(profile)
        self.save()
        return profile

    def remove(self, profile_id):
        """Removes only the profile with this id; returns it, or None."""
        p = self.get(profile_id)
        if p is None:
            return None
        self.profiles = [q for q in self.profiles if q.id != profile_id]
        self.save()
        return p

    def update_appearance(self, profile_id, appearance):
        p = self.get(profile_id)
        if p is None:
            return
        p.appearance = appearance
        self.save()

    def add_ai_tool(self, tool):
        self.ai_tools.append(tool)
        self.save()
        return tool

    def remove_ai_tool(self, tool_id):
        """Removes only the AI tool with this id; returns it, or None."""
        tool = next((t for t in self.ai_tools if t.id == tool_id), None)
        if tool is not None:
            self.ai_tools = [t for t in self.ai_tools if t.id != tool_id]
            self.save()
        return tool

    def update_local_appearance(self, appearance):
        self.local_appearance = appearance
        self.save()

    def update_ai_tool_appearance(self, tool_id, appearance):
        tool = next((t for t in self.ai_tools if t.id == tool_id), None)
        if tool is not None:
            tool.appearance = appearance
            self.save()
