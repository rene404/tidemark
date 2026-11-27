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
| root-Wächter (LaunchDaemon) | `/Library/LaunchDaemons/local.macscope.watchdog.plist` | Alle 30 s: Agent in der Sitzung des Zielbenutzers geladen, laufend und lebendig? Sonst nachladen/starten/neu starten |
| Aufzeichnungsdienst (LaunchDaemon, root) | `/Library/LaunchDaemons/local.macscope.recorder.plist` | Besitzt Verlauf und Zugriffsschlüssel; der Agent schreibt und liest nur über ihn (#22) |
| Verlauf + Zugriffsschlüssel | `/Library/Application Support/MacScope-Data/` (root, 0700) | `timeline.sqlite3`, `access-token`; für den Benutzer weder lesbar noch löschbar |
| Socket | `/var/run/macscope/recorder.sock` (Ordner root 0755, Socket gehört dem Zielbenutzer, 0600) | Nur der Zielbenutzer (geprüft per Kernel-Kennung der Gegenseite) und root dürfen verbinden |
| Lebenszeichen | `~/Library/Application Support/MacScope/heartbeat` (Benutzer) | Agent aktualisiert es alle 10 s vom Hauptthread |
| Wächter-Zustand | `/Library/Application Support/MacScope/watchdog-state.json` | Merkt sich ein veraltetes Lebenszeichen bis zur nächsten Prüfung |
| Wächter-Protokoll | `/Library/Logs/MacScope-watchdog.log` | Jede Wiederherstellung mit Zeitstempel; Rotation über `/etc/newsyslog.d/macscope.conf` (3 × 1 MB) |
| Dienst-Protokoll | `/Library/Logs/MacScope-recorder.log` (0600) | Start, abgelehnte Verbindungen, Fehler; gleiche Rotation |

Im verwalteten Modus:

- Menüleiste zeigt nur Status und „Dashboard öffnen“, **kein** Pausieren, **kein** Beenden.
- Pausieren ist auch im Code gesperrt (`403`).
- Einstellungen kommen ausschließlich aus `managed.json`; Startoptionen werden ignoriert.
- Jedes Ende des Agenten (auch `SIGTERM` aus der Aktivitätsanzeige) gilt als
  unerwartet (Exit-Code 75) und launchd startet neu.
- In anderen Benutzerkonten beendet sich der Agent sofort (Exit 0, kein Neustart).
- Ist `managed.json` vorhanden, aber unsicher (nicht root-eigen, beschreibbar,
  Symlink, fehlerhaft), **startet der Agent nicht**, statt still unverwaltet zu laufen.
- Protokoll des Agenten: `~/Library/Application Support/MacScope/logs/agent.log`
  (rotiert ab 1 MB, 3 Generationen).
- **Aufzeichnungsdienst:** Der Agent kennt nur die Operationen Ereignis anhängen,
  Ereignisse lesen, Auftrag anlegen/fortschreiben/lesen, Aufräumen (nur nach
  Aufbewahrungsfrist) und Schlüssel holen. **Löschen gibt es nicht.** Ist der Dienst
  kurz weg, puffert der Agent bis zu 1000 Ereignisse mit Originalzeit und schreibt sie
  nach; das Dashboard meldet solange `503`. Beim Start wartet der Agent bis zu 30 s
  auf den Dienst, sonst beendet er sich mit 75 und launchd versucht es erneut. Der
  Wächter lädt den Dienst nach, falls er entladen wurde.
- Im Benutzerordner bleiben nur `agent.lock`, `logs/` und `heartbeat`.
- **Lebenszeichen:** Ist es älter als 120 s, und zwar bei **zwei Prüfungen in Folge**,
  setzt der Wächter einen angehaltenen Prozess fort (`SIGCONT`) und startet den
  Agenten neu (`kickstart -k`). Zwei Prüfungen, weil das Lebenszeichen während des
  Ruhezustands ruht; direkt nach dem Aufwachen wäre es sonst fälschlich „veraltet“.

## Einrichten (als Administrator)

Voraussetzung: Python 3.13 von python.org (wird für die root-eigene Umgebung benutzt).

```bash
git clone https://github.com/rene404/tidemark.git && cd tidemark
sudo bash install-managed.command install --user BENUTZER
```

Optionen: `--allow-app BUNDLE-ID` (mehrfach; Standard: nur Firefox),
`--retention-days N`, `--viewer-host NAME` (Tailscale-Name für das Live-Bild),
`--erlaube-admin` (nur für Tests mit Admin-Konto).

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

Zugriffsschlüssel anzeigen (als Admin):

```bash
sudo "/Library/Application Support/MacScope/venv/bin/macscope" token
```

Entfernen (Verlauf in `/Library/Application Support/MacScope-Data` bleibt, mit
`--daten-loeschen` wird er mitgelöscht):

```bash
sudo bash install-managed.command remove
```

Ein Verlauf aus dem früheren unverwalteten Betrieb (`~/Library/Application
Support/MacScope/timeline.sqlite3`) wird nicht übernommen und bleibt unverändert;
der Installer weist darauf hin.

## Bekannte Grenzen

- Der Benutzer kann Verlauf und Schlüssel **nicht** mehr löschen oder ändern (#22),
  aber über den Socket weiterhin **zusätzliche** Ereignisse eintragen oder Aufträge
  fortschreiben und das Dashboard mit dem Schlüssel öffnen (der Agent in seinem Konto
  braucht beides). Kein manipulationssicheres Protokoll im forensischen Sinn.
- Hält der Benutzer den Agenten an, entsteht eine Lücke im Verlauf; der Wächter
  startet ihn nach 2,5–3 Minuten neu (`agent.started` im Verlauf).
- `launchctl bootout` als Benutzer entlädt den Agenten für **bis zu ~30 s**, bis der Wächter ihn nachlädt.
- Ein angehaltener oder hängender Agent wird über das Lebenszeichen erkannt, aber erst
  nach **2,5–3 Minuten** (120 s Grenze + zwei Prüfungen à 30 s).
- Das Lebenszeichen liegt im Datenverzeichnis des Benutzers. Er könnte es löschen
  (führt zum Neustart) oder künstlich aktuell halten (verdeckt einen angehaltenen Agenten).
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
| [ ] | Als Standardbenutzer `kill -STOP <PID des Agenten>` | Nach ≈ 3 min neu gestartet, Eintrag „Lebenszeichen … veraltet“ im Wächter-Protokoll |
| [ ] | Mac > 5 min schlafen lassen, aufwecken, 2 min warten | **Kein** Neustart des Agenten (kein neues `agent.started`, kein Wächter-Eintrag) |
| [ ] | Neustart, Standardbenutzer meldet sich an | Agent startet ohne Zutun |
| [ ] | Admin-Konto anmelden (schneller Benutzerwechsel) | Dort kein Agent, kein Fehler im Protokoll |
| [ ] | `managed.json` testweise `chmod 666` (als Admin), Agent neu starten | Agent startet **nicht**; Wächter-Protokoll meldet Konfigurationsfehler; danach `chmod 644` |
| [ ] | Als Standardbenutzer: `ls`/`rm` in `/Library/Application Support/MacScope-Data` | Verweigert (Ordner nicht lesbar) |
| [ ] | Als Standardbenutzer: `rm /var/run/macscope/recorder.sock` | Verweigert |
| [ ] | Als Standardbenutzer: `launchctl bootout system/local.macscope.recorder` | Verweigert |
| [ ] | Als Admin `sudo launchctl bootout system/local.macscope.recorder`, im Dashboard weiterklicken, nach ≤ 30 s | Dashboard kurz `503`, dann wieder normal; Ereignisse aus der Lücke sind nachgetragen; Wächter-Protokoll „Aufzeichnungsdienst … neu geladen“ |
| [ ] | `sudo …/macscope token` und `sudo -H -u BENUTZER …/macscope token` | Beide zeigen denselben Schlüssel |
| [ ] | Mac neu starten | Dienst läuft vor der Anmeldung (`sudo launchctl print system/local.macscope.recorder`), Agent verbindet sich nach der Anmeldung |
| [ ] | `sudo bash install-managed.command remove` | Alles weg, Verlauf in `MacScope-Data` bleibt |
| [ ] | `sudo bash install-managed.command remove --daten-loeschen` | Auch `MacScope-Data` weg |
