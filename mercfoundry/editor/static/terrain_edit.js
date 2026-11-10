// Terrain editing: brushes that raise, lower, flatten or smooth the map's
// height samples, paint or erase its ground texture layers, and roads laid
// from clicked points. The view follows as you work; Save writes
//   heights -> terrain/<map>.heights.changes.json (the build relights slopes),
//   paint   -> terrain/<map>.layerNN.<texture>.changes.png (changed pixels),
//   roads   -> terrain/<map>.decals.json "roads".
// In terrain mode: left-drag paints (left-click places road points),
// middle-drag orbits, right-drag pans.
import * as THREE from 'three';

const HEIGHT_TOOLS = ['raise', 'lower', 'flatten', 'smooth'];
const TOOLS = [...HEIGHT_TOOLS, 'paint', 'erase', 'road'];

export function createTerrainEditor(ctx) {
  const {$, controls, status, setDirty, terrain, pick, gamePos, scene, threePos} = ctx;
  const st = {
    on: false, tool: 'raise', radius: 12, strength: 0.5, layer: 0,
    stroke: null,          // {heights: Map(i -> before), paint: Map(k -> before), target}
    undo: [],              // strokes, newest last
    changed: new Set(),    // height sample indices changed since the last save
    painted: new Set(),    // paint byte offsets changed since the last save
    base: null,            // the heights as loaded (samples back at these aren't saved)
    paintBase: null,       // the paint as loaded
    pointer: null,         // last pointer event while painting
    meshDirty: false, paintDirty: false,
    road: null,            // {points: [[x, z]], line} while laying one
    roads: [],             // laid, not yet saved: {texture, span, points}
  };
  const ORBIT = controls.mouseButtons.LEFT;
  const roadGroup = new THREE.Group();
  scene.add(roadGroup);

  function setOn(on) {
    st.on = on;
    $('terrain-mode').classList.toggle('on', on);
    $('terrain').hidden = !on;
    // Left-drag paints; orbit moves to the middle button.
    controls.mouseButtons.LEFT = on ? null : ORBIT;
    controls.mouseButtons.MIDDLE = on ? THREE.MOUSE.ROTATE : THREE.MOUSE.DOLLY;
    if (!on) cancelRoad();
    if (on) fillLayers();
    status(on ? 'Terrain: left-drag paints, middle-drag orbits, right-drag pans; Esc leaves.' : 'Terrain editing off');
  }

  function setTool(tool) {
    st.tool = tool;
    for (const t of TOOLS) $(`terrain-${t}`).classList.toggle('on', t === tool);
    $('terrain-paint-row').hidden = tool !== 'paint' && tool !== 'erase';
    $('terrain-road-row').hidden = tool !== 'road';
    $('terrain-brush-rows').hidden = tool === 'road';
    if (tool !== 'road') cancelRoad();
    if (tool === 'paint' || tool === 'erase') fillLayers();
    if (tool === 'road') {
      fillRoadTextures();
      status('Road: click points along the ground, Enter finishes (Esc cancels the road)');
    }
  }

  // The ground's texture layers, for the paint picker.
  function fillLayers() {
    const g = terrain().mesh.userData.ground;
    const sel = $('terrain-layer');
    if (!g) {
      sel.innerHTML = '<option>(ground textures still loading)</option>';
      setTimeout(() => { if (st.on) fillLayers(); }, 1000);
      return;
    }
    if (sel.dataset.map === terrain().meta.name && sel.options.length === g.layers.length) return;
    sel.innerHTML = g.layers.map((l, i) => `<option value="${i}">${i}: ${l.texture}</option>`).join('');
    sel.dataset.map = terrain().meta.name;
    sel.value = String(Math.min(st.layer, g.layers.length - 1));
    st.layer = +sel.value;
  }
  let roadTextures = null;
  async function fillRoadTextures() {
    if (roadTextures && roadTextures.map === terrain().meta.name) return;
    const r = await fetch(`/api/look/${terrain().meta.name}.decals`);
    const doc = r.ok ? await r.json() : {textures: []};
    const names = [...new Set(doc.textures.filter(Boolean))].sort();
    roadTextures = {map: terrain().meta.name, names};
    $('terrain-road-textures').innerHTML = names.map((n) => `<option value="${n}">`).join('');
    if (!$('terrain-road-texture').value) {
      $('terrain-road-texture').value = names.find((n) => /road_straight/.test(n)) || names[0] || '';
    }
  }

  // Grid points under a brush centred at game (x, z): [row * cols + col, weight].
  function near(x, z, radius, cols, count) {
    const {spacing, x0, z0} = terrain().meta;
    const c0 = Math.max(0, Math.floor((x - radius - x0) / spacing)), c1 = Math.min(count - 1, Math.ceil((x + radius - x0) / spacing));
    const r0 = Math.max(0, Math.floor((z - radius - z0) / spacing)), r1 = Math.min(count - 1, Math.ceil((z + radius - z0) / spacing));
    const out = [];
    for (let r = r0; r <= r1; r++) {
      for (let c = c0; c <= c1; c++) {
        const d = Math.hypot(x0 + c * spacing - x, z0 + r * spacing - z);
        if (d >= radius) continue;
        const f = 1 - d / radius;
        out.push([r * cols + c, f * f * (3 - 2 * f)]);         // smooth falloff
      }
    }
    return out;
  }

  function where(e) {
    const hit = pick(e).intersectObject(terrain().mesh)[0];
    return hit ? gamePos(hit.point) : null;
  }

  function applyHeights(at, dt) {
    const t = terrain();
    const h = t.heights, size = t.meta.size;
    const rate = st.strength * 6 * dt;                         // metres a second at full strength
    for (const [i, w] of near(at[0], at[2], st.radius, size, size)) {
      if (!st.stroke.heights.has(i)) st.stroke.heights.set(i, h[i]);
      let v = h[i];
      if (st.tool === 'raise') v += rate * w;
      else if (st.tool === 'lower') v -= rate * w;
      else if (st.tool === 'flatten') v += (st.stroke.target - v) * Math.min(1, rate * w);
      else {
        const r = Math.floor(i / size), c = i % size;
        let sum = 0, n = 0;
        for (const [dr, dc] of [[-1, 0], [1, 0], [0, -1], [0, 1]]) {
          const rr = r + dr, cc = c + dc;
          if (rr >= 0 && rr < size && cc >= 0 && cc < size) { sum += h[rr * size + cc]; n++; }
        }
        v += (sum / n - v) * Math.min(1, rate * w);
      }
      h[i] = v;
      st.changed.add(i);
    }
    st.meshDirty = true;
  }

  function applyPaint(at, dt) {
    const g = terrain().mesh.userData.ground;
    if (!g) return status('Ground textures are still loading', true);
    const rate = st.strength * 400 * dt;                       // opacity steps a second at full strength
    const base = st.layer * g.w * g.w;
    for (const [i, w] of near(at[0], at[2], st.radius, g.w, g.w)) {
      const k = base + i;
      if (!st.stroke.paint.has(k)) st.stroke.paint.set(k, g.paint[k]);
      const d = rate * w;
      g.paint[k] = Math.max(0, Math.min(255, Math.round(g.paint[k] + (st.tool === 'paint' ? d : -d))));
      st.painted.add(k);
    }
    st.paintDirty = true;
  }

  // The mesh's vertex heights follow the samples (normals once a frame).
  function refreshMesh(indices) {
    const t = terrain();
    const pos = t.mesh.geometry.attributes.position;
    for (const i of indices) pos.setY(i, t.heights[i]);
    pos.needsUpdate = true;
    t.mesh.geometry.computeVertexNormals();
    t.mesh.geometry.computeBoundingSphere();
  }
  function refreshPaint() {
    const g = terrain().mesh.userData.ground;
    if (g) g.tex.needsUpdate = true;
  }

  let lastTick = 0;
  function tick(now) {
    if (!st.stroke || !st.pointer) return;
    const dt = lastTick ? Math.min(0.1, (now - lastTick) / 1000) : 1 / 60;
    lastTick = now;
    const at = where(st.pointer);
    if (at) {
      if (HEIGHT_TOOLS.includes(st.tool)) applyHeights(at, dt);
      else applyPaint(at, dt);
      setDirty(true);
    }
    if (st.meshDirty) { st.meshDirty = false; refreshMesh(st.stroke.heights.keys()); }
    if (st.paintDirty) { st.paintDirty = false; refreshPaint(); }
  }

  function down(e) {
    if (!st.on || e.button !== 0) return false;
    const at = where(e);
    if (!at) return true;
    if (st.tool === 'road') { addRoadPoint(at); return true; }
    if (!st.base) st.base = Float32Array.from(terrain().heights);
    const g = terrain().mesh.userData.ground;
    if (g && !st.paintBase) st.paintBase = Uint8Array.from(g.paint);
    st.stroke = {heights: new Map(), paint: new Map(), target: at[1]};
    st.pointer = e;
    lastTick = 0;
    return true;
  }
  function move(e) {
    if (!st.on) return false;
    if (st.stroke) st.pointer = e;
    return true;
  }
  function up() {
    if (!st.stroke) return st.on && st.tool === 'road';
    if (st.stroke.heights.size || st.stroke.paint.size) st.undo.push(st.stroke);
    if (st.undo.length > 50) st.undo.shift();
    if (st.stroke.heights.size) ctx.onHeights && ctx.onHeights();
    st.stroke = null;
    $('terrain-undo').disabled = !st.undo.length;
    return true;
  }

  function undo() {
    const s = st.undo.pop();
    if (!s) return;
    const h = terrain().heights, g = terrain().mesh.userData.ground;
    for (const [i, v] of s.heights) { h[i] = v; st.changed.add(i); }
    if (g) for (const [k, v] of s.paint) { g.paint[k] = v; st.painted.add(k); }
    if (s.heights.size) { refreshMesh(s.heights.keys()); ctx.onHeights && ctx.onHeights(); }
    if (s.paint.size) refreshPaint();
    setDirty(true);
    $('terrain-undo').disabled = !st.undo.length;
    status('Undid a terrain stroke');
  }

  // ---------------------------------------------------------------- roads
  function drawRoad(points, colour) {
    const t = terrain();
    const pts = points.map(([x, z]) => {
      const p = threePos([x, ctx.heightAt(x, z) + 0.4, z]);
      return p;
    });
    const geo = new THREE.BufferGeometry().setFromPoints(pts);
    const line = new THREE.Line(geo, new THREE.LineBasicMaterial({color: colour, depthTest: false}));
    line.renderOrder = 3;
    roadGroup.add(line);
    return line;
  }
  function addRoadPoint(at) {
    if (!st.road) st.road = {points: [], line: null};
    st.road.points.push([Math.round(at[0] * 100) / 100, Math.round(at[2] * 100) / 100]);
    if (st.road.line) roadGroup.remove(st.road.line);
    st.road.line = drawRoad(st.road.points, 0xffcc33);
    status(`Road: ${st.road.points.length} point${st.road.points.length > 1 ? 's' : ''}; Enter finishes, Esc cancels`);
  }
  function cancelRoad() {
    if (st.road && st.road.line) roadGroup.remove(st.road.line);
    st.road = null;
  }
  function finishRoad() {
    if (!st.road || st.road.points.length < 2) return status('A road needs two points or more', true);
    const texture = $('terrain-road-texture').value.trim();
    if (!texture) return status('Pick the road texture first', true);
    const span = Math.max(4, Math.min(60, +$('terrain-road-span').value || 25));
    roadGroup.remove(st.road.line);
    st.roads.push({texture, span, points: st.road.points, line: drawRoad(st.road.points, 0xff8833)});
    st.road = null;
    setDirty(true);
    status(`Road laid (${st.roads.length} to save); Save puts it in the game's ground`);
  }
  const isLayingRoad = () => !!st.road;

  // ---------------------------------------------------------------- saving
  // Changed samples as [x (column), z (row), height units].
  function saveBody() {
    if (!st.changed.size) return null;
    const t = terrain();
    const {size, step} = t.meta;
    const units = (v) => Math.round(v / step);
    const samples = [...st.changed].filter((i) => !st.base || units(t.heights[i]) !== units(st.base[i]))
      .map((i) => [i % size, Math.floor(i / size), units(t.heights[i])]);
    return samples.length ? {map: t.meta.name, samples} : null;
  }
  // Changed paint by layer: {layer: {texture, pixels: [[col, row, opacity]]}}.
  function paintBody() {
    const g = terrain().mesh.userData.ground;
    if (!st.painted.size || !g) return null;
    const layers = {};
    const per = g.w * g.w;
    for (const k of st.painted) {
      if (st.paintBase && g.paint[k] === st.paintBase[k]) continue;
      const L = Math.floor(k / per), i = k % per;
      (layers[L] ||= {texture: g.layers[L].texture, pixels: []}).pixels.push([i % g.w, Math.floor(i / g.w), g.paint[k]]);
    }
    return Object.keys(layers).length ? {map: terrain().meta.name, layers} : null;
  }
  function roadsBody() {
    return st.roads.length ? {map: terrain().meta.name,
                              roads: st.roads.map(({texture, span, points}) => ({texture, span, points}))} : null;
  }
  function saved() {
    st.changed.clear(); st.painted.clear(); st.base = null; st.paintBase = null;
    const hadRoads = st.roads.length > 0;
    for (const r of st.roads) roadGroup.remove(r.line);
    st.roads = [];
    return hadRoads;
  }
  function reset() {
    st.changed.clear(); st.painted.clear(); st.undo = []; st.stroke = null; st.base = null; st.paintBase = null;
    cancelRoad();
    for (const r of st.roads) roadGroup.remove(r.line);
    st.roads = [];
    $('terrain-undo').disabled = true;
  }

  $('terrain-mode').onclick = () => setOn(!st.on);
  for (const tool of TOOLS) $(`terrain-${tool}`).onclick = () => setTool(tool);
  $('terrain-layer').onchange = (e) => { st.layer = +e.target.value; };
  $('terrain-radius').oninput = (e) => { st.radius = +e.target.value; $('terrain-radius-v').textContent = `${st.radius} m`; };
  $('terrain-strength').oninput = (e) => { st.strength = +e.target.value / 100; $('terrain-strength-v').textContent = `${e.target.value}%`; };
  $('terrain-undo').onclick = undo;
  $('terrain-road-finish').onclick = finishRoad;

  return {isOn: () => st.on, setOn, down, move, up, tick, undo, saveBody, paintBody, roadsBody, saved, reset,
          finishRoad, cancelRoad, isLayingRoad};
}
