"""Viewer-Naht, Schicht 1 (Sitzung): austauschbare Live-Bild-Backends.

Phase A: ``AppleBackend`` verweist auf Apples Bildschirmfreigabe (externer Viewer,
Bild und Eingabe laufen nicht durch macscope). Ab B1 kommt ein eingebettetes
Backend mit eigenem Strom hinzu; das Dashboard unterscheidet nur nach ``kind``.
Siehe docs/VIEWER-NAHT.md.
"""
from __future__ import annotations

import re
import socket
import subprocess
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


def parse_peers(output: str, port: int = 5900) -> set:
    """Gegenstellen mit aufgebauter TCP-Verbindung zum lokalen ``port`` aus ``netstat -an``."""
    peers = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 6 or not fields[0].startswith("tcp") or fields[5] != "ESTABLISHED":
            continue
        local, foreign = fields[3], fields[4]
        if local.rpartition(".")[2] != str(port):
            continue
        host = foreign.rpartition(".")[0]
        if host:
            peers.add(host)
    return peers


def screen_sharing_peers(port: int = 5900, run=subprocess.run):
    """Wer ist gerade per Bildschirmfreigabe verbunden? ``None``, wenn netstat nicht läuft.
    netstat zeigt auch Verbindungen von root-Diensten ohne Adminrechte."""
    try:
        result = run(["/usr/sbin/netstat", "-an", "-p", "tcp"], capture_output=True, text=True,
                     timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return parse_peers(result.stdout, port)


class ConnectionWatcher:
    """Erkennt tatsächliche Live-Bild-Verbindungen (nicht nur Anfragen im Dashboard).

    Eine Gegenstelle gilt erst als verbunden, wenn sie bei zwei Abfragen hintereinander
    auftaucht. Das filtert die eigene Port-Prüfung und kurze Verbindungsversuche heraus.
    Mehrere Verbindungen derselben Gegenstelle zählen als eine Sitzung.
    """

    def __init__(self, peers=screen_sharing_peers, interval=5.0, clock=time.monotonic, wall=time.time):
        self._peers, self._interval, self._clock, self._wall = peers, interval, clock, wall
        self._lock = threading.Lock()
        self._last_poll = None
        self._seen = set()
        self._open = {}

    def poll(self):
        """Höchstens alle ``interval`` Sekunden abfragen; liefert ``(kind, details)``-Paare."""
        now = self._clock()
        if self._last_poll is not None and now - self._last_poll < self._interval:
            return []
        self._last_poll = now
        current = self._peers()
        if current is None:
            return []
        with self._lock:
            return self._update(current)

    def _update(self, current):
        events = []
        for peer in sorted(self._open.keys() - current):
            seconds = max(0, round(self._wall() - self._open.pop(peer)))
            events.append(("viewer.closed", {"peer": peer, "seconds": seconds,
                                             "message": f"Bildschirmfreigabe getrennt ({peer}, {duration_text(seconds)})"}))
        for peer in sorted((current & self._seen) - self._open.keys()):
            self._open[peer] = self._wall()
            events.append(("viewer.opened", {"peer": peer, "message": f"Bildschirmfreigabe verbunden ({peer})"}))
        self._seen = current
        return events

    def connected(self):
        """Gerade verbundene Gegenstellen mit Beginn (Unix-Zeit), für das Dashboard."""
        with self._lock:
            return [{"peer": peer, "since": since} for peer, since in sorted(self._open.items())]


def duration_text(seconds):
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} h {minutes} min"
    return f"{minutes} min {seconds} s" if minutes else f"{seconds} s"


class AppleBackend:
    """Phase A: Apple-Bildschirmfreigabe. Ansehen/Steuern wählt man in Apples App,
    tidemark kann den Modus daher nur protokollieren, nicht erzwingen."""

    name = "apple"

    def __init__(self, host=None, probe=screen_sharing_listening, cache_seconds=30.0, clock=time.monotonic,
                 watcher=None):
        if host is not None and not valid_host(host):
            raise ValueError("Ungültiger Viewer-Host (nur Hostname oder IP).")
        self.host = host
        self.watcher = watcher if watcher is not None else ConnectionWatcher()
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
                "modes": list(MODES), "modes_enforced": False, "pre_login": True,
                "connected": self.watcher.connected()}

    def open_session(self, mode):
        return {"kind": "external", "url": f"vnc://{self.host}"}

    def poll_connections(self):
        return self.watcher.poll()


class DemoBackend:
    """Simulation für ``--demo`` und Tests: eingebetteter Betrachter mit synthetischer
    Quelle (Schicht 2 ohne Mac testbar). Modi werden hier durchgesetzt."""

    name = "demo"

    def capabilities(self):
        return {"backend": self.name, "kind": "embedded", "available": True, "reason": None,
                "modes": list(MODES), "modes_enforced": True, "pre_login": False, "connected": []}

    def open_session(self, mode):
        return {"kind": "embedded"}

    def source(self):
        from .stream import DemoSource
        return DemoSource()

    def poll_connections(self):
        return []


class NoBackend:
    name = "none"

    def capabilities(self):
        return {"backend": self.name, "kind": "none", "available": False,
                "reason": "Kein Live-Bild konfiguriert.", "modes": [], "modes_enforced": False, "pre_login": False, "connected": []}

    def open_session(self, mode):  # pragma: no cover - durch available=False nie erreicht
        raise RuntimeError("Kein Live-Bild konfiguriert.")

    def poll_connections(self):
        return []
