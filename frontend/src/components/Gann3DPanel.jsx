import React, { useRef, useEffect, useState } from 'react';
import * as THREE from 'three';
import { X, Sparkle, ArrowsClockwise } from '@phosphor-icons/react';

const ENS_API = `${process.env.REACT_APP_BACKEND_URL}/api/ensemble`;

const TABS = [
  { id: 'spiral',  label: 'Gann Spiral 3D' },
  { id: 'surface', label: 'Price Surface'  },
  { id: 'astro',   label: 'Astro Cycles'   },
];

// ── Helpers ──────────────────────────────────────────────────────────────────
function useThreeCanvas(canvasRef, buildScene, deps) {
  useEffect(() => {
    if (!canvasRef.current) return;
    // Capture ref at effect start so cleanup always uses same element
    const canvasEl = canvasRef.current;
    const W = canvasEl.clientWidth  || 800;
    const H = canvasEl.clientHeight || 500;

    const renderer = new THREE.WebGLRenderer({ canvas: canvasEl, antialias: true, alpha: true });
    renderer.setSize(W, H);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));

    const camera = new THREE.PerspectiveCamera(60, W / H, 0.1, 1000);
    const scene  = new THREE.Scene();
    scene.background = new THREE.Color(0x080810);

    // Ambient + dir light
    scene.add(new THREE.AmbientLight(0xffffff, 0.4));
    const dir = new THREE.DirectionalLight(0xffffff, 0.8);
    dir.position.set(10, 20, 10);
    scene.add(dir);

    // Build scene content
    const { cameraPos, animate: userAnimate } = buildScene(scene, W, H);
    camera.position.set(...(cameraPos || [0, 5, 20]));
    camera.lookAt(0, 0, 0);

    // Mouse orbit
    let isDragging = false, prevX = 0, prevY = 0, rotY = 0, rotX = 0;
    const onDown = (e) => { isDragging = true; prevX = e.clientX; prevY = e.clientY; };
    const onMove = (e) => {
      if (!isDragging) return;
      rotY += (e.clientX - prevX) * 0.005;
      rotX  = Math.max(-1.2, Math.min(1.2, rotX + (e.clientY - prevY) * 0.005));
      prevX = e.clientX; prevY = e.clientY;
    };
    const onUp = () => { isDragging = false; };
    canvasEl.addEventListener('mousedown', onDown);
    canvasEl.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    // (canvasEl already captured above)

    let raf;
    const loop = () => {
      raf = requestAnimationFrame(loop);
      // Orbit camera
      const r = 20;
      camera.position.x = r * Math.sin(rotY) * Math.cos(rotX);
      camera.position.y = r * Math.sin(rotX);
      camera.position.z = r * Math.cos(rotY) * Math.cos(rotX);
      camera.lookAt(0, 0, 0);
      if (userAnimate) userAnimate();
      renderer.render(scene, camera);
    };
    loop();

    const handleResize = () => {
      if (!canvasEl) return;
      const w = canvasEl.clientWidth;
      const h = canvasEl.clientHeight;
      renderer.setSize(w, h);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    };
    window.addEventListener('resize', handleResize);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener('mouseup', onUp);
      window.removeEventListener('resize', handleResize);
      if (canvasEl) {
        canvasEl.removeEventListener('mousedown', onDown);
        canvasEl.removeEventListener('mousemove', onMove);
      }
      renderer.dispose();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

// ── Gann Spiral Scene ─────────────────────────────────────────────────────────
function GannSpiralScene({ canvasRef, centerPrice }) {
  useThreeCanvas(canvasRef, (scene) => {
    const points = [];
    const colors = [];
    const color = new THREE.Color();

    // Gann Square of 9: numbers spiral as r = sqrt(n) at angle theta
    // We add z = r * 0.2 to create a helix
    for (let n = 1; n <= 720; n++) {
      const r = Math.sqrt(n) * 1.2;
      const theta = (Math.sqrt(n) - 1) * Math.PI * 2;
      const x = r * Math.cos(theta);
      const y = r * Math.sin(theta);
      const z = (n / 720) * 8 - 4;

      points.push(new THREE.Vector3(x, z, y));

      // Color by ring (hue cycles)
      const hue = (n / 720) * 0.8 + 0.4;
      color.setHSL(hue, 1.0, 0.6);
      colors.push(color.r, color.g, color.b);
    }

    const geo = new THREE.BufferGeometry().setFromPoints(points);
    geo.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
    const mat = new THREE.LineBasicMaterial({ vertexColors: true, linewidth: 2 });
    scene.add(new THREE.Line(geo, mat));

    // Add key Gann levels as glowing spheres
    const keyAngles = [0, 90, 180, 270, 360].map(d => d * Math.PI / 180);
    keyAngles.forEach((a, idx) => {
      [1, 2, 3, 4, 5].forEach(ring => {
        const r = ring * Math.sqrt(ring) * 1.5;
        const x = r * Math.cos(a);
        const y = r * Math.sin(a);
        const z = (ring * 90 / 720) * 8 - 4;
        const sphere = new THREE.Mesh(
          new THREE.SphereGeometry(0.18, 8, 8),
          new THREE.MeshBasicMaterial({ color: idx % 2 === 0 ? 0x00e676 : 0xf59e0b })
        );
        sphere.position.set(x, z, y);
        scene.add(sphere);
      });
    });

    // Grid plane
    const grid = new THREE.GridHelper(30, 30, 0x1a1a3a, 0x1a1a3a);
    grid.position.y = -5;
    scene.add(grid);

    // Axis lines
    const axMat = new THREE.LineBasicMaterial({ color: 0x334155 });
    [[12,0,0],[-12,0,0],[0,12,0],[0,-12,0],[0,0,12],[0,0,-12]].forEach((p,i) => {
      if (i % 2 === 0) return;
      const axGeo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(0,0,0), new THREE.Vector3(...p)]);
      scene.add(new THREE.Line(axGeo, axMat));
    });

    return { cameraPos: [14, 8, 14] };
  }, [centerPrice]);

  return null;
}

