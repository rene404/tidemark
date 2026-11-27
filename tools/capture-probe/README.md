# CaptureProbe: Risiko-Test für Phase B

Kleines Swift-Programm, **kein Produktivcode**. Es beantwortet vor B1 die
Fragen, an denen das eigene Live-Modul scheitern könnte (siehe Risiken in
[VIEWER-NAHT.md](../../docs/VIEWER-NAHT.md)):

1. Muss der Standardbenutzer bei Bildschirmaufnahme **wiederholt etwas bestätigen**?
2. **Wem** ordnet macOS die Berechtigung zu (Terminal, Python oder dem Programm selbst)?
3. Übersteht die Berechtigung einen **Neubau** (ad-hoc-Signatur ändert sich)?
4. Kann ein **Standardbenutzer** die Berechtigung selbst erteilen?
5. Was liefert die Aufnahme bei **Sperrbildschirm** und Benutzerwechsel?

Ergebnisse unten eintragen und auf einem Branch committen.

## Bauen (auf dem Test-Mac)

Voraussetzung: Xcode Command Line Tools (`xcode-select --install`), macOS 14 oder neuer.

```bash
bash tools/capture-probe/build.sh          # -> ~/Applications/CaptureProbe.app
P=~/Applications/CaptureProbe.app/Contents/MacOS/capture-probe
$P status
```

Alle Ausgaben stehen auch in `~/Library/Logs/tidemark-capture-probe.log`.
`status` fragt nichts an, `request` löst die Rückfragen aus.

## Testfolge

Möglichst **im Standardbenutzer-Konto** (nicht Admin), so wie später beim Vater.

| # | Schritt | Beobachten / notieren |
|---|---|---|
| 1 | Aus dem Terminal: `$P request` | Für **wen** fragt macOS? (Erwartung: „Terminal“, weil das Terminal der verantwortliche Prozess ist.) Danach Systemeinstellungen → Datenschutz & Sicherheit → Bildschirm- & Systemaudioaufnahme: welcher Eintrag? |
| 2 | Als Standardbenutzer den Schalter dort umlegen | Verlangt macOS ein **Admin-Passwort**? |
| 3 | Terminal-Freigabe wieder entfernen. Dann ohne Terminal starten: `open ~/Applications/CaptureProbe.app --args request` | Fragt macOS jetzt für **CaptureProbe**? Eintrag in den Einstellungen? |
| 4 | `open ~/Applications/CaptureProbe.app --args shot ~/Desktop/probe.png` | `ok: true`, Größe stimmt (Retina), Dauer in `ms`. Bild ansehen |
| 5 | Dauertest: `bash tools/capture-probe/agent.sh install 10` | Rückfrage beim Laden? Protokoll füllt sich alle 10 min |
| 6 | Dauertest **mindestens 1 Woche, besser 5 Wochen** laufen lassen, Mac normal benutzen, Neustarts | Jede Rückfrage mit **Datum, Wortlaut, Bildschirmfoto** notieren. Gibt es Zeilen mit `"ok":false`? |
| 7 | Während des Dauertests Bildschirm sperren, später entsperren | Was steht in den Protokollzeilen dieser Zeit (Fehler, schwarzes Bild, normal)? |
| 8 | Schneller Benutzerwechsel ins Admin-Konto | Was protokolliert der Test im Hintergrund? |
| 9 | Neu bauen (`build.sh`), cdhash vergleichen, dann `open … --args status` | Ist `screen_capture` noch `true`, oder ist die Freigabe weg bzw. doppelt in der Liste? |
| 10 | Für B2: `open … --args request`, Bedienungshilfen freigeben, `status` | `accessibility: true`? Admin-Passwort nötig? Übersteht es den Neubau? |

Beenden: `bash tools/capture-probe/agent.sh remove`. Freigaben in den
Systemeinstellungen danach wieder entfernen, App aus `~/Applications` löschen.

## Warum der verantwortliche Prozess wichtig ist

macOS fragt bei Bildschirmaufnahme für den **verantwortlichen** Prozess. Das ist
beim Start aus dem Terminal das Terminal und bei einem LaunchAgent dessen
Programm. Würde der Python-Agent das Swift-Programm direkt starten, könnte die
Freigabe bei **Python** landen, und dann dürfte jedes Python-Skript im Konto den
Bildschirm aufnehmen. Ergibt Schritt 1/3/5 dieses Bild, läuft das Live-Modul in
B1 als **eigener LaunchAgent** (eigenes App-Bundle, eigene Freigabe), und der
Python-Agent spricht nur über einen lokalen Socket mit ihm.

## Ergebnisse

| Feld | Wert |
|---|---|
| Datum Start / Ende Dauertest | |
| macOS-Version | |
| Konto (Standard/Admin) | |

| # | Ergebnis | Notiz |
|---|---|---|
| 1 | | |
| 2 | | |
| 3 | | |
| 4 | | |
| 5 | | |
| 6 | | |
| 7 | | |
| 8 | | |
| 9 | | |
| 10 | | |

**Fazit für B1:** ______
