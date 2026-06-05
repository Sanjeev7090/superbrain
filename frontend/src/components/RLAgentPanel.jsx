import React, { useState, useEffect, useRef, useCallback } from 'react';
import axios from 'axios';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine
} from 'recharts';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const STRATEGY_NAMES = [
  'Godzilla TTE', 'SMC', 'MiroFish', 'Explosive Volume',
  'Falling Knife', 'AI Indicator', 'DEMON Confluence',
  'Golden Setup', 'Reverse Swings', 'AMDS-Hybrid',
  'PAC+S&O', 'Narrative Swing',
];

const STATUS_CONFIG = {
  idle:     { color: '#6b7280', bg: 'bg-gray-500/20',    label: 'Idle',     pulse: false },
  training: { color: '#f59e0b', bg: 'bg-amber-500/20',   label: 'Training', pulse: true  },
  paused:   { color: '#3b82f6', bg: 'bg-blue-500/20',    label: 'Paused',   pulse: false },
  running:  { color: '#10b981', bg: 'bg-emerald-500/20', label: 'Live',     pulse: true  },
};

const SIGNAL_CONFIG = {
  BUY:  { color: '#10b981', bg: 'bg-emerald-500/20', border: 'border-emerald-500/50' },
  SELL: { color: '#ef4444', bg: 'bg-red-500/20',     border: 'border-red-500/50'     },
  HOLD: { color: '#6b7280', bg: 'bg-gray-500/20',    border: 'border-gray-500/50'    },
};

const WEIGHT_COLORS = [
  '#f59e0b','#10b981','#3b82f6','#8b5cf6',
  '#ef4444','#06b6d4','#ec4899','#84cc16',
  '#f97316','#a78bfa','#34d399','#fb7185',
];

const MODE_LABELS = {
  historical: 'Historical',
  live:       'Live',
  hybrid:     'Hybrid',
};

const CustomTooltip = ({ active, payload, label }) => {
  if (active && payload?.length) {
    return (
      <div className="bg-[#1a1a1a] border border-white/10 rounded px-2 py-1 text-xs">
        <p className="text-zinc-400">Ep {label}</p>
        <p style={{ color: payload[0].value >= 0 ? '#10b981' : '#ef4444' }}>
          {Number(payload[0].value).toFixed(3)}
        </p>
      </div>
    );
  }
  return null;
};

