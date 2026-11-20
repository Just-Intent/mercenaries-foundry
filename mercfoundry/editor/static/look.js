// Look: a new map's own HUD and PDA art (maps/<map>.json "hud_textures":
// {game texture: the map's texture}), drawn instead of the game's while the
// map is loaded (Foundry Console). Each row shows both and takes a PNG.

const SHEETS = [
  ['radarmapels', 'radar and PDA map markers: HQ flags, blips'],
  ['bigflags', 'the large faction flags (PDA, mood screen)'],
  ['icons01', 'faction badges over characters and vehicles'],
  ['icons02', 'more faction icons'],
  ['deck52', 'Deck of 52 screen: card outlines, suit pips, backs'],
  ['deck52_intel', 'the Deck screen\'s intel meter'],
  ['desktop_elements', 'PDA screens\' frames'],
  ['desktop_verify_elements', 'the verify screen\'s frame'],
  ['pdabuttons', 'PDA buttons'],
];

export function createLook(ctx) {
  const {$, status, post, mapName} = ctx;
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
  const st = {map: null, data: null, hud: {}, dirty: false, bust: Date.now()};
  const box = document.createElement('div');
  box.id = 'look';
  box.hidden = true;
  $('panel').prepend(box);                // first in the panel, above the view's hints

  async function load() {
    st.map = mapName();
    const r = await fetch(`/api/mapkeys/${encodeURIComponent(st.map)}`);
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || r.statusText);
    st.data = d;
    st.hud = d.new ? {...(d.keys.hud_textures || {})} : {};
    st.dirty = false;
    draw();
  }

  function edited() { st.dirty = true; const b = $('look-save'); if (b) b.disabled = false; }

  function row(game, mine) {
    return `<div class="look-row" data-game="${esc(game)}">
      <div class="look-pair">
        <figure><img src="/api/texture/${encodeURIComponent(game)}.png" alt="" loading="lazy"><figcaption>${esc(game)}</figcaption></figure>
        <span class="look-arrow">&rarr;</span>
        <figure>${mine ? `<img src="/api/modfile/textures/${encodeURIComponent(mine)}.png?${st.bust}" alt="" onerror="this.replaceWith(Object.assign(document.createElement('i'), {textContent: 'no textures/${esc(mine)}.png yet'}))">` : '<i>the game\'s</i>'}
          <figcaption><input data-mine value="${esc(mine || '')}" placeholder="${esc(st.map)}_${esc(game)}"></figcaption></figure>
      </div>
      <div class="buttons"><label class="filebtn">PNG (same layout)<input type="file" accept="image/png" data-upload></label>
        <button data-remove title="Back to the game's">&times;</button></div>
    </div>`;
  }

  function draw() {
    if (box.hidden) return;
    const d = st.data;
    let h = `<div class="panel-head"><h2>Look</h2><button id="look-close">Close</button></div>`;
    if (!d) { box.innerHTML = h + '<p class="hint">Loading...</p>'; bindClose(); return; }
    if (!d.new) {
      box.innerHTML = h + `<p class="hint">A map's own HUD art is for a mod's new map. The game's maps keep the game's.</p>`;
      bindClose();
      return;
    }
    const gen = (d.generated || []).includes('hud_textures');
    h += `<h3>HUD and PDA art</h3>
      <p class="hint">While this map is loaded, the HUD and PDA draw your texture where they drew the game's
        (with Foundry Console). Keep each sheet's layout: every sprite draws the same part of it.</p>
      ${gen ? '<p class="warn">"hud_textures" is written by this mod\'s own tools: edits here are replaced the next time they run.</p>' : ''}
      ${Object.entries(st.hud).map(([g, m]) => row(g, m)).join('') || '<p class="hint">None yet.</p>'}
      <div class="buttons"><select id="look-add"><option value="">Add a sheet...</option>
        ${SHEETS.filter(([n]) => !(n in st.hud)).map(([n, what]) => `<option value="${n}">${n}: ${esc(what)}</option>`).join('')}
        <option value="?">another game texture...</option></select></div>
      <div class="buttons sticky-save"><button id="look-save" ${st.dirty ? '' : 'disabled'}>Save HUD art</button>
        <span id="look-note" class="hint"></span></div>`;
    box.innerHTML = h;
    bind();
  }

  function bindClose() { $('look-close').onclick = () => show(false); }

  function bind() {
    bindClose();
    $('look-add').onchange = () => {
      let g = $('look-add').value;
      if (g === '?') g = (prompt('The game texture (its name, e.g. icons01):') || '').trim();
      if (!g) return draw();
      st.hud[g] = `${st.map}_${g}`;
      edited(); draw();
    };
    box.querySelectorAll('.look-row').forEach((r) => {
      const g = r.dataset.game;
      r.querySelector('[data-mine]').onchange = (e) => { st.hud[g] = e.target.value.trim(); edited(); };
      r.querySelector('[data-remove]').onclick = () => { delete st.hud[g]; edited(); draw(); };
      r.querySelector('[data-upload]').onchange = async (e) => {
        const fl = e.target.files[0];
        if (!fl) return;
        const name = st.hud[g] || `${st.map}_${g}`;
        st.hud[g] = name;
        const b64 = await new Promise((res) => { const rd = new FileReader(); rd.onload = () => res(String(rd.result).split(',')[1]); rd.readAsDataURL(fl); });
        try {
          const out = await post('/api/texture/upload', {name, png: b64});
          status(`Saved ${out.file} (${out.size.join('x')})`);
          st.bust = Date.now();
          edited(); draw();
        } catch (err) { status(err.message, true); }
      };
    });
    $('look-save').onclick = async () => {
      for (const [g, m] of Object.entries(st.hud)) if (!m) delete st.hud[g];
      try {
        const r = await post('/api/mapkeys/save', {map: st.map, changes: {hud_textures: Object.keys(st.hud).length ? st.hud : null}});
        status(`Saved the HUD art to ${r.file}`);
        await load();
      } catch (e) { $('look-note').textContent = e.message; status(`Not saved: ${e.message}`, true); }
    };
  }

  function show(on) {
    box.hidden = !on;
    $('look-mode').classList.toggle('on', on);
    if (on && (st.map !== mapName() || !st.data)) load().catch((e) => status(e.message, true));
    else if (on) draw();
  }
  $('look-mode').onclick = () => show(box.hidden);
  return {show, load, isDirty: () => st.dirty};
}
