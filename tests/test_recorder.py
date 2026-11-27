import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from macscope import managed, recorder
from macscope.recorder import RecorderServer, RecorderUnavailable, RemoteStore, handle
from macscope.service import Service, ServiceError
from macscope.store import Store

TOKEN = "t" * 43


class HandleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / "data", 30)

    def tearDown(self):
        self.temp.cleanup()

    def call(self, op, **args):
        return handle(self.store, lambda: TOKEN, {"op": op, "args": args})

    def test_only_whitelisted_operations_and_arguments(self):
        for request in [{"op": "delete"}, {"op": "connect"}, {"op": "_event"}, {"op": "events", "args": {"sql": "x"}},
                        {"op": "event", "args": []}, "event", None]:
            response = handle(self.store, lambda: TOKEN, request)
            self.assertFalse(response["ok"], request)
            self.assertEqual(response["status"], 400)

    def test_arguments_are_validated(self):
        for args in [{"limit": 10_000}, {"limit": "5"}, {"before": -1}, {"query": "x" * 201}, {"kind": 5}]:
            self.assertEqual(self.call("events", **args)["status"], 400, args)
        self.assertEqual(self.call("event", kind="")["status"], 400)
        self.assertEqual(self.call("event", kind="x", timestamp=time.time() + 3600)["status"], 400)
        self.assertEqual(self.call("set_command", command_id="nope", status="x", message="y")["status"], 400)

    def test_round_trip_ping_and_token(self):
        self.assertTrue(self.call("event", kind="app.launched", app={"name": "A", "bundle_id": "a", "pid": 1})["ok"])
        events = self.call("events", limit=5)["result"]["events"]
        self.assertEqual([e["kind"] for e in events], ["app.launched"])
        self.assertEqual(self.call("ping")["result"], {"retention_days": 30})
        self.assertEqual(self.call("token")["result"], TOKEN)


class SocketTests(unittest.TestCase):
    def setUp(self):
        # Kurzer Pfad: Unix-Sockets erlauben auf macOS höchstens 104 Zeichen.
        self.temp = tempfile.TemporaryDirectory(dir="/tmp")
        self.socket = Path(self.temp.name) / "r.sock"
        self.store = Store(Path(self.temp.name) / "data", 30)
        self.servers = []

    def tearDown(self):
        for server in self.servers:
            server.shutdown()
            server.server_close()
        self.temp.cleanup()

    def serve(self, allowed=None):
        server = RecorderServer(self.socket, self.store, lambda: TOKEN, allowed or {os.getuid()})
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.servers.append(server)
        return server

    def stop(self, server):
        server.shutdown()
        server.server_close()
        self.servers.remove(server)

    def kinds(self):
        return [e["kind"] for e in self.store.events(limit=50)["events"]]

    def test_peer_uid_is_own_uid(self):
        left, right = socket.socketpair(socket.AF_UNIX)
        with left, right:
            self.assertEqual(recorder.peer_uid(left), os.getuid())

    def test_remote_store_behaves_like_store(self):
        self.serve()
        remote = RemoteStore(self.socket)
        self.addCleanup(remote.close)
        self.assertEqual(remote.wait(1)["retention_days"], 30)
        self.assertEqual(remote.token(), TOKEN)
        app = {"name": "TextEdit", "bundle_id": "com.apple.TextEdit", "pid": 7, "launched_at": 1.0}
        remote.event("app.launched", app, {"x": 1})
        command_id = remote.create_command(app)
        remote.set_command(command_id, "requested", "läuft")
        self.assertEqual(remote.command(command_id)["status"], "requested")
        self.assertIsNone(remote.command("0" * 32))
        remote.recover_commands()
        self.assertEqual(remote.command(command_id)["status"], "unconfirmed")
        remote.prune()
        page = remote.events(limit=2, query="TextEdit")
        self.assertEqual(len(page["events"]), 2)
        self.assertTrue(page["has_more"])
        with self.assertRaises(ValueError):
            remote.events(limit=0)
        self.assertEqual(os.stat(self.socket).st_mode & 0o777, 0o600)

    def test_foreign_uid_is_rejected(self):
        self.serve(allowed={os.getuid() + 12345})
        with self.assertRaises(ServiceError) as caught:
            RemoteStore(self.socket).token()
        self.assertEqual(caught.exception.status, 403)

    def test_oversized_request_is_rejected(self):
        self.serve()
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(str(self.socket))
            client.sendall(b"x" * (recorder.MAX_REQUEST + 10) + b"\n")
            response = json.loads(client.makefile("rb").readline())
        self.assertEqual(response["status"], 413)

    def test_events_are_buffered_while_recorder_is_down(self):
        remote = RemoteStore(self.socket, timeout=1)
        self.addCleanup(remote.close)
        self.assertIsNone(remote.event("system.sleep", timestamp=1000.0))
        self.assertEqual(remote.pending(), 1)
        with self.assertRaises(RecorderUnavailable) as caught:
            remote.events(limit=5)
        self.assertEqual(caught.exception.status, 503)
        self.serve()
        remote.event("system.wake")
        self.assertEqual(remote.pending(), 0)
        events = self.store.events(limit=5)["events"]
        self.assertEqual([e["kind"] for e in events], ["system.wake", "system.sleep"])
        self.assertEqual(events[1]["timestamp"], 1000.0)

    def test_reconnects_after_recorder_restart(self):
        server = self.serve()
        remote = RemoteStore(self.socket, timeout=1)
        self.addCleanup(remote.close)
        remote.event("a.one")
        self.stop(server)
        self.serve()
        remote.event("a.two")
        self.assertEqual(remote.pending(), 0)
        self.assertEqual(self.kinds(), ["a.two", "a.one"])

    def test_wait_gives_up(self):
        sleeps = []
        with self.assertRaises(RecorderUnavailable):
            RemoteStore(self.socket, timeout=0.2).wait(0, sleep=sleeps.append)

    def test_refuses_to_replace_non_socket(self):
        self.socket.write_text("keine Socket-Datei")
        with self.assertRaises(ValueError):
            RecorderServer(self.socket, self.store, lambda: TOKEN, {os.getuid()})


class ServiceWithRecorderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir="/tmp")
        self.socket = Path(self.temp.name) / "r.sock"
        self.store = Store(Path(self.temp.name) / "data", 30)
        self.server = RecorderServer(self.socket, self.store, lambda: TOKEN, {os.getuid()})
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self.temp.cleanup()

    def test_outage_is_503_and_does_not_block_apps(self):
        remote = RemoteStore(self.socket, timeout=1)
        self.addCleanup(remote.close)
        service = Service(remote, allowed_bundles={"a.b"})
        app = {"name": "A", "bundle_id": "a.b", "pid": 5, "launched_at": 2.0}
        service.update([app])
        command_id = service.request_quit(5, "a.b", 2.0)
        self.server.shutdown()
        self.server.server_close()
        self.server = None
        os.unlink(self.socket)
        with self.assertRaises(ServiceError) as caught:
            service.request_quit(5, "a.b", 2.0)
        self.assertIn(caught.exception.status, (409, 503))
        with self.assertRaises(RecorderUnavailable):
            service._finish(command_id, app, "terminated", "ok")
        with self.assertRaises(RecorderUnavailable):
            service.request_quit(5, "a.b", 2.0)  # nicht mehr „läuft bereits“, sondern 503
        service.record("app.activated", app)
        self.assertEqual(remote.pending(), 1)
        with self.assertLogs("macscope.service", level="WARNING"):
            service.close()


class PlistTests(unittest.TestCase):
    def test_recorder_plist(self):
        import plistlib
        data = plistlib.loads(managed.recorder_plist(Path("/x/python")))
        self.assertEqual(data["ProgramArguments"], ["/x/python", "-I", "-m", "macscope.recorder"])
        self.assertEqual(data["KeepAlive"], {"SuccessfulExit": False})
        self.assertNotIn("UserName", data)
        self.assertFalse(str(managed.DATA_DIRECTORY).startswith(str(managed.SYSTEM_DIRECTORY) + "/"))
        self.assertLess(len(str(managed.RECORDER_SOCKET)), 104)


if __name__ == "__main__":
    unittest.main()
