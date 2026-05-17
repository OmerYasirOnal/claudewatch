# Changelog

All notable changes per [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versions follow [SemVer](https://semver.org/).

## [Unreleased]

### Added
- Cost forecast endpoint (`GET /api/forecast?window_hours=N`) and matching
  Insights card. Extrapolates 24h / 7d / 30d spend from a rolling window
  of ended sessions in the SQLite history.
- Hourly cost trend chart on Insights, backed by
  `GET /api/history/hourly-cost?hours=N`. Continuous x-axis over the last
  7 days (configurable up to 30); empty hours render as zero bins.
- `GET /api/metrics` (JSON) and `GET /api/metrics.prom` (Prometheus text
  exposition) exposing scheduler tick counts + durations, iTerm refresh
  counts + failures, broadcast totals, SSE subscriber gauge, and detector
  failure counter. Hand-rolled exposition format, no external Prometheus
  client dependency.
- Dark mode toggle in the dashboard header with three states
  (light / dark / auto). `auto` follows `prefers-color-scheme`; choice
  persists in `localStorage`. Inline head bootstrap applies the theme
  before first paint so there's no flash.
- Mac universal binary support via `make UNIVERSAL=1 app` (or
  `make app-universal`). Bundles both arm64 and x86_64
  python-build-standalone trees side-by-side; `PythonRunner.swift`
  picks the right interpreter at runtime via compile-time `#if arch(...)`.
  Single-arch DMG ~29 MB, universal DMG ~57 MB.
- Mac chat panel polish: `Cmd+Return` to send, markdown rendering for
  assistant turns (plain text for user turns), auto-scroll to bottom on
  new messages, composer takes focus on window open.
- Vitest frontend test framework with 118 unit tests covering app state,
  formatters, theme handling, and Insights / Status / History / Settings
  view models.
- Repo hygiene: `CONTRIBUTING.md`, `SECURITY.md`, `CHANGELOG.md`, GitHub
  release workflow (`.github/workflows/release.yml`) that builds the
  universal DMG on tag push.

## [0.2.0] - 2026-05-12
- Initial open-source release. See commit 7ee8ba0.
