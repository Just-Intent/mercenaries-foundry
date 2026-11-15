// New maps: make one (a copy of a game map, blank by default), and a new
// map's settings: where the player arrives, a message on arrival, and which
// of the mod's layers on it load with it (its master script loads them).

export function createMapSettings(ctx) {
  const {$, status, post, viewCentre, switchToMap} = ctx;
  const st = {map: null, data: null};
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));

  async function load(map) {
    st.map = map;
    try {
      const r = await fetch(`/api/mapsettings/${map}`);
      st.data = await r.json();
      if (!r.ok) throw new Error(st.data.error || r.statusText);
    } catch (e) {
      st.data = {new: false, game_maps: []};
      console.warn('map settings:', e);
    }
    draw();
  }

  function show(on) {
    $('mapset').hidden = !on;
    $('mapset-mode').classList.toggle('on', on);
    if (on) draw();
  }

  function draw() {
    const d = st.data;
    if (!d || $('mapset').hidden) return;
    $('mapset-map').textContent = st.map || '';
    const games = (d.game_maps || []).map((m) => `<option${m === 'blank' ? ' selected' : ''}>${esc(m)}</option>`).join('');
    const make = `
      <h3>New map</h3>
      <label class="field">Name <input id="mapset-name" placeholder="letters, digits and _"></label>
      <label class="field">Copy of <select id="mapset-from">${games}</select></label>
      <p class="hint">blank is flat ground and next to nothing else: shape it in Terrain, set its sky in Sky.
        A copy is the same size as the map it copies.</p>
      <div class="buttons"><button id="mapset-make">Make the map</button></div>`;
    if (!d.new) {
      $('mapset-body').innerHTML = `<p class="hint">${esc(st.map)} is one of the game's maps.</p>${make}`;
      return;
    }
    const s = d.start || {};
    const pos = s.position ? s.position.join(', ') : '';
    const layers = (d.layers || []).map((l) => `
      <label class="field"><input type="checkbox" data-layer="${esc(l.name)}"${l.loads ? ' checked' : ''}> ${esc(l.name)}</label>`).join('')
      || '<p class="hint">No layers on this map yet (objects you place here go in one, which loads with it).</p>';
    $('mapset-body').innerHTML = `
      <p class="hint">A copy of ${esc(d.from)} (maps/${esc(d.name)}.json). Enter it from Foundry Console with
        <code>Utility_ResetToBaseLayerOnly('${esc(d.name)}')</code>.</p>
      ${d.own_script ? `<p class="hint">scripts/${esc(d.name)}.lua is its master script, so these settings
        don't apply: load your layers and place the player in it.</p>` : ''}
      <h3>Arrival</h3>
      <label class="field">Position <input id="mapset-pos" value="${esc(pos)}" placeholder="x, y, z (empty: where the map puts you)"></label>
      <label class="field">Heading <input id="mapset-heading" type="number" step="1" value="${esc(s.heading ?? 0)}"></label>
      <div class="buttons"><button id="mapset-here" title="The ground at the middle of the view">Arrive at the middle of the view</button>
        <button id="mapset-clear">Clear</button></div>
      <label class="field">Message on arrival <input id="mapset-msg" value="${esc(d.message)}" placeholder="(none)"></label>
      <h3>Layers that load with it</h3>
      ${layers}
      <div class="buttons"><button id="mapset-save">Save map settings</button></div>
      <p class="hint">Saved straight away; the next game start (through the launcher) uses them.</p>
      ${make}`;
  }

  $('mapset').addEventListener('click', async (e) => {
    const id = e.target.id;
    if (id === 'mapset-here') {
      $('mapset-pos').value = viewCentre().map((v) => Math.round(v * 100) / 100).join(', ');
    }
    if (id === 'mapset-clear') $('mapset-pos').value = '';
    if (id === 'mapset-save') {
      const raw = $('mapset-pos').value.trim();
      const nums = raw ? raw.split(/[\s,]+/).map(Number) : [];
      if (raw && (nums.length !== 3 || nums.some((n) => !Number.isFinite(n)))) return status('Position is x, y, z', true);
      const layers = {};
      for (const box of $('mapset').querySelectorAll('[data-layer]')) layers[box.dataset.layer] = box.checked;
      try {
        st.data = {...await post('/api/maps/save', {map: st.map, doc: {
          start: nums.length ? {position: nums, heading: Number($('mapset-heading').value) || 0} : null,
          message: $('mapset-msg').value, layers}}), game_maps: st.data.game_maps};
        draw();
        status(`Saved ${st.map}'s settings; the next game start uses them`);
      } catch (err) { status(err.message, true); }
    }
    if (id === 'mapset-make') {
      const name = $('mapset-name').value.trim();
      if (!/^[A-Za-z0-9_]+$/.test(name)) return status('A map name is letters, digits and _', true);
      try {
        const r = await post('/api/maps/new', {name, from: $('mapset-from').value});
        status(`Made ${r.name}; loading it...`);
        await switchToMap(r.name);
      } catch (err) { status(err.message, true); }
    }
  });

  $('mapset-mode').onclick = () => show($('mapset').hidden);
  $('mapset-close').onclick = () => show(false);
  return {load, show};
}
