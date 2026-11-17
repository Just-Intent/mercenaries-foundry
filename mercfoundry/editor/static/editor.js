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
import * as SkeletonUtils from 'three/addons/utils/SkeletonUtils.js';
import {createLayout} from './layout.js';
import {createEncounters} from './encounters.js';
import {createLive} from './live.js';
import {applyGround} from './ground.js';
import {createMinimap} from './minimap.js';
import {createTerrainEditor} from './terrain_edit.js';
import {createMissions} from './missions.js';
import {createSky} from './sky.js';
import {createAnimations} from './animations.js';
import {createFactions} from './factions.js';
import {createMapSettings} from './mapset.js';
import {createMods} from './mods.js';

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
window.foundryEditor = {gizmo, camera, scene, renderer, controls};     // for debugging from the browser console
// Several objects selected: the gizmo moves a pivot at their middle, and each
// object follows it (its transform relative to the pivot at the drag's start).
const groupPivot = new THREE.Object3D();
groupPivot.userData.groupPivot = true;
scene.add(groupPivot);
let dragStart = null;        // [[rec, snap, its matrix]] and the pivot's, for undo and group moves
gizmo.addEventListener('dragging-changed', (e) => {
  controls.enabled = !e.value;
  if (!e.value && gizmo.object && gizmo.object.userData.squadPivot) encs.dragEnded();
  // paths' and zones' points, squad members and spawn points: one undo step a drag
  if (gizmo.object && gizmo.object.userData.handle) { if (e.value) layout.dragStart(); else layout.dragEnd(); }
  if (gizmo.object && (gizmo.object.userData.pick || gizmo.object.userData.squadPivot)) {
    if (e.value) encs.dragStart(); else encs.dragStop();
  }
  const objs = gizmo.object && (gizmo.object === groupPivot || gizmo.object.userData.rec) ? selectionRecs() : [];
  if (e.value && objs.length) {
    groupPivot.updateMatrixWorld();
    dragStart = {recs: objs.map((r) => [r, snap(r), transformOf(r)]), pivot: groupPivot.matrixWorld.clone()};
  } else if (!e.value && dragStart) {
    rememberMoves(gizmo.getMode() === 'rotate' ? 'Turned' : 'Moved', dragStart.recs.map(([r, s]) => [r, s]));
    dragStart = null;
  }
});
gizmo.addEventListener('objectChange', () => {
  if (gizmo.object && gizmo.object.userData.handle) layout.handleMoved(gizmo.object);
  else if (gizmo.object && gizmo.object.userData.pick) encs.moved(gizmo.object);
  else if (gizmo.object && gizmo.object.userData.missionMarker) missions.markerMoved(gizmo.object);
  else if (gizmo.object === groupPivot && dragStart) {
    groupPivot.updateMatrixWorld();
    const delta = groupPivot.matrixWorld.clone().multiply(dragStart.pivot.clone().invert());
    for (const [rec, , m] of dragStart.recs) {
      const node = ensureNode(rec);
      delta.clone().multiply(m).decompose(node.position, node.quaternion, node.scale);
      rec.position = gamePos(node.position);
      rec.rotation = m3From(node.quaternion);
      recordEdit(rec);
    }
    showSelected();
    updateSelectionBoxes();
  }
  else if (state.selected) fromNode(state.selected);
});

