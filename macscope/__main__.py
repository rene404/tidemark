from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import platform
import pwd
import shlex
import sys
import threading
from pathlib import Path

from . import managed as managed_config
from .service import Service
from .server import make_server
from .store import Store, load_token, private_directory


def main():
    parser = argparse.ArgumentParser(description="MacScope · lokale macOS-Zeitleiste")
    parser.add_argument("command", nargs="?", choices=["run", "token", "diagnose"], default="run")
    parser.add_argument("--demo", action="store_true", help="Simulation ohne Zugriff auf echte Apps")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--retention-days", type=int, default=30)
    parser.add_argument("--allow-app", action="append", default=[], help="Zusätzlich lokal freigegebene Bundle-ID")
    args = parser.parse_args()
    if args.command == "diagnose":
        try:
            config = managed_config.load()
            mode = f"verwaltet für {config.target_user}" if config else "unverwaltet"
        except managed_config.ManagedConfigError as error:
            mode = f"FEHLER: {error}"
        print(json.dumps({"system": platform.system(), "macos": platform.mac_ver()[0],
                          "architecture": platform.machine(), "python": platform.python_version(),
                          "mode": mode}, indent=2, ensure_ascii=False))
        return
    if not 1024 <= args.port <= 65535 or not 1 <= args.retention_days <= 3650:
        parser.error("Port muss 1024–65535, Aufbewahrung muss 1–3650 Tage sein.")
    if os.geteuid() == 0:
        parser.error("MacScope im angemeldeten Benutzerkonto starten, ohne sudo.")
    if args.command == "run" and not args.demo:
        if sys.platform != "darwin":
            parser.error("Die echte Ereignissammlung benötigt macOS. Vorschau: --demo")
        try:
            from .platform_macos import run
        except ImportError:
            parser.error("PyObjC fehlt. Bitte install.command auf dem iMac ausführen.")
    else:
        from .demo import run
    managed = None
    if args.command == "run" and not args.demo:
        try:
            managed = managed_config.load()
        except managed_config.ManagedConfigError as error:
            parser.error(f"Verwaltete Konfiguration unsicher oder fehlerhaft: {error}")
    if managed:
        user = pwd.getpwuid(os.getuid()).pw_name
        if user != managed.target_user:
            # Exit 0: launchd startet den Agenten für fremde Konten nicht neu.
            print(f"Verwalteter Modus gilt für {managed.target_user}, nicht für {user}. Kein Start.")
            return
        if args.allow_app or args.port != 8765 or args.retention_days != 30:
            print("Verwalteter Modus: Einstellungen kommen aus managed.json, Startoptionen werden ignoriert.")
        args.port, args.retention_days = managed.port, managed.retention_days
    directory = args.data_dir or Path.home() / "Library" / "Application Support" / ("MacScope-Demo" if args.demo else "MacScope")
    private_directory(directory)
    # SQLite sidecars and future local files inherit private permissions.
    os.umask(0o077)
    if args.command == "token":
        print(load_token(directory))
        return
    lock_fd = os.open(directory / "agent.lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(lock_fd)
        parser.error("In diesem Datenverzeichnis läuft bereits ein MacScope-Agent.")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if managed:
        # /Library/LaunchAgents kennt kein ~; Protokoll daher selbst ins Datenverzeichnis.
        private_directory(directory / "logs")
        handler = logging.FileHandler(directory / "logs" / "agent.log")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logging.getLogger().addHandler(handler)
    store = Store(directory, args.retention_days)
    token = load_token(directory)
    allowed = set(managed.allow_apps) if managed else {"org.mozilla.firefox", *args.allow_app}
    service = Service(store, allowed_bundles=allowed, demo=args.demo, managed=bool(managed))
    server = None
    try:
        server = make_server(service, token, args.port)
        thread = threading.Thread(target=server.serve_forever, name="macscope-http", daemon=True)
        thread.start()
        print(f"MacScope {'DEMO' if args.demo else 'aktiv'}: http://127.0.0.1:{args.port}/", flush=True)
        print("Zugriffsschlüssel in einem zweiten Terminal: macscope token --data-dir " + shlex.quote(str(directory)), flush=True)
        run(service, args.port, managed=bool(managed))
    except OSError as error:
        logging.error("MacScope konnte nicht starten: %s", error)
        raise SystemExit(1) from error
    finally:
        if server:
            server.shutdown()
            server.server_close()
        service.close()
        os.close(lock_fd)
    if managed:
        # Jedes Ende im verwalteten Modus gilt als unerwartet: launchd startet neu.
        raise SystemExit(managed_config.EXIT_RESTART)


if __name__ == "__main__":
    main()
