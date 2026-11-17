// Sky and weather: a map's base sky settings, its season sections (a new
// map's config, copied from blank's, has [Winter], [Fall] and [Summer] skies)
// and its atmosphere presets (the named weather a weather zone or a mission
// switches on), edited as the mod's sky/<map>.json. The game's values show;
// changes are kept per key.

import * as THREE from 'three';

// SunAngle "a b" (degrees; the game's 0x211C00): the sun stands a - 90 above
// the horizon, toward (cos b, sin b) in (x, z); x is east and z south, so its
// compass bearing is b + 90 (see terrain_light.py).
export const sunFromAngle = (text) => {
  const [a, b] = String(text || '').trim().split(/\s+/).map(Number);
  return Number.isFinite(a) && Number.isFinite(b) ? {bearing: ((b + 90) % 360 + 360) % 360, height: a - 90} : null;
};
const trim = (v) => String(Math.round(v * 100) / 100);
export const sunAngle = (bearing, height) => `${trim(90 + height)} ${trim(((bearing - 90) % 360 + 360) % 360)}`;
// toward the sun, in the game's (and the editor's) x, y, z
const sunVector = ({bearing, height}) => {
  const b = bearing * Math.PI / 180, h = height * Math.PI / 180;
  return new THREE.Vector3(Math.cos(h) * Math.sin(b), Math.sin(h), -Math.cos(h) * Math.cos(b));
};

// Where the sun is for a place (degrees north, east) and a moment (a Date):
// the usual low-precision solar position (about 0.1 degree).
export function sunAt(lat, lon, when) {
  const rad = Math.PI / 180;
  const n = when.getTime() / 86400000 + 2440587.5 - 2451545.0;
  const L = (280.460 + 0.9856474 * n) % 360;
  const g = ((357.528 + 0.9856003 * n) % 360) * rad;
  const lambda = (L + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) * rad;
  const eps = (23.439 - 0.0000004 * n) * rad;
  const ra = Math.atan2(Math.cos(eps) * Math.sin(lambda), Math.cos(lambda));
  const dec = Math.asin(Math.sin(eps) * Math.sin(lambda));
  const gmst = ((18.697374558 + 24.06570982441908 * n) % 24 + 24) % 24;
  const ha = (gmst * 15 + lon) * rad - ra;
  const phi = lat * rad;
  const el = Math.asin(Math.sin(phi) * Math.sin(dec) + Math.cos(phi) * Math.cos(dec) * Math.cos(ha));
  const az = Math.atan2(-Math.sin(ha), Math.tan(dec) * Math.cos(phi) - Math.sin(phi) * Math.cos(ha));
  return {bearing: ((az / rad) % 360 + 360) % 360, height: el / rad};
}

