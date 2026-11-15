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
      // a free-roam map: travel pilots standing here, to the mod's new maps
      let travel = '';
      if (d.free_roam && (d.new_maps || []).length) {
        const here = (d.pilots || []).map((p) => `
          <div class="field">To ${esc(p.to)} at ${esc((p.position || []).join(', '))}
            <button data-remove-pilot="${esc(p.to)}:${p.index}">Remove</button></div>`).join('')
          || '<p class="hint">None yet.</p>';
        travel = `
          <h3>Travel pilots here</h3>
          <p class="hint">A pilot you use to travel to one of the mod's new maps (a question, then the game
            saves and goes there, to the map's arrival point). Retail's quadrant pilot by default.</p>
          ${here}
          <label class="field">To <select id="mapset-pilot-to">${d.new_maps.map((m) => `<option>${esc(m)}</option>`).join('')}</select></label>
          <label class="field">Heading <input id="mapset-pilot-heading" type="number" step="1" value="0"></label>
          <div class="buttons"><button id="mapset-pilot-add" title="The ground at the middle of the view">Put a pilot at the middle of the view</button></div>`;
      }
      $('mapset-body').innerHTML = `<p class="hint">${esc(st.map)} is one of the game's maps.</p>${travel}${make}`;
      return;
    }
    const s = d.start || {};
    const back = d.return || {};
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
      <h3>Pilot back</h3>
      <p class="hint">A pilot on this map that takes the player back to free roam (beside the pilot that
        brought them, if there is one there). Put pilots that bring the player here on SW or NW (Map, there).</p>
      <label class="field">Position <input id="mapset-back-pos" value="${esc(back.position ? back.position.join(', ') : '')}" placeholder="x, y, z (empty: no pilot back)"></label>
      <label class="field">Heading <input id="mapset-back-heading" type="number" step="1" value="${esc(back.heading ?? 0)}"></label>
      <label class="field">Back to <select id="mapset-back-to">${['sw', 'nw'].map((m) => `<option${(back.to || 'sw') === m ? ' selected' : ''}>${m}</option>`).join('')}</select></label>
      <div class="buttons"><button id="mapset-back-here" title="The ground at the middle of the view">Pilot at the middle of the view</button>
        <button id="mapset-back-clear">Clear</button></div>
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
    const here = () => viewCentre().map((v) => Math.round(v * 100) / 100);
    if (id === 'mapset-back-here') $('mapset-back-pos').value = here().join(', ');
    if (id === 'mapset-back-clear') $('mapset-back-pos').value = '';
    if (id === 'mapset-pilot-add') {
      try {
        st.data = {...await post('/api/maps/travel', {op: 'add', to: $('mapset-pilot-to').value, on: st.map,
                                                     position: here(), heading: Number($('mapset-pilot-heading').value) || 0}),
                   game_maps: st.data.game_maps};
        draw();
        status(`Added a pilot to ${$('mapset-pilot-to') ? $('mapset-pilot-to').value : 'the map'}; the next game start has it`);
      } catch (err) { status(err.message, true); }
    }
    if (e.target.dataset.removePilot) {
      const [to, index] = e.target.dataset.removePilot.split(':');
      try {
        st.data = {...await post('/api/maps/travel', {op: 'remove', to, index: Number(index), on: st.map}),
                   game_maps: st.data.game_maps};
        draw();
        status(`Removed the pilot to ${to}`);
      } catch (err) { status(err.message, true); }
    }
    if (id === 'mapset-save') {
      const raw = $('mapset-pos').value.trim();
      const nums = raw ? raw.split(/[\s,]+/).map(Number) : [];
      if (raw && (nums.length !== 3 || nums.some((n) => !Number.isFinite(n)))) return status('Position is x, y, z', true);
      const layers = {};
      for (const box of $('mapset').querySelectorAll('[data-layer]')) layers[box.dataset.layer] = box.checked;
      const rawBack = $('mapset-back-pos').value.trim();
      const backNums = rawBack ? rawBack.split(/[\s,]+/).map(Number) : [];
      if (rawBack && (backNums.length !== 3 || backNums.some((n) => !Number.isFinite(n)))) return status('The position of the pilot back is x, y, z', true);
      try {
        st.data = {...await post('/api/maps/save', {map: st.map, doc: {
          start: nums.length ? {position: nums, heading: Number($('mapset-heading').value) || 0} : null,
          message: $('mapset-msg').value, layers,
          return: backNums.length ? {position: backNums, heading: Number($('mapset-back-heading').value) || 0,
                                     to: $('mapset-back-to').value} : null}}), game_maps: st.data.game_maps};
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
