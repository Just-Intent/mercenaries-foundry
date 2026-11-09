// Mercenaries Foundry editor: a map's terrain and objects, with object moves,
// turns and copies saved as mod rules (objects/editor.json).
//
// Coordinates: the panel and the server use the game's (x, y, z); the 3D view
// uses three.js axes, (x, y, -z), like the glTF export.
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {TransformControls} from 'three/addons/controls/TransformControls.js';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {createLayout} from './layout.js';
import {createEncounters} from './encounters.js';
import {createLive} from './live.js';

const $ = (id) => document.getElementById(id);
const NEAR_RADIUS = 220;      // metres around the view centre that show real models
const MAX_NODES = 450;

// ------------------------------------------------------------------ renderer
const view = $('view');
const renderer = new THREE.WebGLRenderer({antialias: true});
renderer.setPixelRatio(window.devicePixelRatio);
view.appendChild(renderer.domElement);
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x8fa7bd);
scene.fog = new THREE.Fog(0x8fa7bd, 1500, 4500);
const camera = new THREE.PerspectiveCamera(55, 1, 0.5, 9000);
scene.add(new THREE.HemisphereLight(0xdfe8f0, 0x4a4436, 1.6));
const sun = new THREE.DirectionalLight(0xffffff, 1.6);
sun.position.set(-0.5, 1, 0.35);
scene.add(sun);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.maxPolarAngle = Math.PI * 0.495;
const gizmo = new TransformControls(camera, renderer.domElement);
scene.add(gizmo.getHelper());
gizmo.addEventListener('dragging-changed', (e) => { controls.enabled = !e.value; });
gizmo.addEventListener('objectChange', () => {
  if (gizmo.object && gizmo.object.userData.handle) layout.handleMoved(gizmo.object);
  else if (gizmo.object && gizmo.object.userData.pick) encs.moved(gizmo.object);
  else if (state.selected) fromNode(state.selected);
});