export function createSky(ctx) {
  const {$, status, setDirty} = ctx;
  const st = {map: null, data: null, mod: {}, preset: null, season: null, dirty: false,
              lighting: null, lightingChanged: false};
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const lower = (s) => String(s || '').toLowerCase();

  async function load(map) {
    st.map = map;
    st.preset = null;
    try {
      const r = await fetch(`/api/sky/${map}`);
      st.data = await r.json();
      if (!r.ok) throw new Error(st.data.error || r.statusText);
    } catch (e) {
      st.data = {level: false};
      console.warn('sky:', e);
    }
    st.mod = JSON.parse(JSON.stringify(st.data.mod || {}));
    // "all" (every season) as each season's own keys: the panel shows and saves them per season
    const all = st.mod.seasons && Object.keys(st.mod.seasons).find((n) => lower(n) === 'all');
    if (all) {
      const keys = st.mod.seasons[all];
      delete st.mod.seasons[all];
      for (const sn of (st.data.seasons || []).map((x) => x.name)) {
        const own = st.mod.seasons[sn] || {};
        const merged = {...keys};
        for (const [k, v] of Object.entries(own)) {
          const same = Object.keys(merged).find((x) => lower(x) === lower(k));
          if (same) delete merged[same];
          merged[k] = v;
        }
        st.mod.seasons[sn] = merged;
      }
    }
    st.season = null;
    st.lighting = st.data.lighting && st.data.lighting.doc ? JSON.parse(JSON.stringify(st.data.lighting.doc)) : null;
    st.lightingChanged = false;
    st.dirty = false;
    draw();
    undoBase = snapshot();      // after drawing, which adds an empty "presets" (not an edit)
  }

  function show(on) {
    $('sky').hidden = !on;
    $('sky-mode').classList.toggle('on', on);
    sunMark.visible = on;
    if (on) draw();
  }

  // The sun in the 3D view: a disc in its direction from the view's centre, and a line to it.
  const sunMark = new THREE.Group();
  sunMark.visible = false;
  const disc = new THREE.Mesh(new THREE.SphereGeometry(18, 20, 12),
                              new THREE.MeshBasicMaterial({color: 0xffd25a, depthTest: false, fog: false}));
  disc.renderOrder = 20;
  const ray = new THREE.Line(new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3(0, 1, 0)]),
                             new THREE.LineBasicMaterial({color: 0xffd25a, depthTest: false, fog: false}));
  ray.renderOrder = 20;
  sunMark.add(disc, ray);
  if (ctx.scene) ctx.scene.add(sunMark);
  const SUN_DISTANCE = 400;
  disc.onBeforeRender = () => {
    if (ctx.target) sunMark.position.copy(ctx.target());
  };
  function placeSun() {
    const text = (st.mod.base || {}).SunAngle ?? Object.entries((st.data || {}).base || {}).find(([k]) => lower(k) === 'sunangle')?.[1];
    const sun = sunFromAngle(text);
    disc.visible = ray.visible = !!sun;
    if (!sun) return;
    const v = sunVector(sun).multiplyScalar(SUN_DISTANCE);
    disc.position.copy(v);
    ray.geometry.setFromPoints([new THREE.Vector3(), v]);
  }

  // Place and time for "From a place and time" (Gaza by default: the map this was made for).
  st.when = {lat: 31.5, lon: 34.45, date: '2025-06-21', time: '15:00', utc: 3};
  st.whenOpen = false;
  function setSun(bearing, height) {
    if (!Number.isFinite(bearing) || !Number.isFinite(height)) return status('The sun needs a bearing and a height', true);
    if (height < 1 || height > 90) return status('The sun\'s height is 1-90 degrees above the horizon', true);
    (st.mod.base = st.mod.base || {}).SunAngle = sunAngle(bearing, height);
    edited(); draw();
  }
  // the ground lighting settings from the panel
  function lightingEdited() {
    const lt = st.lighting;
    if (!lt) return;
    const rgb = (id) => { const h = $(id).value; return [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16)); };
    lt.ambient = rgb('lt-ambient');
    lt.diffuse = rgb('lt-diffuse');
    const sh = +$('lt-shadow').value;
    lt.shadow = Number.isFinite(sh) ? Math.max(0, Math.min(1, sh)) : 0.6;
    if ($('lt-tint').checked) delete lt.tint; else lt.tint = false;
    // a colour map tinting the lit ground (128 neutral), and how strongly
    const map = $('lt-tintmap').value;
    if (map) lt.tint_map = map; else { delete lt.tint_map; delete lt.tint_strength; }
    const str = $('lt-tintstr') ? +$('lt-tintstr').value : 1;
    if (map && Number.isFinite(str) && str !== 1) lt.tint_strength = Math.max(0, Math.min(1, str)); else delete lt.tint_strength;
    st.lightingChanged = true;
    edited();
  }

  function edited() {
    st.dirty = true;
    placeSun();
    setDirty(true);
    clearTimeout(undoTimer);
    undoTimer = setTimeout(flushUndo, 700);       // a burst of edits is one undo step
  }
  // The map's sky edits before and after a change: one step on the editor's history.
  let undoBase = null, undoTimer = null;
  const snapshot = () => JSON.stringify({mod: st.mod, lighting: st.lighting, changed: st.lightingChanged});
  function flushUndo() {
    clearTimeout(undoTimer);
    undoTimer = null;
    if (undoBase === null) return;
    const now = snapshot();
    if (now === undoBase) return;
    const before = undoBase, after = now, map = st.map;
    undoBase = now;
    const put = (json) => {
      if (st.map !== map) return status(`That undo step is for ${map}'s sky: open that map first`, true);
      const d = JSON.parse(json);
      st.mod = d.mod;
      st.lighting = d.lighting;
      st.lightingChanged = d.changed;
      undoBase = json;
      st.dirty = true;
      setDirty(true);
      draw();
    };
    ctx.remember && ctx.remember(`Edited ${map}'s sky`, () => put(before), () => put(after));
  }

  // The game's preset by name, the mod's new ones, and a preset's settings
  // as they'll be: [[key, value]], repeated keys (ProcessRGBA) as one entry
  // holding a list.
  const retail = (name) => (st.data.presets || []).find((p) => lower(p.name) === lower(name));
  const modPresets = () => (st.mod.presets = st.mod.presets || {});
  function names() {
    const out = (st.data.presets || []).map((p) => p.name);
    for (const n of Object.keys(modPresets())) if (!retail(n)) out.push(n);
    return out;
  }
  function grouped(keys) {
    const out = [];
    for (const [k, v] of keys) {
      const hit = out.find(([x]) => lower(x) === lower(k));
      if (hit) hit[1] = [].concat(hit[1], v); else out.push([k, v]);
    }
    return out;
  }
  function settings(name) {
    const r = retail(name);
    const m = modPresets()[name] || modPresets()[Object.keys(modPresets()).find((n) => lower(n) === lower(name))] || {};
    const base = r ? r.keys : (retail(m.copy) || {keys: []}).keys;
    const out = grouped(base);
    for (const [k, v] of Object.entries(m)) {
      if (k === 'copy') continue;
      const hit = out.find(([x]) => lower(x) === lower(k));
      if (hit) hit[1] = v; else out.push([k, v]);
    }
    return out;
  }
  const changedKeys = (name) => Object.keys(modPresets()[name] || {}).filter((k) => k !== 'copy');

  // Seasons: the game's lines for one, and the mod's keys over them.
  const modSeasons = () => (st.mod.seasons = st.mod.seasons || {});
  const seasonOf = (name) => (st.data.seasons || []).find((x) => lower(x.name) === lower(name));
  const seasonMod = (name) => (st.mod.seasons || {})[name] || {};
  function seasonRows(name) {
    const out = grouped((seasonOf(name) || {keys: []}).keys);
    for (const [k, v] of Object.entries(seasonMod(name))) {
      const hit = out.find(([x]) => lower(x) === lower(k));
      if (hit) hit[1] = v; else out.push([k, v]);
    }
    return out;
  }

  // "158 167 175" <-> #9ea7af (an RGB setting's first three numbers)
  const nums = (v) => String(v).trim().split(/\s+/).map(Number);
  const toHex = (v) => '#' + nums(v).slice(0, 3).map((n) => Math.max(0, Math.min(255, Math.round(n || 0))).toString(16).padStart(2, '0')).join('');
  const fromHex = (hex, old) => {
    const rgb = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
    const rest = nums(old).slice(3);
    return [...rgb, ...rest].join(' ');
  };
  const isColour = (k) => /rgba?$/i.test(k) && !/^processrgba$/i.test(k);

  function field(k, v, attrs, changed) {
    const list = Array.isArray(v);
    const text = list ? v.join('\n') : String(v).replace(/\t+/g, ' ');
    const input = list
      ? `<textarea ${attrs} data-k="${esc(k)}" rows="${v.length}" title="One line each">${esc(text)}</textarea>`
      : `<input ${attrs} data-k="${esc(k)}" type="text" value="${esc(text)}"${/^skytexture$/i.test(k) ? ' list="sky-textures"' : ''}>`;
    const swatch = !list && isColour(k) ? `<input ${attrs} data-k="${esc(k)}" data-colour="1" type="color" value="${toHex(v)}" title="Pick a colour">` : '';
    return `<label class="sky-row${changed ? ' sky-changed' : ''}"><span>${esc(k)}</span>${swatch}${input}</label>`;
  }

  function draw() {
    const d = st.data;
    if (!d) return;
    placeSun();
    $('sky-map').textContent = st.map ? st.map.toUpperCase() : '';
    if (!d.level) {
      $('sky-body').innerHTML = '<p class="hint">This map has no level settings for its sky.</p>';
      return;
    }
    $('sky-textures').innerHTML = (d.textures || []).map((t) => `<option value="${esc(t)}">`).join('');
    const baseMod = st.mod.base || {};
    const baseKeys = [...Object.keys(d.base || {})];
    for (const k of Object.keys(baseMod)) if (!baseKeys.some((x) => lower(x) === lower(k))) baseKeys.push(k);
    const list = names();
    if (!st.preset || !list.some((n) => lower(n) === lower(st.preset))) st.preset = list[0] || null;
    const seasons = (d.seasons || []).map((x) => x.name);
    if (!st.season || !seasons.includes(st.season)) st.season = seasons[0] || null;
    const sn = st.season;
    const seasonChanged = sn ? Object.keys(seasonMod(sn)).map(lower) : [];
    const seasonHtml = !sn ? '' : `
      <h3>Seasons</h3>
      <p class="hint">This map's config has season skies (${seasons.join(', ')}), as blank's and so every new map's:
        each season's sky texture, colours and glow are drawn instead of the base sky's. SW and NW have none.</p>
      <label class="field">Season <select id="sky-season">${seasons.map((n) => `<option ${n === sn ? 'selected' : ''} value="${esc(n)}">${esc(n)}${
        Object.keys(seasonMod(n)).length ? ' (changed)' : ''}</option>`).join('')}</select></label>
      <div class="buttons">${seasons.length > 1 ? `<button id="sky-season-all" title="Give every season this season's changed settings">Use for every season</button>` : ''}
        ${seasonChanged.length ? `<button id="sky-season-revert" title="Back to the game's values for this season">Revert</button>` : ''}</div>
      ${seasonRows(sn).map(([k, v]) => field(k, v, 'data-season="1"', seasonChanged.includes(lower(k)))).join('')}`;
    const p = st.preset;
    const isNew = p && !retail(p);
    const rows = p ? settings(p) : [];
    const changed = p ? changedKeys(p).map(lower) : [];
    const missing = (d.preset_keys || []).filter((k) => !rows.some(([x]) => lower(x) === lower(k)));
    const sunText = (st.mod.base || {}).SunAngle ?? Object.entries(d.base || {}).find(([k]) => lower(k) === 'sunangle')?.[1];
    const sun = sunFromAngle(sunText);
    const lt = st.lighting, def = (d.lighting || {}).defaults || {};
    const hex = (rgb) => '#' + rgb.map((n) => Math.max(0, Math.min(255, Math.round(n))).toString(16).padStart(2, '0')).join('');
    $('sky-body').innerHTML = `
      <h3>Sun</h3>
      <p class="hint">Its compass bearing (0 north, 90 east, 180 south, 270 west; north is up on the minimap)
        and height above the horizon, written as SunAngle (${esc(sunText || 'none')}). It lights objects and
        characters, and the sun drawn in the 3D view while this panel is open. The ground's light is baked: below.</p>
      <div class="sun-row"><label>Bearing <input id="sun-bearing" type="number" step="1" min="0" max="360" value="${sun ? trim(sun.bearing) : ''}"></label>
        <label>Height <input id="sun-height" type="number" step="0.5" min="1" max="90" value="${sun ? trim(sun.height) : ''}"></label></div>
      <details id="sun-when"${st.whenOpen ? ' open' : ''}><summary>From a place and time</summary>
        <div class="sun-row"><label>Latitude <input id="sun-lat" type="number" step="0.01" value="${st.when.lat}"></label>
          <label>Longitude <input id="sun-lon" type="number" step="0.01" value="${st.when.lon}"></label></div>
        <div class="sun-row"><label>Date <input id="sun-date" type="date" value="${st.when.date}"></label>
          <label>Time <input id="sun-time" type="time" value="${st.when.time}"></label>
          <label title="Hours ahead of UTC (Gaza in summer: 3)">UTC+ <input id="sun-utc" type="number" step="0.5" value="${st.when.utc}"></label></div>
        <div class="buttons"><button id="sun-set">Set the sun</button> <span id="sun-what" class="hint"></span></div>
      </details>
      <h3>Ground lighting</h3>
      <p class="hint">The ground's shading is baked into the map (its vertex colours). This lights all of it from
        the sun above: slopes facing the sun lighter, hills' shadows darker. A new map has none until it's set.</p>
      <label class="check"><input type="checkbox" id="lt-on"${lt ? ' checked' : ''}> Light the ground from the sun
        (${esc((d.lighting || {}).file || 'terrain/<map>.lighting.json')})</label>
      ${lt ? `<div class="sun-row"><label title="Light everywhere, in shadow too">Ambient <input id="lt-ambient" type="color" value="${hex(lt.ambient || def.ambient || [70, 72, 76])}"></label>
        <label title="The sun's light on ground facing it">Sun <input id="lt-diffuse" type="color" value="${hex(lt.diffuse || def.diffuse || [110, 110, 108])}"></label>
        <label title="How much of the sun's light shadowed ground loses (0-1)">Shadow <input id="lt-shadow" type="number" min="0" max="1" step="0.05" value="${lt.shadow ?? def.shadow ?? 0.6}"></label></div>
        <label class="check" title="On maps whose colours have painted tint, keep it"><input type="checkbox" id="lt-tint"${lt.tint === false ? '' : ' checked'}> Keep painted tint</label>
        <label class="field" title="A PNG in the mod's terrain folder, one pixel per height sample (laid out like the heights PNG): its colour tints the lit ground, 128 grey is neutral">Tint map
          <select id="lt-tintmap"><option value="">(none)</option>${[...new Set([...((d.lighting || {}).tint_maps || []), ...(lt.tint_map ? [lt.tint_map] : [])])]
            .map((n) => `<option${n === lt.tint_map ? ' selected' : ''}>${esc(n)}</option>`).join('')}</select></label>
        ${lt.tint_map ? `<label class="field" title="0: no tint, 1: the map's full tint">Tint strength
          <input id="lt-tintstr" type="range" min="0" max="1" step="0.05" value="${lt.tint_strength ?? 1}">
          <span class="hint">${lt.tint_strength ?? 1}</span></label>` : ''}
        <div class="buttons"><button id="lt-preview" title="Light the ground in the 3D view as the build will (takes a few seconds on a big map)">Preview</button>
          <span id="lt-note" class="hint"></span></div>` : ''}
      <h3>Base sky</h3>
      <p class="hint">The map's own sky, outside any weather zone.</p>
      ${baseKeys.map((k) => field(k, baseMod[k] ?? d.base[k] ?? '', 'data-base="1"', k in baseMod)).join('')}
      ${seasonHtml}
      <h3>Weather presets</h3>
      <p class="hint">A preset shows inside a weather zone named after it (New zone, Zone type: Weather), or when
        a mission switches to it (Missions, Events, Weather). ${list.length} on this map${Object.keys(modPresets()).length ? `; ${Object.keys(modPresets()).length} changed or new in this mod` : ''}.</p>
      <label class="field">Preset <select id="sky-preset">${list.map((n) => `<option ${n === p ? 'selected' : ''} value="${esc(n)}">${esc(n)}${
        !retail(n) ? ' (new)' : changedKeys(n).length ? ' (changed)' : ''}</option>`).join('')}</select></label>
      <div class="buttons"><button id="sky-new" title="A new preset, starting as a copy of this one">New from this</button>
        ${p && (isNew || changed.length) ? `<button id="sky-revert" title="${isNew ? 'Remove this new preset' : 'Back to the game\'s values'}">${isNew ? 'Delete' : 'Revert'}</button>` : ''}</div>
      ${p ? `<p class="hint">${isNew ? `New, from ${esc(modPresets()[p].copy || 'nothing')}.` : `In ${esc((retail(p) || {}).file)}.`} Changed settings are highlighted.</p>
        ${rows.map(([k, v]) => field(k, v, 'data-preset="1"', changed.includes(lower(k)))).join('')}
        ${missing.length ? `<label class="field">Add a setting <select id="sky-add"><option value="">(setting)</option>${missing.map((k) => `<option>${esc(k)}</option>`).join('')}</select></label>` : ''}` : ''}`;
  }

  // edits: a base or preset setting, as text (a list for a repeated one)
  $('sky-body').addEventListener('input', (e) => {
    const t = e.target, k = t.dataset.k;
    if (!k) return;
    let v = t.dataset.colour ? fromHex(t.value, (t.parentElement.querySelector('input[type=text]') || {}).value || '0 0 0')
      : t.tagName === 'TEXTAREA' ? t.value.split('\n').map((s) => s.trim()).filter(Boolean) : t.value.trim();
    if (t.dataset.colour) {
      const text = t.parentElement.querySelector('input[type=text]');
      if (text) text.value = v;
    }
    if (t.dataset.base) (st.mod.base = st.mod.base || {})[k] = v;
    else if (t.dataset.season) {
      if (!st.season) return;
      const keys = (modSeasons()[st.season] = modSeasons()[st.season] || {});
      const same = Object.keys(keys).find((x) => lower(x) === lower(k));
      if (same && same !== k) delete keys[same];
      keys[k] = v;
    } else if (st.preset) (modPresets()[st.preset] = modPresets()[st.preset] || {})[k] = v;
    else return;
    if (!t.dataset.colour) {
      const sw = t.parentElement.querySelector('input[type=color]');
      if (sw && typeof v === 'string') sw.value = toHex(v);
    }
    t.closest('.sky-row').classList.add('sky-changed');
    edited();
  });
  $('sky-body').addEventListener('change', (e) => {
    const id = e.target.id;
    if (id === 'sun-bearing' || id === 'sun-height') return setSun(+$('sun-bearing').value, +$('sun-height').value);
    if (/^sun-(lat|lon|date|time|utc)$/.test(id)) {
      st.when = {lat: +$('sun-lat').value, lon: +$('sun-lon').value, date: $('sun-date').value,
                 time: $('sun-time').value, utc: +$('sun-utc').value};
      return;
    }
    if (id === 'lt-on') {
      st.lighting = e.target.checked ? {} : null;
      st.lightingChanged = true;
      edited(); draw();
      return;
    }
    if (/^lt-(ambient|diffuse|shadow|tint|tintstr)$/.test(id)) return lightingEdited();
    if (id === 'lt-tintmap') { lightingEdited(); draw(); return; }
    if (e.target.id === 'sky-preset') { st.preset = e.target.value; draw(); }
    if (e.target.id === 'sky-season') { st.season = e.target.value; draw(); }
    if (e.target.id === 'sky-add' && e.target.value && st.preset) {
      (modPresets()[st.preset] = modPresets()[st.preset] || {})[e.target.value] = '0';
      edited(); draw();
    }
  });
  $('sky-body').addEventListener('toggle', (e) => {
    if (e.target.id === 'sun-when') st.whenOpen = e.target.open;
  }, true);
  $('sky-body').addEventListener('click', async (e) => {
    if (e.target.id === 'sun-set') {
      const w = st.when;
      const [y, mo, da] = String(w.date).split('-').map(Number);
      const [hh, mm] = String(w.time).split(':').map(Number);
      if (![w.lat, w.lon, y, mo, da, hh, mm, w.utc].every(Number.isFinite)) return status('Fill in the place, date and time', true);
      const when = new Date(Date.UTC(y, mo - 1, da, hh, mm) - w.utc * 3600000);
      const sun = sunAt(w.lat, w.lon, when);
      if (sun.height < 1) return status(`The sun is ${sun.height < 0 ? 'below' : 'on'} the horizon then (${trim(sun.height)} degrees)`, true);
      setSun(Math.round(sun.bearing * 10) / 10, Math.round(sun.height * 10) / 10);
      status(`Sun set for ${w.date} ${w.time} (UTC+${w.utc}) at ${w.lat}, ${w.lon}: bearing ${trim(sun.bearing)}, ${trim(sun.height)} degrees up`);
      return;
    }
    if (e.target.id === 'lt-preview' && st.lighting) {
      $('lt-note').textContent = 'Lighting the ground...';
      try {
        const r = await fetch('/api/lighting/preview', {method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({map: st.map, lighting: st.lighting, base: st.mod.base || {}})});
        if (!r.ok) throw new Error((await r.json()).error || r.statusText);
        const ok = ctx.groundLight && ctx.groundLight(await r.arrayBuffer());
        $('lt-note').textContent = ok ? 'Shown in the 3D view (Save to keep it).' : 'The 3D view has no ground for this map.';
      } catch (err) {
        $('lt-note').textContent = '';
        status(`Preview: ${err.message}`, true);
      }
      return;
    }
    if (e.target.id === 'sky-new' && st.preset) {
      const name = (prompt('Name of the new preset (letters, digits, _ and -; a weather zone takes the same name)', `${st.preset}_2`) || '').trim();
      if (!name) return;
      if (!/^[A-Za-z0-9_-]+$/.test(name)) return status('A preset name is letters, digits, _ and -', true);
      if (names().some((n) => lower(n) === lower(name))) return status(`This map already has a preset ${name}`, true);
      const from = retail(st.preset) ? st.preset : (modPresets()[st.preset] || {}).copy;
      const extra = retail(st.preset) ? {...(modPresets()[st.preset] || {})} : {...modPresets()[st.preset]};
      delete extra.copy;
      modPresets()[name] = {copy: from, ...extra};
      st.preset = name;
      edited(); draw();
    }
    if (e.target.id === 'sky-season-all' && st.season) {
      const keys = seasonMod(st.season);
      if (!Object.keys(keys).length) return status(`${st.season} has no changes to copy`, true);
      for (const n of (st.data.seasons || []).map((x) => x.name)) {
        if (n !== st.season) modSeasons()[n] = {...(modSeasons()[n] || {}), ...JSON.parse(JSON.stringify(keys))};
      }
      edited(); draw();
      status(`${st.season}'s changes copied to every season`);
    }
    if (e.target.id === 'sky-season-revert' && st.season) {
      delete modSeasons()[st.season];
      if (!Object.keys(modSeasons()).length) delete st.mod.seasons;
      edited(); draw();
    }
    if (e.target.id === 'sky-revert' && st.preset) {
      delete modPresets()[st.preset];
      st.preset = retail(st.preset) ? st.preset : null;
      edited(); draw();
    }
  });

  function saveBody() {
    if (!st.dirty || !st.data || !st.data.level) return null;
    return st.lightingChanged ? {map: st.map, doc: st.mod, lighting: st.lighting} : {map: st.map, doc: st.mod};
  }
  // after a save: the ground as the build now lights it, when its lighting changed
  function saved() {
    st.dirty = false;
    if ((st.lightingChanged || st.lighting) && ctx.reloadGround) {      // lit from this sun: baked again
      status('Lighting the ground from the sun, as the build will...');
      Promise.resolve(ctx.reloadGround()).then(() => status('The ground is lit as the build lights it'));
    }
    st.lightingChanged = false;
    undoBase = snapshot();
  }
  // The presets a mission can switch to (the game's and the mod's new ones).
  const presetNames = () => (st.data && st.data.level ? names() : []);

  $('sky-mode').onclick = () => show($('sky').hidden);
  $('sky-close').onclick = () => show(false);
  return {load, show, saveBody, saved, presetNames, flushUndo};
}
