/**
 * LiveTickBadge — Real-time price display with direction animation
 *
 * Props:
 *   symbol     (string) — e.g. "RELIANCE.NS", "NIFTY", "^NSEI"
 *   showChange (bool)   — show ±change% (default true)
 *   size       (string) — "sm" | "md" | "lg"  (default "sm")
 *   className  (string)
 */
import React, { useState, useEffect, useRef } from 'react';
import { useLiveTick } from '../hooks/useLiveTick';
import { TrendingUp, TrendingDown, Minus, Wifi, WifiOff } from 'lucide-react';

const fmtPrice = (p) => {
  if (p == null) return '—';
  return p >= 1000
    ? p.toLocaleString('en-IN', { maximumFractionDigits: 1 })
    : p.toLocaleString('en-IN', { maximumFractionDigits: 2 });
};

const fmtChange = (c) => {
  if (c == null) return '';
  return (c >= 0 ? '+' : '') + c.toFixed(2) + '%';
};

export function LiveTickBadge({ symbol, showChange = true, size = 'sm', className = '', dataTestId }) {
  const tick = useLiveTick(symbol);
  const [flash, setFlash] = useState('');
  const prevPrice = useRef(null);

  // Flash animation on price change
  useEffect(() => {
    if (!tick?.price) return;
    if (prevPrice.current !== null && prevPrice.current !== tick.price) {
      setFlash(tick.price > prevPrice.current ? 'up' : 'down');
      const t = setTimeout(() => setFlash(''), 600);
      return () => clearTimeout(t);
    }
    prevPrice.current = tick.price;
  }, [tick?.price]);

  const isUp   = (tick?.change_pct ?? 0) >= 0;
  const color  = isUp ? '#34d399' : '#f87171';

  const textSize = size === 'lg' ? 'text-base font-black'
    : size === 'md' ? 'text-sm font-black'
    : 'text-[11px] font-black';

  const changeSize = size === 'lg' ? 'text-xs'
    : size === 'md' ? 'text-[10px]'
    : 'text-[9px]';

  return (
    <div
      className={`flex items-center gap-1.5 ${className}`}
      data-testid={dataTestId || `live-tick-${(symbol || '').replace(/[^a-z0-9]/gi, '-')}`}
    >
      {/* Price */}
      <span
        className={`${textSize} font-mono transition-colors duration-300`}
        style={{
          color: flash === 'up'   ? '#34d399'
               : flash === 'down' ? '#f87171'
               : '#ffffff',
        }}
      >
        {tick?.price != null ? `₹${fmtPrice(tick.price)}` : '—'}
      </span>

      {/* Change */}
      {showChange && tick?.change_pct != null && (
        <span
          className={`${changeSize} font-semibold flex items-center gap-0.5`}
          style={{ color }}
        >
          {isUp
            ? <TrendingUp  size={9} />
            : <TrendingDown size={9} />}
          {fmtChange(tick.change_pct)}
        </span>
      )}

      {/* Connection dot */}
      <span
        className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${tick?.connected ? 'animate-pulse' : ''}`}
        style={{ background: tick?.connected ? '#34d399' : '#6b7280' }}
        title={tick?.connected ? 'Live' : 'Connecting…'}
      />
    </div>
  );
}

/**
 * LiveTickInline — compact inline version (for headers, badges)
 * Shows price + direction arrow only
 */
export function LiveTickInline({ symbol, className = '' }) {
  const tick = useLiveTick(symbol);
  const [flash, setFlash] = useState('');
  const prevPrice = useRef(null);

  useEffect(() => {
    if (!tick?.price) return;
    if (prevPrice.current !== null && prevPrice.current !== tick.price) {
      setFlash(tick.price > prevPrice.current ? 'up' : 'down');
      const t = setTimeout(() => setFlash(''), 500);
      return () => clearTimeout(t);
    }
    prevPrice.current = tick.price;
  }, [tick?.price]);

  if (!tick?.price) {
    return (
      <span className={`text-[9px] text-zinc-600 font-mono ${className}`}>
        {tick?.connected ? '…' : '—'}
      </span>
    );
  }

  const isUp = (tick.change_pct ?? 0) >= 0;

  return (
    <span
      className={`flex items-center gap-0.5 transition-colors duration-300 ${className}`}
      style={{
        color: flash === 'up' ? '#34d399' : flash === 'down' ? '#f87171' : (isUp ? '#34d399' : '#f87171'),
      }}
      data-testid={`live-tick-inline-${(symbol || '').replace(/[^a-z0-9]/gi, '-')}`}
    >
      <span className="text-[10px] font-mono font-black">₹{fmtPrice(tick.price)}</span>
      <span className="text-[8px]">{isUp ? '▲' : '▼'}{Math.abs(tick.change_pct ?? 0).toFixed(2)}%</span>
      <span
        className={`w-1 h-1 rounded-full flex-shrink-0 ${tick.connected ? 'animate-pulse' : ''}`}
        style={{ background: tick.connected ? '#34d399' : '#6b7280' }}
      />
    </span>
  );
}