function resize() {
  const w = view.clientWidth, h = view.clientHeight;
  renderer.setSize(w, h);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(view);     // the header can wrap, so watch the view itself

// ------------------------------------------------------------------ state
const state = {
  map: null, terrain: null, records: [], byId: new Map(),
  moved: {}, copies: [], removed: new Set(), selected: null, dirty: false, nodes: new Set(),
};
const ghostMat = new THREE.MeshBasicMaterial({color: 0xff4040, transparent: true, opacity: 0.35, depthWrite: false});
let terrainMesh = null, boxes = null;
const loader = new GLTFLoader();
const modelCache = new Map();
const boxGeo = new THREE.BoxGeometry(2, 2, 2).translate(0, 1, 0);
const selMat = new THREE.MeshLambertMaterial({color: 0xffcc33});

const threePos = (p) => new THREE.Vector3(p[0], p[1], -p[2]);
const gamePos = (v) => [round(v.x), round(v.y), round(-v.z)];
const round = (v) => Math.round(v * 1000) / 1000;

function rotMatrix(m3) {
  const m = new THREE.Matrix4();
  m.set(m3[0], m3[3], m3[6], 0,
        m3[1], m3[4], m3[7], 0,
        m3[2], m3[5], m3[8], 0,
        0, 0, 0, 1);
  return m;
}
function m3From(quat) {
  const e = new THREE.Matrix4().makeRotationFromQuaternion(quat).elements;
  return [e[0], e[1], e[2], e[4], e[5], e[6], e[8], e[9], e[10]].map((v) => Math.round(v * 1e6) / 1e6);
}
// Heading in the game's sense (the mod rule "turn"): from the object's x axis.
function heading(m3) {
  // three column 0 is the object's x axis in three space; game z = -three z.
  const gx = m3[0], gz = -m3[2];
  return Math.round(THREE.MathUtils.radToDeg(Math.atan2(-gz, gx)) * 100) / 100;
}
function colourOf(name) {
  let h = 0;
  for (const c of name) h = (h * 31 + c.charCodeAt(0)) >>> 0;
  return new THREE.Color().setHSL((h % 360) / 360, 0.45, 0.55);
}

// ------------------------------------------------------------------ status
function status(text, error = false) {
  $('status').textContent = text;
  $('status').classList.toggle('error', error);
}
async function getJSON(url) {
  const r = await fetch(url);
  const doc = await r.json();
  if (!r.ok) throw new Error(doc.error || r.statusText);
  return doc;
}

// ------------------------------------------------------------------ terrain
function heightAt(x, z) {
  const t = state.terrain;
  if (!t) return 0;
  const {size, spacing, x0, z0} = t.meta;
  const fc = Math.min(Math.max((x - x0) / spacing, 0), size - 1.001);
  const fr = Math.min(Math.max((z - z0) / spacing, 0), size - 1.001);
  const c = Math.floor(fc), r = Math.floor(fr), dc = fc - c, dr = fr - r;
  const h = (rr, cc) => t.heights[rr * size + cc];
  return (h(r, c) * (1 - dc) + h(r, c + 1) * dc) * (1 - dr) + (h(r + 1, c) * (1 - dc) + h(r + 1, c + 1) * dc) * dr;
}

function buildTerrain(meta, heights) {
  const {size, spacing, x0, z0} = meta;
  const pos = new Float32Array(size * size * 3), col = new Float32Array(size * size * 3);
  let lo = Infinity, hi = -Infinity;
  for (const v of heights) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  const low = new THREE.Color(0x5d6b3c), high = new THREE.Color(0x9a9078), c = new THREE.Color();
  for (let r = 0; r < size; r++) {
    for (let k = 0; k < size; k++) {
      const i = r * size + k, h = heights[i];
      pos.set([x0 + k * spacing, h, -(z0 + r * spacing)], i * 3);
      c.copy(low).lerp(high, (h - lo) / Math.max(hi - lo, 1));
      col.set([c.r, c.g, c.b], i * 3);
    }
  }
  const idx = new Uint32Array((size - 1) * (size - 1) * 6);
  let n = 0;
  for (let r = 0; r < size - 1; r++) {
    for (let k = 0; k < size - 1; k++) {
      const a = r * size + k, b = a + size, c1 = a + 1, d = b + 1;
      idx.set([a, c1, b, c1, d, b], n);   // wound so the ground faces up
      n += 6;
    }
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('color', new THREE.BufferAttribute(col, 3));
  g.setIndex(new THREE.BufferAttribute(idx, 1));
  g.computeVertexNormals();
  return new THREE.Mesh(g, new THREE.MeshLambertMaterial({vertexColors: true}));
}

// ------------------------------------------------------------------ objects
function transformOf(rec) {
  const m = rotMatrix(rec.rotation);
  m.setPosition(threePos(rec.position));
  return m;
}

function placeBox(rec) {
  if (rec.slot >= boxes.instanceMatrix.count) return;
  const hidden = rec.node || rec.deleted || rec.removed || rec.hiddenBy;
  const m = hidden ? new THREE.Matrix4().makeScale(0, 0, 0) : transformOf(rec);
  boxes.setMatrixAt(rec.slot, m);
  boxes.instanceMatrix.needsUpdate = true;
}

function loadModel(name) {
  if (!modelCache.has(name)) {
    modelCache.set(name, fetch(`/api/model/${encodeURIComponent(name)}.glb`)
      .then((r) => { if (!r.ok) throw new Error(r.statusText); return r.arrayBuffer(); })
      .then((buf) => new Promise((ok, bad) => loader.parse(buf, '', (g) => ok(g.scene), bad)))
      .catch(() => null));
  }
  return modelCache.get(name);
}

function ensureNode(rec) {
  if (rec.node) return rec.node;
  const node = new THREE.Group();
  node.userData.rec = rec;
  node.matrixAutoUpdate = true;
  transformOf(rec).decompose(node.position, node.quaternion, node.scale);
  const box = new THREE.Mesh(boxGeo, new THREE.MeshLambertMaterial({color: colourOf(rec.model)}));
  node.add(box);
  scene.add(node);
  rec.node = node;
  state.nodes.add(rec);
  placeBox(rec);
  if (rec.deleted) ghost(node);
  loadModel(rec.model).then((model) => {
    if (!model || rec.node !== node) return;
    node.remove(box);
    node.add(model.clone(true));
    if (rec.deleted) ghost(node);
  });
  return node;
}

// A removed object, shown (when selected from the change list) as a red ghost.
function ghost(node) {
  node.traverse((o) => { if (o.isMesh) o.material = ghostMat; });
}

function dropNode(rec, force = false) {
  if (!rec.node || (rec === state.selected && !force)) return;
  scene.remove(rec.node);
  rec.node = null;
  state.nodes.delete(rec);
  placeBox(rec);
}

function streamNearby() {
  const t = controls.target;
  const near = [];
  for (const rec of state.records) {
    if (rec.removed || rec.deleted || rec.hiddenBy) continue;
    const dx = rec.position[0] - t.x, dz = -rec.position[2] - t.z;
    const d = dx * dx + dz * dz;
    if (d < NEAR_RADIUS * NEAR_RADIUS) near.push([d, rec]);
  }
  near.sort((a, b) => a[0] - b[0]);
  const keep = new Set(near.slice(0, MAX_NODES).map((x) => x[1]));
  if (state.selected) keep.add(state.selected);
  for (const rec of [...state.nodes]) if (!keep.has(rec)) dropNode(rec);
  for (const rec of keep) ensureNode(rec);
}

function addRecord(rec) {
  rec.slot = state.records.length;
  state.records.push(rec);
  state.byId.set(rec.id, rec);
  if (rec.slot >= boxes.instanceMatrix.count) return;     // beyond the batch: shown only up close
  boxes.setColorAt(rec.slot, colourOf(rec.model));
  boxes.instanceColor.needsUpdate = true;
  placeBox(rec);
}

// ------------------------------------------------------------------ loading a map
async function loadMap(name) {
  status(`Loading ${name}...`);
  select(null);
  for (const rec of [...state.nodes]) { rec.node && scene.remove(rec.node); }
  state.nodes.clear();
  if (terrainMesh) scene.remove(terrainMesh);
  if (boxes) scene.remove(boxes);
  state.records = []; state.byId.clear(); state.moved = {}; state.copies = []; state.removed = new Set();

  const [meta, buf, objs] = await Promise.all([
    getJSON(`/api/terrain/${name}.json`),
    fetch(`/api/terrain/${name}.bin`).then((r) => r.arrayBuffer()),
    getJSON(`/api/objects/${name}`),
  ]);
  state.map = name;
  state.terrain = {meta, heights: new Float32Array(buf)};
  terrainMesh = buildTerrain(meta, state.terrain.heights);
  scene.add(terrainMesh);

  const capacity = objs.objects.length + 4096;     // room for copies and other mods' objects
  boxes = new THREE.InstancedMesh(boxGeo, new THREE.MeshLambertMaterial(), capacity);
  boxes.count = 0;
  boxes.instanceColor = new THREE.InstancedBufferAttribute(new Float32Array(capacity * 3), 3);
  scene.add(boxes);
  for (const o of objs.objects) {
    addRecord({...o, original: {position: o.position, rotation: o.rotation}});
    boxes.count = state.records.length;
  }
  for (const [id, e] of Object.entries(objs.edits.moved)) {
    const rec = state.byId.get(id);
    if (!rec) continue;
    rec.position = e.position; rec.rotation = e.rotation;
    rec.loadedAt = e.position;       // where a built copy of this mod puts it in the game
    state.moved[id] = {position: e.position, rotation: e.rotation};
    placeBox(rec);
  }
  for (const c of objs.edits.copies) addCopy(state.byId.get(c.source), c.as, c.position, c.rotation, false);
  for (const id of objs.edits.removed || []) {
    const rec = state.byId.get(id);
    if (!rec) continue;
    rec.deleted = true;
    state.removed.add(id);
    placeBox(rec);
  }
  boxes.count = state.records.length;

  const names = [...new Set(state.records.map((r) => r.name).filter(Boolean))].sort();
  $('names').innerHTML = names.map((n) => `<option value="${n}">`).join('');
  const start = state.records.find((r) => /playerstart|drop_start/i.test(r.name)) || state.records[0];
  if (start) lookAt(start.position, 60);
  const [items, encounters] = await Promise.all([layout.load(name), encs.load(name)]);
  refreshChanges();
  setDirty(false);
  const summary = `${state.records.length} objects, ${items} paths and zones, ${encounters} encounters`;
  status(`${summary}; reading other mods...`);
  loadContext(name, summary);      // in the background: it can take a while the first time
}

// Objects other enabled mods add, move or remove (read only here).
async function loadContext(name, summary) {
  let ctx;
  try {
    ctx = await getJSON(`/api/context/${name}`);
  } catch (err) {
    return status(`${summary}; other mods not shown: ${err.message}`, true);
  }
  if (state.map !== name) return;
  for (const [id, by] of Object.entries(ctx.removed)) {
    const rec = state.byId.get(id);
    if (!rec) continue;
    rec.hiddenBy = by;
    dropNode(rec, true);
    placeBox(rec);
  }
  for (const o of ctx.added) {
    addRecord({...o, context: true, original: {position: o.position, rotation: o.rotation}});
  }
  boxes.count = Math.min(state.records.length, boxes.instanceMatrix.count);
  const mods = [...new Set(ctx.added.map((o) => o.by).concat(Object.values(ctx.removed)))];
  const n = ctx.added.length + Object.keys(ctx.removed).length;
  status(n ? `${summary}; ${ctx.added.length} added and ${Object.keys(ctx.removed).length} moved or removed by ${mods.join(', ')}`
    : `${summary}; no other mod changes objects here`);
}

function lookAt(p, distance = 40) {
  const t = threePos(p);
  controls.target.copy(t);
  const cx = t.x - distance * 0.6, cz = t.z + distance * 0.6;
  // Stay above the ground, which may rise between the camera and the object.
  const ground = Math.max(heightAt(cx, -cz), heightAt((cx + t.x) / 2, -(cz + t.z) / 2));
  camera.position.set(cx, Math.max(t.y + distance * 0.55, ground + 5), cz);
  controls.update();
  streamNearby();
}

// ------------------------------------------------------------------ selection and edits
function select(rec) {
  if (rec && layout.hasSelection()) layout.select(null);
  if (rec && encs.hasSelection()) encs.select(null);
  state.selected = rec;
  gizmo.detach();
  $('selected').hidden = !rec;
  $('hint').hidden = !!rec;
  if (!rec) return;
  const node = ensureNode(rec);
  if (!rec.deleted && !rec.context) {
    gizmo.attach(node);
    setMode(gizmo.getMode());
  }
  showSelected();
}

function showSelected() {
  const rec = state.selected;
  if (!rec) return;
  $('sel-title').textContent = rec.copyName ? `${rec.copyName} (copy)` : (rec.name || '(unnamed)');
  $('copy-name-row').hidden = !rec.copyName;
  if (rec.copyName && document.activeElement !== $('copy-name')) $('copy-name').value = rec.copyName;
  $('sel-model').textContent = rec.model;
  $('sel-layer').textContent = rec.layer;
  [$('px').value, $('py').value, $('pz').value] = rec.position.map((v) => v.toFixed(2));
  $('ph').value = heading(rec.rotation).toFixed(1);
  const edited = rec.copyName || state.moved[rec.id] || rec.deleted;
  $('sel-note').textContent = rec.context ? `Added or moved by ${rec.by} (read only here: edit it in that mod).`
    : rec.deleted ? 'Removed (saved as a rule). Undo changes brings it back.'
    : rec.copyName ? `A copy of ${rec.name || rec.model}.`
    : edited ? 'Moved or turned (saved as a rule).' : '';
  $('revert').disabled = !edited || !!rec.context;
  for (const id of ['mode-move', 'mode-turn', 'ground', 'copy', 'delete', 'px', 'py', 'pz', 'ph']) {
    $(id).disabled = !!rec.deleted || !!rec.context;
  }
  $('delete').title = rec.name && !rec.copyName
    ? `Delete. Scripts may use "${rec.name}" by name; removing it can break them.` : 'Delete';
}

function deleteSelected() {
  const rec = state.selected;
  if (!rec || rec.deleted) return;
  if (rec.copyName) return revert(rec);       // a copy just goes
  if (rec.name && !confirm(`"${rec.name}" has a name, so scripts may use it. Remove it anyway?`)) return;
  delete state.moved[rec.id];
  rec.position = rec.original.position; rec.rotation = rec.original.rotation;
  rec.deleted = true;
  state.removed.add(rec.id);
  live.objectRemoved(rec);
  select(null);
  dropNode(rec, true);
  placeBox(rec);
  setDirty(true);
  refreshChanges();
}

function fromNode(rec) {
  rec.position = gamePos(rec.node.position);
  rec.rotation = m3From(rec.node.quaternion);
  recordEdit(rec);
  showSelected();
}

function recordEdit(rec) {
  if (rec.copyName) {
    const c = state.copies.find((x) => x.rec === rec);
    c.position = rec.position; c.rotation = rec.rotation;
  } else {
    state.moved[rec.id] = {position: rec.position, rotation: rec.rotation};
    live.objectMoved(rec);
  }
  setDirty(true);
  refreshChanges();
}

function revert(rec) {
  if (rec.copyName) {
    state.copies = state.copies.filter((x) => x.rec !== rec);
    select(null);
    scene.remove(rec.node); rec.node = null; state.nodes.delete(rec);
    state.byId.delete(rec.id);
    rec.removed = true;     // keeps its slot in the box batch, hidden
    boxes.setMatrixAt(rec.slot, new THREE.Matrix4().makeScale(0, 0, 0));
    boxes.instanceMatrix.needsUpdate = true;
  } else if (rec.deleted) {
    if (live.isOn()) status('Restored here; the game brings it back when it next starts with this mod.');
    rec.deleted = false;
    state.removed.delete(rec.id);
    select(null);
    dropNode(rec, true);         // the ghost goes; streaming brings the object back
    placeBox(rec);
    ensureNode(rec);
    select(rec);
  } else {
    delete state.moved[rec.id];
    rec.position = rec.original.position; rec.rotation = rec.original.rotation;
    if (rec.node) transformOf(rec).decompose(rec.node.position, rec.node.quaternion, rec.node.scale);
    placeBox(rec);
    showSelected();
    live.objectMoved(rec);
  }
  setDirty(true);
  refreshChanges();
}

let copySerial = 1;
function addCopy(src, name, position, rotation, select_ = true) {
  if (!src) return null;
  const rec = {
    id: `copy:${copySerial++}`, layer: src.layer, name: src.name, model: src.model,
    position, rotation, copyName: name, source: src.id,
  };
  addRecord(rec);
  boxes.count = state.records.length;
  state.copies.push({rec, source: src.id, as: name, position, rotation});
  if (select_) { ensureNode(rec); select(rec); setDirty(true); refreshChanges(); }
  return rec;
}

function copySelected() {
  const rec = state.selected;
  if (!rec) return;
  const base = (rec.copyName || rec.name || rec.model).replace(/_copy\d+$/, '');
  let k = 1;
  const taken = takenNames();
  while (taken.has(`${base}_copy${k}`.toLowerCase())) k++;
  const name = `${base}_copy${k}`;    // renamed in the panel
  const src = rec.copyName ? state.byId.get(rec.source) : rec;
  const p = [...rec.position];
  p[0] += 4;
  p[1] = round(heightAt(p[0], p[2]));
  addCopy(src, name, p, rec.rotation);
}

function takenNames(except = null) {
  return new Set(state.records.filter((r) => !r.removed && r !== except)
    .map((r) => (r.copyName || r.name || '').toLowerCase()).filter(Boolean));
}

function renameCopy(rec, name) {
  name = name.trim();
  if (!/^[A-Za-z0-9_\-]+$/.test(name)) return status('Names use letters, digits, _ and - only', true);
  if (takenNames(rec).has(name.toLowerCase())) return status(`Another object is already named ${name}`, true);
  rec.copyName = name;
  state.copies.find((c) => c.rec === rec).as = name;
  status('');
  setDirty(true);
  refreshChanges();
  showSelected();
}

function setDirty(d) {
  state.dirty = d;
  $('save').disabled = !d;
}

function refreshChanges() {
  const items = [];
  for (const id of Object.keys(state.moved)) {
    const rec = state.byId.get(id);
    if (rec) items.push([rec, `Moved ${rec.name || rec.model}`]);
  }
  for (const c of state.copies) items.push([c.rec, `Copy ${c.as} (of ${c.rec.name || c.rec.model})`]);
  for (const id of state.removed) {
    const rec = state.byId.get(id);
    if (rec) items.push([rec, `Removed ${rec.name || rec.model}`]);
  }
  $('count').textContent = items.length ? `(${items.length})` : '';
  const ul = $('changes');
  ul.innerHTML = '';
  for (const [rec, text] of items) {
    const li = document.createElement('li');
    li.textContent = text;
    li.onclick = () => { lookAt(rec.position, 30); select(rec); };
    ul.appendChild(li);
  }
}

async function save() {
  const body = {
    moved: state.moved,
    copies: state.copies.map((c) => ({source: c.source, as: c.as, position: c.position, rotation: c.rotation})),
    removed: [...state.removed],
  };
  status('Saving...');
  try {
    const post = async (url, data) => {
      const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
                                  body: JSON.stringify(data)});
      const doc = await r.json();
      if (!r.ok) throw new Error(doc.error || r.statusText);
      return doc;
    };
    const lay = layout.saveBody();
    let note = '';
    const encBody = encs.saveBody();
    if (encBody) {
      const done = await post('/api/encounters/save', encBody);
      encs.saved();
      note += `; squads in ${done.files.join(', ')}`;
    }
    if (lay) {
      const done = await post('/api/layout/save', lay);
      layout.saved();
      const parts = [];
      if (done.game) parts.push(`${done.game} game paths/zones to layout/`);
      if (done.files.length) parts.push(done.files.join(', '));
      note += parts.length ? `; ${parts.join('; ')}` : '';
    }
    const doc = await post('/api/save', body);
    setDirty(false);
    status(`Saved ${doc.rules} object rules to ${doc.file}${note}`);
  } catch (e) {
    status(`Not saved: ${e.message}`, true);
  }
}

