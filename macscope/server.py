"""Loopback-only HTTP endpoint. Remote connections are carried by the user's SSH tunnel."""
from __future__ import annotations

import hmac
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .service import ServiceError
from .stream import relay
from .websocket import WebSocket, accept_key, handshake_key

WEB = Path(__file__).parent / "web"
HOST_PATTERN = re.compile(r"(?:127\.0\.0\.1|localhost):[0-9]{1,5}\Z", re.ASCII)


def make_server(service, token: str, port: int = 8765):
    class Handler(BaseHTTPRequestHandler):
        server_version = "MacScope"
        sys_version = ""

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, *_):
            # HTTP logs must never contain a token, URL query or captured application title.
            pass

        def _reply(self, status, payload, mime="application/json; charset=utf-8"):
            data = json.dumps(payload, ensure_ascii=False).encode() if isinstance(payload, (dict, list)) else payload
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'self'; style-src 'self'; "
                             "connect-src 'self' ws://127.0.0.1:* ws://localhost:*; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(data)

        def _guard(self, authenticated=True):
            host = self.headers.get("Host", "")
            if not HOST_PATTERN.fullmatch(host):
                raise ServiceError("Ungültiger Host. Dashboard über localhost öffnen.", 403)
            origin = self.headers.get("Origin")
            if origin is not None and origin != "http://" + host:
                raise ServiceError("Zugriff von einer anderen Webseite ist nicht erlaubt.", 403)
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                raise ServiceError("Zugriff von einer anderen Webseite ist nicht erlaubt.", 403)
            if authenticated:
                supplied = self.headers.get("Authorization", "")
                if not hmac.compare_digest(supplied.encode(), ("Bearer " + token).encode()):
                    raise ServiceError("Bitte den Zugriffsschlüssel eingeben.", 401)

        def do_GET(self):
            try:
                route = urlsplit(self.path)
                static = {
                    "/": ("index.html", "text/html; charset=utf-8"),
                    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                    "/style.css": ("style.css", "text/css; charset=utf-8"),
                }
                # Der Strom prüft statt des Schlüssels die einmalige Sitzungs-ID (Browser-WebSockets
                # können keinen Authorization-Header setzen); die ID gibt es nur mit Schlüssel.
                self._guard(authenticated=route.path not in static and route.path != "/api/viewer/stream")
                if route.path in static:
                    filename, mime = static[route.path]
                    self._reply(200, (WEB / filename).read_bytes(), mime)
                elif route.path == "/api/status":
                    self._reply(200, service.status())
                elif route.path == "/api/viewer":
                    self._reply(200, service.viewer_status())
                elif route.path == "/api/viewer/stream":
                    self._stream(route)
                elif route.path == "/api/apps":
                    self._reply(200, {"apps": service.apps()})
                elif route.path == "/api/events":
                    params = parse_qs(route.query, max_num_fields=10)
                    def number(key, default=None):
                        raw = params.get(key, [default])[0]
                        return int(raw) if raw is not None else None
                    limit = number("limit", 100)
                    before, after = number("before"), number("after")
                    if not 1 <= limit <= 500 or any(x is not None and not 0 <= x < 2**63 for x in (before, after)):
                        raise ValueError("Ungültiger Seitenbereich.")
                    query = params.get("q", [""])[0]
                    kind = params.get("kind", [""])[0]
                    if len(query) > 200 or len(kind) > 80:
                        raise ValueError("Suchtext zu lang.")
                    self._reply(200, service.store.events(before=before, after=after, limit=limit,
                                                         query=query, kind=kind))
                elif re.fullmatch(r"/api/commands/[0-9a-f]{32}", route.path):
                    command = service.store.command(route.path.rsplit("/", 1)[1])
                    if not command:
                        raise ServiceError("Auftrag nicht gefunden.", 404)
                    self._reply(200, command)
                else:
                    raise ServiceError("Nicht gefunden.", 404)
            except ServiceError as error:
                self._reply(error.status, {"error": str(error)})
            except ValueError as error:
                self._reply(400, {"error": str(error)})
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass
            except Exception:
                self._reply(500, {"error": "Interner Fehler. Prüfe die lokale Installation."})

        def _stream(self, route):
            if not hasattr(service.viewer, "source"):
                raise ServiceError("Dieses Live-Bild hat keinen eingebetteten Strom.", 404)
            if self.headers.get("Origin") is None:
                raise ServiceError("Nur aus dem Dashboard.", 403)
            key = handshake_key(self.headers)
            session_id = parse_qs(route.query, max_num_fields=2).get("session", [""])[0]
            session = service.attach_stream(session_id)
            reason = "Fehler"
            try:
                self.send_response(101)
                self.send_header("Upgrade", "websocket")
                self.send_header("Connection", "Upgrade")
                self.send_header("Sec-WebSocket-Accept", accept_key(key))
                self.end_headers()
                self.close_connection = True
                # Kurzer Leerlauf-Takt: so merkt die Weiterleitung ein DELETE spätestens nach 1 s.
                ws = WebSocket(self.connection, max_message=8192, idle_timeout=1.0)
                reason = relay(ws, service.viewer.source(), session.mode, session.stopped)
            finally:
                service.finish_stream(session, reason)

        def _json_body(self):
            if self.headers.get_content_type() != "application/json":
                raise ServiceError("JSON erforderlich.", 415)
            if self.headers.get("Transfer-Encoding"):
                raise ServiceError("Transfer-Encoding wird nicht unterstützt.", 400)
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 4096:
                raise ServiceError("Ungültige Nachrichtengröße.", 413)
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("JSON-Objekt erforderlich.")
            return payload

        def do_POST(self):
            try:
                self._guard()
                if self.path == "/api/commands/quit":
                    payload = self._json_body()
                    if set(payload) != {"pid", "bundle_id", "launched_at"}:
                        raise ValueError("pid, bundle_id und launched_at sind erforderlich.")
                    if type(payload["pid"]) is not int or not 1 <= payload["pid"] < 2**31:
                        raise ValueError("Ungültige Prozess-ID.")
                    if not isinstance(payload["bundle_id"], str) or not isinstance(payload["launched_at"], str):
                        raise ValueError("Ungültige App-Identität.")
                    command_id = service.request_quit(**payload)
                    self._reply(202, {"command_id": command_id, "status": "queued"})
                elif self.path == "/api/viewer/session":
                    payload = self._json_body()
                    if set(payload) != {"mode"} or not isinstance(payload["mode"], str):
                        raise ValueError("mode ist erforderlich.")
                    self._reply(201, service.open_viewer(payload["mode"]))
                else:
                    raise ServiceError("Nicht gefunden.", 404)
            except ServiceError as error:
                self._reply(error.status, {"error": str(error)})
            except (ValueError, UnicodeError) as error:
                self._reply(400, {"error": str(error)})
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass
            except Exception:
                self._reply(500, {"error": "Interner Fehler. Keine Aktion bestätigt."})

        def do_DELETE(self):
            try:
                self._guard()
                if self.path != "/api/viewer/session":
                    raise ServiceError("Nicht gefunden.", 404)
                service.close_viewer()
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
            except ServiceError as error:
                self._reply(error.status, {"error": str(error)})
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass
            except Exception:
                self._reply(500, {"error": "Interner Fehler."})

    class LocalServer(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True
        request_queue_size = 16

    # The binding address is intentionally not configurable.
    return LocalServer(("127.0.0.1", port), Handler)
