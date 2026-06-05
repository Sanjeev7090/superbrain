import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import {
  ComposedChart, Bar, Line, XAxis, YAxis, Tooltip,
  ResponsiveContainer, ReferenceLine, Cell, CartesianGrid
} from 'recharts';
import { ChartBar } from '@phosphor-icons/react';
import { toast } from 'sonner';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

// ─── colour constants ───────────────────────────────────────────────
const C = {
  buy:    '#00E676',
  sell:   '#FF3B30',
  wait:   '#FFCC00',
  poc:    '#FF6B00',
  vah:    '#A855F7',
  val:    '#06B6D4',
  cvd:    '#818CF8',
  neutral:'#555',
  bg:     '#0A0A0A',
};

// ─── tiny helpers ───────────────────────────────────────────────────
const sigColor  = s => s === 'BUY' ? C.buy : s === 'SELL' ? C.sell : C.wait;
const fmtNum    = n => (n == null ? '—' : Number(n).toLocaleString('en-IN', { maximumFractionDigits: 2 }));
const fmtK      = n => n > 1e6 ? `${(n/1e6).toFixed(2)}M` : n > 1e3 ? `${(n/1e3).toFixed(1)}K` : n?.toFixed(1);
const pctBar    = (val, total, col) => (
  <div className="h-1 bg-zinc-800 rounded-full overflow-hidden mt-0.5">
    <div style={{ width: `${Math.min(100, val/total*100)}%`, backgroundColor: col }} className="h-full rounded-full" />
  </div>
);

// ─── Signal Header ──────────────────────────────────────────────────
const SignalHeader = ({ d }) => {
  const col = sigColor(d.signal_type);
  return (
    <div className="px-3 py-2 border-b border-white/10 space-y-2">
      {/* Row 1: Signal badge + Buy/Sell + Confidence */}
      <div className="flex items-center gap-2 flex-wrap">
        {/* Signal badge */}
        <div className="flex flex-col items-center justify-center px-3 py-1.5 border shrink-0"
          style={{ borderColor: col + '60', backgroundColor: col + '12' }}>
          <span className="text-[8px] font-mono text-zinc-500 uppercase">Signal</span>
          <span className="text-base font-black" style={{ color: col }}>{d.signal_type}</span>
          <span className="text-[8px] font-mono" style={{ color: col + 'aa' }}>{d.signal_strength}</span>
        </div>

        {/* Buy/Sell pressure bar */}
        <div className="flex flex-col gap-0.5 flex-1 min-w-[90px]">
          <div className="flex justify-between text-[9px]">
            <span className="text-[#00E676]">Buy {d.buy_pct}%</span>
            <span className="text-[#FF3B30]">Sell {d.sell_pct}%</span>
          </div>
          <div className="h-2.5 bg-zinc-800 rounded-sm overflow-hidden flex">
            <div style={{ width: `${d.buy_pct}%`, backgroundColor: C.buy }} className="h-full" />
            <div style={{ width: `${d.sell_pct}%`, backgroundColor: C.sell }} className="h-full" />
          </div>
          <div className="flex justify-between text-[8px] font-mono text-zinc-600">
            <span>Δ {d.current_delta >= 0 ? '+' : ''}{fmtK(d.current_delta)}</span>
            <span>CVD {d.cvd_slope}</span>
          </div>
        </div>

        {/* Confidence */}
        <div className="flex flex-col items-center justify-center shrink-0">
          <span className="text-[8px] text-zinc-500 font-mono">Conf</span>
          <span className="text-lg font-black" style={{ color: col }}>{d.confidence}%</span>
        </div>
      </div>

      {/* Row 2: Entry/SL/Targets + Key Levels */}
      <div className="flex flex-wrap gap-1.5 items-start">
        {/* Levels pills */}
        {d.signal_type !== 'WAIT' && [
          { label: 'Entry', val: d.entry_price, col: '#fff' },
          { label: 'SL',   val: d.stop_loss,   col: C.sell },
          { label: 'T1',   val: d.target1,     col: C.buy  },
          { label: 'T2',   val: d.target2,     col: C.buy  },
        ].map(({ label, val, col: c }) => val && (
          <div key={label} className="flex flex-col items-center px-2 py-1 bg-white/5 border border-white/5">
            <span className="text-[8px] font-mono text-zinc-500">{label}</span>
            <span className="text-[11px] font-mono font-bold" style={{ color: c }}>{fmtNum(val)}</span>
          </div>
        ))}
        {d.risk_reward && d.signal_type !== 'WAIT' && (
          <div className="flex flex-col items-center px-2 py-1 bg-white/5 border border-white/5">
            <span className="text-[8px] font-mono text-zinc-500">R:R</span>
            <span className="text-[11px] font-mono font-bold text-white">{d.risk_reward}</span>
          </div>
        )}
        {/* Key levels inline */}
        <div className="flex gap-2 ml-auto text-[9px] font-mono">
          {[
            { label: 'POC', val: d.poc_price, col: C.poc },
            { label: 'VAH', val: d.vah_price, col: C.vah },
            { label: 'VAL', val: d.val_price, col: C.val },
          ].map(({ label, val, col: c }) => (
            <div key={label} className="flex flex-col items-center">
              <span style={{ color: c }} className="text-[8px]">{label}</span>
              <span className="text-white font-bold">{fmtNum(val)}</span>
            </div>
          ))}
        </div>
        {d.divergence !== 'NONE' && (
          <span className="text-[8px] font-bold px-1.5 py-0.5 rounded"
            style={{ color: d.divergence === 'BULLISH_DIV' ? C.buy : C.sell, backgroundColor: (d.divergence === 'BULLISH_DIV' ? C.buy : C.sell) + '20' }}>
            ⚡ {d.divergence === 'BULLISH_DIV' ? 'Bull Div' : 'Bear Div'}
          </span>
        )}
      </div>
    </div>
  );
};

