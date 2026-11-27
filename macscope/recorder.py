"""Aufzeichnungsdienst für den verwalteten Modus (#22).

Ein root-Dienst (LaunchDaemon) besitzt Verlaufsdatenbank und Zugriffsschlüssel in
``/Library/Application Support/MacScope-Data`` (root, 0700). Der Agent im Konto des
Zielbenutzers spricht über einen Unix-Socket mit ihm (``RemoteStore``) und kann nur
die Operationen aus ``OPERATIONS`` aufrufen: Ereignisse anhängen und lesen, Aufträge
anlegen und fortschreiben. Löschen gibt es nicht; alte Einträge entfernt nur die
Aufbewahrungsfrist. Der Benutzer kann so seinen Verlauf nicht mehr löschen oder
ändern, wohl aber weiterhin Ereignisse hinzufügen (bekannte Grenze).

Protokoll: eine JSON-Zeile je Anfrage ``{"op": …, "args": {…}}`` und je Antwort
``{"ok": true, "result": …}`` bzw. ``{"ok": false, "error": …, "status": …}``.
"""
from __future__ import annotations

import collections
import json
import logging
import os
import pwd
import socket
import socketserver
import stat
import struct
import sys
import threading
import time
from pathlib import Path

from . import managed
from .service import ServiceError
from .store import Store, load_token, private_directory

MAX_REQUEST = 64 * 1024
MAX_RESPONSE = 4 * 1024 * 1024
OPERATIONS = {
    "event": frozenset({"kind", "app", "details", "timestamp"}),
    "events": frozenset({"before", "after", "limit", "query", "kind"}),
    "create_command": frozenset({"target"}),
    "set_command": frozenset({"command_id", "status", "message"}),
    "command": frozenset({"command_id"}),
    "recover_commands": frozenset({"message"}),
    "prune": frozenset(),
    "token": frozenset(),
    "ping": frozenset(),
}
log = logging.getLogger(__name__)


class RecorderUnavailable(ServiceError):
    def __init__(self, message="Der Aufzeichnungsdienst ist nicht erreichbar."):
        super().__init__(message, 503)


def peer_uid(sock) -> int:
    """Benutzerkennung der Gegenseite, vom Kernel geliefert (nicht fälschbar)."""
    if sys.platform == "darwin":
        # SOL_LOCAL = 0, LOCAL_PEERCRED = 1 -> struct xucred {u_int cr_version; uid_t cr_uid; ...}
        raw = sock.getsockopt(0, 1, 76)
        return struct.unpack_from("=II", raw)[1]
    raw = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", raw)[1]


def _validate(op, args):
    if op == "events":
        limit = args.get("limit", 100)
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("Ungültiger Seitenbereich.")
        for key in ("before", "after"):
            value = args.get(key)
            if value is not None and (type(value) is not int or not 0 <= value < 2**63):
                raise ValueError("Ungültiger Seitenbereich.")
        for key, longest in (("query", 200), ("kind", 80)):
            if not isinstance(args.get(key, ""), str) or len(args.get(key, "")) > longest:
                raise ValueError("Suchtext zu lang.")
    elif op == "event":
        if not isinstance(args.get("kind"), str) or not 0 < len(args["kind"]) <= 80:
            raise ValueError("Ungültiger Ereignistyp.")
        timestamp = args.get("timestamp")
        if timestamp is not None and (type(timestamp) not in (int, float) or timestamp > time.time() + 60):
            raise ValueError("Ungültiger Zeitpunkt.")


def handle(store, token, request) -> dict:
    """Eine Anfrage ausführen; nur Operationen und Argumente aus ``OPERATIONS``."""
    if not isinstance(request, dict):
        return {"ok": False, "error": "Ungültige Anfrage.", "status": 400}
    op, args = request.get("op"), request.get("args", {})
    if op not in OPERATIONS or not isinstance(args, dict) or set(args) - OPERATIONS[op]:
        return {"ok": False, "error": "Unbekannte Operation.", "status": 400}
    try:
        _validate(op, args)
        if op == "token":
            result = token()
        elif op == "ping":
            result = {"retention_days": store.retention_days}
        else:
            result = getattr(store, op)(**args)
        return {"ok": True, "result": result}
    except (ValueError, TypeError) as error:
        return {"ok": False, "error": str(error), "status": 400}
    except Exception:
        log.exception("Anfrage %s fehlgeschlagen", op)
        return {"ok": False, "error": "Interner Fehler im Aufzeichnungsdienst.", "status": 500}