function resize() {
  const w = view.clientWidth, h = view.clientHeight;
  renderer.setSize(w, h);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
new ResizeObserver(resize).observe(view);     // the header can wrap, so watch the view itself

// The side panel: drag its left edge to make it wider (kept for next time).
{
  const panel = document.getElementById('panel'), grip = document.getElementById('panel-grip');
  const setWidth = (w) => {
    w = Math.max(260, Math.min(Math.round(w), Math.round(window.innerWidth * 0.7)));
    panel.style.width = `${w}px`;
    return w;
  };
  try { const w = +localStorage.getItem('foundry.panelWidth'); if (w) setWidth(w); } catch (e) { /* no storage */ }
  grip.addEventListener('pointerdown', (e) => {
    e.preventDefault();
    grip.setPointerCapture(e.pointerId);
    const x0 = e.clientX, w0 = panel.getBoundingClientRect().width;
    const move = (ev) => setWidth(w0 + (x0 - ev.clientX));
    const up = () => {
      grip.removeEventListener('pointermove', move);
      try { localStorage.setItem('foundry.panelWidth', String(Math.round(panel.getBoundingClientRect().width))); } catch (err) { /* no storage */ }
    };
    grip.addEventListener('pointermove', move);
    grip.addEventListener('pointerup', up, {once: true});
  });
  grip.addEventListener('dblclick', () => {
    setWidth(290);
    try { localStorage.removeItem('foundry.panelWidth'); } catch (e) { /* no storage */ }
  });
}

// ------------------------------------------------------------------ state
const state = {
  map: null, terrain: null, records: [], byId: new Map(),
  moved: {}, copies: [], removed: new Set(), selected: null, selection: [], dirty: false, nodes: new Set(),
};
window.foundryEditor.state = state;     // (debugging: the selection, copies, moves)
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

// Every change this page sends names the mod it shows (the toolbar's "Saving
// to"): the server refuses one meant for another mod, as when another editor
// tab switched mods or the server was restarted on another one since.
let pageMod = null;
{
  const send = window.fetch.bind(window);
  window.fetch = (url, opts = {}) => {
    if (pageMod && opts && opts.method === 'POST' && String(url).startsWith('/api/')) {
      opts = {...opts, headers: {...(opts.headers || {}), 'X-Foundry-Mod': encodeURIComponent(pageMod)}};
    }
    return send(url, opts);
  };
}

// ------------------------------------------------------------------ status
function status(text, error = false) {
  $('status').textContent = text;
  $('status').classList.toggle('error', error);
}
// The Map list: the free-roam maps first, then the rest (a mod's new maps too).
async function fillMaps() {
  const info = await getJSON('/api/info');
  $('mod').textContent = info.mod;
  $('mod').title = `Everything you save goes into mods/${info.folder}: click to switch to another mod, make one, rename it or delete one`;
  modName = info.mod;
  pageMod = info.folder;
  const maps = info.maps.filter((m) => ['swn', 'sw', 'nw'].includes(m)).concat(
    info.maps.filter((m) => !['swn', 'sw', 'nw'].includes(m)));
  $('map').innerHTML = maps.map((m) => `<option>${m}</option>`).join('');
  return maps;
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
    // SkeletonUtils: a character's skinned body bound to the copy's own bones
    // (a plain clone keeps the original's, and the body doesn't draw)
    const body = SkeletonUtils.clone(model);
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
  if (!rec.node || ((rec === state.selected || state.selection.includes(rec)) && !force)) return;
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
  state.marks = new Map();
  state.selection = [];
  state.selected = null;
  updateSelectionBoxes();
  clearHistory();
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
  // placed copies of objects from other maps draw their model
  if (objs.edits.placed.some((p) => !/^template_/i.test(p.from))) await loadPlaceables();
  if (mine !== loadSerial) return;
  for (const p of objs.edits.placed) {
    addPlaced(p.from, p.name, p.file, p.position, p.rotation, false, p.set);
    state.placedFiles.add(p.file);
  }
  await loadMarks(name);
  if (mine !== loadSerial) return;
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
  else lookAt([0, heightAt(0, 0), 0], 400);         // an empty map (a new one): its middle
  const [items, encounters] = await Promise.all([layout.load(name), encs.load(name)]);
  missions.load(name).catch((err) => console.warn('missions:', err));
  sky.load(name).catch((err) => console.warn('sky:', err));
  mapset.load(name).catch((err) => console.warn('map settings:', err));
  if (mine !== loadSerial) return;
  minimap.setWater(waterAreas ? waterAreas() : []);
  refreshChanges();
  setDirty(false);
  // a mod's ground file the build refuses: the rest of the map still loads, and this says why
  const groundErrors = (objs.edits.terrain_errors || []).join('; ');
  const summary = `${state.records.length} objects, ${items} paths and zones, ${encounters} encounters` +
    (groundErrors ? `; GROUND CHANGES NOT ALL SHOWN (the build would stop on this too): ${groundErrors}` : '');
  status(`${summary}; reading other mods...`, !!groundErrors);
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
  setSelection(rec ? [rec] : []);
}

// The selection: one object, or several (Shift+click adds or takes one away,
// Shift+drag a box). state.selected is the last one picked.
function selectionRecs() {
  return state.selection.length ? state.selection : (state.selected ? [state.selected] : []);
}
function setSelection(recs) {
  recs = [...new Set(recs.filter((r) => r && !r.removed))];
  if (recs.length && layout.hasSelection()) layout.select(null);
  if (recs.length && encs.hasSelection()) encs.select(null);
  state.selection = recs;
  state.selected = recs.length ? recs[recs.length - 1] : null;
  gizmo.detach();
  $('selected').hidden = !recs.length;
  $('hint').hidden = !!recs.length;
  updateSelectionBoxes();
  if (!recs.length) return;
  for (const r of recs) ensureNode(r);
  const movable = recs.filter((r) => !r.deleted && !r.context);
  if (recs.length === 1 && movable.length) {
    gizmo.attach(state.selected.node);
    setMode(gizmo.getMode());
  } else if (movable.length > 1) {
    placePivot();
    gizmo.attach(groupPivot);
    setMode(gizmo.getMode());
  }
  showSelected();
}
function toggleSelected(rec) {
  const now = selectionRecs();
  setSelection(now.includes(rec) ? now.filter((r) => r !== rec) : [...now, rec]);
}
function placePivot() {
  const recs = selectionRecs().filter((r) => !r.deleted && !r.context);
  if (!recs.length) return;
  const c = new THREE.Vector3();
  for (const r of recs) c.add(threePos(r.position));
  groupPivot.position.copy(c.multiplyScalar(1 / recs.length));
  groupPivot.quaternion.identity();
  groupPivot.scale.set(1, 1, 1);
  groupPivot.updateMatrixWorld();
}
// Outlines round each object of a group selection.
const selectionBoxes = [];
function updateSelectionBoxes() {
  for (const b of selectionBoxes.splice(0)) { scene.remove(b); b.geometry.dispose(); }
  if (state.selection.length < 2) return;
  for (const r of state.selection) {
    if (!r.node) continue;
    const b = new THREE.BoxHelper(r.node, 0xffcc33);
    scene.add(b);
    selectionBoxes.push(b);
  }
}

function showSelected() {
  const rec = state.selected;
  if (!rec) return;
  const group = state.selection.length > 1;
  for (const id of ['copy-name-row', 'sel-props-box']) if ($(id)) $(id).hidden = group;
  if (group) {
    const recs = state.selection;
    $('sel-title').textContent = `${recs.length} objects selected`;
    $('sel-model').textContent = [...new Set(recs.map((r) => r.model))].slice(0, 4).join(', ') + (recs.length > 4 ? '...' : '');
    $('sel-layer').textContent = [...new Set(recs.map((r) => r.layer))].slice(0, 3).join(', ');
    const c = groupPivot.position;
    [$('px').value, $('py').value, $('pz').value] = [c.x, c.y, c.z].map((v) => v.toFixed(2));
    $('ph').value = '';
    $('sel-note').textContent = 'Shift+click adds or takes away one; move, turn, drop, copy or remove them together.';
    const any = (f) => recs.some(f);
    $('revert').disabled = !any((r) => r.copyName || state.moved[r.id] || r.deleted || state.marks.has(r.id));
    for (const id of ['mode-move', 'mode-turn', 'ground', 'tilt', 'upright', 'copy', 'delete']) $(id).disabled = false;
    for (const id of ['px', 'py', 'pz', 'ph']) $(id).disabled = true;
    if (any((r) => r.mark)) $('copy').disabled = $('delete').disabled = true;
    return;
  }
  $('sel-title').textContent = rec.copyName ? `${rec.copyName} (${rec.template ? 'placed from ' + rec.template : 'copy'})`
                                             : (rec.name || '(unnamed)');
  $('copy-name-row').hidden = !rec.copyName;
  if (rec.copyName && document.activeElement !== $('copy-name')) $('copy-name').value = rec.copyName;
  $('sel-model').textContent = rec.model;
  $('sel-layer').textContent = rec.layer;
  [$('px').value, $('py').value, $('pz').value] = rec.position.map((v) => v.toFixed(2));
  $('ph').value = heading(rec.rotation).toFixed(1);
  const edited = rec.copyName || state.moved[rec.id] || rec.deleted || state.marks.has(rec.id);
  $('sel-note').textContent = rec.mark ? `Saved in ${rec.layer}. Move or turn it here; add or remove it in the Map panel.`
    : rec.context ? `Added or moved by ${rec.by} (read only here: edit it in that mod).`
    : rec.deleted ? 'Removed (saved as a rule). Undo changes brings it back.'
    : rec.copyName ? `A copy of ${rec.name || rec.model}.`
    : edited ? 'Moved or turned (saved as a rule).' : '';
  $('revert').disabled = !edited || !!rec.context;
  for (const id of ['mode-move', 'mode-turn', 'ground', 'tilt', 'upright', 'copy', 'delete', 'px', 'py', 'pz', 'ph']) {
    $(id).disabled = !!rec.deleted || !!rec.context;
  }
  if (rec.mark) $('copy').disabled = $('delete').disabled = true;
  // a tree: whether bullets hit its branches (its model's collision.json "crown")
  const tree = /^tree/i.test(rec.type || '');
  $('sel-crown-row').hidden = !tree || !!rec.context;
  if (tree) $('sel-crown').checked = crowns.has((rec.geometry || rec.model || '').toLowerCase());
  $('delete').title = rec.name && !rec.copyName
    ? `Delete. Scripts may use "${rec.name}" by name; removing it can break them.` : 'Delete';
  if ($('sel-props-box').open) showProperties(rec);
}

// ------------------------------------------------------------------ properties
// The selected object's properties (the game's, or the source's for a copy).
// A copy's or placed object's can be changed: the changes are its "set"
// (saved with it); the game's own objects are read only here.
const propCache = new Map();
async function showProperties(rec) {
  const copy = rec.copyName ? state.copies.find((c) => c.rec === rec) : null;
  const ref = copy ? (copy.template ? `from:${copy.template}` : `id:${copy.source}`) : `id:${rec.id}`;
  if (!propCache.has(ref)) {
    propCache.set(ref, fetch(`/api/properties/${encodeURIComponent(ref)}`).then(async (r) => {
      const doc = await r.json();
      if (!r.ok) throw new Error(doc.error || r.statusText);
      return doc.properties;
    }));
  }
  let props;
  try { props = await propCache.get(ref); } catch (e) {
    $('sel-props').innerHTML = '';
    $('sel-props-note').textContent = `No properties: ${e.message}`;
    return;
  }
  if (state.selected !== rec) return;
  const set = (copy && copy.set) || {};
  const value = (k) => { const hit = Object.keys(set).find((x) => x.toLowerCase() === k.toLowerCase()); return hit ? set[hit] : null; };
  const rows = props.map(([k, v]) => [k, value(k) ?? v, value(k) !== null]);
  for (const k of Object.keys(set)) if (!props.some(([x]) => x.toLowerCase() === k.toLowerCase())) rows.push([k, set[k], true]);
  const editable = !!copy && !rec.context;
  $('sel-props-note').textContent = editable
    ? 'Change a value to give this copy its own (saved with it). Changed ones are highlighted.'
    : 'The game\'s own object: read only here. Copy it (C) or place one to change its properties.';
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  $('sel-props').innerHTML = rows.map(([k, v, changed]) => `<label class="sky-row${changed ? ' sky-changed' : ''}"><span title="${esc(k)}">${esc(k)}</span>`
    + `<input type="text" data-prop="${esc(k)}" value="${esc(v)}" ${editable ? '' : 'readonly'}></label>`).join('');
  // a new object's own ruin (it has a model of its own and its source has a ruin)
  const ruin = props.find(([k]) => k.toLowerCase() === 'geometrynameruined');
  $('sel-ruin-row').hidden = !(editable && ruin && ruin[1] && value('GeometryFile'));
}
$('sel-props-box').addEventListener('toggle', () => { if ($('sel-props-box').open && state.selected) showProperties(state.selected); });
$('sel-props').addEventListener('change', (e) => {
  const k = e.target.dataset.prop, rec = state.selected;
  if (!k || !rec || !rec.copyName) return;
  const copy = state.copies.find((c) => c.rec === rec);
  if (!copy) return;
  copy.set = copy.set || {};
  const hit = Object.keys(copy.set).find((x) => x.toLowerCase() === k.toLowerCase()) || k;
  copy.set[hit] = e.target.value.trim();
  if (hit.toLowerCase() === 'geometryfile') {           // it draws the model it names
    rec.model = rec.geometry = copy.set[hit];
    if (rec.node) { if (gizmo.object === rec.node) gizmo.detach(); scene.remove(rec.node); rec.node = null; }
    gizmo.attach(ensureNode(rec));
  }
  e.target.closest('.sky-row').classList.add('sky-changed');
  setDirty(true);
  refreshChanges();
  if (hit.toLowerCase() === 'geometryfile') showSelected();      // its model line, and the ruin button
});
// Ruin model: the new object's ruin, built on its source's ruin model.
$('sel-ruin').onclick = () => { $('sel-ruin-file').value = ''; $('sel-ruin-file').click(); };
$('sel-ruin-file').onchange = async () => {
  const file = $('sel-ruin-file').files[0], rec = state.selected;
  const copy = rec && state.copies.find((c) => c.rec === rec);
  if (!file || !copy) return;
  const props = await propCache.get(copy.template ? `from:${copy.template}` : `id:${copy.source}`);
  const base = (props.find(([k]) => k.toLowerCase() === 'geometrynameruined') || [])[1];
  const own = Object.entries(copy.set || {}).find(([k]) => k.toLowerCase() === 'geometryfile');
  const name = (prompt('Name of the ruin model', `${own ? own[1] : 'object'}_ruin`) || '').trim();
  if (!name || !base) return;
  const bytes = new Uint8Array(await file.arrayBuffer());
  let bin = '';
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  try {
    const r = await fetch('/api/models/new', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name, base, glb: btoa(bin)})});
    const doc = await r.json();
    if (!r.ok) throw new Error(doc.error || r.statusText);
    copy.set = copy.set || {};
    copy.set.geometrynameruined = name;
    setDirty(true);
    showProperties(rec);
    status(`Ruin model ${name} saved (${doc.files.join(', ')}), built on ${base}: Save to keep it on this object`);
  } catch (e) {
    status(`Couldn't make the ruin: ${e.message}`, true);
  }
};

