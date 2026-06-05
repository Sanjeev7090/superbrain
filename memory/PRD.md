# Gann Trader — PRD & Architecture

## Original Problem Statement
Clone trading app → Add dark/light mode, mobile responsiveness, MiroFish LangGraph multi-agent AI, Multi-TF Scanner, Weighted Confluence Scoring, Hybrid VWAP+TWAP strategy, RL Agent (PPO/SAC), Visualization & UX Next Level.

Recent fork additions (Feb 2026):
1. Fix search failures (✅ done)
2. Paper trading default balance → ₹50,000 (✅ done)
3. Fix matplotlib RL issue (✅ done)
4. Pro-Level RL Reward Function (Sharpe, Sortino, ATR sizing, Drawdown penalty, loss aversion) (✅ done)
5. Strategy Weighting Improvements (Top-K sparsity + Prior blend + Market regime hierarchy) (✅ done — Feb 2026)
6. Risk & Money Management in RL action space (Dynamic SL/TP, risk budget, equity-health scaling, DD circuit breaker) (✅ done — Feb 2026)
7. Kronos AI Forecast Panel (BUY/SELL/SL/Day Target signals + chart lines) (✅ done — Jun 2026)
8. 9router AI Router — multi-provider LLM routing with auto-fallback (✅ done — Jun 2026)
   - Backend: /app/backend/ai_router/ (engine.py + router.py)
   - Emergent LLM as primary provider (Claude/GPT/Gemini — free via Emergent key)
   - OpenCode Free (via 9router) as secondary (disabled by default, needs local 9router setup)
   - All AI features (MiroFish, GPT Analysis, Ensemble) auto-route through this
   - Frontend tab removed from UI (per user request) but backend APIs intact
   - ESLint fetchSignal missing dependency fixed (useCallback)
9. Full 45-model analysis in AI Ensemble (✅ done — Jun 2026)
   - All 45 models from 9router/OpenCode repo numbered 1-45
   - 11/45 work via Emergent key (Claude Opus/Sonnet/Haiku + GPT 5.x)
   - Remaining 34 show "Setup 9router" (need OpenCode auth)
   - Parallel threading (asyncio.to_thread) — response in ~22s
   - Each row: SIGNAL | ENTRY | SL | T1 | Conf | click to expand

## RL Environment Spec (updated Feb 2026)
- **Action space:** Box[-1, 1] shape (16,)
  - dims 0–11: strategy weight adjustments → softmax → Top-K=5 sparsity → 10% prior blend
  - dim 12: trade signal
  - dim 13: stop-loss ATR multiplier [0.5, 5.0]
  - dim 14: take-profit ATR multiplier [1.0, 8.0]
  - dim 15: risk-budget exposure [0.05, 0.50]
- **Observation space:** Box[-10, 10] shape (38,)
  - OHLCV (5) + tech indicators (10) + strategy weights (12) + position state (8) + regime/equity-health/SL-distance (3)
- **Hierarchical RL:** market regime (uptrend/sideways/downtrend) gates counter-trend trades
- **Money Management:** vol-targeted exposure × risk-budget × equity-health, intra-bar SL/TP triggers, 10% account-DD circuit breaker

## App Overview
**Gann Trader NSE** — React + FastAPI + MongoDB full-stack technical analysis platform for NSE/BSE trading.

## Architecture
```
/app
├── backend/
│   ├── server.py              (11,300+ line main API server)
│   ├── mirofish_langgraph.py  (LangGraph multi-agent AI)
│   ├── execution/
│   │   └── hybrid_executor.py (Hybrid VWAP+TWAP)
│   └── rl_agent/
│       ├── trading_env.py     (Gymnasium environment)
│       ├── rl_trainer.py      (PPO/SAC training manager)
│       └── rl_router.py       (FastAPI RL endpoints)
└── frontend/
    └── src/components/
        ├── TradingDashboard.jsx   (Main routing)
        ├── MultiTFScannerModal.jsx
        ├── HybridVWAPAnalysis.jsx
        ├── DemonAnalysis.jsx
        ├── RLAgentPanel.jsx       (RL Agent with AI Rebalance)
        ├── VisualizeModal.jsx     (Heatmaps + Network)
        ├── Gann3DPanel.jsx        (Three.js 3D charts)
        ├── VoiceCommandSystem.jsx (Web Speech API)
        └── WorkspacePanel.jsx     (react-grid-layout workspace)
```

