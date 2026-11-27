# Changelog

All notable changes to tidemark. Versions follow [Semantic Versioning](https://semver.org/);
releases are tagged on `main` (gitflow).

## [0.2.0] — unreleased

First release intended to replace TeamViewer (phase 0 + A). **Pending:**
acceptance on real hardware ([docs/ABNAHME-PHASE0.md](https://github.com/rene404/tidemark/blob/feature/phase0-nativtest/docs/ABNAHME-PHASE0.md),
[managed mode](docs/VERWALTETER-MODUS.md), [phase A](docs/VIEWER-NAHT.md)).

### Added
- **Managed mode**: root-owned `managed.json` with ownership/permission checks
  (agent refuses to start if unsafe), no pause/quit in the menu bar, settings only
  from the file, exit code 75 → restart by launchd, system-wide LaunchAgent.
- **Root watchdog** (LaunchDaemon, every 30 s): reloads or restarts the agent in
  the target user's session.
- `install-managed.command` to install / remove managed mode; refuses admin
  accounts as targets and removes an old per-user autostart.
- **Live screen (viewer seam, phase A)**: `GET /api/viewer`, `POST /api/viewer/session`,
  Apple Screen Sharing backend (`vnc://<tailscale-name>`), "Live-Bild" panel in
  the dashboard, `viewer.requested` / `viewer.refused` events (logged even while
  paused), `viewer_host` setting / `--viewer-host`.
- `tools/capture-probe`: Swift test tool for phase-B risks (screen-recording
  permission prompts, responsible process, signing).
- CI on GitHub Actions: tests on macOS (Python 3.11 / 3.13), package install with
  PyObjC, Swift build, browser test (Playwright).
- Docs: managed mode, viewer seam, code-signing decision, phase-0 acceptance checklist.

### Changed
- Remote access via **Tailscale** instead of home network only.
- README rewritten in English; installation via `git clone`.
- Dashboard shows the agent version from `/api/status` instead of a fixed string.

### Fixed
- Event filter "App gestartet" was missing due to a broken `<option>` tag.
- Browser smoke test was never runnable (CSP blocked Playwright's `wait_for_function`);
  race when loading older events.

## [0.1.0] — 2026-11-26

Initial prototype (MacScope): AppKit timeline, running apps, confirmed remote
quit for Firefox, loopback dashboard with access token, optional LaunchAgent.
