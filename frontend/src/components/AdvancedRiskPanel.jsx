/**
 * AdvancedRiskPanel — CVaR, Kill Switch, Circuit Breakers, Human-in-Loop Approval
 */
import React, { useState, useEffect, useCallback } from 'react';
import axios from 'axios';
import { toast } from 'sonner';
import { LineChart, Line, CartesianGrid, XAxis, YAxis, ResponsiveContainer } from 'recharts';
import { Shield, AlertTriangle, Zap, CheckCircle, XCircle, Clock, RefreshCw } from 'lucide-react';

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const pct = v => `${(v * 100).toFixed(2)}%`;

// ── Circuit Breaker Status ────────────────────────────────────────────────────
function CircuitStatus({ state }) {
  const cfg = {
    NORMAL:  { color: '#10b981', bg: '#10b98115', label: 'Normal',  icon: CheckCircle },
    WARNING: { color: '#f59e0b', bg: '#f59e0b15', label: 'Warning', icon: AlertTriangle },
    TRIPPED: { color: '#ef4444', bg: '#ef444415', label: 'TRIPPED', icon: Zap },
  }[state] || { color: '#6b7280', bg: '#6b728015', label: state, icon: Clock };

  return (
    <div className="flex items-center gap-2 px-3 py-2 rounded-lg" style={{ background: cfg.bg, border: `1px solid ${cfg.color}30` }}>
      <cfg.icon size={14} style={{ color: cfg.color }} />
      <span className="text-[11px] font-bold" style={{ color: cfg.color }}>{cfg.label}</span>
    </div>
  );
}

// ── Approval Card ─────────────────────────────────────────────────────────────
function ApprovalCard({ approval, onResolve }) {
  return (
    <div className="bg-amber-500/10 border border-amber-500/30 rounded-xl p-3 space-y-2" data-testid={`approval-${approval.id}`}>
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className={`text-[10px] font-black px-1.5 py-0.5 rounded ${approval.direction === 'BUY' ? 'bg-green-500/20 text-green-400' : 'bg-red-500/20 text-red-400'}`}>
            {approval.direction}
          </span>
          <span className="text-[12px] font-bold text-zinc-200">{approval.ticker}</span>
        </div>
        <span className="text-[10px] text-amber-400 font-bold">Risk: {approval.risk_pct}%</span>
      </div>
      <div className="text-[10px] text-zinc-400">{approval.reason}</div>
      <div className="flex items-center justify-between text-[10px] text-zinc-500">
        <span>Qty: {approval.quantity} @ ₹{approval.price}</span>
        <span>Value: ₹{approval.value?.toLocaleString('en-IN')}</span>
      </div>
      <div className="flex gap-2 mt-1">
        <button onClick={() => onResolve(approval.id, true)}
          className="flex-1 py-1.5 text-[11px] font-bold bg-green-600 hover:bg-green-700 text-white rounded flex items-center justify-center gap-1"
          data-testid={`approve-${approval.id}`}>
          <CheckCircle size={11} /> Approve
        </button>
        <button onClick={() => onResolve(approval.id, false)}
          className="flex-1 py-1.5 text-[11px] font-bold bg-red-700/60 hover:bg-red-700 text-white rounded flex items-center justify-center gap-1"
          data-testid={`reject-${approval.id}`}>
          <XCircle size={11} /> Reject
        </button>
      </div>
    </div>
  );
}

// ── Alert Item ────────────────────────────────────────────────────────────────
function AlertItem({ alert }) {
  const sev = {
    CRITICAL: { color: '#ef4444', bg: '#ef444412' },
    WARNING:  { color: '#f59e0b', bg: '#f59e0b12' },
    INFO:     { color: '#3b82f6', bg: '#3b82f612' },
  }[alert.severity] || { color: '#6b7280', bg: '#6b728012' };

  return (
    <div className="flex items-start gap-2.5 py-1.5 border-b border-zinc-800/50 last:border-0" data-testid={`alert-${alert.id}`}>
      <span className="text-[9px] font-bold px-1.5 py-0.5 rounded shrink-0 mt-0.5" style={{ background: sev.bg, color: sev.color }}>
        {alert.severity}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-[11px] text-zinc-300 leading-tight">{alert.message}</p>
        <p className="text-[9px] text-zinc-600 mt-0.5">{new Date(alert.timestamp).toLocaleTimeString()}</p>
      </div>
    </div>
  );
}