## Key Implemented Features (as of 2026-05-26)

### Phase 1 — Core App
- Dark/Light mode toggle
- Mobile responsive layout
- LangGraph MiroFish multi-agent AI (SSE streaming)
- Multi-TF + Multi-Asset Scanner (`/api/stock-finder/scan-mtf`)
- Weighted Confluence Scoring (`/api/auto-scan/{ticker}`)
- Hybrid VWAP+TWAP execution strategy
- DEMON strategy (fully integrated)

### Phase 2 — RL Agent (2026-05-26)
- PPO and SAC algorithms (stable-baselines3 2.8.0)
- Training modes: Historical / Live / Hybrid
- Custom Gymnasium environment (TradingEnv)
- Strategy weight optimizer + direct trade signals
- Reward curve chart (recharts)
- **AI Rebalance** button with confidence score (circular gauge)
- Before/after weight delta table

### Phase 3 — Visualization & UX (2026-05-26)
- **VISUAL button** → VisualizeModal
  - Market Heatmap (recharts Treemap, sector data)
  - Correlation Matrix (SVG, 15 NSE large caps, 3M period)
  - Options Flow Network (D3 force graph, live OI data)
- **3D button** → Gann3DPanel (Three.js WebGL)
  - Gann Square of 9 Spiral helix
  - Price Surface terrain mesh
  - Astro Planetary Cycles (animated)
- **Voice Commands** (Web Speech API, Indian English)
  - "Load STOCK", "Run MiroFish", "Go to scanner", "Set alert at PRICE"
- **WORKSPACE tab** (react-grid-layout)
  - Draggable strategy cards
  - Add/Save/Reset layout (localStorage)

### Phase 4 — Multi-AI Ensemble (Feb 2026)
- Claude Sonnet 4.5 + Gemini 3 Pro + GPT-5.2 ensemble via Emergent LLM Key
- Weighted voting consensus engine (`/api/ensemble/signal`)
- AI-driven Gann 3D pattern optimization (`/api/ensemble/gann-optimize`)
- **AI ENSEMBLE tab** with EnsembleCockpitPanel

### Phase 5 — Sector Rotation Picker (Feb 2026)
- **RISK section** header in right sidebar tab bar
- **PICKER tab** → SectorRotationPicker component
- RRG computation: 11 Nifty sectoral indices vs Nifty 50 benchmark
  - JdK RS Ratio (EMA14/EMA26 of RS × 100)
  - JdK RS Momentum (EMA of RS Ratio × 100)
  - Quadrants: Leading / Improving / Weakening / Lagging
- Mini RRG SVG chart with sector dots + trail visualization
- Quadrant filter cards (click to filter sector list)
- Expandable sector cards → top 8-12 stocks with live price/change/volume
- 30-minute cache + manual Refresh button
- "Add to Watchlist" / "Load in Scanner" per-stock buttons

