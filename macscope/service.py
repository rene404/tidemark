"""Thread-safe bridge between the local HTTP server and the main-thread macOS adapter."""
from __future__ import annotations

import copy
import platform
import queue
import threading
import time

from . import __version__
from .store import Store


class ServiceError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class Service:
    def __init__(self, store: Store, allowed_bundles=None, demo=False):
        self.store = store
        self.allowed_bundles = frozenset({"org.mozilla.firefox"} if allowed_bundles is None else allowed_bundles)
        self.demo = demo
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
        store.recover_commands()
        store.prune()
        store.event("agent.started", details={"version": __version__, "demo": demo})

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
                "demo": self.demo, "paused": self._paused,
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
        with self._lock:
            self._paused = not self._paused
            self.store.event("recording.paused" if self._paused else "recording.resumed")
            return self._paused

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
        self.store.set_command(command_id, status, message)
        with self._lock:
            self._inflight.discard((target["pid"], target["bundle_id"], target["launched_at"]))

    def tick(self, adapter):
        """Must be called on the AppKit main thread. adapter.quit rechecks live identity."""
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
            if not self._closed:
                self._closed = True
                self.store.recover_commands("Agent beendet; Auftrag wird nicht wiederholt.")
                self.store.event("agent.stopped")
