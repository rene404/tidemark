"""Explicit simulation; never executes an OS command or terminates a real app."""
import signal
import time


class DemoAdapter:
    def __init__(self):
        self.running = [
            {"name": "Firefox", "bundle_id": "org.mozilla.firefox", "pid": 4101, "launched_at": "demo-firefox-1"},
            {"name": "Finder", "bundle_id": "com.apple.finder", "pid": 4000, "launched_at": "demo-finder-1"},
            {"name": "Notizen", "bundle_id": "com.apple.Notes", "pid": 4200, "launched_at": "demo-notes-1"},
        ]
        self.deadlines = {}

    def quit(self, target):
        self.deadlines[target["pid"]] = time.monotonic() + 2
        return "requested"

    def is_running(self, target):
        return target in self.running


def run(service, port, managed=False):
    adapter = DemoAdapter()
    stopped = False
    def stop(*_):
        nonlocal stopped
        stopped = True
    for name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(name, stop)
    for app in adapter.running:
        service.record("app.observed", app)
    last_focus = time.monotonic() - 8
    focus_index = 0
    while not stopped:
        for app in list(adapter.running):
            if adapter.deadlines.get(app["pid"], float("inf")) <= time.monotonic():
                adapter.running.remove(app)
                service.record("app.terminated", app)
        if time.monotonic() - last_focus >= 8 and adapter.running:
            focus_index = (focus_index + 1) % len(adapter.running)
            service.record("app.activated", adapter.running[focus_index])
            last_focus = time.monotonic()
        active = adapter.running[focus_index % len(adapter.running)] if adapter.running else None
        service.update(adapter.running, active)
        service.tick(adapter)
        time.sleep(0.25)
