# Deriv Signal Lab

A mobile-friendly **research and paper-signal platform** for Deriv markets. It scans an approved watchlist on
closed candles, produces explainable `LONG` / `SHORT` / `NO_TRADE` decisions, passes every directional idea
through a central risk engine, sends phone alerts (Telegram and Web Push) for new, invalidated, expired,
stop and target events, and runs leak-free backtests and walk-forward evaluations. Every signal, configuration
change and backtest is recorded in a hash-chained audit log.

> **Decision support only. Not financial advice.** No strategy here is guaranteed or "the most profitable",
> and none is expected to stay profitable. `NO_TRADE` is a normal, often correct, outcome. The app **never places
> orders**: the Deriv client refuses every call except a short allow-list of read-only market-data calls.

## What is in the box

| Area | Where | Notes |
|---|---|---|
| Deriv market data (public WebSocket) | `backend/app/data/deriv/` | Built and verified against the official docs and JSON schemas (see below) |
| Simulated data (`SIMULATED_DATA`) | `backend/app/data/simulator.py` | Deterministic regime-switching process for offline use and tests. **Not market data.** |
| Data quality | `backend/app/data/quality.py` | UTC epochs, de-duplication, OHLC validation, drops the forming candle, gap and staleness checks |
| Indicators | `backend/app/indicators/` | EMA/SMA, ATR, RSI, MACD, ADX/DI, Bollinger, Donchian, realised volatility, confirmed swings, HTF alignment, regimes |
| Strategies | `backend/app/strategies/` | Trend pullback, Donchian breakout, mean reversion (range only), momentum with cooldown, fixed-weight ensemble |
| Product adapters | `backend/app/products/adapter.py` | Linear (CFD-like), multiplier and rise/fall. Unknown or unconfirmed specs give `RISK_UNAVAILABLE` |
| Risk engine | `backend/app/risk/engine.py` | Central and non-bypassable; hard cap; never increases risk after losses |
| Backtester | `backend/app/backtest/` | Event-driven, next-bar execution, costs, conservative same-bar rule, walk-forward, benchmarks, eligibility |
| Fundamentals / events | `backend/app/fundamentals/service.py` | Point-in-time calendar (`known_at`). Synthetic indices: `NOT_APPLICABLE` |
| Alerts | `backend/app/notifications/dispatcher.py` | Telegram and Web Push; gated by risk; each (signal, event, channel) sent at most once |
| API / worker | `backend/app/api/main.py`, `backend/app/worker.py` | FastAPI with bearer auth; the scheduler scans a few seconds after each bar closes |
| PWA | `frontend/` | React, TypeScript, Vite and Lightweight Charts; installable; five plain-language screens (see below) |

## Deriv integration: what was verified

On 2026-11-21 the following was read from the official documentation and the published schema archive, then
checked live against the public endpoint (`tests/fixtures/deriv_public_recorded.json` holds the recorded responses):