function deleteSelected() {
  const recs = selectionRecs().filter((r) => !r.deleted);
  if (!recs.length) return;
  if (recs.some((r) => r.mark)) return status('Remove pilots and arrival points in the Map panel', true);
  const named = recs.filter((r) => r.name && !r.copyName);
  if (named.length && !confirm(named.length === 1
    ? `"${named[0].name}" has a name, so scripts may use it. Remove it anyway?`
    : `${named.length} of these have names, so scripts may use them. Remove them anyway?`)) return;
  beginBatch('Removed');
  for (const rec of recs) {
    if (rec.copyName) {                     // a copy just goes
      const entry = removeCopy(rec);
      remember(`Removed ${entry.as}`, () => reviveCopy(rec, entry), () => removeCopy(rec));
    } else {
      const before = snap(rec);
      removeGameObject(rec);
      remember(`Removed ${recName(rec)}`, () => restoreGameObject(rec, before), () => removeGameObject(rec));
    }
  }
  endBatch();
  setSelection([]);
  setDirty(true);
  refreshChanges();
}

// ------------------------------------------------------------------ undo / redo
// Each edit of objects (move, turn, drop, copy, place, remove, undo changes,
// rename, properties) goes on a history of undo and redo steps, Ctrl+Z and
// Ctrl+Y; a group edit is one step. Switching maps clears it.
const history = {past: [], future: []};
let historyMuted = 0, batch = null;
function remember(label, undo, redo) {
  if (historyMuted) return;
  if (batch) { batch.items.push({label, undo, redo}); return; }
  history.past.push({label, undo, redo});
  if (history.past.length > 300) history.past.shift();
  history.future = [];
  showHistory();
}
// Edits between begin and end are one step.
function beginBatch(label) { batch = {label, items: []}; }
function endBatch() {
  const b = batch;
  batch = null;
  if (!b || !b.items.length) return;
  if (b.items.length === 1) return remember(b.items[0].label, b.items[0].undo, b.items[0].redo);
  remember(`${b.label} (${b.items.length})`,
           () => [...b.items].reverse().forEach((i) => i.undo()), () => b.items.forEach((i) => i.redo()));
}
function showHistory() {
  const last = (a) => a[a.length - 1];
  $('undo').disabled = !history.past.length;
  $('redo').disabled = !history.future.length;
  $('undo').title = history.past.length ? `Undo: ${last(history.past).label} (Ctrl+Z)` : 'Nothing to undo (Ctrl+Z)';
  $('redo').title = history.future.length ? `Redo: ${last(history.future).label} (Ctrl+Y)` : 'Nothing to redo (Ctrl+Y)';
}
function stepHistory(redo) {
  missions.flushUndo();             // edits still waiting to be a step
  sky.flushUndo();
  animations.flushUndo();
  factions.flushUndo();
  const from = redo ? history.future : history.past, to = redo ? history.past : history.future;
  const h = from.pop();
  if (!h) return status(redo ? 'Nothing to redo' : 'Nothing to undo');
  historyMuted++;
  try { redo ? h.redo() : h.undo(); } finally { historyMuted--; }
  to.push(h);
  setSelection(selectionRecs().filter((r) => !r.removed));
  if (state.selection.length > 1) placePivot();
  setDirty(true);
  refreshChanges();
  showHistory();
  status(`${redo ? 'Redid' : 'Undid'}: ${h.label}`);
}
function clearHistory() { history.past = []; history.future = []; showHistory(); }
Object.assign(window.foundryEditor, {setSelection, stepHistory, history});   // (debugging)

