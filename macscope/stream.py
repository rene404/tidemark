"""Strom-Protokoll v0 (Viewer-Naht, Schicht 2) und Weiterleitung.

Jede Nachricht: 1 Byte Typ + Nutzlast. Steuernachrichten tragen JSON, ``FRAME``
einen festen Kopf und die rohen Bilddaten:

    FRAME = Typ | Codec (1 Byte) | Flags (1 Byte, Bit 0 = Schlüsselbild) |
            Zeitstempel in ms (8 Byte, big endian) | Bilddaten

Koordinaten sind normiert (0–1), Tasten physische ``KeyboardEvent.code``-Werte.
Die Weiterleitung (``relay``) prüft Richtung, Modus (keine Eingaben bei ``view``),
Größe und Rate; die Quelle bekommt nur geprüfte Nachrichten.
"""
from __future__ import annotations

import json
import math
import socket
import struct
import threading
import time
import zlib

from .websocket import POLICY, Closed

VERSION = 0
# Quelle -> Betrachter
HELLO, FRAME, CURSOR, DISPLAY, BYE = 0x01, 0x02, 0x03, 0x04, 0x05
# Betrachter -> Quelle
START, POINTER, WHEEL, KEY, KEYFRAME, STOP = 0x10, 0x11, 0x12, 0x13, 0x14, 0x15
VIEWER_TYPES = {START, POINTER, WHEEL, KEY, KEYFRAME, STOP}
INPUT_TYPES = {POINTER, WHEEL, KEY}
CODECS = {1: "jpeg", 2: "png"}
CODEC_IDS = {name: number for number, name in CODECS.items()}
FRAME_HEADER = struct.Struct("!BBBQ")
MAX_VIEWER_MESSAGE = 4096
MAX_SOURCE_MESSAGE = 8 * 1024 * 1024


class ProtocolError(ValueError):
    pass


def control(kind: int, payload: dict) -> bytes:
    return bytes([kind]) + json.dumps(payload, separators=(",", ":")).encode()


def frame(codec: str, data: bytes, keyframe=True, timestamp_ms=None) -> bytes:
    stamp = int(time.time() * 1000) if timestamp_ms is None else timestamp_ms
    return FRAME_HEADER.pack(FRAME, CODEC_IDS[codec], 1 if keyframe else 0, stamp) + data


def parse_frame(message: bytes) -> dict:
    kind, codec, flags, stamp = FRAME_HEADER.unpack_from(message)
    if kind != FRAME or codec not in CODECS:
        raise ProtocolError("Kein gültiges Bild.")
    return {"codec": CODECS[codec], "keyframe": bool(flags & 1), "timestamp_ms": stamp,
            "data": message[FRAME_HEADER.size:]}


def _number(value, low, high):
    return type(value) in (int, float) and math.isfinite(value) and low <= value <= high


def check_viewer_message(message: bytes, mode: str):
    """Nachricht vom Betrachter prüfen. Liefert ``(typ, inhalt)`` oder ``ProtocolError``."""
    if not message or len(message) > MAX_VIEWER_MESSAGE:
        raise ProtocolError("Leere oder zu große Nachricht.")
    kind = message[0]
    if kind not in VIEWER_TYPES:
        raise ProtocolError("Unbekannter Nachrichtentyp.")
    if kind in INPUT_TYPES and mode != "control":
        raise ProtocolError("Eingaben sind im Modus Ansehen nicht erlaubt.")
    try:
        payload = json.loads(message[1:] or b"{}")
    except ValueError:
        raise ProtocolError("Ungültiges JSON.") from None
    if not isinstance(payload, dict):
        raise ProtocolError("JSON-Objekt erwartet.")
    if kind == POINTER and not (_number(payload.get("x"), 0, 1) and _number(payload.get("y"), 0, 1)
                                and type(payload.get("buttons", 0)) is int and 0 <= payload.get("buttons", 0) < 32):
        raise ProtocolError("Ungültige Zeigerposition.")
    if kind == WHEEL and not (_number(payload.get("dx", 0), -10000, 10000) and _number(payload.get("dy", 0), -10000, 10000)):
        raise ProtocolError("Ungültiges Scrollen.")
    if kind == KEY and not (isinstance(payload.get("code"), str) and 0 < len(payload["code"]) <= 32
                            and type(payload.get("down")) is bool):
        raise ProtocolError("Ungültige Taste.")
    if kind == START and "max_fps" in payload and not _number(payload["max_fps"], 1, 60):
        raise ProtocolError("Ungültige Bildrate.")
    return kind, payload


