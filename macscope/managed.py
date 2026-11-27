"""Verwalteter Modus: systemweite, root-eigene Konfiguration.

Liegt die Datei ``/Library/Application Support/MacScope/managed.json`` vor, läuft
der Agent verwaltet: kein Pausieren, kein Beenden über die Menüleiste, Einstellungen
nur aus dieser Datei. Die Datei und jeder Ordner darüber müssen root gehören und
dürfen für Gruppe/Andere nicht beschreibbar sein — sonst startet der Agent nicht,
statt still unverwaltet zu laufen.
"""
from __future__ import annotations

import json
import plistlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from .viewer import valid_host

SYSTEM_DIRECTORY = Path("/Library/Application Support/MacScope")
CONFIG_PATH = SYSTEM_DIRECTORY / "managed.json"
AGENT_LABEL = "local.macscope.managed-agent"
WATCHDOG_LABEL = "local.macscope.watchdog"
AGENT_PLIST = Path("/Library/LaunchAgents") / (AGENT_LABEL + ".plist")
WATCHDOG_PLIST = Path("/Library/LaunchDaemons") / (WATCHDOG_LABEL + ".plist")
# Exit code for "verwalteter Agent wurde beendet": launchd startet ihn neu.
EXIT_RESTART = 75

_KEYS = {"target_user", "allow_apps", "retention_days", "port", "viewer_host"}


class ManagedConfigError(Exception):
    pass


@dataclass(frozen=True)
class ManagedConfig:
    target_user: str
    allow_apps: tuple = ("org.mozilla.firefox",)
    retention_days: int = 30
    port: int = 8765
    viewer_host: str | None = None


def _check_secure(path: Path, owner: int):
    """Datei und alle Elternordner gehören root (in Tests: ``owner``), nicht von Gruppe/Anderen beschreibbar.

    Die Datei selbst darf kein Symlink sein. Symlinks in Elternordnern (z. B. /var → /private/var)
    müssen root gehören; geprüft wird zusätzlich die aufgelöste Ordnerkette.
    """
    try:
        info = os.lstat(path)
    except FileNotFoundError as error:
        raise ManagedConfigError(f"{path} fehlt.") from error
    if stat.S_ISLNK(info.st_mode):
        raise ManagedConfigError(f"{path} darf kein Symlink sein.")
    for current in Path(path).parents:  # lexikalische Kette: Symlinks nur von root
        link = os.lstat(current)
        if stat.S_ISLNK(link.st_mode) and link.st_uid != 0:
            raise ManagedConfigError(f"{current} ist ein Symlink, der nicht root gehört.")
    for current in [Path(path), *Path(path).parent.resolve().parents, Path(path).parent.resolve()]:
        info = os.stat(current)
        if info.st_uid not in (owner, 0):
            raise ManagedConfigError(f"{current} muss root gehören.")
        if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            raise ManagedConfigError(f"{current} darf nur für root beschreibbar sein.")


def parse(data) -> ManagedConfig:
    if not isinstance(data, dict):
        raise ManagedConfigError("managed.json muss ein JSON-Objekt sein.")
    unknown = set(data) - _KEYS
    if unknown:
        raise ManagedConfigError("Unbekannte Schlüssel: " + ", ".join(sorted(unknown)))
    user = data.get("target_user")
    if not isinstance(user, str) or not user or user == "root":
        raise ManagedConfigError("target_user muss ein normaler Benutzername sein.")
    apps = data.get("allow_apps", ["org.mozilla.firefox"])
    if not isinstance(apps, list) or not all(isinstance(app, str) and app for app in apps):
        raise ManagedConfigError("allow_apps muss eine Liste von Bundle-IDs sein.")
    retention = data.get("retention_days", 30)
    if type(retention) is not int or not 1 <= retention <= 3650:
        raise ManagedConfigError("retention_days muss 1–3650 sein.")
    port = data.get("port", 8765)
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ManagedConfigError("port muss 1024–65535 sein.")
    host = data.get("viewer_host")
    if host is not None and not valid_host(host):
        raise ManagedConfigError("viewer_host muss ein Hostname oder eine IP sein (Tailscale-Name).")
    return ManagedConfig(user, tuple(apps), retention, port, host)


def load(path: Path = CONFIG_PATH, owner: int = 0):
    """Return ``None`` if unmanaged, the config if managed; raise if present but unsafe."""
    if not os.path.lexists(path):
        return None
    _check_secure(Path(path), owner)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ManagedConfigError(f"managed.json ist nicht lesbar: {error}") from error
    return parse(data)


def agent_plist(python: Path) -> bytes:
    """LaunchAgent für alle GUI-Sitzungen; der Agent selbst startet nur beim Zielbenutzer."""
    return plistlib.dumps({
        "Label": AGENT_LABEL,
        # -I: keine Umgebungsvariablen/Benutzer-site-packages des Kontos einlesen.
        "ProgramArguments": [str(python), "-I", "-m", "macscope", "run"],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 10,
        "LimitLoadToSessionType": "Aqua",
        "ProcessType": "Interactive",
        "Umask": 0o077,
    })


def watchdog_plist(python: Path, interval: int = 30) -> bytes:
    """LaunchDaemon (root), prüft regelmäßig den Agenten in der Sitzung des Zielbenutzers."""
    return plistlib.dumps({
        "Label": WATCHDOG_LABEL,
        "ProgramArguments": [str(python), "-I", "-m", "macscope.watchdog"],
        "RunAtLoad": True,
        "StartInterval": interval,
        "StandardOutPath": "/Library/Logs/MacScope-watchdog.log",
        "StandardErrorPath": "/Library/Logs/MacScope-watchdog.log",
        "Umask": 0o022,
    })