const snap = (rec) => ({position: [...rec.position], rotation: [...rec.rotation]});
const sameSnap = (a, b) => !!a && !!b && a.position.every((v, i) => Math.abs(v - b.position[i]) < 1e-4) &&
  a.rotation.every((v, i) => Math.abs(v - b.rotation[i]) < 1e-6);
const recName = (r) => r.copyName || r.name || r.model;
// Put an object where a snapshot has it (a game object back at its own place
// is no longer "moved").
function putTransform(rec, t) {
  rec.position = [...t.position];
  rec.rotation = [...t.rotation];
  if (rec.node) transformOf(rec).decompose(rec.node.position, rec.node.quaternion, rec.node.scale);
  placeBox(rec);
  if (!rec.copyName && !rec.mark && sameSnap(t, rec.original)) {
    delete state.moved[rec.id];
    live.objectMoved(rec);
  } else if (rec.mark && sameSnap(t, rec.original)) {
    state.marks.delete(rec.id);
  } else {
    recordEdit(rec);
  }
}
// before: [[rec, snapshot]]; the ones that changed are one step.
function rememberMoves(label, before) {
  const changes = before.map(([r, b]) => [r, b, snap(r)]).filter(([, b, a]) => !sameSnap(b, a));
  if (!changes.length) return;
  remember(changes.length > 1 ? `${label} ${changes.length} objects` : `${label} ${recName(changes[0][0])}`,
           () => changes.forEach(([r, b]) => putTransform(r, b)),
           () => changes.forEach(([r, , a]) => putTransform(r, a)));
}
// A copy or placed object taken away (kept, to bring back) and brought back.
function removeCopy(rec) {
  const entry = state.copies.find((x) => x.rec === rec);
  live.objectRemoved(rec);
  state.copies = state.copies.filter((x) => x.rec !== rec);
  if (state.selected === rec || state.selection.includes(rec)) setSelection(selectionRecs().filter((r) => r !== rec));
  if (rec.node) { scene.remove(rec.node); rec.node = null; state.nodes.delete(rec); }
  state.byId.delete(rec.id);
  rec.removed = true;     // keeps its slot in the box batch, hidden
  if (rec.slot < boxes.instanceMatrix.count) {
    boxes.setMatrixAt(rec.slot, new THREE.Matrix4().makeScale(0, 0, 0));
    boxes.instanceMatrix.needsUpdate = true;
  }
  return entry;
}
function reviveCopy(rec, entry) {
  rec.removed = false;
  if (!state.copies.includes(entry)) state.copies.push(entry);
  state.byId.set(rec.id, rec);
  if (entry.file) state.placedFiles.add(entry.file);
  placeBox(rec);
  if (entry.template) live.spawn(entry.template, entry.as, rec.position, heading(rec.rotation));
}
function rememberAdded(rec) {
  const entry = state.copies.find((x) => x.rec === rec);
  if (!entry) return;
  remember(`${entry.template ? 'Placed' : 'Copied'} ${entry.as}`, () => removeCopy(rec), () => reviveCopy(rec, entry));
}
// A game object removed (saved as a rule), and brought back.
function removeGameObject(rec) {
  delete state.moved[rec.id];
  rec.position = rec.original.position; rec.rotation = rec.original.rotation;
  rec.deleted = true;
  state.removed.add(rec.id);
  live.objectRemoved(rec);
  if (state.selected === rec || state.selection.includes(rec)) setSelection(selectionRecs().filter((r) => r !== rec));
  dropNode(rec, true);
  placeBox(rec);
}
function restoreGameObject(rec, before) {
  rec.deleted = false;
  state.removed.delete(rec.id);
  dropNode(rec, true);         // the ghost goes; streaming brings the object back
  placeBox(rec);
  if (before && !sameSnap(before, rec.original)) putTransform(rec, before);
}

