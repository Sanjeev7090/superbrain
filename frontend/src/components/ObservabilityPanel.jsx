/**
 * ObservabilityPanel — In-app Grafana-style: metrics, equity curve, anomaly alerts, PER stats, DreamerV3 continuous training
 */
import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, AreaChart, Area, BarChart, Bar, Cell,
} from 'recharts';
import { Activity, Cpu, RefreshCw, Play, Square, Database, AlertTriangle } from 'lucide-react';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

function Metric({ label, value, color, sub }) {
  return (
    <div className="bg-zinc-900/80 border border-zinc-800 rounded-lg p-2.5">
      <div className="text-[9px] text-zinc-500 uppercase tracking-widest">{label}</div>
      <div className="text-base font-black tabular-nums mt-0.5" style={{ color: color || '#e4e4e7' }}>{value}</div>
      {sub && <div className="text-[9px] text-zinc-600 mt-0.5">{sub}</div>}
    </div>
  );
}

const pct = v => `${(v * 100).toFixed(2)}%`;
const f2  = v => Number(v || 0).toFixed(2);

export default function ObservabilityPanel({ selectedStock }) {
  const [metrics, setMetrics] = useState({});
  const [alerts, setAlerts]   = useState([]);
  const [perStats, setPerStats] = useState({});
  const [rrState, setRrState]   = useState({});
  const [prometheus, setPrometheus] = useState('');
  const [contRunning, setContRunning] = useState(false);
  const [ticker, setTicker]   = useState(selectedStock?.ticker || 'RELIANCE.NS');
  const [tab, setTab]         = useState('overview');

  const refresh = useCallback(async () => {
    try {
      const [mRes, aRes, pRes, rrRes] = await Promise.all([
        axios.get(`${API}/advanced/observability/metrics`),
        axios.get(`${API}/advanced/observability/alerts`),
        axios.get(`${API}/advanced/dreamer/per-stats`),
        axios.get(`${API}/advanced/dreamer/risk-reward`),
      ]);
      setMetrics(mRes.data || {});
      setAlerts(aRes.data?.alerts || []);
      setPerStats(pRes.data || {});
      setRrState(rrRes.data || {});
      setContRunning(mRes.data?.continuous_mode || false);
    } catch {}
  }, []);

  const fetchPrometheus = async () => {
    const res = await axios.get(`${API}/advanced/observability/prometheus`);
    setPrometheus(typeof res.data === 'string' ? res.data : JSON.stringify(res.data, null, 2));
  };

  useEffect(() => { refresh(); const t = setInterval(refresh, 4000); return () => clearInterval(t); }, [refresh]);

  const toggleContinuous = async () => {
    try {
      const res = await axios.post(`${API}/advanced/dreamer/continuous-toggle`, {
        enabled: !contRunning,
        ticker,
      });
      toast.success(res.data?.message || 'Done');
      setContRunning(!contRunning);
    } catch (e) {
      toast.error('Failed to toggle continuous training');
    }
  };

  const m = metrics;
  const equity_pts = (m.equity_curve || []).map((e, i) => ({ i, v: e.equity * 100 }));
  const pnl_pts    = (m.pnl_history  || []).map((v, i) => ({ i, v: v * 100 }));

  const sevColor = s => ({ CRITICAL:'#ef4444', WARNING:'#f59e0b', INFO:'#3b82f6' }[s] || '#6b7280');

  return (
    <div className="space-y-3 p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Activity size={15} className="text-green-400" />
          <span className="text-[13px] font-bold text-white">Observability Dashboard</span>
        </div>
        <button onClick={refresh} className="text-zinc-500 hover:text-white transition-colors">
          <RefreshCw size={13} />
        </button>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-zinc-800">
        {['overview','equity','anomalies','per','dreamer','prometheus'].map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-2 py-1.5 text-[10px] font-bold uppercase tracking-wider border-b-2 transition-colors whitespace-nowrap ${tab === t ? 'border-green-500 text-green-400' : 'border-transparent text-zinc-500 hover:text-white'}`}>
            {t}
          </button>
        ))}
      </div>

      {/* Overview */}
      {tab === 'overview' && (
        <div className="space-y-3">
          <div className="grid grid-cols-3 gap-2">
            <Metric label="Total Trades"  value={m.total_trades || 0} />
            <Metric label="Win Rate"  value={pct(m.win_rate || 0)} color={(m.win_rate || 0) > 0.5 ? '#10b981' : '#ef4444'} />
            <Metric label="Sharpe"    value={f2(m.sharpe_rolling || 0)} color={(m.sharpe_rolling || 0) > 1 ? '#3b82f6' : '#f59e0b'} />
            <Metric label="Gross P&L" value={pct(m.gross_pnl || 0)} color={(m.gross_pnl || 0) >= 0 ? '#10b981' : '#ef4444'} />
            <Metric label="Max DD"    value={pct(m.max_drawdown || 0)} color="#ef4444" />
            <Metric label="Profit F." value={f2(m.profit_factor || 0)} color={(m.profit_factor || 0) > 1 ? '#10b981' : '#ef4444'} />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Metric label="Avg Win"  value={pct(m.avg_win || 0)}  color="#10b981" />
            <Metric label="Avg Loss" value={pct(m.avg_loss || 0)} color="#ef4444" />
          </div>
          {/* Alerts preview */}
          {alerts.filter(a => a.severity === 'CRITICAL').slice(0, 2).map(a => (
            <div key={a.id} className="flex items-center gap-2 px-3 py-2 bg-red-500/10 border border-red-500/30 rounded-lg text-[11px] text-red-400">
              <AlertTriangle size={12} />
              {a.message}
            </div>
          ))}
        </div>
      )}

      {/* Equity chart */}
      {tab === 'equity' && (
        <div className="space-y-3">
          {equity_pts.length > 1 ? (
            <>
              <p className="text-[10px] text-zinc-500">Equity Curve (×100 = starting 100)</p>
              <ResponsiveContainer width="100%" height={180}>
                <AreaChart data={equity_pts}>
                  <defs>
                    <linearGradient id="eqGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.25} />
                      <stop offset="95%" stopColor="#3b82f6" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#27272a" />
                  <XAxis dataKey="i" hide />
                  <YAxis tick={{ fill: '#52525b', fontSize: 10 }} domain={['auto','auto']} />
                  <Tooltip contentStyle={{ background: '#18181b', border: '1px solid #3f3f46', fontSize: 11 }}
                    formatter={v => [`${Number(v).toFixed(3)}`, 'Equity']} />
                  <Area type="monotone" dataKey="v" stroke="#3b82f6" fill="url(#eqGrad)" strokeWidth={1.5} dot={false} />
                </AreaChart>
              </ResponsiveContainer>
            </>
          ) : <p className="text-zinc-600 text-[12px] text-center py-8">No equity data yet — execute some trades</p>}

          {pnl_pts.length > 1 && (
            <>
              <p className="text-[10px] text-zinc-500 mt-2">Per-Trade P&L %</p>
              <ResponsiveContainer width="100%" height={80}>
                <BarChart data={pnl_pts}>
                  <Bar dataKey="v">
                    {pnl_pts.map((entry, index) => (
                      <Cell key={index} fill={entry.v >= 0 ? '#10b981' : '#ef4444'} />
                    ))}
                  </Bar>
                  <XAxis dataKey="i" hide />
                  <YAxis hide />
                </BarChart>
              </ResponsiveContainer>
            </>
          )}
        </div>
      )}

      {/* Anomaly alerts */}
      {tab === 'anomalies' && (
        <div className="space-y-1.5">
          {alerts.length === 0 ? (
            <p className="text-zinc-600 text-[12px] text-center py-8">No anomaly alerts</p>
          ) : alerts.map(a => (
            <div key={a.id} className="rounded-lg p-2.5 border" style={{ background: `${sevColor(a.severity)}10`, borderColor: `${sevColor(a.severity)}30` }} data-testid={`obs-alert-${a.id}`}>
              <div className="flex items-center justify-between mb-0.5">
                <span className="text-[10px] font-bold" style={{ color: sevColor(a.severity) }}>{a.severity}</span>
                <span className="text-[9px] text-zinc-600">{new Date(a.timestamp).toLocaleTimeString()}</span>
              </div>
              <p className="text-[11px] text-zinc-300">{a.message}</p>
              {a.value != null && <p className="text-[9px] text-zinc-500 mt-0.5">value: {JSON.stringify(a.value)}</p>}
            </div>
          ))}
        </div>
      )}

      {/* PER buffer stats */}
      {tab === 'per' && (
        <div className="space-y-3">
          {!perStats.enabled ? (
            <p className="text-zinc-600 text-[12px] text-center py-8">PER buffer not active — start DreamerV3 training</p>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-2">
                {[
                  ['Buffer Size', `${perStats.size?.toLocaleString()} / ${perStats.capacity?.toLocaleString()}`],
                  ['Fill %', `${perStats.fill_pct}%`],
                  ['Total Pushes', perStats.total_pushes?.toLocaleString()],
                  ['Total Samples', perStats.total_samples?.toLocaleString()],
                  ['Beta (IS)', perStats.beta?.toFixed(4)],
                  ['Max Priority', perStats.max_priority?.toFixed(6)],
                ].map(([l, v]) => <Metric key={l} label={l} value={v || '-'} />)}
              </div>
              <div className="bg-zinc-900/80 border border-zinc-800 rounded-xl p-3">
                <p className="text-[10px] text-zinc-500 mb-2">Buffer fill</p>
                <div className="h-2 bg-zinc-800 rounded-full overflow-hidden">
                  <div className="h-full bg-blue-500 rounded-full transition-all duration-500"
                    style={{ width: `${perStats.fill_pct || 0}%` }} />
                </div>
                <p className="text-[9px] text-zinc-600 mt-1">β anneals from 0.4 → 1.0 ({perStats.total_samples?.toLocaleString()} samples)</p>
              </div>
            </>
          )}

          {/* Risk-Reward state */}
          {rrState.enabled && (
            <div className="bg-zinc-900/80 border border-zinc-800 rounded-xl p-3 space-y-1.5">
              <p className="text-[10px] text-zinc-500 uppercase tracking-widest font-bold">Risk-Adjusted Reward Breakdown</p>
              {[
                ['P&L Component',   rrState.pnl_comp,      '#e4e4e7'],
                ['Sharpe Bonus',    rrState.sharpe_bonus,  '#3b82f6'],
                ['CVaR Penalty',   -rrState.cvar_penalty,  '#ef4444'],
                ['Kelly Alignment', rrState.kelly_align,   '#f59e0b'],
                ['Drawdown Penalty',-rrState.dd_penalty,   '#ef4444'],
                ['Sortino Bonus',   rrState.sortino_bonus, '#8b5cf6'],
                ['TOTAL',           rrState.total,         rrState.total >= 0 ? '#10b981' : '#ef4444'],
              ].map(([l, v, c]) => (
                <div key={l} className="flex justify-between text-[11px]">
                  <span className="text-zinc-500">{l}</span>
                  <span className="font-mono font-bold" style={{ color: c }}>{Number(v || 0).toFixed(5)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Continuous DreamerV3 */}
      {tab === 'dreamer' && (
        <div className="space-y-3">
          <div className="bg-zinc-900/80 border border-zinc-800 rounded-xl p-4 space-y-3">
            <div className="flex items-center gap-2">
              <Cpu size={14} className={contRunning ? 'text-green-400 animate-pulse' : 'text-zinc-500'} />
              <span className="text-[12px] font-bold text-white">Online Continuous Training</span>
              {contRunning && (
                <span className="text-[9px] bg-green-500/15 text-green-400 border border-green-500/30 px-2 py-0.5 rounded-full font-bold">RUNNING</span>
              )}
            </div>
            <p className="text-[11px] text-zinc-500 leading-relaxed">
              Continuously collects 200 steps of market data every 60 seconds and online-trains DreamerV3 with PER buffer. Model saved after each cycle.
            </p>
            <div className="flex gap-2 items-center">
              <input value={ticker} onChange={e => setTicker(e.target.value)}
                className="flex-1 bg-zinc-800 border border-zinc-700 rounded px-2 py-1.5 text-[11px] text-zinc-200 font-mono focus:outline-none focus:border-green-500"
                placeholder="RELIANCE.NS" data-testid="cont-ticker-input" />
              <button onClick={toggleContinuous}
                className={`px-4 py-1.5 text-[11px] font-bold rounded flex items-center gap-1.5 transition-colors ${contRunning ? 'bg-red-700 hover:bg-red-600 text-white' : 'bg-green-700 hover:bg-green-600 text-white'}`}
                data-testid="continuous-toggle-btn">
                {contRunning ? <><Square size={11} /> Stop</> : <><Play size={11} /> Start</>}
              </button>
            </div>
            {contRunning && (
              <div className="flex items-center gap-1.5 text-[11px] text-green-400">
                <div className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
                Cycle {m.continuous_cycles || 0} running — {(m.timesteps_done || 0).toLocaleString()} total steps
              </div>
            )}
          </div>

          {/* DreamerV3 per/rr legend */}
          <div className="bg-zinc-900/80 border border-zinc-800 rounded-xl p-3 space-y-1.5">
            <p className="text-[10px] text-zinc-500 uppercase tracking-widest font-bold mb-2">Architecture</p>
            {[
              ['Replay Buffer', perStats.enabled ? `PER (Prioritized) — ${perStats.size?.toLocaleString() || 0} transitions` : 'Uniform (PER inactive)'],
              ['Reward Shaping', rrState.enabled ? 'Risk-Adjusted: Sharpe + CVaR + Kelly + Sortino' : 'Basic P&L'],
              ['Online Learning', contRunning ? 'Active' : 'Inactive'],
              ['IS Beta', perStats.beta?.toFixed(4) || '0.4000'],
            ].map(([l, v]) => (
              <div key={l} className="flex justify-between text-[11px]">
                <span className="text-zinc-500">{l}</span>
                <span className="text-zinc-300">{v}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Prometheus */}
      {tab === 'prometheus' && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <p className="text-[10px] text-zinc-500 uppercase tracking-widest font-bold">Prometheus Metrics Export</p>
            <button onClick={fetchPrometheus} className="text-[10px] text-blue-400 hover:text-blue-300" data-testid="fetch-prometheus-btn">
              Fetch
            </button>
          </div>
          {prometheus ? (
            <pre className="bg-zinc-950 border border-zinc-800 rounded-lg p-3 text-[10px] text-zinc-400 font-mono overflow-x-auto whitespace-pre-wrap max-h-96 overflow-y-auto">
              {prometheus}
            </pre>
          ) : (
            <div className="bg-zinc-900/80 border border-zinc-800 rounded-xl p-4 space-y-1.5 text-[11px]">
              <p className="text-zinc-500">Copy this URL to scrape in Prometheus / Grafana:</p>
              <code className="text-blue-400 text-[10px] font-mono break-all">
                {API}/advanced/observability/prometheus
              </code>
              <p className="text-[9px] text-zinc-600 mt-1">
                Add this target in prometheus.yml → scrape_configs → targets
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
