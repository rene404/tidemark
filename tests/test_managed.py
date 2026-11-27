import json
import logging
import logging.handlers
import os
import plistlib
import pwd
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
from pathlib import Path

from macscope import __main__ as cli, managed, watchdog
from macscope.service import Service, ServiceError
from macscope.store import Store

ME = pwd.getpwuid(os.getuid()).pw_name


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name) / "system"
        self.directory.mkdir(mode=0o755)
        self.directory.chmod(0o755)
        self.path = self.directory / "managed.json"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, data, mode=0o644):
        self.path.write_text(json.dumps(data))
        self.path.chmod(mode)

    def load(self):
        return managed.load(self.path, owner=os.getuid())

    def test_missing_file_means_unmanaged(self):
        self.assertIsNone(self.load())

    def test_valid_file_with_defaults(self):
        self.write({"target_user": "papa"})
        config = self.load()
        self.assertEqual(config.target_user, "papa")
        self.assertEqual(config.allow_apps, ("org.mozilla.firefox",))
        self.assertEqual((config.retention_days, config.port), (30, 8765))

    def test_writable_by_others_is_refused(self):
        self.write({"target_user": "papa"}, mode=0o666)
        with self.assertRaises(managed.ManagedConfigError):
            self.load()

    def test_writable_parent_directory_is_refused(self):
        self.write({"target_user": "papa"})
        self.directory.chmod(0o777)
        with self.assertRaises(managed.ManagedConfigError):
            self.load()

    def test_symlink_is_refused(self):
        real = self.directory / "real.json"
        real.write_text(json.dumps({"target_user": "papa"}))
        self.path.symlink_to(real)
        with self.assertRaises(managed.ManagedConfigError):
            self.load()

    def test_foreign_owner_is_refused(self):
        if os.getuid() == 0:
            self.skipTest("als root gehört alles root")
        self.write({"target_user": "papa"})
        with self.assertRaises(managed.ManagedConfigError):
            managed.load(self.path, owner=os.getuid() + 12345)

    def test_invalid_contents_are_refused(self):
        for data in [[], {"target_user": "root"}, {"target_user": ""}, {"target_user": "papa", "extra": 1},
                     {"target_user": "papa", "allow_apps": "org.mozilla.firefox"},
                     {"target_user": "papa", "retention_days": 0}, {"target_user": "papa", "port": 80},
                     {"target_user": "papa", "port": True}]:
            with self.subTest(data=data):
                self.write(data)
                with self.assertRaises(managed.ManagedConfigError):
                    self.load()

    def test_broken_json_is_refused(self):
        self.path.write_text("{")
        self.path.chmod(0o644)
        with self.assertRaises(managed.ManagedConfigError):
            self.load()


class PlistTests(unittest.TestCase):
    def test_agent_restarts_on_unexpected_exit_and_ignores_user_environment(self):
        spec = plistlib.loads(managed.agent_plist(Path("/x/python")))
        self.assertEqual(spec["Label"], managed.AGENT_LABEL)
        self.assertEqual(spec["ProgramArguments"], ["/x/python", "-I", "-m", "macscope", "run"])
        self.assertEqual(spec["KeepAlive"], {"SuccessfulExit": False})
        self.assertEqual(spec["LimitLoadToSessionType"], "Aqua")
        self.assertNotEqual(managed.EXIT_RESTART, 0)

    def test_watchdog_runs_periodically(self):
        spec = plistlib.loads(managed.watchdog_plist(Path("/x/python"), interval=30))
        self.assertEqual(spec["ProgramArguments"], ["/x/python", "-I", "-m", "macscope.watchdog"])
        self.assertEqual(spec["StartInterval"], 30)
        self.assertTrue(spec["RunAtLoad"])


def result(code=0, stdout=""):
    return subprocess.CompletedProcess([], code, stdout, "fehler" if code else "")


class FakeLaunchctl:
    def __init__(self, logged_in=True, loaded=True, running=True, fail=False):
        self.logged_in, self.loaded, self.running, self.fail = logged_in, loaded, running, fail
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)
        if args[0] == "print" and "/" + managed.AGENT_LABEL not in args[1]:
            return result(0 if self.logged_in else 113)
        if args[0] == "print":
            state = "state = running\n\tpid = 4242" if self.running else "state = not running"
            return result(0, state) if self.loaded else result(113)
        return result(1 if self.fail else 0)


FRESH = lambda home: 5.0  # noqa: E731


