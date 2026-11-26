"""AppKit integration, used exclusively on the logged-in user's main thread."""
from __future__ import annotations

import logging
import signal
import time

import AppKit
import Foundation
import objc

log = logging.getLogger(__name__)


def describe(app):
    launched = app.launchDate()
    return {
        "name": str(app.localizedName() or app.bundleIdentifier() or "Unbekannte App"),
        "bundle_id": str(app.bundleIdentifier() or ""),
        "pid": int(app.processIdentifier()),
        "launched_at": f"{launched.timeIntervalSince1970():.6f}" if launched else "",
    }


class Adapter:
    def __init__(self):
        self.workspace = AppKit.NSWorkspace.sharedWorkspace()

    def apps(self):
        return sorted([describe(app) for app in self.workspace.runningApplications()
                       if app.activationPolicy() == AppKit.NSApplicationActivationPolicyRegular
                       and not app.isTerminated()], key=lambda app: app["name"].casefold())

    def active(self):
        app = self.workspace.frontmostApplication()
        return describe(app) if app else None

    def find(self, target):
        app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(target["pid"])
        if app is None or app.isTerminated():
            return None
        current = describe(app)
        if (current["bundle_id"], current["launched_at"]) != (target["bundle_id"], target["launched_at"]):
            return None
        if app.activationPolicy() != AppKit.NSApplicationActivationPolicyRegular:
            return None
        return app

    def quit(self, target):
        app = self.find(target)
        if app is None:
            return "not_running"
        # terminate() acknowledges the request; it does NOT prove the app has exited.
        return "requested" if app.terminate() else "refused"

    def is_running(self, target):
        return self.find(target) is not None


class MenuDelegate(Foundation.NSObject):
    @objc.python_method
    def configure(self, service, port, managed=False):
        self.service = service
        self.port = port
        self.managed = managed
        self.adapter = Adapter()
        self.observers = []
        self.last_prune = time.monotonic()
        self.failed = False

    def applicationDidFinishLaunching_(self, notification):
        try:
            self.setupMenu()
            center = self.adapter.workspace.notificationCenter()
            mapping = {
                AppKit.NSWorkspaceDidLaunchApplicationNotification: "app.launched",
                AppKit.NSWorkspaceDidTerminateApplicationNotification: "app.terminated",
                AppKit.NSWorkspaceDidActivateApplicationNotification: "app.activated",
                AppKit.NSWorkspaceWillSleepNotification: "system.sleep",
                AppKit.NSWorkspaceDidWakeNotification: "system.wake",
                AppKit.NSWorkspaceSessionDidBecomeActiveNotification: "session.active",
                AppKit.NSWorkspaceSessionDidResignActiveNotification: "session.inactive",
            }
            for notification_name, kind in mapping.items():
                def receive(note, event_kind=kind):
                    try:
                        info = note.userInfo() or {}
                        app = info.get(AppKit.NSWorkspaceApplicationKey)
                        self.service.record(event_kind, describe(app) if app else None)
                        if event_kind != "system.sleep":
                            self.refresh()
                    except Exception:
                        log.exception("Workspace-Ereignis konnte nicht verarbeitet werden")
                observer = center.addObserverForName_object_queue_usingBlock_(
                    notification_name, None, Foundation.NSOperationQueue.mainQueue(), receive)
                self.observers.append(observer)
            self.refresh()
            for app in self.adapter.apps():
                self.service.record("app.observed", app)
            self.timer = Foundation.NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                1.0, self, "tick:", None, True)
        except Exception:
            log.exception("MacScope konnte AppKit nicht starten")
            self.failed = True
            AppKit.NSApplication.sharedApplication().terminate_(None)

    @objc.python_method
    def refresh(self):
        self.service.update(self.adapter.apps(), self.adapter.active())

    @objc.python_method
    def setupMenu(self):
        self.item = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(AppKit.NSVariableStatusItemLength)
        self.item.button().setTitle_("◉ MS")
        self.item.button().setToolTip_("MacScope zeichnet Anwendungsereignisse auf"
                                       + (" (verwaltet)" if self.managed else ""))
        menu = AppKit.NSMenu.alloc().init()
        title = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("MacScope · Aufzeichnung aktiv", None, "")
        title.setEnabled_(False)
        menu.addItem_(title)
        self.title_item = title
        entries = [("Dashboard öffnen", "openDashboard:")]
        if not self.managed:
            # Verwalteter Modus: weder Pausieren noch Beenden über die Menüleiste.
            entries += [("Aufzeichnung pausieren", "togglePause:"), ("MacScope beenden", "stop:")]
        for label, action in entries:
            item = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(label, action, "")
            item.setTarget_(self)
            menu.addItem_(item)
            if action == "togglePause:":
                self.pause_item = item
        self.item.setMenu_(menu)

    def tick_(self, timer):
        try:
            self.refresh()
            self.service.tick(self.adapter)
            if time.monotonic() - self.last_prune > 3600:
                self.service.store.prune()
                self.last_prune = time.monotonic()
        except Exception:
            log.exception("MacScope-Aktualisierung fehlgeschlagen")

    def openDashboard_(self, sender):
        url = Foundation.NSURL.URLWithString_(f"http://127.0.0.1:{self.port}/")
        self.adapter.workspace.openURL_(url)

    def togglePause_(self, sender):
        if self.managed:
            return
        paused = self.service.toggle_pause()
        self.item.button().setTitle_("Ⅱ MS" if paused else "◉ MS")
        self.title_item.setTitle_("MacScope · pausiert" if paused else "MacScope · Aufzeichnung aktiv")
        self.pause_item.setTitle_("Aufzeichnung fortsetzen" if paused else "Aufzeichnung pausieren")
        if not paused:
            for app in self.adapter.apps():
                self.service.record("app.observed", app)

    def stop_(self, sender):
        if self.managed:
            return
        AppKit.NSApplication.sharedApplication().terminate_(None)

    def applicationWillTerminate_(self, notification):
        if hasattr(self, "timer"):
            self.timer.invalidate()
        center = self.adapter.workspace.notificationCenter()
        for observer in self.observers:
            center.removeObserver_(observer)
        self.service.close()


def run(service, port, managed=False):
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    delegate = MenuDelegate.alloc().init()
    delegate.configure(service, port, managed)
    app.setDelegate_(delegate)
    for name in (signal.SIGINT, signal.SIGTERM):
        signal.signal(name, lambda *_: app.terminate_(None))
    app.run()
    if delegate.failed:
        raise RuntimeError("Die macOS-Anbindung konnte nicht gestartet werden.")
