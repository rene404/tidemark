"""Viewer-Naht, Schicht 1 (Sitzung): austauschbare Live-Bild-Backends.

Phase A: ``AppleBackend`` verweist auf Apples Bildschirmfreigabe (externer Viewer,
Bild und Eingabe laufen nicht durch macscope). Ab B1 kommt ein eingebettetes
Backend mit eigenem Strom hinzu; das Dashboard unterscheidet nur nach ``kind``.
Siehe docs/VIEWER-NAHT.md.
"""
from __future__ import annotations

import re
import socket
import threading
import time

MODES = ("view", "control")
# Nur Hostname/IPv4 (Tailscale-MagicDNS-Name oder 100.x.y.z): keine Benutzer, Ports, Pfade.
HOST_PATTERN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,62})(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}))*\Z", re.ASCII)


def valid_host(host) -> bool:
    return isinstance(host, str) and len(host) <= 253 and bool(HOST_PATTERN.fullmatch(host))


def screen_sharing_listening(port: int = 5900, timeout: float = 0.3) -> bool:
    """Apples Bildschirmfreigabe nimmt lokal Verbindungen an (Port offen)."""
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


class AppleBackend:
    """Phase A: Apple-Bildschirmfreigabe. Ansehen/Steuern wählt man in Apples App,
    tidemark kann den Modus daher nur protokollieren, nicht erzwingen."""

    name = "apple"

    def __init__(self, host=None, probe=screen_sharing_listening, cache_seconds=30.0, clock=time.monotonic):
        if host is not None and not valid_host(host):
            raise ValueError("Ungültiger Viewer-Host (nur Hostname oder IP).")
        self.host = host
        self._probe, self._cache_seconds, self._clock = probe, cache_seconds, clock
        self._lock = threading.Lock()
        self._checked_at, self._listening = None, False

    def _available(self):
        with self._lock:
            now = self._clock()
            if self._checked_at is None or now - self._checked_at >= self._cache_seconds:
                self._listening, self._checked_at = bool(self._probe()), now
            return self._listening

    def capabilities(self):
        if not self.host:
            reason = "Kein Viewer-Host konfiguriert (Tailscale-Name des Macs)."
        elif not self._available():
            reason = "Bildschirmfreigabe ist aus. Als Admin unter Systemeinstellungen → Allgemein → Freigaben einschalten."
        else:
            reason = None
        return {"backend": self.name, "kind": "external", "available": reason is None, "reason": reason,
                "modes": list(MODES), "modes_enforced": False, "pre_login": True}

    def open_session(self, mode):
        return {"kind": "external", "url": f"vnc://{self.host}"}


class DemoBackend:
    """Simulation für ``--demo`` und Tests: öffnet nichts."""

    name = "demo"

    def capabilities(self):
        return {"backend": self.name, "kind": "external", "available": True, "reason": None,
                "modes": list(MODES), "modes_enforced": False, "pre_login": False}

    def open_session(self, mode):
        return {"kind": "external", "url": None, "demo": True}


class NoBackend:
    name = "none"

    def capabilities(self):
        return {"backend": self.name, "kind": "none", "available": False,
                "reason": "Kein Live-Bild konfiguriert.", "modes": [], "modes_enforced": False, "pre_login": False}

    def open_session(self, mode):  # pragma: no cover - durch available=False nie erreicht
        raise RuntimeError("Kein Live-Bild konfiguriert.")
