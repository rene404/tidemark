// CaptureProbe — Risiko-Test für Phase B (kein Produktivcode).
//
// Prüft auf dem Test-Mac, wie sich macOS bei Bildschirmaufnahme (ScreenCaptureKit)
// und Bedienungshilfen (für spätere Fernsteuerung) verhält: Rückfragen, wem die
// Berechtigung zugeordnet wird, ob sie einen Neubau übersteht.
//
//   capture-probe status            Berechtigungen anzeigen (fragt nichts an)
//   capture-probe request           Berechtigungen anfragen (löst Rückfragen aus)
//   capture-probe shot [datei.png]  Ein Bildschirmfoto, optional speichern
//   capture-probe loop [minuten]    Dauertest: alle N Minuten ein Foto (nicht gespeichert)
//
// Jede Zeile geht als JSON auf stdout und an ~/Library/Logs/tidemark-capture-probe.log.

import ApplicationServices
import CoreGraphics
import Foundation
import ImageIO
import ScreenCaptureKit
import UniformTypeIdentifiers

let logURL = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Logs/tidemark-capture-probe.log")

func emit(_ command: String, _ fields: [String: Any]) {
    var record = fields
    record["command"] = command
    record["time"] = ISO8601DateFormatter().string(from: Date())
    record["pid"] = Int(getpid())
    record["parent_pid"] = Int(getppid())
    guard let data = try? JSONSerialization.data(withJSONObject: record, options: [.sortedKeys]),
          var line = String(data: data, encoding: .utf8) else { return }
    line += "\n"
    FileHandle.standardOutput.write(line.data(using: .utf8)!)
    if !FileManager.default.fileExists(atPath: logURL.path) {
        FileManager.default.createFile(atPath: logURL.path, contents: nil,
                                       attributes: [.posixPermissions: 0o600])
    }
    if let handle = try? FileHandle(forWritingTo: logURL) {
        handle.seekToEndOfFile()
        handle.write(line.data(using: .utf8)!)
        try? handle.close()
    }
}

func permissions() -> [String: Any] {
    let version = ProcessInfo.processInfo.operatingSystemVersion
    return [
        "macos": "\(version.majorVersion).\(version.minorVersion).\(version.patchVersion)",
        "user": NSUserName(),
        "executable": CommandLine.arguments[0],
        "bundle_id": Bundle.main.bundleIdentifier ?? "-",
        "screen_capture": CGPreflightScreenCaptureAccess(),
        "accessibility": AXIsProcessTrusted(),
    ]
}

func shot(saveTo path: String?) async -> [String: Any] {
    let start = Date()
    func elapsed() -> Int { Int(Date().timeIntervalSince(start) * 1000) }
    guard #available(macOS 14.0, *) else {
        return ["ok": false, "error": "macOS 14 oder neuer nötig (SCScreenshotManager)"]
    }
    do {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let display = content.displays.first(where: { $0.displayID == CGMainDisplayID() })
                ?? content.displays.first else {
            return ["ok": false, "error": "kein Bildschirm gefunden", "ms": elapsed()]
        }
        let filter = SCContentFilter(display: display, excludingWindows: [])
        let configuration = SCStreamConfiguration()
        configuration.width = Int(Float(display.width) * filter.pointPixelScale)
        configuration.height = Int(Float(display.height) * filter.pointPixelScale)
        configuration.showsCursor = true
        let image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: configuration)
        var result: [String: Any] = ["ok": true, "width": image.width, "height": image.height, "ms": elapsed()]
        if let path {
            let url = URL(fileURLWithPath: path)
            guard let destination = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil) else {
                return ["ok": false, "error": "Datei nicht schreibbar: \(path)", "ms": elapsed()]
            }
            CGImageDestinationAddImage(destination, image, nil)
            result["saved"] = CGImageDestinationFinalize(destination) ? path : "FEHLER"
        }
        return result
    } catch {
        return ["ok": false, "error": "\(error)", "ms": elapsed()]
    }
}

let arguments = Array(CommandLine.arguments.dropFirst())
switch arguments.first ?? "status" {
case "status":
    emit("status", permissions())
case "request":
    let screen = CGRequestScreenCaptureAccess()
    let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
    let accessibility = AXIsProcessTrustedWithOptions(options)
    emit("request", permissions().merging(["screen_capture_granted_now": screen,
                                           "accessibility_granted_now": accessibility]) { $1 })
case "shot":
    emit("shot", (await shot(saveTo: arguments.count > 1 ? arguments[1] : nil))
        .merging(["screen_capture": CGPreflightScreenCaptureAccess()]) { $1 })
case "loop":
    let minutes = max(1.0, Double(arguments.count > 1 ? arguments[1] : "10") ?? 10)
    emit("loop-start", permissions().merging(["interval_minutes": minutes]) { $1 })
    while true {
        emit("loop", (await shot(saveTo: nil)).merging(["screen_capture": CGPreflightScreenCaptureAccess()]) { $1 })
        try? await Task.sleep(nanoseconds: UInt64(minutes * 60 * 1_000_000_000))
    }
default:
    FileHandle.standardError.write("Aufruf: capture-probe status|request|shot [datei.png]|loop [minuten]\n".data(using: .utf8)!)
    exit(2)
}
