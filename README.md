# tidemark

Eigener Ersatz für TeamViewer: Fernzugriff auf einen Familien-Mac, dazu ein
durchgehender Aktivitätsverlauf und gezielte Fernaktionen.

- **Projekt/Repo:** `tidemark` · **Python-Paket:** `macscope` (Prototyp-Kern)
- **Fernzugriff:** Tailscale
- **Workflow:** gitflow — Arbeit zweigt von `develop` ab, Releases auf `main`

**Phasenplan:** 0 Fundament (Tailscale, Standardkonto, verwalteter Modus,
root-Wächter) → A Live-Bild über Apple-Bildschirmfreigabe hinter einer
Viewer-Schnittstelle → B1/B2/B3 eigenes Swift-Live-Modul (Bild, Steuerung,
Feinschliff) hinter derselben Schnittstelle.

---

# MacScope 0.1 — eigener iMac-Verlauf mit Fernaktionen

Ein erster Prototyp für den Ziel-Mac (iMac). Ein Agent läuft in der angemeldeten
macOS-Benutzersitzung, speichert Anwendungsereignisse in SQLite und bietet ein
lokales Dashboard. Der Laptop öffnet dieses Dashboard durch einen SSH-Tunnel über Tailscale.

**Status:** Backend und Dashboard sind automatisiert prüfbar. Die Anbindung an
AppKit und der LaunchAgent müssen auf einem echten Mac abgenommen werden.
Dieses Paket ist Quellcode mit Installationsskript, keine signierte oder
notarisierte `.app`-Datei. Es wurde noch nicht auf deinem iMac installiert.

## Was in Version 0.1 enthalten ist

| Funktion | Verhalten |
| --- | --- |
| Zeitleiste | GUI-App gestartet, beendet, aktiviert; Ruhezustand und Aufwachen; Sessionwechsel |
| Anfangszustand | Bereits laufende GUI-Apps werden als „beim Agent-Start vorhanden“ erfasst, nicht als neu gestartet |
| Laufende Apps | Name, Bundle-ID, PID und Startzeit der App-Instanz; Aktualisierung jede Sekunde |
| Fernaktion | Normales Beenden für Firefox; lokal weitere Bundle-IDs freigebbar |
| Bestätigung | „Anfrage gesendet“ und „App-Ende bestätigt“ sind getrennte Zustände |
| Verlauf | Suche nach App/Bundle-ID, Ereignistyp, ältere Einträge, automatische Aktualisierung |
| Menüleiste | Sichtbarer Status „◉ MS“; Dashboard öffnen; Aufzeichnung pausieren; Agent beenden |
| Speicherung | Lokale SQLite-Datenbank, standardmäßig 30 Tage; Bereinigung beim Start und stündlich |
| Zugriff | Nur `127.0.0.1`, Zugriffsschlüssel und SSH-Tunnel |
| Autostart | Optionaler LaunchAgent bei der Anmeldung des installierenden Benutzers |

Der AppKit-Collector ist **kein vollständiges Prozessaudit**. Hintergrunddienste,
kurzlebige CLI-Prozesse, Browser-URLs, Fenstertitel, Inhalte, Dateiveränderungen,
Netzwerkverbindungen und Bildschirmaufzeichnungen sind in 0.1 nicht enthalten.
Sessionwechsel sind keine verlässlichen Ereignisse für Bildschirmsperre oder
An-/Abmeldung. Andere Benutzerkonten werden nicht systemweit erfasst.

## Voraussetzungen