export default function AdvancedRiskPanel() {
  const [circuit, setCircuit] = useState({});
  const [metrics, setMetrics] = useState({});
  const [alerts, setAlerts]   = useState([]);
  const [approvals, setApprovals] = useState([]);
  const [killReason, setKillReason] = useState('');
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState('status'); // status | metrics | approvals | alerts

  const refresh = useCallback(async () => {
    try {
      const [cRes, mRes, aRes, apRes] = await Promise.all([
        axios.get(`${API}/advanced/risk/circuit-status`),
        axios.get(`${API}/advanced/observability/metrics`),
        axios.get(`${API}/advanced/observability/alerts`),
        axios.get(`${API}/advanced/risk/approvals`),
      ]);
      setCircuit(cRes.data || {});
      setMetrics(mRes.data || {});
      setAlerts(aRes.data?.alerts || []);
      setApprovals(apRes.data?.pending || []);
    } catch {}
  }, []);

  useEffect(() => { refresh(); const t = setInterval(refresh, 5000); return () => clearInterval(t); }, [refresh]);

  const handleKillSwitch = async (activate) => {
    if (activate && !killReason.trim()) { toast.error('Enter reason for kill switch'); return; }
    setLoading(true);
    try {
      await axios.post(`${API}/advanced/risk/kill-switch`, {
        action: activate ? 'activate' : 'deactivate',
        reason: killReason || 'Manual override',
      });
      toast.success(activate ? 'Kill switch ACTIVATED' : 'Kill switch deactivated');
      setKillReason('');
      refresh();
    } finally { setLoading(false); }
  };

  const handleResetCircuit = async () => {
    await axios.post(`${API}/advanced/risk/reset-circuit`);
    toast.success('Circuit breaker reset');
    refresh();
  };

  const resolveApproval = async (id, approved) => {
    await axios.post(`${API}/advanced/risk/approve/${id}`, { approved, comment: '' });
    toast.success(approved ? 'Trade approved' : 'Trade rejected');
    refresh();
  };

  const m = metrics;
  const ks = circuit.kill_switch;

  return (
    <div className="space-y-3 p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Shield size={15} className="text-red-400" />
          <span className="text-[13px] font-bold text-white">Advanced Risk Management</span>
        </div>
        <button onClick={refresh} className="text-zinc-500 hover:text-white transition-colors">
          <RefreshCw size={13} />
        </button>
      </div>

      {/* Quick status bar */}
      <div className="flex items-center gap-2 flex-wrap">
        <CircuitStatus state={circuit.circuit_state || 'NORMAL'} />
        <div className={`flex items-center gap-1.5 px-3 py-2 rounded-lg text-[11px] font-bold ${ks ? 'bg-red-500/15 border border-red-500/30 text-red-400' : 'bg-zinc-800/60 border border-zinc-700 text-zinc-400'}`}>
          <Zap size={12} />
          {ks ? 'KILL SWITCH ACTIVE' : 'Kill Switch Off'}
        </div>
        {approvals.length > 0 && (
          <div className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-amber-500/15 border border-amber-500/30 text-amber-400 text-[11px] font-bold">
            <Clock size={12} />
            {approvals.length} Pending Approval{approvals.length > 1 ? 's' : ''}
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b border-zinc-800">
        {['status','metrics','approvals','alerts'].map(t => (
          <button key={t} onClick={() => setTab(t)}
            className={`px-3 py-1.5 text-[11px] font-bold uppercase tracking-wider border-b-2 transition-colors relative ${tab === t ? 'border-red-500 text-red-400' : 'border-transparent text-zinc-500 hover:text-white'}`}>
            {t}
            {t === 'approvals' && approvals.length > 0 && (
              <span className="absolute -top-0.5 -right-0.5 w-3.5 h-3.5 bg-amber-500 text-black text-[8px] font-black rounded-full flex items-center justify-center">
                {approvals.length}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Status tab */}
      {tab === 'status' && (
        <div className="space-y-3">
          {/* Kill Switch */}
          <div className="bg-zinc-900/80 border border-zinc-800 rounded-xl p-3 space-y-2">
            <p className="text-[10px] text-zinc-500 uppercase tracking-widest font-bold">Emergency Kill Switch</p>
            <input
              value={killReason}
              onChange={e => setKillReason(e.target.value)}
              placeholder="Reason (required to activate)..."
              className="w-full bg-zinc-800 border border-zinc-700 rounded px-2 py-1.5 text-[11px] text-zinc-200 focus:outline-none focus:border-red-500"
              data-testid="kill-reason-input"
            />
            <div className="flex gap-2">
              <button onClick={() => handleKillSwitch(true)} disabled={loading || ks}
                className="flex-1 py-2 text-[11px] font-black bg-red-600 hover:bg-red-700 disabled:opacity-40 text-white rounded flex items-center justify-center gap-1.5"
                data-testid="kill-activate-btn">
                <Zap size={11} /> ACTIVATE KILL SWITCH
              </button>
              {ks && (
                <button onClick={() => handleKillSwitch(false)} disabled={loading}
                  className="flex-1 py-2 text-[11px] font-bold bg-green-700 hover:bg-green-600 text-white rounded"
                  data-testid="kill-deactivate-btn">
                  Deactivate
                </button>
              )}
            </div>
          </div>

          {/* Circuit breaker controls */}
          <div className="bg-zinc-900/80 border border-zinc-800 rounded-xl p-3 space-y-2">
            <div className="flex items-center justify-between">
              <p className="text-[10px] text-zinc-500 uppercase tracking-widest font-bold">Circuit Breakers</p>
              {circuit.circuit_state !== 'NORMAL' && (
                <button onClick={handleResetCircuit}
                  className="text-[10px] px-2 py-0.5 bg-zinc-700 hover:bg-zinc-600 text-zinc-200 rounded"
                  data-testid="reset-circuit-btn">
                  Reset
                </button>
              )}
            </div>
            <div className="grid grid-cols-2 gap-2 text-[10px]">
              {[
                ['Daily Drawdown', pct(circuit.current_drawdown || 0), (circuit.current_drawdown || 0) > 0.03 ? '#ef4444' : '#10b981'],
                ['Consec. Losses', circuit.consecutive_losses || 0, (circuit.consecutive_losses || 0) >= 3 ? '#f59e0b' : '#10b981'],
              ].map(([l, v, c]) => (
                <div key={l} className="bg-zinc-800/60 rounded-lg p-2">
                  <div className="text-zinc-500">{l}</div>
                  <div className="font-black text-sm mt-0.5" style={{ color: c }}>{v}</div>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}

      {/* Metrics tab */}
      {tab === 'metrics' && (
        <div className="space-y-2">
          <div className="grid grid-cols-2 gap-2">
            {[
              ['Total Trades', m.total_trades || 0, '#6b7280'],
              ['Win Rate', pct(m.win_rate || 0), (m.win_rate || 0) > 0.5 ? '#10b981' : '#ef4444'],
              ['Gross P&L', pct(m.gross_pnl || 0), (m.gross_pnl || 0) >= 0 ? '#10b981' : '#ef4444'],
              ['Max Drawdown', pct(m.max_drawdown || 0), '#ef4444'],
              ['Sharpe (roll.)', (m.sharpe_rolling || 0).toFixed(2), (m.sharpe_rolling || 0) > 1 ? '#3b82f6' : '#f59e0b'],
              ['Profit Factor', (m.profit_factor || 0).toFixed(2), (m.profit_factor || 0) > 1 ? '#10b981' : '#ef4444'],
            ].map(([l, v, c]) => (
              <div key={l} className="bg-zinc-900/80 border border-zinc-800 rounded-lg p-2.5">
                <div className="text-[9px] text-zinc-500 uppercase tracking-widest">{l}</div>
                <div className="text-[15px] font-black tabular-nums mt-0.5" style={{ color: c }}>{v}</div>
              </div>
            ))}
          </div>
          {m.pnl_history?.length > 5 && (
            <div>
              <p className="text-[10px] text-zinc-500 mb-1">P&L History (per trade)</p>
              <ResponsiveContainer width="100%" height={80}>
                <LineChart data={m.pnl_history?.map((v, i) => ({ i, v: v * 100 })) || []}>
                  <Line type="monotone" dataKey="v" stroke="#3b82f6" dot={false} strokeWidth={1.5} />
                  <CartesianGrid strokeDasharray="3 3" stroke="#333" />
                  <XAxis dataKey="i" hide />
                  <YAxis hide />
                </LineChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      )}

      {/* Approvals tab */}
      {tab === 'approvals' && (
        <div className="space-y-2">
          {approvals.length === 0 ? (
            <div className="text-center py-8 text-zinc-600 text-[12px]">No pending approvals</div>
          ) : (
            approvals.map(a => <ApprovalCard key={a.id} approval={a} onResolve={resolveApproval} />)
          )}
        </div>
      )}

      {/* Alerts tab */}
      {tab === 'alerts' && (
        <div className="space-y-0.5">
          {alerts.length === 0 ? (
            <div className="text-center py-8 text-zinc-600 text-[12px]">No alerts</div>
          ) : (
            alerts.map(a => <AlertItem key={a.id} alert={a} />)
          )}
        </div>
      )}
    </div>
  );
}

// Re-export for charts used in metrics tab — recharts already imported above