class RateLimit:
    """Token-Bucket: ``rate`` Nachrichten pro Sekunde, Spitzen bis ``burst``."""

    def __init__(self, rate=120.0, burst=240.0, clock=time.monotonic):
        self.rate, self.burst, self.clock = rate, burst, clock
        self.tokens, self.updated = burst, clock()

    def allow(self) -> bool:
        now = self.clock()
        self.tokens = min(self.burst, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


def relay(ws, source, mode, stopped: threading.Event, limit=None):
    """Verbindet Betrachter (WebSocket) und Quelle, bis eine Seite aufhört.
    Liefert den Grund als Text (für die Zeitleiste)."""
    limit = limit or RateLimit()
    reason = [None]

    def end(text):
        if reason[0] is None:  # der erste Grund zählt (nicht ein Folgefehler danach)
            reason[0] = text

    def send(message: bytes):
        if len(message) > MAX_SOURCE_MESSAGE or not message or message[0] in VIEWER_TYPES:
            return  # Quelle hält sich nicht ans Protokoll: verwerfen statt weitergeben
        try:
            ws.send(message)
        except Closed:
            end("Verbindung getrennt")
            stopped.set()
        if message[0] == BYE:
            stopped.set()

    source.start(send)
    try:
        while not stopped.is_set():
            try:
                message = ws.receive()
            except Closed as closed:
                end("vom Betrachter beendet" if closed.code in (1000, 1001) else f"Protokollfehler: {closed.reason}")
                break
            except (socket.timeout, OSError):
                end("Verbindung getrennt")
                break
            if message is None:
                continue  # Leerlauf: nur prüfen, ob die Sitzung beendet wurde
            if not limit.allow():
                end("zu viele Nachrichten")
                ws.close(POLICY, "Rate")
                break
            try:
                kind, payload = check_viewer_message(message, mode)
            except ProtocolError as error:
                end(f"abgelehnt: {error}")
                ws.close(POLICY, "Protokoll")
                break
            if kind == STOP:
                end("vom Betrachter beendet")
                break
            source.deliver(kind, payload)
    finally:
        stopped.set()
        source.stop()
        ws.close()
    return reason[0] or "beendet"


# --- Demo-Quelle ------------------------------------------------------------------

def png(width: int, height: int, rows) -> bytes:
    """PNG aus RGB-Zeilen (bytes je Zeile), nur zlib."""
    def chunk(kind, data):
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", zlib.crc32(kind + data))
    raw = b"".join(b"\x00" + row for row in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


class DemoSource:
    """Synthetische Quelle zum Testen ohne Mac: wandernder Balken, Zeiger folgt den
    Eingaben (Modus Steuern). Liefert PNG, weil die Standardbibliothek kein JPEG kann."""

    def __init__(self, width=480, height=300, fps=5.0, clock=time.time):
        self.width, self.height, self.fps, self.clock = width, height, fps, clock
        self.pointer = (0.5, 0.5)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self.received = []

    def hello(self):
        return control(HELLO, {"version": VERSION, "width": self.width, "height": self.height,
                               "scale": 1, "codecs": ["png"], "source": "demo"})

    def render(self, step: int) -> bytes:
        w, h = self.width, self.height
        bar = (step * 12) % w
        px, py = int(self.pointer[0] * (w - 1)), int(self.pointer[1] * (h - 1))
        background = bytes((36, 100, 72))
        rows = []
        for y in range(h):
            row = bytearray(background * w)
            row[bar * 3:(bar + 24) * 3] = bytes((240, 200, 120)) * min(24, w - bar)
            if abs(y - py) <= 3:
                start, end = max(0, px - 12), min(w, px + 13)
                row[start * 3:end * 3] = b"\xff\xff\xff" * (end - start)
            if py - 12 <= y <= py + 12 and 0 <= px < w:
                for x in range(max(0, px - 3), min(w, px + 4)):
                    row[x * 3:x * 3 + 3] = b"\xff\xff\xff"
            rows.append(bytes(row))
        return png(w, h, rows)

    def start(self, send):
        send(self.hello())

        def run():
            step = 0
            while not self._stop.is_set():
                send(frame("png", self.render(step)))
                send(control(CURSOR, {"x": self.pointer[0], "y": self.pointer[1]}))
                step += 1
                self._wake.wait(1 / self.fps)
                self._wake.clear()
            send(control(BYE, {"reason": "beendet"}))

        self._thread = threading.Thread(target=run, name="demo-source", daemon=True)
        self._thread.start()

    def deliver(self, kind, payload):
        self.received.append((kind, payload))
        if kind == START and "max_fps" in payload:
            self.fps = min(float(payload["max_fps"]), 10.0)
        elif kind == POINTER:
            self.pointer = (payload["x"], payload["y"])
            self._wake.set()
        elif kind == KEYFRAME:
            self._wake.set()

    def stop(self):
        self._stop.set()
        self._wake.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