// ─── Volume Profile (horizontal SVG bars) ──────────────────────────
const VolumeProfile = ({ bins, poc, vah, val, height = 260 }) => {
  if (!bins?.length) return null;
  const maxVol = Math.max(...bins.map(b => b.total_vol));
  const barW = 120;  // max bar width in px
  const rowH = height / bins.length;
  const reversed = [...bins].reverse();  // highest price at top

  return (
    <div className="flex flex-col">
      <p className="text-[9px] font-bold uppercase tracking-wider text-zinc-500 mb-1 px-1">
        Volume Profile <span style={{ color: C.poc }}>● POC</span>
        &nbsp;<span style={{ color: C.vah }}>▲ VAH</span>
        &nbsp;<span style={{ color: C.val }}>▼ VAL</span>
      </p>
      <div style={{ height: `${height}px`, overflowY: 'auto', position: 'relative' }}
           className="bg-zinc-900/50 border border-white/5 rounded">
        {reversed.map((bin, i) => {
          const buyW  = (bin.buy_vol  / maxVol) * barW;
          const sellW = (bin.sell_vol / maxVol) * barW;
          const isPOC = bin.is_poc;
          const isVA  = bin.in_value_area;
          const isVAH = Math.abs(bin.price_mid - vah) < (reversed[0].price_high - reversed[0].price_low);
          const isVAL = Math.abs(bin.price_mid - val) < (reversed[0].price_high - reversed[0].price_low);

          return (
            <div key={i}
              style={{ height: `${rowH}px`, minHeight: 8 }}
              className={`flex items-center px-1 gap-0.5 border-b border-white/5
                ${isPOC ? 'bg-orange-500/10' : isVA ? 'bg-white/[0.02]' : ''}
                ${isVAH ? 'border-t border-purple-500/40' : ''}
                ${isVAL ? 'border-b border-cyan-500/40' : ''}`}
              title={`${bin.price_mid.toFixed(2)} | Vol: ${fmtK(bin.total_vol)}`}
            >
              {/* Price label */}
              <span className="text-[7px] font-mono w-[46px] shrink-0"
                style={{ color: isPOC ? C.poc : isVAH ? C.vah : isVAL ? C.val : '#555' }}>
                {bin.price_mid.toFixed(2)}{isPOC ? ' ◆' : ''}
              </span>
              {/* Buy bar */}
              <div style={{ width: `${buyW}px`, minWidth: buyW > 0 ? 1 : 0, backgroundColor: C.buy + (isVA ? 'cc' : '77') }}
                   className="h-[60%] rounded-sm shrink-0" />
              {/* Sell bar */}
              <div style={{ width: `${sellW}px`, minWidth: sellW > 0 ? 1 : 0, backgroundColor: C.sell + (isVA ? 'cc' : '77') }}
                   className="h-[60%] rounded-sm shrink-0" />
            </div>
          );
        })}
      </div>
    </div>
  );
};

