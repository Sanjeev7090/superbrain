import React, { useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';
import { createChart, CrosshairMode } from 'lightweight-charts';
import { Sparkles, Loader2, AlertCircle, Play } from 'lucide-react';

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

const TF_LABEL_TO_KRONOS = {
  '1MIN': '1m', '5M': '5m', '10M': '15m', '15M': '15m', '30M': '30m',
  '1H': '1h', '4H': '4h', '1D': '1d', '1W': '1wk', '1M': '1d',
  '6M': '1d', '1Y': '1d',
};

const resolveKronosTf = (tf) => {
  if (!tf) return '1d';
  if (typeof tf === 'string') return TF_LABEL_TO_KRONOS[tf] || '1d';
  // object form { multiplier, timespan, label }
  const label = tf.label || '';
  if (TF_LABEL_TO_KRONOS[label]) return TF_LABEL_TO_KRONOS[label];
  const mult = tf.multiplier || 1;
  const span = tf.timespan || 'day';
  if (span === 'minute') return mult === 1 ? '1m' : mult <= 5 ? '5m' : mult <= 15 ? '15m' : '30m';
  if (span === 'hour') return mult >= 4 ? '4h' : '1h';
  if (span === 'week') return '1wk';
  return '1d';
};

const PRED_LEN_OPTIONS = [10, 20, 30, 60, 90];

const KronosForecastPanel = ({ selectedStock, timeframe = '1D' }) => {
  const containerRef = useRef(null);
  const chartRef = useRef(null);
  const histSeriesRef = useRef(null);
  const fcSeriesRef = useRef(null);
  const volSeriesRef = useRef(null);

  const [predLen, setPredLen] = useState(30);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [modelStatus, setModelStatus] = useState({ loaded: false, loading: false, error: null });
  const [forecast, setForecast] = useState(null); // { history, forecast }
  const [stats, setStats] = useState(null);

  // -------- Status polling --------
  const refreshStatus = useCallback(async () => {
    try {
      const r = await axios.get(`${API}/kronos/status`);
      setModelStatus(r.data);
    } catch (e) {
      // ignore
    }
  }, []);

  useEffect(() => {
    refreshStatus();
  }, [refreshStatus]);

  // -------- Chart init --------
  useEffect(() => {
    if (!containerRef.current || chartRef.current) return;

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 320,
      layout: {
        background: { type: 'solid', color: '#0A0A0A' },
        textColor: '#A1A1AA',
        fontFamily: "'JetBrains Mono', monospace",
      },
      localization: {
        locale: 'en-US',
        dateFormat: 'yyyy-MM-dd',
      },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.04)' },
        horzLines: { color: 'rgba(255,255,255,0.04)' },
      },
      rightPriceScale: { borderColor: 'rgba(255,255,255,0.1)' },
      timeScale: {
        borderColor: 'rgba(255,255,255,0.1)',
        timeVisible: true,
        secondsVisible: false,
        tickMarkFormatter: (time) => {
          try {
            const d = new Date(time * 1000);
            const mm = String(d.getMonth() + 1).padStart(2, '0');
            const dd = String(d.getDate()).padStart(2, '0');
            const yy = String(d.getFullYear()).slice(-2);
            return `${dd}/${mm}/${yy}`;
          } catch (e) {
            return '';
          }
        },
      },
      crosshair: { mode: CrosshairMode.Normal },
    });

    const histSeries = chart.addCandlestickSeries({
      upColor: '#00E676',
      downColor: '#FF3B30',
      borderUpColor: '#00E676',
      borderDownColor: '#FF3B30',
      wickUpColor: '#00E676',
      wickDownColor: '#FF3B30',
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    });

    const fcSeries = chart.addCandlestickSeries({
      upColor: 'rgba(168, 85, 247, 0.85)',     // purple bull
      downColor: 'rgba(245, 166, 35, 0.85)',   // amber bear
      borderUpColor: '#A855F7',
      borderDownColor: '#F5A623',
      wickUpColor: '#A855F7',
      wickDownColor: '#F5A623',
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    });

    const volSeries = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: '',
      color: 'rgba(255,255,255,0.2)',
    });
    volSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.85, bottom: 0 },
    });

    chartRef.current = chart;
    histSeriesRef.current = histSeries;
    fcSeriesRef.current = fcSeries;
    volSeriesRef.current = volSeries;

    const resizeObserver = new ResizeObserver(entries => {
      if (!entries[0]) return;
      const { width } = entries[0].contentRect;
      chart.applyOptions({ width });
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
    };
  }, []);

  // -------- Render bars when forecast changes --------
  useEffect(() => {
    if (!chartRef.current || !forecast) return;
    const histData = forecast.history.map(b => ({
      time: Math.floor(b.timestamp / 1000),
      open: b.open, high: b.high, low: b.low, close: b.close,
    }));
    const fcData = forecast.forecast.map(b => ({
      time: Math.floor(b.timestamp / 1000),
      open: b.open, high: b.high, low: b.low, close: b.close,
    }));
    const volData = [
      ...forecast.history.map(b => ({
        time: Math.floor(b.timestamp / 1000),
        value: b.volume,
        color: b.close >= b.open ? 'rgba(0,230,118,0.35)' : 'rgba(255,59,48,0.35)',
      })),
      ...forecast.forecast.map(b => ({
        time: Math.floor(b.timestamp / 1000),
        value: b.volume,
        color: b.close >= b.open ? 'rgba(168,85,247,0.45)' : 'rgba(245,166,35,0.45)',
      })),
    ];
    // Sort by time and dedupe (lightweight-charts requires strictly ascending)
    const seen = new Set();
    const uniq = arr => arr.filter(d => {
      if (seen.has(d.time)) return false;
      seen.add(d.time);
      return true;
    });
    histSeriesRef.current.setData(histData);
    seen.clear();
    fcSeriesRef.current.setData(uniq(fcData));
    seen.clear();
    volSeriesRef.current.setData(uniq(volData));

    chartRef.current.timeScale().fitContent();
  }, [forecast]);

  // -------- Forecast trigger --------
  const runForecast = async () => {
    if (!selectedStock?.ticker) {
      setError('Pehle koi stock select karo');
      return;
    }
    setError(null);
    setLoading(true);
    try {
      // Ensure model loaded (will block until ready)
      const tf = resolveKronosTf(timeframe);
      const lookback = 256;
      const resp = await axios.post(`${API}/kronos/forecast`, {
        ticker: selectedStock.ticker,
        timeframe: tf,
        lookback,
        pred_len: predLen,
        T: 1.0,
        top_p: 0.9,
        sample_count: 1,
      }, { timeout: 180000 });

      setForecast(resp.data);

      // Compute simple stats
      const last = resp.data.history[resp.data.history.length - 1];
      const final = resp.data.forecast[resp.data.forecast.length - 1];
      const maxHigh = Math.max(...resp.data.forecast.map(b => b.high));
      const minLow = Math.min(...resp.data.forecast.map(b => b.low));
      const pct = last ? ((final.close - last.close) / last.close) * 100 : 0;
      setStats({
        last_close: last?.close ?? 0,
        final_close: final?.close ?? 0,
        max_high: maxHigh,
        min_low: minLow,
        change_pct: pct,
        direction: pct >= 0 ? 'BULL' : 'BEAR',
      });
      await refreshStatus();
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || 'Forecast failed';
      setError(typeof msg === 'string' ? msg : JSON.stringify(msg));
    } finally {
      setLoading(false);
    }
  };

  const warmup = async () => {
    setError(null);
    try {
      await axios.post(`${API}/kronos/warmup`, {}, { timeout: 180000 });
      await refreshStatus();
    } catch (e) {
      const msg = e?.response?.data?.detail || e?.message || 'Warmup failed';
      setError(typeof msg === 'string' ? msg : JSON.stringify(msg));
    }
  };

  return (
    <div className="border-t border-white/10 bg-[#0A0A0A] flex flex-col" data-testid="kronos-forecast-panel">
      {/* Header */}
      <div className="h-11 px-4 flex items-center justify-between border-b border-white/10 bg-[#0E0E10]">
        <div className="flex items-center gap-3">
          <div className="w-6 h-6 rounded-sm bg-[#A855F7]/15 border border-[#A855F7]/40 flex items-center justify-center">
            <Sparkles className="w-3.5 h-3.5 text-[#A855F7]" />
          </div>
          <div className="flex items-baseline gap-2">
            <span className="text-[11px] font-black uppercase tracking-[0.2em] text-white">
              KRONOS FORECAST
            </span>
            <span className="text-[9px] font-bold uppercase tracking-[0.2em] text-[#A855F7]">
              AI K-LINE
            </span>
          </div>
          <span className="text-[9px] font-mono text-zinc-500 uppercase tracking-wider">
            {modelStatus.loaded ? `Model: ${modelStatus.model?.split('/').pop()}` : 'Model: not loaded'}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <div className="flex items-center gap-1">
            <span className="text-[9px] text-zinc-500 uppercase tracking-wider mr-1">Next</span>
            {PRED_LEN_OPTIONS.map(n => (
              <button
                key={n}
                onClick={() => setPredLen(n)}
                className={`px-2 h-6 text-[10px] font-mono font-bold border ${
                  predLen === n
                    ? 'bg-[#A855F7] text-black border-[#A855F7]'
                    : 'bg-transparent text-zinc-400 border-white/10 hover:border-white/30'
                }`}
                data-testid={`kronos-pred-${n}`}
              >
                {n}
              </button>
            ))}
          </div>
          {!modelStatus.loaded && (
            <button
              onClick={warmup}
              className="px-2 h-6 text-[10px] font-bold uppercase tracking-wider bg-transparent border border-white/15 text-zinc-300 hover:bg-white/5"
              data-testid="kronos-warmup"
            >
              Warmup
            </button>
          )}
          <button
            onClick={runForecast}
            disabled={loading}
            className="h-6 px-3 inline-flex items-center gap-1 text-[10px] font-black uppercase tracking-[0.15em] bg-[#A855F7] hover:bg-[#9333EA] text-black disabled:opacity-50 disabled:cursor-not-allowed"
            data-testid="kronos-run"
          >
            {loading ? (
              <>
                <Loader2 className="w-3 h-3 animate-spin" /> RUNNING
              </>
            ) : (
              <>
                <Play className="w-3 h-3" /> FORECAST
              </>
            )}
          </button>
        </div>
      </div>

      {/* Stats strip */}
      {stats && (
        <div className="px-4 py-2 border-b border-white/10 grid grid-cols-2 md:grid-cols-5 gap-3 bg-[#0B0B0D]">
          <Stat label="Last Close" value={stats.last_close.toFixed(2)} />
          <Stat label="Forecast Close" value={stats.final_close.toFixed(2)} />
          <Stat label="Predicted High" value={stats.max_high.toFixed(2)} color="#00E676" />
          <Stat label="Predicted Low" value={stats.min_low.toFixed(2)} color="#FF3B30" />
          <Stat
            label="Change"
            value={`${stats.change_pct >= 0 ? '+' : ''}${stats.change_pct.toFixed(2)}%`}
            color={stats.change_pct >= 0 ? '#00E676' : '#FF3B30'}
            badge={stats.direction}
            badgeColor={stats.change_pct >= 0 ? '#00E676' : '#FF3B30'}
          />
        </div>
      )}

      {/* Error banner */}
      {error && (
        <div className="px-4 py-2 bg-[#FF3B30]/10 border-b border-[#FF3B30]/30 flex items-start gap-2">
          <AlertCircle className="w-3.5 h-3.5 text-[#FF3B30] mt-0.5 shrink-0" />
          <span className="text-[10px] font-mono text-[#FF3B30] break-all">{error}</span>
        </div>
      )}

      {/* Loading hint */}
      {loading && !forecast && (
        <div className="px-4 py-2 bg-[#A855F7]/10 border-b border-[#A855F7]/30 flex items-center gap-2">
          <Loader2 className="w-3.5 h-3.5 text-[#A855F7] animate-spin" />
          <span className="text-[10px] font-mono text-[#A855F7] uppercase tracking-wider">
            {modelStatus.loaded
              ? `Generating ${predLen} candle forecast...`
              : 'Loading Kronos model (first run ~30–60s)...'}
          </span>
        </div>
      )}

      {/* Empty state */}
      {!forecast && !loading && (
        <div className="flex flex-col items-center justify-center py-10 px-4 text-center">
          <Sparkles className="w-8 h-8 text-[#A855F7] mb-2" />
          <div className="text-[12px] font-bold uppercase tracking-wider text-white">
            Kronos K-Line Forecast
          </div>
          <div className="text-[10px] text-zinc-500 mt-1 max-w-md">
            Foundation model (NeoQuasar/Kronos-small) predicts the next {predLen} candles for{' '}
            <span className="text-[#A855F7] font-mono">{selectedStock?.ticker || 'select a stock'}</span>{' '}
            using {typeof timeframe === 'string' ? timeframe : (timeframe?.label || '1D')} timeframe.
          </div>
          <button
            onClick={runForecast}
            disabled={!selectedStock?.ticker}
            className="mt-3 h-7 px-4 inline-flex items-center gap-1 text-[10px] font-black uppercase tracking-[0.15em] bg-[#A855F7] hover:bg-[#9333EA] text-black disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Play className="w-3 h-3" /> Run Forecast
          </button>
        </div>
      )}

      {/* Chart */}
      <div
        ref={containerRef}
        className="w-full"
        style={{ height: forecast ? 320 : 0, transition: 'height 200ms ease' }}
      />

      {/* Footer legend */}
      {forecast && (
        <div className="h-7 px-4 flex items-center justify-between border-t border-white/10 bg-[#0B0B0D]">
          <div className="flex items-center gap-4 text-[9px] font-mono uppercase tracking-wider text-zinc-500">
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 bg-[#00E676]" /> History
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 bg-[#A855F7]" /> Forecast (Bull)
            </span>
            <span className="flex items-center gap-1.5">
              <span className="w-2 h-2 bg-[#F5A623]" /> Forecast (Bear)
            </span>
          </div>
          <div className="text-[9px] font-mono text-zinc-600 uppercase tracking-wider">
            Lookback {forecast.lookback_used} • Pred {forecast.pred_len} • T 1.0 • top_p 0.9
          </div>
        </div>
      )}
    </div>
  );
};

const Stat = ({ label, value, color, badge, badgeColor }) => (
  <div className="flex flex-col">
    <span className="text-[8px] font-bold uppercase tracking-[0.2em] text-zinc-500">{label}</span>
    <div className="flex items-baseline gap-2 mt-0.5">
      <span
        className="text-sm font-mono font-bold"
        style={{ color: color || '#FFFFFF' }}
      >
        {value}
      </span>
      {badge && (
        <span
          className="text-[8px] font-bold uppercase tracking-[0.2em] px-1.5 py-0.5"
          style={{ background: badgeColor, color: '#000' }}
        >
          {badge}
        </span>
      )}
    </div>
  </div>
);

export default KronosForecastPanel;