- Geplanter erster Zielbereich: macOS 12 oder neuer, Intel oder Apple Silicon.
- Python 3.11 bis 3.14; Empfehlung für dieses Paket: Python 3.13 von
  [python.org](https://www.python.org/downloads/macos/).
- Installation und Betrieb in deiner angemeldeten grafischen Benutzersitzung.
- Internet für die einmalige Installation der Python-Abhängigkeiten.

Der Installer zeigt macOS-Version, Prozessorarchitektur und Python-Version an.
Bei älterem macOS beendet er sich vor der Installation, damit wir das Paket
gezielt anpassen können. Die momentan unbekannte iMac-Version ist daher noch
ein offener Kompatibilitätspunkt.

Für die in 0.1 verwendeten Anwendungsmetadaten wird keine Bildschirmaufnahme
implementiert. Der Agent benötigt keine Root-Installation. macOS kann konkrete
Aktionen trotzdem verweigern; das erscheint als Ergebnis im Verlauf.

## Auf dem iMac installieren

1. Falls noch kein passendes Python vorhanden ist, Python 3.13 installieren.
2. Quellcode holen (git kommt mit den Xcode Command Line Tools; alternativ
   auf GitHub „Code → Download ZIP“ für den Branch `develop`):

```bash
git clone https://github.com/rene404/tidemark.git
cd tidemark
```

3. Ausführen:

```bash
bash install.command
```

Das Skript kopiert die Anwendung nach `~/Applications/MacScope`, erzeugt dort
eine virtuelle Python-Umgebung und installiert die Cocoa-Brücke PyObjC.
Es richtet keinen SSH-Zugang und keine Routerfreigabe ein.

Den Agenten starten:

```bash
bash "$HOME/Applications/MacScope/start.command"
```

In einem zweiten Terminal den Zugriffsschlüssel anzeigen:

```bash
"$HOME/Applications/MacScope/.venv/bin/macscope" token
```

Dann auf dem iMac `http://127.0.0.1:8765/` öffnen und den Schlüssel eingeben.
Der Browser hält den Schlüssel im Arbeitsspeicher des geöffneten Tabs, nicht
in URL, Cookie oder Local Storage. Beim Neuladen musst du ihn erneut eingeben.

Falls du den Autostart möchtest, den manuell gestarteten Agenten zuerst über
„MacScope beenden“ in der Menüleiste schließen und dann ausführen:

```bash
"$HOME/Applications/MacScope/.venv/bin/python" \
  "$HOME/Applications/MacScope/scripts/autostart.py" install
```

Alternativ kann der erste Installationsaufruf `bash install.command --autostart`
lauten. Der Autostart startet MacScope sofort und bei deiner nächsten Anmeldung.
Nach „MacScope beenden“ bleibt es bis zur nächsten Anmeldung beendet; ein
ungewöhnlicher Absturz wird vom LaunchAgent neu gestartet.

## Vom Mac-Laptop verbinden

Auf dem iMac unter Systemeinstellungen → Allgemein → Freigaben die
„Entfernte Anmeldung“ für deinen Benutzer aktivieren. Für den SSH-Zugang
einen eigenen SSH-Schlüssel verwenden. Die genaue Menüführung kann je nach
macOS-Version abweichen.

Die Verbindung läuft über **Tailscale**: Tailscale auf iMac und Laptop
installieren und beide im selben Tailnet anmelden; `tailscale status` zeigt
dann das jeweils andere Gerät. Als `IMAC_ADRESSE` dient der Tailscale-Name
(MagicDNS) oder die `100.x.y.z`-Adresse des iMac. So ist der iMac auch von
unterwegs erreichbar, ohne Portfreigabe im Router. **Für den Tunnel wird
weiterhin der normale macOS-SSH-Dienst benutzt**, nicht Tailscale SSH.
Tailscale-Einrichtung ist nicht Teil des Pakets.

Auf dem Laptop, `BENUTZER` und `IMAC_ADRESSE` durch deine Werte ersetzen:

```bash
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -L 127.0.0.1:8765:127.0.0.1:8765 BENUTZER@IMAC_ADRESSE
```

Das Terminal offen lassen. Auf dem Laptop `http://127.0.0.1:8765/` öffnen
und den auf dem iMac angezeigten MacScope-Schlüssel eingeben.
Wenn Port 8765 auf dem Laptop belegt ist, links beispielsweise 8766 verwenden:

```bash
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -L 127.0.0.1:8766:127.0.0.1:8765 BENUTZER@IMAC_ADRESSE
```

Dann am Laptop `http://127.0.0.1:8766/` öffnen. Die SSH-Hostidentität beim ersten
Verbindungsaufbau mit dem iMac abgleichen. Hostschlüsselprüfungen nicht abschalten.

## Firefox beenden

1. Firefox auf dem iMac öffnen.
2. Im Dashboard bei Firefox „Beenden“ auswählen.
3. Im Dialog „Beenden anfragen“ wählen.

Die Anfrage geht an `NSRunningApplication.terminate()`. Der Agent prüft auf dem
macOS-Hauptthread unmittelbar davor PID, Bundle-ID und Startzeit erneut.
Eine wiederverwendete PID reicht deshalb nicht aus, um die falsche Instanz zu
treffen. Die Anfrage verfällt nach zehn Sekunden in der Warteschlange, auch
nach einem längeren Ruhezustand. Beim Agent-Neustart werden Aufträge nicht
erneut ausgeführt.

Das bestätigte Ende wird bis zu 15 Sekunden überprüft. Bleibt die App offen,
heißt das Ergebnis „App-Ende nicht bestätigt“. Eine Rückfrage zum Speichern oder
zum Schließen mehrerer Tabs kann der Grund sein. Die erste Version erzwingt
kein Beenden. Eine später manuell geschlossene App hat weiterhin ihr normales
App-Ende-Ereignis; ein bereits abgeschlossener Auftrag wird nicht umgedeutet.

## Lokal konfigurieren

Alle folgenden Optionen werden **auf dem iMac beim Start** festgelegt, nicht
über das Dashboard:

```bash
"$HOME/Applications/MacScope/.venv/bin/macscope" run \
  --retention-days 14 \
  --allow-app com.apple.TextEdit
```

Firefox ist standardmäßig freigegeben. `--allow-app` erlaubt zusätzliche
GUI-Anwendungen. Ein anderer lokaler Port ist mit `--port 8767` möglich; das
Ziel des SSH-Tunnels muss dann entsprechend geändert werden. Der Host bleibt
fest auf `127.0.0.1` begrenzt. Der mitgelieferte LaunchAgent nutzt die
Standardwerte; Änderungen daran werden in `scripts/autostart.py` vorgenommen.

Datenverzeichnis:

```text
~/Library/Application Support/MacScope/
  access-token        Zugriffsschlüssel, Dateirechte 0600
  timeline.sqlite3    Ereignisse und Aktionsprotokoll
  agent.lock          Verhindert zwei Agenten mit demselben Datenverzeichnis
  logs/               Start- und Fehlerprotokolle bei Autostart
```

Das Datenverzeichnis hat Rechte 0700. Die SQLite-Datei ist **nicht separat auf
Anwendungsebene verschlüsselt**. Für den Schutz gespeicherter Daten bei
ausgeschaltetem Gerät FileVault auf beiden Macs aktivieren. Die HTTP-Verbindung
bleibt lokal; zwischen den Geräten schützt SSH die Übertragung. Der Laptop
erhält in 0.1 keine dauerhafte Kopie der Ereignisdatenbank.

Ereignisse und Aktionsaufträge werden nach der Aufbewahrungszeit logisch
gelöscht. Das ist keine garantierte physische Löschung alter Datenbankseiten.
Technische Fehlerprotokolle haben in diesem Prototyp noch keine Rotation.

## Vorschau ohne Zugriff auf echte Apps

Aus dem Quellcodeordner, auch unter Linux ausführbar:

```bash
python3 -m macscope run --demo
```

Zweiter Terminalaufruf:

```bash
python3 -m macscope token --demo
```

Die Simulation verwendet ein getrenntes Datenverzeichnis `MacScope-Demo`, ist
im Dashboard gekennzeichnet und kann keine reale App beenden. Mit `--data-dir`
kannst du für einen Test ein temporäres Verzeichnis festlegen. `Ctrl+C` beendet
die Simulation.

## Entfernen

Autostart stoppen und entfernen:

```bash
"$HOME/Applications/MacScope/.venv/bin/python" \
  "$HOME/Applications/MacScope/scripts/autostart.py" remove
```

Einen manuell gestarteten Agenten über seine Menüleiste beenden. Anschließend
den Ordner `~/Applications/MacScope` bei Bedarf im Finder löschen. Ereignisse
bleiben im oben angegebenen Datenverzeichnis, bis du dieses ebenfalls löschst.
Die Deinstallation verändert den separat eingerichteten SSH-/VPN-Zugang nicht.

## Tests und nächste Ausbaustufen

```bash
python3 -m unittest discover -s tests -v
```

Die optionalen Browserprüfungen brauchen Playwright und Chromium; siehe
[Prüfprotokoll](docs/VALIDIERUNG.md). Die Abnahme auf echter Hardware folgt
[ABNAHME-PHASE0.md](docs/ABNAHME-PHASE0.md) (auf dem Branch
`feature/phase0-nativtest`). Bei jedem Push und Pull Request laufen die Tests
automatisch auf GitHub Actions (macOS).

Technischer Aufbau und Erweiterungen:
[ARCHITEKTUR.md](docs/ARCHITEKTUR.md). Priorisierte Erweiterungen sind ein
eigener Firefox-Collector für URLs, gezielte Dateiereignisse und bei Bedarf
ein umfassenderes Prozessaudit. Bildschirmverlauf wäre ein separates Modul mit
eigenen macOS-Freigaben, Speichergrenzen und sichtbarem Aufzeichnungsstatus.

## Primärquellen für die macOS-Anbindung

- [Apple: NSWorkspace App-Start-Ereignisse und Grenzen](https://developer.apple.com/documentation/appkit/nsworkspace/didlaunchapplicationnotification)
- [Apple: NSRunningApplication.terminate()](https://developer.apple.com/documentation/appkit/nsrunningapplication/terminate())
- [Apple: Entfernte Anmeldung](https://support.apple.com/guide/mac-help/allow-a-remote-computer-to-access-your-mac-mchlp1066/mac)
- [PyObjC: Installation](https://pyobjc.readthedocs.io/en/latest/install.html)
- [PyObjC: Unterstützte Plattformen](https://pyobjc.readthedocs.io/en/latest/supported-platforms.html)

Die Bandbreite `pyobjc-framework-Cocoa>=11.1,<13` ist für diesen ersten Stand
bewusst begrenzt. Es gibt noch keine auf einem Mac erzeugte Lockdatei. Vor
breiterem Einsatz die auf dem Ziel-Mac installierten Versionen fixieren und
Intel sowie Apple Silicon separat prüfen.
