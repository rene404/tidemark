# Verwalteter Modus und root-Wächter

Ziel: Auf dem Ziel-Mac kann nur der Administrator MacScope stoppen. Der
überwachte Benutzer ist **Standardbenutzer** und hat keinen Knopf und keinen
dauerhaften Weg, den Agenten abzuschalten.

Warum nicht einfach „als root laufen“? AppKit/NSWorkspace sieht nur die eigene
GUI-Sitzung. Der Agent **muss** im Konto des Benutzers laufen. root sorgt nur
dafür, dass er dort läuft und bleibt.

## Bausteine

| Baustein | Ort (gehört root) | Aufgabe |
|---|---|---|
| Programm + Python-Umgebung | `/Library/Application Support/MacScope/app`, `…/venv` | Benutzer kann nichts ändern oder löschen |
| Konfiguration | `/Library/Application Support/MacScope/managed.json` | Zielbenutzer, freigegebene Apps, Aufbewahrung, Port |
| Systemweiter LaunchAgent | `/Library/LaunchAgents/local.macscope.managed-agent.plist` | Startet den Agenten bei jeder GUI-Anmeldung; bei unerwartetem Ende Neustart nach 10 s |
| root-Wächter (LaunchDaemon) | `/Library/LaunchDaemons/local.macscope.watchdog.plist` | Alle 30 s: Agent in der Sitzung des Zielbenutzers geladen und laufend? Sonst nachladen/starten |
| Wächter-Protokoll | `/Library/Logs/MacScope-watchdog.log` | Jede Wiederherstellung mit Zeitstempel |

Im verwalteten Modus:

- Menüleiste zeigt nur Status und „Dashboard öffnen“, **kein** Pausieren, **kein** Beenden.
- Pausieren ist auch im Code gesperrt (`403`).
- Einstellungen kommen ausschließlich aus `managed.json`; Startoptionen werden ignoriert.
- Jedes Ende des Agenten (auch `SIGTERM` aus der Aktivitätsanzeige) gilt als
  unerwartet (Exit-Code 75) und launchd startet neu.
- In anderen Benutzerkonten beendet sich der Agent sofort (Exit 0, kein Neustart).
- Ist `managed.json` vorhanden, aber unsicher (nicht root-eigen, beschreibbar,
  Symlink, fehlerhaft), **startet der Agent nicht**, statt still unverwaltet zu laufen.
- Protokoll des Agenten: `~/Library/Application Support/MacScope/logs/agent.log`.

## Einrichten (als Administrator)

Voraussetzung: Python 3.13 von python.org (wird für die root-eigene Umgebung benutzt).

```bash
git clone https://github.com/rene404/tidemark.git && cd tidemark
sudo bash install-managed.command install --user BENUTZER
```

Optionen: `--allow-app BUNDLE-ID` (mehrfach; Standard: nur Firefox),
`--retention-days N`, `--erlaube-admin` (nur für Tests mit Admin-Konto).

Der Installer verweigert Administrator-Konten als Ziel, entfernt einen alten
Benutzer-Autostart (`local.macscope.agent`) des Zielbenutzers und lädt Wächter
und Agent sofort. Ein vorhandenes `~/Applications/MacScope` des Benutzers wird
nicht mehr gebraucht und kann gelöscht werden.

Status prüfen:

```bash
/Library/Application\ Support/MacScope/venv/bin/macscope diagnose   # "mode": "verwaltet für …"
sudo launchctl print system/local.macscope.watchdog | grep -E "state|last exit"
launchctl print gui/$(id -u BENUTZER)/local.macscope.managed-agent | grep state
```

Entfernen (Verlaufsdaten im Benutzerkonto bleiben):

```bash
sudo bash install-managed.command remove
```

## Bekannte Grenzen

- Das **Datenverzeichnis** gehört dem Benutzer. Er kann Verlauf und Schlüssel löschen.
  Der Agent legt beides neu an, die Lücke fällt im Dashboard auf. Ein root-eigener
  Speicher wäre ein späterer Schritt.
- `launchctl bootout` als Benutzer entlädt den Agenten für **bis zu ~30 s**, bis der Wächter ihn nachlädt.
- Ein angehaltener Prozess (`kill -STOP`) gilt für launchd als laufend. Das Dashboard
  zeigt dann veraltete Daten an, der Wächter greift nicht.
- Administratoren können alles abschalten. Das ist gewollt.
- Noch **nicht auf echter Hardware abgenommen** (siehe unten).

## Abnahme auf dem Test-Mac

| ✓ | Schritt | Erwartung |
|---|---|---|
| [ ] | Standardbenutzer anlegen, `install … --user` aus Admin-Konto | Läuft ohne Fehler, `diagnose` zeigt „verwaltet für …“ |
| [ ] | Installer mit Admin-Konto als Ziel (ohne `--erlaube-admin`) | Verweigert |
| [ ] | Als Standardbenutzer anmelden | ◉ MS erscheint, Menü ohne Pausieren/Beenden |
| [ ] | Aktivitätsanzeige → Prozess „Python“ beenden | Nach ≈ 10 s wieder da, `agent.started` im Verlauf |
| [ ] | „Sofort beenden“ | Ebenso |
| [ ] | `launchctl bootout gui/$(id -u)/local.macscope.managed-agent` als Standardbenutzer | Nach ≤ 30 s wieder da, Eintrag im Wächter-Protokoll |
| [ ] | `rm`/Bearbeiten unter `/Library/Application Support/MacScope` als Standardbenutzer | Verweigert |
| [ ] | Neustart, Standardbenutzer meldet sich an | Agent startet ohne Zutun |
| [ ] | Admin-Konto anmelden (schneller Benutzerwechsel) | Dort kein Agent, kein Fehler im Protokoll |
| [ ] | `managed.json` testweise `chmod 666` (als Admin), Agent neu starten | Agent startet **nicht**; Wächter-Protokoll meldet Konfigurationsfehler; danach `chmod 644` |
| [ ] | `sudo bash install-managed.command remove` | Alles weg, Verlauf bleibt |