// ── Price Surface Scene ───────────────────────────────────────────────────────
function PriceSurfaceScene({ canvasRef, bars }) {
  useThreeCanvas(canvasRef, (scene) => {
    if (!bars?.length) return { cameraPos: [0, 10, 20] };

    const N = Math.min(bars.length, 80);
    const slice = bars.slice(-N);
    const prices = slice.map(b => (b.high + b.low) / 2);
    const vols   = slice.map(b => b.volume || 1);
    const minP = Math.min(...prices), maxP = Math.max(...prices);
    const maxV = Math.max(...vols);

    const geometry = new THREE.BufferGeometry();
    const vertices = [], colors = [], indices = [];
    const color = new THREE.Color();
    const W = 0.5;

    for (let i = 0; i < N; i++) {
      const pn = (prices[i] - minP) / (maxP - minP + 1e-8);
      const vn = vols[i] / maxV;
      const x = (i / N) * 20 - 10;
      const y = pn * 8;
      const z = 0;

      // 4 corners of a bar
      [[x - W, 0, -vn * 2], [x + W, 0, -vn * 2],
       [x + W, y,  vn * 2], [x - W, y,  vn * 2]].forEach(([vx, vy, vz]) => {
        vertices.push(vx, vy, vz);
        const chg = i > 0 ? prices[i] - prices[i - 1] : 0;
        if (chg > 0) color.setRGB(0.06, 0.9, 0.46);
        else if (chg < 0) color.setRGB(0.94, 0.27, 0.27);
        else color.setRGB(0.5, 0.5, 0.6);
        colors.push(color.r, color.g, color.b);
      });

      const b = i * 4;
      indices.push(b, b+1, b+2,  b, b+2, b+3);
    }

    geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3));
    geometry.setAttribute('color',    new THREE.Float32BufferAttribute(colors,   3));
    geometry.setIndex(indices);
    geometry.computeVertexNormals();

    const mesh = new THREE.Mesh(geometry, new THREE.MeshPhongMaterial({
      vertexColors: true, side: THREE.DoubleSide, shininess: 40
    }));
    scene.add(mesh);

    // Candle wicks
    for (let i = 0; i < N; i++) {
      const x = (i / N) * 20 - 10;
      const hn = (slice[i].high  - minP) / (maxP - minP + 1e-8) * 8;
      const ln = (slice[i].low   - minP) / (maxP - minP + 1e-8) * 8;
      const wickGeo = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(x, ln, 0), new THREE.Vector3(x, hn, 0)
      ]);
      scene.add(new THREE.Line(wickGeo, new THREE.LineBasicMaterial({ color: 0x64748b })));
    }

    const grid = new THREE.GridHelper(24, 20, 0x1e293b, 0x1e293b);
    grid.position.y = -0.5;
    scene.add(grid);

    return { cameraPos: [0, 12, 22] };
  }, [bars]);

  return null;
}

