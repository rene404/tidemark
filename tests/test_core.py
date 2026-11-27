import copy
import http.client
import json
import os
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from macscope.server import make_server
from macscope.service import Service, ServiceError
from macscope.store import Store, load_token

FIREFOX = {"name": "Firefox", "bundle_id": "org.mozilla.firefox", "pid": 1234, "launched_at": "100.123456"}
FINDER = {"name": "Finder", "bundle_id": "com.apple.finder", "pid": 100, "launched_at": "99.000000"}


class FakeAdapter:
    def __init__(self, apps=None, outcome="requested"):
        self.apps = copy.deepcopy(apps if apps is not None else [FIREFOX])
        self.outcome = outcome
        self.calls = []

    def quit(self, target):
        self.calls.append(target)
        return self.outcome if target in self.apps else "not_running"

    def is_running(self, target):
        return target in self.apps


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / "state"
        self.store = Store(self.directory)
        self.service = Service(self.store)
        self.service.update([FIREFOX, FINDER], FIREFOX)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def request(self, app=FIREFOX):
        return self.service.request_quit(app["pid"], app["bundle_id"], app["launched_at"])

    def test_request_is_not_reported_as_terminated_until_exit_observed(self):
        adapter = FakeAdapter()
        command_id = self.request()
        self.service.tick(adapter)
        self.assertEqual(self.store.command(command_id)["status"], "requested")
        adapter.apps.clear()
        self.service.tick(adapter)
        self.assertEqual(self.store.command(command_id)["status"], "terminated")
        kinds = [row["kind"] for row in self.store.events()["events"]]
        self.assertIn("action.queued", kinds)
        self.assertIn("action.terminated", kinds)

    def test_application_can_refuse_quit(self):
        command_id = self.request()
        self.service.tick(FakeAdapter(outcome="refused"))
        self.assertEqual(self.store.command(command_id)["status"], "refused")

    def test_pid_reused_after_queueing_does_not_quit_new_instance(self):
        command_id = self.request()
        replacement = {**FIREFOX, "launched_at": "200.000000"}
        self.service.tick(FakeAdapter([replacement]))
        self.assertEqual(self.store.command(command_id)["status"], "not_running")

    def test_ui_stale_instance_is_rejected_before_queueing(self):
        with self.assertRaises(ServiceError) as error:
            self.request({**FIREFOX, "launched_at": "old"})
        self.assertEqual(error.exception.status, 409)

    def test_unapproved_app_cannot_be_quit(self):
        with self.assertRaises(ServiceError) as error:
            self.request(FINDER)
        self.assertEqual(error.exception.status, 403)

    def test_stale_agent_cannot_accept_commands(self):
        with patch("macscope.service.time.monotonic", return_value=time.monotonic() + 11):
            with self.assertRaises(ServiceError) as error:
                self.request()
        self.assertEqual(error.exception.status, 503)

    def test_queued_command_expires_across_system_sleep(self):
        adapter = FakeAdapter()
        command_id = self.request()
        with patch("macscope.service.time.time", return_value=time.time() + 60):
            self.service.tick(adapter)
        self.assertEqual(adapter.calls, [])
        self.assertEqual(self.store.command(command_id)["status"], "expired")

    def test_app_that_keeps_running_is_unconfirmed(self):
        adapter = FakeAdapter()
        command_id = self.request()
        self.service.tick(adapter)
        with patch("macscope.service.time.time", return_value=time.time() + 20):
            self.service.tick(adapter)
        self.assertEqual(self.store.command(command_id)["status"], "unconfirmed")

    def test_duplicate_action_is_rejected(self):
        self.request()
        with self.assertRaises(ServiceError) as error:
            self.request()
        self.assertEqual(error.exception.status, 409)

    def test_shutdown_rejects_any_late_action_request(self):
        self.service.close()
        self.assertFalse(self.service.status()["agent_online"])
        with self.assertRaises(ServiceError) as error:
            self.request()
        self.assertEqual(error.exception.status, 503)

    def test_empty_local_allowlist_disables_actions(self):
        readonly = Service(self.store, allowed_bundles=[])
        readonly.update([FIREFOX])
        self.assertFalse(readonly.apps()[0]["can_quit"])
        with self.assertRaises(ServiceError) as error:
            readonly.request_quit(FIREFOX["pid"], FIREFOX["bundle_id"], FIREFOX["launched_at"])
        self.assertEqual(error.exception.status, 403)
        readonly.close()

    def test_restart_does_not_replay_pending_actions(self):
        command_id = self.request()
        replacement = Service(self.store)
        replacement.update([FIREFOX])
        adapter = FakeAdapter()
        replacement.tick(adapter)
        self.assertEqual(adapter.calls, [])
        self.assertEqual(self.store.command(command_id)["status"], "unconfirmed")
        replacement.close()

    def test_pause_suppresses_activity_but_preserves_action_audit(self):
        self.service.toggle_pause()
        self.service.record("app.activated", FIREFOX)
        command_id = self.request()
        kinds = [row["kind"] for row in self.store.events()["events"]]
        self.assertNotIn("app.activated", kinds)
        self.assertIn("action.queued", kinds)
        self.assertIsNotNone(self.store.command(command_id))

    def test_event_pagination_is_ordered_and_does_not_duplicate(self):
        ids = [self.store.event("app.activated", FIREFOX) for _ in range(7)]
        page = self.store.events(after=ids[0], limit=2)
        self.assertEqual([row["id"] for row in page["events"]], ids[1:3])
        self.assertTrue(page["has_more"])
        older = self.store.events(before=ids[3], limit=2)
        self.assertEqual([row["id"] for row in older["events"]], [ids[2],ids[1]])

    def test_search_does_not_interpret_sql_or_wildcards(self):
        self.store.event("app.activated", FIREFOX)
        self.assertEqual(len(self.store.events(query="Firefox")["events"]), 1)
        self.assertEqual(self.store.events(query="%' OR 1=1 --")["events"], [])
        self.assertEqual(self.store.events(query="%")["events"], [])

    def test_retention_removes_old_events(self):
        old = self.store.event("app.activated", FIREFOX, timestamp=time.time() - 31 * 86400)
        new = self.store.event("app.activated", FIREFOX)
        self.store.prune()
        ids = [row["id"] for row in self.store.events()["events"]]
        self.assertNotIn(old, ids)
        self.assertIn(new, ids)

    def test_secret_is_stable_and_private(self):
        token = load_token(self.directory)
        self.assertGreaterEqual(len(token), 32)
        self.assertEqual(load_token(self.directory), token)
        self.assertEqual(stat.S_IMODE((self.directory / "access-token").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)

    def test_token_symlink_is_rejected(self):
        target = Path(self.temp.name) / "unrelated"
        target.write_text("do not touch")
        (self.directory / "access-token").symlink_to(target)
        with self.assertRaises(OSError):
            load_token(self.directory)
        self.assertEqual(target.read_text(), "do not touch")


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.service = Service(Store(Path(cls.temp.name) / "state"))
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

    def call(self, path="/api/status", *, method="GET", headers=None, payload=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, body=json.dumps(payload) if payload is not None else None,
                               headers=headers or {})
            response = connection.getresponse()
            data = response.read()
            return response.status, dict(response.getheaders()), data
        finally:
            connection.close()

    def auth(self, **extra):
        return {"Authorization": "Bearer correct-token", **extra}

    def test_server_is_bound_only_to_loopback(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_no_data_without_token(self):
        status, _, _ = self.call()
        self.assertEqual(status, 401)

    def test_wrong_token_denied(self):
        status, _, _ = self.call(headers={"Authorization":"Bearer incorrect-token"})
        self.assertEqual(status, 401)

    def test_authenticated_status_and_security_headers(self):
        status, headers, data = self.call(headers=self.auth())
        self.assertEqual(status, 200)
        self.assertIn("retention_days", json.loads(data))
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_cross_origin_request_denied_even_with_token(self):
        status, _, _ = self.call(headers=self.auth(Origin="https://example.com"))
        self.assertEqual(status, 403)

    def test_dns_rebinding_host_is_denied(self):
        status, _, _ = self.call(headers=self.auth(Host=f"malicious.example:{self.port}"))
        self.assertEqual(status, 403)

    def test_static_dashboard_is_available_without_token(self):
        status, _, data = self.call("/")
        self.assertEqual(status, 200)
        self.assertIn(b"MacScope", data)
        self.assertNotIn(b"correct-token", data)

    def test_invalid_event_range_is_rejected(self):
        for path in ["/api/events?limit=501", "/api/events?before=-1", "/api/events?after=abc",
                     "/api/events?before=5&after=2"]:
            with self.subTest(path=path):
                self.assertEqual(self.call(path, headers=self.auth())[0], 400)

    def test_post_requires_json(self):
        status, _, _ = self.call("/api/commands/quit", method="POST", headers=self.auth(), payload={})
        self.assertEqual(status, 415)

    def test_post_queues_auditable_quit(self):
        self.service.update([FIREFOX])
        target = {key: FIREFOX[key] for key in ("pid","bundle_id","launched_at")}
        status, _, data = self.call("/api/commands/quit", method="POST",
                                   headers=self.auth(**{"Content-Type":"application/json"}), payload=target)
        self.assertEqual(status, 202)
        command_id = json.loads(data)["command_id"]
        status, _, data = self.call("/api/commands/" + command_id, headers=self.auth())
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["status"], "queued")

    def test_post_cannot_accept_arbitrary_commands(self):
        status, _, _ = self.call("/api/commands/quit", method="POST",
                                headers=self.auth(**{"Content-Type":"application/json"}),
                                payload={"command":"rm -rf something"})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
