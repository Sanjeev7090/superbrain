import React, { useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const SIGNAL_CONFIG = {
  STRONGLY_BULLISH:  { label: 'Strongly Bullish', color: '#00E676', bg: 'bg-emerald-500/15 border-emerald-500/30', text: 'text-emerald-400', bar: '#00E676' },
  BULLISH:           { label: 'Bullish',           color: '#4ADE80', bg: 'bg-emerald-500/10 border-emerald-500/20', text: 'text-emerald-400', bar: '#4ADE80' },
  NEUTRAL:           { label: 'Neutral',            color: '#FCD34D', bg: 'bg-yellow-500/10  border-yellow-500/20',  text: 'text-yellow-400',  bar: '#FCD34D' },
  BEARISH:           { label: 'Bearish',            color: '#F87171', bg: 'bg-red-500/10     border-red-500/20',     text: 'text-red-400',     bar: '#F87171' },
  STRONGLY_BEARISH:  { label: 'Strongly Bearish',   color: '#EF4444', bg: 'bg-red-500/15     border-red-500/30',     text: 'text-red-400',     bar: '#EF4444' },
};

/** Semicircle gauge — value 0-2 mapped to 0°-180° */
const SemiGauge = ({ pcr = 1.0, size = 100 }) => {
  const r   = size * 0.38;
  const cx  = size / 2;
  const cy  = size * 0.62;
  const min = 0.4, max = 2.0;
  const clamped = Math.max(min, Math.min(max, pcr));
  const angle   = ((clamped - min) / (max - min)) * 180 - 90; // -90° to +90°
  const rad     = (angle * Math.PI) / 180;
  const nx      = cx + r * Math.cos(rad);
  const ny      = cy + r * Math.sin(rad);

  // Arc segments colours
  const segs = [
    { from: -90, to: -54, color: '#EF4444' },  // strongly bearish
    { from: -54, to: -18, color: '#F87171' },  // bearish
    { from: -18, to: 18,  color: '#FCD34D' },  // neutral
    { from: 18,  to: 54,  color: '#4ADE80' },  // bullish
    { from: 54,  to: 90,  color: '#00E676' },  // strongly bullish
  ];

  const arcPath = (fromDeg, toDeg, ri) => {
    const f  = (fromDeg * Math.PI) / 180;
    const t  = (toDeg   * Math.PI) / 180;
    const x1 = cx + ri * Math.cos(f), y1 = cy + ri * Math.sin(f);
    const x2 = cx + ri * Math.cos(t), y2 = cy + ri * Math.sin(t);
    return `M ${x1} ${y1} A ${ri} ${ri} 0 0 1 ${x2} ${y2}`;
  };

  return (
    <svg width={size} height={size * 0.65} viewBox={`0 0 ${size} ${size * 0.65}`}>
      {/* Track */}
      {segs.map((s, i) => (
        <path key={i} d={arcPath(s.from, s.to, r)} fill="none" stroke={s.color} strokeWidth={8} strokeLinecap="round" opacity={0.35} />
      ))}
      {/* Active filled arc */}
      {(() => {
        const startDeg = -90;
        if (angle <= startDeg) return null;
        const f = (startDeg * Math.PI) / 180;
        const t = (angle    * Math.PI) / 180;
        const x1 = cx + r * Math.cos(f), y1 = cy + r * Math.sin(f);
        const x2 = cx + r * Math.cos(t), y2 = cy + r * Math.sin(t);
        const lg = angle - startDeg > 180 ? 1 : 0;
        const sig = angle >= startDeg ? 1 : 0;
        const sigColor = pcr >= 1.1 ? '#00E676' : pcr >= 0.85 ? '#FCD34D' : '#EF4444';
        return (
          <path
            d={`M ${x1} ${y1} A ${r} ${r} 0 ${lg} ${sig} ${x2} ${y2}`}
            fill="none" stroke={sigColor} strokeWidth={8} strokeLinecap="round"
          />
        );
      })()}
      {/* Needle */}
      <line x1={cx} y1={cy} x2={nx} y2={ny} stroke="white" strokeWidth={2} strokeLinecap="round" />
      <circle cx={cx} cy={cy} r={4} fill="white" />
      {/* PCR value */}
      <text x={cx} y={cy - r * 0.3} textAnchor="middle" fill="white" fontSize={size * 0.12} fontWeight="bold" fontFamily="monospace">
        {pcr.toFixed(2)}
      </text>
    </svg>
  );
};

const PCRBar = ({ label, value, max = 2.0 }) => {
  const pct = Math.min(100, (value / max) * 100);
  const color = value >= 1.1 ? '#00E676' : value >= 0.85 ? '#FCD34D' : '#EF4444';
  return (
    <div>
      <div className="flex justify-between text-[9px] text-zinc-500 mb-1">
        <span>{label}</span>
        <span className="font-mono" style={{ color }}>{value.toFixed(2)}</span>
      </div>
      <div className="h-1.5 bg-zinc-800 rounded-full overflow-hidden">
        <div className="h-full rounded-full transition-all duration-700" style={{ width: `${pct}%`, backgroundColor: color }} />
      </div>
    </div>
  );
};

export default function PCRGauge({ symbol }) {
  const [data, setData]     = useState(null);
  const [loading, setLoading] = useState(true);
  const intervalRef = useRef(null);

  const fetch = useCallback(async () => {
    if (!symbol) return;
    try {
      const res = await axios.get(`${API}/indices/pcr/${symbol}`);
      setData(res.data);
    } catch { /* silent */ }
    finally { setLoading(false); }
  }, [symbol]);

  useEffect(() => {
    setLoading(true);
    setData(null);
    fetch();
    intervalRef.current = setInterval(fetch, 60000);
    return () => clearInterval(intervalRef.current);
  }, [symbol, fetch]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-4">
        <span className="w-4 h-4 border border-zinc-700 border-t-violet-400 rounded-full animate-spin" />
      </div>
    );
  }

  if (!data) return null;

  const cfg = SIGNAL_CONFIG[data.signal] || SIGNAL_CONFIG.NEUTRAL;

  return (
    <div className="rounded-xl border border-white/10 bg-zinc-900/60 p-3" data-testid="pcr-gauge">
      {/* Title row */}
      <div className="flex items-center justify-between mb-2">
        <span className="text-[10px] font-bold text-zinc-400 uppercase tracking-wider">
          PCR — {symbol}
        </span>
        <div className={`flex items-center gap-1 px-2 py-0.5 rounded-full border text-[9px] font-bold uppercase tracking-wider ${cfg.bg} ${cfg.text}`}>
          <span className="w-1.5 h-1.5 rounded-full inline-block" style={{ backgroundColor: cfg.color }} />
          {cfg.label}
        </div>
      </div>

      {/* Gauge + bars */}
      <div className="flex items-start gap-4">
        <div className="shrink-0">
          <SemiGauge pcr={data.oi_pcr} size={96} />
          <p className="text-[8px] text-zinc-600 text-center mt-0.5">OI PCR</p>
        </div>

        <div className="flex-1 space-y-2 pt-1">
          <PCRBar label="OI PCR"     value={data.oi_pcr}  />
          <PCRBar label="Volume PCR" value={data.vol_pcr} />
          <PCRBar label="ATM PCR"    value={data.atm_pcr} />

          <div className="grid grid-cols-2 gap-x-3 pt-1">
            <div className="text-[8px] text-zinc-600">
              Call OI <span className="font-mono text-zinc-400">{(data.total_call_oi / 1e5).toFixed(1)}L</span>
            </div>
            <div className="text-[8px] text-zinc-600">
              Put OI <span className="font-mono text-zinc-400">{(data.total_put_oi  / 1e5).toFixed(1)}L</span>
            </div>
          </div>

          {data.india_vix != null && (
            <div className="text-[8px] text-zinc-600 pt-0.5">
              India VIX <span className="font-mono text-cyan-400">{data.india_vix.toFixed(1)}%</span>
              {data.is_live_derived && <span className="ml-1 text-zinc-700">· Live-Derived</span>}
            </div>
          )}
        </div>
      </div>

      {/* Signal interpretation */}
      <div className={`mt-2 px-2 py-1.5 rounded-lg border text-[9px] leading-snug ${cfg.bg} ${cfg.text}`}>
        {data.signal === 'STRONGLY_BULLISH' && 'PCR > 1.3 — Heavy put buying. Market is oversold. Strong bullish reversal signal.'}
        {data.signal === 'BULLISH'          && 'PCR 1.1–1.3 — More puts than calls. Sentiment tilted bullish for index.'}
        {data.signal === 'NEUTRAL'          && 'PCR 0.85–1.1 — Balanced call/put activity. Market awaiting direction.'}
        {data.signal === 'BEARISH'          && 'PCR 0.65–0.85 — More calls than puts. Sentiment tilted bearish.'}
        {data.signal === 'STRONGLY_BEARISH' && 'PCR < 0.65 — Heavy call buying. Market is overbought. Bearish signal.'}
      </div>

      <p className="text-[8px] text-zinc-700 text-right mt-1.5 font-mono">
        Updates every 60s · {new Date(data.updated_at).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}
      </p>
    </div>
  );
}
