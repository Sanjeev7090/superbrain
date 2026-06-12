/**
 * MonteCarloPanel — Trading Strategy Validation via Monte Carlo Simulation
 *
 * Shows:
 *  • Key stats: Win Probability, Mean Return, Risk of Ruin, Worst Drawdown
 *  • 50 sample equity curves (fan chart)
 *  • Return distribution histogram
 */
import React, { useState, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import {
  AreaChart, Area, LineChart, Line, BarChart, Bar,
  XAxis, YAxis, Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts';
import { Play, RefreshCw, TrendingUp, TrendingDown, AlertTriangle, Info } from 'lucide-react';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const STAT_CARDS = [
  { key: 'win_probability',  label: 'Win Prob',      unit: '%',  good: v => v >= 55, icon: TrendingUp },
  { key: 'mean_return',      label: 'Mean Return',   unit: '%',  good: v => v > 0,   icon: TrendingUp },
  { key: 'median_return',    label: 'Median Return', unit: '%',  good: v => v > 0,   icon: TrendingUp },
  { key: 'percentile_5',    label: 'P5 Return',     unit: '%',  good: v => v > -10, icon: TrendingDown },
  { key: 'percentile_95',   label: 'P95 Return',    unit: '%',  good: v => v > 5,   icon: TrendingUp },
  { key: 'avg_max_drawdown', label: 'Avg Max DD',    unit: '%',  good: v => v > -20, icon: AlertTriangle },
  { key: 'worst_drawdown',   label: 'Worst DD',      unit: '%',  good: v => v > -40, icon: AlertTriangle },
  { key: 'risk_of_ruin',     label: 'Risk of Ruin',  unit: '%',  good: v => v < 5,   icon: AlertTriangle },
];

const PALETTE_UP   = '#34d399';
const PALETTE_DOWN = '#f87171';
const PALETTE_WARN = '#fbbf24';

function fmtNum(v) {
  if (v == null) return '—';
  const s = v >= 0 ? '+' : '';
  return s + v.toFixed(2);
}

// Thin equity curves for fan chart
function buildFanData(curves, initial_capital) {
  if (!curves?.length) return [];
  const len = Math.min(...curves.map(c => c.length));
  return Array.from({ length: len }, (_, i) => {
    const obj = { step: i };
    curves.forEach((c, ci) => {
      obj[`c${ci}`] = ((c[i] / initial_capital) - 1) * 100;
    });
    return obj;
  });
}

export default function MonteCarloPanel({ initialCapital = 100000 }) {
  const [data,    setData]    = useState(null);
  const [loading, setLoading] = useState(false);

  const runSim = useCallback(async () => {
    setLoading(true);
    try {
      const res = await axios.post(
        `${API}/robo/monte-carlo`,
        null,
        { params: { initial_capital: initialCapital, simulations: 1000 } }
      );
      setData(res.data);
      if (res.data.using_demo) {
        toast.info('No closed trades found — showing demo simulation with synthetic data', { duration: 4000 });
      } else {
        toast.success(`Monte Carlo complete — ${res.data.trade_count} trades analysed`, { duration: 3000 });
      }
    } catch (e) {
      toast.error('Simulation failed: ' + (e.response?.data?.detail || e.message));
    } finally {
      setLoading(false);
    }
  }, [initialCapital]);

  const s   = data?.summary;
  const fan = data ? buildFanData(data.sample_equity_curves, data.summary.initial_capital) : [];

  return (
    <div
      className="rounded-xl border border-zinc-800 overflow-hidden"
      style={{ background: 'rgba(9,9,11,0.95)' }}
      data-testid="monte-carlo-panel"
    >
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2.5 border-b border-zinc-800">
        <div className="flex items-center gap-2">
          <div
            className="w-5 h-5 rounded flex items-center justify-center"
            style={{ background: 'rgba(139,92,246,0.15)', border: '1px solid rgba(139,92,246,0.3)' }}
          >
            <TrendingUp size={10} className="text-violet-400" />
          </div>
          <span className="text-[11px] font-black text-white tracking-wide uppercase">Monte Carlo</span>
          <span className="text-[8px] text-zinc-600 font-mono">1000 paths</span>
          {data?.using_demo && (
            <span className="text-[7px] px-1 py-0.5 rounded font-bold"
              style={{ background: 'rgba(251,191,36,0.15)', color: '#fbbf24', border: '1px solid rgba(251,191,36,0.3)' }}>
              DEMO
            </span>
          )}
        </div>
        <button
          data-testid="monte-carlo-run-btn"
          onClick={runSim}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[9px] font-black transition-all disabled:opacity-50"
          style={{
            background: loading ? 'rgba(107,114,128,0.2)' : 'rgba(139,92,246,0.2)',
            color: loading ? '#6b7280' : '#c4b5fd',
            border: `1px solid ${loading ? 'rgba(107,114,128,0.3)' : 'rgba(139,92,246,0.4)'}`,
          }}
        >
          {loading
            ? <><span className="w-2.5 h-2.5 border border-t-violet-400 border-zinc-600 rounded-full animate-spin" /> RUNNING…</>
            : <><Play size={9} fill="currentColor" /> RUN SIM</>
          }
        </button>
      </div>

      {!data && !loading && (
        <div className="flex flex-col items-center justify-center py-8 gap-2">
          <Info size={20} className="text-zinc-700" />
          <p className="text-[10px] text-zinc-600">Click RUN SIM to validate your strategy</p>
          <p className="text-[8px] text-zinc-700 text-center max-w-[200px]">
            Shuffles trade order, applies slippage & skip-rate across 1000 paths
          </p>
        </div>
      )}

      {loading && (
        <div className="flex flex-col items-center justify-center py-8 gap-2">
          <span className="w-6 h-6 border-2 border-t-violet-400 border-zinc-700 rounded-full animate-spin" />
          <p className="text-[10px] text-zinc-500">Running 1000 simulations…</p>
        </div>
      )}

      {data && !loading && (
        <div className="px-3 py-3 space-y-3">

          {/* Stats grid */}
          <div className="grid grid-cols-4 gap-1.5">
            {STAT_CARDS.map(({ key, label, unit, good }) => {
              const val = s?.[key];
              const ok  = val != null ? good(val) : null;
              const col = ok === true ? PALETTE_UP : ok === false ? PALETTE_DOWN : PALETTE_WARN;
              return (
                <div
                  key={key}
                  data-testid={`mc-stat-${key}`}
                  className="rounded-lg p-2 text-center"
                  style={{ background: `${col}0d`, border: `1px solid ${col}22` }}
                >
                  <p className="text-[8px] text-zinc-500 mb-0.5 leading-tight">{label}</p>
                  <p className="text-[11px] font-black font-mono" style={{ color: col }}>
                    {val != null ? `${fmtNum(val)}${unit}` : '—'}
                  </p>
                </div>
              );
            })}
          </div>

          {/* Fan chart — equity curves */}
          {fan.length > 0 && (
            <div>
              <p className="text-[8px] text-zinc-600 mb-1 uppercase tracking-wider">Equity Curves (50 paths)</p>
              <div style={{ height: 120 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={fan} margin={{ top: 2, right: 4, left: -20, bottom: 0 }}>
                    <XAxis dataKey="step" tick={false} axisLine={false} />
                    <YAxis
                      tick={{ fontSize: 8, fill: '#52525b' }}
                      tickFormatter={v => `${v > 0 ? '+' : ''}${v.toFixed(0)}%`}
                      axisLine={false} tickLine={false}
                    />
                    <ReferenceLine y={0} stroke="#3f3f46" strokeDasharray="3 3" />
                    <Tooltip
                      contentStyle={{ background: '#18181b', border: '1px solid #3f3f46', borderRadius: 6, fontSize: 9 }}
                      formatter={(v) => [`${v?.toFixed(2)}%`, '']}
                    />
                    {data.sample_equity_curves.map((_, ci) => (
                      <Line
                        key={`c${ci}`}
                        dataKey={`c${ci}`}
                        stroke={ci % 3 === 0 ? PALETTE_UP : ci % 3 === 1 ? PALETTE_DOWN : '#a78bfa'}
                        strokeWidth={0.6}
                        dot={false}
                        opacity={0.35}
                      />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {/* Return distribution histogram */}
          {data.histogram?.length > 0 && (
            <div>
              <p className="text-[8px] text-zinc-600 mb-1 uppercase tracking-wider">Return Distribution</p>
              <div style={{ height: 90 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={data.histogram} margin={{ top: 2, right: 4, left: -20, bottom: 0 }}>
                    <XAxis
                      dataKey="midpoint"
                      tick={{ fontSize: 7, fill: '#52525b' }}
                      tickFormatter={v => `${v > 0 ? '+' : ''}${v}%`}
                    />
                    <YAxis tick={{ fontSize: 7, fill: '#52525b' }} axisLine={false} tickLine={false} />
                    <ReferenceLine x={0} stroke="#3f3f46" strokeDasharray="3 3" />
                    <Tooltip
                      contentStyle={{ background: '#18181b', border: '1px solid #3f3f46', borderRadius: 6, fontSize: 9 }}
                      formatter={(v, n, p) => [v + ' sims', p.payload?.range]}
                    />
                    <Bar dataKey="count" radius={[2, 2, 0, 0]}
                      fill={PALETTE_UP}
                      // Color bars based on positive/negative
                      label={false}
                    />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </div>
          )}

          {/* Footer */}
          <p className="text-[7px] text-zinc-700 text-center">
            {data.trade_count} trades · slippage 0.08% · commission 0.05% · skip 8% · {data.summary.simulations} paths
          </p>
        </div>
      )}
    </div>
  );
}
