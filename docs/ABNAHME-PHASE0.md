# Abnahme Phase 0 — MacScope nativ auf dem Test-Mac

Ziel: Die AppKit-Anbindung, den Autostart und den Fernzugriff über Tailscale
**zum ersten Mal auf echter Hardware** abnehmen. Der Test-Mac (MacBook Air)
steht für den Ziel-Mac, der Laptop ist der Betrachter.

Ergebnisse direkt hier eintragen (`[x]` = wie erwartet, `[!]` = Abweichung,
mit kurzer Notiz) und auf dem Branch `feature/phase0-nativtest` committen.

| | Test-Mac | Laptop |
|---|---|---|
| Datum | | |
| `sw_vers -productVersion` | | |
| `uname -m` | | |
| Python (`python3 --version`) | | |
| Tailscale-Version | | |
| FileVault an? | | |

---

## 1. Vorbereitung Test-Mac

- [ ] macOS-Updates eingespielt (oder Stand bewusst notiert)
- [ ] Python 3.13 von python.org installiert → `python3.13 --version`
- [ ] Code holen (git kommt mit den Xcode Command Line Tools, sonst ZIP des Branches von GitHub):
  ```bash
  git clone -b feature/phase0-nativtest https://github.com/rene404/tidemark.git
  cd tidemark
  ```
- [ ] Automatische Prüfungen grün:
  ```bash
  python3.13 -m unittest discover -s tests
  bash -n install.command start.command scripts/install-macos.sh
  ```
  Erwartung: `Ran 29 tests … OK`

## 2. Tailscale

- [ ] Tailscale auf Test-Mac **und** Laptop installiert, beide im selben Tailnet angemeldet
- [ ] `tailscale status` zeigt jeweils das andere Gerät
- [ ] Test-Mac: Systemeinstellungen → Allgemein → Freigaben → **Entfernte Anmeldung** an, nur für den Testbenutzer
- [ ] Laptop: eigener SSH-Schlüssel, Login klappt über den Tailscale-Namen:
  ```bash
  ssh BENUTZER@TESTMAC-TAILSCALE-NAME true && echo ok
  ```
  Hostschlüssel beim ersten Mal mit `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` (am Test-Mac) abgleichen.
- [ ] Gegenprobe: Laptop **außerhalb** des Heimnetzes (Handy-Hotspot) erreicht den Test-Mac ebenfalls

## 3. Installation und erster Start (Test-Mac, ohne sudo)

- [ ] `bash install.command` → zeigt macOS-/Architektur-/Python-Zeile, endet ohne Fehler
- [ ] `bash "$HOME/Applications/MacScope/start.command"` → Menüleiste zeigt **◉ MS**
- [ ] Zweites Terminal: `"$HOME/Applications/MacScope/.venv/bin/macscope" token` zeigt Schlüssel
- [ ] `http://127.0.0.1:8765/` lokal öffnen, Schlüssel eingeben → echter Hostname + geöffnete Apps
- [ ] macOS-Rückfragen zu Berechtigungen notiert (welche, wann): ______

## 4. Ereignisse (AppKit)

| ✓ | Schritt | Erwartung | Notiz |
|---|---|---|---|
| [ ] | Bereits geöffnete App im Verlauf prüfen | „Beim Agent-Start vorhanden“, kein erfundener Startzeitpunkt | |
| [ ] | TextEdit öffnen, aktivieren, schließen | Start-, Aktivierungs-, Ende-Ereignis erscheinen | |
| [ ] | Zwischen zwei Apps hin- und herwechseln | Aktivierungen in richtiger Reihenfolge | |
| [ ] | Menüleiste: Aufzeichnung pausieren | Status wechselt, keine App-Ereignisse mehr | |
| [ ] | Aufzeichnung fortsetzen | Resume-Ereignis + neuer beobachteter Zustand | |
| [ ] | Ruhezustand (Deckel zu / Apfelmenü), aufwecken | Schlaf-/Aufwach-Ereignisse | |
| [ ] | Bildschirm sperren, entsperren | Notieren, was (nicht) erscheint — Sessionwechsel gelten als unzuverlässig | |