function fromNode(rec) {
  rec.position = gamePos(rec.node.position);
  rec.rotation = m3From(rec.node.quaternion);
  recordEdit(rec);
  showSelected();
}

function recordEdit(rec) {
  if (rec.mark) {
    state.marks.set(rec.id, rec);
    setDirty(true);
    refreshChanges();
    return;
  }
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

// Undo changes: a copy goes, a removed object comes back, a moved one goes
// back to its place (each on the history).
function revert(rec) {
  if (rec.copyName) {
    const entry = removeCopy(rec);
    remember(`Took away ${entry.as}`, () => reviveCopy(rec, entry), () => removeCopy(rec));
  } else if (rec.deleted) {
    if (live.isOn()) status('Restored here; the game brings it back when it next starts with this mod.');
    restoreGameObject(rec, null);
    remember(`Brought back ${recName(rec)}`, () => removeGameObject(rec), () => restoreGameObject(rec, null));
    if (state.selection.length <= 1) { ensureNode(rec); select(rec); }
  } else {
    const before = snap(rec);
    putTransform(rec, rec.original);
    state.marks.delete(rec.id);
    rememberMoves('Put back', [[rec, before]]);
    showSelected();
  }
  setDirty(true);
  refreshChanges();
}
function revertSelected() {
  const recs = selectionRecs();
  beginBatch('Undid changes');
  for (const r of recs) revert(r);
  endBatch();
  if (state.selection.length > 1) placePivot();
}

// Map marks: what the mod's maps/*.json put on this map (travel pilots, a new
// map's pilot back and its arrival point), drawn and moved like objects and
// saved back to their map file (not copied or removed here: the Map panel).
async function loadMarks(name) {
  let marks = [];
  try { marks = await getJSON(`/api/marks/${name}`); } catch (e) { console.warn('marks:', e); }
  if (state.map !== name) return;
  for (const m of marks) {
    const rec = {
      id: `mark:${m.kind}:${m.to}:${m.index}`, layer: `maps/${m.to}.json`, name: m.label, model: m.model,
      geometry: m.model, type: 'human', position: m.position, rotation: m.rotation, mark: m,
      original: {position: m.position, rotation: m.rotation},
    };
    addRecord(rec);
  }
  boxes.count = state.records.length;
}
// Marks again after the Map panel adds, removes or moves them (unsaved moves of marks are dropped).
async function reloadMarks() {
  for (const rec of state.records.filter((r) => r.mark && !r.removed)) {
    if (state.selected === rec) select(null);
    if (rec.node) { scene.remove(rec.node); rec.node = null; state.nodes.delete(rec); }
    state.byId.delete(rec.id);
    rec.removed = true;     // keeps its slot in the box batch, hidden
    boxes.setMatrixAt(rec.slot, new THREE.Matrix4().makeScale(0, 0, 0));
    boxes.instanceMatrix.needsUpdate = true;
  }
  state.marks.clear();
  await loadMarks(state.map);
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
    rememberAdded(rec);
  }
  return rec;
}

// What a placed object draws: its template's model, or (for "from" a named
// object or "model:<name>") that object's.
function placedLook(from) {
  const low = from.toLowerCase();
  const t = (state.templates || encs.templates() || []).find((x) => x.name.toLowerCase() === low);
  if (t) return {model: t.model, geometry: t.geometry || t.model, type: t.type};
  const p = placeables && placeables.get(low);      // an object or model from any map
  if (p) return {model: p.model, geometry: p.geometry || p.model, type: p.type};
  if (low.startsWith('model:')) return {model: from.slice(6), geometry: from.slice(6), type: ''};
  const r = state.records.find((x) => !x.removed && (x.name || '').toLowerCase() === low);
  return r ? {model: r.model, geometry: r.geometry, type: r.type} : {model: from, geometry: from, type: ''};
}

let placedSerial = 1;
function addPlaced(from, name, file, position, rotation, select_ = true, set = null) {
  const look = placedLook(from);
  // a copy given another model (a new object's) draws that
  const geo = set && Object.entries(set).find(([k]) => k.toLowerCase() === 'geometryfile');
  if (geo && geo[1]) { look.model = geo[1]; look.geometry = geo[1]; }
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
    rememberAdded(rec);
  }
  return rec;
}

// New object: the selected object's properties (and the skeleton of its
// model) with your own model, a .glb. The server writes models/<name>.glb
// and .new.json (the build makes the model); a copy that uses it is placed
// beside the selected one, to move where it belongs and save.
$('new-object').onclick = () => {
  if (!state.selected) return status('Select an object first: the new one starts from it', true);
  $('new-object-file').value = '';
  $('new-object-file').click();
};
$('new-object-file').onchange = async () => {
  const file = $('new-object-file').files[0], src = state.selected;
  if (!file || !src) return;
  const guess = file.name.replace(/\.glb$/i, '').toLowerCase().replace(/[^a-z0-9_-]+/g, '_');
  const name = (prompt('Name of the new model (letters, digits, _ and -)', guess) || '').trim();
  if (!name) return;
  const bytes = new Uint8Array(await file.arrayBuffer());
  let bin = '';
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  const base = src.geometry || src.model;
  try {
    const r = await fetch('/api/models/new', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name, base, glb: btoa(bin)})});
    const doc = await r.json();
    if (!r.ok) throw new Error(doc.error || r.statusText);
    modelCache.delete(name);
    const from = src.template || src.name || `model:${src.model}`;
    const position = [src.position[0] + 3, src.position[1], src.position[2]];
    const placed = addPlaced(from, freshPlacedName(name), state.layerFile, position, src.rotation, true, {GeometryFile: name});
    status(`New model ${name} saved (${doc.files.join(', ')}); placed ${placed.copyName} beside the original: move it and Save`);
  } catch (e) {
    status(`Couldn't make the new object: ${e.message}`, true);
  }
};