function setMode(mode) {
  gizmo.setMode(mode);
  const turn = mode === 'rotate';
  gizmo.showX = !turn; gizmo.showZ = !turn; gizmo.showY = true;
  $('mode-move').classList.toggle('on', !turn);
  $('mode-turn').classList.toggle('on', turn);
}

// ------------------------------------------------------------------ input
const ray = new THREE.Raycaster();
const pointer = new THREE.Vector2();
let downAt = null;
function pick(e) {
  const r = renderer.domElement.getBoundingClientRect();
  pointer.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  ray.setFromCamera(pointer, camera);
  return ray;
}
renderer.domElement.addEventListener('pointerdown', (e) => { downAt = [e.clientX, e.clientY]; });
renderer.domElement.addEventListener('pointerup', (e) => {
  if (!downAt || Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 4 || e.button !== 0) return;
  if (gizmo.dragging || gizmo.axis) return;
  pick(e);
  if (layout.isDrawing()) { layout.drawClick(ray); return; }
  if (encs.isPlacing()) { encs.placeClick(ray); return; }
  // The nearest of an object or a path/zone (point handles win).
  let objectHit = null;
  if (boxes.visible) {
    const targets = [boxes, ...[...state.nodes].map((r) => r.node)];
    for (const h of ray.intersectObjects(targets, true)) {
      if (h.object === boxes) {
        const rec = state.records.find((r) => r.slot === h.instanceId);
        if (rec && !rec.node && !rec.removed) { objectHit = {distance: h.distance, rec}; break; }
        continue;
      }
      let o = h.object;
      while (o && !o.userData.rec) o = o.parent;
      if (o) { objectHit = {distance: h.distance, rec: o.userData.rec}; break; }
    }
  }
  const candidates = [layout.pick(ray), encs.pick(ray)].filter(Boolean);
  if (objectHit) candidates.push({distance: objectHit.distance, apply: () => select(objectHit.rec)});
  candidates.sort((a, b) => a.distance - b.distance);
  if (candidates.length) return candidates[0].apply();
  select(null);
  layout.select(null);
  encs.select(null);
});
renderer.domElement.addEventListener('dblclick', (e) => {
  pick(e);
  const hit = ray.intersectObject(terrainMesh)[0];
  if (hit) { controls.target.copy(hit.point); controls.update(); streamNearby(); }
});

