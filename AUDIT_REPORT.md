# Wave 6 audit — PRs #115 through #121

Post-merge security + performance review covering:

| PR | Title |
|----|-------|
| #115 | feat(forecast): cost extrapolation endpoint + Insights card |
| #116 | test(frontend): Status/History/Settings draft coverage |
| #117 | feat(mac): chat polish — Cmd+Return, markdown, auto-scroll, focus |
| #118 | feat(mac): UNIVERSAL=1 build for arm64 + x86_64 Macs |
| #119 | feat(insights): hourly cost trend chart (7d) |
| #120 | feat(frontend): dark mode toggle (light/dark/auto) |
| #121 | feat(metrics): /api/metrics endpoint + Status tab card |

No inline fixes were made — every finding warrants discussion before patching.

## Issues filed

| # | Priority | Area | Summary |
|---|----------|------|---------|
| #122 | high | cli | `download-python.sh` does not verify SHA-256 of python-build-standalone tarballs (supply-chain) |
| #123 | medium | api | SSE subscriber gauge can leak when StreamingResponse is never iterated |
| #124 | medium | cli | `bundle-backend.sh` foreign-libs scan uses fragile `xargs sh -c` and incorrect `find -o` precedence |
| #125 | medium | api | Forecast endpoint propagates negative cost rows into projections |
| #126 | low | api | `/api/forecast` and `/api/history/hourly-cost` return dollar amounts regardless of `config.plan` |
| #127 | low | frontend | Inline theme-bootstrap script blocks adopting a strict CSP |
| #128 | low | api / tech-debt | Dead `if not rows` branch in `forecast.py` |
| #129 | medium | frontend (mac) | macOS chat assistant markdown auto-links URLs without scheme allowlist |
| #130 | low | frontend / tech-debt | Metrics counters never reset; Status card lacks "since restart" labeling |

## All-clean surfaces

The following were exercised and found to be sound:

- **SQL injection** — every new query in `backend/api/forecast.py`, `backend/api/insights.py`, and `backend/state.py::hourly_cost` is parameterized via aiosqlite bind args. No string interpolation of user input into SQL.
- **XSS / Alpine bindings** — no new `x-html` or `innerHTML` usage anywhere in `frontend/index.html` or `frontend/app.js`. All new bindings use `x-text` (forecast figures, metrics card, hourly-cost summary).
- **Theme cycle & persistence** — `setTheme()` validates input against a 3-value allowlist and falls back to `"auto"` on garbage. `_loadLocalPrefs` accepts only the same allowlist.
- **AppState.broadcast counter increment** — runs entirely before any `await`, so single-threaded asyncio guarantees atomicity. No lock needed.
- **Metrics avg div-by-zero** — explicitly guarded in `_metrics_payload()`; covered by `test_metrics_avg_divide_by_zero_guarded`.
- **time.monotonic()** — correctly used (instead of `time.time()`) for `scheduler_tick_duration_ms_max`, defending against NTP / DST jumps.
- **forecast `window_hours` range** — server enforces `ge=1, le=720`; reproduced and verified by `test_forecast_rejects_out_of_range_window`.
- **`hourly_cost` `hours` range** — explicitly clamped in `state.py:hourly_cost` to `[1, 24*365*100]`, mirroring `prune()`.
- **TrustedHostMiddleware** — still active and the only auth model; metrics, forecast, and hourly-cost are correctly accessible from localhost only (no new POST surfaces; no new CSRF concern).
- **SSE `gen()` shutdown handling** — issue #27's `shutdown_event.wait()` race is preserved; new `_metrics.sse_subscribers` decrement happens in the same `finally` (but see #123 for the increment-side race).
- **Cross-arch Python install** — `bundle-backend.sh` correctly detects when the foreign-arch interpreter can't execute on the host and falls back to a `tar | tar` site-packages mirror. Pure-Python deps only at runtime — fastapi, uvicorn, pydantic, iterm2, typer all import cleanly.
- **Chat send-text** — already audited in #88/#100; `ChatViewModel.send()` correctly bails on `isSending` race and re-checks `remoteEnabled`. No regressions in PR #117.

## Verification

```
PYTHONPATH=. pytest -q                    # 318 passed
ruff check backend/ tests/                # All checks passed
ruff format --check backend/ tests/       # 58 files already formatted
cd frontend && npm test                   # 134 tests passed (9 files)
```

## Notes for follow-up

The highest-priority finding is **#122 (no checksum verification of the bundled Python interpreter)**. This is a build-pipeline issue and fixing it is purely additive: fetch the existing `SHA256SUMS` file from the same GitHub Release and shell out to `shasum -a 256 -c`. One-screen change, no behavior risk.
