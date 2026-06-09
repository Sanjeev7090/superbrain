# Gann Trader — PRD & Architecture

## Original Problem Statement
Clone trading app → Add dark/light mode, mobile responsiveness, MiroFish LangGraph multi-agent AI, Multi-TF Scanner, Weighted AI Signals, Phase 4 Robo-Trader UI, SENSEX options, PCR Gauge. Build an institutional-grade algorithmic trading dashboard for NSE/BSE.

## User Personas
- Retail traders using NSE/BSE (primary)
- Algo traders who want ML-based signals
- Portfolio managers wanting multi-asset optimization

## Core Stack
- **Backend**: FastAPI + MongoDB + Python RL/ML
- **Frontend**: React + Craco + lightweight-charts + Recharts + Tailwind
- **Build**: Craco (webpack aliases)
- **3rd Party**: Emergent LLM Key (GPT-4o), yFinance, NSE scraping (curl_cffi)

---

## What's Been Implemented

### Phase 1 — Foundation (Early Sessions)
- Dark/light theme toggle
- Mobile responsive layout
- NSE stock search + live quotes
- Interactive chart (lightweight-charts)
- Technical indicators (RSI, MACD, BB, etc.)

### Phase 2 — AI Signals
- MiroFish LangGraph multi-agent orchestration
- Multi-Timeframe Scanner
- Weighted AI Signals aggregator
- SMC Canvas Overlay (FVG, Liquidity, Order Blocks)
- BOS/CHoCH detection
- Premium/Discount Zones
- Fullscreen chart mode

### Phase 3 — DreamerV3 Robo-Trader
- DreamerV3 world model RL agent
- Kronos Forecast integration
- Adaptive Learning Engine
- Paper trading mode
- Ensemble AI cockpit

### Phase 6 — Multi-Stock Parallel Trading + Kronos Fix (Jun 2026) ← LATEST
- Multi-stock watchlist management via `GET/POST /api/robo/watchlist`
- Trading loop scans all watchlist tickers per cycle, up to `max_parallel_trades` (1–5) simultaneous positions
- `execution_engine`: `set_max_positions()` + `has_open_position_for(ticker)` added
- `dreamer_robo_orchestrator`: `watchlist` + `max_parallel_trades` in `UserPreferences`
- Settings modal: "Parallel Trading Watchlist" section with 1×–5× buttons + add/remove UI
- Kronos stale data bug fixed: clears old forecast on stock change

### Phase 7 — Auto-Discover + Vertical Tabs UI (Jun 2026)
- **Auto-Discover Momentum Scanner**: `GET /api/robo/watchlist/discover` — scans NSE F&O universe (50 stocks, 8 workers), scores on momentum, volume spike, trend strength, RSI sweet zone (0–100), returns top 8 candidates. 5-min cache with `?refresh=true` override.
- **Vertical Right Sidebar Tabs**: Replaced horizontal tab bar with vertical tab strip (left side of right panel). Compact labels (SCAN, STRAT, PAPER, RL, ROBO, AI ASM, PICK, PE-CE, QNT). Green left-border active indicator. Responsive: Desktop 68px, iPad 60px, Mobile 52px width.
- Responsive across Desktop (1920px), iPad (1024px), Mobile (390px)

### Phase 5 — Linter Fix & Background Tab Persistence (Feb 2026)
- Fixed 3 Ruff F841 blocking linter errors in `dreamer_robo_orchestrator.py`
- `RLAgentPanel` & `RoboDashboard` always mounted (CSS hide/show) — background training/polling continues on tab switch
- Relaxed `META_CONFIDENCE_FLOOR` from 35→30, dynamic agent weights when DreamerV3 idle
- 5X Leverage on BUY positions (paper + live)
- Nifty/Sensex Options Chart fix (`/api/option/intraday`, `/api/option/sensex-intraday`)

