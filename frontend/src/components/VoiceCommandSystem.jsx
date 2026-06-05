import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Microphone, MicrophoneSlash, X, CheckCircle, Warning } from '@phosphor-icons/react';

// Command patterns — ordered from specific to general
const COMMANDS = [
  // Full commands
  { pattern: /^(?:load|open|analyze|chart|search|find|dikhao|dekho|show me)\s+([A-Z0-9.]{2,15})/i, action: 'LOAD_STOCK',    hint: '"Load RELIANCE" · "Dikhao TCS" · "Search INFY"' },
  { pattern: /^([A-Z0-9.]{2,15})\s+(?:load karo|dikha|open karo|chart dikha)/i,                   action: 'LOAD_STOCK_HINDI', hint: '"RELIANCE load karo" · "TCS dikha"' },
  { pattern: /^run\s+(mirofish|smc|demon|godzilla|pac|amds|vwap|gpt)/i,                           action: 'RUN_STRATEGY', hint: '"Run MiroFish" or "Run SMC"' },
  { pattern: /^(?:go to|switch to|show)\s+(scanner|strategies|ghost|paper|rl|workspace|monte)/i,  action: 'NAVIGATE',     hint: '"Go to Scanner" or "Show Strategies"' },
  { pattern: /^set alert (?:at\s+)?(\d+(?:\.\d+)?)/i,                                            action: 'SET_ALERT',    hint: '"Set alert at 2500"' },
  { pattern: /^(?:buy|sell|hold)/i,                                                               action: 'TRADE_SIGNAL', hint: '"Buy" or "Sell"' },
  { pattern: /^scan (?:the )?market/i,                                                            action: 'SCAN_MARKET',  hint: '"Scan the market"' },
  // Simple ticker — last resort (2-12 uppercase letters/numbers, no spaces)
  { pattern: /^([A-Z]{2,12}(?:\.(?:NS|BO))?)$/i,                                                 action: 'LOAD_STOCK',   hint: 'Just say: "RELIANCE" · "TCS" · "INFY"' },
];

const STRATEGY_TAB_MAP = {
  scanner: 'scanner', strategies: 'strategies', ghost: 'ghost',
  paper: 'paper', rl: 'rlagent', workspace: 'workspace', monte: 'montecarlo',
};

const STRATEGY_NAME_MAP = {
  mirofish: 'MiroFish', smc: 'SMC', demon: 'DEMON', godzilla: 'Godzilla',
  pac: 'PAC+S&O', amds: 'AMDS', vwap: 'VWAP', gpt: 'GPT',
};

// Words to ignore as tickers
const IGNORE_WORDS = new Set(['buy', 'sell', 'hold', 'load', 'open', 'show', 'scan', 'find', 'run', 'set', 'go', 'the', 'ok', 'okay', 'yes', 'no', 'stop', 'start']);