## 5. Fernaktion „Beenden“

| ✓ | Schritt | Erwartung | Notiz |
|---|---|---|---|
| [ ] | Firefox öffnen, im Dashboard „Beenden“ anfragen | Anfrage protokolliert, danach separat „App-Ende bestätigt“ | |
| [ ] | Firefox mit mehreren Tabs (Rückfrage beim Schließen), Beenden anfragen, Rückfrage offen lassen | Nach 15 s „App-Ende nicht bestätigt“, **keine** Erfolgsmeldung | |
| [ ] | Nicht freigegebene App (z. B. TextEdit) in der App-Liste ansehen | Kein „Beenden“-Knopf | |

## 6. Neustart-Verhalten und Autostart

- [ ] Agent über Menüleiste beenden, neu starten → sauberer Stopp/Start, alte Aufträge werden **nicht** erneut ausgeführt
- [ ] Autostart einrichten:
  ```bash
  "$HOME/Applications/MacScope/.venv/bin/python" \
    "$HOME/Applications/MacScope/scripts/autostart.py" install
  ```
- [ ] Ab- und wieder anmelden → Agent läuft ohne Zutun
- [ ] Agent per Aktivitätsanzeige „sofort beenden“ (Absturz simulieren) → LaunchAgent startet ihn neu
- [ ] Kompletter Neustart des Test-Macs, **noch nicht anmelden**: vom Laptop `tailscale ping` / `ssh` — erreichbar? ______
- [ ] Nach Anmeldung: Agent läuft, Dashboard erreichbar

## 7. Zugriff vom Laptop über Tailscale

- [ ] Tunnel aufbauen:
  ```bash
  ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
    -L 127.0.0.1:8765:127.0.0.1:8765 BENUTZER@TESTMAC-TAILSCALE-NAME
  ```
- [ ] Laptop: `http://127.0.0.1:8765/` zeigt Test-Mac und dieselben Ereignisse
- [ ] Am Test-Mac eine App öffnen → erscheint am Laptop innerhalb weniger Sekunden
- [ ] Tunnel trennen → Dashboard kennzeichnet veraltete Daten, Aktionsknöpfe gesperrt
- [ ] Beenden-Aktion vom Laptop aus (Firefox) → wie in Abschnitt 5

## 8. Vorgriff auf den verwalteten Modus (nur beobachten, nichts ändern)

Diese Punkte liefern die Grundlage für den nächsten Schritt (Standardkonto,
verwalteter Modus, root-Wächter).

- [ ] Zweites Konto als **Standardbenutzer** (kein Admin) anlegen, dort Abschnitt 3 wiederholen → Installation und Start klappen ohne Admin-Rechte?
- [ ] Notieren, was ein Standardbenutzer heute stoppen kann: Menü „Beenden“, „Pausieren“, Aktivitätsanzeige, `launchctl bootout`, Löschen von `~/Applications/MacScope`
- [ ] Bei schnellem Benutzerwechsel (beide Konten angemeldet): Was sieht der Agent des einen Kontos vom anderen? (Erwartung: nichts)

---

## Wenn etwas schiefgeht — mitnehmen

```bash
"$HOME/Applications/MacScope/.venv/bin/macscope" diagnose
tail -n 100 "$HOME/Library/Application Support/MacScope/logs/stderr.log"
launchctl print "gui/$(id -u)/local.macscope.agent" | head -40
```

Dazu Bildschirmfoto der Fehlermeldung und die Uhrzeit. **Keinen** Inhalt von
`access-token` oder `timeline.sqlite3` ins Repo oder in Tickets kopieren.

## Ergebnis

- [ ] Phase-0-Nativtest bestanden — offene Abweichungen: ______