class _Handler(socketserver.StreamRequestHandler):
    timeout = 30

    def setup(self):
        super().setup()
        with self.server.connections_lock:
            self.server.connections.add(self.request)

    def finish(self):
        with self.server.connections_lock:
            self.server.connections.discard(self.request)
        super().finish()

    def handle(self):
        try:
            uid = peer_uid(self.request)
        except OSError:
            return
        if uid not in self.server.allowed_uids:
            log.warning("Verbindung von uid %s abgelehnt", uid)
            self._send({"ok": False, "error": "Nicht berechtigt.", "status": 403})
            return
        while True:
            try:
                line = self.rfile.readline(MAX_REQUEST + 1)
            except (OSError, TimeoutError):
                return
            if not line:
                return
            if len(line) > MAX_REQUEST:
                self._send({"ok": False, "error": "Anfrage zu groß.", "status": 413})
                return
            try:
                request = json.loads(line)
            except ValueError:
                self._send({"ok": False, "error": "Ungültiges JSON.", "status": 400})
                return
            if not self._send(handle(self.server.store, self.server.token, request)):
                return

    def _send(self, response) -> bool:
        try:
            self.wfile.write(json.dumps(response, ensure_ascii=False).encode() + b"\n")
            self.wfile.flush()
            return True
        except OSError:
            return False


class RecorderServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True

    def __init__(self, path: Path, store, token, allowed_uids, owner=None):
        self.store, self.token, self.allowed_uids = store, token, frozenset(allowed_uids)
        self.connections, self.connections_lock = set(), threading.Lock()
        path = Path(path)
        if path.is_symlink() or (path.exists() and not stat.S_ISSOCK(path.lstat().st_mode)):
            raise ValueError(f"{path} ist kein Socket.")
        path.unlink(missing_ok=True)
        super().__init__(str(path), _Handler)
        # Verbinden braucht Schreibrecht auf den Socket. Der Ordner gehört root und ist
        # für den Benutzer nicht beschreibbar, also kann er den Socket nicht ersetzen.
        if owner is not None:
            os.chown(path, owner, -1)
        os.chmod(path, 0o600)

    def server_close(self):
        """Auch offene Verbindungen trennen (wie beim Ende des Prozesses)."""
        super().server_close()
        with self.connections_lock:
            connections = list(self.connections)
        for connection in connections:
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


