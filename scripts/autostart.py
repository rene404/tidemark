"""Opt-in LaunchAgent for the current Aqua session. No privileged service is installed."""
import os
import pathlib
import plistlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
LABEL = "local.macscope.agent"


def main():
    if sys.platform != "darwin" or os.geteuid() == 0:
        raise SystemExit("Auf macOS im angemeldeten Benutzerkonto ausführen, ohne sudo.")
    if len(sys.argv) != 2 or sys.argv[1] not in {"install", "remove"}:
        raise SystemExit("Aufruf: autostart.py install|remove")
    directory = pathlib.Path.home() / "Library" / "LaunchAgents"
    plist = directory / (LABEL + ".plist")
    domain = f"gui/{os.getuid()}"
    result = subprocess.run(["/bin/launchctl", "bootout", domain + "/" + LABEL], capture_output=True)
    if result.returncode not in (0, 3, 113):
        # A missing service is expected on first install; bootstrap reports actionable errors below.
        print("Bisherigen LaunchAgent geprüft; bei einem Fehler erscheint gleich die launchctl-Meldung.")
    if sys.argv[1] == "remove":
        plist.unlink(missing_ok=True)
        print("Autostart entfernt. Daten und Programm bleiben erhalten.")
        return
    logs = pathlib.Path.home() / "Library" / "Application Support" / "MacScope" / "logs"
    logs.mkdir(parents=True, exist_ok=True, mode=0o700)
    logs.chmod(0o700)
    paths = [logs / "stdout.log", logs / "stderr.log"]
    for path in paths:
        if path.is_symlink():
            raise SystemExit("Protokolldateien dürfen keine Symlinks sein.")
        path.touch(mode=0o600, exist_ok=True)
        path.chmod(0o600)
    directory.mkdir(parents=True, exist_ok=True)
    specification = {
        "Label": LABEL,
        "ProgramArguments": [str(ROOT / ".venv" / "bin" / "python"), "-m", "macscope", "run"],
        "WorkingDirectory": str(ROOT),
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 10,
        "LimitLoadToSessionType": "Aqua",
        "StandardOutPath": str(paths[0]), "StandardErrorPath": str(paths[1]),
        "Umask": 0o077,
    }
    with plist.open("wb") as stream:
        plistlib.dump(specification, stream)
    plist.chmod(0o600)
    subprocess.run(["/bin/launchctl", "bootstrap", domain, str(plist)], check=True)
    print("Autostart installiert. MacScope läuft jetzt und bei der nächsten Anmeldung.")


if __name__ == "__main__":
    main()
