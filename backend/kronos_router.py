"""
Kronos Forecast Router
Integrates the Kronos foundation model (https://github.com/shiyu-coder/Kronos, MIT License)
to generate next-N candle (OHLCV) forecasts and exposes them via /api/kronos/* endpoints.

Models are lazily loaded from Hugging Face on first request (downloaded ~once, then cached).
"""
import os
import logging
from datetime import datetime, timedelta
from typing import Optional, List

import numpy as np
import pandas as pd
import yfinance as yf
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("kronos_router")

kronos_router = APIRouter(prefix="/api/kronos", tags=["Kronos Forecast"])

# Lazy globals — loaded on first request
_MODEL = None
_TOKENIZER = None
_PREDICTOR = None
_LOAD_ERROR: Optional[str] = None
_LOADING = False


def _get_predictor():
    """Lazy-load Kronos tokenizer + predictor model (CPU)."""
    global _MODEL, _TOKENIZER, _PREDICTOR, _LOAD_ERROR, _LOADING

    if _PREDICTOR is not None:
        return _PREDICTOR

    if _LOAD_ERROR is not None:
        raise HTTPException(status_code=503, detail=f"Kronos model unavailable: {_LOAD_ERROR}")

    if _LOADING:
        raise HTTPException(status_code=503, detail="Kronos model is still loading, please retry in a few seconds")

    _LOADING = True
    try:
        logger.info("Kronos: loading tokenizer + model from HuggingFace (first-time download may take ~30-60s)…")
        # Import inside the function so the module load doesn't fail server startup
        from kronos import Kronos, KronosTokenizer, KronosPredictor

        tokenizer_name = os.environ.get("KRONOS_TOKENIZER", "NeoQuasar/Kronos-Tokenizer-base")
        model_name = os.environ.get("KRONOS_MODEL", "NeoQuasar/Kronos-small")

        _TOKENIZER = KronosTokenizer.from_pretrained(tokenizer_name)
        _MODEL = Kronos.from_pretrained(model_name)
        _PREDICTOR = KronosPredictor(_MODEL, _TOKENIZER, device="cpu", max_context=512)
        logger.info(f"Kronos: loaded tokenizer={tokenizer_name} model={model_name} on CPU")
        return _PREDICTOR
    except Exception as e:
        _LOAD_ERROR = str(e)
        logger.exception("Kronos: failed to load model")
        raise HTTPException(status_code=503, detail=f"Failed to load Kronos: {e}")
    finally:
        _LOADING = False


# -------- Pydantic schemas --------

class KronosBar(BaseModel):
    timestamp: int  # ms epoch
    open: float
    high: float
    low: float
    close: float
    volume: float


class KronosForecastRequest(BaseModel):
    ticker: str
    timeframe: str = "1d"            # 1m,5m,15m,30m,1h,4h,1d,1wk
    lookback: int = Field(default=200, ge=64, le=512)
    pred_len: int = Field(default=30, ge=5, le=120)
    T: float = 1.0
    top_p: float = 0.9
    sample_count: int = 1


class KronosForecastResponse(BaseModel):
    ticker: str
    timeframe: str
    history: List[KronosBar]
    forecast: List[KronosBar]
    model: str
    lookback_used: int
    pred_len: int


# -------- Helpers --------

_TIMEFRAME_TO_YF = {
    "1m": ("1m", "7d"),
    "5m": ("5m", "60d"),
    "15m": ("15m", "60d"),
    "30m": ("30m", "60d"),
    "1h": ("1h", "730d"),
    "4h": ("4h", "730d"),
    "1d": ("1d", None),
    "1wk": ("1wk", None),
}

_TIMEFRAME_TO_DELTA = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "15m": timedelta(minutes=15),
    "30m": timedelta(minutes=30),
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1d": timedelta(days=1),
    "1wk": timedelta(weeks=1),
}


def _fetch_history(ticker: str, timeframe: str, lookback: int) -> pd.DataFrame:
    if timeframe not in _TIMEFRAME_TO_YF:
        raise HTTPException(status_code=400, detail=f"Unsupported timeframe: {timeframe}")
    interval, period = _TIMEFRAME_TO_YF[timeframe]
    yf_ticker = yf.Ticker(ticker)
    if period:
        hist = yf_ticker.history(period=period, interval=interval)
    else:
        # Daily / weekly: span lookback+buffer days
        start = (datetime.now() - timedelta(days=max(lookback * 2, 365))).strftime("%Y-%m-%d")
        hist = yf_ticker.history(start=start, interval=interval)
    if hist is None or hist.empty:
        raise HTTPException(status_code=404, detail=f"No bars found for {ticker} ({timeframe})")
    hist = hist.dropna()
    if len(hist) > lookback:
        hist = hist.tail(lookback)
    return hist


