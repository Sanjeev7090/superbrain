import React, { useState, useEffect, useRef } from 'react';

const API = `${process.env.REACT_APP_BACKEND_URL}/api/ensemble`;

// ─── Helpers ────────────────────────────────────────────────────────────────
const SIG_STYLE = {
  BUY:     { bg: '#00E676', text: '#000', glow: '0 0 10px #00E67688' },
  SELL:    { bg: '#FF3B30', text: '#fff', glow: '0 0 10px #FF3B3088' },
  HOLD:    { bg: '#F5A623', text: '#000', glow: '0 0 8px #F5A62366'  },
  WAIT:    { bg: '#F5A623', text: '#000', glow: '0 0 8px #F5A62366'  },
};
const sigStyle = (s) => SIG_STYLE[s?.toUpperCase()] || { bg: '#3F3F46', text: '#A1A1AA', glow: 'none' };

const FAMILY_COLORS = {
  claude:   '#FF6B35',
  gemini:   '#4285F4',
  gpt:      '#74AA9C',
  grok:     '#A855F7',
  deepseek: '#06B6D4',
  glm:      '#F59E0B',
  minimax:  '#EC4899',
  kimi:     '#3B82F6',
  qwen:     '#84CC16',
  kronos:   '#A855F7',
  other:    '#71717A',
};
const familyColor = (f) => FAMILY_COLORS[f] || FAMILY_COLORS.other;
const fmt = (v) => (v != null && !isNaN(+v) ? `₹${(+v).toFixed(1)}` : '—');

// ─── Mini confidence bar ─────────────────────────────────────────────────────
function ConfBar({ value, color }) {
  const v = Math.max(0, Math.min(100, value || 0));
  return (
    <div className="w-full bg-zinc-800/80 rounded-full h-0.5">
      <div className="h-full rounded-full" style={{ width: `${v}%`, background: color, transition: 'width 0.6s' }} />
    </div>
  );
}