$('mode-move').onclick = () => setMode('translate');
$('mode-turn').onclick = () => setMode('rotate');
$('ground').onclick = () => {
  const rec = state.selected;
  if (!rec) return;
  rec.node.position.y = heightAt(rec.position[0], rec.position[2]);
  fromNode(rec);
};
$('copy').onclick = copySelected;
$('delete').onclick = deleteSelected;
$('copy-name').addEventListener('change', () => state.selected && renameCopy(state.selected, $('copy-name').value));
$('revert').onclick = () => state.selected && revert(state.selected);
$('save').onclick = save;
for (const id of ['px', 'py', 'pz']) {
  $(id).addEventListener('change', () => {
    const rec = state.selected;
    if (!rec) return;
    const p = [$('px').value, $('py').value, $('pz').value].map((v) => (v.trim() === '' ? NaN : +v));
    if (p.some((v) => !Number.isFinite(v))) return showSelected();    // ignore a blank or bad entry
    rec.node.position.copy(threePos(p));
    fromNode(rec);
  });
}
$('ph').addEventListener('change', () => {
  const rec = state.selected;
  if (!rec) return;
  if ($('ph').value.trim() === '' || !Number.isFinite(+$('ph').value)) return showSelected();
  const delta = +$('ph').value - heading(rec.rotation);
  // A game turn of t degrees is a three.js turn of -t about the vertical.
  rec.node.rotateOnWorldAxis(new THREE.Vector3(0, 1, 0), THREE.MathUtils.degToRad(-delta));
  fromNode(rec);
});
$('search').addEventListener('change', () => {
  const q = $('search').value.trim().toLowerCase();
  if (!q) return;
  const live = state.records.filter((r) => !r.removed);
  const rec = live.find((r) => (r.copyName || r.name || '').toLowerCase() === q)
    || live.find((r) => (r.name || '').toLowerCase().includes(q) || r.model.toLowerCase().includes(q));
  if (rec) { lookAt(rec.position, 30); select(rec); status(''); return; }
  const item = layout.find(q);
  if (item) { lookAt(item.points[0], 80); select(null); layout.select(item); status(''); return; }
  const found = encs.find(q);
  if (found) { lookAt(found.at, 30); select(null); layout.select(null); encs.select(found); status(''); return; }
  status(`Nothing matches "${q}"`, true);
});
window.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT') return;
  const k = e.key.toLowerCase();
  if (k === 'w') setMode('translate');
  else if (k === 'e') setMode('rotate');
  else if (k === 'g') $('ground').click();
  else if (k === 'c') copySelected();
  else if (k === 'enter' && layout.isDrawing()) layout.finishDraw();
  else if (k === 'insert' && layout.hasSelection()) layout.addPoint();
  else if (k === 'delete') {
    if (layout.hasSelection()) layout.deletePoint();
    else if (encs.hasSelection()) encs.deleteSelected();
    else deleteSelected();
  } else if (k === 'escape') {
    if (layout.isDrawing()) { layout.cancelDraw(); status(''); }
    else if (encs.isPlacing()) { encs.cancelPlace(); status(''); }
    else { select(null); layout.select(null); encs.select(null); }
  }
  else if (k === 's' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); if (state.dirty) save(); }
});
window.addEventListener('beforeunload', (e) => { if (state.dirty) { e.preventDefault(); e.returnValue = ''; } });
$('map').addEventListener('change', async () => {
  if (state.dirty && !confirm('Discard unsaved changes?')) { $('map').value = state.map; return; }
  try { await loadMap($('map').value); } catch (err) { status(err.message, true); }
});