### Phase 4 — QUANT Module (Feb 2026)
**Backend new files:**
- `rl_agent/per_buffer.py` — Prioritized Experience Replay (SumTree, IS weights)
- `rl_agent/risk_reward.py` — Risk-adjusted reward (Sharpe+CVaR+Kelly+Sortino)
- `rl_agent/portfolio_optimizer.py` — Mean-Variance + Black-Litterman + Kelly + CVaR + Efficient Frontier + SOR
- `data_providers/sentiment_provider.py` — Lexicon sentiment + Fear&Greed index
- `observability/metrics_engine.py` — Circuit breakers, kill switch, anomaly detection, Prometheus
- `rl_agent/advanced_router.py` — 22 new API endpoints under /api/advanced/*

**Frontend new files:**
- `PortfolioOptimizerPanel.jsx` — MV + BL + Kelly + CVaR + Efficient Frontier charts
- `AdvancedRiskPanel.jsx` — Kill switch, circuit breakers, human-in-loop approval
- `SentimentPanel.jsx` — News sentiment + Fear&Greed gauge
- `ObservabilityPanel.jsx` — Equity curve, anomaly alerts, PER stats, continuous training toggle, Prometheus

**UI Changes:**
- New `⚡ QUANT` tab in TradingDashboard right panel
- 4 sub-tabs: Portfolio / Risk / Sentiment / Observ.
- TradingView-style grouped timeframe dropdown (with fixed positioning for mobile)

---

## Key API Endpoints

### Core
- `POST /api/orderflow/analyze`
- `POST /api/mirofish/analyze`
- `GET /api/robo/*`
- `GET /api/kronos/*`

### QUANT (Phase 4)
- `POST /api/advanced/portfolio/optimize` — MV / BL
- `POST /api/advanced/portfolio/frontier` — Efficient frontier
- `POST /api/advanced/portfolio/kelly` — Kelly per-asset
- `POST /api/advanced/portfolio/cvar` — CVaR analysis
- `POST /api/advanced/portfolio/hedge-suggest` — Options overlay
- `POST /api/advanced/portfolio/smart-route` — Smart order routing
- `GET  /api/advanced/risk/circuit-status`
- `POST /api/advanced/risk/kill-switch`
- `POST /api/advanced/risk/reset-circuit`
- `GET  /api/advanced/risk/approvals`
- `POST /api/advanced/risk/approve/{id}`
- `GET  /api/advanced/sentiment/news`
- `GET  /api/advanced/sentiment/market`
- `GET  /api/advanced/sentiment/fear-greed`
- `GET  /api/advanced/observability/metrics`
- `GET  /api/advanced/observability/alerts`
- `GET  /api/advanced/observability/prometheus`
- `POST /api/advanced/observability/record-trade`
- `POST /api/advanced/dreamer/continuous-toggle`
- `GET  /api/advanced/dreamer/per-stats`
- `GET  /api/advanced/dreamer/risk-reward`

---

## DB Schema
- `settings` — Robo orchestrator config (ticker, allocated_capital)
- `robo_paper_trades` — Trade audit trail

---

## Architecture Notes
- `server.py` is monolithic (>10k lines) — modularization needed (tech debt)
- `ChartPanel.jsx` is large (>1300 lines) — SMC logic should be extracted to hook
- Observability state is in-memory (reset on restart) — acceptable for MVP
- PER buffer is in-memory (reset on restart) — model saved to disk after each cycle

---

## Prioritized Backlog

### P0 (Critical)
- [ ] server.py refactoring — split into domain routers
- [ ] DEMON endpoint bug — `run_mini_ai_indicator()` tuple unpacking mismatch (server.py:5325)

### P1 (Next)
- [ ] Live mode entry threshold check (50% in trading_loop.py)
- [ ] PCR Alert system — popup when NIFTY/SENSEX PCR crosses threshold
- [ ] Deep UX testing — MultiTF Scanner flows, win probability arc
- [ ] Persist observability metrics to MongoDB

### P2 (Enhancement)
- [ ] 5x Leverage badge display on trade cards
- [ ] Daily trades limit / cooldown config adjustments
- [ ] Real tick data (NSE WebSocket subscription)
- [ ] Reddit/X sentiment (needs API keys)
- [ ] WhatsApp/Telegram share for scan results
- [ ] Multiple expiry switch for Options Sheet
- [ ] Advanced backtesting with P&L curves
- [ ] Real Grafana dashboard config export
- [ ] Co-location / real broker API (Zerodha Kite, Angel One)

### P3 (Backlog)
- [ ] WebXR AR overlay for mobile
- [ ] LangGraph parallel agent execution
- [ ] Multi-account portfolio tracking
