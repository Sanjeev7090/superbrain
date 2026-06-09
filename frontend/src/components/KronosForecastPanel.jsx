import React, { useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';
import { createChart, CrosshairMode } from 'lightweight-charts';
import {
  Sparkles, Loader2, AlertCircle, Play,
  TrendingUp, TrendingDown, Minus, Target, ShieldAlert, ArrowRight,
  ChevronDown, ChevronUp,
} from 'lucide-react';

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

const useIsMobile = () => {
  const [mobile, setMobile] = useState(() => window.innerWidth < 768);
  useEffect(() => {
    const fn = () => setMobile(window.innerWidth < 768);
    window.addEventListener('resize', fn);
    return () => window.removeEventListener('resize', fn);
  }, []);
  return mobile;
};

const KronosForecastPanel = ({ selectedStock, timeframe = '1D' }) => {
  const containerRef   = useRef(null);
  const chartRef       = useRef(null);
  const histSeriesRef  = useRef(null);
  const fcSeriesRef    = useRef(null);
  const volSeriesRef   = useRef(null);
  const priceLinesRef  = useRef([]);

  const isMobile = useIsMobile();

  const [predLen, setPredLen]         = useState(30);
  const [loading, setLoading]         = useState(false);
  const [error, setError]             = useState(null);
  const [modelStatus, setModelStatus] = useState({ loaded: false, loading: false, error: null });
  const [forecast, setForecast]       = useState(null);
  const [stats, setStats]             = useState(null);
  const [collapsed, setCollapsed]     = useState(false); // mobile collapse

  const refreshStatus = useCallback(async () => {
    try {
      const r = await axios.get(`${API}/kronos/status`);
      setModelStatus(r.data);
    } catch (_) {}
  }, []);

  useEffect(() => { refreshStatus(); }, [refreshStatus]);

  // ── Clear stale data when stock changes ─────────────────────────────────────
  useEffect(() => {
    // When selectedStock changes, clear old forecast & stats
    setForecast(null);
    setStats(null);
    setError(null);
    // Clear chart series
    if (histSeriesRef.current) {
      try { histSeriesRef.current.setData([]); } catch (_) {}
    }
    if (fcSeriesRef.current) {
      try { fcSeriesRef.current.setData([]); } catch (_) {}
      // Remove old price lines
      if (priceLinesRef.current.length) {
        priceLinesRef.current.forEach(pl => {
          try { fcSeriesRef.current.removePriceLine(pl); } catch (_) {}
        });
        priceLinesRef.current = [];
      }
    }
    if (volSeriesRef.current) {
      try { volSeriesRef.current.setData([]); } catch (_) {}
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedStock?.ticker]);

  // ── Chart init ──────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!containerRef.current || chartRef.current) return;

    const chartHeight = isMobile ? 240 : 320;

    const chart = createChart(containerRef.current, {
      width:  containerRef.current.clientWidth,
      height: chartHeight,
      layout: {
        background:  { type: 'solid', color: '#0A0A0A' },
        textColor:   '#A1A1AA',
        fontFamily:  "'JetBrains Mono', monospace",
        fontSize:    isMobile ? 10 : 11,
      },
      localization: { locale: 'en-US', dateFormat: 'yyyy-MM-dd' },
      grid: {
        vertLines: { color: 'rgba(255,255,255,0.04)' },
        horzLines: { color: 'rgba(255,255,255,0.04)' },
      },
      rightPriceScale: {
        borderColor: 'rgba(255,255,255,0.1)',
        scaleMargins: { top: 0.05, bottom: 0.05 },
      },
      leftPriceScale:  { visible: false },
      timeScale: {
        borderColor:  'rgba(255,255,255,0.1)',
        timeVisible:  true,
        secondsVisible: false,
        tickMarkFormatter: (time) => {
          try {
            const d = new Date(time * 1000);
            return `${String(d.getDate()).padStart(2,'0')}/${String(d.getMonth()+1).padStart(2,'0')}`;
          } catch { return ''; }
        },
      },
      crosshair: { mode: CrosshairMode.Normal },
      handleScroll:   { mouseWheel: !isMobile },
      handleScale:    { mouseWheel: !isMobile, pinch: true },
    });

    histSeriesRef.current = chart.addCandlestickSeries({
      upColor: '#00E676', downColor: '#FF3B30',
      borderUpColor: '#00E676', borderDownColor: '#FF3B30',
      wickUpColor: '#00E676', wickDownColor: '#FF3B30',
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    });

    fcSeriesRef.current = chart.addCandlestickSeries({
      upColor: 'rgba(168,85,247,0.85)', downColor: 'rgba(245,166,35,0.85)',
      borderUpColor: '#A855F7', borderDownColor: '#F5A623',
      wickUpColor: '#A855F7', wickDownColor: '#F5A623',
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    });

    volSeriesRef.current = chart.addHistogramSeries({
      priceFormat: { type: 'volume' },
      priceScaleId: '',
      color: 'rgba(255,255,255,0.2)',
    });
    volSeriesRef.current.priceScale().applyOptions({
      scaleMargins: { top: 0.85, bottom: 0 },
    });

    chartRef.current = chart;

    const ro = new ResizeObserver(entries => {
      if (!entries[0]) return;
      chart.applyOptions({ width: entries[0].contentRect.width });
    });
    ro.observe(containerRef.current);

    return () => { ro.disconnect(); chart.remove(); chartRef.current = null; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Render data ──────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!chartRef.current || !forecast) return;
    const toBar = b => ({
      time: Math.floor(b.timestamp / 1000),
      open: b.open, high: b.high, low: b.low, close: b.close,
    });
    const seen = new Set();
    const dedup = arr => arr.filter(d => { if (seen.has(d.time)) return false; seen.add(d.time); return true; });

    histSeriesRef.current.setData(forecast.history.map(toBar));
    seen.clear();
    fcSeriesRef.current.setData(dedup(forecast.forecast.map(toBar)));
    seen.clear();
    volSeriesRef.current.setData(dedup([
      ...forecast.history.map(b => ({
        time: Math.floor(b.timestamp / 1000), value: b.volume,
        color: b.close >= b.open ? 'rgba(0,230,118,0.35)' : 'rgba(255,59,48,0.35)',
      })),
      ...forecast.forecast.map(b => ({
        time: Math.floor(b.timestamp / 1000), value: b.volume,
        color: b.close >= b.open ? 'rgba(168,85,247,0.45)' : 'rgba(245,166,35,0.45)',
      })),
    ]));

    // Price lines
    if (priceLinesRef.current.length && fcSeriesRef.current) {
      priceLinesRef.current.forEach(pl => { try { fcSeriesRef.current.removePriceLine(pl); } catch (_) {} });
      priceLinesRef.current = [];
    }
    const sig = forecast.signal;
    if (sig && fcSeriesRef.current) {
      const bull = sig.direction === 'BUY';
      const lines = [
        { price: sig.entry,      color: '#FFFFFF',  title: `ENTRY ${sig.entry.toFixed(2)}`,      lineStyle: 0, lineWidth: 1 },
        { price: sig.stop_loss,  color: '#FF3B30',  title: `SL ${sig.stop_loss.toFixed(2)}`,     lineStyle: 2, lineWidth: 2 },
        { price: sig.day_target, color: '#F5A623',  title: `DAY ${sig.day_target.toFixed(2)}`,   lineStyle: 1, lineWidth: 1 },
      ];
      (sig.targets || []).forEach((t, i) => lines.push({
        price: t, color: bull ? '#00E676' : '#FF3B30',
        title: `T${i+1} ${t.toFixed(2)}`, lineStyle: 2, lineWidth: 1,
      }));
      lines.forEach(l => {
        try {
          priceLinesRef.current.push(fcSeriesRef.current.createPriceLine({
            price: l.price, color: l.color, lineStyle: l.lineStyle,
            lineWidth: l.lineWidth, axisLabelVisible: true, title: l.title,
          }));
        } catch (_) {}
      });
    }
    chartRef.current.timeScale().fitContent();
  }, [forecast]);

  // ── Actions ──────────────────────────────────────────────────────────────────
  const runForecast = async () => {
    if (!selectedStock?.ticker) { setError('Pehle koi stock select karo'); return; }
    setError(null);
    setLoading(true);
    setCollapsed(false);
    try {
      // ── Auto-warmup if model not loaded ──────────────────────────────────
      if (!modelStatus.loaded) {
        setError('Kronos warming up... please wait (30–60s)');
        await axios.post(`${API}/kronos/warmup`, {}, { timeout: 180000 });
        await refreshStatus();
        setError(null);
      }

      const tf = resolveKronosTf(timeframe);
      const resp = await axios.post(`${API}/kronos/forecast`, {
        ticker: selectedStock.ticker, timeframe: tf,
        lookback: 256, pred_len: predLen, T: 1.0, top_p: 0.9, sample_count: 1,
      }, { timeout: 180000 });
      setForecast(resp.data);
      const last  = resp.data.history.at(-1);
      const final = resp.data.forecast.at(-1);
      const pct   = last ? ((final.close - last.close) / last.close) * 100 : 0;
      setStats({
        last_close: last?.close ?? 0,
        final_close: final?.close ?? 0,
        max_high: Math.max(...resp.data.forecast.map(b => b.high)),
        min_low:  Math.min(...resp.data.forecast.map(b => b.low)),
        change_pct: pct,
        direction: pct >= 0 ? 'BULL' : 'BEAR',
      });
      await refreshStatus();
    } catch (e) {
      const status = e?.response?.status;
      const msg    = e?.response?.data?.detail || e?.message || 'Forecast failed';
      const msgStr = typeof msg === 'string' ? msg : JSON.stringify(msg);

      // Auto-retry warmup if 503 (model still loading) and not already warming
      if (status === 503 && !modelStatus.loading) {
        setError('Kronos model not ready — starting warmup automatically...');
        try {
          await axios.post(`${API}/kronos/warmup`, {}, { timeout: 180000 });
          await refreshStatus();
          setError('Warmup done! Click Run Forecast again.');
        } catch {
          setError('Warmup failed — please try again');
        }
      } else {
        setError(msgStr);
      }
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

  const tfLabel = typeof timeframe === 'string' ? timeframe : (timeframe?.label || '1D');
  const modelName = modelStatus.loaded
    ? (modelStatus.model?.split('/').pop() || 'LOADED')
    : 'NOT LOADED';

  return (
    <div className="border-t border-white/10 bg-[#0A0A0A] flex flex-col" data-testid="kronos-forecast-panel">

      {/* ── Header ─────────────────────────────────────────────────────────── */}
      <div className="px-3 sm:px-4 py-2 sm:py-0 sm:h-11 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 sm:gap-0 border-b border-white/10 bg-[#0E0E10]">

        {/* Left: brand + model status */}
        <div className="flex items-center justify-between sm:justify-start gap-2">
          <div className="flex items-center gap-2">
            <div className="w-5 h-5 sm:w-6 sm:h-6 rounded-sm bg-[#A855F7]/15 border border-[#A855F7]/40 flex items-center justify-center shrink-0">
              <Sparkles className="w-3 h-3 sm:w-3.5 sm:h-3.5 text-[#A855F7]" />
            </div>
            <div className="flex items-baseline gap-1.5">
              <span className="text-[11px] font-black uppercase tracking-[0.18em] text-white leading-none">
                KRONOS
              </span>
              <span className="text-[9px] font-bold uppercase tracking-[0.18em] text-[#A855F7] leading-none hidden xs:inline">
                AI K-LINE
              </span>
            </div>
            <span
              className={`text-[8px] font-mono uppercase tracking-wider px-1.5 py-0.5 rounded-sm hidden sm:inline ${
                modelStatus.loaded
                  ? 'text-emerald-400 bg-emerald-400/10 border border-emerald-400/20'
                  : 'text-zinc-500 bg-white/4 border border-white/8'
              }`}
            >
              {modelName}
            </span>
          </div>

          {/* Mobile: collapse toggle */}
          <button
            onClick={() => setCollapsed(c => !c)}
            className="sm:hidden p-1 text-zinc-500 hover:text-white"
            aria-label="Toggle"
          >
            {collapsed ? <ChevronDown className="w-4 h-4" /> : <ChevronUp className="w-4 h-4" />}
          </button>
        </div>

        {/* Right: pred-len selector + buttons */}
        <div className={`flex items-center gap-1.5 flex-wrap sm:flex-nowrap ${collapsed ? 'hidden sm:flex' : 'flex'}`}>
          {/* NEXT label — hidden on very small */}
          <span className="text-[8px] text-zinc-500 uppercase tracking-wider hidden xs:inline shrink-0">Next</span>

          {/* Pred-len buttons */}
          <div className="flex items-center gap-0.5">
            {PRED_LEN_OPTIONS.map(n => (
              <button
                key={n}
                onClick={() => setPredLen(n)}
                className={`w-7 h-6 text-[10px] font-mono font-bold border transition-colors ${
                  predLen === n
                    ? 'bg-[#A855F7] text-black border-[#A855F7]'
                    : 'bg-transparent text-zinc-400 border-white/10 hover:border-white/30 hover:text-white'
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
              className="h-6 px-2 text-[9px] sm:text-[10px] font-bold uppercase tracking-wider bg-transparent border border-white/15 text-zinc-300 hover:bg-white/5 whitespace-nowrap"
              data-testid="kronos-warmup"
            >
              Warmup
            </button>
          )}

          <button
            onClick={runForecast}
            disabled={loading}
            className="h-7 sm:h-6 px-3 inline-flex items-center gap-1 text-[10px] font-black uppercase tracking-[0.12em] bg-[#A855F7] hover:bg-[#9333EA] text-black disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap"
            data-testid="kronos-run"
          >
            {loading
              ? <><Loader2 className="w-3 h-3 animate-spin" /><span className="hidden xs:inline"> RUNNING</span></>
              : <><Play className="w-3 h-3" /><span> FORECAST</span></>
            }
          </button>
        </div>
      </div>

      {/* ── Collapsible content (mobile) ────────────────────────────────────── */}
      <div className={collapsed ? 'hidden sm:block' : 'block'}>

        {/* Stats strip */}
        {stats && (
          <div className="px-3 sm:px-4 py-2 border-b border-white/10 grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-x-3 gap-y-2 bg-[#0B0B0D]">
            <Stat label="Last Close"     value={stats.last_close.toFixed(2)} />
            <Stat label="Forecast Close" value={stats.final_close.toFixed(2)} />
            <Stat label="Pred High"      value={stats.max_high.toFixed(2)} color="#00E676" />
            <Stat label="Pred Low"       value={stats.min_low.toFixed(2)}  color="#FF3B30" />
            <Stat
              label="Change"
              value={`${stats.change_pct >= 0 ? '+' : ''}${stats.change_pct.toFixed(2)}%`}
              color={stats.change_pct >= 0 ? '#00E676' : '#FF3B30'}
              badge={stats.direction}
              badgeColor={stats.change_pct >= 0 ? '#00E676' : '#FF3B30'}
            />
          </div>
        )}

        {/* Signal row */}
        {forecast?.signal && <SignalRow signal={forecast.signal} isMobile={isMobile} />}

        {/* Error banner */}
        {error && (
          <div className="px-3 sm:px-4 py-2 bg-[#FF3B30]/10 border-b border-[#FF3B30]/30 flex items-start gap-2">
            <AlertCircle className="w-3.5 h-3.5 text-[#FF3B30] mt-0.5 shrink-0" />
            <span className="text-[10px] font-mono text-[#FF3B30] break-all">{error}</span>
          </div>
        )}

        {/* Loading hint */}
        {loading && !forecast && (
          <div className="px-3 sm:px-4 py-2 bg-[#A855F7]/10 border-b border-[#A855F7]/30 flex items-center gap-2">
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
          <div className="flex flex-col items-center justify-center py-8 sm:py-10 px-4 text-center">
            <Sparkles className="w-7 h-7 sm:w-8 sm:h-8 text-[#A855F7] mb-2" />
            <p className="text-[12px] font-bold uppercase tracking-wider text-white">
              Kronos K-Line Forecast
            </p>
            <p className="text-[11px] text-zinc-500 mt-1 max-w-xs sm:max-w-md">
              Foundation model (NeoQuasar/Kronos-small) predicts the next {predLen} candles for{' '}
              <span className="text-[#A855F7] font-mono">
                {selectedStock?.ticker || 'select a stock'}
              </span>{' '}
              using {tfLabel} timeframe.
            </p>
            <button
              onClick={runForecast}
              disabled={!selectedStock?.ticker}
              className="mt-3 h-8 px-4 inline-flex items-center gap-1.5 text-[11px] font-black uppercase tracking-[0.12em] bg-[#A855F7] hover:bg-[#9333EA] text-black disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <Play className="w-3 h-3" /> Run Forecast
            </button>
          </div>
        )}

        {/* Chart */}
        <div
          ref={containerRef}
          className="w-full"
          style={{
            height: forecast ? (isMobile ? 240 : 320) : 0,
            transition: 'height 200ms ease',
          }}
        />

        {/* Footer legend */}
        {forecast && (
          <div className="px-3 sm:px-4 py-1.5 sm:h-7 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-1 sm:gap-0 border-t border-white/10 bg-[#0B0B0D]">
            <div className="flex items-center gap-3 text-[8px] sm:text-[9px] font-mono uppercase tracking-wider text-zinc-500 flex-wrap">
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 bg-[#00E676] inline-block" /> History
              </span>
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 bg-[#A855F7] inline-block" /> Bull
              </span>
              <span className="flex items-center gap-1">
                <span className="w-2 h-2 bg-[#F5A623] inline-block" /> Bear
              </span>
              <span className="text-zinc-600 hidden sm:inline">
                Pred {forecast.pred_len} • T 1.0
              </span>
            </div>
            <div className="text-[8px] font-mono text-zinc-600 uppercase tracking-wider">
              Lookback {forecast.lookback_used} · Pred {forecast.pred_len}
            </div>
          </div>
        )}
      </div>
    </div>
  );
};

/* ── Subcomponents ─────────────────────────────────────────────────────────── */

const Stat = ({ label, value, color, badge, badgeColor }) => (
  <div className="flex flex-col min-w-0">
    <span className="text-[8px] font-bold uppercase tracking-[0.18em] text-zinc-500 truncate">{label}</span>
    <div className="flex items-baseline gap-1.5 mt-0.5 flex-wrap">
      <span className="text-sm font-mono font-bold leading-none truncate" style={{ color: color || '#FFFFFF' }}>
        {value}
      </span>
      {badge && (
        <span
          className="text-[7px] font-bold uppercase tracking-[0.18em] px-1 py-0.5 shrink-0"
          style={{ background: badgeColor, color: '#000' }}
        >
          {badge}
        </span>
      )}
    </div>
  </div>
);

const SignalRow = ({ signal, isMobile }) => {
  const dir   = signal.direction;
  const bull  = dir === 'BUY';
  const bear  = dir === 'SELL';
  const accent= bull ? '#00E676' : bear ? '#FF3B30' : '#F5A623';
  const Icon  = bull ? TrendingUp : bear ? TrendingDown : Minus;

  return (
    <div
      className="border-b border-white/10"
      style={{ background: `linear-gradient(90deg, ${accent}18 0%, #0A0A0A 70%)` }}
      data-testid="kronos-signal-row"
    >
      {/* Direction + RR — always full-width row on mobile */}
      <div className="flex items-stretch">
        {/* Direction badge */}
        <div
          className="flex items-center gap-2 px-3 sm:px-4 py-2 min-w-[120px] sm:min-w-[160px] shrink-0"
          style={{ background: accent, color: '#000' }}
        >
          <Icon className="w-4 h-4 sm:w-5 sm:h-5 shrink-0" strokeWidth={2.5} />
          <div className="flex flex-col leading-none min-w-0">
            <span className="text-[8px] font-bold uppercase tracking-[0.18em] opacity-75 whitespace-nowrap">KRONOS</span>
            <span className="text-base sm:text-xl font-black uppercase tracking-tight">{dir}</span>
          </div>
          <div className="ml-auto flex flex-col leading-none text-right shrink-0">
            <span className="text-[8px] font-bold uppercase opacity-75">CONF</span>
            <span className="text-xs sm:text-sm font-mono font-black">{signal.confidence}%</span>
          </div>
        </div>

        {/* Risk-Reward — compact on mobile */}
        <div className="flex items-center justify-end gap-3 sm:gap-4 px-3 sm:px-4 flex-1 border-l border-white/10">
          <div className="flex flex-col leading-none text-right sm:text-left">
            <span className="text-[7px] sm:text-[8px] font-bold uppercase tracking-[0.18em] text-zinc-500">R:R</span>
            <span className="text-xs sm:text-sm font-mono font-bold text-white">1:{signal.risk_reward}</span>
          </div>
          <div className="flex flex-col leading-none text-right">
            <span className="text-[7px] sm:text-[8px] font-bold uppercase tracking-[0.18em] text-zinc-500">Expected</span>
            <span
              className="text-xs sm:text-sm font-mono font-bold"
              style={{ color: signal.expected_move_pct >= 0 ? '#00E676' : '#FF3B30' }}
            >
              {signal.expected_move_pct >= 0 ? '+' : ''}{signal.expected_move_pct}%
            </span>
          </div>
        </div>
      </div>

      {/* Levels grid — 3 cols on mobile, 6 on desktop */}
      <div className="grid grid-cols-3 sm:grid-cols-6 border-t border-white/5">
        <Cell label="Entry"      value={signal.entry}          color="#FFFFFF"  icon={ArrowRight} />
        <Cell label="Stop Loss"  value={signal.stop_loss}      color="#FF3B30"  icon={ShieldAlert} />
        <Cell label="Day Target" value={signal.day_target}     color="#F5A623"  icon={Target} shade />
        <Cell label="T1"         value={signal.targets?.[0]}   color={bull ? '#00E676' : bear ? '#FF3B30' : '#A1A1AA'} />
        <Cell label="T2"         value={signal.targets?.[1]}   color={bull ? '#00E676' : bear ? '#FF3B30' : '#A1A1AA'} />
        <Cell label="T3"         value={signal.targets?.[2]}   color={bull ? '#00E676' : bear ? '#FF3B30' : '#A1A1AA'} />
      </div>
    </div>
  );
};

const Cell = ({ label, value, color, icon: I, shade }) => (
  <div className={`flex flex-col px-2 sm:px-3 py-2 border-r border-white/5 last:border-r-0 ${shade ? 'bg-[#0E0E10]' : ''}`}>
    <div className="flex items-center gap-0.5 text-[7px] sm:text-[8px] font-bold uppercase tracking-[0.18em] text-zinc-500">
      {I && <I className="w-2.5 h-2.5 shrink-0" />}
      <span className="truncate">{label}</span>
    </div>
    <span
      className="text-[11px] sm:text-sm font-mono font-bold mt-0.5 truncate"
      style={{ color: color || '#FFFFFF' }}
    >
      {typeof value === 'number' ? value.toFixed(2) : (value ?? '—')}
    </span>
  </div>
);

export default KronosForecastPanel;