// ─── Footprint Table (last N candles) ──────────────────────────────
const FootprintView = ({ footprint }) => {
  if (!footprint?.length) return null;
  return (
    <div className="flex gap-1 min-w-max">
      {footprint.map((candle, ci) => {
            const col = candle.bullish ? C.buy : C.sell;
            const reversedLevels = [...candle.levels].reverse();
            const maxLevVol = Math.max(...candle.levels.map(l => l.buy_vol + l.sell_vol)) || 1;
            return (
              <div key={ci} className="flex flex-col shrink-0"
                   style={{ minWidth: 64, borderLeft: `2px solid ${col}30` }}>
                {/* Candle header */}
                <div className="px-1 py-0.5 text-center"
                     style={{ backgroundColor: col + '15' }}>
                  <div className="text-[7px] font-mono font-bold" style={{ color: col }}>
                    {candle.bullish ? '▲' : '▼'} {fmtNum(candle.close)}
                  </div>
                  <div className="text-[6px] text-zinc-600 font-mono">
                    Δ{candle.total_delta >= 0 ? '+' : ''}{fmtK(candle.total_delta)}
                  </div>
                </div>
                {/* Price levels */}
                {reversedLevels.map((lv, li) => {
                  const totalLv = lv.buy_vol + lv.sell_vol || 1;
                  const imb = Math.abs(lv.imbalance_pct);
                  const imbColor = lv.delta > 0 ? C.buy : C.sell;
                  return (
                    <div key={li}
                      className="px-0.5 py-px flex gap-0.5 items-center border-b border-white/5"
                      style={{ backgroundColor: imb > 60 ? imbColor + '15' : 'transparent' }}>
                      <span className="text-[6px] font-mono text-zinc-600 w-[30px] shrink-0">
                        {lv.price.toFixed(1)}
                      </span>
                      <span className="text-[7px] font-mono" style={{ color: C.buy, minWidth: 20 }}>
                        {fmtK(lv.buy_vol)}
                      </span>
                      <span className="text-[7px] font-mono text-zinc-600">×</span>
                      <span className="text-[7px] font-mono" style={{ color: C.sell, minWidth: 20 }}>
                        {fmtK(lv.sell_vol)}
                      </span>
                    </div>
                  );
                })}
                {/* Total vol */}
                <div className="px-1 py-0.5 text-[7px] font-mono text-center text-zinc-500">
                  {fmtK(candle.total_volume)}
                </div>
              </div>
            );
          })}
    </div>
  );
};

// ─── CVD + Delta chart ──────────────────────────────────────────────
const DeltaChart = ({ candles }) => {
  if (!candles?.length) return null;
  const data = candles.slice(-60).map((c, i) => ({
    i,
    delta: Math.round(c.delta),
    cvd:   Math.round(c.cvd),
    ts:    c.timestamp
  }));

  const CustomTooltip = ({ active, payload }) => {
    if (!active || !payload?.length) return null;
    return (
      <div className="bg-zinc-900 border border-white/10 px-2 py-1 text-[9px] font-mono">
        <p style={{ color: C.cvd }}>CVD: {fmtK(payload[0]?.value)}</p>
        <p style={{ color: payload[1]?.value > 0 ? C.buy : C.sell }}>Δ: {fmtK(payload[1]?.value)}</p>
      </div>
    );
  };

  return (
    <div className="flex flex-col">
      <div className="flex gap-3 text-[9px] font-mono mb-1 px-1">
        <span style={{ color: C.cvd }}>━ CVD</span>
        <span style={{ color: C.buy }}>▌ +Delta</span>
        <span style={{ color: C.sell }}>▌ −Delta</span>
      </div>
      <ResponsiveContainer width="100%" height={110}>
        <ComposedChart data={data} margin={{ top: 4, right: 4, left: -20, bottom: 0 }}>
          <CartesianGrid strokeDasharray="2 3" stroke="#ffffff08" />
          <XAxis dataKey="i" hide />
          <YAxis yAxisId="cvd" orientation="left" tick={{ fontSize: 7, fill: '#555' }} tickFormatter={v => fmtK(v)} />
          <YAxis yAxisId="delta" orientation="right" tick={{ fontSize: 7, fill: '#555' }} tickFormatter={v => fmtK(v)} />
          <Tooltip content={<CustomTooltip />} />
          <ReferenceLine yAxisId="delta" y={0} stroke="#ffffff20" strokeDasharray="2 2" />
          <Line yAxisId="cvd" type="monotone" dataKey="cvd" stroke={C.cvd} strokeWidth={1.5} dot={false} />
          <Bar yAxisId="delta" dataKey="delta" maxBarSize={6} radius={[1,1,0,0]}>
            {data.map((d, i) => (
              <Cell key={i} fill={d.delta >= 0 ? C.buy : C.sell} fillOpacity={0.85} />
            ))}
          </Bar>
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  );
};

