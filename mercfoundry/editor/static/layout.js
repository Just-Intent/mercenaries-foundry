// Paths and zones in the Foundry editor.
//
// Game items (from the game's own layers) can have their points moved; the
// point count stays, because a path's orientations, pauses and links are
// indexed by point. The mod's own items (its encounters/*.json layers) can
// also gain and lose points, be created and be deleted.
import * as THREE from 'three';

const COLOURS = {
  'game-path': 0x46a6ff, 'mod-path': 0x8cff5a,
  'game-boundary': 0xff9a3c, 'mod-boundary': 0xffe14d, killbox: 0xff4a4a, selected: 0xffffff,
};
const WALL = 6;            // metres: how tall zone walls are drawn
const LIFT = 0.4;          // draw lines a little above their points

export function createLayout(ctx) {
  const {scene, gizmo, heightAt, threePos, gamePos, status, $} = ctx;
  const group = new THREE.Group();
  scene.add(group);
  const handleGeo = new THREE.SphereGeometry(0.7, 12, 8);
  const handleMat = new THREE.MeshBasicMaterial({color: 0xffcc33, depthTest: false});
  const handlePick = new THREE.MeshBasicMaterial({color: 0xff5050, depthTest: false});
  const st = {
    map: null, items: [], files: [], selected: null, handles: [], point: null,
    gameEdits: new Map(), dirtyFiles: new Set(), deleted: [], draw: null, show: {path: true, boundary: true},
  };

  const colourOf = (it) => (it === st.selected ? COLOURS.selected
    : it.kind === 'boundary' && /killbox/i.test(it.properties.type || '') ? COLOURS.killbox
    : COLOURS[`${it.source}-${it.kind}`]);

  // ---------------------------------------------------------------- drawing
  function buildVisual(it) {
    if (it.visual) group.remove(it.visual);
    const g = new THREE.Group();
    g.userData.item = it;
    const pts = it.points.map((p) => threePos(p).add(new THREE.Vector3(0, LIFT, 0)));
    const colour = colourOf(it);
    if (pts.length >= 2) {
      const geo = new THREE.BufferGeometry().setFromPoints(pts);
      const line = it.kind === 'boundary' ? new THREE.LineLoop(geo) : new THREE.Line(geo);
      line.material = new THREE.LineBasicMaterial({color: colour});
      g.add(line);
    }
    if (it.kind === 'boundary' && pts.length >= 3 && it === st.selected) {
      // Low see-through walls along the selected zone's outline, so it reads
      // as an area (other zones are outlines only: some cover the whole map).
      const pos = [];
      for (let i = 0; i < pts.length; i++) {
        const a = pts[i], b = pts[(i + 1) % pts.length];
        pos.push(a.x, a.y, a.z, b.x, b.y, b.z, b.x, b.y + WALL, b.z,
                 a.x, a.y, a.z, b.x, b.y + WALL, b.z, a.x, a.y + WALL, a.z);
      }
      const geo = new THREE.BufferGeometry();
      geo.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
      g.add(new THREE.Mesh(geo, new THREE.MeshBasicMaterial({
        color: colour, transparent: true, opacity: 0.18, side: THREE.DoubleSide, depthWrite: false})));
    }
    g.visible = st.show[it.kind];
    it.visual = g;
    group.add(g);
  }

  function rebuildHandles() {
    for (const h of st.handles) group.remove(h);
    st.handles = [];
    const it = st.selected;
    if (!it) return;
    it.points.forEach((p, i) => {
      const h = new THREE.Mesh(handleGeo, i === st.point ? handlePick : handleMat);
      h.position.copy(threePos(p)).add(new THREE.Vector3(0, LIFT, 0));
      h.renderOrder = 10;
      h.userData.handle = {item: it, index: i};
      group.add(h);
      st.handles.push(h);
    });
  }

  // ---------------------------------------------------------------- loading
  async function load(map) {
    clear();
    const r = await fetch(`/api/layout/${map}`);
    const doc = await r.json();
    if (!r.ok) throw new Error(doc.error || r.statusText);
    st.map = map;
    st.items = doc.items;
    st.files = doc.files;
    st.gameEdits.clear(); st.dirtyFiles.clear(); st.deleted = [];
    for (const it of st.items) {
      it.original = it.points.map((p) => [...p]);
      buildVisual(it);
    }
    refreshList();
    return st.items.length;
  }

  function clear() {
    select(null);
    for (const it of st.items) if (it.visual) group.remove(it.visual);
    st.items = [];
    cancelDraw();
  }

  // ---------------------------------------------------------------- selection
  function select(it, point = null) {
    const before = st.selected;
    st.selected = it;
    st.point = point;
    if (before && before !== it) buildVisual(before);
    if (it) buildVisual(it);
    rebuildHandles();
    if (gizmo.object && gizmo.object.userData.handle) gizmo.detach();
    if (it && point !== null) {
      gizmo.setMode('translate');
      gizmo.showX = gizmo.showY = gizmo.showZ = true;
      gizmo.attach(st.handles[point]);
    }
    showPanel();
  }

  // The nearest path, zone or point under the ray, as {distance, apply}, or
  // null. Point handles always win; a zone's wall only counts at its edge
  // line (so objects inside a zone can still be clicked).
  function pick(ray) {
    if (st.draw) return null;
    const handle = ray.intersectObjects(st.handles, false)[0];
    if (handle) {
      return {distance: -1, apply: () => select(st.selected, handle.object.userData.handle.index)};
    }
    ray.params.Line.threshold = 1.5;
    const lines = [];
    for (const g of group.children) {
      if (!g.visible || !g.userData.item) continue;
      for (const c of g.children) if (c.isLine) lines.push(c);
    }
    const hit = ray.intersectObjects(lines, false)[0];
    if (!hit) return null;
    const item = hit.object.parent.userData.item;
    return {distance: hit.distance, apply: () => { ctx.deselectObject(); select(item); }};
  }

  function handleMoved(handle) {
    const {item, index} = handle.userData.handle;
    const p = handle.position.clone().sub(new THREE.Vector3(0, LIFT, 0));
    item.points[index] = gamePos(p);
    edited(item);
    buildVisual(item);
    showPanel();
  }

  function edited(it) {
    if (it.source === 'game') st.gameEdits.set(it.id, it);
    else st.dirtyFiles.add(it.file);
    ctx.setDirty(true);
    refreshList();
  }

  // ---------------------------------------------------------------- editing mod items
  function shiftIndices(it, from, by) {
    // Pauses and links name points by number: keep them on the same points.
    const spec = it.spec || {};
    if (spec.pauses) {
      const out = {};
      for (const [k, v] of Object.entries(spec.pauses)) {
        const i = +k;
        if (by < 0 && i === from) continue;
        out[i >= from + (by > 0 ? 0 : 1) ? i + by : i] = v;
      }
      spec.pauses = out;
    }
    for (const link of spec.links || []) {
      if (link.our_point !== undefined && link.our_point >= from + (by > 0 ? 0 : 1)) link.our_point += by;
    }
  }

  function addPoint() {
    const it = st.selected;
    if (!it || it.source !== 'mod') return;
    const i = st.point ?? it.points.length - 1;
    const a = it.points[i], b = it.points[(i + 1) % it.points.length];
    const last = it.kind === 'path' && i === it.points.length - 1;
    const p = last ? [a[0] + 5, 0, a[2]] : [(a[0] + b[0]) / 2, 0, (a[2] + b[2]) / 2];
    p[1] = Math.round(heightAt(p[0], p[2]) * 1000) / 1000;
    it.points.splice(i + 1, 0, p);
    shiftIndices(it, i + 1, 1);
    edited(it);
    select(it, i + 1);
  }

  function deletePoint() {
    const it = st.selected;
    if (!it || it.source !== 'mod' || st.point === null) return;
    const need = it.kind === 'path' ? 2 : 3;
    if (it.points.length <= need) return status(`A ${label(it)} needs at least ${need} points`, true);
    it.points.splice(st.point, 1);
    shiftIndices(it, st.point, -1);
    edited(it);
    select(it, null);
  }

  function deleteItem() {
    const it = st.selected;
    if (!it || it.source !== 'mod') return;
    if (!confirm(`Delete ${label(it)} ${it.name}? Scripts that use it by name will fail.`)) return;
    select(null);
    group.remove(it.visual);
    st.items = st.items.filter((x) => x !== it);
    st.dirtyFiles.add(it.file);
    ctx.setDirty(true);
    refreshList();
  }

  function revertItem() {
    const it = st.selected;
    if (!it || it.source !== 'game') return;
    it.points = it.original.map((p) => [...p]);
    st.gameEdits.set(it.id, it);     // saving puts the game's points back
    ctx.setDirty(true);
    buildVisual(it);
    select(it, null);
  }

  const label = (it) => (it.kind === 'path' ? 'path' : 'zone');

  // ---------------------------------------------------------------- new paths and zones
  function slug(s) { return s.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '') || 'mod'; }

  function startDraw(kind) {
    ctx.deselectObject();
    select(null);
    cancelDraw();
    const taken = new Set(st.items.filter((x) => x.kind === kind).map((x) => x.name.toLowerCase()));
    let k = 1;
    const base = kind === 'path' ? 'foundry_path' : 'foundry_zone';
    while (taken.has(`${base}${k}`)) k++;
    st.draw = {kind, points: [], line: null};
    $('draw-name').value = `${base}${k}`;
    const fresh = `${slug(ctx.modName())}_${st.map}.json`;
    for (const f of ctx.encounterFiles()) if (!st.files.includes(f)) st.files.push(f);
    const files = [...new Set([...st.files, fresh])];
    $('draw-file').innerHTML = files.map((f) => `<option>${f}</option>`).join('');
    $('draw-file').value = st.files[0] || fresh;
    $('draw-type-row').hidden = kind !== 'boundary';
    $('draw').hidden = false;
    $('draw-title').textContent = kind === 'path' ? 'New path' : 'New zone';
    showPanel();
    status(`Click the ground to add ${label({kind})} points; Enter finishes, Esc cancels.`);
  }

  function drawClick(ray) {
    if (!st.draw) return false;
    const hit = ray.intersectObject(ctx.terrain())[0];
    if (!hit) return true;
    st.draw.points.push(gamePos(hit.point));
    if (st.draw.line) group.remove(st.draw.line);
    const pts = st.draw.points.map((p) => threePos(p).add(new THREE.Vector3(0, LIFT, 0)));
    const geo = new THREE.BufferGeometry().setFromPoints(pts.length > 1 ? pts : [pts[0], pts[0]]);
    st.draw.line = st.draw.kind === 'boundary' && pts.length > 2
      ? new THREE.LineLoop(geo, new THREE.LineBasicMaterial({color: 0xffffff}))
      : new THREE.Line(geo, new THREE.LineBasicMaterial({color: 0xffffff}));
    group.add(st.draw.line);
    $('draw-count').textContent = `${st.draw.points.length} points`;
    return true;
  }

  function finishDraw() {
    const d = st.draw;
    if (!d) return;
    const need = d.kind === 'path' ? 2 : 3;
    if (d.points.length < need) return status(`A ${label(d)} needs at least ${need} points`, true);
    const name = $('draw-name').value.trim();
    if (!/^[A-Za-z0-9_\-]+$/.test(name)) return status('Names use letters, digits, _ and - only', true);
    if (st.items.some((x) => x.kind === d.kind && x.name.toLowerCase() === name.toLowerCase())) {
      return status(`There is already a ${label(d)} named ${name}`, true);
    }
    const file = $('draw-file').value;
    const props = d.kind === 'boundary' && $('draw-type').value ? {type: $('draw-type').value} : {};
    const spec = {points: d.points};
    if (Object.keys(props).length) spec.properties = props;
    const it = {id: `mod:${file}:${d.kind}:${name}`, source: 'mod', file, layer: file.replace(/\.json$/, ''),
                kind: d.kind, name, points: d.points, properties: props, spec};
    if (!st.files.includes(file)) st.files.push(file);
    cancelDraw();
    st.items.push(it);
    it.original = it.points.map((p) => [...p]);
    buildVisual(it);
    edited(it);
    select(it);
    status(`${name} added`);
  }

  function cancelDraw() {
    if (st.draw && st.draw.line) group.remove(st.draw.line);
    st.draw = null;
    if ($('draw')) $('draw').hidden = true;
  }

  // ---------------------------------------------------------------- panel
  function showPanel() {
    const it = st.selected;
    $('item').hidden = !it;
    if (!it) return;
    $('item-title').textContent = it.name;
    $('item-kind').textContent = `${it.source === 'game' ? 'Game' : 'Mod'} ${label(it)}, ${it.points.length} points`;
    $('item-layer').textContent = it.layer;
    const props = Object.entries(it.properties || {}).map(([k, v]) => `${k} = ${v}`).join(', ');
    $('item-props').textContent = props || '(none)';
    const mod = it.source === 'mod';
    $('item-add').hidden = $('item-del-point').hidden = $('item-delete').hidden = !mod;
    $('item-revert').hidden = mod;
    $('item-del-point').disabled = st.point === null;
    $('item-note').textContent = mod
      ? `Saved in encounters/${it.file}. A script loads the layer with Utility_ReadLevelFile('${it.layer}').`
      : 'A game item: drag its points (click a point first). The number of points stays the same.';
    $('point-xyz').hidden = st.point === null;
    if (st.point !== null) {
      const p = it.points[st.point];
      $('point-label').textContent = `Point ${st.point}`;
      [$('qx').value, $('qy').value, $('qz').value] = p.map((v) => v.toFixed(2));
    }
  }

  function setPointFromPanel() {
    const it = st.selected;
    if (!it || st.point === null) return;
    const p = [$('qx').value, $('qy').value, $('qz').value].map((v) => (v.trim() === '' ? NaN : +v));
    if (p.some((v) => !Number.isFinite(v))) return showPanel();
    it.points[st.point] = p;
    edited(it);
    buildVisual(it);
    select(it, st.point);
  }

  function refreshList() {
    const ul = $('layout-changes');
    if (!ul) return;
    ul.innerHTML = '';
    const items = [...st.gameEdits.values()].map((it) => [it, `Moved points of ${it.name}`]);
    for (const f of st.dirtyFiles) items.push([null, `Changed encounters/${f}`]);
    for (const [it, text] of items) {
      const li = document.createElement('li');
      li.textContent = text;
      if (it) li.onclick = () => { ctx.lookAt(it.points[0], 60); select(it); };
      ul.appendChild(li);
    }
  }

  function setVisible(kind, on) {
    st.show[kind] = on;
    for (const it of st.items) if (it.kind === kind && it.visual) it.visual.visible = on;
  }

  function find(q) {
    q = q.toLowerCase();
    return st.items.find((x) => x.name.toLowerCase() === q) || st.items.find((x) => x.name.toLowerCase().includes(q));
  }

  // ---------------------------------------------------------------- saving
  function saveBody() {
    if (!st.gameEdits.size && !st.dirtyFiles.size) return null;
    const game = [...st.gameEdits.values()].map((it) => ({layer: it.layer, kind: it.kind, name: it.name, points: it.points}));
    const mod = {};
    for (const f of st.dirtyFiles) {
      const part = {paths: {}, boundaries: {}};
      for (const it of st.items.filter((x) => x.source === 'mod' && x.file === f)) {
        part[it.kind === 'path' ? 'paths' : 'boundaries'][it.name] = {...it.spec, points: it.points};
      }
      mod[f] = part;
    }
    return {map: st.map, game, mod};
  }

  function saved() {
    st.gameEdits.clear();
    st.dirtyFiles.clear();
    for (const it of st.items) it.original = it.points.map((p) => [...p]);
    refreshList();
  }

  // ---------------------------------------------------------------- wiring
  $('new-path').onclick = () => startDraw('path');
  $('new-zone').onclick = () => startDraw('boundary');
  $('draw-finish').onclick = finishDraw;
  $('draw-cancel').onclick = () => { cancelDraw(); status(''); };
  $('item-add').onclick = addPoint;
  $('item-del-point').onclick = deletePoint;
  $('item-delete').onclick = deleteItem;
  $('item-revert').onclick = revertItem;
  for (const id of ['qx', 'qy', 'qz']) $(id).addEventListener('change', setPointFromPanel);
  $('show-paths').addEventListener('change', (e) => setVisible('path', e.target.checked));
  $('show-zones').addEventListener('change', (e) => setVisible('boundary', e.target.checked));

  return {
    load, pick, select, handleMoved, drawClick, finishDraw, cancelDraw, saveBody, saved, find,
    isDrawing: () => !!st.draw,
    files: () => [...st.files],
    hasSelection: () => !!st.selected,
    deletePoint, addPoint,
  };
}