export default function RLAgentPanel({ selectedStock }) {
  const [status, setStatus]         = useState(null);
  const [mode, setMode]             = useState('historical');
  const [ticker, setTicker]         = useState('RELIANCE.NS');
  const [timesteps, setTimesteps]   = useState(50000);
  const [loading, setLoading]       = useState(false);
  const [prediction, setPrediction] = useState(null);
  const [rebalance, setRebalance]   = useState(null);
  const [rebalancing, setRebalancing] = useState(false);
  const pollRef = useRef(null);

  useEffect(() => {
    if (selectedStock?.ticker && selectedStock.type !== 'CRYPTO' && selectedStock.type !== 'OPTION') {
      setTicker(selectedStock.ticker);
    }
  }, [selectedStock]);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await axios.get(`${API}/rl-agent/status`);
      setStatus(res.data);
    } catch { /* silent */ }
  }, []);

  const fetchPrediction = useCallback(async () => {
    try {
      const res = await axios.post(`${API}/rl-agent/predict`, { ticker });
      setPrediction(res.data);
    } catch { /* silent */ }
  }, [ticker]);

  useEffect(() => {
    fetchStatus();
    const iv = setInterval(() => {
      fetchStatus();
      if (status?.status === 'running') fetchPrediction();
    }, 2000);
    return () => clearInterval(iv);
  }, [fetchStatus, fetchPrediction, status?.status]);

  const handleStart = async () => {
    setLoading(true);
    try {
      await axios.post(`${API}/rl-agent/train`, {
        algorithm: 'DreamerV3', mode, ticker, timesteps,
      });
      setTimeout(fetchStatus, 800);
    } catch (e) {
      console.error('Start training failed:', e);
    } finally {
      setLoading(false);
    }
  };

  const handleStop = async () => {
    await axios.post(`${API}/rl-agent/stop`);
    fetchStatus();
  };

  const handleReset = async () => {
    if (!window.confirm('Reset DreamerV3 agent? All trained models will be deleted.')) return;
    await axios.post(`${API}/rl-agent/reset`);
    setPrediction(null);
    fetchStatus();
  };

  const handlePredict = async () => {
    setLoading(true);
    try {
      const res = await axios.post(`${API}/rl-agent/predict`, { ticker });
      setPrediction(res.data);
    } finally {
      setLoading(false);
    }
  };

  const handleRebalance = async () => {
    setRebalancing(true);
    try {
      const res = await axios.post(`${API}/rl-agent/rebalance`, { ticker });
      setRebalance(res.data);
      const pred = await axios.post(`${API}/rl-agent/predict`, { ticker });
      setPrediction(pred.data);
    } catch (e) {
      setRebalance({ success: false, error: e?.response?.data?.detail || 'Rebalance failed' });
    } finally {
      setRebalancing(false);
    }
  };

  const st    = status?.status || 'idle';
  const cfg   = STATUS_CONFIG[st] || STATUS_CONFIG.idle;
  const chartData = (status?.episode_rewards || []).map((r, i) => ({ ep: i + 1, reward: r }));
  const progress  = status?.timesteps_total
    ? Math.min(100, (status.timesteps_done / status.timesteps_total) * 100)
    : 0;
  const weights  = status?.last_weights || Array(12).fill(1 / 12);
  const maxWeight = Math.max(...weights);
  const sigCfg   = SIGNAL_CONFIG[prediction?.signal] || SIGNAL_CONFIG.HOLD;
  const kronosOn = prediction?.kronos_active || status?.kronos_active;

  return (
    <div className="flex flex-col gap-0 text-sm select-none" data-testid="rl-agent-panel">

      {/* ── Header ── */}
      <div className="px-4 py-3 border-b border-white/8 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="text-[11px] font-black tracking-widest uppercase text-white">
            DreamerV3
          </span>
          <span className="text-[9px] text-zinc-500 font-mono">World Model RL</span>
          {kronosOn && (
            <span
              className="text-[8px] font-black px-1.5 py-0.5 rounded-full bg-cyan-500/20 text-cyan-400 border border-cyan-500/30 animate-pulse"
              data-testid="kronos-active-badge"
            >
              KRONOS
            </span>
          )}
        </div>
        <div className={`flex items-center gap-1.5 px-2 py-0.5 rounded-full ${cfg.bg}`}>
          <span
            className={`w-1.5 h-1.5 rounded-full ${cfg.pulse ? 'animate-pulse' : ''}`}
            style={{ backgroundColor: cfg.color }}
          />
          <span className="text-[10px] font-bold" style={{ color: cfg.color }}>
            {cfg.label}
          </span>
        </div>
      </div>

      {/* ── DreamerV3 Config info ── */}
      <div className="px-4 py-2 border-b border-white/8">
        <div className="flex gap-2 flex-wrap">
          {[
            { label: 'Latent',   val: '128-dim' },
            { label: 'Horizon',  val: '20 steps' },
            { label: 'Hidden',   val: '512-wide' },
            { label: 'RSSM',     val: 'Enc + Prior + Post' },
            { label: 'Kronos',   val: 'COND injected' },
          ].map(({ label, val }) => (
            <div key={label} className="flex items-center gap-1 bg-white/5 rounded px-2 py-1">
              <span className="text-[9px] text-zinc-500">{label}:</span>
              <span className="text-[9px] font-mono text-violet-400">{val}</span>
            </div>
          ))}
        </div>
      </div>

      {/* ── Config ── */}
      <div className="px-4 py-3 border-b border-white/8 space-y-3">

        {/* Mode */}
        <div>
          <p className="text-[10px] text-zinc-500 mb-1.5 uppercase tracking-widest">Training Mode</p>
          <div className="flex gap-1" data-testid="mode-selector">
            {['historical', 'live', 'hybrid'].map(m => (
              <button
                key={m}
                onClick={() => setMode(m)}
                data-testid={`mode-${m}`}
                className={`flex-1 py-1.5 rounded text-[10px] font-bold transition-all ${
                  mode === m
                    ? 'bg-cyan-600 text-white'
                    : 'bg-white/5 text-zinc-400 hover:bg-white/10'
                }`}
              >
                {MODE_LABELS[m]}
              </button>
            ))}
          </div>
        </div>

        {/* Ticker */}
        <div>
          <p className="text-[10px] text-zinc-500 mb-1 uppercase tracking-widest">Stock Ticker</p>
          <input
            value={ticker}
            onChange={e => setTicker(e.target.value.toUpperCase())}
            placeholder="e.g. RELIANCE.NS"
            data-testid="rl-ticker-input"
            className="w-full bg-white/5 border border-white/10 rounded px-2.5 py-1.5 text-xs text-white placeholder:text-zinc-600 focus:outline-none focus:border-violet-500/50"
          />
        </div>

        {/* Timesteps */}
        <div>
          <div className="flex justify-between items-center mb-1">
            <p className="text-[10px] text-zinc-500 uppercase tracking-widest">Timesteps</p>
            <span className="text-[10px] font-mono text-violet-400">{(timesteps / 1000).toFixed(0)}K</span>
          </div>
          <input
            type="range"
            min={5000} max={500000} step={5000}
            value={timesteps}
            onChange={e => setTimesteps(Number(e.target.value))}
            data-testid="timesteps-slider"
            className="w-full accent-violet-500"
          />
          <div className="flex justify-between text-[9px] text-zinc-600 mt-0.5">
            <span>5K</span><span>250K</span><span>500K</span>
          </div>
        </div>

        {/* Controls */}
        <div className="flex gap-1.5">
          <button
            onClick={handleStart}
            disabled={loading || st === 'training'}
            data-testid="start-training-btn"
            className="flex-1 py-2 rounded text-[11px] font-bold bg-violet-600 hover:bg-violet-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors text-white"
          >
            {st === 'training' ? 'Training...' : 'Start'}
          </button>
          <button
            onClick={handleStop}
            disabled={st !== 'training'}
            data-testid="stop-training-btn"
            className="flex-1 py-2 rounded text-[11px] font-bold bg-amber-600 hover:bg-amber-500 disabled:opacity-40 disabled:cursor-not-allowed transition-colors text-white"
          >
            Stop
          </button>
          <button
            onClick={handleReset}
            data-testid="reset-agent-btn"
            className="px-3 py-2 rounded text-[11px] font-bold bg-red-900/60 hover:bg-red-800/60 transition-colors text-red-400"
          >
            Reset
          </button>
        </div>
      </div>

      {/* ── Progress ── */}
      {st !== 'idle' && (
        <div className="px-4 py-3 border-b border-white/8 space-y-2">
          <div className="flex justify-between text-[10px]">
            <span className="text-zinc-500">Episode</span>
            <span className="text-white font-mono">{status?.episode || 0}</span>
          </div>
          <div className="flex justify-between text-[10px]">
            <span className="text-zinc-500">Timesteps</span>
            <span className="text-white font-mono">
              {(status?.timesteps_done || 0).toLocaleString()} / {(status?.timesteps_total || 0).toLocaleString()}
            </span>
          </div>
          <div className="w-full h-1.5 bg-white/10 rounded-full overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-500"
              style={{ width: `${progress}%`, background: 'linear-gradient(90deg, #7c3aed, #06b6d4)' }}
            />
          </div>

          {/* Metrics Row */}
          <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[10px]">
            <div>
              <span className="text-zinc-500">Avg Rew (10): </span>
              <span className={`font-mono font-bold ${(status?.avg_reward_10 || 0) >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                {(status?.avg_reward_10 || 0).toFixed(4)}
              </span>
            </div>
            <div>
              <span className="text-zinc-500">Best: </span>
              <span className="font-mono text-amber-400">
                {(status?.best_reward === -1e9 ? 0 : status?.best_reward || 0).toFixed(3)}
              </span>
            </div>
          </div>

          {/* DreamerV3 World Model Losses */}
          <div className="rounded-lg bg-white/3 border border-white/5 px-2.5 py-2 space-y-1.5">
            <p className="text-[9px] text-zinc-600 uppercase tracking-widest">World Model Losses</p>
            {[
              { key: 'wm_loss',     label: 'WM Loss',    color: '#7c3aed' },
              { key: 'actor_loss',  label: 'Actor',       color: '#06b6d4' },
              { key: 'critic_loss', label: 'Critic',      color: '#f59e0b' },
            ].map(({ key, label, color }) => (
              <div key={key} className="flex items-center justify-between" data-testid={`loss-${key}`}>
                <span className="text-[9px] text-zinc-500">{label}</span>
                <span className="text-[9px] font-mono" style={{ color }}>
                  {(status?.[key] ?? 0).toFixed(5)}
                </span>
              </div>
            ))}
            {status?.kronos_active && (
              <div className="flex items-center justify-between border-t border-white/5 pt-1 mt-1">
                <span className="text-[9px] text-cyan-400">Kronos Bonus</span>
                <span className="text-[9px] font-mono text-cyan-300">
                  +{(status?.kronos_bonus ?? 0).toFixed(4)}
                </span>
              </div>
            )}
            {status?.kronos_active && (
              <div className="flex items-center justify-between">
                <span className="text-[9px] text-cyan-500">Refresh Every</span>
                <span className="text-[9px] font-mono text-cyan-400">
                  {mode === 'live' ? '50' : mode === 'hybrid' ? '150' : '300'} steps
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Reward Curve ── */}
      {chartData.length > 2 && (
        <div className="px-4 py-3 border-b border-white/8">
          <p className="text-[10px] text-zinc-500 uppercase tracking-widest mb-2">Reward Curve</p>
          <ResponsiveContainer width="100%" height={100}>
            <LineChart data={chartData} margin={{ top: 2, right: 4, left: -20, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" />
              <XAxis dataKey="ep" tick={{ fontSize: 9, fill: '#6b7280' }} tickCount={4} />
              <YAxis tick={{ fontSize: 9, fill: '#6b7280' }} />
              <Tooltip content={<CustomTooltip />} />
              <ReferenceLine y={0} stroke="rgba(255,255,255,0.2)" strokeDasharray="4 2" />
              <Line
                type="monotone" dataKey="reward"
                stroke="#7c3aed" strokeWidth={1.5} dot={false}
                activeDot={{ r: 3, fill: '#7c3aed' }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* ── Strategy Weight Heatmap ── */}
      <div className="px-4 py-3 border-b border-white/8">
        <p className="text-[10px] text-zinc-500 uppercase tracking-widest mb-2.5">
          Strategy Weights (DreamerV3 Optimized)
        </p>
        <div className="space-y-1.5" data-testid="strategy-weights">
          {STRATEGY_NAMES.map((name, i) => {
            const w   = weights[i] || 0;
            const pct = maxWeight > 0 ? (w / maxWeight) * 100 : 0;
            return (
              <div key={name} className="flex items-center gap-2">
                <span className="w-[90px] text-[9px] text-zinc-500 truncate shrink-0">{name}</span>
                <div className="flex-1 h-2 bg-white/5 rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-700"
                    style={{ width: `${pct}%`, backgroundColor: WEIGHT_COLORS[i % WEIGHT_COLORS.length] }}
                  />
                </div>
                <span
                  className="w-8 text-[9px] font-mono text-right shrink-0"
                  style={{ color: WEIGHT_COLORS[i % WEIGHT_COLORS.length] }}
                >
                  {(w * 100).toFixed(1)}%
                </span>
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Current Signal ── */}
      <div className="px-4 py-3">
        <div className="flex items-center justify-between mb-2">
          <p className="text-[10px] text-zinc-500 uppercase tracking-widest">DreamerV3 Signal</p>
          <button
            onClick={handlePredict}
            disabled={loading}
            data-testid="get-prediction-btn"
            className="text-[9px] px-2 py-0.5 rounded border border-white/10 text-zinc-400 hover:text-white hover:border-white/30 transition-colors"
          >
            Refresh
          </button>
        </div>

        {prediction ? (
          <div
            data-testid="rl-signal-display"
            className={`rounded-lg border p-3 ${sigCfg.bg} ${sigCfg.border}`}
          >
            <div className="flex items-center justify-between mb-2">
              <span
                className="text-xl font-black tracking-widest"
                style={{ color: sigCfg.color }}
                data-testid="rl-signal-value"
              >
                {prediction.signal}
              </span>
              <div className="flex items-center gap-2">
                {prediction.kronos_active && (
                  <span className="text-[8px] text-cyan-400 font-mono bg-cyan-500/10 px-1.5 py-0.5 rounded-full border border-cyan-500/20">
                    Kronos guided
                  </span>
                )}
                <span className="text-[10px] font-mono text-zinc-400">
                  {prediction.confidence}% conf
                </span>
              </div>
            </div>
            <div className="w-full h-1 bg-white/10 rounded-full overflow-hidden mb-2">
              <div
                className="h-full rounded-full transition-all"
                style={{ width: `${prediction.confidence}%`, backgroundColor: sigCfg.color }}
              />
            </div>
            <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 text-[10px]">
              <div>
                <span className="text-zinc-500">Episode Return: </span>
                <span className={`font-mono ${prediction.total_return >= 0 ? 'text-emerald-400' : 'text-red-400'}`}>
                  {(prediction.total_return * 100).toFixed(2)}%
                </span>
              </div>
              {prediction.wm_loss !== undefined && (
                <div>
                  <span className="text-zinc-500">WM Loss: </span>
                  <span className="font-mono text-violet-400">
                    {prediction.wm_loss.toFixed(4)}
                  </span>
                </div>
              )}
            </div>
            {prediction.message && (
              <p className="text-[9px] text-zinc-600 mt-1.5 font-mono truncate">{prediction.message}</p>
            )}
          </div>
        ) : (
          <div className="rounded-lg border border-white/8 bg-white/3 p-3 text-center">
            <p className="text-[11px] text-zinc-600">
              {st === 'idle'
                ? 'Configure and start training to get DreamerV3-powered signals'
                : 'Training in progress — signal will appear after first episode'}
            </p>
          </div>
        )}

        {status?.error && (
          <div className="mt-2 rounded border border-red-500/30 bg-red-900/20 p-2">
            <p className="text-[10px] text-red-400">{status.error}</p>
          </div>
        )}
      </div>

      {/* ── AI Rebalance ── */}
      <div className="px-4 py-3 border-t border-white/8">
        <button
          onClick={handleRebalance}
          disabled={rebalancing || st === 'idle'}
          data-testid="ai-rebalance-btn"
          className={`w-full py-2.5 rounded-lg text-[12px] font-black uppercase tracking-widest transition-all duration-300 relative overflow-hidden ${
            st === 'idle'
              ? 'bg-white/5 text-zinc-600 cursor-not-allowed'
              : rebalancing
              ? 'bg-violet-800/60 text-violet-300 cursor-wait'
              : 'bg-gradient-to-r from-violet-700 to-cyan-600 text-white hover:from-violet-600 hover:to-cyan-500 shadow-lg shadow-violet-500/20'
          }`}
        >
          {rebalancing ? (
            <span className="flex items-center justify-center gap-2">
              <span className="w-3 h-3 rounded-full border-2 border-white/40 border-t-white animate-spin" />
              Rebalancing...
            </span>
          ) : (
            'AI Rebalance'
          )}
        </button>

        {rebalance && (
          <div className="mt-3 space-y-2" data-testid="rebalance-result">
            {rebalance.success === false ? (
              <div className="rounded-lg border border-red-500/30 bg-red-900/20 p-2">
                <p className="text-[10px] text-red-400">{rebalance.error}</p>
              </div>
            ) : (
              <>
                <div
                  className="rounded-xl border border-violet-500/30 bg-gradient-to-br from-violet-900/40 to-cyan-900/20 p-3"
                  data-testid="rebalance-confidence"
                >
                  <div className="flex items-center justify-between mb-2">
                    <span className="text-[10px] text-zinc-400 uppercase tracking-widest">Confidence</span>
                    <span className="text-[10px] font-mono text-zinc-500">
                      {rebalance.timestamp ? new Date(rebalance.timestamp).toLocaleTimeString() : ''}
                    </span>
                  </div>

                  <div className="flex items-center gap-4">
                    <div className="relative w-16 h-16 shrink-0">
                      <svg viewBox="0 0 60 60" className="w-full h-full -rotate-90">
                        <circle cx="30" cy="30" r="24" fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="5" />
                        <circle
                          cx="30" cy="30" r="24" fill="none"
                          stroke="url(#conf-grad)" strokeWidth="5"
                          strokeDasharray={`${(rebalance.confidence / 100) * 150.8} 150.8`}
                          strokeLinecap="round"
                        />
                        <defs>
                          <linearGradient id="conf-grad" x1="0%" y1="0%" x2="100%" y2="0%">
                            <stop offset="0%" stopColor="#7c3aed" />
                            <stop offset="100%" stopColor="#06b6d4" />
                          </linearGradient>
                        </defs>
                      </svg>
                      <div className="absolute inset-0 flex items-center justify-center">
                        <span className="text-sm font-black text-white">{rebalance.confidence}%</span>
                      </div>
                    </div>

                    <div className="flex-1 space-y-1">
                      <div className="flex justify-between text-[10px]">
                        <span className="text-zinc-500">Signal</span>
                        <span className={`font-black text-xs ${
                          rebalance.signal === 'BUY' ? 'text-emerald-400'
                          : rebalance.signal === 'SELL' ? 'text-red-400' : 'text-zinc-400'
                        }`}>{rebalance.signal}</span>
                      </div>
                      <div className="flex justify-between text-[10px]">
                        <span className="text-zinc-500">Sig Confidence</span>
                        <span className="font-mono text-white">{rebalance.signal_confidence}%</span>
                      </div>
                      <div className="flex justify-between text-[10px]">
                        <span className="text-zinc-500">Ticker</span>
                        <span className="font-mono text-zinc-300">{rebalance.ticker}</span>
                      </div>
                    </div>
                  </div>

                  <div className="mt-2 w-full h-1 bg-white/10 rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all duration-1000"
                      style={{
                        width: `${rebalance.confidence}%`,
                        background: 'linear-gradient(90deg, #7c3aed, #06b6d4)',
                      }}
                    />
                  </div>
                </div>

                {rebalance.changes?.length > 0 && (
                  <div className="rounded-xl border border-white/8 bg-white/3 p-3">
                    <p className="text-[9px] text-zinc-500 uppercase tracking-widest mb-2">
                      Strategy Weight Changes
                    </p>
                    <div className="space-y-1.5 max-h-48 overflow-y-auto" data-testid="weight-changes">
                      {rebalance.changes
                        .sort((a, b) => Math.abs(b.delta) - Math.abs(a.delta))
                        .map(ch => (
                          <div key={ch.strategy} className="flex items-center gap-2">
                            <div
                              className="w-1.5 h-1.5 rounded-full shrink-0"
                              style={{ backgroundColor: WEIGHT_COLORS[STRATEGY_NAMES.indexOf(ch.strategy) % WEIGHT_COLORS.length] }}
                            />
                            <span className="w-[88px] text-[9px] text-zinc-500 truncate shrink-0">{ch.strategy}</span>
                            <span className="text-[9px] font-mono text-zinc-600 w-8 text-right shrink-0">{ch.old}%</span>
                            <span className="text-zinc-600 text-[9px]">→</span>
                            <span className="text-[9px] font-mono text-white w-8 shrink-0">{ch.new}%</span>
                            <span className={`text-[9px] font-black ml-auto shrink-0 w-10 text-right ${
                              ch.delta > 0.5 ? 'text-emerald-400'
                              : ch.delta < -0.5 ? 'text-red-400' : 'text-zinc-600'
                            }`}>
                              {ch.delta > 0 ? '↑' : ch.delta < 0 ? '↓' : '—'}
                              {Math.abs(ch.delta).toFixed(1)}%
                            </span>
                          </div>
                        ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {st === 'idle' && (
          <p className="text-[9px] text-zinc-600 text-center mt-1.5">
            Train DreamerV3 first to enable AI Rebalance
          </p>
        )}
      </div>
    </div>
  );
}