// ─── Main Component ─────────────────────────────────────────────────
export default function OrderFlowPanel({ stockData, selectedStock }) {
  const [loading, setLoading] = useState(false);
  const [data, setData]       = useState(null);

  const hasData = stockData?.bars?.length >= 30;

  const analyze = useCallback(async () => {
    if (!stockData?.bars?.length) return;
    setLoading(true);
    try {
      const resp = await axios.post(`${API}/orderflow/analyze`, {
        ticker:       selectedStock?.ticker || selectedStock?.symbol || '?',
        bars:         stockData.bars,
        n_vp_bins:    24,
        n_fp_levels:  8,
        vp_lookback:  50,
      });
      setData(resp.data);
    } catch (e) {
      toast.error(e.response?.data?.detail || 'Footprint analysis failed');
    } finally {
      setLoading(false);
    }
  }, [stockData, selectedStock]);

  // Auto-fetch when stock or data changes
  useEffect(() => {
    setData(null);
    if (stockData?.bars?.length >= 30) {
      analyze();
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedStock, stockData]);

  return (
    <div className="border-t border-white/10 bg-[#0A0A0A] shrink-0 flex flex-col relative z-20" data-testid="footprint-panel">

      {/* ── Header ── */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-white/5">
        <div className="flex items-center gap-2">
          <ChartBar size={14} weight="bold" className="text-[#818CF8]" />
          <span className="text-[10px] font-bold uppercase tracking-[0.2em] text-zinc-400">
            Footprint
          </span>
          <span className="text-[8px] text-zinc-600 hidden sm:inline">· CVD · Delta</span>
          {data && (
            <span className="text-[8px] font-mono px-1 py-0.5 rounded"
              style={{ color: sigColor(data.signal_type), backgroundColor: sigColor(data.signal_type) + '20' }}>
              {data.signal_type}
            </span>
          )}
        </div>
        {data && (
          <button
            onClick={() => { setData(null); analyze(); }}
            className="text-[9px] font-mono text-zinc-600 hover:text-white px-1 py-1"
            title="Refresh"
            data-testid="footprint-refresh"
          >↻</button>
        )}
      </div>

      {/* ── Content ── */}
      <div className="overflow-y-auto" style={{ maxHeight: '38vh' }}>
        {loading && (
          <div className="flex items-center justify-center py-5 gap-2">
            <div className="w-1 h-1 bg-[#818CF8] rounded-full animate-bounce" />
            <div className="w-1 h-1 bg-[#818CF8] rounded-full animate-bounce delay-75" />
            <div className="w-1 h-1 bg-[#818CF8] rounded-full animate-bounce delay-150" />
            <span className="text-[10px] font-mono text-zinc-500 ml-1">Loading footprint…</span>
          </div>
        )}

        {!loading && !data && !hasData && (
          <p className="text-[10px] text-zinc-600 px-3 py-3">Select a stock and load chart data first.</p>
        )}

        {!loading && data && (
          <div className="animate-fade-in">
            {/* Signal row */}
            <SignalHeader d={data} />

            {/* Main grid */}
            <div className="p-2 space-y-2">
              {/* Footprint — horizontal scroll on mobile */}
              <div className="flex flex-col">
                <p className="text-[9px] font-bold uppercase tracking-wider text-zinc-500 mb-1 px-1">
                  Footprint (last {data.footprint?.length} candles)
                </p>
                <div className="overflow-x-auto -mx-1 px-1">
                  <FootprintView footprint={data.footprint} />
                </div>
              </div>

              {/* CVD + Delta chart */}
              <div>
                <p className="text-[9px] font-bold uppercase tracking-wider text-zinc-500 mb-1 px-1">
                  CVD + Delta (last 60 bars)
                </p>
                <DeltaChart candles={data.candles} />
              </div>
            </div>

            {/* Recommendation */}
            <div className="px-3 pb-2">
              <p className="text-[9px] text-zinc-500 leading-relaxed border border-white/5 bg-white/[0.02] p-2">
                {data.recommendation}
              </p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
