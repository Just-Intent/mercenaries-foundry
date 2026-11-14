// Mercenaries Foundry editor: a map's terrain and objects, with object moves,
// turns and copies saved as mod rules (objects/editor.json).
//
// Coordinates: the game's world is right-handed, like three.js, so the 3D
// view uses the game's (x, y, z) as they are, and models come from the glTF
// export already in the game's object axes (decoded data turned half round).
import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {TransformControls} from 'three/addons/controls/TransformControls.js';
import {GLTFLoader} from 'three/addons/loaders/GLTFLoader.js';
import {createLayout} from './layout.js';
import {createEncounters} from './encounters.js';
import {createLive} from './live.js';
import {applyGround} from './ground.js';
import {createMinimap} from './minimap.js';
import {createTerrainEditor} from './terrain_edit.js';
import {createMissions} from './missions.js';

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
  else if (gizmo.object && gizmo.object.userData.start) missions.startMoved();
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
const boxGeo = new THREE.BoxGeometry(1, 1, 1);            // stand-ins: scaled to each model's bounds
const bounds = {};                                          // model -> [min x, y, z, max x, y, z] (game axes)
const selMat = new THREE.MeshLambertMaterial({color: 0xffcc33});

const threePos = (p) => new THREE.Vector3(p[0], p[1], p[2]);
const gamePos = (v) => [round(v.x), round(v.y), round(v.z)];
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
  // three column 0 is the object's x axis.
  const gx = m3[0], gz = m3[2];
  return Math.round(THREE.MathUtils.radToDeg(Math.atan2(-gz, gx)) * 100) / 100;
}
// Stand-in colours by kind of object (objectType).
const KIND_COLOURS = [
  [/^(building|static|fixture|bridge)/i, 0xb7ab92],
  [/^tree/i, 0x4f8a3c],
  [/^(prop|constrained|crate|itemcache|destructible)/i, 0xc58a4a],
  [/^human/i, 0xe0504a],
  [/^(jeep|tank|car|truck|apc|heli|plane|boat|bike|vehicle)/i, 0x4a8ee0],
  [/^(location|marker|spawn|trigger)/i, 0xf0d040],
];
function colourOf(rec) {
  for (const [re, c] of KIND_COLOURS) if (re.test(rec.type || '')) return new THREE.Color(c);
  return new THREE.Color(0xa0a8b0);
}
// A unit box's placement inside an object: its model's bounds (centre and
// size), or a 1.5 m marker for models without them.
function standIn(rec) {
  const b = bounds[rec.geometry || rec.model];
  const m = new THREE.Matrix4();
  if (!b) return m.makeTranslation(0, 0.75, 0).multiply(new THREE.Matrix4().makeScale(1.5, 1.5, 1.5));
  const size = [Math.max(b[3] - b[0], 0.3), Math.max(b[4] - b[1], 0.3), Math.max(b[5] - b[2], 0.3)];
  // The header's box is in decoded model axes: turned half round, as the export.
  m.makeTranslation(-(b[0] + b[3]) / 2, (b[1] + b[4]) / 2, -(b[2] + b[5]) / 2);
  return m.multiply(new THREE.Matrix4().makeScale(...size));
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
      pos.set([x0 + k * spacing, h, z0 + r * spacing], i * 3);
      c.copy(low).lerp(high, (h - lo) / Math.max(hi - lo, 1));
      col.set([c.r, c.g, c.b], i * 3);
    }
  }
  const idx = new Uint32Array((size - 1) * (size - 1) * 6);
  let n = 0;
  for (let r = 0; r < size - 1; r++) {
    for (let k = 0; k < size - 1; k++) {
      const a = r * size + k, b = a + size, c1 = a + 1, d = b + 1;
      idx.set([a, b, c1, c1, b, d], n);   // wound so the ground faces up (+z is row by row)
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
  const m = hidden ? new THREE.Matrix4().makeScale(0, 0, 0) : transformOf(rec).multiply(standIn(rec));
  boxes.setMatrixAt(rec.slot, m);
  boxes.instanceMatrix.needsUpdate = true;
}

function loadModel(name) {
  if (!modelCache.has(name)) {
    modelCache.set(name, fetch(`/api/model/${encodeURIComponent(name)}.glb`)
      .then((r) => { if (!r.ok) throw new Error(r.statusText); return r.arrayBuffer(); })
      .then((buf) => new Promise((ok, bad) => loader.parse(buf, '', (g) => {
        // Both sides: some game surfaces (roofs, for one) face the other way
        // from how the export winds them, and would vanish from above.
        g.scene.traverse((o) => { if (o.isMesh) for (const m of [].concat(o.material)) m.side = THREE.DoubleSide; });
        ok(g.scene);
      }, bad)))
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
  const box = new THREE.Mesh(boxGeo, new THREE.MeshLambertMaterial({color: colourOf(rec)}));
  standIn(rec).decompose(box.position, box.quaternion, box.scale);
  node.add(box);
  scene.add(node);
  rec.node = node;
  state.nodes.add(rec);
  placeBox(rec);
  if (rec.deleted) ghost(node);
  loadModel(rec.geometry || rec.model).then(async (model) => {
    if (!model || rec.node !== node) return;
    const body = model.clone(true);
    // Gates: the doors are models of their own, hung on the gate's joints.
    for (const [joint, part] of rec.parts || []) {
      const at = body.getObjectByName(joint), door = await loadModel(part);
      if (!at || !door) continue;
      const d = door.clone(true);
      d.scale.set(1, 1, 1);        // already inside the gate's (turned) model space
      at.add(d);
    }
    if (rec.node !== node) return;
    node.remove(box);
    node.add(body);
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
    const dx = rec.position[0] - t.x, dz = rec.position[2] - t.z;
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
  boxes.setColorAt(rec.slot, colourOf(rec));
  boxes.instanceColor.needsUpdate = true;
  placeBox(rec);
}

// ------------------------------------------------------------------ loading a map
// Switching maps while one loads: the older load stops at its next wait
// (else both add their objects and copies, and saving finds copies twice).
let loadSerial = 0;
async function loadMap(name) {
  const mine = ++loadSerial;
  status(`Loading ${name}...`);
  select(null);
  for (const rec of [...state.nodes]) { rec.node && scene.remove(rec.node); }
  state.nodes.clear();
  if (terrainMesh) scene.remove(terrainMesh);
  if (boxes) scene.remove(boxes);
  state.records = []; state.byId.clear(); state.moved = {}; state.copies = []; state.removed = new Set();
  terrainEdit.reset();
  state.placedFiles = new Set(); state.placing = null;

  const [meta, buf, objs, sizes] = await Promise.all([
    getJSON(`/api/terrain/${name}.json`),
    fetch(`/api/terrain/${name}.bin`).then((r) => r.arrayBuffer()),
    getJSON(`/api/objects/${name}`),
    getJSON(`/api/bounds/${name}`),
  ]);
  if (mine !== loadSerial) return;
  Object.assign(bounds, sizes);
  state.map = name;
  state.terrain = {meta, heights: new Float32Array(buf)};
  terrainMesh = buildTerrain(meta, state.terrain.heights);
  minimap.setTerrain(meta, state.terrain.heights);
  scene.add(terrainMesh);
  // The game's ground textures, in the background (plain shading until then).
  const ground = terrainMesh;
  applyGround(ground, name, scene.fog, {...meta, heights: state.terrain.heights})
    .catch((err) => console.warn('ground textures:', err));

  const capacity = objs.objects.length + 4096;     // room for copies and other mods' objects
  // See-through, so big stand-ins (a forest model can cover a hillside) show
  // where things are without hiding what's behind them.
  boxes = new THREE.InstancedMesh(boxGeo, new THREE.MeshLambertMaterial({transparent: true, opacity: 0.45, depthWrite: false}), capacity);
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
    rec.loadedRot = e.rotation;
    state.moved[id] = {position: e.position, rotation: e.rotation};
    placeBox(rec);
  }
  for (const c of objs.edits.copies) addCopy(state.byId.get(c.source), c.as, c.position, c.rotation, false);
  // Objects placed from templates, in the mod's layers for this map.
  state.layerFile = objs.edits.layer_file;
  if (objs.edits.placed.length && !state.templates) state.templates = await getJSON('/api/templates');
  if (mine !== loadSerial) return;
  for (const p of objs.edits.placed) {
    addPlaced(p.from, p.name, p.file, p.position, p.rotation, false, p.set);
    state.placedFiles.add(p.file);
  }
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
  missions.load(name).catch((err) => console.warn('missions:', err));
  if (mine !== loadSerial) return;
  minimap.setWater(waterAreas ? waterAreas() : []);
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
  try { Object.assign(bounds, await getJSON(`/api/context-bounds/${name}`)); } catch { /* boxes stay markers */ }
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
  const ground = Math.max(heightAt(cx, cz), heightAt((cx + t.x) / 2, (cz + t.z) / 2));
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
  $('sel-title').textContent = rec.copyName ? `${rec.copyName} (${rec.template ? 'placed from ' + rec.template : 'copy'})`
                                             : (rec.name || '(unnamed)');
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
    live.objectMoved(rec);
  } else {
    state.moved[rec.id] = {position: rec.position, rotation: rec.rotation};
    live.objectMoved(rec);
  }
  setDirty(true);
  refreshChanges();
}

function revert(rec) {
  if (rec.copyName) {
    live.objectRemoved(rec);
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
    geometry: src.geometry, type: src.type, parts: src.parts,
    position, rotation, copyName: name, source: src.id,
  };
  addRecord(rec);
  boxes.count = state.records.length;
  state.copies.push({rec, source: src.id, as: name, position, rotation});
  if (select_) {
    ensureNode(rec); select(rec); setDirty(true); refreshChanges();
    // Copies spawn in the running game from the source's own properties (the
    // game finds the source as a move would).
    live.copy(src, name, position, heading(rotation));
  }
  return rec;
}

// What a placed object draws: its template's model, or (for "from" a named
// object or "model:<name>") that object's.
function placedLook(from) {
  const low = from.toLowerCase();
  const t = (state.templates || encs.templates() || []).find((x) => x.name.toLowerCase() === low);
  if (t) return {model: t.model, geometry: t.geometry || t.model, type: t.type};
  if (low.startsWith('model:')) return {model: from.slice(6), geometry: from.slice(6), type: ''};
  const r = state.records.find((x) => !x.removed && (x.name || '').toLowerCase() === low);
  return r ? {model: r.model, geometry: r.geometry, type: r.type} : {model: from, geometry: from, type: ''};
}

let placedSerial = 1;
function addPlaced(from, name, file, position, rotation, select_ = true, set = null) {
  const look = placedLook(from);
  const rec = {
    id: `placed:${placedSerial++}`, layer: file.replace(/\.json$/, ''), name: from, ...look,
    position, rotation, copyName: name, template: from, file,
  };
  addRecord(rec);
  boxes.count = state.records.length;
  state.copies.push({rec, template: from, file, as: name, position, rotation, set});
  if (select_) {
    ensureNode(rec); select(rec); setDirty(true); refreshChanges();
    live.spawn(from, name, position, heading(rotation));    // shows at once if the game has it loaded here
  }
  return rec;
}

function freshPlacedName(from) {
  const base = from.replace(/^template_/i, '').replace(/^model:/i, '') || 'object';
  const taken = takenNames();
  let k = 1;
  while (taken.has(`${base}_${k}`.toLowerCase())) k++;
  return `${base}_${k}`;
}

function startPlace() {
  const from = $('place-template').value.trim();
  if (!from) return status('Type or pick a template to place first', true);
  const low = from.toLowerCase();
  const known = (encs.templates() || []).some((t) => t.name.toLowerCase() === low) ||
    state.records.some((r) => !r.removed && (r.name || '').toLowerCase() === low);
  if (!known) return status(`No template or named object called ${from}`, true);
  select(null); layout.select(null); encs.select(null);
  state.placing = from;
  status(`Click the ground to place ${from} (in encounters/${state.layerFile}, which loads with the map); Esc cancels.`);
}

function placeAt(ray) {
  const hit = ray.intersectObject(terrainMesh)[0];
  if (!hit) return;
  const from = state.placing;
  state.placing = null;
  const at = gamePos(hit.point).map(round);
  addPlaced(from, freshPlacedName(from), state.layerFile, at, [1, 0, 0, 0, 1, 0, 0, 0, 1]);
  status(`Placed ${from}; rename it in the panel if you like.`);
}

function copySelected() {
  const rec = state.selected;
  if (!rec) return;
  if (rec.template) {         // another of the same template, beside it
    const p = [...rec.position];
    p[0] += 4;
    p[1] = round(heightAt(p[0], p[2]));
    addPlaced(rec.template, freshPlacedName(rec.template), rec.file, p, rec.rotation);
    return;
  }
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
  if (live.isOn() && (rec.template || rec.name)) {    // the game knows it by its old name: spawn it again
    live.removeNamed(rec.copyName);
    live.spawn(rec.template || rec.name, name, rec.position, heading(rec.rotation));
  }
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
  for (const c of state.copies) {
    items.push([c.rec, c.template ? `Placed ${c.as} (${c.template})` : `Copy ${c.as} (of ${c.rec.name || c.rec.model})`]);
  }
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
  // A zone or path still being drawn is finished first, so Save keeps it.
  if (layout.isDrawing()) layout.finishDraw();
  const body = {
    map: state.map,
    moved: state.moved,
    copies: state.copies.filter((c) => !c.template)
      .map((c) => ({source: c.source, as: c.as, position: c.position, rotation: c.rotation})),
    removed: [...state.removed],
  };
  // Placed objects, by layer file (files that lost all theirs get an empty list).
  const placed = {};
  for (const f of state.placedFiles) placed[f] = [];
  for (const c of state.copies.filter((x) => x.template)) {
    (placed[c.file] ||= []).push({from: c.template, name: c.as, position: c.position, rotation: c.rotation,
                                  ...(c.set ? {set: c.set} : {})});
  }
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
    if (Object.keys(placed).length) {
      const done = await post('/api/placed/save', {map: state.map, files: placed});
      for (const f of Object.keys(placed)) state.placedFiles.add(f);
      note += `; placed objects in ${done.files.join(', ')}`;
    }
    // Missions first: a mission's layer file is made with it (and so isn't
    // set to load with the map when squads are saved into it).
    // A mission that can't be saved yet doesn't hold up the rest.
    try {
      const mission = missions.saveBody();
      if (mission) {
        const done = await post('/api/missions/save', mission);
        await missions.saved(done);
        note += `; mission ${done.id} in ${done.file}`;
      }
    } catch (e) {
      note += `; mission not saved: ${e.message}`;
    }
    const terr = terrainEdit.saveBody(), paint = terrainEdit.paintBody(), roads = terrainEdit.roadsBody();
    if (terr) {
      const done = await post('/api/terrain/save', terr);
      note += `; ground heights in ${done.file}`;
    }
    if (paint) {
      const done = await post('/api/terrain/paint', paint);
      note += `; ground paint in ${done.files.join(', ')}`;
    }
    if (roads) {
      const done = await post('/api/terrain/roads', roads);
      note += `; roads in ${done.file}`;
    }
    if (terrainEdit.saved() && terrainMesh) {
      // The new roads as the game lays them: the ground and its decals again.
      applyGround(terrainMesh, state.map, scene.fog, {...state.terrain.meta, heights: state.terrain.heights})
        .catch((err) => console.warn('ground textures:', err));
    }
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
    missions.load(state.map).catch(() => {});      // squads and zones just saved into a mission's layer
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
renderer.domElement.addEventListener('pointerdown', (e) => {
  downAt = [e.clientX, e.clientY];
  if (terrainEdit.down(e)) { try { renderer.domElement.setPointerCapture(e.pointerId); } catch (err) { /* no capture */ } }
});
renderer.domElement.addEventListener('pointerup', (e) => {
  if (terrainEdit.up() || terrainEdit.isOn()) return;
  if (!downAt || Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 4 || e.button !== 0) return;
  if (gizmo.dragging || gizmo.axis) return;
  pick(e);
  if (layout.isDrawing()) { layout.drawClick(ray); return; }
  if (encs.isPlacing()) { encs.placeClick(ray); return; }
  if (state.placing) { placeAt(ray); return; }
  // The nearest of an object or a path/zone (point handles win).
  let objectHit = null;
  if (boxes && boxes.visible) {        // (no map yet while loading)
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
  const candidates = [layout.pick(ray), encs.pick(ray), missions.pick(ray)].filter(Boolean);
  if (objectHit) candidates.push({distance: objectHit.distance, apply: () => select(objectHit.rec)});
  candidates.sort((a, b) => a.distance - b.distance);
  if (candidates.length) return candidates[0].apply();
  select(null);
  layout.select(null);
  encs.select(null);
});
// Hover: what an object is (name, model, kind), without selecting it.
let hoverPending = null;
function objectUnder(e) {
  pick(e);
  const targets = [boxes, ...[...state.nodes].map((r) => r.node)].filter((t) => t && t.visible);
  for (const h of ray.intersectObjects(targets, true)) {
    if (h.object === boxes) {
      const rec = state.records.find((r) => r.slot === h.instanceId);
      if (rec && !rec.node && !rec.removed) return rec;
      continue;
    }
    let o = h.object;
    while (o && !o.userData.rec) o = o.parent;
    if (o) return o.userData.rec;
  }
  return null;
}
renderer.domElement.addEventListener('pointermove', (e) => {
  if (terrainEdit.move(e)) { $('tip').hidden = true; return; }
  if (hoverPending || e.buttons) { if (e.buttons) $('tip').hidden = true; return; }
  hoverPending = requestAnimationFrame(() => {
    hoverPending = null;
    const rec = boxes ? objectUnder(e) : null;
    const tip = $('tip');
    // Objects first; else a squad member, spawn point, path or zone.
    let title, text;
    if (rec) {
      title = rec.copyName ? `${rec.copyName} (copy)` : rec.name || '(unnamed)';
      text = [rec.geometry || rec.model, rec.type].filter(Boolean).join(' · ') +
             (rec.context ? `\nfrom ${rec.by}` : rec.deleted ? '\nremoved' : '');
    } else {
      const other = [encs.hoverText(ray), layout.hoverText(ray)].filter(Boolean)
        .sort((a, b) => a.distance - b.distance)[0];
      if (!other) { tip.hidden = true; return; }
      ({title, text} = other);
    }
    tip.innerHTML = '';
    const b = document.createElement('b');
    b.textContent = title;
    tip.append(b, document.createTextNode(`\n${text}`));
    const r = view.getBoundingClientRect();
    tip.style.left = `${Math.min(e.clientX - r.left + 14, r.width - 220)}px`;
    tip.style.top = `${e.clientY - r.top + 14}px`;
    tip.hidden = false;
  });
});
renderer.domElement.addEventListener('pointerleave', () => { $('tip').hidden = true; });
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
$('place').onclick = startPlace;
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
// Search suggestions: objects, zones and paths, encounters and squads.
function fillNames() {
  const names = [...new Set([...state.records.map((r) => r.copyName || r.name).filter(Boolean),
                             ...layout.names(), ...encs.names()])].sort();
  $('names').innerHTML = names.map((n) => `<option value="${n}">`).join('');
}
$('search').addEventListener('focus', fillNames);
$('search').addEventListener('change', () => showByName($('search').value));
function showByName(name) {
  const q = (name || '').trim().toLowerCase();
  if (!q) return false;
  const live = state.records.filter((r) => !r.removed);
  const rec = live.find((r) => (r.copyName || r.name || '').toLowerCase() === q)
    || live.find((r) => (r.name || '').toLowerCase().includes(q) || r.model.toLowerCase().includes(q));
  // Exact names first (a zone named like part of an object's name wins over it).
  const item = layout.find(q);
  const exactItem = item && item.name.toLowerCase() === q;
  const squad = q.includes(':') ? encs.find(q) : null;
  if (rec && !exactItem && !squad) { lookAt(rec.position, 30); select(rec); status(''); return true; }
  if (item && !squad) {
    // A zone or path: look at its middle, from far enough to see it whole.
    const mid = [0, 1, 2].map((k) => item.points.reduce((s, p) => s + p[k], 0) / item.points.length);
    const span = Math.max(...item.points.map((p) => Math.hypot(p[0] - mid[0], p[2] - mid[2])), 20);
    lookAt(mid, span * 2.2); select(null); layout.select(item); status(''); return true;
  }
  const found = squad || encs.find(q);
  if (found) { lookAt(found.at, 30); select(null); layout.select(null); encs.select(found); status(''); return true; }
  status(`Nothing matches "${q}"`, true);
  return false;
}
window.addEventListener('keydown', (e) => {
  if (e.target.tagName === 'INPUT') return;
  const k = e.key.toLowerCase();
  if (k === 'w') setMode('translate');
  else if (k === 'e') setMode('rotate');
  else if (k === 'g') $('ground').click();
  else if (k === 'c') copySelected();
  else if (k === 'p') startPlace();
  else if (k === 'm') minimap.toggle();
  else if (k === 't') terrainEdit.setOn(!terrainEdit.isOn());
  else if (k === 'z' && (e.ctrlKey || e.metaKey) && terrainEdit.isOn()) { e.preventDefault(); terrainEdit.undo(); }
  else if (k === 'enter' && terrainEdit.isLayingRoad()) terrainEdit.finishRoad();
  else if (k === 'enter' && layout.isDrawing()) layout.finishDraw();
  else if (k === 'insert' && layout.hasSelection()) layout.addPoint();
  else if (k === 'delete') {
    if (layout.hasSelection()) layout.deletePoint();
    else if (encs.hasSelection()) encs.deleteSelected();
    else deleteSelected();
  } else if (k === 'escape') {
    if (terrainEdit.isLayingRoad()) { terrainEdit.cancelRoad(); status('Road cancelled'); }
    else if (terrainEdit.isOn()) terrainEdit.setOn(false);
    else if (layout.isDrawing()) { layout.cancelDraw(); status(''); }
    else if (encs.isPlacing()) { encs.cancelPlace(); status(''); }
    else if (state.placing) { state.placing = null; status(''); }
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
// Terrain editing: brushes on the height samples; the mesh and minimap follow.
const terrainEdit = createTerrainEditor({
  $, controls, status, setDirty: (v) => setDirty(v), gamePos: (v) => gamePos(v), pick: (e) => pick(e),
  scene, threePos: (p) => threePos(p), heightAt: (x, z) => heightAt(x, z),
  terrain: () => ({mesh: terrainMesh, meta: state.terrain.meta, heights: state.terrain.heights}),
  onHeights: () => minimap.setTerrain(state.terrain.meta, state.terrain.heights),
});
// Missions: contracts, their objectives and their layer's squads and zones.
const missions = createMissions({
  $, status, setDirty: (v) => setDirty(v), show: (name) => showByName(name),
  scene, threePos: (p) => threePos(p), lookAt: (p) => lookAt(p, 30),
  viewCentre: () => { const g = gamePos(controls.target); g[1] = round(heightAt(g[0], g[2])); return g; },
  newSquadIn: (file) => encs.startNewSquad(file),
  drawZoneIn: (file) => layout.startDraw('boundary', file),
  gamePos: (v) => gamePos(v), groundAt: (x, z) => heightAt(x, z),
  // The start marker takes the gizmo (move along the ground, or turn).
  grab: (node) => { select(null); layout.select(null); encs.select(null); gizmo.attach(node); setMode(gizmo.getMode()); },
  release: (node) => { if (gizmo.object === node) gizmo.detach(); },
});
// The minimap (water areas come from the layout module once it has loaded).
let waterAreas = null;
const minimap = createMinimap({$, heightAt, lookAt: (p, d) => lookAt(p, d)});
const layout = createLayout({
  onWater: (fn) => { waterAreas = fn; },
  scene, gizmo, heightAt, threePos, gamePos, status, $, setDirty, lookAt,
  deselectObject: () => { select(null); encs.select(null); },
  modName: () => modName,
  terrain: () => terrainMesh,
  encounterFiles: () => encs.files(),
});
const encs = createEncounters({
  scene, gizmo, heightAt, threePos, gamePos, status, $, setDirty, camera, slug,
  live: () => live,
  deselectOthers: () => { select(null); layout.select(null); },
  modName: () => modName,
  terrain: () => terrainMesh,
  layoutFiles: () => layout.files(),
  layerFile: () => state.layerFile,
  objectNames: () => new Set(state.records.filter((r) => !r.removed)
    .map((r) => (r.copyName || r.name || '').toLowerCase()).filter(Boolean)),
});
const live = createLive({
  scene, threePos, heightAt, status, $, lookAt, heading,
  onLoaded: (names) => encs.setLoaded(names),
  target: () => gamePos(controls.target),
  sourceOf: (rec) => state.byId.get(rec.source),
});
$('show-objects').addEventListener('change', (e) => {
  const on = e.target.checked;
  if (boxes) boxes.visible = on;
  for (const rec of state.nodes) if (rec.node) rec.node.visible = on;
  if (!on) select(null);
});

// ------------------------------------------------------------------ start
let lastStream = 0;
let lastMinimap = 0;
function frame(t) {
  terrainEdit.tick(t);
  controls.update();
  if (t - lastStream > 500) { lastStream = t; if (state.records.length) streamNearby(); }
  if (t - lastMinimap > 100) { lastMinimap = t; minimap.draw(gamePos(controls.target), gamePos(camera.position), live.player()); }
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
