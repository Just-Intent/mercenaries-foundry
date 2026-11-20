// Generators: what Gaza's tools did by script, in the editor.
// - Kits: a selection of objects saved as a kit (kits/<name>.json), stamped
//   on the nearest free ground to the view's centre (no object or road under
//   it, gentle ground, above a height if you give one), or filling a zone.
// - Traffic from roads: walkers along the kerbs or cars in lanes, on a new
//   map's drawn roads around the view's centre, clear of buildings: a traffic
//   zone in an encounters layer.
// - The game's traffic budgets (routes and names) per map.

const ASPHALT = 0.44;
const SPAWN = {
  walkers: 'template_civ_civilian01, template_civ_civilian02, template_civ_civilian03, template_civ_civilian04',
  cars: 'template_civ_80civic, template_civ_lada124, template_civ_markettruck, template_civ_generictruck',
};

export function createGenerators(ctx) {
  const {$, status, post, records, boundsOf, heightAt, viewCentre, selection, entryOf, placeMany, mapName, layerFile,
         findZone, reloadMap, lookAt} = ctx;
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const st = {kits: [], roads: null, roadsMap: null, counts: null, kit: '', yaw: 0, slope: 0.25, above: '', search: 150,
              fill: {zone: '', spacing: 35, most: 20}, traffic: {kind: 'walkers', radius: 400, zone: '', layer: '', density: '', spawn: '', most: 26}};
  const box = document.createElement('div');
  box.id = 'gen';
  box.hidden = true;
  $('panel').prepend(box);

  // ---------------------------------------------------------------- footprints
  const yawRows = (deg) => {
    const a = deg * Math.PI / 180, c = Math.cos(a), s = Math.sin(a);
    return [c, 0, -s, 0, 1, 0, s, 0, c].map((v) => Math.round(v * 1e6) / 1e6);
  };
  const headingOf = (m3) => Math.atan2(-m3[2], m3[0]) * 180 / Math.PI;
  // local (x, z) turned by a heading, as the game turns an object's axes
  const turn = (lx, lz, deg) => {
    const a = deg * Math.PI / 180, c = Math.cos(a), s = Math.sin(a);
    return [lx * c + lz * s, -lx * s + lz * c];
  };
  function halfOf(rec) {
    const b = boundsOf(rec.geometry || rec.model);
    if (!b) return null;
    return [Math.max(Math.abs(b[0]), Math.abs(b[3])), Math.max(Math.abs(b[2]), Math.abs(b[5]))];
  }
  // every object's footprint near a point: [x, z, heading, half x, half z]
  function footprints(cx, cz, reach) {
    const out = [];
    for (const r of records()) {
      if (r.removed || r.deleted) continue;
      const p = r.position;
      if (Math.abs(p[0] - cx) > reach || Math.abs(p[2] - cz) > reach) continue;
      if (/^(human|location|marker|spawn|trigger)/i.test(r.type || '')) continue;
      const h = halfOf(r);
      if (!h || (h[0] < 0.4 && h[1] < 0.4)) continue;
      out.push([p[0], p[2], headingOf(r.rotation), h[0], h[1]]);
    }
    return out;
  }
  class Blocked {
    constructor(items, roads, margin = 1.5) {
      this.margin = margin;
      this.cell = new Map();
      this.add = (x, z, yaw, hx, hz) => {
        const ob = [x, z, yaw * Math.PI / 180, hx, hz], r = Math.max(hx, hz) + margin;
        for (let cx = Math.floor((x - r) / 50); cx <= Math.floor((x + r) / 50); cx++)
          for (let cz = Math.floor((z - r) / 50); cz <= Math.floor((z + r) / 50); cz++) {
            const k = `${cx},${cz}`;
            if (!this.cell.has(k)) this.cell.set(k, []);
            this.cell.get(k).push(ob);
          }
      };
      for (const it of items) this.add(...it);
      this.segs = new Map();             // roads: segments by 50 m cell, with their half width
      for (const r of roads || []) {
        const half = r.span * ASPHALT / 2 + 1;
        for (let i = 0; i + 1 < r.points.length; i++) {
          const [a, b] = [r.points[i], r.points[i + 1]];
          for (let cx = Math.floor((Math.min(a[0], b[0]) - half) / 50); cx <= Math.floor((Math.max(a[0], b[0]) + half) / 50); cx++)
            for (let cz = Math.floor((Math.min(a[1], b[1]) - half) / 50); cz <= Math.floor((Math.max(a[1], b[1]) + half) / 50); cz++) {
              const k = `${cx},${cz}`;
              if (!this.segs.has(k)) this.segs.set(k, []);
              this.segs.get(k).push([a, b, half]);
            }
        }
      }
    }
    hit(x, z) {
      const k = `${Math.floor(x / 50)},${Math.floor(z / 50)}`;
      for (const [ox, oz, yaw, hx, hz] of this.cell.get(k) || []) {
        const dx = x - ox, dz = z - oz;
        const lx = dx * Math.cos(yaw) - dz * Math.sin(yaw), lz = dx * Math.sin(yaw) + dz * Math.cos(yaw);
        if (Math.abs(lx) <= hx + this.margin && Math.abs(lz) <= hz + this.margin) return 'object';
      }
      for (const [a, b, half] of this.segs.get(k) || []) {
        const vx = b[0] - a[0], vz = b[1] - a[1], len2 = vx * vx + vz * vz || 1;
        const t = Math.max(0, Math.min(1, ((x - a[0]) * vx + (z - a[1]) * vz) / len2));
        if (Math.hypot(x - (a[0] + vx * t), z - (a[1] + vz * t)) <= half) return 'road';
      }
      return null;
    }
  }
  // whether a kit's rectangle at (x, z, heading) stands on free, gentle ground
  function freeAt(blocked, x, z, yaw, half) {
    const [hx, hz] = half;
    const hs = [];
    for (let i = -hx; i <= hx + 0.01; i += Math.max(1.5, hx / 4))
      for (let k = -hz; k <= hz + 0.01; k += Math.max(1.5, hz / 4)) {
        const [dx, dz] = turn(i, k, yaw);
        const why = blocked.hit(x + dx, z + dz);
        if (why) return why;
        hs.push(heightAt(x + dx, z + dz));
      }
    const lo = Math.min(...hs), hi = Math.max(...hs);
    if (st.above !== '' && lo < +st.above) return 'low';
    if (hi - lo > st.slope * 2 * Math.hypot(hx, hz)) return 'slope';
    return null;
  }
  // the nearest free spot to (x, z): rings out from it
  function nearestFree(blocked, x, z, yaw, half, reach) {
    const step = Math.max(4, Math.min(half[0], half[1]));
    if (!freeAt(blocked, x, z, yaw, half)) return [x, z];
    for (let r = step; r <= reach; r += step) {
      const n = Math.ceil(2 * Math.PI * r / step);
      for (let k = 0; k < n; k++) {
        const a = 2 * Math.PI * k / n;
        const px = x + r * Math.cos(a), pz = z + r * Math.sin(a);
        if (!freeAt(blocked, px, pz, yaw, half)) return [px, pz];
      }
    }
    return null;
  }

  async function roads() {
    if (st.roadsMap !== mapName()) {
      st.roads = await (await fetch(`/api/gen/roads/${encodeURIComponent(mapName())}`)).json();
      st.roadsMap = mapName();
    }
    return st.roads;
  }

  // ---------------------------------------------------------------- kits
  function kitFromSelection() {
    const recs = selection();
    if (recs.length < 1) throw new Error('Select the objects first (Shift+click or Shift+drag for several)');
    const cx = recs.reduce((s, r) => s + r.position[0], 0) / recs.length;
    const cz = recs.reduce((s, r) => s + r.position[2], 0) / recs.length;
    let hx = 1, hz = 1;
    const objects = recs.map((r) => {
      const e = entryOf(r);
      const from = e ? e.template : (r.name ? r.name : `model:${r.model}`);
      const yaw = headingOf(r.rotation);
      const dx = r.position[0] - cx, dz = r.position[2] - cz;
      const h = halfOf(r) || [1, 1];
      for (const [sx, sz] of [[1, 1], [1, -1], [-1, 1], [-1, -1]]) {
        const [ox, oz] = turn(sx * h[0], sz * h[1], yaw);
        hx = Math.max(hx, Math.abs(dx + ox)); hz = Math.max(hz, Math.abs(dz + oz));
      }
      return {from, offset: [dx, r.position[1] - heightAt(r.position[0], r.position[2]), dz], yaw,
              ...(e && e.set ? {set: e.set} : {})};
    });
    return {objects, half: [hx, hz]};
  }

  function stampKit(kit, x, z, yaw, blocked) {
    const items = kit.objects.map((o) => {
      const [dx, dz] = turn(o.offset[0], o.offset[2], yaw);
      const px = x + dx, pz = z + dz;
      return {from: o.from, position: [px, heightAt(px, pz) + o.offset[1], pz].map((v) => Math.round(v * 1000) / 1000),
              rotation: yawRows(yaw + (o.yaw || 0)), set: o.set || null};
    });
    if (blocked) blocked.add(x, z, yaw, kit.half[0], kit.half[1]);
    return items;
  }

  // ---------------------------------------------------------------- the panel
  async function load() {
    st.kits = await (await fetch('/api/kits')).json();
    st.counts = await (await fetch('/api/gen/counts')).json();
    draw();
  }

  function draw() {
    if (box.hidden) return;
    const k = st.kits.find((x) => x.name === st.kit);
    const c = (st.counts && st.counts[mapName()]) || {routes: 0, names: 0};
    const t = st.traffic, f = st.fill;
    box.innerHTML = `<div class="panel-head"><h2>Generators</h2><button id="gen-close">Close</button></div>
      <h3>Kits</h3>
      <p class="hint">A kit is a group of objects kept together (a checkpoint, a camp block, a MASH): save a selection
        as one, then stamp it on free ground (no object or road under it, gentle ground) or fill a zone with it.
        Stamped objects go in ${esc(layerFile())} like anything you place, to save with Save.</p>
      <div class="buttons"><input id="gen-kit-name" placeholder="new kit's name"><button id="gen-kit-save">Save the selection as a kit</button></div>
      <label class="sky-row"><span>Kit</span><select id="gen-kit"><option value="">choose</option>
        ${st.kits.map((x) => `<option ${x.name === st.kit ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</select></label>
      ${k ? `<p class="hint">${k.objects.length} objects, about ${(2 * k.half[0]).toFixed(0)} x ${(2 * k.half[1]).toFixed(0)} m.</p>` : ''}
      <label class="sky-row"><span>Heading</span><input id="gen-yaw" type="number" step="15" value="${st.yaw}"></label>
      <label class="sky-row" title="How steep the ground under it may be: rise over run"><span>Steepest</span><input id="gen-slope" type="number" step="0.05" value="${st.slope}"></label>
      <label class="sky-row" title="Leave empty for anywhere; e.g. 1 keeps it off the beach"><span>Ground above</span><input id="gen-above" type="number" step="0.5" value="${esc(st.above)}" placeholder="any height"></label>
      <label class="sky-row"><span>Look up to (m)</span><input id="gen-search" type="number" step="25" value="${st.search}"></label>
      <div class="buttons"><button id="gen-stamp" ${k ? '' : 'disabled'}>Stamp near the view's centre</button></div>
      <h4>Fill a zone</h4>
      <label class="sky-row"><span>Zone</span><input id="gen-fill-zone" value="${esc(f.zone)}" placeholder="a zone's name (New zone draws one)"></label>
      <label class="sky-row"><span>Spacing / most</span><span class="hq-trio"><input id="gen-fill-spacing" type="number" value="${f.spacing}">
        <input id="gen-fill-most" type="number" value="${f.most}"></span></label>
      <div class="buttons"><button id="gen-fill" ${k ? '' : 'disabled'}>Fill it</button></div>
      <h3>Traffic from roads</h3>
      <p class="hint">The game's own traffic: walkers along both kerbs, or cars one lane each way, on this map's drawn
        roads around the view's centre, clear of buildings and props. The game spawns them near the player only.
        It writes a traffic zone into an encounters layer (its old paths replaced) and reloads the map.</p>
      <label class="sky-row"><span>Who</span><select id="gen-t-kind"><option value="walkers" ${t.kind === 'walkers' ? 'selected' : ''}>Walkers</option>
        <option value="cars" ${t.kind === 'cars' ? 'selected' : ''}>Cars</option></select></label>
      <label class="sky-row"><span>Around (m)</span><input id="gen-t-radius" type="number" step="50" value="${t.radius}"></label>
      <label class="sky-row"><span>Zone</span><input id="gen-t-zone" value="${esc(t.zone)}" placeholder="${esc(mapName())}-town-${t.kind}"></label>
      <label class="sky-row"><span>Layer</span><input id="gen-t-layer" value="${esc(t.layer)}" placeholder="${esc(mapName())}_traffic"></label>
      <label class="field">Who spawns (templates, comma between)<input id="gen-t-spawn" value="${esc(t.spawn)}" placeholder="${esc(SPAWN[t.kind])}"></label>
      <label class="sky-row"><span>Density / paths</span><span class="hq-trio"><input id="gen-t-density" type="number" value="${esc(t.density)}" placeholder="${t.kind === 'cars' ? 250 : 240}">
        <input id="gen-t-most" type="number" value="${t.most}"></span></label>
      <div class="buttons"><button id="gen-traffic">Lay it</button></div>
      <h3>Budgets on ${esc(mapName())}</h3>
      <p class="hint">Traffic routes: ${c.routes.toLocaleString()} of 1,024. Names (paths and zones in the mod's layers):
        ${c.names.toLocaleString()} of 2,048 (the game's own use some too). Past either, the map hangs while loading.</p>`;
    bind();
  }

  const num = (id) => +$(id).value;
  function bind() {
    $('gen-close').onclick = () => show(false);
    $('gen-kit').onchange = () => { st.kit = $('gen-kit').value; draw(); };
    for (const [id, k] of [['gen-yaw', 'yaw'], ['gen-slope', 'slope'], ['gen-search', 'search']]) $(id).onchange = () => { st[k] = num(id); };
    $('gen-above').onchange = () => { st.above = $('gen-above').value; };
    for (const [id, k] of [['gen-fill-zone', 'zone']]) $(id).onchange = () => { st.fill[k] = $(id).value.trim(); };
    for (const [id, k] of [['gen-fill-spacing', 'spacing'], ['gen-fill-most', 'most']]) $(id).onchange = () => { st.fill[k] = num(id); };
    $('gen-t-kind').onchange = () => { st.traffic.kind = $('gen-t-kind').value; draw(); };
    for (const [id, k] of [['gen-t-zone', 'zone'], ['gen-t-layer', 'layer'], ['gen-t-spawn', 'spawn'], ['gen-t-density', 'density']])
      $(id).onchange = () => { st.traffic[k] = $(id).value.trim(); };
    for (const [id, k] of [['gen-t-radius', 'radius'], ['gen-t-most', 'most']]) $(id).onchange = () => { st.traffic[k] = num(id); };

    $('gen-kit-save').onclick = async () => {
      const name = $('gen-kit-name').value.trim();
      try {
        const kit = kitFromSelection();
        const r = await post('/api/kits/save', {name, doc: kit});
        status(`Saved ${r.objects} objects as ${r.file}`);
        st.kit = name;
        await load();
      } catch (e) { status(e.message, true); }
    };
    $('gen-stamp').onclick = async () => {
      const kit = st.kits.find((x) => x.name === st.kit);
      try {
        const c = viewCentre();
        const reach = st.search + Math.max(...kit.half) + 50;
        const blocked = new Blocked(footprints(c[0], c[2], reach), await roads());
        const at = nearestFree(blocked, c[0], c[2], st.yaw, kit.half, st.search);
        if (!at) return status(`No free ground for ${kit.name} within ${st.search} m of the view's centre`, true);
        placeMany(stampKit(kit, at[0], at[1], st.yaw), `Stamped ${kit.name}`);
        lookAt([at[0], heightAt(at[0], at[1]), at[1]], 60);
        status(`Stamped ${kit.name} at ${at.map((v) => v.toFixed(0)).join(', ')} (${Math.hypot(at[0] - c[0], at[1] - c[2]).toFixed(0)} m from the centre): Save to keep it`);
      } catch (e) { status(e.message, true); }
    };
    $('gen-fill').onclick = async () => {
      const kit = st.kits.find((x) => x.name === st.kit);
      const zone = findZone(st.fill.zone);
      if (!zone) return status(`No zone called ${st.fill.zone || '(none given)'} on this map`, true);
      const pts = zone.points.map((p) => [p[0], p[2]]);
      const inside = (x, z) => {
        let on = false;
        for (let i = 0, j = pts.length - 1; i < pts.length; j = i++) {
          const [xi, zi] = pts[i], [xj, zj] = pts[j];
          if ((zi > z) !== (zj > z) && x < (xj - xi) * (z - zi) / (zj - zi) + xi) on = !on;
        }
        return on;
      };
      const xs = pts.map((p) => p[0]), zs = pts.map((p) => p[1]);
      const [x0, x1, z0, z1] = [Math.min(...xs), Math.max(...xs), Math.min(...zs), Math.max(...zs)];
      const blocked = new Blocked(footprints((x0 + x1) / 2, (z0 + z1) / 2, Math.max(x1 - x0, z1 - z0) / 2 + 60), await roads());
      const items = [];
      let n = 0, why = {};
      const s = Math.max(5, st.fill.spacing);
      for (let z = z0 + s / 2; z <= z1 && n < st.fill.most; z += s)
        for (let x = x0 + s / 2; x <= x1 && n < st.fill.most; x += s) {
          if (!inside(x, z)) continue;
          const no = freeAt(blocked, x, z, st.yaw, kit.half);
          if (no) { why[no] = (why[no] || 0) + 1; continue; }
          items.push(...stampKit(kit, x, z, st.yaw, blocked));
          n++;
        }
      if (!n) return status(`No free spot in ${st.fill.zone} (${Object.entries(why).map(([k, v]) => `${v} on ${k === 'object' ? 'objects' : k === 'road' ? 'roads' : k === 'low' ? 'low ground' : 'slopes'}`).join(', ') || 'too small'})`, true);
      placeMany(items, `Filled ${st.fill.zone} with ${n} ${kit.name}`);
      status(`${n} ${kit.name} in ${st.fill.zone}${Object.keys(why).length ? ` (left out: ${Object.entries(why).map(([k, v]) => `${v} on ${k}`).join(', ')})` : ''}: Save to keep them`);
    };
    $('gen-traffic').onclick = async () => {
      const t = st.traffic, c = viewCentre();
      const zone = t.zone || `${mapName()}-town-${t.kind}`;
      const layer = t.layer || `${mapName()}_traffic`;
      const spawn = (t.spawn || SPAWN[t.kind]).split(',').map((x) => x.trim()).filter(Boolean);
      try {
        const r = await post('/api/gen/traffic', {map: mapName(), kind: t.kind, area: {centre: [c[0], c[2]], radius: t.radius},
          footprints: footprints(c[0], c[2], t.radius + 60), layer, zone, spawn,
          density: t.density === '' ? null : +t.density, max_paths: t.most});
        status(`${r.zone}: ${r.paths} paths, ${r.km} km, ${r.linked} linked on, in ${r.file}; reloading the map...`);
        await reloadMap();
        st.counts = await (await fetch('/api/gen/counts')).json();
        draw();
      } catch (e) { status(e.message, true); }
    };
  }

  function show(on) {
    box.hidden = !on;
    $('gen-mode').classList.toggle('on', on);
    if (on) load().catch((e) => status(e.message, true));
  }
  $('gen-mode').onclick = () => show(box.hidden);
  return {show};
}
