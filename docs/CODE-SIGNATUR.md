# Entscheidungsvorlage: Code-Signatur für das Swift-Live-Modul (B1)

Stand: 2026-11-27 · Entscheidung erst vor B1 nötig, **nach** dem CaptureProbe-Test.

## Warum die Signatur zählt

macOS speichert Freigaben für Bildschirmaufnahme und Bedienungshilfen (TCC)
zusammen mit der **Code-Identität** des Programms, der „Designated Requirement“.
Ändert sie sich, ist die Freigabe weg, und es muss erneut jemand mit
Admin-Passwort freigeben.

## Die wichtigste Erkenntnis vorweg

**Die Signatur löst die wiederkehrende Rückfrage nicht.** Seit macOS 15
(Sequoia) fragt macOS bei Programmen, die den Bildschirm per ScreenCaptureKit
aufnehmen, **etwa monatlich** erneut nach, ob die Freigabe bestehen bleiben soll.
Das gilt unabhängig von der Signatur. Entwickler haben keinen Weg, das zu
umgehen. Nur eine MDM-Verwaltung kann es per `forceBypassScreenCaptureAlert`
unterdrücken, und auch das wirkt laut Berichten nicht vollständig.

Für den Ziel-Mac heißt das: **Der Benutzer sähe ungefähr einmal im Monat eine
Systemrückfrage**, die er bestätigen müsste. Apples eigene Bildschirmfreigabe
(Phase A) ist ein Systemdienst und davon nicht betroffen. Dieser Punkt kann
dafür sprechen, **Apple dauerhaft als Live-Bild zu behalten** und B auf
Bereiche ohne Bildschirmaufnahme zu beschränken. Der Dauertest mit CaptureProbe
(Schritt 6) zeigt, wie es auf der konkreten macOS-Version aussieht.

## Optionen

| | A · Ad hoc (heute) | B · Eigenes Zertifikat | C · Apple Developer ID |
|---|---|---|---|
| Kosten | 0 | 0 | 99 USD/Jahr (Apple Developer Program, Notarisierung inklusive) |
| Freigabe übersteht Neubau/Update | **Nein**, jeder Build ist für macOS neuer Code | **Ja** laut Erfahrungsberichten (gleiches Zertifikat → gleiche Identität) | **Ja**, von Apple so vorgesehen |
| Funktioniert mit Bildschirmaufnahme auf aktuellem macOS | Zweifelhaft: Berichte, dass ad hoc signierte Programme nie als freigegeben gelten | Nicht belegt, **muss getestet werden** | Ja |
| Monatliche Rückfrage | bleibt | bleibt | bleibt |
| Aufwand | keiner | Zertifikat einmal in der Schlüsselbundverwaltung erstellen, privaten Schlüssel sichern | Anmeldung bei Apple, Zertifikat, Notarisierung im Build |
| Weitergabe an andere Macs | nein | nein (nur selbst gebaut/installiert) | ja (Gatekeeper akzeptiert) |
| Geeignet für | CaptureProbe-Erstlauf | Hobby-Betrieb auf eigenen Macs, wenn der Test besteht | falls B scheitert oder Weitergabe gewünscht |

## Empfehlung

1. **Jetzt nichts kaufen.** Erst den CaptureProbe-Test auf dem Test-Mac, inklusive
   Schritt 9b mit eigenem Zertifikat.
2. Ist die **monatliche Rückfrage** dort real und nicht abschaltbar: bewusst
   entscheiden, ob das für den Ziel-Mac zumutbar ist. Falls nicht, bleibt das
   Live-Bild bei Apple, und das Swift-Modul entfällt oder beschränkt sich auf
   Steuerung (B2 braucht nur Bedienungshilfen, keine Bildschirmaufnahme).
3. Lohnt sich B: **Option B (eigenes Zertifikat)**, wenn Schritt 9b zeigt, dass die
   Freigabe Neubauten übersteht und `status` sie meldet. Sonst **Option C**.

## Eigenes Zertifikat erstellen (für Schritt 9b)

1. Schlüsselbundverwaltung → Menü „Schlüsselbundverwaltung“ → Zertifikatsassistent →
   „Zertifikat erstellen …“
2. Name: `tidemark lokal`, Identitätstyp: Selbstsigniertes Stammzertifikat,
   Zertifikatstyp: **Codesignierung**
3. Bauen: `SIGN_IDENTITY="tidemark lokal" bash tools/capture-probe/build.sh`
4. Prüfen: Die Ausgabe zeigt eine `Designated Requirement` mit dem Zertifikat statt `cdhash`.

Den privaten Schlüssel nicht weitergeben und nicht ins Repo legen. Wer ihn hat,
kann Programme bauen, die macOS als „dasselbe Programm“ mit allen Freigaben ansieht.

## Quellen

- [MacRumors: Sequoia verlangt monatliche Bestätigung für Bildschirmaufnahme](https://www.macrumors.com/2024/08/15/macos-sequoia-screen-recording-app-permissions/)
- [9to5Mac: gilt auch für ScreenCaptureKit-Apps](https://9to5mac.com/2024/08/14/macos-sequoia-screen-recording-prompt-monthly/)
- [TidBITS: von wöchentlich auf monatlich, MDM-Grenzen](https://tidbits.com/2024/08/19/apple-reduces-excessive-sequoia-permission-requests-shifts-to-monthly)
- [TechRadar: keine Umgehung für Entwickler](https://techradar.com/computing/mac-os/on-macos-sequoia-youll-only-have-to-deal-with-screen-recording-permission-pop-ups-once-a-month-rather-than-once-a-week)
- [Jamf Community: forceBypassScreenCaptureAlert wirkt nur teilweise](https://community.jamf.com/general-discussions-2/how-to-suppress-microsoft-teams-screen-access-notifications-35065)
- [Apple Developer Forums: Freigabe nach Update weg bei ad-hoc-Signatur (Designated Requirement)](https://developer.apple.com/forums/thread/795739)
- [Cap Issue #1722: ad hoc signierte Programme melden unter Sequoia nie eine Freigabe](https://github.com/CapSoftware/Cap/issues/1722)
- [Apple: Mitgliedschaft und Developer ID/Notarisierung](https://developer.apple.com/programs/whats-included/)
- [Apple: Anmeldung, Preis 99 USD/Jahr](https://developer.apple.com/support/enrollment/)

Die Berichte zur monatlichen Rückfrage stammen von 2024 (macOS 15). Das
Verhalten der aktuellen macOS-Version auf dem Test-Mac ist maßgeblich.