function freshPlacedName(from) {
  const base = from.replace(/^template_/i, '').replace(/^model:/i, '') || 'object';
  const taken = takenNames();
  let k = 1;
  while (taken.has(`${base}_${k}`.toLowerCase())) k++;
  return `${base}_${k}`;
}

// Everything Place can copy, from any map (templates, named objects, models
// as model:<name>), fetched once: the Place list and what each draws.
let placeables = null, placeablesList = [];
// Templates the running game has loaded where the player is (the live link
// asks): those spawn live as they're placed, so they're listed first.
let liveLoaded = new Set();
function fillPlaceables() {
  const label = (p) => (liveLoaded.has(p.name.toLowerCase()) ? 'spawns live now, ' : '') +
    (p.kind === 'template' ? `${p.type || 'template'} (${p.model})`
      : p.kind === 'object' ? `${p.type || 'object'} on ${p.layer} (${p.model})` : `model, as on ${p.layer}`);
  const list = [...placeablesList].sort((a, b) => liveLoaded.has(b.name.toLowerCase()) - liveLoaded.has(a.name.toLowerCase()));
  $('placeables').innerHTML = list.map((p) => `<option value="${p.name}">${label(p)}</option>`).join('');
}
function setLiveLoaded(names) {
  liveLoaded = new Set((names || []).map((n) => n.toLowerCase()));
  if (placeables) fillPlaceables();
}
async function loadPlaceables() {
  if (placeables) return placeables;
  placeablesList = await getJSON('/api/placeables');
  placeables = new Map(placeablesList.map((p) => [p.name.toLowerCase(), p]));
  fillPlaceables();
  return placeables;
}
$('place-template').addEventListener('focus', () => {
  loadPlaceables().catch((e) => status(e.message, true));
  live.askLoaded();          // what the running game can spawn here (listed first when it answers)
});

async function startPlace() {
  const from = $('place-template').value.trim();
  if (!from) return status('Type or pick something to place first: a template, a named object, or model:<model>', true);
  const low = from.toLowerCase();
  try { await loadPlaceables(); } catch (e) { return status(e.message, true); }
  const known = placeables.has(low) ||
    state.records.some((r) => !r.removed && (r.name || '').toLowerCase() === low);
  if (!known) return status(`Nothing called ${from} on any map (a template, a named object, or model:<model>)`, true);
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
  const live_ = live.isOn() && live.loaded();
  status(live_ && !liveLoaded.has(from.toLowerCase())
    ? `Placed ${from}; the game hasn't it loaded here, so it shows in game after Save and the next game start (rename it in the panel if you like).`
    : `Placed ${from}; rename it in the panel if you like.`);
}