// ── Astro Cycles Scene ────────────────────────────────────────────────────────
function AstroCyclesScene({ canvasRef }) {
  const rotRef = useRef(0);

  useThreeCanvas(canvasRef, (scene) => {
    const PLANETS = [
      { name: 'Mercury', days: 88,   radius: 3,    color: 0x94a3b8, size: 0.3 },
      { name: 'Venus',   days: 225,  radius: 5,    color: 0xfbbf24, size: 0.45 },
      { name: 'Mars',    days: 687,  radius: 7.5,  color: 0xef4444, size: 0.4 },
      { name: 'Jupiter', days: 4333, radius: 10.5, color: 0xf97316, size: 0.6 },
      { name: 'Saturn',  days: 10759,radius: 13.5, color: 0xe2e8f0, size: 0.55 },
    ];

    // Gann degree rings
    [90, 180, 270, 360].forEach((deg, i) => {
      const mat = new THREE.LineDashedMaterial({ color: 0x1e3a5f, dashSize: 0.3, gapSize: 0.2 });
      const pts = [];
      for (let a = 0; a <= 64; a++) {
        const theta = (a / 64) * Math.PI * 2;
        pts.push(new THREE.Vector3(Math.cos(theta) * (i + 1.5), 0, Math.sin(theta) * (i + 1.5)));
      }
      const line = new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), mat);
      line.computeLineDistances();
      scene.add(line);
    });

    const planetMeshes = [];
    PLANETS.forEach(p => {
      // Orbit ring
      const ringPts = [];
      for (let a = 0; a <= 128; a++) {
        const theta = (a / 128) * Math.PI * 2;
        ringPts.push(new THREE.Vector3(Math.cos(theta) * p.radius, 0, Math.sin(theta) * p.radius));
      }
      scene.add(new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(ringPts),
        new THREE.LineBasicMaterial({ color: p.color, opacity: 0.3, transparent: true })
      ));

      // Planet sphere
      const mesh = new THREE.Mesh(
        new THREE.SphereGeometry(p.size, 16, 16),
        new THREE.MeshPhongMaterial({ color: p.color, emissive: p.color, emissiveIntensity: 0.3 })
      );
      scene.add(mesh);
      planetMeshes.push({ mesh, radius: p.radius, speed: (2 * Math.PI) / p.days });
    });

    // Sun at center
    const sun = new THREE.Mesh(
      new THREE.SphereGeometry(0.8, 16, 16),
      new THREE.MeshBasicMaterial({ color: 0xfef08a })
    );
    const sunGlow = new THREE.PointLight(0xfef08a, 2, 30);
    scene.add(sun);
    scene.add(sunGlow);

    // Gann angle lines (45°, 135°, 225°, 315°)
    [45, 135, 225, 315].forEach(deg => {
      const theta = (deg * Math.PI) / 180;
      const pts = [new THREE.Vector3(0, 0, 0), new THREE.Vector3(Math.cos(theta) * 14, 0, Math.sin(theta) * 14)];
      scene.add(new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(pts),
        new THREE.LineBasicMaterial({ color: 0x00e676, opacity: 0.2, transparent: true })
      ));
    });

    let t = 0;
    const animate = () => {
      t += 0.005;
      planetMeshes.forEach(({ mesh, radius, speed }) => {
        const angle = t * speed * 200;
        mesh.position.set(Math.cos(angle) * radius, 0, Math.sin(angle) * radius);
      });
    };

    return { cameraPos: [0, 18, 0], animate };
  }, []);

  return null;
}

