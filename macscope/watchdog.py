"""root-Wächter: startet den verwalteten Agenten in der Sitzung des Zielbenutzers neu.

Läuft als LaunchDaemon alle 30 Sekunden (``StartInterval``), prüft einmal und
beendet sich. Er kann den Agenten nicht selbst *sein* — AppKit sieht nur die
eigene GUI-Sitzung —, sondern lädt bzw. startet den LaunchAgent dort nach,
falls der Benutzer ihn per ``launchctl bootout`` oder Prozess-Beenden entfernt hat.
"""
from __future__ import annotations

import os
import pwd
import subprocess
import sys
import time

from . import managed

LAUNCHCTL = "/bin/launchctl"


def _launchctl(*args):
    return subprocess.run([LAUNCHCTL, *args], capture_output=True, text=True, timeout=20)


def check(config, run=_launchctl, agent_plist=managed.AGENT_PLIST):
    """Ein Prüfdurchgang. Gibt eine Beschreibung der Aktion zurück oder ``None``."""
    try:
        uid = pwd.getpwnam(config.target_user).pw_uid
    except KeyError:
        return f"Zielbenutzer {config.target_user} existiert nicht"
    domain = f"gui/{uid}"
    if run("print", domain).returncode != 0:
        return None  # nicht angemeldet: nichts zu tun
    service = run("print", f"{domain}/{managed.AGENT_LABEL}")
    if service.returncode != 0:
        result = run("bootstrap", domain, str(agent_plist))
        if result.returncode != 0:
            return f"Agent fehlte, Nachladen fehlgeschlagen: {(result.stderr or '').strip()}"
        return "Agent fehlte (entladen) und wurde nachgeladen"
    if "state = running" not in service.stdout:
        result = run("kickstart", f"{domain}/{managed.AGENT_LABEL}")
        if result.returncode != 0:
            return f"Agent lief nicht, Start fehlgeschlagen: {(result.stderr or '').strip()}"
        return "Agent lief nicht und wurde gestartet"
    return None


def main():
    if os.geteuid() != 0:
        raise SystemExit("Der Wächter läuft nur als root (LaunchDaemon).")
    try:
        config = managed.load()
    except managed.ManagedConfigError as error:
        print(time.strftime("%Y-%m-%d %H:%M:%S"), "FEHLER Konfiguration:", error, flush=True)
        raise SystemExit(1) from error
    if config is None:
        return  # nicht verwaltet: Wächter ist untätig
    message = check(config)
    if message:
        print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


if __name__ == "__main__":
    sys.exit(main())