function copySelected() {
  const recs = selectionRecs();
  if (recs.length > 1) {
    if (recs.some((r) => r.mark)) return status('Add pilots in the Map panel', true);
    beginBatch('Copied');
    const made = [];
    for (const r of recs) { copyOne(r); if (state.selected && state.selected !== r) made.push(state.selected); }
    endBatch();
    setSelection(made);
    return;
  }
  copyOne(state.selected);
}
function copyOne(rec) {
  if (!rec) return;
  if (rec.mark) return status('Add pilots in the Map panel', true);
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
  const old = rec.copyName;
  const setName = (n) => {
    rec.copyName = n;
    state.copies.find((c) => c.rec === rec).as = n;
    if (state.selected === rec) showSelected();
  };
  setName(name);
  remember(`Renamed ${old} to ${name}`, () => setName(old), () => setName(name));
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
  for (const rec of (state.marks || new Map()).values()) items.push([rec, `Moved ${rec.name}`]);
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
      .map((c) => ({source: c.source, as: c.as, position: c.position, rotation: c.rotation,
                    ...(c.set && Object.keys(c.set).length ? {set: c.set} : {})})),
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
    if (state.marks.size) {
      const done = await post('/api/marks/save', {marks: [...state.marks.values()].map((r) => ({
        kind: r.mark.kind, to: r.mark.to, index: r.mark.index, position: r.position, rotation: r.rotation}))});
      for (const r of state.marks.values()) r.original = {position: r.position, rotation: r.rotation};
      state.marks.clear();
      mapset.load(state.map).catch((err) => console.warn('map settings:', err));
      note += `; pilots and arrival points in ${done.files.join(', ')}`;
    }
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
    const fxBody = factions.saveBody();
    if (fxBody) {
      const done = await post('/api/factions/save', fxBody);
      factions.saved();
      note += done.empty ? '; factions back to the game\'s' : `; faction names in ${done.file}`;
    }
    const skyBody = sky.saveBody();
    if (skyBody) {
      const done = await post('/api/sky/save', skyBody);
      sky.saved();
      note += done.empty ? '; sky back to the game\'s' : `; sky and weather in ${done.file}`;
    }
    for (const body of animations.saveBodies()) {
      const done = await post('/api/animtable/save', body);
      animations.saved(body.name, done.file);
      note += done.file ? `; animation table ${body.name} in ${done.file}` : `; animation table ${body.name} back to the game's`;
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
  for (const id of ['mode-move', 'enc-mode-move']) $(id).classList.toggle('on', !turn);
  for (const id of ['mode-turn', 'enc-mode-turn']) $(id).classList.toggle('on', turn);
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
// Shift+drag on the view: a box; the objects in it join the selection. (In
// the capture phase, so the camera controls don't take the drag first.)
const boxEl = document.createElement('div');
boxEl.id = 'select-box';
boxEl.hidden = true;
view.appendChild(boxEl);
let boxFrom = null;
view.addEventListener('pointerdown', (e) => {
  if (!e.shiftKey || e.button !== 0 || terrainEdit.isOn() || gizmo.axis || state.placing) return;
  const r = renderer.domElement.getBoundingClientRect();
  boxFrom = [e.clientX - r.left, e.clientY - r.top];
  controls.enabled = false;
}, true);
window.addEventListener('pointermove', (e) => {
  if (!boxFrom) return;
  const r = renderer.domElement.getBoundingClientRect();
  const x = e.clientX - r.left, y = e.clientY - r.top;
  if (Math.hypot(x - boxFrom[0], y - boxFrom[1]) < 5) return;
  Object.assign(boxEl.style, {left: `${Math.min(x, boxFrom[0])}px`, top: `${Math.min(y, boxFrom[1])}px`,
                              width: `${Math.abs(x - boxFrom[0])}px`, height: `${Math.abs(y - boxFrom[1])}px`});
  boxEl.hidden = false;
});
window.addEventListener('pointerup', (e) => {
  if (!boxFrom) return;
  const from = boxFrom;
  boxFrom = null;
  controls.enabled = true;
  if (boxEl.hidden) return;              // a Shift+click: the canvas's click handles it
  boxEl.hidden = true;
  const r = renderer.domElement.getBoundingClientRect();
  const x0 = Math.min(from[0], e.clientX - r.left), x1 = Math.max(from[0], e.clientX - r.left);
  const y0 = Math.min(from[1], e.clientY - r.top), y1 = Math.max(from[1], e.clientY - r.top);
  const v = new THREE.Vector3(), cam = camera.position;
  const hits = state.records.filter((rec) => {
    if (rec.removed || rec.context || rec.hiddenBy) return false;
    v.copy(threePos(rec.position));
    if (v.distanceTo(cam) > 1500) return false;
    v.project(camera);
    if (v.z > 1) return false;
    const sx = (v.x + 1) / 2 * r.width, sy = (1 - v.y) / 2 * r.height;
    return sx >= x0 && sx <= x1 && sy >= y0 && sy <= y1;
  });
  if (hits.length > 200) return status(`${hits.length} objects in that box: draw a smaller one (200 at most)`, true);
  setSelection([...selectionRecs(), ...hits]);
  status(hits.length ? `${state.selection.length} objects selected` : 'No objects in that box');
});

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
  // The mission's start arrow and giver win: they're small and usually stand
  // in front of a building's bounding box.
  const marker = missions.pick(ray);
  if (marker) return marker.apply();
  const candidates = [layout.pick(ray), encs.pick(ray)].filter(Boolean);
  if (objectHit && e.shiftKey) return toggleSelected(objectHit.rec);
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
  const recs = selectionRecs().filter((r) => !r.deleted && !r.context);
  if (!recs.length) return;
  const before = recs.map((r) => [r, snap(r)]);
  for (const rec of recs) {
    const node = ensureNode(rec);
    node.position.y = heightAt(rec.position[0], rec.position[2]);
    rec.position = gamePos(node.position);
    recordEdit(rec);
  }
  rememberMoves('Dropped to ground', before);
  if (state.selection.length > 1) { placePivot(); updateSelectionBoxes(); }
  showSelected();
};
// Tilt: each selected object leans so its up axis is the ground's normal
// where it stands (from the heights 2 m around it), keeping its heading;
// Upright stands it straight again. Saved as the whole rotation; the live
// link moves the game's copy upright (it sends headings only).
function groundNormal(x, z) {
  const d = 2;
  const n = new THREE.Vector3(heightAt(x - d, z) - heightAt(x + d, z), 2 * d, heightAt(x, z - d) - heightAt(x, z + d));
  return n.normalize();
}
function leanTo(normalOf, label) {
  const recs = selectionRecs().filter((r) => !r.deleted && !r.context);
  if (!recs.length) return;
  const before = recs.map((r) => [r, snap(r)]);
  for (const rec of recs) {
    const node = ensureNode(rec);
    const up = new THREE.Vector3(0, 1, 0).applyQuaternion(node.quaternion);
    const turn = new THREE.Quaternion().setFromUnitVectors(up, normalOf(rec));
    node.quaternion.premultiply(turn);
    rec.position = gamePos(node.position);
    rec.rotation = m3From(node.quaternion);
    recordEdit(rec);
  }
  rememberMoves(label, before);
  if (state.selection.length > 1) { placePivot(); updateSelectionBoxes(); }
  showSelected();
}
$('tilt').onclick = () => leanTo((r) => groundNormal(r.position[0], r.position[2]), 'Tilted');
$('upright').onclick = () => leanTo(() => new THREE.Vector3(0, 1, 0), 'Stood upright');
// Trees whose branches stop bullets (the mod's models/<model>.collision.json "crown").
const crowns = new Set();
getJSON('/api/crowns').then((list) => { for (const m of list) crowns.add(m.toLowerCase()); }).catch(() => {});
$('sel-crown').addEventListener('change', async () => {
  const rec = state.selected;
  if (!rec) return;
  const model = rec.geometry || rec.model, on = $('sel-crown').checked;
  try {
    const r = await fetch('/api/crown', {method: 'POST', headers: {'Content-Type': 'application/json'},
                                         body: JSON.stringify({model, on})});
    const doc = await r.json();
    if (!r.ok) throw new Error(doc.error || r.statusText);
    if (on) crowns.add(model.toLowerCase()); else crowns.delete(model.toLowerCase());
    status(on ? `Bullets hit the branches of every ${model} after the next game start (${doc.file})`
              : `${model}'s branches let bullets through again (${doc.file})`);
  } catch (e) { $('sel-crown').checked = !on; status(e.message, true); }
});
$('copy').onclick = copySelected;
$('place').onclick = startPlace;
$('delete').onclick = deleteSelected;
$('copy-name').addEventListener('change', () => state.selected && renameCopy(state.selected, $('copy-name').value));
$('revert').onclick = () => revertSelected();
$('undo').onclick = () => stepHistory(false);
$('redo').onclick = () => stepHistory(true);
$('save').onclick = save;
for (const id of ['px', 'py', 'pz']) {
  $(id).addEventListener('change', () => {
    const rec = state.selected;
    if (!rec) return;
    const p = [$('px').value, $('py').value, $('pz').value].map((v) => (v.trim() === '' ? NaN : +v));
    if (p.some((v) => !Number.isFinite(v))) return showSelected();    // ignore a blank or bad entry
    const before = [[rec, snap(rec)]];
    rec.node.position.copy(threePos(p));
    fromNode(rec);
    rememberMoves('Moved', before);
  });
}
$('ph').addEventListener('change', () => {
  const rec = state.selected;
  if (!rec) return;
  if ($('ph').value.trim() === '' || !Number.isFinite(+$('ph').value)) return showSelected();
  const delta = +$('ph').value - heading(rec.rotation);
  const before = [[rec, snap(rec)]];
  // A game turn of t degrees is a three.js turn of -t about the vertical.
  rec.node.rotateOnWorldAxis(new THREE.Vector3(0, 1, 0), THREE.MathUtils.degToRad(-delta));
  fromNode(rec);
  rememberMoves('Turned', before);
});
// Search suggestions: objects, zones and paths, encounters and squads.
function fillNames() {
  const names = [...new Set([...state.records.map((r) => r.copyName || r.name).filter(Boolean),
                             ...layout.names(), ...encs.names()])].sort();
  $('names').innerHTML = names.map((n) => `<option value="${n}">`).join('');
}
$('search').addEventListener('focus', fillNames);
// Found: give the keys back to the view (W / E, Delete...), which ignores typing in boxes.
$('search').addEventListener('change', () => { if (showByName($('search').value)) $('search').blur(); });
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
  else if ((k === 'y' || (k === 'z' && e.shiftKey)) && (e.ctrlKey || e.metaKey)) { e.preventDefault(); stepHistory(true); }
  else if (k === 'z' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); stepHistory(false); }
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
  reloadGround: () => applyGround(terrainMesh, state.map, scene.fog, {...state.terrain.meta, heights: state.terrain.heights}),
  // a stroke's heights to the running game, when it's on this map (Foundry Console's "heights")
  liveHeights: ({samples, check}) => live.heights(samples, check),
  livePaint: ({patches, check}) => live.paint(patches, check),
});
// Missions: contracts, their objectives and their layer's squads and zones.
const missions = createMissions({
  $, status, setDirty: (v) => setDirty(v), show: (name) => showByName(name),
  scene, threePos: (p) => threePos(p), lookAt: (p) => lookAt(p, 30),
  viewCentre: () => { const g = gamePos(controls.target); g[1] = round(heightAt(g[0], g[2])); return g; },
  newSquadIn: (file) => encs.startNewSquad(file),
  remember: (...a) => remember(...a),
  skyPresets: () => sky.presetNames(),
  drawZoneIn: (file) => layout.startDraw('boundary', file),
  gamePos: (v) => gamePos(v), groundAt: (x, z) => heightAt(x, z),
  // The start marker takes the gizmo (move along the ground, or turn).
  grab: (node) => { select(null); layout.select(null); encs.select(null); gizmo.attach(node); setMode(gizmo.getMode()); },
  release: (node) => { if (gizmo.object === node) gizmo.detach(); },
  // The selected object (for destroy targets and deliver vehicles): its name in
  // the game (a copy's or placed object's own), or null if it has none.
  selectedObject: () => {
    const r = state.selected;
    return r ? {name: r.copyName || r.name || null, kind: r.type || '', model: r.geometry || r.model || ''} : null;
  },
});
// Sky and weather: the map's base sky and atmosphere presets (sky/<map>.json).
const sky = createSky({$, status, setDirty: (v) => setDirty(v), remember: (...a) => remember(...a)});
// Animations: a character's animation table (animtables/<table>.changes.json), with a 3D preview.
const animations = createAnimations({$, status, setDirty: (v) => setDirty(v), remember: (...a) => remember(...a)});
const factions = createFactions({$, status, setDirty: (v) => setDirty(v), remember: (...a) => remember(...a)});
// Mods: the one saved to (the toolbar button), switching, making, editing, deleting.
createMods({
  $, status,
  post: async (url, body) => {
    const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const doc = await r.json();
    if (!r.ok) throw new Error(doc.error || r.statusText);
    return doc;
  },
  isDirty: () => state.dirty,
  // after a switch (the map again, from the new mod), or just its new name
  afterSwitch: async (nameOnly) => {
    const maps = await fillMaps();
    const want = maps.includes(state.map) ? state.map : (maps.includes('swn') ? 'swn' : maps[0]);
    $('map').value = want;
    if (nameOnly) return;
    setDirty(false);
    await loadMap(want);
  },
});
// New maps: make one, and a new map's arrival point, message and layers.
const mapset = createMapSettings({
  $, status,
  post: async (url, body) => {
    const r = await fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    const doc = await r.json();
    if (!r.ok) throw new Error(doc.error || r.statusText);
    return doc;
  },
  viewCentre: () => { const g = gamePos(controls.target); g[1] = round(heightAt(g[0], g[2])); return g; },
  reloadMarks: () => reloadMarks(),
  switchToMap: async (name) => {
    if (state.dirty && !confirm('Discard unsaved changes?')) return;
    await fillMaps();
    $('map').value = name;
    await loadMap(name);
  },
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
  skyPresets: () => sky.presetNames(),
  remember: (...a) => remember(...a),
});
const encs = createEncounters({
  scene, gizmo, heightAt, threePos, gamePos, status, $, setDirty, camera, slug,
  live: () => live,
  deselectOthers: () => { select(null); layout.select(null); },
  setMode: (mode) => setMode(mode),
  modName: () => modName,
  terrain: () => terrainMesh,
  layoutFiles: () => layout.files(),
  layerFile: () => state.layerFile,
  objectNames: () => new Set(state.records.filter((r) => !r.removed)
    .map((r) => (r.copyName || r.name || '').toLowerCase()).filter(Boolean)),
  remember: (...a) => remember(...a),
});
Object.assign(window.foundryEditor, {layout, encs, missions});     // (debugging)
// A line of Lua to the running game (its reply shows under Game replies).
$('live-lua').addEventListener('keydown', (e) => {
  if (e.key !== 'Enter') return;
  const line = $('live-lua').value.trim();
  if (!line) return;
  if (!live.lua(line)) return status('Turn the live link on with the game running first', true);
  $('game-log-title').hidden = false;
  status(`Sent to the game: ${line}`);
  $('live-lua').select();
});
const live = createLive({
  scene, threePos, heightAt, status, $, lookAt, heading,
  onLoaded: (names) => { encs.setLoaded(names); setLiveLoaded(names); },
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
    const maps = await fillMaps();
    $('map').value = maps.includes('swn') ? 'swn' : maps[0];
    await loadMap($('map').value);
  } catch (err) {
    status(err.message, true);
  }
}
start();