export default function VoiceCommandSystem({ onLoadStock, onNavigate, onSetAlert, onRunStrategy, onScanMarket }) {
  const [listening,   setListening]   = useState(false);
  const [transcript,  setTranscript]  = useState('');
  const [feedback,    setFeedback]    = useState(null);
  const [supported,   setSupported]   = useState(true);
  const [showHelp,    setShowHelp]    = useState(false);
  const recognitionRef   = useRef(null);
  const feedbackTimer    = useRef(null);
  const processCommandRef = useRef(null);   // keeps processCommand current (fixes stale closure)

  const showFeedback = useCallback((type, message) => {
    clearTimeout(feedbackTimer.current);
    setFeedback({ type, message });
    feedbackTimer.current = setTimeout(() => setFeedback(null), 3500);
  }, []);

  const processCommand = useCallback((text) => {
    const t = text.trim();
    for (const cmd of COMMANDS) {
      const m = t.match(cmd.pattern);
      if (!m) continue;

      switch (cmd.action) {
        case 'LOAD_STOCK':
        case 'LOAD_STOCK_HINDI': {
          let symbol = (m[1] || '').toUpperCase().trim();
          if (!symbol || IGNORE_WORDS.has(symbol.toLowerCase())) break;
          if (!symbol.includes('.')) symbol += '.NS';
          onLoadStock?.(symbol);
          showFeedback('success', `Loading ${symbol.replace('.NS','')}…`);
          setTranscript('');
          return;
        }
        case 'RUN_STRATEGY': {
          const strat = STRATEGY_NAME_MAP[m[1].toLowerCase()] || m[1];
          onRunStrategy?.(strat);
          showFeedback('success', `Running ${strat}…`);
          setTranscript('');
          return;
        }
        case 'NAVIGATE': {
          const dest = m[1].toLowerCase();
          const tabId = STRATEGY_TAB_MAP[dest] || dest;
          onNavigate?.(tabId);
          showFeedback('success', `Switched to ${m[1]}`);
          setTranscript('');
          return;
        }
        case 'SET_ALERT': {
          const price = parseFloat(m[1]);
          onSetAlert?.(price);
          showFeedback('success', `Alert at ₹${price.toFixed(2)}`);
          setTranscript('');
          return;
        }
        case 'TRADE_SIGNAL':
          showFeedback('success', `Signal: ${t.toUpperCase()}`);
          setTranscript('');
          return;
        case 'SCAN_MARKET':
          onScanMarket?.();
          showFeedback('success', 'Scanning market…');
          setTranscript('');
          return;
        default: break;
      }
    }
    showFeedback('error', `Samajh nahi aaya: "${t.substring(0, 40)}"`);
    setTranscript('');
  }, [onLoadStock, onNavigate, onSetAlert, onRunStrategy, onScanMarket, showFeedback]);

  // Keep ref current so onresult always calls the latest version (fixes stale closure)
  useEffect(() => {
    processCommandRef.current = processCommand;
  }, [processCommand]);

  // Setup SpeechRecognition ONCE — use processCommandRef to avoid stale closure
  useEffect(() => {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) { setSupported(false); return; }

    const rec = new SR();
    rec.continuous      = false;
    rec.interimResults  = true;
    rec.lang            = 'en-IN';
    rec.maxAlternatives = 3;   // more alternatives = better match for Indian accents

    rec.onresult = (e) => {
      // Try all alternatives, pick best match
      const results = e.results[0];
      const text = results[0].transcript;
      setTranscript(text);
      if (results.isFinal) {
        // Try each alternative transcript for better matching
        let processed = false;
        for (let i = 0; i < results.length && !processed; i++) {
          const alt = results[i].transcript.trim();
          if (alt) {
            processCommandRef.current?.(alt);
            processed = true;
          }
        }
      }
    };

    rec.onerror = (e) => {
      if (e.error === 'not-allowed') {
        showFeedback('error', 'Mic permission denied. Please allow microphone access.');
      } else if (e.error === 'no-speech') {
        showFeedback('error', 'Kuch suna nahi, phir try karo');
      } else if (e.error !== 'aborted') {
        showFeedback('error', `Error: ${e.error}`);
      }
      setListening(false);
      setTranscript('');
    };

    rec.onend = () => {
      setListening(false);
    };

    recognitionRef.current = rec;
    return () => {
      try { rec.abort(); } catch (e) {}
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleListen = useCallback(() => {
    const rec = recognitionRef.current;
    if (!rec) return;
    if (listening) {
      rec.stop();
      setListening(false);
      setTranscript('');
    } else {
      try {
        rec.start();
        setListening(true);
        setTranscript('');
        setFeedback(null);
      } catch (e) {
        // Already running — abort and restart
        try { rec.abort(); } catch (_) {}
        setTimeout(() => {
          try { rec.start(); setListening(true); } catch (_) {}
        }, 200);
      }
    }
  }, [listening]);

  if (!supported) return null;

  return (
    <div className="fixed bottom-20 right-4 z-[90] flex flex-col items-end gap-2" data-testid="voice-command-system">

      {/* Help panel */}
      {showHelp && (
        <div className="bg-[#0d0d0d] border border-white/10 rounded-xl p-4 w-72 shadow-2xl">
          <div className="flex items-center justify-between mb-3">
            <span className="text-[11px] font-bold text-white uppercase tracking-widest">Voice Commands</span>
            <button onClick={() => setShowHelp(false)} className="text-zinc-500 hover:text-white">
              <X size={14} />
            </button>
          </div>
          <div className="space-y-2">
            {COMMANDS.map((c, i) => (
              <div key={i} className="text-[10px]">
                <span className="text-violet-400 font-mono">{c.hint}</span>
              </div>
            ))}
          </div>
          <p className="text-[9px] text-zinc-600 mt-3 border-t border-white/5 pt-2">
            Hindi accent supported (en-IN) · Just say a ticker to load it
          </p>
        </div>
      )}

      {/* Transcript bubble */}
      {(listening || transcript) && (
        <div className="bg-[#0d0d0d] border border-violet-500/30 rounded-xl px-4 py-2 max-w-[280px] shadow-xl">
          <div className="flex items-center gap-2 mb-1">
            <span className="w-1.5 h-1.5 rounded-full bg-violet-500 animate-pulse" />
            <span className="text-[10px] text-violet-400 font-bold uppercase tracking-widest">
              {transcript ? 'Suna…' : 'Bol…'}
            </span>
          </div>
          {transcript && <p className="text-xs text-white font-mono">{transcript}</p>}
        </div>
      )}

      {/* Feedback bubble */}
      {feedback && (
        <div className={`flex items-center gap-2 rounded-xl px-4 py-2 border shadow-xl ${
          feedback.type === 'success'
            ? 'bg-emerald-950/80 border-emerald-500/30'
            : 'bg-red-950/80 border-red-500/30'
        }`}>
          {feedback.type === 'success'
            ? <CheckCircle size={14} className="text-emerald-400 shrink-0" />
            : <Warning      size={14} className="text-red-400 shrink-0" />}
          <span className={`text-xs font-mono ${feedback.type === 'success' ? 'text-emerald-300' : 'text-red-300'}`}>
            {feedback.message}
          </span>
        </div>
      )}

      {/* Mic button */}
      <div className="flex items-center gap-2">
        {/* Help toggle */}
        <button
          onClick={() => setShowHelp(h => !h)}
          className="w-8 h-8 rounded-full bg-[#1a1a2e] border border-white/10 flex items-center justify-center text-zinc-500 hover:text-white hover:border-white/30 transition-all"
          title="Voice command help"
          data-testid="voice-help-btn"
        >
          <span className="text-[11px] font-bold">?</span>
        </button>

        {/* Main mic button */}
        <button
          onClick={toggleListen}
          data-testid="voice-mic-btn"
          className={`relative w-12 h-12 rounded-full flex items-center justify-center transition-all duration-300 shadow-lg ${
            listening
              ? 'bg-violet-600 scale-110 shadow-violet-500/40'
              : 'bg-[#1a1a2e] border border-white/15 hover:border-violet-500/50 hover:scale-105'
          }`}
        >
          {listening && (
            <>
              <span className="absolute inset-0 rounded-full bg-violet-500/20 animate-ping" />
              <span className="absolute inset-[-4px] rounded-full border border-violet-500/30 animate-pulse" />
            </>
          )}
          {listening
            ? <MicrophoneSlash size={20} weight="fill" className="text-white relative z-10" />
            : <Microphone      size={20} weight="fill" className="text-violet-400 relative z-10" />}
        </button>
      </div>
    </div>
  );
}
