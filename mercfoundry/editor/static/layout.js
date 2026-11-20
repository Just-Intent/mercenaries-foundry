// Paths and zones in the Foundry editor.
//
// Game items (from the game's own layers) can have their points moved; the
// point count stays, because a path's orientations, pauses and links are
// indexed by point. The mod's own items (its encounters/*.json layers) can
// also gain and lose points, be created and be deleted. So can the zones the
// mod adds to the map's own record (source "map"): the game makes water only
// from those (layout/<map>.lineregion.changes.json on a game map,
// maps/<map>/boundaries.json on a new one).
import * as THREE from 'three';

const COLOURS = {
  'game-path': 0x46a6ff, 'mod-path': 0x8cff5a,
  'game-boundary': 0xff9a3c, 'mod-boundary': 0xffe14d, 'map-boundary': 0x5ad1ff, killbox: 0xff4a4a, selected: 0xffffff,
};
const WALL = 6;            // metres: how tall zone walls are drawn
const LIFT = 0.4;          // draw lines a little above their points

export function createLayout(ctx) {
  const {scene, gizmo, heightAt, threePos, gamePos, status, $} = ctx;
  const group = new THREE.Group();
  scene.add(group);
  // Water: the game's "type water" boundaries, drawn as their surface (a flat
  // fill at the boundary's "height", in its red/green/blue/alpha colour).
  // Kept apart from the outlines so it has its own toggle and isn't picked.
  const waterGroup = new THREE.Group();
  scene.add(waterGroup);
  const handleGeo = new THREE.SphereGeometry(0.7, 12, 8);
  const handleMat = new THREE.MeshBasicMaterial({color: 0xffcc33, depthTest: false});
  const handlePick = new THREE.MeshBasicMaterial({color: 0xff5050, depthTest: false});
  const st = {
    map: null, items: [], files: [], selected: null, handles: [], point: null,
    gameEdits: new Map(), dirtyFiles: new Set(), deleted: [], draw: null, show: {path: true, boundary: true},
    showWater: true, own: null, ownDirty: false,
  };
  // The mod's own items and the zones it adds to the map's record can be reshaped and deleted.
  const editable = (it) => it.source === 'mod' || (it.source === 'map' && it.editable !== false);
  // Water as the game's SW harbour has it (nampowater): small waves, a grey-green tint.
  const WATER_DEFAULTS = {type: 'water', numgrids: '16', gridsize: '2', amplitude: '.2',
                          red: '86', green: '101', blue: '100', alpha: '180'};

  const colourOf = (it) => (it === st.selected ? COLOURS.selected
    : it.kind === 'boundary' && /killbox/i.test(it.properties.type || '') ? COLOURS.killbox
    : COLOURS[`${it.source}-${it.kind}`]);

  // ---------------------------------------------------------------- drawing
  const WATER_BLUE = new THREE.Color(0x2f6f9f);
  const waterTextures = new Map();
  function waterTexture(name) {          // tiled every 16 m (the shape's UVs are in metres)
    if (!waterTextures.has(name)) {
      const tex = new THREE.TextureLoader().load(`/api/texture/${encodeURIComponent(name)}.png`);
      tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
      tex.repeat.set(1 / 16, 1 / 16);
      tex.colorSpace = THREE.SRGBColorSpace;
      waterTextures.set(name, tex);
    }
    return waterTextures.get(name);
  }
  const isWater = (it) => it.kind === 'boundary' && /^water$/i.test((it.properties || {}).type || '');
  function prop(it, key) {        // property names vary in case and spacing in the game's data
    const want = key.toLowerCase();
    for (const [k, v] of Object.entries(it.properties || {})) if (k.trim().toLowerCase() === want) return v;
    return undefined;
  }
  function buildWater(it) {
    if (it.water) { waterGroup.remove(it.water); it.water.geometry.dispose(); it.water = null; }
    if (!isWater(it) || it.points.length < 3) return;
    const shape = new THREE.Shape(it.points.map((p) => new THREE.Vector2(p[0], p[2])));
    const geo = new THREE.ShapeGeometry(shape);
    geo.rotateX(Math.PI / 2);          // shape (x, z) -> three (x, 0, z)
    const h = parseFloat(prop(it, 'height'));
    const y = Number.isFinite(h) ? h : it.points.reduce((s, p) => s + p[1], 0) / it.points.length;
    const byte = (k, d) => { const v = parseFloat(prop(it, k)); return Number.isFinite(v) ? v / 255 : d; };
    const colour = new THREE.Color(byte('red', 0.24), byte('green', 0.38), byte('blue', 0.45));
    if (prop(it, 'greeb') !== undefined && prop(it, 'green') === undefined) colour.g = byte('greeb', 0.38);
    // The game's tints are greyish (it adds a texture and a sky reflection):
    // lean them toward blue so water reads as water here.
    colour.lerp(WATER_BLUE, 0.45);
    const mat = new THREE.MeshLambertMaterial({color: colour, map: waterTexture(prop(it, 'texture') || 'oceanwater'),
                                               transparent: true, opacity: Math.max(0.45, byte('alpha', 0.7)),
                                               side: THREE.DoubleSide, depthWrite: false});
    const mesh = new THREE.Mesh(geo, mat);
    mesh.position.y = y;
    mesh.renderOrder = 2;
    mesh.visible = st.showWater;
    it.water = mesh;
    waterGroup.add(mesh);
  }
  function buildVisual(it) {
    buildWater(it);
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
    st.own = doc.own || null;
    st.ownDirty = false;
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
    for (const it of st.items) {
      if (it.visual) group.remove(it.visual);
      if (it.water) { waterGroup.remove(it.water); it.water.geometry.dispose(); it.water = null; }
    }
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

  function hoverText(ray) {
    if (st.draw) return null;
    ray.params.Line.threshold = 1.5;
    const lines = [];
    for (const g of group.children) {
      if (!g.visible || !g.userData.item) continue;
      for (const c of g.children) if (c.isLine) lines.push(c);
    }
    const hit = ray.intersectObjects(lines, false)[0];
    if (!hit) return null;
    const it = hit.object.parent.userData.item;
    const type = it.properties && it.properties.type ? ` (${it.properties.type})` : '';
    return {distance: hit.distance, title: it.name,
            text: `${it.source} ${it.kind === 'path' ? 'path' : 'zone' + type}, layer ${it.layer}`};
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
    else if (it.source === 'map') st.ownDirty = true;
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
    if (spec.events) {
      const out = {};
      for (const [k, v] of Object.entries(spec.events)) {
        const i = +k;
        if (by < 0 && i === from) continue;
        out[i >= from + (by > 0 ? 0 : 1) ? i + by : i] = v;
      }
      spec.events = out;
    }
    for (const link of spec.links || []) {
      if (link.our_point !== undefined && link.our_point >= from + (by > 0 ? 0 : 1)) link.our_point += by;
    }
  }

  function addPoint() {
    const it = st.selected;
    if (!it || !editable(it)) return;
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
    if (!it || !editable(it) || st.point === null) return;
    const need = it.kind === 'path' ? 2 : 3;
    if (it.points.length <= need) return status(`A ${label(it)} needs at least ${need} points`, true);
    it.points.splice(st.point, 1);
    shiftIndices(it, st.point, -1);
    edited(it);
    select(it, null);
  }

  function deleteItem() {
    const it = st.selected;
    if (!it || !editable(it)) return;
    if (!confirm(`Delete ${label(it)} ${it.name}? Scripts that use it by name will fail.`)) return;
    removeItem(it);
    ctx.remember && ctx.remember(`Deleted ${label(it)} ${it.name}`, () => restoreItem(it), () => removeItem(it));
  }

  // ---------------------------------------------------------------- undo
  // A path's or zone's data (points, settings) before and after an edit; and
  // whole ones taken away and brought back. Steps go on the editor's history.
  const itemData = (it) => JSON.stringify({points: it.points, spec: it.spec || null, properties: it.properties || {}});
  function putItemData(it, json) {
    const d = JSON.parse(json);
    it.points = d.points;
    if (d.spec) it.spec = d.spec;
    it.properties = d.properties;
    if (it.spec && it.source !== 'game') { it.spec.points = it.points; it.spec.properties = it.properties; }
    edited(it);
    buildVisual(it);
    if (st.selected === it) select(it, null);
  }
  function removeItem(it) {
    if (st.selected === it) select(null);
    if (it.visual) group.remove(it.visual);
    if (it.water) { waterGroup.remove(it.water); it.water = null; }
    st.items = st.items.filter((x) => x !== it);
    if (it.source === 'mod') st.dirtyFiles.add(it.file);
    if (it.source === 'map') st.ownDirty = true;
    ctx.setDirty(true);
    refreshList();
  }
  function restoreItem(it) {
    if (!st.items.includes(it)) st.items.push(it);
    buildVisual(it);
    edited(it);
  }
  // fn edits the selected path or zone: one undo step if it changed it
  const onItem = (what, fn) => (...args) => {
    const it = st.selected, before = it ? itemData(it) : null;
    const r = fn(...args);
    if (it && st.items.includes(it) && ctx.remember) {
      const after = itemData(it);
      if (after !== before) ctx.remember(`${what} ${it.name}`, () => putItemData(it, before), () => putItemData(it, after));
    }
    return r;
  };
  let dragBefore = null;          // a point being dragged: its item's data first
  function dragStart() { dragBefore = st.selected ? [st.selected, itemData(st.selected)] : null; }
  function dragEnd() {
    if (!dragBefore || !ctx.remember) return;
    const [it, before] = dragBefore;
    dragBefore = null;
    const after = itemData(it);
    if (after !== before) ctx.remember(`Moved a point of ${it.name}`, () => putItemData(it, before), () => putItemData(it, after));
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

  const OWN = '@map';            // the draw-file choice for the map's own record
  // The map's water zones as the game will have them: its own and the mod's added ones.
  const ownWater = () => st.items.filter((x) => isWater(x) && x.layer.toLowerCase() === String(st.map).toLowerCase()
                                          && (x.source === 'game' || x.source === 'map'));

  function startDraw(kind, file, type) {
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
    const want = typeof file === 'string' && file !== OWN ? file : null;
    const files = [...new Set([...st.files, fresh, ...(want ? [want] : [])])];
    $('draw-file').innerHTML = files.map((f) => `<option value="${f}">encounters/${f}</option>`).join('')
      + (kind === 'boundary' && st.own ? `<option value="${OWN}">The map's own zones (${st.own.file}): water shows only from these</option>` : '');
    $('draw-file').value = want || st.files[0] || fresh;
    $('draw-type-row').hidden = kind !== 'boundary';
    if (kind === 'boundary') $('draw-type').value = type || '';
    if (type === 'water') $('draw-name').value = `foundry_water${k}`;
    drawWeatherRow();
    $('draw').hidden = false;
    $('draw-title').textContent = kind === 'path' ? 'New path' : type === 'water' ? 'New water' : 'New zone';
    drawWaterHint();
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

  // A weather zone (type atmosphere) is named after the preset it shows: the
  // game matches them by name.
  function drawWeatherRow() {
    const on = !$('draw-type-row').hidden && $('draw-type').value === 'atmosphere';
    $('draw-weather-row').hidden = !on;
    if (!on) return;
    const presets = (ctx.skyPresets && ctx.skyPresets()) || [];
    const cur = $('draw-weather').value;
    $('draw-weather').innerHTML = presets.map((p) => `<option ${p === cur ? 'selected' : ''}>${p}</option>`).join('')
      || '<option value="">(no presets on this map)</option>';
    if ($('draw-weather').value) $('draw-name').value = $('draw-weather').value;
  }
  // Water goes in the map's own record: picking it as the type picks that.
  function drawWaterHint() {
    const water = !$('draw-type-row').hidden && $('draw-type').value === 'water';
    if (water && st.own && $('draw-file').value !== OWN) $('draw-file').value = OWN;
    const n = ownWater().length;
    $('draw-water-hint').hidden = !water;
    $('draw-water-hint').textContent = !water ? '' : $('draw-file').value !== OWN
      ? 'Water in a layer file doesn\'t show in the game: pick the map\'s own zones.'
      : `Draw the water's outline along the shore: its surface starts at the points' average height (change it after). `
        + `${n} of the ${(st.own && st.own.water_limit) || 16} water zones the game holds on this map are used.`;
  }
  $('draw-type').addEventListener('change', drawWeatherRow);
  $('draw-type').addEventListener('change', drawWaterHint);
  $('draw-file').addEventListener('change', drawWaterHint);
  $('draw-weather').addEventListener('change', () => { $('draw-name').value = $('draw-weather').value; });

  function finishDraw() {
    const d = st.draw;
    if (!d) return;
    if (d.kind === 'boundary' && $('draw-type').value === 'atmosphere') {
      const presets = (ctx.skyPresets && ctx.skyPresets()) || [];
      const n = $('draw-name').value.trim().toLowerCase();
      if (!presets.some((p) => p.toLowerCase() === n)) {
        return status('A weather zone is named after one of the map\'s presets (pick it above, or make one in Sky)', true);
      }
    }
    const need = d.kind === 'path' ? 2 : 3;
    if (d.points.length < need) return status(`A ${label(d)} needs at least ${need} points`, true);
    const name = $('draw-name').value.trim();
    if (!/^[A-Za-z0-9_\-]+$/.test(name)) return status('Names use letters, digits, _ and - only', true);
    if (st.items.some((x) => x.kind === d.kind && x.name.toLowerCase() === name.toLowerCase())) {
      return status(`There is already a ${label(d)} named ${name}`, true);
    }
    const file = $('draw-file').value;
    const type = d.kind === 'boundary' ? $('draw-type').value : '';
    const props = type === 'water' ? {...WATER_DEFAULTS, height: averageHeight(d.points)} : type ? {type} : {};
    const spec = {points: d.points};
    if (Object.keys(props).length) spec.properties = props;
    let it;
    if (file === OWN) {
      if (!st.own.editable) return status(st.own.note, true);
      const limit = st.own.water_limit || 16;
      if (type === 'water' && ownWater().length >= limit) {
        return status(`This map already has ${limit} water zones, all the game holds: make one bigger instead`, true);
      }
      it = {id: `map:${st.map}:boundary:${name}`, source: 'map', file: st.own.file, layer: st.map,
            kind: 'boundary', name, points: d.points, properties: props, spec, editable: true};
    } else {
      it = {id: `mod:${file}:${d.kind}:${name}`, source: 'mod', file, layer: file.replace(/\.json$/, ''),
            kind: d.kind, name, points: d.points, properties: props, spec};
      if (!st.files.includes(file)) st.files.push(file);
    }
    cancelDraw();
    st.items.push(it);
    it.original = it.points.map((p) => [...p]);
    buildVisual(it);
    edited(it);
    select(it);
    ctx.remember && ctx.remember(`Added ${label(it)} ${name}`, () => removeItem(it), () => restoreItem(it));
    status(type === 'water' ? `${name} added: set its surface height and colour in the panel` : `${name} added`);
  }
  // A water surface's starting height: the drawn points' average (they're on the shore), to 0.1 m.
  const averageHeight = (pts) => (Math.round(pts.reduce((s, p) => s + p[1], 0) / pts.length * 10) / 10).toFixed(1);

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
    $('item-kind').textContent = `${it.source === 'game' ? 'Game' : it.source === 'map' ? 'The map\'s own' : 'Mod'} ${label(it)}, ${it.points.length} points`;
    $('item-layer').textContent = it.layer;
    const mod = editable(it);
    drawProps(it, mod);
    $('item-add').hidden = $('item-del-point').hidden = $('item-delete').hidden = !mod;
    $('item-revert').hidden = it.source !== 'game';
    $('item-del-point').disabled = st.point === null;
    $('item-note').textContent = it.source === 'map'
      ? (it.editable === false ? st.own.note : `Saved in ${it.file}, with the map's own zones.`)
      : mod ? `Saved in encounters/${it.file}.`
      : 'A game item: drag its points (click a point first). The number of points stays the same.';
    showExtras(it);
    showWaterPanel(it);
    $('point-xyz').hidden = st.point === null;
    if (st.point !== null) {
      const p = it.points[st.point];
      $('point-label').textContent = `Point ${st.point}`;
      [$('qx').value, $('qy').value, $('qz').value] = p.map((v) => v.toFixed(2));
    }
  }

  // ---------------------------------------------------------------- properties
  // A zone's or path's properties: read only for the game's, a table to edit
  // for the mod's own (ambience zones: sound and width; killboxes; traffic
  // paths' width...), with presets for the common kinds of zone.
  const PRESETS = {
    ambience: {label: 'Ambience zone', props: {type: 'ambience', sound: '', width: '20'},
               hint: 'Switches the background sound (a soundscape: maps json "ambience") inside it, fading over width metres.'},
    killbox: {label: 'Allowed area', props: {type: 'killbox', TestInside: 'false'},
              hint: 'With TestInside false, leaving it is deadly: the edge of the playable map.'},
    trigger: {label: 'Trigger', props: {type: 'trigger'}, hint: 'A zone missions watch (goto, hold, leave).'},
  };
  function drawProps(it, can) {
    const esc = (v) => String(v ?? '').replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
    const entries = Object.entries(it.properties || {});
    if (!can) {
      $('item-props').textContent = entries.map(([k, v]) => `${k} = ${v}`).join(', ') || '(none)';
      return;
    }
    const isZone = it.kind === 'boundary';
    const type = (prop(it, 'type') || '').toLowerCase();
    $('item-props').innerHTML = `<div class="props-table">${entries.map(([k, v]) => `<div class="prop-row" data-k="${esc(k)}">
        <input class="prop-k" value="${esc(k)}" spellcheck="false"><input class="prop-v" value="${esc(v)}" spellcheck="false">
        <button class="prop-del" title="Remove it">&times;</button></div>`).join('')}</div>
      <div class="buttons"><button class="prop-add">+ Property</button>
        ${isZone && !isWater(it) ? Object.entries(PRESETS).map(([k, p]) => `<button class="prop-preset ${type === k ? 'on' : ''}" data-preset="${k}" title="${esc(p.hint)}">${p.label}</button>`).join('') : ''}</div>
      ${PRESETS[type] ? `<p class="hint">${esc(PRESETS[type].hint)}</p>` : ''}`;
    const box = $('item-props');
    const changed = () => { if (it.spec) it.spec.properties = it.properties; edited(it); buildVisual(it); };
    const step = (fn) => onItem('Changed the properties of', fn);     // one undo step each
    box.querySelectorAll('.prop-row').forEach((row) => {
      const old = row.dataset.k;
      row.querySelector('.prop-v').onchange = step((e) => { it.properties[old] = e.target.value; changed(); });
      row.querySelector('.prop-k').onchange = step((e) => {
        const nk = e.target.value.trim();
        if (!nk || (nk !== old && nk in it.properties)) { e.target.value = old; return status('A property needs a name of its own', true); }
        const v = it.properties[old];
        delete it.properties[old];
        it.properties[nk] = v;
        changed(); showPanel();
      });
      row.querySelector('.prop-del').onclick = step(() => { delete it.properties[old]; changed(); showPanel(); });
    });
    box.querySelector('.prop-add').onclick = step(() => {
      let k = 'key', n = 1;
      while (k in it.properties) k = `key${++n}`;
      it.properties[k] = '';
      changed(); showPanel();
      const rows = box.querySelectorAll('.prop-k');
      if (rows.length) { rows[rows.length - 1].focus(); rows[rows.length - 1].select(); }
    });
    box.querySelectorAll('.prop-preset').forEach((b) => b.onclick = step(() => {
      const pr = PRESETS[b.dataset.preset].props;
      for (const [k, v] of Object.entries(pr)) if (k === 'type' || prop(it, k) === undefined) setProp(it, k, v);
      setProp(it, 'type', pr.type);
      changed(); showPanel();
    }));
  }

  // ---------------------------------------------------------------- water settings
  // A water zone's surface: height, tint, see-through, waves and texture
  // (the game's keys: height, red/green/blue/alpha 0-255, amplitude, texture).
  const hex2 = (n) => Math.max(0, Math.min(255, Math.round(+n || 0))).toString(16).padStart(2, '0');
  function showWaterPanel(it) {
    const on = isWater(it);
    $('water-extras').hidden = !on;
    if (!on) return;
    const can = editable(it);
    for (const el of $('water-extras').querySelectorAll('input, button')) el.disabled = !can;
    const v = (k, d) => { const x = prop(it, k); return x === undefined ? d : x; };
    const set = (id, val) => { if (document.activeElement !== $(id)) $(id).value = val; };
    set('wa-height', v('height', ''));
    set('wa-colour', `#${hex2(v('red', 86))}${hex2(v('green', v('greeb', 101)))}${hex2(v('blue', 100))}`);
    set('wa-alpha', v('alpha', 180));
    set('wa-waves', v('amplitude', 0));
    set('wa-texture', v('texture', ''));
    const inLayer = it.source === 'mod';
    $('wa-move').hidden = !inLayer || !st.own;
    $('wa-note').textContent = inLayer
      ? 'This water is in a layer file, and the game makes water only from the map\'s own zones: move it there.'
      : it.source === 'game' ? 'The game\'s own water (read only here).'
      : `${ownWater().length} of the ${st.own.water_limit || 16} water zones the game holds on this map.`;
  }
  // Set (or, with '', drop) a property, keeping the game's spelling of its key.
  function setProp(it, key, value) {
    const have = Object.keys(it.properties).find((k) => k.trim().toLowerCase() === key);
    if (value === '' || value === null) { if (have) delete it.properties[have]; } else it.properties[have || key] = String(value);
    if (it.spec) it.spec.properties = it.properties;
  }
  function waterEdited() {
    const it = st.selected;
    if (!it || !isWater(it) || !editable(it)) return;
    const h = $('wa-height').value.trim();
    if (h === '' || !Number.isFinite(+h)) return status('A water surface needs a height (metres)', true), showPanel();
    setProp(it, 'height', (+h).toFixed(2).replace(/\.?0+$/, ''));
    const c = $('wa-colour').value;
    setProp(it, 'red', parseInt(c.slice(1, 3), 16));
    setProp(it, 'green', parseInt(c.slice(3, 5), 16));
    if (prop(it, 'greeb') !== undefined) setProp(it, 'greeb', '');
    setProp(it, 'blue', parseInt(c.slice(5, 7), 16));
    setProp(it, 'alpha', Math.max(0, Math.min(255, Math.round(+$('wa-alpha').value || 0))));
    const w = $('wa-waves').value.trim();
    setProp(it, 'amplitude', w === '' || +w === 0 ? '' : String(+w));
    const t = $('wa-texture').value.trim();
    if (t && !/^[A-Za-z0-9_]+$/.test(t)) return status('A texture name is letters, digits and _', true), showPanel();
    setProp(it, 'texture', t);
    edited(it);
    buildVisual(it);
    showPanel();
    status('');
  }
  function waterToGround() {
    const it = st.selected;
    if (!it || !editable(it)) return;
    $('wa-height').value = averageHeight(it.points.map((p) => [p[0], heightAt(p[0], p[2]), p[2]]));
    waterEdited();
  }
  // A water zone in a layer file, moved to the map's own record (one undo step).
  function moveToOwn() {
    const it = st.selected;
    if (!it || it.source !== 'mod' || !st.own) return;
    if (!st.own.editable) return status(st.own.note, true);
    if (ownWater().length >= (st.own.water_limit || 16)) return status('This map already has all the water zones the game holds', true);
    if (st.items.some((x) => x !== it && x.source === 'map' && x.name.toLowerCase() === it.name.toLowerCase())) {
      return status(`The map already has a zone named ${it.name}`, true);
    }
    const before = {source: it.source, file: it.file, layer: it.layer, id: it.id};
    const after = {source: 'map', file: st.own.file, layer: st.map, id: `map:${st.map}:boundary:${it.name}`, editable: true};
    const put = (to) => {
      st.dirtyFiles.add(it.source === 'mod' ? it.file : before.file);
      Object.assign(it, to);
      st.ownDirty = true;
      ctx.setDirty(true);
      buildVisual(it);
      refreshList();
      if (st.selected === it) showPanel();
    };
    put(after);
    ctx.remember && ctx.remember(`Moved ${it.name} to the map's own zones`, () => put(before), () => put(after));
    status(`${it.name} is now one of the map's own zones (${st.own.file})`);
  }

  // ---------------------------------------------------------------- path extras
  // Pauses (seconds), events (animation clips) and links, on the mod's own
  // paths: what the build writes as PSTM, EVNT and JNCT.
  let animList = null;
  async function loadAnims() {
    if (animList) return;
    animList = [];
    try { animList = await (await fetch('/api/animations')).json(); } catch (e) { return; }
    $('anims').innerHTML = animList.filter((n) => /fidget|idle|look|salute|smoke|talk|wave|point/i.test(n))
      .map((n) => `<option value="${n}">`).join('');
  }
  const eventsText = (list) => (list || []).map((e) => (typeof e === 'string' ? e
    : (e.value === undefined || +e.value === 100 ? e.anim : `${e.anim}:${e.value}`))).join(', ');
  function parseEvents(text) {
    const out = [];
    for (const part of text.split(',').map((s) => s.trim()).filter(Boolean)) {
      const [anim, chance] = part.split(':').map((s) => s.trim());
      if (!/^[A-Za-z0-9_]+$/.test(anim)) throw new Error(`"${anim}" isn't a clip name`);
      if (chance === undefined) out.push(anim);
      else if (Number.isFinite(+chance) && +chance >= 0 && +chance <= 100) out.push({anim, value: +chance});
      else throw new Error(`the chance for ${anim} is 0 to 100`);
    }
    return out;
  }
  function showExtras(it) {
    const on = it.source === 'mod' && it.kind === 'path';
    $('path-extras').hidden = !on;
    if (!on) return;
    loadAnims();
    const spec = it.spec || (it.spec = {});
    $('path-names').innerHTML = st.items.filter((x) => x.kind === 'path' && x !== it)
      .map((x) => `<option value="${x.name}">`).join('');
    $('point-extras').hidden = st.point === null;
    if (st.point !== null) {
      const pause = (spec.pauses || {})[st.point];
      const events = (spec.events || {})[st.point];
      if (document.activeElement !== $('pt-pause')) $('pt-pause').value = pause === undefined ? '' : pause;
      if (document.activeElement !== $('pt-anims')) $('pt-anims').value = eventsText(events);
    }
    const ul = $('path-links');
    ul.innerHTML = '';
    for (const [i, link] of (spec.links || []).entries()) {
      const li = document.createElement('li');
      const ours = link.our_point === undefined ? it.points.length - 1 : link.our_point;
      li.textContent = `Point ${ours} → ${link.path}, point ${link.their_point || 0} `;
      const x = document.createElement('button');
      x.textContent = 'Remove';
      x.onclick = () => { spec.links.splice(i, 1); edited(it); showPanel(); };
      li.appendChild(x);
      ul.appendChild(li);
    }
  }
  function setPause() {
    const it = st.selected;
    if (!it || st.point === null) return;
    const spec = it.spec || (it.spec = {});
    const v = $('pt-pause').value.trim();
    if (v !== '' && (spec.events || {})[st.point]) {
      status('This point plays animations; the game never has a pause and animations on one path point', true);
      return showPanel();
    }
    if (v !== '' && Object.keys(spec.events || {}).length) {
      status('This path has animations; the game never has pauses and animations on one path', true);
      return showPanel();
    }
    spec.pauses = spec.pauses || {};
    if (v === '' || +v <= 0) delete spec.pauses[st.point];
    else spec.pauses[st.point] = +v;
    if (!Object.keys(spec.pauses).length) delete spec.pauses;
    edited(it);
    status('');
  }
  function setAnims() {
    const it = st.selected;
    if (!it || st.point === null) return;
    const spec = it.spec || (it.spec = {});
    let list;
    try { list = parseEvents($('pt-anims').value); } catch (e) { status(e.message, true); return; }
    if (list.length && Object.keys(spec.pauses || {}).length) {
      status('This path has pauses; the game never has pauses and animations on one path', true);
      return showPanel();
    }
    spec.events = spec.events || {};
    if (list.length) spec.events[st.point] = list; else delete spec.events[st.point];
    if (!Object.keys(spec.events).length) delete spec.events;
    edited(it);
    status(list.length ? 'Characters following this path play these on reaching the point (needs toolkit 0.65 to build).' : '');
  }
  function addLink() {
    const it = st.selected;
    if (!it) return;
    const other = $('link-path').value.trim();
    const target = st.items.find((x) => x.kind === 'path' && x.name.toLowerCase() === other.toLowerCase());
    if (!target) return status(`No path called ${other} on this map`, true);
    const their = Math.max(0, Math.min(target.points.length - 1, Math.round(+$('link-their').value || 0)));
    const spec = it.spec || (it.spec = {});
    spec.links = spec.links || [];
    spec.links.push({path: target.name, their_point: their,
                     our_point: st.point === null ? it.points.length - 1 : st.point});
    edited(it);
    showPanel();
    status('');
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
    if (st.ownDirty && st.own) items.push([null, `Changed the map's own zones (${st.own.file})`]);
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
    if (!st.gameEdits.size && !st.dirtyFiles.size && !st.ownDirty) return null;
    const game = [...st.gameEdits.values()].map((it) => ({layer: it.layer, kind: it.kind, name: it.name, points: it.points}));
    const mod = {};
    for (const f of st.dirtyFiles) {
      const part = {paths: {}, boundaries: {}};
      for (const it of st.items.filter((x) => x.source === 'mod' && x.file === f)) {
        part[it.kind === 'path' ? 'paths' : 'boundaries'][it.name] = {...it.spec, points: it.points};
      }
      mod[f] = part;
    }
    const body = {map: st.map, game, mod};
    if (st.ownDirty) {
      body.own = {};
      for (const it of st.items.filter((x) => x.source === 'map' && x.editable !== false)) {
        body.own[it.name] = {points: it.points, properties: it.properties};
      }
    }
    return body;
  }

  function saved() {
    st.gameEdits.clear();
    st.dirtyFiles.clear();
    st.ownDirty = false;
    for (const it of st.items) it.original = it.points.map((p) => [...p]);
    refreshList();
  }

  // ---------------------------------------------------------------- wiring
  addPoint = onItem('Added a point to', addPoint);
  deletePoint = onItem('Deleted a point of', deletePoint);
  revertItem = onItem('Put back', revertItem);
  setPointFromPanel = onItem('Moved a point of', setPointFromPanel);
  setPause = onItem('Changed a pause on', setPause);
  setAnims = onItem('Changed the animations on', setAnims);
  addLink = onItem('Added a link to', addLink);
  $('new-path').onclick = () => startDraw('path');
  $('new-zone').onclick = () => startDraw('boundary');
  $('new-water').onclick = () => startDraw('boundary', OWN, 'water');
  waterEdited = onItem('Changed the water of', waterEdited);
  waterToGround = onItem('Changed the water of', waterToGround);
  for (const id of ['wa-height', 'wa-colour', 'wa-alpha', 'wa-waves', 'wa-texture']) $(id).addEventListener('change', waterEdited);
  $('wa-ground').onclick = waterToGround;
  $('wa-move').onclick = moveToOwn;
  $('draw-finish').onclick = finishDraw;
  $('draw-cancel').onclick = () => { cancelDraw(); status(''); };
  $('item-add').onclick = addPoint;
  $('item-del-point').onclick = deletePoint;
  $('item-delete').onclick = deleteItem;
  $('item-revert').onclick = revertItem;
  for (const id of ['qx', 'qy', 'qz']) $(id).addEventListener('change', setPointFromPanel);
  $('show-paths').addEventListener('change', (e) => setVisible('path', e.target.checked));
  $('show-zones').addEventListener('change', (e) => setVisible('boundary', e.target.checked));
  ctx.onWater && ctx.onWater(() => st.items.filter(isWater).map((it) => it.points));
  $('pt-pause').addEventListener('change', setPause);
  $('pt-anims').addEventListener('change', setAnims);
  $('link-add').onclick = addLink;
  $('show-water').addEventListener('change', (e) => {
    st.showWater = e.target.checked;
    for (const it of st.items) if (it.water) it.water.visible = st.showWater;
  });

  return {
    load, pick, select, handleMoved, hoverText, drawClick, finishDraw, cancelDraw, saveBody, saved, find,
    isDrawing: () => !!st.draw,
    names: () => st.items.map((x) => x.name),
    startDraw,
    files: () => [...st.files],
    hasSelection: () => !!st.selected,
    deletePoint, addPoint, dragStart, dragEnd,
  };
}
