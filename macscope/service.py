"""Thread-safe bridge between the local HTTP server and the main-thread macOS adapter."""
from __future__ import annotations

import copy
import hmac
import logging
import os
import platform
import queue
import secrets
import threading
import time

from . import __version__
from .store import Store
from .viewer import MODES, NoBackend, duration_text


class ServiceError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class ViewerSession:
    """Eingebettete Live-Bild-Sitzung: einmal verbindbar, kurz gültig, jederzeit beendbar."""

    CONNECT_WITHIN = 30.0

    def __init__(self, mode, backend):
        self.id = secrets.token_urlsafe(24)
        self.mode, self.backend = mode, backend
        self.created = time.monotonic()
        self.connected_at = None
        self.stopped = threading.Event()


class Service:
    def __init__(self, store: Store, allowed_bundles=None, demo=False, managed=False, viewer=None,
                 heartbeat_path=None, heartbeat_interval=10):
        self.store = store
        self.allowed_bundles = frozenset({"org.mozilla.firefox"} if allowed_bundles is None else allowed_bundles)
        self.demo = demo
        self.managed = managed
        self.viewer = viewer or NoBackend()
        self.heartbeat_path = heartbeat_path
        self._heartbeat_interval = heartbeat_interval
        self._last_beat = None
        self._beat_failed = False
        self.started_at = time.time()
        self._lock = threading.RLock()
        self._apps = []
        self._updated = 0.0
        self._paused = False
        self._active = None
        self._queue = queue.Queue(maxsize=32)
        self._inflight = set()
        self._waiting = {}
        self._closed = False
        self._session = None
        store.recover_commands()
        store.prune()
        store.event("agent.started", details={"version": __version__, "demo": demo, "managed": managed})

    def update(self, apps, active=None):
        with self._lock:
            self._apps = copy.deepcopy(apps)
            self._active = copy.deepcopy(active)
            self._updated = time.monotonic()

    def status(self):
        with self._lock:
            age = time.monotonic() - self._updated if self._updated else None
            return {
                "version": __version__, "hostname": platform.node(),
                "platform": platform.platform(), "started_at": self.started_at,
                "demo": self.demo, "managed": self.managed, "paused": self._paused,
                "agent_online": not self._closed and age is not None and age < 10,
                "snapshot_age_seconds": round(age, 1) if age is not None else None,
                "active_app": copy.deepcopy(self._active),
                "retention_days": self.store.retention_days,
                "scope": "Anwendungen der angemeldeten Benutzersitzung",
            }

    def apps(self):
        with self._lock:
            return [{**copy.deepcopy(app), "can_quit": bool(
                app.get("bundle_id") in self.allowed_bundles and app.get("launched_at")
                and self.status()["agent_online"])} for app in self._apps]

    def record(self, kind, app=None, details=None):
        with self._lock:
            if self._paused:
                return
            self.store.event(kind, app, details)

    def toggle_pause(self):
        if self.managed:
            raise ServiceError("Im verwalteten Modus kann die Aufzeichnung nicht pausiert werden.", 403)
        with self._lock:
            self._paused = not self._paused
            self.store.event("recording.paused" if self._paused else "recording.resumed")
            return self._paused

    def viewer_status(self):
        capabilities = self.viewer.capabilities()
        with self._lock:
            session = self._session
            capabilities["session"] = None if session is None else {
                "mode": session.mode, "connected": session.connected_at is not None}
        return capabilities

    def open_viewer(self, mode):
        """Live-Bild anfragen. Jede Anfrage wird protokolliert, auch während einer Pause."""
        if mode not in MODES:
            raise ServiceError("Ungültiger Modus.", 400)
        if self._closed:
            raise ServiceError("Der Agent wird gerade beendet.", 503)
        capabilities = self.viewer.capabilities()
        details = {"backend": capabilities["backend"], "mode": mode}
        if not capabilities["available"]:
            self.store.event("viewer.refused", details={**details, "message": capabilities["reason"]})
            raise ServiceError(capabilities["reason"], 409)
        descriptor = self.viewer.open_session(mode)
        if descriptor.get("kind") == "embedded":
            with self._lock:
                if self._session is not None:
                    self._session.stopped.set()  # nur eine Sitzung zugleich; die alte endet
                    if self._session.connected_at is None:
                        self._session = None
                session = ViewerSession(mode, capabilities["backend"])
                self._session = session
            descriptor = {"kind": "embedded", "mode": mode, "session": session.id,
                          "stream": f"/api/viewer/stream?session={session.id}"}
        label = "Steuern" if mode == "control" else "Ansehen"
        message = (f"Live-Bild angefragt ({label})" if capabilities["modes_enforced"]
                   else "Live-Bild angefragt (Modus wird in der Bildschirmfreigabe gewählt)")
        self.store.event("viewer.requested", details={**details, "message": message})
        return descriptor

    def attach_stream(self, session_id):
        """Strom einer angefragten Sitzung annehmen (einmalig, innerhalb von 30 s)."""
        with self._lock:
            session = self._session
            valid = (session is not None and isinstance(session_id, str)
                     and hmac.compare_digest(session.id.encode(), session_id.encode())
                     and session.connected_at is None and not session.stopped.is_set()
                     and time.monotonic() - session.created <= ViewerSession.CONNECT_WITHIN)
            if not valid:
                raise ServiceError("Sitzung ungültig oder abgelaufen.", 403)
            session.connected_at = time.time()
        label = "Steuern" if session.mode == "control" else "Ansehen"
        self.store.event("viewer.opened", details={"backend": session.backend, "mode": session.mode,
                                                   "message": f"Live-Bild verbunden ({label})"})
        return session

    def finish_stream(self, session, reason):
        with self._lock:
            if self._session is session:
                self._session = None
        seconds = max(0, round(time.time() - session.connected_at))
        self.store.event("viewer.closed", details={"backend": session.backend, "mode": session.mode,
                                                   "seconds": seconds, "reason": reason,
                                                   "message": f"Live-Bild getrennt ({reason}, {duration_text(seconds)})"})

    def close_viewer(self):
        """Laufende Sitzung beenden (DELETE /api/viewer/session)."""
        with self._lock:
            session = self._session
            if session is None:
                raise ServiceError("Keine laufende Live-Bild-Sitzung.", 404)
            session.stopped.set()
            if session.connected_at is None:
                self._session = None

    def request_quit(self, pid, bundle_id, launched_at):
        with self._lock:
            if not self.status()["agent_online"]:
                raise ServiceError("Der macOS-Agent liefert gerade keine aktuellen Daten.", 503)
            if bundle_id not in self.allowed_bundles:
                raise ServiceError("Für diese Anwendung sind Fernaktionen nicht freigegeben.", 403)
            target = next((app for app in self._apps if app["pid"] == pid
                           and app["bundle_id"] == bundle_id
                           and app["launched_at"] == launched_at and launched_at), None)
            if not target:
                raise ServiceError("Die ausgewählte App-Instanz läuft nicht mehr. Liste aktualisieren.", 409)
            identity = (pid, bundle_id, launched_at)
            if identity in self._inflight:
                raise ServiceError("Für diese App läuft bereits ein Auftrag.", 409)
            if self._queue.full():
                raise ServiceError("Die Auftragswarteschlange ist voll.", 429)
            command_id = self.store.create_command(target)
            self._inflight.add(identity)
            self._queue.put_nowait((command_id, copy.deepcopy(target), (time.monotonic(), time.time())))
            return command_id

    def _finish(self, command_id, target, status, message):
        try:
            self.store.set_command(command_id, status, message)
        finally:
            # Auch wenn der Aufzeichnungsdienst gerade fehlt: App nicht dauerhaft blockieren.
            with self._lock:
                self._inflight.discard((target["pid"], target["bundle_id"], target["launched_at"]))

    def beat(self):
        """Lebenszeichen für den root-Wächter (mtime der Datei). Hängt der Hauptthread,
        bleibt es aus, und der Wächter startet den Agenten neu."""
        if not self.heartbeat_path:
            return
        now = time.monotonic()
        if self._last_beat is not None and now - self._last_beat < self._heartbeat_interval:
            return
        self._last_beat = now
        try:
            fd = os.open(self.heartbeat_path, os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                os.utime(fd)
            finally:
                os.close(fd)
            self._beat_failed = False
        except OSError as error:
            if not self._beat_failed:
                logging.getLogger(__name__).warning("Lebenszeichen nicht schreibbar: %s", error)
            self._beat_failed = True

    def watch_viewer(self):
        """Tatsächliche Live-Bild-Verbindungen protokollieren, auch während einer Pause."""
        try:
            events = self.viewer.poll_connections()
        except Exception:
            logging.getLogger(__name__).exception("Live-Bild-Verbindungen nicht abfragbar")
            return
        for kind, details in events:
            self.store.event(kind, details={"backend": self.viewer.name, **details})

    def tick(self, adapter):
        """Must be called on the AppKit main thread. adapter.quit rechecks live identity."""
        self.beat()
        self.watch_viewer()
        while True:
            try:
                command_id, target, queued_at = self._queue.get_nowait()
            except queue.Empty:
                break
            # The wall clock also advances during macOS sleep; monotonic alone may not.
            if max(time.monotonic() - queued_at[0], time.time() - queued_at[1]) > 10:
                self._finish(command_id, target, "expired", "Auftrag abgelaufen; keine Aktion ausgeführt.")
                continue
            try:
                outcome = adapter.quit(target)
            except Exception:
                self._finish(command_id, target, "failed", "Die macOS-Schnittstelle meldet einen Fehler.")
                continue
            if outcome == "not_running":
                self._finish(command_id, target, "not_running", "Diese App-Instanz läuft nicht mehr.")
            elif outcome == "refused":
                self._finish(command_id, target, "refused", "macOS hat die Beenden-Anfrage nicht angenommen.")
            elif outcome == "requested":
                self.store.set_command(command_id, "requested", "Beenden angefragt; warte auf das tatsächliche App-Ende.")
                self._waiting[command_id] = (target, (time.monotonic(), time.time()))
            else:
                self._finish(command_id, target, "failed", "Unbekannte Antwort der macOS-Schnittstelle.")
        for command_id, (target, requested_at) in list(self._waiting.items()):
            try:
                running = adapter.is_running(target)
            except Exception:
                running = None
            if running is False:
                self._finish(command_id, target, "terminated", "Das Ende der ausgewählten App-Instanz wurde bestätigt.")
                del self._waiting[command_id]
            elif max(time.monotonic() - requested_at[0], time.time() - requested_at[1]) >= 15:
                self._finish(command_id, target, "unconfirmed",
                             "App-Ende nicht bestätigt. Möglicherweise wartet die App auf eine Rückfrage.")
                del self._waiting[command_id]

    def close(self):
        with self._lock:
            if self._session is not None:
                self._session.stopped.set()
            if not self._closed:
                self._closed = True
                try:
                    self.store.recover_commands("Agent beendet; Auftrag wird nicht wiederholt.")
                except ServiceError as error:
                    logging.getLogger(__name__).warning("Aufträge beim Beenden nicht abgeschlossen: %s", error)
                self.store.event("agent.stopped")