class WatchdogTests(unittest.TestCase):
    config = managed.ManagedConfig(ME)

    def check(self, fake, **kwargs):
        kwargs.setdefault("heartbeat", FRESH)
        return watchdog.check(self.config, fake, **kwargs)

    def test_nothing_when_user_not_logged_in(self):
        fake = FakeLaunchctl(logged_in=False)
        self.assertIsNone(self.check(fake))
        self.assertEqual(len(fake.calls), 1)

    def test_nothing_when_agent_running(self):
        fake = FakeLaunchctl()
        self.assertIsNone(self.check(fake))
        self.assertFalse([c for c in fake.calls if c[0] != "print"])

    def test_unloaded_agent_is_bootstrapped(self):
        fake = FakeLaunchctl(loaded=False)
        self.assertIn("nachgeladen", self.check(fake, agent_plist=Path("/L/a.plist")))
        self.assertEqual(fake.calls[-1], ("bootstrap", f"gui/{os.getuid()}", "/L/a.plist"))

    def test_stopped_agent_is_kickstarted(self):
        fake = FakeLaunchctl(running=False)
        self.assertIn("gestartet", self.check(fake))
        self.assertEqual(fake.calls[-1], ("kickstart", f"gui/{os.getuid()}/{managed.AGENT_LABEL}"))

    def test_failures_are_reported(self):
        self.assertIn("fehlgeschlagen", self.check(FakeLaunchctl(loaded=False, fail=True)))
        self.assertIn("fehlgeschlagen", self.check(FakeLaunchctl(running=False, fail=True)))

    def test_unknown_user_is_reported(self):
        fake = FakeLaunchctl()
        self.assertIn("existiert nicht", watchdog.check(managed.ManagedConfig("gibt-es-nicht-xyz"), fake))
        self.assertEqual(fake.calls, [])

    def test_stale_heartbeat_needs_two_consecutive_checks(self):
        state, resumed = {}, []
        stale = lambda home: 600.0  # noqa: E731
        fake = FakeLaunchctl()
        self.assertIsNone(self.check(fake, state=state, heartbeat=stale, resume=resumed.append))
        self.assertTrue(state["stale"])
        self.assertFalse([c for c in fake.calls if c[0] == "kickstart"])
        message = self.check(fake, state=state, heartbeat=stale, resume=resumed.append)
        self.assertIn("seit 600 s veraltet", message)
        self.assertEqual(resumed, [4242])
        self.assertEqual(fake.calls[-1], ("kickstart", "-k", f"gui/{os.getuid()}/{managed.AGENT_LABEL}"))
        self.assertEqual(state, {})

    def test_heartbeat_recovering_after_wake_does_not_restart(self):
        state, fake = {}, FakeLaunchctl()
        self.check(fake, state=state, heartbeat=lambda home: 3600.0)
        self.assertIsNone(self.check(fake, state=state, heartbeat=FRESH))
        self.assertEqual(state, {})
        self.assertFalse([c for c in fake.calls if c[0] == "kickstart"])

    def test_missing_heartbeat_restarts_on_second_check(self):
        state, fake = {}, FakeLaunchctl()
        missing = lambda home: None  # noqa: E731
        self.assertIsNone(self.check(fake, state=state, heartbeat=missing, resume=lambda pid: None))
        self.assertIn("fehlt", self.check(fake, state=state, heartbeat=missing, resume=lambda pid: None))

    def test_unloaded_or_stopped_agent_resets_stale_state(self):
        for fake in [FakeLaunchctl(loaded=False), FakeLaunchctl(running=False), FakeLaunchctl(logged_in=False)]:
            state = {"stale": True}
            self.check(fake, state=state)
            self.assertEqual(state, {})

    def test_heartbeat_age_reads_mtime_without_following_links(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertIsNone(watchdog.heartbeat_age(home))
            directory = managed.user_data_directory(home)
            directory.mkdir(parents=True)
            beat = directory / managed.HEARTBEAT_NAME
            beat.touch()
            os.utime(beat, (1000, 1000))
            self.assertEqual(watchdog.heartbeat_age(home, now=lambda: 1300), 300)


class HeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_beat_creates_private_file_and_respects_interval(self):
        path = self.directory / "heartbeat"
        service = Service(Store(self.directory / "state"), managed=True, heartbeat_path=path, heartbeat_interval=60)
        service.beat()
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        os.utime(path, (1000, 1000))
        service.beat()  # innerhalb des Intervalls: keine Aktualisierung
        self.assertEqual(path.stat().st_mtime, 1000)
        service._last_beat -= 61
        service.beat()
        self.assertGreater(path.stat().st_mtime, 1000)
        service.close()

    def test_symlinked_heartbeat_is_not_followed(self):
        target = self.directory / "elsewhere"
        target.write_text("x")
        os.utime(target, (1000, 1000))
        path = self.directory / "heartbeat"
        path.symlink_to(target)
        service = Service(Store(self.directory / "state"), managed=True, heartbeat_path=path)
        with self.assertLogs("macscope.service", level="WARNING"):
            service.beat()
        self.assertEqual(target.stat().st_mtime, 1000)
        service.close()

    def test_unmanaged_service_writes_no_heartbeat(self):
        service = Service(Store(self.directory / "state"))
        service.beat()
        self.assertFalse((self.directory / "heartbeat").exists())
        service.close()


class LoggingTests(unittest.TestCase):
    def test_rotating_log_in_data_directory(self):
        root = logging.getLogger()
        before = list(root.handlers)
        with tempfile.TemporaryDirectory() as temp:
            try:
                cli.configure_logging(Path(temp))
                added = [h for h in root.handlers if h not in before]
                rotating = [h for h in added if isinstance(h, logging.handlers.RotatingFileHandler)]
                self.assertEqual(len(rotating), 1)
                self.assertEqual((rotating[0].maxBytes, rotating[0].backupCount), (1_000_000, 3))
                self.assertTrue((Path(temp) / "logs" / "agent.log").exists())
            finally:
                for handler in root.handlers[:]:
                    if handler not in before:
                        root.removeHandler(handler)
                        handler.close()

    def test_newsyslog_rotates_watchdog_log(self):
        line = managed.newsyslog_conf().splitlines()[-1].split("\t")
        self.assertEqual(line, [str(managed.WATCHDOG_LOG), "root:wheel", "644", "3", "1024", "*", "N"])


class ManagedServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Service(Store(Path(self.temp.name) / "state"), managed=True)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_pause_is_refused_and_status_reports_managed(self):
        with self.assertRaises(ServiceError) as caught:
            self.service.toggle_pause()
        self.assertEqual(caught.exception.status, 403)
        self.assertTrue(self.service.status()["managed"])
        self.assertFalse(self.service.status()["paused"])


class ManagedStartTests(unittest.TestCase):
    def setUp(self):
        root = logging.getLogger()
        handlers = list(root.handlers)
        def restore():
            for handler in root.handlers[:]:
                if handler not in handlers:
                    root.removeHandler(handler)
                    handler.close()
        self.addCleanup(restore)

    def start(self, config, *argv):
        fake_platform = types.SimpleNamespace(run=lambda *a, **k: self.fail("Agent darf nicht starten"))
        with patch.object(sys, "argv", ["macscope", "run", *argv]), \
             patch.object(sys, "platform", "darwin"), \
             patch.dict(sys.modules, {"macscope.platform_macos": fake_platform}), \
             patch.object(cli.managed_config, "load", return_value=config), patch("builtins.print"):
            return cli.main()

    def test_other_account_exits_cleanly_without_starting(self):
        self.assertIsNone(self.start(managed.ManagedConfig("gibt-es-nicht-xyz")))

    def test_unsafe_config_refuses_to_start(self):
        with patch.object(cli.managed_config, "load", side_effect=managed.ManagedConfigError("unsicher")), \
             patch.object(sys, "argv", ["macscope", "run"]), patch.object(sys, "platform", "darwin"), \
             patch.dict(sys.modules, {"macscope.platform_macos": types.SimpleNamespace(run=None)}), \
             patch("sys.stderr"):
            with self.assertRaises(SystemExit) as caught:
                cli.main()
        self.assertEqual(caught.exception.code, 2)

    def test_target_account_runs_managed_and_exits_for_restart(self):
        calls = []
        with tempfile.TemporaryDirectory() as temp:
            fake_platform = types.SimpleNamespace(run=lambda service, port, managed=False: calls.append(
                (service.managed, service.allowed_bundles, port, managed)))
            config = managed.ManagedConfig(ME, ("com.apple.TextEdit",), 14, 8799)
            with patch.object(sys, "argv", ["macscope", "run", "--data-dir", temp + "/d", "--allow-app", "x.y"]), \
                 patch.object(sys, "platform", "darwin"), \
                 patch.dict(sys.modules, {"macscope.platform_macos": fake_platform}), \
                 patch.object(cli.managed_config, "load", return_value=config), patch("builtins.print"):
                with self.assertRaises(SystemExit) as caught:
                    cli.main()
        self.assertEqual(caught.exception.code, managed.EXIT_RESTART)
        self.assertEqual(calls, [(True, frozenset({"com.apple.TextEdit"}), 8799, True)])


if __name__ == "__main__":
    unittest.main()
