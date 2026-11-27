"""root-Wächter: startet den verwalteten Agenten in der Sitzung des Zielbenutzers neu.

Läuft als LaunchDaemon alle 30 Sekunden (``StartInterval``), prüft einmal und
beendet sich. Er kann den Agenten nicht selbst *sein* — AppKit sieht nur die
eigene GUI-Sitzung —, sondern lädt bzw. startet den LaunchAgent dort nach,
falls der Benutzer ihn per ``launchctl bootout`` oder Prozess-Beenden entfernt hat.
"""
from __future__ import annotations

import json
import os
import pwd
import re
import signal
import subprocess
import sys
import time

from . import managed

LAUNCHCTL = "/bin/launchctl"


def _launchctl(*args):
    return subprocess.run([LAUNCHCTL, *args], capture_output=True, text=True, timeout=20)


def heartbeat_age(home, now=time.time):
    """Alter des Lebenszeichens in Sekunden, ``None`` wenn es fehlt. Folgt keinem Symlink."""
    path = managed.user_data_directory(home) / managed.HEARTBEAT_NAME
    try:
        return max(0.0, now() - os.lstat(path).st_mtime)
    except OSError:
        return None


def _pid(output):
    match = re.search(r"^\s*pid = (\d+)$", output, re.MULTILINE)
    return int(match.group(1)) if match else None


def check(config, run=_launchctl, agent_plist=managed.AGENT_PLIST, state=None,
          heartbeat=heartbeat_age, stale_after=managed.HEARTBEAT_STALE_AFTER, resume=None):
    """Ein Prüfdurchgang. Gibt eine Beschreibung der Aktion zurück oder ``None``.

    ``state`` (dict) überdauert zwischen den Durchgängen: Ein veraltetes Lebenszeichen
    führt erst beim **zweiten** Durchgang in Folge zum Neustart, damit ein gerade
    aufgewachter Mac (Lebenszeichen ruhte während des Schlafs) nicht neu gestartet wird.
    """
    state = {} if state is None else state
    resume = resume or (lambda pid: os.kill(pid, signal.SIGCONT))
    try:
        account = pwd.getpwnam(config.target_user)
    except KeyError:
        return f"Zielbenutzer {config.target_user} existiert nicht"
    domain = f"gui/{account.pw_uid}"
    if run("print", domain).returncode != 0:
        state.clear()
        return None  # nicht angemeldet: nichts zu tun
    target = f"{domain}/{managed.AGENT_LABEL}"
    service = run("print", target)
    if service.returncode != 0:
        state.clear()
        result = run("bootstrap", domain, str(agent_plist))
        if result.returncode != 0:
            return f"Agent fehlte, Nachladen fehlgeschlagen: {(result.stderr or '').strip()}"
        return "Agent fehlte (entladen) und wurde nachgeladen"
    if "state = running" not in service.stdout:
        state.clear()
        result = run("kickstart", target)
        if result.returncode != 0:
            return f"Agent lief nicht, Start fehlgeschlagen: {(result.stderr or '').strip()}"
        return "Agent lief nicht und wurde gestartet"
    age = heartbeat(account.pw_dir)
    if age is not None and age <= stale_after:
        state.clear()
        return None
    if not state.get("stale"):
        state["stale"] = True  # erst beobachten (z. B. direkt nach dem Aufwachen)
        return None
    state.clear()
    pid = _pid(service.stdout)
    if pid:
        try:
            resume(pid)  # angehaltenen Prozess fortsetzen, damit er beendet werden kann
        except OSError:
            pass
    result = run("kickstart", "-k", target)
    reason = "fehlt" if age is None else f"seit {int(age)} s veraltet"
    if result.returncode != 0:
        return f"Lebenszeichen {reason}, Neustart fehlgeschlagen: {(result.stderr or '').strip()}"
    return f"Lebenszeichen {reason}: Agent hing oder war angehalten und wurde neu gestartet"


def _load_state(path):
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(path, state):
    try:
        temporary = path.with_name(path.name + ".neu")
        temporary.write_text(json.dumps(state))
        temporary.chmod(0o644)
        temporary.replace(path)
    except OSError as error:
        print(time.strftime("%Y-%m-%d %H:%M:%S"), "Zustand nicht speicherbar:", error, flush=True)


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
    state = _load_state(managed.WATCHDOG_STATE)
    message = check(config, state=state)
    _save_state(managed.WATCHDOG_STATE, state)
    if message:
        print(time.strftime("%Y-%m-%d %H:%M:%S"), message, flush=True)


if __name__ == "__main__":
    sys.exit(main())
