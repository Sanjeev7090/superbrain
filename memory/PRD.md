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

### Phase 1 — Foundation
- Dark/light theme toggle, mobile responsive layout, NSE stock search + live quotes
- Interactive chart (lightweight-charts), Technical indicators (RSI, MACD, BB, etc.)

### Phase 2 — AI Signals
- MiroFish LangGraph multi-agent orchestration (5 LLM nodes: Tech→Vol→Sentiment→Risk→Decision)
- Multi-Timeframe Scanner, Weighted AI Signals aggregator
- SMC Canvas Overlay (FVG, Liquidity, Order Blocks, BOS/CHoCH, Supply/Demand)

### Phase 3 — DreamerV3 Robo-Trader
- DreamerV3 world model RL agent, Kronos Forecast integration
- Adaptive Learning Engine, Paper trading mode

### Phase 4 — QUANT Module
- RL agent (PER buffer, risk-adjusted reward), Portfolio Optimizer
- Advanced Risk Panel, Sentiment Panel, Observability Panel

### Phase 5 — Linter Fixes + Background Tab Persistence
- Fixed blocking Ruff errors, RLAgentPanel always mounted (CSS), dynamic agent weights

### Phase 6 — Multi-Stock Parallel Trading
- Multi-stock watchlist management, parallel position sizing
- Settings modal with watchlist add/remove + max_parallel_trades buttons

### Phase 7 — Auto-Discover + Vertical Tabs UI
- Auto-Discover Momentum Scanner (NSE F&O universe, 50 stocks)
- Vertical Right Sidebar Tabs (SCAN/STRAT/PAPER/RL/ROBO/AI ASM/PICK/PE-CE/QNT)

### Phase 8 — SMC Canvas Upgrades + DeltaDash + F&O Parity (Jun 2026)
- F&O Put-Call Parity Scanner: `/api/options/parity-scanner`, "Open in Chart" button
- DeltaDash Analysis Scoreboard: `/api/deltadash/scoreboard` (44+ tickers × 6 TFs)
- ChartPanel.jsx massively upgraded: Supply/Demand Zones, Wyckoff Accumulation/Distribution,
  Manipulation (Stop Hunts), Refined Entry with SL/TGT dashed lines + R:R ratios

### Phase 10 — Danger Mode + Brain Auto-Activation (Jun 2026)
- **Danger Mode (risk_tolerance = "danger")**:
  - No direct equity trades — F&O universe only
  - `danger_scanner.py`: 34 F&O tickers scored by momentum (5d return, vol spike, ATR, RSI)
    + PCR parity boost (STRONGLY_BULLISH=+22, BULLISH=+14, BEARISH=-10, STRONGLY_BEARISH=-18)
  - `GET /api/robo/danger-scan` endpoint returns top picks with pcr_signal, final_score, sector
  - Trading loop auto-overrides watchlist with danger scan picks each cycle
  - DreamerV3 gets +25% confidence boost in danger mode
  - Frontend: 2×2 risk grid, red Danger card with skull SVG icon, "F&O ONLY" badge,
    "Danger Mode Active" warning notice, "DANGER · F&O" header badge, F&O Picks panel
- **Hybrid Brain Auto-Activation with Auto Start**:
  - `POST /api/robo/start` fires `_warmup_brain()` as asyncio background task
  - Warmup: loads survival state from MongoDB → `think_and_decide()` → updates `_state` immediately
  - `_state` gets: `brain_active`, `brain_action`, `brain_confidence`, `brain_fear`, `brain_regime`
  - `GET /api/robo/status` now returns all brain fields
  - `POST /api/robo/stop` resets `brain_active=False`
  - Trading loop: each cycle calls `hybrid_brain.decide_sync()`, applies brain-dreamer alignment boost
    (+10 conf if agree) or fear circuit breaker (forced HOLD if fear > 0.70) or disagreement penalty (-15)
  - Frontend: "BRAIN ON" pulsing badge in header when active, "Brain Live Strip" below start button
    showing action/confidence/fear/regime, mini ⚡ button to re-fire manually
- **Hybrid Super Brain v2 (`hybrid_super_brain.py` fully rewritten as central brain)**:
  - `MildSurvivalEngine` — MongoDB-persisted fear/boost scalar, grace period, overnight decay
  - `PsychologicalHarvester` — FOMO, Apathy, Regime, Narrative Credibility from real market data
  - `MetaReasoner` — MiroFish LangGraph 5-node pipeline (ainvoke, NOT SSE), agreement scoring
  - `HybridSuperBrain` — Central orchestrator: DreamerV3 → StrategyCollaborator (6 agents) → 
    MiroFish LangGraph → MetaReasoner → RPM heat gate → MongoDB audit
  - `decide_sync()` + `update_daily_pnl_sync()` for DreamerV3 tight coupling
- **Hybrid Brain Visualization in RoboAdvisorDashboard.jsx**:
  - Fear Level circular gauge, Consecutive Misses, Daily Target, Last PnL cards
  - "Fire Brain" button → live decision with confidence bar, component breakdown
  - Brain State tab + Decision Log tab (scrollable audit)
- **Unified Audit Log**: Brain decisions + Paper trades merged in `/api/robo/audit`
- **Live P&L on Open Positions**: `GET /api/robo/positions` enriches each position with 
  `current_price`, `unrealized_pnl`, `pnl_pct`, `price_change` (15s cache via yfinance)
- **Watchlist Clear Fix**: `removeFromWatchlist` immediately POSTs to backend (no save-required)

---

## Prioritized Backlog

### P1
- [ ] Run/test Auto-mode to evaluate live brain decisions natively in paper mode
- [ ] Visualize StrategyCollaborator 6-agent signals in ROBO tab (radar/table view)

### P2
- [ ] PCR Alert system — popup on NIFTY/SENSEX PCR threshold cross
- [ ] Real tick data — NSE WebSocket for sub-second price updates
- [ ] Auto-mode brain override — when brain fires BUY and dreamer says HOLD, show override log

### P3
- [ ] ChartPanel.jsx refactoring (~1700 lines → split into SmcOverlay.jsx, ChartCore.jsx)
- [ ] server.py modularization (11k+ lines — route by feature into /agents/)
- [ ] Kronos fix — TATAMOTORS.NS delisted ticker cleanup in default scan universe

---

## Key API Endpoints
- `POST /api/hybrid-brain/decide` — Full 5-layer decision (psych+strategy+miro+dreamer+survival)
- `GET  /api/hybrid-brain/state`  — Fear level, consecutive fails, daily target, PnL
- `GET  /api/hybrid-brain/audit`  — Decision history (MongoDB)
- `GET  /api/robo/positions`      — Open positions with live current_price + unrealized_pnl
- `GET  /api/robo/audit`          — Paper trades + brain decisions merged
- `GET  /api/deltadash/scoreboard`— 44+ tickers × 6 TF multi-indicator scorer
- `POST /api/options/put-call-parity` — PCR calculator
- `GET  /api/options/parity-scanner`  — Auto-scanner for all indices

## DB Collections
- `hybrid_brain_state` — survival fear/fail counters (MongoDB-persisted)
- `hybrid_brain_audit` — all brain decisions log
- `robo_user_preferences` — user trading settings singleton
- `robo_orders` — all paper/live trade orders
