"""Verwalteten Modus einrichten/entfernen (als root, über install-managed.command).

install: Programm nach /Library/Application Support/MacScope (gehört root),
         managed.json schreiben, systemweiten LaunchAgent + root-Wächter laden.
remove:  alles davon wieder entfernen; Verlaufsdaten des Benutzers bleiben.
"""
import argparse
import grp
import json
import os
import pathlib
import pwd
import shutil
import stat
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from macscope import managed  # noqa: E402

APP = managed.SYSTEM_DIRECTORY / "app"
VENV = managed.SYSTEM_DIRECTORY / "venv"
PYTHON = VENV / "bin" / "python"
OLD_LABEL = "local.macscope.agent"


def launchctl(*args, check=False):
    result = subprocess.run(["/bin/launchctl", *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise SystemExit(f"launchctl {' '.join(args)} fehlgeschlagen: {result.stderr.strip()}")
    return result


def is_admin(user):
    return subprocess.run(["/usr/sbin/dseditgroup", "-o", "checkmember", "-m", user, "admin"],
                          capture_output=True).returncode == 0


def logged_in(uid):
    return launchctl("print", f"gui/{uid}").returncode == 0


def lock_down(path):
    """root:wheel, für niemand anderen beschreibbar — rekursiv."""
    wheel = grp.getgrnam("wheel").gr_gid
    for current, directories, files in os.walk(path):
        for name in [current, *(os.path.join(current, f) for f in files + directories)]:
            os.lchown(name, 0, wheel)
            mode = os.lstat(name).st_mode
            if not stat.S_ISLNK(mode):
                os.chmod(name, stat.S_IMODE(mode) & ~0o022)


def write_root_file(path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".neu")
    temporary.write_bytes(data)
    os.chown(temporary, 0, 0)
    temporary.chmod(0o644)
    temporary.replace(path)


def check_interpreter(target_uid):
    real = pathlib.Path(sys.executable).resolve()
    info = real.stat()
    if not (3, 11) <= sys.version_info[:2] < (3, 15):
        raise SystemExit("Python 3.11–3.14 erforderlich.")
    if info.st_uid == target_uid or info.st_mode & stat.S_IWOTH:
        raise SystemExit(f"{real} darf dem Zielbenutzer nicht gehören/von ihm beschreibbar sein.")


def remove_old_user_agent(account):
    plist = pathlib.Path(account.pw_dir) / "Library" / "LaunchAgents" / (OLD_LABEL + ".plist")
    launchctl("bootout", f"gui/{account.pw_uid}/{OLD_LABEL}")
    if plist.exists():
        plist.unlink()
        print(f"Alter Benutzer-Autostart entfernt: {plist}")


def install(args):
    try:
        account = pwd.getpwnam(args.user)
    except KeyError:
        raise SystemExit(f"Benutzer {args.user} existiert nicht.")
    if account.pw_uid < 501:
        raise SystemExit("Zielbenutzer muss ein normales Anmeldekonto sein.")
    if is_admin(args.user) and not args.erlaube_admin:
        raise SystemExit(f"{args.user} ist Administrator und könnte alles wieder abschalten. "
                         "Erst zum Standardbenutzer machen (oder --erlaube-admin für Tests).")
    check_interpreter(account.pw_uid)
    config = managed.parse({"target_user": args.user, "allow_apps": args.allow_app or ["org.mozilla.firefox"],
                            "retention_days": args.retention_days, "port": 8765,
                            "viewer_host": args.viewer_host})

    managed.SYSTEM_DIRECTORY.mkdir(parents=True, exist_ok=True)
    if APP.exists():
        shutil.rmtree(APP)
    APP.mkdir()
    for name in ["macscope", "scripts", "docs"]:
        shutil.copytree(ROOT / name, APP / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for name in ["pyproject.toml", "README.md"]:
        shutil.copy2(ROOT / name, APP / name)
    if not PYTHON.exists():
        subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True)
    subprocess.run([str(PYTHON), "-m", "pip", "install", "--quiet", "--upgrade", str(APP)], check=True)
    lock_down(managed.SYSTEM_DIRECTORY)
    managed.SYSTEM_DIRECTORY.chmod(0o755)

    write_root_file(managed.CONFIG_PATH, json.dumps({
        "target_user": config.target_user, "allow_apps": list(config.allow_apps),
        "retention_days": config.retention_days, "port": config.port,
        **({"viewer_host": config.viewer_host} if config.viewer_host else {})}, indent=2).encode())
    managed.load()  # Selbstprüfung: Besitz/Rechte so, wie der Agent sie verlangt

    remove_old_user_agent(account)
    write_root_file(managed.AGENT_PLIST, managed.agent_plist(PYTHON))
    write_root_file(managed.WATCHDOG_PLIST, managed.watchdog_plist(PYTHON))
    write_root_file(managed.NEWSYSLOG_CONF, managed.newsyslog_conf().encode())
    launchctl("bootout", f"system/{managed.WATCHDOG_LABEL}")
    launchctl("bootstrap", "system", str(managed.WATCHDOG_PLIST), check=True)
    if logged_in(account.pw_uid):
        launchctl("bootout", f"gui/{account.pw_uid}/{managed.AGENT_LABEL}")
        launchctl("bootstrap", f"gui/{account.pw_uid}", str(managed.AGENT_PLIST), check=True)
        print(f"Agent in der laufenden Sitzung von {args.user} gestartet.")
    else:
        print(f"Agent startet bei der nächsten Anmeldung von {args.user}.")
    print("Verwalteter Modus eingerichtet. Wächter-Protokoll: /Library/Logs/MacScope-watchdog.log")


def remove(args):
    launchctl("bootout", f"system/{managed.WATCHDOG_LABEL}")
    try:
        config = managed.load()
    except managed.ManagedConfigError:
        config = None
    if config:
        try:
            launchctl("bootout", f"gui/{pwd.getpwnam(config.target_user).pw_uid}/{managed.AGENT_LABEL}")
        except KeyError:
            pass
    for path in [managed.WATCHDOG_PLIST, managed.AGENT_PLIST, managed.NEWSYSLOG_CONF]:
        path.unlink(missing_ok=True)
    if managed.SYSTEM_DIRECTORY.exists():
        shutil.rmtree(managed.SYSTEM_DIRECTORY)
    print("Verwalteter Modus entfernt. Verlaufsdaten im Benutzerkonto bleiben erhalten.")


def main():
    if sys.platform != "darwin" or os.geteuid() != 0:
        raise SystemExit("Auf macOS mit sudo ausführen: sudo bash install-managed.command …")
    parser = argparse.ArgumentParser(description="MacScope verwalteter Modus")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("install")
    setup.add_argument("--user", required=True, help="Konto, in dem der Agent laufen soll")
    setup.add_argument("--allow-app", action="append", help="Bundle-ID (Standard: Firefox)")
    setup.add_argument("--retention-days", type=int, default=30)
    setup.add_argument("--viewer-host", help="Tailscale-Name dieses Macs für das Live-Bild")
    setup.add_argument("--erlaube-admin", action="store_true", help="Nur für Tests")
    commands.add_parser("remove")
    args = parser.parse_args()
    if args.command == "install":
        install(args)
    else:
        remove(args)


if __name__ == "__main__":
    main()
