// Sky and weather: a map's base sky settings and its atmosphere presets (the
// named weather a weather zone or a mission switches on), edited as the
// mod's sky/<map>.json. The game's values show; changes are kept per key.

export function createSky(ctx) {
  const {$, status, setDirty} = ctx;
  const st = {map: null, data: null, mod: {}, preset: null, dirty: false};
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
    st.dirty = false;
    draw();
    undoBase = JSON.stringify(st.mod);      // after drawing, which adds an empty "presets" (not an edit)
  }

  function show(on) {
    $('sky').hidden = !on;
    $('sky-mode').classList.toggle('on', on);
    if (on) draw();
  }

  function edited() {
    st.dirty = true;
    setDirty(true);
    clearTimeout(undoTimer);
    undoTimer = setTimeout(flushUndo, 700);       // a burst of edits is one undo step
  }
  // The map's sky edits before and after a change: one step on the editor's history.
  let undoBase = null, undoTimer = null;
  function flushUndo() {
    clearTimeout(undoTimer);
    undoTimer = null;
    if (undoBase === null) return;
    const now = JSON.stringify(st.mod);
    if (now === undoBase) return;
    const before = undoBase, after = now, map = st.map;
    undoBase = now;
    const put = (json) => {
      if (st.map !== map) return status(`That undo step is for ${map}'s sky: open that map first`, true);
      st.mod = JSON.parse(json);
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
    const p = st.preset;
    const isNew = p && !retail(p);
    const rows = p ? settings(p) : [];
    const changed = p ? changedKeys(p).map(lower) : [];
    const missing = (d.preset_keys || []).filter((k) => !rows.some(([x]) => lower(x) === lower(k)));
    $('sky-body').innerHTML = `
      <h3>Base sky</h3>
      <p class="hint">The map's own sky, outside any weather zone.</p>
      ${baseKeys.map((k) => field(k, baseMod[k] ?? d.base[k] ?? '', 'data-base="1"', k in baseMod)).join('')}
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
    else if (st.preset) (modPresets()[st.preset] = modPresets()[st.preset] || {})[k] = v;
    else return;
    if (!t.dataset.colour) {
      const sw = t.parentElement.querySelector('input[type=color]');
      if (sw && typeof v === 'string') sw.value = toHex(v);
    }
    t.closest('.sky-row').classList.add('sky-changed');
    edited();
  });
  $('sky-body').addEventListener('change', (e) => {
    if (e.target.id === 'sky-preset') { st.preset = e.target.value; draw(); }
    if (e.target.id === 'sky-add' && e.target.value && st.preset) {
      (modPresets()[st.preset] = modPresets()[st.preset] || {})[e.target.value] = '0';
      edited(); draw();
    }
  });
  $('sky-body').addEventListener('click', (e) => {
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
    if (e.target.id === 'sky-revert' && st.preset) {
      delete modPresets()[st.preset];
      st.preset = retail(st.preset) ? st.preset : null;
      edited(); draw();
    }
  });

  function saveBody() {
    if (!st.dirty || !st.data || !st.data.level) return null;
    return {map: st.map, doc: st.mod};
  }
  function saved() { st.dirty = false; }
  // The presets a mission can switch to (the game's and the mod's new ones).
  const presetNames = () => (st.data && st.data.level ? names() : []);

  $('sky-mode').onclick = () => show($('sky').hidden);
  $('sky-close').onclick = () => show(false);
  return {load, show, saveBody, saved, presetNames, flushUndo};
}