// ─── Compact row card (for numbered list) ────────────────────────────────────
function ModelRow({ result }) {
  const [expanded, setExpanded] = useState(false);
  const sig = (result.signal || 'HOLD').toUpperCase();
  const ss  = sigStyle(sig);
  const fc  = familyColor(result.family);
  const ok  = result.ok !== false;

  return (
    <div
      className="border-b border-zinc-800/60 hover:bg-zinc-900/60 transition-colors cursor-pointer"
      onClick={() => ok && setExpanded(e => !e)}
      data-testid={`model-row-${result.num}`}
    >
      {/* Main row */}
      <div className="flex items-center gap-2 px-3 py-1.5">
        {/* Number */}
        <span
          className="text-[9px] font-black w-5 text-right flex-shrink-0 font-mono"
          style={{ color: fc }}
        >
          {result.num}
        </span>

        {/* Family indicator */}
        <span className="w-1 h-3 rounded-full flex-shrink-0" style={{ background: fc }} />

        {/* Model name */}
        <span className="text-[10px] text-zinc-300 flex-1 truncate font-medium">{result.model}</span>

        {/* Signal badge or status */}
        {ok ? (
          <>
            <span
              className="text-[8px] font-black px-1.5 py-0.5 rounded-sm flex-shrink-0"
              style={{ background: ss.bg + '22', color: ss.bg, border: `1px solid ${ss.bg}44` }}
            >
              {sig}
            </span>
            <span className="text-[9px] font-mono text-zinc-500 w-8 text-right flex-shrink-0">
              {result.confidence || 0}%
            </span>
            <span className="text-[9px] font-mono text-zinc-400 w-16 text-right truncate flex-shrink-0">
              {fmt(result.entry_price)}
            </span>
            <span className="text-[9px] font-mono text-rose-400 w-16 text-right truncate flex-shrink-0">
              {fmt(result.stop_loss)}
            </span>
            <span className="text-[9px] font-mono text-emerald-400 w-16 text-right truncate flex-shrink-0">
              {fmt(result.target_1)}
            </span>
          </>
        ) : (
          <span className="text-[9px] text-zinc-600 flex-shrink-0">
            {result.error?.includes('401') || result.error?.includes('auth')
              ? '⚙ Setup 9router'
              : result.error?.includes('timeout')
              ? '⏱ Timeout'
              : '✕ Failed'}
          </span>
        )}
      </div>

      {/* Expanded detail */}
      {expanded && ok && (
        <div className="px-3 pb-2 pt-0.5 bg-zinc-900/40 space-y-1.5">
          {/* Price grid */}
          <div className="grid grid-cols-5 gap-1 text-center text-[9px]">
            {[
              { label: 'ENTRY', val: result.entry_price, color: '#A1A1AA' },
              { label: 'SL',    val: result.stop_loss,   color: '#FF3B30' },
              { label: 'T1',    val: result.target_1,    color: '#00E676' },
              { label: 'T2',    val: result.target_2,    color: '#00C853' },
              { label: 'T3',    val: result.target_3,    color: '#69F0AE' },
            ].map(({ label, val, color }) => (
              <div key={label} className="bg-black/30 rounded px-1 py-1">
                <div className="text-[7px] uppercase tracking-widest" style={{ color }}>{label}</div>
                <div className="font-mono font-bold text-zinc-200 mt-0.5">{fmt(val)}</div>
              </div>
            ))}
          </div>
          <ConfBar value={result.confidence} color={fc} />
          {result.rationale && (
            <p className="text-[9px] text-zinc-500 leading-relaxed line-clamp-2">{result.rationale}</p>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Consensus bar ───────────────────────────────────────────────────────────
function ConsensusStrip({ counts, total, avgConf, consensus }) {
  if (!counts) return null;
  const buy  = counts.BUY  || 0;
  const sell = counts.SELL || 0;
  const hold = counts.HOLD || 0;
  const ss   = sigStyle(consensus);
  return (
    <div className="px-3 py-2.5 bg-[#0E0E10] border-b border-zinc-800/60">
      <div className="flex items-center justify-between mb-1.5">
        <div className="flex items-center gap-2">
          <span
            className="text-xs font-black px-2 py-0.5 rounded-sm"
            style={{ background: ss.bg, color: ss.text, boxShadow: ss.glow }}
            data-testid="consensus-signal"
          >
            {consensus}
          </span>
          <span className="text-[9px] text-zinc-400">Assemble Consensus</span>
        </div>
        <span className="text-[9px] font-mono text-zinc-400" data-testid="consensus-confidence">
          Avg {avgConf}% confidence
        </span>
      </div>
      {/* Vote bar */}
      <div className="flex rounded-sm overflow-hidden h-2" style={{ gap: 1 }}>
        {buy  > 0 && <div style={{ flex: buy,  background: '#00E676' }} title={`BUY: ${buy}`} />}
        {hold > 0 && <div style={{ flex: hold, background: '#F5A623' }} title={`HOLD: ${hold}`} />}
        {sell > 0 && <div style={{ flex: sell, background: '#FF3B30' }} title={`SELL: ${sell}`} />}
      </div>
      <div className="flex justify-between mt-1 text-[8px] font-mono">
        <span className="text-[#00E676]">BUY {buy}</span>
        <span className="text-[#F5A623]">HOLD {hold}</span>
        <span className="text-[#FF3B30]">SELL {sell}</span>
        <span className="text-zinc-600">{buy + sell + hold}/{total} responded</span>
      </div>
    </div>
  );
}

// ─── Loading progress ────────────────────────────────────────────────────────
function LoadingBar({ label }) {
  const [dots, setDots] = useState('');
  useEffect(() => {
    const t = setInterval(() => setDots(d => d.length < 3 ? d + '.' : ''), 400);
    return () => clearInterval(t);
  }, []);
  return (
    <div className="flex flex-col items-center justify-center py-12 gap-3">
      <div className="w-8 h-8 border-2 border-fuchsia-500/40 border-t-fuchsia-400 rounded-full animate-spin" />
      <div className="text-[10px] text-zinc-400">{label}{dots}</div>
      <div className="text-[9px] text-zinc-600">4 AI calls → 45 model results</div>
    </div>
  );
}

// ─── Main panel ──────────────────────────────────────────────────────────────
export default function EnsembleCockpitPanel({ selectedStock }) {
  const [status, setStatus]           = useState(null);
  const [busy, setBusy]               = useState(false);
  const [activeTask, setActiveTask]   = useState(null);
  const [signalResult, setSignalResult] = useState(null);
  const [fullResult, setFullResult]   = useState(null);
  const [gannResult, setGannResult]   = useState(null);
  const [error, setError]             = useState(null);
  const [mode, setMode]               = useState('standard'); // 'standard' | 'full'

  useEffect(() => {
    fetch(`${API}/status`).then(r => r.json()).then(setStatus).catch(() => {});
  }, []);

  const ticker = selectedStock?.ticker || selectedStock?.id || 'RELIANCE.NS';

  const runSignal = async () => {
    setBusy(true); setActiveTask('signal'); setError(null); setMode('standard');
    try {
      const r = await fetch(`${API}/signal`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker }),
      });
      const data = await r.json();
      if (!data.success) throw new Error(data.error || 'failed');
      setSignalResult(data);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); setActiveTask(null); }
  };

  const runFullAnalysis = async () => {
    setBusy(true); setActiveTask('full'); setError(null); setMode('full');
    try {
      const r = await fetch(`${API}/full-analysis`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker }),
      });
      const data = await r.json();
      if (!data.success) throw new Error(data.error || 'failed');
      setFullResult(data);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); setActiveTask(null); }
  };

  const runGann = async () => {
    setBusy(true); setActiveTask('gann'); setError(null);
    try {
      const r = await fetch(`${API}/gann-optimize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker }),
      });
      const data = await r.json();
      if (!data.success) throw new Error(data.error || 'failed');
      setGannResult(data);
    } catch (e) { setError(e.message); }
    finally { setBusy(false); setActiveTask(null); }
  };

  const fmt2 = (v) => (v != null && !isNaN(+v) ? `₹${(+v).toFixed(2)}` : '—');

  return (
    <div className="flex flex-col bg-[#0A0A0A] text-white h-full" data-testid="ensemble-cockpit">

      {/* Header */}
      <div className="px-3 py-2.5 border-b border-zinc-800/80 bg-[#0E0E10] flex-shrink-0">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-[11px] font-black uppercase tracking-[0.15em] text-white">AI ASSEMBLE</div>
            <div className="text-[8px] text-zinc-600 mt-0.5">
              {status?.key_configured
                ? <span className="text-emerald-500">● Emergent Key Active</span>
                : <span className="text-rose-500">● No Key</span>}
              &nbsp;·&nbsp;
              <span className="text-zinc-500">{ticker}</span>
            </div>
          </div>
          <div className="text-right text-[8px] text-zinc-600">
            <div>45 Models</div>
            <div>Claude · GPT · Gemini + 30 More</div>
          </div>
        </div>
      </div>

      {/* Action buttons */}
      <div className="flex-shrink-0 px-3 py-2 grid grid-cols-3 gap-1.5 border-b border-zinc-800/60 bg-[#0C0C0C]">
        <button
          onClick={runSignal}
          disabled={busy}
          className="py-1.5 text-[9px] font-black uppercase tracking-wider border border-fuchsia-500/40 text-fuchsia-300 hover:bg-fuchsia-500/10 disabled:opacity-40 transition-all rounded-sm flex items-center justify-center gap-1"
          data-testid="btn-ask-ensemble-signal"
        >
          {activeTask === 'signal'
            ? <span className="animate-spin inline-block">↻</span>
            : '⚡'}
          3 Models
        </button>
        <button
          onClick={runFullAnalysis}
          disabled={busy}
          className="py-1.5 text-[9px] font-black uppercase tracking-wider border border-[#00E676]/40 text-[#00E676] hover:bg-[#00E676]/10 disabled:opacity-40 transition-all rounded-sm flex items-center justify-center gap-1"
          data-testid="btn-full-analysis"
        >
          {activeTask === 'full'
            ? <span className="animate-spin inline-block">↻</span>
            : '◉'}
          All 45
        </button>
        <button
          onClick={runGann}
          disabled={busy}
          className="py-1.5 text-[9px] font-black uppercase tracking-wider border border-cyan-500/40 text-cyan-300 hover:bg-cyan-500/10 disabled:opacity-40 transition-all rounded-sm flex items-center justify-center gap-1"
          data-testid="btn-ai-gann-optimize"
        >
          {activeTask === 'gann'
            ? <span className="animate-spin inline-block">↻</span>
            : '◎'}
          Gann+SoQ
        </button>
      </div>

      {/* Column headers (for list) */}
      {(signalResult || fullResult) && !busy && (
        <div className="flex-shrink-0 flex items-center gap-2 px-3 py-1 bg-[#0B0B0D] border-b border-zinc-800/60">
          <span className="text-[7px] uppercase tracking-widest text-zinc-600 w-5 text-right">#</span>
          <span className="w-1 flex-shrink-0" />
          <span className="text-[7px] uppercase tracking-widest text-zinc-600 flex-1">Model</span>
          <span className="text-[7px] uppercase tracking-widest text-zinc-600 w-10 text-right">Signal</span>
          <span className="text-[7px] uppercase tracking-widest text-zinc-600 w-8 text-right">Conf</span>
          <span className="text-[7px] uppercase tracking-widest text-zinc-600 w-16 text-right">Entry</span>
          <span className="text-[7px] uppercase tracking-widest text-zinc-600 w-16 text-right">SL</span>
          <span className="text-[7px] uppercase tracking-widest text-zinc-600 w-16 text-right">T1</span>
        </div>
      )}

      {error && (
        <div className="flex-shrink-0 mx-3 mt-2 text-[10px] text-rose-400 border border-rose-500/30 bg-rose-500/8 rounded px-2 py-1.5" data-testid="ensemble-error">
          ⚠ {error}
        </div>
      )}
      {/* Scrollable content */}
      <div className="flex-1 overflow-y-auto min-h-0">

        {/* LOADING */}
        {busy && activeTask === 'full' && <LoadingBar label="Asking all 45 models (4 AI calls)" />}
        {busy && activeTask === 'signal' && <LoadingBar label="Asking 3 models" />}
        {busy && activeTask === 'gann' && <LoadingBar label="Running AI Gann + SoQ optimisation" />}

        {/* ── FULL ANALYSIS (45 models) ── */}
        {!busy && mode === 'full' && fullResult && (
          <div data-testid="full-analysis-result">
            {fullResult.budget_warning && (
              <div className="mx-3 mt-2 px-3 py-2 border border-amber-500/30 bg-amber-500/8 rounded text-[9px]" data-testid="budget-warning">
                <div className="font-bold text-amber-400 mb-0.5">Universal Key Budget Exceeded</div>
                <div className="text-zinc-500 leading-relaxed">
                  Go to <span className="text-zinc-300">Profile → Universal Key → Add Balance</span> to top up and re-run.
                </div>
              </div>
            )}
            <ConsensusStrip
              counts={fullResult.vote_counts}
              total={fullResult.total}
              avgConf={fullResult.avg_confidence}
              consensus={fullResult.consensus}
            />
            <div>
              {fullResult.models.map((r) => (
                <ModelRow key={r.num} result={r} />
              ))}
            </div>
          </div>
        )}

        {/* ── STANDARD (3 models) ── */}
        {!busy && mode === 'standard' && signalResult && (
          <div data-testid="signal-result">
            {/* Mini consensus */}
            <div className="px-3 py-2 bg-[#0E0E10] border-b border-zinc-800/60 flex items-center gap-3">
              {(() => {
                const v = signalResult.verdict;
                const ss = sigStyle(v.consensus);
                return (
                  <>
                    <span
                      className="text-[10px] font-black px-2 py-0.5 rounded-sm"
                      style={{ background: ss.bg, color: ss.text }}
                      data-testid="consensus-signal"
                    >{v.consensus}</span>
                    <span className="text-[9px] text-zinc-400">
                      {v.valid_voters}/{v.total_voters} models agree · {v.confidence}% conf
                    </span>
                  </>
                );
              })()}
            </div>
            {signalResult.verdict.per_model.map((r, i) => (
              <ModelRow key={i} result={{ ...r, num: i + 1, family: r.provider === 'kronos' ? 'kronos' : r.provider }} />
            ))}
            {!signalResult.kronos_loaded && (
              <div className="px-3 py-2 text-[9px] text-[#A855F7] border-t border-zinc-800/50 bg-[#A855F7]/5">
                Kronos AI — Load model via WARMUP button below, then rerun.
              </div>
            )}
            {signalResult.context && (
              <div className="border-t border-zinc-800/60 px-3 py-2">
                <div className="text-[8px] font-bold uppercase tracking-widest text-zinc-600 mb-1.5">Market Context</div>
                <div className="grid grid-cols-3 gap-x-4 gap-y-1">
                  {Object.entries(signalResult.context).map(([k, v]) => (
                    <div key={k} className="text-[9px]">
                      <span className="text-zinc-600 uppercase tracking-wider text-[7px]">{k.replace(/_/g,' ')}</span>
                      <div className="font-mono text-zinc-300">{String(v)}</div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── GANN RESULT ── */}
        {!busy && gannResult && (
          <div className="border-t border-zinc-800/60 pt-2 pb-4 px-3" data-testid="gann-result">
            <div className="text-[8px] font-black uppercase tracking-[0.2em] text-cyan-400 mb-2">AI Gann + SoQ Result</div>
            {(() => {
              const v = gannResult.ensemble;
              if (!v) return null;
              const ss = sigStyle(v.consensus);
              return (
                <div className="flex items-center gap-3 mb-3 px-2 py-2 rounded border"
                  style={{ borderColor: ss.bg + '44', background: ss.bg + '10' }}>
                  <span className="text-sm font-black" style={{ color: ss.bg }}>{v.consensus}</span>
                  <span className="text-[9px] text-zinc-400">{v.confidence}% confidence</span>
                </div>
              );
            })()}
            {gannResult.ensemble?.votes?.map((v, i) => (
              <ModelRow key={i} result={{ ...v, num: i + 1, family: 'gann' }} />
            ))}
            {/* SoQ levels table */}
            {gannResult.soq_levels?.length > 0 && (
              <div className="mt-3 border border-zinc-800 rounded overflow-hidden">
                <div className="px-2 py-1 text-[7px] uppercase tracking-widest text-zinc-500 border-b border-zinc-800 bg-zinc-900/40">
                  Square of 9 — Ring {gannResult.soq_ring}
                </div>
                <div className="max-h-40 overflow-y-auto">
                  {gannResult.soq_levels.map(l => (
                    <div key={l.step} className="flex items-center gap-3 px-2 py-1 border-b border-zinc-800/40 text-[9px] font-mono hover:bg-zinc-800/30">
                      <span className="text-zinc-600 w-4">{l.step}</span>
                      <span className="text-zinc-500 w-8">{l.angle_deg}°</span>
                      <span className="text-emerald-400 flex-1">R: ₹{l.resistance}</span>
                      <span className="text-rose-400">S: ₹{l.support}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Empty state */}
        {!busy && !signalResult && !fullResult && !gannResult && (
          <div className="flex flex-col items-center justify-center py-12 text-center px-4">
            <div className="text-3xl mb-3">◉</div>
            <div className="text-[11px] text-zinc-400 font-medium">Stock select karke button dabao</div>
            <div className="text-[9px] text-zinc-600 mt-2 leading-relaxed">
              <span className="text-fuchsia-400">⚡ 3 Models</span> — Claude, Gemini, GPT (fast ~10s)<br/>
              <span className="text-[#00E676]">◉ All 45</span> — Sabhi models ka full analysis (~35s)
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
