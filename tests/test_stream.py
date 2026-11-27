import base64
import http.client
import json
import os
import socket
import struct
import tempfile
import threading
import time
import unittest
import zlib
from pathlib import Path

from macscope import stream, viewer, websocket
from macscope.server import make_server
from macscope.service import Service, ServiceError
from macscope.store import Store


def client_frame(opcode, payload=b"", fin=True, masked=True):
    """Frame wie ein Browser ihn schickt (maskiert)."""
    first = (0x80 if fin else 0) | opcode
    length = len(payload)
    if length < 126:
        header = struct.pack("!BB", first, (0x80 if masked else 0) | length)
    elif length < 1 << 16:
        header = struct.pack("!BBH", first, (0x80 if masked else 0) | 126, length)
    else:
        header = struct.pack("!BBQ", first, (0x80 if masked else 0) | 127, length)
    if not masked:
        return header + payload
    mask = os.urandom(4)
    return header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


def read_server_frame(sock):
    def exact(count):
        data = b""
        while len(data) < count:
            chunk = sock.recv(count - len(data))
            if not chunk:
                raise ConnectionError("zu")
            data += chunk
        return data
    first, second = exact(2)
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", exact(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", exact(8))[0]
    return first & 0x0F, exact(length)


class WebSocketTests(unittest.TestCase):
    def setUp(self):
        self.server_sock, self.client = socket.socketpair()
        self.ws = websocket.WebSocket(self.server_sock, max_message=1024, idle_timeout=0.2)

    def tearDown(self):
        self.server_sock.close()
        self.client.close()

    def test_accept_key_matches_rfc_example(self):
        self.assertEqual(websocket.accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_handshake_validation(self):
        good = {"Upgrade": "websocket", "Connection": "keep-alive, Upgrade", "Sec-WebSocket-Version": "13",
                "Sec-WebSocket-Key": base64.b64encode(b"x" * 16).decode()}
        self.assertEqual(websocket.handshake_key(good), good["Sec-WebSocket-Key"])
        for change in [{"Upgrade": "h2c"}, {"Connection": "close"}, {"Sec-WebSocket-Version": "8"},
                       {"Sec-WebSocket-Key": "kurz"}, {"Sec-WebSocket-Key": base64.b64encode(b"x" * 15).decode()}]:
            with self.assertRaises(ValueError, msg=change):
                websocket.handshake_key({**good, **change})

    def test_binary_ping_and_idle(self):
        self.assertIsNone(self.ws.receive())
        self.client.sendall(client_frame(websocket.PING, b"p") + client_frame(websocket.BINARY, b"\x01" * 300))
        self.assertEqual(self.ws.receive(), b"\x01" * 300)
        self.assertEqual(read_server_frame(self.client), (websocket.PONG, b"p"))
        sender = threading.Thread(target=self.ws.send, args=(b"x" * 70000,))
        sender.start()  # größer als der Socket-Puffer: Senden darf am Leerlauf-Takt nicht scheitern
        time.sleep(0.5)
        self.assertEqual(read_server_frame(self.client), (websocket.BINARY, b"x" * 70000))
        sender.join(timeout=2)

    def test_close_is_acknowledged(self):
        self.client.sendall(client_frame(websocket.CLOSE, struct.pack("!H", 1000)))
        with self.assertRaises(websocket.Closed) as caught:
            self.ws.receive()
        self.assertEqual(caught.exception.code, 1000)
        self.assertEqual(read_server_frame(self.client)[0], websocket.CLOSE)

    def test_protocol_violations_close_the_connection(self):
        cases = [(client_frame(websocket.BINARY, b"a", masked=False), websocket.PROTOCOL_ERROR),
                 (client_frame(websocket.TEXT, b"a"), websocket.UNSUPPORTED),
                 (client_frame(websocket.BINARY, b"a", fin=False), websocket.UNSUPPORTED),
                 (client_frame(websocket.BINARY, b"a" * 2000), websocket.TOO_BIG),
                 (client_frame(websocket.PING, b"a" * 126), websocket.PROTOCOL_ERROR)]
        for data, code in cases:
            with self.subTest(code=code):
                self.setUp()
                self.client.sendall(data)
                with self.assertRaises(websocket.Closed) as caught:
                    self.ws.receive()
                self.assertEqual(caught.exception.code, code)
                opcode, payload = read_server_frame(self.client)
                self.assertEqual((opcode, struct.unpack("!H", payload[:2])[0]), (websocket.CLOSE, code))
                self.tearDown()
        self.setUp()


class ProtocolTests(unittest.TestCase):
    def test_input_only_in_control_mode(self):
        pointer = stream.control(stream.POINTER, {"x": 0.5, "y": 0.25, "buttons": 1})
        with self.assertRaises(stream.ProtocolError):
            stream.check_viewer_message(pointer, "view")
        self.assertEqual(stream.check_viewer_message(pointer, "control"),
                         (stream.POINTER, {"x": 0.5, "y": 0.25, "buttons": 1}))
        self.assertEqual(stream.check_viewer_message(bytes([stream.KEYFRAME]), "view"), (stream.KEYFRAME, {}))

    def test_invalid_messages(self):
        bad = [b"", bytes([stream.FRAME]) + b"{}", bytes([stream.START]) + b"[]", bytes([stream.START]) + b"{x",
               stream.control(stream.POINTER, {"x": 2, "y": 0}), stream.control(stream.POINTER, {"x": float("nan"), "y": 0}),
               stream.control(stream.KEY, {"code": "KeyA"}), stream.control(stream.KEY, {"code": "x" * 40, "down": True}),
               stream.control(stream.START, {"max_fps": 1000}), bytes([stream.START]) + b" " * 5000]
        for message in bad:
            with self.subTest(message=message[:20]), self.assertRaises(stream.ProtocolError):
                stream.check_viewer_message(message, "control")

    def test_frame_round_trip_and_png(self):
        data = stream.DemoSource(width=40, height=20).render(3)
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        width, height = struct.unpack("!II", data[16:24])
        self.assertEqual((width, height), (40, 20))
        idat = data[data.index(b"IDAT") + 4:data.index(b"IEND") - 8]
        self.assertEqual(len(zlib.decompress(idat)), 20 * (1 + 40 * 3))
        parsed = stream.parse_frame(stream.frame("png", data, timestamp_ms=42))
        self.assertEqual((parsed["codec"], parsed["keyframe"], parsed["timestamp_ms"], parsed["data"]),
                         ("png", True, 42, data))

    def test_rate_limit(self):
        now = [0.0]
        limit = stream.RateLimit(rate=10, burst=3, clock=lambda: now[0])
        self.assertEqual([limit.allow() for _ in range(4)], [True, True, True, False])
        now[0] += 0.1
        self.assertTrue(limit.allow())


class FakeSocket:
    def __init__(self, incoming):
        self.incoming, self.sent, self.closed = list(incoming), [], None

    def receive(self):
        if not self.incoming:
            raise websocket.Closed(1001, "weg")
        item = self.incoming.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def send(self, message):
        self.sent.append(message)

    def close(self, code=1000, reason=""):
        self.closed = self.closed or code


class RelayTests(unittest.TestCase):
    def run_relay(self, incoming, mode="view", **kwargs):
        ws, source = FakeSocket(incoming), stream.DemoSource(width=16, height=8, fps=50)
        reason = stream.relay(ws, source, mode, threading.Event(), **kwargs)
        return reason, ws, source

    def test_view_mode_blocks_input(self):
        reason, ws, source = self.run_relay([stream.control(stream.START, {"max_fps": 5}),
                                             stream.control(stream.POINTER, {"x": 0.1, "y": 0.1})])
        self.assertIn("abgelehnt", reason)
        self.assertEqual(ws.closed, websocket.POLICY)
        self.assertEqual([kind for kind, _ in source.received], [stream.START])
        self.assertEqual(ws.sent[0][0], stream.HELLO)

    def test_control_mode_forwards_input_and_stop_ends(self):
        reason, ws, source = self.run_relay([None, stream.control(stream.POINTER, {"x": 0.1, "y": 0.9}),
                                             bytes([stream.STOP])], mode="control")
        self.assertEqual(reason, "vom Betrachter beendet")
        self.assertEqual(source.pointer, (0.1, 0.9))

    def test_rate_limit_closes(self):
        reason, ws, _ = self.run_relay([bytes([stream.KEYFRAME])] * 5, limit=stream.RateLimit(rate=0, burst=2))
        self.assertEqual(reason, "zu viele Nachrichten")
        self.assertEqual(ws.closed, websocket.POLICY)


class HTTPStreamTests(unittest.TestCase):
    TOKEN = "correct-token"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state")
        self.service = Service(self.store, viewer=viewer.DemoBackend())
        self.server = make_server(self.service, self.TOKEN, 0)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.service.close()
        self.temp.cleanup()

    def api(self, method, path, payload=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Authorization": "Bearer " + self.TOKEN, "Content-Type": "application/json"}
        connection.request(method, path, body=json.dumps(payload) if payload is not None else None, headers=headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, json.loads(data) if data else None

    def connect(self, path, origin=True):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode()
        host = f"127.0.0.1:{self.port}"
        request = (f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n"
                   + (f"Origin: http://{host}\r\n" if origin else "") + "\r\n")
        sock.sendall(request.encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += sock.recv(1)
        status = int(head.split(b" ", 2)[1])
        if status == 101:
            self.assertIn(websocket.accept_key(key).encode(), head)
        return status, sock

    def kinds(self):
        return [e["kind"] for e in self.store.events(limit=50)["events"]]

    def test_view_session_streams_frames_and_is_logged(self):
        _, session = self.api("POST", "/api/viewer/session", {"mode": "view"})
        status, sock = self.connect(session["stream"])
        with sock:
            self.assertEqual(status, 101)
            opcode, hello = read_server_frame(sock)
            self.assertEqual(hello[0], stream.HELLO)
            self.assertEqual(json.loads(hello[1:])["source"], "demo")
            sock.sendall(client_frame(websocket.BINARY, stream.control(stream.START, {"max_fps": 10})))
            while True:
                opcode, message = read_server_frame(sock)
                if message[0] == stream.FRAME:
                    break
            self.assertTrue(stream.parse_frame(message)["data"].startswith(b"\x89PNG"))
            self.assertEqual(self.api("GET", "/api/viewer")[1]["session"], {"mode": "view", "connected": True})
            sock.sendall(client_frame(websocket.BINARY, bytes([stream.STOP])))
            deadline = time.time() + 5
            while "viewer.closed" not in self.kinds() and time.time() < deadline:
                time.sleep(0.05)
        self.assertIn("viewer.opened", self.kinds())
        closed = [e for e in self.store.events(limit=50)["events"] if e["kind"] == "viewer.closed"][0]
        self.assertEqual(closed["details"]["mode"], "view")
        self.assertIn("vom Betrachter beendet", closed["details"]["message"])

    def test_session_id_is_single_use_and_checked(self):
        _, session = self.api("POST", "/api/viewer/session", {"mode": "view"})
        self.assertEqual(self.connect("/api/viewer/stream?session=falsch")[0], 403)
        self.assertEqual(self.connect(session["stream"], origin=False)[0], 403)
        status, sock = self.connect(session["stream"])
        with sock:
            self.assertEqual(status, 101)
            self.assertEqual(self.connect(session["stream"])[0], 403)

    def test_session_expires(self):
        _, session = self.api("POST", "/api/viewer/session", {"mode": "view"})
        self.service._session.created -= 31
        self.assertEqual(self.connect(session["stream"])[0], 403)

    def test_delete_ends_running_stream(self):
        _, session = self.api("POST", "/api/viewer/session", {"mode": "control"})
        status, sock = self.connect(session["stream"])
        with sock:
            self.assertEqual(self.api("DELETE", "/api/viewer/session")[0], 204)
            sock.settimeout(5)
            seen_close = False
            while not seen_close:
                opcode, _ = read_server_frame(sock)
                seen_close = opcode == websocket.CLOSE
        self.assertEqual(self.api("DELETE", "/api/viewer/session")[0], 404)

    def test_input_in_view_mode_is_refused_and_logged(self):
        _, session = self.api("POST", "/api/viewer/session", {"mode": "view"})
        status, sock = self.connect(session["stream"])
        with sock:
            sock.sendall(client_frame(websocket.BINARY, stream.control(stream.KEY, {"code": "KeyA", "down": True})))
            deadline = time.time() + 5
            while "viewer.closed" not in self.kinds() and time.time() < deadline:
                time.sleep(0.05)
        closed = [e for e in self.store.events(limit=50)["events"] if e["kind"] == "viewer.closed"][0]
        self.assertIn("Ansehen nicht erlaubt", closed["details"]["reason"])

    def test_apple_backend_has_no_stream(self):
        self.service.viewer = viewer.AppleBackend("imac", probe=lambda: True,
                                                  watcher=viewer.ConnectionWatcher(lambda: set()))
        self.assertEqual(self.connect("/api/viewer/stream?session=x")[0], 404)

    def test_delete_requires_token(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        connection.request("DELETE", "/api/viewer/session")
        self.assertEqual(connection.getresponse().status, 401)
        connection.close()


class ServiceSessionTests(unittest.TestCase):
    def test_new_session_replaces_unconnected_one(self):
        with tempfile.TemporaryDirectory() as temp:
            service = Service(Store(Path(temp) / "s"), viewer=viewer.DemoBackend())
            first = service.open_viewer("view")["session"]
            second = service.open_viewer("control")["session"]
            with self.assertRaises(ServiceError):
                service.attach_stream(first)
            self.assertEqual(service.attach_stream(second).mode, "control")
            service.close()


if __name__ == "__main__":
    unittest.main()
