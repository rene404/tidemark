"""Minimales WebSocket nach RFC 6455, nur Serverseite und nur Standardbibliothek.

Bewusst klein: binäre Nachrichten, keine Fragmentierung, keine Erweiterungen
(keine Kompression), Ping/Pong und Close. Für den Live-Bild-Strom (Viewer-Naht,
Schicht 2) durch den SSH-Tunnel; siehe docs/VIEWER-NAHT.md.
"""
from __future__ import annotations

import base64
import hashlib
import select
import socket
import struct
import threading

GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
CONTINUATION, TEXT, BINARY, CLOSE, PING, PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA
NORMAL, GOING_AWAY, PROTOCOL_ERROR, UNSUPPORTED, POLICY, TOO_BIG = 1000, 1001, 1002, 1003, 1008, 1009


class Closed(Exception):
    """Verbindung ist zu (Gegenseite hat geschlossen oder Protokollfehler)."""

    def __init__(self, code=NORMAL, reason=""):
        super().__init__(f"{code} {reason}".strip())
        self.code, self.reason = code, reason


def accept_key(key: str) -> str:
    return base64.b64encode(hashlib.sha1(key.encode("ascii") + GUID).digest()).decode("ascii")


def handshake_key(headers) -> str:
    """Prüft die Upgrade-Anfrage und gibt den Sec-WebSocket-Key zurück (sonst ValueError)."""
    if headers.get("Upgrade", "").lower() != "websocket":
        raise ValueError("WebSocket-Upgrade erforderlich.")
    if "upgrade" not in [part.strip().lower() for part in headers.get("Connection", "").split(",")]:
        raise ValueError("WebSocket-Upgrade erforderlich.")
    if headers.get("Sec-WebSocket-Version") != "13":
        raise ValueError("WebSocket-Version 13 erforderlich.")
    key = headers.get("Sec-WebSocket-Key", "")
    try:
        if len(base64.b64decode(key, validate=True)) != 16:
            raise ValueError
    except (ValueError, base64.binascii.Error):
        raise ValueError("Ungültiger Sec-WebSocket-Key.") from None
    return key


def encode_frame(opcode: int, payload: bytes = b"") -> bytes:
    """Server-Frame (unmaskiert, FIN gesetzt)."""
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", 0x80 | opcode, length)
    elif length < 1 << 16:
        header = struct.pack("!BBH", 0x80 | opcode, 126, length)
    else:
        header = struct.pack("!BBQ", 0x80 | opcode, 127, length)
    return header + payload


class WebSocket:
    """Eine Verbindung. ``receive`` aus genau einem Thread, ``send`` aus beliebigen."""

    def __init__(self, sock, max_message=64 * 1024, idle_timeout=None, io_timeout=30.0):
        """``idle_timeout``: so lange wartet ``receive`` auf den Beginn einer Nachricht, dann
        ``None``. ``io_timeout``: Frist für Senden und für den Rest einer begonnenen Nachricht."""
        self.sock, self.max_message, self.idle_timeout = sock, max_message, idle_timeout
        self._buffer = b""
        self._send_lock = threading.Lock()
        self.closed = False
        sock.settimeout(io_timeout)

    def _read(self, count, idle_ok=False):
        while len(self._buffer) < count:
            if idle_ok and not self._buffer and self.idle_timeout is not None:
                if not select.select([self.sock], [], [], self.idle_timeout)[0]:
                    raise socket.timeout
            try:
                chunk = self.sock.recv(max(4096, count - len(self._buffer)))
            except socket.timeout:
                if idle_ok and not self._buffer:
                    raise
                raise Closed(PROTOCOL_ERROR, "Zeitüberschreitung mitten im Frame") from None
            except OSError:
                raise Closed(GOING_AWAY, "Verbindung getrennt") from None
            if not chunk:
                raise Closed(GOING_AWAY, "Verbindung getrennt")
            self._buffer += chunk
        data, self._buffer = self._buffer[:count], self._buffer[count:]
        return data

    def receive(self):
        """Nächste Binärnachricht. ``None`` bei Leerlauf (Zeitüberschreitung ohne Daten).
        Ping wird beantwortet, Close bestätigt (dann ``Closed``)."""
        while True:
            try:
                first, second = self._read(2, idle_ok=True)
            except socket.timeout:
                return None
            fin, opcode, masked, length = first & 0x80, first & 0x0F, second & 0x80, second & 0x7F
            if first & 0x70:
                self.fail(PROTOCOL_ERROR, "Erweiterungen nicht unterstützt")
            if not masked:
                self.fail(PROTOCOL_ERROR, "Client-Frames müssen maskiert sein")
            if length == 126:
                length = struct.unpack("!H", self._read(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read(8))[0]
            control = opcode >= 0x8
            if control and (length > 125 or not fin):
                self.fail(PROTOCOL_ERROR, "Ungültiger Steuer-Frame")
            if not fin or opcode == CONTINUATION:
                self.fail(UNSUPPORTED, "Fragmentierte Nachrichten nicht unterstützt")
            if length > self.max_message:
                self.fail(TOO_BIG, "Nachricht zu groß")
            mask = self._read(4)
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(self._read(length))) if length else b""
            if opcode == BINARY:
                return payload
            if opcode == PING:
                self._send(PONG, payload)
            elif opcode == PONG:
                continue
            elif opcode == CLOSE:
                code = struct.unpack("!H", payload[:2])[0] if len(payload) >= 2 else NORMAL
                self.close(NORMAL)
                raise Closed(code, payload[2:].decode("utf-8", "replace"))
            elif opcode == TEXT:
                self.fail(UNSUPPORTED, "Nur Binärnachrichten")
            else:
                self.fail(PROTOCOL_ERROR, "Unbekannter Opcode")

    def _send(self, opcode, payload=b""):
        frame = encode_frame(opcode, payload)
        with self._send_lock:
            if self.closed and opcode != CLOSE:
                raise Closed(GOING_AWAY, "bereits geschlossen")
            try:
                self.sock.sendall(frame)
            except OSError:
                self.closed = True
                raise Closed(GOING_AWAY, "Verbindung getrennt") from None

    def send(self, payload: bytes):
        self._send(BINARY, payload)

    def close(self, code=NORMAL, reason=""):
        if self.closed:
            return
        try:
            self._send(CLOSE, struct.pack("!H", code) + reason.encode("utf-8")[:120])
        except Closed:
            pass
        self.closed = True

    def fail(self, code, reason):
        self.close(code, reason)
        raise Closed(code, reason)
