/**
 * useLiveTick — NSE real-time tick data via WebSocket
 *
 * Usage:
 *   const tick = useLiveTick("RELIANCE.NS")
 *   // tick → { price, change, change_pct, high, low, volume, direction, ts, connected }
 *
 * Auto-reconnects on disconnect. Cleans up on unmount.
 */
import { useState, useEffect, useRef, useCallback } from 'react';

const WS_BASE = (process.env.REACT_APP_BACKEND_URL || '')
  .replace('https://', 'wss://')
  .replace('http://', 'ws://');

const WS_URL = `${WS_BASE}/api/ws/nse-tick`;
const RECONNECT_DELAY = 3000; // ms

export function useLiveTick(symbol) {
  const [tick,      setTick]      = useState(null);
  const [connected, setConnected] = useState(false);
  const wsRef      = useRef(null);
  const timerRef   = useRef(null);
  const mountedRef = useRef(true);

  const connect = useCallback(() => {
    if (!symbol || !mountedRef.current) return;
    try {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!mountedRef.current) { ws.close(); return; }
        setConnected(true);
        ws.send(JSON.stringify({ action: 'subscribe', tickers: [symbol] }));
      };

      ws.onmessage = (ev) => {
        if (!mountedRef.current) return;
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === 'tick' && msg.data) {
            setTick({ ...msg.data, symbol: msg.ticker });
          }
        } catch { /* ignore parse errors */ }
      };

      ws.onclose = () => {
        if (!mountedRef.current) return;
        setConnected(false);
        timerRef.current = setTimeout(connect, RECONNECT_DELAY);
      };

      ws.onerror = () => {
        ws.close();
      };
    } catch { /* WebSocket not supported */ }
  }, [symbol]);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      clearTimeout(timerRef.current);
      if (wsRef.current) {
        wsRef.current.close();
        wsRef.current = null;
      }
    };
  }, [connect]);

  return { ...tick, connected };
}

/**
 * useMultiTick — subscribe to multiple symbols at once (single connection)
 *
 * Usage:
 *   const ticks = useMultiTick(["^NSEI","^NSEBANK","^BSESN"])
 *   // ticks → { "^NSEI": { price, change_pct, ... }, ... }
 */
export function useMultiTick(symbols = []) {
  const [ticks,     setTicks]     = useState({});
  const [connected, setConnected] = useState(false);
  const wsRef      = useRef(null);
  const timerRef   = useRef(null);
  const mountedRef = useRef(true);
  const symbolsKey = symbols.join(',');

  const connect = useCallback(() => {
    if (!symbols.length || !mountedRef.current) return;
    try {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        if (!mountedRef.current) { ws.close(); return; }
        setConnected(true);
        ws.send(JSON.stringify({ action: 'subscribe', tickers: symbols }));
      };

      ws.onmessage = (ev) => {
        if (!mountedRef.current) return;
        try {
          const msg = JSON.parse(ev.data);
          if (msg.type === 'tick' && msg.data) {
            setTicks(prev => ({ ...prev, [msg.ticker]: msg.data }));
          }
        } catch { /* ignore */ }
      };

      ws.onclose = () => {
        if (!mountedRef.current) return;
        setConnected(false);
        timerRef.current = setTimeout(connect, RECONNECT_DELAY);
      };

      ws.onerror = () => ws.close();
    } catch { /* no WebSocket */ }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [symbolsKey]);

  useEffect(() => {
    mountedRef.current = true;
    connect();
    return () => {
      mountedRef.current = false;
      clearTimeout(timerRef.current);
      if (wsRef.current) { wsRef.current.close(); wsRef.current = null; }
    };
  }, [connect]);

  return { ticks, connected };
}