def _build_future_timestamps(last_ts: pd.Timestamp, pred_len: int, timeframe: str) -> pd.DatetimeIndex:
    delta = _TIMEFRAME_TO_DELTA[timeframe]
    return pd.DatetimeIndex([last_ts + delta * (i + 1) for i in range(pred_len)])


def _df_to_bars(df: pd.DataFrame) -> List[KronosBar]:
    bars = []
    for idx, row in df.iterrows():
        try:
            ts_ms = int(pd.Timestamp(idx).timestamp() * 1000)
        except Exception:
            ts_ms = int(datetime.utcnow().timestamp() * 1000)
        bars.append(KronosBar(
            timestamp=ts_ms,
            open=float(row.get("open", row.get("Open", 0))),
            high=float(row.get("high", row.get("High", 0))),
            low=float(row.get("low", row.get("Low", 0))),
            close=float(row.get("close", row.get("Close", 0))),
            volume=float(row.get("volume", row.get("Volume", 0))),
        ))
    return bars


# -------- Endpoints --------

@kronos_router.get("/status")
async def kronos_status():
    return {
        "loaded": _PREDICTOR is not None,
        "loading": _LOADING,
        "error": _LOAD_ERROR,
        "tokenizer": os.environ.get("KRONOS_TOKENIZER", "NeoQuasar/Kronos-Tokenizer-base"),
        "model": os.environ.get("KRONOS_MODEL", "NeoQuasar/Kronos-small"),
        "device": "cpu",
    }


@kronos_router.post("/warmup")
async def kronos_warmup():
    """Pre-load the Kronos model (so first /forecast is fast)."""
    _get_predictor()
    return {"loaded": True}


@kronos_router.post("/forecast", response_model=KronosForecastResponse)
async def kronos_forecast(req: KronosForecastRequest):
    """Generate the next `pred_len` candles for the given ticker using Kronos-small."""
    predictor = _get_predictor()

    hist = _fetch_history(req.ticker, req.timeframe, req.lookback)

    # Build the input DataFrame in the columns Kronos expects
    in_df = pd.DataFrame({
        "open": hist["Open"].astype(float).values,
        "high": hist["High"].astype(float).values,
        "low": hist["Low"].astype(float).values,
        "close": hist["Close"].astype(float).values,
        "volume": hist["Volume"].astype(float).values,
    })
    # Kronos optional 'amount' = volume * typical price
    in_df["amount"] = in_df["volume"] * ((in_df["open"] + in_df["high"] + in_df["low"] + in_df["close"]) / 4.0)

    x_timestamp = pd.Series(pd.to_datetime(hist.index)).reset_index(drop=True)
    last_ts = x_timestamp.iloc[-1]
    y_timestamp_idx = _build_future_timestamps(last_ts, req.pred_len, req.timeframe)
    y_timestamp = pd.Series(y_timestamp_idx)

    try:
        pred_df = predictor.predict(
            df=in_df,
            x_timestamp=x_timestamp,
            y_timestamp=y_timestamp,
            pred_len=req.pred_len,
            T=req.T,
            top_p=req.top_p,
            sample_count=req.sample_count,
            verbose=False,
        )
    except Exception as e:
        logger.exception("Kronos: prediction failed")
        raise HTTPException(status_code=500, detail=f"Kronos prediction failed: {e}")

    # Build history bars from hist
    hist_bars: List[KronosBar] = []
    for idx, row in hist.iterrows():
        hist_bars.append(KronosBar(
            timestamp=int(pd.Timestamp(idx).timestamp() * 1000),
            open=float(row["Open"]),
            high=float(row["High"]),
            low=float(row["Low"]),
            close=float(row["Close"]),
            volume=float(row["Volume"]),
        ))

    # Build forecast bars
    pred_df = pred_df.copy()
    pred_df.index = y_timestamp_idx  # ensure proper index
    forecast_bars: List[KronosBar] = []
    for ts, row in pred_df.iterrows():
        # Guard: ensure high >= max(open,close), low <= min(open,close)
        o = float(row["open"]); c = float(row["close"])
        h = float(row.get("high", max(o, c)))
        l = float(row.get("low", min(o, c)))
        h = max(h, o, c)
        l = min(l, o, c)
        forecast_bars.append(KronosBar(
            timestamp=int(pd.Timestamp(ts).timestamp() * 1000),
            open=o,
            high=h,
            low=l,
            close=c,
            volume=float(max(row.get("volume", 0.0), 0.0)),
        ))

    return KronosForecastResponse(
        ticker=req.ticker.upper(),
        timeframe=req.timeframe,
        history=hist_bars,
        forecast=forecast_bars,
        model=os.environ.get("KRONOS_MODEL", "NeoQuasar/Kronos-small"),
        lookback_used=len(hist_bars),
        pred_len=len(forecast_bars),
    )
