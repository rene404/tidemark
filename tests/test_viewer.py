import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from macscope import managed, viewer
from macscope.server import make_server
from macscope.service import Service, ServiceError
from macscope.store import Store


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class BackendTests(unittest.TestCase):
    def test_host_validation(self):
        for host in ["imac", "imac.tail1234.ts.net", "100.101.102.103", "a-b.c"]:
            self.assertTrue(viewer.valid_host(host), host)
        for host in ["", "-imac", "user@imac", "imac:5900", "imac/x", "imac\n", "vnc://imac", "a..b", None, 5]:
            self.assertFalse(viewer.valid_host(host), host)
        with self.assertRaises(ValueError):
            viewer.AppleBackend("user@imac")

    def test_without_host_unavailable(self):
        caps = viewer.AppleBackend(None, probe=lambda: True).capabilities()
        self.assertFalse(caps["available"])
        self.assertIn("Viewer-Host", caps["reason"])

    def test_screen_sharing_off_is_reported(self):
        caps = viewer.AppleBackend("imac", probe=lambda: False).capabilities()
        self.assertFalse(caps["available"])
        self.assertIn("Bildschirmfreigabe ist aus", caps["reason"])

    def test_ready_backend_is_external_and_does_not_claim_enforced_modes(self):
        backend = viewer.AppleBackend("imac.tail1234.ts.net", probe=lambda: True)
        caps = backend.capabilities()
        self.assertTrue(caps["available"])
        self.assertEqual((caps["kind"], caps["modes_enforced"], caps["pre_login"]), ("external", False, True))
        self.assertEqual(backend.open_session("view"), {"kind": "external", "url": "vnc://imac.tail1234.ts.net"})

    def test_probe_is_cached(self):
        calls, clock = [], Clock()
        backend = viewer.AppleBackend("imac", probe=lambda: calls.append(1) or True, cache_seconds=30, clock=clock)
        backend.capabilities(); backend.capabilities()
        self.assertEqual(len(calls), 1)
        clock.now = 31
        backend.capabilities()
        self.assertEqual(len(calls), 2)

    def test_probe_against_closed_port(self):
        self.assertFalse(viewer.screen_sharing_listening(port=1, timeout=0.2))


NETSTAT = """Active Internet connections (including servers)
Proto Recv-Q Send-Q  Local Address          Foreign Address        (state)
tcp4       0      0  100.64.0.2.5900        100.64.0.7.51234       ESTABLISHED
tcp4       0      0  100.64.0.2.5900        100.64.0.7.51240       ESTABLISHED
tcp6       0      0  fd7a:115c::2.5900      fd7a:115c::7.50001     ESTABLISHED
tcp4       0      0  100.64.0.2.5900        100.64.0.9.40000       TIME_WAIT
tcp4       0      0  100.64.0.2.15900       100.64.0.8.40001       ESTABLISHED
tcp4       0      0  100.64.0.2.50000       100.64.0.5.5900        ESTABLISHED
tcp46      0      0  *.5900                 *.*                    LISTEN
"""


class Peers:
    def __init__(self):
        self.value = set()

    def __call__(self):
        return self.value


class ConnectionTests(unittest.TestCase):
    def test_parse_only_inbound_established_on_port(self):
        self.assertEqual(viewer.parse_peers(NETSTAT), {"100.64.0.7", "fd7a:115c::7"})
        self.assertEqual(viewer.parse_peers(""), set())

    def test_netstat_failure_is_none(self):
        def broken(*args, **kwargs):
            raise OSError("missing")
        self.assertIsNone(viewer.screen_sharing_peers(run=broken))

    def test_real_netstat_runs(self):
        self.assertIsInstance(viewer.screen_sharing_peers(port=1), (set, type(None)))

    def make(self):
        peers, clock, wall = Peers(), Clock(), Clock()
        wall.now = 1000.0
        return peers, clock, wall, viewer.ConnectionWatcher(peers, interval=5, clock=clock, wall=wall)

    def step(self, watcher, clock, wall, seconds=5):
        clock.now += seconds
        wall.now += seconds
        return watcher.poll()

    def test_open_needs_two_polls_and_close_reports_duration(self):
        peers, clock, wall, watcher = self.make()
        peers.value = {"100.64.0.7"}
        self.assertEqual(watcher.poll(), [])
        events = self.step(watcher, clock, wall)
        self.assertEqual([kind for kind, _ in events], ["viewer.opened"])
        self.assertEqual(events[0][1]["peer"], "100.64.0.7")
        self.assertEqual(watcher.connected(), [{"peer": "100.64.0.7", "since": 1005.0}])
        self.assertEqual(self.step(watcher, clock, wall, 120), [])
        peers.value = set()
        events = self.step(watcher, clock, wall)
        self.assertEqual([kind for kind, _ in events], ["viewer.closed"])
        self.assertEqual(events[0][1]["seconds"], 125)
        self.assertIn("2 min 5 s", events[0][1]["message"])
        self.assertEqual(watcher.connected(), [])

    def test_short_probe_connection_is_ignored(self):
        peers, clock, wall, watcher = self.make()
        peers.value = {"127.0.0.1"}
        watcher.poll()
        peers.value = set()
        self.assertEqual(self.step(watcher, clock, wall), [])
        self.assertEqual(self.step(watcher, clock, wall), [])

    def test_poll_is_rate_limited_and_failure_keeps_state(self):
        calls = []
        peers, clock, wall, watcher = self.make()
        peers.value = {"100.64.0.7"}
        counting = viewer.ConnectionWatcher(lambda: calls.append(1) or peers(), interval=5, clock=clock, wall=wall)
        counting.poll()
        counting.poll()
        self.assertEqual(len(calls), 1)
        self.step(counting, clock, wall)
        self.assertEqual(len(counting.connected()), 1)
        peers.value = None
        self.assertEqual(self.step(counting, clock, wall), [])
        self.assertEqual(len(counting.connected()), 1)

    def test_backend_exposes_connected(self):
        caps = viewer.AppleBackend("imac", probe=lambda: True,
                                   watcher=viewer.ConnectionWatcher(lambda: set())).capabilities()
        self.assertEqual(caps["connected"], [])
        self.assertEqual(viewer.DemoBackend().capabilities()["connected"], [])
        self.assertEqual(viewer.NoBackend().capabilities()["connected"], [])


class ServiceViewerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "state")

    def tearDown(self):
        self.temp.cleanup()

    def kinds(self):
        return [event["kind"] for event in self.store.events(limit=50)["events"]]

    def test_unavailable_viewer_is_refused_and_logged(self):
        service = Service(self.store, viewer=viewer.AppleBackend("imac", probe=lambda: False))
        with self.assertRaises(ServiceError) as caught:
            service.open_viewer("control")
        self.assertEqual(caught.exception.status, 409)
        self.assertIn("viewer.refused", self.kinds())
        service.close()

    def test_request_is_logged_even_while_paused(self):
        service = Service(self.store, viewer=viewer.AppleBackend("imac", probe=lambda: True))
        service.toggle_pause()
        self.assertEqual(service.open_viewer("view")["url"], "vnc://imac")
        event = [e for e in self.store.events(limit=50)["events"] if e["kind"] == "viewer.requested"][0]
        self.assertEqual(event["details"]["backend"], "apple")
        self.assertEqual(event["details"]["mode"], "view")
        service.close()

    def test_connections_are_logged_even_while_paused(self):
        peers, clock = Peers(), Clock()
        watcher = viewer.ConnectionWatcher(peers, interval=5, clock=clock)
        service = Service(self.store, viewer=viewer.AppleBackend("imac", probe=lambda: True, watcher=watcher))
        service.toggle_pause()
        peers.value = {"100.64.0.7"}
        service.watch_viewer()
        clock.now += 5
        service.watch_viewer()
        peers.value = set()
        clock.now += 5
        service.watch_viewer()
        self.assertIn("viewer.opened", self.kinds())
        self.assertIn("viewer.closed", self.kinds())
        event = [e for e in self.store.events(limit=50)["events"] if e["kind"] == "viewer.opened"][0]
        self.assertEqual(event["details"]["backend"], "apple")
        self.assertEqual(event["details"]["peer"], "100.64.0.7")
        service.close()

    def test_watcher_error_does_not_break_tick(self):
        def broken():
            raise RuntimeError("boom")
        service = Service(self.store, viewer=viewer.AppleBackend(
            "imac", probe=lambda: True, watcher=viewer.ConnectionWatcher(broken)))
        with self.assertLogs("macscope.service", level="ERROR"):
            service.watch_viewer()
        service.close()

    def test_invalid_mode_and_default_backend(self):
        service = Service(self.store)
        with self.assertRaises(ServiceError):
            service.open_viewer("admin")
        self.assertFalse(service.viewer_status()["available"])
        with self.assertRaises(ServiceError) as caught:
            service.open_viewer("view")
        self.assertEqual(caught.exception.status, 409)
        service.close()


class HTTPViewerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.service = Service(Store(Path(cls.temp.name) / "state"), viewer=viewer.DemoBackend())
        cls.server = make_server(cls.service, "correct-token", 0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)
        cls.service.close()
        cls.temp.cleanup()

    def call(self, path, method="GET", payload=None, token="correct-token"):
        headers = {"Authorization": "Bearer " + token}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, body=json.dumps(payload) if payload is not None else None,
                               headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read() or b"null")
        finally:
            connection.close()

    def test_capabilities_require_token(self):
        self.assertEqual(self.call("/api/viewer", token="wrong")[0], 401)
        status, data = self.call("/api/viewer")
        self.assertEqual(status, 200)
        self.assertEqual(data["backend"], "demo")

    def test_open_session(self):
        status, data = self.call("/api/viewer/session", "POST", {"mode": "control"})
        self.assertEqual(status, 201)
        self.assertTrue(data["demo"])

    def test_session_payload_is_strict(self):
        for payload in [{}, {"mode": 1}, {"mode": "control", "url": "vnc://evil"}, []]:
            with self.subTest(payload=payload):
                self.assertEqual(self.call("/api/viewer/session", "POST", payload)[0], 400)
        self.assertEqual(self.call("/api/viewer/session", "POST", {"mode": "root"})[0], 400)

    def test_unknown_post_path(self):
        self.assertEqual(self.call("/api/viewer/other", "POST", {"mode": "view"})[0], 404)


class ManagedViewerHostTests(unittest.TestCase):
    def test_viewer_host_in_managed_config(self):
        self.assertEqual(managed.parse({"target_user": "papa", "viewer_host": "imac.ts.net"}).viewer_host, "imac.ts.net")
        self.assertIsNone(managed.parse({"target_user": "papa"}).viewer_host)
        with self.assertRaises(managed.ManagedConfigError):
            managed.parse({"target_user": "papa", "viewer_host": "x@y"})


if __name__ == "__main__":
    unittest.main()