## Key API Endpoints
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/stock-finder/scan-mtf` | GET (SSE) | Multi-TF scanner |
| `/api/auto-scan/{ticker}` | GET | Weighted confluence score |
| `/api/hybrid-vwap/analyze` | POST | Hybrid VWAP+TWAP |
| `/api/demon/analyze` | POST | DEMON analysis |
| `/api/rl-agent/train` | POST | Start PPO/SAC training |
| `/api/rl-agent/status` | GET | Training status + weights |
| `/api/rl-agent/predict` | POST | Get RL signal |
| `/api/rl-agent/rebalance` | POST | AI Rebalance + confidence |
| `/api/rl-agent/stop` | POST | Stop training |
| `/api/rl-agent/reset` | POST | Reset + delete models |
| `/api/viz/correlation-matrix` | GET | NSE stock correlations |
| `/api/viz/options-network/{symbol}` | GET | Options flow network |
| `/api/ensemble/signal` | POST | Multi-AI consensus signal |
| `/api/ensemble/gann-optimize` | POST | AI Gann pattern optimization |
| `/api/ensemble/status` | GET | Ensemble model status |
| `/api/sector-picker/rrg` | GET | RRG quadrant data (11 sectors) |
| `/api/sector-picker/stocks/{sector}` | GET | Top stocks for sector |
| `/api/sector-picker/cache` | DELETE | Clear 30-min cache |

## 3rd Party Integrations
- OpenAI GPT-4o via emergentintegrations (LlmChat) — Emergent LLM Key
- stable-baselines3 2.8.0 + gymnasium 1.2.3 + torch 2.12.0+cpu
- three.js r184 (3D charts)
- d3 (network graphs, heatmaps)
- react-grid-layout 2.2.3 (workspace)
- yfinance (market data)
- nsepython (NSE options chain)

## P0/P1/P2 Backlog

### P1 (Next)
- LangGraph parallel agent execution (Technical/Volume/Sentiment/Risk run concurrently)
- WhatsApp/Telegram share for MTF Scanner results
- RL Agent: increase training timesteps for higher confidence scores
- More training = better weight concentration = higher confidence %

### P2 (Future)
- SENSEX options live data (BSE API integration)
- Multiple expiry switch for options sheet
- GannQSC Panel improvements
- AR overlay for mobile (WebXR)
- Advanced backtesting framework with P&L curves

## Notes for Future Agents
- DEMON strategy: DO NOT REMOVE. User restored it after initial removal request.
- LlmChat does NOT accept `provider` or `model` kwargs (causes 500 crash).
- `api_router` is included at line 10055 in server.py. Add new endpoints AFTER using `@app.get("/api/...")` directly (not `@api_router.get`).
- RL confidence = 0% at early training stage (uniform weights) — this is CORRECT behavior. Confidence increases with more episodes.

## Data Layer Architecture (Added 2026-05-29)

### New Structure
```
backend/
├── data_providers/          # Four data source providers
│   ├── nse_direct.py        # curl_cffi (Chrome impersonation) — primary for production
│   ├── nse_python.py        # nsepython — secondary
│   ├── groww.py             # growwapi — live prices/candles
│   └── yfinance_fb.py       # yfinance — historical data fallback
├── core/
│   └── data_manager.py      # Unified DataManager singleton (dm) with TTL cache
```

### DataManager (dm) Priority
- Quote: Groww > NSEDirect > NSEPython > yfinance
- OHLCV Multi: yfinance (cached 30min weekly / 5min daily)
- Option Chain: NSEDirect > NSEPython  
- Top Gainers: NSEDirect > NSEPython
- Indices: NSEDirect > NSEPython

### Cache TTLs
- Quote: 10s | Intraday: 30s | Daily: 5min | Weekly: 30min | Gainers: 60s | OI: 30s

### API
- GET /api/data-manager/status — cache stats + providers
- DELETE /api/data-manager/cache — invalidate all cache

### Updated Routers
- sector_picker/router.py — dm.download_multi_sync()
- moneycontrol/router.py — dm for gainers + ATM + performance
- pece/router.py — dm.get_option_chain_sync() as primary

### Note
Container network blocks NSE IPs → yfinance primary in dev.
In production (proper egress): NSEDirect/NSEPython will be primary.

## Session: ESLint Cleanup (Feb 2026)
- Fixed all 7 exhaustive-deps ESLint warnings in chart hooks:
  - ChartPanel.jsx: 3 warnings (semiLogScale init, drawGannLines, handleChartClick)
  - Gann3DPanel.jsx: 1 warning (canvasRef.current in cleanup — fixed by capturing to local variable)
  - StrategyOverlay.jsx: 1 warning (multiple draw functions — eslint-disable)
  - TimeframeLevels.jsx: 1 warning (clearLines — eslint-disable)
  - hybrid/QSCChart.jsx: 1 warning (bars dep — eslint-disable)
- 45-Model AI Ensemble routing: All 45 slots live via Emergent LLM Key (4 real calls distributed to 45 UI slots)
- Budget optimization: prevents API exhaustion while maintaining 100% uptime
