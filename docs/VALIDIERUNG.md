# Prüfprotokoll — 26. November 2026

## Hier ausgeführte Prüfungen

Umgebung: Linux, Python 3.12. Der echte macOS-Collector wurde nicht ausgeführt.

| Prüfung | Ergebnis |
| --- | --- |
| 29 automatisierte Backend- und HTTP-Tests | Bestanden |
| CLI-Demo mit echtem HTTP-Zugriff und simulierter Fernaktion | Bestanden; App-Ende getrennt bestätigt und Agent sauber beendet |
| Python-Syntax für Anwendung und Installationshelfer | Bestanden |
| JavaScript-Syntax mit `node --check` | Bestanden |
| Bash-Syntax der Installations- und Startskripte | Bestanden |
| Paketbau und enthaltene Webdateien | Wheel erfolgreich gebaut; Module und alle drei Webdateien enthalten |
| Gerenderter Browser-End-to-End-Test | In dieser Umgebung nicht ausführbar: kein lauffähiges Chromium-Binary |
| AppKit-Ereignisse, Menüleiste und tatsächliches Beenden einer Mac-App | Offen; benötigt einen echten Mac |
| Installation, LaunchAgent, SSH-Tunnel und Neustartverhalten | Offen; benötigt die beiden Macs |

Die Tests prüfen insbesondere Authentifizierung, Host-/Origin-Abweisung,
parameterisierte Suche, Cursor, Aufbewahrung, private Dateirechte, abgelehnte
Aktionen, veraltete Snapshots, PID-Wiederverwendung, Auftragsablauf über eine
Ruhephase, Neustart ohne Auftragswiederholung und die Unterscheidung zwischen
angenommener Anfrage und tatsächlich bestätigtem App-Ende.

Die Tests für Aktionsausführung verwenden einen simulierten nativen Adapter.
Sie belegen das Backend-Verhalten, nicht die Funktion von AppKit auf dem iMac.

## Wiederholen

Im Projektordner:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q macscope scripts
node --check macscope/web/app.js
bash -n install.command start.command scripts/install-macos.sh
```

Optionaler vollständiger Dashboard-Test mit einem gesonderten Test-Python:

```bash
python3 -m pip install playwright
python3 -m playwright install chromium
python3 tests/browser_smoke.py
```

Alternativ wählt `MACSCOPE_CHROMIUM` den absoluten Pfad zu einem passenden
Chromium-Binary. Der Test startet ausschließlich `--demo` in einem temporären
Datenverzeichnis und beendet keine echte Mac-App. Er prüft Anmeldung, Suche,
Pagination, Textdarstellung potentieller HTML-Eingaben, Bestätigungsdialog,
simuliertes App-Ende, schmale Ansicht und das Entfernen der Daten beim Trennen.

## Abnahme auf dem iMac

| Schritt | Erwartung |
| --- | --- |
| `sw_vers` und `uname -m` ausführen | macOS-Version und Intel/Apple Silicon sind bekannt |
| Installer und manuellen Start ausführen | Menüleisteneintrag „◉ MS“ erscheint |
| Dashboard öffnen und Schlüssel eingeben | Tatsächlicher Hostname und geöffnete Apps erscheinen |
| Bereits geöffnete App im Verlauf prüfen | „Beim Agent-Start vorhanden“, kein erfundener Startzeitpunkt |
| TextEdit öffnen, aktivieren und wieder schließen | Entsprechende App-Ereignisse erscheinen |
| Aufzeichnung über die Menüleiste pausieren | Status wechselt; gewöhnliche App-Ereignisse werden nicht hinzugefügt |
| Aufzeichnung fortsetzen | Resume-Ereignis und neuer beobachteter Zustand erscheinen |
| Firefox öffnen und Beenden anfragen | Anfrage wird protokolliert; tatsächliches App-Ende wird separat bestätigt |
| Falls Firefox eine Rückfrage zeigt, diese offen lassen | Nach 15 Sekunden „App-Ende nicht bestätigt“ statt einer Erfolgsmeldung |
| iMac schlafen lassen und wieder aufwecken | Nach Wiederaufnahme erscheinen passende Systemereignisse |
| Agent beenden und erneut starten | Sauberer Stopp/Start; offene Aufträge werden nicht erneut ausgeführt |
| Autostart installieren und ab-/anmelden | Agent startet in derselben Benutzer-Anmeldung |
| SSH-Tunnel vom Laptop aufbauen | Dashboard auf Laptop zeigt den iMac und dieselben Ereignisse |
| Verbindung unterbrechen | Dashboard kennzeichnet veraltete Daten; Aktionsschaltflächen werden gesperrt |
| Fernzugriff nach Neustart und Anmeldung prüfen | Erreichbarkeit einschließlich FileVault-/VPN-Verhalten ist bekannt |

Besonders wichtig ist die native Abnahme des Quit-Verhaltens: Apples API kann
eine Anfrage ablehnen oder die App kann auf eine Benutzerantwort warten. Das
Dashboard darf in diesen Fällen kein bestätigtes Ende behaupten.