// ── Main Modal ───────────────────────────────────────────────────────────────
export default function Gann3DPanel({ onClose, stockData, selectedStock }) {
  const [tab, setTab] = useState('spiral');
  const canvasRef = useRef(null);
  const bars = stockData?.bars || [];
  const lastPrice = bars.length ? bars[bars.length - 1]?.close : 100;

  // AI optimisation state
  const [aiBusy, setAiBusy] = useState(false);
  const [aiRes, setAiRes] = useState(null);
  const [aiErr, setAiErr] = useState(null);

  const ticker = selectedStock?.ticker || selectedStock?.id;

  const runAiOptimize = async () => {
    if (!ticker) { setAiErr('Select a stock first'); return; }
    setAiBusy(true); setAiErr(null);
    try {
      const r = await fetch(`${ENS_API}/gann-optimize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ticker }),
      });
      const data = await r.json();
      if (!data.success) throw new Error(data.error || 'failed');
      setAiRes(data);
    } catch (e) {
      setAiErr(e.message);
    } finally {
      setAiBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[100] bg-black/95 flex flex-col" data-testid="gann-3d-panel">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-3 border-b border-white/10 bg-[#080810] shrink-0">
        <div className="flex items-center gap-3">
          <span className="text-sm font-black tracking-widest uppercase" style={{ color: '#00e676' }}>
            3D Immersive Charts
          </span>
          <span className="text-[10px] text-zinc-500 font-mono">Drag to orbit · Gann · Price · Astro</span>
        </div>
        <div className="flex items-center gap-2">
          {/* AI Optimise Badge Button */}
          <button
            onClick={runAiOptimize}
            disabled={aiBusy || !ticker}
            data-testid="gann-ai-optimize-btn"
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md text-[10px] font-bold uppercase tracking-widest border border-fuchsia-500/40 bg-fuchsia-500/10 hover:bg-fuchsia-500/20 text-fuchsia-300 disabled:opacity-40 disabled:cursor-not-allowed transition-all"
            title={ticker ? `Run 3-AI ensemble Gann optimisation for ${ticker}` : 'Select a stock first'}
          >
            {aiBusy ? <ArrowsClockwise size={12} className="animate-spin" /> : <Sparkle size={12} weight="fill" />}
            {aiBusy ? 'Optimising…' : 'AI Optimise'}
          </button>
          <button onClick={onClose} className="p-1.5 rounded hover:bg-white/10 transition-colors" data-testid="close-3d">
            <X size={18} className="text-zinc-400" />
          </button>
        </div>
      </div>

      {/* AI result overlay (slim banner above tabs) */}
      {(aiRes || aiErr) && (
        <div className="px-6 py-2 border-b border-white/10 bg-black/60 shrink-0" data-testid="gann-ai-result">
          {aiErr ? (
            <span className="text-xs text-rose-400">⚠ {aiErr}</span>
          ) : (
            <div className="flex flex-wrap items-center gap-3 text-[11px]">
              <span className="text-fuchsia-400 font-bold uppercase tracking-widest">AI-Tuned</span>
              <span className="text-zinc-400">Consensus: <span className="text-emerald-300 font-bold">{aiRes.ensemble?.consensus}</span> ({aiRes.ensemble?.confidence}%)</span>
              <span className="text-zinc-400">Pivot: <span className="font-mono text-zinc-200">{aiRes.chosen_pivot?.type} ₹{aiRes.chosen_pivot?.price?.toFixed?.(2)}</span></span>
              <span className="text-zinc-400">Angles: {aiRes.active_angles?.map(a => <span key={a} className="ml-1 px-1.5 py-0.5 rounded bg-cyan-500/15 border border-cyan-500/30 text-cyan-300 text-[10px] font-mono">{a}</span>)}</span>
              <span className="text-zinc-400">SoQ Ring: <span className="text-cyan-300 font-mono">{aiRes.soq_ring}</span> ({aiRes.soq_levels?.length} levels)</span>
            </div>
          )}
        </div>
      )}

      {/* Tabs */}
      <div className="flex border-b border-white/10 bg-[#080810] shrink-0">
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            data-testid={`3d-tab-${t.id}`}
            className={`px-6 py-2.5 text-[11px] font-bold tracking-widest uppercase transition-colors ${
              tab === t.id ? 'text-[#00e676] border-b-2 border-[#00e676]' : 'text-zinc-500 hover:text-zinc-300'
            }`}>
            {t.label}
          </button>
        ))}
      </div>

      {/* Canvas */}
      <div className="flex-1 relative overflow-hidden">
        <canvas ref={canvasRef} className="w-full h-full" style={{ display: 'block' }} />
        {tab === 'spiral'  && <GannSpiralScene canvasRef={canvasRef} centerPrice={lastPrice} />}
        {tab === 'surface' && <PriceSurfaceScene canvasRef={canvasRef} bars={bars} />}
        {tab === 'astro'   && <AstroCyclesScene canvasRef={canvasRef} />}

        {/* Info overlay */}
        <div className="absolute bottom-4 left-4 bg-black/60 backdrop-blur-sm rounded-lg px-3 py-2 text-[10px] text-zinc-400 border border-white/10">
          {tab === 'spiral'  && <><span className="text-[#00e676]">Gann Square of 9</span> — 3D Spiral Helix · Green/Gold = Key Gann Levels</>}
          {tab === 'surface' && <><span className="text-emerald-400">Price Surface</span> — X: Time · Y: Price · Z: Volume · Color: Direction</>}
          {tab === 'astro'   && <><span className="text-yellow-400">Planetary Cycles</span> — Mercury(88d) Venus(225d) Mars(687d) Jupiter(4333d) Saturn(10759d)</>}
        </div>
      </div>
    </div>
  );
}
