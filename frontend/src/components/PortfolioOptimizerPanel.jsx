/**
 * PortfolioOptimizerPanel — Mean-Variance, Black-Litterman, Kelly, CVaR, Efficient Frontier
 */
import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import {
  LineChart, Line, ScatterChart, Scatter, XAxis, YAxis,
  CartesianGrid, Tooltip, ResponsiveContainer, ReferenceDot,
} from 'recharts';
import { TrendingUp, RefreshCw, PieChart, Target } from 'lucide-react';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const NIFTY50_STOCKS = [
  'RELIANCE.NS','TCS.NS','HDFCBANK.NS','INFY.NS','ICICIBANK.NS',
  'HINDUNILVR.NS','ITC.NS','SBIN.NS','BHARTIARTL.NS','KOTAKBANK.NS',
];

const pct = v => `${(v * 100).toFixed(1)}%`;
const fmt2 = v => Number(v).toFixed(2);

function WeightBar({ name, weight, color }) {
  return (
    <div className="flex items-center gap-2 text-[11px]">
      <span className="text-zinc-400 w-28 truncate">{name.replace('.NS','')}</span>
      <div className="flex-1 bg-zinc-800 rounded-full h-1.5 overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{ width: `${Math.min(weight * 100 * 2.5, 100)}%`, background: color }}
        />
      </div>
      <span className="text-zinc-200 w-12 text-right tabular-nums">{pct(weight)}</span>
    </div>
  );
}

const COLORS = ['#3b82f6','#10b981','#f59e0b','#ef4444','#8b5cf6','#06b6d4','#ec4899','#84cc16','#f97316','#a78bfa'];