- Public market-data WebSocket, no authentication: `wss://api.derivws.com/trading/v1/options/ws/public`.
  [Docs: WebSocket public](https://developers.deriv.com/docs/options/ws-public/), [API overview](https://developers.deriv.com/docs/intro/api-overview/)
- `active_symbols` returns `underlying_symbol`, `underlying_symbol_name`, `market`, `submarket`, `pip_size`, `exchange_is_open`, …
  [Docs](https://developers.deriv.com/docs/data/active-symbols/)
- `ticks_history` with `style: "candles"`. Allowed granularities are `60…86400`. `count` defaults to 1000, and history is paged backwards with `end`. `candles[].epoch` is the candle's **open** time.
  [Docs](https://developers.deriv.com/docs/data/ticks-history/), [Ticks](https://developers.deriv.com/docs/data/ticks/)
- `ping`, `time`, `forget`, `forget_all`, request correlation with `req_id`, and the error shape `{"error": {"code", "message"}}`.
  [Ping](https://developers.deriv.com/docs/system/ping/), [Time](https://developers.deriv.com/docs/system/time/), [Forget](https://developers.deriv.com/docs/subscription/forget/)
- Rate limits: non-trading calls share 220 per minute and 14,400 per hour; the client stays at 180 and 12,000.
  [Limits](https://developers.deriv.com/docs/limits/), [Best practices](https://developers.deriv.com/docs/best-practices/)
- Schemas: <https://github.com/deriv-com/deriv-api-schemas/releases> (CI downloads them and validates the requests the app builds).
- Authentication (not used): [Authentication](https://developers.deriv.com/docs/intro/authentication/), [OTP WebSocket](https://developers.deriv.com/docs/options/websocket/)

Deliberate choices:

- **No token is used.** Public data needs none. Authenticated WebSockets need an OTP from an endpoint whose OAuth
  scope is `trade`, which is more than a read-only tool should hold. `DERIV_API_TOKEN` and `DERIV_APP_ID` are
  accepted as settings but not used in this build.
- The `ohlc` stream payload is not described in the published schema, so the app does not rely on it. Closed
  candles are fetched again with `ticks_history` after each bar closes.
- Provenance: Deriv data is `LIVE_DATA` while the stream is fresh and `DELAYED_DATA` otherwise. The simulator is
  always `SIMULATED_DATA`. Provenance is shown on every signal, chart, scan, backtest and alert.

## How the pieces stay honest

**No look-ahead.**
- Indicators are causal: tests check that truncating the series never changes earlier values.
- Swings are known only after their right-hand bars close.
- Higher-timeframe values come from *closed* HTF bars only.
- The backtester fills orders at the **next bar's open** after the signal bar closes.
- `strict=True` recomputes every feature from `candles[:i+1]` at every bar, and a test asserts that this gives
  identical trades.
- Another test corrupts all prices after a cut-off and checks that trades completed before it are unchanged.

**Costs and fills.**
- Each fill pays half the spread; market fills also pay ATR-scaled slippage; commission is charged in bps per side.
- If the stop and the target are both inside one bar, the **stop is assumed first** and the trade is flagged.
- A gap through the stop fills at the worse open price.
- Results are reported both gross and net of costs.
- Not modelled: overnight financing, partial fills, liquidity.

**Evaluation protocol.**
1. Data is split chronologically: 60% train, 20% validation, 20% hold-out test.
2. Walk-forward folds run over train and validation. Parameters come from a small, pre-declared grid and are
   chosen **on each fold's training window only**. The objective is net expectancy in R with a minimum trade
   count, not maximum profit.
3. The hold-out segment is used once.
4. `ELIGIBLE_FOR_PAPER_ALERTS` requires all of the following on the hold-out: at least 30 trades, net expectancy
   ≥ 0.05R, profit factor ≥ 1.1, max drawdown ≤ 20%, at least 60% of walk-forward folds positive, and beating the
   95th percentile of a seeded random-entry benchmark.

The label means "passed a research screen", nothing more.

**Risk engine.** Every directional decision goes through it, including in backtests.
- Fixed-fraction risk (default 0.5%) with a hard 2% cap.
- After a configurable loss streak, risk is halved; after a longer streak, new signals pause for 24h.
- Daily loss limit, maximum open positions, one position per symbol (no averaging down), and an aggregate open-risk limit.
- Kill switch, data-quality gate and event-window gate.
- `RiskDecision` objects can only be created by the engine. The alert dispatcher checks the decision again before
  sending anything.
- Paper alerts are `ACTIONABLE` only for live data **and** an eligible strategy. Everything else is
  `RESEARCH_ONLY`, which is off by default and enabled with `RESEARCH_ONLY_ALERTS=true`.

**Product risk (Deriv MT5).** Signals are sized as **Deriv MT5 CFDs**. The app bundles Deriv's published
[MT5 trading specifications](https://deriv.com/trading-specifications) (`backend/app/products/deriv_mt5_specs.json`,
retrieved 2026-11-22): contract size, minimum and maximum lot, minimum and typical spread, and swap. They're matched to Deriv
API symbols by name, which covers 86 of the 95 active symbols; unmatched symbols get no suggestion rather than a guess.

Suggestions start unconfirmed. Tap **Use these MT5 specs** on a market, or **Use MT5 specs for all my markets** in
Settings, after checking them against the **Specification** window in your MT5 terminal. Values can change and differ by
account type. Until a market is confirmed, its risk is `RISK_UNAVAILABLE` and alerts stay research-only.

Modelling notes:
- The volume step is taken as the minimum lot, so sizes only ever round down.
- Costs come from the spread (MT5 synthetics carry no commission).
- Swap is shown but **not** included in tests.
- MT5 prices are assumed to track the same index as Deriv's public data feed, which the app uses for charts, signals and
  tests. Verify this in your terminal.

You can still set a spec by hand with `PUT /api/instruments/<symbol>`.

**Score.** The `score` field is an ordinal strength used for ranking. It is **not** a probability.

## Using the app

The app has five tabs:

| Tab | What it's for |
|---|---|
| **Today** | Is the scanner running? How many ideas are open? A big **Scan now** button. Plus a short "How this app works" guide. |
| **Signals** | Buy and sell ideas, filterable by status (Open, Target reached, Stopped out, Cancelled, Expired, Blocked). Tap an idea to see the price, stop and target, the reasons behind it, and its safety checks. The technical details are tucked away below. |
| **Markets** | Your watchlist, with one line per market saying what the strategies see. **Add market** searches every Deriv symbol, forex included. Tap a market for its chart, each strategy's reading, its MT5 contract, and the live spread on Deriv's feed. |
| **Research** | **Test all** runs every strategy on every watched market over about 6 months, in the background. A single **Full test** produces a clear verdict, Passed or Not passed, and passing is what unlocks paper alerts for that market. |
| **Settings** | Appearance (System, Dark or Light), the emergency stop, phone alerts, safety limits, MT5 contracts, economic-calendar CSV import, system health, and the access token. |

The **Today** screen also has a paper-results card for the last 7 or 30 days. The look is a dark, trading-terminal style with a light theme; on desktop the tabs move to a left rail.

Every screen shows whether the data is live, delayed or simulated. The first time you open the app it asks for the access token.

## Running it

### Quick start (no Docker)

```bash
cd backend && python3.12 -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
uvicorn app.api.main:app --reload            # http://localhost:8000/api/health
python -m app.worker                         # in another shell: scans after each bar close
cd ../frontend && npm ci && npm run dev      # http://localhost:5173 (proxies /api)
```

Without `APP_ACCESS_TOKEN`, development mode leaves the API open. Production refuses to start without a token of
24 or more characters. In the app, enter the token under **Settings**.

The app uses **live Deriv data by default** (`DATA_PROVIDER=deriv`). It needs no Deriv account or token, only outbound access to `api.derivws.com`. For an offline demo, set `DATA_PROVIDER=simulator`; everything it produces is labelled as simulated.

### Docker Compose (Postgres, Redis, API, worker, web)

```bash
cp .env.example .env      # set APP_ACCESS_TOKEN, POSTGRES_PASSWORD, DATA_PROVIDER, notification keys
docker compose up --build # web on http://localhost:8080
```

`migrate` runs `alembic upgrade head` before the API and worker start. To use the app as a PWA on your phone,
serve `web` over HTTPS (for example through a reverse proxy or tunnel); Web Push and installation require HTTPS.

### Phone alerts

- **Telegram:** create a bot with @BotFather, send it a message, then set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
- **Web Push:** run `npx web-push generate-vapid-keys`, set `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY` and
  `VAPID_SUBJECT`, then tap **Settings → Enable push on this device**.
- **Test:** use **Settings → Send test**.

### Tests and checks

```bash
cd backend && ruff check . && ruff format --check . && mypy app && pytest -q
DERIV_SCHEMAS_DIR=/path/to/extracted/schemas pytest tests/test_deriv.py   # official-schema contract tests
DSL_LIVE_TESTS=1 pytest -m live                                           # live smoke test (network)
cd frontend && npm run typecheck && npm run build
```

CI (`.github/workflows/ci.yml`) runs lint, types, tests with the official schemas, Alembic checks on PostgreSQL,
dependency audits and Docker builds.

## API sketch

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness (no auth) |
| GET | `/api/status` | Provider and provenance, DB, Redis, worker heartbeat, risk config |
| GET | `/api/signals[?status=&symbol=]` | List signals |
| GET | `/api/signals/{id}` | Detail: reasons, indicators, risk checks, lifecycle, deliveries, hashes |
| GET | `/api/scan/snapshots` | Latest per-strategy decision, including `NO_TRADE` reasons |
| POST | `/api/scan/run` | Run a scan now |
| GET | `/api/candles?symbol=&timeframe=` | Closed candles, data quality and signals |
| GET, POST, PUT, DELETE | `/api/watchlists…` | Bounded watchlists (≤ 50 items) |
| POST | `/api/backtests` | Run a backtest, or a walk-forward evaluation with `kind: "evaluation"` |
| GET | `/api/backtests/{id}` | Metrics, equity curve and full trade log |
| GET, PUT | `/api/risk/config` | Read or change risk settings (audited) |
| POST | `/api/risk/kill-switch?engaged=` | Engage or release the kill switch |
| GET | `/api/audit` | Recent entries and hash-chain verification |
| POST | `/api/events` | Import a point-in-time economic calendar |
| PUT | `/api/instruments/{symbol}` | Set a verified product spec |

## Known limitations and next steps

- No economic calendar ships with the app, and nothing is invented. Import one as CSV under **Settings → Economic calendar**. Until then, FX symbols report `NO_DATA`.
- The live spread shown for each market comes from Deriv's public data feed, which may differ from your MT5 spread. Tests use the MT5 spread.
- Backtests on the simulator say nothing about real markets. They exist to exercise the engine.
- Until a market's product details are confirmed, Research tests on it assume a contract size of 1. Results in R, win rate, profit factor and drawdown % are unaffected; currency amounts are illustrative. The result page says so.
- Paper outcomes use the signal reference price and do not model your real fills.
- Docker images were not built in the development sandbox; CI builds them.
