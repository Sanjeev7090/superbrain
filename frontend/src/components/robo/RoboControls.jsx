/**
 * RoboControls — Phase 4
 * Start/Stop toggle, Mode selector (Paper/Shadow/Live), Interval picker.
 * Live mode shows full-screen warning modal before activation.
 */
import React, { useState } from 'react';
import { Play, Square, AlertTriangle } from 'lucide-react';

// ── Live Mode Warning Modal ───────────────────────────────────────────────────
function LiveWarningModal({ onConfirm, onCancel, loading }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/85 backdrop-blur-sm px-4">
      <div className="bg-zinc-950 border border-red-700/60 rounded-2xl w-full max-w-md shadow-2xl">
        <div className="p-5 border-b border-red-900/40">
          <div className="flex items-center gap-3">
            <AlertTriangle size={20} className="text-red-400 flex-shrink-0" />
            <h2 className="font-black text-red-400 text-base">LIVE TRADING WARNING</h2>
          </div>
        </div>
        <div className="p-5 space-y-3 text-sm">
          <p className="font-semibold text-red-300">
            Switching to LIVE mode will place REAL orders on your Groww account.
          </p>
          <ul className="space-y-1.5 text-xs text-zinc-400 list-disc ml-4">
            <li>Real capital from your Groww account will be used</li>
            <li>30-second confirmation delay before each order</li>
            <li>30% position size reduction applied as safety margin</li>
            <li>GROWW_API_KEY + GROWW_API_SECRET must be in backend/.env</li>
            <li>Circuit breakers active — will close positions on drawdown</li>
            <li>No guaranteed returns — trading involves risk of total loss</li>
          </ul>
          <p className="text-[10px] text-zinc-600 italic">
            This software is provided as-is. By confirming, you accept full financial responsibility.
          </p>
        </div>
        <div className="flex gap-3 p-5 border-t border-zinc-800">
          <button
            onClick={onCancel}
            className="flex-1 py-2.5 bg-zinc-800 hover:bg-zinc-700 border border-zinc-700 text-zinc-300 rounded-xl text-sm font-semibold transition-colors"
          >
            Stay in Paper Mode
          </button>
          <button
            onClick={onConfirm}
            disabled={loading}
            className="flex-1 py-2.5 bg-red-700 hover:bg-red-600 text-white rounded-xl text-sm font-black transition-colors disabled:opacity-50"
            data-testid="confirm-live-btn"
          >
            {loading ? 'Switching…' : 'I Understand — Go Live'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Main RoboControls component ───────────────────────────────────────────────
export default function RoboControls({
  isActive,
  execMode,
  intervalMin,
  circuitBreaker,
  loading,
  modeLoading,
  onToggleAuto,
  onModeChange,
  onSetInterval,
}) {
  const [showLiveWarn, setShowLiveWarn] = useState(false);
  const [liveLoading,  setLiveLoading]  = useState(false);

  const handleModeClick = (newMode) => {
    if (newMode === 'live' && execMode !== 'live') {
      setShowLiveWarn(true);
    } else {
      onModeChange(newMode);
    }
  };

  const handleConfirmLive = async () => {
    setLiveLoading(true);
    try {
      await onModeChange('live');
    } finally {
      setLiveLoading(false);
      setShowLiveWarn(false);
    }
  };

  const modes = [
    { key: 'paper',  label: 'Paper',  color: '#10b981', desc: 'Simulate. No real orders.' },
    { key: 'shadow', label: 'Shadow', color: '#818cf8', desc: 'Observe only. Zero execution.' },
    { key: 'live',   label: 'Live',   color: '#ef4444', desc: 'REAL Groww orders. RISK.' },
  ];

  const intervals = [1, 5, 10, 15, 30];

  return (
    <>
      {showLiveWarn && (
        <LiveWarningModal
          onConfirm={handleConfirmLive}
          onCancel={() => setShowLiveWarn(false)}
          loading={liveLoading}
        />
      )}

      <div className="space-y-3" data-testid="robo-controls">
        {/* Start / Stop */}
        <button
          onClick={onToggleAuto}
          disabled={loading || circuitBreaker}
          data-testid="toggle-auto-btn"
          className={`w-full py-3.5 rounded-xl font-black text-sm flex items-center justify-center gap-2.5 transition-all duration-200 ${
            isActive
              ? 'bg-red-600/15 hover:bg-red-600/25 border border-red-500/40 text-red-300 hover:text-red-200'
              : circuitBreaker
              ? 'bg-zinc-800/50 border border-zinc-700 text-zinc-600 cursor-not-allowed'
              : 'bg-emerald-600/15 hover:bg-emerald-600/25 border border-emerald-500/40 text-emerald-300 hover:text-emerald-200'
          }`}
          style={{
            boxShadow: isActive
              ? '0 0 20px rgba(239,68,68,0.15)'
              : circuitBreaker
              ? 'none'
              : '0 0 20px rgba(16,185,129,0.15)',
          }}
        >
          {loading ? (
            <span className="animate-spin inline-block w-4 h-4 border-2 border-current border-t-transparent rounded-full" />
          ) : isActive ? (
            <Square size={14} weight="fill" />
          ) : (
            <Play size={14} weight="fill" />
          )}
          {loading ? 'Processing…' : isActive ? 'Stop Auto Mode' : 'Start Auto Mode'}
        </button>

        {circuitBreaker && (
          <div className="bg-red-900/20 border border-red-600/40 rounded-xl px-3 py-2 text-[10px] text-red-400 flex items-center gap-2">
            <AlertTriangle size={12} />
            Circuit breaker active. Reset to resume.
          </div>
        )}

        {/* Execution Mode */}
        <div>
          <p className="text-[9px] text-zinc-600 uppercase tracking-widest mb-1.5 font-semibold">
            Execution Mode
          </p>
          <div className="grid grid-cols-3 gap-1.5">
            {modes.map(({ key, label, color, desc }) => (
              <button
                key={key}
                onClick={() => handleModeClick(key)}
                disabled={modeLoading}
                data-testid={`mode-btn-${key}`}
                title={desc}
                className={`py-2 rounded-xl text-[10px] font-bold transition-all border ${
                  execMode === key
                    ? 'opacity-100'
                    : 'bg-zinc-900 border-zinc-800 text-zinc-500 hover:border-zinc-700 opacity-70 hover:opacity-100'
                }`}
                style={execMode === key ? {
                  background: color + '18',
                  borderColor: color + '50',
                  color,
                  boxShadow: `0 0 12px ${color}20`,
                } : {}}
              >
                {key === 'paper' ? '📄' : key === 'shadow' ? '👁' : '🔴'} {label}
              </button>
            ))}
          </div>
          <p className="text-[9px] text-zinc-600 mt-1 text-center">
            {modes.find(m => m.key === execMode)?.desc}
          </p>
        </div>

        {/* Scan Interval */}
        <div>
          <p className="text-[9px] text-zinc-600 uppercase tracking-widest mb-1.5 font-semibold">
            Scan Interval
          </p>
          <div className="flex gap-1.5">
            {intervals.map(m => (
              <button
                key={m}
                onClick={() => onSetInterval(m)}
                data-testid={`interval-btn-${m}`}
                className={`flex-1 py-1.5 rounded-lg text-[10px] font-bold border transition-all ${
                  intervalMin === m
                    ? 'bg-violet-600/20 border-violet-500/50 text-violet-300'
                    : 'bg-zinc-900 border-zinc-800 text-zinc-500 hover:border-zinc-700'
                }`}
              >
                {m}m
              </button>
            ))}
          </div>
        </div>

        {/* Safety checklist */}
        <div className="space-y-1.5">
          {[
            { icon: '✓', text: 'Capital protection priority',    color: '#10b981' },
            { icon: '✓', text: 'Daily loss kill-switch active',  color: '#10b981' },
            { icon: '✓', text: 'EOD close at 15:15 IST',         color: '#10b981' },
            { icon: '✓', text: 'Circuit breaker on 5% drawdown', color: '#10b981' },
          ].map(({ icon, text, color }) => (
            <div key={text} className="flex items-center gap-2 text-[10px] text-zinc-500">
              <span style={{ color }}>{icon}</span>
              {text}
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