export default function PortfolioOptimizerPanel() {
  const [tickers, setTickers] = useState(NIFTY50_STOCKS.slice(0, 5).join('\n'));
  const [method, setMethod]   = useState('mv');
  const [period, setPeriod]   = useState('1y');
  const [result, setResult]   = useState(null);
  const [frontier, setFrontier] = useState([]);
  const [loading, setLoading] = useState(false);
  const [tab, setTab]         = useState('weights');  // weights | frontier | kelly | cvar

  const optimize = useCallback(async () => {
    const tkList = tickers.split(/[\n,\s]+/).map(t => t.trim()).filter(Boolean);
    if (tkList.length < 2) { toast.error('Minimum 2 tickers required'); return; }
    setLoading(true);
    try {
      const [optRes, frRes] = await Promise.all([
        axios.post(`${API}/advanced/portfolio/optimize`, { tickers: tkList, method, period }),
        axios.post(`${API}/advanced/portfolio/frontier`, { tickers: tkList, period }),
      ]);
      setResult(optRes.data);
      setFrontier(frRes.data?.frontier || []);
      if (optRes.data.error) toast.error(optRes.data.error);
      else toast.success('Portfolio optimized!');
    } catch (e) {
      toast.error('Optimization failed');
    } finally {
      setLoading(false);
    }
  }, [tickers, method, period]);

  const weights  = result?.weights  || {};
  const kelly    = result?.kelly    || {};
  const cvar     = result?.cvar     || {};
  const frontier_pts = frontier.map(f => ({ x: (f.volatility * 100).toFixed(2), y: (f.return * 100).toFixed(2), sharpe: f.sharpe }));

  const optPoint = result ? {
    x: ((result.volatility || 0) * 100).toFixed(2),
    y: ((result.expected_return || 0) * 100).toFixed(2),
  } : null;

  return (
    <div className="space-y-4 p-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <PieChart size={16} className="text-blue-400" />
          <span className="text-[13px] font-bold text-white">Portfolio Optimizer</span>
        </div>
        <div className="flex items-center gap-1.5">
          {['mv','bl'].map(m => (
            <button key={m} onClick={() => setMethod(m)}
              className={`px-2 py-0.5 text-[10px] font-bold uppercase rounded transition-colors ${method === m ? 'bg-blue-600 text-white' : 'bg-zinc-800 text-zinc-400 hover:text-white'}`}
              data-testid={`method-${m}`}>
              {m === 'mv' ? 'Mean-Variance' : 'Black-Litterman'}
            </button>
          ))}
          {['6mo','1y','2y'].map(p => (
            <button key={p} onClick={() => setPeriod(p === '6mo' ? '6mo' : p)}
              className={`px-2 py-0.5 text-[10px] rounded transition-colors ${period === p ? 'bg-zinc-600 text-white' : 'bg-zinc-800 text-zinc-500 hover:text-white'}`}>
              {p.toUpperCase()}
            </button>
          ))}
        </div>
      </div>

      {/* Ticker input */}
      <div>
        <label className="text-[10px] text-zinc-500 uppercase tracking-widest block mb-1">Tickers (one per line)</label>
        <textarea
          value={tickers}
          onChange={e => setTickers(e.target.value)}
          rows={4}
          className="w-full bg-zinc-900 border border-zinc-700 rounded p-2 text-[11px] text-zinc-200 font-mono resize-none focus:outline-none focus:border-blue-500"
          placeholder="RELIANCE.NS&#10;TCS.NS&#10;INFY.NS"
          data-testid="ticker-input"
        />
      </div>

      <button
        onClick={optimize}
        disabled={loading}
        className="w-full py-2 text-[12px] font-bold bg-blue-600 hover:bg-blue-700 disabled:opacity-50 text-white rounded transition-colors flex items-center justify-center gap-2"
        data-testid="optimize-btn"
      >
        {loading ? <><RefreshCw size={13} className="animate-spin" /> Optimizing...</> : <><TrendingUp size={13} /> Optimize Portfolio</>}
      </button>

      {/* Summary strip */}
      {result && !result.error && (
        <div className="grid grid-cols-3 gap-2">
          {[
            { label: 'Expected Return', val: pct(result.expected_return || 0), color: '#10b981' },
            { label: 'Volatility', val: pct(result.volatility || 0), color: '#f59e0b' },
            { label: 'Sharpe Ratio', val: fmt2(result.sharpe || 0), color: '#3b82f6' },
          ].map(s => (
            <div key={s.label} className="bg-zinc-900/80 border border-zinc-800 rounded-lg p-2.5 text-center">
              <div className="text-[9px] text-zinc-500 uppercase tracking-widest">{s.label}</div>
              <div className="text-base font-black tabular-nums mt-0.5" style={{ color: s.color }}>{s.val}</div>
            </div>
          ))}
        </div>
      )}

      {/* Tabs */}
      {result && !result.error && (
        <>
          <div className="flex gap-1 border-b border-zinc-800">
            {['weights','frontier','kelly','cvar'].map(t => (
              <button key={t} onClick={() => setTab(t)}
                className={`px-3 py-1.5 text-[11px] font-bold uppercase tracking-wider border-b-2 transition-colors ${tab === t ? 'border-blue-500 text-blue-400' : 'border-transparent text-zinc-500 hover:text-white'}`}>
                {t}
              </button>
            ))}
          </div>

          {/* Weights tab */}
          {tab === 'weights' && (
            <div className="space-y-2">
              {Object.entries(weights).sort((a, b) => b[1] - a[1]).map(([t, w], i) => (
                <WeightBar key={t} name={t} weight={w} color={COLORS[i % COLORS.length]} />
              ))}
            </div>
          )}

          {/* Efficient Frontier tab */}
          {tab === 'frontier' && frontier_pts.length > 0 && (
            <div>
              <p className="text-[10px] text-zinc-500 mb-2">Efficient Frontier — Risk vs Return</p>
              <ResponsiveContainer width="100%" height={220}>
                <ScatterChart>
                  <CartesianGrid strokeDasharray="3 3" stroke="#333" />
                  <XAxis dataKey="x" name="Volatility %" label={{ value: 'Vol %', position: 'insideBottom', offset: -5 }} tick={{ fill: '#71717a', fontSize: 10 }} />
                  <YAxis dataKey="y" name="Return %" label={{ value: 'Ret %', angle: -90, position: 'insideLeft' }} tick={{ fill: '#71717a', fontSize: 10 }} />
                  <Tooltip cursor={false} content={({ active, payload }) => active && payload?.length ? (
                    <div className="bg-zinc-900 border border-zinc-700 rounded p-2 text-[11px]">
                      <p className="text-zinc-300">Vol: {payload[0]?.payload?.x}%</p>
                      <p className="text-green-400">Ret: {payload[0]?.payload?.y}%</p>
                      <p className="text-blue-400">Sharpe: {payload[0]?.payload?.sharpe}</p>
                    </div>
                  ) : null} />
                  <Scatter data={frontier_pts} fill="#3b82f6" opacity={0.7} />
                  {optPoint && <ReferenceDot x={optPoint.x} y={optPoint.y} r={6} fill="#f59e0b" stroke="#f59e0b" />}
                </ScatterChart>
              </ResponsiveContainer>
              <p className="text-[9px] text-zinc-600 mt-1">Orange dot = optimal portfolio</p>
            </div>
          )}

          {/* Kelly tab */}
          {tab === 'kelly' && (
            <div className="space-y-2">
              <p className="text-[10px] text-zinc-500 mb-1">Dynamic Kelly Criterion — Position Sizing</p>
              {Object.entries(kelly).map(([t, k]) => (
                <div key={t} className="bg-zinc-900/80 border border-zinc-800 rounded-lg p-2.5">
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-[11px] font-bold text-zinc-200">{t.replace('.NS','')}</span>
                    <span className="text-[11px] font-black text-amber-400">{pct(k.kelly)} Kelly</span>
                  </div>
                  <div className="grid grid-cols-3 gap-2 text-[10px]">
                    <div><span className="text-zinc-500">Win Rate</span><br/><span className="text-green-400">{pct(k.win_rate)}</span></div>
                    <div><span className="text-zinc-500">Avg Win</span><br/><span className="text-green-400">{pct(k.avg_win)}</span></div>
                    <div><span className="text-zinc-500">Avg Loss</span><br/><span className="text-red-400">{pct(k.avg_loss)}</span></div>
                  </div>
                </div>
              ))}
            </div>
          )}

          {/* CVaR tab */}
          {tab === 'cvar' && (
            <div className="space-y-2">
              <p className="text-[10px] text-zinc-500 mb-1">CVaR (Expected Shortfall) — Tail Risk</p>
              {Object.entries(cvar).map(([t, c]) => (
                <div key={t} className="bg-zinc-900/80 border border-zinc-800 rounded-lg p-2.5">
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-[11px] font-bold text-zinc-200">{t.replace('.NS','')}</span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded text-xs font-bold"
                      style={{ background: (c.cvar || 0) < -0.03 ? '#ef444420' : '#10b98120', color: (c.cvar || 0) < -0.03 ? '#ef4444' : '#10b981' }}>
                      CVaR: {pct(c.cvar || 0)}
                    </span>
                  </div>
                  <div className="grid grid-cols-2 gap-x-4 gap-y-0.5 text-[10px]">
                    {[['VaR 5%', pct(c.var || 0), '#f59e0b'], ['Max DD', pct(c.max_drawdown || 0), '#ef4444'],
                      ['Volatility', pct(c.volatility || 0), '#6b7280'], ['Sharpe', fmt2(c.sharpe || 0), '#3b82f6'],
                      ['Sortino', fmt2(c.sortino || 0), '#8b5cf6'], ['Win Rate', pct(c.win_rate || 0), '#10b981'],
                    ].map(([l,v,col]) => (
                      <div key={l} className="flex justify-between">
                        <span className="text-zinc-500">{l}</span>
                        <span style={{ color: col }}>{v}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
