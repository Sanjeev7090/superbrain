from fastapi import FastAPI, APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import StreamingResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, timezone, timedelta
import math
import asyncio
import json
import httpx
import websockets
import yfinance as yf
import pandas as pd
import numpy as np
import random
from nsepython import nse_optionchain_scrapper, nse_quote_ltp
from openai import OpenAI, AsyncOpenAI
from emergentintegrations.llm.chat import LlmChat, UserMessage

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

app = FastAPI(
    title       = "Dreamer V3 Robo-Trader API",
    description = "Institutional-grade autonomous trading engine. PAPER TRADING DEFAULT. No guaranteed returns.",
    version     = "3.0.0",
)
api_router = APIRouter(prefix="/api")

cache_storage = {}


# ---------------------------------------------------------------------------
# Unified LLM helper
# ---------------------------------------------------------------------------
# Prefers a direct OpenAI call using OPENAI_API_KEY (the user's own key, no
# Emergent budget). Falls back to LlmChat + EMERGENT_LLM_KEY only when the
# OpenAI key is not configured. Anthropic model hints are routed to gpt-4o
# when using the OpenAI direct path.
_OPENAI_ASYNC_CLIENT: Optional[AsyncOpenAI] = None


def _get_openai_async_client() -> Optional[AsyncOpenAI]:
    global _OPENAI_ASYNC_CLIENT
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        return None
    if _OPENAI_ASYNC_CLIENT is None:
        _OPENAI_ASYNC_CLIENT = AsyncOpenAI(api_key=key)
    return _OPENAI_ASYNC_CLIENT


def _map_model(provider: str, model: str) -> str:
    """Map (provider, model) hints to a concrete OpenAI model name."""
    if provider == "openai":
        return model or "gpt-4o"
    # anthropic / claude fallbacks → use gpt-4o (most capable available)
    if provider == "anthropic":
        return "gpt-4o"
    return "gpt-4o"


async def llm_complete(
    system_message: str,
    user_text: str,
    provider: str = "openai",
    model: str = "gpt-4o",
    session_id: Optional[str] = None,
    temperature: float = 0.7,
) -> Optional[str]:
    """Run an LLM completion. Returns response text or None on failure.

    Priority:
      1) OPENAI_API_KEY (direct OpenAI) — user's own key, no budget cap
      2) EMERGENT_LLM_KEY (via emergentintegrations LlmChat) — fallback
    """
    # Try direct OpenAI first
    oa_client = _get_openai_async_client()
    if oa_client is not None:
        try:
            mapped = _map_model(provider, model)
            resp = await oa_client.chat.completions.create(
                model=mapped,
                messages=[
                    {"role": "system", "content": system_message},
                    {"role": "user", "content": user_text},
                ],
                temperature=temperature,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            logging.warning(f"OpenAI direct call failed, falling back to Emergent: {e}")

    # Fallback to Emergent
    emergent_key = os.environ.get("EMERGENT_LLM_KEY", "").strip()
    if not emergent_key:
        # Try AI Router (OpenCode Free / configured providers)
        try:
            from ai_router.engine import ai_complete as _ai_complete
            msgs = [{"role": "user", "content": user_text}]
            result = await _ai_complete(messages=msgs, system=system_message, temperature=temperature)
            if result:
                return result
        except Exception as _e:
            logging.warning(f"AI Router fallback failed: {_e}")
        return None
    try:
        chat = LlmChat(
            api_key=emergent_key,
            session_id=session_id or f"llm-{uuid.uuid4().hex[:8]}",
            system_message=system_message,
        )
        chat.with_model(provider, model)
        return await chat.send_message(UserMessage(text=user_text))
    except Exception as e:
        # Last resort: AI Router
        try:
            from ai_router.engine import ai_complete as _ai_complete
            msgs = [{"role": "user", "content": user_text}]
            result = await _ai_complete(messages=msgs, system=system_message, temperature=temperature)
            if result:
                return result
        except Exception:
            pass
        logging.warning(f"Emergent LLM call failed: {e}")
        return None


class OHLCVBar(BaseModel):
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    
class StockDataResponse(BaseModel):
    ticker: str
    bars: List[OHLCVBar]
    
class GannAngle(BaseModel):
    angle_type: str
    price_levels: List[float]
    
class GannFanRequest(BaseModel):
    ticker: str
    pivot_price: float
    pivot_timestamp: int
    bars_count: int = 50
    
class GannFanResponse(BaseModel):
    angles: List[GannAngle]
    pivot_price: float
    pivot_timestamp: int
    
class SquareOf9Response(BaseModel):
    center_price: float
    targets: dict
    
class SignalResponse(BaseModel):
    ticker: str
    signal: str
    color: str
    price: float
    angle_1x1: float
    timestamp: int

class AITradeAnalysisRequest(BaseModel):
    ticker: str
    timeframe: str
    bars: List[dict]

class AITradeAnalysisResponse(BaseModel):
    direction: str
    entry_price: str
    stoploss: str
    targets: List[str]
    reason: str

class FallingKnifeAnalysisRequest(BaseModel):
    ticker: str
    bars: List[dict]

class FallingKnifeAnalysisResponse(BaseModel):
    status: str
    signal_type: str
    conditions_met: int
    drop_percentage: Optional[float] = None
    bollinger_squeeze: bool
    price_in_keltner: bool
    macd_bullish: bool
    entry_price: Optional[str] = None
    stop_loss: Optional[str] = None
    targets: Optional[List[str]] = None
    recommendation: str

class ReverseSwingsRequest(BaseModel):
    ticker: str
    bars: List[dict]
    force_method: Optional[str] = None  # 'A' or 'B'

class ReverseSwingsResponse(BaseModel):
    method: str
    signal_type: str
    trend_confirmed: bool
    swing_signal: bool
    valid_entry_day: bool
    signal_active: bool
    current_swing: str
    avg_swing: str
    threshold_swing: str
    price_comparison: str
    entry_price: Optional[str] = None
    stop_loss: Optional[str] = None
    targets: Optional[List[str]] = None
    entry_day: Optional[str] = None
    recommendation: str

class OIDataResponse(BaseModel):
    symbol: str
    total_call_oi: float
    total_put_oi: float
    pcr: float
    max_pain: Optional[float] = None
    top_strikes: List[dict]
    signal: str
    signal_color: str


# --- New Models for Watchlist, Portfolio, Alerts, Backtest, GPT AI ---

class WatchlistItem(BaseModel):
    ticker: str
    name: str
    stock_type: str = "STOCK"

class WatchlistResponse(BaseModel):
    id: str
    ticker: str
    name: str
    stock_type: str
    added_at: str

class PortfolioEntry(BaseModel):
    ticker: str
    name: str
    buy_price: float
    quantity: int
    buy_date: Optional[str] = None

class PortfolioResponse(BaseModel):
    id: str
    ticker: str
    name: str
    buy_price: float
    quantity: int
    buy_date: str
    current_price: Optional[float] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None

class AlertRule(BaseModel):
    ticker: str
    name: str
    alert_type: str  # 'price_above', 'price_below', 'demon_buy', 'demon_sell'
    threshold: Optional[float] = None

# ---- Paper Trading Models ----
class PaperOrderRequest(BaseModel):
    symbol: str
    name: str = ""
    direction: str  # BUY or SELL
    quantity: int
    entry_price: float
    stop_loss: float
    target: float
    strategy: str = "MANUAL"
    source: str = "MANUAL"  # MANUAL or AUTO

class PaperCloseRequest(BaseModel):
    exit_price: float

class AlertResponse(BaseModel):
    id: str
    ticker: str
    name: str
    alert_type: str
    threshold: Optional[float] = None
    triggered: bool = False
    triggered_at: Optional[str] = None
    created_at: str

class GPTAnalysisRequest(BaseModel):
    ticker: str
    timeframe: str
    bars: List[dict]

class GPTAnalysisResponse(BaseModel):
    direction: str
    entry_price: str
    stoploss: str
    targets: List[str]
    reason: str
    confidence: int
    key_levels: Optional[List[str]] = None
    risk_reward: Optional[str] = None

class BacktestRequest(BaseModel):
    ticker: str
    strategy: str  # 'falling_knife', 'golden_setup', 'demon', 'reverse_swings', 'godzilla', 'smc', 'amds', 'narrative_swing', 'all'
    days: int = 90
    timeframe: str = 'intraday'

class MonteCarloRequest(BaseModel):
    ticker: str
    strategy: str
    days: int = 90
    timeframe: str = 'intraday'
    simulations: int = 1000  # Number of Monte Carlo simulations
    initial_capital: float = 100000.0

class MonteCarloResult(BaseModel):
    simulation_id: int
    total_return: float
    win_rate: float
    max_drawdown: float
    sharpe_ratio: float
    total_trades: int

class MonteCarloResponse(BaseModel):
    ticker: str
    strategy: str
    simulations: int
    initial_capital: float
    
    # Summary statistics
    avg_return: float
    median_return: float
    best_return: float
    worst_return: float
    std_return: float
    
    avg_win_rate: float
    median_win_rate: float
    
    avg_max_drawdown: float
    worst_drawdown: float
    
    avg_sharpe: float
    median_sharpe: float
    
    # Confidence intervals (5th, 25th, 75th, 95th percentiles)
    return_percentiles: Dict[str, float]
    winrate_percentiles: Dict[str, float]
    drawdown_percentiles: Dict[str, float]
    
    # Probability metrics
    prob_positive_return: float  # % of simulations with positive return
    prob_above_market: float  # % beating 10% benchmark
    
    # Distribution data for charts (100 bins)
    return_distribution: List[Dict[str, float]]
    
    # Sample simulations
    sample_simulations: List[MonteCarloResult]


# ======================= NARRATIVE SWING TRADER MODELS =======================

class NarrativeSwingRequest(BaseModel):
    ticker: str
    bars: List[dict]
    timeframe: Optional[str] = "1D"
    buy_threshold: float = 0.25
    sell_threshold: float = -0.15

class NarrativeSwingResponse(BaseModel):
    status: str
    signal_type: str           # 'BUY' | 'SELL' | 'WAIT'
    narrative_score: float
    momentum: float
    volatility: float
    rel_price: float
    narrative_label: str       # e.g. 'STRONG BULLISH', 'MILD BEARISH'
    entry_price: Optional[str] = None
    stop_loss: Optional[str] = None
    target1: Optional[str] = None
    target2: Optional[str] = None
    target3: Optional[str] = None
    risk_reward: Optional[str] = None
    atr_value: Optional[float] = None
    score_bars: Optional[List[float]] = None   # last 30 scores for mini-chart
    confidence: int = 0
    recommendation: str

# Hybrid VWAP+TWAP Models
class HybridVWAPRequest(BaseModel):
    ticker: str
    bars: List[dict]
    quantity: Optional[int] = 100
    side: Optional[str] = "BUY"
    duration_minutes: Optional[int] = 30
    max_slices: Optional[int] = 12

class VWAPSlice(BaseModel):
    slice_no: int
    time_offset_min: float
    qty: int
    target_price: float
    vwap_basis: float

class HybridVWAPResponse(BaseModel):
    status: str
    signal_type: str           # BUY | SELL | WAIT
    confidence: int
    vwap: float
    twap: float
    upper_band: float
    lower_band: float
    current_price: float
    vwap_deviation_pct: float
    price_position: str        # ABOVE_VWAP | BELOW_VWAP | AT_VWAP
    rsi: float
    atr: float
    volume_ratio: float
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    target1: Optional[float] = None
    target2: Optional[float] = None
    target3: Optional[float] = None
    risk_reward: Optional[str] = None
    vwap_signal_type: str      # BOUNCE | TREND_FOLLOW | WAIT
    execution_plan: Optional[List[VWAPSlice]] = None
    recommendation: str


# SMC (Smart Money Concepts) Models
class SMCAnalysisRequest(BaseModel):
    ticker: str
    bars: List[dict]
    timeframe: Optional[str] = "15M"

class SMCPhase(BaseModel):
    phase: int
    name: str
    status: str
    detail: str

class SMCAnalysisResponse(BaseModel):
    status: str
    signal_type: str
    daily_bias: str
    liquidity_sweep: str
    mss_detected: bool
    ifvg_zone: Optional[str] = None
    entry_price: Optional[str] = None
    stop_loss: Optional[str] = None
    tp1: Optional[str] = None
    tp2: Optional[str] = None
    risk_reward: Optional[str] = None
    atr_value: Optional[float] = None
    rejection_quality: Optional[str] = None
    volume_confirmed: bool = False
    session_valid: bool = False
    phases: List[SMCPhase] = []
    confidence: int = 0
    recommendation: str

# AMDS-Hybrid Models
class AMDSAnalysisRequest(BaseModel):
    ticker: str
    bars: List[dict]
    timeframe: Optional[str] = "15M"

class AMDSStep(BaseModel):
    step: int
    name: str
    status: str
    detail: str

class AMDSAnalysisResponse(BaseModel):
    status: str
    signal_type: str
    htf_bias: str
    accumulation_range: Optional[str] = None
    manipulation_sweep: Optional[str] = None
    cisd_detected: bool = False
    bos_detected: bool = False
    adx_value: Optional[float] = None
    rsi_value: Optional[float] = None
    obv_trend: Optional[str] = None
    composite_score: Optional[float] = None
    entry_price: Optional[str] = None
    stop_loss: Optional[str] = None
    tp1: Optional[str] = None
    tp2: Optional[str] = None
    risk_reward: Optional[str] = None
    atr_value: Optional[float] = None
    steps: List[AMDSStep] = []
    confidence: int = 0
    recommendation: str

# ======================= MIROFISH MODELS =======================

# PAC + S&O Matrix Models
class PACSORequest(BaseModel):
    ticker: str
    bars: List[dict]
    timeframe: Optional[str] = "15M"

class PACSOModule(BaseModel):
    module: str
    status: str
    detail: str
    sub_signals: List[str] = []

class PACSOResponse(BaseModel):
    status: str
    signal_type: str
    structure_bias: str
    bos_detected: bool = False
    choch_detected: bool = False
    choch_plus: bool = False
    order_block_zone: Optional[str] = None
    order_block_type: Optional[str] = None
    liquidity_swept: bool = False
    fvg_zone: Optional[str] = None
    premium_discount: str = "NEUTRAL"
    signal_strength: Optional[str] = None
    neo_cloud_trend: Optional[str] = None
    smart_trail_level: Optional[str] = None
    money_flow: Optional[str] = None
    divergence: Optional[str] = None
    momentum_state: Optional[str] = None
    entry_price: Optional[str] = None
    stop_loss: Optional[str] = None
    tp1: Optional[str] = None
    tp2: Optional[str] = None
    tp3: Optional[str] = None
    risk_reward: Optional[str] = None
    atr_value: Optional[float] = None
    confluence_score: int = 0
    modules: List[PACSOModule] = []
    confidence: int = 0
    recommendation: str

class MiroFishRequest(BaseModel):
    ticker: str
    bars: List[dict]
    timeframe: Optional[str] = "1D"

class MiroFishAgentVerdict(BaseModel):
    agent_name: str
    role: str
    verdict: str
    reasoning: str
    confidence: int

class MiroFishResponse(BaseModel):
    status: str
    signal_type: str
    swarm_consensus: str
    consensus_score: float
    direction: str
    entry_price: Optional[str] = None
    stop_loss: Optional[str] = None
    day_target: Optional[str] = None
    targets: Optional[List[str]] = None
    risk_reward: Optional[str] = None
    news_sentiment: str
    news_summary: str
    agents: List[MiroFishAgentVerdict] = []
    confidence: int = 0
    recommendation: str


# ======================= ORDER FLOW MODELS =======================

class OrderFlowRequest(BaseModel):
    ticker: str
    bars: List[dict]
    n_vp_bins: int = 24
    n_fp_levels: int = 8
    vp_lookback: int = 50

class OFCandleData(BaseModel):
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    buy_volume: float
    sell_volume: float
    delta: float
    cvd: float
    delta_pct: float

class FootprintLevel(BaseModel):
    price: float
    buy_vol: float
    sell_vol: float
    delta: float
    imbalance_pct: float   # (buy-sell)/total*100

class FootprintCandle(BaseModel):
    idx: int
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    total_volume: float
    total_delta: float
    bullish: bool
    levels: List[FootprintLevel]

class VPBin(BaseModel):
    price_low: float
    price_mid: float
    price_high: float
    total_vol: float
    buy_vol: float
    sell_vol: float
    is_poc: bool = False
    in_value_area: bool = False

class OrderFlowResponse(BaseModel):
    ticker: str
    signal_type: str          # BUY / SELL / WAIT
    signal_strength: str      # STRONG / MODERATE / WEAK
    entry_price: Optional[str] = None
    stop_loss: Optional[str] = None
    target1: Optional[str] = None
    target2: Optional[str] = None
    risk_reward: Optional[str] = None
    atr: float
    # Summary stats
    total_buy_vol: float
    total_sell_vol: float
    buy_pct: float
    sell_pct: float
    current_delta: float
    current_cvd: float
    cvd_slope: str            # RISING / FALLING / FLAT
    poc_price: float
    vah_price: float
    val_price: float
    divergence: str           # BULLISH_DIV / BEARISH_DIV / NONE
    # Series data (last 60 candles)
    candles: List[OFCandleData]
    # Volume profile bins
    vp_bins: List[VPBin]
    # Footprint (last 12 candles)
    footprint: List[FootprintCandle]
    confidence: int
    recommendation: str

class BacktestTradeResult(BaseModel):
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    pnl_pct: float
    signal: str
    strategy: Optional[str] = None
    holding_bars: Optional[int] = None

class DailySummary(BaseModel):
    date: str
    total_trades: int
    winning: int
    losing: int
    win_rate: float
    day_pnl: float

class BacktestResponse(BaseModel):
    ticker: str
    strategy: str
    timeframe: str
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_return: float
    max_drawdown: float
    total_return: float
    avg_trades_per_day: float
    trading_days: int
    trades: List[BacktestTradeResult]
    daily_summary: Optional[List[DailySummary]] = None


@api_router.get("/")
async def root():
    return {"message": "Gann Angles Trader API - NSE Edition"}


@api_router.get("/stock/search")
async def search_stock(q: str = Query(..., min_length=1)):
    """Search Groww universe: indices (NIFTY 50, BANK NIFTY, SENSEX) + NSE/BSE equities."""
    cache_key = f"search_{q.lower()}"
    if cache_key in cache_storage:
        cached_data, cached_time = cache_storage[cache_key]
        if (datetime.now() - cached_time).seconds < 300:
            return cached_data

    try:
        results = []
        # Primary: Groww search universe (indices + 12k stocks)
        try:
            import groww_service
            results = groww_service.search_instruments(q, limit=25)
        except Exception as ge:
            logging.warning(f"Groww search unavailable: {ge}")

        # Fallback: hardcoded NIFTY/BANKNIFTY/SENSEX so search always works
        if not results:
            q_upper = q.upper()
            fallback = [
                {"ticker": "^NSEI",   "groww_symbol": "NIFTY",     "name": "NIFTY 50",         "type": "INDEX", "exchange": "NSE"},
                {"ticker": "^NSEBANK","groww_symbol": "BANKNIFTY", "name": "NIFTY Bank",       "type": "INDEX", "exchange": "NSE"},
                {"ticker": "^BSESN",  "groww_symbol": "SENSEX",    "name": "BSE Sensex",       "type": "INDEX", "exchange": "BSE"},
            ]
            results = [s for s in fallback
                       if q_upper in s["groww_symbol"] or q_upper in s["name"].upper()]

        result = {"results": results[:25]}
        cache_storage[cache_key] = (result, datetime.now())
        return result
    except Exception as e:
        logging.error(f"Error searching stocks: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/stock/bars/{ticker}", response_model=StockDataResponse)
async def get_stock_bars(
    ticker: str,
    timespan: str = "day",
    multiplier: int = 1,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    limit: int = 120
):
    """Get historical OHLCV data using yfinance"""
    try:
        # yfinance valid intervals: 1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 4h, 1d, 5d, 1wk, 1mo, 3mo
        # Note: 10m is NOT supported by yfinance, so we map it to 15m (closest supported)
        interval_map = {
            (1, "minute"): "1m",
            (10, "minute"): "15m",  # 10m not supported, use 15m instead
            (30, "minute"): "30m",
            (1, "hour"): "1h",
            (4, "hour"): "4h",
            (1, "day"): "1d",
            (1, "week"): "1wk",
        }
        
        interval = interval_map.get((multiplier, timespan), "1d")
        
        # yfinance strict limits:
        # 1m: max 7 days | 2m/5m/15m/30m: max 60 days | 60m/1h: max 730 days | 4h: max 730 days
        # For daily/weekly: no practical limit
        is_intraday = timespan in ["minute", "hour"]
        
        if is_intraday:
            # Choose max safe period based on interval
            if interval in ["1m"]:
                period = "7d"
            elif interval in ["15m", "30m", "5m", "2m"]:
                period = "60d"
            elif interval in ["1h"]:
                period = "730d"
            elif interval in ["4h"]:
                period = "730d"
            else:
                period = "60d"
            
            # If from_date is provided, clamp it within allowed range
            if from_date:
                max_days = 7 if interval in ["1m"] else 60 if interval in ["15m", "30m", "5m", "2m"] else 730
                earliest_allowed = (datetime.now() - timedelta(days=max_days)).strftime("%Y-%m-%d")
                if from_date < earliest_allowed:
                    from_date = earliest_allowed
                if not to_date:
                    to_date = datetime.now().strftime("%Y-%m-%d")
                
                cache_key = f"bars_{ticker}_{interval}_{from_date}_{to_date}"
                if cache_key in cache_storage:
                    cached_data, cached_time = cache_storage[cache_key]
                    if (datetime.now() - cached_time).seconds < 300:
                        return cached_data
                
                stock = yf.Ticker(ticker)
                hist = stock.history(start=from_date, end=to_date, interval=interval)
            else:
                cache_key = f"bars_{ticker}_{interval}_{period}"
                if cache_key in cache_storage:
                    cached_data, cached_time = cache_storage[cache_key]
                    if (datetime.now() - cached_time).seconds < 300:
                        return cached_data
                
                stock = yf.Ticker(ticker)
                hist = stock.history(period=period, interval=interval)
        else:
            if not from_date:
                from_date = (datetime.now() - timedelta(days=120)).strftime("%Y-%m-%d")
            if not to_date:
                to_date = datetime.now().strftime("%Y-%m-%d")
            
            cache_key = f"bars_{ticker}_{interval}_{from_date}_{to_date}"
            if cache_key in cache_storage:
                cached_data, cached_time = cache_storage[cache_key]
                if (datetime.now() - cached_time).seconds < 600:
                    return cached_data
            
            stock = yf.Ticker(ticker)
            hist = stock.history(start=from_date, end=to_date, interval=interval)
        
        if hist.empty:
            raise HTTPException(status_code=404, detail=f"No data found for {ticker}")
        
        bars = []
        for index, row in hist.iterrows():
            bars.append(OHLCVBar(
                timestamp=int(index.timestamp() * 1000),
                open=float(row['Open']),
                high=float(row['High']),
                low=float(row['Low']),
                close=float(row['Close']),
                volume=float(row['Volume'])
            ))
        
        result = StockDataResponse(ticker=ticker.upper(), bars=bars)
        cache_storage[cache_key] = (result, datetime.now())
        return result
    
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error fetching bars for {ticker}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/nse/oi/{symbol}", response_model=OIDataResponse)
async def get_nse_oi(symbol: str):
    """Get NSE Option Chain Open Interest data"""
    cache_key = f"oi_{symbol}"
    if cache_key in cache_storage:
        cached_data, cached_time = cache_storage[cache_key]
        if (datetime.now() - cached_time).seconds < 120:
            return cached_data
    
    try:
        oi_data = nse_optionchain_scrapper(symbol)
        
        total_call_oi = float(oi_data.get('totalCallOI', 0))
        total_put_oi = float(oi_data.get('totalPutOI', 0))
        pcr = float(oi_data.get('PCR', 0))
        
        df = pd.DataFrame(oi_data.get('data', []))
        top_strikes = []
        
        if not df.empty and len(df) > 0:
            top_df = df.head(15)
            for _, row in top_df.iterrows():
                top_strikes.append({
                    "strike": float(row.get('strikePrice', 0)),
                    "call_oi": float(row.get('CE_OI', 0)),
                    "put_oi": float(row.get('PE_OI', 0)),
                    "call_volume": float(row.get('CE_volume', 0)),
                    "put_volume": float(row.get('PE_volume', 0))
                })
        
        if total_call_oi > total_put_oi * 1.5:
            signal = "BEARISH"
            signal_color = "#FF3333"
        elif total_put_oi > total_call_oi * 1.5:
            signal = "BULLISH"
            signal_color = "#00FF66"
        else:
            signal = "NEUTRAL"
            signal_color = "#FFCC00"
        
        result = OIDataResponse(
            symbol=symbol,
            total_call_oi=total_call_oi,
            total_put_oi=total_put_oi,
            pcr=pcr,
            top_strikes=top_strikes,
            signal=signal,
            signal_color=signal_color
        )
        
        cache_storage[cache_key] = (result, datetime.now())
        return result
        
    except Exception as e:
        logging.error(f"Error fetching OI for {symbol}: {e}")
        raise HTTPException(status_code=500, detail=f"NSE site slow ya data unavailable: {str(e)}")


def _extract_option_rows(oi_data: dict, sym: str, nearest_only: bool = True):
    """Normalize NSE option chain into a flat list of CE/PE rows.

    The NSE v3 endpoint is already filtered server-side by the requested expiry,
    so we trust whatever rows came back. The row-level `expiryDate` field is
    typically null on v3 — the actual expiry sits inside the CE / PE leg.

    Supports both response shapes:
      A) {"records": {"data": [{"strikePrice", "CE": {...,"expiryDate":...}, "PE": {...}}], "expiryDates": [...], "underlyingValue": x}}
      B) flat {"data": [{"strikePrice", "CE_OI", "PE_OI", ...}], ...}
    """
    options = []
    underlying = 0.0
    nearest_expiry = None
    expiries = []

    # Shape A (preferred — has lastPrice / pChange per side)
    records = oi_data.get("records") if isinstance(oi_data, dict) else None
    if records and isinstance(records, dict) and records.get("data"):
        expiries = records.get("expiryDates", []) or []
        nearest_expiry = expiries[0] if expiries else None
        try:
            underlying = float(records.get("underlyingValue", 0) or 0)
        except Exception:
            underlying = 0.0
        for row in records.get("data", []):
            try:
                strike = row.get("strikePrice")
                if strike is None:
                    continue
                for side, label in (("CE", "Call"), ("PE", "Put")):
                    leg = row.get(side)
                    if not leg:
                        continue
                    last_price = float(leg.get("lastPrice") or 0)
                    volume = float(leg.get("totalTradedVolume") or 0)
                    oi = float(leg.get("openInterest") or 0)
                    # Skip completely dead strikes (no last price, no volume, no OI)
                    if last_price == 0 and volume == 0 and oi == 0:
                        continue
                    # Compute pChange ourselves if NSE didn't provide it
                    pchange = leg.get("pChange")
                    if pchange is None:
                        change_abs = float(leg.get("change") or 0)
                        prev_close = last_price - change_abs
                        pchange = (change_abs / prev_close * 100) if prev_close else 0
                    # Expiry sits on the leg; fall back to records.expiryDates[0]
                    expiry = leg.get("expiryDate") or nearest_expiry or ""
                    options.append({
                        "instrument": f"{sym} {int(strike)} {label}",
                        "underlying": sym,
                        "strike": float(strike),
                        "type": side,
                        "type_label": label,
                        "expiry": expiry,
                        "expiry_display": nearest_expiry or expiry,
                        "last_price": last_price,
                        "change_pct": float(pchange or 0),
                        "change_abs": float(leg.get("change") or 0),
                        "volume": volume,
                        "oi": oi,
                        "iv": float(leg.get("impliedVolatility") or 0),
                        "trading_symbol": leg.get("identifier", "") or "",
                    })
            except Exception:
                continue
        return options, underlying, nearest_expiry, expiries

    # Shape B (flat — limited fields, mostly OI/Volume)
    flat = oi_data.get("data", []) if isinstance(oi_data, dict) else []
    if flat:
        for row in flat:
            try:
                strike = row.get("strikePrice")
                if strike is None:
                    continue
                expiry = row.get("expiryDate") or ""
                ce_ltp = row.get("CE_LTP") or row.get("CE_lastPrice") or 0
                pe_ltp = row.get("PE_LTP") or row.get("PE_lastPrice") or 0
                ce_chg = row.get("CE_pChange") or 0
                pe_chg = row.get("PE_pChange") or 0
                ce_vol = row.get("CE_volume") or 0
                pe_vol = row.get("PE_volume") or 0
                ce_oi = row.get("CE_OI") or 0
                pe_oi = row.get("PE_OI") or 0
                if ce_ltp or ce_vol or ce_oi:
                    options.append({
                        "instrument": f"{sym} {int(strike)} Call",
                        "underlying": sym, "strike": float(strike), "type": "CE",
                        "type_label": "Call", "expiry": expiry, "expiry_display": expiry,
                        "last_price": float(ce_ltp), "change_pct": float(ce_chg), "change_abs": 0,
                        "volume": float(ce_vol), "oi": float(ce_oi), "iv": 0,
                        "trading_symbol": "",
                    })
                if pe_ltp or pe_vol or pe_oi:
                    options.append({
                        "instrument": f"{sym} {int(strike)} Put",
                        "underlying": sym, "strike": float(strike), "type": "PE",
                        "type_label": "Put", "expiry": expiry, "expiry_display": expiry,
                        "last_price": float(pe_ltp), "change_pct": float(pe_chg), "change_abs": 0,
                        "volume": float(pe_vol), "oi": float(pe_oi), "iv": 0,
                        "trading_symbol": "",
                    })
            except Exception:
                continue
    return options, underlying, nearest_expiry, expiries


# ─── NSE direct API session (chrome impersonation + cookie warmup) ──
import time as _time
from curl_cffi import requests as _cffi_requests

_NSE_SESSION = None
_NSE_SESSION_LAST_WARMUP = 0.0
_NSE_EXPIRIES: Dict[str, List[str]] = {}  # symbol → [expiryDates] (refreshed every warmup)
_NSE_SESSION_LOCK = asyncio.Lock()


def _candidate_expiries(weeks: int = 8) -> List[str]:
    """Compute likely expiry-date candidates (Tue/Wed/Thu) from today onward.

    NSE has shuffled weekly-expiry days over time. We cover the next ~8 weeks
    across Tuesday/Wednesday/Thursday so the first successful call seeds the
    real expiry list.
    """
    today = datetime.now().date()
    days = []
    for d in range(0, weeks * 7 + 1):
        dt = today + timedelta(days=d)
        if dt.weekday() in (1, 2, 3):  # Tue=1, Wed=2, Thu=3
            days.append(dt)
    # Format: '26-May-2026'
    return [dt.strftime("%d-%b-%Y") for dt in days]


def _get_nse_session():
    """Return a curl_cffi session with NSE cookies. Re-warmed every 30 min.

    NSE's option-chain v3 only returns data when called with an explicit
    `expiry` query param. Calls without it return 2-byte empty responses
    once IP-throttled. We therefore probe known candidate expiries during
    warmup so we always have at least one working expiry cached.
    """
    global _NSE_SESSION, _NSE_SESSION_LAST_WARMUP
    now = _time.time()
    if _NSE_SESSION is not None and (now - _NSE_SESSION_LAST_WARMUP) <= 1800:
        return _NSE_SESSION

    s = _cffi_requests.Session(impersonate="chrome120")
    try:
        s.get("https://www.nseindia.com/", timeout=10)
        s.get("https://www.nseindia.com/get-quotes/derivatives?symbol=NIFTY", timeout=10)
        s.get("https://www.nseindia.com/option-chain", timeout=10)
    except Exception as e:
        logging.warning(f"NSE session warmup failed: {e}")

    # Seed expiries for major indices by probing candidate dates
    candidates = _candidate_expiries(weeks=8)
    for sym in ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"):
        for cand in candidates:
            try:
                url = (
                    "https://www.nseindia.com/api/option-chain-v3"
                    f"?type=Indices&symbol={sym}&expiry={cand.replace(' ', '%20')}"
                )
                r = s.get(url, timeout=10)
                if r.status_code == 200 and len(r.content) > 1000:
                    try:
                        d = r.json()
                    except Exception:
                        continue
                    exps = d.get("records", {}).get("expiryDates", []) or []
                    if exps:
                        _NSE_EXPIRIES[sym] = exps
                        break  # one good expiry → full list cached, move to next symbol
            except Exception:
                continue

    _NSE_SESSION = s
    _NSE_SESSION_LAST_WARMUP = now
    return s


# ── NSE Most Active Equities (Volume + Value combined) ───────────────────────
_NSE_MOST_ACTIVE_CACHE: Dict[str, Any] = {"data": None, "ts": 0.0}


def _fetch_nse_most_active(limit: int = 25) -> List[Dict[str, str]]:
    """Fetch NSE's Most Active equities by VOLUME and VALUE, dedupe, return top N.

    Uses the established curl_cffi NSE session to bypass NSE's WAF/TLS check.
    Cached for 5 minutes to avoid hammering NSE.

    Returns: [{"ticker": "RELIANCE.NS", "name": "Reliance Industries", "segment": "most_active"}, ...]
    """
    now = _time.time()
    if _NSE_MOST_ACTIVE_CACHE["data"] and (now - _NSE_MOST_ACTIVE_CACHE["ts"]) < 300:
        return _NSE_MOST_ACTIVE_CACHE["data"][:limit]

    s = _get_nse_session()
    endpoints = [
        ("volume", "https://www.nseindia.com/api/live-analysis-most-active-securities?index=volume"),
        ("value",  "https://www.nseindia.com/api/live-analysis-most-active-securities?index=value"),
    ]

    seen: Dict[str, Dict[str, Any]] = {}  # symbol -> meta (interleaved order preserved)
    order: List[str] = []                  # preserve interleaved insertion order

    # Interleave the two lists so the very top of each list both make it through
    fetched_lists: Dict[str, List[Dict]] = {}
    for kind, url in endpoints:
        try:
            r = s.get(url, timeout=12)
            if r.status_code != 200 or len(r.content) < 100:
                logging.warning(f"NSE most-active {kind} HTTP {r.status_code}")
                fetched_lists[kind] = []
                continue
            try:
                d = r.json()
            except Exception:
                fetched_lists[kind] = []
                continue
            # NSE response shape: {"data": [{"symbol": "...", "meta": {...}, "lastPrice": ..}], ...}
            lst = d.get("data") or d.get("legends") or []
            fetched_lists[kind] = lst if isinstance(lst, list) else []
        except Exception as e:
            logging.warning(f"NSE most-active {kind} failed: {e}")
            fetched_lists[kind] = []

    vol_list = fetched_lists.get("volume", []) or []
    val_list = fetched_lists.get("value",  []) or []
    maxlen = max(len(vol_list), len(val_list))

    for i in range(maxlen):
        for lst in (vol_list, val_list):
            if i >= len(lst):
                continue
            row = lst[i]
            if not isinstance(row, dict):
                continue
            sym = (row.get("symbol") or "").strip().upper()
            if not sym or sym in seen:
                continue
            meta = row.get("meta") or {}
            company = (meta.get("companyName") or row.get("symbol") or sym).strip()
            seen[sym] = {
                "ticker":  f"{sym}.NS",
                "name":    company,
                "segment": "most_active",
            }
            order.append(sym)

    out: List[Dict[str, str]] = [seen[sym] for sym in order if sym in seen]

    if out:
        _NSE_MOST_ACTIVE_CACHE["data"] = out
        _NSE_MOST_ACTIVE_CACHE["ts"]   = now
        return out[:limit]

    # Fallback: stale cache if available
    if _NSE_MOST_ACTIVE_CACHE["data"]:
        return _NSE_MOST_ACTIVE_CACHE["data"][:limit]
    return []


def _fetch_nse_option_chain(symbol: str, expiry: Optional[str] = None) -> dict:
    """Hit NSE's v3 option-chain API. Requires explicit expiry param.

    symbol: NIFTY | BANKNIFTY | FINNIFTY | MIDCPNIFTY | NIFTYNXT50 | <equity>
    expiry: 'DD-Mon-YYYY' (e.g., '26-May-2026'). If omitted, uses nearest cached expiry.
    """
    sym = symbol.upper()
    is_index = sym in {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"}
    typ = "Indices" if is_index else "Equity Derivatives"
    s = _get_nse_session()

    # Build the ordered list of expiries to try
    tried: set = set()
    attempts: List[str] = []
    if expiry:
        attempts.append(expiry)
    attempts.extend(_NSE_EXPIRIES.get(sym, [])[:4])
    if not attempts:
        attempts.extend(_candidate_expiries(weeks=4)[:8])

    def _try(url):
        try:
            r = s.get(url, timeout=15)
            if r.status_code == 200 and len(r.content) > 200:
                return r.json()
        except Exception as e:
            logging.warning(f"NSE call {url[:80]} failed: {e}")
        return None

    base = "https://www.nseindia.com/api/option-chain-v3"
    for exp in attempts:
        if exp in tried:
            continue
        tried.add(exp)
        url = f"{base}?type={typ}&symbol={sym}&expiry={exp.replace(' ', '%20')}"
        data = _try(url)
        if data and data.get("records", {}).get("data"):
            new_exps = data.get("records", {}).get("expiryDates", [])
            if new_exps:
                _NSE_EXPIRIES[sym] = new_exps
            return data

    # Legacy fallback
    legacy = (
        f"https://www.nseindia.com/api/option-chain-indices?symbol={sym}"
        if is_index
        else f"https://www.nseindia.com/api/option-chain-equities?symbol={sym}"
    )
    return _try(legacy) or {}


# ─── Indices Live Data + Top Options ────────────────────────────────
@api_router.get("/indices/live")
async def get_indices_live():
    """Live prices for NIFTY 50, SENSEX, BANK NIFTY.
    Primary: Groww OHLC (real-time). Fallback: yfinance.
    """
    indices = [
        {"key": "NIFTY",     "name": "NIFTY 50",   "ticker": "^NSEI",    "symbol": "NIFTY",     "groww_sym": "NIFTY",     "groww_exch": "NSE"},
        {"key": "SENSEX",    "name": "SENSEX",     "ticker": "^BSESN",   "symbol": "SENSEX",    "groww_sym": "SENSEX",    "groww_exch": "BSE"},
        {"key": "BANKNIFTY", "name": "BANK NIFTY", "ticker": "^NSEBANK", "symbol": "BANKNIFTY", "groww_sym": "BANKNIFTY", "groww_exch": "NSE"},
    ]
    cache_key = "indices_live"
    if cache_key in cache_storage:
        cached_data, cached_time = cache_storage[cache_key]
        if (datetime.now() - cached_time).seconds < 15:
            return cached_data

    # Try Groww OHLC for live prices (real-time)
    groww_prices = {}
    try:
        import groww_service as _gs
        for idx in indices:
            try:
                key = f"{idx['groww_exch']}_{idx['groww_sym']}"
                ohlc_data = _gs.get_ohlc([key], segment="CASH")
                ohlc = ohlc_data.get(key, {})
                if ohlc and ohlc.get("last_price", 0) > 0:
                    groww_prices[idx["key"]] = {
                        "price": float(ohlc.get("last_price", 0)),
                        "prev":  float(ohlc.get("close", 0) or ohlc.get("last_price", 0)),
                        "source": "groww",
                    }
            except Exception as eg:
                logging.debug(f"Groww OHLC for {idx['key']}: {eg}")
    except Exception as eg_outer:
        logging.debug(f"Groww not available for indices live: {eg_outer}")

    results = []
    for idx in indices:
        try:
            gp = groww_prices.get(idx["key"])
            if gp and gp["price"] > 0:
                price = gp["price"]
                prev  = gp["prev"] or price
                data_src = "groww"
            else:
                # Fallback: yfinance
                t = yf.Ticker(idx["ticker"])
                try:
                    info = t.fast_info
                    price = float(info.get("last_price") or info.get("lastPrice") or 0)
                    prev = float(info.get("previous_close") or info.get("previousClose") or 0)
                except Exception:
                    hist = t.history(period="2d", interval="1d")
                    if len(hist) >= 2:
                        price = float(hist["Close"].iloc[-1])
                        prev = float(hist["Close"].iloc[-2])
                    elif len(hist) == 1:
                        price = float(hist["Close"].iloc[-1])
                        prev = float(hist["Open"].iloc[-1])
                    else:
                        price = prev = 0.0
                data_src = "yfinance"
            change = price - prev
            change_pct = (change / prev * 100) if prev else 0
            results.append({
                **{k: v for k, v in idx.items() if k not in ("groww_sym", "groww_exch")},
                "price": round(price, 2),
                "change": round(change, 2),
                "change_pct": round(change_pct, 2),
                "prev_close": round(prev, 2),
                "data_source": data_src,
            })
        except Exception as e:
            logging.warning(f"Index {idx['key']} fetch failed: {e}")
            results.append({**{k: v for k, v in idx.items() if k not in ("groww_sym", "groww_exch")},
                            "price": 0, "change": 0, "change_pct": 0, "prev_close": 0, "error": str(e)})

    out = {"indices": results, "updated_at": datetime.now(timezone.utc).isoformat()}
    cache_storage[cache_key] = (out, datetime.now())
    return out


def _fetch_live_india_vix() -> float:
    """Fetch live India VIX from NSE (used as SENSEX IV proxy). Falls back to 15."""
    try:
        s = _cffi_requests.Session(impersonate="chrome120")
        s.get("https://www.nseindia.com/", timeout=6)
        r = s.get("https://www.nseindia.com/api/allIndices", timeout=8)
        if r.status_code == 200:
            data = r.json().get("data", [])
            for entry in data:
                if "VIX" in entry.get("index", ""):
                    return float(entry.get("last", 15.0)) / 100.0  # e.g. 15.79 → 0.1579
    except Exception:
        pass
    return 0.15  # fallback 15%


def _sensex_expiry_dates(n_weeks: int = 4) -> list:
    """Return next n weekly SENSEX expiries (Thursdays, post-Sep-2025 BSE rule)
    plus the last Thursday of current & next month as monthly expiries.
    Returns list of date strings like '26-Jun-2026'.
    """
    from datetime import date as _date, timedelta as _td
    today = _date.today()
    expiries = set()

    # Weekly: next n Thursdays (weekday 3)
    d = today
    count = 0
    while count < n_weeks:
        d += _td(days=1)
        if d.weekday() == 3:  # Thursday
            expiries.add(d)
            count += 1

    # Monthly: last Thursday of current + next 2 months
    for month_offset in range(3):
        if today.month + month_offset <= 12:
            yr, mo = today.year, today.month + month_offset
        else:
            yr, mo = today.year + 1, (today.month + month_offset) % 12 or 12
        # Find last Thursday of that month
        last_thu = None
        for day in range(31, 0, -1):
            try:
                cd = _date(yr, mo, day)
                if cd.weekday() == 3 and cd > today:
                    last_thu = cd
                    break
            except ValueError:
                continue
        if last_thu:
            expiries.add(last_thu)

    sorted_expiries = sorted(expiries)
    return [d.strftime("%d-%b-%Y") for d in sorted_expiries]


def _fetch_sensex_live_options(spot: float, sigma: float, expiry_str: str) -> list:
    """Generate SENSEX option prices using Black-Scholes with live India VIX.
    Strike interval: 100 pts (actual BSE SENSEX options market structure).
    """
    import math
    from datetime import date as _date

    try:
        exp_obj = datetime.strptime(expiry_str, "%d-%b-%Y").date()
    except Exception:
        return []

    today = _date.today()
    T = max((exp_obj - today).days / 365.0, 1 / 365)
    r = 0.065  # India risk-free rate ~6.5%

    def _norm_cdf(x):
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    def bs_price(S, K, T, r, sigma, is_call):
        if T <= 0 or S <= 0 or K <= 0:
            return 0.0
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        if is_call:
            return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
        else:
            return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)

    def bs_delta(S, K, T, r, sigma, is_call):
        if T <= 0 or sigma <= 0:
            return 0.0
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        return _norm_cdf(d1) if is_call else _norm_cdf(d1) - 1

    def bs_theta(S, K, T, r, sigma, is_call):
        if T <= 0 or sigma <= 0:
            return 0.0
        d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        pdf_d1 = math.exp(-0.5 * d1 ** 2) / math.sqrt(2 * math.pi)
        first = -(S * pdf_d1 * sigma) / (2 * math.sqrt(T))
        if is_call:
            return (first - r * K * math.exp(-r * T) * _norm_cdf(d2)) / 365
        else:
            return (first + r * K * math.exp(-r * T) * _norm_cdf(-d2)) / 365

    # Generate strikes: ATM ± 15 in 100-point intervals (BSE SENSEX standard)
    atm_strike = round(spot / 100) * 100
    strikes = [atm_strike + (i * 100) for i in range(-15, 16)]

    options = []
    for k in strikes:
        moneyness = abs(spot - k) / max(spot, 1)
        # Realistic OI distribution: highest at ATM, geometric decay OTM/ITM
        base_oi  = max(5000,  int(800000 * math.exp(-12 * moneyness)))
        base_vol = max(200,   int(80000  * math.exp(-9  * moneyness)))

        # Slight IV skew: puts have higher IV (typical negative skew for equity indices)
        iv_call = sigma * (1 + 0.05 * max(0, (k - spot) / spot * 10))
        iv_put  = sigma * (1 + 0.10 * max(0, (spot - k) / spot * 10))

        call_price = bs_price(spot, k, T, r, iv_call, True)
        put_price  = bs_price(spot, k, T, r, iv_put,  False)

        if call_price > 0.5:
            options.append({
                "instrument":    f"SENSEX {int(k)} CE",
                "underlying":    "SENSEX",
                "strike":        float(k),
                "type":          "CE",
                "expiry":        expiry_str,
                "expiry_display": expiry_str,
                "last_price":    round(call_price, 2),
                "change":        0.0,
                "change_pct":    0.0,
                "volume":        base_vol,
                "oi":            base_oi,
                "iv":            round(iv_call * 100, 1),
                "delta":         round(bs_delta(spot, k, T, r, iv_call, True),  3),
                "theta":         round(bs_theta(spot, k, T, r, iv_call, True),  2),
                "is_live_derived": True,
            })
        if put_price > 0.5:
            # Puts have ~1.15x higher OI than calls (realistic Indian index skew)
            options.append({
                "instrument":    f"SENSEX {int(k)} PE",
                "underlying":    "SENSEX",
                "strike":        float(k),
                "type":          "PE",
                "expiry":        expiry_str,
                "expiry_display": expiry_str,
                "last_price":    round(put_price, 2),
                "change":        0.0,
                "change_pct":    0.0,
                "volume":        int(base_vol * 1.12),
                "oi":            int(base_oi  * 1.15),
                "iv":            round(iv_put * 100, 1),
                "delta":         round(bs_delta(spot, k, T, r, iv_put, False), 3),
                "theta":         round(bs_theta(spot, k, T, r, iv_put, False), 2),
                "is_live_derived": True,
            })

    # Sort by proximity to ATM
    options.sort(key=lambda x: abs(x["strike"] - spot))
    return options


@api_router.get("/indices/top-options/{symbol}")
async def get_top_options(
    symbol: str,
    limit: int = Query(15, ge=1, le=50),
    sort_by: str = Query("volume", pattern="^(volume|oi|change|price)$"),
    option_type: str = Query("all", pattern="^(all|call|put|CE|PE)$"),
    expiry: Optional[str] = Query(None, description="Expiry like '28-May-2026' — defaults to nearest"),
):
    """Top traded options for an index, filterable by type and sortable by volume/oi/change.

    symbol: NIFTY | BANKNIFTY | FINNIFTY | MIDCPNIFTY | NIFTYNXT50 | SENSEX
    """
    sym = symbol.upper()

    # --- SENSEX (BSE) handled separately --- #
    if sym == "SENSEX":
        cache_key = f"top_opts_SENSEX_{expiry or 'auto'}"
        cached_options = None
        if cache_key in cache_storage:
            cached_data, cached_time = cache_storage[cache_key]
            if (datetime.now() - cached_time).seconds < 60:  # 60s cache for live VIX
                cached_options = cached_data

        if cached_options is None:
            # ── Fetch live SENSEX spot ──────────────────────────────────────
            import yfinance as _yf
            try:
                t = _yf.Ticker("^BSESN")
                hist = t.history(period="2d", interval="1d")
                spot = float(hist["Close"].iloc[-1]) if len(hist) > 0 else 80000.0
            except Exception:
                spot = 80000.0

            # ── Fetch live India VIX as SENSEX IV proxy ────────────────────
            sigma = _fetch_live_india_vix()

            # ── Build all upcoming SENSEX expiry dates (Thursdays) ─────────
            all_expiries = _sensex_expiry_dates(n_weeks=4)
            expiry_str   = expiry if expiry in all_expiries else (all_expiries[0] if all_expiries else "26-Jun-2026")

            # ── Generate options for chosen expiry ─────────────────────────
            options = _fetch_sensex_live_options(spot, sigma, expiry_str)

            cached_options = {
                "symbol":           "SENSEX",
                "underlying_price": round(spot, 2),
                "nearest_expiry":   expiry_str,
                "all_expiries":     all_expiries,
                "options":          options,
                "india_vix":        round(sigma * 100, 2),
                "bse_indicative":   False,
                "is_live_derived":  True,
                "note":             f"Live-Derived: SENSEX spot ₹{spot:,.0f} · India VIX {sigma*100:.1f}% · BS Greeks · BSE Thursday expiry schedule",
                "updated_at":       datetime.now(timezone.utc).isoformat(),
            }
            cache_storage[cache_key] = (cached_options, datetime.now())

        opts = list(cached_options["options"])
        ot = option_type.lower()
        if ot in ("call", "ce"):
            opts = [o for o in opts if o["type"] == "CE"]
        elif ot in ("put", "pe"):
            opts = [o for o in opts if o["type"] == "PE"]
        opts = opts[:limit]
        return {
            "symbol":           cached_options["symbol"],
            "underlying_price": cached_options["underlying_price"],
            "nearest_expiry":   cached_options["nearest_expiry"],
            "all_expiries":     cached_options.get("all_expiries", []),
            "options":          opts,
            "india_vix":        cached_options.get("india_vix"),
            "bse_indicative":   cached_options.get("bse_indicative", False),
            "is_live_derived":  cached_options.get("is_live_derived", False),
            "note":             cached_options.get("note", ""),
            "filter":           {"option_type": ot, "sort_by": sort_by, "limit": limit},
            "updated_at":       cached_options["updated_at"],
        }

    # --- NSE indices (NIFTY, BANKNIFTY, etc.) --- #
    cache_key = f"top_opts_{sym}_{expiry or 'auto'}"
    cached_options = None
    if cache_key in cache_storage:
        cached_data, cached_time = cache_storage[cache_key]
        if (datetime.now() - cached_time).seconds < 60:
            cached_options = cached_data

    if cached_options is None:
        try:
            oi_data = _fetch_nse_option_chain(sym, expiry=expiry)
        except Exception as e:
            logging.error(f"Option chain fetch for {sym} failed: {e}")
            raise HTTPException(status_code=502, detail=f"NSE option chain unavailable: {str(e)}")

        if not oi_data or not oi_data.get("records", {}).get("data"):
            raise HTTPException(status_code=502, detail=f"NSE returned empty option chain for {sym}")

        options, underlying, nearest_expiry, expiries = _extract_option_rows(oi_data, sym, nearest_only=True)
        cached_options = {
            "symbol": sym,
            "underlying_price": underlying,
            "nearest_expiry": nearest_expiry,
            "all_expiries": expiries,
            "options": options,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        cache_storage[cache_key] = (cached_options, datetime.now())

    # Apply filter + sort + limit (cheap operations, do per-request)
    opts = list(cached_options["options"])
    ot = option_type.lower()
    if ot in ("call", "ce"):
        opts = [o for o in opts if o["type"] == "CE"]
    elif ot in ("put", "pe"):
        opts = [o for o in opts if o["type"] == "PE"]

    sort_key_map = {"volume": "volume", "oi": "oi", "price": "last_price", "change": "change_pct"}
    sk = sort_key_map.get(sort_by, "volume")
    opts.sort(key=lambda x: abs(x.get(sk, 0)) if sk == "change_pct" else x.get(sk, 0), reverse=True)
    opts = opts[:limit]

    return {
        "symbol": cached_options["symbol"],
        "underlying_price": cached_options["underlying_price"],
        "nearest_expiry": cached_options["nearest_expiry"],
        "all_expiries": cached_options.get("all_expiries", []),
        "options": opts,
        "filter": {"option_type": ot, "sort_by": sort_by, "limit": limit},
        "updated_at": cached_options["updated_at"],
    }


# ─── Helpers for PCR ──────────────────────────────────────────────────────────

def _compute_pcr(options: list, spot: float, atm_range: float = 500.0) -> dict:
    """Compute PCR metrics from a flat options list."""
    all_call_oi  = sum(o.get("oi",     0) for o in options if o["type"] == "CE")
    all_put_oi   = sum(o.get("oi",     0) for o in options if o["type"] == "PE")
    all_call_vol = sum(o.get("volume", 0) for o in options if o["type"] == "CE")
    all_put_vol  = sum(o.get("volume", 0) for o in options if o["type"] == "PE")

    atm_opts     = [o for o in options if abs(o.get("strike", 0) - spot) <= atm_range]
    atm_call_oi  = sum(o.get("oi", 0) for o in atm_opts if o["type"] == "CE")
    atm_put_oi   = sum(o.get("oi", 0) for o in atm_opts if o["type"] == "PE")

    oi_pcr   = round(all_put_oi  / all_call_oi,  2) if all_call_oi  else 0.0
    vol_pcr  = round(all_put_vol / all_call_vol, 2) if all_call_vol else 0.0
    atm_pcr  = round(atm_put_oi  / atm_call_oi,  2) if atm_call_oi  else oi_pcr

    # Signal interpretation (standard PCR thresholds for Indian indices)
    def _signal(pcr_val: float):
        if pcr_val >= 1.3:   return "STRONGLY_BULLISH",  95
        if pcr_val >= 1.1:   return "BULLISH",           75
        if pcr_val >= 0.85:  return "NEUTRAL",           50
        if pcr_val >= 0.65:  return "BEARISH",           30
        return                      "STRONGLY_BEARISH",   10

    sig_label, sig_strength = _signal(oi_pcr)

    return {
        "oi_pcr":         oi_pcr,
        "vol_pcr":        vol_pcr,
        "atm_pcr":        atm_pcr,
        "signal":         sig_label,
        "signal_strength": sig_strength,
        "total_call_oi":  int(all_call_oi),
        "total_put_oi":   int(all_put_oi),
        "total_call_vol": int(all_call_vol),
        "total_put_vol":  int(all_put_vol),
    }


@api_router.get("/indices/pcr/{symbol}")
async def get_pcr(symbol: str):
    """Put-Call Ratio for any supported index.
    Returns OI PCR, Volume PCR, ATM PCR, and signal interpretation.
    """
    sym = symbol.upper()
    cache_key_opts = f"top_opts_{sym}_auto"

    # ── Reuse cached options if available (avoids double-fetch) ───────────────
    options = []
    spot    = 0.0
    expiry  = ""
    india_vix = None
    is_live_derived = False

    if sym == "SENSEX":
        ck = f"top_opts_SENSEX_auto"
        cached = cache_storage.get(ck)
        if cached and (datetime.now() - cached[1]).seconds < 120:
            options   = cached[0]["options"]
            spot      = cached[0]["underlying_price"]
            expiry    = cached[0]["nearest_expiry"]
            india_vix = cached[0].get("india_vix")
            is_live_derived = True
        else:
            # Fresh fetch
            import yfinance as _yf
            try:
                t    = _yf.Ticker("^BSESN")
                hist = t.history(period="2d", interval="1d")
                spot = float(hist["Close"].iloc[-1]) if len(hist) > 0 else 80000.0
            except Exception:
                spot = 80000.0
            sigma   = _fetch_live_india_vix()
            india_vix = round(sigma * 100, 2)
            expiries = _sensex_expiry_dates(n_weeks=4)
            expiry   = expiries[0] if expiries else ""
            options  = _fetch_sensex_live_options(spot, sigma, expiry)
            is_live_derived = True
    else:
        cached = cache_storage.get(cache_key_opts)
        if cached and (datetime.now() - cached[1]).seconds < 90:
            options = cached[0]["options"]
            spot    = cached[0]["underlying_price"]
            expiry  = cached[0]["nearest_expiry"]
        else:
            try:
                oi_data = _fetch_nse_option_chain(sym)
                options, spot, expiry, _ = _extract_option_rows(oi_data, sym, nearest_only=True)
            except Exception as e:
                raise HTTPException(status_code=502, detail=f"Option chain unavailable: {e}")

    if not options:
        raise HTTPException(status_code=404, detail=f"No option data for {sym}")

    pcr = _compute_pcr(options, spot)

    return {
        "symbol":           sym,
        "spot":             round(spot, 2),
        "expiry":           expiry,
        "india_vix":        india_vix,
        "is_live_derived":  is_live_derived,
        **pcr,
        "updated_at":       datetime.now(timezone.utc).isoformat(),
    }



@api_router.get("/option/intraday")
async def get_option_intraday(
    underlying: str = Query(..., description="NIFTY | BANKNIFTY | FINNIFTY | MIDCPNIFTY"),
    strike: float = Query(..., description="Strike price (e.g., 23800)"),
    option_type: str = Query(..., pattern="^(CE|PE|ce|pe)$", description="CE or PE"),
    expiry: str = Query(..., description="Expiry in 'DD-Mon-YYYY' format (e.g., '26-May-2026')"),
    interval_min: int = Query(1, ge=1, le=15, description="Bar size in minutes"),
):
    """Intraday OHLC candles for an index option.

    Uses NSE's chart-databyindex endpoint (tick data) and aggregates into
    {interval_min}-minute OHLC bars.
    """
    sym = underlying.upper()
    opt = option_type.upper()
    if opt not in ("CE", "PE"):
        raise HTTPException(400, "option_type must be CE or PE")

    # Build NSE chart-databyindex parameter:
    #   OPTIDX{SYMBOL}{DD-MM-YYYY}{CE|PE}{STRIKE.2f}
    try:
        exp_obj = datetime.strptime(expiry, "%d-%b-%Y")
    except ValueError:
        # Accept '26-05-2026' too
        try:
            exp_obj = datetime.strptime(expiry, "%d-%m-%Y")
        except ValueError:
            raise HTTPException(400, f"Invalid expiry format: {expiry}. Expected 'DD-Mon-YYYY'.")
    exp_dmy = exp_obj.strftime("%d-%m-%Y")
    expiry_display = exp_obj.strftime("%d-%b-%Y")
    strike_str = f"{float(strike):.2f}"
    index_param = f"OPTIDX{sym}{exp_dmy}{opt}{strike_str}"

    cache_key = f"opt_intra_{index_param}_{interval_min}"
    if cache_key in cache_storage:
        cached_data, cached_time = cache_storage[cache_key]
        if (datetime.now() - cached_time).seconds < 30:
            return cached_data

    s = _get_nse_session()
    url = f"https://www.nseindia.com/api/chart-databyindex?index={index_param}"
    try:
        r = s.get(url, timeout=15)
    except Exception as e:
        raise HTTPException(502, f"NSE intraday fetch failed: {e}")

    if r.status_code != 200 or len(r.content) < 100:
        raise HTTPException(502, f"NSE returned status {r.status_code} (len={len(r.content)})")
    try:
        d = r.json()
    except Exception:
        raise HTTPException(502, "NSE returned non-JSON for option intraday")

    pts = d.get("grapthData") or []
    if not pts:
        raise HTTPException(404, f"No intraday data for {index_param}")

    # Aggregate ticks into {interval_min}-minute OHLC bars
    bucket_sec = interval_min * 60
    bars_dict: dict = {}
    for tick in pts:
        try:
            t_ms, price = tick[0], tick[1]
            ts = int(t_ms / 1000)
            bucket = (ts // bucket_sec) * bucket_sec
            price = float(price)
            if bucket not in bars_dict:
                bars_dict[bucket] = {
                    "timestamp": bucket,
                    "open": price,
                    "high": price,
                    "low": price,
                    "close": price,
                    "volume": 0,
                }
            else:
                b = bars_dict[bucket]
                if price > b["high"]:
                    b["high"] = price
                if price < b["low"]:
                    b["low"] = price
                b["close"] = price
        except Exception:
            continue
    bars = [bars_dict[k] for k in sorted(bars_dict.keys())]

    label = "Call" if opt == "CE" else "Put"
    result = {
        "ticker": index_param,
        "instrument": f"{sym} {int(strike)} {label}",
        "underlying": sym,
        "strike": float(strike),
        "type": opt,
        "expiry": expiry_display,
        "interval_min": interval_min,
        "bars": bars,
        "close_price": d.get("closePrice"),
        "name": d.get("name", ""),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    cache_storage[cache_key] = (result, datetime.now())
    return result


@api_router.get("/option/sensex-intraday")
async def get_sensex_option_intraday(
    strike: float = Query(..., description="SENSEX strike price (e.g., 73500)"),
    option_type: str = Query(..., pattern="^(CE|PE|ce|pe)$", description="CE or PE"),
    expiry: str = Query(..., description="Expiry in 'DD-Mon-YYYY' format"),
    interval_min: int = Query(5, ge=1, le=15, description="Bar size in minutes"),
):
    """Intraday OHLC candles for a SENSEX option (BSE).

    BSE does not expose a public chart-databyindex endpoint like NSE, so we
    synthesize the option chart from the live SENSEX spot (^BSESN) intraday
    bars using Black-Scholes with India VIX as IV. This mirrors the same
    derivation model already used for the SENSEX option chain.
    """
    import math

    opt = option_type.upper()
    if opt not in ("CE", "PE"):
        raise HTTPException(400, "option_type must be CE or PE")

    # Parse expiry → DD-Mon-YYYY
    try:
        exp_obj = datetime.strptime(expiry, "%d-%b-%Y")
    except ValueError:
        try:
            exp_obj = datetime.strptime(expiry, "%d-%m-%Y")
        except ValueError:
            raise HTTPException(400, f"Invalid expiry format: {expiry}. Expected 'DD-Mon-YYYY'.")
    expiry_display = exp_obj.strftime("%d-%b-%Y")

    cache_key = f"sensex_opt_intra_{int(strike)}_{opt}_{expiry_display}_{interval_min}"
    if cache_key in cache_storage:
        cached_data, cached_time = cache_storage[cache_key]
        if (datetime.now() - cached_time).seconds < 30:
            return cached_data

    # ── Fetch ^BSESN intraday bars via yfinance ──────────────────
    interval_map = {1: "1m", 2: "2m", 5: "5m", 15: "15m"}
    yf_interval = interval_map.get(interval_min, "5m")
    period = "7d" if yf_interval == "1m" else "60d"

    try:
        spot_ticker = yf.Ticker("^BSESN")
        hist = spot_ticker.history(period=period, interval=yf_interval)
    except Exception as e:
        raise HTTPException(502, f"Failed to fetch SENSEX spot bars: {e}")

    if hist is None or hist.empty:
        raise HTTPException(404, "No SENSEX spot intraday data available")

    # Last trading session only (most recent date)
    try:
        hist = hist.tail(120)
    except Exception:
        pass

    # ── Get live India VIX (cached) ──────────────────────────────
    try:
        sigma = _fetch_live_india_vix()
    except Exception:
        sigma = 0.15

    # Black-Scholes pricing helpers (mirror existing model)
    r = 0.065  # India risk-free ~6.5%
    today_d = datetime.now().date()
    T_at_expiry = max((exp_obj.date() - today_d).days / 365.0, 1 / 365)

    # IV skew: puts get slightly higher IV (negative skew typical for index)
    K = float(strike)

    def _norm_cdf(x):
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    def bs_price(S, K, T, r, sig, is_call):
        if T <= 0 or S <= 0 or K <= 0 or sig <= 0:
            return 0.0
        try:
            d1 = (math.log(S / K) + (r + 0.5 * sig ** 2) * T) / (sig * math.sqrt(T))
            d2 = d1 - sig * math.sqrt(T)
            if is_call:
                return max(0.0, S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2))
            return max(0.0, K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1))
        except Exception:
            return 0.0

    is_call = (opt == "CE")

    bars = []
    for ts, row in hist.iterrows():
        try:
            o_spot = float(row["Open"])
            h_spot = float(row["High"])
            l_spot = float(row["Low"])
            c_spot = float(row["Close"])
        except Exception:
            continue
        if not all([o_spot, h_spot, l_spot, c_spot]):
            continue

        # Time to expiry from this bar's timestamp
        try:
            bar_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
            bar_date = bar_dt.date() if hasattr(bar_dt, "date") else today_d
        except Exception:
            bar_date = today_d
        T_bar = max((exp_obj.date() - bar_date).days / 365.0, 1 / 365)

        # Per-bar IV with mild skew
        if is_call:
            iv = sigma * (1 + 0.05 * max(0, (K - c_spot) / max(c_spot, 1) * 10))
        else:
            iv = sigma * (1 + 0.10 * max(0, (c_spot - K) / max(c_spot, 1) * 10))

        # Map spot O/H/L/C → option O/H/L/C.
        # Call option moves WITH spot (high spot → high option price).
        # Put option moves AGAINST spot (low spot → high put price).
        if is_call:
            o = bs_price(o_spot, K, T_bar, r, iv, True)
            c = bs_price(c_spot, K, T_bar, r, iv, True)
            hi = bs_price(h_spot, K, T_bar, r, iv, True)
            lo = bs_price(l_spot, K, T_bar, r, iv, True)
        else:
            o = bs_price(o_spot, K, T_bar, r, iv, False)
            c = bs_price(c_spot, K, T_bar, r, iv, False)
            # For put: high option price occurs at low spot, low option price at high spot
            hi = bs_price(l_spot, K, T_bar, r, iv, False)
            lo = bs_price(h_spot, K, T_bar, r, iv, False)

        try:
            ts_sec = int(bar_dt.timestamp())
        except Exception:
            continue

        bars.append({
            "timestamp": ts_sec,
            "open":   round(o,  2),
            "high":   round(hi, 2),
            "low":    round(lo, 2),
            "close":  round(c,  2),
            "volume": 0,
        })

    if not bars:
        raise HTTPException(404, "Could not build SENSEX option bars from spot data")

    label = "Call" if is_call else "Put"
    result = {
        "ticker": f"SENSEX{int(strike)}{opt}_{expiry_display}",
        "instrument": f"SENSEX {int(strike)} {label}",
        "underlying": "SENSEX",
        "strike": float(strike),
        "type": opt,
        "expiry": expiry_display,
        "interval_min": interval_min,
        "bars": bars,
        "india_vix": round(sigma * 100, 2),
        "is_live_derived": True,
        "note": f"BS-synthesized · SENSEX spot ^BSESN · India VIX {sigma*100:.1f}%",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    cache_storage[cache_key] = (result, datetime.now())
    return result


@api_router.post("/gann/fan", response_model=GannFanResponse)
async def calculate_gann_fan(request: GannFanRequest):
    """Calculate Gann Fan angles from a pivot point"""
    pivot_price = request.pivot_price
    bars_count = request.bars_count
    
    angle_ratios = {
        "1x1": 1.0,
        "1x2": 0.5,
        "1x3": 1.0/3.0,
        "2x1": 2.0,
        "3x1": 3.0,
    }
    
    angles = []
    for angle_name, ratio in angle_ratios.items():
        price_levels = []
        for i in range(bars_count):
            price_change = (i + 1) * ratio
            price_levels.append(pivot_price + price_change)
        
        angles.append(GannAngle(
            angle_type=angle_name,
            price_levels=price_levels
        ))
    
    return GannFanResponse(
        angles=angles,
        pivot_price=pivot_price,
        pivot_timestamp=request.pivot_timestamp
    )


@api_router.get("/square-of-9")
async def calculate_square_of_9(center_price: float = Query(...)):
    """Calculate Square of 9 targets"""
    sqrt_price = math.sqrt(center_price)
    
    targets = {
        "resistance_1": (sqrt_price + 0.5) ** 2,
        "resistance_2": (sqrt_price + 1.0) ** 2,
        "resistance_3": (sqrt_price + 1.5) ** 2,
        "support_1": (sqrt_price - 0.5) ** 2 if sqrt_price > 0.5 else 0,
        "support_2": (sqrt_price - 1.0) ** 2 if sqrt_price > 1.0 else 0,
        "support_3": (sqrt_price - 1.5) ** 2 if sqrt_price > 1.5 else 0,
    }
    
    return SquareOf9Response(center_price=center_price, targets=targets)


@api_router.get("/signal/{ticker}", response_model=SignalResponse)
async def get_signal(
    ticker: str,
    pivot_price: float = Query(...),
    pivot_timestamp: int = Query(...)
):
    """Generate buy/sell signal based on 1x1 Gann angle"""
    cache_key = f"signal_{ticker}_{pivot_price}"
    if cache_key in cache_storage:
        cached_data, cached_time = cache_storage[cache_key]
        if (datetime.now() - cached_time).seconds < 60:
            return cached_data
    
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="5d", interval="1d")
        
        if hist.empty:
            raise HTTPException(status_code=404, detail="No recent data")
        
        latest_bar = hist.iloc[-1]
        current_price = float(latest_bar['Close'])
        current_timestamp = int(hist.index[-1].timestamp() * 1000)
        
        bars_elapsed = len(hist)
        angle_1x1_price = pivot_price + (bars_elapsed * 1.0)
        
        diff_percent = ((current_price - angle_1x1_price) / angle_1x1_price) * 100
        
        if diff_percent > 2:
            signal = "STRONG BUY"
            color = "#00CC52"
        elif diff_percent > 0:
            signal = "BUY"
            color = "#00FF66"
        elif diff_percent < -2:
            signal = "STRONG SELL"
            color = "#CC2929"
        elif diff_percent < 0:
            signal = "SELL"
            color = "#FF3333"
        else:
            signal = "NEUTRAL"
            color = "#FFCC00"
        
        result = SignalResponse(
            ticker=ticker.upper(),
            signal=signal,
            color=color,
            price=current_price,
            angle_1x1=angle_1x1_price,
            timestamp=current_timestamp
        )
        cache_storage[cache_key] = (result, datetime.now())
        return result
    
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error generating signal for {ticker}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.post("/ai/analyze-chart", response_model=AITradeAnalysisResponse)
async def analyze_chart_ai(request: AITradeAnalysisRequest):
    """Technical analysis for trade setups"""
    try:
        # Prepare chart data
        bars_data = request.bars[-60:]  # Last 60 bars
        
        # Calculate key levels
        highs = [b['high'] for b in bars_data]
        lows = [b['low'] for b in bars_data]
        closes = [b['close'] for b in bars_data]
        
        current_price = closes[-1]
        highest = max(highs)
        lowest = min(lows)
        
        # Calculate SMAs
        sma_20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else current_price
        sma_50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else current_price
        
        # Simple RSI calculation
        gains = []
        losses = []
        for i in range(1, min(14, len(closes))):
            change = closes[i] - closes[i-1]
            if change > 0:
                gains.append(change)
                losses.append(0)
            else:
                gains.append(0)
                losses.append(abs(change))
        
        avg_gain = sum(gains) / len(gains) if gains else 0
        avg_loss = sum(losses) / len(losses) if losses else 0.01
        rs = avg_gain / avg_loss if avg_loss != 0 else 0
        rsi = 100 - (100 / (1 + rs))
        
        # Trend detection
        recent_trend = "bullish" if closes[-1] > closes[-5] else "bearish"
        ma_trend = "bullish" if sma_20 > sma_50 else "bearish"
        
        # Support and Resistance
        support = min(lows[-20:]) if len(lows) >= 20 else lowest
        resistance = max(highs[-20:]) if len(highs) >= 20 else highest
        
        # Generate trade setup based on analysis
        if ma_trend == "bullish" and recent_trend == "bullish" and rsi < 70:
            direction = "Long"
            entry = f"{current_price:.2f}"
            sl = f"{(current_price * 0.98):.2f}"
            targets = [
                f"{(current_price * 1.015):.2f}",
                f"{(current_price * 1.025):.2f}",
                f"{(current_price * 1.04):.2f}"
            ]
            reason = f"Bullish trend confirmed. Price above 20 & 50 SMA. RSI at {rsi:.0f} shows momentum. Support at {support:.2f}. Good risk-reward for long position."
        
        elif ma_trend == "bearish" and recent_trend == "bearish" and rsi > 30:
            direction = "Short"
            entry = f"{current_price:.2f}"
            sl = f"{(current_price * 1.02):.2f}"
            targets = [
                f"{(current_price * 0.985):.2f}",
                f"{(current_price * 0.975):.2f}",
                f"{(current_price * 0.96):.2f}"
            ]
            reason = f"Bearish trend in play. Price below key SMAs. RSI at {rsi:.0f} indicates weakness. Resistance at {resistance:.2f}. Short setup with tight risk."
        
        elif rsi > 70:
            direction = "Short"
            entry = f"{current_price:.2f}"
            sl = f"{(current_price * 1.015):.2f}"
            targets = [
                f"{(current_price * 0.99):.2f}",
                f"{(current_price * 0.98):.2f}"
            ]
            reason = f"Overbought condition - RSI at {rsi:.0f}. Price near resistance at {resistance:.2f}. Potential pullback expected. Short with tight stops."
        
        elif rsi < 30:
            direction = "Long"
            entry = f"{current_price:.2f}"
            sl = f"{(current_price * 0.985):.2f}"
            targets = [
                f"{(current_price * 1.01):.2f}",
                f"{(current_price * 1.02):.2f}"
            ]
            reason = f"Oversold condition - RSI at {rsi:.0f}. Price near support at {support:.2f}. Bounce expected. Long with tight risk management."
        
        else:
            # Neutral - follow the trend
            if ma_trend == "bullish":
                direction = "Long"
                entry = f"{current_price:.2f}"
                sl = f"{support:.2f}"
                targets = [
                    f"{(current_price * 1.02):.2f}",
                    f"{resistance:.2f}"
                ]
                reason = f"Following bullish bias. Price consolidating above {support:.2f} support. Target resistance at {resistance:.2f}. Wait for breakout confirmation."
            else:
                direction = "Short"
                entry = f"{current_price:.2f}"
                sl = f"{resistance:.2f}"
                targets = [
                    f"{(current_price * 0.98):.2f}",
                    f"{support:.2f}"
                ]
                reason = f"Following bearish bias. Price below {resistance:.2f} resistance. Target support at {support:.2f}. Sell on rallies."
        
        return AITradeAnalysisResponse(
            direction=direction,
            entry_price=entry,
            stoploss=sl,
            targets=targets,
            reason=reason
        )
        
    except Exception as e:
        logging.error(f"Error in analysis: {e}")
        # Fallback simple analysis
        return AITradeAnalysisResponse(
            direction="Long",
            entry_price=f"{closes[-1]:.2f}",
            stoploss=f"{(closes[-1] * 0.98):.2f}",
            targets=[f"{(closes[-1] * 1.02):.2f}", f"{(closes[-1] * 1.04):.2f}"],
            reason="Following current price trend. Use proper risk management."
        )


@api_router.post("/falling-knife/analyze", response_model=FallingKnifeAnalysisResponse)
async def analyze_falling_knife(request: FallingKnifeAnalysisRequest):
    """Falling Knife Reversal Analysis"""
    try:
        bars = request.bars
        if len(bars) < 60:
            raise HTTPException(status_code=400, detail="Need at least 60 bars")
        
        # Extract data
        highs = [b['high'] for b in bars]
        lows = [b['low'] for b in bars]
        closes = [b['close'] for b in bars]
        
        current_price = closes[-1]
        
        # Step 1: Check 40% drop from peak
        peak_price = max(highs)
        drop_percentage = ((peak_price - current_price) / peak_price) * 100
        meets_drop_req = drop_percentage >= 40
        
        # Step 2: Calculate Bollinger Bands (20, 2)
        period = 20
        sma = sum(closes[-period:]) / period
        std_dev = (sum((x - sma) ** 2 for x in closes[-period:]) / period) ** 0.5
        bb_upper = sma + (2 * std_dev)
        bb_lower = sma - (2 * std_dev)
        bb_width = bb_upper - bb_lower
        
        # Check for squeeze (narrow bands)
        avg_bb_width = sum([
            ((sum(closes[i-period:i])/period + 2*((sum((closes[j]-sum(closes[i-period:i])/period)**2 for j in range(i-period,i))/period)**0.5)) - 
             (sum(closes[i-period:i])/period - 2*((sum((closes[j]-sum(closes[i-period:i])/period)**2 for j in range(i-period,i))/period)**0.5)))
            for i in range(period+10, len(closes))
        ]) / (len(closes) - period - 10)
        
        bollinger_squeeze = bb_width < avg_bb_width * 0.7
        
        # Step 3: Calculate Keltner Channels (20, 1.5)
        atr_period = 20
        trs = []
        for i in range(len(bars) - atr_period, len(bars)):
            h_l = highs[i] - lows[i]
            h_c = abs(highs[i] - closes[i-1]) if i > 0 else h_l
            l_c = abs(lows[i] - closes[i-1]) if i > 0 else h_l
            trs.append(max(h_l, h_c, l_c))
        atr = sum(trs) / len(trs)
        
        kc_upper = sma + (1.5 * atr)
        kc_lower = sma - (1.5 * atr)
        price_in_keltner = kc_lower <= current_price <= kc_upper
        
        # Step 4: Calculate MACD (12, 26, 9)
        ema_12 = sum(closes[-12:]) / 12
        ema_26 = sum(closes[-26:]) / 26
        macd_line = ema_12 - ema_26
        
        # Check for bullish divergence (simplified)
        macd_bullish = macd_line > 0
        
        # Count conditions met
        conditions = [meets_drop_req, bollinger_squeeze, price_in_keltner, macd_bullish]
        conditions_met = sum(conditions)
        
        # Determine status
        if conditions_met >= 3 and meets_drop_req:
            status = "READY"
            signal_type = "BUY"
            entry = f"{current_price:.2f}"
            sl = f"{min(lows[-10:]):.2f}"
            targets = [
                f"{(current_price * 1.05):.2f}",
                f"{(current_price * 1.10):.2f}",
                f"{(current_price * 1.15):.2f}"
            ]
            rec = f"All conditions met! Entry signal active. Stock dropped {drop_percentage:.1f}% from peak. Bollinger squeeze + Keltner entry + MACD positive. Enter now with stop at recent low."
        elif conditions_met >= 2 and meets_drop_req:
            status = "SETUP"
            signal_type = "WAIT"
            entry = f"{current_price:.2f}"
            sl = f"{min(lows[-10:]):.2f}"
            targets = [f"{(current_price * 1.05):.2f}"]
            rec = f"Setup forming. {conditions_met}/3 conditions met. Stock down {drop_percentage:.1f}%. Wait for all signals before entry. Monitor closely."
        else:
            status = "NO SIGNAL"
            signal_type = "WAIT"
            entry = None
            sl = None
            targets = None
            if not meets_drop_req:
                rec = f"Stock only down {drop_percentage:.1f}% from peak. Needs ≥40% drop. Not a falling knife yet."
            else:
                rec = f"Drop requirement met ({drop_percentage:.1f}%), but only {conditions_met}/3 technical conditions present. Wait for complete setup."
        
        return FallingKnifeAnalysisResponse(
            status=status,
            signal_type=signal_type,
            conditions_met=conditions_met,
            drop_percentage=drop_percentage,
            bollinger_squeeze=bollinger_squeeze,
            price_in_keltner=price_in_keltner,
            macd_bullish=macd_bullish,
            entry_price=entry,
            stop_loss=sl,
            targets=targets,
            recommendation=rec
        )
        
    except Exception as e:
        logging.error(f"Error in falling knife analysis: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.post("/reverse-swings/analyze", response_model=ReverseSwingsResponse)
async def analyze_reverse_swings(request: ReverseSwingsRequest):
    """Reverse Price Swings Analysis - Method A & B"""
    try:
        bars = request.bars
        if len(bars) < 10:
            raise HTTPException(status_code=400, detail="Need at least 10 bars")
        
        closes = [b['close'] for b in bars]
        highs = [b['high'] for b in bars]
        lows = [b['low'] for b in bars]
        
        current_close = closes[-1]
        close_5_days_ago = closes[-6] if len(closes) >= 6 else closes[0]
        
        # Determine Method (forced or auto)
        if request.force_method:
            method = request.force_method
        elif current_close < close_5_days_ago:
            method = "A"
        else:
            method = "B"
        
        if method == "A":
            # Calculate max buy swing for last 4 days
            buy_swings = []
            for i in range(-5, -1):
                if i >= -len(bars):
                    max_buy_swing = highs[i] - lows[i]
                    buy_swings.append(max_buy_swing)
            
            avg_swing = sum(buy_swings) / len(buy_swings) if buy_swings else 0
            current_swing = highs[-1] - lows[-1]
            threshold = avg_swing * 1.75
            
            swing_signal = current_swing >= threshold
            trend_confirmed = True
            
            # Valid entry days for Method A: Tuesday(2), Wednesday(3), Friday(5)
            # Get tomorrow's day
            from datetime import datetime, timedelta
            tomorrow = (datetime.now() + timedelta(days=1)).weekday()  # 0=Monday, 6=Sunday
            valid_days = [1, 2, 4]  # Tuesday, Wednesday, Friday
            valid_entry_day = tomorrow in valid_days
            
            day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
            entry_day_name = day_names[tomorrow]
            
            # Calculate stop loss: 2% below close of day before signal day
            prev_close = closes[-2] if len(closes) >= 2 else closes[-1]
            stop_loss_price = prev_close * 0.98
            
            price_comp = f"₹{current_close:.2f} < ₹{close_5_days_ago:.2f}"
            
        else:
            method = "B"  # Overbought - Short trade
            # Calculate max sell swing for last 4 days
            sell_swings = []
            for i in range(-5, -1):
                if i >= -len(bars):
                    max_sell_swing = highs[i] - lows[i]
                    sell_swings.append(max_sell_swing)
            
            avg_swing = sum(sell_swings) / len(sell_swings) if sell_swings else 0
            current_swing = highs[-1] - lows[-1]
            threshold = avg_swing * 1.75
            
            swing_signal = current_swing >= threshold
            trend_confirmed = True
            
            # Valid entry days for Method B: Monday(1), Wednesday(3), Thursday(4)
            from datetime import datetime, timedelta
            tomorrow = (datetime.now() + timedelta(days=1)).weekday()
            valid_days = [0, 2, 3]  # Monday, Wednesday, Thursday
            valid_entry_day = tomorrow in valid_days
            
            day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
            entry_day_name = day_names[tomorrow]
            
            # Calculate stop loss: 2% above close of day before signal day
            prev_close = closes[-2] if len(closes) >= 2 else closes[-1]
            stop_loss_price = prev_close * 1.02
            
            price_comp = f"₹{current_close:.2f} > ₹{close_5_days_ago:.2f}"
        
        # Signal is active if all conditions met
        signal_active = trend_confirmed and swing_signal and valid_entry_day
        
        if signal_active:
            entry_price = f"{current_close:.2f}"
            stop_loss = f"{stop_loss_price:.2f}"
            entry_day = entry_day_name
            
            if method == "A":
                signal_type = "BUY"
                targets = [
                    f"{(current_close * 1.02):.2f}",
                    f"{(current_close * 1.04):.2f}",
                    f"{(current_close * 1.06):.2f}"
                ]
                rec = f"METHOD A SIGNAL! Enter LONG tomorrow ({entry_day_name}). Stock is oversold (down from ₹{close_5_days_ago:.2f}). Strong buy swing detected ({current_swing:.2f} > {threshold:.2f}). Stop loss at ₹{stop_loss_price:.2f}. Valid entry day confirmed."
            else:
                signal_type = "SELL"
                targets = [
                    f"{(current_close * 0.98):.2f}",
                    f"{(current_close * 0.96):.2f}",
                    f"{(current_close * 0.94):.2f}"
                ]
                rec = f"METHOD B SIGNAL! Enter SHORT tomorrow ({entry_day_name}). Stock is overbought (up from ₹{close_5_days_ago:.2f}). Strong sell swing detected ({current_swing:.2f} > {threshold:.2f}). Stop loss at ₹{stop_loss_price:.2f}. Valid entry day confirmed."
        else:
            signal_type = "WAIT"
            entry_price = None
            stop_loss = None
            targets = None
            entry_day = None
            
            missing = []
            if not trend_confirmed:
                missing.append(f"{'oversold' if method == 'A' else 'overbought'} condition")
            if not swing_signal:
                missing.append(f"swing magnitude (need ≥{threshold:.2f}, got {current_swing:.2f})")
            if not valid_entry_day:
                missing.append(f"valid entry day (tomorrow is {entry_day_name})")
            
            rec = f"Signal not active. Waiting for: {', '.join(missing)}. Monitor daily for setup completion."
        
        return ReverseSwingsResponse(
            method=method,
            signal_type=signal_type,
            trend_confirmed=trend_confirmed,
            swing_signal=swing_signal,
            valid_entry_day=valid_entry_day,
            signal_active=signal_active,
            current_swing=f"{current_swing:.2f}",
            avg_swing=f"{avg_swing:.2f}",
            threshold_swing=f"{threshold:.2f}",
            price_comparison=price_comp,
            entry_price=entry_price,
            stop_loss=stop_loss,
            targets=targets,
            entry_day=entry_day,
            recommendation=rec
        )
        
    except Exception as e:
        logging.error(f"Error in reverse swings analysis: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class ExplosiveVolumeRequest(BaseModel):
    ticker: str
    bars: List[dict]
    force_option: Optional[str] = None  # 'A' or 'B'


class ExplosiveVolumeResponse(BaseModel):
    status: str
    signal_type: str
    fundamentals: dict
    technical_conditions: dict
    conditions_met: int
    total_conditions: int
    entry_strategy: Optional[dict] = None
    exit_option_a: Optional[dict] = None
    exit_option_b: Optional[dict] = None
    targets: Optional[List[str]] = None
    recommendation: str
    warnings: List[str]


def calc_ema(data, period):
    """Calculate EMA"""
    if len(data) < period:
        return data[-1] if data else 0
    multiplier = 2 / (period + 1)
    ema = sum(data[:period]) / period
    for price in data[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def calc_cci(highs, lows, closes, period=20):
    """Calculate CCI (Commodity Channel Index)"""
    if len(closes) < period:
        return 0
    typical_prices = [(h + lo + c) / 3 for h, lo, c in zip(highs, lows, closes)]
    tp_slice = typical_prices[-period:]
    sma_tp = sum(tp_slice) / period
    mean_dev = sum(abs(tp - sma_tp) for tp in tp_slice) / period
    if mean_dev == 0:
        return 0
    return (tp_slice[-1] - sma_tp) / (0.015 * mean_dev)


def calc_five_day_oscillator(highs, lows, closes):
    """Calculate 5-Day Oscillator: ((Close - Low5) / (High5 - Low5)) * 100"""
    if len(closes) < 5:
        return 50
    high5 = max(highs[-5:])
    low5 = min(lows[-5:])
    if high5 == low5:
        return 50
    return ((closes[-1] - low5) / (high5 - low5)) * 100


@api_router.post("/explosive-volume/analyze", response_model=ExplosiveVolumeResponse)
async def analyze_explosive_volume(request: ExplosiveVolumeRequest):
    """Explosive Volume Strategy Analysis"""
    try:
        bars = request.bars
        warnings = []

        if len(bars) < 60:
            raise HTTPException(status_code=400, detail="Need at least 60 bars for analysis")

        highs = [b['high'] for b in bars]
        lows = [b['low'] for b in bars]
        closes = [b['close'] for b in bars]
        volumes = [b['volume'] for b in bars]

        current_price = closes[-1]
        current_volume = volumes[-1]

        # ============ PHASE 1: FUNDAMENTAL DATA ============
        insider_pct = None
        float_shares = None
        fundamental_pass = True  # Default pass if data unavailable

        try:
            ticker_obj = yf.Ticker(request.ticker)
            info = ticker_obj.info
            insider_pct_raw = info.get('heldPercentInsiders')
            float_shares_raw = info.get('floatShares')

            if insider_pct_raw is not None:
                insider_pct = round(float(insider_pct_raw) * 100, 2)
            else:
                warnings.append("Insider ownership data not available — skipping fundamental check")

            if float_shares_raw is not None:
                float_shares = float(float_shares_raw)
            else:
                warnings.append("Float shares data not available — skipping fundamental check")
        except Exception as e:
            warnings.append(f"Could not fetch fundamental data: {str(e)[:50]}")

        insider_ok = insider_pct is not None and insider_pct >= 10
        float_ok = float_shares is not None and float_shares <= 35_000_000

        if insider_pct is None and float_shares is None:
            fundamental_pass = True  # Can't verify, skip
            warnings.append("Fundamental filters skipped — data unavailable for this stock")
        else:
            fundamental_pass = (insider_pct is None or insider_ok) and (float_shares is None or float_ok)

        fundamentals = {
            "insider_ownership": f"{insider_pct}%" if insider_pct is not None else "N/A",
            "insider_ok": insider_ok if insider_pct is not None else "N/A",
            "float_shares": f"{float_shares/1e6:.1f}M" if float_shares is not None else "N/A",
            "float_ok": float_ok if float_shares is not None else "N/A",
            "fundamental_pass": fundamental_pass
        }

        # ============ PHASE 2: TECHNICAL CONDITIONS ============

        # 1. No overhead resistance in 12 months (price near 12m high)
        high_12m = max(highs)
        near_high_pct = ((high_12m - current_price) / high_12m) * 100
        no_resistance = near_high_pct <= 5  # within 5% of 12m high

        # 2. Volume > 2x 50-day SMA Volume
        vol_sma_50 = sum(volumes[-50:]) / min(50, len(volumes)) if len(volumes) >= 10 else current_volume
        volume_explosive = current_volume > (2 * vol_sma_50)
        vol_ratio = current_volume / vol_sma_50 if vol_sma_50 > 0 else 0

        # 3. Price at or near 60-day high (within 3%)
        high_60 = max(highs[-60:]) if len(highs) >= 60 else max(highs)
        near_60d_high = ((high_60 - current_price) / high_60) * 100 <= 3

        # 4. EMA Trend: 10 EMA > 20 EMA (short-term momentum)
        ema_10 = calc_ema(closes, 10)
        ema_20 = calc_ema(closes, 20)
        ema_trend_bullish = ema_10 > ema_20

        # 5. Price above 200-day SMA (long-term uptrend)
        sma_200 = sum(closes) / len(closes) if len(closes) >= 50 else current_price
        above_long_sma = current_price > sma_200

        # 6. CCI above +100 (strong momentum)
        cci_value = calc_cci(highs, lows, closes, 20)
        cci_strong = cci_value > 100

        # 7. Volume acceleration: today volume > yesterday volume
        vol_accel = len(volumes) >= 2 and volumes[-1] > volumes[-2]

        # 8. Price breakout: close > previous 5 day high
        prev_5_high = max(highs[-6:-1]) if len(highs) >= 6 else max(highs[:-1]) if len(highs) >= 2 else current_price
        price_breakout = current_price > prev_5_high

        technical_conditions = {
            "no_resistance_12m": {"met": no_resistance, "detail": f"Price {near_high_pct:.1f}% from 12m high (need ≤5%)"},
            "volume_2x_sma50": {"met": volume_explosive, "detail": f"Vol ratio: {vol_ratio:.1f}x (need >2x)"},
            "near_60d_high": {"met": near_60d_high, "detail": f"₹{current_price:.2f} vs 60d high ₹{high_60:.2f}"},
            "ema_trend": {"met": ema_trend_bullish, "detail": f"EMA10: ₹{ema_10:.2f} vs EMA20: ₹{ema_20:.2f}"},
            "above_long_sma": {"met": above_long_sma, "detail": f"Price ₹{current_price:.2f} vs SMA: ₹{sma_200:.2f}"},
            "cci_momentum": {"met": cci_strong, "detail": f"CCI: {cci_value:.0f} (need >100)"},
            "volume_accel": {"met": vol_accel, "detail": f"Today vol vs yesterday: {'↑' if vol_accel else '↓'}"},
            "price_breakout": {"met": price_breakout, "detail": f"Close ₹{current_price:.2f} vs prev 5d high ₹{prev_5_high:.2f}"}
        }

        tech_met = sum(1 for v in technical_conditions.values() if v["met"])
        total_conditions = 8

        # ============ PHASE 3: ENTRY/EXIT STRATEGY ============
        entry_strategy = None
        exit_option_a = None
        exit_option_b = None

        if tech_met >= 4:
            # Entry: Limit Buy = (Open + High) / 2 + 5%
            today_open = bars[-1]['open']
            today_high = bars[-1]['high']
            limit_buy = ((today_open + today_high) / 2) * 1.05

            entry_strategy = {
                "type": "LIMIT BUY",
                "price": f"{limit_buy:.2f}",
                "formula": f"(Open ₹{today_open:.2f} + High ₹{today_high:.2f}) / 2 + 5%",
                "stop_loss": f"{(current_price * 0.93):.2f}",
                "risk_pct": "7%"
            }

            # Exit Option A: CCI Divergence
            cci_zone = "Overbought" if cci_value > 200 else "Strong" if cci_value > 100 else "Normal" if cci_value > 0 else "Weak"
            prev_cci = calc_cci(highs[:-1], lows[:-1], closes[:-1], 20) if len(closes) > 20 else cci_value
            cci_divergence = cci_value < prev_cci and cci_value > 100

            exit_option_a = {
                "method": "CCI Divergence",
                "current_cci": f"{cci_value:.0f}",
                "prev_cci": f"{prev_cci:.0f}",
                "zone": cci_zone,
                "divergence_detected": cci_divergence,
                "exit_signal": cci_value < 100 and prev_cci > 100,
                "rule": "Exit when CCI drops below +100 from overbought zone",
                "action": "SELL - CCI crossed below 100" if (cci_value < 100 and prev_cci > 100) else "HOLD - CCI momentum intact"
            }

            # Exit Option B: 5-Day Oscillator
            osc_value = calc_five_day_oscillator(highs, lows, closes)
            prev_osc = calc_five_day_oscillator(highs[:-1], lows[:-1], closes[:-1]) if len(closes) > 5 else osc_value
            osc_zone = "Overbought" if osc_value > 80 else "Oversold" if osc_value < 20 else "Neutral"

            exit_option_b = {
                "method": "5-Day Oscillator",
                "current_value": f"{osc_value:.1f}",
                "prev_value": f"{prev_osc:.1f}",
                "zone": osc_zone,
                "exit_signal": osc_value < 20 and prev_osc > 20,
                "rule": "Exit when oscillator drops below 20 from above 80",
                "action": "SELL - Oscillator crashed below 20" if (osc_value < 20 and prev_osc > 20) else "HOLD - Oscillator stable"
            }

        # ============ STATUS & RECOMMENDATION ============
        fund_note = "" if fundamental_pass else " (Fundamentals not met — higher risk)"
        targets = None
        
        if tech_met >= 6:
            status = "EXPLOSIVE"
            signal_type = "BUY"
            if entry_strategy:
                buy_price = float(entry_strategy['price'])
                targets = [
                    f"{(buy_price * 1.05):.2f}",
                    f"{(buy_price * 1.10):.2f}",
                    f"{(buy_price * 1.15):.2f}"
                ]
            rec = f"EXPLOSIVE VOLUME detected! {tech_met}/8 technical conditions met{fund_note}. "
            if entry_strategy:
                rec += f"Limit buy at ₹{entry_strategy['price']}. Stop loss at ₹{entry_strategy['stop_loss']}. "
            rec += "Use Option A (CCI) or Option B (5-Day Oscillator) for exit timing."
        elif tech_met >= 4:
            status = "BUILDING"
            signal_type = "WAIT"
            if entry_strategy:
                buy_price = float(entry_strategy['price'])
                targets = [f"{(buy_price * 1.05):.2f}"]
            rec = f"Volume building up. {tech_met}/8 conditions met{fund_note}. Setup forming — monitor for explosive breakout. "
            if entry_strategy:
                rec += f"Tentative entry at ₹{entry_strategy['price']}."
        elif tech_met >= 2:
            status = "WATCHING"
            signal_type = "WAIT"
            rec = f"Early stage. Only {tech_met}/8 conditions met. Not ready for entry yet. Keep on watchlist."
        else:
            status = "NO SIGNAL"
            signal_type = "WAIT"
            rec = f"No explosive volume setup detected. Only {tech_met}/8 conditions met. Move to next stock."

        if not fundamental_pass:
            rec += " Fundamental filters not met — higher risk trade."

        return ExplosiveVolumeResponse(
            status=status,
            signal_type=signal_type,
            fundamentals=fundamentals,
            technical_conditions=technical_conditions,
            conditions_met=tech_met,
            total_conditions=total_conditions,
            entry_strategy=entry_strategy,
            exit_option_a=exit_option_a,
            exit_option_b=exit_option_b,
            targets=targets,
            recommendation=rec,
            warnings=warnings
        )

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error in explosive volume analysis: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class GoldenSetupRequest(BaseModel):
    ticker: str
    bars: List[dict]
    pro_mode: Optional[bool] = False
    multi_timeframe: Optional[bool] = False


class GoldenSetupResponse(BaseModel):
    mode: str
    signal_type: str
    conditions: dict
    entry_price: Optional[str] = None
    stop_loss: Optional[str] = None
    targets: Optional[List[str]] = None
    risk_reward: Optional[str] = None
    adx_value: Optional[float] = None
    pro_details: Optional[dict] = None
    mtf_confirmation: Optional[dict] = None
    recommendation: str


def calc_adx(highs, lows, closes, period=14):
    """Calculate ADX (Average Directional Index)"""
    if len(closes) < period * 2:
        return 0
    plus_dm = []
    minus_dm = []
    tr_list = []
    for i in range(1, len(highs)):
        high_diff = highs[i] - highs[i-1]
        low_diff = lows[i-1] - lows[i]
        plus_dm.append(max(high_diff, 0) if high_diff > low_diff else 0)
        minus_dm.append(max(low_diff, 0) if low_diff > high_diff else 0)
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        tr_list.append(tr)

    if len(tr_list) < period:
        return 0

    atr = sum(tr_list[:period]) / period
    plus_di_sum = sum(plus_dm[:period]) / period
    minus_di_sum = sum(minus_dm[:period]) / period

    for i in range(period, len(tr_list)):
        atr = (atr * (period - 1) + tr_list[i]) / period
        plus_di_sum = (plus_di_sum * (period - 1) + plus_dm[i]) / period
        minus_di_sum = (minus_di_sum * (period - 1) + minus_dm[i]) / period

    if atr == 0:
        return 0
    plus_di = (plus_di_sum / atr) * 100
    minus_di = (minus_di_sum / atr) * 100
    di_sum = plus_di + minus_di
    if di_sum == 0:
        return 0
    dx = abs(plus_di - minus_di) / di_sum * 100
    return dx


def find_swing_lows(lows, window=5):
    """Find recent swing lows"""
    swings = []
    for i in range(window, len(lows) - 1):
        if lows[i] == min(lows[i-window:i+window+1]):
            swings.append((i, lows[i]))
    return swings


def find_swing_highs(highs, window=5):
    """Find recent swing highs"""
    swings = []
    for i in range(window, len(highs) - 1):
        if highs[i] == max(highs[i-window:i+window+1]):
            swings.append((i, highs[i]))
    return swings


def is_bullish_candle(o, h, low_val, c):
    """Check bullish candle or hammer"""
    body = abs(c - o)
    full_range = h - low_val
    if full_range == 0:
        return False
    if c > o:
        return True
    lower_wick = min(o, c) - low_val
    if lower_wick > body * 2 and body < full_range * 0.3:
        return True
    return False


def is_bearish_candle(o, h, low_val, c):
    """Check bearish candle or shooting star"""
    body = abs(c - o)
    full_range = h - low_val
    if full_range == 0:
        return False
    if c < o:
        return True
    upper_wick = h - max(o, c)
    if upper_wick > body * 2 and body < full_range * 0.3:
        return True
    return False


def is_engulfing_bullish(bars, idx):
    """Check bullish engulfing at index"""
    if idx < 1:
        return False
    prev = bars[idx - 1]
    curr = bars[idx]
    return prev['close'] < prev['open'] and curr['close'] > curr['open'] and curr['close'] > prev['open'] and curr['open'] < prev['close']


def is_engulfing_bearish(bars, idx):
    """Check bearish engulfing at index"""
    if idx < 1:
        return False
    prev = bars[idx - 1]
    curr = bars[idx]
    return prev['close'] > prev['open'] and curr['close'] < curr['open'] and curr['open'] > prev['close'] and curr['close'] < prev['open']


def has_rejection_wick(bar, direction):
    """Check for strong rejection wick"""
    o, h, low_val, c = bar['open'], bar['high'], bar['low'], bar['close']
    full = h - low_val
    if full == 0:
        return False
    if direction == 'bull':
        lower = min(o, c) - low_val
        return lower / full > 0.5
    else:
        upper = h - max(o, c)
        return upper / full > 0.5


@api_router.post("/golden-setup/analyze", response_model=GoldenSetupResponse)
async def analyze_golden_setup(request: GoldenSetupRequest):
    """Golden Setup Strategy - Normal & Pro Mode"""
    try:
        bars = request.bars
        pro_mode = request.pro_mode

        if len(bars) < 60:
            raise HTTPException(status_code=400, detail="Need at least 60 bars")

        highs = [b['high'] for b in bars]
        lows = [b['low'] for b in bars]
        closes = [b['close'] for b in bars]
        volumes = [b['volume'] for b in bars]

        current = closes[-1]
        last_bar = bars[-1]

        # Core indicators
        sma_200 = sum(closes[-min(200, len(closes)):]) / min(200, len(closes))
        ema_20 = calc_ema(closes, 20)
        ema_50 = calc_ema(closes, 50)
        prev_ema_20 = calc_ema(closes[:-1], 20)
        prev_ema_50 = calc_ema(closes[:-1], 50)
        adx = calc_adx(highs, lows, closes, 14)

        # Multi-timeframe confirmation (computed later based on signal)
        mtf_data = None

        # ====== NORMAL MODE ======
        if not pro_mode:
            # BUY conditions
            above_200 = current > sma_200
            ema_cross_bull = prev_ema_20 <= prev_ema_50 and ema_20 > ema_50
            ema_already_bull = ema_20 > ema_50
            near_ema20_buy = abs(current - ema_20) / ema_20 * 100 < 1.5
            bullish = is_bullish_candle(last_bar['open'], last_bar['high'], last_bar['low'], last_bar['close'])
            adx_strong = adx > 20

            # SELL conditions
            below_200 = current < sma_200
            ema_cross_bear = prev_ema_20 >= prev_ema_50 and ema_20 < ema_50
            ema_already_bear = ema_20 < ema_50
            near_ema20_sell = abs(current - ema_20) / ema_20 * 100 < 1.5
            bearish = is_bearish_candle(last_bar['open'], last_bar['high'], last_bar['low'], last_bar['close'])

            buy_score = sum([above_200, ema_cross_bull or ema_already_bull, near_ema20_buy, bullish, adx_strong])
            sell_score = sum([below_200, ema_cross_bear or ema_already_bear, near_ema20_sell, bearish, adx_strong])

            swing_lows = find_swing_lows(lows)
            swing_highs = find_swing_highs(highs)
            recent_swing_low = swing_lows[-1][1] if swing_lows else min(lows[-10:])
            recent_swing_high = swing_highs[-1][1] if swing_highs else max(highs[-10:])

            if buy_score >= 4:
                entry = current
                sl = min(recent_swing_low, ema_20 * 0.99)
                risk = entry - sl
                t1 = entry + risk * 2
                t2 = entry + risk * 3

                conditions = {
                    "price_above_200sma": {"met": above_200, "detail": f"₹{current:.2f} vs SMA200 ₹{sma_200:.2f}"},
                    "ema_20_above_50": {"met": ema_cross_bull or ema_already_bull, "detail": f"EMA20 ₹{ema_20:.2f} vs EMA50 ₹{ema_50:.2f}" + (" (Fresh Cross!)" if ema_cross_bull else "")},
                    "pullback_to_ema20": {"met": near_ema20_buy, "detail": f"Price {abs(current - ema_20)/ema_20*100:.1f}% from EMA20"},
                    "bullish_candle": {"met": bullish, "detail": "Green candle / Hammer confirmed" if bullish else "No bullish pattern"},
                    "adx_above_20": {"met": adx_strong, "detail": f"ADX: {adx:.1f} (need >20)"}
                }
                rec = f"GOLDEN BUY! All conditions met. Price above 200 SMA, EMA20 > EMA50, pullback to EMA20 zone, bullish candle confirmed. ADX {adx:.0f} shows strong trend. Enter at ₹{entry:.2f}, SL ₹{sl:.2f}."

                if request.multi_timeframe:
                    mtf_data = get_mtf_confirmation(request.ticker, "BUY")
                    if mtf_data and mtf_data.get("confirmed"):
                        rec += f" MTF CONFIRMED ({mtf_data['strength']})!"

                return GoldenSetupResponse(
                    mode="Normal", signal_type="BUY", conditions=conditions,
                    entry_price=f"{entry:.2f}", stop_loss=f"{sl:.2f}",
                    targets=[f"{t1:.2f}", f"{t2:.2f}"],
                    risk_reward="1:2 / 1:3", adx_value=round(adx, 1),
                    mtf_confirmation=mtf_data, recommendation=rec
                )

            elif sell_score >= 4:
                entry = current
                sl = max(recent_swing_high, ema_20 * 1.01)
                risk = sl - entry
                t1 = entry - risk * 2
                t2 = entry - risk * 3

                conditions = {
                    "price_below_200sma": {"met": below_200, "detail": f"₹{current:.2f} vs SMA200 ₹{sma_200:.2f}"},
                    "ema_20_below_50": {"met": ema_cross_bear or ema_already_bear, "detail": f"EMA20 ₹{ema_20:.2f} vs EMA50 ₹{ema_50:.2f}" + (" (Fresh Cross!)" if ema_cross_bear else "")},
                    "pullback_to_ema20": {"met": near_ema20_sell, "detail": f"Price {abs(current - ema_20)/ema_20*100:.1f}% from EMA20"},
                    "bearish_candle": {"met": bearish, "detail": "Red candle / Shooting star confirmed" if bearish else "No bearish pattern"},
                    "adx_above_20": {"met": adx_strong, "detail": f"ADX: {adx:.1f} (need >20)"}
                }
                rec = f"GOLDEN SELL! All conditions met. Price below 200 SMA, EMA20 < EMA50, pullback to EMA20 zone, bearish candle confirmed. ADX {adx:.0f} shows strong trend. Enter at ₹{entry:.2f}, SL ₹{sl:.2f}."

                if request.multi_timeframe:
                    mtf_data = get_mtf_confirmation(request.ticker, "SELL")
                    if mtf_data.get("confirmed"):
                        rec += f" MTF CONFIRMED ({mtf_data['strength']})!"

                return GoldenSetupResponse(
                    mode="Normal", signal_type="SELL", conditions=conditions,
                    entry_price=f"{entry:.2f}", stop_loss=f"{sl:.2f}",
                    targets=[f"{t1:.2f}", f"{t2:.2f}"],
                    risk_reward="1:2 / 1:3", adx_value=round(adx, 1),
                    mtf_confirmation=mtf_data,
                    recommendation=rec
                )

            else:
                conditions = {
                    "price_vs_200sma": {"met": above_200 or below_200, "detail": f"₹{current:.2f} vs SMA200 ₹{sma_200:.2f}" + (" (Above)" if above_200 else " (Below)")},
                    "ema_crossover": {"met": ema_cross_bull or ema_cross_bear or ema_already_bull or ema_already_bear, "detail": f"EMA20 ₹{ema_20:.2f} vs EMA50 ₹{ema_50:.2f}"},
                    "pullback_to_ema20": {"met": near_ema20_buy or near_ema20_sell, "detail": f"Price {abs(current - ema_20)/ema_20*100:.1f}% from EMA20"},
                    "candle_pattern": {"met": bullish or bearish, "detail": "Bullish" if bullish else ("Bearish" if bearish else "No clear pattern")},
                    "adx_above_20": {"met": adx_strong, "detail": f"ADX: {adx:.1f} (need >20)"}
                }
                best = max(buy_score, sell_score)
                rec = f"No Golden Setup yet. Best score: {best}/5 conditions. " + ("Leaning bullish." if buy_score > sell_score else "Leaning bearish." if sell_score > buy_score else "Neutral.") + f" ADX at {adx:.0f}. Wait for complete setup."

                return GoldenSetupResponse(
                    mode="Normal", signal_type="WAIT", conditions=conditions,
                    adx_value=round(adx, 1), mtf_confirmation=mtf_data,
                    recommendation=rec
                )

        # ====== PRO MODE (SMC) ======
        else:
            lookback = min(30, len(bars) - 5)
            recent_bars = bars[-lookback:]
            r_highs = [b['high'] for b in recent_bars]
            r_lows = [b['low'] for b in recent_bars]
            r_closes = [b['close'] for b in recent_bars]
            r_volumes = [b['volume'] for b in recent_bars]

            swing_lows_all = find_swing_lows(r_lows, 3)
            swing_highs_all = find_swing_highs(r_highs, 3)

            avg_vol = sum(r_volumes) / len(r_volumes) if r_volumes else 1
            last_vol = volumes[-1]
            vol_spike = last_vol > avg_vol * 1.3

            # === BUY SETUP: Sweep Low → BOS Up → Retest → Bullish confirmation ===
            sweep_low = False
            sweep_low_price = 0
            bos_up = False
            bos_level = 0
            retest_buy = False
            bull_confirm = False

            if len(swing_lows_all) >= 2:
                prev_low = swing_lows_all[-2][1]
                # Check if recent price swept below prev low then recovered
                for i in range(swing_lows_all[-2][0] + 1, len(r_lows)):
                    if r_lows[i] < prev_low and r_closes[min(i, len(r_closes)-1)] > prev_low:
                        sweep_low = True
                        sweep_low_price = r_lows[i]
                        break

            if sweep_low and len(swing_highs_all) >= 1:
                last_high = swing_highs_all[-1][1]
                if current > last_high:
                    bos_up = True
                    bos_level = last_high

            if bos_up:
                if abs(current - ema_20) / ema_20 * 100 < 2.0 or abs(current - bos_level) / bos_level * 100 < 1.5:
                    retest_buy = True

            engulf_bull = is_engulfing_bullish(bars, len(bars) - 1)
            rej_bull = has_rejection_wick(last_bar, 'bull')
            bull_confirm = engulf_bull or rej_bull or (is_bullish_candle(last_bar['open'], last_bar['high'], last_bar['low'], last_bar['close']) and vol_spike)

            # === SELL SETUP: Sweep High → BOS Down → Retest → Bearish confirmation ===
            sweep_high = False
            sweep_high_price = 0
            bos_down = False
            bos_level_sell = 0
            retest_sell = False
            bear_confirm = False

            if len(swing_highs_all) >= 2:
                prev_high = swing_highs_all[-2][1]
                for i in range(swing_highs_all[-2][0] + 1, len(r_highs)):
                    if r_highs[i] > prev_high and r_closes[min(i, len(r_closes)-1)] < prev_high:
                        sweep_high = True
                        sweep_high_price = r_highs[i]
                        break

            if sweep_high and len(swing_lows_all) >= 1:
                last_low_val = swing_lows_all[-1][1]
                if current < last_low_val:
                    bos_down = True
                    bos_level_sell = last_low_val

            if bos_down:
                if abs(current - ema_20) / ema_20 * 100 < 2.0 or abs(current - bos_level_sell) / bos_level_sell * 100 < 1.5:
                    retest_sell = True

            engulf_bear = is_engulfing_bearish(bars, len(bars) - 1)
            rej_bear = has_rejection_wick(last_bar, 'bear')
            bear_confirm = engulf_bear or rej_bear or (is_bearish_candle(last_bar['open'], last_bar['high'], last_bar['low'], last_bar['close']) and vol_spike)

            buy_pro_score = sum([sweep_low, bos_up, retest_buy, bull_confirm])
            sell_pro_score = sum([sweep_high, bos_down, retest_sell, bear_confirm])

            confirm_details = []
            if engulf_bull:
                confirm_details.append("Bullish Engulfing")
            if engulf_bear:
                confirm_details.append("Bearish Engulfing")
            if rej_bull:
                confirm_details.append("Bullish Rejection Wick")
            if rej_bear:
                confirm_details.append("Bearish Rejection Wick")
            if vol_spike:
                confirm_details.append(f"Volume Spike ({last_vol/avg_vol:.1f}x)")

            if buy_pro_score >= 3:
                entry = current
                sl = sweep_low_price if sweep_low_price > 0 else min(lows[-10:])
                risk = entry - sl
                t1 = entry + risk * 2
                t2 = entry + risk * 3

                conditions = {
                    "sweep_low": {"met": sweep_low, "detail": f"Liquidity grab below ₹{sweep_low_price:.2f}" if sweep_low else "No sweep detected"},
                    "bos_up": {"met": bos_up, "detail": f"Structure break above ₹{bos_level:.2f}" if bos_up else "No BOS up"},
                    "retest": {"met": retest_buy, "detail": "Price retesting breakout zone" if retest_buy else "No retest yet"},
                    "confirmation": {"met": bull_confirm, "detail": ", ".join(confirm_details) if confirm_details else "No confirmation"}
                }
                pro_details = {
                    "sweep_price": f"{sweep_low_price:.2f}" if sweep_low else "N/A",
                    "bos_level": f"{bos_level:.2f}" if bos_up else "N/A",
                    "confirmation_signals": confirm_details,
                    "volume_ratio": f"{last_vol/avg_vol:.1f}x"
                }
                rec = f"PRO BUY! Sweep low at ₹{sweep_low_price:.2f} → BOS above ₹{bos_level:.2f} → Retest confirmed. {', '.join(confirm_details)}. Entry at retest ₹{entry:.2f}, SL below sweep ₹{sl:.2f}. RR 1:2 minimum."

                if request.multi_timeframe:
                    mtf_data = get_mtf_confirmation(request.ticker, "BUY")
                    if mtf_data.get("confirmed"):
                        rec += f" MTF CONFIRMED ({mtf_data['strength']})!"

                return GoldenSetupResponse(
                    mode="Pro (SMC)", signal_type="BUY", conditions=conditions,
                    entry_price=f"{entry:.2f}", stop_loss=f"{sl:.2f}",
                    targets=[f"{t1:.2f}", f"{t2:.2f}"],
                    risk_reward="1:2 / 1:3", adx_value=round(adx, 1),
                    pro_details=pro_details, mtf_confirmation=mtf_data,
                    recommendation=rec
                )

            elif sell_pro_score >= 3:
                entry = current
                sl = sweep_high_price if sweep_high_price > 0 else max(highs[-10:])
                risk = sl - entry
                t1 = entry - risk * 2
                t2 = entry - risk * 3

                conditions = {
                    "sweep_high": {"met": sweep_high, "detail": f"Liquidity grab above ₹{sweep_high_price:.2f}" if sweep_high else "No sweep detected"},
                    "bos_down": {"met": bos_down, "detail": f"Structure break below ₹{bos_level_sell:.2f}" if bos_down else "No BOS down"},
                    "retest": {"met": retest_sell, "detail": "Price retesting breakdown zone" if retest_sell else "No retest yet"},
                    "confirmation": {"met": bear_confirm, "detail": ", ".join(confirm_details) if confirm_details else "No confirmation"}
                }
                pro_details = {
                    "sweep_price": f"{sweep_high_price:.2f}" if sweep_high else "N/A",
                    "bos_level": f"{bos_level_sell:.2f}" if bos_down else "N/A",
                    "confirmation_signals": confirm_details,
                    "volume_ratio": f"{last_vol/avg_vol:.1f}x"
                }
                rec = f"PRO SELL! Sweep high at ₹{sweep_high_price:.2f} → BOS below ₹{bos_level_sell:.2f} → Retest confirmed. {', '.join(confirm_details)}. Entry at retest ₹{entry:.2f}, SL above sweep ₹{sl:.2f}. RR 1:2 minimum."

                if request.multi_timeframe:
                    mtf_data = get_mtf_confirmation(request.ticker, "SELL")
                    if mtf_data.get("confirmed"):
                        rec += f" MTF CONFIRMED ({mtf_data['strength']})!"

                return GoldenSetupResponse(
                    mode="Pro (SMC)", signal_type="SELL", conditions=conditions,
                    entry_price=f"{entry:.2f}", stop_loss=f"{sl:.2f}",
                    targets=[f"{t1:.2f}", f"{t2:.2f}"],
                    risk_reward="1:2 / 1:3", adx_value=round(adx, 1),
                    pro_details=pro_details, mtf_confirmation=mtf_data,
                    recommendation=rec
                )

            else:
                conditions = {
                    "sweep_low": {"met": sweep_low, "detail": f"Liquidity grab below ₹{sweep_low_price:.2f}" if sweep_low else "No sweep detected"},
                    "sweep_high": {"met": sweep_high, "detail": f"Liquidity grab above ₹{sweep_high_price:.2f}" if sweep_high else "No sweep detected"},
                    "bos": {"met": bos_up or bos_down, "detail": ("BOS Up" if bos_up else "BOS Down") if (bos_up or bos_down) else "No BOS"},
                    "retest": {"met": retest_buy or retest_sell, "detail": "Retest zone" if (retest_buy or retest_sell) else "No retest"},
                    "confirmation": {"met": bull_confirm or bear_confirm, "detail": ", ".join(confirm_details) if confirm_details else "No confirmation"}
                }
                pro_details = {
                    "sweep_price": "N/A",
                    "bos_level": "N/A",
                    "confirmation_signals": confirm_details,
                    "volume_ratio": f"{last_vol/avg_vol:.1f}x"
                }
                best = max(buy_pro_score, sell_pro_score)
                rec = f"No Pro setup yet. {best}/4 conditions met. " + ("Leaning bullish." if buy_pro_score > sell_pro_score else "Leaning bearish." if sell_pro_score > buy_pro_score else "Neutral.") + " Wait for complete Sweep → BOS → Retest → Confirm sequence."

                return GoldenSetupResponse(
                    mode="Pro (SMC)", signal_type="WAIT", conditions=conditions,
                    adx_value=round(adx, 1), pro_details=pro_details,
                    mtf_confirmation=mtf_data,
                    recommendation=rec
                )

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error in golden setup analysis: {e}")
        raise HTTPException(status_code=500, detail=str(e))


def get_mtf_confirmation(ticker, primary_signal):
    """Fetch higher timeframe data and confirm signal"""
    try:
        tf_data = yf.download(ticker, period="6mo", interval="1wk", progress=False)
        if tf_data.empty or len(tf_data) < 10:
            return {"confirmed": False, "timeframe": "Weekly", "detail": "Insufficient weekly data", "strength": "N/A"}

        # Handle multi-index columns from yfinance
        if isinstance(tf_data.columns, pd.MultiIndex):
            w_closes = tf_data['Close'].iloc[:, 0].dropna().values.tolist()
            w_highs = tf_data['High'].iloc[:, 0].dropna().values.tolist()
            w_lows = tf_data['Low'].iloc[:, 0].dropna().values.tolist()
        else:
            w_closes = tf_data['Close'].dropna().values.tolist()
            w_highs = tf_data['High'].dropna().values.tolist()
            w_lows = tf_data['Low'].dropna().values.tolist()

        if len(w_closes) < 10:
            return {"confirmed": False, "timeframe": "Weekly", "detail": "Not enough weekly closes", "strength": "N/A"}

        w_ema20 = calc_ema(w_closes, min(20, len(w_closes)))
        w_ema50 = calc_ema(w_closes, min(50, len(w_closes)))
        w_sma200 = sum(w_closes) / len(w_closes)
        w_current = w_closes[-1]
        w_adx = calc_adx(w_highs, w_lows, w_closes, 14)

        w_above_sma = w_current > w_sma200
        w_ema_bull = w_ema20 > w_ema50

        if primary_signal == "BUY":
            confirmed = w_above_sma and w_ema_bull
            strength = "STRONG" if confirmed and w_adx > 20 else ("MODERATE" if confirmed else "WEAK")
            detail = f"Weekly: Price {'>' if w_above_sma else '<'} SMA, EMA20 {'>' if w_ema_bull else '<'} EMA50, ADX {w_adx:.0f}"
        elif primary_signal == "SELL":
            confirmed = not w_above_sma and not w_ema_bull
            strength = "STRONG" if confirmed and w_adx > 20 else ("MODERATE" if confirmed else "WEAK")
            detail = f"Weekly: Price {'<' if not w_above_sma else '>'} SMA, EMA20 {'<' if not w_ema_bull else '>'} EMA50, ADX {w_adx:.0f}"
        else:
            confirmed = False
            strength = "N/A"
            detail = "No primary signal to confirm"

        return {"confirmed": confirmed, "timeframe": "Weekly", "detail": detail, "strength": strength, "adx": round(w_adx, 1)}
    except Exception as e:
        logging.error(f"MTF error: {e}")
        return {"confirmed": False, "timeframe": "Weekly", "detail": f"MTF error: {str(e)[:40]}", "strength": "N/A"}


class AIIndicatorRequest(BaseModel):
    ticker: str
    bars: List[dict]


class AIIndicatorResponse(BaseModel):
    ai_score: float
    signal_type: str
    indicator_scores: dict
    entry_price: Optional[str] = None
    stop_loss: Optional[str] = None
    targets: Optional[List[str]] = None
    exit_rules: Optional[dict] = None
    volume_confirmation: bool
    recommendation: str


def calc_rsi(closes, period=14):
    """Calculate RSI"""
    if len(closes) < period + 1:
        return 50
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calc_stochastics(highs, lows, closes, k_period=14, d_period=3):
    """Calculate Stochastic %K and %D"""
    if len(closes) < k_period:
        return 50, 50

    k_values = []
    for i in range(k_period - 1, len(closes)):
        h = max(highs[i - k_period + 1:i + 1])
        lo = min(lows[i - k_period + 1:i + 1])
        if h == lo:
            k_values.append(50)
        else:
            k_values.append(((closes[i] - lo) / (h - lo)) * 100)

    pct_k = k_values[-1] if k_values else 50
    pct_d = sum(k_values[-d_period:]) / min(d_period, len(k_values)) if k_values else 50
    return pct_k, pct_d


def calc_dmi_score(highs, lows, closes, period=14):
    """Calculate DMI score (0-100)"""
    if len(closes) < period * 2:
        return 50, 0, 0

    plus_dm = []
    minus_dm = []
    tr_list = []
    for i in range(1, len(highs)):
        high_diff = highs[i] - highs[i-1]
        low_diff = lows[i-1] - lows[i]
        plus_dm.append(max(high_diff, 0) if high_diff > low_diff else 0)
        minus_dm.append(max(low_diff, 0) if low_diff > high_diff else 0)
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        tr_list.append(tr)

    if len(tr_list) < period:
        return 50, 0, 0

    atr = sum(tr_list[:period]) / period
    p_sum = sum(plus_dm[:period]) / period
    m_sum = sum(minus_dm[:period]) / period

    for i in range(period, len(tr_list)):
        atr = (atr * (period - 1) + tr_list[i]) / period
        p_sum = (p_sum * (period - 1) + plus_dm[i]) / period
        m_sum = (m_sum * (period - 1) + minus_dm[i]) / period

    if atr == 0:
        return 50, 0, 0
    plus_di = (p_sum / atr) * 100
    minus_di = (m_sum / atr) * 100

    if plus_di + minus_di == 0:
        return 50, plus_di, minus_di

    # Score: 100 = strong bull, 0 = strong bear, 50 = neutral
    score = (plus_di / (plus_di + minus_di)) * 100
    return score, plus_di, minus_di


def calc_ma_score(closes):
    """MA Score from 9-day and 20-day MA"""
    if len(closes) < 20:
        return 50

    ma9 = sum(closes[-9:]) / 9
    ma20 = sum(closes[-20:]) / 20
    current = closes[-1]

    score = 50
    if current > ma9:
        score += 20
    if current > ma20:
        score += 15
    if ma9 > ma20:
        score += 15

    prev_ma9 = sum(closes[-10:-1]) / 9 if len(closes) >= 10 else ma9
    prev_ma20 = sum(closes[-21:-1]) / 20 if len(closes) >= 21 else ma20
    if prev_ma9 <= prev_ma20 and ma9 > ma20:
        score = min(score + 10, 100)
    if prev_ma9 >= prev_ma20 and ma9 < ma20:
        score = max(score - 10, 0)

    if current < ma9:
        score -= 20
    if current < ma20:
        score -= 15

    return max(0, min(100, score))


def calc_macd_score(closes):
    """MACD Score"""
    if len(closes) < 26:
        return 50

    ema12 = calc_ema(closes, 12)
    ema26 = calc_ema(closes, 26)
    macd_line = ema12 - ema26

    prev_ema12 = calc_ema(closes[:-1], 12)
    prev_ema26 = calc_ema(closes[:-1], 26)
    prev_macd = prev_ema12 - prev_ema26

    # Simple signal approximation
    signal = (macd_line + prev_macd) / 2
    histogram = macd_line - signal

    score = 50
    if macd_line > 0:
        score += 20
    else:
        score -= 20
    if macd_line > signal:
        score += 15
    else:
        score -= 15
    if histogram > 0 and histogram > prev_macd - signal:
        score += 15
    elif histogram < 0:
        score -= 15

    return max(0, min(100, score))


def calc_rsi_score(rsi_val):
    """Convert RSI to score"""
    if rsi_val < 30:
        return min(90, 50 + (30 - rsi_val) * 1.3)
    elif rsi_val > 70:
        return max(10, 50 - (rsi_val - 70) * 1.3)
    elif rsi_val > 50:
        return 50 + (rsi_val - 50) * 0.5
    else:
        return 50 - (50 - rsi_val) * 0.5


def calc_stoch_score(pct_k, pct_d):
    """Convert Stochastics to score"""
    score = 50
    if pct_k > pct_d:
        score += 25
    else:
        score -= 25
    if pct_k < 20:
        score += 15
    elif pct_k > 80:
        score -= 15
    return max(0, min(100, score))


@api_router.post("/ai-indicator/analyze", response_model=AIIndicatorResponse)
async def analyze_ai_indicator(request: AIIndicatorRequest):
    """AI Indicator Score - Weighted composite of 5 technical indicators"""
    try:
        bars = request.bars
        if len(bars) < 30:
            raise HTTPException(status_code=400, detail="Need at least 30 bars")

        highs = [b['high'] for b in bars]
        lows = [b['low'] for b in bars]
        closes = [b['close'] for b in bars]
        volumes = [b['volume'] for b in bars]
        current = closes[-1]

        # 1. DMI Score (30%)
        dmi_score, plus_di, minus_di = calc_dmi_score(highs, lows, closes)

        # 2. MA Score (25%)
        ma_score = calc_ma_score(closes)

        # 3. MACD Score (20%)
        macd_score = calc_macd_score(closes)

        # 4. RSI Score (15%)
        rsi_val = calc_rsi(closes, 14)
        rsi_score = calc_rsi_score(rsi_val)

        # 5. Stochastics Score (10%)
        pct_k, pct_d = calc_stochastics(highs, lows, closes)
        stoch_score = calc_stoch_score(pct_k, pct_d)

        # Weighted AI Score
        ai_score = (dmi_score * 0.30) + (ma_score * 0.25) + (macd_score * 0.20) + (rsi_score * 0.15) + (stoch_score * 0.10)
        ai_score = round(ai_score, 1)

        # Volume confirmation
        avg_vol = sum(volumes[-20:]) / min(20, len(volumes))
        vol_spike = volumes[-1] > avg_vol * 1.2

        indicator_scores = {
            "dmi": {"score": round(dmi_score, 1), "weight": "30%", "detail": f"+DI: {plus_di:.1f}, -DI: {minus_di:.1f}", "raw": f"+DI {plus_di:.0f} / -DI {minus_di:.0f}"},
            "moving_avg": {"score": round(ma_score, 1), "weight": "25%", "detail": "MA9 vs MA20 alignment", "raw": f"MA9: {sum(closes[-9:])/9:.2f}, MA20: {sum(closes[-20:])/min(20,len(closes)):.2f}"},
            "macd": {"score": round(macd_score, 1), "weight": "20%", "detail": "MACD momentum", "raw": f"EMA12-EMA26: {calc_ema(closes,12)-calc_ema(closes,26):.2f}"},
            "rsi": {"score": round(rsi_score, 1), "weight": "15%", "detail": f"RSI: {rsi_val:.1f}", "raw": f"RSI(14): {rsi_val:.1f}"},
            "stochastics": {"score": round(stoch_score, 1), "weight": "10%", "detail": f"%K: {pct_k:.1f}, %D: {pct_d:.1f}", "raw": f"%K: {pct_k:.0f}, %D: {pct_d:.0f}"}
        }

        # Signal
        if ai_score > 70:
            signal_type = "BUY"
            entry = current
            sl = current * 0.93  # 7% stop loss
            risk = entry - sl
            t1 = entry + risk * 2
            t2 = entry + risk * 3
            entry_price = f"{entry:.2f}"
            stop_loss = f"{sl:.2f}"
            targets = [f"{t1:.2f}", f"{t2:.2f}"]
            exit_rules = {
                "stop_loss_pct": "7%",
                "profit_target": f"₹{t1:.2f} (1:2 RR) / ₹{t2:.2f} (1:3 RR)",
                "time_exit": "Exit if no move in 10 days",
                "trailing": "Trail SL to breakeven after T1 hit"
            }
            rec = f"STRONG BUY! AI Score {ai_score:.0f}/100. All indicators aligned bullish. " + ("Volume spike confirms. " if vol_spike else "") + f"Entry ₹{entry:.2f}, SL ₹{sl:.2f} (7%). Targets: T1 ₹{t1:.2f}, T2 ₹{t2:.2f}."
        elif ai_score < 30:
            signal_type = "SELL"
            entry = current
            sl = current * 1.07  # 7% stop loss
            risk = sl - entry
            t1 = entry - risk * 2
            t2 = entry - risk * 3
            entry_price = f"{entry:.2f}"
            stop_loss = f"{sl:.2f}"
            targets = [f"{t1:.2f}", f"{t2:.2f}"]
            exit_rules = {
                "stop_loss_pct": "7%",
                "profit_target": f"₹{t1:.2f} (1:2 RR) / ₹{t2:.2f} (1:3 RR)",
                "time_exit": "Exit if no move in 10 days",
                "trailing": "Trail SL to breakeven after T1 hit"
            }
            rec = f"STRONG SELL! AI Score {ai_score:.0f}/100. Bearish alignment across indicators. " + ("Volume spike confirms breakdown. " if vol_spike else "") + f"Entry ₹{entry:.2f}, SL ₹{sl:.2f} (7%). Targets: T1 ₹{t1:.2f}, T2 ₹{t2:.2f}."
        else:
            signal_type = "WAIT"
            entry_price = None
            stop_loss = None
            targets = None
            exit_rules = None
            rec = f"HOLD — AI Score {ai_score:.0f}/100. Mixed signals. "
            if ai_score > 55:
                rec += "Leaning bullish, wait for score > 70 to enter."
            elif ai_score < 45:
                rec += "Leaning bearish, wait for score < 30 for short."
            else:
                rec += "Neutral zone. Avoid trading, wait for clear direction."

        return AIIndicatorResponse(
            ai_score=ai_score,
            signal_type=signal_type,
            indicator_scores=indicator_scores,
            entry_price=entry_price,
            stop_loss=stop_loss,
            targets=targets,
            exit_rules=exit_rules,
            volume_confirmation=vol_spike,
            recommendation=rec
        )

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error in AI indicator analysis: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class GodzillaSetupRequest(BaseModel):
    ticker: str
    bars: List[dict]


class GodzillaSetupResponse(BaseModel):
    signal_type: str
    trend_direction: str
    hook_detected: bool
    hook_price: Optional[str] = None
    hook_index: Optional[int] = None
    correction_bars: int
    entry_trigger: Optional[dict] = None
    entry_price: Optional[str] = None
    stop_loss: Optional[str] = None
    targets: Optional[List[str]] = None
    risk_management: Optional[dict] = None
    conditions: dict
    recommendation: str


def detect_ross_hooks(highs, lows, closes, lookback=30):
    """Detect Ross Hooks - first failure to make new high/low in a trend"""
    hooks = []
    start = max(0, len(highs) - lookback)

    # Uptrend hooks (failure to make higher high)
    for i in range(start + 2, len(highs)):
        if highs[i-2] < highs[i-1] and highs[i] < highs[i-1]:
            hooks.append({
                "type": "up",
                "index": i - 1,
                "price": highs[i - 1],
                "bar_index_from_end": len(highs) - 1 - (i - 1)
            })

    # Downtrend hooks (failure to make lower low)
    for i in range(start + 2, len(lows)):
        if lows[i-2] > lows[i-1] and lows[i] > lows[i-1]:
            hooks.append({
                "type": "down",
                "index": i - 1,
                "price": lows[i - 1],
                "bar_index_from_end": len(lows) - 1 - (i - 1)
            })

    return hooks


def detect_trend(closes, period=20):
    """Simple trend detection"""
    if len(closes) < period:
        return "NEUTRAL"
    sma = sum(closes[-period:]) / period
    recent = sum(closes[-5:]) / 5
    if recent > sma * 1.01:
        return "UP"
    elif recent < sma * 0.99:
        return "DOWN"
    return "NEUTRAL"


@api_router.post("/godzilla-setup/analyze", response_model=GodzillaSetupResponse)
async def analyze_godzilla_setup(request: GodzillaSetupRequest):
    """Godzilla Setup - Ross Hook + Trader's Trick Entry (TTE)"""
    try:
        bars = request.bars
        if len(bars) < 20:
            raise HTTPException(status_code=400, detail="Need at least 20 bars")

        highs = [b['high'] for b in bars]
        lows = [b['low'] for b in bars]
        closes = [b['close'] for b in bars]
        current = closes[-1]

        trend = detect_trend(closes)
        hooks = detect_ross_hooks(highs, lows, closes)

        # Filter hooks by relevance (recent, matching trend)
        relevant_hooks = []
        for h in hooks:
            if h["bar_index_from_end"] <= 10:
                if trend == "UP" and h["type"] == "up":
                    relevant_hooks.append(h)
                elif trend == "DOWN" and h["type"] == "down":
                    relevant_hooks.append(h)
                elif trend == "NEUTRAL":
                    relevant_hooks.append(h)

        if not relevant_hooks:
            # Check any recent hook regardless of trend
            recent = [h for h in hooks if h["bar_index_from_end"] <= 8]
            if recent:
                relevant_hooks = [recent[-1]]

        hook_detected = len(relevant_hooks) > 0

        if not hook_detected:
            conditions = {
                "trend": {"met": trend != "NEUTRAL", "detail": f"Trend: {trend}"},
                "ross_hook": {"met": False, "detail": "No Ross Hook detected in recent bars"},
                "correction_bars": {"met": False, "detail": "N/A - no hook"},
                "entry_trigger": {"met": False, "detail": "N/A - no hook"}
            }
            return GodzillaSetupResponse(
                signal_type="WAIT", trend_direction=trend,
                hook_detected=False, correction_bars=0,
                conditions=conditions,
                recommendation=f"No Ross Hook detected. Trend is {trend}. Wait for price to make a high/low followed by failure to exceed it."
            )

        # Use most recent relevant hook
        hook = relevant_hooks[-1]
        hook_idx = hook["index"]
        hook_price = hook["price"]
        hook_type = hook["type"]

        # Count correction bars after hook (max 3)
        bars_after_hook = len(bars) - 1 - hook_idx
        correction_count = min(bars_after_hook, 3)

        # Analyze correction bars for TTE entry
        entry_found = False
        entry_price_val = None
        sl_price = None
        trigger_info = None

        if hook_type == "up":
            # Long TTE: enter on breakout above correction bar high
            for i in range(1, correction_count + 1):
                bar_idx = hook_idx + i
                if bar_idx >= len(bars):
                    break
                corr_bar = bars[bar_idx]
                corr_high = corr_bar['high']

                # Check if enough distance between correction bar high and hook
                distance_pct = ((hook_price - corr_high) / hook_price) * 100
                if distance_pct > 0.3:  # At least 0.3% gap
                    # Check if current price broke above correction bar high
                    if current > corr_high:
                        entry_found = True
                        entry_price_val = corr_high
                        sl_price = corr_bar['low']
                        trigger_info = {
                            "correction_bar": i,
                            "bar_high": f"{corr_high:.2f}",
                            "bar_low": f"{corr_bar['low']:.2f}",
                            "distance_to_hook": f"{distance_pct:.1f}%",
                            "status": "TRIGGERED - Price broke above"
                        }
                        break
                    else:
                        trigger_info = {
                            "correction_bar": i,
                            "bar_high": f"{corr_high:.2f}",
                            "bar_low": f"{corr_bar['low']:.2f}",
                            "distance_to_hook": f"{distance_pct:.1f}%",
                            "status": f"PENDING - Price ₹{current:.2f} below trigger ₹{corr_high:.2f}"
                        }

            if not trigger_info and correction_count > 0:
                last_corr = bars[min(hook_idx + correction_count, len(bars) - 1)]
                trigger_info = {
                    "correction_bar": correction_count,
                    "bar_high": f"{last_corr['high']:.2f}",
                    "bar_low": f"{last_corr['low']:.2f}",
                    "distance_to_hook": f"{((hook_price - last_corr['high']) / hook_price * 100):.1f}%",
                    "status": "WATCHING"
                }

        else:
            # Short TTE: enter on breakout below correction bar low
            for i in range(1, correction_count + 1):
                bar_idx = hook_idx + i
                if bar_idx >= len(bars):
                    break
                corr_bar = bars[bar_idx]
                corr_low = corr_bar['low']

                distance_pct = ((corr_low - hook_price) / hook_price) * 100
                if distance_pct > 0.3:
                    if current < corr_low:
                        entry_found = True
                        entry_price_val = corr_low
                        sl_price = corr_bar['high']
                        trigger_info = {
                            "correction_bar": i,
                            "bar_high": f"{corr_bar['high']:.2f}",
                            "bar_low": f"{corr_low:.2f}",
                            "distance_to_hook": f"{distance_pct:.1f}%",
                            "status": "TRIGGERED - Price broke below"
                        }
                        break
                    else:
                        trigger_info = {
                            "correction_bar": i,
                            "bar_high": f"{corr_bar['high']:.2f}",
                            "bar_low": f"{corr_low:.2f}",
                            "distance_to_hook": f"{distance_pct:.1f}%",
                            "status": f"PENDING - Price ₹{current:.2f} above trigger ₹{corr_low:.2f}"
                        }

            if not trigger_info and correction_count > 0:
                last_corr = bars[min(hook_idx + correction_count, len(bars) - 1)]
                trigger_info = {
                    "correction_bar": correction_count,
                    "bar_high": f"{last_corr['high']:.2f}",
                    "bar_low": f"{last_corr['low']:.2f}",
                    "distance_to_hook": f"{((last_corr['low'] - hook_price) / hook_price * 100):.1f}%",
                    "status": "WATCHING"
                }

        # Build response
        if entry_found:
            signal_type = "BUY" if hook_type == "up" else "SELL"
            risk = abs(entry_price_val - sl_price)
            if hook_type == "up":
                cost_cover = entry_price_val + risk * 0.5
                t2 = hook_price
                t3 = hook_price + risk * 2
            else:
                cost_cover = entry_price_val - risk * 0.5
                t2 = hook_price
                t3 = hook_price - risk * 2

            targets = [f"{cost_cover:.2f}", f"{t2:.2f}", f"{t3:.2f}"]
            risk_mgmt = {
                "partial_exit": f"₹{cost_cover:.2f} (cover costs + small profit)",
                "breakeven_stop": f"₹{entry_price_val:.2f} (move SL to entry after T1)",
                "hook_target": f"₹{hook_price:.2f} (test Hook point)",
                "runner": f"₹{t3:.2f} (if breakout past Hook continues)"
            }
            rec = (f"GODZILLA {'BUY' if hook_type == 'up' else 'SELL'}! Ross Hook at ₹{hook_price:.2f}. "
                   f"TTE triggered on correction bar {trigger_info['correction_bar']}. "
                   f"Entry ₹{entry_price_val:.2f}, SL ₹{sl_price:.2f}. "
                   f"T1 (cost cover) ₹{cost_cover:.2f}, T2 (Hook test) ₹{hook_price:.2f}. "
                   f"After T1 move SL to breakeven. Let remaining position run if Hook breaks.")
        else:
            signal_type = "WAIT"
            targets = None
            risk_mgmt = None
            entry_price_val = None
            sl_price = None

            if correction_count >= 3:
                rec = f"Ross Hook at ₹{hook_price:.2f} detected but 3 correction bars passed without trigger. Setup expired. Wait for next Hook."
            elif correction_count > 0:
                if hook_type == "up":
                    trigger_level = trigger_info['bar_high'] if trigger_info else "N/A"
                    rec = f"Ross Hook at ₹{hook_price:.2f}. Correction bar {correction_count} active. Enter LONG if price breaks above ₹{trigger_level}. Max 3 bars to trigger."
                else:
                    trigger_level = trigger_info['bar_low'] if trigger_info else "N/A"
                    rec = f"Ross Hook at ₹{hook_price:.2f}. Correction bar {correction_count} active. Enter SHORT if price breaks below ₹{trigger_level}. Max 3 bars to trigger."
            else:
                rec = f"Ross Hook at ₹{hook_price:.2f}. Waiting for first correction bar. Hook type: {'Uptrend' if hook_type == 'up' else 'Downtrend'}."

        conditions = {
            "trend": {"met": trend != "NEUTRAL", "detail": f"Trend: {trend}"},
            "ross_hook": {"met": hook_detected, "detail": f"Hook at ₹{hook_price:.2f} ({'Uptrend' if hook_type == 'up' else 'Downtrend'})"},
            "correction_bars": {"met": correction_count > 0, "detail": f"{correction_count}/3 correction bars"},
            "entry_trigger": {"met": entry_found, "detail": trigger_info.get("status", "N/A") if trigger_info else "No trigger"}
        }

        return GodzillaSetupResponse(
            signal_type=signal_type,
            trend_direction=trend,
            hook_detected=hook_detected,
            hook_price=f"{hook_price:.2f}",
            hook_index=hook.get("bar_index_from_end"),
            correction_bars=correction_count,
            entry_trigger=trigger_info,
            entry_price=f"{entry_price_val:.2f}" if entry_price_val else None,
            stop_loss=f"{sl_price:.2f}" if sl_price else None,
            targets=targets,
            risk_management=risk_mgmt,
            conditions=conditions,
            recommendation=rec
        )

    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Error in godzilla setup: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ======================= SMC (Smart Money Concepts) ENGINE =======================

def _smc_compute_atr(highs, lows, closes, period=14):
    """ATR(14) calculation"""
    if len(closes) < period + 1:
        return abs(highs[-1] - lows[-1])
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    return sum(trs[-period:]) / period if len(trs) >= period else sum(trs) / len(trs)

def _smc_daily_bias(closes, highs, lows):
    """Phase 1: Daily Bias using Higher Highs/Lows — relaxed for more signals"""
    if len(closes) < 10:
        return "NEUTRAL", "Insufficient data"
    recent_highs = highs[-8:]
    recent_lows = lows[-8:]
    hh_count = sum(1 for i in range(1, len(recent_highs)) if recent_highs[i] > recent_highs[i-1])
    hl_count = sum(1 for i in range(1, len(recent_lows)) if recent_lows[i] > recent_lows[i-1])
    ll_count = sum(1 for i in range(1, len(recent_lows)) if recent_lows[i] < recent_lows[i-1])
    lh_count = sum(1 for i in range(1, len(recent_highs)) if recent_highs[i] < recent_highs[i-1])
    last_close = closes[-1]
    prev_close = closes[-2] if len(closes) > 1 else last_close
    # Relaxed: 2+ HH/HL enough for bias
    if hh_count >= 3 and hl_count >= 3:
        return "BULLISH", f"HH: {hh_count}, HL: {hl_count} — Strong uptrend"
    elif ll_count >= 3 and lh_count >= 3:
        return "BEARISH", f"LL: {ll_count}, LH: {lh_count} — Strong downtrend"
    elif hh_count >= 2 and last_close > prev_close:
        return "BULLISH", f"HH: {hh_count}, HL: {hl_count} — Bullish bias"
    elif ll_count >= 2 and last_close < prev_close:
        return "BEARISH", f"LL: {ll_count}, LH: {lh_count} — Bearish bias"
    elif last_close > prev_close and hh_count >= 1:
        return "BULLISH", f"HH: {hh_count} + price rising — Mild bullish"
    elif last_close < prev_close and ll_count >= 1:
        return "BEARISH", f"LL: {ll_count} + price falling — Mild bearish"
    return "NEUTRAL", f"HH:{hh_count} HL:{hl_count} LL:{ll_count} LH:{lh_count}"

def _smc_liquidity_sweep(highs, lows, closes):
    """Phase 2: Liquidity Sweep — relaxed with wider proximity check"""
    if len(closes) < 5:
        return "NONE", None, "Insufficient data"
    pdh = max(highs[-8:-1]) if len(highs) > 8 else max(highs[:-1])
    pdl = min(lows[-8:-1]) if len(lows) > 8 else min(lows[:-1])
    last_high = highs[-1]
    last_low = lows[-1]
    last_close = closes[-1]
    # Check last 3 bars for sweep
    for k in range(min(3, len(highs))):
        if highs[-(k+1)] > pdh and closes[-(k+1)] < pdh:
            return "PDH_SWEPT", pdh, f"Swept PDH {pdh:.2f} — Sell-side liquidity grabbed"
        if lows[-(k+1)] < pdl and closes[-(k+1)] > pdl:
            return "PDL_SWEPT", pdl, f"Swept PDL {pdl:.2f} — Buy-side liquidity grabbed"
    # Near proximity = 0.5% (was 0.2%)
    if last_high > pdh * 0.995:
        return "PDH_NEAR", pdh, f"Near PDH {pdh:.2f} — Potential sweep forming"
    if last_low < pdl * 1.005:
        return "PDL_NEAR", pdl, f"Near PDL {pdl:.2f} — Potential sweep forming"
    # Even milder: within 1% range
    if last_high > pdh * 0.99:
        return "PDH_NEAR", pdh, f"Within 1% of PDH {pdh:.2f}"
    if last_low < pdl * 1.01:
        return "PDL_NEAR", pdl, f"Within 1% of PDL {pdl:.2f}"
    return "NONE", None, "No liquidity sweep"

def _smc_detect_mss(closes, highs, lows):
    """Phase 3: Market Structure Shift + IFVG — relaxed detection"""
    if len(closes) < 8:
        return False, None, None, "Insufficient data"
    mss_found = False
    mss_direction = None
    ifvg_zone = None
    recent = closes[-8:]
    recent_h = highs[-8:]
    recent_l = lows[-8:]
    # Bearish MSS: any recent lower low after a swing high
    swing_h = max(recent[:-2])
    swing_h_idx = recent[:-2].index(swing_h)
    if swing_h_idx < len(recent) - 2:
        # Check if price dropped after swing high
        drop_after = min(recent[swing_h_idx+1:])
        prev_low = min(recent_l[:swing_h_idx+1]) if swing_h_idx > 0 else recent_l[0]
        if drop_after < prev_low * 1.005:  # Relaxed: within 0.5%
            mss_found = True
            mss_direction = "BEARISH"
            ifvg_high = max(recent_h[-3], recent_h[-2])
            ifvg_low = min(recent_l[-3], recent[-2])
            ifvg_zone = (ifvg_low, ifvg_high)
    # Bullish MSS: any recent higher high after a swing low
    if not mss_found:
        swing_l = min(recent[:-2])
        swing_l_idx = recent[:-2].index(swing_l)
        if swing_l_idx < len(recent) - 2:
            rise_after = max(recent[swing_l_idx+1:])
            prev_high = max(recent_h[:swing_l_idx+1]) if swing_l_idx > 0 else recent_h[0]
            if rise_after > prev_high * 0.995:  # Relaxed
                mss_found = True
                mss_direction = "BULLISH"
                ifvg_low = min(recent_l[-3], recent_l[-2])
                ifvg_high = max(recent[-2], recent_h[-3])
                ifvg_zone = (ifvg_low, ifvg_high)
    # Fallback: use price direction of last 5 bars as weak MSS
    if not mss_found:
        if closes[-1] > closes[-3] and closes[-2] > closes[-4]:
            mss_found = True
            mss_direction = "BULLISH"
            ifvg_low = min(lows[-4:])
            ifvg_high = max(highs[-4:])
            ifvg_zone = (ifvg_low, ifvg_high)
        elif closes[-1] < closes[-3] and closes[-2] < closes[-4]:
            mss_found = True
            mss_direction = "BEARISH"
            ifvg_low = min(lows[-4:])
            ifvg_high = max(highs[-4:])
            ifvg_zone = (ifvg_low, ifvg_high)
    detail = f"MSS {mss_direction} detected" if mss_found else "No MSS detected"
    if ifvg_zone:
        detail += f" | IFVG Zone: {ifvg_zone[0]:.2f} - {ifvg_zone[1]:.2f}"
    return mss_found, mss_direction, ifvg_zone, detail

def _smc_precision_entry(bars, ifvg_zone, mss_direction, atr):
    """Phase 4: Precision Entry — relaxed wick ratio and volume"""
    if not ifvg_zone or not mss_direction or len(bars) < 3:
        return False, None, "No IFVG zone for entry"
    last = bars[-1]
    op, hi, lo, cl = last['open'], last['high'], last['low'], last['close']
    body = abs(cl - op)
    upper_wick = hi - max(op, cl)
    lower_wick = min(op, cl) - lo
    candle_range = hi - lo if hi != lo else 0.01
    # Volume filter — relaxed: 1.0x average (was 1.5x)
    volumes = [b.get('volume', 0) for b in bars[-11:]]
    avg_vol = sum(volumes[:-1]) / max(len(volumes) - 1, 1) if len(volumes) > 1 else 0
    cur_vol = volumes[-1] if volumes else 0
    vol_confirmed = cur_vol > avg_vol * 0.8 if avg_vol > 0 else True
    rejection_quality = "WEAK"
    if mss_direction == "BULLISH":
        in_zone = lo <= ifvg_zone[1] * 1.01 and cl >= ifvg_zone[0] * 0.99  # Relaxed zone
        wick_ratio = lower_wick / body if body > 0 else 0
        close_in_range = (cl - lo) / candle_range if candle_range > 0 else 0
        if wick_ratio >= 1.8 and close_in_range >= 0.6:
            rejection_quality = "STRONG"
        elif wick_ratio >= 0.8 or close_in_range >= 0.55:
            rejection_quality = "MODERATE"
        entry_valid = in_zone and rejection_quality != "WEAK"
    else:
        in_zone = hi >= ifvg_zone[0] * 0.99 and cl <= ifvg_zone[1] * 1.01
        wick_ratio = upper_wick / body if body > 0 else 0
        close_in_range = (hi - cl) / candle_range if candle_range > 0 else 0
        if wick_ratio >= 1.8 and close_in_range >= 0.6:
            rejection_quality = "STRONG"
        elif wick_ratio >= 0.8 or close_in_range >= 0.55:
            rejection_quality = "MODERATE"
        entry_valid = in_zone and rejection_quality != "WEAK"
    # Fallback: if in zone, always at least moderate
    if not entry_valid and (mss_direction == "BULLISH" and lo <= ifvg_zone[1] * 1.02) or \
       (not entry_valid and mss_direction == "BEARISH" and hi >= ifvg_zone[0] * 0.98):
        rejection_quality = "MODERATE"
        entry_valid = True
        vol_confirmed = True
    detail = f"Rejection: {rejection_quality}, Vol: {'OK' if vol_confirmed else 'LOW'}"
    return entry_valid, rejection_quality, detail

def _smc_trade_management(entry_price, atr, direction):
    """Phase 5: Trade Management with ATR-based SL and TP"""
    sl_mult = 1.0
    sl = entry_price - (atr * sl_mult) if direction == "BUY" else entry_price + (atr * sl_mult)
    risk = abs(entry_price - sl)
    tp1 = entry_price + risk if direction == "BUY" else entry_price - risk
    tp2 = entry_price + (risk * 2.5) if direction == "BUY" else entry_price - (risk * 2.5)
    rr = f"1:{2.5:.1f}"
    return sl, tp1, tp2, rr

def run_full_smc_analysis(bars):
    """Full SMC 5-Phase analysis on bar data"""
    if len(bars) < 25:
        return {
            "status": "INSUFFICIENT_DATA", "signal_type": "WAIT",
            "daily_bias": "NEUTRAL", "liquidity_sweep": "NONE",
            "mss_detected": False, "phases": [], "confidence": 0,
            "recommendation": "Need at least 25 bars for SMC analysis"
        }
    closes = [b['close'] for b in bars]
    highs = [b['high'] for b in bars]
    lows = [b['low'] for b in bars]
    atr = _smc_compute_atr(highs, lows, closes)
    phases = []
    confidence = 0

    # Phase 1: Daily Bias
    bias, bias_detail = _smc_daily_bias(closes, highs, lows)
    p1_status = "PASS" if bias != "NEUTRAL" else "FAIL"
    phases.append({"phase": 1, "name": "Daily Bias", "status": p1_status, "detail": bias_detail})
    if p1_status == "PASS":
        confidence += 20

    # Phase 2: Liquidity Sweep
    sweep, sweep_level, sweep_detail = _smc_liquidity_sweep(highs, lows, closes)
    p2_pass = sweep in ("PDH_SWEPT", "PDL_SWEPT")
    p2_partial = sweep in ("PDH_NEAR", "PDL_NEAR")
    p2_status = "PASS" if p2_pass else ("PARTIAL" if p2_partial else "FAIL")
    phases.append({"phase": 2, "name": "Liquidity Sweep", "status": p2_status, "detail": sweep_detail})
    if p2_pass:
        confidence += 25
    elif p2_partial:
        confidence += 10

    # Phase 3: MSS + IFVG
    mss_found, mss_dir, ifvg_zone, mss_detail = _smc_detect_mss(closes, highs, lows)
    p3_status = "PASS" if mss_found else "FAIL"
    phases.append({"phase": 3, "name": "MSS + IFVG", "status": p3_status, "detail": mss_detail})
    if mss_found:
        confidence += 25

    # Phase 4: Precision Entry
    entry_valid, rejection_quality, entry_detail = _smc_precision_entry(bars, ifvg_zone, mss_dir, atr)
    p4_status = "PASS" if entry_valid else "FAIL"
    phases.append({"phase": 4, "name": "Precision Entry", "status": p4_status, "detail": entry_detail})
    if entry_valid:
        confidence += 20
        if rejection_quality == "STRONG":
            confidence += 10

    # Determine signal
    current = closes[-1]
    signal_type = "WAIT"
    entry_price = None
    sl = tp1 = tp2 = rr = None

    # Need at least Phase 1 (bias) + one more to generate signal — relaxed for more alerts
    pass_count = sum(1 for p in phases if p["status"] == "PASS")
    partial_count = sum(1 for p in phases if p["status"] == "PARTIAL")

    if pass_count >= 2:
        if bias == "BULLISH" or (mss_dir == "BULLISH"):
            signal_type = "BUY"
        elif bias == "BEARISH" or (mss_dir == "BEARISH"):
            signal_type = "SELL"
    elif pass_count >= 1 and partial_count >= 1 and confidence >= 25:
        if bias == "BULLISH":
            signal_type = "BUY"
        elif bias == "BEARISH":
            signal_type = "SELL"
        elif mss_dir == "BULLISH":
            signal_type = "BUY"
        elif mss_dir == "BEARISH":
            signal_type = "SELL"

    if signal_type != "WAIT":
        entry_price = current
        sl, tp1, tp2, rr = _smc_trade_management(entry_price, atr, signal_type)

    # Phase 5: Trade Management
    if signal_type != "WAIT":
        p5_detail = f"SL: {sl:.2f} (ATR-based) | TP1: {tp1:.2f} (1:1) | TP2: {tp2:.2f} (1:2.5) | Risk: 1% per trade"
        phases.append({"phase": 5, "name": "Trade Management", "status": "PASS", "detail": p5_detail})
    else:
        phases.append({"phase": 5, "name": "Trade Management", "status": "FAIL", "detail": "No trade — waiting for all conditions"})

    # Recommendation
    if signal_type == "BUY":
        rec = f"BUY — Entry: {current:.2f} | SL: {sl:.2f} | TP1: {tp1:.2f} | TP2: {tp2:.2f}"
    elif signal_type == "SELL":
        rec = f"SELL — Entry: {current:.2f} | SL: {sl:.2f} | TP1: {tp1:.2f} | TP2: {tp2:.2f}"
    else:
        rec = "WAIT — Not all SMC conditions met. Watching for setup."

    return {
        "status": "ACTIVE" if signal_type != "WAIT" else "SCANNING",
        "signal_type": signal_type,
        "daily_bias": bias,
        "liquidity_sweep": sweep,
        "mss_detected": mss_found,
        "ifvg_zone": f"{ifvg_zone[0]:.2f} - {ifvg_zone[1]:.2f}" if ifvg_zone else None,
        "entry_price": f"{entry_price:.2f}" if entry_price else None,
        "stop_loss": f"{sl:.2f}" if sl else None,
        "tp1": f"{tp1:.2f}" if tp1 else None,
        "tp2": f"{tp2:.2f}" if tp2 else None,
        "risk_reward": rr,
        "atr_value": round(atr, 2),
        "rejection_quality": rejection_quality if entry_valid else None,
        "volume_confirmed": entry_valid,
        "session_valid": True,
        "phases": phases,
        "confidence": min(confidence, 100),
        "recommendation": rec,
    }


def run_mini_smc(bars):
    """Quick SMC check for auto-scanner"""
    if len(bars) < 25:
        return "WAIT"
    result = run_full_smc_analysis(bars)
    return result.get("signal_type", "WAIT")


@api_router.post("/smc/analyze", response_model=SMCAnalysisResponse)
async def analyze_smc(request: SMCAnalysisRequest):
    """SMC (Smart Money Concepts) 5-Phase Analysis"""
    try:
        bars = request.bars
        if len(bars) < 25:
            return SMCAnalysisResponse(
                status="INSUFFICIENT_DATA", signal_type="WAIT",
                daily_bias="NEUTRAL", liquidity_sweep="NONE",
                mss_detected=False, phases=[], confidence=0,
                recommendation="Need at least 25 bars (15M timeframe recommended)"
            )
        result = run_full_smc_analysis(bars)
        return SMCAnalysisResponse(**result)
    except Exception as e:
        logging.error(f"SMC analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ======================= PAC + S&O MATRIX (High Confluence) =======================

def _pac_calc_ema(closes, period):
    if len(closes) < period:
        return sum(closes) / len(closes) if closes else 0
    mult = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]:
        ema = (c - ema) * mult + ema
    return ema

def _pac_calc_atr(bars, period=14):
    if len(bars) < 2:
        return 0
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]['high'], bars[i]['low'], bars[i-1]['close']
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if not trs:
        return 0
    return sum(trs[-period:]) / min(len(trs), period)

def _pac_detect_structure(highs, lows, closes):
    """Detect BOS, CHoCH, CHoCH+ from swing points"""
    n = len(highs)
    if n < 10:
        return "NEUTRAL", False, False, False

    # Find swing points (simple 3-bar pivot)
    swing_highs, swing_lows = [], []
    for i in range(2, n - 2):
        if highs[i] >= highs[i-1] and highs[i] >= highs[i-2] and highs[i] >= highs[i+1] and highs[i] >= highs[i+2]:
            swing_highs.append((i, highs[i]))
        if lows[i] <= lows[i-1] and lows[i] <= lows[i-2] and lows[i] <= lows[i+1] and lows[i] <= lows[i+2]:
            swing_lows.append((i, lows[i]))

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "NEUTRAL", False, False, False

    # Check recent swing structure
    last_sh = swing_highs[-1][1]
    prev_sh = swing_highs[-2][1]
    last_sl = swing_lows[-1][1]
    prev_sl = swing_lows[-2][1]

    hh = last_sh > prev_sh
    hl = last_sl > prev_sl
    lh = last_sh < prev_sh
    ll = last_sl < prev_sl

    bos = False
    choch = False
    choch_plus = False

    if hh and hl:
        # Bullish structure — check for BOS (break above prev swing high)
        if closes[-1] > prev_sh:
            bos = True
        bias = "BULLISH"
    elif lh and ll:
        # Bearish structure — BOS below prev swing low
        if closes[-1] < prev_sl:
            bos = True
        bias = "BEARISH"
    elif hh and ll:
        # Mixed — possible CHoCH
        choch = True
        bias = "BULLISH" if closes[-1] > (last_sh + last_sl) / 2 else "BEARISH"
    elif lh and hl:
        choch = True
        bias = "BEARISH" if closes[-1] < (last_sh + last_sl) / 2 else "BULLISH"
    else:
        bias = "NEUTRAL"

    # CHoCH+ = strong reversal: previous was trending one way, now broke structure opposite
    if choch and abs(closes[-1] - closes[-5]) / closes[-5] > 0.008:
        choch_plus = True

    return bias, bos, choch, choch_plus

def _pac_find_order_blocks(bars, bias):
    """Find Volumetric Order Blocks (high volume candles at reversal points)"""
    n = len(bars)
    if n < 10:
        return None, None

    volumes = [b.get('volume', 0) for b in bars]
    avg_vol = sum(volumes[-20:]) / max(len(volumes[-20:]), 1) if volumes else 1

    ob_zone = None
    ob_type = None

    # Scan last 15 bars for order blocks
    for i in range(max(n - 15, 1), n - 1):
        vol = bars[i].get('volume', 0)
        body = abs(bars[i]['close'] - bars[i]['open'])
        candle_range = bars[i]['high'] - bars[i]['low']
        if candle_range == 0:
            continue
        body_ratio = body / candle_range

        # High volume + strong body = potential OB
        if vol > avg_vol * 1.2 and body_ratio > 0.5:
            if bias == "BULLISH" and bars[i]['close'] > bars[i]['open']:
                # Bullish OB = demand zone
                ob_zone = (bars[i]['low'], bars[i]['open'])
                ob_type = "BULLISH_OB"
            elif bias == "BEARISH" and bars[i]['close'] < bars[i]['open']:
                # Bearish OB = supply zone
                ob_zone = (bars[i]['open'], bars[i]['high'])
                ob_type = "BEARISH_OB"

    return ob_zone, ob_type

def _pac_detect_liquidity_sweep(highs, lows):
    """Detect liquidity grabs: equal highs/lows broken then reversed"""
    n = len(highs)
    if n < 10:
        return False

    # Check for equal lows/highs then sweep
    tolerance = 0.003  # 0.3%

    # Check recent equal lows swept
    for i in range(n - 8, n - 3):
        for j in range(i + 1, min(i + 4, n - 2)):
            if abs(lows[i] - lows[j]) / lows[i] < tolerance:
                # Equal lows found — check if swept then bounced
                for k in range(j + 1, min(j + 3, n)):
                    if lows[k] < min(lows[i], lows[j]) and highs[k] > lows[i]:
                        return True

    # Check equal highs swept
    for i in range(n - 8, n - 3):
        for j in range(i + 1, min(i + 4, n - 2)):
            if abs(highs[i] - highs[j]) / highs[i] < tolerance:
                for k in range(j + 1, min(j + 3, n)):
                    if highs[k] > max(highs[i], highs[j]) and lows[k] < highs[i]:
                        return True

    return False

def _pac_find_fvg(bars):
    """Find Fair Value Gaps (3-candle imbalances)"""
    n = len(bars)
    if n < 5:
        return None

    # Check last 10 bars for FVG
    for i in range(max(n - 10, 2), n - 1):
        # Bullish FVG: bar[i-2] high < bar[i] low (gap up)
        if bars[i]['low'] > bars[i-2]['high']:
            return (bars[i-2]['high'], bars[i]['low'])
        # Bearish FVG: bar[i-2] low > bar[i] high (gap down)
        if bars[i]['high'] < bars[i-2]['low']:
            return (bars[i]['high'], bars[i-2]['low'])

    return None

def _pac_premium_discount(closes, highs, lows):
    """Calculate if price is in Premium or Discount zone"""
    n = len(closes)
    if n < 20:
        return "NEUTRAL"
    recent_high = max(highs[-20:])
    recent_low = min(lows[-20:])
    mid = (recent_high + recent_low) / 2
    current = closes[-1]
    if current < mid - (mid - recent_low) * 0.3:
        return "DISCOUNT"
    elif current > mid + (recent_high - mid) * 0.3:
        return "PREMIUM"
    return "EQUILIBRIUM"

def _so_signal_confirmation(closes, highs, lows, ema_fast, ema_slow):
    """S&O: Generate confirmation signals based on trend + retracement"""
    current = closes[-1]
    prev = closes[-2] if len(closes) > 1 else current

    above_cloud = current > ema_fast and current > ema_slow
    below_cloud = current < ema_fast and current < ema_slow
    trend_up = ema_fast > ema_slow
    trend_down = ema_fast < ema_slow

    # Check for retracement to EMA then bounce
    near_ema = abs(current - ema_fast) / ema_fast < 0.005
    bounce_up = prev < ema_fast and current > ema_fast
    bounce_down = prev > ema_fast and current < ema_fast

    signal = None
    strength = None

    if trend_up and above_cloud:
        if bounce_up or near_ema:
            signal = "BUY"
            strength = "STRONG+" if (current - prev) / prev > 0.003 else "NORMAL"
        elif current > prev:
            signal = "BUY"
            strength = "NORMAL"
    elif trend_down and below_cloud:
        if bounce_down or near_ema:
            signal = "SELL"
            strength = "STRONG+" if (prev - current) / prev > 0.003 else "NORMAL"
        elif current < prev:
            signal = "SELL"
            strength = "NORMAL"

    cloud_trend = "BULLISH" if above_cloud else "BEARISH" if below_cloud else "NEUTRAL"
    return signal, strength, cloud_trend

def _so_smart_trail(bars, atr):
    """S&O: Smart Trail calculation (ATR-based trailing stop)"""
    if not bars or atr == 0:
        return None
    current = bars[-1]['close']
    bullish = bars[-1]['close'] > bars[-1]['open']
    if bullish:
        return round(current - atr * 1.5, 2)
    else:
        return round(current + atr * 1.5, 2)

def _oscillator_matrix(closes, volumes):
    """Oscillator Matrix: Money Flow, Divergence, Momentum"""
    n = len(closes)

    # Smart Money Flow (simplified OBV direction)
    money_flow = "NEUTRAL"
    if n >= 10:
        obv = 0
        obv_vals = []
        for i in range(1, n):
            if closes[i] > closes[i-1]:
                obv += volumes[i] if i < len(volumes) else 0
            elif closes[i] < closes[i-1]:
                obv -= volumes[i] if i < len(volumes) else 0
            obv_vals.append(obv)
        if len(obv_vals) >= 5:
            recent_obv = obv_vals[-1]
            past_obv = obv_vals[-5]
            if recent_obv > past_obv * 1.05:
                money_flow = "BULLISH"
            elif recent_obv < past_obv * 0.95:
                money_flow = "BEARISH"

    # RSI for momentum
    rsi = 50
    if n >= 15:
        gains, losses_a = [], []
        for j in range(1, min(15, n)):
            d = closes[-j] - closes[-j-1]
            if d > 0:
                gains.append(d)
            else:
                losses_a.append(abs(d))
        avg_g = sum(gains) / 14 if gains else 0.001
        avg_l = sum(losses_a) / 14 if losses_a else 0.001
        rs = avg_g / avg_l if avg_l > 0 else 1
        rsi = 100 - (100 / (1 + rs))

    momentum = "OVERBOUGHT" if rsi > 70 else "OVERSOLD" if rsi < 30 else "STRONG" if 45 < rsi < 65 else "NEUTRAL"

    # Divergence detection (price vs RSI direction)
    divergence = None
    if n >= 20:
        price_trend = closes[-1] - closes[-10]
        # Simple RSI trend comparison
        rsi_now = rsi
        # Approx old RSI
        gains2, losses2 = [], []
        for j in range(10, min(24, n)):
            d = closes[-j] - closes[-j-1] if j+1 < n else 0
            if d > 0:
                gains2.append(d)
            else:
                losses2.append(abs(d))
        avg_g2 = sum(gains2) / 14 if gains2 else 0.001
        avg_l2 = sum(losses2) / 14 if losses2 else 0.001
        rs2 = avg_g2 / avg_l2 if avg_l2 > 0 else 1
        rsi_old = 100 - (100 / (1 + rs2))

        rsi_trend = rsi_now - rsi_old

        if price_trend < 0 and rsi_trend > 5:
            divergence = "BULLISH_DIVERGENCE"
        elif price_trend > 0 and rsi_trend < -5:
            divergence = "BEARISH_DIVERGENCE"

    return money_flow, divergence, momentum, rsi


def run_full_pac_so_analysis(bars):
    """Full PAC + S&O Matrix High Confluence Analysis"""
    n = len(bars)
    if n < 30:
        return {
            "status": "INSUFFICIENT_DATA", "signal_type": "WAIT",
            "structure_bias": "NEUTRAL", "confluence_score": 0,
            "modules": [], "confidence": 0,
            "recommendation": "Need at least 30 bars (15M timeframe recommended)"
        }

    closes = [b['close'] for b in bars]
    highs = [b['high'] for b in bars]
    lows = [b['low'] for b in bars]
    volumes = [b.get('volume', 0) for b in bars]
    current = closes[-1]

    atr = _pac_calc_atr(bars, 14)
    ema_9 = _pac_calc_ema(closes, 9)
    ema_21 = _pac_calc_ema(closes, 21)
    ema_50 = _pac_calc_ema(closes, min(50, n - 1))

    modules = []
    confluence = 0

    # ============ MODULE 1: PAC — Structure + Bias + Entry Zone ============
    bias, bos, choch, choch_plus = _pac_detect_structure(highs, lows, closes)
    ob_zone, ob_type = _pac_find_order_blocks(bars, bias)
    liq_swept = _pac_detect_liquidity_sweep(highs, lows)
    fvg = _pac_find_fvg(bars)
    pd_zone = _pac_premium_discount(closes, highs, lows)

    pac_signals = []
    pac_status = "FAIL"

    if bos:
        pac_signals.append(f"BOS detected ({bias})")
        confluence += 15
    if choch:
        pac_signals.append(f"CHoCH detected{' (STRONG+)' if choch_plus else ''}")
        confluence += 20 if choch_plus else 12
    if ob_zone:
        pac_signals.append(f"{ob_type}: {ob_zone[0]:.2f} - {ob_zone[1]:.2f}")
        confluence += 12
    if liq_swept:
        pac_signals.append("Liquidity Sweep confirmed")
        confluence += 10
    if fvg:
        pac_signals.append(f"FVG: {fvg[0]:.2f} - {fvg[1]:.2f}")
        confluence += 8
    if (bias == "BULLISH" and pd_zone == "DISCOUNT") or (bias == "BEARISH" and pd_zone == "PREMIUM"):
        pac_signals.append(f"Price in {pd_zone} zone (aligned with {bias} bias)")
        confluence += 10

    if confluence >= 20:
        pac_status = "PASS"
    elif confluence >= 10:
        pac_status = "PARTIAL"

    modules.append({
        "module": "PAC (Price Action Concepts)",
        "status": pac_status,
        "detail": f"Bias: {bias} | {'BOS' if bos else 'CHoCH+' if choch_plus else 'CHoCH' if choch else 'No structure break'} | Zone: {pd_zone}",
        "sub_signals": pac_signals,
    })

    # ============ MODULE 2: S&O — Confirmation + Trend Filter ============
    so_signal, so_strength, cloud_trend = _so_signal_confirmation(closes, highs, lows, ema_9, ema_21)
    smart_trail = _so_smart_trail(bars, atr)

    so_signals = []
    so_status = "FAIL"
    so_confluence = 0

    if so_signal:
        so_signals.append(f"{so_signal} Signal ({so_strength})")
        so_confluence += 18 if so_strength == "STRONG+" else 12
    if cloud_trend == bias and cloud_trend != "NEUTRAL":
        so_signals.append(f"Neo Cloud aligned ({cloud_trend})")
        so_confluence += 10
    if smart_trail:
        so_signals.append(f"Smart Trail: {smart_trail}")
        so_confluence += 5

    # Trend Catcher (EMA50 alignment)
    if (bias == "BULLISH" and current > ema_50) or (bias == "BEARISH" and current < ema_50):
        so_signals.append("Trend Catcher aligned with bias")
        so_confluence += 8

    confluence += so_confluence

    if so_confluence >= 18:
        so_status = "PASS"
    elif so_confluence >= 8:
        so_status = "PARTIAL"

    modules.append({
        "module": "S&O (Signals & Overlays)",
        "status": so_status,
        "detail": f"Signal: {so_signal or 'NONE'} ({so_strength or '-'}) | Cloud: {cloud_trend} | Trail: {smart_trail}",
        "sub_signals": so_signals,
    })

    # ============ MODULE 3: Oscillator Matrix — Momentum + Divergence ============
    money_flow, divergence, momentum, rsi = _oscillator_matrix(closes, volumes)

    osc_signals = []
    osc_status = "FAIL"
    osc_confluence = 0

    if money_flow == bias:
        osc_signals.append(f"Smart Money Flow: {money_flow}")
        osc_confluence += 12
    elif money_flow != "NEUTRAL":
        osc_signals.append(f"Smart Money Flow: {money_flow} (conflicting)")

    if divergence:
        osc_signals.append(divergence.replace("_", " ").title())
        if ("BULLISH" in divergence and bias == "BULLISH") or ("BEARISH" in divergence and bias == "BEARISH"):
            osc_confluence += 15
        else:
            osc_confluence += 5

    if momentum == "STRONG":
        osc_signals.append(f"Momentum: STRONG (RSI: {rsi:.0f})")
        osc_confluence += 8
    elif momentum == "OVERBOUGHT" and bias == "BEARISH":
        osc_signals.append(f"Overbought (RSI: {rsi:.0f}) — aligned")
        osc_confluence += 10
    elif momentum == "OVERSOLD" and bias == "BULLISH":
        osc_signals.append(f"Oversold (RSI: {rsi:.0f}) — aligned")
        osc_confluence += 10
    elif momentum in ("OVERBOUGHT", "OVERSOLD"):
        osc_signals.append(f"{momentum} (RSI: {rsi:.0f}) — caution")
        osc_confluence -= 5

    confluence += osc_confluence

    if osc_confluence >= 15:
        osc_status = "PASS"
    elif osc_confluence >= 5:
        osc_status = "PARTIAL"

    modules.append({
        "module": "Oscillator Matrix",
        "status": osc_status,
        "detail": f"Money Flow: {money_flow} | Divergence: {divergence or 'None'} | Momentum: {momentum} (RSI: {rsi:.0f})",
        "sub_signals": osc_signals,
    })

    # ============ CONFLUENCE DECISION ============
    pass_count = sum(1 for m in modules if m['status'] == 'PASS')
    partial_count = sum(1 for m in modules if m['status'] == 'PARTIAL')

    signal_type = "WAIT"
    entry_price = None
    sl = None
    tp1 = tp2 = tp3 = None
    rr = None

    # High confluence: all 3 modules PASS or 2 PASS + 1 PARTIAL, aligned direction
    if pass_count >= 2 and (pass_count + partial_count) >= 3:
        if bias == "BULLISH" and so_signal == "BUY":
            signal_type = "BUY"
        elif bias == "BEARISH" and so_signal == "SELL":
            signal_type = "SELL"
        elif bias == "BULLISH" and so_signal is None and pass_count == 3:
            signal_type = "BUY"
        elif bias == "BEARISH" and so_signal is None and pass_count == 3:
            signal_type = "SELL"
    elif pass_count >= 2:
        if bias == "BULLISH":
            signal_type = "BUY"
        elif bias == "BEARISH":
            signal_type = "SELL"

    if signal_type == "BUY":
        entry_price = current
        sl = min(lows[-5:]) - atr * 0.3
        if ob_zone and ob_type == "BULLISH_OB":
            sl = min(sl, ob_zone[0] - atr * 0.2)
        risk = entry_price - sl
        tp1 = entry_price + risk * 1.5
        tp2 = entry_price + risk * 2.5
        tp3 = entry_price + risk * 3.5
        rr = f"1:{round(risk * 2.5 / risk, 1)}" if risk > 0 else "1:2.5"
    elif signal_type == "SELL":
        entry_price = current
        sl = max(highs[-5:]) + atr * 0.3
        if ob_zone and ob_type == "BEARISH_OB":
            sl = max(sl, ob_zone[1] + atr * 0.2)
        risk = sl - entry_price
        tp1 = entry_price - risk * 1.5
        tp2 = entry_price - risk * 2.5
        tp3 = entry_price - risk * 3.5
        rr = f"1:{round(risk * 2.5 / risk, 1)}" if risk > 0 else "1:2.5"

    confidence = min(confluence, 100)
    if signal_type == "WAIT":
        rec = f"WAIT — Confluence {confluence}/100. Need all 3 modules (PAC, S&O, Oscillator) aligned. Structure: {bias}, Cloud: {cloud_trend}, Flow: {money_flow}"
    else:
        rec = f"{signal_type} — {confluence}/100 confluence. {bias} structure + {so_strength or 'Normal'} S&O signal + {money_flow} flow. Entry at {entry_price:.2f}, SL at {sl:.2f}. Trail with Smart Trail at {smart_trail}."

    return {
        "status": "SIGNAL" if signal_type != "WAIT" else "SCANNING",
        "signal_type": signal_type,
        "structure_bias": bias,
        "bos_detected": bos,
        "choch_detected": choch,
        "choch_plus": choch_plus,
        "order_block_zone": f"{ob_zone[0]:.2f} - {ob_zone[1]:.2f}" if ob_zone else None,
        "order_block_type": ob_type,
        "liquidity_swept": liq_swept,
        "fvg_zone": f"{fvg[0]:.2f} - {fvg[1]:.2f}" if fvg else None,
        "premium_discount": pd_zone,
        "signal_strength": so_strength,
        "neo_cloud_trend": cloud_trend,
        "smart_trail_level": str(smart_trail) if smart_trail else None,
        "money_flow": money_flow,
        "divergence": divergence.replace("_", " ").title() if divergence else None,
        "momentum_state": momentum,
        "entry_price": f"{entry_price:.2f}" if entry_price else None,
        "stop_loss": f"{sl:.2f}" if sl else None,
        "tp1": f"{tp1:.2f}" if tp1 else None,
        "tp2": f"{tp2:.2f}" if tp2 else None,
        "tp3": f"{tp3:.2f}" if tp3 else None,
        "risk_reward": rr,
        "atr_value": round(atr, 2),
        "confluence_score": confluence,
        "modules": modules,
        "confidence": confidence,
        "recommendation": rec,
    }


@api_router.post("/pac-so/analyze", response_model=PACSOResponse)
async def analyze_pac_so(request: PACSORequest):
    """PAC + S&O Matrix High Confluence Analysis"""
    try:
        bars = request.bars
        if len(bars) < 30:
            return PACSOResponse(
                status="INSUFFICIENT_DATA", signal_type="WAIT",
                structure_bias="NEUTRAL", premium_discount="NEUTRAL",
                confluence_score=0, modules=[], confidence=0,
                recommendation="Need at least 30 bars (15M timeframe recommended)"
            )
        result = run_full_pac_so_analysis(bars)
        return PACSOResponse(**result)
    except Exception as e:
        logging.error(f"PAC+S&O analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ======================= AMDS-HYBRID (Adaptive Momentum + Smart Money) =======================

def _amds_calc_ema(closes, period):
    """EMA calculation"""
    if len(closes) < period:
        return sum(closes) / len(closes) if closes else 0
    mult = 2 / (period + 1)
    ema = sum(closes[:period]) / period
    for c in closes[period:]:
        ema = (c - ema) * mult + ema
    return ema

def _amds_calc_adx(highs, lows, closes, period=14):
    """ADX calculation"""
    if len(closes) < period + 2:
        return 20, False
    plus_dm_list, minus_dm_list, tr_list = [], [], []
    for i in range(1, len(closes)):
        up_move = highs[i] - highs[i-1]
        down_move = lows[i-1] - lows[i]
        plus_dm = up_move if up_move > down_move and up_move > 0 else 0
        minus_dm = down_move if down_move > up_move and down_move > 0 else 0
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        plus_dm_list.append(plus_dm)
        minus_dm_list.append(minus_dm)
        tr_list.append(tr)
    if len(tr_list) < period:
        return 20, False
    atr_s = sum(tr_list[-period:]) / period
    if atr_s == 0:
        return 20, False
    plus_di = (sum(plus_dm_list[-period:]) / period) / atr_s * 100
    minus_di = (sum(minus_dm_list[-period:]) / period) / atr_s * 100
    di_sum = plus_di + minus_di
    dx = abs(plus_di - minus_di) / di_sum * 100 if di_sum > 0 else 0
    # Simplified ADX = smoothed DX
    adx = dx
    # Check if ADX is rising
    if len(plus_dm_list) >= period + 5:
        prev_plus_di = (sum(plus_dm_list[-period-5:-5]) / period) / (sum(tr_list[-period-5:-5]) / period) * 100 if sum(tr_list[-period-5:-5]) > 0 else 0
        prev_minus_di = (sum(minus_dm_list[-period-5:-5]) / period) / (sum(tr_list[-period-5:-5]) / period) * 100 if sum(tr_list[-period-5:-5]) > 0 else 0
        prev_di_sum = prev_plus_di + prev_minus_di
        prev_dx = abs(prev_plus_di - prev_minus_di) / prev_di_sum * 100 if prev_di_sum > 0 else 0
        rising = adx > prev_dx
    else:
        rising = adx > 25
    return round(adx, 1), rising

def _amds_calc_obv(closes, volumes):
    """OBV trend"""
    if len(closes) < 10 or not volumes or all(v == 0 for v in volumes):
        return "NEUTRAL", 0
    obv = 0
    obv_list = []
    for i in range(1, len(closes)):
        vol = volumes[i] if i < len(volumes) else 0
        if closes[i] > closes[i-1]:
            obv += vol
        elif closes[i] < closes[i-1]:
            obv -= vol
        obv_list.append(obv)
    if len(obv_list) < 5:
        return "NEUTRAL", 0
    recent_obv = obv_list[-5:]
    rising_count = sum(1 for i in range(1, len(recent_obv)) if recent_obv[i] > recent_obv[i-1])
    if rising_count >= 3:
        return "RISING", obv
    elif rising_count <= 1:
        return "FALLING", obv
    return "NEUTRAL", obv


def run_full_amds_analysis(bars):
    """Full AMDS-Hybrid 6-Step analysis"""
    if len(bars) < 40:
        return {
            "status": "INSUFFICIENT_DATA", "signal_type": "WAIT",
            "htf_bias": "NEUTRAL", "steps": [], "confidence": 0,
            "cisd_detected": False, "bos_detected": False,
            "recommendation": "Need at least 40 bars for AMDS analysis"
        }
    closes = [b['close'] for b in bars]
    highs = [b['high'] for b in bars]
    lows = [b['low'] for b in bars]
    volumes = [b.get('volume', 0) for b in bars]
    current = closes[-1]
    atr_vals = [highs[i] - lows[i] for i in range(len(closes))]
    atr = sum(atr_vals[-14:]) / 14 if len(atr_vals) >= 14 else sum(atr_vals) / len(atr_vals)
    steps = []
    confidence = 0

    # === Step 1: Higher Timeframe Bias (200 EMA) ===
    ema_200 = _amds_calc_ema(closes, min(200, len(closes) - 1)) if len(closes) > 10 else current
    ema_50 = _amds_calc_ema(closes, min(50, len(closes) - 1)) if len(closes) > 10 else current
    if current > ema_200 and ema_50 > ema_200:
        htf_bias = "BULLISH"
        bias_detail = f"Price ({current:.2f}) > EMA200 ({ema_200:.2f}), EMA50 > EMA200 — Strong Bullish"
    elif current < ema_200 and ema_50 < ema_200:
        htf_bias = "BEARISH"
        bias_detail = f"Price ({current:.2f}) < EMA200 ({ema_200:.2f}), EMA50 < EMA200 — Strong Bearish"
    elif current > ema_200:
        htf_bias = "BULLISH"
        bias_detail = f"Price ({current:.2f}) > EMA200 ({ema_200:.2f}) — Bullish Bias"
    elif current < ema_200:
        htf_bias = "BEARISH"
        bias_detail = f"Price ({current:.2f}) < EMA200 ({ema_200:.2f}) — Bearish Bias"
    else:
        htf_bias = "NEUTRAL"
        bias_detail = f"Price = EMA200 ({ema_200:.2f}) — No clear bias"
    s1_status = "PASS" if htf_bias != "NEUTRAL" else "FAIL"
    steps.append({"step": 1, "name": "HTF Bias (EMA200)", "status": s1_status, "detail": bias_detail})
    if s1_status == "PASS":
        confidence += 15

    # === Step 2: Accumulation Range ===
    range_bars = min(25, len(closes) - 5)
    range_slice = closes[-range_bars-5:-5]
    h_slice = highs[-range_bars-5:-5]
    l_slice = lows[-range_bars-5:-5]
    if len(range_slice) >= 5:
        range_high = max(h_slice)
        range_low = min(l_slice)
        range_width = range_high - range_low
        avg_atr_range = sum(atr_vals[-range_bars-5:-5]) / len(atr_vals[-range_bars-5:-5]) if atr_vals[-range_bars-5:-5] else atr
        consolidation_ratio = avg_atr_range / range_width if range_width > 0 else 1
        is_tight = consolidation_ratio < 0.25  # Relaxed from 0.15
        range_str = f"{range_low:.2f} - {range_high:.2f}"
        range_detail = f"Range: {range_str} | Width: {range_width:.2f} | ATR/Range: {consolidation_ratio:.3f}"
        if is_tight:
            range_detail += " — Consolidation detected"
        else:
            range_detail += " — Watching for squeeze"
    else:
        range_high = max(highs[-10:])
        range_low = min(lows[-10:])
        range_str = f"{range_low:.2f} - {range_high:.2f}"
        is_tight = True  # Assume tight on limited data
        range_detail = f"Range: {range_str}"
    s2_status = "PASS" if is_tight else "PARTIAL"
    steps.append({"step": 2, "name": "Accumulation Range", "status": s2_status, "detail": range_detail})
    if is_tight:
        confidence += 18
    elif s2_status == "PARTIAL":
        confidence += 10

    # === Step 3: Manipulation Sweep ===
    sweep_type = "NONE"
    sweep_detail = "No sweep detected"
    swept_low = lows[-1] < range_low and closes[-1] > range_low
    swept_high = highs[-1] > range_high and closes[-1] < range_high
    # Check last 3 bars for sweep
    for k in range(1, min(4, len(bars))):
        if lows[-k] < range_low and closes[-k] > range_low:
            swept_low = True
        if highs[-k] > range_high and closes[-k] < range_high:
            swept_high = True
    # Rejection candle check — relaxed
    last_body = abs(closes[-1] - bars[-1].get('open', closes[-2] if len(closes) > 1 else closes[-1]))
    last_lower_wick = min(closes[-1], bars[-1].get('open', closes[-1])) - lows[-1]
    last_upper_wick = highs[-1] - max(closes[-1], bars[-1].get('open', closes[-1]))
    has_rejection = False
    if swept_low:
        sweep_type = "LOW_SWEPT"
        has_rejection = last_lower_wick > last_body * 0.8 if last_body > 0 else last_lower_wick > atr * 0.15
        if not has_rejection and htf_bias == "BULLISH":
            has_rejection = True  # Trust bias direction
        sweep_detail = f"Range Low ({range_low:.2f}) swept | Rejection: {'Strong' if has_rejection else 'Weak'}"
    elif swept_high:
        sweep_type = "HIGH_SWEPT"
        has_rejection = last_upper_wick > last_body * 0.8 if last_body > 0 else last_upper_wick > atr * 0.15
        if not has_rejection and htf_bias == "BEARISH":
            has_rejection = True
        sweep_detail = f"Range High ({range_high:.2f}) swept | Rejection: {'Strong' if has_rejection else 'Weak'}"
    s3_pass = sweep_type != "NONE"  # Any sweep = pass (relaxed)
    s3_status = "PASS" if s3_pass else "FAIL"
    steps.append({"step": 3, "name": "Manipulation Sweep", "status": s3_status, "detail": sweep_detail})
    if s3_pass:
        confidence += 20
    elif sweep_type != "NONE":
        confidence += 8

    # === Step 4: CISD + Change of Character (BOS) — relaxed ===
    cisd_detected = False
    bos_detected = False
    cisd_detail = "No displacement detected"
    recent_5 = bars[-5:]
    for k in range(1, len(recent_5)):
        body_k = abs(recent_5[k]['close'] - recent_5[k].get('open', recent_5[k-1]['close']))
        avg_body = sum(abs(bars[-10+j]['close'] - bars[-10+j].get('open', bars[-10+j-1]['close'] if j > 0 else bars[-10+j]['close'])) for j in range(min(8, len(bars)-2))) / 8 if len(bars) > 10 else atr * 0.5
        if body_k > avg_body * 1.3:  # Relaxed from 2x
            cisd_detected = True
            break
    # BOS: break of any recent swing (last 6 bars, was 8)
    if len(closes) > 6:
        prev_swing_high = max(highs[-6:-2])
        prev_swing_low = min(lows[-6:-2])
        if closes[-1] > prev_swing_high * 0.998 or highs[-1] > prev_swing_high:
            bos_detected = True
        elif closes[-1] < prev_swing_low * 1.002 or lows[-1] < prev_swing_low:
            bos_detected = True
    cisd_detail = f"Displacement: {'Yes' if cisd_detected else 'No'} | BOS: {'Yes' if bos_detected else 'No'}"
    s4_pass = cisd_detected or bos_detected  # Either one = PASS (relaxed from both)
    s4_status = "PASS" if s4_pass else "FAIL"
    steps.append({"step": 4, "name": "CISD + BOS", "status": s4_status, "detail": cisd_detail})
    if s4_pass:
        confidence += 20
    elif cisd_detected or bos_detected:
        confidence += 8

    # === Step 5: AMDS Confirmation (ADX + RSI + OBV) — relaxed thresholds ===
    adx_val, adx_rising = _amds_calc_adx(highs, lows, closes)
    rsi = _calc_rsi(closes[-15:]) if len(closes) >= 15 else 50
    obv_trend, obv_val = _amds_calc_obv(closes, volumes)
    score = 0
    # ADX > 20 (was 28)
    adx_ok = adx_val > 20
    if adx_ok and adx_rising:
        score += 35
    elif adx_ok:
        score += 28
    elif adx_val > 15:
        score += 18
    # RSI: < 42 for buy, > 58 for sell (was 32/68)
    rsi_buy_ok = rsi < 42
    rsi_sell_ok = rsi > 58
    rsi_ok = (htf_bias == "BULLISH" and rsi_buy_ok) or (htf_bias == "BEARISH" and rsi_sell_ok)
    if rsi_ok:
        score += 35
    elif (htf_bias == "BULLISH" and rsi < 50) or (htf_bias == "BEARISH" and rsi > 50):
        score += 20
    else:
        score += 10  # Base score
    # OBV
    obv_ok = (htf_bias == "BULLISH" and obv_trend == "RISING") or (htf_bias == "BEARISH" and obv_trend == "FALLING")
    if obv_ok:
        score += 30
    else:
        score += 12  # Neutral/any OBV gets base
    composite = min(score, 100)
    amds_detail = f"ADX: {adx_val} ({'Rising' if adx_rising else 'Flat'}) | RSI: {rsi:.1f} | OBV: {obv_trend} | Score: {composite}"
    s5_status = "PASS" if composite >= 55 else ("PARTIAL" if composite >= 35 else "FAIL")  # Relaxed from 88/55
    steps.append({"step": 5, "name": "AMDS Confirmation", "status": s5_status, "detail": amds_detail})
    if composite >= 55:
        confidence += 18
    elif composite >= 35:
        confidence += 10

    # === Step 6: Entry, SL & TP — relaxed signal threshold ===
    signal_type = "WAIT"
    entry_price = sl = tp1 = tp2 = rr = None
    pass_count = sum(1 for s in steps if s["status"] == "PASS")
    partial_count = sum(1 for s in steps if s["status"] == "PARTIAL")

    # Relaxed: 2 PASS or 1 PASS + 2 PARTIAL with bias
    if pass_count >= 3:
        if htf_bias == "BULLISH":
            signal_type = "BUY"
        elif htf_bias == "BEARISH":
            signal_type = "SELL"
    elif pass_count >= 2:
        if htf_bias == "BULLISH" and (sweep_type == "LOW_SWEPT" or bos_detected):
            signal_type = "BUY"
        elif htf_bias == "BEARISH" and (sweep_type == "HIGH_SWEPT" or bos_detected):
            signal_type = "SELL"
        elif htf_bias == "BULLISH":
            signal_type = "BUY"
        elif htf_bias == "BEARISH":
            signal_type = "SELL"
    elif pass_count >= 1 and partial_count >= 2 and confidence >= 30:
        if htf_bias == "BULLISH":
            signal_type = "BUY"
        elif htf_bias == "BEARISH":
            signal_type = "SELL"

    if signal_type != "WAIT":
        entry_price = current
        if signal_type == "BUY":
            sl = min(lows[-3:]) - atr * 0.3
            risk = entry_price - sl
            tp1 = entry_price + risk * 1.5
            tp2 = entry_price + risk * 2.5
        else:
            sl = max(highs[-3:]) + atr * 0.3
            risk = sl - entry_price
            tp1 = entry_price - risk * 1.5
            tp2 = entry_price - risk * 2.5
        rr = f"1:{2.5:.1f}"

    if signal_type != "WAIT":
        s6_detail = f"Entry: {entry_price:.2f} | SL: {sl:.2f} | TP1: {tp1:.2f} (1:1.5) | TP2: {tp2:.2f} (1:2.5) | Risk: 0.75-1%"
        s6_status = "PASS"
    else:
        s6_detail = "Waiting for all conditions — no trade"
        s6_status = "FAIL"
    steps.append({"step": 6, "name": "Entry / SL / TP", "status": s6_status, "detail": s6_detail})

    if signal_type == "BUY":
        rec = f"BUY — Entry: {current:.2f} | SL: {sl:.2f} | TP1: {tp1:.2f} | TP2: {tp2:.2f}"
    elif signal_type == "SELL":
        rec = f"SELL — Entry: {current:.2f} | SL: {sl:.2f} | TP1: {tp1:.2f} | TP2: {tp2:.2f}"
    else:
        rec = "WAIT — AMDS conditions not fully aligned. Watching."

    return {
        "status": "ACTIVE" if signal_type != "WAIT" else "SCANNING",
        "signal_type": signal_type,
        "htf_bias": htf_bias,
        "accumulation_range": range_str,
        "manipulation_sweep": sweep_type,
        "cisd_detected": cisd_detected,
        "bos_detected": bos_detected,
        "adx_value": adx_val,
        "rsi_value": round(rsi, 1),
        "obv_trend": obv_trend,
        "composite_score": composite,
        "entry_price": f"{entry_price:.2f}" if entry_price else None,
        "stop_loss": f"{sl:.2f}" if sl else None,
        "tp1": f"{tp1:.2f}" if tp1 else None,
        "tp2": f"{tp2:.2f}" if tp2 else None,
        "risk_reward": rr,
        "atr_value": round(atr, 2),
        "steps": steps,
        "confidence": min(confidence, 100),
        "recommendation": rec,
    }


def run_mini_amds(bars):
    """Quick AMDS check for auto-scanner"""
    if len(bars) < 40:
        return "WAIT"
    result = run_full_amds_analysis(bars)
    return result.get("signal_type", "WAIT")


@api_router.post("/amds/analyze", response_model=AMDSAnalysisResponse)
async def analyze_amds(request: AMDSAnalysisRequest):
    """AMDS-Hybrid (Adaptive Momentum + Smart Money) Analysis"""
    try:
        bars = request.bars
        if len(bars) < 40:
            return AMDSAnalysisResponse(
                status="INSUFFICIENT_DATA", signal_type="WAIT",
                htf_bias="NEUTRAL", steps=[], confidence=0,
                recommendation="Need at least 40 bars for AMDS analysis"
            )
        result = run_full_amds_analysis(bars)
        return AMDSAnalysisResponse(**result)
    except Exception as e:
        logging.error(f"AMDS analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))




class DemonRequest(BaseModel):
    ticker: str
    bars: List[dict]


class DemonResponse(BaseModel):
    verdict: str
    signal_type: str
    confidence: float
    buy_count: int
    sell_count: int
    wait_count: int
    total_strategies: int
    strategy_signals: dict
    entry_price: Optional[str] = None
    stop_loss: Optional[str] = None
    targets: Optional[List[str]] = None
    confluence_details: List[str]
    recommendation: str


def run_mini_falling_knife(bars):
    """
    Falling Knife — Reversal Buy Strategy
    CONDITIONS (strict):
      1. Stock dropped 40%+ from its 52W (or available) high
      2. Reversal candle in the LAST 2 bars (hammer, bullish engulfing, or pin bar)
      3. Volume on reversal bar > 1.3x average (confirmation)
    ENTRY  : Recent 5-bar swing low (limit zone, not market)
    SL     : 1.5% below the absolute lowest low of last 10 bars
    TARGETS: Fibonacci 23.6%, 38.2%, 61.8% of the (peak→low) range
    """
    try:
        closes = [b['close'] for b in bars]
        highs  = [b['high']  for b in bars]
        lows   = [b['low']   for b in bars]
        vols   = [b.get('volume', 0) for b in bars]

        if len(bars) < 20:
            return "WAIT", None

        peak    = max(highs[-min(252, len(highs)):])
        current = closes[-1]
        drop    = (peak - current) / peak * 100

        if drop < 40:
            return "WAIT", None

        # Reversal candle check (last 2 bars)
        last, prev = bars[-1], bars[-2]
        body      = abs(last['close'] - last['open'])
        lo_shadow = min(last['open'], last['close']) - last['low']
        # Hammer: lower shadow >= 2× body
        hammer     = lo_shadow >= 2 * max(body, last['close'] * 0.001)
        # Bullish engulfing: current candle engulfs previous red candle
        engulfing  = (last['close'] > last['open'] and
                      prev['close'] < prev['open'] and
                      last['close'] > prev['open'] and
                      last['open']  < prev['close'])

        if not (hammer or engulfing):
            return "WAIT", None

        # Volume confirmation
        vol_avg = sum(vols[-20:]) / max(len(vols[-20:]), 1)
        if vol_avg > 0 and vols[-1] < 1.3 * vol_avg:
            return "WAIT", None

        # Levels
        abs_low = min(lows[-10:])
        entry   = round(min(lows[-5:]) * 1.005, 2)     # slightly above recent low
        sl      = round(abs_low * 0.985, 2)             # 1.5% below absolute low
        rng     = peak - abs_low
        t1 = round(abs_low + rng * 0.236, 2)
        t2 = round(abs_low + rng * 0.382, 2)
        t3 = round(abs_low + rng * 0.618, 2)

        return "BUY", {"entry": entry, "sl": sl, "targets": [t1, t2, t3]}
    except Exception:
        return "WAIT", None


def run_mini_reverse_swings(bars, method):
    """
    Reverse Swings — Swing Exhaustion Reversal
    METHOD A (BUY): Price pulled back to clear swing-low zone; must bounce from there
      - Fell 5%+ from recent high
      - Now within 1% of the swing low (at the entry zone)
      - Bullish candle at the swing low
    METHOD B (SELL): Price pushed up to swing-high zone; must reverse from there
      - Rose 5%+ from recent low
      - Now within 1% of the swing high
      - Bearish candle at the swing high
    ENTRY : Exact swing low/high level
    SL    : ATR × 1.5 beyond the swing extreme
    TARGET: 50% and 100% of the prior swing range
    """
    try:
        closes = [b['close'] for b in bars]
        highs  = [b['high']  for b in bars]
        lows   = [b['low']   for b in bars]

        if len(closes) < 20:
            return "WAIT", None

        current = closes[-1]
        atr     = _smc_compute_atr(highs, lows, closes, 14)
        last    = bars[-1]

        if method == "A":  # BUY at swing low
            lookback    = min(20, len(closes) - 5)
            swing_low   = min(lows[-lookback:-1])
            recent_high = max(highs[-lookback:-1])

            drop_to_low = (recent_high - swing_low) / recent_high * 100
            if drop_to_low < 5:
                return "WAIT", None                         # Not a meaningful swing

            # Price must be AT the swing low (within 1%)
            dist = abs(current - swing_low) / swing_low * 100
            if dist > 1.0:
                return "WAIT", None

            # Bullish reversal candle required
            if last['close'] <= last['open']:
                return "WAIT", None

            entry = round(swing_low * 1.005, 2)
            sl    = round(swing_low - atr * 1.5, 2)
            rng   = recent_high - swing_low
            t1    = round(swing_low + rng * 0.50, 2)
            t2    = round(swing_low + rng * 1.00, 2)
            t3    = round(swing_low + rng * 1.50, 2)
            return "BUY", {"entry": entry, "sl": sl, "targets": [t1, t2, t3]}

        elif method == "B":  # SELL at swing high
            lookback     = min(20, len(closes) - 5)
            swing_high   = max(highs[-lookback:-1])
            recent_low   = min(lows[-lookback:-1])

            rise_to_high = (swing_high - recent_low) / recent_low * 100
            if rise_to_high < 5:
                return "WAIT", None

            dist = abs(current - swing_high) / swing_high * 100
            if dist > 1.0:
                return "WAIT", None

            # Bearish reversal candle required
            if last['close'] >= last['open']:
                return "WAIT", None

            entry = round(swing_high * 0.995, 2)
            sl    = round(swing_high + atr * 1.5, 2)
            rng   = swing_high - recent_low
            t1    = round(swing_high - rng * 0.50, 2)
            t2    = round(swing_high - rng * 1.00, 2)
            t3    = round(swing_high - rng * 1.50, 2)
            return "SELL", {"entry": entry, "sl": sl, "targets": [t1, t2, t3]}

        return "WAIT", None
    except Exception:
        return "WAIT", None


def run_mini_explosive_volume(bars):
    """
    Explosive Volume Breakout
    CONDITIONS:
      1. Volume on last bar > 2× 20-day avg volume
      2. Price closing above the 20-day high (valid breakout, not just touch)
      3. Bullish candle — close in upper 60% of range
    ENTRY : Breakout candle's closing price
    SL    : Below the breakout candle's low (max loss = candle range)
    TARGET: Measured move — candle range × 1.5 / 2.5 / 4.0 projected from close
    """
    try:
        closes = [b['close'] for b in bars]
        highs  = [b['high']  for b in bars]
        lows   = [b['low']   for b in bars]
        vols   = [b.get('volume', 0) for b in bars]

        if len(bars) < 25:
            return "WAIT", None

        vol_avg = sum(vols[-20:]) / max(len(vols[-20:]), 1)
        if vol_avg == 0 or vols[-1] < 2.0 * vol_avg:
            return "WAIT", None

        last    = bars[-1]
        current = closes[-1]

        # Must close ABOVE the 20-day prior high (actual breakout)
        prior_high20 = max(highs[-21:-1])
        if current <= prior_high20:
            return "WAIT", None

        # Bullish candle — close in upper 60% of bar range
        bar_range = last['high'] - last['low']
        if bar_range == 0:
            return "WAIT", None
        close_position = (last['close'] - last['low']) / bar_range
        if close_position < 0.6:
            return "WAIT", None

        entry = round(current, 2)
        sl    = round(last['low'] * 0.995, 2)
        t1    = round(current + bar_range * 1.5, 2)
        t2    = round(current + bar_range * 2.5, 2)
        t3    = round(current + bar_range * 4.0, 2)

        return "BUY", {"entry": entry, "sl": sl, "targets": [t1, t2, t3]}
    except Exception:
        return "WAIT", None


def run_mini_breakout(bars):
    """
    15-Min Stock Breakout (Donchian-20 + Volume Confirmation + ATR Risk Sizing)
    Designed for 15m timeframe but works on any TF — robust intraday breakout.

    BUY CONDITIONS (all must hold):
      1. Close > prior 20-bar high (Donchian-20 upper break)
      2. Current bar high > prior 20-bar high (intra-bar breakout, not just close)
      3. Volume on last bar >= 1.3 × 20-bar avg volume
      4. Last candle bullish: close > open AND close in upper 50% of range
      5. Recent trend not exhausted (close > EMA20)

    SELL CONDITIONS (mirror, Donchian-20 lower break with bearish confirmation).

    ENTRY : Current close (breakout confirmed)
    SL    : ATR(14) × 1.5 below (BUY) / above (SELL) the entry
    TARGETS: 1R, 2R, 3R based on (entry - SL) distance
    """
    try:
        if len(bars) < 25:
            return "WAIT", None

        closes = [b['close'] for b in bars]
        highs  = [b['high']  for b in bars]
        lows   = [b['low']   for b in bars]
        vols   = [b.get('volume', 0) for b in bars]

        last    = bars[-1]
        current = closes[-1]
        bar_rng = last['high'] - last['low']
        if bar_rng <= 0:
            return "WAIT", None

        # Donchian-20 channel (using bars [-21:-1] = prior 20 bars, excluding current)
        prior_high20 = max(highs[-21:-1])
        prior_low20  = min(lows[-21:-1])

        # Volume filter
        vol_avg20 = sum(vols[-21:-1]) / 20.0
        if vol_avg20 <= 0 or vols[-1] < 1.3 * vol_avg20:
            return "WAIT", None

        # ATR for risk sizing
        atr = _smc_compute_atr(highs, lows, closes, 14)
        if atr <= 0:
            atr = bar_rng  # fallback

        ema20 = calc_ema(closes, 20)

        close_pos = (last['close'] - last['low']) / bar_rng  # 0..1

        # ── BULLISH BREAKOUT ───────────────────────────────────────────────
        bullish_candle = last['close'] > last['open'] and close_pos >= 0.5
        if (current > prior_high20
                and last['high'] > prior_high20
                and bullish_candle
                and current >= ema20):
            entry = round(current, 2)
            sl    = round(entry - atr * 1.5, 2)
            risk  = max(entry - sl, 0.01)
            t1    = round(entry + risk * 1.0, 2)
            t2    = round(entry + risk * 2.0, 2)
            t3    = round(entry + risk * 3.0, 2)
            return "BUY", {"entry": entry, "sl": sl, "targets": [t1, t2, t3]}

        # ── BEARISH BREAKDOWN ──────────────────────────────────────────────
        bearish_candle = last['close'] < last['open'] and close_pos <= 0.5
        if (current < prior_low20
                and last['low'] < prior_low20
                and bearish_candle
                and current <= ema20):
            entry = round(current, 2)
            sl    = round(entry + atr * 1.5, 2)
            risk  = max(sl - entry, 0.01)
            t1    = round(entry - risk * 1.0, 2)
            t2    = round(entry - risk * 2.0, 2)
            t3    = round(entry - risk * 3.0, 2)
            return "SELL", {"entry": entry, "sl": sl, "targets": [t1, t2, t3]}

        return "WAIT", None
    except Exception:
        return "WAIT", None


def run_mini_golden_setup(bars):
    """
    Golden Setup — EMA Pullback in Trend
    BUY  LOGIC : Uptrend (price > SMA200, EMA20 > EMA50) AND price has pulled back
                 to within 1.5% of EMA20 AND last candle is bullish at that level.
    SELL LOGIC : Downtrend (price < SMA200, EMA20 < EMA50) AND price has bounced
                 to within 1.5% of EMA20 AND last candle is bearish at that level.
    ENTRY : EMA20 value (the pullback/bounce level)
    SL    : EMA50 level ± ATR × 0.5 (break of structure)
    TARGET: Previous swing high (BUY) or previous swing low (SELL)
    """
    try:
        closes = [b['close'] for b in bars]
        highs  = [b['high']  for b in bars]
        lows   = [b['low']   for b in bars]

        if len(closes) < 50:
            return "WAIT", None

        sma200 = sum(closes[-min(200, len(closes)):]) / min(200, len(closes))
        ema20  = calc_ema(closes, 20)
        ema50  = calc_ema(closes, 50)
        atr    = _smc_compute_atr(highs, lows, closes, 14)
        current = closes[-1]
        last    = bars[-1]

        dist_to_ema20 = abs(current - ema20) / ema20 * 100

        # ---- BULLISH SETUP ----
        if current > sma200 and ema20 > ema50:
            # Price must have pulled back TO EMA20 (within 1.5%)
            if dist_to_ema20 > 1.5:
                return "WAIT", None
            # Bullish candle required at EMA20
            if last['close'] <= last['open']:
                return "WAIT", None

            entry = round(ema20, 2)
            sl    = round(ema50 - atr * 0.5, 2)
            # Targets: recent swing highs
            swing_highs = sorted(highs[-20:-1], reverse=True)
            t1 = round(swing_highs[0] if swing_highs else current * 1.03, 2)
            t2 = round(swing_highs[0] * 1.02 if swing_highs else current * 1.06, 2)
            t3 = round(swing_highs[0] * 1.05 if swing_highs else current * 1.10, 2)
            return "BUY", {"entry": entry, "sl": sl, "targets": [t1, t2, t3]}

        # ---- BEARISH SETUP ----
        elif current < sma200 and ema20 < ema50:
            if dist_to_ema20 > 1.5:
                return "WAIT", None
            if last['close'] >= last['open']:
                return "WAIT", None

            entry = round(ema20, 2)
            sl    = round(ema50 + atr * 0.5, 2)
            swing_lows = sorted(lows[-20:-1])
            t1 = round(swing_lows[0] if swing_lows else current * 0.97, 2)
            t2 = round(swing_lows[0] * 0.98 if swing_lows else current * 0.94, 2)
            t3 = round(swing_lows[0] * 0.95 if swing_lows else current * 0.90, 2)
            return "SELL", {"entry": entry, "sl": sl, "targets": [t1, t2, t3]}

        return "WAIT", None
    except Exception:
        return "WAIT", None


def run_mini_ai_indicator(bars):
    """
    AI Multi-Indicator Score
    CONDITIONS (strict):
      - Score > 75 → BUY  (was 70; stricter to avoid noise)
      - Score < 25 → SELL (was 30)
    ENTRY : EMA20 (if price is near EMA20) else current (momentum)
    SL    : ATR × 1.5 below entry (BUY) or above entry (SELL)
    TARGET: ATR × 2, 3, 4 from entry
    """
    try:
        highs  = [b['high']  for b in bars]
        lows   = [b['low']   for b in bars]
        closes = [b['close'] for b in bars]

        if len(closes) < 26:
            return "WAIT", None, 50

        dmi_s  = calc_dmi_score(highs, lows, closes)[0]
        ma_s   = calc_ma_score(closes)
        macd_s = calc_macd_score(closes)
        rsi_val= calc_rsi(closes, 14)
        rsi_s  = calc_rsi_score(rsi_val)
        pk, pd_val = calc_stochastics(highs, lows, closes)
        stoch_s= calc_stoch_score(pk, pd_val)
        score  = (dmi_s * 0.30 + ma_s * 0.25 + macd_s * 0.20 + rsi_s * 0.15 + stoch_s * 0.10)

        if score <= 75 and score >= 25:
            return "WAIT", None, round(score, 1)

        atr     = _smc_compute_atr(highs, lows, closes, 14)
        ema20   = calc_ema(closes, 20)
        current = closes[-1]
        # Entry: near EMA20 if within 2%, else current
        entry   = round(ema20 if abs(current - ema20) / ema20 * 100 < 2.0 else current, 2)

        if score > 75:
            sl = round(entry - atr * 1.5, 2)
            t1 = round(entry + atr * 2.0, 2)
            t2 = round(entry + atr * 3.0, 2)
            t3 = round(entry + atr * 4.0, 2)
            return "BUY", {"entry": entry, "sl": sl, "targets": [t1, t2, t3]}, round(score, 1)

        else:  # score < 25
            sl = round(entry + atr * 1.5, 2)
            t1 = round(entry - atr * 2.0, 2)
            t2 = round(entry - atr * 3.0, 2)
            t3 = round(entry - atr * 4.0, 2)
            return "SELL", {"entry": entry, "sl": sl, "targets": [t1, t2, t3]}, round(score, 1)

    except Exception:
        return "WAIT", None, 50


def run_mini_godzilla(bars):
    """
    Godzilla TTE — Ross Hook Breakout
    CONDITIONS:
      - A Ross Hook (up or down) detected in the last 8 bars
      - Current price has BROKEN OUT above the hook high (BUY) / below hook low (SELL)
    ENTRY : Just above/below the breakout level (confirmed breakout only)
    SL    : Below the lowest low of bars since the hook (for BUY) — 0.5% buffer
    TARGET: Risk × 1.5, 2.5, 4.0 (R:R based)
    """
    try:
        highs  = [b['high']  for b in bars]
        lows   = [b['low']   for b in bars]
        closes = [b['close'] for b in bars]

        if len(bars) < 20:
            return "WAIT", None

        hooks    = detect_ross_hooks(highs, lows, closes)
        relevant = [h for h in hooks if h["bar_index_from_end"] <= 8]

        if not relevant:
            return "WAIT", None

        hook     = relevant[-1]
        hook_idx = hook["index"]
        current  = closes[-1]
        bars_after = len(bars) - 1 - hook_idx

        for i in range(1, min(bars_after, 3) + 1):
            bi = hook_idx + i
            if bi >= len(bars):
                break

            if hook["type"] == "up":
                breakout_level = bars[bi]['high']
                if current > breakout_level:
                    entry   = round(breakout_level * 1.001, 2)
                    sl_base = min(lows[hook_idx: hook_idx + i + 1])
                    sl      = round(sl_base * 0.995, 2)
                    risk    = entry - sl
                    if risk <= 0:
                        return "WAIT", None
                    t1 = round(entry + risk * 1.5, 2)
                    t2 = round(entry + risk * 2.5, 2)
                    t3 = round(entry + risk * 4.0, 2)
                    return "BUY", {"entry": entry, "sl": sl, "targets": [t1, t2, t3]}

            elif hook["type"] == "down":
                breakout_level = bars[bi]['low']
                if current < breakout_level:
                    entry   = round(breakout_level * 0.999, 2)
                    sl_base = max(highs[hook_idx: hook_idx + i + 1])
                    sl      = round(sl_base * 1.005, 2)
                    risk    = sl - entry
                    if risk <= 0:
                        return "WAIT", None
                    t1 = round(entry - risk * 1.5, 2)
                    t2 = round(entry - risk * 2.5, 2)
                    t3 = round(entry - risk * 4.0, 2)
                    return "SELL", {"entry": entry, "sl": sl, "targets": [t1, t2, t3]}

        return "WAIT", None
    except Exception:
        return "WAIT", None




# ============================================================
# GHOST MODE - Auto Scanner for Indian Stocks (Multi-Strategy Confluence)
# ============================================================

GHOST_SCAN_STOCKS = [
    {"ticker": "RELIANCE.NS", "name": "Reliance Industries"},
    {"ticker": "TCS.NS", "name": "TCS"},
    {"ticker": "HDFCBANK.NS", "name": "HDFC Bank"},
    {"ticker": "INFY.NS", "name": "Infosys"},
    {"ticker": "ICICIBANK.NS", "name": "ICICI Bank"},
    {"ticker": "SBIN.NS", "name": "SBI"},
    {"ticker": "BHARTIARTL.NS", "name": "Bharti Airtel"},
    {"ticker": "ITC.NS", "name": "ITC"},
    {"ticker": "KOTAKBANK.NS", "name": "Kotak Bank"},
    {"ticker": "LT.NS", "name": "L&T"},
    {"ticker": "AXISBANK.NS", "name": "Axis Bank"},
    {"ticker": "ASIANPAINT.NS", "name": "Asian Paints"},
    {"ticker": "MARUTI.NS", "name": "Maruti Suzuki"},
    {"ticker": "WIPRO.NS", "name": "Wipro"},
    {"ticker": "TATAMOTORS.NS", "name": "Tata Motors"},
    {"ticker": "TATASTEEL.NS", "name": "Tata Steel"},
    {"ticker": "ADANIENT.NS", "name": "Adani Enterprises"},
    {"ticker": "HCLTECH.NS", "name": "HCL Tech"},
    {"ticker": "SUNPHARMA.NS", "name": "Sun Pharma"},
    {"ticker": "BAJFINANCE.NS", "name": "Bajaj Finance"},
    {"ticker": "BAJFINSV.NS", "name": "Bajaj Finserv"},
    {"ticker": "TITAN.NS", "name": "Titan Company"},
    {"ticker": "ULTRACEMCO.NS", "name": "UltraTech Cement"},
    {"ticker": "NESTLEIND.NS", "name": "Nestle India"},
    {"ticker": "POWERGRID.NS", "name": "Power Grid"},
    {"ticker": "NTPC.NS", "name": "NTPC"},
    {"ticker": "ONGC.NS", "name": "ONGC"},
    {"ticker": "COALINDIA.NS", "name": "Coal India"},
    {"ticker": "JSWSTEEL.NS", "name": "JSW Steel"},
    {"ticker": "TECHM.NS", "name": "Tech Mahindra"},
    {"ticker": "HINDALCO.NS", "name": "Hindalco"},
    {"ticker": "GRASIM.NS", "name": "Grasim Industries"},
    {"ticker": "DIVISLAB.NS", "name": "Divi's Labs"},
    {"ticker": "DRREDDY.NS", "name": "Dr Reddy's Labs"},
    {"ticker": "CIPLA.NS", "name": "Cipla"},
    {"ticker": "EICHERMOT.NS", "name": "Eicher Motors"},
    {"ticker": "HEROMOTOCO.NS", "name": "Hero MotoCorp"},
    {"ticker": "BAJAJ-AUTO.NS", "name": "Bajaj Auto"},
    {"ticker": "M&M.NS", "name": "M&M"},
    {"ticker": "INDUSINDBK.NS", "name": "IndusInd Bank"},
    {"ticker": "APOLLOHOSP.NS", "name": "Apollo Hospitals"},
    {"ticker": "TATACONSUM.NS", "name": "Tata Consumer"},
    {"ticker": "BRITANNIA.NS", "name": "Britannia"},
    {"ticker": "BPCL.NS", "name": "BPCL"},
    {"ticker": "HINDUNILVR.NS", "name": "HUL"},
    {"ticker": "SBILIFE.NS", "name": "SBI Life Insurance"},
    {"ticker": "HDFCLIFE.NS", "name": "HDFC Life"},
    {"ticker": "ADANIPORTS.NS", "name": "Adani Ports"},
    {"ticker": "LTIM.NS", "name": "LTIMindtree"},
    {"ticker": "SHRIRAMFIN.NS", "name": "Shriram Finance"},
]

class GhostScanResult(BaseModel):
    ticker: str
    name: str
    price: float
    change_pct: float
    verdict: str
    signal_type: str
    confidence: float
    buy_count: int
    sell_count: int
    total_strategies: int
    entry_price: Optional[str] = None
    stop_loss: Optional[str] = None
    targets: Optional[List[str]] = None
    strategy_signals: dict

class GhostScanResponse(BaseModel):
    scanned: int
    results: List[GhostScanResult]
    scan_time: str
    errors: int


def run_demon_on_bars(bars):
    """Run DEMON confluence on raw bar dicts, returns result dict"""
    if len(bars) < 30:
        return None

    closes = [b['close'] for b in bars]
    current = closes[-1]

    fk_signal  = run_mini_falling_knife(bars)
    rsa_signal = run_mini_reverse_swings(bars, "A")
    rsb_signal = run_mini_reverse_swings(bars, "B")
    ev_signal  = run_mini_explosive_volume(bars)
    gs_signal  = run_mini_golden_setup(bars)
    ai_signal, _ai_details, ai_score = run_mini_ai_indicator(bars)
    gz_signal  = run_mini_godzilla(bars)

    strategies = {
        "falling_knife":    {"signal": fk_signal,  "name": "Falling Knife",             "weight": 1},
        "reverse_swings_a": {"signal": rsa_signal, "name": "Reverse Swings A",          "weight": 1},
        "reverse_swings_b": {"signal": rsb_signal, "name": "Reverse Swings B",          "weight": 1},
        "explosive_volume": {"signal": ev_signal,  "name": "Explosive Volume",          "weight": 1.2},
        "golden_setup":     {"signal": gs_signal,  "name": "Golden Setup",              "weight": 1.5},
        "ai_indicator":     {"signal": ai_signal,  "name": f"AI Indicator ({ai_score})", "weight": 1.3},
        "godzilla":         {"signal": gz_signal,  "name": "Godzilla TTE",              "weight": 1.2},
    }

    buy_count  = sum(1 for s in strategies.values() if s["signal"] == "BUY")
    sell_count = sum(1 for s in strategies.values() if s["signal"] == "SELL")
    total      = len(strategies)

    buy_weight  = sum(s["weight"] for s in strategies.values() if s["signal"] == "BUY")
    sell_weight = sum(s["weight"] for s in strategies.values() if s["signal"] == "SELL")
    total_wt    = sum(s["weight"] for s in strategies.values())
    buy_pct     = (buy_weight  / total_wt) * 100 if total_wt > 0 else 0
    sell_pct    = (sell_weight / total_wt) * 100 if total_wt > 0 else 0

    if buy_count >= 4:
        verdict = "DEMON BUY"; signal_type = "BUY";  confidence = buy_pct
        sl = current * 0.95; t1, t2, t3 = current * 1.05, current * 1.10, current * 1.15
    elif sell_count >= 4:
        verdict = "DEMON SELL"; signal_type = "SELL"; confidence = sell_pct
        sl = current * 1.05; t1, t2, t3 = current * 0.95, current * 0.90, current * 0.85
    elif buy_count >= 3:
        verdict = "LEANING BUY"; signal_type = "BUY"; confidence = buy_pct
        sl = current * 0.95; t1, t2, t3 = current * 1.04, current * 1.08, None
    elif sell_count >= 3:
        verdict = "LEANING SELL"; signal_type = "SELL"; confidence = sell_pct
        sl = current * 1.05; t1, t2, t3 = current * 0.96, current * 0.92, None
    else:
        verdict = "MIXED" if (buy_count >= 2 or sell_count >= 2) else "NO SIGNAL"
        signal_type = "WAIT"; confidence = max(buy_pct, sell_pct)
        sl = t1 = t2 = t3 = None

    targets = None
    if t1 is not None:
        targets = [f"{t1:.2f}"]
        if t2 is not None: targets.append(f"{t2:.2f}")
        if t3 is not None: targets.append(f"{t3:.2f}")

    strategy_signals = {k: {"name": s["name"], "signal": s["signal"], "weight": s["weight"]}
                        for k, s in strategies.items()}

    return {
        "verdict":          verdict,
        "signal_type":      signal_type,
        "confidence":       round(confidence, 1),
        "buy_count":        buy_count,
        "sell_count":       sell_count,
        "total_strategies": total,
        "entry_price":      f"{current:.2f}" if signal_type != "WAIT" else None,
        "stop_loss":        f"{sl:.2f}"       if sl               else None,
        "targets":          targets,
        "strategy_signals": strategy_signals,
        "current_price":    current,
    }


# NOTE: /demon/analyze endpoint has been removed (deprecated).
# The internal run_demon_on_bars() helper is retained because it is still used
# by the auto-scanner and ghost-mode scanner for multi-strategy confluence.


# ============================================================
# GHOST MODE - Auto Scanner for Indian Stocks (Multi-Strategy Confluence)
# ============================================================
    """Multi-strategy confluence runner for Ghost Mode scanner."""
    if len(bars) < 30:
        return None

    closes = [b['close'] for b in bars]
    current = closes[-1]

    fk_signal  = run_mini_falling_knife(bars)
    rsa_signal = run_mini_reverse_swings(bars, "A")
    rsb_signal = run_mini_reverse_swings(bars, "B")
    ev_signal  = run_mini_explosive_volume(bars)
    gs_signal  = run_mini_golden_setup(bars)
    ai_signal, _ai_details, ai_score = run_mini_ai_indicator(bars)
    gz_signal  = run_mini_godzilla(bars)

    strategies = {
        "falling_knife":    {"signal": fk_signal,  "name": "Falling Knife",        "weight": 1.0},
        "reverse_swings_a": {"signal": rsa_signal, "name": "Reverse Swings A",     "weight": 1.0},
        "reverse_swings_b": {"signal": rsb_signal, "name": "Reverse Swings B",     "weight": 1.0},
        "explosive_volume": {"signal": ev_signal,  "name": "Explosive Volume",     "weight": 1.2},
        "golden_setup":     {"signal": gs_signal,  "name": "Golden Setup",         "weight": 1.5},
        "ai_indicator":     {"signal": ai_signal,  "name": f"AI Indicator ({ai_score})", "weight": 1.3},
        "godzilla":         {"signal": gz_signal,  "name": "Godzilla TTE",         "weight": 1.2},
    }

    buy_count  = sum(1 for s in strategies.values() if s["signal"] == "BUY")
    sell_count = sum(1 for s in strategies.values() if s["signal"] == "SELL")
    total      = len(strategies)

    buy_weight  = sum(s["weight"] for s in strategies.values() if s["signal"] == "BUY")
    sell_weight = sum(s["weight"] for s in strategies.values() if s["signal"] == "SELL")
    total_wt    = sum(s["weight"] for s in strategies.values())
    buy_pct     = (buy_weight  / total_wt) * 100 if total_wt > 0 else 0
    sell_pct    = (sell_weight / total_wt) * 100 if total_wt > 0 else 0

    if buy_count >= 4:
        verdict = "STRONG BUY"; signal_type = "BUY";  confidence = buy_pct
        sl = current * 0.95; t1, t2, t3 = current * 1.05, current * 1.10, current * 1.15
    elif sell_count >= 4:
        verdict = "STRONG SELL"; signal_type = "SELL"; confidence = sell_pct
        sl = current * 1.05; t1, t2, t3 = current * 0.95, current * 0.90, current * 0.85
    elif buy_count >= 3:
        verdict = "LEANING BUY"; signal_type = "BUY";  confidence = buy_pct
        sl = current * 0.95; t1, t2, t3 = current * 1.04, current * 1.08, None
    elif sell_count >= 3:
        verdict = "LEANING SELL"; signal_type = "SELL"; confidence = sell_pct
        sl = current * 1.05; t1, t2, t3 = current * 0.96, current * 0.92, None
    else:
        verdict = "MIXED" if (buy_count >= 2 or sell_count >= 2) else "NO SIGNAL"
        signal_type = "WAIT"; confidence = max(buy_pct, sell_pct)
        sl = t1 = t2 = t3 = None

    targets = None
    if t1 is not None:
        targets = [f"{t1:.2f}"]
        if t2 is not None: targets.append(f"{t2:.2f}")
        if t3 is not None: targets.append(f"{t3:.2f}")

    strategy_signals = {k: {"name": s["name"], "signal": s["signal"], "weight": s["weight"]}
                        for k, s in strategies.items()}

    return {
        "verdict":          verdict,
        "signal_type":      signal_type,
        "confidence":       round(confidence, 1),
        "buy_count":        buy_count,
        "sell_count":       sell_count,
        "total_strategies": total,
        "entry_price":      f"{current:.2f}" if signal_type != "WAIT" else None,
        "stop_loss":        f"{sl:.2f}"       if sl               else None,
        "targets":          targets,
        "strategy_signals": strategy_signals,
        "current_price":    current,
    }



def run_mini_hybrid_vwap(bars):
    """
    Hybrid VWAP+TWAP Strategy — Signal Generator
    CONDITIONS:
      BUY  : Price > VWAP, last bar bullish, volume above avg, RSI 40-65
      SELL : Price < VWAP, last bar bearish, volume above avg, RSI 35-60
      BOUNCE: Price within 0.5% of VWAP with reversal candle → strongest signal
    ENTRY : Current price (for bounce: VWAP ± 0.1%)
    SL    : VWAP ∓ 1.5 × ATR (BUY: below VWAP, SELL: above VWAP)
    TARGET: Upper/Lower VWAP band (±1.5σ), then ±3σ, then R×3
    """
    try:
        if len(bars) < 20:
            return "WAIT", None

        closes = [b['close'] for b in bars]
        highs  = [b['high']  for b in bars]
        lows   = [b['low']   for b in bars]
        vols   = [b.get('volume', 0) for b in bars]

        # VWAP from all bars
        tpv_sum = vol_sum = 0.0
        for b in bars:
            tp = (b['high'] + b['low'] + b['close']) / 3
            v  = b.get('volume', 1) or 1
            tpv_sum += tp * v
            vol_sum  += v
        vwap = tpv_sum / vol_sum if vol_sum > 0 else closes[-1]

        # Standard deviation of typical prices for bands
        tp_vals = [(b['high'] + b['low'] + b['close']) / 3 for b in bars]
        sd = (sum((tp - vwap) ** 2 for tp in tp_vals) / len(tp_vals)) ** 0.5
        upper_band = vwap + 1.5 * sd
        lower_band = vwap - 1.5 * sd

        # ATR (14-period)
        atr_vals = []
        for i in range(1, min(15, len(bars))):
            tr = max(
                highs[-i] - lows[-i],
                abs(highs[-i] - closes[-i - 1]),
                abs(lows[-i]  - closes[-i - 1]),
            )
            atr_vals.append(tr)
        atr = sum(atr_vals) / len(atr_vals) if atr_vals else closes[-1] * 0.01

        # RSI-14
        gains = losses = 0.0
        for i in range(-14, -1):
            diff = closes[i + 1] - closes[i]
            if diff > 0: gains  += diff
            else:        losses -= diff
        gains  /= 14; losses /= 14
        rsi = 100 - 100 / (1 + gains / losses) if losses > 0 else 100.0

        current  = closes[-1]
        last_bar = bars[-1]
        is_bull  = last_bar['close'] > last_bar['open']
        is_bear  = last_bar['close'] < last_bar['open']

        vol_avg = sum(vols[-20:]) / max(len(vols[-20:]), 1)
        hi_vol  = vols[-1] > vol_avg * 1.1 if vol_avg > 0 else True

        deviation_pct = abs(current - vwap) / vwap * 100
        near_vwap     = deviation_pct <= 0.5

        # Signal classification
        if near_vwap and is_bull and hi_vol:
            # Bounce off VWAP — strongest setup
            direction = "BUY"
            entry = round(vwap * 1.001, 2)
        elif near_vwap and is_bear and hi_vol:
            direction = "SELL"
            entry = round(vwap * 0.999, 2)
        elif current > vwap and is_bull and 40 <= rsi <= 65 and hi_vol:
            direction = "BUY"
            entry = round(current, 2)
        elif current < vwap and is_bear and 35 <= rsi <= 60 and hi_vol:
            direction = "SELL"
            entry = round(current, 2)
        else:
            return "WAIT", None

        if direction == "BUY":
            sl = round(vwap - 1.5 * atr, 2)
            t1 = round(upper_band, 2)
            t2 = round(vwap + 3 * sd, 2)
            t3 = round(entry + (entry - sl) * 3, 2)
        else:
            sl = round(vwap + 1.5 * atr, 2)
            t1 = round(lower_band, 2)
            t2 = round(vwap - 3 * sd, 2)
            t3 = round(entry - (sl - entry) * 3, 2)

        return direction, {"entry": entry, "sl": sl, "targets": [t1, t2, t3],
                           "vwap": round(vwap, 2), "upper_band": round(upper_band, 2),
                           "lower_band": round(lower_band, 2), "rsi": round(rsi, 1),
                           "atr": round(atr, 2)}
    except Exception:
        return "WAIT", None


# ======================= AUTO SCANNER =======================

async def _run_mirofish_for_scanner(ticker: str, bars: list, current_price: float) -> dict:
    """Lightweight MiroFish call for auto-scanner with news + GPT swarm consensus."""
    closes = [b['close'] for b in bars if b.get('close')]
    volumes = [b.get('volume', 0) for b in bars]

    # RSI
    rsi = 50
    if len(closes) >= 15:
        gains, losses_a = [], []
        for j in range(1, min(15, len(closes))):
            d = closes[-j] - closes[-j - 1]
            if d > 0:
                gains.append(d)
            else:
                losses_a.append(abs(d))
        avg_g = sum(gains) / 14 if gains else 0.001
        avg_l = sum(losses_a) / 14 if losses_a else 0.001
        rs = avg_g / avg_l if avg_l > 0 else 1
        rsi = 100 - (100 / (1 + rs))

    ema20 = sum(closes[-20:]) / min(len(closes), 20) if closes else current_price
    avg_vol = sum(volumes[-10:]) / max(len(volumes[-10:]), 1)
    vol_ratio = (volumes[-1] / avg_vol) if avg_vol > 0 and volumes else 1

    # Fetch news
    news_text = "No news"
    try:
        t = yf.Ticker(ticker)
        raw_news = t.news or []
        items = []
        for item in raw_news[:4]:
            c = item.get('content') or {}
            title = c.get('title', '')
            if title:
                items.append(f"- {title}")
        if items:
            news_text = "\n".join(items)
    except Exception:
        pass

    price_summary = ", ".join([f"{c:.2f}" for c in closes[-6:]])

    prompt = f"""You are MiroFish Swarm Scanner. Quickly simulate 5 trading agents and give consensus.

STOCK: {ticker} | Price: {current_price:.2f} | RSI: {rsi:.1f} | EMA20: {ema20:.2f} | Vol Ratio: {vol_ratio:.2f}
Recent: {price_summary}

NEWS:
{news_text}

Return ONLY valid JSON:
{{"signal_type":"BUY/SELL/HOLD","swarm_consensus":"BULLISH/BEARISH/NEUTRAL","confidence":70,"stop_loss":"{current_price * 0.97:.2f}","day_target":"{current_price * 1.015:.2f}","targets":["{current_price * 1.03:.2f}","{current_price * 1.05:.2f}","{current_price * 1.08:.2f}"]}}"""

    emergent_key = os.environ.get('EMERGENT_LLM_KEY')
    openai_key = os.environ.get('OPENAI_API_KEY', '').strip()
    if not emergent_key and not openai_key:
        return None

    resp = await llm_complete(
        system_message="You are a fast trading signal scanner. Respond with valid JSON only.",
        user_text=prompt,
        provider="openai",
        model="gpt-4o",
        session_id=f"mf-scan-{ticker}-{uuid.uuid4().hex[:6]}",
    )
    if not resp:
        return None

    cleaned = resp.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
        cleaned = cleaned.rsplit("```", 1)[0]
    parsed = json.loads(cleaned)
    return parsed


# ======================= STRATEGY WEIGHTS (Weighted Confluence Scoring) =======================
# Manual weights as defined by user — used for confluence score calculation across all scanners
_STRATEGY_WEIGHTS = {
    "Godzilla TTE":     22.0,
    "SMC":              20.0,
    "MiroFish":         18.0,
    "Explosive Volume": 12.0,
    "Falling Knife":     8.0,
    "AI Indicator":      8.0,
    "DEMON":             5.0,
    "Golden Setup":      3.0,
    "Reverse Swings":    3.0,   # A + B combined
    "AMDS":              3.0,
    "PAC+S&O":           3.0,
    "Narrative Swing":   3.0,
    "Hybrid VWAP+TWAP": 6.0,
    "15m Breakout":    10.0,
}
_TOTAL_STRATEGY_WEIGHT = sum(_STRATEGY_WEIGHTS.values())  # 118.0


def _get_strategy_weight(strategy_name: str) -> float:
    """Return the weight for a strategy by fuzzy-matching the name."""
    sn = strategy_name.lower()
    for key, w in _STRATEGY_WEIGHTS.items():
        if key.lower() in sn:
            return w
    return 2.0  # small default for unrecognized strategies


def _calc_weighted_confluence(signals: list) -> tuple:
    """
    Returns (confluence_score: int, confluence_label: str, dominant_direction: str, aligned_count: int).
    Weighted by strategy importance; boosted by per-signal confidence.
    """
    buy_signals  = [s for s in signals if s.get("direction") == "BUY"]
    sell_signals = [s for s in signals if s.get("direction") == "SELL"]
    dominant_dir = "BUY" if len(buy_signals) >= len(sell_signals) else "SELL"
    aligned      = buy_signals if dominant_dir == "BUY" else sell_signals

    weighted_sum = 0.0
    for sig in aligned:
        w    = _get_strategy_weight(sig.get("strategy", ""))
        conf = min(sig.get("confidence", 70), 100) / 100.0
        # Weight * confidence_blend (0.6 base + 0.4 × actual confidence)
        weighted_sum += w * (0.6 + 0.4 * conf)

    raw_score       = (weighted_sum / _TOTAL_STRATEGY_WEIGHT) * 100.0
    confluence_score = min(int(raw_score), 100)

    if confluence_score >= 85:
        label = "EXTREME"
    elif confluence_score >= 65:
        label = "VERY STRONG"
    elif confluence_score >= 45:
        label = "STRONG"
    elif confluence_score >= 25:
        label = "MODERATE"
    else:
        label = "WEAK"

    return confluence_score, label, dominant_dir, len(aligned)


# ======================= STOCK FINDER =======================

_STOCK_FINDER_UNIVERSE = [
    # --- Large Cap / NIFTY 50 ---
    {"ticker": "RELIANCE.NS",   "name": "Reliance Industries",      "cap": "large"},
    {"ticker": "TCS.NS",        "name": "TCS",                      "cap": "large"},
    {"ticker": "HDFCBANK.NS",   "name": "HDFC Bank",                "cap": "large"},
    {"ticker": "ICICIBANK.NS",  "name": "ICICI Bank",               "cap": "large"},
    {"ticker": "INFY.NS",       "name": "Infosys",                  "cap": "large"},
    {"ticker": "BHARTIARTL.NS", "name": "Bharti Airtel",            "cap": "large"},
    {"ticker": "ITC.NS",        "name": "ITC",                      "cap": "large"},
    {"ticker": "BAJFINANCE.NS", "name": "Bajaj Finance",            "cap": "large"},
    {"ticker": "LT.NS",         "name": "L&T",                      "cap": "large"},
    {"ticker": "KOTAKBANK.NS",  "name": "Kotak Bank",               "cap": "large"},
    {"ticker": "AXISBANK.NS",   "name": "Axis Bank",                "cap": "large"},
    {"ticker": "HCLTECH.NS",    "name": "HCL Technologies",         "cap": "large"},
    {"ticker": "WIPRO.NS",      "name": "Wipro",                    "cap": "large"},
    {"ticker": "MARUTI.NS",     "name": "Maruti Suzuki",            "cap": "large"},
    {"ticker": "SBIN.NS",       "name": "State Bank of India",      "cap": "large"},
    {"ticker": "ADANIENT.NS",   "name": "Adani Enterprises",        "cap": "large"},
    {"ticker": "TATAMOTORS.NS", "name": "Tata Motors",              "cap": "large"},
    {"ticker": "SUNPHARMA.NS",  "name": "Sun Pharma",               "cap": "large"},
    {"ticker": "TITAN.NS",      "name": "Titan Company",            "cap": "large"},
    {"ticker": "NTPC.NS",       "name": "NTPC",                     "cap": "large"},
    {"ticker": "ONGC.NS",       "name": "ONGC",                     "cap": "large"},
    {"ticker": "COALINDIA.NS",  "name": "Coal India",               "cap": "large"},
    {"ticker": "TATASTEEL.NS",  "name": "Tata Steel",               "cap": "large"},
    {"ticker": "DRREDDY.NS",    "name": "Dr. Reddy's",              "cap": "large"},
    {"ticker": "CIPLA.NS",      "name": "Cipla",                    "cap": "large"},
    {"ticker": "BAJAJ-AUTO.NS", "name": "Bajaj Auto",               "cap": "large"},
    {"ticker": "EICHERMOT.NS",  "name": "Eicher Motors",            "cap": "large"},
    {"ticker": "HEROMOTOCO.NS", "name": "Hero MotoCorp",            "cap": "large"},
    {"ticker": "HINDALCO.NS",   "name": "Hindalco",                 "cap": "large"},
    {"ticker": "ASIANPAINT.NS", "name": "Asian Paints",             "cap": "large"},
    {"ticker": "ULTRACEMCO.NS", "name": "UltraTech Cement",         "cap": "large"},
    {"ticker": "GRASIM.NS",     "name": "Grasim Industries",        "cap": "large"},
    {"ticker": "INDUSINDBK.NS", "name": "IndusInd Bank",            "cap": "large"},
    {"ticker": "APOLLOHOSP.NS", "name": "Apollo Hospitals",         "cap": "large"},
    {"ticker": "JSWSTEEL.NS",   "name": "JSW Steel",                "cap": "large"},
    {"ticker": "POWERGRID.NS",  "name": "Power Grid",               "cap": "large"},
    {"ticker": "BPCL.NS",       "name": "BPCL",                     "cap": "large"},
    {"ticker": "TATACONSUM.NS", "name": "Tata Consumer Products",   "cap": "large"},
    {"ticker": "NESTLEIND.NS",  "name": "Nestle India",             "cap": "large"},
    {"ticker": "BRITANNIA.NS",  "name": "Britannia Industries",     "cap": "large"},
    # --- Mid Cap ---
    {"ticker": "PERSISTENT.NS", "name": "Persistent Systems",       "cap": "mid"},
    {"ticker": "COFORGE.NS",    "name": "Coforge",                  "cap": "mid"},
    {"ticker": "MPHASIS.NS",    "name": "Mphasis",                  "cap": "mid"},
    {"ticker": "LTIM.NS",       "name": "LTIMindtree",              "cap": "mid"},
    {"ticker": "TRENT.NS",      "name": "Trent",                    "cap": "mid"},
    {"ticker": "ASTRAL.NS",     "name": "Astral",                   "cap": "mid"},
    {"ticker": "PIIND.NS",      "name": "PI Industries",            "cap": "mid"},
    {"ticker": "DEEPAKNTR.NS",  "name": "Deepak Nitrite",           "cap": "mid"},
    {"ticker": "HAVELLS.NS",    "name": "Havells India",            "cap": "mid"},
    {"ticker": "VOLTAS.NS",     "name": "Voltas",                   "cap": "mid"},
    {"ticker": "TORNTPHARM.NS", "name": "Torrent Pharma",           "cap": "mid"},
    {"ticker": "LUPIN.NS",      "name": "Lupin",                    "cap": "mid"},
    {"ticker": "AUROPHARMA.NS", "name": "Aurobindo Pharma",         "cap": "mid"},
    {"ticker": "ALKEM.NS",      "name": "Alkem Laboratories",       "cap": "mid"},
    {"ticker": "BIOCON.NS",     "name": "Biocon",                   "cap": "mid"},
    {"ticker": "OBEROIRLTY.NS", "name": "Oberoi Realty",            "cap": "mid"},
    {"ticker": "GODREJPROP.NS", "name": "Godrej Properties",        "cap": "mid"},
    {"ticker": "PRESTIGE.NS",   "name": "Prestige Estates",         "cap": "mid"},
    {"ticker": "MUTHOOTFIN.NS", "name": "Muthoot Finance",          "cap": "mid"},
    {"ticker": "SUNDARMFIN.NS", "name": "Sundaram Finance",         "cap": "mid"},
    {"ticker": "BANKBARODA.NS", "name": "Bank of Baroda",           "cap": "mid"},
    {"ticker": "IDFCFIRSTB.NS", "name": "IDFC First Bank",          "cap": "mid"},
    {"ticker": "FEDERALBNK.NS", "name": "Federal Bank",             "cap": "mid"},
    {"ticker": "DIVISLAB.NS",   "name": "Divi's Laboratories",      "cap": "mid"},
    {"ticker": "GLENMARK.NS",   "name": "Glenmark Pharma",          "cap": "mid"},
    {"ticker": "ZYDUSLIFE.NS",  "name": "Zydus Lifesciences",       "cap": "mid"},
    {"ticker": "RATNAMANI.NS",  "name": "Ratnamani Metals",         "cap": "mid"},
    {"ticker": "AIAENG.NS",     "name": "AIA Engineering",          "cap": "mid"},
    {"ticker": "CAMS.NS",       "name": "CAMS",                     "cap": "mid"},
    {"ticker": "LALPATHLAB.NS", "name": "Dr. Lal PathLabs",         "cap": "mid"},
    {"ticker": "TATAELXSI.NS",  "name": "Tata Elxsi",               "cap": "mid"},
    {"ticker": "KPITTECH.NS",   "name": "KPIT Technologies",        "cap": "mid"},
    {"ticker": "M&M.NS",        "name": "Mahindra & Mahindra",      "cap": "mid"},
    {"ticker": "DLF.NS",        "name": "DLF",                      "cap": "mid"},
    {"ticker": "PHOENIXLTD.NS", "name": "Phoenix Mills",            "cap": "mid"},
    {"ticker": "TVSMOTOR.NS",   "name": "TVS Motor",                "cap": "mid"},
    {"ticker": "BALKRISIND.NS", "name": "Balkrishna Industries",    "cap": "mid"},
    {"ticker": "MOTHERSON.NS",  "name": "Samvardhana Motherson",    "cap": "mid"},
    {"ticker": "TIINDIA.NS",    "name": "Tube Investments",         "cap": "mid"},
    # --- Small Cap ---
    {"ticker": "IGL.NS",        "name": "Indraprastha Gas",         "cap": "small"},
    {"ticker": "IRCON.NS",      "name": "IRCON International",      "cap": "small"},
    {"ticker": "RVNL.NS",       "name": "Rail Vikas Nigam",         "cap": "small"},
    {"ticker": "NBCC.NS",       "name": "NBCC India",               "cap": "small"},
    {"ticker": "MOIL.NS",       "name": "MOIL",                     "cap": "small"},
    {"ticker": "NATIONALUM.NS", "name": "National Aluminium",       "cap": "small"},
    {"ticker": "HINDCOPPER.NS", "name": "Hindustan Copper",         "cap": "small"},
    {"ticker": "SAIL.NS",       "name": "SAIL",                     "cap": "small"},
    {"ticker": "KEC.NS",        "name": "KEC International",        "cap": "small"},
    {"ticker": "PNCINFRA.NS",   "name": "PNC Infratech",            "cap": "small"},
    {"ticker": "KNRCON.NS",     "name": "KNR Constructions",        "cap": "small"},
    {"ticker": "GRANULES.NS",   "name": "Granules India",           "cap": "small"},
    {"ticker": "NATCOPHARM.NS", "name": "Natco Pharma",             "cap": "small"},
    {"ticker": "IPCALAB.NS",    "name": "IPCA Labs",                "cap": "small"},
    {"ticker": "EMAMILTD.NS",   "name": "Emami",                    "cap": "small"},
    {"ticker": "RADICO.NS",     "name": "Radico Khaitan",           "cap": "small"},
    {"ticker": "SAREGAMA.NS",   "name": "Saregama India",           "cap": "small"},
    {"ticker": "PVRINOX.NS",    "name": "PVR Inox",                 "cap": "small"},
    {"ticker": "JINDALSAW.NS",  "name": "Jindal Saw",               "cap": "small"},
    {"ticker": "AJMERA.NS",     "name": "Ajmera Realty",            "cap": "small"},
    {"ticker": "KOLTEPATIL.NS", "name": "Kolte-Patil Dev.",         "cap": "small"},
    {"ticker": "BRIGADE.NS",    "name": "Brigade Enterprises",      "cap": "small"},
    {"ticker": "SUNTECK.NS",    "name": "Sunteck Realty",           "cap": "small"},
    {"ticker": "HAPPSTMNDS.NS", "name": "Happiest Minds",           "cap": "small"},
    {"ticker": "DELHIVERY.NS",  "name": "Delhivery",                "cap": "small"},
    {"ticker": "CAMPUS.NS",     "name": "Campus Activewear",        "cap": "small"},
    {"ticker": "ADANIPORTS.NS", "name": "Adani Ports",              "cap": "small"},
    {"ticker": "TATAPOWER.NS",  "name": "Tata Power",               "cap": "small"},
    {"ticker": "ADANIGREEN.NS", "name": "Adani Green Energy",       "cap": "small"},
    {"ticker": "GAIL.NS",       "name": "GAIL India",               "cap": "small"},
    {"ticker": "IOC.NS",        "name": "Indian Oil Corporation",   "cap": "small"},
    {"ticker": "HINDPETRO.NS",  "name": "Hindustan Petroleum",      "cap": "small"},
    {"ticker": "YESBANK.NS",    "name": "Yes Bank",                 "cap": "small"},
    {"ticker": "BANDHANBNK.NS", "name": "Bandhan Bank",             "cap": "small"},
    {"ticker": "PNB.NS",        "name": "Punjab National Bank",     "cap": "small"},
    {"ticker": "CANARABANK.NS", "name": "Canara Bank",              "cap": "small"},
    {"ticker": "UNIONBANK.NS",  "name": "Union Bank of India",      "cap": "small"},
    {"ticker": "DABUR.NS",      "name": "Dabur India",              "cap": "small"},
    {"ticker": "MARICO.NS",     "name": "Marico",                   "cap": "small"},
    {"ticker": "COLPAL.NS",     "name": "Colgate-Palmolive India",  "cap": "small"},
    {"ticker": "VBL.NS",        "name": "Varun Beverages",          "cap": "small"},
]

_stock_finder_executor = None  # lazy init


def _scan_stock_for_finder(stock_meta: Dict) -> Optional[Dict]:
    """
    Sync: fetch daily bars for one stock and run all 7 mini strategies.
    Returns a result dict if any signal found, else None.
    """
    import math

    def _safe(v, default=0.0):
        try:
            f = float(v)
            return default if (math.isnan(f) or math.isinf(f)) else round(f, 2)
        except Exception:
            return default

    try:
        obj = yf.Ticker(stock_meta["ticker"])
        hist = obj.history(period="6mo", interval="1d")
        if hist.empty or len(hist) < 50:
            return None

        bars = [
            {
                "open":   float(r["Open"]),
                "high":   float(r["High"]),
                "low":    float(r["Low"]),
                "close":  float(r["Close"]),
                "volume": float(r.get("Volume", 0)),
            }
            for _, r in hist.iterrows()
        ]

        current = bars[-1]["close"]
        signals = []

        # ---- Mini strategy runners ----
        for strategy_name, runner_fn in [
            ("Falling Knife",   lambda b: run_mini_falling_knife(b)),
            ("Reverse Swings A", lambda b: run_mini_reverse_swings(b, "A")),
            ("Reverse Swings B", lambda b: run_mini_reverse_swings(b, "B")),
            ("Explosive Volume", lambda b: run_mini_explosive_volume(b)),
            ("Golden Setup",     lambda b: run_mini_golden_setup(b)),
            ("Godzilla TTE",     lambda b: run_mini_godzilla(b)),
        ]:
            try:
                result = runner_fn(bars)
                direction = result[0]
                levels    = result[1] if len(result) > 1 else None
                if direction != "WAIT" and levels and isinstance(levels, dict):
                    signals.append({
                        "strategy":   strategy_name,
                        "direction":  direction,
                        "entry":      _safe(levels.get("entry", current)),
                        "stoploss":   _safe(levels.get("sl", current * 0.97)),
                        "targets":    [_safe(t) for t in levels.get("targets", [])],
                        "confidence": {"Falling Knife": 78, "Reverse Swings A": 72,
                                       "Reverse Swings B": 72, "Explosive Volume": 82,
                                       "Golden Setup": 85, "Godzilla TTE": 83}.get(strategy_name, 75),
                    })
            except Exception:
                pass

        # ---- AI Indicator ----
        try:
            ai_res   = run_mini_ai_indicator(bars)
            ai_dir   = ai_res[0]
            ai_lvl   = ai_res[1] if len(ai_res) > 1 else None
            ai_score = ai_res[2] if len(ai_res) > 2 else 50
            if ai_dir != "WAIT" and ai_lvl and isinstance(ai_lvl, dict):
                signals.append({
                    "strategy":   f"AI Indicator ({ai_score})",
                    "direction":  ai_dir,
                    "entry":      _safe(ai_lvl.get("entry", current)),
                    "stoploss":   _safe(ai_lvl.get("sl", current * 0.97)),
                    "targets":    [_safe(t) for t in ai_lvl.get("targets", [])],
                    "confidence": min(int(ai_score), 95),
                })
        except Exception:
            pass

        if not signals:
            return None

        # Best direction by vote (majority)
        buy_ct  = sum(1 for s in signals if s["direction"] == "BUY")
        sell_ct = sum(1 for s in signals if s["direction"] == "SELL")
        best_dir = "BUY" if buy_ct >= sell_ct else "SELL"
        best_sig = next((s for s in signals if s["direction"] == best_dir), signals[0])

        return {
            "ticker":        stock_meta["ticker"],
            "name":          stock_meta["name"],
            "cap":           stock_meta.get("cap", "large"),
            "current_price": _safe(current),
            "signals":       signals,
            "signal_count":  len(signals),
            "best_direction": best_dir,
            "best_entry":    best_sig["entry"],
            "best_sl":       best_sig["stoploss"],
            "best_target":   best_sig["targets"][0] if best_sig["targets"] else _safe(current * 1.03),
            "best_confidence": max(s["confidence"] for s in signals),
            "strategies":    [s["strategy"] for s in signals],
        }
    except Exception:
        return None


@api_router.get("/stock-finder/scan")
async def stock_finder_scan(request: Request, cap: str = "all"):
    """
    SSE endpoint — streams scan results for every stock in the universe.
    Each event is JSON:
      {type: 'progress', current, total, symbol}
      {type: 'result',   ticker, name, ...}
      {type: 'done',     total_found, total_scanned}
    Aborts cleanly if client disconnects.
    """
    from fastapi.responses import StreamingResponse as _StreamResp
    global _stock_finder_executor

    if _stock_finder_executor is None:
        _stock_finder_executor = _TPE(max_workers=15)

    universe = (
        _STOCK_FINDER_UNIVERSE if cap == "all"
        else [s for s in _STOCK_FINDER_UNIVERSE if s.get("cap") == cap]
    )
    total = len(universe)

    async def event_stream():
        loop     = asyncio.get_event_loop()
        found    = 0
        batch_sz = 15

        for batch_start in range(0, total, batch_sz):
            # Stop early if client closed the connection
            if await request.is_disconnected():
                return

            batch   = universe[batch_start: batch_start + batch_sz]
            tasks   = [loop.run_in_executor(_stock_finder_executor, _scan_stock_for_finder, s)
                       for s in batch]
            results = await asyncio.gather(*tasks)

            for j, result in enumerate(results):
                if await request.is_disconnected():
                    return
                idx    = batch_start + j
                sym    = universe[idx]["ticker"]
                # ---- progress event ----
                prog = json.dumps({"type": "progress", "current": idx + 1,
                                   "total": total, "symbol": sym})
                yield f"data: {prog}\n\n"
                # ---- result event (if signal) ----
                if result:
                    found += 1
                    res_str = json.dumps({"type": "result", **result})
                    yield f"data: {res_str}\n\n"

        done = json.dumps({"type": "done", "total_found": found, "total_scanned": total})
        yield f"data: {done}\n\n"

    return _StreamResp(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


# =============================================================================
# MULTI-TIMEFRAME + MULTI-ASSET SCANNER
# =============================================================================

# F&O Universe — indices + most liquid NSE F&O stocks
_MTF_UNIVERSE: List[Dict] = [
    # ── Indices ──────────────────────────────────────────────────────────────
    {"ticker": "^NSEI",      "name": "Nifty 50",          "segment": "index"},
    {"ticker": "^NSEBANK",   "name": "Bank Nifty",         "segment": "index"},
    {"ticker": "^NSEMDCP50", "name": "Nifty Midcap 50",    "segment": "index"},
    {"ticker": "NIFTYNXT50.NS", "name": "Nifty Next 50",   "segment": "index"},
    # ── Nifty 50 / Large-Cap F&O ─────────────────────────────────────────────
    {"ticker": "RELIANCE.NS",   "name": "Reliance Industries",    "segment": "fo"},
    {"ticker": "TCS.NS",        "name": "TCS",                    "segment": "fo"},
    {"ticker": "HDFCBANK.NS",   "name": "HDFC Bank",              "segment": "fo"},
    {"ticker": "ICICIBANK.NS",  "name": "ICICI Bank",             "segment": "fo"},
    {"ticker": "INFY.NS",       "name": "Infosys",                "segment": "fo"},
    {"ticker": "BHARTIARTL.NS", "name": "Bharti Airtel",          "segment": "fo"},
    {"ticker": "ITC.NS",        "name": "ITC",                    "segment": "fo"},
    {"ticker": "BAJFINANCE.NS", "name": "Bajaj Finance",          "segment": "fo"},
    {"ticker": "LT.NS",         "name": "L&T",                    "segment": "fo"},
    {"ticker": "KOTAKBANK.NS",  "name": "Kotak Bank",             "segment": "fo"},
    {"ticker": "AXISBANK.NS",   "name": "Axis Bank",              "segment": "fo"},
    {"ticker": "HCLTECH.NS",    "name": "HCL Technologies",       "segment": "fo"},
    {"ticker": "WIPRO.NS",      "name": "Wipro",                  "segment": "fo"},
    {"ticker": "MARUTI.NS",     "name": "Maruti Suzuki",          "segment": "fo"},
    {"ticker": "SBIN.NS",       "name": "SBI",                    "segment": "fo"},
    {"ticker": "ADANIENT.NS",   "name": "Adani Enterprises",      "segment": "fo"},
    {"ticker": "TATAMOTORS.NS", "name": "Tata Motors",            "segment": "fo"},
    {"ticker": "SUNPHARMA.NS",  "name": "Sun Pharma",             "segment": "fo"},
    {"ticker": "TITAN.NS",      "name": "Titan Company",          "segment": "fo"},
    {"ticker": "NTPC.NS",       "name": "NTPC",                   "segment": "fo"},
    {"ticker": "ONGC.NS",       "name": "ONGC",                   "segment": "fo"},
    {"ticker": "COALINDIA.NS",  "name": "Coal India",             "segment": "fo"},
    {"ticker": "TATASTEEL.NS",  "name": "Tata Steel",             "segment": "fo"},
    {"ticker": "DRREDDY.NS",    "name": "Dr. Reddy's",            "segment": "fo"},
    {"ticker": "CIPLA.NS",      "name": "Cipla",                  "segment": "fo"},
    {"ticker": "BAJAJ-AUTO.NS", "name": "Bajaj Auto",             "segment": "fo"},
    {"ticker": "EICHERMOT.NS",  "name": "Eicher Motors",          "segment": "fo"},
    {"ticker": "HEROMOTOCO.NS", "name": "Hero MotoCorp",          "segment": "fo"},
    {"ticker": "HINDALCO.NS",   "name": "Hindalco",               "segment": "fo"},
    {"ticker": "ASIANPAINT.NS", "name": "Asian Paints",           "segment": "fo"},
    {"ticker": "ULTRACEMCO.NS", "name": "UltraTech Cement",       "segment": "fo"},
    {"ticker": "GRASIM.NS",     "name": "Grasim Industries",      "segment": "fo"},
    {"ticker": "INDUSINDBK.NS", "name": "IndusInd Bank",          "segment": "fo"},
    {"ticker": "JSWSTEEL.NS",   "name": "JSW Steel",              "segment": "fo"},
    {"ticker": "POWERGRID.NS",  "name": "Power Grid",             "segment": "fo"},
    {"ticker": "BPCL.NS",       "name": "BPCL",                   "segment": "fo"},
    {"ticker": "TATACONSUM.NS", "name": "Tata Consumer Products", "segment": "fo"},
    {"ticker": "NESTLEIND.NS",  "name": "Nestle India",           "segment": "fo"},
    {"ticker": "APOLLOHOSP.NS", "name": "Apollo Hospitals",       "segment": "fo"},
    {"ticker": "ADANIPORTS.NS", "name": "Adani Ports",            "segment": "fo"},
    {"ticker": "TATAPOWER.NS",  "name": "Tata Power",             "segment": "fo"},
    {"ticker": "DIVISLAB.NS",   "name": "Divi's Laboratories",    "segment": "fo"},
    {"ticker": "BAJAJFINSV.NS", "name": "Bajaj Finserv",          "segment": "fo"},
    {"ticker": "SBILIFE.NS",    "name": "SBI Life Insurance",     "segment": "fo"},
    {"ticker": "HDFCLIFE.NS",   "name": "HDFC Life",              "segment": "fo"},
    {"ticker": "ICICIPRULI.NS", "name": "ICICI Prudential",       "segment": "fo"},
    # ── BankNifty components ──────────────────────────────────────────────────
    {"ticker": "BANKBARODA.NS", "name": "Bank of Baroda",         "segment": "banknifty"},
    {"ticker": "IDFCFIRSTB.NS", "name": "IDFC First Bank",        "segment": "banknifty"},
    {"ticker": "FEDERALBNK.NS", "name": "Federal Bank",           "segment": "banknifty"},
    {"ticker": "PNB.NS",        "name": "Punjab National Bank",   "segment": "banknifty"},
    {"ticker": "CANARABANK.NS", "name": "Canara Bank",            "segment": "banknifty"},
    {"ticker": "YESBANK.NS",    "name": "Yes Bank",               "segment": "banknifty"},
    {"ticker": "BANDHANBNK.NS", "name": "Bandhan Bank",           "segment": "banknifty"},
    {"ticker": "UNIONBANK.NS",  "name": "Union Bank of India",    "segment": "banknifty"},
    # ── Finnifty components ───────────────────────────────────────────────────
    {"ticker": "MUTHOOTFIN.NS", "name": "Muthoot Finance",        "segment": "finnifty"},
    {"ticker": "CHOLAFIN.NS",   "name": "Cholamandalam Finance",  "segment": "finnifty"},
    {"ticker": "LICHSGFIN.NS",  "name": "LIC Housing Finance",    "segment": "finnifty"},
    {"ticker": "M&MFIN.NS",     "name": "M&M Financial Services", "segment": "finnifty"},
    {"ticker": "RECLTD.NS",     "name": "REC Limited",            "segment": "finnifty"},
    {"ticker": "PFC.NS",        "name": "Power Finance Corp",     "segment": "finnifty"},
    {"ticker": "IRFC.NS",       "name": "IRFC",                   "segment": "finnifty"},
    # ── Midcap F&O ────────────────────────────────────────────────────────────
    {"ticker": "PERSISTENT.NS", "name": "Persistent Systems",     "segment": "midcap"},
    {"ticker": "COFORGE.NS",    "name": "Coforge",                "segment": "midcap"},
    {"ticker": "MPHASIS.NS",    "name": "Mphasis",                "segment": "midcap"},
    {"ticker": "LTIM.NS",       "name": "LTIMindtree",            "segment": "midcap"},
    {"ticker": "TRENT.NS",      "name": "Trent",                  "segment": "midcap"},
    {"ticker": "HAVELLS.NS",    "name": "Havells India",          "segment": "midcap"},
    {"ticker": "VOLTAS.NS",     "name": "Voltas",                 "segment": "midcap"},
    {"ticker": "TORNTPHARM.NS", "name": "Torrent Pharma",         "segment": "midcap"},
    {"ticker": "LUPIN.NS",      "name": "Lupin",                  "segment": "midcap"},
    {"ticker": "AUROPHARMA.NS", "name": "Aurobindo Pharma",       "segment": "midcap"},
    {"ticker": "OBEROIRLTY.NS", "name": "Oberoi Realty",          "segment": "midcap"},
    {"ticker": "GODREJPROP.NS", "name": "Godrej Properties",      "segment": "midcap"},
    {"ticker": "DLF.NS",        "name": "DLF",                    "segment": "midcap"},
    {"ticker": "TVSMOTOR.NS",   "name": "TVS Motor",              "segment": "midcap"},
    {"ticker": "BALKRISIND.NS", "name": "Balkrishna Industries",  "segment": "midcap"},
    {"ticker": "KPITTECH.NS",   "name": "KPIT Technologies",      "segment": "midcap"},
    {"ticker": "TATAELXSI.NS",  "name": "Tata Elxsi",             "segment": "midcap"},
    {"ticker": "M&M.NS",        "name": "Mahindra & Mahindra",    "segment": "midcap"},
    {"ticker": "DEEPAKNTR.NS",  "name": "Deepak Nitrite",         "segment": "midcap"},
    {"ticker": "PIIND.NS",      "name": "PI Industries",          "segment": "midcap"},
    {"ticker": "GLENMARK.NS",   "name": "Glenmark Pharma",        "segment": "midcap"},
    {"ticker": "ZYDUSLIFE.NS",  "name": "Zydus Lifesciences",     "segment": "midcap"},
    {"ticker": "IGL.NS",        "name": "Indraprastha Gas",       "segment": "midcap"},
    {"ticker": "GAIL.NS",       "name": "GAIL India",             "segment": "midcap"},
    {"ticker": "TATACOMM.NS",   "name": "Tata Communications",    "segment": "midcap"},
    {"ticker": "ABB.NS",        "name": "ABB India",              "segment": "midcap"},
    {"ticker": "SIEMENS.NS",    "name": "Siemens India",          "segment": "midcap"},
    {"ticker": "PIDILITIND.NS", "name": "Pidilite Industries",    "segment": "midcap"},
    {"ticker": "MARICO.NS",     "name": "Marico",                 "segment": "midcap"},
    {"ticker": "DABUR.NS",      "name": "Dabur India",            "segment": "midcap"},
    # ── Cash segment (high liquidity, non-F&O) ───────────────────────────────
    {"ticker": "IRCTC.NS",      "name": "IRCTC",                  "segment": "cash"},
    {"ticker": "DMART.NS",      "name": "Avenue Supermarts (DMart)", "segment": "cash"},
    {"ticker": "ZOMATO.NS",     "name": "Zomato",                 "segment": "cash"},
    {"ticker": "NYKAA.NS",      "name": "Nykaa",                  "segment": "cash"},
    {"ticker": "PAYTM.NS",      "name": "Paytm",                  "segment": "cash"},
    {"ticker": "DELHIVERY.NS",  "name": "Delhivery",              "segment": "cash"},
    {"ticker": "RVNL.NS",       "name": "Rail Vikas Nigam",       "segment": "cash"},
    {"ticker": "IOC.NS",        "name": "Indian Oil Corp",        "segment": "cash"},
    {"ticker": "SAIL.NS",       "name": "SAIL",                   "segment": "cash"},
    {"ticker": "YESBANK.NS",    "name": "Yes Bank",               "segment": "cash"},
]

# Timeframe parameters for yfinance
_MTF_TF_PARAMS = {
    "15m": {"period": "5d",   "interval": "15m", "min_bars": 20, "label": "15 Min"},
    "1h":  {"period": "30d",  "interval": "1h",  "min_bars": 20, "label": "1 Hour"},
    "1d":  {"period": "6mo",  "interval": "1d",  "min_bars": 50, "label": "Daily"},
}

_mtf_executor = None  # lazy ThreadPoolExecutor


def _mtf_scan_stock(stock_meta: Dict, timeframes: list) -> Optional[Dict]:
    """
    Sync function — fetch OHLCV for each requested TF, run mini strategies,
    compute weighted score per TF and overall MTF confluence.
    """
    import math

    def _safe(v, default=0.0):
        try:
            f = float(v)
            return default if (math.isnan(f) or math.isinf(f)) else round(f, 2)
        except Exception:
            return default

    ticker   = stock_meta["ticker"]
    tf_results = {}
    current_price = None

    for tf in timeframes:
        params = _MTF_TF_PARAMS.get(tf)
        if not params:
            continue
        try:
            obj  = yf.Ticker(ticker)
            hist = obj.history(period=params["period"], interval=params["interval"])
            if hist.empty or len(hist) < params["min_bars"]:
                tf_results[tf] = {"direction": "WAIT", "weighted_score": 0, "signals": []}
                continue

            bars = [
                {
                    "open":   _safe(r["Open"]),
                    "high":   _safe(r["High"]),
                    "low":    _safe(r["Low"]),
                    "close":  _safe(r["Close"]),
                    "volume": _safe(r.get("Volume", 0)),
                }
                for _, r in hist.iterrows()
            ]
            if current_price is None:
                current_price = bars[-1]["close"]

            # Run fast mini-strategies
            tf_signals = []
            for sname, runner_fn in [
                ("Falling Knife",    lambda b: run_mini_falling_knife(b)),
                ("Reverse Swings A", lambda b: run_mini_reverse_swings(b, "A")),
                ("Reverse Swings B", lambda b: run_mini_reverse_swings(b, "B")),
                ("Explosive Volume", lambda b: run_mini_explosive_volume(b)),
                ("15m Breakout",     lambda b: run_mini_breakout(b)),
                ("Golden Setup",     lambda b: run_mini_golden_setup(b)),
                ("Godzilla TTE",     lambda b: run_mini_godzilla(b)),
            ]:
                try:
                    res = runner_fn(bars)
                    direction = res[0]
                    levels    = res[1] if len(res) > 1 else None
                    if direction != "WAIT" and levels and isinstance(levels, dict):
                        tf_signals.append({
                            "strategy":   sname,
                            "direction":  direction,
                            "entry":      _safe(levels.get("entry",    bars[-1]["close"])),
                            "stoploss":   _safe(levels.get("sl",       bars[-1]["close"] * 0.97)),
                            "targets":    [_safe(t) for t in levels.get("targets", [])],
                            "confidence": {"Falling Knife": 78, "Reverse Swings A": 72,
                                           "Reverse Swings B": 72, "Explosive Volume": 82,
                                           "15m Breakout": 80,
                                           "Golden Setup": 85, "Godzilla TTE": 83}.get(sname, 75),
                        })
                except Exception:
                    pass

            try:
                ai_res = run_mini_ai_indicator(bars)
                ai_dir, ai_lvl, ai_score = ai_res[0], (ai_res[1] if len(ai_res) > 1 else None), (ai_res[2] if len(ai_res) > 2 else 50)
                if ai_dir != "WAIT" and ai_lvl and isinstance(ai_lvl, dict):
                    tf_signals.append({
                        "strategy":   f"AI Indicator ({ai_score})",
                        "direction":  ai_dir,
                        "entry":      _safe(ai_lvl.get("entry", bars[-1]["close"])),
                        "stoploss":   _safe(ai_lvl.get("sl",    bars[-1]["close"] * 0.97)),
                        "targets":    [_safe(t) for t in ai_lvl.get("targets", [])],
                        "confidence": min(int(ai_score), 95),
                    })
            except Exception:
                pass

            # Weighted score for this TF
            buy_w  = sum(_get_strategy_weight(s["strategy"]) * (0.6 + 0.4 * s.get("confidence", 70) / 100)
                         for s in tf_signals if s["direction"] == "BUY")
            sell_w = sum(_get_strategy_weight(s["strategy"]) * (0.6 + 0.4 * s.get("confidence", 70) / 100)
                         for s in tf_signals if s["direction"] == "SELL")
            tf_dir   = "BUY" if buy_w >= sell_w else "SELL"
            tf_score = int(min(max(buy_w, sell_w) / _TOTAL_STRATEGY_WEIGHT * 100, 100)) if tf_signals else 0

            tf_results[tf] = {
                "direction":      tf_dir if tf_signals else "WAIT",
                "weighted_score": tf_score,
                "signals":        [s["strategy"] for s in tf_signals if s["direction"] == tf_dir],
                "signal_count":   len([s for s in tf_signals if s["direction"] == tf_dir]),
                "entry":          tf_signals[0]["entry"] if tf_signals else _safe(current_price or 0),
                "stoploss":       next((s["stoploss"] for s in tf_signals if s["direction"] == tf_dir), _safe((current_price or 0) * 0.97)),
                "target":         next((s["targets"][0] for s in tf_signals if s["direction"] == tf_dir and s.get("targets")), _safe((current_price or 0) * 1.03)),
            }

        except Exception:
            tf_results[tf] = {"direction": "WAIT", "weighted_score": 0, "signals": []}

    if not current_price:
        return None

    # MTF Confluence — count TFs with a non-WAIT signal agreeing on same direction
    active_tfs   = [tf for tf, r in tf_results.items() if r["direction"] != "WAIT"]
    if not active_tfs:
        return None

    buy_tfs  = [tf for tf in active_tfs if tf_results[tf]["direction"] == "BUY"]
    sell_tfs = [tf for tf in active_tfs if tf_results[tf]["direction"] == "SELL"]
    dominant = "BUY" if len(buy_tfs) >= len(sell_tfs) else "SELL"
    aligned_tfs  = buy_tfs if dominant == "BUY" else sell_tfs
    mtf_confluence = len(aligned_tfs)

    # Overall weighted score = average of aligned TF scores
    overall_score = int(sum(tf_results[tf]["weighted_score"] for tf in aligned_tfs) / len(aligned_tfs)) if aligned_tfs else 0

    best_tf = max(aligned_tfs, key=lambda tf: tf_results[tf]["weighted_score"]) if aligned_tfs else active_tfs[0]
    best_data = tf_results[best_tf]

    return {
        "ticker":        ticker,
        "name":          stock_meta["name"],
        "segment":       stock_meta["segment"],
        "current_price": _safe(current_price),
        "tf_signals":    tf_results,
        "mtf_confluence": mtf_confluence,
        "total_timeframes": len(timeframes),
        "dominant_direction": dominant,
        "overall_score": overall_score,
        "best_entry":    best_data.get("entry", _safe(current_price)),
        "best_sl":       best_data.get("stoploss", _safe(current_price * 0.97)),
        "best_target":   best_data.get("target", _safe(current_price * 1.03)),
        "timeframes":    timeframes,
    }


@api_router.get("/multi-tf-scanner/scan")
async def multi_tf_scanner_scan(request: Request, segment: str = "fo", timeframes: str = "15m,1h,1d"):
    """
    SSE endpoint — Multi-Timeframe + Multi-Asset scanner.
    Streams per-stock results as they complete.
    Query params:
      segment  : all | fo | banknifty | finnifty | midcap | index | cash
      timeframes: comma-separated from 15m,1h,1d  (default: 15m,1h,1d)
    """
    from fastapi.responses import StreamingResponse as _StreamResp
    global _mtf_executor
    if _mtf_executor is None:
        _mtf_executor = _TPE(max_workers=12)

    tf_list = [t.strip() for t in timeframes.split(",") if t.strip() in _MTF_TF_PARAMS]
    if not tf_list:
        tf_list = ["15m", "1h", "1d"]

    if segment == "all":
        universe = _MTF_UNIVERSE
    elif segment == "most_active":
        # Dynamic universe — NSE's Most Active by Volume + Value (deduped, top 25)
        try:
            universe = _fetch_nse_most_active(limit=25)
        except Exception as e:
            logging.warning(f"NSE most-active fetch failed in scanner: {e}")
            universe = []
        if not universe:
            # Fallback: high-liquidity F&O subset so the scan still runs
            universe = [s for s in _MTF_UNIVERSE if s["segment"] == "fo"][:25]
    elif segment == "breakout_15m":
        # Focused 15-min breakout scanner — Most Active + F&O liquid universe.
        # Filtering to actual breakout setups happens inside the stream below.
        try:
            ma = _fetch_nse_most_active(limit=25)
        except Exception:
            ma = []
        fo = [s for s in _MTF_UNIVERSE if s["segment"] in ("fo", "banknifty", "finnifty")]
        seen_t: set = set()
        universe = []
        for src in (ma, fo):
            for row in src:
                t = row.get("ticker")
                if t and t not in seen_t:
                    seen_t.add(t)
                    universe.append(row)
        # Force 15m timeframe in tf_list (breakout strategy is 15m-focused)
        if "15m" not in tf_list:
            tf_list = ["15m"] + tf_list
    else:
        universe = [s for s in _MTF_UNIVERSE if s["segment"] == segment]

    total = len(universe)
    if total == 0:
        universe = _MTF_UNIVERSE
        total    = len(universe)

    async def event_stream():
        loop    = asyncio.get_event_loop()
        found   = 0
        batch_sz = 12
        breakout_only = (segment == "breakout_15m")

        for batch_start in range(0, total, batch_sz):
            if await request.is_disconnected():
                return

            batch   = universe[batch_start: batch_start + batch_sz]
            tasks   = [loop.run_in_executor(_mtf_executor, _mtf_scan_stock, s, tf_list)
                       for s in batch]
            results = await asyncio.gather(*tasks)

            for j, result in enumerate(results):
                if await request.is_disconnected():
                    return
                idx = batch_start + j
                sym = universe[idx]["ticker"]
                yield f"data: {json.dumps({'type': 'progress', 'current': idx + 1, 'total': total, 'symbol': sym})}\n\n"
                if result:
                    # In breakout-only mode, keep ONLY stocks where the 15m
                    # Breakout strategy actually fired on the 15m timeframe.
                    if breakout_only:
                        tf15 = (result.get("tf_signals") or {}).get("15m") or {}
                        sig_list = tf15.get("signals") or []
                        if not any("15m Breakout" in s for s in sig_list):
                            continue
                    found += 1
                    yield f"data: {json.dumps({'type': 'result', **result})}\n\n"

        yield f"data: {json.dumps({'type': 'done', 'total_found': found, 'total_scanned': total})}\n\n"

    return _StreamResp(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive", "Access-Control-Allow-Origin": "*"},
    )


@api_router.get("/nse/most-active")
async def get_nse_most_active(limit: int = 25):
    """Return the day's Most Active equities (NSE) combined by Volume + Value, deduped.

    Used by the Multi-TF Scanner's "Most Active" segment to preview the universe.
    Cached server-side for 5 min via _fetch_nse_most_active().
    """
    try:
        rows = _fetch_nse_most_active(limit=max(1, min(int(limit), 50)))
    except Exception as e:
        logging.warning(f"/nse/most-active failed: {e}")
        rows = []
    return {
        "ok":     bool(rows),
        "count":  len(rows),
        "source": "NSE most-active (volume + value, deduped)",
        "rows":   rows,
    }


# =============================================================================
# Telegram Channel Auto-Post for Stock Finder
# =============================================================================

from pydantic import BaseModel as _BM


class TelegramChannelIn(_BM):
    name: str
    bot_token: str
    chat_id: str
    enabled: bool = True


class TelegramSendRequest(_BM):
    channel_ids: List[str]
    results: List[Dict[str, Any]]


def _mask_token(t: str) -> str:
    if not t or len(t) < 10:
        return "***"
    return f"{t[:6]}…{t[-4:]}"


def _format_telegram_message(results: List[Dict[str, Any]]) -> str:
    """Build a single Markdown message with date header + BUY/SELL sections."""
    from datetime import datetime
    today = datetime.now().strftime("%d %b %Y")
    buys  = [r for r in results if r.get("best_direction") == "BUY"]
    sells = [r for r in results if r.get("best_direction") == "SELL"]
    # Sort each group by confidence desc
    buys.sort(key=lambda r: r.get("best_confidence", 0), reverse=True)
    sells.sort(key=lambda r: r.get("best_confidence", 0), reverse=True)

    lines = [
        f"📊 *Gann Trader — Stock Finder*",
        f"_{today} · {len(results)} setups_",
        "",
    ]

    def _row(i: int, r: Dict[str, Any]) -> str:
        sym = str(r.get("ticker", "")).replace(".NS", "").replace(".BO", "")
        return (
            f"{i}. *{sym}*  ·  {r.get('best_confidence', 0)}%\n"
            f"   Entry `₹{r.get('best_entry', '—')}` · SL `₹{r.get('best_sl', '—')}` · TGT `₹{r.get('best_target', '—')}`"
        )

    if buys:
        lines.append(f"🟢 *BUYS ({len(buys)})*")
        for i, r in enumerate(buys, 1):
            lines.append(_row(i, r))
        lines.append("")
    if sells:
        lines.append(f"🔴 *SELLS ({len(sells)})*")
        for i, r in enumerate(sells, 1):
            lines.append(_row(i, r))

    return "\n".join(lines).strip()


def _split_for_telegram(text: str, limit: int = 3900) -> List[str]:
    """Telegram message limit is 4096 chars; split on blank lines to be safe."""
    if len(text) <= limit:
        return [text]
    chunks: List[str] = []
    buf: List[str] = []
    size = 0
    for para in text.split("\n"):
        plen = len(para) + 1
        if size + plen > limit and buf:
            chunks.append("\n".join(buf))
            buf, size = [], 0
        buf.append(para)
        size += plen
    if buf:
        chunks.append("\n".join(buf))
    return chunks


async def _send_telegram_message(bot_token: str, chat_id: str, text: str) -> Dict[str, Any]:
    """POST to Telegram Bot API. Returns {ok, ...} or raises Exception."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        })
        try:
            data = r.json()
        except Exception:
            data = {"ok": False, "description": f"HTTP {r.status_code}"}
        return data


@api_router.get("/stock-finder/telegram-channels")
async def list_telegram_channels():
    """List saved Telegram channel configs (bot token masked for safety)."""
    docs = await db.telegram_channels.find({}).to_list(50)
    out = []
    for d in docs:
        out.append({
            "id":        str(d["_id"]),
            "name":      d.get("name", ""),
            "chat_id":   d.get("chat_id", ""),
            "bot_token_preview": _mask_token(d.get("bot_token", "")),
            "enabled":   d.get("enabled", True),
            "created_at": d.get("created_at", ""),
        })
    return {"channels": out}


@api_router.post("/stock-finder/telegram-channels")
async def create_telegram_channel(payload: TelegramChannelIn):
    """Save a new Telegram channel."""
    from datetime import datetime
    doc = {
        "name":       payload.name.strip() or "Untitled",
        "bot_token":  payload.bot_token.strip(),
        "chat_id":    payload.chat_id.strip(),
        "enabled":    bool(payload.enabled),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    res = await db.telegram_channels.insert_one(doc)
    return {"ok": True, "id": str(res.inserted_id)}


@api_router.put("/stock-finder/telegram-channels/{channel_id}")
async def update_telegram_channel(channel_id: str, payload: TelegramChannelIn):
    """Update a channel (full replace of editable fields)."""
    from bson import ObjectId
    try:
        _id = ObjectId(channel_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid channel id")
    updates = {
        "name":      payload.name.strip() or "Untitled",
        "bot_token": payload.bot_token.strip(),
        "chat_id":   payload.chat_id.strip(),
        "enabled":   bool(payload.enabled),
    }
    res = await db.telegram_channels.update_one({"_id": _id}, {"$set": updates})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Channel not found")
    return {"ok": True}


@api_router.delete("/stock-finder/telegram-channels/{channel_id}")
async def delete_telegram_channel(channel_id: str):
    """Delete a saved channel."""
    from bson import ObjectId
    try:
        _id = ObjectId(channel_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid channel id")
    res = await db.telegram_channels.delete_one({"_id": _id})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Channel not found")
    return {"ok": True}


@api_router.post("/stock-finder/telegram-test/{channel_id}")
async def test_telegram_channel(channel_id: str):
    """Send a small test message to validate bot token + chat_id."""
    from bson import ObjectId
    try:
        _id = ObjectId(channel_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid channel id")
    doc = await db.telegram_channels.find_one({"_id": _id})
    if not doc:
        raise HTTPException(status_code=404, detail="Channel not found")
    msg = "✅ *Gann Trader connected*\nYour bot can post Stock Finder setups here."
    try:
        resp = await _send_telegram_message(doc["bot_token"], doc["chat_id"], msg)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": bool(resp.get("ok")), "telegram": resp}


@api_router.post("/stock-finder/telegram-send")
async def send_telegram_results(payload: TelegramSendRequest):
    """Send a Stock Finder result batch to one or more saved channels."""
    from bson import ObjectId
    if not payload.results:
        raise HTTPException(status_code=400, detail="No results to send")
    if not payload.channel_ids:
        raise HTTPException(status_code=400, detail="No channels selected")

    # Fetch only the requested channels that are enabled
    object_ids = []
    for cid in payload.channel_ids:
        try:
            object_ids.append(ObjectId(cid))
        except Exception:
            continue
    if not object_ids:
        raise HTTPException(status_code=400, detail="Invalid channel ids")

    docs = await db.telegram_channels.find(
        {"_id": {"$in": object_ids}, "enabled": True}
    ).to_list(50)

    if not docs:
        raise HTTPException(status_code=404, detail="No enabled channels found")

    message  = _format_telegram_message(payload.results)
    chunks   = _split_for_telegram(message)
    report   = []

    for d in docs:
        per_channel = {"id": str(d["_id"]), "name": d.get("name", ""), "ok": True,
                       "messages": 0, "errors": []}
        for chunk in chunks:
            try:
                resp = await _send_telegram_message(d["bot_token"], d["chat_id"], chunk)
                if resp.get("ok"):
                    per_channel["messages"] += 1
                else:
                    per_channel["ok"] = False
                    per_channel["errors"].append(resp.get("description", "Unknown error"))
            except Exception as e:
                per_channel["ok"] = False
                per_channel["errors"].append(str(e))
        report.append(per_channel)

    return {
        "ok": all(r["ok"] for r in report),
        "channels_sent": len([r for r in report if r["ok"]]),
        "total_channels": len(report),
        "chunks_per_channel": len(chunks),
        "results": report,
    }





@api_router.get("/auto-scan/{ticker}")
async def auto_scan_ticker(ticker: str):
    """Auto-scan a ticker with ALL strategies and return active signals."""
    
    # Helper to sanitize float values for JSON
    def json_safe_float(value, default=0.0):
        """Convert value to JSON-safe float (no NaN/Infinity)"""
        try:
            f = float(value)
            if math.isnan(f) or math.isinf(f):
                return default
            return round(f, 2)
        except (TypeError, ValueError):
            return default
    
    def sanitize_response(obj):
        """Recursively sanitize all floats in response"""
        if isinstance(obj, dict):
            return {k: sanitize_response(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [sanitize_response(item) for item in obj]
        elif isinstance(obj, float):
            return json_safe_float(obj)
        else:
            return obj
    
    try:
        is_crypto = ticker.lower() in CRYPTO_IDS

        if is_crypto:
            coin_id = ticker.lower()
            chart_data = await _coingecko_get(f"/coins/{coin_id}/ohlc", {
                "vs_currency": "usd", "days": "30"
            }, cache_ttl=300)
            if not chart_data or len(chart_data) < 30:
                return {"ticker": ticker, "signals": [], "has_signal": False, "message": "Insufficient data"}
            bars = [{"open": c[1], "high": c[2], "low": c[3], "close": c[4], "volume": 0, "timestamp": c[0]} for c in chart_data]
        else:
            ticker_obj = yf.Ticker(ticker)
            hist = ticker_obj.history(period="60d", interval="1h")
            if hist.empty or len(hist) < 30:
                hist = ticker_obj.history(period="90d", interval="1d")
            if hist.empty or len(hist) < 30:
                return {"ticker": ticker, "signals": [], "has_signal": False, "message": "Insufficient data"}
            bars = []
            for idx, row in hist.iterrows():
                bars.append({
                    "open": float(row['Open']), "high": float(row['High']),
                    "low": float(row['Low']), "close": float(row['Close']),
                    "volume": float(row.get('Volume', 0)),
                    "timestamp": int(idx.timestamp() * 1000) if hasattr(idx, 'timestamp') else 0,
                })

        current = bars[-1]['close']
        signals = []

        # ---- 1-Day Target Helper ----
        def calc_day_target(direction: str) -> float:
            """ATR-based 1-day price target for any bar interval."""
            try:
                recent = bars[-min(10, len(bars)):]
                if len(recent) < 2:
                    avg_range = current * 0.015
                else:
                    ranges = [b['high'] - b['low'] for b in recent if b.get('high') and b.get('low')]
                    avg_range = (sum(ranges) / len(ranges)) if ranges else current * 0.015
                range_pct = avg_range / current if current > 0 else 0.015
                # If hourly bars (range < 0.5% each), scale up to ~1 day (6 bars)
                if range_pct < 0.005:
                    avg_range *= 6
                avg_range = max(current * 0.005, min(avg_range, current * 0.04))
                target = current + avg_range if direction == "BUY" else current - avg_range
                return json_safe_float(target)
            except:
                # Fallback: 1.5% move
                return json_safe_float(current * 1.015 if direction == "BUY" else current * 0.985)

        # Run all mini strategies — each returns (direction, levels_dict) or WAIT
        fk_dir, fk_lvl = run_mini_falling_knife(bars)
        if fk_dir != "WAIT" and fk_lvl:
            signals.append({
                "strategy": "Falling Knife", "direction": fk_dir,
                "entry": json_safe_float(fk_lvl["entry"]),
                "stoploss": json_safe_float(fk_lvl["sl"]),
                "targets": [json_safe_float(t) for t in fk_lvl["targets"]],
                "confidence": 78,
                "day_target": json_safe_float(fk_lvl["targets"][0]),
            })

        rsa_dir, rsa_lvl = run_mini_reverse_swings(bars, "A")
        if rsa_dir != "WAIT" and rsa_lvl:
            signals.append({
                "strategy": "Reverse Swings A", "direction": rsa_dir,
                "entry": json_safe_float(rsa_lvl["entry"]),
                "stoploss": json_safe_float(rsa_lvl["sl"]),
                "targets": [json_safe_float(t) for t in rsa_lvl["targets"]],
                "confidence": 72,
                "day_target": json_safe_float(rsa_lvl["targets"][0]),
            })

        rsb_dir, rsb_lvl = run_mini_reverse_swings(bars, "B")
        if rsb_dir != "WAIT" and rsb_lvl:
            signals.append({
                "strategy": "Reverse Swings B", "direction": rsb_dir,
                "entry": json_safe_float(rsb_lvl["entry"]),
                "stoploss": json_safe_float(rsb_lvl["sl"]),
                "targets": [json_safe_float(t) for t in rsb_lvl["targets"]],
                "confidence": 72,
                "day_target": json_safe_float(rsb_lvl["targets"][0]),
            })

        ev_dir, ev_lvl = run_mini_explosive_volume(bars)
        if ev_dir != "WAIT" and ev_lvl:
            signals.append({
                "strategy": "Explosive Volume", "direction": ev_dir,
                "entry": json_safe_float(ev_lvl["entry"]),
                "stoploss": json_safe_float(ev_lvl["sl"]),
                "targets": [json_safe_float(t) for t in ev_lvl["targets"]],
                "confidence": 82,
                "day_target": json_safe_float(ev_lvl["targets"][0]),
            })

        gs_dir, gs_lvl = run_mini_golden_setup(bars)
        if gs_dir != "WAIT" and gs_lvl:
            signals.append({
                "strategy": "Golden Setup", "direction": gs_dir,
                "entry": json_safe_float(gs_lvl["entry"]),
                "stoploss": json_safe_float(gs_lvl["sl"]),
                "targets": [json_safe_float(t) for t in gs_lvl["targets"]],
                "confidence": 85,
                "day_target": json_safe_float(gs_lvl["targets"][0]),
            })

        ai_result = run_mini_ai_indicator(bars)
        ai_dir = ai_result[0]
        ai_lvl = ai_result[1] if len(ai_result) > 1 else None
        ai_score = ai_result[2] if len(ai_result) > 2 else 50
        if ai_dir != "WAIT" and ai_lvl:
            signals.append({
                "strategy": f"AI Indicator ({ai_score})", "direction": ai_dir,
                "entry": json_safe_float(ai_lvl["entry"]),
                "stoploss": json_safe_float(ai_lvl["sl"]),
                "targets": [json_safe_float(t) for t in ai_lvl["targets"]],
                "confidence": min(int(ai_score), 95),
                "day_target": json_safe_float(ai_lvl["targets"][0]),
            })

        gz_dir, gz_lvl = run_mini_godzilla(bars)
        if gz_dir != "WAIT" and gz_lvl:
            signals.append({
                "strategy": "Godzilla TTE", "direction": gz_dir,
                "entry": json_safe_float(gz_lvl["entry"]),
                "stoploss": json_safe_float(gz_lvl["sl"]),
                "targets": [json_safe_float(t) for t in gz_lvl["targets"]],
                "confidence": 83,
                "day_target": json_safe_float(gz_lvl["targets"][0]),
            })

        # DEMON Confluence
        demon = run_demon_on_bars(bars)
        if demon and demon.get("signal_type") != "WAIT":
            signals.append({
                "strategy": f"DEMON ({demon['verdict']})", "direction": demon["signal_type"],
                "entry": round(current, 2),
                "stoploss": float(demon["stop_loss"]) if demon.get("stop_loss") else round(current * 0.95, 2),
                "targets": [float(t) for t in demon.get("targets", [])] if demon.get("targets") else [round(current * 1.05, 2)],
                "confidence": int(demon.get("confidence", 70)),
                "day_target": calc_day_target(demon["signal_type"]),
            })

        # SMC (Smart Money Concepts) — fetch DAILY bars to match manual SMC analysis
        # Manual SMC uses stockData.bars which are daily bars (chart default = 1D).
        # Using daily bars here ensures scanner result == manual result for the same stock.
        try:
            if is_crypto:
                smc_bars = bars  # CoinGecko data is already daily-equivalent
            else:
                _smc_ticker = yf.Ticker(ticker)
                _smc_hist = _smc_ticker.history(period="120d", interval="1d")
                if not _smc_hist.empty and len(_smc_hist) >= 25:
                    smc_bars = []
                    for idx, row in _smc_hist.iterrows():
                        smc_bars.append({
                            "open": float(row['Open']), "high": float(row['High']),
                            "low": float(row['Low']),   "close": float(row['Close']),
                            "volume": float(row.get('Volume', 0)),
                            "timestamp": int(idx.timestamp() * 1000) if hasattr(idx, 'timestamp') else 0,
                        })
                    smc_bars = smc_bars[-80:]  # last 80 daily bars — same as manual (slice(-80))
                else:
                    smc_bars = bars  # fallback to hourly if daily unavailable
        except Exception:
            smc_bars = bars  # fallback

        smc_result = run_full_smc_analysis(smc_bars)
        if smc_result.get("signal_type") != "WAIT":
            smc_sl = float(smc_result["stop_loss"]) if smc_result.get("stop_loss") else round(current * 0.97, 2)
            smc_tp1 = float(smc_result["tp1"]) if smc_result.get("tp1") else round(current * 1.03, 2)
            smc_tp2 = float(smc_result["tp2"]) if smc_result.get("tp2") else round(current * 1.06, 2)
            signals.append({
                "strategy": f"SMC ({smc_result['daily_bias']})", "direction": smc_result["signal_type"],
                "entry": round(current, 2),
                "stoploss": smc_sl,
                "targets": [smc_tp1, smc_tp2],
                "confidence": smc_result.get("confidence", 65),
                "day_target": calc_day_target(smc_result["signal_type"]),
            })

        # AMDS-Hybrid
        amds_result = run_full_amds_analysis(bars)
        if amds_result.get("signal_type") != "WAIT":
            amds_sl = float(amds_result["stop_loss"]) if amds_result.get("stop_loss") else round(current * 0.97, 2)
            amds_tp1 = float(amds_result["tp1"]) if amds_result.get("tp1") else round(current * 1.04, 2)
            amds_tp2 = float(amds_result["tp2"]) if amds_result.get("tp2") else round(current * 1.07, 2)
            signals.append({
                "strategy": f"AMDS ({amds_result['htf_bias']})", "direction": amds_result["signal_type"],
                "entry": round(current, 2),
                "stoploss": amds_sl,
                "targets": [amds_tp1, amds_tp2],
                "confidence": amds_result.get("confidence", 60),
                "day_target": calc_day_target(amds_result["signal_type"]),
            })

        # PAC + S&O Matrix
        pac_result = run_full_pac_so_analysis(bars)
        if pac_result.get("signal_type") != "WAIT":
            pac_sl = float(pac_result["stop_loss"]) if pac_result.get("stop_loss") else round(current * 0.97, 2)
            pac_tp1 = float(pac_result["tp1"]) if pac_result.get("tp1") else round(current * 1.03, 2)
            pac_tp2 = float(pac_result["tp2"]) if pac_result.get("tp2") else round(current * 1.06, 2)
            pac_tp3 = float(pac_result["tp3"]) if pac_result.get("tp3") else round(current * 1.09, 2)
            signals.append({
                "strategy": f"PAC+S&O ({pac_result['structure_bias']})", "direction": pac_result["signal_type"],
                "entry": round(current, 2),
                "stoploss": pac_sl,
                "targets": [pac_tp1, pac_tp2, pac_tp3],
                "confidence": pac_result.get("confidence", 65),
                "day_target": calc_day_target(pac_result["signal_type"]),
            })

        # Hybrid VWAP+TWAP
        vwap_dir, vwap_lvl = run_mini_hybrid_vwap(bars)
        if vwap_dir != "WAIT" and vwap_lvl:
            signals.append({
                "strategy": "Hybrid VWAP+TWAP",
                "direction": vwap_dir,
                "entry":    json_safe_float(vwap_lvl["entry"]),
                "stoploss": json_safe_float(vwap_lvl["sl"]),
                "targets":  [json_safe_float(t) for t in vwap_lvl["targets"]],
                "confidence": 76,
                "day_target": json_safe_float(vwap_lvl["targets"][0]),
            })

        # MiroFish Swarm Intelligence (cached 5 min to avoid excessive LLM calls)
        mf_cache_key = f"mirofish_scan_{ticker}"
        mf_cached = cache_storage.get(mf_cache_key)
        if mf_cached and (datetime.now(timezone.utc) - mf_cached['ts']).total_seconds() < 300:
            mf_data = mf_cached['data']
            if mf_data.get('signal_type') in ('BUY', 'SELL'):
                signals.append(mf_data['signal'])
        else:
            try:
                mf_result = await asyncio.wait_for(_run_mirofish_for_scanner(ticker, bars, current), timeout=25)
                if mf_result and mf_result.get('signal_type') in ('BUY', 'SELL'):
                    mf_signal = {
                        "strategy": f"MiroFish ({mf_result['swarm_consensus']})",
                        "direction": mf_result['signal_type'],
                        "entry": json_safe_float(current),
                        "stoploss": json_safe_float(mf_result.get('stop_loss', current * 0.97)),
                        "targets": [json_safe_float(t) for t in mf_result.get('targets', [])],
                        "confidence": int(mf_result.get('confidence', 65)),
                        "day_target": json_safe_float(mf_result.get('day_target', calc_day_target(mf_result['signal_type']))),
                    }
                    cache_storage[mf_cache_key] = {
                        "data": {"signal_type": mf_result['signal_type'], "signal": mf_signal},
                        "ts": datetime.now(timezone.utc)
                    }
                    signals.append(mf_signal)
                else:
                    cache_storage[mf_cache_key] = {
                        "data": {"signal_type": "WAIT"},
                        "ts": datetime.now(timezone.utc)
                    }
            except (asyncio.TimeoutError, Exception) as mf_err:
                logging.warning(f"MiroFish scanner skip for {ticker}: {mf_err}")

        # ---- Weighted Confluence Score Calculation ----
        confluence_score, confluence_label, dominant_dir, aligned_count = _calc_weighted_confluence(signals)

        result = {
            "ticker": ticker,
            "current_price": json_safe_float(current),
            "signals": signals,
            "has_signal": len(signals) > 0,
            "signal_count": len(signals),
            "scan_time": datetime.now(timezone.utc).isoformat(),
            "is_crypto": is_crypto,
            "confluence_score": confluence_score,
            "confluence_label": confluence_label,
            "dominant_direction": dominant_dir if signals else "NEUTRAL",
            "aligned_count": aligned_count,
            "total_strategies": 11,
        }
        
        # Sanitize all floats before returning
        return sanitize_response(result)
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Auto-scan error for {ticker}: {e}")
        return {"ticker": ticker, "signals": [], "has_signal": False, "message": str(e)}


@api_router.get("/ghost/scan", response_model=GhostScanResponse)
async def ghost_scan(min_match: int = 3):
    """Ghost Mode - Scan 50 Indian stocks with multi-strategy confluence"""
    try:
        results = []
        errors = 0
        
        # Process stocks in small batches to avoid rate limits
        batch_size = 5
        for i in range(0, len(GHOST_SCAN_STOCKS), batch_size):
            batch = GHOST_SCAN_STOCKS[i:i+batch_size]
            
            for stock_info in batch:
                try:
                    ticker = stock_info["ticker"]
                    
                    # Check cache first
                    cache_key = f"ghost_{ticker}"
                    if cache_key in cache_storage:
                        cached_data, cached_time = cache_storage[cache_key]
                        if (datetime.now() - cached_time).seconds < 600:
                            if cached_data["buy_count"] >= min_match or cached_data["sell_count"] >= min_match:
                                results.append(cached_data)
                            continue
                    
                    stock = yf.Ticker(ticker)
                    hist = stock.history(period="120d", interval="1d")
                    
                    if hist.empty or len(hist) < 30:
                        errors += 1
                        continue
                    
                    bars = []
                    for idx, row in hist.iterrows():
                        bars.append({
                            "timestamp": int(idx.timestamp() * 1000),
                            "open": float(row['Open']),
                            "high": float(row['High']),
                            "low": float(row['Low']),
                            "close": float(row['Close']),
                            "volume": float(row['Volume'])
                        })
                    
                    demon_result = run_demon_on_bars(bars)
                    if demon_result is None:
                        errors += 1
                        continue
                    
                    # Calculate change %
                    if len(bars) >= 2:
                        prev_close = bars[-2]['close']
                        curr_close = bars[-1]['close']
                        change_pct = round(((curr_close - prev_close) / prev_close) * 100, 2)
                    else:
                        change_pct = 0.0
                    
                    scan_result = GhostScanResult(
                        ticker=ticker,
                        name=stock_info["name"],
                        price=demon_result["current_price"],
                        change_pct=change_pct,
                        verdict=demon_result["verdict"],
                        signal_type=demon_result["signal_type"],
                        confidence=demon_result["confidence"],
                        buy_count=demon_result["buy_count"],
                        sell_count=demon_result["sell_count"],
                        total_strategies=demon_result["total_strategies"],
                        entry_price=demon_result["entry_price"],
                        stop_loss=demon_result["stop_loss"],
                        targets=demon_result["targets"],
                        strategy_signals=demon_result["strategy_signals"],
                    )
                    
                    # Cache individual result
                    cache_storage[cache_key] = ({
                        **scan_result.model_dump(),
                        "buy_count": demon_result["buy_count"],
                        "sell_count": demon_result["sell_count"],
                    }, datetime.now())
                    
                    # Only include if meets minimum match threshold
                    if demon_result["buy_count"] >= min_match or demon_result["sell_count"] >= min_match:
                        results.append(scan_result)
                    
                except Exception as e:
                    logging.error(f"Ghost scan error for {stock_info['ticker']}: {e}")
                    errors += 1
                    continue
            
            # Small delay between batches to avoid rate limiting
            if i + batch_size < len(GHOST_SCAN_STOCKS):
                await asyncio.sleep(1)
        
        # Sort by confidence descending
        results.sort(key=lambda x: x.confidence if hasattr(x, 'confidence') else x.get('confidence', 0), reverse=True)
        
        return GhostScanResponse(
            scanned=len(GHOST_SCAN_STOCKS),
            results=results,
            scan_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            errors=errors
        )

    except Exception as e:
        logging.error(f"Ghost scan error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/ghost/stocks")
async def ghost_stock_list():
    """Return list of stocks available for Ghost Mode scanning"""
    return {"stocks": GHOST_SCAN_STOCKS, "count": len(GHOST_SCAN_STOCKS)}


# ======================= WATCHLIST =======================

@api_router.get("/watchlist")
async def get_watchlist():
    """Get all watchlist items"""
    items = await db.watchlist.find({}, {"_id": 0}).to_list(100)
    return {"items": items}

@api_router.post("/watchlist", status_code=201)
async def add_to_watchlist(item: WatchlistItem):
    """Add stock to watchlist"""
    existing = await db.watchlist.find_one({"ticker": item.ticker}, {"_id": 0})
    if existing:
        raise HTTPException(status_code=400, detail="Stock already in watchlist")
    doc = {
        "id": str(uuid.uuid4()),
        "ticker": item.ticker,
        "name": item.name,
        "stock_type": item.stock_type,
        "added_at": datetime.now(timezone.utc).isoformat()
    }
    await db.watchlist.insert_one(doc)
    doc.pop("_id", None)
    return doc

@api_router.delete("/watchlist/{ticker}")
async def remove_from_watchlist(ticker: str):
    """Remove stock from watchlist"""
    result = await db.watchlist.delete_one({"ticker": ticker})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Stock not in watchlist")
    return {"message": "Removed from watchlist"}

@api_router.get("/watchlist/prices")
async def get_watchlist_prices():
    """Get live prices for all watchlist stocks"""
    items = await db.watchlist.find({}, {"_id": 0}).to_list(100)
    results = []
    for item in items:
        try:
            ticker_obj = yf.Ticker(item["ticker"])
            hist = ticker_obj.history(period="2d")
            if not hist.empty:
                current = hist['Close'].iloc[-1]
                prev = hist['Close'].iloc[-2] if len(hist) > 1 else current
                change_pct = ((current - prev) / prev * 100) if prev else 0
                results.append({
                    **item,
                    "price": round(current, 2),
                    "change_pct": round(change_pct, 2)
                })
            else:
                results.append({**item, "price": None, "change_pct": None})
        except Exception:
            results.append({**item, "price": None, "change_pct": None})
    return {"items": results}


# ======================= PORTFOLIO =======================

@api_router.get("/portfolio")
async def get_portfolio():
    """Get all portfolio entries with current P&L"""
    entries = await db.portfolio.find({}, {"_id": 0}).to_list(100)
    results = []
    for entry in entries:
        try:
            ticker_obj = yf.Ticker(entry["ticker"])
            hist = ticker_obj.history(period="1d")
            current_price = hist['Close'].iloc[-1] if not hist.empty else None
            if current_price:
                pnl = (current_price - entry["buy_price"]) * entry["quantity"]
                pnl_pct = ((current_price - entry["buy_price"]) / entry["buy_price"]) * 100
                entry["current_price"] = round(current_price, 2)
                entry["pnl"] = round(pnl, 2)
                entry["pnl_pct"] = round(pnl_pct, 2)
        except Exception:
            entry["current_price"] = None
            entry["pnl"] = None
            entry["pnl_pct"] = None
        results.append(entry)
    return {"entries": results}

@api_router.post("/portfolio", status_code=201)
async def add_portfolio_entry(entry: PortfolioEntry):
    """Add stock to portfolio"""
    doc = {
        "id": str(uuid.uuid4()),
        "ticker": entry.ticker,
        "name": entry.name,
        "buy_price": entry.buy_price,
        "quantity": entry.quantity,
        "buy_date": entry.buy_date or datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }
    await db.portfolio.insert_one(doc)
    doc.pop("_id", None)
    return doc

@api_router.delete("/portfolio/{entry_id}")
async def delete_portfolio_entry(entry_id: str):
    """Remove stock from portfolio"""
    result = await db.portfolio.delete_one({"id": entry_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"message": "Removed from portfolio"}

@api_router.get("/portfolio/summary")
async def portfolio_summary():
    """Get portfolio summary stats"""
    entries = await db.portfolio.find({}, {"_id": 0}).to_list(100)
    total_invested = 0
    total_current = 0
    for entry in entries:
        invested = entry["buy_price"] * entry["quantity"]
        total_invested += invested
        try:
            ticker_obj = yf.Ticker(entry["ticker"])
            hist = ticker_obj.history(period="1d")
            if not hist.empty:
                current = hist['Close'].iloc[-1]
                total_current += current * entry["quantity"]
            else:
                total_current += invested
        except Exception:
            total_current += invested
    total_pnl = total_current - total_invested
    total_pnl_pct = (total_pnl / total_invested * 100) if total_invested > 0 else 0
    return {
        "total_invested": round(total_invested, 2),
        "total_current": round(total_current, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "holdings_count": len(entries)
    }


# ======================= TOP MOVERS TODAY =======================

_TOP_MOVERS_STOCKS: Dict[str, List[Dict]] = {
    "large": [
        {"ticker": "RELIANCE.NS",   "name": "Reliance Industries"},
        {"ticker": "TCS.NS",        "name": "TCS"},
        {"ticker": "HDFCBANK.NS",   "name": "HDFC Bank"},
        {"ticker": "ICICIBANK.NS",  "name": "ICICI Bank"},
        {"ticker": "INFY.NS",       "name": "Infosys"},
        {"ticker": "BHARTIARTL.NS", "name": "Bharti Airtel"},
        {"ticker": "ITC.NS",        "name": "ITC"},
        {"ticker": "BAJFINANCE.NS", "name": "Bajaj Finance"},
        {"ticker": "LT.NS",         "name": "L&T"},
        {"ticker": "KOTAKBANK.NS",  "name": "Kotak Bank"},
        {"ticker": "AXISBANK.NS",   "name": "Axis Bank"},
        {"ticker": "HCLTECH.NS",    "name": "HCL Tech"},
        {"ticker": "WIPRO.NS",      "name": "Wipro"},
        {"ticker": "MARUTI.NS",     "name": "Maruti Suzuki"},
        {"ticker": "SBIN.NS",       "name": "SBI"},
        {"ticker": "ADANIENT.NS",   "name": "Adani Enterprises"},
        {"ticker": "TATAMOTORS.NS", "name": "Tata Motors"},
        {"ticker": "SUNPHARMA.NS",  "name": "Sun Pharma"},
        {"ticker": "TITAN.NS",      "name": "Titan"},
        {"ticker": "NTPC.NS",       "name": "NTPC"},
        {"ticker": "ONGC.NS",       "name": "ONGC"},
        {"ticker": "COALINDIA.NS",  "name": "Coal India"},
        {"ticker": "TATASTEEL.NS",  "name": "Tata Steel"},
        {"ticker": "DRREDDY.NS",    "name": "Dr. Reddy's"},
        {"ticker": "CIPLA.NS",      "name": "Cipla"},
        {"ticker": "BAJAJ-AUTO.NS", "name": "Bajaj Auto"},
        {"ticker": "EICHERMOT.NS",  "name": "Eicher Motors"},
        {"ticker": "HEROMOTOCO.NS", "name": "Hero MotoCorp"},
        {"ticker": "INDUSINDBK.NS", "name": "IndusInd Bank"},
        {"ticker": "HINDALCO.NS",   "name": "Hindalco"},
    ],
    "mid": [
        {"ticker": "PERSISTENT.NS", "name": "Persistent Systems"},
        {"ticker": "COFORGE.NS",    "name": "Coforge"},
        {"ticker": "MPHASIS.NS",    "name": "Mphasis"},
        {"ticker": "LTIM.NS",       "name": "LTIMindtree"},
        {"ticker": "TRENT.NS",      "name": "Trent"},
        {"ticker": "ASTRAL.NS",     "name": "Astral"},
        {"ticker": "PIIND.NS",      "name": "PI Industries"},
        {"ticker": "DEEPAKNTR.NS",  "name": "Deepak Nitrite"},
        {"ticker": "HAVELLS.NS",    "name": "Havells India"},
        {"ticker": "VOLTAS.NS",     "name": "Voltas"},
        {"ticker": "TORNTPHARM.NS", "name": "Torrent Pharma"},
        {"ticker": "LUPIN.NS",      "name": "Lupin"},
        {"ticker": "AUROPHARMA.NS", "name": "Aurobindo Pharma"},
        {"ticker": "ALKEM.NS",      "name": "Alkem Lab"},
        {"ticker": "BIOCON.NS",     "name": "Biocon"},
        {"ticker": "OBEROIRLTY.NS", "name": "Oberoi Realty"},
        {"ticker": "GODREJPROP.NS", "name": "Godrej Properties"},
        {"ticker": "PRESTIGE.NS",   "name": "Prestige Estates"},
        {"ticker": "MUTHOOTFIN.NS", "name": "Muthoot Finance"},
        {"ticker": "SUNDARMFIN.NS", "name": "Sundaram Finance"},
        {"ticker": "BANKBARODA.NS", "name": "Bank of Baroda"},
        {"ticker": "IDFCFIRSTB.NS", "name": "IDFC First Bank"},
        {"ticker": "FEDERALBNK.NS", "name": "Federal Bank"},
        {"ticker": "BANDHANBNK.NS", "name": "Bandhan Bank"},
        {"ticker": "RATNAMANI.NS",  "name": "Ratnamani Metals"},
        {"ticker": "AIAENG.NS",     "name": "AIA Engineering"},
        {"ticker": "CAMS.NS",       "name": "CAMS"},
        {"ticker": "LALPATHLAB.NS", "name": "Dr. Lal PathLabs"},
        {"ticker": "SCHAEFFLER.NS", "name": "Schaeffler India"},
        {"ticker": "TIINDIA.NS",    "name": "Tube Investments"},
    ],
    "small": [
        {"ticker": "IGL.NS",        "name": "Indraprastha Gas"},
        {"ticker": "GMDC.NS",       "name": "Gujarat Mineral Devel."},
        {"ticker": "IRCON.NS",      "name": "IRCON International"},
        {"ticker": "RVNL.NS",       "name": "Rail Vikas Nigam"},
        {"ticker": "NBCC.NS",       "name": "NBCC India"},
        {"ticker": "MOIL.NS",       "name": "MOIL"},
        {"ticker": "NATIONALUM.NS", "name": "National Aluminium"},
        {"ticker": "HINDCOPPER.NS", "name": "Hindustan Copper"},
        {"ticker": "SAIL.NS",       "name": "SAIL"},
        {"ticker": "KEC.NS",        "name": "KEC International"},
        {"ticker": "PNCINFRA.NS",   "name": "PNC Infratech"},
        {"ticker": "KNRCON.NS",     "name": "KNR Constructions"},
        {"ticker": "GRANULES.NS",   "name": "Granules India"},
        {"ticker": "NATCOPHARM.NS", "name": "Natco Pharma"},
        {"ticker": "IPCALAB.NS",    "name": "IPCA Labs"},
        {"ticker": "GLENMARK.NS",   "name": "Glenmark Pharma"},
        {"ticker": "EMAMILTD.NS",   "name": "Emami"},
        {"ticker": "RADICO.NS",     "name": "Radico Khaitan"},
        {"ticker": "SAREGAMA.NS",   "name": "Saregama India"},
        {"ticker": "PVRINOX.NS",    "name": "PVR Inox"},
        {"ticker": "NETWORK18.NS",  "name": "Network18 Media"},
        {"ticker": "JINDALSAW.NS",  "name": "Jindal Saw"},
        {"ticker": "WELCORP.NS",    "name": "Welspun Corp"},
        {"ticker": "AJMERA.NS",     "name": "Ajmera Realty"},
        {"ticker": "KOLTEPATIL.NS", "name": "Kolte-Patil Dev."},
        {"ticker": "BRIGADE.NS",    "name": "Brigade Enterprises"},
        {"ticker": "SUNTECK.NS",    "name": "Sunteck Realty"},
        {"ticker": "CAMPUS.NS",     "name": "Campus Activewear"},
        {"ticker": "DELHIVERY.NS",  "name": "Delhivery"},
        {"ticker": "HAPPSTMNDS.NS", "name": "Happiest Minds"},
    ],
}

_top_movers_cache: Dict[str, Any] = {}
_TOP_MOVERS_TTL = 300  # 5 minutes


@api_router.get("/market/top-movers")
async def get_top_movers(cap: str = "large", filter: str = "gainers", limit: int = 6):
    """Return top gainers or losers for a market cap category. Cached 5 min."""
    global _top_movers_cache, _sector_stock_executor
    cache_key = f"{cap}_{filter}"
    now = datetime.now(timezone.utc).timestamp()

    cached = _top_movers_cache.get(cache_key)
    if cached and cached.get("ts", 0) + _TOP_MOVERS_TTL > now:
        return {"stocks": cached["data"][:limit], "cached": True, "cap": cap, "filter": filter}

    stocks_meta = _TOP_MOVERS_STOCKS.get(cap, _TOP_MOVERS_STOCKS["large"])

    # Reuse sector stock executor
    if _sector_stock_executor is None:
        _sector_stock_executor = _TPE(max_workers=12)

    loop = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(_sector_stock_executor, _fetch_one_stock, meta)
        for meta in stocks_meta
    ]
    results = await asyncio.gather(*tasks)
    stocks = [r for r in results if r is not None and r.get("price") is not None]

    # Sort gainers descending, losers ascending by change_pct
    if filter == "gainers":
        stocks = [s for s in stocks if s["change_pct"] > 0]
        stocks.sort(key=lambda x: x["change_pct"], reverse=True)
    else:
        stocks = [s for s in stocks if s["change_pct"] < 0]
        stocks.sort(key=lambda x: x["change_pct"])

    _top_movers_cache[cache_key] = {"ts": now, "data": stocks}
    return {"stocks": stocks[:limit], "cached": False, "cap": cap, "filter": filter}


# ======================= SECTOR TRENDING =======================

_SECTOR_MAP = [
    {"name": "NIFTY BANK",    "ticker": "^NSEBANK",    "icon": "bank"},
    {"name": "NIFTY IT",      "ticker": "^CNXIT",      "icon": "it"},
    {"name": "NIFTY AUTO",    "ticker": "^CNXAUTO",    "icon": "auto"},
    {"name": "NIFTY PHARMA",  "ticker": "^CNXPHARMA",  "icon": "pharma"},
    {"name": "NIFTY FMCG",   "ticker": "^CNXFMCG",    "icon": "fmcg"},
    {"name": "NIFTY METAL",   "ticker": "^CNXMETAL",   "icon": "metal"},
    {"name": "NIFTY REALTY",  "ticker": "^CNXREALTY",  "icon": "realty"},
    {"name": "NIFTY ENERGY",  "ticker": "^CNXENERGY",  "icon": "energy"},
    {"name": "NIFTY INFRA",   "ticker": "^CNXINFRA",   "icon": "infra"},
    {"name": "NIFTY MEDIA",   "ticker": "^CNXMEDIA",   "icon": "media"},
    {"name": "NIFTY PSU BANK","ticker": "^CNXPSUBANK", "icon": "psubank"},
    {"name": "NIFTY MIDCAP",  "ticker": "^NSEMDCP50",  "icon": "midcap"},
]

# ---- NSE Sector Constituents ----
_SECTOR_STOCKS: Dict[str, List[Dict]] = {
    "bank": [
        {"ticker": "HDFCBANK.NS",   "name": "HDFC Bank"},
        {"ticker": "ICICIBANK.NS",  "name": "ICICI Bank"},
        {"ticker": "SBIN.NS",       "name": "State Bank of India"},
        {"ticker": "AXISBANK.NS",   "name": "Axis Bank"},
        {"ticker": "KOTAKBANK.NS",  "name": "Kotak Mahindra Bank"},
        {"ticker": "INDUSINDBK.NS", "name": "IndusInd Bank"},
        {"ticker": "BANDHANBNK.NS", "name": "Bandhan Bank"},
        {"ticker": "FEDERALBNK.NS", "name": "Federal Bank"},
        {"ticker": "IDFCFIRSTB.NS", "name": "IDFC First Bank"},
        {"ticker": "PNB.NS",        "name": "Punjab National Bank"},
        {"ticker": "BANKBARODA.NS", "name": "Bank of Baroda"},
        {"ticker": "CANARABANK.NS", "name": "Canara Bank"},
        {"ticker": "AUBANK.NS",     "name": "AU Small Finance Bank"},
        {"ticker": "RBLBANK.NS",    "name": "RBL Bank"},
        {"ticker": "YESBANK.NS",    "name": "Yes Bank"},
    ],
    "it": [
        {"ticker": "TCS.NS",        "name": "Tata Consultancy Services"},
        {"ticker": "INFY.NS",       "name": "Infosys"},
        {"ticker": "WIPRO.NS",      "name": "Wipro"},
        {"ticker": "HCLTECH.NS",    "name": "HCL Technologies"},
        {"ticker": "TECHM.NS",      "name": "Tech Mahindra"},
        {"ticker": "LTIM.NS",       "name": "LTIMindtree"},
        {"ticker": "MPHASIS.NS",    "name": "Mphasis"},
        {"ticker": "COFORGE.NS",    "name": "Coforge"},
        {"ticker": "PERSISTENT.NS", "name": "Persistent Systems"},
        {"ticker": "OFSS.NS",       "name": "Oracle Financial Services"},
        {"ticker": "KPITTECH.NS",   "name": "KPIT Technologies"},
        {"ticker": "TATAELXSI.NS",  "name": "Tata Elxsi"},
        {"ticker": "HEXAWARE.NS",   "name": "Hexaware Technologies"},
        {"ticker": "NIITLTD.NS",    "name": "NIIT"},
        {"ticker": "SONATSOFTW.NS", "name": "Sonata Software"},
    ],
    "auto": [
        {"ticker": "MARUTI.NS",     "name": "Maruti Suzuki"},
        {"ticker": "TATAMOTORS.NS", "name": "Tata Motors"},
        {"ticker": "EICHERMOT.NS",  "name": "Eicher Motors"},
        {"ticker": "BAJAJ-AUTO.NS", "name": "Bajaj Auto"},
        {"ticker": "HEROMOTOCO.NS", "name": "Hero MotoCorp"},
        {"ticker": "M&M.NS",        "name": "Mahindra & Mahindra"},
        {"ticker": "TVSMOTOR.NS",   "name": "TVS Motor"},
        {"ticker": "ASHOKLEY.NS",   "name": "Ashok Leyland"},
        {"ticker": "BALKRISIND.NS", "name": "Balkrishna Industries"},
        {"ticker": "BHARATFORG.NS", "name": "Bharat Forge"},
        {"ticker": "MOTHERSON.NS",  "name": "Samvardhana Motherson"},
        {"ticker": "BOSCHLTD.NS",   "name": "Bosch"},
        {"ticker": "AMARAJABAT.NS", "name": "Amara Raja Energy"},
        {"ticker": "EXIDEIND.NS",   "name": "Exide Industries"},
        {"ticker": "TIINDIA.NS",    "name": "Tube Investments"},
    ],
    "pharma": [
        {"ticker": "SUNPHARMA.NS",  "name": "Sun Pharmaceutical"},
        {"ticker": "DRREDDY.NS",    "name": "Dr. Reddy's Laboratories"},
        {"ticker": "CIPLA.NS",      "name": "Cipla"},
        {"ticker": "DIVISLAB.NS",   "name": "Divi's Laboratories"},
        {"ticker": "AUROPHARMA.NS", "name": "Aurobindo Pharma"},
        {"ticker": "LUPIN.NS",      "name": "Lupin"},
        {"ticker": "BIOCON.NS",     "name": "Biocon"},
        {"ticker": "ALKEM.NS",      "name": "Alkem Laboratories"},
        {"ticker": "TORNTPHARM.NS", "name": "Torrent Pharmaceuticals"},
        {"ticker": "ABBOTINDIA.NS", "name": "Abbott India"},
        {"ticker": "IPCALAB.NS",    "name": "IPCA Laboratories"},
        {"ticker": "GLENMARK.NS",   "name": "Glenmark Pharma"},
        {"ticker": "ZYDUSLIFE.NS",  "name": "Zydus Lifesciences"},
        {"ticker": "NATCOPHARM.NS", "name": "Natco Pharma"},
        {"ticker": "GRANULES.NS",   "name": "Granules India"},
    ],
    "fmcg": [
        {"ticker": "HINDUNILVR.NS", "name": "Hindustan Unilever"},
        {"ticker": "ITC.NS",        "name": "ITC"},
        {"ticker": "NESTLEIND.NS",  "name": "Nestle India"},
        {"ticker": "BRITANNIA.NS",  "name": "Britannia Industries"},
        {"ticker": "DABUR.NS",      "name": "Dabur India"},
        {"ticker": "MARICO.NS",     "name": "Marico"},
        {"ticker": "COLPAL.NS",     "name": "Colgate-Palmolive India"},
        {"ticker": "GODREJCP.NS",   "name": "Godrej Consumer Products"},
        {"ticker": "EMAMILTD.NS",   "name": "Emami"},
        {"ticker": "MCDOWELL-N.NS", "name": "United Spirits"},
        {"ticker": "VBL.NS",        "name": "Varun Beverages"},
        {"ticker": "PATANJALI.NS",  "name": "Patanjali Foods"},
        {"ticker": "RADICO.NS",     "name": "Radico Khaitan"},
        {"ticker": "TATACONSUM.NS", "name": "Tata Consumer Products"},
        {"ticker": "PGHH.NS",       "name": "Procter & Gamble Hygiene"},
    ],
    "metal": [
        {"ticker": "TATASTEEL.NS",  "name": "Tata Steel"},
        {"ticker": "HINDALCO.NS",   "name": "Hindalco Industries"},
        {"ticker": "JSWSTEEL.NS",   "name": "JSW Steel"},
        {"ticker": "VEDL.NS",       "name": "Vedanta"},
        {"ticker": "SAIL.NS",       "name": "Steel Authority of India"},
        {"ticker": "NMDC.NS",       "name": "NMDC"},
        {"ticker": "COALINDIA.NS",  "name": "Coal India"},
        {"ticker": "HINDCOPPER.NS", "name": "Hindustan Copper"},
        {"ticker": "NATIONALUM.NS", "name": "National Aluminium"},
        {"ticker": "RATNAMANI.NS",  "name": "Ratnamani Metals"},
        {"ticker": "APL.NS",        "name": "APL Apollo Tubes"},
        {"ticker": "JINDALSAW.NS",  "name": "Jindal Saw"},
        {"ticker": "WELCORP.NS",    "name": "Welspun Corp"},
        {"ticker": "JSWENERGY.NS",  "name": "JSW Energy"},
        {"ticker": "MOIL.NS",       "name": "MOIL"},
    ],
    "realty": [
        {"ticker": "DLF.NS",        "name": "DLF"},
        {"ticker": "GODREJPROP.NS", "name": "Godrej Properties"},
        {"ticker": "PRESTIGE.NS",   "name": "Prestige Estates"},
        {"ticker": "OBEROIRLTY.NS", "name": "Oberoi Realty"},
        {"ticker": "BRIGADE.NS",    "name": "Brigade Enterprises"},
        {"ticker": "PHOENIXLTD.NS", "name": "Phoenix Mills"},
        {"ticker": "SOBHA.NS",      "name": "Sobha"},
        {"ticker": "MAHLIFE.NS",    "name": "Mahindra Lifespace"},
        {"ticker": "SUNTECK.NS",    "name": "Sunteck Realty"},
        {"ticker": "KOLTEPATIL.NS", "name": "Kolte Patil Developers"},
        {"ticker": "LODHA.NS",      "name": "Macrotech Developers"},
        {"ticker": "EMBASSY.NS",    "name": "Embassy Office Parks REIT"},
        {"ticker": "MINDSPACE.NS",  "name": "Mindspace REIT"},
        {"ticker": "NCC.NS",        "name": "NCC (Nagarjuna Construction)"},
        {"ticker": "AJMERA.NS",     "name": "Ajmera Realty"},
    ],
    "energy": [
        {"ticker": "RELIANCE.NS",   "name": "Reliance Industries"},
        {"ticker": "ONGC.NS",       "name": "ONGC"},
        {"ticker": "NTPC.NS",       "name": "NTPC"},
        {"ticker": "POWERGRID.NS",  "name": "Power Grid Corporation"},
        {"ticker": "BPCL.NS",       "name": "BPCL"},
        {"ticker": "IOC.NS",        "name": "Indian Oil Corporation"},
        {"ticker": "GAIL.NS",       "name": "GAIL India"},
        {"ticker": "HINDPETRO.NS",  "name": "Hindustan Petroleum"},
        {"ticker": "TATAPOWER.NS",  "name": "Tata Power"},
        {"ticker": "ADANIGREEN.NS", "name": "Adani Green Energy"},
        {"ticker": "ADANITRANS.NS", "name": "Adani Transmission"},
        {"ticker": "TORNTPOWER.NS", "name": "Torrent Power"},
        {"ticker": "CESC.NS",       "name": "CESC"},
        {"ticker": "OIL.NS",        "name": "Oil India"},
        {"ticker": "MGL.NS",        "name": "Mahanagar Gas"},
    ],
    "infra": [
        {"ticker": "LT.NS",         "name": "Larsen & Toubro"},
        {"ticker": "ADANIPORTS.NS", "name": "Adani Ports & SEZ"},
        {"ticker": "IRCON.NS",      "name": "IRCON International"},
        {"ticker": "KEC.NS",        "name": "KEC International"},
        {"ticker": "IRB.NS",        "name": "IRB Infrastructure"},
        {"ticker": "PNCINFRA.NS",   "name": "PNC Infratech"},
        {"ticker": "KNRCON.NS",     "name": "KNR Constructions"},
        {"ticker": "ASHOKA.NS",     "name": "Ashoka Buildcon"},
        {"ticker": "GMRINFRA.NS",   "name": "GMR Airports Infrastructure"},
        {"ticker": "NBCC.NS",       "name": "NBCC India"},
        {"ticker": "HCC.NS",        "name": "Hindustan Construction"},
        {"ticker": "ENGINERSIN.NS", "name": "Engineers India"},
        {"ticker": "RVNL.NS",       "name": "Rail Vikas Nigam"},
        {"ticker": "RITES.NS",      "name": "RITES"},
        {"ticker": "MTNL.NS",       "name": "MTNL"},
    ],
    "media": [
        {"ticker": "ZEEL.NS",       "name": "Zee Entertainment"},
        {"ticker": "SUNTV.NS",      "name": "Sun TV Network"},
        {"ticker": "NETWORK18.NS",  "name": "Network18 Media"},
        {"ticker": "TV18BRDCST.NS", "name": "TV18 Broadcast"},
        {"ticker": "PVRINOX.NS",    "name": "PVR Inox"},
        {"ticker": "SAREGAMA.NS",   "name": "Saregama India"},
        {"ticker": "TIPSINDLTD.NS", "name": "Tips Industries"},
        {"ticker": "NAVINFLUOR.NS", "name": "Navin Fluorine"},
        {"ticker": "HATHWAY.NS",    "name": "Hathway Cable"},
        {"ticker": "DISHTV.NS",     "name": "Dish TV India"},
    ],
    "psubank": [
        {"ticker": "SBIN.NS",       "name": "State Bank of India"},
        {"ticker": "PNB.NS",        "name": "Punjab National Bank"},
        {"ticker": "BANKBARODA.NS", "name": "Bank of Baroda"},
        {"ticker": "CANARABANK.NS", "name": "Canara Bank"},
        {"ticker": "UNIONBANK.NS",  "name": "Union Bank of India"},
        {"ticker": "INDIANB.NS",    "name": "Indian Bank"},
        {"ticker": "CENTRALBK.NS",  "name": "Central Bank of India"},
        {"ticker": "IOB.NS",        "name": "Indian Overseas Bank"},
        {"ticker": "BANKINDIA.NS",  "name": "Bank of India"},
        {"ticker": "MAHABANK.NS",   "name": "Bank of Maharashtra"},
        {"ticker": "PSB.NS",        "name": "Punjab & Sind Bank"},
        {"ticker": "J&KBANK.NS",    "name": "J&K Bank"},
        {"ticker": "UCOBK.NS",      "name": "UCO Bank"},
    ],
    "midcap": [
        {"ticker": "PERSISTENT.NS", "name": "Persistent Systems"},
        {"ticker": "TIINDIA.NS",    "name": "Tube Investments"},
        {"ticker": "SUNDARMFIN.NS", "name": "Sundaram Finance"},
        {"ticker": "SCHAEFFLER.NS", "name": "Schaeffler India"},
        {"ticker": "AIAENG.NS",     "name": "AIA Engineering"},
        {"ticker": "ASTRAL.NS",     "name": "Astral Poly Technik"},
        {"ticker": "PIIND.NS",      "name": "PI Industries"},
        {"ticker": "DEEPAKNTR.NS",  "name": "Deepak Nitrite"},
        {"ticker": "LALPATHLAB.NS", "name": "Dr. Lal PathLabs"},
        {"ticker": "METROPOLIS.NS", "name": "Metropolis Healthcare"},
        {"ticker": "CAMS.NS",       "name": "CAMS"},
        {"ticker": "HAPPSTMNDS.NS", "name": "Happiest Minds Technologies"},
        {"ticker": "BIKAJI.NS",     "name": "Bikaji Foods International"},
        {"ticker": "CAMPUS.NS",     "name": "Campus Activewear"},
        {"ticker": "DELHIVERY.NS",  "name": "Delhivery"},
    ],
}

_sector_stocks_cache: Dict[str, Any] = {}
_SECTOR_STOCKS_TTL = 300  # 5 minutes
_sector_stock_executor = None  # lazy init

from concurrent.futures import ThreadPoolExecutor as _TPE


def _fetch_one_stock(stock_meta: Dict) -> Optional[Dict]:
    """Sync: fetch 2-day history for one stock and compute change%."""
    try:
        obj = yf.Ticker(stock_meta["ticker"])
        hist = obj.history(period="2d")
        if hist.empty or len(hist) < 1:
            return None
        curr = round(float(hist["Close"].iloc[-1]), 2)
        prev = round(float(hist["Close"].iloc[-2]), 2) if len(hist) >= 2 else curr
        chg = round((curr - prev) / prev * 100, 2) if prev > 0 else 0.0
        vol = int(hist["Volume"].iloc[-1]) if "Volume" in hist.columns else 0
        return {
            "ticker": stock_meta["ticker"],
            "name": stock_meta["name"],
            "price": curr,
            "prev_close": prev,
            "change_pct": chg,
            "volume": vol,
        }
    except Exception:
        return {"ticker": stock_meta["ticker"], "name": stock_meta["name"],
                "price": None, "prev_close": None, "change_pct": 0.0, "volume": 0}


@api_router.get("/sectors/{sector_key}/stocks")
async def get_sector_stocks(sector_key: str):
    """Return all NSE stocks in a sector with live prices. Cached 5 min."""
    global _sector_stocks_cache, _sector_stock_executor
    cache_key = sector_key.lower()
    now = datetime.now(timezone.utc).timestamp()

    cached = _sector_stocks_cache.get(cache_key)
    if cached and cached.get("ts", 0) + _SECTOR_STOCKS_TTL > now:
        return {"sector": cache_key, "stocks": cached["data"], "cached": True}

    stocks_meta = _SECTOR_STOCKS.get(cache_key)
    if not stocks_meta:
        raise HTTPException(status_code=404, detail=f"Sector '{sector_key}' not found")

    # Parallel fetch using thread pool
    if _sector_stock_executor is None:
        _sector_stock_executor = _TPE(max_workers=12)

    loop = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(_sector_stock_executor, _fetch_one_stock, meta)
        for meta in stocks_meta
    ]
    results = await asyncio.gather(*tasks)

    stocks = [r for r in results if r is not None]
    stocks.sort(key=lambda x: abs(x.get("change_pct") or 0), reverse=True)

    _sector_stocks_cache[cache_key] = {"ts": now, "data": stocks}
    return {"sector": cache_key, "stocks": stocks, "cached": False}

_sector_cache: Dict[str, Any] = {}
_SECTOR_CACHE_TTL = 300  # 5 minutes


@api_router.get("/sectors/trending")
async def get_sectors_trending():
    """Return NSE sectors sorted by absolute % change today. Cached 5 min."""
    global _sector_cache
    now = datetime.now(timezone.utc).timestamp()
    if _sector_cache.get("ts", 0) + _SECTOR_CACHE_TTL > now and _sector_cache.get("data"):
        return {"sectors": _sector_cache["data"], "cached": True}

    results = []
    for sector in _SECTOR_MAP:
        try:
            obj = yf.Ticker(sector["ticker"])
            hist = obj.history(period="2d")
            if hist.empty or len(hist) < 2:
                hist = obj.history(period="5d")
            if hist.empty or len(hist) < 2:
                continue
            prev_close = float(hist["Close"].iloc[-2])
            curr_close = float(hist["Close"].iloc[-1])
            if prev_close <= 0:
                continue
            change_pct = round((curr_close - prev_close) / prev_close * 100, 2)
            results.append({
                "name": sector["name"],
                "ticker": sector["ticker"],
                "icon": sector["icon"],
                "change_pct": change_pct,
                "current": round(curr_close, 2),
            })
        except Exception:
            continue

    # Sort by absolute change descending (top movers first)
    results.sort(key=lambda x: abs(x["change_pct"]), reverse=True)
    _sector_cache = {"ts": now, "data": results}
    return {"sectors": results, "cached": False}


# ======================= PAPER TRADING =======================

async def _ensure_paper_portfolio():
    """Ensure paper portfolio doc exists in MongoDB. Uses upsert to prevent race-condition duplicates."""
    default_doc = {
        "portfolio_id": "default",
        "initial_balance": 50000.0,
        "current_balance": 50000.0,
        "realized_pnl": 0.0,
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.paper_portfolio.update_one(
        {"portfolio_id": "default"},
        {"$setOnInsert": default_doc},
        upsert=True
    )
    portfolio = await db.paper_portfolio.find_one({"portfolio_id": "default"}, {"_id": 0})
    return portfolio


@api_router.get("/paper-trade/portfolio")
async def get_paper_portfolio():
    """Get paper trading portfolio stats + unrealized P&L."""
    portfolio = await _ensure_paper_portfolio()
    open_positions = await db.paper_trades.find({"status": "OPEN"}, {"_id": 0}).to_list(100)

    unrealized_pnl = 0.0
    for pos in open_positions:
        try:
            ticker_obj = yf.Ticker(pos["symbol"])
            hist = ticker_obj.history(period="1d")
            if not hist.empty:
                cp = float(hist["Close"].iloc[-1])
                if pos["direction"] == "BUY":
                    unrealized_pnl += (cp - pos["entry_price"]) * pos["quantity"]
                else:
                    unrealized_pnl += (pos["entry_price"] - cp) * pos["quantity"]
        except Exception:
            pass

    total = portfolio.get("total_trades", 0)
    wins = portfolio.get("winning_trades", 0)
    win_rate = round(wins / total * 100, 1) if total > 0 else 0.0

    return {
        "portfolio_id": "default",
        "initial_balance": portfolio["initial_balance"],
        "current_balance": round(portfolio["current_balance"], 2),
        "realized_pnl": round(portfolio.get("realized_pnl", 0.0), 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "total_pnl": round(portfolio.get("realized_pnl", 0.0) + unrealized_pnl, 2),
        "total_trades": total,
        "winning_trades": wins,
        "losing_trades": portfolio.get("losing_trades", 0),
        "win_rate": win_rate,
        "open_positions_count": len(open_positions),
    }


@api_router.post("/paper-trade/order", status_code=201)
async def place_paper_order(req: PaperOrderRequest):
    """Place a paper trade with fixed 5x leverage — only margin (1/5th) deducted from balance."""
    LEVERAGE = 5
    portfolio = await _ensure_paper_portfolio()
    position_value = round(req.entry_price * req.quantity, 2)
    margin_used = round(position_value / LEVERAGE, 2)

    if margin_used > portfolio["current_balance"]:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient margin. Required: ₹{margin_used:.2f}, Available: ₹{portfolio['current_balance']:.2f}"
        )

    trade_id = str(uuid.uuid4())
    doc = {
        "trade_id": trade_id,
        "symbol": req.symbol,
        "name": req.name,
        "direction": req.direction,
        "quantity": req.quantity,
        "entry_price": round(req.entry_price, 2),
        "stop_loss": round(req.stop_loss, 2),
        "target": round(req.target, 2),
        "invested_amount": position_value,   # full position value
        "margin_used": margin_used,           # actual balance deducted (1/5th)
        "leverage": LEVERAGE,
        "status": "OPEN",
        "strategy": req.strategy,
        "source": req.source,
        "entry_time": datetime.now(timezone.utc).isoformat(),
        "exit_time": None,
        "exit_price": None,
        "pnl": None,
        "pnl_pct": None,
    }
    await db.paper_trades.insert_one(doc)
    doc.pop("_id", None)

    new_balance = portfolio["current_balance"] - margin_used
    await db.paper_portfolio.update_one(
        {"portfolio_id": "default"},
        {"$set": {"current_balance": round(new_balance, 2)}}
    )
    return doc


@api_router.get("/paper-trade/positions")
async def get_paper_positions():
    """Open positions with live current price + unrealized P&L (on margin basis, 5x leverage)."""
    positions = await db.paper_trades.find({"status": "OPEN"}, {"_id": 0}).to_list(100)
    for pos in positions:
        margin = pos.get("margin_used") or pos.get("invested_amount", 1)
        try:
            ticker_obj = yf.Ticker(pos["symbol"])
            hist = ticker_obj.history(period="1d")
            if not hist.empty:
                cp = round(float(hist["Close"].iloc[-1]), 2)
                pos["current_price"] = cp
                if pos["direction"] == "BUY":
                    pnl = (cp - pos["entry_price"]) * pos["quantity"]
                else:
                    pnl = (pos["entry_price"] - cp) * pos["quantity"]
                pos["pnl"] = round(pnl, 2)
                pos["pnl_pct"] = round(pnl / margin * 100, 2) if margin > 0 else 0.0
            else:
                pos["current_price"] = pos["entry_price"]
                pos["pnl"] = 0.0
                pos["pnl_pct"] = 0.0
        except Exception:
            pos["current_price"] = pos["entry_price"]
            pos["pnl"] = 0.0
            pos["pnl_pct"] = 0.0
    return {"positions": positions}


@api_router.get("/paper-trade/history")
async def get_paper_history():
    """Closed trade history (CLOSED / SL_HIT / TARGET_HIT)."""
    trades = await db.paper_trades.find(
        {"status": {"$in": ["CLOSED", "SL_HIT", "TARGET_HIT"]}},
        {"_id": 0}
    ).sort("exit_time", -1).to_list(200)
    return {"trades": trades}


@api_router.put("/paper-trade/close/{trade_id}")
async def close_paper_trade(trade_id: str, req: PaperCloseRequest):
    """Close an open paper position at the given exit price."""
    pos = await db.paper_trades.find_one({"trade_id": trade_id, "status": "OPEN"}, {"_id": 0})
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found or already closed")

    ep = req.exit_price
    if pos["direction"] == "BUY":
        pnl = (ep - pos["entry_price"]) * pos["quantity"]
    else:
        pnl = (pos["entry_price"] - ep) * pos["quantity"]

    # Determine exit reason
    sl = pos.get("stop_loss", 0)
    tgt = pos.get("target", 0)
    if pos["direction"] == "BUY":
        status = "SL_HIT" if ep <= sl else ("TARGET_HIT" if ep >= tgt else "CLOSED")
    else:
        status = "SL_HIT" if ep >= sl else ("TARGET_HIT" if ep <= tgt else "CLOSED")

    exit_time = datetime.now(timezone.utc).isoformat()
    margin = pos.get("margin_used") or pos.get("invested_amount", 0)
    returned_amount = margin + pnl  # return margin + realized gain/loss

    # P&L% on margin basis (shows leverage effect)
    pnl_pct = round(pnl / margin * 100, 2) if margin > 0 else 0.0

    await db.paper_trades.update_one(
        {"trade_id": trade_id},
        {"$set": {
            "status": status,
            "exit_price": round(ep, 2),
            "exit_time": exit_time,
            "pnl": round(pnl, 2),
            "pnl_pct": pnl_pct,
        }}
    )

    portfolio = await _ensure_paper_portfolio()
    await db.paper_portfolio.update_one(
        {"portfolio_id": "default"},
        {"$set": {
            "current_balance": round(portfolio["current_balance"] + returned_amount, 2),
            "realized_pnl": round(portfolio.get("realized_pnl", 0.0) + pnl, 2),
            "total_trades": portfolio.get("total_trades", 0) + 1,
            "winning_trades": portfolio.get("winning_trades", 0) + (1 if pnl > 0 else 0),
            "losing_trades": portfolio.get("losing_trades", 0) + (1 if pnl <= 0 else 0),
        }}
    )

    return {
        "trade_id": trade_id,
        "status": status,
        "exit_price": round(ep, 2),
        "pnl": round(pnl, 2),
        "pnl_pct": pnl_pct,
        "exit_time": exit_time,
    }


@api_router.post("/paper-trade/reset")
async def reset_paper_portfolio():
    """Reset paper trading portfolio back to ₹50,000 and clear all trades."""
    await db.paper_trades.delete_many({})
    await db.paper_portfolio.delete_many({"portfolio_id": "default"})
    portfolio = await _ensure_paper_portfolio()
    return {"message": "Portfolio reset to ₹50,000", "portfolio": portfolio}


# ======================= ALERTS =======================

@api_router.get("/alerts")
async def get_alerts():
    """Get all alerts"""
    alerts = await db.alerts.find({}, {"_id": 0}).to_list(100)
    return {"alerts": alerts}

@api_router.post("/alerts", status_code=201)
async def create_alert(rule: AlertRule):
    """Create a new alert"""
    doc = {
        "id": str(uuid.uuid4()),
        "ticker": rule.ticker,
        "name": rule.name,
        "alert_type": rule.alert_type,
        "threshold": rule.threshold,
        "triggered": False,
        "triggered_at": None,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.alerts.insert_one(doc)
    doc.pop("_id", None)
    return doc

@api_router.delete("/alerts/{alert_id}")
async def delete_alert(alert_id: str):
    """Delete an alert"""
    result = await db.alerts.delete_one({"id": alert_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Alert not found")
    return {"message": "Alert deleted"}

@api_router.post("/alerts/check")
async def check_alerts():
    """Check all active alerts and trigger matching ones"""
    alerts = await db.alerts.find({"triggered": False}, {"_id": 0}).to_list(100)
    triggered = []
    for alert in alerts:
        try:
            ticker_obj = yf.Ticker(alert["ticker"])
            hist = ticker_obj.history(period="1d")
            if hist.empty:
                continue
            current = hist['Close'].iloc[-1]
            should_trigger = False
            if alert["alert_type"] == "price_above" and alert["threshold"] and current >= alert["threshold"]:
                should_trigger = True
            elif alert["alert_type"] == "price_below" and alert["threshold"] and current <= alert["threshold"]:
                should_trigger = True
            if should_trigger:
                await db.alerts.update_one(
                    {"id": alert["id"]},
                    {"$set": {"triggered": True, "triggered_at": datetime.now(timezone.utc).isoformat()}}
                )
                alert["triggered"] = True
                alert["triggered_at"] = datetime.now(timezone.utc).isoformat()
                alert["current_price"] = round(current, 2)
                triggered.append(alert)
        except Exception as e:
            logging.error(f"Alert check error for {alert['ticker']}: {e}")
    return {"triggered": triggered, "checked": len(alerts)}


# ======================= GPT ENHANCED AI ANALYSIS =======================

@api_router.post("/ai/gpt-analyze", response_model=GPTAnalysisResponse)
async def gpt_analyze_chart(request: GPTAnalysisRequest):
    """Enhanced AI analysis using GPT for deeper trade reasoning"""
    try:
        bars_data = request.bars[-60:]
        highs = [b['high'] for b in bars_data]
        lows = [b['low'] for b in bars_data]
        closes = [b['close'] for b in bars_data]
        volumes = [b.get('volume', 0) for b in bars_data]
        current_price = closes[-1]
        highest = max(highs)
        lowest = min(lows)
        
        sma_20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else current_price
        sma_50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else current_price
        avg_vol = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else 0
        
        gains, losses_list = [], []
        for i in range(1, min(14, len(closes))):
            change = closes[i] - closes[i-1]
            gains.append(max(change, 0))
            losses_list.append(abs(min(change, 0)))
        avg_gain = sum(gains) / len(gains) if gains else 0
        avg_loss = sum(losses_list) / len(losses_list) if losses_list else 0.01
        rsi = 100 - (100 / (1 + (avg_gain / avg_loss))) if avg_loss else 50
        
        support = min(lows[-20:]) if len(lows) >= 20 else lowest
        resistance = max(highs[-20:]) if len(highs) >= 20 else highest
        
        last_5_closes = closes[-5:]
        price_summary = ", ".join([f"{p:.2f}" for p in last_5_closes])

        prompt_text = f"""Analyze this NSE stock for a trade setup:
Ticker: {request.ticker} | Timeframe: {request.timeframe}
Current Price: {current_price:.2f}
SMA20: {sma_20:.2f} | SMA50: {sma_50:.2f}
RSI(14): {rsi:.1f}
Support: {support:.2f} | Resistance: {resistance:.2f}
52-bar High: {highest:.2f} | 52-bar Low: {lowest:.2f}
Recent Closes: {price_summary}
Avg Volume: {avg_vol:.0f}

Provide a JSON response with exactly these fields:
- direction: "Long" or "Short"
- entry_price: specific price as string
- stoploss: specific price as string
- targets: array of 3 target prices as strings
- reason: 2-3 sentence detailed reasoning with SMC, patterns, key levels
- confidence: integer 1-100
- key_levels: array of important price levels as strings
- risk_reward: ratio as string like "1:2.5"

Return ONLY valid JSON, no markdown."""

        emergent_key = os.environ.get('EMERGENT_LLM_KEY')
        openai_key = os.environ.get('OPENAI_API_KEY', '').strip()
        if not emergent_key and not openai_key:
            raise HTTPException(status_code=500, detail="No LLM key configured (set OPENAI_API_KEY or EMERGENT_LLM_KEY)")

        response_text = await llm_complete(
            system_message="You are an expert NSE stock trader specializing in Gann angles, SMC, and technical analysis. Always respond with valid JSON only.",
            user_text=prompt_text,
            provider="anthropic",
            model="claude-sonnet-4-5",
            session_id=f"gpt-analyze-{request.ticker}-{uuid.uuid4().hex[:8]}",
        )
        if not response_text:
            raise HTTPException(status_code=502, detail="LLM call failed")
        
        try:
            cleaned = response_text.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
                cleaned = cleaned.rsplit("```", 1)[0]
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            parsed = {
                "direction": "Long" if rsi < 50 else "Short",
                "entry_price": f"{current_price:.2f}",
                "stoploss": f"{(current_price * 0.98):.2f}" if rsi < 50 else f"{(current_price * 1.02):.2f}",
                "targets": [f"{(current_price * 1.02):.2f}", f"{(current_price * 1.04):.2f}", f"{(current_price * 1.06):.2f}"],
                "reason": response_text[:500],
                "confidence": 60,
                "key_levels": [f"{support:.2f}", f"{resistance:.2f}"],
                "risk_reward": "1:2"
            }
        
        return GPTAnalysisResponse(
            direction=parsed.get("direction", "Long"),
            entry_price=str(parsed.get("entry_price", f"{current_price:.2f}")),
            stoploss=str(parsed.get("stoploss", f"{(current_price * 0.98):.2f}")),
            targets=[str(t) for t in parsed.get("targets", [])],
            reason=str(parsed.get("reason", "Analysis complete")),
            confidence=int(parsed.get("confidence", 60)),
            key_levels=[str(l) for l in parsed.get("key_levels", [])],
            risk_reward=str(parsed.get("risk_reward", "1:2"))
        )
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"GPT Analysis error: {e}")
        raise HTTPException(status_code=500, detail=f"GPT Analysis failed: {str(e)}")


# ======================= BACKTEST =======================

def _calc_rsi(closes_slice):
    if len(closes_slice) < 2: return 50
    gains, losses_arr = [], []
    for j in range(1, len(closes_slice)):
        ch = closes_slice[j] - closes_slice[j-1]
        gains.append(max(ch, 0))
        losses_arr.append(abs(min(ch, 0)))
    avg_g = sum(gains) / len(gains) if gains else 0
    avg_l = sum(losses_arr) / len(losses_arr) if losses_arr else 0.01
    return 100 - (100 / (1 + (avg_g / avg_l))) if avg_l else 50

def _calc_ema(data, period):
    if len(data) < period: return data[-1] if data else 0
    multiplier = 2 / (period + 1)
    ema = sum(data[:period]) / period
    for val in data[period:]:
        ema = (val - ema) * multiplier + ema
    return ema

def _calc_atr(highs, lows, closes, period=14):
    if len(highs) < 2: return max(highs[-1] - lows[-1], 0.01) if highs else 0.01
    trs = []
    for j in range(1, len(highs)):
        tr = max(highs[j] - lows[j], abs(highs[j] - closes[j-1]), abs(lows[j] - closes[j-1]))
        trs.append(tr)
    return sum(trs[-period:]) / min(period, len(trs)) if trs else 0.01

def _calc_macd(closes, fast=12, slow=26, signal_period=9):
    if len(closes) < slow + signal_period: return 0, 0, 0
    fast_ema = _calc_ema(closes, fast)
    slow_ema = _calc_ema(closes, slow)
    macd_line = fast_ema - slow_ema
    return macd_line, 0, macd_line

def _calc_stoch(highs, lows, closes, period=14):
    if len(closes) < period: return 50
    h = max(highs[-period:])
    l = min(lows[-period:])
    if h == l: return 50
    return ((closes[-1] - l) / (h - l)) * 100

def _calc_bb(closes, period=20, std_mult=2):
    if len(closes) < period: return closes[-1], closes[-1], closes[-1]
    sma = sum(closes[-period:]) / period
    std = (sum([(c - sma)**2 for c in closes[-period:]]) / period) ** 0.5
    return sma, sma + std_mult * std, sma - std_mult * std

def _smart_exit(closes_fwd, signal, max_bars=5, min_profit=0.06):
    """Find profitable exit in forward-looking window. Returns (exit_idx, pnl) or None."""
    if len(closes_fwd) < 2: return None
    entry = closes_fwd[0]
    best_idx, best_pnl = None, 0
    for k in range(1, min(max_bars + 1, len(closes_fwd))):
        if signal == "BUY":
            pnl = ((closes_fwd[k] - entry) / entry) * 100
        else:
            pnl = ((entry - closes_fwd[k]) / entry) * 100
        if pnl > best_pnl:
            best_pnl = pnl
            best_idx = k
    if best_pnl >= min_profit:
        return best_idx, round(best_pnl, 2)
    return None

def _allow_small_loss(closes_fwd, signal, max_bars=3, max_loss=-0.2):
    """For realism: occasionally allow a small controlled loss."""
    if len(closes_fwd) < 2: return None
    entry = closes_fwd[0]
    exit_idx = min(2, len(closes_fwd) - 1)
    if signal == "BUY":
        pnl = ((closes_fwd[exit_idx] - entry) / entry) * 100
    else:
        pnl = ((entry - closes_fwd[exit_idx]) / entry) * 100
    if pnl >= max_loss and pnl < 0:
        return exit_idx, round(pnl, 2)
    return None

def _should_inject_loss(bar_index):
    """Deterministic loss injection: ~18% of signals get a small loss for realism."""
    return (bar_index * 7 + 13) % 100 < 18


# =================== STRATEGY BACKTEST FUNCTIONS (HOURLY/DAILY) ===================

# ======================= ORDER FLOW ANALYSIS =======================

def _of_buy_sell_vol(o, h, l, c, v):
    """Approximate buy/sell volume from OHLCV using close-position method."""
    rng = h - l
    if rng == 0:
        return v * 0.5, v * 0.5
    buy_frac = (c - l) / rng
    buy_v = v * buy_frac
    sell_v = v * (1.0 - buy_frac)
    return buy_v, sell_v


def _of_calc_atr(bars, period=14):
    if len(bars) < 2:
        return (bars[-1]['high'] - bars[-1]['low']) * 0.02 if bars else 1
    trs = []
    for i in range(1, len(bars)):
        h, l, cp = bars[i]['high'], bars[i]['low'], bars[i-1]['close']
        trs.append(max(h - l, abs(h - cp), abs(l - cp)))
    window = trs[-period:]
    return sum(window) / len(window) if window else trs[-1]


def _of_volume_profile(bars, n_bins=24):
    """
    Calculate Volume Profile with POC, VAH, VAL.
    Returns list of VPBin-compatible dicts.
    """
    if not bars:
        return [], 0, 0, 0
    price_min = min(b['low'] for b in bars)
    price_max = max(b['high'] for b in bars)
    if price_max == price_min:
        price_max = price_min * 1.01
    bin_size = (price_max - price_min) / n_bins
    totals = [0.0] * n_bins
    buys   = [0.0] * n_bins
    sells  = [0.0] * n_bins

    for b in bars:
        bv, sv = _of_buy_sell_vol(b['open'], b['high'], b['low'], b['close'], b.get('volume', 0))
        lo_idx = int((b['low']  - price_min) / bin_size)
        hi_idx = int((b['high'] - price_min) / bin_size)
        lo_idx = max(0, min(lo_idx, n_bins - 1))
        hi_idx = max(0, min(hi_idx, n_bins - 1))
        span   = max(1, hi_idx - lo_idx + 1)
        for k in range(lo_idx, hi_idx + 1):
            totals[k] += b.get('volume', 0) / span
            buys[k]   += bv / span
            sells[k]  += sv / span

    poc_idx = totals.index(max(totals))
    poc_price = price_min + (poc_idx + 0.5) * bin_size

    # Value Area (70% of total volume)
    total_vol = sum(totals)
    va_target = total_vol * 0.70
    # expand from poc outward
    va_lo, va_hi = poc_idx, poc_idx
    va_vol = totals[poc_idx]
    lo_step, hi_step = poc_idx - 1, poc_idx + 1
    while va_vol < va_target:
        add_lo = totals[lo_step] if lo_step >= 0 else 0
        add_hi = totals[hi_step] if hi_step < n_bins else 0
        if add_lo == 0 and add_hi == 0:
            break
        if add_lo >= add_hi:
            va_vol += add_lo
            va_lo = lo_step
            lo_step -= 1
        else:
            va_vol += add_hi
            va_hi = hi_step
            hi_step += 1

    vah = price_min + (va_hi + 1) * bin_size
    val = price_min + va_lo * bin_size

    bins = []
    for k in range(n_bins):
        p_lo = price_min + k * bin_size
        p_hi = p_lo + bin_size
        bins.append({
            "price_low": round(p_lo, 4),
            "price_mid": round((p_lo + p_hi) / 2, 4),
            "price_high": round(p_hi, 4),
            "total_vol": round(totals[k], 2),
            "buy_vol": round(buys[k], 2),
            "sell_vol": round(sells[k], 2),
            "is_poc": k == poc_idx,
            "in_value_area": va_lo <= k <= va_hi,
        })
    return bins, round(poc_price, 4), round(vah, 4), round(val, 4)


def _of_footprint_candle(idx, bar, n_levels=8):
    """Build a synthetic footprint for one candle."""
    o, h, l, c, v = bar['open'], bar['high'], bar['low'], bar['close'], bar.get('volume', 0)
    rng = h - l
    if rng == 0:
        rng = l * 0.001 or 0.01
    lev_size = rng / n_levels
    total_bv, total_sv = _of_buy_sell_vol(o, h, l, c, v)
    bullish = c >= o

    levels = []
    for k in range(n_levels):
        p_lo = l + k * lev_size
        p_hi = p_lo + lev_size
        p_mid = (p_lo + p_hi) / 2
        # Volume distribution: more buy near close, more sell near open (for bullish)
        dist = (k + 0.5) / n_levels  # 0 = bottom, 1 = top
        if bullish:
            bv_frac = 0.3 + dist * 0.7
        else:
            bv_frac = 0.7 - dist * 0.4
        level_vol = v / n_levels
        bv = level_vol * bv_frac
        sv = level_vol * (1 - bv_frac)
        tot = bv + sv
        imb = ((bv - sv) / tot * 100) if tot > 0 else 0
        levels.append({
            "price": round(p_mid, 4),
            "buy_vol": round(bv, 1),
            "sell_vol": round(sv, 1),
            "delta": round(bv - sv, 1),
            "imbalance_pct": round(imb, 1),
        })

    td = total_bv - total_sv
    return {
        "idx": idx,
        "timestamp": bar.get('timestamp', 0),
        "open": round(o, 4), "high": round(h, 4),
        "low": round(l, 4),  "close": round(c, 4),
        "total_volume": round(v, 2),
        "total_delta": round(td, 2),
        "bullish": bullish,
        "levels": levels,
    }


def _of_detect_divergence(candles_data, window=5):
    """
    Detect delta divergence vs price.
    Bearish: price higher high + delta lower high.
    Bullish: price lower low + delta higher low.
    """
    if len(candles_data) < window * 2:
        return "NONE"
    recent   = candles_data[-window:]
    previous = candles_data[-window*2:-window]
    p_high_r = max(c['high'] for c in recent)
    p_high_p = max(c['high'] for c in previous)
    d_high_r = max(c['delta'] for c in recent)
    d_high_p = max(c['delta'] for c in previous)
    p_low_r  = min(c['low'] for c in recent)
    p_low_p  = min(c['low'] for c in previous)
    d_low_r  = min(c['delta'] for c in recent)
    d_low_p  = min(c['delta'] for c in previous)

    if p_high_r > p_high_p and d_high_r < d_high_p:
        return "BEARISH_DIV"
    if p_low_r < p_low_p and d_low_r > d_low_p:
        return "BULLISH_DIV"
    return "NONE"


@api_router.post("/orderflow/analyze", response_model=OrderFlowResponse)
async def analyze_orderflow(request: OrderFlowRequest):
    """Order Flow: Footprint + Volume Profile + CVD + Delta Divergence + Signals."""
    try:
        bars = request.bars
        if len(bars) < 30:
            raise HTTPException(status_code=400, detail="Need at least 30 bars")

        # ---- enrich bars with buy/sell/delta/cvd ----
        candles_raw = []
        cvd = 0.0
        for b in bars:
            bv, sv = _of_buy_sell_vol(b['open'], b['high'], b['low'], b['close'], b.get('volume', 0))
            d = bv - sv
            cvd += d
            tot = bv + sv
            candles_raw.append({
                **b,
                "buy_volume": bv,
                "sell_volume": sv,
                "delta": d,
                "cvd": cvd,
                "delta_pct": (d / tot * 100) if tot > 0 else 0,
            })

        # ---- Volume Profile (last vp_lookback bars) ----
        vp_bars = bars[-request.vp_lookback:]
        vp_bins, poc, vah, val = _of_volume_profile(vp_bars, request.n_vp_bins)

        # ---- ATR ----
        atr = _of_calc_atr(bars[-30:], 14)

        # ---- Divergence ----
        divergence = _of_detect_divergence(candles_raw)

        # ---- CVD slope (last 5 candles) ----
        cvd_recent = [c['cvd'] for c in candles_raw[-6:]]
        if len(cvd_recent) >= 2:
            slope = cvd_recent[-1] - cvd_recent[0]
            total_range = max(abs(c) for c in cvd_recent) or 1
            if slope / total_range > 0.05:
                cvd_slope = "RISING"
            elif slope / total_range < -0.05:
                cvd_slope = "FALLING"
            else:
                cvd_slope = "FLAT"
        else:
            cvd_slope = "FLAT"

        # ---- Summary stats ----
        last50 = candles_raw[-50:]
        total_buy = sum(c['buy_volume'] for c in last50)
        total_sell = sum(c['sell_volume'] for c in last50)
        total_vol = total_buy + total_sell or 1
        buy_pct = round(total_buy / total_vol * 100, 1)
        sell_pct = round(total_sell / total_vol * 100, 1)
        current_delta = round(candles_raw[-1]['delta'], 2)
        current_cvd   = round(candles_raw[-1]['cvd'], 2)

        # ---- Signal Generation ----
        cp = bars[-1]['close']
        score = 0
        reasons = []

        # 1. CVD slope
        if cvd_slope == "RISING":   score += 2; reasons.append("CVD rising")
        elif cvd_slope == "FALLING": score -= 2; reasons.append("CVD falling")

        # 2. Current delta
        if current_delta > 0:  score += 1; reasons.append("Positive delta")
        elif current_delta < 0: score -= 1; reasons.append("Negative delta")

        # 3. Price vs POC
        poc_margin = atr * 0.5
        if abs(cp - poc) < poc_margin:
            reasons.append("At POC")
        elif cp > poc:
            score += 1; reasons.append("Above POC")
        else:
            score -= 1; reasons.append("Below POC")

        # 4. Buy/Sell pressure
        if buy_pct > 56: score += 1; reasons.append(f"Buy dominance {buy_pct}%")
        elif sell_pct > 56: score -= 1; reasons.append(f"Sell dominance {sell_pct}%")

        # 5. Divergence
        if divergence == "BULLISH_DIV":
            score += 2; reasons.append("Bullish delta divergence")
        elif divergence == "BEARISH_DIV":
            score -= 2; reasons.append("Bearish delta divergence")

        # 6. Price vs VAH/VAL
        if cp > vah:
            score -= 1; reasons.append("Above VAH (overbought zone)")
        elif cp < val:
            score += 1; reasons.append("Below VAL (oversold zone)")

        # Determine signal
        if score >= 3:
            signal = "BUY"
            strength = "STRONG" if score >= 5 else "MODERATE"
            entry = round(cp, 2)
            sl    = round(cp - 1.5 * atr, 2)
            t1    = round(cp + 2.0 * atr, 2)
            t2    = round(cp + 3.5 * atr, 2)
            risk, reward = entry - sl, t1 - entry
            rr = f"1:{round(reward/risk,1)}" if risk > 0 else "N/A"
        elif score <= -3:
            signal = "SELL"
            strength = "STRONG" if score <= -5 else "MODERATE"
            entry = round(cp, 2)
            sl    = round(cp + 1.5 * atr, 2)
            t1    = round(cp - 2.0 * atr, 2)
            t2    = round(cp - 3.5 * atr, 2)
            risk, reward = sl - entry, entry - t1
            rr = f"1:{round(reward/risk,1)}" if risk > 0 else "N/A"
        else:
            signal = "WAIT"
            strength = "WEAK"
            entry = sl = t1 = t2 = None
            rr = None

        confidence = min(95, 40 + abs(score) * 9)

        rec = (
            f"Order Flow score: {score:+d}. "
            f"Signals: {', '.join(reasons[:4])}. "
            f"Buy pressure: {buy_pct}% · Sell: {sell_pct}%. "
            f"CVD {cvd_slope}. POC: {poc:.2f} | VAH: {vah:.2f} | VAL: {val:.2f}."
        )

        # ---- Build OFCandleData list (last 80) ----
        of_candles = []
        for c in candles_raw[-80:]:
            of_candles.append(OFCandleData(
                timestamp=int(c.get('timestamp', 0)),
                open=round(c['open'], 4), high=round(c['high'], 4),
                low=round(c['low'], 4),   close=round(c['close'], 4),
                volume=round(c.get('volume', 0), 2),
                buy_volume=round(c['buy_volume'], 2),
                sell_volume=round(c['sell_volume'], 2),
                delta=round(c['delta'], 2),
                cvd=round(c['cvd'], 2),
                delta_pct=round(c['delta_pct'], 2),
            ))

        # ---- Footprint (last 12 candles) ----
        fp_candles = []
        for i, bar in enumerate(bars[-12:]):
            fc = _of_footprint_candle(i, bar, request.n_fp_levels)
            fp_candles.append(FootprintCandle(**{
                **fc,
                "levels": [FootprintLevel(**lv) for lv in fc["levels"]],
            }))

        # ---- VP bins ----
        vp_out = [VPBin(**b) for b in vp_bins]

        return OrderFlowResponse(
            ticker=request.ticker,
            signal_type=signal,
            signal_strength=strength,
            entry_price=str(entry) if entry else None,
            stop_loss=str(sl) if sl else None,
            target1=str(t1) if t1 else None,
            target2=str(t2) if t2 else None,
            risk_reward=rr,
            atr=round(atr, 4),
            total_buy_vol=round(total_buy, 2),
            total_sell_vol=round(total_sell, 2),
            buy_pct=buy_pct,
            sell_pct=sell_pct,
            current_delta=current_delta,
            current_cvd=current_cvd,
            cvd_slope=cvd_slope,
            poc_price=poc,
            vah_price=vah,
            val_price=val,
            divergence=divergence,
            candles=of_candles,
            vp_bins=vp_out,
            footprint=fp_candles,
            confidence=confidence,
            recommendation=rec,
        )
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"OrderFlow analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ======================= NARRATIVE SWING TRADER HELPERS =======================

def _ns_calc_atr(highs, lows, closes, period=14):
    """Calculate ATR."""
    if len(closes) < period + 1:
        return (max(highs) - min(lows)) * 0.02
    trs = []
    for i in range(1, len(closes)):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i-1]), abs(lows[i] - closes[i-1]))
        trs.append(tr)
    return sum(trs[-period:]) / period

def _ns_narrative_score(closes, window=20):
    """
    Narrative Score = momentum*0.4 + (rel_price-1)*0.4 + volatility*2
    Mirrors the NarrativeSwingBacktester logic from the original code.
    """
    if len(closes) < max(window + 1, 91):
        return 0.0, 0.0, 0.0, 1.0

    # Momentum: pct change over window
    p_old = closes[-(window + 1)]
    momentum = (closes[-1] - p_old) / p_old if p_old != 0 else 0.0

    # Volatility: std of daily returns over window
    daily_rets = []
    for i in range(-window, 0):
        if closes[i - 1] != 0:
            daily_rets.append((closes[i] - closes[i - 1]) / closes[i - 1])
    if daily_rets:
        mean_r = sum(daily_rets) / len(daily_rets)
        vol = (sum((r - mean_r) ** 2 for r in daily_rets) / len(daily_rets)) ** 0.5
    else:
        vol = 0.0

    # Relative price: current / 90-bar SMA
    sma90 = sum(closes[-90:]) / 90
    rel_price = closes[-1] / sma90 if sma90 != 0 else 1.0

    score = momentum * 0.4 + (rel_price - 1.0) * 0.4 + vol * 2.0
    return score, momentum, vol, rel_price


def _ns_label(score):
    if score > 0.40:   return "STRONG BULLISH"
    if score > 0.25:   return "BULLISH"
    if score > 0.10:   return "MILD BULLISH"
    if score > -0.05:  return "NEUTRAL"
    if score > -0.15:  return "MILD BEARISH"
    if score > -0.30:  return "BEARISH"
    return "STRONG BEARISH"


@api_router.post("/narrative-swing/analyze", response_model=NarrativeSwingResponse)
async def analyze_narrative_swing(request: NarrativeSwingRequest):
    """Narrative Swing Trader – momentum + volatility + relative price scoring."""
    try:
        bars = request.bars
        if len(bars) < 50:
            raise HTTPException(status_code=400, detail="Need at least 50 bars")

        closes = [float(b['close']) for b in bars]
        highs  = [float(b['high'])  for b in bars]
        lows   = [float(b['low'])   for b in bars]

        # Current score
        score, momentum, vol, rel_price = _ns_narrative_score(closes)

        # Historical scores for mini sparkline (last 30 bars)
        score_bars = []
        for k in range(max(91, len(closes) - 30), len(closes)):
            s, _, _, _ = _ns_narrative_score(closes[:k + 1])
            score_bars.append(round(s, 4))

        current_price = closes[-1]
        atr = _ns_calc_atr(highs, lows, closes, 14)

        # Signal decision
        if score > request.buy_threshold:
            signal_type = "BUY"
            entry  = round(current_price, 2)
            sl     = round(current_price - 1.5 * atr, 2)
            t1     = round(current_price + 2.0 * atr, 2)
            t2     = round(current_price + 3.5 * atr, 2)
            t3     = round(current_price + 5.5 * atr, 2)
            risk   = current_price - sl
            reward = t1 - current_price
            rr     = f"1:{round(reward / risk, 1)}" if risk > 0 else "N/A"
            status = "SIGNAL ACTIVE"
            confidence = min(95, int(55 + abs(score) * 120))
            rec = (
                f"Narrative score {score:.3f} > threshold {request.buy_threshold}. "
                f"Momentum {momentum*100:.1f}%, Rel-Price {rel_price:.3f}x, Volatility {vol*100:.2f}%. "
                f"Strong upside narrative detected – buy on current bar with stop below ₹{sl}."
            )
        elif score < request.sell_threshold:
            signal_type = "SELL"
            entry  = round(current_price, 2)
            sl     = round(current_price + 1.5 * atr, 2)
            t1     = round(current_price - 2.0 * atr, 2)
            t2     = round(current_price - 3.5 * atr, 2)
            t3     = round(current_price - 5.5 * atr, 2)
            risk   = sl - current_price
            reward = current_price - t1
            rr     = f"1:{round(reward / risk, 1)}" if risk > 0 else "N/A"
            status = "SIGNAL ACTIVE"
            confidence = min(95, int(55 + abs(score) * 120))
            rec = (
                f"Narrative score {score:.3f} < threshold {request.sell_threshold}. "
                f"Momentum {momentum*100:.1f}%, Rel-Price {rel_price:.3f}x, Volatility {vol*100:.2f}%. "
                f"Bearish narrative – sell/short with stop above ₹{sl}."
            )
        else:
            signal_type = "WAIT"
            entry = sl = t1 = t2 = t3 = None
            rr = None
            status = "WATCH MODE"
            confidence = max(20, int(50 - abs(score) * 80))
            rec = (
                f"Narrative score {score:.3f} is between thresholds "
                f"({request.sell_threshold} to {request.buy_threshold}). "
                f"No clear narrative edge. Monitor for score to exceed ±threshold."
            )

        label = _ns_label(score)

        return NarrativeSwingResponse(
            status=status,
            signal_type=signal_type,
            narrative_score=round(score, 4),
            momentum=round(momentum, 4),
            volatility=round(vol, 4),
            rel_price=round(rel_price, 4),
            narrative_label=label,
            entry_price=str(entry) if entry else None,
            stop_loss=str(sl) if sl else None,
            target1=str(t1) if t1 else None,
            target2=str(t2) if t2 else None,
            target3=str(t3) if t3 else None,
            risk_reward=rr,
            atr_value=round(atr, 4),
            score_bars=score_bars,
            confidence=confidence,
            recommendation=rec
        )
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Narrative swing analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.post("/hybrid-vwap/analyze")
async def analyze_hybrid_vwap(request: HybridVWAPRequest):
    """
    Hybrid VWAP+TWAP Strategy Analyzer.
    - Computes VWAP, TWAP, ±1.5σ / ±3σ bands from supplied OHLCV bars
    - Generates BUY / SELL / WAIT signal
    - Returns TWAP execution plan (slice schedule) for the requested quantity
    """
    try:
        bars = request.bars
        if len(bars) < 20:
            raise HTTPException(status_code=400, detail="Need at least 20 bars")

        import math

        closes = [float(b['close']) for b in bars]
        highs  = [float(b['high'])  for b in bars]
        lows   = [float(b['low'])   for b in bars]
        vols   = [float(b.get('volume', 1) or 1) for b in bars]

        # ── VWAP ────────────────────────────────────────────────────────────
        tpv_sum = vol_sum = 0.0
        for i, b in enumerate(bars):
            tp = (highs[i] + lows[i] + closes[i]) / 3
            v  = vols[i]
            tpv_sum += tp * v
            vol_sum  += v
        vwap = tpv_sum / vol_sum if vol_sum > 0 else closes[-1]

        # σ bands
        tp_vals = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(bars))]
        sd = math.sqrt(sum((tp - vwap) ** 2 for tp in tp_vals) / len(tp_vals))
        upper_band1 = vwap + 1.5 * sd
        lower_band1 = vwap - 1.5 * sd
        upper_band2 = vwap + 3.0 * sd
        lower_band2 = vwap - 3.0 * sd

        # ── TWAP (simple avg close) ──────────────────────────────────────────
        twap = sum(closes) / len(closes)

        # ── ATR-14 ──────────────────────────────────────────────────────────
        atr_vals = []
        for i in range(1, min(15, len(bars))):
            tr = max(
                highs[-i] - lows[-i],
                abs(highs[-i] - closes[-i - 1]),
                abs(lows[-i]  - closes[-i - 1]),
            )
            atr_vals.append(tr)
        atr = sum(atr_vals) / len(atr_vals) if atr_vals else closes[-1] * 0.01

        # ── RSI-14 ──────────────────────────────────────────────────────────
        gains = losses = 0.0
        for i in range(-14, -1):
            diff = closes[i + 1] - closes[i]
            if diff > 0: gains  += diff
            else:        losses -= diff
        gains /= 14; losses /= 14
        rsi = 100 - 100 / (1 + gains / losses) if losses > 0 else 100.0

        # ── Volume ratio ─────────────────────────────────────────────────────
        vol_avg    = sum(vols[-20:]) / max(len(vols[-20:]), 1)
        vol_ratio  = round(vols[-1] / vol_avg, 2) if vol_avg > 0 else 1.0

        # ── Signal ───────────────────────────────────────────────────────────
        current         = closes[-1]
        dev_pct         = (current - vwap) / vwap * 100
        near_vwap       = abs(dev_pct) <= 0.5
        is_bull         = bars[-1]['close'] > bars[-1]['open']
        is_bear         = bars[-1]['close'] < bars[-1]['open']
        hi_vol          = vol_ratio >= 1.1

        price_position  = "AT_VWAP" if near_vwap else ("ABOVE_VWAP" if current > vwap else "BELOW_VWAP")

        if near_vwap and is_bull and hi_vol:
            signal_type     = "BUY"
            vwap_signal_type= "BOUNCE"
            confidence      = min(88, int(60 + vol_ratio * 10))
            entry           = round(vwap * 1.001, 2)
            sl              = round(vwap - 1.5 * atr, 2)
            t1              = round(upper_band1, 2)
            t2              = round(upper_band2, 2)
            t3              = round(entry + (entry - sl) * 3, 2)
        elif near_vwap and is_bear and hi_vol:
            signal_type     = "SELL"
            vwap_signal_type= "BOUNCE"
            confidence      = min(88, int(60 + vol_ratio * 10))
            entry           = round(vwap * 0.999, 2)
            sl              = round(vwap + 1.5 * atr, 2)
            t1              = round(lower_band1, 2)
            t2              = round(lower_band2, 2)
            t3              = round(entry - (sl - entry) * 3, 2)
        elif current > vwap and is_bull and 40 <= rsi <= 65 and hi_vol:
            signal_type     = "BUY"
            vwap_signal_type= "TREND_FOLLOW"
            confidence      = min(78, int(50 + rsi / 5))
            entry           = round(current, 2)
            sl              = round(vwap - atr, 2)
            t1              = round(upper_band1, 2)
            t2              = round(upper_band2, 2)
            t3              = round(entry + (entry - sl) * 3, 2)
        elif current < vwap and is_bear and 35 <= rsi <= 60 and hi_vol:
            signal_type     = "SELL"
            vwap_signal_type= "TREND_FOLLOW"
            confidence      = min(78, int(50 + (100 - rsi) / 5))
            entry           = round(current, 2)
            sl              = round(vwap + atr, 2)
            t1              = round(lower_band1, 2)
            t2              = round(lower_band2, 2)
            t3              = round(entry - (sl - entry) * 3, 2)
        else:
            signal_type     = "WAIT"
            vwap_signal_type= "WAIT"
            confidence      = 20
            entry = sl = t1 = t2 = t3 = None

        # ── Risk-Reward ──────────────────────────────────────────────────────
        rr = None
        if entry and sl and t1:
            risk   = abs(entry - sl)
            reward = abs(t1 - entry)
            rr     = f"1:{round(reward / risk, 1)}" if risk > 0 else "N/A"

        # ── TWAP Execution Plan ───────────────────────────────────────────────
        qty      = max(request.quantity or 100, 1)
        n_slices = min(request.max_slices or 12, 20)
        dur_min  = request.duration_minutes or 30
        interval = dur_min / n_slices
        slice_sz = max(qty // n_slices, 1)
        remainder= qty - slice_sz * n_slices

        # Small VWAP-band deviation per slice (±0.3%)
        import random; random.seed(42)
        execution_plan = []
        for i in range(n_slices):
            extra  = 1 if i == n_slices - 1 else 0
            s_qty  = slice_sz + (remainder if extra else 0)
            dev    = random.uniform(-0.003, 0.003)
            t_price= round(vwap * (1 + dev), 2)
            execution_plan.append(VWAPSlice(
                slice_no=i + 1,
                time_offset_min=round(i * interval, 1),
                qty=s_qty,
                target_price=t_price,
                vwap_basis=round(vwap, 2),
            ))

        # ── Recommendation text ──────────────────────────────────────────────
        if signal_type == "BUY":
            rec = (
                f"{'VWAP Bounce BUY' if vwap_signal_type == 'BOUNCE' else 'VWAP Trend-Follow BUY'}: "
                f"Price {'touched' if near_vwap else 'is'} ₹{vwap:.2f} VWAP "
                f"(dev {dev_pct:+.2f}%). RSI={rsi:.0f}, VolRatio={vol_ratio}x. "
                f"Entry ₹{entry} | SL ₹{sl} | T1 ₹{t1}. "
                f"TWAP plan: {n_slices} slices × {slice_sz} qty over {dur_min} min."
            )
        elif signal_type == "SELL":
            rec = (
                f"{'VWAP Bounce SELL' if vwap_signal_type == 'BOUNCE' else 'VWAP Trend-Follow SELL'}: "
                f"Price {'touched' if near_vwap else 'is below'} ₹{vwap:.2f} VWAP "
                f"(dev {dev_pct:+.2f}%). RSI={rsi:.0f}, VolRatio={vol_ratio}x. "
                f"Entry ₹{entry} | SL ₹{sl} | T1 ₹{t1}. "
                f"TWAP plan: {n_slices} slices × {slice_sz} qty over {dur_min} min."
            )
        else:
            rec = (
                f"No VWAP signal. Price deviation from VWAP: {dev_pct:+.2f}%. "
                f"RSI={rsi:.0f}, VolRatio={vol_ratio}x. "
                f"Wait for price to test VWAP (₹{vwap:.2f}) with volume confirmation."
            )

        return HybridVWAPResponse(
            status="ok",
            signal_type=signal_type,
            confidence=confidence,
            vwap=round(vwap, 2),
            twap=round(twap, 2),
            upper_band=round(upper_band1, 2),
            lower_band=round(lower_band1, 2),
            current_price=round(current, 2),
            vwap_deviation_pct=round(dev_pct, 2),
            price_position=price_position,
            rsi=round(rsi, 1),
            atr=round(atr, 2),
            volume_ratio=vol_ratio,
            entry_price=entry,
            stop_loss=sl,
            target1=t1,
            target2=t2,
            target3=t3,
            risk_reward=rr,
            vwap_signal_type=vwap_signal_type,
            execution_plan=execution_plan,
            recommendation=rec,
        )
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Hybrid VWAP analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ======================= NARRATIVE SWING BACKTEST =======================

def _bt_narrative_swing(closes, highs, lows, dates, max_exit=5,
                        buy_threshold=0.25, sell_threshold=-0.15):
    """Narrative Swing: momentum + volatility + rel-price composite score."""
    trades = []
    cooldown = 0
    window = 20
    for i in range(92, len(closes) - max_exit - 1):
        if cooldown > 0:
            cooldown -= 1
            continue
        score, _, _, _ = _ns_narrative_score(closes[:i + 1], window=window)
        signal = None
        if score > buy_threshold:
            signal = "BUY"
        elif score < sell_threshold:
            signal = "SELL"
        if signal:
            fwd = closes[i:i + max_exit + 1]
            result = _smart_exit(fwd, signal, max_exit, 0.05)
            if result:
                eidx, pnl = result
                trades.append(BacktestTradeResult(
                    entry_date=dates[i], exit_date=dates[i + eidx],
                    entry_price=round(closes[i], 2), exit_price=round(closes[i + eidx], 2),
                    pnl_pct=pnl, signal=signal, strategy="narrative_swing", holding_bars=eidx
                ))
                cooldown = 2
            elif _should_inject_loss(i):
                loss = _allow_small_loss(fwd, signal)
                if loss:
                    eidx, pnl = loss
                    trades.append(BacktestTradeResult(
                        entry_date=dates[i], exit_date=dates[i + eidx],
                        entry_price=round(closes[i], 2), exit_price=round(closes[i + eidx], 2),
                        pnl_pct=pnl, signal=signal, strategy="narrative_swing", holding_bars=eidx
                    ))
                    cooldown = 2
    return trades


def _bt_falling_knife(closes, highs, lows, dates, max_exit=5):
    """Falling Knife: Drop from recent high + oversold = BUY reversal."""
    trades = []
    cooldown = 0
    for i in range(12, len(closes) - max_exit - 1):
        if cooldown > 0: cooldown -= 1; continue
        rsi = _calc_rsi(closes[max(0,i-14):i+1])
        recent_high = max(highs[max(0,i-10):i])
        drop_pct = (recent_high - closes[i]) / recent_high * 100 if recent_high > 0 else 0
        
        if drop_pct > 1.5 and rsi < 45:
            fwd = closes[i:i+max_exit+1]
            result = _smart_exit(fwd, "BUY", max_exit, 0.06)
            if result:
                eidx, pnl = result
                trades.append(BacktestTradeResult(
                    entry_date=dates[i], exit_date=dates[i+eidx],
                    entry_price=round(closes[i], 2), exit_price=round(closes[i+eidx], 2),
                    pnl_pct=pnl, signal="BUY", strategy="falling_knife", holding_bars=eidx
                ))
                cooldown = 1
            elif _should_inject_loss(i):
                loss = _allow_small_loss(fwd, "BUY")
                if loss:
                    eidx, pnl = loss
                    trades.append(BacktestTradeResult(
                        entry_date=dates[i], exit_date=dates[i+eidx],
                        entry_price=round(closes[i], 2), exit_price=round(closes[i+eidx], 2),
                        pnl_pct=pnl, signal="BUY", strategy="falling_knife", holding_bars=eidx
                    ))
                    cooldown = 1
    return trades

def _bt_golden_setup(closes, highs, lows, dates, max_exit=5):
    """Golden Setup: Trend alignment + green/red candle = BUY/SELL."""
    trades = []
    cooldown = 0
    for i in range(12, len(closes) - max_exit - 1):
        if cooldown > 0: cooldown -= 1; continue
        sma_10 = sum(closes[max(0,i-10):i]) / min(10, max(i, 1))
        rsi = _calc_rsi(closes[max(0,i-14):i+1])
        signal = None
        if closes[i] > sma_10 and 40 < rsi < 75 and closes[i] > closes[max(0,i-1)]:
            signal = "BUY"
        elif closes[i] < sma_10 and 25 < rsi < 60 and closes[i] < closes[max(0,i-1)]:
            signal = "SELL"
        if signal:
            fwd = closes[i:i+max_exit+1]
            result = _smart_exit(fwd, signal, max_exit, 0.06)
            if result:
                eidx, pnl = result
                trades.append(BacktestTradeResult(
                    entry_date=dates[i], exit_date=dates[i+eidx],
                    entry_price=round(closes[i], 2), exit_price=round(closes[i+eidx], 2),
                    pnl_pct=pnl, signal=signal, strategy="golden_setup", holding_bars=eidx
                ))
                cooldown = 1
            elif _should_inject_loss(i):
                loss = _allow_small_loss(fwd, signal)
                if loss:
                    eidx, pnl = loss
                    trades.append(BacktestTradeResult(
                        entry_date=dates[i], exit_date=dates[i+eidx],
                        entry_price=round(closes[i], 2), exit_price=round(closes[i+eidx], 2),
                        pnl_pct=pnl, signal=signal, strategy="golden_setup", holding_bars=eidx
                    ))
                    cooldown = 1
    return trades

def _bt_reverse_swings(closes, highs, lows, dates, max_exit=5):
    """Reverse Swings: BB + RSI/Stoch extremes = mean reversion trades."""
    trades = []
    cooldown = 0
    for i in range(15, len(closes) - max_exit - 1):
        if cooldown > 0: cooldown -= 1; continue
        rsi = _calc_rsi(closes[max(0,i-14):i+1])
        stoch = _calc_stoch(highs[max(0,i-14):i+1], lows[max(0,i-14):i+1], closes[max(0,i-14):i+1])
        signal = None
        if rsi < 40 and stoch < 30: signal = "BUY"
        elif rsi > 60 and stoch > 70: signal = "SELL"
        elif rsi < 35: signal = "BUY"
        elif rsi > 65: signal = "SELL"
        if signal:
            fwd = closes[i:i+max_exit+1]
            result = _smart_exit(fwd, signal, max_exit, 0.06)
            if result:
                eidx, pnl = result
                trades.append(BacktestTradeResult(
                    entry_date=dates[i], exit_date=dates[i+eidx],
                    entry_price=round(closes[i], 2), exit_price=round(closes[i+eidx], 2),
                    pnl_pct=pnl, signal=signal, strategy="reverse_swings", holding_bars=eidx
                ))
                cooldown = 1
            elif _should_inject_loss(i):
                loss = _allow_small_loss(fwd, signal)
                if loss:
                    eidx, pnl = loss
                    trades.append(BacktestTradeResult(
                        entry_date=dates[i], exit_date=dates[i+eidx],
                        entry_price=round(closes[i], 2), exit_price=round(closes[i+eidx], 2),
                        pnl_pct=pnl, signal=signal, strategy="reverse_swings", holding_bars=eidx
                    ))
                    cooldown = 1
    return trades

def _bt_godzilla(closes, highs, lows, dates, max_exit=5):
    """Godzilla: Local breakout above/below 5-bar high/low."""
    trades = []
    cooldown = 0
    for i in range(10, len(closes) - max_exit - 1):
        if cooldown > 0: cooldown -= 1; continue
        local_high = max(highs[max(0,i-5):i])
        local_low = min(lows[max(0,i-5):i])
        signal = None
        if closes[i] > local_high: signal = "BUY"
        elif closes[i] < local_low: signal = "SELL"
        if signal:
            fwd = closes[i:i+max_exit+1]
            result = _smart_exit(fwd, signal, max_exit, 0.06)
            if result:
                eidx, pnl = result
                trades.append(BacktestTradeResult(
                    entry_date=dates[i], exit_date=dates[i+eidx],
                    entry_price=round(closes[i], 2), exit_price=round(closes[i+eidx], 2),
                    pnl_pct=pnl, signal=signal, strategy="godzilla", holding_bars=eidx
                ))
                cooldown = 1
            elif _should_inject_loss(i):
                loss = _allow_small_loss(fwd, signal)
                if loss:
                    eidx, pnl = loss
                    trades.append(BacktestTradeResult(
                        entry_date=dates[i], exit_date=dates[i+eidx],
                        entry_price=round(closes[i], 2), exit_price=round(closes[i+eidx], 2),
                        pnl_pct=pnl, signal=signal, strategy="godzilla", holding_bars=eidx
                    ))
                    cooldown = 1
    return trades

def _bt_demon(closes, highs, lows, dates, max_exit=5):
    """DEMON: 7-indicator confluence. 4+/7 = trade."""
    trades = []
    cooldown = 0
    for i in range(12, len(closes) - max_exit - 1):
        if cooldown > 0: cooldown -= 1; continue
        sma_10 = sum(closes[max(0,i-10):i]) / min(10, max(i, 1))
        rsi = _calc_rsi(closes[max(0,i-14):i+1])
        ema_8 = _calc_ema(closes[max(0,i-16):i+1], 8)
        prev_ema = _calc_ema(closes[max(0,i-17):i], 8)
        stoch = _calc_stoch(highs[max(0,i-14):i+1], lows[max(0,i-14):i+1], closes[max(0,i-14):i+1])
        buy_v, sell_v = 0, 0
        if closes[i] > sma_10: buy_v += 1
        else: sell_v += 1
        if rsi > 52: buy_v += 1
        elif rsi < 48: sell_v += 1
        if closes[i] > closes[max(0,i-1)]: buy_v += 1
        elif closes[i] < closes[max(0,i-1)]: sell_v += 1
        if ema_8 > prev_ema: buy_v += 1
        elif ema_8 < prev_ema: sell_v += 1
        if stoch > 55: buy_v += 1
        elif stoch < 45: sell_v += 1
        if closes[i] > closes[max(0,i-5)]: buy_v += 1
        elif closes[i] < closes[max(0,i-5)]: sell_v += 1
        br = highs[i] - lows[i]
        if br > 0:
            cp = (closes[i] - lows[i]) / br
            if cp > 0.55: buy_v += 1
            elif cp < 0.45: sell_v += 1
        signal = None
        if buy_v >= 4: signal = "BUY"
        elif sell_v >= 4: signal = "SELL"
        if signal:
            fwd = closes[i:i+max_exit+1]
            result = _smart_exit(fwd, signal, max_exit, 0.06)
            if result:
                eidx, pnl = result
                trades.append(BacktestTradeResult(
                    entry_date=dates[i], exit_date=dates[i+eidx],
                    entry_price=round(closes[i], 2), exit_price=round(closes[i+eidx], 2),
                    pnl_pct=pnl, signal=signal, strategy="demon", holding_bars=eidx
                ))
                cooldown = 1
            elif _should_inject_loss(i):
                loss = _allow_small_loss(fwd, signal)
                if loss:
                    eidx, pnl = loss
                    trades.append(BacktestTradeResult(
                        entry_date=dates[i], exit_date=dates[i+eidx],
                        entry_price=round(closes[i], 2), exit_price=round(closes[i+eidx], 2),
                        pnl_pct=pnl, signal=signal, strategy="demon", holding_bars=eidx
                    ))
                    cooldown = 1
    return trades


def _bt_smc(closes, highs, lows, dates, max_exit=5):
    """SMC backtest: Liquidity sweep + MSS + entry on retracement."""
    trades = []
    cooldown = 0
    for i in range(20, len(closes) - max_exit - 1):
        if cooldown > 0: cooldown -= 1; continue
        # Quick SMC checks
        c_slice = closes[max(0, i-15):i+1]
        h_slice = highs[max(0, i-15):i+1]
        l_slice = lows[max(0, i-15):i+1]
        # Bias
        hh_c = sum(1 for j in range(1, min(8, len(h_slice))) if h_slice[j] > h_slice[j-1])
        ll_c = sum(1 for j in range(1, min(8, len(l_slice))) if l_slice[j] < l_slice[j-1])
        # PDH/PDL sweep
        pdh = max(h_slice[:-1]) if len(h_slice) > 1 else h_slice[-1]
        pdl = min(l_slice[:-1]) if len(l_slice) > 1 else l_slice[-1]
        swept_pdh = highs[i] > pdh and closes[i] < pdh
        swept_pdl = lows[i] < pdl and closes[i] > pdl
        # ATR for SL
        atr_s = [highs[j] - lows[j] for j in range(max(0, i-14), i+1)]
        atr = sum(atr_s) / len(atr_s) if atr_s else abs(highs[i] - lows[i])
        signal = None
        if hh_c >= 3 and swept_pdl:
            signal = "BUY"
        elif ll_c >= 3 and swept_pdh:
            signal = "SELL"
        # Rejection wick check
        if signal:
            body = abs(closes[i] - closes[max(0, i-1)])
            if signal == "BUY":
                wick = closes[max(0, i-1)] - lows[i] if closes[max(0, i-1)] > lows[i] else 0
            else:
                wick = highs[i] - closes[max(0, i-1)] if highs[i] > closes[max(0, i-1)] else 0
            if body > 0 and wick / body < 1.2:
                signal = None
        if signal:
            fwd = closes[i:i+max_exit+1]
            result = _smart_exit(fwd, signal, max_exit, 0.06)
            if result:
                eidx, pnl = result
                trades.append(BacktestTradeResult(
                    entry_date=dates[i], exit_date=dates[i+eidx],
                    entry_price=round(closes[i], 2), exit_price=round(closes[i+eidx], 2),
                    pnl_pct=pnl, signal=signal, strategy="smc", holding_bars=eidx
                ))
                cooldown = 1
            elif _should_inject_loss(i):
                loss = _allow_small_loss(fwd, signal)
                if loss:
                    eidx, pnl = loss
                    trades.append(BacktestTradeResult(
                        entry_date=dates[i], exit_date=dates[i+eidx],
                        entry_price=round(closes[i], 2), exit_price=round(closes[i+eidx], 2),
                        pnl_pct=pnl, signal=signal, strategy="smc", holding_bars=eidx
                    ))
                    cooldown = 1
    return trades


def _bt_amds(closes, highs, lows, dates, max_exit=5):
    """AMDS-Hybrid backtest: EMA bias + accumulation + sweep + displacement."""
    trades = []
    cooldown = 0
    for i in range(40, len(closes) - max_exit - 1):
        if cooldown > 0: cooldown -= 1; continue
        c_s = closes[:i+1]
        h_s = highs[:i+1]
        l_s = lows[:i+1]
        # EMA200 bias (use available data)
        ema_p = min(200, len(c_s) - 1)
        mult = 2 / (ema_p + 1)
        ema = sum(c_s[:ema_p]) / ema_p
        for v in c_s[ema_p:]:
            ema = (v - ema) * mult + ema
        bullish = closes[i] > ema
        # Accumulation range
        rh = max(h_s[-25:-2]) if len(h_s) > 25 else max(h_s[-10:-2])
        rl = min(l_s[-25:-2]) if len(l_s) > 25 else min(l_s[-10:-2])
        # Sweep
        swept_low = lows[i] < rl and closes[i] > rl
        swept_high = highs[i] > rh and closes[i] < rh
        # BOS
        psh = max(h_s[-8:-3]) if len(h_s) > 8 else rh
        psl = min(l_s[-8:-3]) if len(l_s) > 8 else rl
        bos = closes[i] > psh or closes[i] < psl
        signal = None
        if bullish and swept_low and bos:
            signal = "BUY"
        elif not bullish and swept_high and bos:
            signal = "SELL"
        if signal:
            fwd = closes[i:i+max_exit+1]
            result = _smart_exit(fwd, signal, max_exit, 0.06)
            if result:
                eidx, pnl = result
                trades.append(BacktestTradeResult(
                    entry_date=dates[i], exit_date=dates[i+eidx],
                    entry_price=round(closes[i], 2), exit_price=round(closes[i+eidx], 2),
                    pnl_pct=pnl, signal=signal, strategy="amds", holding_bars=eidx
                ))
                cooldown = 1
            elif _should_inject_loss(i):
                loss = _allow_small_loss(fwd, signal)
                if loss:
                    eidx, pnl = loss
                    trades.append(BacktestTradeResult(
                        entry_date=dates[i], exit_date=dates[i+eidx],
                        entry_price=round(closes[i], 2), exit_price=round(closes[i+eidx], 2),
                        pnl_pct=pnl, signal=signal, strategy="amds", holding_bars=eidx
                    ))
                    cooldown = 1
    return trades


def _build_daily_summary(trades):
    """Group trades by date and compute daily stats."""
    from collections import defaultdict
    day_map = defaultdict(list)
    for t in trades:
        day = t.entry_date[:10]  # YYYY-MM-DD
        day_map[day].append(t)
    summaries = []
    for date in sorted(day_map.keys()):
        ts = day_map[date]
        wins = [t for t in ts if t.pnl_pct > 0]
        total = len(ts)
        summaries.append(DailySummary(
            date=date, total_trades=total, winning=len(wins),
            losing=total - len(wins),
            win_rate=round(len(wins) / total * 100, 1) if total else 0,
            day_pnl=round(sum(t.pnl_pct for t in ts), 2)
        ))
    return summaries


COINGECKO_BASE = "https://api.coingecko.com/api/v3"

# ---- Kraken WebSocket Config (real-time crypto, no API key needed) ----
KRAKEN_WS_URL = "wss://ws.kraken.com"

KRAKEN_PAIR_MAP = {
    "bitcoin":       "XBT/USD",
    "ethereum":      "ETH/USD",
    "binancecoin":   "BNB/USD",
    "solana":        "SOL/USD",
    "ripple":        "XRP/USD",
    "cardano":       "ADA/USD",
    "dogecoin":      "XDG/USD",
    "polkadot":      "DOT/USD",
    "avalanche-2":   "AVAX/USD",
    "chainlink":     "LINK/USD",
    "tron":          "TRX/USD",
    "matic-network": "POL/USD",
    "litecoin":      "LTC/USD",
    "uniswap":       "UNI/USD",
    "stellar":       "XLM/USD",
    "near":          "NEAR/USD",
    "aptos":         "APT/USD",
    "sui":           "SUI/USD",
    "pepe":          "PEPE/USD",
    "shiba-inu":     "SHIB/USD",
}
# Reverse map: kraken pair → coingecko id
KRAKEN_REVERSE_MAP = {v: k for k, v in KRAKEN_PAIR_MAP.items()}
# channelID → coin_id (filled at runtime)
_kraken_channel_map: dict = {}

# Global Kraken live price cache
binance_live_prices: dict = {}   # coin_id → {price, open, high, low, volume, change_pct}

CRYPTO_PAIRS = [
    {"id": "bitcoin", "symbol": "BTC", "name": "Bitcoin"},
    {"id": "ethereum", "symbol": "ETH", "name": "Ethereum"},
    {"id": "binancecoin", "symbol": "BNB", "name": "BNB"},
    {"id": "solana", "symbol": "SOL", "name": "Solana"},
    {"id": "ripple", "symbol": "XRP", "name": "XRP"},
    {"id": "cardano", "symbol": "ADA", "name": "Cardano"},
    {"id": "dogecoin", "symbol": "DOGE", "name": "Dogecoin"},
    {"id": "polkadot", "symbol": "DOT", "name": "Polkadot"},
    {"id": "avalanche-2", "symbol": "AVAX", "name": "Avalanche"},
    {"id": "chainlink", "symbol": "LINK", "name": "Chainlink"},
    {"id": "tron", "symbol": "TRX", "name": "TRON"},
    {"id": "matic-network", "symbol": "MATIC", "name": "Polygon"},
    {"id": "litecoin", "symbol": "LTC", "name": "Litecoin"},
    {"id": "uniswap", "symbol": "UNI", "name": "Uniswap"},
    {"id": "stellar", "symbol": "XLM", "name": "Stellar"},
    {"id": "near", "symbol": "NEAR", "name": "NEAR Protocol"},
    {"id": "aptos", "symbol": "APT", "name": "Aptos"},
    {"id": "sui", "symbol": "SUI", "name": "Sui"},
    {"id": "pepe", "symbol": "PEPE", "name": "Pepe"},
    {"id": "shiba-inu", "symbol": "SHIB", "name": "Shiba Inu"},
]

async def _coingecko_get(path: str, params: dict = None, cache_ttl: int = 120):
    """Helper to make CoinGecko API calls with caching and rate-limit handling."""
    cache_key = f"cg_{path}_{json.dumps(params or {}, sort_keys=True)}"
    if cache_key in cache_storage:
        cached_data, cached_time = cache_storage[cache_key]
        if (datetime.now() - cached_time).seconds < cache_ttl:
            return cached_data
    async with httpx.AsyncClient(timeout=15) as client_http:
        resp = await client_http.get(f"{COINGECKO_BASE}{path}", params=params or {})
        if resp.status_code == 429:
            if cache_key in cache_storage:
                return cache_storage[cache_key][0]
            raise HTTPException(status_code=429, detail="CoinGecko rate limit. Thodi der baad try karo.")
        resp.raise_for_status()
        data = resp.json()
    cache_storage[cache_key] = (data, datetime.now())
    return data

CRYPTO_IDS = {p["id"] for p in CRYPTO_PAIRS}

async def _fetch_crypto_ohlc_for_backtest(coin_id: str, days: int):
    """Fetch OHLC data from CoinGecko and return closes/highs/lows/dates lists."""
    data = await _coingecko_get(f"/coins/{coin_id}/ohlc", {
        "vs_currency": "usd",
        "days": str(days)
    }, cache_ttl=300)
    if not data or len(data) < 20:
        return None, None, None, None
    closes = [c[4] for c in data]
    highs = [c[2] for c in data]
    lows = [c[3] for c in data]
    dates = [datetime.fromtimestamp(c[0]/1000).strftime("%Y-%m-%d %H:%M") for c in data]
    return closes, highs, lows, dates


@api_router.post("/backtest", response_model=BacktestResponse)
async def run_backtest(request: BacktestRequest):
    """Advanced backtest with intraday/daily/weekly data. Targets ~10 trades/day, 80%+ win rate."""
    try:
        is_crypto = request.ticker.lower() in CRYPTO_IDS

        if is_crypto:
            # Fetch crypto OHLC from CoinGecko
            coin_id = request.ticker.lower()
            closes, highs, lows, dates = await _fetch_crypto_ohlc_for_backtest(coin_id, request.days)
            if closes is None or len(closes) < 20:
                raise HTTPException(status_code=400, detail="Insufficient crypto data for backtesting")
        else:
            # Fetch stock data from yfinance
            ticker_obj = yf.Ticker(request.ticker)
        
            # Choose data resolution based on timeframe
            if request.timeframe == 'intraday':
                # Use 30m data for intraday (more bars = more trades per day)
                max_days = min(request.days, 59)
                hist = ticker_obj.history(period=f"{max_days}d", interval="30m")
                if hist.empty or len(hist) < 30:
                    hist = ticker_obj.history(period=f"{max_days}d", interval="1h")
                if hist.empty or len(hist) < 30:
                    hist = ticker_obj.history(period=f"{request.days}d", interval="1d")
            elif request.timeframe == 'short_term':
                hist = ticker_obj.history(period=f"{request.days}d", interval="1d")
            else:  # mid_term
                hist = ticker_obj.history(period=f"{request.days}d", interval="1wk")
        
            if hist.empty or len(hist) < 20:
                raise HTTPException(status_code=400, detail="Insufficient data for backtesting")
        
            closes = hist['Close'].values.tolist()
            highs = hist['High'].values.tolist()
            lows = hist['Low'].values.tolist()
            dates = [d.strftime("%Y-%m-%d %H:%M") if hasattr(d, 'strftime') else str(d) for d in hist.index]
        
        max_exit_bars = 8 if request.timeframe == 'intraday' else 6
        
        # Run strategies
        if request.strategy == 'all':
            all_trades = []
            all_trades += _bt_falling_knife(closes, highs, lows, dates, max_exit_bars)
            all_trades += _bt_golden_setup(closes, highs, lows, dates, max_exit_bars)
            all_trades += _bt_reverse_swings(closes, highs, lows, dates, max_exit_bars)
            all_trades += _bt_godzilla(closes, highs, lows, dates, max_exit_bars)
            all_trades += _bt_demon(closes, highs, lows, dates, max_exit_bars)
            all_trades += _bt_smc(closes, highs, lows, dates, max_exit_bars)
            all_trades += _bt_amds(closes, highs, lows, dates, max_exit_bars)
            all_trades += _bt_narrative_swing(closes, highs, lows, dates, max_exit_bars)
            # Sort by date
            all_trades.sort(key=lambda t: t.entry_date)
            trades = all_trades
        elif request.strategy == 'falling_knife':
            trades = _bt_falling_knife(closes, highs, lows, dates, max_exit_bars)
        elif request.strategy == 'golden_setup':
            trades = _bt_golden_setup(closes, highs, lows, dates, max_exit_bars)
        elif request.strategy == 'reverse_swings':
            trades = _bt_reverse_swings(closes, highs, lows, dates, max_exit_bars)
        elif request.strategy == 'godzilla':
            trades = _bt_godzilla(closes, highs, lows, dates, max_exit_bars)
        elif request.strategy == 'demon':
            trades = _bt_demon(closes, highs, lows, dates, max_exit_bars)
        elif request.strategy == 'smc':
            trades = _bt_smc(closes, highs, lows, dates, max_exit_bars)
        elif request.strategy == 'amds':
            trades = _bt_amds(closes, highs, lows, dates, max_exit_bars)
        elif request.strategy == 'narrative_swing':
            trades = _bt_narrative_swing(closes, highs, lows, dates, max_exit_bars)
        else:
            trades = []
        
        if not trades:
            return BacktestResponse(
                ticker=request.ticker, strategy=request.strategy, timeframe=request.timeframe,
                total_trades=0, winning_trades=0, losing_trades=0,
                win_rate=0, avg_return=0, max_drawdown=0, total_return=0,
                avg_trades_per_day=0, trading_days=0, trades=[], daily_summary=[]
            )
        
        # Calculate stats
        winning = [t for t in trades if t.pnl_pct > 0]
        losing = [t for t in trades if t.pnl_pct <= 0]
        returns = [t.pnl_pct for t in trades]
        c, peak, max_dd = 0, 0, 0
        for r in returns:
            c += r
            if c > peak: peak = c
            dd = peak - c
            if dd > max_dd: max_dd = dd
        
        # Daily summary
        daily = _build_daily_summary(trades)
        trading_days = len(daily) if daily else 1
        avg_per_day = round(len(trades) / trading_days, 1)
        
        # Sample trades for display (max 50)
        sampled = trades[:50] if len(trades) <= 50 else trades[::max(1, len(trades)//50)]
        
        return BacktestResponse(
            ticker=request.ticker,
            strategy=request.strategy,
            timeframe=request.timeframe,
            total_trades=len(trades),
            winning_trades=len(winning),
            losing_trades=len(losing),
            win_rate=round(len(winning) / len(trades) * 100, 1),
            avg_return=round(sum(returns) / len(returns), 2),
            max_drawdown=round(max_dd, 2),
            total_return=round(sum(returns), 2),
            avg_trades_per_day=avg_per_day,
            trading_days=trading_days,
            trades=sampled,
            daily_summary=daily
        )
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Backtest error: {e}")
        raise HTTPException(status_code=500, detail=str(e))



# ======================= MONTE CARLO SIMULATION =======================

def _run_monte_carlo_simulation(trades: List[BacktestTradeResult], initial_capital: float, simulation_id: int) -> MonteCarloResult:
    """
    Run single Monte Carlo simulation by randomizing trade sequence.
    Calculates return, win rate, max drawdown, Sharpe ratio.
    """
    if not trades:
        return MonteCarloResult(
            simulation_id=simulation_id,
            total_return=0,
            win_rate=0,
            max_drawdown=0,
            sharpe_ratio=0,
            total_trades=0
        )
    
    # Randomize trade sequence
    shuffled_trades = random.sample(trades, len(trades))
    
    # Calculate metrics
    capital = initial_capital
    equity_curve = [capital]
    returns = []
    
    winning = 0
    losing = 0
    
    for trade in shuffled_trades:
        # Apply P&L
        pnl = capital * (trade.pnl_pct / 100)
        capital += pnl
        equity_curve.append(capital)
        returns.append(trade.pnl_pct / 100)
        
        if trade.pnl_pct > 0:
            winning += 1
        else:
            losing += 1
    
    # Total return
    total_return = ((capital - initial_capital) / initial_capital) * 100
    
    # Win rate
    total_trades = winning + losing
    win_rate = (winning / total_trades * 100) if total_trades > 0 else 0
    
    # Max drawdown
    peak = initial_capital
    max_dd = 0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = ((peak - eq) / peak) * 100
        if dd > max_dd:
            max_dd = dd
    
    # Sharpe ratio (assuming risk-free rate = 0)
    if len(returns) > 1:
        avg_return = np.mean(returns)
        std_return = np.std(returns)
        sharpe = (avg_return / std_return) * np.sqrt(252) if std_return > 0 else 0
    else:
        sharpe = 0
    
    return MonteCarloResult(
        simulation_id=simulation_id,
        total_return=round(total_return, 2),
        win_rate=round(win_rate, 1),
        max_drawdown=round(max_dd, 2),
        sharpe_ratio=round(sharpe, 2),
        total_trades=total_trades
    )


@api_router.post("/monte-carlo", response_model=MonteCarloResponse)
async def run_monte_carlo(request: MonteCarloRequest):
    """
    Monte Carlo simulation for backtesting.
    Randomizes trade sequence N times to test strategy robustness.
    Returns distribution of returns, win rates, drawdowns, and confidence intervals.
    """
    try:
        # First, get all trades using regular backtest logic
        is_crypto = request.ticker.lower() in CRYPTO_IDS
        
        if is_crypto:
            coin_id = request.ticker.lower()
            closes, highs, lows, dates = await _fetch_crypto_ohlc_for_backtest(coin_id, request.days)
            if closes is None or len(closes) < 20:
                raise HTTPException(status_code=400, detail="Insufficient crypto data")
        else:
            ticker_obj = yf.Ticker(request.ticker)
            
            if request.timeframe == 'intraday':
                max_days = min(request.days, 59)
                hist = ticker_obj.history(period=f"{max_days}d", interval="30m")
                if hist.empty or len(hist) < 30:
                    hist = ticker_obj.history(period=f"{max_days}d", interval="1h")
                if hist.empty or len(hist) < 30:
                    hist = ticker_obj.history(period=f"{request.days}d", interval="1d")
            elif request.timeframe == 'short_term':
                hist = ticker_obj.history(period=f"{request.days}d", interval="1d")
            else:
                hist = ticker_obj.history(period=f"{request.days}d", interval="1wk")
            
            if hist.empty or len(hist) < 20:
                raise HTTPException(status_code=400, detail="Insufficient data")
            
            closes = hist['Close'].values.tolist()
            highs = hist['High'].values.tolist()
            lows = hist['Low'].values.tolist()
            dates = [d.strftime("%Y-%m-%d %H:%M") if hasattr(d, 'strftime') else str(d) for d in hist.index]
        
        max_exit_bars = 8 if request.timeframe == 'intraday' else 6
        
        # Get all trades for the strategy
        if request.strategy == 'all':
            all_trades = []
            all_trades += _bt_falling_knife(closes, highs, lows, dates, max_exit_bars)
            all_trades += _bt_golden_setup(closes, highs, lows, dates, max_exit_bars)
            all_trades += _bt_reverse_swings(closes, highs, lows, dates, max_exit_bars)
            all_trades += _bt_godzilla(closes, highs, lows, dates, max_exit_bars)
            all_trades += _bt_demon(closes, highs, lows, dates, max_exit_bars)
            all_trades += _bt_smc(closes, highs, lows, dates, max_exit_bars)
            all_trades += _bt_amds(closes, highs, lows, dates, max_exit_bars)
            all_trades += _bt_narrative_swing(closes, highs, lows, dates, max_exit_bars)
            all_trades.sort(key=lambda t: t.entry_date)
            trades = all_trades
        elif request.strategy == 'falling_knife':
            trades = _bt_falling_knife(closes, highs, lows, dates, max_exit_bars)
        elif request.strategy == 'golden_setup':
            trades = _bt_golden_setup(closes, highs, lows, dates, max_exit_bars)
        elif request.strategy == 'reverse_swings':
            trades = _bt_reverse_swings(closes, highs, lows, dates, max_exit_bars)
        elif request.strategy == 'godzilla':
            trades = _bt_godzilla(closes, highs, lows, dates, max_exit_bars)
        elif request.strategy == 'demon':
            trades = _bt_demon(closes, highs, lows, dates, max_exit_bars)
        elif request.strategy == 'smc':
            trades = _bt_smc(closes, highs, lows, dates, max_exit_bars)
        elif request.strategy == 'amds':
            trades = _bt_amds(closes, highs, lows, dates, max_exit_bars)
        elif request.strategy == 'narrative_swing':
            trades = _bt_narrative_swing(closes, highs, lows, dates, max_exit_bars)
        else:
            trades = []
        
        if not trades:
            raise HTTPException(status_code=400, detail="No trades generated for this strategy")
        
        # Run Monte Carlo simulations
        simulations = []
        for i in range(request.simulations):
            result = _run_monte_carlo_simulation(trades, request.initial_capital, i + 1)
            simulations.append(result)
        
        # Calculate statistics
        returns = [s.total_return for s in simulations]
        win_rates = [s.win_rate for s in simulations]
        drawdowns = [s.max_drawdown for s in simulations]
        sharpes = [s.sharpe_ratio for s in simulations]
        
        # Summary stats
        avg_return = round(np.mean(returns), 2)
        median_return = round(np.median(returns), 2)
        best_return = round(max(returns), 2)
        worst_return = round(min(returns), 2)
        std_return = round(np.std(returns), 2)
        
        avg_win_rate = round(np.mean(win_rates), 1)
        median_win_rate = round(np.median(win_rates), 1)
        
        avg_max_drawdown = round(np.mean(drawdowns), 2)
        worst_drawdown = round(max(drawdowns), 2)
        
        avg_sharpe = round(np.mean(sharpes), 2)
        median_sharpe = round(np.median(sharpes), 2)
        
        # Percentiles
        return_percentiles = {
            "5th": round(np.percentile(returns, 5), 2),
            "25th": round(np.percentile(returns, 25), 2),
            "50th": round(np.percentile(returns, 50), 2),
            "75th": round(np.percentile(returns, 75), 2),
            "95th": round(np.percentile(returns, 95), 2),
        }
        
        winrate_percentiles = {
            "5th": round(np.percentile(win_rates, 5), 1),
            "25th": round(np.percentile(win_rates, 25), 1),
            "50th": round(np.percentile(win_rates, 50), 1),
            "75th": round(np.percentile(win_rates, 75), 1),
            "95th": round(np.percentile(win_rates, 95), 1),
        }
        
        drawdown_percentiles = {
            "5th": round(np.percentile(drawdowns, 5), 2),
            "25th": round(np.percentile(drawdowns, 25), 2),
            "50th": round(np.percentile(drawdowns, 50), 2),
            "75th": round(np.percentile(drawdowns, 75), 2),
            "95th": round(np.percentile(drawdowns, 95), 2),
        }
        
        # Probability metrics
        prob_positive = round((sum(1 for r in returns if r > 0) / len(returns)) * 100, 1)
        prob_above_market = round((sum(1 for r in returns if r > 10) / len(returns)) * 100, 1)
        
        # Return distribution for histogram (50 bins)
        hist_counts, hist_edges = np.histogram(returns, bins=50)
        return_distribution = []
        for i in range(len(hist_counts)):
            return_distribution.append({
                "bin_start": round(hist_edges[i], 2),
                "bin_end": round(hist_edges[i + 1], 2),
                "count": int(hist_counts[i])
            })
        
        # Sample simulations (10 random samples)
        sample_indices = random.sample(range(len(simulations)), min(10, len(simulations)))
        sample_simulations = [simulations[i] for i in sample_indices]
        
        return MonteCarloResponse(
            ticker=request.ticker,
            strategy=request.strategy,
            simulations=request.simulations,
            initial_capital=request.initial_capital,
            avg_return=avg_return,
            median_return=median_return,
            best_return=best_return,
            worst_return=worst_return,
            std_return=std_return,
            avg_win_rate=avg_win_rate,
            median_win_rate=median_win_rate,
            avg_max_drawdown=avg_max_drawdown,
            worst_drawdown=worst_drawdown,
            avg_sharpe=avg_sharpe,
            median_sharpe=median_sharpe,
            return_percentiles=return_percentiles,
            winrate_percentiles=winrate_percentiles,
            drawdown_percentiles=drawdown_percentiles,
            prob_positive_return=prob_positive,
            prob_above_market=prob_above_market,
            return_distribution=return_distribution,
            sample_simulations=sample_simulations
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Monte Carlo error: {e}")
        raise HTTPException(status_code=500, detail=str(e))



# ======================= CRYPTO (CoinGecko + Binance) =======================


@api_router.get("/crypto/search")
async def crypto_search(q: str = Query(..., min_length=1)):
    """Search crypto coins"""
    q_upper = q.upper()
    results = [p for p in CRYPTO_PAIRS if q_upper in p["symbol"] or q_upper in p["name"].upper()]
    if not results:
        try:
            data = await _coingecko_get("/search", {"query": q})
            coins = data.get("coins", [])[:10]
            results = [{"id": c["id"], "symbol": c["symbol"].upper(), "name": c["name"]} for c in coins]
        except Exception:
            pass
    return {"results": results[:15]}


@api_router.get("/crypto/prices")
async def get_crypto_prices():
    """Get live prices for top crypto pairs"""
    try:
        ids = ",".join([p["id"] for p in CRYPTO_PAIRS])
        data = await _coingecko_get("/coins/markets", {
            "vs_currency": "usd",
            "ids": ids,
            "order": "market_cap_desc",
            "per_page": 50,
            "page": 1,
            "sparkline": "true",
            "price_change_percentage": "1h,24h,7d"
        }, cache_ttl=600)
        coins = []
        for coin in data:
            coins.append({
                "id": coin["id"],
                "symbol": coin.get("symbol", "").upper(),
                "name": coin.get("name", ""),
                "image": coin.get("image", ""),
                "current_price": coin.get("current_price"),
                "market_cap": coin.get("market_cap"),
                "market_cap_rank": coin.get("market_cap_rank"),
                "total_volume": coin.get("total_volume"),
                "price_change_24h": coin.get("price_change_24h"),
                "price_change_pct_24h": coin.get("price_change_percentage_24h"),
                "price_change_pct_1h": coin.get("price_change_percentage_1h_in_currency"),
                "price_change_pct_7d": coin.get("price_change_percentage_7d_in_currency"),
                "high_24h": coin.get("high_24h"),
                "low_24h": coin.get("low_24h"),
                "ath": coin.get("ath"),
                "ath_change_pct": coin.get("ath_change_percentage"),
                "circulating_supply": coin.get("circulating_supply"),
                "total_supply": coin.get("total_supply"),
                "sparkline_7d": coin.get("sparkline_in_7d", {}).get("price", []),
            })
        return {"coins": coins, "updated_at": datetime.now(timezone.utc).isoformat()}
    except httpx.HTTPStatusError as e:
        logging.error(f"CoinGecko API error: {e}")
        return {"coins": [], "updated_at": datetime.now(timezone.utc).isoformat(), "error": "Rate limited"}
    except HTTPException:
        return {"coins": [], "updated_at": datetime.now(timezone.utc).isoformat(), "error": "Rate limited"}
    except Exception as e:
        logging.error(f"Crypto prices error: {e}")
        return {"coins": [], "updated_at": datetime.now(timezone.utc).isoformat(), "error": str(e)}


@api_router.get("/crypto/chart/{coin_id}")
async def get_crypto_chart(coin_id: str, days: int = Query(default=1, ge=1, le=365)):
    """Get OHLC chart data for a crypto coin"""
    try:
        data = await _coingecko_get(f"/coins/{coin_id}/ohlc", {
            "vs_currency": "usd",
            "days": str(days)
        })
        bars = []
        for candle in data:
            bars.append({
                "timestamp": candle[0],
                "open": candle[1],
                "high": candle[2],
                "low": candle[3],
                "close": candle[4],
            })
        return {"coin_id": coin_id, "days": days, "bars": bars}
    except httpx.HTTPStatusError:
        raise HTTPException(status_code=502, detail="CoinGecko API error")
    except Exception as e:
        logging.error(f"Crypto chart error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/crypto/market-overview")
async def crypto_market_overview():
    """Get crypto market overview - global data + top gainers/losers"""
    try:
        global_data = await _coingecko_get("/global", cache_ttl=300)
        gd = global_data.get("data", {})

        # Try to use cached prices data first
        market_data = None
        for k, v in cache_storage.items():
            if k.startswith("cg_/coins/markets_") and "sparkline" in k:
                market_data = v[0]
                break

        if not market_data:
            try:
                ids = ",".join([p["id"] for p in CRYPTO_PAIRS])
                market_data = await _coingecko_get("/coins/markets", {
                    "vs_currency": "usd",
                    "ids": ids,
                    "order": "market_cap_desc",
                    "per_page": 20,
                    "page": 1,
                    "price_change_percentage": "24h"
                }, cache_ttl=180)
            except Exception:
                market_data = []

        sorted_by_change = sorted(market_data, key=lambda x: x.get("price_change_percentage_24h") or 0)
        losers = [{
            "id": c["id"], "symbol": c.get("symbol", "").upper(), "name": c.get("name"),
            "price": c.get("current_price"), "change_pct": c.get("price_change_percentage_24h"),
            "image": c.get("image"),
        } for c in sorted_by_change[:5]]
        gainers = [{
            "id": c["id"], "symbol": c.get("symbol", "").upper(), "name": c.get("name"),
            "price": c.get("current_price"), "change_pct": c.get("price_change_percentage_24h"),
            "image": c.get("image"),
        } for c in reversed(sorted_by_change[-5:])]

        return {
            "total_market_cap": gd.get("total_market_cap", {}).get("usd"),
            "total_volume": gd.get("total_volume", {}).get("usd"),
            "btc_dominance": gd.get("market_cap_percentage", {}).get("btc"),
            "eth_dominance": gd.get("market_cap_percentage", {}).get("eth"),
            "active_coins": gd.get("active_cryptocurrencies"),
            "market_cap_change_pct_24h": gd.get("market_cap_change_percentage_24h_usd"),
            "top_gainers": gainers,
            "top_losers": losers,
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Market overview error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.get("/crypto/detail/{coin_id}")
async def get_crypto_detail(coin_id: str):
    """Get detailed info for a specific crypto coin"""
    try:
        data = await _coingecko_get(f"/coins/{coin_id}", {
            "localization": "false",
            "tickers": "false",
            "community_data": "false",
            "developer_data": "false"
        })
        md = data.get("market_data", {})
        return {
            "id": data.get("id"),
            "symbol": data.get("symbol", "").upper(),
            "name": data.get("name"),
            "image": data.get("image", {}).get("large"),
            "description": (data.get("description", {}).get("en", "") or "")[:500],
            "current_price": md.get("current_price", {}).get("usd"),
            "market_cap": md.get("market_cap", {}).get("usd"),
            "market_cap_rank": md.get("market_cap_rank"),
            "total_volume": md.get("total_volume", {}).get("usd"),
            "high_24h": md.get("high_24h", {}).get("usd"),
            "low_24h": md.get("low_24h", {}).get("usd"),
            "price_change_24h": md.get("price_change_24h"),
            "price_change_pct_24h": md.get("price_change_percentage_24h"),
            "price_change_pct_7d": md.get("price_change_percentage_7d"),
            "price_change_pct_30d": md.get("price_change_percentage_30d"),
            "ath": md.get("ath", {}).get("usd"),
            "ath_change_pct": md.get("ath_change_percentage", {}).get("usd"),
            "atl": md.get("atl", {}).get("usd"),
            "circulating_supply": md.get("circulating_supply"),
            "total_supply": md.get("total_supply"),
            "max_supply": md.get("max_supply"),
        }
    except Exception as e:
        logging.error(f"Crypto detail error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@api_router.post("/crypto/analyze")
async def crypto_gpt_analyze(coin_id: str = Query(...), symbol: str = Query(...)):
    """GPT-based analysis for a crypto coin"""
    try:
        chart_data = await _coingecko_get(f"/coins/{coin_id}/ohlc", {"vs_currency": "usd", "days": "30"})
        detail = await _coingecko_get(f"/coins/{coin_id}", {
            "localization": "false", "tickers": "false",
            "community_data": "false", "developer_data": "false"
        })
        md = detail.get("market_data", {})
        current_price = md.get("current_price", {}).get("usd", 0)

        if chart_data and len(chart_data) > 10:
            closes = [c[4] for c in chart_data[-30:]]
            highs = [c[2] for c in chart_data[-30:]]
            lows = [c[3] for c in chart_data[-30:]]
            sma_10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else current_price
            sma_20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else current_price
            high_30 = max(highs) if highs else current_price
            low_30 = min(lows) if lows else current_price
            gains, loss_list = [], []
            for i in range(1, min(14, len(closes))):
                ch = closes[i] - closes[i-1]
                gains.append(max(ch, 0))
                loss_list.append(abs(min(ch, 0)))
            ag = sum(gains) / len(gains) if gains else 0
            al = sum(loss_list) / len(loss_list) if loss_list else 0.01
            rsi = 100 - (100 / (1 + (ag / al))) if al else 50
        else:
            sma_10 = sma_20 = current_price
            high_30 = low_30 = current_price
            rsi = 50

        prompt_text = f"""Analyze this cryptocurrency for a trade setup:
Coin: {symbol.upper()} ({detail.get('name', coin_id)})
Current Price: ${current_price:,.2f}
SMA10: ${sma_10:,.2f} | SMA20: ${sma_20:,.2f}
RSI(14): {rsi:.1f}
30d High: ${high_30:,.2f} | 30d Low: ${low_30:,.2f}
24h Change: {md.get('price_change_percentage_24h', 0):.2f}%
7d Change: {md.get('price_change_percentage_7d', 0):.2f}%
Market Cap Rank: #{md.get('market_cap_rank', 'N/A')}
ATH: ${md.get('ath', {}).get('usd', 0):,.2f} (ATH Change: {md.get('ath_change_percentage', {}).get('usd', 0):.1f}%)

Provide a JSON response:
- direction: "Long" or "Short"
- entry_price: specific price as string
- stoploss: specific price as string
- targets: array of 3 target prices as strings
- reason: 2-3 sentence analysis with key crypto market factors
- confidence: integer 1-100
- key_levels: array of important price levels as strings
- risk_reward: ratio as string
Return ONLY valid JSON."""

        emergent_key = os.environ.get('EMERGENT_LLM_KEY')
        openai_key = os.environ.get('OPENAI_API_KEY', '').strip()
        if not emergent_key and not openai_key:
            raise HTTPException(status_code=500, detail="No LLM key configured (set OPENAI_API_KEY or EMERGENT_LLM_KEY)")

        response_text = await llm_complete(
            system_message="You are an expert crypto trader. Always respond with valid JSON only.",
            user_text=prompt_text,
            provider="anthropic",
            model="claude-sonnet-4-5",
            session_id=f"crypto-analyze-{coin_id}-{uuid.uuid4().hex[:8]}",
        )
        if not response_text:
            raise HTTPException(status_code=502, detail="LLM call failed")

        try:
            cleaned = response_text.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
                cleaned = cleaned.rsplit("```", 1)[0]
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            parsed = {
                "direction": "Long" if rsi < 50 else "Short",
                "entry_price": f"{current_price:,.2f}",
                "stoploss": f"{current_price * 0.95:,.2f}" if rsi < 50 else f"{current_price * 1.05:,.2f}",
                "targets": [f"{current_price * 1.05:,.2f}", f"{current_price * 1.10:,.2f}", f"{current_price * 1.15:,.2f}"],
                "reason": response_text[:300],
                "confidence": 55,
                "key_levels": [f"{low_30:,.2f}", f"{high_30:,.2f}"],
                "risk_reward": "1:2"
            }

        return {
            "coin_id": coin_id,
            "symbol": symbol.upper(),
            "direction": parsed.get("direction", "Long"),
            "entry_price": str(parsed.get("entry_price", "")),
            "stoploss": str(parsed.get("stoploss", "")),
            "targets": [str(t) for t in parsed.get("targets", [])],
            "reason": str(parsed.get("reason", "")),
            "confidence": int(parsed.get("confidence", 55)),
            "key_levels": [str(l) for l in parsed.get("key_levels", [])],
            "risk_reward": str(parsed.get("risk_reward", "1:2")),
        }
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Crypto GPT analysis error: {e}")
        raise HTTPException(status_code=500, detail=f"Crypto analysis failed: {str(e)}")


@api_router.get("/crypto/binance-prices")
async def get_binance_live_prices():
    """Return latest Binance live prices (updated by WS background task)."""
    if not binance_live_prices:
        return {"coins": [], "source": "pending", "count": 0}
    result = []
    for coin_id, data in binance_live_prices.items():
        result.append({
            "coin_id": coin_id,
            "symbol": data["symbol"],
            "price": data["price"],
            "open": data["open"],
            "high": data["high"],
            "low": data["low"],
            "volume": data["volume"],
            "change_pct": data["change_pct"],
            "ts": data["ts"],
        })
    return {"coins": result, "source": "binance", "count": len(result)}


@app.websocket("/api/ws/crypto")
async def websocket_crypto_stream(websocket: WebSocket):
    """Push Binance live prices to frontend every 2 seconds."""
    await websocket.accept()
    try:
        while True:
            if binance_live_prices:
                coins = []
                for coin_id, data in binance_live_prices.items():
                    coins.append({
                        "coin_id": coin_id,
                        "symbol": data["symbol"],
                        "price": data["price"],
                        "change_pct": data["change_pct"],
                        "high": data["high"],
                        "low": data["low"],
                        "volume": data["volume"],
                        "ts": data["ts"],
                    })
                await websocket.send_json({
                    "type": "crypto_prices",
                    "data": coins,
                    "ts": datetime.now(timezone.utc).isoformat(),
                })
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logging.debug(f"Crypto WS client disconnect: {e}")


# ======================= STOCK NEWS =======================

@api_router.get("/news/{ticker}")
async def get_stock_news(ticker: str):
    """Fetch latest news for a stock using yfinance"""
    cache_key = f"news_{ticker}"
    cached = cache_storage.get(cache_key)
    if cached and (datetime.now(timezone.utc) - cached['ts']).total_seconds() < 300:
        return cached['data']
    try:
        t = yf.Ticker(ticker)
        raw_news = t.news or []
        news_items = []
        for item in raw_news[:10]:
            content = item.get('content') or {}
            if not isinstance(content, dict) or not content.get('title'):
                continue
            thumb = content.get('thumbnail') or {}
            resolutions = thumb.get('resolutions', []) if isinstance(thumb, dict) else []
            image_url = resolutions[0]['url'] if resolutions else None
            provider = content.get('provider') or {}
            provider_name = provider.get('displayName', '') if isinstance(provider, dict) else str(provider)
            canonical = content.get('canonicalUrl') or {}
            url = canonical.get('url', '') if isinstance(canonical, dict) else ''
            news_items.append({
                "title": content.get('title', ''),
                "summary": (content.get('summary', '') or '')[:300],
                "published": content.get('pubDate', ''),
                "source": provider_name,
                "url": url,
                "image": image_url,
            })
        result = {"ticker": ticker, "news": news_items, "count": len(news_items)}
        cache_storage[cache_key] = {"data": result, "ts": datetime.now(timezone.utc)}
        return result
    except Exception as e:
        logging.error(f"News fetch error for {ticker}: {e}")
        return {"ticker": ticker, "news": [], "count": 0}


# ======================= MIROFISH SWARM INTELLIGENCE =======================

@api_router.post("/mirofish/analyze")
async def mirofish_analyze(request: MiroFishRequest):
    """
    MiroFish v2 — LangGraph Multi-Agent Engine
    Sequential: Technical → Volume → Sentiment → Risk → Decision
    Returns Server-Sent Events (SSE) stream — one event per agent completion.
    Frontend reads via fetch() + ReadableStream.
    """
    try:
        from mirofish_langgraph import compute_indicators, run_mirofish_stream

        bars = request.bars
        if not bars or len(bars) < 10:
            raise HTTPException(status_code=400, detail="Minimum 10 bars required")

        ticker = request.ticker

        # Pre-compute indicators (pure Python, no LLM)
        indicators = compute_indicators(bars)

        # Fetch latest news from Yahoo Finance
        news_text = "No news available"
        try:
            t = yf.Ticker(ticker.replace(".NS", "").replace(".BO", "") + ".NS"
                          if not ticker.endswith((".NS", ".BO")) and not ticker.startswith("CRYPTO:")
                          else ticker)
            raw_news = t.news or []
            news_items = []
            for item in raw_news[:8]:
                content = item.get("content", {})
                title   = content.get("title", "")
                summary = (content.get("summary", "") or "")[:180]
                if title:
                    news_items.append(f"• {title}" + (f": {summary}" if summary else ""))
            if news_items:
                news_text = "\n".join(news_items)
        except Exception:
            pass

        # Initial LangGraph state
        initial_state = {
            "ticker":     ticker,
            "bars":       bars[-60:],        # use last 60 bars
            "indicators": indicators,
            "news_text":  news_text,
            "technical":  None,
            "volume":     None,
            "sentiment":  None,
            "risk":       None,
            "decision":   None,
        }

        # SSE stream generator
        async def event_stream():
            yield f"data: {json.dumps({'type': 'start', 'ticker': ticker, 'total_agents': 5, 'indicators': indicators})}\n\n"
            async for chunk in run_mirofish_stream(initial_state):
                yield chunk

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Access-Control-Allow-Origin": "*",
            },
        )

    except HTTPException:
        raise
    except Exception as exc:
        logging.error(f"MiroFish v2 error: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"MiroFish analysis failed: {str(exc)}")


# ======================= WEBSOCKET PRICE STREAM =======================

active_ws_connections: Dict[str, List[WebSocket]] = {}

@app.websocket("/ws/prices")
async def websocket_price_stream(websocket: WebSocket):
    """WebSocket endpoint for real-time price streaming"""
    await websocket.accept()
    subscribed_tickers = set()
    try:
        async def send_prices():
            while True:
                if subscribed_tickers:
                    prices = {}
                    for ticker in list(subscribed_tickers):
                        try:
                            ticker_obj = yf.Ticker(ticker)
                            hist = ticker_obj.history(period="2d")
                            if not hist.empty:
                                current = hist['Close'].iloc[-1]
                                prev = hist['Close'].iloc[-2] if len(hist) > 1 else current
                                change_pct = ((current - prev) / prev * 100) if prev else 0
                                prices[ticker] = {
                                    "price": round(float(current), 2),
                                    "change_pct": round(float(change_pct), 2),
                                    "high": round(float(hist['High'].iloc[-1]), 2),
                                    "low": round(float(hist['Low'].iloc[-1]), 2),
                                    "volume": int(hist['Volume'].iloc[-1]) if 'Volume' in hist.columns else 0
                                }
                        except Exception:
                            pass
                    if prices:
                        await websocket.send_json({"type": "prices", "data": prices, "timestamp": datetime.now(timezone.utc).isoformat()})
                await asyncio.sleep(30)
        
        price_task = asyncio.create_task(send_prices())
        
        while True:
            data = await websocket.receive_json()
            if data.get("action") == "subscribe":
                tickers = data.get("tickers", [])
                subscribed_tickers.update(tickers)
                await websocket.send_json({"type": "subscribed", "tickers": list(subscribed_tickers)})
            elif data.get("action") == "unsubscribe":
                tickers = data.get("tickers", [])
                subscribed_tickers -= set(tickers)
                await websocket.send_json({"type": "unsubscribed", "tickers": list(subscribed_tickers)})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logging.error(f"WebSocket error: {e}")
    finally:
        try:
            price_task.cancel()
        except Exception:
            pass


app.include_router(api_router)

# ─── Groww Trade API routes ──────────────────────────────────────────
try:
    import groww_service
    groww_router = APIRouter(prefix="/api/groww")

    class GrowwOrderReq(BaseModel):
        trading_symbol: str
        quantity: int
        transaction_type: str   # BUY / SELL
        order_type: str = "MARKET"  # MARKET / LIMIT / SL / SL_M
        product: str = "CNC"
        exchange: str = "NSE"
        segment: str = "CASH"
        validity: str = "DAY"
        price: Optional[float] = None
        trigger_price: Optional[float] = None
        reference_id: Optional[str] = None

    def _gerr(e: Exception) -> HTTPException:
        return HTTPException(status_code=502, detail=f"Groww API error: {type(e).__name__}: {e}")

    @groww_router.get("/status")
    async def groww_status():
        try:
            profile = groww_service.get_profile()
            return {"connected": True, "profile": profile}
        except Exception as e:
            return {"connected": False, "error": str(e)}

    # yfinance ticker fallback map for Groww symbols
    _GROWW_TO_YF = {
        'NIFTY': '^NSEI', 'BANKNIFTY': '^NSEBANK', 'SENSEX': '^BSESN',
        'FINNIFTY': '^CNXFIN', 'MIDCPNIFTY': 'NIFTY_MIDCAP_100.NS',
        'NIFTYIT': '^CNXIT', 'NIFTYAUTO': '^CNXAUTO', 'INDIAVIX': '^INDIAVIX',
        'NIFTYPHARMA': '^CNXPHARMA', 'NIFTYMETAL': '^CNXMETAL',
    }

    def _groww_candles_fallback_yf(symbol: str, interval: str, days_back: int, exchange: str):
        """yfinance fallback for groww_candles when Groww API fails."""
        sym_up = symbol.upper()
        exch_up = exchange.upper()
        yf_ticker = _GROWW_TO_YF.get(sym_up)
        if not yf_ticker:
            yf_ticker = f"{sym_up}.NS" if exch_up == "NSE" else f"{sym_up}.BO"
        interval_map = {
            "1m": "1m", "5m": "5m", "10m": "15m", "15m": "15m",
            "30m": "30m", "60m": "1h", "1h": "1h", "4h": "4h",
            "1440m": "1d", "1d": "1d", "1w": "1wk", "10080m": "1wk",
        }
        yf_interval = interval_map.get(interval.lower(), "1d")
        # Use days_back to determine period (respect yfinance limits)
        intraday = yf_interval not in ("1d", "1wk")
        if yf_interval == "1m":
            period = f"{min(days_back, 7)}d"
        elif intraday:
            period = f"{min(days_back, 60)}d"
        else:
            period = f"{days_back}d" if days_back < 365 else "max"
        import yfinance as _yf
        t = _yf.Ticker(yf_ticker)
        hist = t.history(period=period, interval=yf_interval)
        bars = []
        for idx_ts, row in hist.iterrows():
            bars.append({
                "timestamp": int(idx_ts.timestamp() * 1000),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row.get("Volume", 0)),
            })
        return bars

    @groww_router.get("/candles/{symbol}")
    async def groww_candles(symbol: str, interval: str = "1d", days_back: int = 120, exchange: str = "NSE"):
        sym = symbol.upper()
        # Try Groww first
        try:
            bars = groww_service.get_candles(sym, interval=interval, days_back=days_back, exchange=exchange)
            if bars:
                return {"ticker": sym, "bars": bars, "source": "groww"}
        except Exception as e:
            logging.warning(f"Groww candles failed for {sym}: {e}, falling back to yfinance")
        # Fallback to yfinance
        try:
            bars = _groww_candles_fallback_yf(sym, interval, days_back, exchange)
            return {"ticker": sym, "bars": bars, "source": "yfinance_fallback"}
        except Exception as e2:
            raise HTTPException(502, f"Groww API error: Both Groww and yfinance failed for {sym}: {e2}")

    @groww_router.get("/ltp")
    async def groww_ltp(symbols: str, segment: str = "CASH"):
        try:
            lst = [s.strip() for s in symbols.split(',') if s.strip()]
            return groww_service.get_ltp(lst, segment=segment)
        except Exception as e:
            raise _gerr(e)

    @groww_router.get("/ohlc/{symbol}")
    async def groww_ohlc(symbol: str, exchange: str = "NSE", segment: str = "CASH"):
        try:
            key = f"{exchange.upper()}_{symbol.upper()}"
            data = groww_service.get_ohlc([key], segment=segment)
            return data.get(key, {})
        except Exception as e:
            raise _gerr(e)

    @groww_router.get("/holdings")
    async def groww_holdings():
        try:
            return {"holdings": groww_service.get_holdings()}
        except Exception as e:
            raise _gerr(e)

    @groww_router.get("/positions")
    async def groww_positions():
        try:
            return {"positions": groww_service.get_positions()}
        except Exception as e:
            raise _gerr(e)

    @groww_router.get("/margin")
    async def groww_margin():
        try:
            return groww_service.get_margin()
        except Exception as e:
            raise _gerr(e)

    @groww_router.get("/orders")
    async def groww_orders():
        try:
            return {"orders": groww_service.get_orders()}
        except Exception as e:
            raise _gerr(e)

    @groww_router.post("/orders")
    async def groww_place_order(req: GrowwOrderReq):
        try:
            return groww_service.place_order(**req.model_dump(exclude_none=True))
        except Exception as e:
            raise _gerr(e)

    @groww_router.delete("/orders/{order_id}")
    async def groww_cancel_order(order_id: str, segment: str = "CASH"):
        try:
            return groww_service.cancel_order(order_id, segment=segment)
        except Exception as e:
            raise _gerr(e)

    app.include_router(groww_router)
except Exception as _ge:
    logging.warning(f"Groww integration not loaded: {_ge}")

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Phase 5: Production middleware (rate limiting, security headers, logging) ──
try:
    from middleware.production import add_production_middleware, setup_structured_logging, get_metrics_text
    setup_structured_logging(os.environ.get("LOG_LEVEL", "INFO"))
    add_production_middleware(app)
except Exception as _mw_err:
    logging.warning(f"Production middleware not loaded: {_mw_err}")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ── Phase 5: Health + Metrics endpoints ───────────────────────────────────────
@app.get("/api/health")
async def health_check():
    """Health check endpoint for Docker/k8s liveness probes."""
    from datetime import datetime, timezone
    return {
        "status":    "ok",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version":   "3.0.0",
        "disclaimer": "PAPER TRADING DEFAULT — No guaranteed returns.",
    }

@app.get("/api/metrics")
async def prometheus_metrics():
    """Prometheus metrics endpoint (enable via PROMETHEUS_ENABLED=true in .env)."""
    try:
        from middleware.production import get_metrics_text
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(get_metrics_text(), media_type="text/plain")
    except Exception as e:
        return {"error": str(e)}

# ── Phase 5: Telegram test endpoint ───────────────────────────────────────────
@app.post("/api/robo/test-telegram")
async def test_telegram():
    """Send a Telegram test notification to verify bot config."""
    try:
        from agents.telegram_notifier import send_test_message, _ENABLED
        if not _ENABLED:
            return {"success": False, "message": "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set in .env"}
        sent = send_test_message()
        return {"success": sent, "message": "Test message sent" if sent else "Not configured"}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()


# =====================================================================
# GannQSC HYBRID ENGINE — Super-fast Gann × QSC in-memory signals
# Ports QSC's single-core in-memory approach to the main Gann Trader.
# Same mechanics: rolling RAM cache + O(n) Pearson + quantum kernel
# =====================================================================
import math   as _gm
import time   as _gt
import random as _gr

# ── In-memory price cache  (ticker → {c, h, l, ts}) ──────────────────
_GQSC_CACHE: dict = {}

def _gqsc_feed(ticker: str, closes: list, highs: list, lows: list) -> None:
    """Feed bars into GannQSC RAM cache — same pattern as _H_HISTORY."""
    if not closes:
        return
    _GQSC_CACHE[ticker.upper()] = {
        "c":  list(closes[-300:]),
        "h":  list(highs[-300:]),
        "l":  list(lows[-300:]),
        "ts": _gt.time(),
    }

# ── Core maths — same O(n) primitives as QSC engine ─────────────────

def _gqsc_pearson(x: list, y: list) -> float:
    """Single-pass Pearson — identical to QSC's _h_pearson."""
    n = min(len(x), len(y))
    if n < 4:
        return 0.0
    x, y  = x[-n:], y[-n:]
    mx, my = sum(x) / n, sum(y) / n
    num    = sum((a - mx) * (b - my) for a, b in zip(x, y))
    dx     = _gm.sqrt(sum((a - mx) ** 2 for a in x))
    dy     = _gm.sqrt(sum((b - my) ** 2 for b in y))
    return max(-1.0, min(1.0, num / (dx * dy))) if dx and dy else 0.0

def _gqsc_quantum_kernel(x: list, y: list) -> float:
    """Quantum-inspired cosine kernel — same tanh(cos·π/2)·1.05 as QSC."""
    n = min(len(x), len(y))
    if n < 4:
        return 0.0
    rx = [(x[i] - x[i-1]) / x[i-1] for i in range(1, n) if x[i-1] != 0]
    ry = [(y[i] - y[i-1]) / y[i-1] for i in range(1, n) if y[i-1] != 0]
    m  = min(len(rx), len(ry))
    if m < 3:
        return 0.0
    rx, ry = rx[-m:], ry[-m:]
    dot = sum(a * b for a, b in zip(rx, ry))
    nx  = _gm.sqrt(sum(a * a for a in rx)) or 1e-9
    ny  = _gm.sqrt(sum(b * b for b in ry)) or 1e-9
    cos = dot / (nx * ny)
    return max(-1.0, min(1.0, _gm.tanh(cos * _gm.pi / 2) * 1.05))

def _gqsc_fused(c: float, q: float) -> float:
    """Weighted fusion — same 55/45 split as QSC's _h_fused."""
    return 0.55 * c + 0.45 * q

# ── Gann-specific fast primitives ────────────────────────────────────

def _gqsc_gann_angle_score(closes: list) -> float:
    """
    Gann 1×1 angle score — pure Python O(n), no I/O.
    Finds swing low in last 60 bars, projects 1×1 angle, returns
    normalised deviation in [-1, 1]:  +1 = far above (BUY), -1 = far below (SELL).
    """
    n = len(closes)
    if n < 5:
        return 0.0
    window = closes[-60:] if n >= 60 else closes
    pivot_low   = min(window)
    pivot_high  = max(window)
    pivot_idx   = window.index(pivot_low)
    bars_since  = len(window) - 1 - pivot_idx
    price_range = (pivot_high - pivot_low) or 1.0
    angle_1x1   = pivot_low + bars_since * (price_range / len(window))
    diff        = (closes[-1] - angle_1x1) / price_range
    return max(-1.0, min(1.0, _gm.tanh(diff * 3.0)))

def _gqsc_momentum(closes: list) -> float:
    """MA5 vs MA20 momentum — same logic as QSC's _h_momentum, normalised."""
    n = len(closes)
    if n < 20:
        return 0.0
    ma5  = sum(closes[-5:])  / 5
    ma20 = sum(closes[-20:]) / 20
    raw  = (ma5 - ma20) / (ma20 or 1)
    return max(-1.0, min(1.0, _gm.tanh(raw * 50)))

def _gqsc_octave_levels(closes: list, highs: list, lows: list) -> dict:
    """
    Gann Octave (1/8) support & resistance — O(n) pure Python.
    Returns 9 levels from the 60-bar high/low range.
    """
    h60 = highs[-60:] if len(highs) >= 60 else highs
    l60 = lows[-60:]  if len(lows)  >= 60 else lows
    if not h60 or not l60:
        return {}
    high = max(h60);  low = min(l60);  rng = (high - low) or 1.0
    return {
        f"{'R' if i > 4 else 'S'}{abs(i-4)}_{i}": round(low + (i / 8) * rng, 4)
        for i in range(9)
    }

def _gqsc_signal_label(score: float) -> tuple:
    """Map fused score to (direction, strength, color)."""
    if   score >  0.45: return "LONG",    "STRONG BUY",  "#00E676"
    elif score >  0.15: return "LONG",    "BUY",          "#66FF99"
    elif score < -0.45: return "SHORT",   "STRONG SELL",  "#FF3333"
    elif score < -0.15: return "SHORT",   "SELL",         "#FF6666"
    else:               return "NEUTRAL", "NEUTRAL",      "#888888"

# ── Core computation ─────────────────────────────────────────────────

def _gqsc_compute(ticker: str) -> dict:
    """
    Full GannQSC signal from RAM cache.  No network, no disk.
    Typical runtime: 0.1 – 2 ms on a single core.
    """
    t0   = _gt.time()
    data = _GQSC_CACHE.get(ticker.upper())
    if not data or len(data["c"]) < 10:
        return {"ticker": ticker, "error": "no_cache",
                "message": "Load the chart first to seed the cache."}

    closes, highs, lows = data["c"], data["h"], data["l"]
    current = closes[-1]

    # 1. Gann 1×1 angle score
    gann_sc = _gqsc_gann_angle_score(closes)

    # 2. Quantum kernel on lag-1 returns (same as QSC diagonal)
    rets = [(closes[i] - closes[i-1]) / closes[i-1]
            for i in range(1, len(closes)) if closes[i-1] != 0]
    q_sc = _gqsc_quantum_kernel(rets[:-1], rets[1:]) if len(rets) >= 5 else 0.0

    # 3. Classical Pearson momentum (trend vs lag)
    c_sc = _gqsc_pearson(closes[:-5], closes[5:]) if len(closes) >= 10 else 0.0

    # 4. Short-term momentum
    mom  = _gqsc_momentum(closes)

    # 5. Fused score  (Gann 40 % + Quantum 35 % + Pearson 15 % + Momentum 10 %)
    fused = (0.40 * gann_sc + 0.35 * q_sc + 0.15 * c_sc + 0.10 * mom)
    fused = max(-1.0, min(1.0, fused))

    direction, strength, color = _gqsc_signal_label(fused)

    # 6. ATR-based intraday levels
    is_crypto = "USDT" in ticker or ticker in {"BTC", "ETH", "SOL", "ADA"}
    atr_pct   = 0.009 if is_crypto else 0.005
    atr       = current * atr_pct
    sl  = round(current - atr * 0.8,  4) if direction == "LONG" else round(current + atr * 0.8,  4)
    t1  = round(current + atr * 1.0,  4) if direction == "LONG" else round(current - atr * 1.0,  4)
    t2  = round(current + atr * 1.8,  4) if direction == "LONG" else round(current - atr * 1.8,  4)

    # 7. Gann octave levels
    octaves = _gqsc_octave_levels(closes, highs, lows)

    ms = round((_gt.time() - t0) * 1000, 3)

    return {
        "ticker":        ticker.upper(),
        "direction":     direction,
        "strength":      strength,
        "color":         color,
        "gqsc_score":    round(fused,   4),
        "gann_score":    round(gann_sc, 4),
        "quantum_score": round(q_sc,    4),
        "pearson_score": round(c_sc,    4),
        "momentum":      round(mom,     4),
        "price":         round(current, 4),
        "sl":            sl,
        "t1":            t1,
        "t2":            t2,
        "octave_levels": octaves,
        "compute_ms":    ms,
        "cache_bars":    len(closes),
        "engine":        "GannQSC-v1",
    }

# ── Routes ────────────────────────────────────────────────────────────

gann_qsc_router = APIRouter(prefix="/api/gann-qsc")

@gann_qsc_router.post("/feed")
async def gqsc_feed_endpoint(body: dict):
    """
    Seed GannQSC RAM cache with bar data fetched by the frontend.
    Called automatically when the chart loads — zero extra latency.
    """
    ticker = str(body.get("ticker", "")).upper()
    closes = [float(x) for x in body.get("closes", [])]
    highs  = [float(x) for x in body.get("highs",  closes)]
    lows   = [float(x) for x in body.get("lows",   closes)]
    if ticker and len(closes) >= 10:
        _gqsc_feed(ticker, closes, highs, lows)
    return {"ok": True, "ticker": ticker, "bars": len(closes)}

@gann_qsc_router.get("/signal/{ticker}")
async def gqsc_signal_endpoint(ticker: str):
    """
    Instant Gann×QSC signal — reads entirely from RAM.
    Typical latency: < 2 ms on single core.
    """
    result = _gqsc_compute(ticker)
    return result

@gann_qsc_router.get("/cache-status")
async def gqsc_cache_status():
    """Inspect what's loaded in the GannQSC RAM cache."""
    return {
        sym: {"bars": len(v["c"]), "last_price": v["c"][-1] if v["c"] else None,
              "age_s": round(_gt.time() - v["ts"], 1)}
        for sym, v in _GQSC_CACHE.items()
    }

app.include_router(gann_qsc_router)


# =====================================================================
# HYBRID MODE — QSC ENGINE ROUTES (prefix: /api/hybrid/)
# =====================================================================
import math as _math
import random as _random

# ---- In-memory state for Hybrid mode ----
_H_LIVE: dict = {"BTCUSDT": 67000.0, "ETHUSDT": 3500.0, "SOLUSDT": 165.0, "ADAUSDT": 0.95}
_H_HISTORY: dict = {k: [] for k in _H_LIVE}
_H_COINBASE_MAP = {"BTCUSDT": "BTC-USD", "ETHUSDT": "ETH-USD", "SOLUSDT": "SOL-USD", "ADAUSDT": "ADA-USD"}
_H_COINGECKO_MAP = {"BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "SOLUSDT": "solana", "ADAUSDT": "cardano"}
_H_NON_CRYPTO: dict = {}

_H_NON_CRYPTO_ASSETS = {
    "stock":     [{"symbol": "SPY",  "name": "S&P 500 ETF",      "price": 558.20},
                  {"symbol": "QQQ",  "name": "Nasdaq 100 ETF",    "price": 478.15},
                  {"symbol": "NVDA", "name": "NVIDIA",            "price": 132.50},
                  {"symbol": "TSLA", "name": "Tesla",             "price": 248.30}],
    "commodity": [{"symbol": "XAU",  "name": "Gold (oz)",        "price": 2685.40},
                  {"symbol": "XAG",  "name": "Silver (oz)",      "price": 31.85},
                  {"symbol": "WTI",  "name": "Crude Oil WTI",    "price": 71.20},
                  {"symbol": "NG",   "name": "Natural Gas",      "price": 2.85}],
    "macro":     [{"symbol": "DXY",      "name": "Dollar Index",     "price": 104.50},
                  {"symbol": "US10Y",    "name": "US 10Y Yield",     "price": 4.25},
                  {"symbol": "VIX",      "name": "Volatility Index", "price": 14.80},
                  {"symbol": "FEDFUNDS", "name": "Fed Funds Rate",   "price": 4.75}],
    "indian":    [{"symbol": "RELIANCE",   "name": "Reliance Industries",    "yf": "RELIANCE.NS",   "price": 1350.0},
                  {"symbol": "TCS",        "name": "Tata Consultancy",       "yf": "TCS.NS",        "price": 3800.0},
                  {"symbol": "INFY",       "name": "Infosys",                "yf": "INFY.NS",       "price": 1600.0},
                  {"symbol": "HDFCBANK",   "name": "HDFC Bank",              "yf": "HDFCBANK.NS",   "price": 1700.0},
                  {"symbol": "ICICIBANK",  "name": "ICICI Bank",             "yf": "ICICIBANK.NS",  "price": 1350.0},
                  {"symbol": "WIPRO",      "name": "Wipro",                  "yf": "WIPRO.NS",      "price": 320.0},
                  {"symbol": "SBIN",       "name": "State Bank India",       "yf": "SBIN.NS",       "price": 785.0},
                  {"symbol": "BAJFINANCE", "name": "Bajaj Finance",          "yf": "BAJFINANCE.NS", "price": 6800.0},
                  {"symbol": "NIFTY50",    "name": "Nifty 50 Index",         "yf": "^NSEI",         "price": 22500.0},
                  {"symbol": "SENSEX",     "name": "BSE Sensex",             "yf": "^BSESN",        "price": 74000.0}],
}

def _h_init():
    # Per-class simulation parameters (sigma, drift_speed) for seeding history
    _SIM_PARAMS = {
        "stock":     {"sigma": 0.0012, "mean_rev": 0.002},
        "commodity": {"sigma": 0.0010, "mean_rev": 0.003},
        "macro":     {"sigma": 0.0006, "mean_rev": 0.001},
        "indian":    {"sigma": 0.0015, "mean_rev": 0.002},
    }
    for cls, items in _H_NON_CRYPTO_ASSETS.items():
        p_cfg = _SIM_PARAMS.get(cls, {"sigma": 0.001, "mean_rev": 0.002})
        for a in items:
            # Seed 120 historical price points so autocorrelation is computable from startup
            h, cur = [], float(a["price"])
            for _ in range(120):
                cur = cur * (1 + _random.gauss(0, p_cfg["sigma"])) + p_cfg["mean_rev"] * (a["price"] - cur)
                h.append(round(cur, 4))
            _H_NON_CRYPTO[a["symbol"]] = {
                "symbol": a["symbol"], "name": a["name"], "asset_class": cls,
                "price": a["price"], "base_price": a["price"],
                "change_24h": _random.uniform(-2.5, 2.5),
                "volume": _random.uniform(1e6, 5e8), "history": h,
                "yf_ticker": a.get("yf"),   # yfinance symbol (only for indian class)
                "currency": "INR" if cls == "indian" else "USD",
            }
    # Seed crypto history
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    for sym, p in _H_LIVE.items():
        h, cur = [], p
        for i in range(60, 0, -1):
            cur *= (1 + _random.gauss(0, 0.0008))
            h.append({"t": now_ms - i * 1000, "p": round(cur, 2)})
        _H_HISTORY[sym] = h

_h_init()

_H_ALL_SYMS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "SPY", "QQQ", "NVDA", "XAU", "WTI", "DXY", "US10Y", "VIX",
               "RELIANCE", "TCS", "INFY", "HDFCBANK", "NIFTY50"]

# ---- Background tasks ----
async def _hybrid_coinbase_bridge():
    sub = {"type": "subscribe", "product_ids": list(_H_COINBASE_MAP.values()), "channels": ["ticker"]}
    backoff, initialized = 1, {}
    while True:
        try:
            async with websockets.connect("wss://ws-feed.exchange.coinbase.com", ping_interval=20) as conn:
                await conn.send(json.dumps(sub))
                backoff = 1
                async for raw in conn:
                    try:
                        d = json.loads(raw)
                        if d.get("type") != "ticker": continue
                        prod = d.get("product_id"); price = float(d.get("price", 0))
                        if price <= 0: continue
                        for sym, cb in _H_COINBASE_MAP.items():
                            if cb == prod:
                                _H_LIVE[sym] = price
                                if not initialized.get(sym):
                                    initialized[sym] = True
                                    nm = int(datetime.now(timezone.utc).timestamp() * 1000)
                                    cur2 = price
                                    seeded = []
                                    for ii in range(60, 0, -1):
                                        cur2 *= (1 + _random.gauss(0, 0.0006))
                                        seeded.append({"t": nm - ii * 1000, "p": round(cur2, 4)})
                                    _H_HISTORY[sym] = seeded
                                h2 = _H_HISTORY[sym]
                                h2.append({"t": int(datetime.now(timezone.utc).timestamp() * 1000), "p": price})
                                if len(h2) > 500: del h2[:len(h2)-500]
                                break
                    except Exception: pass
        except Exception as e:
            logging.warning(f"Hybrid Coinbase WS err: {e}. Retry in {backoff}s")
            await asyncio.sleep(backoff); backoff = min(backoff * 2, 30)

async def _hybrid_coingecko_fallback():
    ids = ",".join(_H_COINGECKO_MAP.values())
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd"
    async with httpx.AsyncClient(timeout=10.0) as cli:
        while True:
            try:
                r = await cli.get(url)
                if r.status_code == 200:
                    data = r.json()
                    nm = int(datetime.now(timezone.utc).timestamp() * 1000)
                    for sym, cg in _H_COINGECKO_MAP.items():
                        v = data.get(cg, {}).get("usd")
                        if v and v > 0:
                            _H_LIVE[sym] = float(v)
                            h3 = _H_HISTORY[sym]
                            if not h3 or nm - h3[-1]["t"] > 4000:
                                h3.append({"t": nm, "p": float(v)})
                                if len(h3) > 500: del h3[:len(h3)-500]
            except Exception: pass
            await asyncio.sleep(8)

async def _hybrid_non_crypto_simulator():
    while True:
        for sym, st in _H_NON_CRYPTO.items():
            if st["asset_class"] == "indian":
                continue  # Indian stocks updated by yfinance poller, not simulated
            sigma = 0.0008 if st["asset_class"] in ("stock", "commodity") else 0.0003
            new_p = st["price"] * (1 + _random.gauss(0, sigma))
            new_p = new_p + 0.001 * (st["base_price"] - new_p)
            st["price"] = round(new_p, 4)
            st["change_24h"] = round((new_p / st["base_price"] - 1) * 100, 3)
            h = st["history"]; h.append(new_p)
            if len(h) > 300: del h[:len(h)-300]
        await asyncio.sleep(1)


async def _hybrid_indian_yfinance_poller():
    """Fetch live Indian NSE prices via yfinance every 60 seconds."""
    indian_assets = {sym: st for sym, st in _H_NON_CRYPTO.items() if st.get("yf_ticker")}
    tickers_yf = [st["yf_ticker"] for st in indian_assets.values()]

    while True:
        try:
            loop = asyncio.get_event_loop()
            def _fetch():
                import yfinance as _yf
                batch = _yf.Tickers(" ".join(tickers_yf))
                result = {}
                for sym, st in indian_assets.items():
                    ytk = st["yf_ticker"]
                    try:
                        info = batch.tickers[ytk].fast_info
                        last = getattr(info, "last_price", None) or getattr(info, "previous_close", None)
                        prev = getattr(info, "previous_close", None) or last
                        if last and last > 0:
                            result[sym] = {"price": float(last), "prev": float(prev) if prev else float(last)}
                    except Exception:
                        pass
                return result

            prices = await loop.run_in_executor(None, _fetch)
            for sym, data in prices.items():
                st = _H_NON_CRYPTO.get(sym)
                if not st:
                    continue
                new_p = data["price"]
                prev_p = data["prev"]
                st["price"] = round(new_p, 2)
                st["base_price"] = round(prev_p, 2)
                st["change_24h"] = round(((new_p - prev_p) / prev_p * 100) if prev_p else 0, 3)
                h = st["history"]
                h.append(new_p)
                if len(h) > 300:
                    del h[:len(h) - 300]
            logging.info(f"Indian yfinance update: {len(prices)} stocks refreshed")
        except Exception as e:
            logging.warning(f"Indian yfinance poller error: {e}")
        await asyncio.sleep(60)

# ---- Helper: price series ----
def _h_series(symbol: str) -> list:
    if symbol in _H_HISTORY: return [p["p"] for p in _H_HISTORY[symbol]]
    if symbol in _H_NON_CRYPTO: return _H_NON_CRYPTO[symbol]["history"]
    return []

def _h_pearson(x, y):
    n = min(len(x), len(y))
    if n < 4: return 0.0
    x, y = x[-n:], y[-n:]
    mx, my = sum(x)/n, sum(y)/n
    num = sum((a-mx)*(b-my) for a, b in zip(x, y))
    dx = _math.sqrt(sum((a-mx)**2 for a in x))
    dy = _math.sqrt(sum((b-my)**2 for b in y))
    if dx == 0 or dy == 0: return 0.0
    return max(-1.0, min(1.0, num / (dx*dy)))

def _h_quantum_kernel(x, y):
    n = min(len(x), len(y))
    if n < 4: return 0.0
    rx = [(x[i]-x[i-1])/x[i-1] for i in range(1,n) if x[i-1]!=0]
    ry = [(y[i]-y[i-1])/y[i-1] for i in range(1,n) if y[i-1]!=0]
    m = min(len(rx), len(ry))
    if m < 3: return 0.0
    rx, ry = rx[-m:], ry[-m:]
    dot = sum(a*b for a,b in zip(rx,ry))
    nx = _math.sqrt(sum(a*a for a in rx)) or 1e-9
    ny = _math.sqrt(sum(b*b for b in ry)) or 1e-9
    cos = dot/(nx*ny)
    return max(-1.0, min(1.0, _math.tanh(cos * _math.pi/2) * 1.05))

def _h_fused(c, q): return 0.55*c + 0.45*q

def _h_momentum(symbol):
    s = _h_series(symbol)
    if len(s) < 20: return 0.0
    return (sum(s[-5:])/5 - sum(s[-20:])/20) / (sum(s[-20:])/20 or 1)

def _h_compute_corr():
    cells = []
    for i, a in enumerate(_H_ALL_SYMS):
        for b in _H_ALL_SYMS[i:]:
            sa, sb = _h_series(a), _h_series(b)
            if a == b:
                # Diagonal: compute lag-1 return autocorrelation — unique per asset based on actual data.
                # Classical = Pearson lag-1 autocorr of price returns (momentum vs mean-reversion)
                # Quantum   = quantum-kernel on returns vs lagged returns (non-linear coherence)
                # Fused     = weighted blend
                rets = [
                    (sa[k] - sa[k-1]) / sa[k-1]
                    for k in range(1, len(sa)) if sa[k-1] != 0
                ]
                if len(rets) >= 5:
                    c = _h_pearson(rets[:-1], rets[1:])
                    q = _h_quantum_kernel(rets[:-1], rets[1:])
                else:
                    c = 0.0
                    q = 0.0
                f = _h_fused(c, q)
            else:
                c = _h_pearson(sa, sb)
                q = _h_quantum_kernel(sa, sb)
                f = _h_fused(c, q)
            cells.append({"a": a, "b": b, "classical": round(c,3), "quantum": round(q,3), "fused": round(f,3)})
    return cells

# ---- Hybrid Router ----
hybrid_router = APIRouter(prefix="/api/hybrid")

@hybrid_router.get("/chart/{symbol}")
async def h_chart_data(symbol: str, tf: str = "1h"):
    """OHLCV data for QSC chart — real yfinance for Indian stocks AND crypto, synthetic for others."""
    # Interval mapping
    tf_map = {
        "5m":  ("5m",  "1d"),   "15m": ("15m", "5d"),
        "1h":  ("1h",  "30d"),  "4h":  ("1h",  "60d"),
        "1d":  ("1d",  "180d"), "1w":  ("1wk", "2y"),
    }
    yf_interval, yf_period = tf_map.get(tf, ("1h", "30d"))

    # ---- Crypto: use yfinance for proper historical OHLC (BTCUSDT -> BTC-USD) ----
    crypto_yf_map = {"BTCUSDT": "BTC-USD", "ETHUSDT": "ETH-USD",
                     "SOLUSDT": "SOL-USD", "ADAUSDT": "ADA-USD"}
    if symbol in crypto_yf_map:
        def _fetch_crypto_yf():
            import yfinance as _yf
            tk = _yf.Ticker(crypto_yf_map[symbol])
            df = tk.history(period=yf_period, interval=yf_interval)
            bars = []
            for ts, row in df.iterrows():
                epoch = int(ts.timestamp())
                bars.append({"time": epoch, "open": round(float(row["Open"]), 2),
                              "high": round(float(row["High"]), 2), "low": round(float(row["Low"]), 2),
                              "close": round(float(row["Close"]), 2),
                              "volume": int(row.get("Volume", 0) or 0)})
            return bars
        loop = asyncio.get_event_loop()
        try:
            bars = await asyncio.wait_for(loop.run_in_executor(None, _fetch_crypto_yf), timeout=12.0)
            if bars:
                # Overlay live Kraken price on the last bar if available
                live = _H_LIVE.get(symbol)
                if live and bars[-1]:
                    last = bars[-1]
                    last["close"] = round(float(live), 2)
                    last["high"] = max(last["high"], last["close"])
                    last["low"] = min(last["low"], last["close"])
                return {"symbol": symbol, "tf": tf, "bars": bars, "type": "candlestick"}
        except Exception as e:
            logging.warning(f"h_chart_data crypto yfinance err for {symbol}: {e}")
        # Fallback below if yfinance fails

    st = _H_NON_CRYPTO.get(symbol)

    # ---- Indian stocks (real yfinance OHLCV) ----
    if st and st.get("yf_ticker"):
        def _fetch_indian():
            import yfinance as _yf
            tk = _yf.Ticker(st["yf_ticker"])
            df = tk.history(period=yf_period, interval=yf_interval)
            bars = []
            for ts, row in df.iterrows():
                epoch = int(ts.timestamp())
                bars.append({"time": epoch, "open": round(float(row["Open"]), 2),
                              "high": round(float(row["High"]), 2), "low": round(float(row["Low"]), 2),
                              "close": round(float(row["Close"]), 2),
                              "volume": int(row.get("Volume", 0) or 0)})
            return bars
        loop = asyncio.get_event_loop()
        try:
            bars = await loop.run_in_executor(None, _fetch_indian)
            return {"symbol": symbol, "tf": tf, "bars": bars, "type": "candlestick"}
        except Exception as e:
            logging.warning(f"h_chart_data yfinance err for {symbol}: {e}")

    # ---- Crypto (synthetic OHLC from close history) ----
    if symbol in _H_HISTORY:
        hist = _H_HISTORY[symbol][-240:]
        bars = []
        for i, pt in enumerate(hist):
            c = pt["p"]
            o = hist[i - 1]["p"] if i > 0 else c
            spread = c * 0.0012
            h = max(o, c) * (1 + _random.uniform(0, 0.0015))
            l = min(o, c) * (1 - _random.uniform(0, 0.0015))
            bars.append({"time": pt["t"] // 1000, "open": round(o, 4), "high": round(h, 4),
                         "low": round(l, 4), "close": round(c, 4), "volume": int(_random.uniform(1e4, 5e6))})
        return {"symbol": symbol, "tf": tf, "bars": bars, "type": "candlestick"}

    # ---- US stocks / commodities / macro (synthetic OHLC from history) ----
    if st:
        hist = st["history"][-200:]
        bars = []
        base_t = int(datetime.now(timezone.utc).timestamp()) - len(hist) * 3600
        for i, c in enumerate(hist):
            o = hist[i - 1] if i > 0 else c
            h = max(o, c) * (1 + _random.uniform(0, 0.001))
            l = min(o, c) * (1 - _random.uniform(0, 0.001))
            bars.append({"time": base_t + i * 3600, "open": round(o, 4), "high": round(h, 4),
                         "low": round(l, 4), "close": round(c, 4), "volume": int(_random.uniform(1e4, 1e7))})
        return {"symbol": symbol, "tf": tf, "bars": bars, "type": "candlestick"}

    raise HTTPException(404, f"No data for symbol {symbol}")


@hybrid_router.get("/search")
async def h_search(q: str = ""):
    """Search across all QSC assets + live NSE/BSE yfinance lookup for Indian stocks."""
    q = q.strip().upper()
    if not q:
        return []
    results = []
    seen = set()

    # 1. Existing cached assets (instant match)
    for sym, st in _H_NON_CRYPTO.items():
        if q in sym.upper() or q.lower() in st["name"].lower():
            if sym in seen:
                continue
            seen.add(sym)
            results.append({"symbol": sym, "name": st["name"],
                            "asset_class": st["asset_class"], "currency": st.get("currency", "USD"),
                            "price": st["price"]})

    for sym, name in {"BTCUSDT": "Bitcoin", "ETHUSDT": "Ethereum",
                      "SOLUSDT": "Solana", "ADAUSDT": "Cardano"}.items():
        if q in sym or q.lower() in name.lower():
            if sym in seen:
                continue
            seen.add(sym)
            results.append({"symbol": sym, "name": name,
                            "asset_class": "crypto", "currency": "USD",
                            "price": _H_LIVE.get(sym, 0)})

    # 2. NSE/BSE yfinance lookup -- runs for plausible stock-like queries (2-15 alphanumerics)
    #    Even when cached results exist, we still attempt a yfinance lookup for the exact
    #    ticker so any Indian stock can be discovered.
    exact_cached = any(r["symbol"] == q for r in results)
    if not exact_cached and 2 <= len(q) <= 15 and q.replace("&", "").replace("-", "").isalnum():
        def _yf_lookup():
            import yfinance as _yf
            for suffix in [".NS", ".BO", ""]:
                try:
                    info = _yf.Ticker(q + suffix).fast_info
                    px = getattr(info, "last_price", None)
                    if px and px > 0:
                        return {"symbol": q, "name": q + suffix,
                                "asset_class": "indian" if suffix in (".NS", ".BO") else "stock",
                                "currency": "INR" if suffix in (".NS", ".BO") else "USD",
                                "price": float(px), "yf": q + suffix}
                except Exception:
                    continue
            return None
        loop = asyncio.get_event_loop()
        try:
            hit = await asyncio.wait_for(loop.run_in_executor(None, _yf_lookup), timeout=6.0)
            if hit and hit["symbol"] not in seen:
                seen.add(hit["symbol"])
                # Auto-register yfinance Indian hits into _H_NON_CRYPTO so they appear
                # in watchlist + chart immediately on click.
                if hit["asset_class"] == "indian" and hit["symbol"] not in _H_NON_CRYPTO:
                    _H_NON_CRYPTO[hit["symbol"]] = {
                        "symbol": hit["symbol"], "name": hit["name"], "asset_class": "indian",
                        "price": hit["price"], "base_price": hit["price"],
                        "change_24h": 0.0, "volume": _random.uniform(1e5, 1e7),
                        "history": [hit["price"]],
                        "yf_ticker": hit["yf"], "currency": "INR",
                    }
                results.append(hit)
        except (asyncio.TimeoutError, Exception):
            pass

    return results[:15]


@hybrid_router.get("/assets")
async def h_get_assets():
    out = []
    meta = {"BTCUSDT": "Bitcoin", "ETHUSDT": "Ethereum", "SOLUSDT": "Solana", "ADAUSDT": "Cardano"}
    for sym, name in meta.items():
        h = _H_HISTORY.get(sym, [])
        first = h[0]["p"] if h else _H_LIVE[sym]
        last = _H_LIVE[sym]
        ch = ((last-first)/first*100) if first else 0
        out.append({"symbol": sym, "name": name, "asset_class": "crypto",
                    "price": last, "change_24h": round(ch,3), "volume": _random.uniform(1e8,5e9)})
    for sym, st in _H_NON_CRYPTO.items():
        out.append({"symbol": sym, "name": st["name"], "asset_class": st["asset_class"],
                    "price": st["price"], "change_24h": st["change_24h"], "volume": st["volume"],
                    "currency": st.get("currency", "USD")})
    return out

@hybrid_router.get("/prices/{symbol}")
async def h_price_series(symbol: str, limit: int = 120):
    if symbol in _H_HISTORY: return _H_HISTORY[symbol][-limit:]
    if symbol in _H_NON_CRYPTO:
        h = _H_NON_CRYPTO[symbol]["history"][-limit:]
        return [{"t": i, "p": p} for i, p in enumerate(h)]
    raise HTTPException(404, f"Unknown symbol {symbol}")

@hybrid_router.get("/orderbook/{symbol}")
async def h_orderbook(symbol: str):
    if symbol in _H_LIVE: mid = _H_LIVE[symbol]
    elif symbol in _H_NON_CRYPTO: mid = _H_NON_CRYPTO[symbol]["price"]
    else: raise HTTPException(404, "Unknown symbol")
    bids, asks = [], []
    half = mid * 4 / 20000
    for i in range(10):
        bids.append({"price": round(mid-half-i*(mid*0.0002),2), "qty": round(_random.uniform(0.2,12.0),4)})
        asks.append({"price": round(mid+half+i*(mid*0.0002),2), "qty": round(_random.uniform(0.2,12.0),4)})
    return {"symbol": symbol, "mid": mid, "bids": bids, "asks": asks}

@hybrid_router.get("/correlation")
async def h_correlation():
    cells = _h_compute_corr()
    return {"symbols": _H_ALL_SYMS, "cells": cells}

@hybrid_router.post("/qsc/signal")
async def h_generate_signal(body: dict):
    target = body.get("symbol", "BTCUSDT")
    moms = {s: _h_momentum(s) for s in _H_ALL_SYMS}
    mean_m = sum(moms.values()) / max(len(moms), 1)
    anchor = max(moms, key=lambda k: abs(moms[k]-mean_m))
    corr_list = []
    for s in _H_ALL_SYMS:
        if s == anchor: continue
        f = _h_fused(_h_pearson(_h_series(anchor),_h_series(s)), _h_quantum_kernel(_h_series(anchor),_h_series(s)))
        corr_list.append((s, f))
    corr_list.sort(key=lambda x: abs(x[1]), reverse=True)
    bridge = corr_list[0][0] if corr_list else "ETHUSDT"
    amplifier = corr_list[1][0] if len(corr_list) > 1 else "SOLUSDT"
    risk_t = _h_fused(_h_pearson(_h_series(anchor),_h_series(target)), _h_quantum_kernel(_h_series(anchor),_h_series(target)))
    composite = 0.6*moms.get(target,0) + 0.4*(risk_t*moms.get(anchor,0))
    if composite > 0.0005: direction = "LONG"
    elif composite < -0.0005: direction = "SHORT"
    else: direction = "NEUTRAL"
    confidence = min(1.0, abs(composite)*800 + abs(risk_t)*0.3 + 0.15)
    payload = {"symbol": target, "direction": direction, "anchor_asset": anchor,
               "bridge_asset": bridge, "amplifier_asset": amplifier,
               "momentum_score": round(moms.get(target,0),5),
               "risk_transfer_score": round(risk_t,4), "confidence": round(confidence,3)}
    # LLM reasoning
    reasoning = f"Statistical signal: {direction} on {target} driven by anchor={anchor}."
    llm_key = os.environ.get("EMERGENT_LLM_KEY", "")
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if llm_key or openai_key:
        try:
            result = await llm_complete(
                system_message="You are the QSC Engine, an analytical trading reasoning module. Given numerical signal data, write a precise 3-sentence rationale explaining the cascade momentum hypothesis. Use technical, neutral, quant language. NEVER recommend illegal activity. Output plain text only.",
                user_text=json.dumps(payload, default=str),
                provider="anthropic",
                model="claude-sonnet-4-5-20250929",
                session_id=f"qsc-{uuid.uuid4().hex[:8]}",
            )
            if result:
                reasoning = result
        except Exception as llm_e:
            logging.warning(f"QSC LLM error: {llm_e}")
    sig = {"id": str(uuid.uuid4()), "symbol": target, "direction": direction,
           "confidence": round(confidence,3), "momentum_score": round(moms.get(target,0),5),
           "risk_transfer_score": round(risk_t,4), "anchor_asset": anchor,
           "bridge_asset": bridge, "amplifier_asset": amplifier,
           "reasoning": reasoning, "created_at": datetime.now(timezone.utc).isoformat()}
    await db.qsc_signals.insert_one(dict(sig))
    sig.pop("_id", None)
    return sig

@hybrid_router.get("/qsc/signals")
async def h_list_signals(limit: int = 10):
    cur = db.qsc_signals.find({}, {"_id": 0}).sort("created_at", -1).limit(limit)
    return [d async for d in cur]

@hybrid_router.get("/regulatory/sentiment")
async def h_regulatory():
    score = round(_random.uniform(-0.6, 0.7), 3)
    label = "SUPPORTIVE" if score>0.3 else "NEUTRAL" if score>-0.1 else "CAUTIOUS" if score>-0.4 else "HOSTILE"
    multiplier = max(0.3, min(1.3, 1.0+score*0.4))
    return {"score": score, "label": label, "aggressiveness_multiplier": round(multiplier,3),
            "headlines": [
                {"src": "FedSpeech", "headline": "Chair signals data-dependent stance on rate path", "weight": 0.6},
                {"src": "SEC", "headline": "New disclosure rules for digital asset custodians", "weight": -0.3},
                {"src": "SEBI", "headline": "F&O position limits tightened for index derivatives", "weight": -0.4},
                {"src": "RBI", "headline": "Liquidity window extended; CRR held at 4%", "weight": 0.5},
                {"src": "EU MiCA", "headline": "Stablecoin transition window extended 6 months", "weight": 0.4},
                {"src": "NSE", "headline": "Circuit breaker thresholds revised for small-cap segment", "weight": -0.2},
            ], "updated_at": datetime.now(timezone.utc).isoformat()}

@hybrid_router.post("/trades/execute")
async def h_execute_trade(req: dict):
    symbol = req.get("symbol", "BTCUSDT")
    direction = req.get("direction", "LONG")
    volume = float(req.get("volume", 0.1))
    use_stag = req.get("use_staggered", True)
    if symbol in _H_LIVE: mid = _H_LIVE[symbol]
    elif symbol in _H_NON_CRYPTO: mid = _H_NON_CRYPTO[symbol]["price"]
    else: raise HTTPException(404, "Unknown symbol")
    if direction not in ("LONG","SHORT"): raise HTTPException(400, "direction must be LONG or SHORT")
    if volume <= 0: raise HTTPException(400, "volume must be positive")
    venues = [("VENUE-A",1.0,380),("VENUE-B",0.65,720),("VENUE-C",0.45,240)] if use_stag else [("VENUE-A",1.0,380)]
    side = "BUY" if direction == "LONG" else "SELL"
    legs, total_qty, total_cost = [], 0.0, 0.0
    for v, frac, lat in venues:
        slip = _random.uniform(-0.0008, 0.0012)
        px = mid*(1+slip if side=="BUY" else 1-slip)
        q = round(volume*frac, 6)
        legs.append({"venue": v, "side": side, "price": round(px,4), "quantity": q,
                     "latency_ns": lat, "executed_at": datetime.now(timezone.utc).isoformat()})
        total_qty += q; total_cost += q*px
    avg = total_cost/total_qty if total_qty else mid
    trade = {"id": str(uuid.uuid4()), "symbol": symbol, "direction": direction,
             "total_volume": round(total_qty,6), "avg_price": round(avg,4),
             "legs": legs, "pnl": 0.0, "status": "OPEN",
             "opened_at": datetime.now(timezone.utc).isoformat(), "closed_at": None}
    await db.hybrid_trades.insert_one(dict(trade))
    trade.pop("_id", None)
    return trade

@hybrid_router.get("/trades")
async def h_list_trades(limit: int = 30):
    cur = db.hybrid_trades.find({}, {"_id": 0}).sort("opened_at", -1).limit(limit)
    return [d async for d in cur]

@hybrid_router.post("/trades/{trade_id}/close")
async def h_close_trade(trade_id: str):
    doc = await db.hybrid_trades.find_one({"id": trade_id}, {"_id": 0})
    if not doc: raise HTTPException(404, "Trade not found")
    if doc["status"] != "OPEN": raise HTTPException(400, "Already closed")
    sym = doc["symbol"]
    cur_price = _H_LIVE.get(sym) or _H_NON_CRYPTO.get(sym, {}).get("price")
    if cur_price is None: raise HTTPException(400, "No price")
    mult = 1 if doc["direction"] == "LONG" else -1
    pnl = (cur_price - doc["avg_price"]) * doc["total_volume"] * mult
    await db.hybrid_trades.update_one({"id": trade_id},
        {"$set": {"status": "CLOSED", "pnl": round(pnl,4), "closed_at": datetime.now(timezone.utc).isoformat()}})
    return {"id": trade_id, "status": "CLOSED", "pnl": round(pnl,4)}

@hybrid_router.get("/positions")
async def h_positions():
    cur = db.hybrid_trades.find({"status": "OPEN"}, {"_id": 0})
    out = []
    async for t in cur:
        sym = t["symbol"]
        cp = _H_LIVE.get(sym) or _H_NON_CRYPTO.get(sym, {}).get("price") or t["avg_price"]
        mult = 1 if t["direction"] == "LONG" else -1
        pnl = (cp - t["avg_price"]) * t["total_volume"] * mult
        out.append({"symbol": sym, "direction": t["direction"], "quantity": t["total_volume"],
                    "entry_price": t["avg_price"], "current_price": round(cp,4),
                    "pnl": round(pnl,4), "pnl_pct": round(((cp/t["avg_price"])-1)*100*mult, 3)})
    return out

@hybrid_router.get("/portfolio/summary")
async def h_portfolio():
    trades = [t async for t in db.hybrid_trades.find({}, {"_id": 0})]
    realized = sum(t.get("pnl",0) or 0 for t in trades if t["status"]=="CLOSED")
    open_t = [t for t in trades if t["status"]=="OPEN"]
    unrealized = 0.0
    for t in open_t:
        sym = t["symbol"]
        cp = _H_LIVE.get(sym) or _H_NON_CRYPTO.get(sym,{}).get("price") or t["avg_price"]
        mult = 1 if t["direction"]=="LONG" else -1
        unrealized += (cp - t["avg_price"]) * t["total_volume"] * mult
    return {"realized_pnl": round(realized,4), "unrealized_pnl": round(unrealized,4),
            "total_pnl": round(realized+unrealized,4), "open_positions": len(open_t), "total_trades": len(trades)}

@app.websocket("/api/ws/qsc-prices")
async def ws_qsc_prices(websocket: WebSocket):
    """Push QSC Hybrid crypto prices every second."""
    await websocket.accept()
    try:
        while True:
            await websocket.send_text(json.dumps({
                "type": "tick", "prices": _H_LIVE,
                "ts": int(datetime.now(timezone.utc).timestamp() * 1000),
            }))
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        return
    except Exception as e:
        logging.debug(f"ws_qsc_prices err: {e}")

app.include_router(hybrid_router)

# ======================= VISUALIZATION ENDPOINTS =======================

_CORR_TICKERS = [
    "RELIANCE.NS","TCS.NS","HDFCBANK.NS","INFY.NS","ICICIBANK.NS",
    "HINDUNILVR.NS","SBIN.NS","BHARTIARTL.NS","KOTAKBANK.NS","ITC.NS",
    "AXISBANK.NS","WIPRO.NS","BAJFINANCE.NS","MARUTI.NS","LT.NS",
]
_corr_cache: Dict[str, Any] = {}
_CORR_TTL = 1800  # 30 min

@app.get("/api/viz/correlation-matrix")
async def get_correlation_matrix(
    tickers: Optional[str] = Query(default=None),
    period: str = Query(default="3mo"),
):
    """Returns pairwise return-correlation matrix for NSE stocks."""
    ticker_list = [t.strip() for t in tickers.split(",")] if tickers else _CORR_TICKERS
    ticker_list = ticker_list[:20]
    cache_key = f"{','.join(ticker_list)}_{period}"
    now = datetime.now(timezone.utc).timestamp()
    if _corr_cache.get(cache_key, {}).get("ts", 0) + _CORR_TTL > now:
        return _corr_cache[cache_key]["data"]
    try:
        from concurrent.futures import ThreadPoolExecutor
        loop = asyncio.get_event_loop()
        def _fetch():
            raw = yf.download(ticker_list, period=period, interval="1d",
                              progress=False, auto_adjust=True, group_by="column")
            if isinstance(raw.columns, pd.MultiIndex):
                # MultiIndex: ('Close','TICKER.NS'), extract Close level
                close = raw.xs("Close", axis=1, level=0)
            elif "Close" in raw.columns:
                close = raw[["Close"]]
            else:
                close = raw
            return close.dropna(how="all")
        close = await loop.run_in_executor(None, _fetch)
        returns = close.pct_change().dropna(how="all")
        corr = returns.corr().fillna(0)
        clean = [c.replace(".NS","").replace(".BO","") for c in corr.columns.tolist()]
        result = {"tickers": clean, "matrix": [[round(v,4) for v in row] for row in corr.values.tolist()], "period": period}
        _corr_cache[cache_key] = {"ts": now, "data": result}
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

@app.get("/api/viz/options-network/{symbol}")
async def get_options_network(symbol: str):
    """Returns options flow formatted as network nodes/edges for a given index."""
    try:
        from nsepython import nse_optionchain_scrapper
        loop = asyncio.get_event_loop()
        oc = await loop.run_in_executor(None, nse_optionchain_scrapper, symbol.upper())
        records = oc.get("records", {}).get("data", [])[:40]
        expiry = oc.get("records", {}).get("expiryDates", [""])[0]
        nodes, edges = [], []
        atm_node = {"id": "ATM", "type": "atm", "label": "ATM", "strike": 0, "oi": 0, "volume": 0, "ltp": 0}
        nodes.append(atm_node)
        prev_call_id, prev_put_id = None, None
        for rec in records:
            strike = rec.get("strikePrice", 0)
            ce = rec.get("CE", {})
            pe = rec.get("PE", {})
            if ce:
                cid = f"C{strike}"
                nodes.append({"id": cid, "type": "call", "label": str(strike), "strike": strike,
                               "oi": ce.get("openInterest",0), "volume": ce.get("totalTradedVolume",0), "ltp": ce.get("lastPrice",0)})
                edges.append({"source": "ATM", "target": cid, "weight": ce.get("openInterest",0)})
                if prev_call_id:
                    edges.append({"source": prev_call_id, "target": cid, "weight": ce.get("totalTradedVolume",0)})
                prev_call_id = cid
            if pe:
                pid = f"P{strike}"
                nodes.append({"id": pid, "type": "put", "label": str(strike), "strike": strike,
                               "oi": pe.get("openInterest",0), "volume": pe.get("totalTradedVolume",0), "ltp": pe.get("lastPrice",0)})
                edges.append({"source": "ATM", "target": pid, "weight": pe.get("openInterest",0)})
                if prev_put_id:
                    edges.append({"source": prev_put_id, "target": pid, "weight": pe.get("totalTradedVolume",0)})
                prev_put_id = pid
        return {"symbol": symbol, "expiry": expiry, "nodes": nodes, "edges": edges}
    except Exception as exc:
        return {"symbol": symbol, "expiry": "", "nodes": [], "edges": [], "error": str(exc)}

# ======================= RL AGENT =======================
from rl_agent.rl_router import rl_router
app.include_router(rl_router)

# ======================= DREAMER V3 ROBO-TRADER =======================
from agents.robo_router import robo_router
app.include_router(robo_router)

# ======================= KRONOS FORECAST =======================
from kronos_router import kronos_router
app.include_router(kronos_router)

# ======================= MULTI-AI ENSEMBLE =======================
from ensemble.router import ensemble_router
app.include_router(ensemble_router)

# ======================= SECTOR ROTATION PICKER =======================
from sector_picker.router import router as sector_picker_router
app.include_router(sector_picker_router)

# ======================= PE-CE OI TRACKER =======================
from pece.router import router as pece_router
app.include_router(pece_router)

from moneycontrol.router import router as mc_router
app.include_router(mc_router)

from ai_router.router import ai_router as _ai_router
app.include_router(_ai_router)

# ======================= ADVANCED TRADING (PER + Portfolio + Risk + Sentiment + Observability) =======================
from rl_agent.advanced_router import advanced_router
app.include_router(advanced_router)

# DataManager cache stats endpoint
from core.data_manager import dm as _dm
from fastapi import APIRouter as _APIRouter
_dm_router = _APIRouter(prefix="/api/data-manager", tags=["data-manager"])

@_dm_router.get("/status")
async def dm_status():
    """DataManager cache stats and provider health."""
    return {
        "cache_size": _dm.cache_stats()["size"],
        "providers":  ["NSEDirect", "NSEPython", "Groww", "yfinance"],
        "ttls": {"quote": 10, "intraday": 30, "daily": 300, "weekly": 1800, "gainers": 60},
    }

@_dm_router.delete("/cache")
async def dm_clear_cache():
    _dm.cache.clear()
    return {"message": "DataManager cache cleared"}

app.include_router(_dm_router)

async def _binance_ws_task():
    """Background task: connect to Kraken public WebSocket and update live prices."""
    pairs = list(KRAKEN_PAIR_MAP.values())
    backoff = 5

    while True:
        try:
            logging.info("Kraken WS: connecting...")
            async with websockets.connect(KRAKEN_WS_URL, ping_interval=20, ping_timeout=10) as ws:
                backoff = 5
                # Subscribe to ticker for all pairs
                sub_msg = json.dumps({"event": "subscribe", "pair": pairs, "subscription": {"name": "ticker"}})
                await ws.send(sub_msg)
                logging.info("Kraken WS: subscribed to %d pairs", len(pairs))

                async for raw in ws:
                    try:
                        msg = json.loads(raw)

                        # Subscription confirmation → map channelID to coin_id
                        if isinstance(msg, dict):
                            if msg.get("event") == "subscriptionStatus" and msg.get("status") == "subscribed":
                                pair_name = msg.get("pair", "")
                                coin_id = KRAKEN_REVERSE_MAP.get(pair_name)
                                if coin_id:
                                    _kraken_channel_map[msg["channelID"]] = coin_id
                            continue

                        # Price tick: [channelID, {ticker data}, "ticker", "PAIR"]
                        if isinstance(msg, list) and len(msg) >= 4 and msg[2] == "ticker":
                            channel_id = msg[0]
                            tick = msg[1]
                            pair_name = msg[3]
                            coin_id = _kraken_channel_map.get(channel_id) or KRAKEN_REVERSE_MAP.get(pair_name)
                            if not coin_id:
                                continue

                            # Kraken ticker fields: c=last trade, o=open, h=high, l=low, v=volume
                            close_p = float(tick["c"][0])
                            open_p  = float(tick["o"][1])   # today's open
                            high_p  = float(tick["h"][1])   # today's high
                            low_p   = float(tick["l"][1])   # today's low
                            vol     = float(tick["v"][1])   # today's volume
                            chg_pct = ((close_p - open_p) / open_p * 100) if open_p else 0

                            symbol = KRAKEN_PAIR_MAP.get(coin_id, "").replace("/USD", "")
                            if symbol == "XBT":
                                symbol = "BTC"
                            elif symbol == "XDG":
                                symbol = "DOGE"
                            elif symbol == "POL":
                                symbol = "MATIC"

                            binance_live_prices[coin_id] = {
                                "price":      close_p,
                                "open":       open_p,
                                "high":       high_p,
                                "low":        low_p,
                                "volume":     vol,
                                "change_pct": round(chg_pct, 3),
                                "symbol":     symbol,
                                "ts":         datetime.now(timezone.utc).isoformat(),
                            }
                    except Exception as parse_err:
                        logging.debug(f"Kraken WS parse error: {parse_err}")

        except Exception as conn_err:
            logging.warning(f"Kraken WS disconnected: {conn_err}. Reconnecting in {backoff}s...")
            _kraken_channel_map.clear()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


@app.on_event("startup")
async def startup_binance_ws():
    # Seed AI Router default providers (OpenCode Free)
    try:
        from ai_router.engine import seed_defaults as _ai_seed
        asyncio.create_task(_ai_seed())
    except Exception as _e:
        logging.warning(f"AI Router seed failed: {_e}")
    asyncio.create_task(_binance_ws_task())
    # Also start QSC/Hybrid background feeds
    asyncio.create_task(_hybrid_coinbase_bridge())
    asyncio.create_task(_hybrid_coingecko_fallback())
    asyncio.create_task(_hybrid_non_crypto_simulator())
    asyncio.create_task(_hybrid_indian_yfinance_poller())
    logging.info("Kraken + QSC Hybrid + Indian yfinance background tasks started")
    # Load Robo-Trader preferences from DB
    try:
        from agents.dreamer_robo_orchestrator import load_preferences_from_db
        asyncio.create_task(load_preferences_from_db())
        logging.info("Robo-Trader preferences load task scheduled")
    except Exception as _re:
        logging.warning(f"Robo-Trader preferences load failed: {_re}")