// ------------------------------------------------------------------ paths and zones
let modName = '';
const slug = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '') || 'mod';
const layout = createLayout({
  scene, gizmo, heightAt, threePos, gamePos, status, $, setDirty, lookAt,
  deselectObject: () => { select(null); encs.select(null); },
  modName: () => modName,
  terrain: () => terrainMesh,
  encounterFiles: () => encs.files(),
});
const encs = createEncounters({
  scene, gizmo, heightAt, threePos, gamePos, status, $, setDirty, camera, slug,
  deselectOthers: () => { select(null); layout.select(null); },
  modName: () => modName,
  terrain: () => terrainMesh,
  layoutFiles: () => layout.files(),
  objectNames: () => new Set(state.records.filter((r) => !r.removed)
    .map((r) => (r.copyName || r.name || '').toLowerCase()).filter(Boolean)),
});
const live = createLive({
  scene, threePos, heightAt, status, $, lookAt, heading,
  target: () => gamePos(controls.target),
});
$('show-objects').addEventListener('change', (e) => {
  const on = e.target.checked;
  if (boxes) boxes.visible = on;
  for (const rec of state.nodes) if (rec.node) rec.node.visible = on;
  if (!on) select(null);
});

// ------------------------------------------------------------------ start
let lastStream = 0;
function frame(t) {
  controls.update();
  if (t - lastStream > 500) { lastStream = t; if (state.records.length) streamNearby(); }
  renderer.render(scene, camera);
  requestAnimationFrame(frame);
}

async function start() {
  resize();
  requestAnimationFrame(frame);
  try {
    const info = await getJSON('/api/info');
    $('mod').textContent = info.mod;
    modName = info.mod;
    const maps = info.maps.filter((m) => ['swn', 'sw', 'nw'].includes(m)).concat(
      info.maps.filter((m) => !['swn', 'sw', 'nw'].includes(m)));
    $('map').innerHTML = maps.map((m) => `<option>${m}</option>`).join('');
    $('map').value = maps.includes('swn') ? 'swn' : maps[0];
    await loadMap($('map').value);
  } catch (err) {
    status(err.message, true);
  }
}
start();