class RemoteStore:
    """Store-Ersatz im Agenten: gleiche Methoden, Daten beim Aufzeichnungsdienst.

    Ist der Dienst kurz weg (Neustart), puffert ``event`` bis zu ``buffer`` Ereignisse
    mit ihrem ursprünglichen Zeitpunkt und schreibt sie beim nächsten Erfolg nach.
    Lesende Aufrufe melden ``RecorderUnavailable`` (HTTP 503).
    """

    def __init__(self, path=None, timeout=5.0, buffer=1000, retention_days=30):
        self.path = str(managed.RECORDER_SOCKET if path is None else path)
        self.timeout, self.retention_days = timeout, retention_days
        self._lock = threading.Lock()
        self._sock = self._file = None
        self._pending = collections.deque(maxlen=buffer)

    def _close(self):
        for item in (self._file, self._sock):
            try:
                if item:
                    item.close()
            except OSError:
                pass
        self._sock = self._file = None

    def _exchange(self, payload):
        reused = self._sock is not None
        for attempt in range(2):
            try:
                if self._sock is None:
                    self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    self._sock.settimeout(self.timeout)
                    self._sock.connect(self.path)
                    self._file = self._sock.makefile("rb")
                self._sock.sendall(payload)
                line = self._file.readline(MAX_RESPONSE + 1)
                if not line or len(line) > MAX_RESPONSE:
                    raise ConnectionError("Antwort fehlt oder zu groß")
                return json.loads(line)
            except (OSError, ValueError):
                self._close()
                # Nur eine gealterte Verbindung einmal neu aufbauen, sonst aufgeben.
                if not reused or attempt:
                    raise RecorderUnavailable() from None
                reused = False

    def _call(self, op, **args):
        payload = json.dumps({"op": op, "args": args}).encode() + b"\n"
        with self._lock:
            response = self._exchange(payload)
        if not isinstance(response, dict):
            raise RecorderUnavailable()
        if response.get("ok"):
            return response.get("result")
        message = str(response.get("error", "Fehler im Aufzeichnungsdienst."))
        if response.get("status") == 400:
            raise ValueError(message)
        raise ServiceError(message, int(response.get("status") or 500))

    def _flush(self):
        while self._pending:
            kind, app, details, timestamp = self._pending[0]
            self._call("event", kind=kind, app=app, details=details, timestamp=timestamp)
            self._pending.popleft()

    def wait(self, seconds=30.0, interval=1.0, sleep=time.sleep):
        """Beim Start auf den Dienst warten; liefert die Aufbewahrungsfrist."""
        deadline = time.monotonic() + seconds
        while True:
            try:
                info = self._call("ping")
                self.retention_days = info["retention_days"]
                return info
            except RecorderUnavailable:
                if time.monotonic() >= deadline:
                    raise
                sleep(interval)

    def pending(self) -> int:
        return len(self._pending)

    def event(self, kind, app=None, details=None, timestamp=None):
        timestamp = time.time() if timestamp is None else timestamp
        try:
            self._flush()
            return self._call("event", kind=kind, app=app, details=details, timestamp=timestamp)
        except RecorderUnavailable:
            if len(self._pending) == self._pending.maxlen:
                log.warning("Puffer voll: ältestes nicht gespeichertes Ereignis verworfen")
            self._pending.append((kind, app, details, timestamp))
            return None

    def events(self, **args):
        return self._call("events", **args)

    def create_command(self, target):
        return self._call("create_command", target=target)

    def set_command(self, command_id, status, message):
        return self._call("set_command", command_id=command_id, status=status, message=message)

    def command(self, command_id):
        return self._call("command", command_id=command_id)

    def recover_commands(self, message=None):
        return self._call("recover_commands", **({} if message is None else {"message": message}))

    def prune(self):
        return self._call("prune")

    def token(self):
        return self._call("token")

    def close(self):
        with self._lock:
            self._close()


def _secure_run_directory(path: Path):
    path.mkdir(mode=0o755, exist_ok=True)
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != 0:
        raise SystemExit(f"{path} muss ein Ordner von root sein.")
    path.chmod(0o755)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if os.geteuid() != 0:
        raise SystemExit("Der Aufzeichnungsdienst läuft nur als root (LaunchDaemon).")
    try:
        config = managed.load()
    except managed.ManagedConfigError as error:
        log.error("Konfiguration: %s", error)
        raise SystemExit(1) from error
    if config is None:
        return  # nicht verwaltet: nichts zu tun (Exit 0, launchd startet nicht neu)
    try:
        target = pwd.getpwnam(config.target_user).pw_uid
    except KeyError as error:
        log.error("Benutzer %s existiert nicht", config.target_user)
        raise SystemExit(1) from error
    os.umask(0o077)
    private_directory(managed.DATA_DIRECTORY)
    store = Store(managed.DATA_DIRECTORY, config.retention_days)
    token = load_token(managed.DATA_DIRECTORY)
    _secure_run_directory(managed.RUN_DIRECTORY)
    server = RecorderServer(managed.RECORDER_SOCKET, store, lambda: token, {target, 0}, owner=target)

    def prune_hourly():
        while True:
            time.sleep(3600)
            try:
                store.prune()
            except Exception:
                log.exception("Aufräumen fehlgeschlagen")

    threading.Thread(target=prune_hourly, name="prune", daemon=True).start()
    log.info("Aufzeichnungsdienst bereit für %s: %s", config.target_user, managed.RECORDER_SOCKET)
    server.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
