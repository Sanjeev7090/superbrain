/**
 * DailyProgressBar — Phase 4
 * Shows daily P&L progress toward user-defined target.
 * Animated, color-coded, responsive.
 */
import React from 'react';

const fmt  = (v, d = 0) => (v == null ? '—' : Number(v).toLocaleString('en-IN', { minimumFractionDigits: d, maximumFractionDigits: d }));
const fmtInr = v => `₹${fmt(v, 0)}`;

export default function DailyProgressBar({ currentPnl = 0, target = 1, dailyTargetPct = 0 }) {
  const pct       = Math.max(-10, Math.min(150, target > 0 ? (currentPnl / target) * 100 : 0));
  const isPos     = currentPnl >= 0;
  const fillPct   = Math.min(100, Math.max(0, pct));
  const remaining = Math.max(0, target - currentPnl);

  const trackColor = isPos
    ? pct >= 100 ? '#10b981' : pct >= 50 ? '#f59e0b' : '#3b82f6'
    : '#ef4444';

  const barGradient = isPos
    ? pct >= 100
      ? 'linear-gradient(90deg, #059669, #10b981, #34d399)'
      : pct >= 50
      ? 'linear-gradient(90deg, #3b82f6, #f59e0b)'
      : 'linear-gradient(90deg, #6366f1, #3b82f6)'
    : 'linear-gradient(90deg, #dc2626, #ef4444)';

  return (
    <div className="w-full" data-testid="daily-progress-bar">
      {/* Label row */}
      <div className="flex items-end justify-between mb-2">
        <div>
          <p className="text-[10px] text-zinc-500 uppercase tracking-widest mb-0.5">Daily Progress</p>
          <div className="flex items-baseline gap-2">
            <span
              className="text-2xl font-black tabular-nums"
              style={{ color: isPos ? trackColor : '#ef4444' }}
            >
              {isPos ? '+' : ''}{fmtInr(currentPnl)}
            </span>
            <span className="text-sm text-zinc-500">of {fmtInr(target)}</span>
          </div>
        </div>
        <div className="text-right">
          <span
            className="text-3xl font-black tabular-nums"
            style={{ color: pct >= 100 ? '#10b981' : trackColor }}
          >
            {Math.max(0, Math.round(pct))}%
          </span>
          {remaining > 0 && currentPnl >= 0 && (
            <p className="text-[10px] text-zinc-500 mt-0.5">{fmtInr(remaining)} remaining</p>
          )}
          {pct >= 100 && (
            <p className="text-[10px] text-emerald-400 mt-0.5 font-semibold">TARGET HIT</p>
          )}
          {currentPnl < 0 && (
            <p className="text-[10px] text-red-400 mt-0.5">In drawdown</p>
          )}
        </div>
      </div>

      {/* Track */}
      <div className="h-3 bg-zinc-800 rounded-full overflow-hidden relative">
        {/* Background pattern */}
        <div className="absolute inset-0 opacity-20"
          style={{ backgroundImage: 'repeating-linear-gradient(90deg, transparent, transparent 24px, #ffffff08 24px, #ffffff08 25px)' }}
        />
        {/* Fill */}
        <div
          className="h-full rounded-full transition-all duration-700 ease-out relative"
          style={{ width: `${fillPct}%`, background: barGradient }}
        >
          {/* Shine */}
          <div className="absolute inset-0 rounded-full"
            style={{ background: 'linear-gradient(180deg, rgba(255,255,255,0.15) 0%, transparent 100%)' }}
          />
        </div>
        {/* Target marker (at 100%) */}
        <div className="absolute right-0 top-0 h-full w-px bg-zinc-600 opacity-50" />
      </div>

      {/* Micro stats */}
      <div className="flex items-center gap-4 mt-2 text-[10px] text-zinc-600">
        <span>Start: {fmtInr(0)}</span>
        <span className="flex-1 text-center">
          {pct >= 50 && pct < 100 && <span className="text-amber-500">Halfway there</span>}
          {pct >= 100 && <span className="text-emerald-400 font-bold">Daily target achieved!</span>}
          {pct < 50 && pct >= 0 && <span>Keep going</span>}
          {pct < 0 && <span className="text-red-500">Below start</span>}
        </span>
        <span>Target: {fmtInr(target)}</span>
      </div>
    </div>
  );
}
